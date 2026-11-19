import asyncio
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, text, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from ..core.database import AsyncSessionLocal, engine, not_deleted
from ..models import RecurringInvoice, Invoice, InvoiceItem, Settings, User, LateFeeAction
from .pdf import generate_invoice_pdf
from .email import send_email_async
logger = logging.getLogger("kr.scheduler")

ADVISORY_LOCK_ID = 847291
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")

def get_kolkata_today() -> date:
    """Returns the current date in Asia/Kolkata timezone."""
    return datetime.now(KOLKATA_TZ).date()

def _month_send_date(year: int, month: int, send_day: Optional[int]) -> date:
    """Calendar day this month when the maintenance email goes out."""
    last = monthrange(year, month)[1]
    day = min(max(int(send_day or 1), 1), 28, last)
    return date(year, month, day)


async def step_a_generate_recurring_invoices(today: date) -> List[str]:
    """
    Maintenance billing after Go Live:
    - The go-live month is billed separately for the days left.
    - On the 1st of every later month, generate the full month.
    - Email on Send Day of Month. Due date is that day plus Due After N Days.
    Skips periods already billed (including pro-rata stubs) via period_key.
    """
    from .maintenance_billing import first_of_next_month

    period_key = f"{today.year}-{today.month:02d}"
    invoices_to_email = []

    async with AsyncSessionLocal() as session:
        rec_stmt = (
            select(RecurringInvoice)
            .options(selectinload(RecurringInvoice.client), selectinload(RecurringInvoice.project))
            .where(
                not_deleted(RecurringInvoice),
                RecurringInvoice.active == True,
                RecurringInvoice.starts_on <= today,
                or_(RecurringInvoice.ends_on.is_(None), RecurringInvoice.ends_on >= today),
            )
        )
        active_recurrings = (await session.execute(rec_stmt)).scalars().all()

        for rec in active_recurrings:
            # Full-month bills start only after Go Live creates the first prorata invoice.
            if rec.project_id:
                proj = rec.project
                if not proj or not getattr(proj, "go_live_at", None):
                    continue

            # Prefer next_run_date; fall back to send_day for legacy rows
            if rec.next_run_date is not None:
                if rec.next_run_date > today:
                    continue
            elif (rec.send_day or 1) > today.day:
                continue

            # Check if an invoice for (recurring_id, period_key) already exists
            existing = await session.scalar(
                select(Invoice.id).where(
                    Invoice.recurring_id == rec.id,
                    Invoice.period_key == period_key
                )
            )
            if existing:
                # Period already billed (full or stub) — push next run forward
                rec.next_run_date = first_of_next_month(today)
                continue

            month_start = date(today.year, today.month, 1)
            month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])
            covered = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.project_id == rec.project_id,
                        Invoice.ledger == "maintenance",
                        Invoice.status.notin_(["cancelled", "void"]),
                        Invoice.covers_from.is_not(None),
                        Invoice.covers_from <= month_end,
                        Invoice.covers_to >= month_start,
                    )
                )
            ).scalars().first()
            if covered and covered.covers_to:
                rec.next_run_date = covered.covers_to + timedelta(days=1)
                continue

            # Allocate next invoice number atomically
            st_stmt = select(Settings).where(Settings.id == "default").with_for_update()
            st = (await session.execute(st_stmt)).scalar_one_or_none()
            if not st:
                continue

            from .invoice_numbers import assign_unique_invoice_number, is_recurring_period_conflict
            from .audit import write_audit

            issue_date = date(today.year, today.month, 1)

            # Full month is created on the 1st. Email waits for the send day.
            subtotal = rec.amount_paise or 0
            from .tax_payroll import effective_tax_rate, compute_tax_paise
            tax_rate = effective_tax_rate(st, float(rec.tax_rate_percent) if rec.tax_rate_percent is not None else None)
            tax_paise = compute_tax_paise(subtotal, tax_rate)
            total_paise = subtotal + tax_paise
            send_on = _month_send_date(today.year, today.month, rec.send_day)
            due_date = send_on + timedelta(days=rec.due_after_days or 5)

            new_inv = Invoice(
                invoice_number="",
                invoice_type="maintenance",
                ledger="maintenance",
                client_id=rec.client_id,
                project_id=rec.project_id,
                recurring_id=rec.id,
                period_key=period_key,
                covers_from=month_start,
                covers_to=month_end,
                monthly_rate_paise=subtotal,
                status="draft",
                issue_date=issue_date,
                due_date=due_date,
                subtotal_paise=subtotal,
                tax_rate_percent=tax_rate,
                tax_paise=tax_paise,
                total_paise=total_paise,
                email_status="pending",
                email_retry_count=0,
                notes=rec.notes or f"Monthly Maintenance - {period_key}"
            )
            try:
                await assign_unique_invoice_number(
                    session,
                    new_inv,
                    invoice_type="maintenance",
                    issue_date=issue_date,
                    settings_row=st,
                )
            except IntegrityError as exc:
                if is_recurring_period_conflict(exc):
                    rec.next_run_date = first_of_next_month(today)
                    if new_inv in session:
                        session.expunge(new_inv)
                    continue
                raise

            await write_audit(
                session,
                None,
                "invoices",
                new_inv.id,
                "create",
                after={
                    "invoice_number": new_inv.invoice_number,
                    "period_key": period_key,
                    "recurring_id": rec.id,
                    "note": "scheduler",
                },
                note="scheduler",
            )

            # Add line items
            template_items = rec.line_items_template or []
            if template_items:
                for itm in template_items:
                    qty = float(itm.get("quantity", 1))
                    rate = int(itm.get("unit_rate_paise", subtotal))
                    session.add(InvoiceItem(
                        invoice_id=new_inv.id,
                        description=itm.get("description", f"Maintenance Service ({period_key})"),
                        quantity=qty,
                        unit_rate_paise=rate,
                        amount_paise=int(qty * rate)
                    ))
            else:
                session.add(InvoiceItem(
                    invoice_id=new_inv.id,
                    description=f"Monthly Maintenance Support ({period_key})",
                    quantity=1.0,
                    unit_rate_paise=subtotal,
                    amount_paise=subtotal
                ))

            rec.next_run_date = first_of_next_month(today)
            if send_on <= today:
                invoices_to_email.append(new_inv.id)

        # Drafts created on the 1st are emailed once Send Day of Month arrives.
        waiting = (
            await session.execute(
                select(Invoice)
                .options(selectinload(Invoice.recurring_invoice))
                .where(
                    Invoice.recurring_id.is_not(None),
                    Invoice.email_status == "pending",
                    Invoice.status == "draft",
                    Invoice.paid_at.is_(None),
                    Invoice.invoice_type.in_(["maintenance", "amc"]),
                )
            )
        ).scalars().all()
        queued = set(invoices_to_email)
        for inv in waiting:
            linked = inv.recurring_invoice
            if not linked or not inv.issue_date:
                continue
            send_on = _month_send_date(inv.issue_date.year, inv.issue_date.month, linked.send_day)
            if send_on <= today and inv.id not in queued:
                invoices_to_email.append(inv.id)
                queued.add(inv.id)

        # Commit ALL invoices before sending emails (Never hold lock during SMTP)
        await session.commit()

    return invoices_to_email

