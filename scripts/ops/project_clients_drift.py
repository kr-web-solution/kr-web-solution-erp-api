"""Read-only: count/list active projects missing project_clients rows.

Usage:
  python scripts/project_clients_drift.py
  # uses DATABASE_URL / TEST_DATABASE_URL; never prints full URL
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

CUTOFF = "2026-11-19 07:36:00+00"


def resolve_url() -> tuple[str, str, str]:
    url = (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        text = Path(".env").read_text(encoding="utf-8")
        m = re.search(r"^DATABASE_URL=(.+)$", text, re.M)
        if not m:
            raise SystemExit("No DATABASE_URL / TEST_DATABASE_URL")
        url = m.group(1).strip().strip('"').strip("'")
    u = url.replace("postgresql+asyncpg://", "postgresql://")
    p = urlparse(u)
    return u, p.hostname or "", (p.path or "/").lstrip("/")


def main() -> int:
    u, host, db = resolve_url()
    # Read-only ops script — still refuse accidental remote unless confirmed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.db_target_guard import assert_migration_or_seed_allowed

    assert_migration_or_seed_allowed(u, tool="project_clients_drift")
    conn = psycopg2.connect(u)
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET default_transaction_read_only = on")
        cur.execute(
            """
            SELECT p.id, p.name, p.client_id, p.created_at, p.status
            FROM projects p
            WHERE p.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
              )
            ORDER BY p.created_at
            """
        )
        rows = list(cur.fetchall())
        after = [r for r in rows if r["created_at"] and str(r["created_at"]) >= CUTOFF]
        report = {
            "host": host,
            "database": db,
            "active_projects_zero_clients": len(rows),
            "created_after_0736_utc": len(after),
            "rows": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "client_id": r["client_id"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "status": r["status"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()
    out = Path(__file__).with_name("project_clients_drift_out.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("database=", db, "host=", host)
    print("zero_clients=", report["active_projects_zero_clients"])
    print("after_0736=", report["created_after_0736_utc"])
    print("wrote", out.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
