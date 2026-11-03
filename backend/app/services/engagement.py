"""Human-operated engagement pipeline: deals, interactions, follow-ups, outcomes.

Nothing is sent automatically. Stage transitions are recorded only when an authenticated
operator/admin writes them. Outreach drafts stay separate until marked sent manually.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deal, Interaction, Opportunity, OutreachDraft
from app.models.enums import DealStage, OpportunityStatus, OutreachStatus
from app.utils import utcnow

STAGE_TO_OPP_STATUS = {
    DealStage.IDENTIFIED: OpportunityStatus.QUALIFIED,
    DealStage.CONTACTED: OpportunityStatus.ENGAGED,
    DealStage.CONVERSATION: OpportunityStatus.ENGAGED,
    DealStage.PROPOSAL: OpportunityStatus.ENGAGED,
    DealStage.NEGOTIATION: OpportunityStatus.ENGAGED,
    DealStage.WON: OpportunityStatus.WON,
    DealStage.LOST: OpportunityStatus.LOST,
}

ALLOWED_KINDS = frozenset(
    {
        "note",
        "email_sent",
        "email_received",
        "call",
        "meeting",
        "proposal_sent",
        "follow_up",
        "outcome",
    }
)


class EngagementError(ValueError):
    pass


def get_or_create_deal(session: Session, opportunity_id: uuid.UUID) -> Deal:
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise EngagementError("opportunity not found")
    existing = session.execute(
        select(Deal).where(Deal.opportunity_id == opportunity_id)
    ).scalar_one_or_none()
    if existing:
        return existing
    deal = Deal(opportunity_id=opportunity_id, stage=DealStage.IDENTIFIED)
    session.add(deal)
    session.flush()
    return deal


def update_deal(
    session: Session,
    deal_id: uuid.UUID,
    *,
    stage: str | None = None,
    value_usd: float | None = None,
    notes: str | None = None,
    next_follow_up_at: datetime | None = None,
    clear_follow_up: bool = False,
) -> Deal:
    deal = session.get(Deal, deal_id)
    if deal is None:
        raise EngagementError("deal not found")
    if stage is not None:
        try:
            st = DealStage(stage)
        except ValueError as exc:
            raise EngagementError(f"invalid stage: {stage}") from exc
        deal.stage = st
        opp = session.get(Opportunity, deal.opportunity_id)
        if opp is not None:
            opp.status = STAGE_TO_OPP_STATUS[st]
    if value_usd is not None:
        deal.value_usd = value_usd
    if notes is not None:
        deal.notes = notes
    if clear_follow_up:
        deal.next_follow_up_at = None
    elif next_follow_up_at is not None:
        if next_follow_up_at.tzinfo is None:
            next_follow_up_at = next_follow_up_at.replace(tzinfo=utcnow().tzinfo)
        deal.next_follow_up_at = next_follow_up_at
    session.flush()
    return deal


def add_interaction(
    session: Session,
    deal_id: uuid.UUID,
    *,
    kind: str,
    summary: str,
    occurred_at: datetime | None = None,
    contact_id: uuid.UUID | None = None,
) -> Interaction:
    if kind not in ALLOWED_KINDS:
        raise EngagementError(f"kind must be one of {sorted(ALLOWED_KINDS)}")
    if not summary.strip():
        raise EngagementError("summary is required")
    deal = session.get(Deal, deal_id)
    if deal is None:
        raise EngagementError("deal not found")
    when = occurred_at or utcnow()
    if when.tzinfo is None:
        when = when.replace(tzinfo=utcnow().tzinfo)
    row = Interaction(
        deal_id=deal_id,
        contact_id=contact_id,
        kind=kind,
        summary=summary.strip()[:4000],
        occurred_at=when,
    )
    session.add(row)
    # Light stage nudges from interaction kinds (never auto-win/lose).
    if kind == "email_sent" and deal.stage == DealStage.IDENTIFIED:
        deal.stage = DealStage.CONTACTED
        opp = session.get(Opportunity, deal.opportunity_id)
        if opp:
            opp.status = OpportunityStatus.ENGAGED
    elif kind == "meeting" and deal.stage in (DealStage.IDENTIFIED, DealStage.CONTACTED):
        deal.stage = DealStage.CONVERSATION
    elif kind == "proposal_sent" and deal.stage not in (DealStage.WON, DealStage.LOST):
        deal.stage = DealStage.PROPOSAL
    session.flush()
    return row


def mark_draft_sent(session: Session, draft_id: uuid.UUID, who: str) -> OutreachDraft:
    """Record that a human manually sent an already-approved draft. Does not transmit."""
    draft = session.get(OutreachDraft, draft_id)
    if draft is None:
        raise EngagementError("draft not found")
    if draft.status != OutreachStatus.APPROVED:
        raise EngagementError("only approved drafts can be marked sent")
    draft.status = OutreachStatus.SENT
    deal = get_or_create_deal(session, draft.opportunity_id)
    if deal.stage == DealStage.IDENTIFIED:
        deal.stage = DealStage.CONTACTED
    opp = session.get(Opportunity, draft.opportunity_id)
    if opp and opp.status not in (OpportunityStatus.WON, OpportunityStatus.LOST):
        opp.status = OpportunityStatus.ENGAGED
    add_interaction(
        session,
        deal.id,
        kind="email_sent",
        summary=f"Marked sent by {who}: {draft.subject or '(no subject)'}",
    )
    session.flush()
    return draft


def list_deals(session: Session, *, follow_ups_only: bool = False) -> list[dict[str, Any]]:
    rows = session.execute(select(Deal).order_by(Deal.updated_at.desc()).limit(200)).scalars()
    out: list[dict[str, Any]] = []
    now = utcnow()
    for d in rows:
        if follow_ups_only and (d.next_follow_up_at is None or d.next_follow_up_at > now):
            continue
        opp = session.get(Opportunity, d.opportunity_id)
        interactions = list(
            session.execute(
                select(Interaction)
                .where(Interaction.deal_id == d.id)
                .order_by(Interaction.occurred_at.desc())
                .limit(20)
            ).scalars()
        )
        out.append(
            {
                "id": d.id,
                "opportunity_id": d.opportunity_id,
                "opportunity_title": opp.title if opp else None,
                "stage": d.stage,
                "value_usd": float(d.value_usd) if d.value_usd is not None else None,
                "notes": d.notes,
                "next_follow_up_at": d.next_follow_up_at,
                "updated_at": d.updated_at,
                "interactions": [
                    {
                        "id": i.id,
                        "kind": i.kind,
                        "summary": i.summary,
                        "occurred_at": i.occurred_at,
                    }
                    for i in interactions
                ],
            }
        )
    return out
