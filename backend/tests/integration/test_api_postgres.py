"""Runs against a real PostgreSQL: applies Alembic migrations, then exercises the API."""

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.connectors.registry import build_connectors
from app.core.config import Settings
from app.orchestrator.runner import claim_next_run, execute_run

pytestmark = pytest.mark.integration


@pytest.fixture
def client(pg_url, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", pg_url)
    from app.core.config import get_settings
    from app.db.session import get_engine, get_session_factory

    for fn in (get_settings, get_engine, get_session_factory):
        fn.cache_clear()
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(Config("alembic.ini"), "head")
    from app.main import create_app

    yield TestClient(create_app())
    for fn in (get_settings, get_engine, get_session_factory):
        fn.cache_clear()


def _process_queue(pg_url: str) -> None:
    settings = Settings(database_url=pg_url)
    connectors = build_connectors(["mock_opportunities", "mock_agencies"])
    with sessionmaker(bind=create_engine(pg_url))() as s:
        run = claim_next_run(s)
        assert run is not None
        execute_run(s, run.id, connectors, settings)


def test_health_and_ready(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_end_to_end_with_approval_gate(client, pg_url) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/runs", json={})
    assert r.status_code == 202
    run_id = r.json()["id"]
    _process_queue(pg_url)
    assert client.get(f"/runs/{run_id}").json()["status"] == "succeeded"

    demand = client.get("/opportunities", params={"category": "confirmed_demand"}).json()
    assert {o["category"] for o in demand} == {"confirmed_demand"}
    detail = client.get(f"/opportunities/{demand[0]['id']}").json()
    assert detail["evidence"] and detail["evidence"][0]["source_url"]

    drafts = client.get("/drafts", params={"status": "pending_approval"}).json()
    assert drafts
    d = drafts[0]
    # placeholders block approval; nothing is approved without a named human
    assert client.post(f"/drafts/{d['id']}/approve", json={"approver": "me"}).status_code == 409
    edited = client.patch(f"/drafts/{d['id']}", json={"body": "Final text written by a human."})
    assert edited.status_code == 200
    ok = client.post(f"/drafts/{d['id']}/approve", json={"approver": "me"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(f"/drafts/{d['id']}/approve", json={"approver": "me"}).status_code == 409
    assert client.post(f"/drafts/{d['id']}/approve", json={"approver": ""}).status_code == 422


def test_stale_cannot_be_verified(client, pg_url) -> None:  # type: ignore[no-untyped-def]
    client.post("/runs", json={})
    _process_queue(pg_url)
    stale = next(
        o for o in client.get("/opportunities").json() if o["verification_status"] == "stale"
    )
    assert (
        client.post(f"/opportunities/{stale['id']}/verify", json={"approver": "me"}).status_code
        == 409
    )


def test_connector_status_blocks_unreviewed_and_flags_paid(client) -> None:  # type: ignore[no-untyped-def]
    by_name = {c["name"]: c for c in client.get("/connectors").json()}
    assert by_name["mock_opportunities"]["paid"] is False
    assert by_name["apify_ted_tenders"]["paid"] is True
    assert by_name["apify_ted_tenders"]["enabled"] is False  # opt-in only
    assert by_name["apify_ted_tenders"]["paid_execution_approved"] is False
    assert by_name["apify_clutch_agencies"]["usable"] is False


def test_search_profiles_drive_runs(client, pg_url) -> None:  # type: ignore[no-untyped-def]
    bad = client.post("/search-profiles", json={"name": "x", "connector_names": ["nope"]})
    assert bad.status_code == 422
    r = client.post(
        "/search-profiles",
        json={
            "name": "EU AI tenders",
            "keywords": ["machine learning"],
            "regions": ["DEU", "FRA"],
            "connector_names": ["mock_opportunities"],
            "max_results": 10,
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.post("/search-profiles", json={"name": "EU AI tenders"}).status_code == 409

    run = client.post("/runs", json={"profile_id": pid}).json()
    state_q = client.get(f"/runs/{run['id']}").json()
    assert state_q["status"] == "queued"
    _process_queue(pg_url)
    done = client.get(f"/runs/{run['id']}").json()
    assert done["status"] == "succeeded"
    # profile restricted the run to one provider: only opportunity-side categories appear
    cats = {o["category"] for o in client.get("/opportunities").json()}
    assert "verified_partnership" not in cats and "confirmed_demand" in cats

    ev = client.get("/evidence", params={"run_id": run["id"]}).json()
    assert ev and all(e["provider"] == "mock_opportunities" for e in ev)
    assert all(e["source_check_status"] == "not_checked" for e in ev)  # verification off by default

    assert (
        client.patch(f"/search-profiles/{pid}", json={"enabled": False}).json()["enabled"] is False
    )
    assert client.post("/runs", json={"profile_id": pid}).status_code == 409
