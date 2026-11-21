"""Partial payment helpers — verified sums drive amount_paid_paise / outstanding."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import BusinessRuleError, ConflictException
from ..models import Invoice, InvoicePayment
from ..core.database import not_deleted


def payment_to_dict(p: InvoicePayment) -> dict:
    return {
        "id": p.id,
        "amount_paise": int(p.amount_paise or 0),
        "method": p.method,
        "reference": p.reference,
        "paid_at": p.paid_at,
        "note": p.note,
        "status": getattr(p, "status", None) or "verified",
        "rejection_reason": getattr(p, "rejection_reason", None),
        "recorded_by": p.recorded_by,
        "verified_by": getattr(p, "verified_by", None),
        "verified_at": getattr(p, "verified_at", None),
        "rejected_by": getattr(p, "rejected_by", None),
        "rejected_at": getattr(p, "rejected_at", None),
        "has_proof": bool(getattr(p, "proof_object_key", None)),
    }


def sum_verified_paise(payments: Iterable[InvoicePayment]) -> int:
    total = 0
    for p in payments or []:
        if getattr(p, "deleted_at", None):
            continue
        if (getattr(p, "status", None) or "verified") != "verified":
            continue
        total += max(0, int(p.amount_paise or 0))
    return total


async def recalc_amount_paid(db: AsyncSession, inv: Invoice) -> int:
    result = await db.execute(
        select(func.coalesce(func.sum(InvoicePayment.amount_paise), 0)).where(
            InvoicePayment.invoice_id == inv.id,
            InvoicePayment.status == "verified",
            not_deleted(InvoicePayment),
        )
    )
    paid = int(result.scalar() or 0)
    inv.amount_paid_paise = paid
    return paid


async def assert_unique_reference(
    db: AsyncSession,
    *,
    invoice_id: str,
    reference: Optional[str],
    exclude_payment_id: Optional[str] = None,
) -> None:
    ref = (reference or "").strip()
    if not ref:
        return
    stmt = select(InvoicePayment).where(
        InvoicePayment.invoice_id == invoice_id,
        func.lower(InvoicePayment.reference) == ref.lower(),
        InvoicePayment.status != "rejected",
        not_deleted(InvoicePayment),
    )
    if exclude_payment_id:
        stmt = stmt.where(InvoicePayment.id != exclude_payment_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise ConflictException(
            f"A payment with reference '{ref}' already exists on this invoice"
        )


def maybe_mark_invoice_paid(inv: Invoice, *, actor_id: Optional[str] = None) -> bool:
    """If verified paid >= total, set paid. Returns True if newly marked paid."""
    if inv.status in ("cancelled", "void", "paid"):
        return False
    total = int(inv.total_paise or 0)
    paid = int(getattr(inv, "amount_paid_paise", 0) or 0)
    if total > 0 and paid >= total:
        from .invoice_status import apply_status

        apply_status(inv, "paid")
        inv.paid_at = datetime.now(timezone.utc)
        if actor_id:
            inv.payment_verified_at = inv.paid_at
            inv.payment_verified_by = actor_id
        return True
    return False


def assert_payment_mutable(inv: Invoice) -> None:
    if inv.status in ("cancelled", "void"):
        raise BusinessRuleError("Cannot record payments on a cancelled invoice")
    if inv.status == "draft":
        raise BusinessRuleError("Send the invoice before recording payments")
