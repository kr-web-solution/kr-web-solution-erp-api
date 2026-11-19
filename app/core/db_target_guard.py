"""Refuse accidental alembic/seed against non-local databases."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse, unquote


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def database_name(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return unquote((parsed.path or "").lstrip("/").split("?")[0])


def database_host(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return (parsed.hostname or "").lower()


def is_local_database(url: str) -> bool:
    host = database_host(url)
    return host in _LOCAL_HOSTS or host.endswith(".local")


def assert_migration_or_seed_allowed(url: str, *, tool: str) -> None:
    """Abort unless local DB or explicit remote confirmation.

    Requires both:
      --i-know-this-is-remote  (CLI arg or CONFIRM_REMOTE_DB=YES)
      CONFIRM_DB_NAME=<exact database name>
    """
    if is_local_database(url):
        return

    argv = " ".join(sys.argv)
    flagged = (
        "--i-know-this-is-remote" in argv
        or os.environ.get("CONFIRM_REMOTE_DB", "").strip() == "YES"
    )
    expected = os.environ.get("CONFIRM_DB_NAME", "").strip()
    actual = database_name(url)
    if flagged and expected and expected == actual:
        print(
            f"WARNING: {tool} proceeding against NON-LOCAL database {actual!r} "
            f"on host {database_host(url)!r} (explicit confirmation present)."
        )
        return

    raise SystemExit(
        f"REFUSING {tool}: database host {database_host(url)!r} is not local. "
        f"Use a local DB, or pass --i-know-this-is-remote and set "
        f"CONFIRM_DB_NAME={actual!r} (exact name) plus CONFIRM_REMOTE_DB=YES."
    )
