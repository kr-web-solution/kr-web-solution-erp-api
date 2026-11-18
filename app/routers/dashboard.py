from datetime import date, datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..core.dependencies import get_db, get_current_user
from ..models import Project, Lead, Invoice, User, LateFeeAction, Client
from ..schemas import (
    DashboardResponse,
    RecentItem,
    LateFeeActionResponse,
    RevenueMonthPoint,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def _last_six_month_keys(today: date) -> List[tuple[str, str, date, date]]:
    """Return [(label, YYYY-MM, start, end_exclusive), ...] oldest → newest."""
    months: List[tuple[str, str, date, date]] = []
    y, m = today.year, today.month
    for _ in range(6):
        start = date(y, m, 1)
        if m == 12:
            end = date(y + 1, 1, 1)
        else:
            end = date(y, m + 1, 1)
        label = start.strftime("%b")
        key = start.strftime("%Y-%m")
        months.append((label, key, start, end))
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    months.reverse()
    return months


async def _revenue_by_month(
    db: AsyncSession,
    today: date,
    *,
    client_id: Optional[str] = None,
) -> List[RevenueMonthPoint]:
    """Billed = non-draft invoices by issue_date; collected = paid by paid_at (IST)."""
    window = _last_six_month_keys(today)
    range_start = window[0][2]
    range_end = window[-1][3]

    billed_map = {key: 0 for _, key, _, _ in window}
    collected_map = {key: 0 for _, key, _, _ in window}

    billed_stmt = select(Invoice.issue_date, Invoice.total_paise).where(
        Invoice.status != "draft",
        Invoice.issue_date >= range_start,
        Invoice.issue_date < range_end,
    )
    if client_id:
        billed_stmt = billed_stmt.where(Invoice.client_id == client_id)
    for issue_date, total in (await db.execute(billed_stmt)).all():
        if not issue_date:
            continue
        key = issue_date.strftime("%Y-%m")
        if key in billed_map:
            billed_map[key] += int(total or 0)

    paid_stmt = select(Invoice.paid_at, Invoice.issue_date, Invoice.total_paise).where(
        Invoice.status == "paid",
    )
    if client_id:
        paid_stmt = paid_stmt.where(Invoice.client_id == client_id)
    for paid_at, issue_date, total in (await db.execute(paid_stmt)).all():
        when = None
        if paid_at is not None:
            if getattr(paid_at, "tzinfo", None) is None:
                paid_at = paid_at.replace(tzinfo=timezone.utc)
            when = paid_at.astimezone(KOLKATA_TZ).date()
        elif issue_date is not None:
            when = issue_date
        if when is None or when < range_start or when >= range_end:
            continue
        key = when.strftime("%Y-%m")
        if key in collected_map:
            collected_map[key] += int(total or 0)

    return [
        RevenueMonthPoint(
            month=label,
            year_month=key,
            billed_paise=billed_map[key],
            collected_paise=collected_map[key],
        )
        for label, key, _, _ in window
    ]

@router.get("", response_model=DashboardResponse)
async def get_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    today = datetime.now(KOLKATA_TZ).date()
    revenue_by_month: List[RevenueMonthPoint] = []

    if current_user.role == "worker":
        # Worker: only assigned projects
        running_projects_stmt = (
            select(func.count())
            .select_from(Project)
            .join(Project.workers)
            .where(User.id == current_user.id, Project.status == "running")
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
            .where(User.id == current_user.id)
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
            .where(Project.client_id == client_id, Project.status == "running")
        )
        running_projects_count = (await db.scalar(running_projects_stmt)) or 0
        open_leads_count = 0
        failed_email_invoices_count = 0
        needs_action_invoices = []

        # Unpaid invoices for client (sent + overdue)
        unpaid_inv_stmt = (
            select(func.coalesce(func.sum(Invoice.total_paise), 0))
            .where(Invoice.client_id == client_id, Invoice.status.in_(["sent", "overdue"]))
        )
        unpaid_invoices_total_paise = (await db.scalar(unpaid_inv_stmt)) or 0

        # Overdue invoices: status == 'overdue' OR (status == 'sent' AND due_date < today)
        overdue_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.client_id == client_id,
                ((Invoice.status == "overdue") | ((Invoice.status == "sent") & (Invoice.due_date < today)))
            )
        )
        overdue_invoices_count = (await db.scalar(overdue_stmt)) or 0

        # Recent invoices + projects for client
        rec_inv_stmt = (
            select(Invoice)
            .where(Invoice.client_id == client_id, Invoice.status != "draft")
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
        revenue_by_month = await _revenue_by_month(db, today, client_id=client_id)

    else:
        # Admin / Founder CEO: Complete business overview
        running_projects_stmt = (
            select(func.count())
            .select_from(Project)
            .where(Project.status == "running")
        )
        running_projects_count = (await db.scalar(running_projects_stmt)) or 0

        open_leads_stmt = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.status.in_(["new", "contacted", "proposal"]))
        )
        open_leads_count = (await db.scalar(open_leads_stmt)) or 0

        unpaid_inv_stmt = (
            select(func.coalesce(func.sum(Invoice.total_paise), 0))
            .where(Invoice.status.in_(["sent", "overdue"]))
        )
        unpaid_invoices_total_paise = (await db.scalar(unpaid_inv_stmt)) or 0

        overdue_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(
                (Invoice.status == "overdue") |
                ((Invoice.status == "sent") & (Invoice.due_date < today))
            )
        )
        overdue_invoices_count = (await db.scalar(overdue_stmt)) or 0

        failed_email_stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.email_status == "failed")
        )
        failed_email_invoices_count = (await db.scalar(failed_email_stmt)) or 0

        needs_act_stmt = (
            select(LateFeeAction, Invoice.invoice_number, Client.name.label("client_name"))
            .join(Invoice, LateFeeAction.invoice_id == Invoice.id)
            .join(Client, Invoice.client_id == Client.id)
            .where(LateFeeAction.status == "pending")
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

        # Gather recent 5 items across projects, leads, and invoices
        rec_prj = (await db.execute(select(Project).order_by(Project.created_at.desc()).limit(3))).scalars().all()
        rec_lead = (await db.execute(select(Lead).order_by(Lead.created_at.desc()).limit(3))).scalars().all()
        rec_inv = (await db.execute(select(Invoice).order_by(Invoice.created_at.desc()).limit(3))).scalars().all()

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
        revenue_by_month = await _revenue_by_month(db, today)

    return DashboardResponse(
        running_projects_count=running_projects_count,
        open_leads_count=open_leads_count,
        unpaid_invoices_total_paise=unpaid_invoices_total_paise,
        overdue_invoices_count=overdue_invoices_count,
        failed_email_invoices_count=failed_email_invoices_count,
        recent_items=recent_items,
        needs_action_invoices=needs_action_invoices,
        revenue_by_month=revenue_by_month,
    )

