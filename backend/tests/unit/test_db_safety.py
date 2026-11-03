"""The live database must be impossible to wipe from the test suite (it was, once)."""

import pytest

from tests.conftest import assert_safe_test_db


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://gtm:gtm@localhost:5433/gtm",
        "postgresql+psycopg://gtm:gtm@localhost:5432/gtm?sslmode=disable",
        "postgresql+psycopg://gtm:gtm@localhost:5433/postgres",
        "postgresql+psycopg://gtm:gtm@db:5432/production",
    ],
)
def test_live_looking_databases_are_refused(url: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="Refusing"):
        assert_safe_test_db(url)


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg://gtm:gtm@localhost:5433/gtm_test", "postgresql://u:p@h/scratch_test?x=1"],
)
def test_clearly_named_test_databases_are_allowed(url: str) -> None:
    assert_safe_test_db(url)


def test_name_containing_test_is_not_proof_without_the_sandbox_marker() -> None:
    # passes the name check, but the server probe must refuse an unreachable/unmarked database
    url = "postgresql+psycopg://gtm:gtm@localhost:1/my_test_data_live"
    assert_safe_test_db(url)
    with pytest.raises(BaseException):  # noqa: B017 - connection error or pytest.fail
        assert_safe_test_db(url, probe=True)


def test_the_configured_application_database_is_refused_even_if_named_test(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gtm:gtm@localhost:5433/gtm_test")
    with pytest.raises(pytest.fail.Exception, match="application database"):
        assert_safe_test_db("postgresql+psycopg://gtm:gtm@127.0.0.1:5433/gtm_test")
