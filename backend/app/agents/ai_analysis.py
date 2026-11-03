import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gateway import RecordBudgetExceeded
from app.ai.tasks import AITasks
from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.base import LLMDisabledError
from app.models import Opportunity
from app.models.enums import OpportunityCategory, Triage, VerificationStatus
from app.services.spend import SpendLimitExceeded

log = get_logger(__name__)


class AIAnalysisAgent:
    """One structured dossier per record (summary, requirements, budget/deadline, AI-project
    judgement, evidence verdict, findings, fit). Deterministic filters run first: rejected,
    stale or off-topic records never reach the model, and identical inputs are cached."""

    def __init__(self, session: Session, tasks: AITasks, settings: Settings) -> None:
        self.session, self.tasks, self.settings = session, tasks, settings

    def candidates(self, opportunity_ids: list[str]) -> list[Opportunity]:
        ids = [uuid.UUID(o) for o in opportunity_ids]
        return list(
            self.session.execute(
                select(Opportunity)
                .where(
                    Opportunity.id.in_(ids),
                    Opportunity.verification_status != VerificationStatus.STALE,
                    Opportunity.triage != Triage.REJECTED,
                    # speculative prospects have no stated need to analyse
                    Opportunity.category != OpportunityCategory.SPECULATIVE_PROSPECT,
                )
                .order_by(Opportunity.confidence.desc())
                .limit(self.settings.ai_max_opportunities_per_run)
            ).scalars()
        )

    def run(self, opportunity_ids: list[str], run_id: uuid.UUID | None) -> dict[str, Any]:
        opps = self.candidates(opportunity_ids)
        results: dict[str, dict[str, Any]] = {}
        stopped: str | None = None
        skipped = len(opportunity_ids) - len(opps)
        for opp in opps:
            try:
                dossier = self.tasks.dossier(opp, run_id)
            except RecordBudgetExceeded as exc:  # this record only; keep going
                log.warning("ai_record_budget", opportunity=str(opp.id), reason=str(exc))
                results[str(opp.id)] = {"dossier_ok": False, "error": str(exc)}
                continue
            except SpendLimitExceeded as exc:
                stopped = str(exc)
                log.warning("ai_stage_stopped", reason=stopped)
                break
            except LLMDisabledError as exc:
                stopped = str(exc)
                break
            if dossier is None:
                results[str(opp.id)] = {"dossier_ok": False}
                continue
            results[str(opp.id)] = {
                "dossier_ok": True,
                "ai_project": dossier.ai_project,
                "verdict": dossier.verdict,
                "reliability": dossier.reliability,
                "fit_score": dossier.fit_score,
                "risks": dossier.risks,
                "budget": dossier.budget.text if dossier.budget else None,
                "deadline": dossier.deadline.text if dossier.deadline else None,
            }
        return {
            "results": results,
            "stopped": stopped,
            "considered": len(opps),
            "skipped_by_filter": skipped,
        }
