"""Soft-deleted recurring plans must not appear in list API."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import RecurringInvoice
from tests.conftest import make_project_with_client
from tests.test_client_project_rules import _make_client


@pytest.mark.asyncio
async def test_list_recurring_excludes_soft_deleted(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    async with TestingSessionLocal() as session:
        c = await _make_client(session, f"PlanClient-{tag}")
        p = await make_project_with_client(
            session, name=f"PlanProject-{tag}", client_id=c.id, status="running"
        )
        alive = RecurringInvoice(
            client_id=c.id,
            project_id=p.id,
            amount_paise=50000,
            send_day=1,
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date.today(),
            day_of_month=1,
            next_run_date=date.today(),
            active=True,
            notes=f"alive-{tag}",
        )
        gone = RecurringInvoice(
            client_id=c.id,
            project_id=p.id,
            amount_paise=90000,
            send_day=1,
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date.today(),
            day_of_month=1,
            next_run_date=date.today(),
            active=True,
            notes=f"gone-{tag}",
        )
        session.add_all([alive, gone])
        await session.flush()
        gone.soft_delete()
        gone.active = False
        await session.commit()
        alive_id, gone_id = alive.id, gone.id

    res = client.get("/api/v1/invoices/recurring", cookies=founder_cookies)
    assert res.status_code == 200, res.text
    ids = {row["id"] for row in res.json()}
    assert alive_id in ids
    assert gone_id not in ids
