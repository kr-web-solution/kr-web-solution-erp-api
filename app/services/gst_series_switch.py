"""Hard-delete + recreate when switching an invoice between GST / non-GST series."""

from __future__ import annotations

import logging
import shutil
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AuditLog,
    Engagement,
    Invoice,
    InvoiceAuditLog,
    InvoiceItem,
    LateFeeAction,
    Project,
    Settings,
)
from .invoice_numbers import (
    GST_SERIES_CHANGE_STATUSES,
    GST_TOGGLE_BLOCKED_TIP,
    assign_unique_invoice_number,
    evaluate_gst_series_change,
    reclaim_sequence_if_tip,
)
from .invoice_payment_proof import uploads_root
from .invoice_typing import normalize_line_kind, resolve_invoice_typing
from .tax_payroll import apply_invoice_tax_snapshot

logger = logging.getLogger("kr.gst_series_switch")


def _purge_payment_proof_files(invoice_id: str, object_key: Optional[str] = None) -> None:
    """Remove local proof folder and best-effort remote object."""
    try:
        local_dir = uploads_root() / invoice_id
        if local_dir.exists() and local_dir.is_dir():
            shutil.rmtree(local_dir, ignore_errors=True)
    except Exception:
        logger.exception("failed to remove local payment proof dir for %s", invoice_id)
    if object_key:
        try:
            from .object_storage import delete_object, storage_enabled

            if storage_enabled():
                delete_object(object_key)
        except Exception:
            logger.exception("failed to remove remote payment proof %s", object_key)


async def hard_delete_invoice_completely(db: AsyncSession, invoice: Invoice) -> str:
    """
    Permanently remove an invoice and related rows/files. Not soft-delete.
    Returns the old invoice_number (for reclaim).
    """
    old_id = invoice.id
    old_number = invoice.invoice_number
    proof_key = getattr(invoice, "payment_proof_object_key", None)

    # Clear outbound FKs that SET NULL would leave orphaned conceptually
    await db.execute(
        update(Project)
        .where(Project.advance_invoice_id == old_id)
        .values(advance_invoice_id=None)
    )
    await db.execute(
        update(Project)
        .where(Project.balance_invoice_id == old_id)
        .values(balance_invoice_id=None)
    )
    await db.execute(
        update(Engagement)
        .where(Engagement.advance_invoice_id == old_id)
        .values(advance_invoice_id=None)
    )
    await db.execute(
        update(Engagement)
        .where(Engagement.final_invoice_id == old_id)
        .values(final_invoice_id=None)
    )
    await db.execute(
        update(LateFeeAction)
        .where(LateFeeAction.late_fee_invoice_id == old_id)
        .values(late_fee_invoice_id=None)
    )
    await db.execute(
        update(Invoice)
        .where(Invoice.parent_invoice_id == old_id)
        .values(parent_invoice_id=None)
    )

    await db.execute(delete(InvoiceAuditLog).where(InvoiceAuditLog.invoice_id == old_id))
    await db.execute(
        delete(AuditLog).where(
            AuditLog.entity_type == "invoices",
            AuditLog.entity_id == old_id,
        )
    )

    # Cascade should remove items / payments / late_fee_actions(invoice_id)
    await db.delete(invoice)
    await db.flush()

    _purge_payment_proof_files(old_id, proof_key)
    logger.info("hard-deleted invoice %s (%s)", old_id, old_number)
    return old_number


