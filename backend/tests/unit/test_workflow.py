from sqlalchemy import func, select

from app.connectors.registry import build_connectors
from app.models import Contact, Evidence, Opportunity, OutreachDraft, WorkflowRun
from app.orchestrator.graph import NODE_ORDER
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run


def _run_once(session, settings):  # type: ignore[no-untyped-def]
    connectors = build_connectors(["mock_opportunities", "mock_agencies"])
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    return execute_run(session, run.id, connectors, settings)


def test_full_workflow(session, settings) -> None:  # type: ignore[no-untyped-def]
    run = _run_once(session, settings)
    assert run.status == "succeeded"
    assert run.state["completed_nodes"] == NODE_ORDER

    opps = {o.title: o for o in session.execute(select(Opportunity)).scalars()}
    assert len(opps) == 5
    cats = {o.category for o in opps.values()}
    assert cats == {"confirmed_demand", "verified_partnership", "speculative_prospect"}

    stale = opps["Document-processing automation pilot"]
    assert stale.verification_status == "stale" and stale.status == "disqualified"

    # every opportunity keeps its evidence; none is auto-verified
    assert all(o.verification_status != "verified" for o in opps.values())
    ev = session.execute(select(Evidence)).scalars().all()
    assert all(e.source_url and e.original_text for e in ev)

    # contact exists only because the source contained it
    assert [c.full_name for c in session.execute(select(Contact)).scalars()] == ["Pat Example"]

    drafts = session.execute(select(OutreachDraft)).scalars().all()
    assert drafts and all(d.status == "pending_approval" for d in drafts)
    assert all(d.approved_by is None for d in drafts)
    assert run.state["metrics"]["drafts_pending_approval"] == len(drafts)


def test_rerun_deduplicates(session, settings) -> None:  # type: ignore[no-untyped-def]
    _run_once(session, settings)
    n_opp = session.execute(select(func.count()).select_from(Opportunity)).scalar_one()
    n_ev = session.execute(select(func.count()).select_from(Evidence)).scalar_one()
    n_dr = session.execute(select(func.count()).select_from(OutreachDraft)).scalar_one()
    _run_once(session, settings)
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == n_opp
    assert session.execute(select(func.count()).select_from(Evidence)).scalar_one() == n_ev
    assert session.execute(select(func.count()).select_from(OutreachDraft)).scalar_one() == n_dr


def test_failed_run_retries_then_fails(session, settings) -> None:  # type: ignore[no-untyped-def]
    class Boom:
        name = "boom"
        is_mock = True
        from app.connectors.base import SignalKind

        kinds = frozenset({SignalKind.PROJECT_REQUEST})

        def estimate_cost(self, q):  # type: ignore[no-untyped-def]
            raise RuntimeError("provider exploded")

        def discover(self, q):  # type: ignore[no-untyped-def]
            raise RuntimeError

    enqueue_run(session, "full_discovery", {"query": {}})
    for expected in ("queued", "failed"):  # worker_max_attempts == 2
        run = claim_next_run(session)
        assert run is not None
        out = execute_run(session, run.id, [Boom()], settings)  # type: ignore[list-item]
        assert out.status == expected
    assert "provider exploded" in (session.execute(select(WorkflowRun)).scalar_one().error or "")
    assert claim_next_run(session) is None
