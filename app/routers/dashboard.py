from datetime import date, datetime, timedelta, timezone
from typing import List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..core.dependencies import get_db, get_current_user, is_privileged
from ..core.database import not_deleted
from ..models import Project, Lead, Invoice, InvoicePayment, User, LateFeeAction, Client
from ..schemas import (
    DashboardResponse,
    RecentItem,
    LateFeeActionResponse,
    RevenueMonthPoint,
)
from ..services.project_visibility import projects_visible_to_client

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
# Revenue / AR must ignore soft-deleted rows and voided numbers (F-043).
_EXCLUDED_INVOICE_STATUSES = ("draft", "cancelled", "void")
_LIVE_UNPAID_STATUSES = ("sent", "overdue")

DashboardPeriod = Literal["week", "month", "6m", "1y", "full"]
_VALID_PERIODS = ("week", "month", "6m", "1y", "full")

# Bucket: (label, key, start_inclusive, end_exclusive)
Bucket = Tuple[str, str, date, date]


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _month_buckets(end: date, count: int) -> List[Bucket]:
    """Last `count` calendar months ending at `end`'s month. Oldest → newest."""
    months: List[Bucket] = []
    y, m = end.year, end.month
    for _ in range(count):
        start = date(y, m, 1)
        ny, nm = _add_months(y, m, 1)
        end_ex = date(ny, nm, 1)
        months.append((start.strftime("%b"), start.strftime("%Y-%m"), start, end_ex))
        y, m = _add_months(y, m, -1)
    months.reverse()
    return months


def _day_buckets(start: date, end_inclusive: date) -> List[Bucket]:
    buckets: List[Bucket] = []
    d = start
    while d <= end_inclusive:
        buckets.append((d.strftime("%d %b"), d.strftime("%Y-%m-%d"), d, d + timedelta(days=1)))
        d += timedelta(days=1)
    return buckets


async def _earliest_invoice_date(
    db: AsyncSession, *, client_id: Optional[str] = None
) -> Optional[date]:
    stmt = select(func.min(Invoice.issue_date)).where(
        not_deleted(Invoice),
        Invoice.status.notin_(_EXCLUDED_INVOICE_STATUSES),
    )
    if client_id:
        stmt = stmt.where(Invoice.client_id == client_id)
    return await db.scalar(stmt)


async def _period_buckets(
    db: AsyncSession,
    today: date,
    period: DashboardPeriod,
    *,
    client_id: Optional[str] = None,
) -> tuple[str, List[Bucket]]:
    """Return (grain, buckets) where grain is 'day' or 'month'."""
    if period == "week":
        start = today - timedelta(days=6)
        return "day", _day_buckets(start, today)
    if period == "month":
        start = date(today.year, today.month, 1)
        return "day", _day_buckets(start, today)
    if period == "6m":
        return "month", _month_buckets(today, 6)
    if period == "1y":
        return "month", _month_buckets(today, 12)
    # full — monthly from earliest invoice (or last 12 months if empty)
    earliest = await _earliest_invoice_date(db, client_id=client_id)
    if earliest is None:
        return "month", _month_buckets(today, 12)
    start_month = date(earliest.year, earliest.month, 1)
    months: List[Bucket] = []
    y, m = start_month.year, start_month.month
    while True:
        start = date(y, m, 1)
        if start > date(today.year, today.month, 1):
            break
        ny, nm = _add_months(y, m, 1)
        end_ex = date(ny, nm, 1)
        label = start.strftime("%b %y") if (today.year - earliest.year) >= 1 else start.strftime("%b")
        months.append((label, start.strftime("%Y-%m"), start, end_ex))
        y, m = ny, nm
    return "month", months or _month_buckets(today, 1)


def _bucket_key(when: date, grain: str) -> str:
    return when.strftime("%Y-%m-%d") if grain == "day" else when.strftime("%Y-%m")


def _period_label(period: DashboardPeriod, today: date, window: List[Bucket]) -> str:
    if not window:
        return "No range"
    start, end_ex = window[0][2], window[-1][3]
    end = end_ex - timedelta(days=1)
    if period == "week":
        return f"{start.day} {start.strftime('%b')}–{end.day} {end.strftime('%b %Y')}"
    if period == "month":
        return today.strftime("%b %Y")  # e.g. Nov 2026
    if period in ("6m", "1y"):
        return f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"
    return "All time"


def _to_ist_date(value: Optional[datetime], fallback: Optional[date] = None) -> Optional[date]:
    if value is None:
        return fallback
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KOLKATA_TZ).date()


