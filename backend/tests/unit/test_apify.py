import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.connectors.apify import ApifyActorConnector
from app.connectors.apify_client import ApifyClient
from app.connectors.apify_specs import SPECS, parse_date
from app.connectors.base import ConnectorError, DiscoveryQuery, SignalKind
from app.connectors.registry import build_connectors, register_connector
from app.core.config import Settings
from app.models import Contact, Evidence, Opportunity, ProviderSpend
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run

FIX = Path(__file__).parent.parent / "fixtures" / "apify"


def load(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIX / name).read_text())
    return data


class FakeApify:
    """Records every request; emulates the documented run -> poll -> dataset flow."""

    def __init__(self, items: list[dict[str, Any]], usage: float = 0.01) -> None:
        self.items, self.usage = items, usage
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/runs"):
            return httpx.Response(201, json={"data": {"id": "run1", "status": "RUNNING"}})
        if path == "/v2/actor-runs/run1":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "run1",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "ds1",
                        "usageTotalUsd": self.usage,
                    }
                },
            )
        if path == "/v2/datasets/ds1/items":
            return httpx.Response(200, json=self.items)
        return httpx.Response(404, json={})


def make(spec_name: str, items: list[dict[str, Any]], settings: Settings, usage: float = 0.01):  # type: ignore[no-untyped-def]
    fake = FakeApify(items, usage)
    client = ApifyClient(
        SecretStr("test-token"), transport=httpx.MockTransport(fake), poll_seconds=0
    )
    return ApifyActorConnector(SPECS[spec_name], settings, client), fake


@pytest.fixture
def approved(settings: Settings) -> Settings:
    return settings.model_copy(update={"apify_paid_execution_approved": True})


def test_ted_maps_only_valid_open_notices(approved: Settings) -> None:
    conn, fake = make("apify_ted_tenders", load("ted_tenders_items.json"), approved)
    q = DiscoveryQuery(keywords=["AI", "machine learning"], regions=["DEU", "XXX"], max_results=50)
    result = conn.discover(q)

    assert [f.title for f in result.findings] == [
        "Development of an AI knowledge assistant for citizen services"
    ]
    f = result.findings[0]
    assert f.kind is SignalKind.PROJECT_REQUEST
    assert f.source_url.startswith("https://ted.example/notice/100001-2026")
    assert "retrieval-based assistant" in f.original_text  # verbatim description kept
    assert f.published_at is not None and f.contacts == []
    assert result.actual_cost_usd == 0.01

    post = next(r for r in fake.requests if r.method == "POST")
    assert post.url.path == "/v2/acts/foxlabs~ted-tenders/runs"
    assert post.headers["authorization"] == "Bearer test-token"
    body = json.loads(post.content)
    assert "noticeTypes" not in body  # open calls are selected when mapping
    assert body["countries"] == ["DEU"]  # unsupported region dropped, not guessed
    assert body["keywords"] == "AI, machine learning" and body["maxResults"] == 50
    assert float(post.url.params["maxTotalChargeUsd"]) <= approved.spend_limit_per_run_usd


def test_worldbank_contacts_only_when_printed(approved: Settings) -> None:
    conn, _ = make("apify_worldbank_tenders", load("worldbank_items.json"), approved)
    result = conn.discover(DiscoveryQuery(keywords=["machine learning"]))
    assert len(result.findings) == 2
    assert [c.email for c in result.findings[0].contacts] == ["procurement@agri.example"]
    assert result.findings[1].contacts == []  # blank name / malformed email => nothing invented


def test_paid_execution_is_gated_and_makes_no_http_call(settings: Settings) -> None:
    assert settings.apify_paid_execution_approved is False
    conn, fake = make("apify_ted_tenders", load("ted_tenders_items.json"), settings)
    with pytest.raises(ConnectorError, match="not approved"):
        conn.discover(DiscoveryQuery())
    assert fake.requests == []


