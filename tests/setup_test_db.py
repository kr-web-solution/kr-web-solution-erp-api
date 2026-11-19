"""Create/migrate the dedicated local test database. Never targets prod/staging."""

from __future__ import annotations

import asyncio
import os
import subprocess
from urllib.parse import urlparse

import asyncpg

from tests.db_safety import assert_safe_test_database

async def reset_test_database() -> None:
    test_url = (os.environ.get("TEST_DATABASE_URL") or "").strip()
    if not test_url:
        raise SystemExit(
            "Set TEST_DATABASE_URL to a dedicated local test database "
            "(name must contain 'test' or 'pytest'). No password default is baked into this script."
        )
    os.environ["ENVIRONMENT"] = "test"
    os.environ["PRODUCTION"] = "false"
    db_name = assert_safe_test_database(
        database_url=test_url,
        environment="test",
        production_flag="false",
    )

    parsed = urlparse(test_url.replace("postgresql+asyncpg://", "postgresql://"))
    user = parsed.username or "postgres"
    password = parsed.password or ""
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    admin_url = f"postgresql://{user}:{password}@{host}:{port}/postgres"

    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid();
            """,
            db_name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        print(f"Recreated clean test database: {db_name}")
    finally:
        await conn.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = test_url
    env["ENVIRONMENT"] = "local"
    env["PRODUCTION"] = "false"
    res = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    print("Alembic stdout:", res.stdout)
    if res.returncode != 0:
        print("Alembic stderr:", res.stderr)
        raise SystemExit(res.returncode)
    print("Alembic upgrade head OK")


if __name__ == "__main__":
    asyncio.run(reset_test_database())
