"""Batch 4: invoice numbers, status locks, recurring uniqueness, tax rounding."""

from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal as TestingSessionLocal
from app.core.exceptions import ConflictException
from app.models import Invoice, RecurringInvoice, Settings
from app.services.invoice_numbers import assign_unique_invoice_number
from app.services.invoice_status import (
    InvoiceStatusError,
    apply_status,
    assert_can_cancel,
    assert_can_pay,
    assert_deletable,
    assert_editable,
    can_transition,
)
from app.services.maintenance_billing import last_day_of_month
from app.services.scheduler import _month_send_date, step_a_generate_recurring_invoices
from app.services.tax_payroll import compute_tax_paise
from tests.conftest import make_project_with_client
from tests.test_client_project_rules import _make_client


def test_invoice_number_unique_index_exists():
    async def _run():
        async with TestingSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT indexname FROM pg_indexes
                        WHERE tablename = 'invoices'
                          AND indexdef ILIKE '%UNIQUE%'
                          AND indexdef ILIKE '%invoice_number%'
                        """
                    )
                )
            ).all()
            assert rows, "invoices.invoice_number must have a unique index"

    asyncio.run(_run())


def test_uq_invoice_recurring_period_exists():
    async def _run():
        async with TestingSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conname = 'uq_invoice_recurring_period'
                        """
                    )
                )
            ).all()
            assert rows, "uq_invoice_recurring_period must exist"

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_concurrent_invoice_number_generation_unique():
    tag = uuid4().hex[:8]
    async with TestingSessionLocal() as session:
        client = await _make_client(session, f"NumClient-{tag}")
        project = await make_project_with_client(
            session, name=f"NumProject-{tag}", client_id=client.id
        )
        await session.commit()
        cid, pid = client.id, project.id

    async def _create_one(n: int) -> str:
        async with TestingSessionLocal() as session:
            st = (
                await session.execute(
                    select(Settings).where(Settings.id == "default").with_for_update()
                )
            ).scalar_one()
            inv = Invoice(
                invoice_number="",
                invoice_type="custom_dev",
                client_id=cid,
                project_id=pid,
                issue_date=date(2026, 11, 19),
                due_date=date(2026, 12, 3),
                subtotal_paise=100 + n,
                tax_rate_percent=0,
                tax_paise=0,
                total_paise=100 + n,
                status="draft",
                notes=f"concurrent-{tag}-{n}",
            )
            await assign_unique_invoice_number(
                session,
                inv,
                invoice_type="custom_dev",
                issue_date=date(2026, 11, 19),
                settings_row=st,
            )
            await session.commit()
            return inv.invoice_number

    numbers = await asyncio.gather(*[_create_one(i) for i in range(6)])
    assert len(numbers) == 6
    assert len(set(numbers)) == 6
    assert all(numbers)


def test_tax_round_half_up_to_paise():
    # 0.5 paise rounds up (unlike Python banker's round())
    assert compute_tax_paise(1, 50) == 1
    assert compute_tax_paise(1, 49) == 0
    assert compute_tax_paise(10_000_00, 18) == 1_800_00


def test_month_send_date_clamps_day_31():
    assert _month_send_date(2026, 2, 31) == date(2026, 2, 28)
    assert _month_send_date(2026, 1, 31) == date(2026, 1, 28)
    assert _month_send_date(2026, 4, 31) == date(2026, 4, 28)


def test_month_end_uses_calendar_monthrange():
    assert last_day_of_month(date(2026, 2, 1)) == date(2026, 2, monthrange(2026, 2)[1])
    assert last_day_of_month(date(2024, 2, 1)) == date(2024, 2, 29)


