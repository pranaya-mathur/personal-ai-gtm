"""Regression tests for the production-hardening audit (findings A-I)."""

import uuid
from datetime import UTC, datetime, timedelta

from app.connectors.base import RawFinding, SignalKind
from app.core.config import Settings
from app.models import Opportunity
from app.services.ingest import ingest_finding
from app.services.intake import intake_lead
from app.services.triage import apply_assessment, assess, assess_opportunity

NOW = datetime.now(UTC)
S = Settings(database_url="sqlite://")


def _a(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        title="Generative AI assistant", text="Design and deploy an AI assistant, remote work.",
        category="confirmed_demand", org_kind=None, source_status="open",
        deadline=NOW + timedelta(days=10), budget_text=None, contact_route="a@b.example",
        known_contacts=0, settings=S,
    )  # fmt: skip
    base.update(kw)
    return assess(**base)


# ---------------------------------------------------------------- Finding F
def test_open_flag_cannot_outlive_a_passed_deadline() -> None:
    r = _a(source_status="open", deadline=NOW - timedelta(days=2))
    assert r.triage == "rejected" and "closed" in (r.rejection_reason or "")
    assert "passed" in r.criteria["active"]["detail"]


def test_unresolved_delivery_model_is_not_pursuable() -> None:
    r = _a(text="Design and deploy an AI assistant.")
    assert r.triage == "needs_verification" and r.pursuability == "needs_verification"
    assert r.criteria["remote_or_subcontract"]["result"] == "unknown"


def test_formal_procurement_needs_supplier_eligibility() -> None:
    r = _a(formal_procurement=True)
    assert r.triage == "needs_verification"
    assert r.criteria["procurement_complexity"]["result"] == "unknown"
    ok = _a(formal_procurement=True, attested={"supplier_eligibility": {"by": "olga"}})
    assert ok.triage == "actionable" and ok.pursuability == "pursuable"


def test_unknown_budget_is_allowed_but_never_counted_as_suitability() -> None:
    r = _a()
    assert r.triage == "actionable"
    assert r.criteria["budget"]["result"] == "unknown"
    assert any(m.startswith("budget: not stated") for m in r.missing)


def test_attestation_cannot_override_a_failed_criterion() -> None:
    r = _a(
        text="Design an AI assistant. Minimum annual turnover of 5M required. Remote.",
        formal_procurement=True,
        attested={"supplier_eligibility": {"by": "olga"}},
    )
    assert r.triage != "actionable" and r.criteria["procurement_complexity"]["result"] == "fail"


# ---------------------------------------------------------------- Finding C
def _intake(session):  # type: ignore[no-untyped-def]
    return intake_lead(
        session, S, "olga",
        dict(source_url="https://forum.example/post/1", platform="forum",
             requirement="Need an AI assistant built, remote OK",
             excerpt="We need a contractor to design and deploy an AI assistant; remote is fine.",
             published_at=NOW - timedelta(days=2), status="open",
             deadline=NOW + timedelta(days=20), contact_route="hello@buyer.example"),
    )  # fmt: skip


def test_verification_refreshes_triage_status_and_audit(api) -> None:  # type: ignore[no-untyped-def]
    with api.factory() as s:
        opp = _intake(s)
        s.commit()
        oid = str(opp.id)
        assert opp.triage == "needs_verification" and opp.status == "new"
    r = api.post(f"/opportunities/{oid}/verify")
    assert r.status_code == 200 and r.json()["verification_status"] == "verified"
    with api.factory() as s:
        opp = s.get(Opportunity, uuid.UUID(oid))
        assert opp.triage == "actionable" and opp.status == "qualified"
        assert opp.assessment["pursuability"] == "pursuable"