async def step_b_dispatch_due_reminders(today: date) -> int:
    """
    Unpaid invoices inside the due window.
    Maintenance uses Remind Every N Days, counted from the send day.
    Other invoices stay on alternate days (2, 4, …) from the issue date.
    """
    reminders_to_send = []
    async with AsyncSessionLocal() as session:
        rem_stmt = (
            select(Invoice)
            .options(selectinload(Invoice.client), selectinload(Invoice.recurring_invoice))
            .where(
                Invoice.paid_at.is_(None),
                Invoice.due_date >= today,
                Invoice.status.in_(["draft", "sent"]),
                Invoice.invoice_type != "late_fee",
            )
        )
        unpaid = (await session.execute(rem_stmt)).scalars().all()

        for inv in unpaid:
            if inv.recurring_invoice and inv.invoice_type in ("maintenance", "amc"):
                if inv.status == "draft" and inv.email_status != "sent":
                    continue
                interval = max(int(inv.recurring_invoice.reminder_every_days or 2), 1)
                anchor = _month_send_date(
                    inv.issue_date.year, inv.issue_date.month, inv.recurring_invoice.send_day
                )
                if anchor < inv.issue_date:
                    anchor = inv.issue_date
            else:
                interval = 2
                anchor = inv.issue_date
            days_since = (today - anchor).days
            if days_since < interval or days_since % interval != 0:
                continue
            if inv.last_reminder_at and inv.last_reminder_at.astimezone(KOLKATA_TZ).date() == today:
                continue
            reminders_to_send.append(inv.id)

    sent_count = 0
    for rem_id in reminders_to_send:
        sent = await _dispatch_reminder_email(rem_id, today)
        if sent:
            sent_count += 1
    return sent_count


