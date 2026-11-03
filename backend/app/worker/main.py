import signal
import time

from app.connectors.registry import build_connectors
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_session_factory
from app.orchestrator.runner import claim_next_run, execute_run, new_owner
from app.services.scheduler import record_run_results, schedule_tick

log = get_logger("worker")
_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    connectors = build_connectors(settings.enabled_connector_names, settings)
    factory = get_session_factory()
    owner = new_owner()
    log.info("worker_started", owner=owner, connectors=[c.name for c in connectors])
    while _running:
        with factory() as session:
            if settings.scheduler_enabled:
                try:
                    schedule_tick(session, settings)
                except Exception as exc:  # scheduling must never take the worker down
                    session.rollback()
                    log.error("scheduler_tick_failed", error=str(exc)[:200])
            run = claim_next_run(session, owner, settings)
            if run is None:
                time.sleep(settings.worker_poll_interval_seconds)
                continue
            done = execute_run(session, run.id, connectors, settings)
            if done.trigger == "scheduled":
                record_run_results(session, done)
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
