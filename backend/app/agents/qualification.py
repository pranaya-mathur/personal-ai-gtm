import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tasks import AITasks
from app.core.config import Settings, get_settings
from app.models import Evidence, Opportunity, OpportunityEvidence, Qualification
from app.models.enums import OpportunityCategory, OpportunityStatus, VerificationStatus
from app.services.spend import SpendLimitExceeded

CATEGORY_WEIGHT = {
    OpportunityCategory.CONFIRMED_DEMAND: 1.0,
    OpportunityCategory.VERIFIED_PARTNERSHIP: 0.7,
    OpportunityCategory.SPECULATIVE_PROSPECT: 0.3,
}
QUALIFY_THRESHOLD = 60.0


def score_opportunity(
    category: str, confidence: float, research: dict[str, object]
) -> tuple[float, dict[str, float]]:
    """Transparent rubric (0-100). Every component is derived from stored evidence."""
    criteria = {
        "category_weight": CATEGORY_WEIGHT[OpportunityCategory(category)] * 40,
        "source_confidence": confidence * 25,
        "freshness": 15.0 if research.get("all_fresh") else 0.0,
        "source_confirmed": 5.0 if research.get("source_confirmed") else 0.0,
        "dated_and_sourced": 10.0
        if research.get("has_publication_date") and research.get("has_source_url")
        else 0.0,
        "known_contact": 5.0 if int(research.get("known_contacts", 0) or 0) > 0 else 0.0,  # type: ignore[call-overload]
    }
    return round(sum(criteria.values()), 1), criteria


class QualificationAgent:
    AI_WEIGHT = 0.4  # blend: (1-w) deterministic rubric + w validated AI fit score

    def __init__(
        self,
        session: Session,
        tasks: AITasks | None = None,
        ai_results: dict[str, dict[str, Any]] | None = None,
        run_id: uuid.UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session, self.tasks = session, tasks
        self.ai_results, self.run_id = ai_results or {}, run_id

    def _on_topic(self, opp: Opportunity) -> bool:
        texts = self.session.execute(
            select(Evidence.original_text)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(OpportunityEvidence.opportunity_id == opp.id)
        ).scalars()
        blob = " ".join([opp.title, *texts]).lower()
        return any(
            re.search(rf"(?<!\w){re.escape(t)}(?!\w)", blob) for t in self.settings.topic_term_list
        )

    def run(self, research: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for oid, rep in research.items():
            opp = self.session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            on_topic = self._on_topic(opp)
            det_score, det_criteria = score_opportunity(opp.category, opp.confidence, rep)
            score: float = det_score
            criteria: dict[str, Any] = dict(det_criteria)
            stale = opp.verification_status == VerificationStatus.STALE
            ai = self.ai_results.get(oid)
            ai_unsupported = bool(ai and ai.get("verdict") == "unsupported")
            note = ""
            if self.tasks is not None and ai and not ai_unsupported and not stale:
                try:
                    assessment = self.tasks.assess(opp, self.run_id)
                except SpendLimitExceeded as exc:
                    assessment, note = None, f"; AI qualification skipped ({exc})"
                if assessment is not None:
                    score = round(
                        (1 - self.AI_WEIGHT) * det_score + self.AI_WEIGHT * assessment.fit_score, 1
                    )
                    criteria["deterministic_score"] = det_score
                    criteria["ai_fit_score"] = assessment.fit_score
                    criteria["ai_risks"] = assessment.risks
                    criteria["ai_criteria"] = [
                        {
                            "name": c.name,
                            "score": c.score,
                            "rationale": c.rationale,
                            "evidence_id": c.support.evidence_id,
                            "quote": c.support.quote,
                        }
                        for c in assessment.criteria
                    ]
            criteria["on_topic"] = on_topic
            numeric = ", ".join(
                f"{k}={v:g}" for k, v in criteria.items() if isinstance(v, int | float)
            )
            rationale = (
                f"score={score} from {numeric}"
                + ("; evidence is stale" if stale else "")
                + (
                    "; AI judged the evidence does not support this opportunity"
                    if ai_unsupported
                    else ""
                )
                + ("" if on_topic else "; evidence does not mention any AI topic term")
                + note
            )
            self.session.add(
                Qualification(
                    opportunity_id=opp.id, score=score, criteria=criteria, rationale=rationale
                )
            )
            if opp.status == OpportunityStatus.NEW:
                if stale:
                    opp.status = OpportunityStatus.DISQUALIFIED
                elif score >= QUALIFY_THRESHOLD and not ai_unsupported and on_topic:
                    opp.status = OpportunityStatus.QUALIFIED
            out[oid] = {"score": score, "status": opp.status}
        self.session.flush()
        return out
