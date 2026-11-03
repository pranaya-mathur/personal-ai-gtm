import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        spend_limit_per_run_usd=1.0,
        spend_limit_daily_usd=5.0,
        spend_limit_monthly_usd=50.0,
        worker_max_attempts=2,
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


SANDBOX_MARKER = "gtm-test-sandbox"  # COMMENT ON DATABASE <db> IS 'gtm-test-sandbox' (explicit, by an operator)


def _identity(url: str) -> tuple[str, str, str]:
    from sqlalchemy.engine import make_url

    u = make_url(url)
    host = {"127.0.0.1": "localhost", "::1": "localhost"}.get(u.host or "", u.host or "localhost")
    return host, str(u.port or 5432), u.database or ""


def live_identities() -> set[tuple[str, str, str]]:
    """Every database the application itself may be configured to use (env + .env defaults)."""
    from app.core.config import Settings

    out: set[tuple[str, str, str]] = set()
    for u in {os.environ.get("DATABASE_URL"), Settings().database_url}:
        if u and not u.startswith("sqlite"):
            out.add(_identity(u))
    return out


def assert_safe_test_db(url: str, *, probe: bool = False) -> None:
    """Integration tests DROP the public schema, so a name containing "test" is NOT enough.

    Refused unless ALL hold: it is not the application's configured database (host/port/name),
    its name is not a well-known live name, and - with probe=True - the server confirms the
    connected database is the one named AND carries the explicit sandbox marker comment that an
    operator placed on it (scripts/create_test_db.sh). A live database never has that marker."""
    host, port, db_name = _identity(url)
    if not db_name or "test" not in db_name:
        pytest.fail(f"Refusing to use {db_name!r} as a test database: name must contain 'test'.")
    if db_name in {"gtm", "postgres", "template0", "template1"} or (host, port, db_name) in live_identities():
        pytest.fail(f"Refusing {db_name!r}: it is (or aliases) the application database.")
    if not probe:
        return
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    try:
        with eng.connect() as c:
            current = c.execute(text("select current_database()")).scalar_one()
            comment = c.execute(
                text("select shobj_description(oid, 'pg_database') from pg_database where datname = current_database()")
            ).scalar_one_or_none()
    finally:
        eng.dispose()
    if current != db_name:
        pytest.fail(f"Refusing: connected database {current!r} is not {db_name!r}.")
    if comment != SANDBOX_MARKER:
        pytest.fail(
            f"Refusing {db_name!r}: it lacks the explicit sandbox marker. An operator must run "
            "scripts/create_test_db.sh (never done automatically) before destructive tests."
        )


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    assert_safe_test_db(url, probe=True)
    return url


ADMIN_TOKEN, OPERATOR_TOKEN, VIEWER_TOKEN = "a" * 24, "o" * 24, "v" * 24


@pytest.fixture
def api(monkeypatch):  # type: ignore[no-untyped-def]
    """FastAPI TestClient over an in-memory SQLite database (never touches PostgreSQL)."""
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.api.auth import reset_rate_limits
    from app.core.config import get_settings
    from app.db.session import get_db
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv(
        "API_TOKENS",
        f"alice:admin:{ADMIN_TOKEN},olga:operator:{OPERATOR_TOKEN},victor:viewer:{VIEWER_TOKEN}",
    )
    get_settings.cache_clear()
    reset_rate_limits()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    def _db():  # type: ignore[no-untyped-def]
        with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer " + OPERATOR_TOKEN})
    c.factory = factory  # type: ignore[attr-defined]
    yield c
    get_settings.cache_clear()