def test_verification_does_not_override_failed_criteria(api) -> None:  # type: ignore[no-untyped-def]
    with api.factory() as s:
        opp = intake_lead(
            s, S, "olga",
            dict(source_url="https://forum.example/post/2", platform="forum",
                 requirement="Need an AI assistant", status="closed",
                 excerpt="We needed a contractor to build an AI assistant; this is now closed.",
                 published_at=NOW - timedelta(days=2), contact_route="x@buyer.example"),
        )  # fmt: skip
        s.commit()
        oid = str(opp.id)
    api.post(f"/opportunities/{oid}/verify")
    with api.factory() as s:
        assert s.get(Opportunity, uuid.UUID(oid)).triage == "rejected"


def test_verify_rejects_unknown_attestation_and_viewers(api) -> None:  # type: ignore[no-untyped-def]
    with api.factory() as s:
        oid = str(_intake(s).id)
        s.commit()
    assert api.post(f"/opportunities/{oid}/verify", json={"attest": ["budget"]}).status_code == 422
    api.headers["Authorization"] = "Bearer " + "v" * 24
    assert api.post(f"/opportunities/{oid}/verify").status_code == 403


# ---------------------------------------------------------------- Finding D (truthful outcomes)
from sqlalchemy import select  # noqa: E402

from app.connectors.base import ConnectorError, CostEstimate, DiscoveryResult  # noqa: E402
from app.models import WorkflowRun  # noqa: E402
from app.orchestrator import outcome as oc  # noqa: E402
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run  # noqa: E402


class _Src:
    is_mock = True
    kinds = frozenset({SignalKind.PROJECT_REQUEST})

    def __init__(self, name: str, findings=None, error: bool = False):  # type: ignore[no-untyped-def]
        self.name, self._f, self._e = name, findings or [], error

    def estimate_cost(self, q):  # type: ignore[no-untyped-def]
        return CostEstimate(usd=0.0)

    def discover(self, q):  # type: ignore[no-untyped-def]
        if self._e:
            raise ConnectorError("upstream 503")
        return DiscoveryResult(provider=self.name, findings=self._f)


def _finding(n: int = 1) -> RawFinding:
    return RawFinding(
        kind=SignalKind.PROJECT_REQUEST, title=f"Build an AI assistant {n}",
        source_url=f"https://buyer{n}.example/rfp", published_at=NOW, organization_name=f"Buyer{n}",
        original_text="We need a contractor to design and deploy a generative AI assistant, remote.",
        source_status="open", deadline=NOW + timedelta(days=9), contact_route="a@buyer.example",
    )  # fmt: skip


def _go(session, sources):  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", enabled_discoverers="", worker_max_attempts=2)
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session, "w1", s)
    return execute_run(session, run.id, sources, s)


def test_every_source_failing_is_a_failed_run_not_success(session) -> None:  # type: ignore[no-untyped-def]
    run = _go(session, [_Src("a", error=True), _Src("b", error=True)])
    assert run.status == "failed" and run.outcome == oc.FAILED
    assert "all 2 executed source(s) failed" in run.error
    assert run.state["outcome"]["sources_failed"] == ["a", "b"]


def test_partial_no_records_and_success_are_distinguished(session) -> None:  # type: ignore[no-untyped-def]
    r = _go(session, [_Src("ok", [_finding(1)]), _Src("bad", error=True)])
    assert r.status == "succeeded" and r.outcome == oc.PARTIAL
    r = _go(session, [_Src("empty")])
    assert r.status == "succeeded" and r.outcome == oc.NO_RECORDS
    r = _go(session, [_Src("ok2", [_finding(2)])])
    assert r.status == "succeeded" and r.outcome == oc.SUCCEEDED
    assert r.state["outcome"]["retrieved"] == 1


def test_cancelled_run_has_cancelled_outcome(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", enabled_discoverers="")
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session, "w1", s)
    run.status = "cancelled"
    session.commit()
    out = execute_run(session, run.id, [_Src("a")], s)
    assert out.status == "cancelled" and out.outcome == oc.CANCELLED


