import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tasks import AITasks
from app.models import (
    Contact,
    Evidence,
    Opportunity,
    OpportunityEvidence,
    Organization,
    OutreachDraft,
)
from app.models.enums import OpportunityStatus, OutreachStatus
from app.services.spend import SpendLimitExceeded


class OutreachIntelligenceAgent:
    """Prepares drafts for human review. It never sends, and every draft is created in
    PENDING_APPROVAL. Text uses only facts held in evidence; unknowns stay as placeholders."""

    def __init__(
        self, session: Session, tasks: AITasks | None = None, run_id: uuid.UUID | None = None
    ) -> None:
        self.session, self.tasks, self.run_id = session, tasks, run_id

    def run(self, qualified: dict[str, dict[str, object]]) -> list[str]:
        draft_ids: list[str] = []
        for oid, q in qualified.items():
            if q["status"] != OpportunityStatus.QUALIFIED:
                continue
            opp = self.session.get(Opportunity, uuid.UUID(oid))
            if opp is None:
                continue
            exists = self.session.execute(
                select(OutreachDraft).where(OutreachDraft.opportunity_id == opp.id)
            ).first()
            if exists:
                continue
            org = (
                self.session.get(Organization, opp.organization_id) if opp.organization_id else None
            )
            ev = (
                self.session.execute(
                    select(Evidence)
                    .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
                    .where(OpportunityEvidence.opportunity_id == opp.id)
                )
                .scalars()
                .first()
            )
            contact = (
                self.session.execute(select(Contact).where(Contact.organization_id == org.id))
                .scalars()
                .first()
                if org
                else None
            )
            greeting = (
                f"Hi {contact.full_name},"
                if contact
                else "Hi [RECIPIENT NAME - not found in evidence],"
            )
            body = (
                f"{greeting}\n\n"
                f'I came across "{opp.title}"'
                + (f" from {org.name}" if org else "")
                + (f" ({ev.source_url})" if ev else "")
                + ".\n\n[YOUR RELEVANT EXPERIENCE - to be written by you]\n\n"
                "[PROPOSED NEXT STEP]\n"
            )
            subject = f"Re: {opp.title}"
            if self.tasks is not None:
                try:
                    ai_draft = self.tasks.draft(
                        opp, self.run_id, contact.full_name if contact else None
                    )
                except SpendLimitExceeded:
                    ai_draft = None  # budget exhausted: keep the evidence-only template
                if ai_draft is not None:
                    subject, body = ai_draft.subject, ai_draft.body
            draft = OutreachDraft(
                opportunity_id=opp.id,
                contact_id=contact.id if contact else None,
                subject=subject,
                body=body,
                status=OutreachStatus.PENDING_APPROVAL,
            )
            self.session.add(draft)
            self.session.flush()
            draft_ids.append(str(draft.id))
        return draft_ids
