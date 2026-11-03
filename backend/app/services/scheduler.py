"""Safe scheduled discovery: per-source schedules, overlap prevention, rate limits, retry/backoff,
freshness monitoring. Paid providers are NEVER scheduled by default.

A scheduled tick creates at most ONE queued/running scheduled workflow run at a time (no overlap).
Each due source rides in that run; results are folded back into the per-source health row, which
drives retry backoff and the freshness alerts shown to the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import DiscoveryQuery
from app.core.config import Settings
from app.models import SourceSchedule, WorkflowRun
from app.models.enums import RunStatus
from app.orchestrator.runner import enqueue_run
from app.utils import utcnow

CANDIDATES = "agency_candidates"  # pseudo-source: candidate discovery + partner-page reading


@dataclass(frozen=True)
class Default:
    channel: str  # A active requests | B agencies | C proactive buyers
    interval_minutes: int
    paid: bool = False


DEFAULTS: dict[str, Default] = {
    "ted_api": Default("A", 360),
    "uk_contracts_finder": Default("A", 360),
    "uk_find_a_tender": Default("A", 360),
    "canadabuys": Default("A", 720),
    "worldbank_api": Default("A", 720),
    "sam_gov": Default("A", 720),
    "hn_freelancer_threads": Default("A", 360),
    CANDIDATES: Default("B", 1440),
    "public_partner_pages": Default("B", 1440),
    "public_announcement_feeds": Default("B", 720),
    "wikidata_agencies": Default("B", 10080),
    "hn_hiring_triggers": Default("C", 720),
    "ted_planning_notices": Default("C", 720),
    # paid: seeded DISABLED, never enabled by the scheduler on its own
    "intent_search": Default("A", 1440, paid=True),
    "apify_intent_search": Default("A", 1440, paid=True),
}


def ensure_schedules(session: Session, settings: Settings) -> list[SourceSchedule]:
    """Create missing rows for every configured connector. Paid ones start disabled."""
    have = {s.connector: s for s in session.execute(select(SourceSchedule)).scalars()}
    names = set(settings.enabled_connector_names) | {CANDIDATES}
    for name in sorted(names):
        d = DEFAULTS.get(name)
        if d is None:
            paid = name.startswith("apify")
            d = Default("A", 1440, paid=paid)
        if d.paid is False and name.startswith("mock"):
            continue  # fixtures are never scheduled
        if name not in have:
            row = SourceSchedule(
                connector=name,
                channel=d.channel,
                interval_minutes=d.interval_minutes,
                enabled=not d.paid,
                paid=d.paid,
                next_run_at=None,  # due immediately
            )
            session.add(row)
            have[name] = row
    session.flush()
    return [have[n] for n in sorted(have)]


def _aware(dt: datetime | None) -> datetime | None:
    from datetime import UTC

    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def due_sources(
    session: Session, settings: Settings, now: datetime | None = None
) -> list[SourceSchedule]:
    now = now or utcnow()
    out: list[SourceSchedule] = []
    for s in session.execute(select(SourceSchedule).order_by(SourceSchedule.connector)).scalars():
        if not s.enabled:
            continue
        if s.paid and not settings.scheduler_allow_paid:
            continue  # paid providers need the explicit global flag too
        nxt, last = _aware(s.next_run_at), _aware(s.last_attempt_at)
        if nxt is not None and nxt > now:
            continue
        if last is not None and now - last < timedelta(minutes=s.min_gap_minutes):
            continue  # per-provider rate limit
        out.append(s)
    return out


def _active_scheduled_run(session: Session) -> WorkflowRun | None:
    return (
        session.execute(
            select(WorkflowRun).where(
                WorkflowRun.trigger == "scheduled",
                WorkflowRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
            )
        )
        .scalars()
        .first()
    )


def schedule_tick(
    session: Session, settings: Settings, now: datetime | None = None
) -> WorkflowRun | None:
    """Enqueue one scheduled run for everything due. No-op while a scheduled run is in flight."""
    if not settings.scheduler_enabled:
        return None
    now = now or utcnow()
    ensure_schedules(session, settings)
    if _active_scheduled_run(session) is not None:
        return None  # overlap prevention
    due = due_sources(session, settings, now)
    if not due:
        session.commit()
        return None
    names = [s.connector for s in due if s.connector != CANDIDATES]
    query = DiscoveryQuery(
        connector_names=names, include_candidates=any(s.connector == CANDIDATES for s in due)
    )
    run = enqueue_run(
        session,
        "full_discovery",
        {"query": query.model_dump(), "scheduled_sources": [s.connector for s in due]},
        trigger="scheduled",
    )
    for s in due:
        s.last_attempt_at = now
        s.next_run_at = now + timedelta(minutes=s.interval_minutes)
    session.commit()
    return run


def record_run_results(session: Session, run: WorkflowRun, now: datetime | None = None) -> None:
    """Fold a finished scheduled run back into each source's health and retry/backoff state."""
    wanted = (run.input or {}).get("scheduled_sources") or []
    if not wanted or run.status not in (RunStatus.SUCCEEDED, RunStatus.FAILED):
        return
    now = now or utcnow()
    per: dict[str, Any] = ((run.state or {}).get("outcome") or {}).get("per_source", {})
    for s in session.execute(
        select(SourceSchedule).where(SourceSchedule.connector.in_(wanted))
    ).scalars():
        key = "candidate_pages" if s.connector == CANDIDATES else s.connector
        info = per.get(key) or per.get(f"discoverer:{s.connector}")
        if info is None:
            status, err = (
                ("failed", run.error) if run.status == RunStatus.FAILED else ("skipped", None)
            )
        else:
            status, err = info["status"], info.get("error")
        s.last_status = status
        if status == "skipped":
            continue
        if status in ("failed", "blocked"):
            s.consecutive_failures += 1
            s.last_error = (err or "failed")[:300]
            if s.consecutive_failures <= s.max_retries:  # retry sooner, exponential, capped
                retry = timedelta(
                    minutes=min(s.interval_minutes, 5 * 2 ** (s.consecutive_failures - 1))
                )
                s.next_run_at = now + retry
        else:
            s.consecutive_failures, s.last_error = 0, None
            s.last_success_at = now
            s.last_retrieved = int(info.get("retrieved", 0)) if info else 0
            s.last_new = int(info.get("created", 0)) if info else 0
    session.commit()


def freshness(s: SourceSchedule, now: datetime | None = None) -> str:
    """ok | stale (no success for 2 intervals) | never_succeeded | failing | disabled"""
    now = now or utcnow()
    if not s.enabled:
        return "disabled"
    if s.consecutive_failures >= 3:
        return "failing"
    ok = _aware(s.last_success_at)
    if ok is None:
        return "never_succeeded"
    return "stale" if now - ok > timedelta(minutes=2 * s.interval_minutes) else "ok"
