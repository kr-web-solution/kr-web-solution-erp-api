"""023 view upgrade/downgrade roundtrip on the isolated test DB only."""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlalchemy import text

from tests.conftest import TestingSessionLocal


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    env["ENVIRONMENT"] = "local"
    env["PRODUCTION"] = "false"
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.asyncio
async def test_023_views_roundtrip_excludes_void():
    """Upgrade → confirm void filter → downgrade to 022 → upgrade 023 again."""
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr

    async with TestingSessionLocal() as session:
        def_023 = (
            await session.execute(
                text("SELECT pg_get_viewdef('v_invoice_monthly_billed'::regclass, true)")
            )
        ).scalar_one()
    assert "void" in def_023.lower() or "cancelled" in def_023.lower()

    down = _alembic("downgrade", "e2f3a4b5c6d7")  # 022
    assert down.returncode == 0, down.stderr

    async with TestingSessionLocal() as session:
        def_022 = (
            await session.execute(
                text("SELECT pg_get_viewdef('v_invoice_monthly_billed'::regclass, true)")
            )
        ).scalar_one()
    assert "draft" in def_022.lower()

    up_again = _alembic("upgrade", "f3a4b5c6d7e8")
    assert up_again.returncode == 0, up_again.stderr

    async with TestingSessionLocal() as session:
        def_back = (
            await session.execute(
                text("SELECT pg_get_viewdef('v_invoice_monthly_billed'::regclass, true)")
            )
        ).scalar_one()
    assert "void" in def_back.lower() or "cancelled" in def_back.lower()

    final = _alembic("upgrade", "head")
    assert final.returncode == 0, final.stderr
