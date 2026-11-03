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
