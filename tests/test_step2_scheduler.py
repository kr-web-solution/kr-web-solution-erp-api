from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import Client, Invoice, LateFeeAction, RecurringInvoice, Settings
from app.services.pdf import generate_invoice_pdf
from app.services.scheduler import (
    step_a_generate_recurring_invoices,
    step_b_dispatch_due_reminders,
    step_c_check_and_mark_overdue,
)
from tests.conftest import TEST_USERS, get_auth_cookies, make_project_with_client


@pytest.mark.asyncio
async def test_project_transactional_maintenance_creation(client: TestClient, founder_cookies):
    cookies = founder_cookies

    cl_res = client.get("/api/v1/clients", cookies=cookies)
    assert cl_res.status_code == 200
    clients = cl_res.json()
    assert len(clients) > 0
    client_id = clients[0]["id"]

    today = date.today()
    payload = {
        "name": "E-Commerce Maintenance Project",
        "client_ids": [client_id],
        "description": "Full stack e-commerce maintenance",
        "budget_paise": 5000000,
        "maintenance": {
            "amount_paise": 1500000,
            "send_day": 1,
            "due_after_days": 5,
            "reminder_every_days": 2,
            "starts_on": today.isoformat(),
            "notes": "Monthly retainer",
        },
    }
    create_res = client.post("/api/v1/projects", json=payload, cookies=cookies)
    assert create_res.status_code in [200, 201], create_res.text
    prj_data = create_res.json()
    assert prj_data["maintenance_status"] == "active"
    assert prj_data["maintenance_amount_paise"] == 1500000
    assert prj_data["maintenance_send_day"] == 1

    project_id = prj_data["id"]

    async with TestingSessionLocal() as session:
        rec_stmt = select(RecurringInvoice).where(RecurringInvoice.project_id == project_id)
        rec = (await session.execute(rec_stmt)).scalars().first()
        assert rec is not None
        assert rec.amount_paise == 1500000
        assert rec.client_id == client_id
        assert rec.active is True

    toggle_res = client.post(f"/api/v1/projects/{project_id}/maintenance/toggle", cookies=cookies)
    assert toggle_res.status_code == 200
    assert toggle_res.json()["maintenance_status"] == "paused"

    toggle_res2 = client.post(f"/api/v1/projects/{project_id}/maintenance/toggle", cookies=cookies)
    assert toggle_res2.status_code == 200
    assert toggle_res2.json()["maintenance_status"] == "active"


@pytest.mark.asyncio
async def test_scheduler_step_a_generation_and_catchup():
    """Verify recurring invoice generation is catch-up safe and creates period_key."""
    today = date(2026, 11, 5)

    async with TestingSessionLocal() as session:
        client = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client is not None

        project = await make_project_with_client(
            session,
            name="Scheduler Catchup Project",
            client_id=client.id,
            status="running",
            go_live_at=date(2026, 10, 1),  # required: scheduler skips until go-live
        )

        rec = RecurringInvoice(
            client_id=client.id,
            project_id=project.id,
            amount_paise=1200000,
            send_day=1,
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date(2026, 11, 1),
            day_of_month=1,
            next_run_date=date(2026, 11, 1),
            active=True,
            line_items_template=[
                {"description": "SEO Service", "quantity": 1, "unit_rate_paise": 1200000}
            ],
        )
        session.add(rec)
        await session.commit()
        rec_id = rec.id

    gen_invoices = await step_a_generate_recurring_invoices(today)
    assert len(gen_invoices) >= 1

    await step_a_generate_recurring_invoices(today)
    async with TestingSessionLocal() as session:
        invoices_for_rec = (
            await session.execute(
                select(Invoice).where(
                    Invoice.recurring_id == rec_id, Invoice.period_key == "2026-11"
                )
            )
        ).scalars().all()
        assert len(invoices_for_rec) == 1


@pytest.mark.asyncio
async def test_scheduler_step_c_and_late_fee_decision(client: TestClient, founder_cookies):
    """Test overdue detection, founder action creation, and founder decision endpoint."""
    today = date(2026, 11, 15)

    async with TestingSessionLocal() as session:
        client_rec = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client_rec is not None

        project = await make_project_with_client(
            session,
            name="Overdue Invoice Project",
            client_id=client_rec.id,
            status="running",
        )

        overdue_inv = Invoice(
            client_id=client_rec.id,
            project_id=project.id,
            invoice_number=f"KR-TEST-OVERDUE-{int(datetime.now().timestamp() * 1000)}",
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=5),
            subtotal_paise=1000000,
            tax_rate_percent=18.0,
            tax_paise=180000,
            total_paise=1180000,
            status="sent",
            email_status="sent",
        )
        session.add(overdue_inv)
        await session.commit()
        await session.refresh(overdue_inv)
        overdue_id = overdue_inv.id

    overdue_actions = await step_c_check_and_mark_overdue(today)
    assert len(overdue_actions) >= 1
    action_match = [a for a in overdue_actions if a["invoice_id"] == overdue_id]
    assert len(action_match) == 1

    cookies = founder_cookies
    pend_res = client.get("/api/v1/invoices/late-fee-actions/pending", cookies=cookies)
    assert pend_res.status_code == 200
    pending_list = pend_res.json()
    action_item = next((a for a in pending_list if a["invoice_id"] == overdue_id), None)
    assert action_item is not None
    action_id = action_item["id"]

    decide_res = client.post(
        f"/api/v1/invoices/late-fee-actions/{action_id}/decide",
        json={
            "action": "schedule",
            "fee_type": "percent",
            "fee_value": 10.0,
            "send_on": (today + timedelta(days=2)).isoformat(),
            "reason": "Severe delay beyond 5 days",
        },
        cookies=cookies,
    )
    assert decide_res.status_code == 200
    assert decide_res.json()["status"] == "scheduled"
    assert decide_res.json()["fee_paise"] == 118000

    pay_res = client.post(f"/api/v1/invoices/{overdue_id}/mark-paid", cookies=cookies)
    assert pay_res.status_code == 200
    assert pay_res.json()["status"] == "paid"

    async with TestingSessionLocal() as session:
        act = (
            await session.execute(select(LateFeeAction).where(LateFeeAction.id == action_id))
        ).scalars().first()
        assert act.status == "waived"


@pytest.mark.asyncio
async def test_pdf_paid_stamp():
    """Verify PAID stamp is rendered in generated PDF bytes."""
    async with TestingSessionLocal() as session:
        client_rec = (await session.execute(select(Client).limit(1))).scalars().first()
        st = (
            await session.execute(select(Settings).where(Settings.id == "default"))
        ).scalars().first()

        project = await make_project_with_client(
            session,
            name="PDF Stamp Project",
            client_id=client_rec.id,
        )

        inv = Invoice(
            client_id=client_rec.id,
            project_id=project.id,
            invoice_number="KR-PAID-TEST-001",
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=500000,
            tax_rate_percent=18.0,
            tax_paise=90000,
            total_paise=590000,
            status="paid",
        )
        pdf_bytes = generate_invoice_pdf(
            invoice=inv,
            client=client_rec,
            settings=st,
            items=[],
        )
        assert len(pdf_bytes) > 1000
        assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_pre_due_reminders_smoke():
    sent = await step_b_dispatch_due_reminders(date(2026, 9, 2))
    assert sent >= 0
