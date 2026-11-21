"""Multi-GSTIN registration selection by issue date and place of supply."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .tax_payroll import normalize_state_code


async def load_gst_registrations(db: AsyncSession) -> List[Any]:
    from ..models import GstRegistration

    rows = (
        await db.execute(
            select(GstRegistration)
            .where(GstRegistration.deleted_at.is_(None))
            .order_by(GstRegistration.effective_from.desc())
        )
    ).scalars().all()
    return list(rows)


async def resolve_settings_for_invoice(
    db: AsyncSession,
    settings: Any,
    *,
    client: Any = None,
    issue_date: Optional[date] = None,
) -> tuple[Any, Optional[Any]]:
    """Return (effective_settings, selected_registration)."""
    regs = await load_gst_registrations(db)
    customer_code = None
    if client is not None:
        customer_code = normalize_state_code(getattr(client, "state_code", None))
        if not customer_code:
            customer_code = normalize_state_code(getattr(client, "gstin", None))
    chosen = select_gst_registration(
        regs,
        issue_date=issue_date,
        customer_state_code=customer_code,
    )
    return settings_overlay_from_registration(settings, chosen), chosen


def registration_effective_on(reg: Any, on: date) -> bool:
    if not getattr(reg, "is_active", True):
        return False
    if getattr(reg, "deleted_at", None) is not None:
        return False
    start = getattr(reg, "effective_from", None)
    end = getattr(reg, "effective_to", None)
    if start and on < start:
        return False
    if end and on > end:
        return False
    return True


def select_gst_registration(
    registrations: Sequence[Any],
    *,
    issue_date: Optional[date] = None,
    customer_state_code: Optional[str] = None,
) -> Optional[Any]:
    """
    Pick the supplier GST registration for a new invoice.

    1. Active + effective on issue_date
    2. Prefer matching customer state_code (for correct POS / registration)
    3. Else prefer is_primary
    4. Else newest effective_from
    """
    on = issue_date or date.today()
    active = [r for r in registrations if registration_effective_on(r, on)]
    if not active:
        return None

    customer = normalize_state_code(customer_state_code)
    if customer:
        matched = [
            r
            for r in active
            if normalize_state_code(getattr(r, "state_code", None)) == customer
        ]
        if matched:
            active = matched

    primaries = [r for r in active if getattr(r, "is_primary", False)]
    pool = primaries or active
    pool = sorted(
        pool,
        key=lambda r: getattr(r, "effective_from", date.min) or date.min,
        reverse=True,
    )
    return pool[0] if pool else None


_SETTINGS_PASSTHROUGH = (
    "company_phone",
    "company_website",
    "company_tagline",
    "company_logo_url",
    "company_logo_content_hash",
    "company_pan",
    "smtp_from_email",
    "gst_enabled",
    "gst_unregistered",
    "default_tax_rate",
    "default_sac_code",
    "payment_terms",
    "invoice_terms_by_type",
    "bank_beneficiary_name",
    "bank_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_upi_id",
    "bank_paypal_id",
    "bank_upi_details",
    "bank_upi_qr_url",
    "bank_wire_instructions",
    "invoice_prefix",
    "invoice_sequences",
    "invoice_next_number",
)


def settings_overlay_from_registration(
    settings: Any,
    registration: Optional[Any],
) -> Any:
    """
    Return a settings-like object with GSTIN/state/address from the selected
    registration overlaid on the live settings row.
    """
    if registration is None:
        return settings
    base = settings or SimpleNamespace()
    payload = {k: getattr(base, k, None) for k in _SETTINGS_PASSTHROUGH}
    payload.update(
        {
            "company_name": getattr(registration, "legal_name", None)
            or getattr(base, "company_name", None),
            "company_address": getattr(registration, "address", None)
            or getattr(base, "company_address", None),
            "company_gstin": getattr(registration, "gstin", None)
            or getattr(base, "company_gstin", None),
            "company_state": getattr(registration, "state", None)
            or getattr(base, "company_state", None),
            "company_state_code": getattr(registration, "state_code", None)
            or getattr(base, "company_state_code", None),
            "gst_enabled": getattr(base, "gst_enabled", False),
            "gst_unregistered": getattr(base, "gst_unregistered", False),
        }
    )
    return SimpleNamespace(**payload)


def registration_to_dict(reg: Any) -> Dict[str, Any]:
    return {
        "id": getattr(reg, "id", None),
        "gstin": getattr(reg, "gstin", None),
        "legal_name": getattr(reg, "legal_name", None),
        "address": getattr(reg, "address", None),
        "state": getattr(reg, "state", None),
        "state_code": getattr(reg, "state_code", None),
        "effective_from": (
            getattr(reg, "effective_from").isoformat()
            if getattr(reg, "effective_from", None)
            else None
        ),
        "effective_to": (
            getattr(reg, "effective_to").isoformat()
            if getattr(reg, "effective_to", None)
            else None
        ),
        "is_primary": bool(getattr(reg, "is_primary", False)),
        "is_active": bool(getattr(reg, "is_active", True)),
    }


def list_active_dicts(registrations: Sequence[Any]) -> List[Dict[str, Any]]:
    return [registration_to_dict(r) for r in registrations if getattr(r, "deleted_at", None) is None]