async def step_b_overdue_hourly_reminders(now: Optional[datetime] = None) -> int:
    """
    Overdue-only: email client when LateFeeAction.next_reminder_at is due
    (cadence set by founder as reminder_every_hours).
    """
    if now is None:
        now = datetime.now(KOLKATA_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KOLKATA_TZ)

    to_send: List[tuple] = []
    async with AsyncSessionLocal() as session:
        act_stmt = (
            select(LateFeeAction)
            .options(selectinload(LateFeeAction.invoice).selectinload(Invoice.client))
            .where(
                LateFeeAction.reminder_every_hours.is_not(None),
                LateFeeAction.next_reminder_at.is_not(None),
                LateFeeAction.next_reminder_at <= now,
                LateFeeAction.status.in_(["pending", "scheduled", "extended", "sent"]),
            )
        )
        actions = (await session.execute(act_stmt)).scalars().all()
        for act in actions:
            inv = act.invoice
            if not inv or inv.paid_at or inv.status == "paid":
                act.next_reminder_at = None
                continue
            if inv.status != "overdue" and inv.due_date >= now.date():
                # Not overdue anymore (e.g. extended) — pause overdue mails until due again
                continue
            to_send.append((act.id, inv.id, int(act.reminder_every_hours or 24)))
        await session.commit()

    sent = 0
    for act_id, inv_id, hours in to_send:
        ok = await _dispatch_reminder_email(inv_id, now.date(), overdue=True)
        if ok:
            sent += 1
        async with AsyncSessionLocal() as session:
            act = (
                await session.execute(select(LateFeeAction).where(LateFeeAction.id == act_id))
            ).scalars().first()
            if act:
                act.repeat_count = (act.repeat_count or 0) + 1
                act.next_reminder_at = datetime.now(timezone.utc) + timedelta(hours=hours)
                await session.commit()
    return sent


async def run_evening_payment_reminders(today: Optional[date] = None) -> dict:
    """18:00 IST: alternate-day reminders inside the 5-day payment window."""
    if today is None:
        today = get_kolkata_today()
    summary = {"reminders_sent": 0}
    async with engine.connect() as lock_conn:
        lock_res = await lock_conn.scalar(text(f"SELECT pg_try_advisory_lock({ADVISORY_LOCK_ID + 1})"))
        if not lock_res:
            logger.info("Evening reminders skipped: lock held.")
            return summary
        try:
            rem_sent = await step_b_dispatch_due_reminders(today)
            summary["reminders_sent"] = rem_sent
        finally:
            await lock_conn.execute(text(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_ID + 1})"))
            logger.info(f"Evening payment reminders finished. Summary: {summary}")
    return summary


async def run_hourly_overdue_reminders() -> dict:
    """Hourly: overdue unpaid follow-ups per founder-configured hours."""
    summary = {"overdue_reminders_sent": 0}
    async with engine.connect() as lock_conn:
        lock_res = await lock_conn.scalar(text(f"SELECT pg_try_advisory_lock({ADVISORY_LOCK_ID + 2})"))
        if not lock_res:
            return summary
        try:
            summary["overdue_reminders_sent"] = await step_b_overdue_hourly_reminders()
        finally:
            await lock_conn.execute(text(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_ID + 2})"))
    return summary

