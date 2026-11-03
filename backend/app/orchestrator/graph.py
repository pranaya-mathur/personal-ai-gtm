"""LangGraph workflow: discovery -> research -> qualification -> outreach prep -> analytics.

Each node is wrapped so it is skipped when already recorded in `completed_nodes`; together
with per-node persistence in the runner this makes a retried run resume instead of redo.
"""

import uuid
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.ai_analysis import AIAnalysisAgent
from app.agents.analytics import PipelineAnalyticsAgent
from app.agents.discovery import AgencyPartnershipIntelligenceAgent, OpportunityIntelligenceAgent
from app.agents.outreach import OutreachIntelligenceAgent
from app.agents.qualification import QualificationAgent
from app.agents.research import ResearchEvidenceAgent
from app.ai.gateway import LLMGateway
from app.ai.tasks import AITasks
from app.connectors.base import DiscoveryConnector, DiscoveryQuery
from app.core.config import Settings
from app.core.logging import get_logger
from app.llm import LLMClient, build_llm
from app.models import Opportunity
from app.services.source_check import SourceChecker
from app.services.source_verification import verify_sources
from app.services.spend import SpendGuard
from app.services.triage import apply_assessment, assess_opportunity

log = get_logger(__name__)

NODE_ORDER = [
    "opportunity_intelligence",
    "agency_partnership_intelligence",
    "source_verification",
    "research_evidence",
    "triage",
    "ai_analysis",
    "qualification",
    "outreach_intelligence",
    "pipeline_analytics",
]


class WorkflowState(TypedDict, total=False):
    run_id: str
    query: dict[str, Any]
    opportunity_ids: list[str]
    errors: list[str]
    connector_stats: list[dict[str, Any]]
    source_checks: dict[str, Any]
    ai: dict[str, Any]
    triage: dict[str, int]
    ai_results: dict[str, dict[str, Any]]
    research: dict[str, dict[str, Any]]
    qualified: dict[str, dict[str, Any]]
    draft_ids: list[str]
    metrics: dict[str, Any]
    completed_nodes: list[str]


def build_graph(
    session: Session,
    connectors: list[DiscoveryConnector],
    spend: SpendGuard,
    settings: Settings,
    checker: SourceChecker | None = None,
    llm: LLMClient | None = None,
) -> Any:
    gateway = LLMGateway(session, settings, llm or build_llm(settings), spend)
    ai_ready, ai_reason = gateway.availability()
    tasks = AITasks(session, gateway) if ai_ready else None

    def step(name: str, fn: Callable[[WorkflowState], dict[str, Any]]) -> Callable[..., Any]:
        def wrapped(state: WorkflowState) -> dict[str, Any]:
            done = list(state.get("completed_nodes", []))
            if name in done:
                return {}
            log.info("node_started", node=name, run_id=state["run_id"])
            updates = fn(state)
            updates["completed_nodes"] = [*done, name]
            log.info("node_finished", node=name, run_id=state["run_id"])
            return updates

        return wrapped

    def discovery(agent_cls: type[Any]) -> Callable[[WorkflowState], dict[str, Any]]:
        def run(state: WorkflowState) -> dict[str, Any]:
            result = agent_cls(session, connectors, spend).run(
                DiscoveryQuery(**state["query"]), uuid.UUID(state["run_id"])
            )
            ids = sorted(set(state.get("opportunity_ids", [])) | set(result["opportunity_ids"]))
            return {
                "opportunity_ids": ids,
                "errors": [*state.get("errors", []), *result["errors"]],
                "connector_stats": [*state.get("connector_stats", []), *result["connector_stats"]],
            }

        return run

    def source_verification(state: WorkflowState) -> dict[str, Any]:
        if not settings.source_verification_enabled:
            return {"source_checks": {"skipped": "SOURCE_VERIFICATION_ENABLED=false"}}
        results = verify_sources(
            session, state.get("opportunity_ids", []), checker or SourceChecker()
        )
        return {"source_checks": results}

    def research(state: WorkflowState) -> dict[str, Any]:
        return {"research": ResearchEvidenceAgent(session).run(state.get("opportunity_ids", []))}

    def triage(state: WorkflowState) -> dict[str, Any]:
        """Deterministic stage A+B before any model call; rejected records never reach the AI."""
        summary: dict[str, int] = {}
        for oid in state.get("opportunity_ids", []):
            opp = session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            a = assess_opportunity(session, opp, settings)
            apply_assessment(opp, a)
            summary[str(a.triage)] = summary.get(str(a.triage), 0) + 1
        session.flush()
        return {"triage": summary}

    def ai_analysis(state: WorkflowState) -> dict[str, Any]:
        if tasks is None:
            return {"ai": {"skipped": ai_reason}}
        out = AIAnalysisAgent(session, tasks, settings).run(
            state.get("opportunity_ids", []), uuid.UUID(state["run_id"])
        )
        return {
            "ai": {k: v for k, v in out.items() if k != "results"},
            "ai_results": out["results"],
        }

    def qualification(state: WorkflowState) -> dict[str, Any]:
        agent = QualificationAgent(
            session, tasks, state.get("ai_results", {}), uuid.UUID(state["run_id"]), settings
        )
        return {"qualified": agent.run(state.get("research", {}))}

    def outreach(state: WorkflowState) -> dict[str, Any]:
        agent = OutreachIntelligenceAgent(session, tasks, uuid.UUID(state["run_id"]))
        return {"draft_ids": agent.run(state.get("qualified", {}))}

    def analytics(state: WorkflowState) -> dict[str, Any]:
        return {"metrics": PipelineAnalyticsAgent(session).run()}

    impl: dict[str, Callable[[WorkflowState], dict[str, Any]]] = {
        "opportunity_intelligence": discovery(OpportunityIntelligenceAgent),
        "agency_partnership_intelligence": discovery(AgencyPartnershipIntelligenceAgent),
        "source_verification": source_verification,
        "research_evidence": research,
        "triage": triage,
        "ai_analysis": ai_analysis,
        "qualification": qualification,
        "outreach_intelligence": outreach,
        "pipeline_analytics": analytics,
    }
    graph = StateGraph(WorkflowState)
    for name in NODE_ORDER:
        graph.add_node(name, step(name, impl[name]))
    graph.add_edge(START, NODE_ORDER[0])
    for a, b in zip(NODE_ORDER, NODE_ORDER[1:], strict=False):
        graph.add_edge(a, b)
    graph.add_edge(NODE_ORDER[-1], END)
    return graph.compile()
