"""Invoice number allocation: KRWS-SVC-2026-1001 (calendar year).

GST ON:  {COMPANY}-{CODE}-{YEAR}-{SEQ}       e.g. KRWS-SVC-2026-1001
GST OFF: {COMPANY}-{CODE}-{YEAR}-G-{SEQ}     e.g. KRWS-SVC-2026-G-1001

KRWS = company short code · SVC = invoice type · 2026 = calendar year · 1001 = sequence
(GST on/off series are independent counters; both start at 1001 by default).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..models import Invoice, Settings

logger = logging.getLogger("kr.invoice_numbers")

MAX_UNIQUE_RETRIES = 8

# First number in a new service/year/(GST) series
DEFAULT_SEQ_START = 1001

# Service label → short code used in the invoice number
INVOICE_SERVICE_CODES: Dict[str, str] = {
    "development": "DEV",
    "custom_dev": "DEV",
    "project": "DEV",
    "advance": "DEV",
    "balance": "DEV",
    "maintenance": "MNT",
    "amc": "MNT",
    "hosting": "HST",
    "services": "SVC",
    "licensing": "LIC",
    "late_fee": "SVC",
    "credit_note": "CRN",
    "debit_note": "DBN",
    "mixed": "MIX",
}

INVOICE_SERVICE_LABELS: Dict[str, str] = {
    "DEV": "Development",
    "MNT": "Maintenance",
    "HST": "Hosting",
    "SVC": "Services",
    "LIC": "Licensing",
    "CRN": "Credit note",
    "DBN": "Debit note",
    "MIX": "Mixed",
}

DEFAULT_COMPANY_CODE = "KRWS"


def indian_fy_bounds(d: Optional[date] = None) -> Tuple[date, date, str]:
    """
    Indian financial year (1 Apr – 31 Mar) — used by GST registration windows.

    Returns (fy_start, fy_end, label) where label is e.g. '2526' for FY 2025-26.
    Invoice numbers use calendar year via invoice_year_label(), not this.
    """
    on = d or date.today()
    if on.month >= 4:
        start_year = on.year
    else:
        start_year = on.year - 1
    fy_start = date(start_year, 4, 1)
    fy_end = date(start_year + 1, 3, 31)
    label = f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"
    return fy_start, fy_end, label


def indian_fy_label(d: Optional[date] = None) -> str:
    return indian_fy_bounds(d)[2]


def invoice_year_label(d: Optional[date] = None) -> str:
    """Calendar year used in invoice numbers, e.g. '2026'."""
    on = d or date.today()
    return f"{on.year:04d}"


def company_code_from_settings(st: Settings) -> str:
    raw = (st.invoice_prefix or DEFAULT_COMPANY_CODE).strip()
    if not raw:
        return DEFAULT_COMPANY_CODE
    # Legacy values like "INV-2026-" or "KRWS-DEV-" → take first segment
    code = raw.rstrip("-").split("-")[0].strip().upper()
    return code or DEFAULT_COMPANY_CODE


def service_code_for(invoice_type: Optional[str]) -> str:
    key = (invoice_type or "custom_dev").strip().lower()
    return INVOICE_SERVICE_CODES.get(key, "SVC")


def format_invoice_number(
    company: str,
    service_code: str,
    year_label: str,
    seq: int,
    *,
    gst_enabled: bool = True,
) -> str:
    """
    GST ON:  KRWS-SVC-2026-1001
    GST OFF: KRWS-SVC-2026-G-1001
    """
    if gst_enabled:
        return f"{company}-{service_code}-{year_label}-{seq}"
    return f"{company}-{service_code}-{year_label}-G-{seq}"


def sequence_key(service_code: str, year_label: str, *, gst_enabled: bool) -> str:
    """Independent counters for GST vs non-GST series (calendar year)."""
    if gst_enabled:
        return f"{service_code}-{year_label}"
    return f"{service_code}-{year_label}-G"


def default_prefix_scheme(company: str = DEFAULT_COMPANY_CODE) -> list[dict[str, str]]:
    year = invoice_year_label()
    rows: list[dict[str, str]] = []
    for code, label in INVOICE_SERVICE_LABELS.items():
        rows.append(
            {
                "service": label,
                "code": code,
                "gst": "on",
                "prefix": f"{company}-{code}",
                "example": format_invoice_number(
                    company, code, year, DEFAULT_SEQ_START, gst_enabled=True
                ),
            }
        )
        rows.append(
            {
                "service": f"{label} (non-GST)",
                "code": code,
                "gst": "off",
                "prefix": f"{company}-{code}-G",
                "example": format_invoice_number(
                    company, code, year, DEFAULT_SEQ_START, gst_enabled=False
                ),
            }
        )
    return rows


async def _used_seqs_live(
    db: AsyncSession,
    *,
    code: str,
    year_label: str,
    gst_enabled: bool,
) -> set[int]:
    """Sequence numbers still held by live (non-soft-deleted) invoices in this series."""
    rows = (
        await db.execute(
            select(Invoice.invoice_number).where(Invoice.deleted_at.is_(None))
        )
    ).all()
    used: set[int] = set()
    for (num,) in rows:
        parsed = parse_series_key(num)
        if not parsed:
            continue
        if (
            parsed["code"] == code
            and parsed["year"] == year_label
            and bool(parsed["gst_enabled"]) == bool(gst_enabled)
        ):
            used.add(int(parsed["seq"]))
    return used


async def _free_soft_deleted_number(db: AsyncSession, candidate: str) -> None:
    """Soft-deleted rows still occupy unique invoice_number — park them to revoke."""
    blockers = (
        await db.execute(
            select(Invoice).where(
                Invoice.invoice_number == candidate,
                Invoice.deleted_at.is_not(None),
            )
        )
    ).scalars().all()
    for row in blockers:
        row.invoice_number = f"LEGACY-DEL-{row.id}"
    if blockers:
        await db.flush()


async def peek_lowest_free_invoice_number(
    db: AsyncSession,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
    settings_row: Optional[Settings] = None,
    gst_enabled: bool = True,
) -> str:
    """
    Read-only: lowest free number in the series (treats soft-deleted as free).
    Does not mutate sequences or rows.
    """
    st = settings_row
    if st is None:
        st = (
            await db.execute(select(Settings).where(Settings.id == "default"))
        ).scalar_one_or_none()
    if not st:
        raise ValueError("Settings row missing")
    company = company_code_from_settings(st)
    code = service_code_for(invoice_type)
    year_label = invoice_year_label(issue_date or date.today())
    used = await _used_seqs_live(
        db, code=code, year_label=year_label, gst_enabled=bool(gst_enabled)
    )
    next_num = DEFAULT_SEQ_START
    while next_num in used:
        next_num += 1
    return format_invoice_number(
        company, code, year_label, next_num, gst_enabled=bool(gst_enabled)
    )


async def allocate_invoice_number(
    db: AsyncSession,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
    settings_row: Optional[Settings] = None,
    gst_enabled: Optional[bool] = None,
) -> str:
    """
    Atomically allocate the lowest free number for a service/year/GST series.

    Reuses gaps left by deleted unpaid invoices (revoked numbers).

    GST ON:  {COMPANY}-{CODE}-{YEAR}-{N}     e.g. KRWS-SVC-2026-1001
    GST OFF: {COMPANY}-{CODE}-{YEAR}-G-{N}   e.g. KRWS-SVC-2026-G-1001
    """
    if settings_row is None:
        st_stmt = select(Settings).where(Settings.id == "default").with_for_update()
        settings_row = (await db.execute(st_stmt)).scalar_one_or_none()
    st = settings_row
    if not st:
        raise ValueError("Settings row missing")

    company = company_code_from_settings(st)
    code = service_code_for(invoice_type)
    on = issue_date or date.today()
    year_label = invoice_year_label(on)

    # Resolve GST: explicit arg → else company Settings toggle
    if gst_enabled is None:
        gst_on = bool(getattr(st, "gst_enabled", False))
    else:
        gst_on = bool(gst_enabled)

    seq_key = sequence_key(code, year_label, gst_enabled=gst_on)
    used = await _used_seqs_live(
        db, code=code, year_label=year_label, gst_enabled=gst_on
    )

    # Fill gaps from DEFAULT_SEQ_START upward (revoked / deleted numbers first)
    next_num = DEFAULT_SEQ_START
    while next_num in used:
        next_num += 1

    candidate = format_invoice_number(
        company, code, year_label, next_num, gst_enabled=gst_on
    )
    # Soft-deleted leftovers still hold the unique key — free them
    await _free_soft_deleted_number(db, candidate)

    # Safety: if somehow still taken by a live row, walk forward
    while True:
        live_id = await db.scalar(
            select(Invoice.id).where(
                Invoice.invoice_number == candidate,
                Invoice.deleted_at.is_(None),
            )
        )
        if not live_id:
            await _free_soft_deleted_number(db, candidate)
            still = await db.scalar(
                select(Invoice.id).where(Invoice.invoice_number == candidate)
            )
            if not still:
                break
        next_num += 1
        candidate = format_invoice_number(
            company, code, year_label, next_num, gst_enabled=gst_on
        )

    sequences: Dict[str, Any] = dict(st.invoice_sequences or {})
    tip = max(used | {next_num}) + 1
    sequences[seq_key] = tip
    st.invoice_sequences = sequences
    flag_modified(st, "invoice_sequences")
    st.invoice_next_number = max(int(st.invoice_next_number or 1), tip)
    return candidate


def is_invoice_number_conflict(exc: BaseException) -> bool:
    """True when IntegrityError / UniqueViolation is on invoices.invoice_number."""
    orig = getattr(exc, "orig", None) or exc
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate is None:
        sqlstate = getattr(getattr(orig, "__cause__", None), "sqlstate", None)
    pgcode = getattr(orig, "pgcode", None)
    msg = str(orig).lower()
    unique = sqlstate == "23505" or pgcode == "23505" or "unique" in msg or "duplicate" in msg
    if not unique:
        return False
    return (
        "invoice_number" in msg
        or "ix_invoices_invoice_number" in msg
        or "invoices_invoice_number" in msg
    )


def is_recurring_period_conflict(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None) or exc
    msg = str(orig).lower()
    sqlstate = getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )
    unique = sqlstate == "23505" or "unique" in msg or "duplicate" in msg
    return unique and "uq_invoice_recurring_period" in msg


def resolve_gst_for_numbering(
    invoice: Any = None,
    *,
    gst_enabled: Optional[bool] = None,
    settings_row: Optional[Settings] = None,
) -> bool:
    """Effective GST flag used for the number series."""
    if gst_enabled is not None:
        return bool(gst_enabled)
    if invoice is not None:
        inv_flag = getattr(invoice, "gst_enabled", None)
        if inv_flag is not None:
            return bool(inv_flag)
    if settings_row is not None:
        return bool(getattr(settings_row, "gst_enabled", False))
    return False


# Unpaid statuses may switch GST series when tip-of-series (hard-delete + recreate).
GST_SERIES_CHANGE_STATUSES = frozenset({"draft", "sent", "overdue", "proof_pending"})

GST_TOGGLE_BLOCKED_TIP = (
    "You can’t change GST on this invoice because a later invoice already exists "
    "in this number series (or it is paid / cancelled). "
    "Cancel this invoice (it will stay frozen with this number), then create a new "
    "invoice with the same details on the GST or non-GST series you need."
)


def number_is_non_gst_series(invoice_number: Optional[str]) -> bool:
    """True when number uses the GST-OFF infix (…-YEAR-G-SEQ)."""
    parsed = parse_series_key(invoice_number)
    if not parsed:
        return False
    return not parsed["gst_enabled"]


def parse_series_key(invoice_number: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parse KRWS-SVC-2026-1001 or KRWS-SVC-2026-G-1001.

    Returns dict with company, code, year, gst_enabled, seq — or None if unparseable.
    """
    if not invoice_number:
        return None
    parts = invoice_number.strip().upper().split("-")
    if len(parts) == 5 and parts[3] == "G" and parts[4].isdigit():
        return {
            "company": parts[0],
            "code": parts[1],
            "year": parts[2],
            "gst_enabled": False,
            "seq": int(parts[4]),
        }
    if len(parts) == 4 and parts[3].isdigit():
        return {
            "company": parts[0],
            "code": parts[1],
            "year": parts[2],
            "gst_enabled": True,
            "seq": int(parts[3]),
        }
    return None