async def step_c_check_and_mark_overdue(today: date) -> List[dict]:
    """
    Step (c): Mark overdue, create pending LateFeeAction, notify Founder CEO.
    Send NOTHING to client.
    Returns list of overdue records notified to founders.
    """
    overdue_to_notify = []
    async with AsyncSessionLocal() as session:
        od_stmt = (
            select(Invoice)
            .options(selectinload(Invoice.client))
            .where(
                Invoice.paid_at.is_(None),
                Invoice.due_date < today,
                Invoice.overdue_notified_at.is_(None),
                # Do not clobber proof awaiting founder verification (or terminal states)
                Invoice.status.notin_(
                    ["paid", "cancelled", "void", "proof_pending"]
                ),
            )
        )
        overdue_invs = (await session.execute(od_stmt)).scalars().all()

        for o_inv in overdue_invs:
            o_inv.status = "overdue"
            o_inv.overdue_notified_at = datetime.now(timezone.utc)

            # Check if action already created
            has_action = await session.scalar(
                select(LateFeeAction.id).where(LateFeeAction.invoice_id == o_inv.id)
            )
            if not has_action:
                # Default 5% or minimum ₹500 — founder decides fine / extend / hours
                calc_fee = max(50000, int(round(o_inv.total_paise * 0.05)))
                action = LateFeeAction(
                    invoice_id=o_inv.id,
                    fee_paise=calc_fee,
                    fee_type="percent",
                    fee_value=5.0,
                    reason="Payment not received by due date",
                    send_on=today + timedelta(days=3),
                    reminder_every_hours=24,  # founder can change; overdue mails start after decide or immediately
                    next_reminder_at=datetime.now(timezone.utc) + timedelta(hours=24),
                    status="pending"
                )
                session.add(action)

            overdue_to_notify.append({
                "invoice_id": o_inv.id,
                "invoice_number": o_inv.invoice_number,
                "client_name": o_inv.client.name if o_inv.client else "Client",
                "due_date": str(o_inv.due_date),
                "total_rupees": o_inv.total_paise / 100.0
            })

        await session.commit()

    if overdue_to_notify:
        await _notify_founders_of_overdue(overdue_to_notify)

    return overdue_to_notify

async def step_d_process_scheduled_late_fees(today: date) -> List[str]:
    """
    Step (d): On send_on date, create and email late fee invoice linked via parent_invoice_id.
    Includes repeat reminders (max 3).
    Returns list of created late fee invoice IDs.
    """
    late_fees_to_create = []
    async with AsyncSessionLocal() as session:
        lfa_stmt = (
            select(LateFeeAction)
            .options(selectinload(LateFeeAction.invoice))
            .where(
                LateFeeAction.status == "scheduled",
                LateFeeAction.send_on <= today
            )
        )
        scheduled_actions = (await session.execute(lfa_stmt)).scalars().all()

        for act in scheduled_actions:
            parent_inv = act.invoice
            if not parent_inv or parent_inv.status == "paid":
                # Parent invoice was paid; waive late fee action
                act.status = "waived"
                continue

            # Create Late Fee invoice linked to parent
            st_stmt = select(Settings).where(Settings.id == "default").with_for_update()
            st = (await session.execute(st_stmt)).scalar_one_or_none()
            if not st:
                continue

            from .invoice_numbers import allocate_invoice_number

            lf_number = await allocate_invoice_number(
                session,
                invoice_type="late_fee",
                issue_date=today,
                settings_row=st,
            )

            subtotal = act.fee_paise
            from .tax_payroll import effective_tax_rate, compute_tax_paise
            tax_rate = effective_tax_rate(st)
            tax_paise = compute_tax_paise(subtotal, tax_rate)
            total_paise = subtotal + tax_paise

            lf_invoice = Invoice(
                invoice_number=lf_number,
                invoice_type="late_fee",
                parent_invoice_id=parent_inv.id,
                client_id=parent_inv.client_id,
                project_id=parent_inv.project_id,
                status="draft",
                issue_date=today,
                due_date=today + timedelta(days=5),
                subtotal_paise=subtotal,
                tax_rate_percent=tax_rate,
                tax_paise=tax_paise,
                total_paise=total_paise,
                email_status="pending",
                notes=f"Late fee notice for Invoice {parent_inv.invoice_number}. {act.reason}"
            )
            session.add(lf_invoice)
            await session.flush()

            session.add(InvoiceItem(
                invoice_id=lf_invoice.id,
                description=f"Late Payment Fee for {parent_inv.invoice_number}: {act.reason}",
                quantity=1.0,
                unit_rate_paise=subtotal,
                amount_paise=subtotal
            ))

            act.status = "sent"
            act.late_fee_invoice_id = lf_invoice.id

            # Repeat schedule: every N days, max 3 repeats
            if act.repeat_every_days and act.repeat_count < 3:
                act.repeat_count += 1
                act.send_on = today + timedelta(days=act.repeat_every_days)
                act.status = "scheduled"

            late_fees_to_create.append(lf_invoice.id)

        await session.commit()

    return late_fees_to_create

