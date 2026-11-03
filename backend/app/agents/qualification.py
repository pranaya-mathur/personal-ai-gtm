import uuid

from sqlalchemy.orm import Session

from app.models import Opportunity, Qualification
from app.models.enums import OpportunityCategory, OpportunityStatus, VerificationStatus

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
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, research: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for oid, rep in research.items():
            opp = self.session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            score, criteria = score_opportunity(opp.category, opp.confidence, rep)
            stale = opp.verification_status == VerificationStatus.STALE
            rationale = (
                f"score={score} from {', '.join(f'{k}={v:g}' for k, v in criteria.items())}"
                + ("; evidence is stale" if stale else "")
            )
            self.session.add(
                Qualification(
                    opportunity_id=opp.id, score=score, criteria=criteria, rationale=rationale
                )
            )
            if opp.status == OpportunityStatus.NEW:
                if stale:
                    opp.status = OpportunityStatus.DISQUALIFIED
                elif score >= QUALIFY_THRESHOLD:
                    opp.status = OpportunityStatus.QUALIFIED
            out[oid] = {"score": score, "status": opp.status}
        self.session.flush()
        return out