def reclaim_sequence_if_tip(settings_row: Settings, invoice_number: Optional[str]) -> bool:
    """
    If this number was the tip of its series (next counter == seq+1), roll the
    counter back so the next allocate reuses the same seq (no gap).
    """
    parsed = parse_series_key(invoice_number)
    if not parsed or not settings_row:
        return False
    key = sequence_key(
        parsed["code"], parsed["year"], gst_enabled=bool(parsed["gst_enabled"])
    )
    sequences: Dict[str, Any] = dict(settings_row.invoice_sequences or {})
    next_num = int(sequences.get(key) or DEFAULT_SEQ_START)
    seq = int(parsed["seq"])
    if next_num != seq + 1:
        return False
    sequences[key] = seq
    settings_row.invoice_sequences = sequences
    flag_modified(settings_row, "invoice_sequences")
    return True


async def max_seq_in_series(
    db: AsyncSession,
    *,
    code: str,
    year: str,
    gst_enabled: bool,
) -> int:
    """Highest live (non-soft-deleted) sequence in a CODE+YEAR+GST series."""
    rows = (
        await db.execute(
            select(Invoice.invoice_number).where(Invoice.deleted_at.is_(None))
        )
    ).all()
    max_seq = 0
    for (num,) in rows:
        parsed = parse_series_key(num)
        if not parsed:
            continue
        if (
            parsed["code"] == code
            and parsed["year"] == year
            and bool(parsed["gst_enabled"]) == bool(gst_enabled)
        ):
            max_seq = max(max_seq, int(parsed["seq"]))
    return max_seq


