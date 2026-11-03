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
