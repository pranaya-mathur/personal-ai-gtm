"""Milestone 3 regressions: two-stage qualification, agency discovery, AI call economy."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.ai import schemas as S
from app.ai.claims import validate_output
from app.cli.audit import audit
from app.connectors.agencies import (
    AnnouncementFeedConnector,
    PublicPartnerPageConnector,
    WikidataAgencyConnector,
)
from app.connectors.base import DiscoveryQuery, RawFinding, SignalKind
from app.core.config import Settings
from app.llm.mock import MockLLM
from app.models import AiAnalysis, LlmUsage, Opportunity, Qualification
from app.models.enums import OpportunityCategory
from app.services.ingest import ingest_finding
from app.services.partnership import classify_partner_text
from app.services.source_check import SourceChecker
from app.services.triage import AiSignal, assess

from .test_ai import RoutingLLM, _process, gateway, live

NOW = datetime.now(UTC)
TERMS = Settings(database_url="sqlite://").topic_term_list


def a(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        title="x", text="", category="confirmed_demand", org_kind="buyer", source_status="open",
        deadline=NOW + timedelta(days=20), budget_text=None, contact_route="buyer@example.org",
        known_contacts=0, settings=Settings(database_url="sqlite://"),
    )  # fmt: skip
    base.update(kw)
    return assess(**base)


# ------------------------------------------------------------------ stage A / B
def test_generic_it_tender_that_only_lists_ai_is_not_actionable() -> None:
    r = a(
        title="ERP maintenance services",
        text="Maintenance of accounting modules. Supplier may adopt emerging technologies such "
        "as AI, IoT, blockchain, cloud, analytics.",
    )
    assert r.technical_relevance == "unclear" and r.triage == "needs_verification"
    assert any("scope" in m for m in r.missing)


def test_genuine_ai_request_is_actionable_and_budget_is_explicitly_unknown() -> None:
    r = a(
        title="Generative AI assistant for citizen services",
        text="The authority seeks a contractor to design and deploy a RAG assistant.",
    )
    assert r.technical_relevance == "relevant" and r.triage == "actionable"
    assert r.criteria["budget"]["detail"].startswith("UNKNOWN")
    assert "budget: not stated" in r.missing  # missing stays visible, never invented


@pytest.mark.parametrize("status", ["closed", "awarded"])
def test_closed_or_awarded_tenders_are_rejected_explicitly(status: str) -> None:
    r = a(title="AI chatbot", text="Build a chatbot.", source_status=status)
    assert r.triage == "rejected" and status in (r.rejection_reason or "")


def test_off_topic_is_rejected_not_returned_to_queue() -> None:
    r = a(title="School furniture", text="Supply of desks and chairs.")
    assert r.triage == "rejected" and "not_relevant" in (r.rejection_reason or "")


def test_deadline_in_past_without_status_is_inactive() -> None:
    r = a(title="AI platform", text="Build an AI platform.", source_status="unknown",
          deadline=NOW - timedelta(days=1))  # fmt: skip
    assert r.triage == "rejected" and "closed" in (r.rejection_reason or "")


def test_missing_contact_and_deadline_keep_it_in_verification() -> None:
    r = a(title="AI platform", text="Build an AI platform.", source_status="unknown",
          deadline=None, contact_route=None)  # fmt: skip
    assert r.triage == "needs_verification"
    assert any(m.startswith("active") for m in r.missing)
    assert any(m.startswith("contact_route") for m in r.missing)


def test_large_stated_value_fails_small_team_realism_and_blocks_actionable() -> None:
    r = a(title="AI platform", text="Build an AI platform.", budget_text="500000 EUR")
    assert r.criteria["deal_size"]["result"] == "fail"
    assert r.triage == "needs_verification"


def test_ai_advisory_cannot_create_actionable_alone() -> None:
    r = a(title="ERP", text="Includes AI, IoT, blockchain.", ai=AiSignal("explicit", "supported"),
          contact_route=None)  # fmt: skip
    assert r.technical_relevance == "relevant" and r.triage == "needs_verification"
    r2 = a(title="Generative AI portal", text="Build it.", ai=AiSignal("none", "unsupported"))
    assert r2.technical_relevance == "unclear" and r2.triage != "actionable"


def test_italian_ai_preposition_and_lowercase_rag_are_not_ai_terms() -> None:
    r = a(title="Servizi ai cittadini", text="Fornitura di stracci (rag) ai comuni.")
    assert r.technical_relevance == "not_relevant"


# ------------------------------------------------------------------ agencies
def test_agency_with_complementary_services_only_yields_no_signal() -> None:
    text = "We are a full-service digital agency. We build websites, apps and cloud platforms."
    assert classify_partner_text(text, TERMS) is None


def test_agency_seeking_ai_delivery_partners_is_an_active_requirement() -> None:
    text = "Careers. We are looking for AI delivery partners and subcontractors for client work."
    s = classify_partner_text(text, TERMS)
    assert s is not None and s.partnership_class == "active_requirement"
    assert "looking for AI delivery partners" in s.sentences[0]  # verbatim evidence


def test_existing_subcontracting_model_is_verified_not_active() -> None:
    s = classify_partner_text(
        "Our projects are delivered with white-label engineering teams.", TERMS
    )
    assert s is not None and s.partnership_class == "verified_model"


def test_seeking_without_ai_downgrades_to_model_not_ai_requirement() -> None:
    s = classify_partner_text("We are seeking subcontractors for our web projects today.", TERMS)
    assert s is not None and s.partnership_class == "verified_model"


def _checker(pages: dict[str, tuple[int, str]]) -> SourceChecker:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        code, body = pages.get(f"https://{req.headers['host']}{req.url.path}", (404, ""))
        return httpx.Response(code, text=body)

    return SourceChecker(
        transport=httpx.MockTransport(handler), resolver=lambda h: ["93.184.216.34"]
    )


def test_partner_page_connector_reads_only_listed_permitted_pages() -> None:
    s = Settings(
        database_url="sqlite://", agency_seed_urls="alpha.example, https://beta.example/private/p"
    )
    pages = {
        "https://alpha.example/partners": (
            200,
            "<html><title>Alpha Labs | Partners</title><body><p>We are looking for AI delivery "
            "partners and subcontractors to join our client projects.</p></body></html>",
        ),
        "https://beta.example/private/p": (
            200,
            "<p>We are looking for AI subcontractors always.</p>",
        ),
    }
    res = PublicPartnerPageConnector(s, _checker(pages)).discover(DiscoveryQuery())
    assert [f.organization_domain for f in res.findings] == ["alpha.example"]  # robots blocked beta
    f = res.findings[0]
    # a live page is undated: it can evidence a MODEL but never a current request
    assert f.partnership_class == "verified_model" and f.kind is SignalKind.PARTNER_ACTIVITY
    assert f.source_status == "unknown" and f.contact_route is None  # page URL is not a contact
    assert f.source_url == "https://alpha.example/partners" and f.evergreen


def test_partner_page_connector_requires_seeds() -> None:
    from app.connectors.base import ConnectorError

    with pytest.raises(ConnectorError, match="AGENCY_SEED_URLS"):
        PublicPartnerPageConnector(Settings(database_url="sqlite://")).discover(DiscoveryQuery())


def test_feed_connector_parses_items_and_rejects_entity_tricks() -> None:
    feed = (
        '<?xml version="1.0"?><rss><channel>'
        "<item><title>Gamma launches white-label AI programme</title>"
        "<link>https://gamma.example/news/1</link><pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate>"
        "<description>Gamma now delivers client work through white-label AI engineering "
        "partners across Europe.</description></item>"
        "<item><title>Office party</title><link>https://gamma.example/news/2</link>"
        "<description>Cake was served at the annual office party.</description></item>"
        "</channel></rss>"
    )
    conn = AnnouncementFeedConnector(
        Settings(database_url="sqlite://", agency_feed_urls="https://gamma.example/feed")
    )
    out = conn._parse("https://gamma.example/feed", feed)
    assert [f.partnership_class for f in out] == ["verified_model"]
    assert out[0].published_at is not None
    assert conn._parse("x", '<!DOCTYPE r [<!ENTITY a "b">]><rss/>') == []


def test_wikidata_recorded_payload_gives_potential_partners_only(session) -> None:  # type: ignore[no-untyped-def]
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/wikidata_gb_recorded_2026-11-03.json").read_text()
    )
    findings = WikidataAgencyConnector.parse(payload)
    assert findings and all(f.partnership_class == "potential" for f in findings)
    out = ingest_finding(session, findings[0], "wikidata_agencies", None, 90)
    opp = session.get(Opportunity, out.opportunity_id)
    assert opp is not None and opp.category == OpportunityCategory.SPECULATIVE_PROSPECT
    from app.services.triage import assess_opportunity

    assert (
        assess_opportunity(session, opp, Settings(database_url="sqlite://")).triage
        == "needs_verification"
    )


def test_wikidata_connector_error_is_a_connector_error_not_a_record() -> None:
    from app.connectors.base import ConnectorError

    c = WikidataAgencyConnector(
        Settings(database_url="sqlite://"), httpx.MockTransport(lambda r: httpx.Response(500))
    )
    with pytest.raises(ConnectorError):
        c.discover(DiscoveryQuery(regions=["GB"]))


# ------------------------------------------------------------------ data quality
def test_duplicate_findings_collapse_and_keep_one_record(session) -> None:  # type: ignore[no-untyped-def]
    f = RawFinding(
        kind=SignalKind.PROJECT_REQUEST, title="AI assistant", source_url="https://b.example/1?utm_x=1",
        original_text="Build an AI assistant.", published_at=NOW, organization_name="B",
    )  # fmt: skip
    first = ingest_finding(session, f, "p", None, 90)
    second = ingest_finding(
        session, f.model_copy(update={"source_url": "https://b.example/1"}), "p", None, 90
    )
    assert first.opportunity_id == second.opportunity_id and second.duplicate
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == 1


# ------------------------------------------------------------------ AI economy
def _dossier_task(gw, session, opp_id, validator=None, run_id=None):  # type: ignore[no-untyped-def]
    from .test_ai import evidence_block

    ev, user = evidence_block(session)
    return gw.run_task(
        task="dossier", schema=S.OpportunityDossier, system="sys", user=user, run_id=run_id,
        opportunity_id=opp_id, agent="research_evidence",
        validator=validator or (lambda o: validate_output(o, ev, 1)),
    )  # fmt: skip


def _seed(session):  # type: ignore[no-untyped-def]
    from .test_ai import seed

    return seed(session)[0]


def test_identical_input_is_served_from_cache_without_a_model_call(session) -> None:  # type: ignore[no-untyped-def]
    opp = _seed(session)
    llm = RoutingLLM()
    gw = gateway(session, live(), llm)
    assert _dossier_task(gw, session, opp) is not None
    assert _dossier_task(gw, session, opp) is not None
    assert len(llm.calls) == 1
    outcomes = sorted(u.outcome for u in session.execute(select(LlmUsage)).scalars())
    assert outcomes == ["accepted", "cached"]


def test_unsupported_dossier_claim_is_rejected_and_repair_is_limited(session) -> None:  # type: ignore[no-untyped-def]
    opp = _seed(session)
    llm = RoutingLLM()

    def bad(n, u):  # type: ignore[no-untyped-def]
        d = MockLLM().complete_structured("", u, S.OpportunityDossier, 10).parsed
        assert isinstance(d, S.OpportunityDossier)
        return d.model_copy(
            update={
                "requirements": [S.Claim(text="t", evidence_id="x", quote="invented quote text")]
            }
        )

    llm.handlers[S.OpportunityDossier] = bad
    gw = gateway(session, live(ai_max_repair_attempts=1), llm)
    assert _dossier_task(gw, session, opp, run_id=None) is None
    assert len(llm.calls) == 2  # initial + exactly one repair, never more
    rows = session.execute(select(LlmUsage)).scalars().all()
    assert all(
        r.outcome == "rejected" and r.reason and r.agent == "research_evidence" for r in rows
    )
    assert session.execute(select(AiAnalysis)).scalar_one().validated is False


def test_zero_repair_budget_means_single_attempt(session) -> None:  # type: ignore[no-untyped-def]
    opp = _seed(session)
    llm = RoutingLLM()
    llm.handlers[S.OpportunityDossier] = lambda n, u: S.OpportunityDossier(
        summary="s", requirements=[S.Claim(text="t", evidence_id="x", quote="not in evidence")],
        budget=None, deadline=None, ai_project="none", ai_project_support=None,
        verdict="supported", reliability=0.5, findings=[], open_questions=[], fit_score=1, risks=[],
    )  # fmt: skip
    assert (
        _dossier_task(gateway(session, live(ai_max_repair_attempts=0), llm), session, opp) is None
    )
    assert len(llm.calls) == 1


def test_per_record_call_allowance_stops_excessive_calls(session) -> None:  # type: ignore[no-untyped-def]
    from app.ai.gateway import RecordBudgetExceeded

    opp = _seed(session)
    run_id = uuid.uuid4()
    from app.models import WorkflowRun

    session.add(WorkflowRun(id=run_id, kind="t", input={}, state={}))
    session.flush()
    llm = RoutingLLM()
    llm.handlers[S.OpportunityDossier] = lambda n, u: S.OpportunityDossier(
        summary="s", requirements=[S.Claim(text="t", evidence_id="x", quote="not in evidence")],
        budget=None, deadline=None, ai_project="none", ai_project_support=None,
        verdict="supported", reliability=0.5, findings=[], open_questions=[], fit_score=1, risks=[],
    )  # fmt: skip
    gw = gateway(session, live(ai_max_calls_per_record=1, ai_max_repair_attempts=3), llm)
    with pytest.raises(RecordBudgetExceeded):
        _dossier_task(gw, session, opp, run_id=run_id)
    assert len(llm.calls) == 1


def test_per_record_cost_budget_is_enforced(session) -> None:  # type: ignore[no-untyped-def]
    from app.ai.gateway import RecordBudgetExceeded
    from app.models import WorkflowRun

    opp = _seed(session)
    run_id = uuid.uuid4()
    session.add(WorkflowRun(id=run_id, kind="t", input={}, state={}))
    session.flush()
    llm = RoutingLLM()
    gw = gateway(session, live(ai_record_budget_usd=0.0000001), llm)
    with pytest.raises(RecordBudgetExceeded, match="per-record"):
        _dossier_task(gw, session, opp, run_id=run_id)
    assert llm.calls == []


def test_one_invalid_response_does_not_fail_the_workflow_or_other_records(session) -> None:  # type: ignore[no-untyped-def]
    llm = RoutingLLM()
    n = {"i": 0}

    def flaky(c, u):  # type: ignore[no-untyped-def]
        n["i"] += 1
        if n["i"] <= 2:  # first record: initial + repair both invalid
            return S.OpportunityDossier(
                summary="s", requirements=[S.Claim(text="t", evidence_id="x", quote="never in text")],  # noqa: E501
                budget=None, deadline=None, ai_project="none", ai_project_support=None,
                verdict="supported", reliability=0.5, findings=[], open_questions=[], fit_score=1, risks=[],  # noqa: E501
            )  # fmt: skip
        return MockLLM().complete_structured("", u, S.OpportunityDossier, 10).parsed

    llm.handlers[S.OpportunityDossier] = flaky
    run = _process(session, live(), llm)
    assert run.status == "succeeded" and run.attempts == 1  # no whole-workflow retry
    results = run.state["ai_results"].values()
    assert any(not r["dossier_ok"] for r in results) and any(r["dossier_ok"] for r in results)


def test_rejected_and_stale_records_never_reach_the_model_and_second_run_is_cached(session) -> None:  # type: ignore[no-untyped-def]
    llm = RoutingLLM()
    s = live()
    run1 = _process(session, s, llm)
    dossiers = [c for c in llm.calls if c[0] is S.OpportunityDossier]
    # mock data: 1 closed/stale record is rejected before AI; 1 speculative is skipped
    assert run1.state["ai"]["skipped_by_filter"] >= 2
    assert len(dossiers) == 3
    first_total = len(llm.calls)
    _process(session, s, llm)
    assert len(llm.calls) == first_total  # identical sources: zero new model calls
    cached = session.execute(
        select(func.count()).select_from(LlmUsage).where(LlmUsage.outcome == "cached")
    ).scalar_one()
    assert cached >= 3


def test_every_ledger_row_is_attributed_to_an_agent(session) -> None:  # type: ignore[no-untyped-def]
    _process(session, live(), RoutingLLM())
    agents = {u.agent for u in session.execute(select(LlmUsage)).scalars()}
    assert agents <= {"research_evidence", "outreach_intelligence"} and agents


# ------------------------------------------------------------------ audit of existing records
def test_audit_preserves_evidence_and_history_and_never_promotes_category(session) -> None:  # type: ignore[no-untyped-def]
    def add(title: str, text: str, **kw):  # type: ignore[no-untyped-def]
        f = RawFinding(
            kind=SignalKind.PROJECT_REQUEST, title=title, source_url=f"https://x.example/{title[:8]}",
            original_text=text, published_at=NOW, organization_name=f"Org {title[:6]}", **kw,
        )  # fmt: skip
        return ingest_finding(session, f, "apify_ted_tenders", None, 90).opportunity_id

    off = add("Romania Software services", "Development of accounting software modules.")
    ok = add("Generative AI assistant", "Design and deploy an AI assistant.", source_status="open",
             deadline=NOW + timedelta(days=9), contact_route="a@b.example")  # fmt: skip
    session.get(Opportunity, off).status = "qualified"  # type: ignore[union-attr]  # old keyword gate
    session.add(Qualification(opportunity_id=off, score=82.5, criteria={}, rationale="old"))
    session.flush()
    before = {o.id: o.category for o in session.execute(select(Opportunity)).scalars()}
    n_ev = session.execute(
        select(func.count()).select_from(__import__("app.models", fromlist=["Evidence"]).Evidence)
    ).scalar_one()

    rows = audit(session, Settings(database_url="sqlite://"), apply=True)
    assert len(rows) == 2
    o_off, o_ok = session.get(Opportunity, off), session.get(Opportunity, ok)
    assert o_off.triage == "rejected" and o_off.status == "disqualified"  # type: ignore[union-attr]
    assert o_ok.triage == "actionable" and o_ok.status == "qualified"  # type: ignore[union-attr]
    assert {o.id: o.category for o in session.execute(select(Opportunity)).scalars()} == before
    assert o_ok.verification_status == "unverified"  # type: ignore[union-attr]  # never auto-verified
    quals = (
        session.execute(select(Qualification).where(Qualification.opportunity_id == off))
        .scalars()
        .all()
    )
    assert len(quals) == 2 and quals[0].rationale == "old"  # history kept, audit row appended
    assert any(q.criteria.get("previous", {}).get("status") == "qualified" for q in quals)
    from app.models import Evidence

    assert session.execute(select(func.count()).select_from(Evidence)).scalar_one() == n_ev


def test_dry_run_audit_changes_nothing(session) -> None:  # type: ignore[no-untyped-def]
    f = RawFinding(kind=SignalKind.PROJECT_REQUEST, title="Desks", source_url="https://x.example/d",
                   original_text="Supply desks.", published_at=NOW, organization_name="O")  # fmt: skip  # noqa: E501
    oid = ingest_finding(session, f, "p", None, 90).opportunity_id
    rows = audit(session, Settings(database_url="sqlite://"), apply=False)
    assert rows[0]["after"]["triage"] == "rejected"
    assert session.get(Opportunity, oid).triage == "unassessed"  # type: ignore[union-attr]


def test_fetch_connects_to_the_pinned_ip_with_original_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, text="hello")

    calls = {"n": 0}

    def resolver(host: str) -> list[str]:  # a hostile DNS: answers differently each time
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["127.0.0.1"]

    c = SourceChecker(transport=httpx.MockTransport(handler), resolver=resolver)
    status, body = c.fetch_page("https://a.example/p")
    assert status.value == "confirmed" and body == "hello"
    page = seen[-1]
    assert page.url.host == "93.184.216.34"  # connected to the validated address
    assert page.headers["host"] == "a.example"
    assert page.extensions["sni_hostname"] == "a.example"


def test_non_public_resolution_is_never_connected_to() -> None:
    hits: list[str] = []
    c = SourceChecker(
        transport=httpx.MockTransport(lambda r: hits.append(str(r.url)) or httpx.Response(200)),
        resolver=lambda h: ["93.184.216.34", "10.0.0.1"],  # one private answer poisons the set
    )
    assert c.fetch_page("https://a.example/")[0].value == "blocked"
    assert hits == []


def test_evergreen_partner_page_can_never_become_an_actionable_project(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", agency_seed_urls="alpha.example")
    pages = {
        "https://alpha.example/partners": (
            200,
            "<title>Alpha Labs</title><p>We are looking for AI delivery partners and "
            "subcontractors to join our client projects. Write to partners@alpha.example.</p>",
        )
    }
    f = PublicPartnerPageConnector(s, _checker(pages)).discover(DiscoveryQuery()).findings[0]
    assert f.partnership_class == "verified_model" and f.contact_route == "partners@alpha.example"
    ingest_finding(session, f, "public_partner_pages", None, 90)
    from app.services.triage import assess_opportunity

    opp = session.execute(select(Opportunity)).scalars().one()
    assert opp.category == OpportunityCategory.VERIFIED_PARTNERSHIP
    assert assess_opportunity(session, opp, s).triage == "needs_verification"


def test_dated_agency_request_still_needs_a_human_and_a_real_contact(session) -> None:  # type: ignore[no-untyped-def]
    f = RawFinding(
        kind=SignalKind.PARTNER_ACTIVITY, title="Gamma seeks AI subcontractors",
        source_url="https://gamma.example/news/1",
        original_text="We are looking for AI subcontractors to build RAG assistants.",
        published_at=NOW, organization_name="Gamma", organization_domain="gamma.example",
        partnership_class="active_requirement", contact_route="https://gamma.example/news/1",
    )  # fmt: skip
    ingest_finding(session, f, "public_announcement_feeds", None, 90)
    from app.services.triage import assess_opportunity

    opp = session.execute(select(Opportunity)).scalars().one()
    assert opp.category == OpportunityCategory.CONFIRMED_DEMAND  # the source states a need
    r = assess_opportunity(session, opp, Settings(database_url="sqlite://"))
    assert r.triage == "needs_verification"
    assert r.criteria["contact_route"]["result"] == "unknown"  # the page itself is no contact
    assert r.criteria["requirement_evidence"]["result"] == "unknown"


def test_deal_size_bands_and_unknown_budget() -> None:
    inside = a(title="AI chatbot build", text="Build a chatbot.", budget_text="8000 USD")
    assert (
        inside.criteria["deal_size"]["result"] == "pass"
        and "preferred" in inside.criteria["deal_size"]["detail"]
    )
    mid = a(title="AI chatbot build", text="Build a chatbot.", budget_text="60000 EUR")
    assert (
        mid.criteria["deal_size"]["result"] == "pass"
        and "outside the preferred" in mid.criteria["deal_size"]["detail"]
    )
    assert (
        a(title="AI chatbot", text="Build a chatbot.").criteria["deal_size"]["result"] == "unknown"
    )
    big = a(title="AI chatbot", text="Build a chatbot.", budget_text="2500000 EUR")
    assert big.criteria["deal_size"]["result"] == "fail" and big.triage == "needs_verification"


def test_turnover_requirements_block_actionable() -> None:
    r = a(title="AI chatbot", text="Build a chatbot. Minimum annual turnover of 5M required.")
    assert (
        r.criteria["procurement_complexity"]["result"] == "fail"
        and r.triage == "needs_verification"
    )


def test_contact_route_must_not_be_the_source_page_itself() -> None:
    from app.services.triage import valid_route

    assert valid_route("buyer@x.example", ["https://x.example/n"])
    assert not valid_route("https://x.example/n/", ["https://x.example/n"])
    assert valid_route("https://x.example/apply", ["https://x.example/n"])
    assert not valid_route(None, []) and not valid_route("not a route", [])
