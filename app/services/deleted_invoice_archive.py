"""Archive hard-deleted invoices into deleted_invoices (founder/admin DB reference)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import DeletedInvoice, Invoice, User

logger = logging.getLogger("kr.deleted_invoice_archive")


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def build_invoice_delete_payload(invoice: Invoice) -> Dict[str, Any]:
    """Serialize invoice + line items + payments for archival."""
    items: List[Dict[str, Any]] = []
    for it in list(getattr(invoice, "items", None) or []):
        items.append(
            {
                "id": it.id,
                "description": it.description,
                "quantity": float(it.quantity) if it.quantity is not None else None,
                "unit_rate_paise": it.unit_rate_paise,
                "amount_paise": it.amount_paise,
                "line_kind": it.line_kind,
                "hsn_sac": it.hsn_sac,
                "unit": it.unit,
            }
        )
    payments: List[Dict[str, Any]] = []
    for pay in list(getattr(invoice, "payments", None) or []):
        payments.append(
            {
                "id": pay.id,
                "amount_paise": pay.amount_paise,
                "method": pay.method,
                "reference": pay.reference,
                "paid_at": _iso(pay.paid_at),
                "note": pay.note,
                "recorded_by": pay.recorded_by,
            }
        )
    client = getattr(invoice, "client", None)
    project = getattr(invoice, "project", None)
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type,
        "ledger": invoice.ledger,
        "status": invoice.status,
        "client_id": invoice.client_id,
        "client_name": getattr(client, "name", None),
        "project_id": invoice.project_id,
        "project_name": getattr(project, "name", None),
        "recurring_id": invoice.recurring_id,
        "period_key": invoice.period_key,
        "parent_invoice_id": invoice.parent_invoice_id,
        "period_months": invoice.period_months,
        "covers_from": _iso(invoice.covers_from),
        "covers_to": _iso(invoice.covers_to),
        "monthly_rate_paise": invoice.monthly_rate_paise,
        "issue_date": _iso(invoice.issue_date),
        "due_date": _iso(invoice.due_date),
        "subtotal_paise": invoice.subtotal_paise,
        "tax_rate_percent": float(invoice.tax_rate_percent or 0),
        "tax_paise": invoice.tax_paise,
        "total_paise": invoice.total_paise,
        "gst_enabled": invoice.gst_enabled,
        "place_of_supply": invoice.place_of_supply,
        "supply_type": invoice.supply_type,
        "cgst_paise": invoice.cgst_paise,
        "sgst_paise": invoice.sgst_paise,
        "igst_paise": invoice.igst_paise,
        "discount_paise": invoice.discount_paise,
        "round_off_paise": invoice.round_off_paise,
        "amount_paid_paise": invoice.amount_paid_paise,
        "po_number": invoice.po_number,
        "notes": invoice.notes,
        "upi_id_override": invoice.upi_id_override,
        "document_snapshot": invoice.document_snapshot,
        "snapshot_frozen_at": _iso(invoice.snapshot_frozen_at),
        "paid_at": _iso(invoice.paid_at),
        "payment_proof_filename": invoice.payment_proof_filename,
        "payment_proof_uploaded_at": _iso(invoice.payment_proof_uploaded_at),
        "created_at": _iso(getattr(invoice, "created_at", None)),
        "updated_at": _iso(getattr(invoice, "updated_at", None)),
        "items": items,
        "payments": payments,
    }


def archive_deleted_invoice(
    db: AsyncSession,
    invoice: Invoice,
    *,
    deleted_by: Optional[User] = None,
    deleted_reason: str = "user_delete",
) -> DeletedInvoice:
    """
    Insert a deleted_invoices row from the live invoice (call before hard-delete).
    Does not commit.
    """
    client = getattr(invoice, "client", None)
    project = getattr(invoice, "project", None)
    row = DeletedInvoice(
        original_invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        ledger=invoice.ledger,
        status=invoice.status,
        client_id=invoice.client_id,
        client_name=getattr(client, "name", None),
        project_id=invoice.project_id,
        project_name=getattr(project, "name", None),
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        subtotal_paise=int(invoice.subtotal_paise or 0),
        tax_rate_percent=float(invoice.tax_rate_percent or 0),
        tax_paise=int(invoice.tax_paise or 0),
        total_paise=int(invoice.total_paise or 0),
        gst_enabled=invoice.gst_enabled,
        notes=invoice.notes,
        payload=build_invoice_delete_payload(invoice),
        deleted_by=getattr(deleted_by, "id", None) if deleted_by else None,
        deleted_by_email=getattr(deleted_by, "email", None) if deleted_by else None,
        deleted_reason=(deleted_reason or "user_delete")[:40],
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(row)
    logger.info(
        "archived deleted invoice %s (%s) reason=%s",
        invoice.id,
        invoice.invoice_number,
        deleted_reason,
    )
    return row


# Eager-load shape expected before archive + hard-delete
DELETED_INVOICE_LOAD_OPTIONS = (
    selectinload(Invoice.items),
    selectinload(Invoice.payments),
    selectinload(Invoice.late_fee_actions),
    selectinload(Invoice.client),
    selectinload(Invoice.project),
)
