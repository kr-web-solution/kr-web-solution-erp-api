"""Immutable document snapshot for issued invoices."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional, Set

from .invoice_terms import resolve_invoice_terms
from .tax_payroll import gst_is_enabled

# Freeze only when payment is complete (or cancel/void). Unpaid stays live.
FROZEN_SNAPSHOT_STATUSES: Set[str] = frozenset({"paid", "cancelled", "void"})
# Back-compat alias used by ensure/backfill helpers.
ISSUED_SNAPSHOT_STATUSES: Set[str] = FROZEN_SNAPSHOT_STATUSES


def build_document_snapshot(
    *,
    invoice: Any,
    client: Any,
    settings: Any,
    project: Any = None,
    parent_invoice: Any = None,
    company_profile_audit_id: Optional[str] = None,
    gst_registration: Any = None,
) -> Dict[str, Any]:
    """Capture everything needed to re-render the issued invoice later."""
    from .gst_registrations import registration_to_dict, settings_overlay_from_registration

    effective_settings = settings_overlay_from_registration(settings, gst_registration)
    terms = resolve_invoice_terms(
        invoice_type=getattr(invoice, "invoice_type", None),
        invoice_notes=getattr(invoice, "notes", None),
        settings=effective_settings,
        status=getattr(invoice, "status", None),
    )

    company_gstin = None
    if effective_settings and gst_is_enabled(effective_settings):
        company_gstin = getattr(effective_settings, "company_gstin", None) or None
        if not company_gstin and getattr(effective_settings, "gst_unregistered", False):
            company_gstin = "N/A"

    snap: Dict[str, Any] = {
        "version": 1,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "company": {
            "name": getattr(effective_settings, "company_name", None) if effective_settings else None,
            "address": getattr(effective_settings, "company_address", None) if effective_settings else None,
            "gstin": company_gstin,
            "pan": getattr(effective_settings, "company_pan", None) if effective_settings else None,
            "state": getattr(effective_settings, "company_state", None) if effective_settings else None,
            "state_code": getattr(effective_settings, "company_state_code", None) if effective_settings else None,
            "email": getattr(effective_settings, "smtp_from_email", None) if effective_settings else None,
            "phone": getattr(effective_settings, "company_phone", None) if effective_settings else None,
            "website": getattr(effective_settings, "company_website", None) if effective_settings else None,
            "tagline": getattr(effective_settings, "company_tagline", None) if effective_settings else None,
            "logo_url": getattr(effective_settings, "company_logo_url", None) if effective_settings else None,
            "logo_content_hash": getattr(effective_settings, "company_logo_content_hash", None)
            if effective_settings
            else None,
            "gst_enabled": (
                bool(getattr(invoice, "gst_enabled"))
                if getattr(invoice, "gst_enabled", None) is not None
                else (bool(gst_is_enabled(effective_settings)) if effective_settings else False)
            ),
            "gst_unregistered": bool(getattr(effective_settings, "gst_unregistered", False))
            if effective_settings
            and (
                getattr(invoice, "gst_enabled", None) is not False
            )
            else False,
        },
        "client": {
            "name": getattr(client, "name", None) if client else None,
            "email": getattr(client, "email", None) if client else None,
            "phone": getattr(client, "phone", None) if client else None,
            "address": getattr(client, "address", None) if client else None,
            "city": getattr(client, "city", None) if client else None,
            "state": getattr(client, "state", None) if client else None,
            "pincode": getattr(client, "pincode", None) if client else None,
            "gstin": getattr(client, "gstin", None) if client else None,
            "pan": getattr(client, "pan", None) if client else None,
            "state_code": getattr(client, "state_code", None) if client else None,
        },
        "project": {
            "name": getattr(project, "name", None) if project else None,
            "ref": (
                str(project.id).replace("prj_", "PRJ-").upper()
                if project and getattr(project, "id", None)
                else None
            ),
        },
        "payment": build_payment_snapshot_block(effective_settings, invoice)
        if effective_settings
        else {
            "beneficiary": None,
            "bank_name": None,
            "account_number": None,
            "ifsc": None,
            "upi_id": None,
            "paypal_id": None,
            "upi_qr_url": None,
            "wire_instructions": [],
            "bank_upi_details": None,
        },
        "terms": list(terms),
        "parent_invoice_number": getattr(parent_invoice, "invoice_number", None)
        if parent_invoice
        else None,
    }
    if company_profile_audit_id:
        snap["company_profile_audit_id"] = company_profile_audit_id
    if gst_registration is not None:
        snap["gst_registration"] = registration_to_dict(gst_registration)
    return snap


def freeze_document_snapshot(
    invoice: Any,
    *,
    client: Any,
    settings: Any,
    project: Any = None,
    parent_invoice: Any = None,
    force: bool = False,
    company_profile_audit_id: Optional[str] = None,
    gst_registration: Any = None,
) -> Dict[str, Any]:
    """Persist snapshot on invoice. Idempotent unless force=True."""
    existing = getattr(invoice, "document_snapshot", None)
    if existing and not force:
        return existing
    snap = build_document_snapshot(
        invoice=invoice,
        client=client,
        settings=settings,
        project=project,
        parent_invoice=parent_invoice,
        company_profile_audit_id=company_profile_audit_id,
        gst_registration=gst_registration,
    )
    invoice.document_snapshot = snap
    invoice.snapshot_frozen_at = datetime.now(timezone.utc)
    try:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(invoice, "document_snapshot")
    except Exception:
        pass
    return snap


def get_snapshot(invoice: Any) -> Optional[Dict[str, Any]]:
    raw = getattr(invoice, "document_snapshot", None)
    return raw if isinstance(raw, dict) else None


def needs_issued_snapshot(invoice: Any) -> bool:
    """True when paid/cancelled/void but missing a freeze snapshot."""
    status = (getattr(invoice, "status", None) or "").lower()
    if status not in FROZEN_SNAPSHOT_STATUSES:
        return False
    return get_snapshot(invoice) is None


def ensure_issued_snapshot(
    invoice: Any,
    *,
    client: Any,
    settings: Any,
    project: Any = None,
    parent_invoice: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Freeze-on-read for paid/cancelled/void invoices missing a snapshot.

    Unpaid invoices never freeze here — they always use live details.
    """
    if not needs_issued_snapshot(invoice):
        # Drop stale unpaid freezes so live company/client always win.
        status = (getattr(invoice, "status", None) or "").lower()
        if status not in FROZEN_SNAPSHOT_STATUSES and get_snapshot(invoice) is not None:
            invoice.document_snapshot = None
            invoice.snapshot_frozen_at = None
        return get_snapshot(invoice)
    return freeze_document_snapshot(
        invoice,
        client=client,
        settings=settings,
        project=project,
        parent_invoice=parent_invoice,
    )


