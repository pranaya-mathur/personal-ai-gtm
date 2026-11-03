"""Fit analysis, personalised outreach drafts, proposals and onboarding. Every commitment is
human-approved; nothing is sent from here."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import Identity, require_role
from app.db.session import get_db
from app.models import Contact, Deal, OnboardingItem, Opportunity, OutreachDraft, Proposal
from app.models.enums import OutreachStatus
from app.services import capabilities as caps
from app.services.fit import PIPELINE, opportunity_fit, outreach_from_fit, pipeline_stage
from app.services.proposals import (
    ProposalError,
    approve_proposal,
    edit_proposal,
    ensure_onboarding,
    mark_sent,
    prepare_proposal,
    proposal_dict,
    toggle_onboarding,
)

router = APIRouter(dependencies=[Depends(require_role("viewer"))])
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))


class ProjectIn(BaseModel):
    title: str = Field(max_length=300)
    verifiable_reference: str = Field(max_length=1000)
    summary: str | None = Field(default=None, max_length=2000)


class AreaIn(BaseModel):
    key: str = Field(max_length=64)
    label: str = Field(max_length=200)
    terms: list[str] = []
    projects: list[ProjectIn] = []


class ProposalEdit(BaseModel):
    body: str | None = None
    price_text: str | None = Field(default=None, max_length=256)


class Toggle(BaseModel):
    done: bool


def _opp(db: Session, opp_id: uuid.UUID) -> Opportunity:
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    return opp


@router.get("/capabilities")
def get_capabilities(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return caps.get_profile(db)


@router.put("/capabilities")
def put_capabilities(
    areas: list[AreaIn], who: Identity = ADMIN, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Only the admin edits the profile; a project reference needs a verifiable link/contact."""
    try:
        out = caps.save_profile(db, [a.model_dump() for a in areas], who.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return out


@router.get("/opportunities/{opp_id}/fit")
def get_fit(opp_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return opportunity_fit(db, _opp(db, opp_id))


@router.post("/opportunities/{opp_id}/draft", status_code=201)
def create_draft(
    opp_id: uuid.UUID, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Prepare a personalised outreach draft (pending approval; never sent from here)."""
    opp = _opp(db, opp_id)
    if opp.triage == "rejected":
        raise HTTPException(409, "rejected opportunities get no outreach")
    existing = (
        db.execute(
            select(OutreachDraft).where(
                OutreachDraft.opportunity_id == opp.id,
                OutreachDraft.status == OutreachStatus.PENDING_APPROVAL,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(409, "a draft is already pending approval for this opportunity")
    fit = opportunity_fit(db, opp)
    contact = (
        db.execute(select(Contact).where(Contact.organization_id == opp.organization_id))
        .scalars()
        .first()
        if opp.organization_id
        else None
    )
    subject, body = outreach_from_fit(fit, contact.full_name if contact else None)
    draft = OutreachDraft(
        opportunity_id=opp.id,
        contact_id=contact.id if contact else None,
        subject=subject,
        body=body,
        status=OutreachStatus.PENDING_APPROVAL,
    )
    db.add(draft)
    db.commit()
    return {
        "draft_id": str(draft.id),
        "subject": subject,
        "status": draft.status,
        "prepared_by": who.name,
    }


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = {s: 0 for s in [*PIPELINE, "rejected"]}
    items: list[dict[str, Any]] = []
    for opp in db.execute(select(Opportunity)).scalars():
        if opp.channel and opp.assessment is not None:
            st = pipeline_stage(db, opp)
            counts[st] += 1
            if st not in ("rejected", "discovered"):
                items.append(
                    {
                        "opportunity_id": str(opp.id),
                        "title": opp.title,
                        "stage": st,
                        "channel": opp.channel,
                    }
                )
    return {"stages": PIPELINE, "counts": counts, "active": items}


@router.post("/opportunities/{opp_id}/proposal", status_code=201)
def create_proposal(
    opp_id: uuid.UUID, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> dict[str, Any]:
    opp = _opp(db, opp_id)
    if opp.triage == "rejected":
        raise HTTPException(409, "rejected opportunities get no proposal")
    row = prepare_proposal(db, opp, who.name)
    db.commit()
    return proposal_dict(row)


@router.get("/proposals")
def list_proposals(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        proposal_dict(p)
        for p in db.execute(
            select(Proposal).order_by(Proposal.created_at.desc()).limit(100)
        ).scalars()
    ]


@router.patch("/proposals/{proposal_id}", dependencies=[OPERATOR])
def patch_proposal(
    proposal_id: uuid.UUID, body: ProposalEdit, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        row = edit_proposal(db, proposal_id, body.body, body.price_text)
    except ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return proposal_dict(row)


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: uuid.UUID, who: Identity = ADMIN, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        row = approve_proposal(
            db, proposal_id, who.name
        )  # the authenticated admin, not a body field
    except ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return proposal_dict(row)


@router.post("/proposals/{proposal_id}/mark-sent", dependencies=[OPERATOR])
def proposal_sent(proposal_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = mark_sent(db, proposal_id)
    except ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return proposal_dict(row)


def _item(i: OnboardingItem) -> dict[str, Any]:
    return {"id": str(i.id), "label": i.label, "done": i.done, "done_by": i.done_by,
            "done_at": i.done_at.isoformat() if i.done_at else None}  # fmt: skip


@router.get("/deals/{deal_id}/onboarding")
def get_onboarding(deal_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        _item(i)
        for i in db.execute(
            select(OnboardingItem)
            .where(OnboardingItem.deal_id == deal_id)
            .order_by(OnboardingItem.position)
        ).scalars()
    ]


@router.post("/deals/{deal_id}/onboarding", status_code=201, dependencies=[OPERATOR])
def start_onboarding(deal_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")
    if deal.stage != "won":
        raise HTTPException(409, "onboarding starts only after the deal is marked won")
    items = ensure_onboarding(db, deal_id)
    db.commit()
    return [_item(i) for i in items]


@router.patch("/onboarding/{item_id}")
def tick(
    item_id: uuid.UUID, body: Toggle, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        row = toggle_onboarding(db, item_id, body.done, who.name)
    except ProposalError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return _item(row)


__all__ = ["router", "datetime"]
