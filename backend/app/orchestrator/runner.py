import json
import os
import socket
import threading
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.connectors.base import DiscoveryConnector
from app.core.config import Settings
from app.core.logging import get_logger
from app.llm import LLMClient
from app.models import WorkflowRun
from app.models.enums import RunStatus
from app.orchestrator import outcome as outcomes
from app.orchestrator.graph import WorkflowState, build_graph
from app.services.source_check import SourceChecker
from app.services.spend import SpendGuard
from app.utils import utcnow

log = get_logger(__name__)
LEGACY_STALE_LOCK = timedelta(minutes=10)  # only for runs claimed before leases existed

DISCOVERY_WORKFLOW = "full_discovery"


class LeaseLost(Exception):
    """Another worker took over this run (its lease lapsed); this worker must stop writing."""


def new_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def enqueue_run(
    session: Session, kind: str, input_: dict[str, Any], trigger: str = "manual"
) -> WorkflowRun:
    run = WorkflowRun(kind=kind, input=input_, state={}, trigger=trigger)
    session.add(run)
    session.commit()
    return run


def claim_next_run(
    session: Session, owner: str | None = None, settings: Settings | None = None
) -> WorkflowRun | None:
    """Atomically claim one queued run under a renewable lease. A RUNNING run is recovered only
    when its lease has lapsed (the worker died or stopped heartbeating), never merely because it
    has been busy for a long time."""
    s = settings or Settings(database_url="sqlite://")
    now = utcnow()
    owner = owner or new_owner()
    lapsed = session.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.status == RunStatus.RUNNING,
            (WorkflowRun.lease_expires_at < now)
            | (
                WorkflowRun.lease_expires_at.is_(None)
                & (WorkflowRun.locked_at < now - LEGACY_STALE_LOCK)
            ),
        )
        .with_for_update(skip_locked=True)
    ).scalars()
    for r in lapsed:
        if r.attempts >= s.worker_max_attempts:
            # a run that keeps killing its workers must stop; never loop on billable work
            r.status, r.finished_at, r.outcome = RunStatus.FAILED, now, outcomes.FAILED
            r.error = f"worker lease lapsed on attempt {r.attempts}; giving up"
        else:
            r.status = RunStatus.QUEUED
        r.lease_owner = r.lease_expires_at = None
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
    run.locked_at = now
    run.lease_owner = owner
    run.lease_expires_at = now + timedelta(seconds=s.run_lease_seconds)
    run.started_at = run.started_at or now
    run.attempts += 1
    session.commit()
    return run


def _jsonable(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(state, default=str))
    return result


class _Heartbeat:
    """Renews the lease from its own connection while a (possibly very long) node executes."""

    def __init__(self, session: Session, run_id: uuid.UUID, owner: str | None, settings: Settings):
        self.bind = session.get_bind()
        self.run_id, self.owner, self.settings = run_id, owner, settings
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # in-memory SQLite cannot be shared across threads; leases are still renewed per node
        if self.owner is None or self.bind.dialect.name != "postgresql":
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="run-heartbeat")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.run_heartbeat_seconds):
            try:
                with Session(self.bind) as s:
                    n = s.execute(
                        update(WorkflowRun)
                        .where(
                            WorkflowRun.id == self.run_id,
                            WorkflowRun.lease_owner == self.owner,
                            WorkflowRun.status == RunStatus.RUNNING,
                        )
                        .values(
                            lease_expires_at=utcnow()
                            + timedelta(seconds=self.settings.run_lease_seconds)
                        )
                    ).rowcount
                    s.commit()
                if n == 0:
                    self.lost.set()
                    return
            except Exception as exc:  # a transient DB error must not kill the run; lease retries
                log.warning("heartbeat_failed", run_id=str(self.run_id), error=str(exc)[:120])

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _renew(session: Session, run: WorkflowRun, settings: Settings) -> None:
    run.locked_at = utcnow()
    if run.lease_owner is not None:
        run.lease_expires_at = utcnow() + timedelta(seconds=settings.run_lease_seconds)


def execute_run(
    session: Session,
    run_id: uuid.UUID,
    connectors: list[DiscoveryConnector],
    settings: Settings,
    checker: SourceChecker | None = None,
    llm: LLMClient | None = None,
) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    assert run is not None
    owner = run.lease_owner
    beat = _Heartbeat(session, run_id, owner, settings)
    beat.start()
    try:
        graph = build_graph(
            session, connectors, SpendGuard(session, settings), settings, checker, llm
        )
        state: WorkflowState = (
            WorkflowState(**run.state)  # type: ignore[typeddict-item]
            if run.state.get("completed_nodes")
            else WorkflowState(run_id=str(run.id), query=run.input.get("query", {}))
        )
        state["run_id"] = str(run.id)
        last: dict[str, Any] = dict(state)
        for snapshot in graph.stream(state, stream_mode="values"):
            session.refresh(run, ["status", "lease_owner"])
            if owner is not None and (run.lease_owner != owner or beat.lost.is_set()):
                raise LeaseLost(f"run {run.id} was taken over by {run.lease_owner}")
            if run.status == RunStatus.CANCELLED:
                run.finished_at = utcnow()
                run.outcome = outcomes.CANCELLED
                run.lease_owner = run.lease_expires_at = None
                session.commit()
                log.info("run_cancelled", run_id=str(run.id))
                return run
            started = run.started_at
            if (
                started is not None
                and (utcnow() - started).total_seconds() > settings.run_timeout_seconds
            ):
                raise TimeoutError(f"run exceeded {settings.run_timeout_seconds}s")
            last = snapshot
            run.state = _jsonable(snapshot)  # persist after every node
            _renew(session, run, settings)
            session.commit()
        summary = outcomes.summarize(last)
        last["outcome"] = summary
        run.state = _jsonable(last)
        run.outcome = summary["outcome"]
        run.finished_at = utcnow()
        run.lease_owner = run.lease_expires_at = None
        if summary["outcome"] == outcomes.FAILED:
            run.status = RunStatus.FAILED  # terminal: the workflow resumes past discovery
            run.error = summary["detail"]
        else:
            run.status = RunStatus.SUCCEEDED
            run.error = None
        session.commit()
        log.info("run_finished", run_id=str(run.id), status=run.status, outcome=run.outcome)
    except LeaseLost as exc:
        session.rollback()  # the new owner is in charge; write nothing
        log.warning("run_lease_lost", run_id=str(run_id), error=str(exc))
        return session.get(WorkflowRun, run_id)  # type: ignore[return-value]
    except Exception as exc:
        session.rollback()
        run = session.get(WorkflowRun, run_id)
        assert run is not None
        if owner is not None and run.lease_owner not in (owner, None):
            return run  # someone else owns it now: do not touch it
        run.error = f"{type(exc).__name__}: {exc}"
        if run.status == RunStatus.CANCELLED:
            return run
        terminal = run.attempts >= settings.worker_max_attempts or isinstance(exc, TimeoutError)
        run.status = RunStatus.FAILED if terminal else RunStatus.QUEUED
        run.finished_at = utcnow() if terminal else None
        run.outcome = outcomes.FAILED if terminal else None
        run.lease_owner = run.lease_expires_at = None
        session.commit()
        log.error("run_failed", run_id=str(run_id), error=run.error, terminal=terminal)
    finally:
        beat.stop()
    return run
