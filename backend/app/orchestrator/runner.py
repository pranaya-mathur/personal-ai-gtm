import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import DiscoveryConnector
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import WorkflowRun
from app.models.enums import RunStatus
from app.orchestrator.graph import WorkflowState, build_graph
from app.services.spend import SpendGuard
from app.utils import utcnow

log = get_logger(__name__)
STALE_LOCK = timedelta(minutes=10)

DISCOVERY_WORKFLOW = "full_discovery"


def enqueue_run(session: Session, kind: str, input_: dict[str, Any]) -> WorkflowRun:
    run = WorkflowRun(kind=kind, input=input_, state={})
    session.add(run)
    session.commit()
    return run


def claim_next_run(session: Session) -> WorkflowRun | None:
    """Atomically claim one queued run; also recovers runs whose worker died mid-flight."""
    stale_cutoff = utcnow() - STALE_LOCK
    for r in session.execute(
        select(WorkflowRun).where(
            WorkflowRun.status == RunStatus.RUNNING, WorkflowRun.locked_at < stale_cutoff
        )
    ).scalars():
        r.status = RunStatus.QUEUED
    run = (
        session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.status == RunStatus.QUEUED)
            .order_by(WorkflowRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if run is None:
        session.commit()
        return None
    run.status = RunStatus.RUNNING
    run.locked_at = utcnow()
    run.started_at = run.started_at or utcnow()
    run.attempts += 1
    session.commit()
    return run


def _jsonable(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(state, default=str))
    return result


def execute_run(
    session: Session,
    run_id: uuid.UUID,
    connectors: list[DiscoveryConnector],
    settings: Settings,
) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    assert run is not None
    try:
        graph = build_graph(session, connectors, SpendGuard(session, settings))
        state: WorkflowState = (
            WorkflowState(**run.state)  # type: ignore[typeddict-item]
            if run.state.get("completed_nodes")
            else WorkflowState(run_id=str(run.id), query=run.input.get("query", {}))
        )
        state["run_id"] = str(run.id)
        last: dict[str, Any] = dict(state)
        for snapshot in graph.stream(state, stream_mode="values"):
            last = snapshot
            run.state = _jsonable(snapshot)  # persist after every node
            run.locked_at = utcnow()
            session.commit()
        run.state = _jsonable(last)
        run.status = RunStatus.SUCCEEDED
        run.error = None
        run.finished_at = utcnow()
        session.commit()
        log.info("run_succeeded", run_id=str(run.id))
    except Exception as exc:
        session.rollback()
        run = session.get(WorkflowRun, run_id)
        assert run is not None
        run.error = f"{type(exc).__name__}: {exc}"
        terminal = run.attempts >= settings.worker_max_attempts
        run.status = RunStatus.FAILED if terminal else RunStatus.QUEUED
        run.finished_at = utcnow() if terminal else None
        session.commit()
        log.error("run_failed", run_id=str(run_id), error=run.error, terminal=terminal)
    return run