def test_missing_token_blocks_when_approved(approved: Settings) -> None:
    conn = ApifyActorConnector(SPECS["apify_ted_tenders"], approved)
    with pytest.raises(ConnectorError, match="APIFY_TOKEN"):
        conn.discover(DiscoveryQuery())


def test_unreviewed_spec_cannot_be_enabled(approved: Settings) -> None:
    assert SPECS["apify_clutch_agencies"].reviewed is False
    with pytest.raises(ValueError, match="not approved"):
        ApifyActorConnector(SPECS["apify_clutch_agencies"], approved)
    with pytest.raises(ValueError):
        build_connectors(["apify_clutch_agencies"], approved)


def test_cost_estimate_is_upper_bound(settings: Settings) -> None:
    conn = ApifyActorConnector(SPECS["apify_ted_tenders"], settings)
    assert conn.estimate_cost(DiscoveryQuery(max_results=10)).usd == pytest.approx(0.04005)
    # capped by the spec's max_results_cap even if the query asks for more
    assert conn.estimate_cost(DiscoveryQuery(max_results=500)).usd == pytest.approx(0.40005)


def test_run_failure_surfaces_as_connector_error(approved: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "r", "status": "FAILED"}})
        return httpx.Response(404)

    client = ApifyClient(SecretStr("t"), transport=httpx.MockTransport(handler), poll_seconds=0)
    conn = ApifyActorConnector(SPECS["apify_ted_tenders"], approved, client)
    with pytest.raises(ConnectorError, match="FAILED"):
        conn.discover(DiscoveryQuery())


def test_api_error_does_not_leak_token(approved: Settings) -> None:
    client = ApifyClient(
        SecretStr("super-secret"),
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized")),
        poll_seconds=0,
    )
    conn = ApifyActorConnector(SPECS["apify_ted_tenders"], approved, client)
    with pytest.raises(ConnectorError) as exc:
        conn.discover(DiscoveryQuery())
    assert "super-secret" not in str(exc.value)


def test_parse_date_never_guesses() -> None:
    assert parse_date("2026-10-28").year == 2026  # type: ignore[union-attr]
    assert parse_date("20261028").month == 10  # type: ignore[union-attr]
    assert parse_date("31.10.2026").day == 31  # type: ignore[union-attr]
    assert parse_date("2026-10-28T10:00:00Z") is not None
    assert parse_date("soon") is None and parse_date(None) is None and parse_date("") is None


def _run(session, settings, connectors):  # type: ignore[no-untyped-def]
    enqueue_run(session, "full_discovery", {"query": DiscoveryQuery().model_dump()})
    run = claim_next_run(session)
    assert run is not None
    return execute_run(session, run.id, connectors, settings)


def test_workflow_with_apify_persists_dedupes_and_tracks_spend(session, approved) -> None:  # type: ignore[no-untyped-def]
    ted, _ = make("apify_ted_tenders", load("ted_tenders_items.json"), approved, usage=0.02)
    wb, _ = make("apify_worldbank_tenders", load("worldbank_items.json"), approved, usage=0.03)
    run = _run(session, approved, [ted, wb])
    assert run.status == "succeeded"

    opps = session.execute(select(Opportunity)).scalars().all()
    # TED: 1 valid; World Bank: 2 rows are the same notice (canonical URL + title) => 1
    assert len(opps) == 2
    assert {o.category for o in opps} == {"confirmed_demand"}
    assert all(o.verification_status != "verified" for o in opps)

    evs = session.execute(select(Evidence)).scalars().all()
    assert {e.provider for e in evs} == {"apify_ted_tenders", "apify_worldbank_tenders"}
    assert all(e.source_url and e.original_text for e in evs)
    assert len(evs) == 2  # trailing-slash duplicate collapsed

    assert [c.email for c in session.execute(select(Contact)).scalars()] == [
        "procurement@agri.example"
    ]
    spend = session.execute(select(func.sum(ProviderSpend.amount_usd))).scalar_one()
    assert float(spend) == pytest.approx(0.05)

    n_opp = len(opps)
    _run(session, approved, [ted, wb])  # same results again
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == n_opp
    assert session.execute(select(func.count()).select_from(Evidence)).scalar_one() == 2


