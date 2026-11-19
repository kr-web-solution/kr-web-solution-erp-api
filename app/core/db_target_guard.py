"""Refuse accidental alembic/seed against non-local or tunnelled databases."""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlparse, unquote


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TEST_NAME_RE = re.compile(
    r"(^|[_\-])test([_\-]|$)|(^|[_\-])pytest([_\-]|$)|_testing$",
    re.IGNORECASE,
)
# Default local allow-list — SSH tunnel to prod named kr-web-solution-erp is NOT included.
_DEFAULT_LOCAL_ALLOW = frozenset(
    {
        "kr-web-solution-erp-test",
        "kr_workspace_erp_test",
        "kr_workspace_erp",
    }
)


def database_name(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return unquote((parsed.path or "").lstrip("/").split("?")[0])


def database_host(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return (parsed.hostname or "").lower()


def is_local_host(url: str) -> bool:
    host = database_host(url)
    return host in _LOCAL_HOSTS or host.endswith(".local")


def local_allow_list() -> set[str]:
    raw = os.environ.get("ALLOWED_LOCAL_DB_NAMES", "").strip()
    if raw:
        return {x.strip() for x in raw.split(",") if x.strip()}
    return set(_DEFAULT_LOCAL_ALLOW)


def _has_db_environment_marker(url: str) -> bool:
    """Optional: settings.company_name or a GUC — best-effort, never raises as allow."""
    marker = os.environ.get("REQUIRE_DB_ENV_MARKER", "").strip()
    if not marker:
        return False
    try:
        import psycopg2

        u = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = psycopg2.connect(u)
        try:
            conn.set_session(readonly=True, autocommit=True)
            cur = conn.cursor()
            # Prefer explicit key if column exists; else skip
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'settings' AND column_name = 'environment_marker'
                """
            )
            if not cur.fetchone():
                return False
            cur.execute(
                "SELECT environment_marker FROM settings WHERE id = 'default'"
            )
            row = cur.fetchone()
            return bool(row and row[0] == marker)
        finally:
            conn.close()
    except Exception:
        return False


def assert_migration_or_seed_allowed(url: str, *, tool: str) -> None:
    """Abort unless safe local DB or explicit remote confirmation.

    Localhost alone is NOT enough (SSH tunnels). Require:
      - DB name matches test/pytest pattern, OR
      - DB name in ALLOWED_LOCAL_DB_NAMES, OR
      - settings.environment_marker matches REQUIRE_DB_ENV_MARKER

    Non-local hosts require:
      --i-know-this-is-remote (or CONFIRM_REMOTE_DB=YES)
      CONFIRM_DB_NAME=<exact database name>
    """
    actual = database_name(url)
    host = database_host(url)

    if is_local_host(url):
        if _TEST_NAME_RE.search(actual or ""):
            return
        if actual in local_allow_list():
            return
        if _has_db_environment_marker(url):
            return
        raise SystemExit(
            f"REFUSING {tool}: host {host!r} looks local but database {actual!r} "
            f"is not in ALLOWED_LOCAL_DB_NAMES and is not a *-test name. "
            f"This blocks SSH tunnels to production. Add the name to the allow-list "
            f"only for intentional local/dev DBs, or set REQUIRE_DB_ENV_MARKER + "
            f"settings.environment_marker."
        )

    argv = " ".join(sys.argv)
    flagged = (
        "--i-know-this-is-remote" in argv
        or os.environ.get("CONFIRM_REMOTE_DB", "").strip() == "YES"
    )
    expected = os.environ.get("CONFIRM_DB_NAME", "").strip()
    if flagged and expected and expected == actual:
        print(
            f"WARNING: {tool} proceeding against NON-LOCAL database {actual!r} "
            f"on host {host!r} (explicit confirmation present)."
        )
        return

    raise SystemExit(
        f"REFUSING {tool}: database host {host!r} is not local. "
        f"Use a local DB, or pass --i-know-this-is-remote and set "
        f"CONFIRM_DB_NAME={actual!r} (exact name) plus CONFIRM_REMOTE_DB=YES."
    )