async def _revenue_by_period(
    db: AsyncSession,
    today: date,
    period: DashboardPeriod = "6m",
    *,
    client_id: Optional[str] = None,
) -> tuple[List[RevenueMonthPoint], str, int, int]:
    """Billed by invoice issue_date; collected by verified payment paid_at (IST)."""
    grain, window = await _period_buckets(db, today, period, client_id=client_id)
    if not window:
        return [], _period_label(period, today, []), 0, 0
    range_start = window[0][2]
    range_end = window[-1][3]
    label = _period_label(period, today, window)

    billed_map = {key: 0 for _, key, _, _ in window}
    collected_map = {key: 0 for _, key, _, _ in window}

    billed_stmt = select(Invoice.issue_date, Invoice.total_paise).where(
        not_deleted(Invoice),
        Invoice.status.notin_(_EXCLUDED_INVOICE_STATUSES),
        Invoice.issue_date >= range_start,
        Invoice.issue_date < range_end,
    )
    if client_id:
        billed_stmt = billed_stmt.where(Invoice.client_id == client_id)
    for issue_date, total in (await db.execute(billed_stmt)).all():
        if not issue_date:
            continue
        key = _bucket_key(issue_date, grain)
        if key in billed_map:
            billed_map[key] += int(total or 0)

    # Collected = money actually received in the window (payment date), not invoice issue date
    pay_stmt = (
        select(InvoicePayment.paid_at, InvoicePayment.amount_paise, Invoice.issue_date)
        .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
        .where(
            not_deleted(InvoicePayment),
            not_deleted(Invoice),
            InvoicePayment.status == "verified",
            Invoice.status.notin_(_EXCLUDED_INVOICE_STATUSES),
        )
    )
    if client_id:
        pay_stmt = pay_stmt.where(Invoice.client_id == client_id)
    for paid_at, amount, issue_date in (await db.execute(pay_stmt)).all():
        when = _to_ist_date(paid_at, fallback=issue_date)
        if when is None or when < range_start or when >= range_end:
            continue
        key = _bucket_key(when, grain)
        if key in collected_map:
            collected_map[key] += int(amount or 0)

    points = [
        RevenueMonthPoint(
            month=lbl,
            year_month=key,
            billed_paise=billed_map[key],
            collected_paise=collected_map[key],
        )
        for lbl, key, _, _ in window
    ]
    billed_total = sum(p.billed_paise for p in points)
    collected_total = sum(p.collected_paise for p in points)
    return points, label, billed_total, collected_total