async def step_e_retry_failed_emails(today: date) -> int:
    """
    Step (e): Retry failed invoice emails (max 3 retries).
    Returns count of retries attempted.
    """
    async with AsyncSessionLocal() as session:
        failed_stmt = select(Invoice.id).where(
            Invoice.email_status == "failed",
            Invoice.email_retry_count < 3
        )
        failed_ids = (await session.execute(failed_stmt)).scalars().all()

    for f_id in failed_ids:
        await _dispatch_invoice_email(f_id, is_new=False)
    return len(failed_ids)

async def run_daily_maintenance_jobs(today: Optional[date] = None) -> dict:
    """
    Main daily maintenance and late-fee processing workflow.
    Guaranteed single-run via PostgreSQL advisory lock on a dedicated connection.
    All scheduler functions take `today` parameter (defaults to Asia/Kolkata today).
    """
    if today is None:
        today = get_kolkata_today()

    logger.info(f"Running daily maintenance billing & overdue jobs for {today} (Asia/Kolkata)")

    summary = {
        "today": str(today),
        "invoices_generated": 0,
        "reminders_sent": 0,
        "overdue_marked": 0,
        "late_fee_invoices_created": 0,
        "failed_retries_attempted": 0,
    }

    # Acquire PostgreSQL session-level advisory lock on a dedicated connection
    async with engine.connect() as lock_conn:
        lock_res = await lock_conn.scalar(text(f"SELECT pg_try_advisory_lock({ADVISORY_LOCK_ID})"))
        if not lock_res:
            logger.info("Scheduler skipped: advisory lock already held by another worker instance.")
            return summary

        try:
            # Step (a): Generate maintenance invoices
            gen_invoices = await step_a_generate_recurring_invoices(today)
            summary["invoices_generated"] = len(gen_invoices)

            # Dispatch emails outside the transactional lock
            for inv_id in gen_invoices:
                await _dispatch_invoice_email(inv_id, is_new=True)

            # Step (b): Retry failed emails
            retries = await step_e_retry_failed_emails(today)
            summary["failed_retries_attempted"] = retries

            # Reminders run on the evening job (1st/3rd/5th) — not at 09:00
            summary["reminders_sent"] = 0

            # Step (d): Check and mark overdue
            od_notified = await step_c_check_and_mark_overdue(today)
            summary["overdue_marked"] = len(od_notified)

            # Step (e): Process scheduled late fees
            lf_created = await step_d_process_scheduled_late_fees(today)
            summary["late_fee_invoices_created"] = len(lf_created)

            # Email newly created late fee invoices
            for lf_id in lf_created:
                await _dispatch_invoice_email(lf_id, is_new=True)

        finally:
            await lock_conn.execute(text(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_ID})"))
            logger.info(f"Daily maintenance jobs finished. Summary: {summary}")

    return summary

async def _dispatch_invoice_email(invoice_id: str, is_new: bool = True) -> bool:
    """Renders PDF and sends invoice email outside transactional locks."""
    async with AsyncSessionLocal() as session:
        inv_stmt = (
            select(Invoice)
            .options(
                selectinload(Invoice.client),
                selectinload(Invoice.project),
                selectinload(Invoice.items),
                selectinload(Invoice.parent_invoice)
            )
            .where(Invoice.id == invoice_id)
        )
        inv = (await session.execute(inv_stmt)).scalars().first()
        if not inv or not inv.client:
            return False

        st_stmt = select(Settings).where(Settings.id == "default")
        st = (await session.execute(st_stmt)).scalars().first()
        if not st or not st.smtp_host or not st.smtp_from_email:
            # SMTP not configured; mark status as failed
            inv.email_status = "failed"
            inv.email_error = "SMTP credentials not configured in settings."
            inv.email_retry_count += 1
            await session.commit()
            return False

        # Generate PDF
        pdf_bytes = generate_invoice_pdf(
            invoice=inv,
            client=inv.client,
            settings=st,
            items=inv.items,
            project=inv.project,
            parent_invoice=inv.parent_invoice
        )

        from .email_html import compose_invoice_client_email

        # Paid invoices must never get an "invoice ready / total payable" mail
        if inv.status == "paid":
            kind_force = "paid"
        elif inv.invoice_type == "late_fee":
            kind_force = "late_fee"
        else:
            kind_force = None

        _kind, subject, html, plain, attachment_name = compose_invoice_client_email(
            company_name=st.company_name,
            company_address=st.company_address or "",
            company_gstin=st.company_gstin or "",
            company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
            client_name=inv.client.primary_contact_name or inv.client.name,
            invoice_number=inv.invoice_number,
            total_paise=inv.total_paise,
            due_date=inv.due_date,
            issue_date=inv.issue_date,
            paid_at=inv.paid_at,
            bank_upi_details=st.bank_upi_details or "Please contact billing for bank transfer details.",
            invoice_type=inv.invoice_type or "invoice",
            project_name=(inv.project.name if inv.project else "") or "",
            notes=inv.notes or "",
            status=inv.status or "draft",
            force_kind=kind_force,
        )

        try:
            await send_email_async(
                to_email=inv.client.email,
                subject=subject,
                body=html,
                plain_body=plain,
                settings=st,
                attachment_bytes=pdf_bytes,
                attachment_filename=attachment_name,
                cc_email=st.invoice_cc_email,
                is_html=True,
            )
            inv.email_status = "sent"
            inv.email_error = None
            inv.last_emailed_at = datetime.now(timezone.utc)
            if inv.status == "draft":
                inv.status = "sent"
            await session.commit()
            return True
        except Exception as e:
            inv.email_status = "failed"
            inv.email_error = str(e)
            inv.email_retry_count += 1
            await session.commit()
            return False

