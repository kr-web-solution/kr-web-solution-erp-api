"""Go Live prorata, scheduler, advance/balance linkage."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.models import Client, Invoice, Project, RecurringInvoice, Settings
from app.services.maintenance_billing import (
    compute_prorata,
    create_prorata_invoice,
    create_project_amount_invoice,
)
from app.services.scheduler import step_a_generate_recurring_invoices, step_b_dispatch_due_reminders
from tests.conftest import make_project_with_client


def test_compute_prorata_golive_aug_10_inclusive():
    """Go-live 10 Aug in 31-day month → 22/31 × monthly (10–31 inclusive)."""
    monthly = 3100000
    calc = compute_prorata(monthly, date(2026, 8, 10))
    assert calc.skipped is False
    assert calc.billable_days == 22
    assert calc.days_in_month == 31
    assert calc.period_key == "2026-08"
    assert calc.amount_paise == int(round(monthly * 22 / 31))


def test_compute_prorata_last_day_is_one_day():
    calc = compute_prorata(1000000, date(2026, 8, 31))
    assert calc.billable_days == 1
    assert calc.amount_paise == int(round(1000000 * 1 / 31))


@pytest.mark.asyncio
async def test_create_prorata_idempotent_and_advances_next_run():
    async with TestingSessionLocal() as session:
        client = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client is not None

        project = await make_project_with_client(
            session,
            name="GoLive Stub Project",
            client_id=client.id,
            status="running",
            budget_paise=3000000,
            go_live_at=None,
        )

        rec = RecurringInvoice(
            client_id=client.id,
            project_id=project.id,
            amount_paise=3100000,
            send_day=1,
            due_after_days=4,
            reminder_every_days=2,
            starts_on=date(2026, 8, 1),
            day_of_month=1,
            next_run_date=date(2099, 1, 1),
            active=True,
            tax_rate_percent=18.0,
        )
        session.add(rec)
        await session.commit()
        rec_id = rec.id
        project_id = project.id

    go_live = date(2026, 8, 10)

    async with TestingSessionLocal() as session:
        rec = (
            await session.execute(select(RecurringInvoice).where(RecurringInvoice.id == rec_id))
        ).scalars().first()
        inv1, calc1 = await create_prorata_invoice(session, rec, go_live, commit=True)
        assert inv1 is not None
        assert calc1.billable_days == 22
        assert calc1.period_key == "2026-08"
        assert inv1.subtotal_paise == calc1.amount_paise
        assert inv1.due_date == date(2026, 8, 10) + timedelta(days=4)
        assert rec.next_run_date == date(2026, 9, 1)
        inv1_id = inv1.id

    async with TestingSessionLocal() as session:
        rec = (
            await session.execute(select(RecurringInvoice).where(RecurringInvoice.id == rec_id))
        ).scalars().first()
        inv2, _ = await create_prorata_invoice(session, rec, go_live, commit=True)
        assert inv2 is not None
        assert inv2.id == inv1_id

    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                select(Invoice).where(
                    Invoice.recurring_id == rec_id,
                    Invoice.period_key == "2026-08",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        proj = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalars().first()
        # go_live_at is set by API; service alone does not — only next_run advanced
        assert proj is not None


@pytest.mark.asyncio
async def test_scheduler_skips_before_golive_and_bills_after_stub():
    async with TestingSessionLocal() as session:
        client = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client is not None
        st = (await session.execute(select(Settings).where(Settings.id == "default"))).scalar_one()

        project = await make_project_with_client(
            session,
            name="Scheduler GoLive Gate",
            client_id=client.id,
            status="running",
            budget_paise=500000,
            go_live_at=date(2026, 8, 10),
        )

        rec = RecurringInvoice(
            client_id=client.id,
            project_id=project.id,
            amount_paise=1000000,
            send_day=1,
            due_after_days=4,
            reminder_every_days=2,
            starts_on=date(2026, 8, 10),
            day_of_month=1,
            next_run_date=date(2026, 9, 1),
            active=True,
            tax_rate_percent=18.0,
        )
        session.add(rec)
        await session.flush()

        stub = Invoice(
            invoice_number=f"{st.invoice_prefix}GL{st.invoice_next_number}",
            invoice_type="maintenance",
            client_id=client.id,
            project_id=project.id,
            recurring_id=rec.id,
            period_key="2026-08",
            status="sent",
            issue_date=date(2026, 8, 10),
            due_date=date(2026, 8, 14),
            subtotal_paise=200000,
            tax_rate_percent=18.0,
            tax_paise=36000,
            total_paise=236000,
            email_status="sent",
            notes="Pro-rata AMC stub · 2026-08",
        )
        st.invoice_next_number += 1
        session.add(stub)
        await session.commit()
        rec_id = rec.id

    # Mid-August: next_run is Sep 1 → must not create another Aug invoice
    await step_a_generate_recurring_invoices(date(2026, 8, 28))
    async with TestingSessionLocal() as session:
        aug_rows = (
            await session.execute(
                select(Invoice).where(
                    Invoice.recurring_id == rec_id,
                    Invoice.period_key == "2026-08",
                )
            )
        ).scalars().all()
        assert len(aug_rows) == 1

    gen_sep = await step_a_generate_recurring_invoices(date(2026, 9, 1))
    assert len(gen_sep) >= 1

    async with TestingSessionLocal() as session:
        sep = (
            await session.execute(
                select(Invoice).where(
                    Invoice.recurring_id == rec_id,
                    Invoice.period_key == "2026-09",
                )
            )
        ).scalars().first()
        assert sep is not None
        assert sep.subtotal_paise == 1000000
        assert sep.issue_date == date(2026, 9, 1)
        assert sep.due_date == date(2026, 9, 5)


@pytest.mark.asyncio
async def test_pre_due_reminders_run_without_error():
    # Smoke: alternate-day pre-due path executes
    sent = await step_b_dispatch_due_reminders(date(2026, 9, 2))
    assert sent >= 0


@pytest.mark.asyncio
async def test_balance_and_advance_amounts():
    async with TestingSessionLocal() as session:
        client = (await session.execute(select(Client).limit(1))).scalars().first()
        assert client is not None
        project = await make_project_with_client(
            session,
            name="Balance Invoice Project",
            client_id=client.id,
            status="running",
            budget_paise=3000000,
            development_days=15,
        )
        advance_paise = 1500000

        adv = await create_project_amount_invoice(
            session,
            client_id=client.id,
            project_id=project.id,
            amount_paise=advance_paise,
            description="Advance",
            notes="Advance",
        )
        project.advance_invoice_id = adv.id

        balance = max(int(project.budget_paise or 0) - advance_paise, 0)
        assert balance == 1500000
        bal = await create_project_amount_invoice(
            session,
            client_id=client.id,
            project_id=project.id,
            amount_paise=balance,
            description="Balance",
            notes="Balance",
            due_after_days=project.development_days or 15,
        )
        project.balance_invoice_id = bal.id
        await session.commit()
        assert project.advance_invoice_id == adv.id
        assert project.balance_invoice_id == bal.id
        assert bal.subtotal_paise == 1500000
        assert bal.due_date == date.today() + timedelta(days=15)