@router.get("", response_model=DashboardResponse)
async def get_dashboard_summary(
    period: str = Query("month", description="week | month | 6m | 1y | full"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    today = datetime.now(KOLKATA_TZ).date()
    period_key: DashboardPeriod = period if period in _VALID_PERIODS else "month"  # type: ignore[assignment]
    revenue_by_month: List[RevenueMonthPoint] = []
    period_label = _period_label(period_key, today, [])
    billed_paise = 0
    collected_paise = 0

    if current_user.role == "worker":
        # Worker: only assigned projects
        running_projects_stmt = (
            select(func.count())
            .select_from(Project)
            .join(Project.workers)
            .where(
                User.id == current_user.id,
                Project.status == "running",
                not_deleted(Project),
            )
        )
        running_projects_count = (await db.scalar(running_projects_stmt)) or 0
        open_leads_count = 0
        failed_email_invoices_count = 0
        needs_action_invoices = []
        unpaid_invoices_total_paise = 0
        overdue_invoices_count = 0
        failed_email_invoices_count = 0
        needs_action_invoices = []

        # Recent 5 projects assigned to worker
        rec_prj_stmt = (
            select(Project)
            .join(Project.workers)
            .where(User.id == current_user.id, not_deleted(Project))
            .order_by(Project.updated_at.desc())
            .limit(5)
        )
        recent_projects = (await db.execute(rec_prj_stmt)).scalars().all()
        recent_items = [
            RecentItem(
                id=p.id,
                type="project",
                title=p.name,
                subtitle=f"Status: {p.status}",
                status=p.status,
                date=p.updated_at
            )
            for p in recent_projects
        ]

    elif current_user.role == "client":
        client_id = current_user.client_id or ""
        running_projects_stmt = (
            select(func.count())
            .select_from(Project)
            .where(
                projects_visible_to_client(client_id),
                Project.status == "running",
                not_deleted(Project),
            )
        )
        running_projects_count = (await db.scalar(running_projects_stmt)) or 0
        open_leads_count = 0
        failed_email_invoices_count = 0
        needs_action_invoices = []

        # Unpaid invoices for client (sent + overdue); exclude soft-deleted / void
        unpaid_inv_stmt = (
            select(func.coalesce(func.sum(Invoice.total_paise), 0))
            .where(
                not_deleted(Invoice),
                Invoice.client_id == client_id,
                Invoice.status.in_(_LIVE_UNPAID_STATUSES),
            )
        )
        unpaid_invoices_total_paise = (await db.scalar(unpaid_inv_stmt)) or 0

        overdue_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(
                not_deleted(Invoice),
                Invoice.client_id == client_id,
                ((Invoice.status == "overdue") | ((Invoice.status == "sent") & (Invoice.due_date < today)))
            )
        )
        overdue_invoices_count = (await db.scalar(overdue_stmt)) or 0

        rec_inv_stmt = (
            select(Invoice)
            .where(
                not_deleted(Invoice),
                Invoice.client_id == client_id,
                Invoice.status.notin_(_EXCLUDED_INVOICE_STATUSES),
            )
            .order_by(Invoice.created_at.desc())
            .limit(5)
        )
        recent_invoices = (await db.execute(rec_inv_stmt)).scalars().all()
        recent_items = [
            RecentItem(
                id=inv.id,
                type="invoice",
                title=f"Invoice {inv.invoice_number}",
                subtitle=f"Rs. {inv.total_paise / 100:,.2f}",
                status=inv.status,
                date=inv.created_at
            )
            for inv in recent_invoices
        ]
        revenue_by_month, period_label, billed_paise, collected_paise = await _revenue_by_period(
            db, today, period_key, client_id=client_id
        )

    elif not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    else:
        # Admin / Founder CEO: Complete business overview
        running_projects_stmt = (
            select(func.count())
            .select_from(Project)
            .where(Project.status == "running", not_deleted(Project))
        )
        running_projects_count = (await db.scalar(running_projects_stmt)) or 0

        open_leads_stmt = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.status.in_(["new", "contacted", "proposal"]), not_deleted(Lead))
        )
        open_leads_count = (await db.scalar(open_leads_stmt)) or 0

        unpaid_inv_stmt = (
            select(func.coalesce(func.sum(Invoice.total_paise), 0))
            .where(
                not_deleted(Invoice),
                Invoice.status.in_(_LIVE_UNPAID_STATUSES),
            )
        )
        unpaid_invoices_total_paise = (await db.scalar(unpaid_inv_stmt)) or 0

        overdue_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(
                not_deleted(Invoice),
                (Invoice.status == "overdue") |
                ((Invoice.status == "sent") & (Invoice.due_date < today))
            )
        )
        overdue_invoices_count = (await db.scalar(overdue_stmt)) or 0

        failed_email_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(not_deleted(Invoice), Invoice.email_status == "failed")
        )
        failed_email_invoices_count = (await db.scalar(failed_email_stmt)) or 0

        needs_act_stmt = (
            select(LateFeeAction, Invoice.invoice_number, Client.name.label("client_name"))
            .join(Invoice, LateFeeAction.invoice_id == Invoice.id)
            .join(Client, Invoice.client_id == Client.id)
            .where(
                LateFeeAction.status == "pending",
                not_deleted(Invoice),
                not_deleted(Client),
            )
            .order_by(LateFeeAction.created_at.desc())
        )
        res = (await db.execute(needs_act_stmt)).all()
        needs_action_invoices = [
            LateFeeActionResponse(
                id=action.id,
                invoice_id=action.invoice_id,
                invoice_number=inv_num,
                client_name=cli_name,
                fee_paise=action.fee_paise,
                fee_type=action.fee_type,
                fee_value=action.fee_value,
                reason=action.reason,
                send_on=action.send_on,
                repeat_every_days=action.repeat_every_days,
                repeat_count=action.repeat_count,
                status=action.status,
                decided_by=action.decided_by,
                decided_at=action.decided_at,
                late_fee_invoice_id=action.late_fee_invoice_id,
                created_at=action.created_at,
                updated_at=action.updated_at,
            )
            for action, inv_num, cli_name in res
        ]

        rec_prj = (
            await db.execute(
                select(Project)
                .where(not_deleted(Project))
                .order_by(Project.created_at.desc())
                .limit(3)
            )
        ).scalars().all()
        rec_lead = (
            await db.execute(
                select(Lead).where(not_deleted(Lead)).order_by(Lead.created_at.desc()).limit(3)
            )
        ).scalars().all()
        rec_inv = (
            await db.execute(
                select(Invoice)
                .where(
                    not_deleted(Invoice),
                    Invoice.status.notin_(_EXCLUDED_INVOICE_STATUSES),
                )
                .order_by(Invoice.created_at.desc())
                .limit(3)
            )
        ).scalars().all()

        all_recent = []
        for p in rec_prj:
            all_recent.append(RecentItem(
                id=p.id,
                type="project",
                title=p.name,
                subtitle="Project",
                status=p.status,
                date=p.created_at
            ))
        for l in rec_lead:
            all_recent.append(RecentItem(
                id=l.id,
                type="lead",
                title=l.name,
                subtitle=l.company,
                status=l.status,
                date=l.created_at
            ))
        for inv in rec_inv:
            all_recent.append(RecentItem(
                id=inv.id,
                type="invoice",
                title=f"Invoice {inv.invoice_number}",
                subtitle=f"Rs. {inv.total_paise / 100:,.2f}",
                status=inv.status,
                date=inv.created_at
            ))

        all_recent.sort(key=lambda x: x.date, reverse=True)
        recent_items = all_recent[:5]
        revenue_by_month, period_label, billed_paise, collected_paise = await _revenue_by_period(
            db, today, period_key
        )

    return DashboardResponse(
        running_projects_count=running_projects_count,
        open_leads_count=open_leads_count,
        unpaid_invoices_total_paise=unpaid_invoices_total_paise,
        overdue_invoices_count=overdue_invoices_count,
        failed_email_invoices_count=failed_email_invoices_count,
        recent_items=recent_items,
        needs_action_invoices=needs_action_invoices,
        revenue_by_month=revenue_by_month,
        period=period_key,
        period_label=period_label,
        billed_paise=billed_paise,
        collected_paise=collected_paise,
    )

