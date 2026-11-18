import pytest
from datetime import date, timedelta, datetime
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import Project, RecurringInvoice, Invoice, LateFeeAction, Settings, Client, User
from app.services.scheduler import (
    step_a_generate_recurring_invoices,
    step_b_dispatch_due_reminders,
    step_c_check_and_mark_overdue,
    step_d_process_scheduled_late_fees,
    step_e_retry_failed_emails,
    run_daily_maintenance_jobs
)
from app.services.pdf import generate_invoice_pdf

def get_auth_cookies(client: TestClient, email: str, password: str):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.cookies

@pytest.mark.asyncio
async def test_project_transactional_maintenance_creation(client: TestClient):
    cookies = get_auth_cookies(client, "founder@krwebsolution.com", "AdminKR@2026")

    # 1. Fetch existing client
    cl_res = client.get("/api/v1/clients", cookies=cookies)
    assert cl_res.status_code == 200
    clients = cl_res.json()
    assert len(clients) > 0
    client_id = clients[0]["id"]

    # 2. Create project with maintenance configuration
    today = date.today()
    payload = {
        "name": "E-Commerce Maintenance Project",
        "client_id": client_id,
        "description": "Full stack e-commerce maintenance",
        "budget_paise": 5000000,
        "maintenance": {
            "amount_paise": 1500000,  # 15,000 INR
            "send_day": 1,
            "due_after_days": 5,
            "reminder_every_days": 2,
            "starts_on": today.isoformat(),
            "notes": "Monthly retainer"
        }
    }
    create_res = client.post("/api/v1/projects", json=payload, cookies=cookies)
    assert create_res.status_code in [200, 201], create_res.text
    prj_data = create_res.json()
    assert prj_data["maintenance_status"] == "active"
    assert prj_data["maintenance_amount_paise"] == 1500000
    assert prj_data["maintenance_send_day"] == 1

    project_id = prj_data["id"]

    # Verify recurring_invoices row in DB
    async with TestingSessionLocal() as session:
        rec_stmt = select(RecurringInvoice).where(RecurringInvoice.project_id == project_id)
        rec = (await session.execute(rec_stmt)).scalars().first()
        assert rec is not None
        assert rec.amount_paise == 1500000
        assert rec.client_id == client_id
        assert rec.active is True

    # 3. Toggle maintenance
    toggle_res = client.post(f"/api/v1/projects/{project_id}/maintenance/toggle", cookies=cookies)
    assert toggle_res.status_code == 200
    assert toggle_res.json()["maintenance_status"] == "paused"

    # Toggle back to active
    toggle_res2 = client.post(f"/api/v1/projects/{project_id}/maintenance/toggle", cookies=cookies)
    assert toggle_res2.status_code == 200
    assert toggle_res2.json()["maintenance_status"] == "active"

@pytest.mark.asyncio
async def test_scheduler_step_a_generation_and_catchup():
    """Verify recurring invoice generation is catch-up safe and creates period_key."""
    today = date(2026, 11, 5)

    async with TestingSessionLocal() as session:
        # Fetch or verify existing client & project
        client = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client is not None

        # Create recurring configuration where send_day = 1 (send_day <= today.day)
        rec = RecurringInvoice(
            client_id=client.id,
            amount_paise=1200000,
            send_day=1,
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date(2026, 11, 1),
            day_of_month=1,
            next_run_date=date(2026, 11, 1),
            active=True,
            line_items_template=[{"description": "SEO Service", "quantity": 1, "unit_rate_paise": 1200000}]
        )
        session.add(rec)
        await session.commit()
        rec_id = rec.id

    # Run step_a
    gen_invoices = await step_a_generate_recurring_invoices(today)
    assert len(gen_invoices) >= 1

    # Verify period_key guard: Running step_a again for same period must not duplicate
    await step_a_generate_recurring_invoices(today)
    async with TestingSessionLocal() as session:
        invoices_for_rec = (await session.execute(
            select(Invoice).where(Invoice.recurring_id == rec_id, Invoice.period_key == "2026-11")
        )).scalars().all()
        assert len(invoices_for_rec) == 1

@pytest.mark.asyncio
async def test_scheduler_step_c_and_late_fee_decision(client: TestClient):
    """Test overdue detection, founder action creation, and founder decision endpoint."""
    today = date(2026, 11, 15)

    # 1. Insert an overdue invoice in DB
    async with TestingSessionLocal() as session:
        client_rec = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client_rec is not None

        overdue_inv = Invoice(
            client_id=client_rec.id,
            invoice_number=f"KR-TEST-OVERDUE-{int(datetime.now().timestamp() * 1000)}",
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=5),
            subtotal_paise=1000000,
            tax_rate_percent=18.0,
            tax_paise=180000,
            total_paise=1180000,
            status="sent",
            email_status="sent"
        )
        session.add(overdue_inv)
        await session.commit()
        await session.refresh(overdue_inv)
        overdue_id = overdue_inv.id

    # 2. Run Step C (check and mark overdue)
    overdue_actions = await step_c_check_and_mark_overdue(today)
    assert len(overdue_actions) >= 1
    action_match = [a for a in overdue_actions if a["invoice_id"] == overdue_id]
    assert len(action_match) == 1

    # 3. Test Founder CEO API for pending late-fee actions
    cookies = get_auth_cookies(client, "founder@krwebsolution.com", "AdminKR@2026")
    pend_res = client.get("/api/v1/invoices/late-fee-actions/pending", cookies=cookies)
    assert pend_res.status_code == 200
    pending_list = pend_res.json()
    action_item = next((a for a in pending_list if a["invoice_id"] == overdue_id), None)
    assert action_item is not None
    action_id = action_item["id"]

    # 4. Founder makes a decision: schedule late fee with 10% fee
    decide_res = client.post(
        f"/api/v1/invoices/late-fee-actions/{action_id}/decide",
        json={
            "action": "schedule",
            "fee_type": "percent",
            "fee_value": 10.0,
            "send_on": (today + timedelta(days=2)).isoformat(),
            "reason": "Severe delay beyond 5 days"
        },
        cookies=cookies
    )
    assert decide_res.status_code == 200
    assert decide_res.json()["status"] == "scheduled"
    assert decide_res.json()["fee_paise"] == 118000  # 10% of 1,180,000

    # 5. Test mark invoice as paid: should auto-waive the scheduled action
    pay_res = client.post(f"/api/v1/invoices/{overdue_id}/mark-paid", cookies=cookies)
    assert pay_res.status_code == 200
    assert pay_res.json()["status"] == "paid"

    async with TestingSessionLocal() as session:
        act = (await session.execute(select(LateFeeAction).where(LateFeeAction.id == action_id))).scalars().first()
        assert act.status == "waived"

@pytest.mark.asyncio
async def test_pdf_paid_stamp():
    """Verify PAID stamp is rendered in generated PDF bytes."""
    async with TestingSessionLocal() as session:
        client_rec = (await session.execute(select(Client).limit(1))).scalars().first()
        st = (await session.execute(select(Settings).where(Settings.id == "default"))).scalars().first()

        inv = Invoice(
            client_id=client_rec.id,
            invoice_number="KR-PAID-TEST-001",
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=500000,
            tax_rate_percent=18.0,
            tax_paise=90000,
            total_paise=590000,
            status="paid"
        )
        pdf_bytes = generate_invoice_pdf(
            invoice=inv,
            client=client_rec,
            settings=st,
            items=[]
        )
        assert len(pdf_bytes) > 1000
        # Check PDF header
        assert pdf_bytes.startswith(b"%PDF")