# ---------------------------------------------------------------- Finding E (leases)
def test_long_running_node_is_not_stolen_while_its_lease_is_renewed(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", run_lease_seconds=120)
    enqueue_run(session, "x", {})
    run = claim_next_run(session, "w1", s)
    run.locked_at = datetime.now(UTC) - timedelta(hours=1)  # old lock timestamp, live lease
    session.commit()
    assert claim_next_run(session, "w2", s) is None
    assert session.get(WorkflowRun, run.id).status == "running"


def test_lapsed_lease_is_recovered_then_given_up_after_max_attempts(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", worker_max_attempts=2)
    enqueue_run(session, "x", {})
    run = claim_next_run(session, "w1", s)
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # w1 crashed
    session.commit()
    again = claim_next_run(session, "w2", s)
    assert again is not None and again.id == run.id and again.lease_owner == "w2"
    assert again.attempts == 2
    again.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # w2 crashed too
    session.commit()
    assert claim_next_run(session, "w3", s) is None
    dead = session.get(WorkflowRun, run.id)
    assert dead.status == "failed" and "lease lapsed" in dead.error


def test_worker_that_lost_its_lease_writes_nothing(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", enabled_discoverers="")
    enqueue_run(session, "x", {})
    run = claim_next_run(session, "w1", s)
    stolen = {"done": False}

    class Thief(_Src):
        def discover(self, q):  # type: ignore[no-untyped-def]
            # while w1 works, its lease lapses and w2 takes the run over
            with session.no_autoflush:
                r = session.get(WorkflowRun, run.id)
                r.lease_owner, stolen["done"] = "w2", True
                session.commit()
            return DiscoveryResult(provider=self.name, findings=[_finding(7)])

    out = execute_run(session, run.id, [Thief("t")], s)
    assert stolen["done"] and out.lease_owner == "w2" and out.status == "running"
    assert "opportunity_intelligence" not in out.state.get("completed_nodes", [])  # nothing after the takeover


# ---------------------------------------------------------------- Finding B (durable provider runs)
import httpx  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.agents.discovery import OpportunityIntelligenceAgent  # noqa: E402
from app.connectors.apify_client import ApifyClient  # noqa: E402
from app.connectors.base import DiscoveryQuery  # noqa: E402
from app.connectors.intent_sources import ApifySearchIntentConnector  # noqa: E402
from app.models import ProviderRun, ProviderSpend  # noqa: E402
from app.services.provider_runs import charge_of, settle_spend_from_runs  # noqa: E402
from app.services.spend import SpendGuard  # noqa: E402


def _apify(session, handler, **kw):  # type: ignore[no-untyped-def]
    s = Settings(
        database_url="sqlite://", apify_paid_execution_approved=True,
        apify_test_budget_usd=5.0, spend_limit_per_run_usd=5.0, **kw,
    )  # fmt: skip
    client = ApifyClient(SecretStr("t"), transport=httpx.MockTransport(handler), poll_seconds=0)
    return s, ApifySearchIntentConnector(s, client, None)


def test_multiple_actor_runs_keep_every_provider_run_id(session) -> None:  # type: ignore[no-untyped-def]
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            n["i"] += 1
            return httpx.Response(201, json={"data": {"id": f"RUN{n['i']}", "status": "SUCCEEDED",
                                                      "defaultDatasetId": f"D{n['i']}",
                                                      "usageTotalUsd": 0.01}})  # fmt: skip
        return httpx.Response(200, json=[])

    s, c = _apify(session, handler, apify_search_engines="google,bing")
    out = OpportunityIntelligenceAgent(session, [c], SpendGuard(session, s)).run(DiscoveryQuery(), None)
    rows = list(session.execute(select(ProviderRun)).scalars())
    assert n["i"] >= 3 and len(rows) == n["i"]
    assert {r.external_id for r in rows} == {f"RUN{i}" for i in range(1, n["i"] + 1)}
    assert all(r.status == "succeeded" and r.spend_id for r in rows)
    desc = session.execute(select(ProviderSpend)).scalars().first().description
    assert all(f"apify_run:RUN{i}" in desc for i in range(1, n["i"] + 1))  # none overwritten
    assert not out["errors"]


def test_uncertain_submission_is_never_resubmitted(session) -> None:  # type: ignore[no-untyped-def]
    posts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        raise httpx.ReadTimeout("no response", request=req)

    s, c = _apify(session, handler, apify_search_engines="google")
    wf = uuid.uuid4()
    from app.models import WorkflowRun as WR

    session.add(WR(id=wf, kind="x", input={}, state={}))
    session.flush()
    agent = OpportunityIntelligenceAgent(session, [c], SpendGuard(session, s))
    first = agent.run(DiscoveryQuery(), wf)
    assert posts["n"] == len(list(session.execute(select(ProviderRun)).scalars()))  # 1 per planned run
    assert first["errors"] and "unknown" in first["errors"][0].lower()
    before = posts["n"]
    agent2 = OpportunityIntelligenceAgent(session, [c], SpendGuard(session, s))
    second = agent2.run(DiscoveryQuery(), wf)  # same workflow run, e.g. retry after a crash
    assert posts["n"] == before  # NO new POST: the uncertain run is refused, not repeated
    assert second["errors"] and "not re-submitting" in second["errors"][0]
    assert {r.status for r in session.execute(select(ProviderRun)).scalars()} == {"unknown"}


def test_retry_attaches_to_a_known_run_instead_of_submitting(session) -> None:  # type: ignore[no-untyped-def]
    posts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posts["n"] += 1
        return httpx.Response(
            200, json={"data": {"id": "RUNX", "status": "SUCCEEDED", "defaultDatasetId": "DX",
                                "usageTotalUsd": 0.02}},
        )  # fmt: skip

    s, c = _apify(session, handler, apify_search_engines="google")
    client = c._get_client()
    from app.services.provider_runs import RunLedger

    led = RunLedger(session, "apify_intent_search", None, None)
    row_id, _ = led.begin("apify/google-search-scraper", {"q": 1}, 0.5)
    led.submitted(row_id, {"id": "RUNX", "defaultDatasetId": "DX"})  # worker died after this
    led2 = RunLedger(session, "apify_intent_search", None, None)
    run = client.run_actor("apify/google-search-scraper", {"q": 1}, 0.5, led2)
    assert posts["n"] == 0 and run["id"] == "RUNX"


def test_charge_uses_events_when_usage_understates() -> None:
    run = {
        "usageTotalUsd": 0.00005, "status": "SUCCEEDED", "chargedEventCounts": {"result": 20},
        "pricingInfo": {"pricingPerEvent": {"actorChargeEvents": {"result": {"eventPriceUsd": 0.0038}}}},
    }  # fmt: skip
    c = charge_of(run)
    assert c["charged"] == 0.076 and c["basis"] == "events" and c["reported"] == 0.00005
    assert charge_of({"status": "RUNNING"})["charged"] is None  # unknown stays unknown


def test_reservation_is_replaced_only_by_real_final_charges(session) -> None:  # type: ignore[no-untyped-def]
    sp = ProviderSpend(provider="apify:x", amount_usd=0.5, description="reserved")
    session.add(sp)
    session.flush()
    a = ProviderRun(provider="x", actor="a", idempotency_key="k1", spend_id=sp.id,
                    status="succeeded", charged_usd=0.07, external_id="R1")  # fmt: skip
    b = ProviderRun(provider="x", actor="a", idempotency_key="k2", spend_id=sp.id,
                    status="submitted", external_id="R2")  # fmt: skip
    session.add_all([a, b])
    session.flush()
    assert settle_spend_from_runs(session, sp.id) is None and float(sp.amount_usd) == 0.5
    b.status, b.charged_usd = "succeeded", 0.03
    assert settle_spend_from_runs(session, sp.id) == 0.1 and float(sp.amount_usd) == 0.1


def test_reconcile_reports_unresolved_submissions_and_never_posts(session) -> None:  # type: ignore[no-untyped-def]
    from app.cli.reconcile import reconcile

    session.add(ProviderRun(provider="x", actor="a", idempotency_key="k9", status="unknown"))
    session.flush()
    methods: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        methods.append(req.method)
        return httpx.Response(200, json={"data": {}})

    client = ApifyClient(SecretStr("t"), transport=httpx.MockTransport(handler))
    res = reconcile(session, client, apply=True)
    assert any("UNRESOLVED" in str(e["action"]) for e in res) and "POST" not in methods


# ---------------------------------------------------------------- Finding A (adaptive discovery loop)
from app.connectors.intent_sources import SearchIntentConnector  # noqa: E402
from app.models import DiscoveryQueryStat  # noqa: E402
from app.services.query_strategy import build_plan, fingerprint, prefer_effective  # noqa: E402
from tests.unit.test_milestone4 import _fake_checker  # noqa: E402

GOOD_PAGE = (
    '<html><head><title>Support project</title>'
    f'<meta property="article:published_time" content="{NOW:%Y-%m-%d}"></head><body><p>We are '
    "looking for a contractor to build a generative AI assistant for our support team. Remote "
    "delivery. Apply to jobs@buyer-a.example by 2026-12-30.</p></body></html>"
)


def _search_conn(good_query: str):  # type: ignore[no-untyped-def]
    s = Settings(
        database_url="sqlite://", search_discovery_enabled=True, brave_api_key=SecretStr("k"),
        search_max_pages=3, intent_queries_agency="", query_plan_max_per_channel=4,
        intent_queries_buyer="ai chatbot developer wanted|rag project contractor needed|"
        "need llm agent builder|looking for ai automation freelancer",
    )  # fmt: skip

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["q"]
        url = "https://buyer-a.example/ai-job" if q == good_query else f"https://noise.example/{abs(hash(q)) % 999}"
        return httpx.Response(200, json={"web": {"results": [{"url": url, "title": q}]}})

    pages = {"https://buyer-a.example/ai-job": (200, GOOD_PAGE)}
    return s, SearchIntentConnector(s, httpx.MockTransport(handler), _fake_checker(pages))


def test_query_outcomes_are_persisted_attributed_and_change_future_ordering(session) -> None:  # type: ignore[no-untyped-def]
    good = "rag project contractor needed"
    s, conn = _search_conn(good)
    conn.is_mock = False
    spend = SpendGuard(session, s.model_copy(update={"search_budget_usd": 5.0}))
    first_order = [p.text for p in conn.planned()]
    wf = uuid.uuid4()
    for i in range(3):  # repeated execution
        from app.models import WorkflowRun as WR

        wf = uuid.uuid4()
        session.add(WR(id=wf, kind="x", input={}, state={}))
        session.flush()
        conn.query_log = {}
        conn.set_plan(prefer_effective(session, build_plan(s)))
        agent_conn = conn
        # run through the real agent so persistence + attribution use production code
        from app.agents.discovery import OpportunityIntelligenceAgent as Agent

        res = Agent(session, [agent_conn], spend).run(DiscoveryQuery(), wf)
        assert not res["errors"], res["errors"]
        for att in res["query_attribution"]:
            from app.models import Opportunity as Opp
            from app.services.commercial_class import commercial_class
            from app.services.query_strategy import record_outcome

            opp = session.get(Opp, uuid.UUID(att["oid"]))
            apply_assessment(opp, assess_opportunity(session, opp, s))
            record_outcome(session, provider=att["provider"], query_fingerprint=att["fp"],
                           commercial_class=commercial_class(category=opp.category, triage=opp.triage,
                                                             channel=opp.channel),
                           duplicate=att["duplicate"])  # fmt: skip
        conn.set_plan(None)  # type: ignore[arg-type]
    rows = {r.query_text: r for r in session.execute(select(DiscoveryQueryStat)).scalars()}
    assert rows[good].searches == 3 and rows[good].urls_retrieved == 3
    assert rows[good].needs_verification + rows[good].confirmed_active >= 1  # attributed correctly
    noise = [r for t, r in rows.items() if t != good]
    assert noise and all(r.needs_verification + r.confirmed_active == 0 for r in noise)
    new_order = [p.text for p in prefer_effective(session, build_plan(s))]
    assert new_order[0] == good and new_order != first_order or first_order[0] == good
    assert fingerprint(good) in {r.fingerprint for r in rows.values()}