async def _dispatch_reminder_email(invoice_id: str, today: date, overdue: bool = False) -> bool:
    """Dispatches a polite payment reminder to the client."""
    async with AsyncSessionLocal() as session:
        inv_stmt = (
            select(Invoice)
            .options(selectinload(Invoice.client), selectinload(Invoice.items), selectinload(Invoice.project))
            .where(Invoice.id == invoice_id)
        )
        inv = (await session.execute(inv_stmt)).scalars().first()
        if not inv or not inv.client:
            return False

        st = (await session.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        if not st or not st.smtp_host or not st.smtp_from_email:
            return False

        pdf_bytes = generate_invoice_pdf(
            invoice=inv,
            client=inv.client,
            settings=st,
            items=inv.items,
            project=inv.project
        )

        from .email_html import compose_invoice_client_email

        if inv.status == "paid" or inv.paid_at:
            return False

        days_left = (inv.due_date - today).days
        force_kind = "overdue" if (overdue or days_left < 0) else "reminder"
        _kind, subject, html, plain, attachment_name = compose_invoice_client_email(
            company_name=st.company_name,
            company_address=st.company_address or "",
            company_gstin=st.company_gstin or "",
            company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
            client_name=inv.client.primary_contact_name or inv.client.name,
            invoice_number=inv.invoice_number,
            total_paise=inv.total_paise,
            due_date=inv.due_date,
            issue_date=inv.issue_date,
            paid_at=inv.paid_at,
            bank_upi_details=st.bank_upi_details or "Contact billing for details.",
            invoice_type=inv.invoice_type or "invoice",
            project_name=(inv.project.name if inv.project else "") or "",
            notes=inv.notes or "",
            status=inv.status or "sent",
            force_kind=force_kind,
        )
        if force_kind == "reminder":
            subject = f"Friendly Reminder: Invoice {inv.invoice_number} Due in {max(0, days_left)} Days"

        try:
            await send_email_async(
                to_email=inv.client.email,
                subject=subject,
                body=html,
                plain_body=plain,
                settings=st,
                attachment_bytes=pdf_bytes,
                attachment_filename=attachment_name,
                cc_email=st.invoice_cc_email,
                is_html=True,
            )
            inv.reminder_count += 1
            inv.last_reminder_at = datetime.now(timezone.utc)
            await session.commit()
            return True
        except Exception as e:
            logger.warning(f"Failed to dispatch reminder for invoice {inv.invoice_number}: {e}")
            return False

async def _notify_founders_of_overdue(overdue_list: List[dict]):
    """Sends an overdue summary email exclusively to Founder CEO accounts."""
    async with AsyncSessionLocal() as session:
        st = (await session.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        if not st or not st.smtp_host or not st.smtp_from_email:
            return

        founders = (await session.execute(
            select(User).where(User.role.in_(["admin", "founder_ceo"]), User.is_active == True)
        )).scalars().all()

        from .email_html import build_founder_overdue_email

        for f in founders:
            try:
                subject, html, plain = build_founder_overdue_email(
                    company_name=st.company_name or "KR Web Solution",
                    founder_name=f.full_name or "Founder",
                    items=overdue_list,
                    company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
                )
                await send_email_async(
                    to_email=f.email,
                    subject=subject,
                    body=html,
                    plain_body=plain,
                    settings=st,
                    is_html=True,
                )
            except Exception as e:
                logger.error(f"Failed to notify founder {f.email}: {e}")

_scheduler: Optional[AsyncIOScheduler] = None


def _scheduler_decision() -> tuple[bool, str]:
    """Return (should_start, reason) for logging and start_scheduler."""
    from urllib.parse import urlparse
    from ..core.config import settings

    env = (settings.ENVIRONMENT or "").strip().lower()
    enable_raw = settings.ENABLE_SCHEDULER
    enable_repr = "unset" if enable_raw is None else str(enable_raw).lower()

    if env == "test":
        return False, f"ENVIRONMENT={env!r} ENABLE_SCHEDULER={enable_repr} → off (test)"

    if enable_raw is True:
        explicit_on = True
        why_default = "ENABLE_SCHEDULER=true"
    elif enable_raw is False:
        return False, f"ENVIRONMENT={env!r} ENABLE_SCHEDULER=false → off (explicit)"
    else:
        explicit_on = env == "production"
        why_default = (
            "ENABLE_SCHEDULER unset; default on only when ENVIRONMENT=production"
            if explicit_on
            else "ENABLE_SCHEDULER unset; ENVIRONMENT is not production → off"
        )

    if not explicit_on:
        return False, f"ENVIRONMENT={env!r} ENABLE_SCHEDULER={enable_repr} → off ({why_default})"

    if env != "production":
        raw = (settings.DATABASE_URL or "").replace("postgresql+asyncpg://", "postgresql://")
        host = (urlparse(raw).hostname or "").lower()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return (
                False,
                f"ENVIRONMENT={env!r} ENABLE_SCHEDULER={enable_repr} → off "
                f"(non-local DB host blocked outside production)",
            )

    return True, f"ENVIRONMENT={env!r} ENABLE_SCHEDULER={enable_repr} → on ({why_default})"


def scheduler_should_start() -> bool:
    """Decide whether APScheduler jobs may be registered.

    - Explicit ENABLE_SCHEDULER=true/false wins.
    - Default: on only when ENVIRONMENT=production.
    - Always off for ENVIRONMENT=test.
    - Off when DATABASE_URL host is non-local unless ENVIRONMENT=production
      (blocks local-dev process pointed at a remote DB via tunnel/URL).
    """
    ok, _ = _scheduler_decision()
    return ok


def log_scheduler_startup_status() -> None:
    """One clear startup line: whether the scheduler will run and why."""
    ok, reason = _scheduler_decision()
    logger.info("Scheduler startup: %s — %s", "RUNNING" if ok else "NOT RUNNING", reason)


def start_scheduler():
    global _scheduler
    if not scheduler_should_start():
        return
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        # Daily job at 09:00 Asia/Kolkata — generate monthly invoices + overdue
        _scheduler.add_job(
            run_daily_maintenance_jobs,
            'cron',
            hour=9,
            minute=0,
            timezone=KOLKATA_TZ,
            id='daily_maintenance_job'
        )
        # Evening: alternate-day payment reminders inside due window
        _scheduler.add_job(
            run_evening_payment_reminders,
            'cron',
            hour=18,
            minute=0,
            timezone=KOLKATA_TZ,
            id='evening_payment_reminders_job'
        )
        # Hourly: overdue follow-ups using founder-configured hours
        _scheduler.add_job(
            run_hourly_overdue_reminders,
            'cron',
            minute=5,
            timezone=KOLKATA_TZ,
            id='hourly_overdue_reminders_job'
        )
        _scheduler.start()
        logger.info(
            "Scheduler started: 09:00 generate/overdue, 18:00 alt-day reminders, hourly overdue follow-ups."
        )

        # Run catch-up once at app startup
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(run_daily_maintenance_jobs())
        except RuntimeError:
            pass


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Maintenance scheduler stopped.")


def registered_job_ids() -> list[str]:
    if _scheduler is None:
        return []
    return [j.id for j in _scheduler.get_jobs()]
