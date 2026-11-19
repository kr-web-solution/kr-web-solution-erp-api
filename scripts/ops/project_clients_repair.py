"""Idempotent repair: add missing project_clients from projects.client_id (is_primary=true).

Dry-run by default. Never creates duplicates.
  COMMIT_REPAIR=YES to persist.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras


def resolve_url() -> tuple[str, str, str]:
    url = (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        text = Path(".env").read_text(encoding="utf-8")
        m = re.search(r"^DATABASE_URL=(.+)$", text, re.M)
        if not m:
            raise SystemExit("No DATABASE_URL")
        url = m.group(1).strip().strip('"').strip("'")
    u = url.replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(u)
    return u, p.hostname or "", (p.path or "/").lstrip("/")


def main() -> int:
    u, host, db = resolve_url()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.db_target_guard import assert_migration_or_seed_allowed

    assert_migration_or_seed_allowed(u, tool="project_clients_repair")
    commit = os.environ.get("COMMIT_REPAIR") == "YES"
    print("database=", db, "host=", host, "mode=", "COMMIT" if commit else "DRY_RUN")

    conn = psycopg2.connect(u)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT p.id, p.client_id, p.name
            FROM projects p
            WHERE p.deleted_at IS NULL
              AND p.client_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
              )
            ORDER BY p.id
            """
        )
        missing = list(cur.fetchall())
        print("missing_count=", len(missing))
        for row in missing:
            print("  would_insert", row["id"], "client=", row["client_id"], "name=", row["name"])

        if missing:
            cur.executemany(
                """
                INSERT INTO project_clients (project_id, client_id, is_primary)
                VALUES (%(id)s, %(client_id)s, true)
                ON CONFLICT DO NOTHING
                """,
                missing,
            )
            print("insert_attempted=", len(missing), "rowcount=", cur.rowcount)

        # Idempotent second pass should insert 0
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM projects p
            WHERE p.deleted_at IS NULL
              AND p.client_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
              )
            """
        )
        remaining = cur.fetchone()["n"]
        print("remaining_after=", remaining)

        if commit:
            conn.commit()
            print("COMMITTED")
        else:
            conn.rollback()
            print("DRY_RUN rolled back")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