def snapshot_payment_has_data(payment: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(payment, dict):
        return False
    wire = [w for w in (payment.get("wire_instructions") or []) if str(w).strip()]
    return bool(
        payment.get("beneficiary")
        or payment.get("bank_name")
        or payment.get("account_number")
        or payment.get("ifsc")
        or payment.get("upi_id")
        or payment.get("paypal_id")
        or payment.get("bank_upi_details")
        or wire
    )


def document_details_are_locked(invoice: Any) -> bool:
    """
    True only after payment (or cancel/void).

    Unpaid invoices (draft/sent/overdue/proof_pending) always use live company,
    client, project, and payment details so founders can edit until paid.
    """
    status = (getattr(invoice, "status", None) or "").lower()
    return status in ("paid", "cancelled", "void")


def payment_details_are_locked(invoice: Any) -> bool:
    """Alias — payment freezes with the rest of the document on paid/cancel/void."""
    return document_details_are_locked(invoice)


def build_payment_snapshot_block(
    settings: Any,
    invoice: Any = None,
) -> Dict[str, Any]:
    """Current bank / UPI / PayPal block from live company settings."""
    from .payment_instructions import resolve_payment_instructions
    from .tax_payroll import resolve_payment_upi

    pay = (
        resolve_payment_upi(settings, invoice)
        if settings
        else {"upi_id": None, "upi_qr_url": None}
    )
    instr = resolve_payment_instructions(settings, upi_id=pay.get("upi_id"))
    return {
        "beneficiary": instr.get("beneficiary"),
        "bank_name": instr.get("bank_name"),
        "account_number": instr.get("account_number"),
        "ifsc": instr.get("ifsc"),
        "upi_id": instr.get("upi_id"),
        "paypal_id": instr.get("paypal_id"),
        "upi_qr_url": pay.get("upi_qr_url"),
        "wire_instructions": list(instr.get("wire_instructions") or []),
        "bank_upi_details": getattr(settings, "bank_upi_details", None)
        if settings
        else None,
    }


def refresh_snapshot_payment(invoice: Any, settings: Any) -> Optional[Dict[str, Any]]:
    """
    Lock current live payment details into an existing snapshot (e.g. on mark-paid).
    Does not rewrite company / client identity.
    """
    snap = get_snapshot(invoice)
    if not snap or settings is None:
        return snap
    snap = dict(snap)
    snap["payment"] = build_payment_snapshot_block(settings, invoice)
    invoice.document_snapshot = snap
    try:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(invoice, "document_snapshot")
    except Exception:
        pass
    return snap


def company_branding_from_snapshot(
    snapshot: Optional[Dict[str, Any]],
    live: Any = None,
    *,
    prefer_live: bool = False,
) -> Dict[str, Optional[str]]:
    """
    Supplier branding for email bodies.

    prefer_live / unpaid: always use live settings.
    Paid/cancelled: use frozen company when snapshot exists.
    """
    if (
        not prefer_live
        and snapshot
        and isinstance(snapshot.get("company"), dict)
    ):
        company = snapshot["company"]
        return {
            "company_name": company.get("name") or "KR Web Solution",
            "company_address": company.get("address") or "",
            "company_gstin": company.get("gstin") or "",
            "company_phone": company.get("phone") or "",
            "company_email": company.get("email") or "",
        }
    return {
        "company_name": getattr(live, "company_name", None) or "KR Web Solution",
        "company_address": getattr(live, "company_address", None) or "",
        "company_gstin": getattr(live, "company_gstin", None) or "",
        "company_phone": getattr(live, "company_phone", None) or "",
        "company_email": getattr(live, "smtp_from_email", None) or "",
    }


def settings_view_from_snapshot(
    snapshot: Optional[Dict[str, Any]],
    live: Any = None,
    *,
    prefer_live_payment: bool = False,
) -> Any:
    """
    Settings-like object for PDF rendering.

    Unpaid (prefer_live_payment): full live company + bank/UPI/PayPal.
    Paid/cancelled: frozen company identity + frozen payment block.
    """
    if not snapshot:
        return live
    if prefer_live_payment and live is not None:
        return live

    company = snapshot.get("company") or {}
    payment = snapshot.get("payment") if isinstance(snapshot.get("payment"), dict) else {}
    base = live or SimpleNamespace()

    gstin_raw = company.get("gstin")
    company_gstin = None if gstin_raw == "N/A" else gstin_raw

    use_live_bank = not snapshot_payment_has_data(payment)
    if use_live_bank:
        bank_beneficiary = getattr(base, "bank_beneficiary_name", None)
        bank_name = getattr(base, "bank_name", None)
        bank_account = getattr(base, "bank_account_number", None)
        bank_ifsc = getattr(base, "bank_ifsc", None)
        bank_upi = getattr(base, "bank_upi_id", None)
        bank_paypal = getattr(base, "bank_paypal_id", None)
        bank_upi_details = getattr(base, "bank_upi_details", None)
        bank_wire = getattr(base, "bank_wire_instructions", None)
        bank_qr = getattr(base, "bank_upi_qr_url", None)
    else:
        bank_beneficiary = payment.get("beneficiary")
        bank_name = payment.get("bank_name")
        bank_account = payment.get("account_number")
        bank_ifsc = payment.get("ifsc")
        bank_upi = payment.get("upi_id")
        bank_paypal = payment.get("paypal_id")
        bank_upi_details = payment.get("bank_upi_details")
        bank_wire = "\n".join(payment.get("wire_instructions") or []) or None
        bank_qr = payment.get("upi_qr_url")

    return SimpleNamespace(
        company_name=company.get("name"),
        company_address=company.get("address"),
        company_gstin=company_gstin,
        company_pan=company.get("pan"),
        company_state=company.get("state"),
        company_state_code=company.get("state_code"),
        company_phone=company.get("phone"),
        company_website=company.get("website"),
        company_tagline=company.get("tagline"),
        company_logo_url=company.get("logo_url"),
        company_logo_content_hash=company.get("logo_content_hash"),
        gst_enabled=bool(company.get("gst_enabled")),
        gst_unregistered=bool(company.get("gst_unregistered")),
        smtp_from_email=company.get("email"),
        bank_beneficiary_name=bank_beneficiary,
        bank_name=bank_name,
        bank_account_number=bank_account,
        bank_ifsc=bank_ifsc,
        bank_upi_id=bank_upi,
        bank_paypal_id=bank_paypal,
        bank_upi_qr_url=bank_qr,
        bank_upi_details=bank_upi_details,
        bank_wire_instructions=bank_wire,
        payment_terms=getattr(base, "payment_terms", None),
        invoice_terms_by_type={},
        default_sac_code=getattr(base, "default_sac_code", None),
    )


def client_view_from_snapshot(
    snapshot: Optional[Dict[str, Any]],
    live: Any = None,
    *,
    prefer_live: bool = False,
) -> Any:
    """Client bill-to: live while unpaid; frozen after payment."""
    if not snapshot or prefer_live:
        return live
    c = snapshot.get("client") or {}
    return SimpleNamespace(
        name=c.get("name"),
        email=c.get("email"),
        phone=c.get("phone"),
        address=c.get("address"),
        city=c.get("city"),
        state=c.get("state"),
        pincode=c.get("pincode"),
        gstin=c.get("gstin"),
        pan=c.get("pan"),
        state_code=c.get("state_code"),
        primary_contact_name=None,
        designation=None,
        contact_email=None,
        contact_phone=None,
    )
