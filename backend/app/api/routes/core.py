import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.analytics import PipelineAnalyticsAgent
from app.connectors.registry import build_connectors
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Evidence, Opportunity, OpportunityEvidence, OutreachDraft, WorkflowRun
from app.models.enums import OpportunityCategory, OutreachStatus, VerificationStatus
from app.orchestrator.runner import enqueue_run
from app.schemas.api import (
    ApprovalRequest,
    DraftOut,
    DraftUpdate,
    OpportunityDetail,
    OpportunityOut,
    RunCreate,
    RunOut,
)
from app.utils import utcnow

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/connectors")
def connectors() -> list[dict[str, object]]:
    return [
        {"name": c.name, "is_mock": c.is_mock, "kinds": sorted(k.value for k in c.kinds)}
        for c in build_connectors(get_settings().enabled_connector_names)
    ]


@router.post("/runs", response_model=RunOut, status_code=202)
def create_run(body: RunCreate, db: Session = Depends(get_db)) -> WorkflowRun:
    return enqueue_run(db, body.kind, {"query": body.query.model_dump()})


@router.get("/runs", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)) -> list[WorkflowRun]:
    return list(
        db.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(50)).scalars()
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    category: OpportunityCategory | None = None, db: Session = Depends(get_db)
) -> list[Opportunity]:
    stmt = select(Opportunity).order_by(Opportunity.last_seen_at.desc()).limit(200)
    if category:
        stmt = stmt.where(Opportunity.category == category)
    return list(db.execute(stmt).scalars())


@router.get("/opportunities/{opp_id}", response_model=OpportunityDetail)
def get_opportunity(opp_id: uuid.UUID, db: Session = Depends(get_db)) -> OpportunityDetail:
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    evs = db.execute(
        select(Evidence)
        .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
        .where(OpportunityEvidence.opportunity_id == opp_id)
    ).scalars()
    data = OpportunityOut.model_validate(opp).model_dump()
    return OpportunityDetail(**data, evidence=list(evs))


@router.post("/opportunities/{opp_id}/verify", response_model=OpportunityOut)
def verify_opportunity(
    opp_id: uuid.UUID, body: ApprovalRequest, db: Session = Depends(get_db)
) -> Opportunity:
    """Human verification: the only path to VERIFIED. Stale evidence cannot be verified."""
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    if opp.verification_status == VerificationStatus.STALE:
        raise HTTPException(409, "stale opportunities cannot be verified; refresh the source")
    opp.verification_status = VerificationStatus.VERIFIED
    db.commit()
    return opp


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    status: OutreachStatus | None = None, db: Session = Depends(get_db)
) -> list[OutreachDraft]:
    stmt = select(OutreachDraft).order_by(OutreachDraft.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(OutreachDraft.status == status)
    return list(db.execute(stmt).scalars())


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def edit_draft(
    draft_id: uuid.UUID, body: DraftUpdate, db: Session = Depends(get_db)
) -> OutreachDraft:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    if draft.status != OutreachStatus.PENDING_APPROVAL:
        raise HTTPException(409, "only pending drafts can be edited")
    draft.body = body.body
    if body.subject is not None:
        draft.subject = body.subject
    db.commit()
    return draft


def _decide(
    db: Session, draft_id: uuid.UUID, approver: str, status: OutreachStatus
) -> OutreachDraft:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    if draft.status != OutreachStatus.PENDING_APPROVAL:
        raise HTTPException(409, f"draft is {draft.status}, not pending approval")
    if status is OutreachStatus.APPROVED and "[" in draft.body:
        raise HTTPException(409, "draft still contains [PLACEHOLDER] text; edit it first")
    draft.status = status
    draft.approved_by = approver
    draft.approved_at = utcnow()
    db.commit()
    return draft


@router.post("/drafts/{draft_id}/approve", response_model=DraftOut)
def approve_draft(
    draft_id: uuid.UUID, body: ApprovalRequest, db: Session = Depends(get_db)
) -> OutreachDraft:
    """Marks approval only. Milestone 1 has no sender, so nothing is ever transmitted."""
    return _decide(db, draft_id, body.approver, OutreachStatus.APPROVED)


@router.post("/drafts/{draft_id}/reject", response_model=DraftOut)
def reject_draft(
    draft_id: uuid.UUID, body: ApprovalRequest, db: Session = Depends(get_db)
) -> OutreachDraft:
    return _decide(db, draft_id, body.approver, OutreachStatus.REJECTED)


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict[str, object]:
    return PipelineAnalyticsAgent(db).run()
