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


def assert_safe_test_db(url: str) -> None:
    """Integration tests DROP the public schema. Refuse anything that is not clearly a throwaway
    test database, for EVERY test that receives the URL (not only the fixture that drops)."""
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in db_name:
        pytest.fail(
            f"Refusing to use {db_name!r} as a test database (it may be the live database). "
            "Point TEST_DATABASE_URL at a dedicated database whose name contains 'test'."
        )


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    assert_safe_test_db(url)
    return url
