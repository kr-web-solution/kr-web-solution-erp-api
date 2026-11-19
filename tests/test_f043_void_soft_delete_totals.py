"""F-043: voided and soft-deleted invoices must not change revenue/unpaid totals."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import Invoice
from app.routers.dashboard import _revenue_by_month
from app.services.client_project import create_project
from app.services.project_money import summarize_invoices
from tests.test_client_project_rules import _make_client
from sqlalchemy import func, select
from app.core.database import not_deleted


@pytest.mark.asyncio
async def test_project_money_and_revenue_ignore_void_and_soft_deleted():
    async with TestingSessionLocal() as session:
        c = await _make_client(session, "F043 Money Client")
        project = await create_project(session, name="F043 Money Project", client_ids=[c.id])
        live = Invoice(
            invoice_number=f"F043-LIVE-{project.id[-6:]}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="sent",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal_paise=50_000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=50_000,
        )
        voided = Invoice(
            invoice_number=f"F043-VOID-{project.id[-6:]}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="cancelled",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal_paise=99_000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=99_000,
        )
        soft = Invoice(
            invoice_number=f"F043-SOFT-{project.id[-6:]}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=project.id,
            status="sent",
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=7),
            subtotal_paise=88_000,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=88_000,
        )
        session.add_all([live, voided, soft])
        await session.flush()
        soft.soft_delete()
        await session.commit()
        cid = c.id

        from types import SimpleNamespace

        summary = summarize_invoices(
            SimpleNamespace(budget_paise=0, recurring_invoices=[]),
            [live, voided, soft],
        )
        assert summary["development"]["total_invoiced"] == 50_000

        # Unpaid total: only live sent
        unpaid = await session.scalar(
            select(func.coalesce(func.sum(Invoice.total_paise), 0)).where(
                not_deleted(Invoice),
                Invoice.client_id == cid,
                Invoice.status.in_(("sent", "overdue")),
            )
        )
        assert int(unpaid) == 50_000

        points = await _revenue_by_month(session, date.today(), client_id=cid)
        billed = sum(p.billed_paise for p in points)
        assert billed == 50_000


def test_dashboard_unchanged_when_only_void_or_soft_added(client: TestClient, founder_cookies):
    before = client.get("/api/v1/dashboard", cookies=founder_cookies)
    assert before.status_code == 200
    b = before.json()
    unpaid0 = b["unpaid_invoices_total_paise"]
    billed0 = sum(p["billed_paise"] for p in b.get("revenue_by_month") or [])
    running0 = b["running_projects_count"]

    c = client.post(
        "/api/v1/clients",
        json={"name": "F043 Dash Client", "email": "f043-dash@example.com"},
        cookies=founder_cookies,
    )
    assert c.status_code == 201
    # Soft-deleted client alone should not affect running/unpaid/billed
    # (no project created)
    after = client.get("/api/v1/dashboard", cookies=founder_cookies)
    assert after.status_code == 200
    a = after.json()
    assert a["unpaid_invoices_total_paise"] == unpaid0
    assert sum(p["billed_paise"] for p in a.get("revenue_by_month") or []) == billed0
    assert a["running_projects_count"] == running0
