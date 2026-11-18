"""Pro-rata and monthly AMC invoice helpers (Go Live inclusive)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Invoice, InvoiceItem, Project, RecurringInvoice, Settings


@dataclass
class ProrataComputation:
    start: date
    end: date
    billable_days: int
    days_in_month: int
    amount_paise: int
    period_key: str
    skipped: bool


def first_of_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def last_day_of_month(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def compute_prorata(monthly_paise: int, start: date) -> ProrataComputation:
    """
    Bill from `start` (inclusive, e.g. go-live day) through end of that month.
    amount = round(monthly × billable_days / days_in_month)
    """
    end = last_day_of_month(start)
    days_in_month = end.day
    period_key = f"{start.year}-{start.month:02d}"
    if start > end:
        return ProrataComputation(
            start=start,
            end=end,
            billable_days=0,
            days_in_month=days_in_month,
            amount_paise=0,
            period_key=period_key,
            skipped=True,
        )
    billable_days = end.day - start.day + 1
    if billable_days <= 0:
        return ProrataComputation(
            start=start,
            end=end,
            billable_days=0,
            days_in_month=days_in_month,
            amount_paise=0,
            period_key=period_key,
            skipped=True,
        )
    amount = int(round((monthly_paise or 0) * billable_days / days_in_month))
    return ProrataComputation(
        start=start,
        end=end,
        billable_days=billable_days,
        days_in_month=days_in_month,
        amount_paise=amount,
        period_key=period_key,
        skipped=False,
    )


async def allocate_invoice_number(
    db: AsyncSession,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
) -> str:
    from .invoice_numbers import allocate_invoice_number as _allocate

    return await _allocate(db, invoice_type=invoice_type, issue_date=issue_date)


def _month_abbr(d: date) -> str:
    return d.strftime("%b")


async def create_prorata_invoice(
    db: AsyncSession,
    recurring: RecurringInvoice,
    go_live_date: date,
    *,
    commit: bool = False,
) -> Tuple[Optional[Invoice], ProrataComputation]:
    """
    Create a draft pro-rata maintenance invoice from go-live date (inclusive) → month end.
    Idempotent on (recurring_id, period_key).
    Advances next_run_date to 1st of next month.
    """
    start = go_live_date
    calc = compute_prorata(recurring.amount_paise or 0, start)

    if calc.skipped or calc.amount_paise <= 0:
        recurring.next_run_date = first_of_next_month(start)
        if recurring.starts_on is None or start < recurring.starts_on:
            recurring.starts_on = start
        if commit:
            await db.commit()
        return None, calc

    existing = await db.scalar(
        select(Invoice.id).where(
            Invoice.recurring_id == recurring.id,
            Invoice.period_key == calc.period_key,
        )
    )
    if existing:
        recurring.next_run_date = first_of_next_month(start)
        inv = (
            await db.execute(select(Invoice).where(Invoice.id == existing))
        ).scalars().first()
        if commit:
            await db.commit()
        return inv, calc

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalar_one_or_none()
    from .tax_payroll import effective_tax_rate, compute_tax_paise
    tax_rate = effective_tax_rate(
        st, float(recurring.tax_rate_percent) if recurring.tax_rate_percent is not None else None
    )
    subtotal = calc.amount_paise
    tax_paise = compute_tax_paise(subtotal, tax_rate)
    total_paise = subtotal + tax_paise
    due_after = recurring.due_after_days or 5
    send_day = min(int(recurring.send_day or 1), 28)
    send_on = date(start.year, start.month, min(send_day, calc.end.day))
    # Due runs from the send day, or from go-live when that day has already passed.
    due_anchor = send_on if send_on >= start else start
    due_date = due_anchor + timedelta(days=due_after)

    inv_number = await allocate_invoice_number(
        db, invoice_type="amc", issue_date=start
    )
    desc = (
        f"Maintenance (prorata) {start.day}–{calc.end.day} {_month_abbr(start)} "
        f"({calc.billable_days}/{calc.days_in_month} days)"
    )

    new_inv = Invoice(
        invoice_number=inv_number,
        invoice_type="amc",
        ledger="maintenance",
        period_months=1,
        covers_from=start,
        covers_to=calc.end,
        monthly_rate_paise=recurring.amount_paise,
        client_id=recurring.client_id,
        project_id=recurring.project_id,
        recurring_id=recurring.id,
        period_key=calc.period_key,
        status="draft",
        issue_date=start,
        due_date=due_date,
        subtotal_paise=subtotal,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        email_status="pending",
        email_retry_count=0,
        notes=f"Pro-rata AMC stub · {calc.period_key}",
    )
    db.add(new_inv)
    await db.flush()
    db.add(
        InvoiceItem(
            invoice_id=new_inv.id,
            description=desc,
            quantity=1.0,
            unit_rate_paise=subtotal,
            amount_paise=subtotal,
        )
    )

    if recurring.starts_on is None or start < recurring.starts_on:
        recurring.starts_on = start
    recurring.next_run_date = first_of_next_month(start)

    if commit:
        await db.commit()
        await db.refresh(new_inv)
    return new_inv, calc


async def upsert_project_maintenance(
    db: AsyncSession,
    project: Project,
    amount_paise: int,
    *,
    send_day: int = 1,
    due_after_days: int = 4,
) -> Optional[RecurringInvoice]:
    """Create or update AMC schedule. Monthly runs only after go_live_at is set."""
    amount_paise = int(amount_paise or 0)
    if amount_paise <= 0:
        return None
    today = date.today()
    hold_until = date(2099, 1, 1)
    recurrings = list(getattr(project, "recurring_invoices", None) or [])
    if recurrings:
        rec = recurrings[0]
        rec.amount_paise = amount_paise
        rec.send_day = send_day
        rec.due_after_days = due_after_days
        rec.day_of_month = send_day
        rec.active = True
        if not project.go_live_at and (rec.next_run_date is None or rec.next_run_date <= today):
            rec.next_run_date = hold_until
        return rec
    rec = RecurringInvoice(
        client_id=project.client_id,
        project_id=project.id,
        amount_paise=amount_paise,
        send_day=send_day,
        due_after_days=due_after_days,
        reminder_every_days=2,
        starts_on=today,
        day_of_month=send_day,
        next_run_date=hold_until if not project.go_live_at else first_of_next_month(today),
        active=True,
        tax_rate_percent=0.0,
        line_items_template=[
            {
                "description": "Monthly maintenance & support retainer",
                "quantity": 1,
                "unit_rate_paise": amount_paise,
            }
        ],
    )
    db.add(rec)
    await db.flush()
    return rec


async def create_project_amount_invoice(
    db: AsyncSession,
    *,
    client_id: str,
    project_id: str,
    amount_paise: int,
    description: str,
    notes: str,
    due_after_days: int = 5,
    invoice_type: str = "advance",
) -> Invoice:
    """Draft project invoice from a base (ex-GST) amount. Default due in 5 days."""
    if amount_paise <= 0:
        raise ValueError("amount_paise must be > 0")
    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalar_one_or_none()
    from .tax_payroll import effective_tax_rate, compute_tax_paise
    tax_rate = effective_tax_rate(st)
    subtotal = int(amount_paise)
    tax_paise = compute_tax_paise(subtotal, tax_rate)
    total_paise = subtotal + tax_paise
    today = date.today()
    inv_number = await allocate_invoice_number(
        db, invoice_type=invoice_type, issue_date=today
    )
    inv = Invoice(
        invoice_number=inv_number,
        invoice_type=invoice_type,
        ledger="development",
        client_id=client_id,
        project_id=project_id,
        status="draft",
        issue_date=today,
        due_date=today + timedelta(days=due_after_days),
        subtotal_paise=subtotal,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        email_status="pending",
        notes=notes,
    )
    db.add(inv)
    await db.flush()
    db.add(
        InvoiceItem(
            invoice_id=inv.id,
            description=description,
            quantity=1.0,
            unit_rate_paise=subtotal,
            amount_paise=subtotal,
        )
    )
    return inv