def test_spend_limit_blocks_apify_before_any_request(session, approved) -> None:  # type: ignore[no-untyped-def]
    strict = approved.model_copy(update={"spend_limit_per_run_usd": 0.01})
    conn, fake = make("apify_ted_tenders", load("ted_tenders_items.json"), strict)
    run = _run(session, strict, [conn])
    assert run.status == "succeeded"
    assert fake.requests == []  # blocked pre-flight
    assert any("blocked" in e for e in run.state["errors"])
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == 0


def test_connector_selection_by_name(session, approved) -> None:  # type: ignore[no-untyped-def]
    ted, fake_t = make("apify_ted_tenders", load("ted_tenders_items.json"), approved)
    wb, fake_w = make("apify_worldbank_tenders", load("worldbank_items.json"), approved)
    q = DiscoveryQuery(connector_names=["apify_worldbank_tenders"]).model_dump()
    enqueue_run(session, "full_discovery", {"query": q})
    run = claim_next_run(session)
    assert run is not None
    execute_run(session, run.id, [ted, wb], approved)
    assert fake_t.requests == [] and fake_w.requests


def test_registered_provider_is_swappable(approved) -> None:  # type: ignore[no-untyped-def]
    conn, _ = make("apify_ted_tenders", [], approved)
    register_connector("swap_test", lambda s: conn)
    assert build_connectors(["swap_test"], approved)[0] is conn


def test_clutch_mapping_is_company_signal_never_partnership() -> None:
    f = SPECS["apify_clutch_agencies"].map_item(load("clutch_items.json")[0], DiscoveryQuery())
    assert f is not None and f.kind is SignalKind.COMPANY_SIGNAL


def test_recorded_worldbank_item_has_a_parsed_date_and_is_not_stale() -> None:
    """Recorded from the real Actor on 2026-11-03: dates look like '15-Oct-2026'."""
    assert parse_date("15-Oct-2026") is not None and parse_date("15-Oct-2026").year == 2026  # type: ignore[union-attr]
    item = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/apify/worldbank_recorded_2026-11-03.json"
        ).read_text()
    )[0]
    spec = SPECS["apify_worldbank_tenders"]
    f = spec.map_item(item, DiscoveryQuery())
    assert f is not None and f.published_at is not None
    assert f.organization_name == "Bangladesh Public Procurement Authority"
    item["submissionDeadlineDate"] = "2020-01-01"
    assert spec.map_item(item, DiscoveryQuery()) is None  # closed windows are skipped


def test_ted_cpv_filter_only_used_without_keywords() -> None:
    spec = SPECS["apify_ted_tenders"]
    with_kw = spec.build_input(DiscoveryQuery(keywords=["generative AI"]), 20)
    assert "cpvCodes" not in with_kw and with_kw["keywords"] == "generative AI"
    assert "cpvCodes" in spec.build_input(DiscoveryQuery(), 20)


def test_ted_only_open_calls_are_mapped() -> None:
    """Field names/values recorded from a real probe on 2026-11-03."""
    spec = SPECS["apify_ted_tenders"]
    base = {
        "title": "Norway – IT services: consulting, software development",
        "detailUrl": "https://ted.europa.eu/en/notice/-/detail/755731-2026",
        "publicationDate": "2026-10-30",
        "deadlineDate": "2099-11-30",
        "buyerName": "FORSVARSMATERIELL",
        "buyerCountry": "NOR",
    }
    assert "noticeTypes" not in spec.build_input(DiscoveryQuery(keywords=["x"]), 5)
    f = spec.map_item({**base, "noticeType": "cn-standard"}, DiscoveryQuery())
    assert f is not None and f.published_at is not None and f.organization_country == "NOR"
    assert spec.map_item({**base, "noticeType": "can-standard"}, DiscoveryQuery()) is None
