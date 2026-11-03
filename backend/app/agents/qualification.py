import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Opportunity, Qualification
from app.models.enums import OpportunityCategory, VerificationStatus
from app.services.triage import AiSignal, apply_assessment, assess_opportunity

CATEGORY_WEIGHT = {
    OpportunityCategory.CONFIRMED_DEMAND: 1.0,
    OpportunityCategory.VERIFIED_PARTNERSHIP: 0.7,
    OpportunityCategory.SPECULATIVE_PROSPECT: 0.3,
}


def score_opportunity(
    category: str, confidence: float, research: dict[str, object]
) -> tuple[float, dict[str, float]]:
    """Transparent ranking rubric (0-100). It ORDERS records; it never decides qualification."""
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
    """Re-runs the deterministic two-stage assessment (now with the AI dossier as advisory
    input) and records the outcome, reasons and gaps. Makes no model call of its own."""

    AI_WEIGHT = 0.4  # blend: (1-w) deterministic rubric + w validated AI fit score

    def __init__(
        self,
        session: Session,
        tasks: object | None = None,
        ai_results: dict[str, dict[str, Any]] | None = None,
        run_id: uuid.UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session = session
        self.ai_results, self.run_id = ai_results or {}, run_id

    def run(self, research: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for oid, rep in research.items():
            opp = self.session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            ai = self.ai_results.get(oid) or {}
            usable = bool(ai.get("dossier_ok"))
            signal = AiSignal(ai.get("ai_project"), ai.get("verdict")) if usable else None
            assessment = assess_opportunity(self.session, opp, self.settings, signal)
            previous = apply_assessment(opp, assessment)

            det_score, det_criteria = score_opportunity(opp.category, opp.confidence, rep)
            score: float = det_score
            criteria: dict[str, Any] = dict(det_criteria)
            if usable and opp.verification_status != VerificationStatus.STALE:
                score = round(
                    (1 - self.AI_WEIGHT) * det_score + self.AI_WEIGHT * ai["fit_score"], 1
                )
                criteria.update(
                    deterministic_score=det_score,
                    ai_fit_score=ai["fit_score"],
                    ai_risks=ai["risks"],
                )
            criteria["assessment"] = assessment.to_json()
            criteria["previous"] = previous
            rationale = (
                f"{assessment.triage}: "
                + "; ".join(assessment.reasons)
                + (f" | missing: {'; '.join(assessment.missing)}" if assessment.missing else "")
                + f" | rank score {score}"
            )
            self.session.add(
                Qualification(
                    opportunity_id=opp.id, score=score, criteria=criteria, rationale=rationale
                )
            )
            out[oid] = {"score": score, "status": opp.status, "triage": str(assessment.triage)}
        self.session.flush()
        return out