def test_illegal_status_transitions_unit():
    assert can_transition("draft", "sent") is True
    assert can_transition("paid", "cancelled") is False
    assert can_transition("cancelled", "paid") is False

    paid = SimpleNamespace(status="paid", invoice_type="custom_dev")
    cancelled = SimpleNamespace(status="cancelled", invoice_type="custom_dev")
    with pytest.raises(InvoiceStatusError, match="credit note"):
        assert_can_cancel(paid)
    with pytest.raises(ConflictException, match="locked"):
        assert_editable("paid")
    with pytest.raises(InvoiceStatusError, match="cancelled"):
        assert_editable("cancelled")
    with pytest.raises(InvoiceStatusError, match="Cannot delete paid"):
        assert_deletable("paid")
    with pytest.raises(InvoiceStatusError, match="Cannot delete a cancelled"):
        assert_deletable("cancelled")
    with pytest.raises(InvoiceStatusError, match="Cannot pay a cancelled"):
        assert_can_pay(cancelled)
    with pytest.raises(InvoiceStatusError):
        apply_status(paid, "sent")


@pytest.mark.asyncio
async def test_paid_and_cancelled_cannot_edit_or_delete(client: TestClient, founder_cookies):
    tag = uuid4().hex[:8]
    async with TestingSessionLocal() as session:
        c = await _make_client(session, f"LockClient-{tag}")
        p = await make_project_with_client(session, name=f"LockProject-{tag}", client_id=c.id)
        paid = Invoice(
            invoice_number=f"LOCK-PAID-{tag}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=p.id,
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=500,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=500,
            status="paid",
        )
        cancelled = Invoice(
            invoice_number=f"LOCK-CAN-{tag}",
            invoice_type="custom_dev",
            client_id=c.id,
            project_id=p.id,
            issue_date=date.today(),
            due_date=date.today(),
            subtotal_paise=400,
            tax_rate_percent=0,
            tax_paise=0,
            total_paise=400,
            status="cancelled",
        )
        session.add_all([paid, cancelled])
        await session.commit()
        paid_id, can_id = paid.id, cancelled.id

    paid_patch = client.patch(
        f"/api/v1/invoices/{paid_id}", json={"notes": "nope"}, cookies=founder_cookies
    )
    assert paid_patch.status_code == 409, paid_patch.text
    paid_del = client.delete(f"/api/v1/invoices/{paid_id}", cookies=founder_cookies)
    assert paid_del.status_code == 400, paid_del.text
    paid_cancel = client.post(f"/api/v1/invoices/{paid_id}/cancel", cookies=founder_cookies)
    assert paid_cancel.status_code == 400, paid_cancel.text

    can_patch = client.patch(
        f"/api/v1/invoices/{can_id}", json={"notes": "nope"}, cookies=founder_cookies
    )
    assert can_patch.status_code == 400, can_patch.text
    can_del = client.delete(f"/api/v1/invoices/{can_id}", cookies=founder_cookies)
    assert can_del.status_code == 400, can_del.text
    can_pay = client.post(f"/api/v1/invoices/{can_id}/pay", cookies=founder_cookies)
    assert can_pay.status_code == 400, can_pay.text


@pytest.mark.asyncio
async def test_step_a_double_run_does_not_duplicate_period():
    tag = uuid4().hex[:8]
    today = date(2026, 11, 5)
    async with TestingSessionLocal() as session:
        c = await _make_client(session, f"RecClient-{tag}")
        p = await make_project_with_client(
            session,
            name=f"RecProject-{tag}",
            client_id=c.id,
            status="running",
            go_live_at=date(2026, 10, 1),
        )
        rec = RecurringInvoice(
            client_id=c.id,
            project_id=p.id,
            amount_paise=99_00,
            send_day=31,  # stored >28; scheduler clamps
            due_after_days=5,
            reminder_every_days=2,
            starts_on=date(2026, 11, 1),
            day_of_month=31,
            next_run_date=date(2026, 11, 1),
            active=True,
        )
        session.add(rec)
        await session.commit()
        rec_id = rec.id

    await step_a_generate_recurring_invoices(today)
    await step_a_generate_recurring_invoices(today)

    async with TestingSessionLocal() as session:
        invoices = (
            await session.execute(
                select(Invoice).where(
                    Invoice.recurring_id == rec_id, Invoice.period_key == "2026-11"
                )
            )
        ).scalars().all()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv.covers_to == date(2026, 11, monthrange(2026, 11)[1])
        assert inv.due_date.day <= 28
