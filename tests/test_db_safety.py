"""Unit tests for the pytest database safety guard."""

import pytest

from tests.db_safety import UnsafeTestDatabaseError, assert_safe_test_database, is_clearly_test_database


@pytest.mark.parametrize(
    "name,ok",
    [
        ("kr-web-solution-erp-test", True),
        ("kr_workspace_erp_test", True),
        ("pytest_kr", True),
        ("kr-web-solution-erp", False),
        ("kr_workspace_erp", False),
        ("production", False),
        ("", False),
    ],
)
def test_is_clearly_test_database(name, ok):
    assert is_clearly_test_database(name) is ok


def test_guard_accepts_test_url():
    name = assert_safe_test_database(
        database_url="postgresql+asyncpg://u:p@localhost:5432/kr-web-solution-erp-test",
        environment="test",
        production_flag="false",
    )
    assert name == "kr-web-solution-erp-test"


def test_guard_rejects_prod_env():
    with pytest.raises(UnsafeTestDatabaseError, match="ENVIRONMENT"):
        assert_safe_test_database(
            database_url="postgresql+asyncpg://u:p@localhost:5432/kr-web-solution-erp-test",
            environment="production",
            production_flag="false",
        )


def test_guard_rejects_non_test_db_name():
    with pytest.raises(UnsafeTestDatabaseError, match="clearly indicates"):
        assert_safe_test_database(
            database_url="postgresql+asyncpg://u:p@localhost:5432/kr-web-solution-erp",
            environment="local",
            production_flag="false",
        )
