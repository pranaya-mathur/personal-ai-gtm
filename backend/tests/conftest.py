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


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url