async def is_latest_in_series(db: AsyncSession, invoice: Invoice) -> bool:
    """True when this live invoice holds the highest seq in its current series."""
    parsed = parse_series_key(getattr(invoice, "invoice_number", None))
    if not parsed:
        return False
    tip = await max_seq_in_series(
        db,
        code=parsed["code"],
        year=parsed["year"],
        gst_enabled=bool(parsed["gst_enabled"]),
    )
    return int(parsed["seq"]) >= tip and tip > 0


DELETE_BLOCKED_NOT_TIP = (
    "You can only delete the latest invoice in this number series "
    "(for example delete …-1002 before …-1001). "
    "If you still need to stop this invoice, cancel it instead — "
    "the number and details stay frozen, then create a new invoice with the same details."
)

_DELETE_BLOCKED_STATUSES = frozenset({"paid", "cancelled", "void"})


def _status_allows_delete(status: Optional[str]) -> bool:
    return (status or "").strip().lower() not in _DELETE_BLOCKED_STATUSES


async def live_series_tips(db: AsyncSession) -> Dict[Tuple[str, str, bool], int]:
    """Map (code, year, gst_enabled) → highest live sequence."""
    rows = (
        await db.execute(
            select(Invoice.invoice_number).where(Invoice.deleted_at.is_(None))
        )
    ).all()
    tips: Dict[Tuple[str, str, bool], int] = {}
    for (num,) in rows:
        parsed = parse_series_key(num)
        if not parsed:
            continue
        key = (parsed["code"], parsed["year"], bool(parsed["gst_enabled"]))
        seq = int(parsed["seq"])
        if seq > tips.get(key, 0):
            tips[key] = seq
    return tips


