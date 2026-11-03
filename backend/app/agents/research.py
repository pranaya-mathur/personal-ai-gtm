import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Evidence, Opportunity, OpportunityEvidence
from app.models.enums import VerificationStatus


class ResearchEvidenceAgent:
    """Audits evidence completeness for each opportunity. It does not verify claims or add
    facts: verification is a human action (see POST /opportunities/{id}/verify)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, opportunity_ids: list[str]) -> dict[str, dict[str, object]]:
        report: dict[str, dict[str, object]] = {}
        for oid in opportunity_ids:
            opp = self.session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            evs = (
                self.session.execute(
                    select(Evidence)
                    .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
                    .where(OpportunityEvidence.opportunity_id == opp.id)
                )
                .scalars()
                .all()
            )
            contacts = 0
            if opp.organization_id:
                contacts = len(
                    self.session.execute(
                        select(Contact).where(Contact.organization_id == opp.organization_id)
                    )
                    .scalars()
                    .all()
                )
            report[oid] = {
                "evidence_count": len(evs),
                "has_source_url": all(bool(e.source_url) for e in evs) and bool(evs),
                "has_publication_date": all(e.published_at is not None for e in evs) and bool(evs),
                "all_fresh": all(e.verification_status != VerificationStatus.STALE for e in evs),
                "known_contacts": contacts,
            }
        return report
