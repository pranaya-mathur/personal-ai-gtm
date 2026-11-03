import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Opportunity, Qualification
from app.models.enums import VerificationStatus
from app.services.ranking import score_opportunity
from app.services.triage import AiSignal, apply_assessment, assess_opportunity


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
            signal = (
                AiSignal(ai.get("ai_project"), ai.get("verdict"), ai.get("external_delivery"))
                if usable
                else None
            )
            assessment = assess_opportunity(self.session, opp, self.settings, signal)
            previous = apply_assessment(opp, assessment)

            research_flags = dict(rep)
            research_flags["has_contact_route"] = bool(opp.contact_route)
            age_days = None
            if opp.first_seen_at:
                seen = opp.first_seen_at
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=UTC)
                age_days = (datetime.now(UTC) - seen).days
            deal_size = (assessment.criteria.get("deal_size") or {}).get("result")
            det_score, det_criteria = score_opportunity(
                channel=opp.channel,
                category=opp.category,
                triage=str(assessment.triage),
                confidence=opp.confidence,
                research=research_flags,
                deal_size_result=deal_size,
                age_days=age_days,
            )
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
                    ai_small_team_fit=ai.get("small_team_fit"),
                    ai_next_action=ai.get("suggested_next_action"),
                )
            criteria["assessment"] = assessment.to_json()
            criteria["previous"] = previous
            rationale = (
                f"{assessment.triage}: "
                + "; ".join(assessment.reasons)
                + (f" | missing: {'; '.join(assessment.missing)}" if assessment.missing else "")
                + f" | rank score {score} ({criteria.get('rubric')})"
            )
            self.session.add(
                Qualification(
                    opportunity_id=opp.id, score=score, criteria=criteria, rationale=rationale
                )
            )
            out[oid] = {"score": score, "status": opp.status, "triage": str(assessment.triage)}
        self.session.flush()
        return out