def can_delete_with_tips(
    invoice: Invoice,
    tips: Dict[Tuple[str, str, bool], int],
) -> bool:
    """True when status allows delete and invoice is tip of its series."""
    if not _status_allows_delete(getattr(invoice, "status", None)):
        return False
    parsed = parse_series_key(getattr(invoice, "invoice_number", None))
    if not parsed:
        return False
    key = (parsed["code"], parsed["year"], bool(parsed["gst_enabled"]))
    tip = tips.get(key, 0)
    return tip > 0 and int(parsed["seq"]) >= tip


async def can_delete_invoice(db: AsyncSession, invoice: Invoice) -> bool:
    if not _status_allows_delete(getattr(invoice, "status", None)):
        return False
    return await is_latest_in_series(db, invoice)


async def assert_can_delete_invoice(db: AsyncSession, invoice: Invoice) -> None:
    """Raise InvoiceStatusError when delete is not allowed."""
    from .invoice_status import InvoiceStatusError, assert_deletable

    assert_deletable(getattr(invoice, "status", None) or "")
    if not await is_latest_in_series(db, invoice):
        raise InvoiceStatusError(DELETE_BLOCKED_NOT_TIP)


async def invoice_has_live_children(db: AsyncSession, invoice_id: str) -> bool:
    child_id = await db.scalar(
        select(Invoice.id).where(
            Invoice.parent_invoice_id == invoice_id,
            Invoice.deleted_at.is_(None),
        )
    )
    return child_id is not None


