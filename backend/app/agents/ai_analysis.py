import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tasks import AITasks
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import Opportunity
from app.models.enums import OpportunityStatus, VerificationStatus
from app.services.spend import SpendLimitExceeded

log = get_logger(__name__)


class AIAnalysisAgent:
    """Opportunity analysis, requirement extraction, evidence evaluation and research brief.
    Only runs on fresh, non-disqualified opportunities and is capped per run."""

    def __init__(self, session: Session, tasks: AITasks, settings: Settings) -> None:
        self.session, self.tasks, self.settings = session, tasks, settings

    def run(self, opportunity_ids: list[str], run_id: uuid.UUID | None) -> dict[str, Any]:
        ids = [uuid.UUID(o) for o in opportunity_ids]
        opps = (
            self.session.execute(
                select(Opportunity)
                .where(
                    Opportunity.id.in_(ids),
                    Opportunity.verification_status != VerificationStatus.STALE,
                    Opportunity.status != OpportunityStatus.DISQUALIFIED,
                )
                .order_by(Opportunity.confidence.desc())
                .limit(self.settings.ai_max_opportunities_per_run)
            )
            .scalars()
            .all()
        )
        results: dict[str, dict[str, Any]] = {}
        stopped: str | None = None
        for opp in opps:
            try:
                analysis = self.tasks.analyze(opp, run_id)
                requirements = self.tasks.extract_requirements(opp, run_id)
                evaluation = self.tasks.evaluate_evidence(opp, run_id)
                brief = self.tasks.research(opp, run_id)
            except SpendLimitExceeded as exc:
                stopped = str(exc)
                log.warning("ai_stage_stopped", reason=stopped)
                break
            results[str(opp.id)] = {
                "analysis_ok": analysis is not None,
                "requirements_ok": requirements is not None,
                "verdict": evaluation.verdict if evaluation else None,
                "reliability": evaluation.reliability if evaluation else None,
                "research_ok": brief is not None,
            }
        return {"results": results, "stopped": stopped, "considered": len(opps)}
