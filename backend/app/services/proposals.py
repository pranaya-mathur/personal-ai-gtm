"""Proposal preparation and the onboarding checklist. Human-approved at every commitment point.

A proposal is generated from stored evidence and the fit analysis. Anything the owner must decide
(price, timeline, experience) is a [PLACEHOLDER]; approval is refused while any remain.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deal, OnboardingItem, Opportunity, Proposal
from app.services.fit import opportunity_fit
from app.utils import utcnow

ONBOARDING_DEFAULT = [
    "Signed agreement / statement of work (reviewed and approved by the owner)",
    "Scope, milestones and acceptance criteria confirmed in writing",
    "Payment terms, invoicing details and currency agreed",
    "NDA, data-protection and security requirements confirmed",
    "Access to data/systems arranged through the client's own secure process",
    "Kick-off meeting scheduled; owners and communication cadence agreed",
    "Delivery repository/environment and tracking set up",
]


class ProposalError(ValueError):
    pass


def prepare_proposal(session: Session, opp: Opportunity, who: str) -> Proposal:
    fit = opportunity_fit(session, opp)
    ev = fit["evidence"][0] if fit["evidence"] else None
    caps = fit["matched_capabilities"]
    verified = [p for c in caps for p in c["projects"]]
    experience = (
        "\n".join(f"- {p['title']} — reference: {p['verifiable_reference']}" for p in verified)
        if verified
        else "[RELEVANT EXPERIENCE - none on file. Add approved, verifiable references; do not invent]"
    )
    price = opp.budget_text
    phases = "\n".join(f"{i}. {s}" for i, s in enumerate(fit["what_i_could_deliver"], 1))
    body = (
        f"# Proposal: {opp.title[:140]}\n\n"
        f"Prepared for: {fit['organization'] or '[CLIENT NAME]'}\n\n"
        "## Understanding of the need\n"
        f"{fit['problem']}\n"
        + (f"\nSource: {ev['url']}\n" if ev else "")
        + f"\n## Proposed approach\n{phases}\n\n"
        "## Deliverables\n- Working system for the agreed scope, documented\n"
        "- Evaluation results on real examples\n- Handover session\n\n"
        "## Assumptions (to confirm with the client)\n"
        + "\n".join(f"- {m}" for m in (fit["missing"] or ["None recorded"]))
        + "\n- Access to representative data and a named business contact\n\n"
        "## Out of scope\n- Anything not listed above; changes are scoped separately\n\n"
        f"## Experience\n{experience}\n\n"
        f"## Timeline\n[TIMELINE - to be estimated by the owner after discovery]\n\n"
        f"## Price\n{('Client-stated budget: ' + price + '. ') if price else ''}"
        "[PRICE - set by the owner; no figure is suggested here]\n"
    )
    scope = {
        "phases": fit["what_i_could_deliver"],
        "assumptions": fit["missing"],
        "capabilities": [c["key"] for c in caps],
        "stated_budget": price,
    }
    row = Proposal(
        opportunity_id=opp.id,
        title=f"Proposal: {opp.title[:200]}",
        scope=scope,
        body=body,
        price_text=None,
        status="draft",
        prepared_by=who,
    )
    session.add(row)
    session.flush()
    return row


def edit_proposal(
    session: Session, proposal_id: uuid.UUID, body: str | None, price_text: str | None
) -> Proposal:
    row = session.get(Proposal, proposal_id)
    if row is None:
        raise ProposalError("proposal not found")
    if row.status != "draft":
        raise ProposalError("only draft proposals can be edited")
    if body is not None:
        row.body = body
    if price_text is not None:
        row.price_text = price_text.strip() or None
    session.flush()
    return row


def approve_proposal(session: Session, proposal_id: uuid.UUID, who: str) -> Proposal:
    row = session.get(Proposal, proposal_id)
    if row is None:
        raise ProposalError("proposal not found")
    if row.status != "draft":
        raise ProposalError(f"proposal is {row.status}")
    if "[" in row.body or not row.price_text:
        raise ProposalError("replace every [PLACEHOLDER] and set the price before approval")
    row.status, row.approved_by, row.approved_at = "approved", who, utcnow()
    session.flush()
    return row


def mark_sent(session: Session, proposal_id: uuid.UUID) -> Proposal:
    """Records that the OWNER sent it by their own means; nothing is transmitted from here."""
    row = session.get(Proposal, proposal_id)
    if row is None:
        raise ProposalError("proposal not found")
    if row.status != "approved":
        raise ProposalError("only an approved proposal can be marked sent")
    row.status = "sent"
    session.flush()
    return row


def ensure_onboarding(session: Session, deal_id: uuid.UUID) -> list[OnboardingItem]:
    deal = session.get(Deal, deal_id)
    if deal is None:
        raise ProposalError("deal not found")
    existing = {
        i.label: i
        for i in session.execute(
            select(OnboardingItem).where(OnboardingItem.deal_id == deal_id)
        ).scalars()
    }
    for pos, label in enumerate(ONBOARDING_DEFAULT):
        if label not in existing:
            row = OnboardingItem(deal_id=deal_id, label=label, position=pos)
            session.add(row)
            existing[label] = row
    session.flush()
    return sorted(existing.values(), key=lambda i: i.position)


def toggle_onboarding(session: Session, item_id: uuid.UUID, done: bool, who: str) -> OnboardingItem:
    row = session.get(OnboardingItem, item_id)
    if row is None:
        raise ProposalError("item not found")
    row.done = done
    row.done_by, row.done_at = (who, utcnow()) if done else (None, None)
    session.flush()
    return row


def proposal_dict(p: Proposal) -> dict[str, Any]:
    return {
        "id": str(p.id), "opportunity_id": str(p.opportunity_id), "title": p.title, "status": p.status,
        "body": p.body, "price_text": p.price_text, "prepared_by": p.prepared_by,
        "approved_by": p.approved_by, "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "has_placeholders": "[" in p.body or not p.price_text,
    }  # fmt: skip
