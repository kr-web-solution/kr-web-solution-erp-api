"""Invoice number allocation: KRWS-DEV-2026-001 style series."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..models import Invoice, Settings

logger = logging.getLogger("kr.invoice_numbers")

MAX_UNIQUE_RETRIES = 8

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
    "mixed": "MIX",
}

INVOICE_SERVICE_LABELS: Dict[str, str] = {
    "DEV": "Development",
    "MNT": "Maintenance",
    "HST": "Hosting",
    "SVC": "Services",
    "LIC": "Licensing",
    "CRN": "Credit note",
    "MIX": "Mixed",
}

DEFAULT_COMPANY_CODE = "KRWS"


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


def format_invoice_number(company: str, service_code: str, year: int, seq: int) -> str:
    return f"{company}-{service_code}-{year}-{seq:03d}"


def default_prefix_scheme(company: str = DEFAULT_COMPANY_CODE) -> list[dict[str, str]]:
    return [
        {
            "service": label,
            "code": code,
            "prefix": f"{company}-{code}",
            "example": f"{company}-{code}-{date.today().year}-001",
        }
        for code, label in INVOICE_SERVICE_LABELS.items()
    ]


async def allocate_invoice_number(
    db: AsyncSession,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
    settings_row: Optional[Settings] = None,
) -> str:
    """
    Atomically allocate the next number for a service/year series.

    Format: {COMPANY}-{CODE}-{YYYY}-{NNN}  e.g. KRWS-DEV-2026-001
    Caller should hold / share a FOR UPDATE lock on settings when batching.
    """
    if settings_row is None:
        st_stmt = select(Settings).where(Settings.id == "default").with_for_update()
        settings_row = (await db.execute(st_stmt)).scalar_one_or_none()
    st = settings_row
    if not st:
        raise ValueError("Settings row missing")

    company = company_code_from_settings(st)
    code = service_code_for(invoice_type)
    year = (issue_date or date.today()).year
    seq_key = f"{code}-{year}"

    sequences: Dict[str, Any] = dict(st.invoice_sequences or {})
    next_num = int(sequences.get(seq_key) or 1)
    if next_num < 1:
        next_num = 1

    while True:
        candidate = format_invoice_number(company, code, year, next_num)
        exists = await db.scalar(
            select(Invoice.id).where(Invoice.invoice_number == candidate)
        )
        if not exists:
            break
        next_num += 1

    sequences[seq_key] = next_num + 1
    st.invoice_sequences = sequences
    flag_modified(st, "invoice_sequences")
    # Keep legacy counter in sync for older tooling / displays
    st.invoice_next_number = max(int(st.invoice_next_number or 1), next_num + 1)
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


async def assign_unique_invoice_number(
    db: AsyncSession,
    invoice: Invoice,
    *,
    invoice_type: str = "custom_dev",
    issue_date: Optional[date] = None,
    settings_row: Optional[Settings] = None,
) -> str:
    """Allocate a number, bind it, flush; retry on UniqueViolation (concurrent creates)."""
    db.add(invoice)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_UNIQUE_RETRIES + 1):
        number = await allocate_invoice_number(
            db,
            invoice_type=invoice_type,
            issue_date=issue_date,
            settings_row=settings_row,
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