def _status_allows_gst_series_change(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in GST_SERIES_CHANGE_STATUSES


async def evaluate_gst_series_change(
    db: AsyncSession,
    invoice: Invoice,
    *,
    target_gst_enabled: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Returns (allowed, error_tip).

    Allowed for unpaid (draft/sent/overdue/proof_pending), latest in current series,
    no live children, when the target GST flag would move to the other series.
    """
    if not _status_allows_gst_series_change(getattr(invoice, "status", None)):
        return False, GST_TOGGLE_BLOCKED_TIP
    parsed = parse_series_key(getattr(invoice, "invoice_number", None))
    if not parsed:
        return False, GST_TOGGLE_BLOCKED_TIP
    if bool(parsed["gst_enabled"]) == bool(target_gst_enabled):
        # Already on the correct series — no wipe needed
        return True, None
    if await invoice_has_live_children(db, invoice.id):
        return False, GST_TOGGLE_BLOCKED_TIP
    if not await is_latest_in_series(db, invoice):
        return False, GST_TOGGLE_BLOCKED_TIP
    return True, None


async def can_change_gst_series(db: AsyncSession, invoice: Invoice) -> bool:
    """UI hint: unpaid + latest + no children (ignores whether flag already matches)."""
    if not _status_allows_gst_series_change(getattr(invoice, "status", None)):
        return False
    if not parse_series_key(getattr(invoice, "invoice_number", None)):
        return False
    if await invoice_has_live_children(db, invoice.id):
        return False
    return await is_latest_in_series(db, invoice)


async def assign_unique_invoice_number(
    db: AsyncSession,
    invoice: Invoice,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
    settings_row: Optional[Settings] = None,
    gst_enabled: Optional[bool] = None,
) -> str:
    """Allocate a number, bind it, flush; retry on UniqueViolation (concurrent creates)."""
    db.add(invoice)
    gst_on = resolve_gst_for_numbering(
        invoice, gst_enabled=gst_enabled, settings_row=settings_row
    )
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_UNIQUE_RETRIES + 1):
        number = await allocate_invoice_number(
            db,
            invoice_type=invoice_type,
            issue_date=issue_date,
            settings_row=settings_row,
            gst_enabled=gst_on,
        )
        invoice.invoice_number = number
        try:
            async with db.begin_nested():
                await db.flush()
            return number
        except IntegrityError as exc:
            if not is_invoice_number_conflict(exc):
                raise
            last_exc = exc
            logger.warning(
                "invoice_number unique conflict on %s (attempt %s/%s)",
                number,
                attempt,
                MAX_UNIQUE_RETRIES,
            )
    raise last_exc or RuntimeError("Failed to allocate a unique invoice number")
