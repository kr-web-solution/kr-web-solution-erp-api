"""Company / studio expense ledger — founder and admin only."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import extract, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, require_founder_ceo
from ..models import CompanyExpense, User
from ..schemas import (
    CompanyExpenseListResponse,
    CompanyExpenseResponse,
    CompanyExpenseSectionTotal,
)
from ..services.company_expense_files import (
    delete_company_expense_receipt,
    read_company_expense_receipt,
    save_company_expense_receipt,
)

router = APIRouter(prefix="/company-expenses", tags=["Company Expenses"])

SECTIONS = {"recurring", "daily", "upgrade", "other"}
RENEWAL_INTERVALS = {1, 3, 6, 12}
DUE_SOON_DAYS = 7
RENEWAL_HORIZON_DAYS = 60

SECTION_CATEGORIES = {
    "recurring": {"server", "domain", "hosting", "software", "email"},
    "daily": {"food", "travel", "supplies", "fuel", "petty_cash"},
    "upgrade": {"furniture", "equipment", "investment"},
    "other": {"other"},
}


def _normalize_category(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_").replace("-", "_")


def _validate_section_category(section: str, category: str) -> str:
    if section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Invalid section")
    cat = _normalize_category(category)
    allowed = SECTION_CATEGORIES[section]
    if cat not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category for {section}. Allowed: {', '.join(sorted(allowed))}",
        )
    return cat


def _validate_interval(raw: Optional[int]) -> int:
    if raw is None:
        return 1
    try:
        months = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid renewal interval") from exc
    if months not in RENEWAL_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail="renewal_interval_months must be 1, 3, 6, or 12",
        )
    return months


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _due_status(next_renewal_on: Optional[date], today: Optional[date] = None) -> Optional[str]:
    if not next_renewal_on:
        return None
    today = today or date.today()
    if next_renewal_on < today:
        return "overdue"
    if next_renewal_on <= today + timedelta(days=DUE_SOON_DAYS):
        return "due_soon"
    return "upcoming"


def _expense_response(row: CompanyExpense) -> CompanyExpenseResponse:
    return CompanyExpenseResponse(
        id=row.id,
        expense_date=row.expense_date,
        section=row.section,
        category=row.category,
        title=row.title,
        vendor=row.vendor,
        amount_paise=row.amount_paise,
        note=row.note,
        receipt_object_key=row.receipt_object_key,
        receipt_original_filename=row.receipt_original_filename,
        renewal_interval_months=row.renewal_interval_months,
        next_renewal_on=row.next_renewal_on,
        due_status=_due_status(row.next_renewal_on) if row.section == "recurring" else None,
        created_by_user_id=row.created_by_user_id,
        created_by_name=row.created_by.full_name if row.created_by else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _parse_month(month: Optional[str]) -> tuple[int, int]:
    """Return (year, month) for YYYY-MM, defaulting to current month."""
    today = date.today()
    if not month:
        return today.year, today.month
    try:
        year_s, month_s = month.split("-", 1)
        year, mon = int(year_s), int(month_s)
        if mon < 1 or mon > 12:
            raise ValueError
        return year, mon
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM") from exc


@router.get("", response_model=CompanyExpenseListResponse)
async def list_company_expenses(
    section: Optional[str] = Query(None),
    month: Optional[str] = Query(
        None, description="Optional YYYY-MM filter; omit to list all"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    if section and section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Invalid section")

    q = select(CompanyExpense).options(selectinload(CompanyExpense.created_by))
    if month:
        year, mon = _parse_month(month)
        q = q.where(
            extract("year", CompanyExpense.expense_date) == year,
            extract("month", CompanyExpense.expense_date) == mon,
        )
    if section:
        q = q.where(CompanyExpense.section == section)
        # Recurring page lists subscriptions only (payments from "Mark renewed" have no interval)
        if section == "recurring":
            q = q.where(CompanyExpense.renewal_interval_months.is_not(None))
    q = q.order_by(CompanyExpense.expense_date.desc(), CompanyExpense.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    items = [_expense_response(r) for r in rows]

    # Section totals always across the full ledger (not month-scoped)
    all_q = select(CompanyExpense)
    all_rows = (await db.execute(all_q)).scalars().all()
    totals_map = {s: 0 for s in ("recurring", "daily", "upgrade", "other")}
    for r in all_rows:
        totals_map[r.section] = totals_map.get(r.section, 0) + r.amount_paise

    section_totals = [
        CompanyExpenseSectionTotal(section=s, total_paise=totals_map[s])
        for s in ("recurring", "daily", "upgrade", "other")
    ]
    month_total = sum(totals_map.values())  # all-time total (field name kept for API compat)
    largest_row = max(all_rows, key=lambda r: r.amount_paise, default=None)
    largest = _expense_response(largest_row) if largest_row is not None else None

    today = date.today()
    horizon = today + timedelta(days=RENEWAL_HORIZON_DAYS)
    renew_q = (
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(
            CompanyExpense.section == "recurring",
            CompanyExpense.next_renewal_on.is_not(None),
            or_(
                CompanyExpense.next_renewal_on < today,  # overdue
                CompanyExpense.next_renewal_on <= horizon,
            ),
        )
        .order_by(CompanyExpense.next_renewal_on.asc())
        .limit(50)
    )
    renewals = [_expense_response(r) for r in (await db.execute(renew_q)).scalars().all()]

    return CompanyExpenseListResponse(
        items=items,
        section_totals=section_totals,
        month_total_paise=month_total,
        largest=largest,
        upcoming_renewals=renewals,
    )


@router.post("", response_model=CompanyExpenseResponse, status_code=201)
async def create_company_expense(
    expense_date: date = Form(...),
    section: str = Form(...),
    category: str = Form(...),
    title: str = Form(...),
    amount_paise: int = Form(...),
    vendor: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    next_renewal_on: Optional[date] = Form(None),
    renewal_interval_months: Optional[int] = Form(None),
    receipt: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    cat = _validate_section_category(section, category)
    if amount_paise <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    title_clean = (title or "").strip()
    if not title_clean:
        raise HTTPException(status_code=400, detail="Title is required")

    interval = None
    if section == "recurring":
        interval = _validate_interval(renewal_interval_months)
        if next_renewal_on is None:
            next_renewal_on = add_months(expense_date, interval)
    else:
        next_renewal_on = None

    key = original = content_type = None
    if receipt and receipt.filename:
        key, original, content_type = await save_company_expense_receipt(receipt)

    row = CompanyExpense(
        expense_date=expense_date,
        section=section,
        category=cat,
        title=title_clean,
        vendor=(vendor or "").strip() or None,
        amount_paise=amount_paise,
        note=(note or "").strip() or None,
        receipt_object_key=key,
        receipt_original_filename=original,
        receipt_content_type=content_type,
        renewal_interval_months=interval,
        next_renewal_on=next_renewal_on,
        created_by_user_id=current_user.id,
    )
    db.add(row)
    await db.flush()
    result = await db.execute(
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(CompanyExpense.id == row.id)
    )
    return _expense_response(result.scalars().one())


@router.patch("/{expense_id}", response_model=CompanyExpenseResponse)
async def update_company_expense(
    expense_id: str,
    expense_date: Optional[date] = Form(None),
    section: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    amount_paise: Optional[int] = Form(None),
    vendor: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    next_renewal_on: Optional[date] = Form(None),
    renewal_interval_months: Optional[int] = Form(None),
    clear_renewal: Optional[bool] = Form(False),
    receipt: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    result = await db.execute(
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(CompanyExpense.id == expense_id)
    )
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")

    new_section = section if section is not None else row.section
    if section is not None and section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Invalid section")

    if category is not None or section is not None:
        cat_source = category if category is not None else row.category
        row.category = _validate_section_category(new_section, cat_source)
    row.section = new_section

    if expense_date is not None:
        row.expense_date = expense_date
    if title is not None:
        title_clean = title.strip()
        if not title_clean:
            raise HTTPException(status_code=400, detail="Title is required")
        row.title = title_clean
    if amount_paise is not None:
        if amount_paise <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        row.amount_paise = amount_paise
    if vendor is not None:
        row.vendor = vendor.strip() or None
    if note is not None:
        row.note = note.strip() or None

    if row.section != "recurring" or clear_renewal:
        row.next_renewal_on = None
        row.renewal_interval_months = None
    else:
        if renewal_interval_months is not None:
            row.renewal_interval_months = _validate_interval(renewal_interval_months)
        elif row.renewal_interval_months is None:
            row.renewal_interval_months = 1
        if next_renewal_on is not None:
            row.next_renewal_on = next_renewal_on

    if receipt and receipt.filename:
        if row.receipt_object_key:
            delete_company_expense_receipt(row.receipt_object_key)
        key, original, content_type = await save_company_expense_receipt(receipt)
        row.receipt_object_key = key
        row.receipt_original_filename = original
        row.receipt_content_type = content_type

    await db.flush()
    result = await db.execute(
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(CompanyExpense.id == row.id)
    )
    return _expense_response(result.scalars().one())


@router.post("/{expense_id}/renew", response_model=CompanyExpenseResponse)
async def renew_company_expense(
    expense_id: str,
    next_renewal_on: date = Form(...),
    receipt: UploadFile = File(...),
    amount_paise: Optional[int] = Form(None),
    payment_date: Optional[date] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Complete a renewal: set next due date and store this payment's receipt."""
    result = await db.execute(
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(CompanyExpense.id == expense_id)
    )
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")
    if row.section != "recurring":
        raise HTTPException(status_code=400, detail="Only recurring expenses can be renewed")
    if not receipt or not receipt.filename:
        raise HTTPException(status_code=400, detail="Payment receipt is required")

    interval = row.renewal_interval_months or 1
    if interval not in RENEWAL_INTERVALS:
        interval = 1
        row.renewal_interval_months = 1

    today = date.today()
    paid_on = payment_date or today
    if next_renewal_on <= paid_on:
        raise HTTPException(
            status_code=400,
            detail="Next renewal date must be after the payment date",
        )

    paid_amount = amount_paise if amount_paise is not None else row.amount_paise
    if paid_amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Store this period's payment as its own ledger row (no renewal schedule)
    key, original, content_type = await save_company_expense_receipt(receipt)
    payment = CompanyExpense(
        expense_date=paid_on,
        section="recurring",
        category=row.category,
        title=f"{row.title} (renewal payment)",
        vendor=row.vendor,
        amount_paise=paid_amount,
        note=f"Renewal payment for {row.title}",
        receipt_object_key=key,
        receipt_original_filename=original,
        receipt_content_type=content_type,
        renewal_interval_months=None,
        next_renewal_on=None,
        created_by_user_id=current_user.id,
    )
    db.add(payment)

    # Advance the subscription
    row.next_renewal_on = next_renewal_on
    row.renewal_interval_months = interval
    if amount_paise is not None:
        row.amount_paise = paid_amount

    await db.flush()
    result = await db.execute(
        select(CompanyExpense)
        .options(selectinload(CompanyExpense.created_by))
        .where(CompanyExpense.id == row.id)
    )
    return _expense_response(result.scalars().one())


@router.delete("/{expense_id}")
async def delete_company_expense(
    expense_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(CompanyExpense).where(CompanyExpense.id == expense_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")
    if row.receipt_object_key:
        delete_company_expense_receipt(row.receipt_object_key)
    await db.delete(row)
    await db.flush()
    return {"message": "Deleted"}


@router.get("/{expense_id}/receipt")
async def download_company_expense_receipt(
    expense_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(CompanyExpense).where(CompanyExpense.id == expense_id))
    row = result.scalars().first()
    if not row or not row.receipt_object_key:
        raise HTTPException(status_code=404, detail="Receipt not found")
    data = read_company_expense_receipt(row.receipt_object_key)
    filename = row.receipt_original_filename or "receipt"
    return Response(
        content=data,
        media_type=row.receipt_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
