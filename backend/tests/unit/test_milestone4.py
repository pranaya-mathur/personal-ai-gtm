# ruff: noqa: E501
"""Milestone 4: automatic source discovery, free official-API connectors, contact routes."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.base import ConnectorError, DiscoveryQuery, RawFinding, SignalKind
from app.connectors.discoverers import (
    BraveSearchDiscoverer,
    GitHubOrgDiscoverer,
    WikidataDiscoverer,
    build_discoverers,
)
from app.connectors.public_apis import (
    ContractsFinderConnector,
    HackerNewsFreelancerConnector,
    TedApiConnector,
    WorldBankApiConnector,
)
from app.core.config import Settings
from app.models import ContactRoute, Opportunity, SourceCandidate
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run
from app.services.candidates import Candidate, normalize_domain, upsert_candidate
from app.services.ingest import ingest_finding
from app.services.routes import emails_in, role_near, routes_from_html
from app.services.triage import assess_opportunity

FIX = Path(__file__).parents[1] / "fixtures"
S = Settings(database_url="sqlite://")
NOW = datetime.now(UTC)


def load(name: str):  # type: ignore[no-untyped-def]
    return json.loads((FIX / name).read_text())


# ------------------------------------------------------------------ recorded real API shapes
def test_ted_api_recorded_notices_map_with_real_fields() -> None:
    found = TedApiConnector.parse(load("ted_api_recorded_2026-11-03.json"))
    assert len(found) == 6
    f = next(x for x in found if "udir" in (x.contact_route or ""))
    assert f.contact_route == "jostein.knutsen@udir.no" and f.budget_text == "10000000.00 NOK"
    assert f.source_url.startswith("https://ted.europa.eu/en/notice/") and f.published_at
    assert f.source_status == "unknown"  # no deadline in the record: stays unknown
    assert any(r.kind == "email" for r in f.routes)
    dated = [x for x in found if x.deadline is not None]
    assert dated and all(x.source_status in ("open", "closed") for x in dated)


def test_ted_query_is_bounded_by_date_notice_type_and_keywords() -> None:
    q = TedApiConnector(S).build_query(DiscoveryQuery(keywords=["chatbot"], max_age_days=30))
    assert "notice-type = cn-standard" in q and 'title-proc ~ "chatbot"' in q
    assert "publication-date >=" in q


def test_ted_connector_http_error_is_a_connector_error() -> None:
    c = TedApiConnector(S, httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ConnectorError, match="500"):
        c.discover(DiscoveryQuery())


def test_worldbank_api_flags_awards_and_keeps_contacts() -> None:
    found = WorldBankApiConnector.parse(load("worldbank_api_recorded_2026-11-03.json"))
    awards = [f for f in found if f.source_status == "awarded"]
    assert awards and all(f.contact_route is None for f in awards)  # awards print no contact
    eoi = next(f for f in found if f.organization_name == "ICT Authority")
    assert eoi.contact_route == "thomas.bwaley@icta.go.ke"
    assert eoi.source_status == "closed"  # deadline 2026-10-28 already passed
    assert eoi.source_url.startswith("https://projects.worldbank.org/")


def test_contracts_finder_filters_to_it_or_ai_and_extracts_buyer_route() -> None:
    soon = (NOW + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def rel(title: str, cpv: str, desc: str = "") -> dict:  # type: ignore[type-arg]
        return {
            "tender": {
                "title": title, "description": desc, "classification": {"id": cpv},
                "tenderPeriod": {"endDate": soon}, "datePublished": "2026-11-01T00:00:00Z",
                "value": {"amount": 8000, "currency": "GBP"},
                "documents": [{"url": f"https://cf.example/notice/{title[:6]}"}],
            },
            "parties": [{"name": "Buyer Council", "roles": ["buyer"],
                         "contactPoint": {"email": "Procure@Buyer.example"},
                         "details": {"url": "https://buyer.example/tenders"}}],
        }  # fmt: skip

    payload = {
        "releases": [
            rel("School catering", "55524000"),
            rel("Chatbot for residents", "72000000", "Build a chatbot."),
            rel("Cleaning", "90910000", "Uses AI scheduling"),
        ]
    }
    found = ContractsFinderConnector.parse(payload, S.topic_term_list)
    assert [f.title for f in found] == ["Chatbot for residents", "Cleaning"]  # catering dropped
    f = found[0]
    assert f.contact_route == "procure@buyer.example" and f.source_status == "open"
    assert f.budget_text == "8000 GBP"
    assert {r.kind for r in f.routes} == {"email", "procurement_link"}


def test_hn_freelancer_threads_only_seeking_posts_with_real_contacts() -> None:
    thread = {"children": [
        {"id": 1, "created_at": "2026-10-20T10:00:00Z", "text": "SEEKING WORK | Remote | I am a dev"},
        {"id": 2, "created_at": "2026-10-20T10:00:00Z", "text": "SEEKING FREELANCER | Acme Ltd | We need an AI engineer to build a RAG chatbot. Email: hiring@acme.example Details https:&#x2F;&#x2F;acme.example&#x2F;brief"},
        {"id": 3, "created_at": "2026-10-21T10:00:00Z", "text": "SEEKING FREELANCER | We're an agency with overflow work for our clients, need white-label AI help. DM me"},
    ]}  # fmt: skip
    found = HackerNewsFreelancerConnector.parse(thread, S.topic_term_list)
    assert [f.source_url for f in found] == [
        "https://news.ycombinator.com/item?id=2",
        "https://news.ycombinator.com/item?id=3",
    ]
    buyer, agency = found
    assert buyer.kind is SignalKind.PROJECT_REQUEST and buyer.contact_route == "hiring@acme.example"
    assert (
        agency.kind is SignalKind.PARTNER_ACTIVITY
        and agency.partnership_class == "active_requirement"
    )
    assert agency.contact_route is None  # "DM me" is not a route; nothing is invented


# ------------------------------------------------------------------ routes
def test_route_extraction_prints_only_what_exists() -> None:
    html = '<p>Head of Partnerships: jane@agency.example</p><a href="mailto:Sales@Agency.example">x</a><form action="/c"></form><a href="/tenders/submit">Submit a bid</a><p>noreply@agency.example</p>'
    text = "Head of Partnerships: jane@agency.example"
    routes = routes_from_html(html, "https://agency.example/contact", text)
    vals = {(r.kind, r.value) for r in routes}
    assert ("email", "jane@agency.example") in vals and ("email", "sales@agency.example") in vals
    assert ("inquiry_form", "https://agency.example/contact") in vals
    assert ("procurement_link", "https://agency.example/tenders/submit") in vals
    assert not any("noreply" in v for _, v in vals)
    assert role_near(text, "jane@agency.example") == "Head of Partnerships"
    assert emails_in("no addresses here") == []


def test_ingest_stores_each_route_once_and_never_the_source_page(session) -> None:  # type: ignore[no-untyped-def]
    f = TedApiConnector.parse(load("ted_api_recorded_2026-11-03.json"))[0]
    ingest_finding(session, f, "ted_api", None, 400)
    ingest_finding(session, f, "ted_api", None, 400)
    routes = session.execute(select(ContactRoute)).scalars().all()
    assert len(routes) == 1 and routes[0].kind == "email"
    assert routes[0].value != f.source_url


# ------------------------------------------------------------------ candidates / discoverers
def test_candidates_deduplicate_and_exclude_marketplaces_and_socials(session) -> None:  # type: ignore[no-untyped-def]
    assert normalize_domain("https://www.Alpha.example/x") == "alpha.example"
    for bad in (
        "https://upwork.com/agencies/1",
        "linkedin.com/company/x",
        "github.com/a",
        "nodots",
    ):
        assert normalize_domain(bad) is None
    assert upsert_candidate(session, Candidate("alpha.example", origin="t"))
    assert not upsert_candidate(session, Candidate("https://www.alpha.example/", origin="u"))
    assert session.execute(select(func.count()).select_from(SourceCandidate)).scalar_one() == 1


def test_wikidata_discoverer_uses_offset_to_find_new_organizations() -> None:
    sent: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req.content.decode())
        return httpx.Response(200, json=load("wikidata_gb_recorded_2026-11-03.json"))

    d = WikidataDiscoverer(S, httpx.MockTransport(handler))
    out = d.discover(DiscoveryQuery(regions=["GB"]), already_known=30)
    assert "OFFSET+30" in sent[0] or "OFFSET%2030" in sent[0] or "OFFSET 30" in sent[0]
    assert out and all(c.origin == "wikidata" and c.country == "GB" for c in out)


def test_github_discoverer_keeps_only_self_published_websites() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/search/users":
            return httpx.Response(
                200, json={"items": [{"login": "a"}, {"login": "b"}, {"login": "c"}]}
            )
        data = {
            "a": {
                "login": "a",
                "name": "Alpha AI",
                "blog": "https://alpha.example",
                "location": "Berlin",
                "html_url": "https://github.com/a",
            },
            "b": {"login": "b", "blog": "", "html_url": "https://github.com/b"},
            "c": {"login": "c", "blog": "https://www.linkedin.com/company/c", "html_url": "x"},
        }
        return httpx.Response(200, json=data[p.rsplit("/", 1)[-1]])

    out = GitHubOrgDiscoverer(S, httpx.MockTransport(handler)).discover(DiscoveryQuery(), 0)
    assert [(c.domain, c.name) for c in out] == [("alpha.example", "Alpha AI")]


def test_github_rate_limit_stops_politely_without_inventing_candidates() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/search/users":
            return httpx.Response(200, json={"items": [{"login": "a"}]})
        return httpx.Response(403)

    assert GitHubOrgDiscoverer(S, httpx.MockTransport(handler)).discover(DiscoveryQuery(), 0) == []


def test_search_discovery_is_off_unless_explicitly_enabled_and_keyed() -> None:
    with pytest.raises(ConnectorError, match="off"):
        BraveSearchDiscoverer(S).discover(DiscoveryQuery(), 0)
    on = Settings(database_url="sqlite://", search_discovery_enabled=True, brave_api_key="k")  # type: ignore[arg-type]
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://agency.example/partners", "title": "Agency"},
                        {"url": "https://upwork.com/x", "title": "no"},
                    ]
                }
            },
        )

    out = BraveSearchDiscoverer(on, httpx.MockTransport(handler)).discover(DiscoveryQuery(), 0)
    assert seen[0].headers["x-subscription-token"] == "k"
    assert [c.domain for c in out] == ["agency.example"] and out[
        0
    ].page_url == "https://agency.example/partners"


def test_unknown_discoverer_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown discoverers"):
        build_discoverers(Settings(database_url="sqlite://", enabled_discoverers="nope"))


# ------------------------------------------------------------------ workflow: discovery -> verification
def _fake_checker(pages: dict[str, tuple[int, str]]):  # type: ignore[no-untyped-def]
    from app.services.source_check import SourceChecker

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            *pages.get(f"https://{req.headers['host']}{req.url.path}", (404, ""))[:1],
            text=pages.get(f"https://{req.headers['host']}{req.url.path}", (404, ""))[1],
        )

    return SourceChecker(
        transport=httpx.MockTransport(handler), resolver=lambda h: ["93.184.216.34"]
    )


def test_candidates_are_verified_by_reading_their_pages_and_unknown_orgs_get_stored(
    session,
) -> None:  # type: ignore[no-untyped-def]
    for dom in ("silent.example", "model.example", "dark.example"):
        upsert_candidate(session, Candidate(dom, name=dom, origin="wikidata", country="GB"))
    session.commit()
    pages = {
        "https://model.example/partners": (
            200,
            '<p>Our delivery runs through white-label partner teams for every client.</p><a href="mailto:partners@model.example">Write</a>',
        ),
        "https://model.example/contact": (
            200,
            '<form action="/send"></form><p>Head of Partnerships: p@model.example</p>',
        ),
        "https://silent.example/partners": (
            200,
            "<p>We are a full-service agency building apps.</p>",
        ),
    }
    from app.orchestrator.graph import build_graph  # noqa: F401

    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    s = Settings(database_url="sqlite://", candidates_per_run=10)
    run = execute_run(session, run.id, [], s, checker=_fake_checker(pages))
    assert run.status == "succeeded"
    assert run.state["candidate_stats"] == {
        "checked": 3,
        "signal": 1,
        "no_signal": 1,
        "blocked": 0,
        "error": 1,
    }
    status = {c.domain: c.status for c in session.execute(select(SourceCandidate)).scalars()}
    assert status == {
        "model.example": "signal",
        "silent.example": "no_signal",
        "dark.example": "error",
    }
    opps = session.execute(select(Opportunity)).scalars().all()
    assert len(opps) == 1  # only the organization whose page STATED something becomes a record
    o = opps[0]
    assert o.category == "verified_partnership" and o.channel == "agency"
    assert assess_opportunity(session, o, s).triage == "needs_verification"
    kinds = {(r.kind, r.value) for r in session.execute(select(ContactRoute)).scalars()}
    assert ("email", "partners@model.example") in kinds and ("email", "p@model.example") in kinds
    assert any(k == "inquiry_form" for k, _ in kinds)

    # second run: nothing new to read, no duplicates, no extra records
    enqueue_run(session, "full_discovery", {"query": {}})
    r2 = claim_next_run(session)
    assert r2 is not None
    r2 = execute_run(session, r2.id, [], s, checker=_fake_checker(pages))
    assert r2.state["candidate_stats"]["checked"] == 0
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == 1


def test_discoverer_failure_is_recorded_and_does_not_fail_the_run(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", enabled_discoverers="search")  # key missing -> error
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    run = execute_run(session, run.id, [], s)
    assert run.status == "succeeded"
    assert run.state["source_discovery"][0]["error"].startswith("search discovery is off")


# ------------------------------------------------------------------ classification of direct buyers
def test_direct_buyer_with_ai_request_and_contact_is_actionable_but_expired_is_rejected(
    session,
) -> None:  # type: ignore[no-untyped-def]
    def f(title: str, status: str, deadline: datetime | None):  # type: ignore[no-untyped-def]
        return RawFinding(
            kind=SignalKind.PROJECT_REQUEST, title=title, source_url="https://b.example/" + title[:5],
            original_text="Seeking a supplier to design and deploy a generative AI assistant.",
            published_at=NOW, organization_name="Buyer " + title[:5], source_status=status,  # type: ignore[arg-type]
            deadline=deadline, contact_route="proc@buyer.example",
        )  # fmt: skip

    ok = ingest_finding(
        session, f("Open AI assistant", "open", NOW + timedelta(days=9)), "ted_api", None, 90
    )
    old = ingest_finding(
        session, f("Expired AI assistant", "closed", NOW - timedelta(days=3)), "ted_api", None, 90
    )
    assert (
        assess_opportunity(session, session.get(Opportunity, ok.opportunity_id), S).triage
        == "actionable"
    )  # type: ignore[arg-type]
    r = assess_opportunity(session, session.get(Opportunity, old.opportunity_id), S)  # type: ignore[arg-type]
    assert r.triage == "rejected" and "closed" in (r.rejection_reason or "")


def test_hiring_post_without_ai_or_request_is_rejected_not_a_project(session) -> None:  # type: ignore[no-untyped-def]
    thread = {
        "children": [
            {
                "id": 9,
                "created_at": "2026-10-20T10:00:00Z",
                "text": "SEEKING FREELANCER | Need a bookkeeper for our shop. hi@shop.example",
            }
        ]
    }
    f = HackerNewsFreelancerConnector.parse(thread, S.topic_term_list)[0]
    out = ingest_finding(session, f, "hn_freelancer_threads", None, 90)
    r = assess_opportunity(session, session.get(Opportunity, out.opportunity_id), S)  # type: ignore[arg-type]
    assert r.triage == "rejected" and "not_relevant" in (r.rejection_reason or "")


def test_mock_ai_is_never_attached_to_real_records(session) -> None:  # type: ignore[no-untyped-def]
    from app.models import AiAnalysis

    f = RawFinding(
        kind=SignalKind.PROJECT_REQUEST, title="Generative AI assistant", source_url="https://b.example/1",
        original_text="Seeking a supplier to build a generative AI assistant.", published_at=NOW,
        organization_name="Real Buyer", source_status="open", deadline=NOW + timedelta(days=5),
        contact_route="p@buyer.example",
    )  # fmt: skip
    out = ingest_finding(session, f, "ted_api", None, 90)
    session.commit()
    s = Settings(database_url="sqlite://")  # mock LLM (default)
    from app.agents.ai_analysis import AIAnalysisAgent
    from app.ai.gateway import LLMGateway
    from app.ai.tasks import AITasks
    from app.llm import build_llm
    from app.services.spend import SpendGuard

    gw = LLMGateway(session, s, build_llm(s), SpendGuard(session, s))
    res = AIAnalysisAgent(session, AITasks(session, gw), s).run([str(out.opportunity_id)], None)
    assert res["considered"] == 0 and session.execute(select(AiAnalysis)).first() is None


# ------------------------------------------------------------------ false positives seen in the real run
def _a(title, text, **kw):  # type: ignore[no-untyped-def]
    from app.services.triage import assess

    base = dict(
        category="confirmed_demand", org_kind="buyer", source_status="open", deadline=NOW + timedelta(days=9),
        budget_text=None, contact_route="p@buyer.example", known_contacts=0, settings=S,
    )  # fmt: skip
    base.update(kw)
    return assess(title=title, text=text, **base)


def test_company_named_rag_is_not_a_rag_project() -> None:
    r = _a(
        "Germany – Electricity distribution and control apparatus – Delivery of two frames",
        "RAG Aktiengesellschaft requests delivery of two frames for the plant. Contact RAG AG.",
        org_name="RAG Aktiengesellschaft",
    )
    assert r.technical_relevance == "not_relevant" and r.triage == "rejected"


def test_individual_recruitment_is_rejected_as_hiring_not_a_project() -> None:
    r = _a("Congo Digital Acceleration Project", "Recrutement d'un Expert-Chief Data & AI Officer")
    assert r.triage == "rejected" and "hiring_post" in (r.rejection_reason or "")


def test_hardware_supply_for_ai_is_not_an_ai_implementation_project() -> None:
    r = _a(
        "Italy – Servers – Supply and commissioning of an IT infrastructure for generative AI workloads",
        "Open procedure for the supply, installation and commissioning of servers for generative AI.",
    )
    assert r.technical_relevance == "not_relevant" and r.triage == "rejected"


def test_survey_about_ai_is_not_actionable() -> None:
    r = _a(
        "Ireland – Survey services – Survey on the use and impact of artificial intelligence at work",
        "The contractor will conduct a telephone survey examining artificial intelligence use.",
    )
    assert r.technical_relevance != "relevant" and r.triage != "actionable"


def test_genuine_software_ai_tender_still_passes_after_tightening() -> None:
    r = _a(
        "France – Software programming and consultancy services – Implementation of a chatbot for the website",
        "The contractor will design and deploy a customer chatbot.",
    )
    assert r.technical_relevance == "relevant" and r.triage == "actionable"


def test_checker_follows_the_sites_own_partner_links_and_classifies_silent_vs_down() -> None:
    from app.connectors.agencies import PartnerPageChecker

    pages = {
        "https://linked.example/": (
            200,
            '<a href="/en/our-partner-network">Our partner network</a><a href="https://other.example/partner">x</a>',
        ),
        "https://linked.example/en/our-partner-network": (
            200,
            "<p>We deliver client work together with trusted subcontractors across Europe.</p>",
        ),
        "https://quiet.example/": (200, "<p>Welcome to our agency.</p>"),
    }
    chk = PartnerPageChecker(S, _fake_checker(pages))
    status, _, found = chk.check("linked.example")
    assert (
        status == "signal"
        and found[0].source_url == "https://linked.example/en/our-partner-network"
    )
    assert chk.check("quiet.example")[0] == "no_signal"  # reachable but silent: no record, no error
    assert chk.check("down.example")[0] == "error"  # unreachable is distinct from silent


def test_old_post_without_an_open_deadline_is_rejected_as_stale() -> None:
    r = _a(
        "AI chatbot build",
        "Seeking a freelancer to build a chatbot.",
        source_status="unknown",
        deadline=None,
        stale=True,
    )
    assert r.triage == "rejected" and "stale" in (r.rejection_reason or "")
    fresh = _a(
        "AI chatbot build",
        "Seeking a freelancer to build a chatbot.",
        source_status="unknown",
        deadline=None,
        stale=False,
    )
    assert fresh.triage == "needs_verification"


# ------------------------------------------------------------------ Milestone 5: intent sources
def test_intent_agency_seeking_vs_offering_vs_recruitment() -> None:
    from app.services.intent import classify_intent

    t = S.topic_term_list
    seeking = "We are looking for AI delivery partners and subcontractors to join our client projects. Contact partners@x.example."
    assert classify_intent(seeking, t).kind == "agency_seeking"
    offering = "We provide white-label AI solutions and development services. Book a free call today with our team."
    assert classify_intent(offering, t).kind == "agency_offering"
    hiring = (
        "We are hiring a full-time machine learning engineer. Salary competitive. Join our team."
    )
    assert classify_intent(hiring, t).kind == "recruitment"
    assert (
        classify_intent(
            "AI is transforming every industry according to analysts this year.", t
        ).kind
        == "none"
    )


def test_buyer_needs_request_plus_build_plus_ai_in_one_sentence() -> None:
    from app.services.intent import classify_intent

    t = S.topic_term_list
    ok = "We are looking for a developer to build an AI agent that triages support tickets, budget $6,000."
    assert classify_intent(ok, t).kind == "buyer_request"
    generic = "Generative AI is a big topic. Many companies are looking for ways to cut costs this quarter."
    assert classify_intent(generic, t).kind == "none"
    news = "Company announces new chatbot platform for enterprises. The platform can build agents."
    assert classify_intent(news, t).kind != "buyer_request"


def test_marketplaces_job_boards_and_socials_are_excluded() -> None:
    from app.services.intent import is_excluded_source

    for u in (
        "https://www.upwork.com/jobs/x",
        "https://fr.linkedin.com/jobs/1",
        "https://www.reddit.com/r/forhire",
        "https://uk.indeed.com/viewjob",
        "https://clutch.co/agencies",
        "https://www.toptal.com/ai",
    ):
        assert is_excluded_source(u), u
    assert not is_excluded_source("https://agency.example/partners")


def test_page_date_comes_only_from_machine_readable_markup() -> None:
    from app.services.intent import page_date

    assert (
        page_date('<meta property="article:published_time" content="2026-10-28T09:00:00Z">').day
        == 28
    )  # type: ignore[union-attr]
    assert (
        page_date(
            '<script type="application/ld+json">{"datePublished":"2026-10-01"}</script>'
        ).month
        == 10
    )  # type: ignore[union-attr]
    assert page_date("<p>Posted a while ago</p>") is None


def test_canadabuys_csv_keeps_open_ai_notices_and_flags_closed() -> None:
    from app.connectors.intent_sources import CanadaBuysConnector

    csv_text = (
        "title-titre-eng,tenderDescription-descriptionAppelOffres-eng,noticeURL-URLavis-eng,tenderStatus-appelOffresStatut-eng,"
        "tenderClosingDate-appelOffresDateCloture,publicationDate-datePublication,contactInfoEmail-informationsContactCourriel,"
        "contractingEntityName-nomEntitContractante-eng\n"
        'Machine learning pilot for case triage,"The department seeks a supplier to develop and deploy a machine learning model.",https://merx.example/1,Open,2099-01-15,2026-10-20,Proc@Dept.example,Dept of Examples\n'
        'Old chatbot,"Build a chatbot.",https://merx.example/2,Expired,2025-01-15,2025-01-01,,Dept of Examples\n'
        'Office chairs,"Supply of chairs.",https://merx.example/3,Open,2099-01-15,2026-10-20,,Dept of Examples\n'
    )
    found = CanadaBuysConnector.parse(csv_text, S.topic_term_list)
    assert [f.title for f in found] == ["Machine learning pilot for case triage", "Old chatbot"]
    assert found[0].source_status == "open" and found[0].contact_route == "proc@dept.example"
    assert found[1].source_status == "closed" and found[0].organization_country == "CAN"


def test_find_a_tender_builds_notice_urls_from_release_ids() -> None:
    from app.connectors.intent_sources import FindATenderConnector

    soon = (NOW + timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rel = {
        "id": "103904-2026",
        "tender": {
            "title": "Chatbot for residents",
            "description": "Develop a chatbot.",
            "classification": {"id": "72000000"},
            "tenderPeriod": {"endDate": soon},
            "datePublished": "2026-11-01T00:00:00Z",
        },
        "parties": [
            {
                "name": "Council",
                "roles": ["buyer"],
                "contactPoint": {"email": "Tenders@Council.example"},
            }
        ],
    }
    found = FindATenderConnector.parse(
        {"releases": [rel]}, S.topic_term_list, FindATenderConnector._notice_url
    )
    assert found[0].source_url == "https://www.find-tender.service.gov.uk/Notice/103904-2026"
    assert found[0].contact_route == "tenders@council.example" and found[0].source_status == "open"


def _intent_settings(**kw):  # type: ignore[no-untyped-def]
    return Settings(
        database_url="sqlite://",
        search_discovery_enabled=True,
        brave_api_key="k",
        intent_queries_agency="q-agency",
        intent_queries_buyer="q-buyer",
        **kw,
    )  # type: ignore[arg-type]


def test_search_intent_reads_pages_and_keeps_only_dated_stated_requests() -> None:
    from app.connectors.intent_sources import SearchIntentConnector

    results = {
        "q-agency": [
            {"url": "https://alpha.example/news/partners", "title": "Alpha"},
            {"url": "https://www.upwork.com/jobs/1", "title": "marketplace"},
            {"url": "https://offer.example/services", "title": "Offer"},
        ],
        "q-buyer": [
            {"url": "https://buyer.example/brief", "title": "Brief"},
            {"url": "https://undated.example/brief", "title": "Undated"},
        ],
    }
    pages = {
        "https://alpha.example/news/partners": '<meta property="article:published_time" content="2026-10-28T09:00:00Z"><p>We are looking for AI delivery partners and subcontractors for our client projects. Email partners@alpha.example.</p>',
        "https://offer.example/services": "<p>We provide white-label AI solutions and development services. Book a free call today.</p>",
        "https://buyer.example/brief": '<meta property="article:published_time" content="2026-10-30T09:00:00Z"><p>We are looking for a developer to build an AI agent for support triage. Write to hi@buyer.example.</p>',
        "https://undated.example/brief": "<p>We are looking for a developer to build an AI agent for support triage.</p>",
    }

    def api(req: httpx.Request) -> httpx.Response:
        q = req.url.params["q"]
        assert req.url.params["freshness"] == "pm" and req.headers["x-subscription-token"] == "k"
        return httpx.Response(200, json={"web": {"results": results[q]}})

    c = SearchIntentConnector(_intent_settings(), httpx.MockTransport(api), _checker_pages(pages))
    res = c.discover(DiscoveryQuery())
    by_url = {f.source_url: f for f in res.findings}
    assert set(by_url) == {
        "https://alpha.example/news/partners",
        "https://buyer.example/brief",
        "https://undated.example/brief",
    }
    a, b, u = (
        by_url["https://alpha.example/news/partners"],
        by_url["https://buyer.example/brief"],
        by_url["https://undated.example/brief"],
    )
    assert a.kind is SignalKind.PARTNER_ACTIVITY and a.partnership_class == "active_requirement"
    assert a.contact_route == "partners@alpha.example" and a.published_at is not None
    assert b.kind is SignalKind.PROJECT_REQUEST and b.contact_route == "hi@buyer.example"
    assert u.published_at is None  # kept for audit, but undated = never current
    assert res.actual_cost_usd == pytest.approx(0.01)  # 2 requests x $0.005


def _checker_pages(pages):  # type: ignore[no-untyped-def]
    return _fake_checker({k: (200, v) for k, v in pages.items()})


def test_undated_search_hit_is_rejected_as_stale_and_dated_one_needs_verification(session) -> None:  # type: ignore[no-untyped-def]
    from app.connectors.intent_sources import SearchIntentConnector

    pages = {
        "https://buyer.example/brief": '<meta property="article:published_time" content="NOWSTAMP"><p>We are looking for a developer to build an AI agent for support triage. Write to hi@buyer.example.</p>'.replace(
            "NOWSTAMP", NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
        ),
        "https://undated.example/brief": "<p>We are looking for a developer to build an AI agent for support triage.</p>",
    }

    def api(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"web": {"results": [{"url": u, "title": "t"} for u in pages]}}
        )

    c = SearchIntentConnector(_intent_settings(), httpx.MockTransport(api), _checker_pages(pages))
    for f in c.discover(DiscoveryQuery(max_results=10)).findings:
        ingest_finding(session, f, "intent_search", None, 90)
    got = [
        str(assess_opportunity(session, o, S).triage)
        for o in session.execute(select(Opportunity)).scalars()
    ]
    assert sorted(got) == ["needs_verification", "rejected"]  # never "actionable": no open status


def test_search_intent_is_off_without_explicit_enable_and_key() -> None:
    from app.connectors.intent_sources import SearchIntentConnector

    with pytest.raises(ConnectorError, match="off"):
        SearchIntentConnector(S).discover(DiscoveryQuery())
    est = SearchIntentConnector(_intent_settings()).estimate_cost(DiscoveryQuery())
    assert est.usd == pytest.approx(0.01)


def test_search_budget_cap_blocks_before_any_request(session) -> None:  # type: ignore[no-untyped-def]
    from app.services.spend import SpendGuard, SpendLimitExceeded

    s = Settings(database_url="sqlite://", search_budget_usd=0.01)
    g = SpendGuard(session, s)
    g.reserve(0.01, None, "brave", "brave:intent_search", "discovery")
    with pytest.raises(SpendLimitExceeded, match="brave"):
        g.reserve(0.005, None, "brave", "brave:intent_search", "discovery")


def test_untitled_findings_from_different_sites_are_not_merged(session) -> None:  # type: ignore[no-untyped-def]
    def mk(host: str) -> RawFinding:
        return RawFinding(
            kind=SignalKind.PROJECT_REQUEST, title="Brief", source_url=f"https://{host}/b",
            original_text=f"Looking for a developer to build an AI agent ({host}).", published_at=NOW,
        )  # fmt: skip

    a = ingest_finding(session, mk("one.example"), "intent_search", None, 90)
    b = ingest_finding(session, mk("two.example"), "intent_search", None, 90)
    assert a.opportunity_id != b.opportunity_id


def test_equipment_supply_titles_are_not_software_projects_even_without_a_category_prefix() -> None:
    r = _a(
        "Supply, Delivery, Installation and Commissioning of an Advanced Fluorescence Microscope",
        "The microscope uses machine learning for image analysis. Supplier must install and commission it.",
    )
    assert r.technical_relevance == "not_relevant" and r.triage == "rejected"


# ------------------------------------------------------------------ Apify SERP intent (mock transport only)
class _FakeSerp:
    def __init__(self, by_actor):  # type: ignore[no-untyped-def]
        self.by_actor, self.runs, self.inputs = by_actor, [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if req.method == "POST" and p.endswith("/runs"):
            actor = p.split("/acts/")[1].split("/runs")[0]
            self.runs.append((actor, req.url.params["maxTotalChargeUsd"]))
            self.inputs.append(json.loads(req.content))
            return httpx.Response(201, json={"data": {"id": actor, "status": "RUNNING"}})
        if p.startswith("/v2/actor-runs/"):
            rid = p.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": rid,
                        "status": "SUCCEEDED",
                        "defaultDatasetId": rid,
                        "usageTotalUsd": 0.0123,
                    }
                },
            )
        if p.startswith("/v2/datasets/"):
            return httpx.Response(200, json=self.by_actor[p.rsplit("/", 2)[-2]])
        return httpx.Response(404)


def _serp(urls):  # type: ignore[no-untyped-def]
    return [{"organicResults": [{"url": u, "title": "t"} for u in urls]}]


def _apify_intent(**kw):  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.connectors.apify_client import ApifyClient
    from app.connectors.intent_sources import ApifySearchIntentConnector

    pages = kw.pop("pages")
    fake = _FakeSerp(kw.pop("serp"))
    s = Settings(
        database_url="sqlite://", apify_paid_execution_approved=kw.pop("approved", True),
        intent_queries_agency="a1|a2", intent_queries_buyer="b1|b2", apify_search_bing_queries=1,
    )  # fmt: skip
    client = ApifyClient(SecretStr("t"), transport=httpx.MockTransport(fake), poll_seconds=0)
    return ApifySearchIntentConnector(s, client, _checker_pages(pages)), fake


def test_apify_intent_estimate_matches_published_pricing_and_stays_under_the_cap() -> None:
    c, _ = _apify_intent(pages={}, serp={})
    est = c.estimate_cost(DiscoveryQuery()).usd
    # google: 2 runs x (0.00005 + 2 pages x 0.0045); bing: 2 runs x (0.0005 + 10 results x 0.0045)
    assert est == pytest.approx(2 * (0.00005 + 2 * 0.0045) + 2 * (0.0005 + 10 * 0.0045))
    assert est < 0.25


def test_apify_intent_requires_paid_approval() -> None:
    c, fake = _apify_intent(pages={}, serp={}, approved=False)
    with pytest.raises(ConnectorError, match="not approved"):
        c.discover(DiscoveryQuery())
    assert fake.runs == []


def test_apify_intent_runs_two_engines_caps_each_run_and_reads_only_stated_requests() -> None:
    serp = {
        "apify~google-search-scraper": _serp(
            ["https://alpha.example/partners", "https://www.upwork.com/x"]
        ),
        "tri_angle~bing-search-scraper": _serp(["https://buyer.example/brief"]),
    }
    pages = {
        "https://alpha.example/partners": '<meta property="article:published_time" content="2026-10-28T09:00:00Z"><p>We are looking for AI delivery partners and subcontractors for our client projects. Email partners@alpha.example.</p>',
        "https://buyer.example/brief": '<meta property="article:published_time" content="2026-10-30T09:00:00Z"><p>We are looking for a developer to build an AI agent for support triage. Write to hi@buyer.example.</p>',
    }
    c, fake = _apify_intent(pages=pages, serp=serp)
    res = c.discover(DiscoveryQuery())
    actors = [a for a, _ in fake.runs]
    assert sorted(set(actors)) == ["apify~google-search-scraper", "tri_angle~bing-search-scraper"]
    assert all(float(cap) == 0.5 for _, cap in fake.runs)  # Apify's minimum ceiling, user-approved
    assert fake.inputs[0]["queries"] == "a1\na2" and fake.inputs[0]["maxPagesPerQuery"] == 1
    assert (
        "quickDateRange" in fake.inputs[0] and "quickDateRange" not in fake.inputs[2]
    )  # bing has no such field
    kinds = {f.source_url: f.kind for f in res.findings}
    assert kinds == {
        "https://alpha.example/partners": SignalKind.PARTNER_ACTIVITY,
        "https://buyer.example/brief": SignalKind.PROJECT_REQUEST,
    }
    assert res.actual_cost_usd == pytest.approx(
        4 * 0.0123
    )  # the provider-reported usage, not the estimate


def test_provider_rejection_releases_the_reservation_and_is_not_retried(session) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.agents.discovery import OpportunityIntelligenceAgent
    from app.connectors.apify_client import ApifyClient
    from app.connectors.intent_sources import ApifySearchIntentConnector
    from app.models import ProviderSpend
    from app.services.spend import SpendGuard

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"type": "max-total-charge-usd-below-minimum"}})

    s = Settings(
        database_url="sqlite://", apify_paid_execution_approved=True, apify_test_budget_usd=0.25
    )
    c = ApifySearchIntentConnector(
        s, ApifyClient(SecretStr("t"), transport=httpx.MockTransport(handler), poll_seconds=0), None
    )
    agent = OpportunityIntelligenceAgent(session, [c], SpendGuard(session, s))
    out = agent.run(DiscoveryQuery(), None)
    assert calls["n"] == 1  # a 4xx is final: no retries
    assert out["errors"] and "rejected" in out["errors"][0].lower()
    spent = [float(r.amount_usd) for r in session.execute(select(ProviderSpend)).scalars()]
    assert spent == [0.0]  # nothing was billed, so nothing stays reserved


# ------------------------------------------------------------------ false positives seen in the paid search run
def test_agency_marketing_blog_about_clients_needing_partners_is_not_a_request() -> None:
    from app.services.intent import classify_intent, is_editorial

    blog = "Series A teams with existing engineers need an AI product development partner that can embed and hand over smoothly."
    assert classify_intent(blog, S.topic_term_list).kind != "agency_seeking"
    assert is_editorial(
        "How to Choose an AI Development Agency in 2026 | Anivia Labs",
        "https://x.example/blog/how-to-choose",
    )
    assert is_editorial(
        "Top 10 generative AI agent development companies", "https://x.example/top-10"
    )
    assert not is_editorial("Partners wanted", "https://agency.example/partners")


def test_first_person_request_still_counts() -> None:
    from app.services.intent import classify_intent

    t = S.topic_term_list
    assert (
        classify_intent(
            "We are looking for AI delivery partners and subcontractors for client work.", t
        ).kind
        == "agency_seeking"
    )
    assert (
        classify_intent(
            "Third party analysts are looking for a developer to build an AI agent.", t
        ).kind
        != "buyer_request"
    )


def test_a_connector_serving_both_agents_is_called_and_billed_once_per_run(session) -> None:  # type: ignore[no-untyped-def]
    from app.connectors.base import CostEstimate, DiscoveryResult
    from app.models import ProviderSpend

    class Both:
        name, is_mock = "apify_both", False
        kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.PARTNER_ACTIVITY})
        calls = 0

        def estimate_cost(self, q):  # type: ignore[no-untyped-def]
            return CostEstimate(usd=0.05)

        def discover(self, q):  # type: ignore[no-untyped-def]
            Both.calls += 1
            return DiscoveryResult(provider=self.name, findings=[], actual_cost_usd=0.02)

    s = Settings(database_url="sqlite://", apify_test_budget_usd=0.25)
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    run = execute_run(session, run.id, [Both()], s)  # type: ignore[list-item]
    assert run.status == "succeeded" and Both.calls == 1
    spent = [float(r.amount_usd) for r in session.execute(select(ProviderSpend)).scalars()]
    assert spent == [0.02]
