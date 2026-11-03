"""Autonomous-operation surface: source schedules + freshness, the new-opportunity review queue
and the provider-run ledger. Read access for viewers; changing a schedule is admin only."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import Identity, require_role
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Evidence, Opportunity, OpportunityEvidence, ProviderRun, SourceSchedule
from app.models.enums import Triage
from app.services.commercial_class import commercial_class
from app.services.scheduler import ensure_schedules, freshness

router = APIRouter(dependencies=[Depends(require_role("viewer"))])
ADMIN = Depends(require_role("admin"))


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=43200)
    min_gap_minutes: int | None = Field(default=None, ge=1, le=43200)


def _row(s: SourceSchedule) -> dict[str, Any]:
    return {
        "connector": s.connector,
        "channel": s.channel,
        "enabled": s.enabled,
        "paid": s.paid,
        "interval_minutes": s.interval_minutes,
        "min_gap_minutes": s.min_gap_minutes,
        "next_run_at": s.next_run_at,
        "last_attempt_at": s.last_attempt_at,
        "last_success_at": s.last_success_at,
        "last_status": s.last_status,
        "last_error": s.last_error,
        "consecutive_failures": s.consecutive_failures,
        "last_retrieved": s.last_retrieved,
        "last_new": s.last_new,
        "freshness": freshness(s),
    }


@router.get("/schedules")
def schedules(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    rows = ensure_schedules(db, settings)
    db.commit()
    return {
        "scheduler_enabled": settings.scheduler_enabled,
        "paid_scheduling_allowed": settings.scheduler_allow_paid,
        "sources": [_row(s) for s in rows],
    }


@router.patch("/schedules/{connector}")
def patch_schedule(
    connector: str, body: SchedulePatch, who: Identity = ADMIN, db: Session = Depends(get_db)
) -> dict[str, Any]:
    ensure_schedules(db, get_settings())
    s = db.execute(
        select(SourceSchedule).where(SourceSchedule.connector == connector)
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, "unknown source")
    if body.enabled and s.paid and not get_settings().scheduler_allow_paid:
        raise HTTPException(
            409, "paid providers are never scheduled until SCHEDULER_ALLOW_PAID is set by the owner"
        )
    for field in ("enabled", "interval_minutes", "min_gap_minutes"):
        v = getattr(body, field)
        if v is not None:
            setattr(s, field, v)
    db.commit()
    return _row(s)


@router.get("/review-queue")
def review_queue(limit: int = 50, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Newly found, not-yet-reviewed opportunities worth a human's attention, best first.
    Mock fixtures are excluded; rejected and already-verified records are not queued."""
    out: list[dict[str, Any]] = []
    stmt = (
        select(Opportunity)
        .where(
            Opportunity.triage.in_([Triage.ACTIONABLE, Triage.NEEDS_VERIFICATION]),
            Opportunity.verification_status == "unverified",
        )
        .order_by(Opportunity.first_seen_at.desc())
        .limit(500)
    )
    for o in db.execute(stmt).scalars():
        providers = set(
            db.execute(
                select(Evidence.provider)
                .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
                .where(OpportunityEvidence.opportunity_id == o.id)
            ).scalars()
        )
        if not providers or all(p.startswith("mock") for p in providers):
            continue
        pursuability = (o.assessment or {}).get("pursuability", "unassessed")
        if pursuability not in ("pursuable", "needs_verification"):
            continue
        out.append(
            {
                "opportunity_id": str(o.id),
                "title": o.title,
                "channel": o.channel,
                "pursuability": pursuability,
                "commercial_class": commercial_class(
                    category=o.category, triage=o.triage, channel=o.channel
                ),
                "first_seen_at": o.first_seen_at,
                "missing": (o.assessment or {}).get("missing", [])[:4],
                "next_action": (o.assessment or {}).get("next_action"),
                "sources": sorted(providers),
            }
        )
    out.sort(
        key=lambda r: (r["pursuability"] != "pursuable", str(r["first_seen_at"])), reverse=False
    )
    return out[:limit]


@router.get("/provider-runs")
def provider_runs(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r.id),
            "provider": r.provider,
            "actor": r.actor,
            "external_id": r.external_id,
            "status": r.status,
            "max_charge_usd": float(r.max_charge_usd),
            "reported_usd": None if r.reported_usd is None else float(r.reported_usd),
            "event_usd": None if r.event_usd is None else float(r.event_usd),
            "charged_usd": None if r.charged_usd is None else float(r.charged_usd),
            "cost_basis": r.cost_basis,
            "created_at": r.created_at,
        }
        for r in db.execute(
            select(ProviderRun).order_by(ProviderRun.created_at.desc()).limit(100)
        ).scalars()
    ]
