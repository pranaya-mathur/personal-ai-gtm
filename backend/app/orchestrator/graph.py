"""LangGraph workflow: discovery -> research -> qualification -> outreach prep -> analytics.

Each node is wrapped so it is skipped when already recorded in `completed_nodes`; together
with per-node persistence in the runner this makes a retried run resume instead of redo.
"""

import uuid
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.analytics import PipelineAnalyticsAgent
from app.agents.discovery import AgencyPartnershipIntelligenceAgent, OpportunityIntelligenceAgent
from app.agents.outreach import OutreachIntelligenceAgent
from app.agents.qualification import QualificationAgent
from app.agents.research import ResearchEvidenceAgent
from app.connectors.base import DiscoveryConnector, DiscoveryQuery
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.source_check import SourceChecker
from app.services.source_verification import verify_sources
from app.services.spend import SpendGuard

log = get_logger(__name__)

NODE_ORDER = [
    "opportunity_intelligence",
    "agency_partnership_intelligence",
    "source_verification",
    "research_evidence",
    "qualification",
    "outreach_intelligence",
    "pipeline_analytics",
]


class WorkflowState(TypedDict, total=False):
    run_id: str
    query: dict[str, Any]
    opportunity_ids: list[str]
    errors: list[str]
    source_checks: dict[str, Any]
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
) -> Any:
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
            return {"opportunity_ids": ids, "errors": [*state.get("errors", []), *result["errors"]]}

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

    def qualification(state: WorkflowState) -> dict[str, Any]:
        return {"qualified": QualificationAgent(session).run(state.get("research", {}))}

    def outreach(state: WorkflowState) -> dict[str, Any]:
        return {"draft_ids": OutreachIntelligenceAgent(session).run(state.get("qualified", {}))}

    def analytics(state: WorkflowState) -> dict[str, Any]:
        return {"metrics": PipelineAnalyticsAgent(session).run()}

    impl: dict[str, Callable[[WorkflowState], dict[str, Any]]] = {
        "opportunity_intelligence": discovery(OpportunityIntelligenceAgent),
        "agency_partnership_intelligence": discovery(AgencyPartnershipIntelligenceAgent),
        "source_verification": source_verification,
        "research_evidence": research,
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