async def switch_invoice_gst_series(
    db: AsyncSession,
    invoice: Invoice,
    *,
    target_gst_enabled: bool,
    settings_row: Settings,
    update_fields: Optional[Dict[str, Any]] = None,
    new_items: Optional[List[Dict[str, Any]]] = None,
    deleted_by: Any = None,
) -> Invoice:
    """
    Gate → archive to deleted_invoices → hard-delete old → create on other GST series.

    Raises ValueError with GST_TOGGLE_BLOCKED_TIP when not allowed.
    """
    # Lock settings early so concurrent creates cannot race past "latest"
    st_locked = (
        await db.execute(
            select(Settings).where(Settings.id == "default").with_for_update()
        )
    ).scalar_one_or_none()
    st = st_locked or settings_row
    if not st:
        raise ValueError("Settings row missing")

    allowed, tip = await evaluate_gst_series_change(
        db, invoice, target_gst_enabled=bool(target_gst_enabled)
    )
    if not allowed:
        raise ValueError(tip or GST_TOGGLE_BLOCKED_TIP)

    # If already on the correct series, caller should not invoke wipe — but be safe
    from .invoice_numbers import parse_series_key

    parsed = parse_series_key(invoice.invoice_number)
    if parsed and bool(parsed["gst_enabled"]) == bool(target_gst_enabled):
        return invoice

    fields = dict(update_fields or {})
    # Snapshot recreate payload from current row + patch
    client_id = invoice.client_id
    project_id = invoice.project_id
    invoice_type = fields.get("invoice_type") or invoice.invoice_type or "custom_dev"
    issue_date = fields.get("issue_date") or invoice.issue_date
    due_date = fields.get("due_date") or invoice.due_date
    notes = fields["notes"] if "notes" in fields else invoice.notes
    upi_id_override = (
        fields["upi_id_override"] if "upi_id_override" in fields else invoice.upi_id_override
    )
    upi_qr_url = fields["upi_qr_url"] if "upi_qr_url" in fields else invoice.upi_qr_url
    period_months = fields.get("period_months", invoice.period_months or 1)
    covers_from = fields["covers_from"] if "covers_from" in fields else invoice.covers_from
    covers_to = fields["covers_to"] if "covers_to" in fields else invoice.covers_to
    monthly_rate_paise = (
        fields["monthly_rate_paise"]
        if "monthly_rate_paise" in fields
        else invoice.monthly_rate_paise
    )
    discount_paise = int(
        fields["discount_paise"]
        if "discount_paise" in fields
        else (getattr(invoice, "discount_paise", 0) or 0)
    )
    po_number = fields["po_number"] if "po_number" in fields else getattr(invoice, "po_number", None)
    parent_invoice_id = invoice.parent_invoice_id
    recurring_id = invoice.recurring_id
    period_key = invoice.period_key
    ledger = invoice.ledger
    prev_status = (invoice.status or "draft").strip().lower()
    if prev_status not in GST_SERIES_CHANGE_STATUSES:
        prev_status = "draft"

    # Line items: prefer patch payload, else copy existing
    if new_items is not None:
        item_specs = list(new_items)
    else:
        item_specs = [
            {
                "description": it.description,
                "quantity": float(it.quantity or 1),
                "unit_rate_paise": int(it.unit_rate_paise or 0),
                "line_kind": getattr(it, "line_kind", None),
                "hsn_sac": None if not target_gst_enabled else getattr(it, "hsn_sac", None),
                "unit": getattr(it, "unit", None),
            }
            for it in (invoice.items or [])
        ]

    header_type, header_ledger, _ = resolve_invoice_typing(
        invoice_type=invoice_type,
        items=item_specs,
    )
    invoice_type = header_type
    ledger = header_ledger or ledger

    subtotal = 0
    for spec in item_specs:
        qty = float(spec.get("quantity", 1) or 1)
        rate = int(spec.get("unit_rate_paise", 0) or 0)
        subtotal += int(round(qty * rate))

    tax_rate_in = float(
        fields["tax_rate_percent"]
        if "tax_rate_percent" in fields
        else (invoice.tax_rate_percent or 0)
    )
    if not target_gst_enabled:
        tax_rate_in = 0.0

    client = invoice.client
    if target_gst_enabled:
        tax_snap = apply_invoice_tax_snapshot(
            settings=st,
            client=client,
            subtotal_paise=subtotal,
            tax_rate_percent=tax_rate_in,
            discount_paise=discount_paise,
        )
        tax_rate = float(tax_snap["tax_rate_percent"])
        tax_paise = int(tax_snap["tax_paise"])
        total_paise = int(tax_snap["total_paise"]) + int(tax_snap.get("round_off_paise", 0) or 0)
        place_of_supply = tax_snap.get("place_of_supply")
        supply_type = tax_snap.get("supply_type")
        cgst_paise = int(tax_snap.get("cgst_paise") or 0)
        sgst_paise = int(tax_snap.get("sgst_paise") or 0)
        igst_paise = int(tax_snap.get("igst_paise") or 0)
    else:
        tax_rate = 0.0
        tax_paise = 0
        total_paise = max(0, subtotal - discount_paise)
        place_of_supply = None
        supply_type = None
        cgst_paise = sgst_paise = igst_paise = 0

    from .deleted_invoice_archive import archive_deleted_invoice

    # Founder reference: keep old number + details, then permanently remove live row.
    archive_deleted_invoice(
        db,
        invoice,
        deleted_by=deleted_by,
        deleted_reason="gst_series_switch",
    )
    await db.flush()

    old_number = await hard_delete_invoice_completely(db, invoice)
    reclaim_sequence_if_tip(st, old_number)

    new_inv = Invoice(
        invoice_number="",
        invoice_type=invoice_type,
        ledger=ledger,
        client_id=client_id,
        project_id=project_id,
        status=prev_status,
        issue_date=issue_date if isinstance(issue_date, date) else date.today(),
        due_date=due_date if isinstance(due_date, date) else date.today(),
        subtotal_paise=subtotal,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        gst_enabled=bool(target_gst_enabled),
        place_of_supply=place_of_supply,
        supply_type=supply_type,
        cgst_paise=cgst_paise,
        sgst_paise=sgst_paise,
        igst_paise=igst_paise,
        discount_paise=discount_paise,
        round_off_paise=0,
        amount_paid_paise=0,
        po_number=po_number,
        notes=notes,
        upi_id_override=upi_id_override,
        upi_qr_url=upi_qr_url,
        period_months=period_months or 1,
        covers_from=covers_from,
        covers_to=covers_to,
        monthly_rate_paise=monthly_rate_paise,
        parent_invoice_id=parent_invoice_id,
        recurring_id=recurring_id,
        period_key=period_key,
        email_status="pending",
    )
    await assign_unique_invoice_number(
        db,
        new_inv,
        invoice_type=invoice_type,
        issue_date=new_inv.issue_date,
        settings_row=st,
        gst_enabled=bool(target_gst_enabled),
    )

    default_sac = (getattr(st, "default_sac_code", None) or "").strip() or None
    for spec in item_specs:
        qty = float(spec.get("quantity", 1) or 1)
        rate = int(spec.get("unit_rate_paise", 0) or 0)
        amt = int(round(qty * rate))
        db.add(
            InvoiceItem(
                invoice_id=new_inv.id,
                description=str(spec.get("description") or ""),
                quantity=qty,
                unit_rate_paise=rate,
                amount_paise=amt,
                line_kind=normalize_line_kind(
                    spec.get("line_kind"), invoice_type if invoice_type != "mixed" else "custom_dev"
                ),
                hsn_sac=(
                    None
                    if not target_gst_enabled
                    else (spec.get("hsn_sac") or default_sac)
                ),
                unit=(spec.get("unit") or None),
            )
        )

    await db.flush()
    # New unpaid invoice stays live (no freeze) until paid.

    logger.info(
        "GST series switch: %s → %s (gst=%s, status=%s)",
        old_number,
        new_inv.invoice_number,
        target_gst_enabled,
        prev_status,
    )
    return new_inv


async def reload_invoice_detail(db: AsyncSession, invoice_id: str) -> Optional[Invoice]:
    return (
        await db.execute(
            select(Invoice)
            .options(
                selectinload(Invoice.client),
                selectinload(Invoice.project),
                selectinload(Invoice.items),
                selectinload(Invoice.parent_invoice),
            )
            .where(Invoice.id == invoice_id)
        )
    ).scalars().first()
