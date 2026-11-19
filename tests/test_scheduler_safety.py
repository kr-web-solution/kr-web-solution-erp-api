"""Scheduler enable gate + advisory lock single-runner."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core.database import engine
from app.services import scheduler as sched


def test_scheduler_off_in_test_environment(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            ENVIRONMENT="test",
            ENABLE_SCHEDULER=True,
            DATABASE_URL="postgresql+asyncpg://postgres:x@localhost:5432/kr-web-solution-erp-test",
            PRODUCTION=False,
        ),
        raising=False,
    )
    # Patch the import site used inside scheduler_should_start
    import app.core.config as cfg

    monkeypatch.setattr(
        cfg,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="test",
            ENABLE_SCHEDULER=True,
            DATABASE_URL="postgresql+asyncpg://postgres:x@localhost:5432/kr-web-solution-erp-test",
            PRODUCTION=False,
        ),
    )
    assert sched.scheduler_should_start() is False
    sched.stop_scheduler()
    sched.start_scheduler()
    assert sched.registered_job_ids() == []


def test_scheduler_default_off_outside_production(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(
        cfg,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="development",
            ENABLE_SCHEDULER=None,
            DATABASE_URL="postgresql+asyncpg://postgres:x@localhost:5432/kr-web-solution-erp",
            PRODUCTION=False,
        ),
    )
    assert sched.scheduler_should_start() is False


def test_scheduler_blocks_non_local_db_in_dev(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(
        cfg,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="development",
            ENABLE_SCHEDULER=True,
            DATABASE_URL="postgresql+asyncpg://u:p@203.0.113.10:5432/prod",
            PRODUCTION=False,
        ),
    )
    assert sched.scheduler_should_start() is False


@pytest.mark.asyncio
async def test_second_instance_skips_daily_jobs_via_advisory_lock():
    """While one connection holds the lock, a second run returns without work."""
    lock_id = sched.ADVISORY_LOCK_ID

    async with engine.connect() as holder:
        got = await holder.scalar(text(f"SELECT pg_try_advisory_lock({lock_id})"))
        assert got is True
        try:
            summary = await sched.run_daily_maintenance_jobs(today=date(2099, 1, 1))
            assert summary["invoices_generated"] == 0
            assert summary["reminders_sent"] == 0
        finally:
            await holder.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))
