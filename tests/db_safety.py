"""Hard guards so pytest never touches a non-test database."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse, unquote


# DB names that clearly indicate an isolated test database.
_TEST_DB_NAME_RE = re.compile(
    r"(^|[_\-])test([_\-]|$)|(^|[_\-])pytest([_\-]|$)|_testing$",
    re.IGNORECASE,
)

_FORBIDDEN_ENVS = frozenset({"production", "prod", "staging", "stage"})


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when pytest would connect to a non-test or production database."""


def database_name_from_url(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    name = unquote((parsed.path or "").lstrip("/").split("?")[0])
    return name


def is_clearly_test_database(db_name: str) -> bool:
    if not db_name:
        return False
    return bool(_TEST_DB_NAME_RE.search(db_name))


def assert_safe_test_database(
    *,
    database_url: str | None = None,
    environment: str | None = None,
    production_flag: str | None = None,
) -> str:
    """Abort pytest collection/setup if the target DB is not clearly a test DB.

    Returns the validated database name.
    """
    env = (environment if environment is not None else os.environ.get("ENVIRONMENT", "")).strip().lower()
    prod_raw = (
        production_flag
        if production_flag is not None
        else os.environ.get("PRODUCTION", "false")
    )
    production = str(prod_raw).strip().lower() in {"1", "true", "yes", "on"}

    if production or env in _FORBIDDEN_ENVS:
        raise UnsafeTestDatabaseError(
            f"Refusing to run tests: ENVIRONMENT={env!r} PRODUCTION={production}. "
            "Tests must use ENVIRONMENT=test|local|development against a dedicated test database."
        )

    url = (database_url or os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise UnsafeTestDatabaseError(
            "Refusing to run tests: set TEST_DATABASE_URL to a dedicated test database "
            "(database name must contain 'test' or 'pytest')."
        )

    db_name = database_name_from_url(url)
    if not is_clearly_test_database(db_name):
        raise UnsafeTestDatabaseError(
            f"Refusing to run tests against database {db_name!r}. "
            "TEST_DATABASE_URL must point at a DB whose name clearly indicates a test database "
            "(e.g. kr-web-solution-erp-test). Dev/staging/prod databases are blocked."
        )

    return db_name
