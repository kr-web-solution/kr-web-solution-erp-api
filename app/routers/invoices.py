from datetime import date, datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, get_current_user, require_founder_ceo, is_privileged
from ..core.database import not_deleted
from ..models import (
    Invoice,
    InvoiceItem,
    InvoicePayment,
    Project,
    Settings,
    RecurringInvoice,
    User,
    LateFeeAction,
)
from ..services.audit import snapshot_entity, write_audit
from ..services.invoice_status import (
    apply_status,
    assert_can_cancel,
    assert_can_pay,
    assert_can_send,
    assert_can_upload_payment_proof,
    assert_can_verify_payment,
    assert_content_editable,
    assert_editable,
    assert_soft_editable,
)
from ..schemas import (
    InvoiceResponse,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceItemResponse,
    InvoicePaymentResponse,
    InvoicePaymentCreate,
    InvoicePaymentReject,
    RecurringInvoiceResponse,
    RecurringInvoiceCreate,
    RecurringInvoiceUpdate,
    LateFeeActionResponse,
    LateFeeDecisionRequest,
    CreditNoteCreate,
    DebitNoteCreate,
)
from ..services.invoice_payments import (
    assert_payment_mutable,
    assert_unique_reference,
    maybe_mark_invoice_paid,
    recalc_amount_paid,
)
from ..services.pdf import generate_invoice_pdf
from ..services.email import send_email_async
from ..services.amount_in_words import amount_in_words_inr
from ..services.invoice_terms import resolve_invoice_terms
from ..services.tax_payroll import (
    effective_tax_rate,
    compute_tax_paise,
    gst_is_enabled,
    resolve_payment_upi,
    apply_invoice_tax_snapshot,
)

router = APIRouter(prefix="/invoices", tags=["Invoices"])

async def _hold_maintenance_emails(db: AsyncSession, project_id: str, covers_to: date) -> None:
    """Push the next automatic maintenance email to the day after this coverage."""
    rec = (
        await db.execute(
            select(RecurringInvoice).where(
                RecurringInvoice.project_id == project_id,
                not_deleted(RecurringInvoice),
            )
        )
    ).scalars().first()
    if not rec:
        return
    nxt = covers_to + timedelta(days=1)
    current = rec.next_run_date
    # Year 2090+ is a placeholder hold, not a real billing date.
    if current is None or current.year >= 2090 or current < nxt:
        rec.next_run_date = nxt


async def _load_settings(db: AsyncSession) -> Optional[Settings]:
    return (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()


async def _gst_toggle_response_fields(
    db: AsyncSession,
    inv: Optional[Invoice],
    st: Optional[Settings],
) -> tuple[bool, Optional[str]]:
    """(can_change_gst, gst_toggle_preview_number) for InvoiceResponse."""
    if not inv:
        return False, None
    from ..services.invoice_numbers import (
        can_change_gst_series,
        number_is_non_gst_series,
        peek_lowest_free_invoice_number,
    )

    can_gst = await can_change_gst_series(db, inv)
    if not can_gst:
        return False, None
    want_gst = number_is_non_gst_series(inv.invoice_number)
    preview = await peek_lowest_free_invoice_number(
        db,
        invoice_type=inv.invoice_type or "custom_dev",
        issue_date=inv.issue_date,
        settings_row=st,
        gst_enabled=want_gst,
    )
    return True, preview


def _invoice_eager_options(*, include_parent: bool = False):
    opts = [
        selectinload(Invoice.client),
        selectinload(Invoice.project),
        selectinload(Invoice.items),
        selectinload(Invoice.payments),
    ]
    if include_parent:
        opts.append(selectinload(Invoice.parent_invoice))
    return opts


def _record_invoice_payment(
    *,
    inv: Invoice,
    amount_paise: int,
    method: Optional[str],
    reference: Optional[str],
    note: Optional[str],
    recorded_by: Optional[str],
    paid_at: Optional[datetime] = None,
) -> InvoicePayment:
    amt = max(0, int(amount_paise or 0))
    now = paid_at or datetime.now(timezone.utc)
    pay = InvoicePayment(
        invoice_id=inv.id,
        amount_paise=amt,
        method=(method or "bank_transfer"),
        reference=(reference or None),
        note=(note or None),
        recorded_by=recorded_by,
        paid_at=now,
        status="verified",
        verified_by=recorded_by,
        verified_at=now,
    )
    inv.amount_paid_paise = int(getattr(inv, "amount_paid_paise", 0) or 0) + amt
    return pay


def _loaded_parent_invoice(inv: Invoice) -> Optional[Invoice]:
    """Return parent only when already eager-loaded (safe under async)."""
    try:
        unloaded = sa_inspect(inv).unloaded
    except Exception:
        return None
    if "parent_invoice" in unloaded:
        return None
    return getattr(inv, "parent_invoice", None)


def format_invoice_response(
    inv: Invoice,
    settings: Optional[Settings] = None,
    *,
    can_change_gst: bool = False,
    gst_toggle_preview_number: Optional[str] = None,
    can_delete: bool = False,
) -> InvoiceResponse:
    # Query-time overdue calculation
    effective_status = inv.status
    if effective_status == "sent" and inv.due_date and inv.due_date < date.today():
        effective_status = "overdue"

    amount_paid = int(getattr(inv, "amount_paid_paise", 0) or 0)
    total = int(inv.total_paise or 0)
    if effective_status not in ("paid", "cancelled", "void") and 0 < amount_paid < total:
        # Surface partial payments without inventing a DB status code.
        pass
    elif effective_status == "paid" and amount_paid <= 0 and total > 0:
        amount_paid = total

    from ..services.invoice_snapshot import (
        document_details_are_locked,
        get_snapshot,
        payment_details_are_locked,
        snapshot_payment_has_data,
    )
    from ..services.payment_instructions import resolve_payment_instructions

    snap = get_snapshot(inv)
    doc_locked = document_details_are_locked(inv)
    pay = resolve_payment_upi(settings, inv) if settings else {"upi_id": None, "upi_qr_url": None}
    pay_instr = resolve_payment_instructions(settings, upi_id=pay.get("upi_id"))
    # Unpaid → live bank/UPI/PayPal. Paid/cancelled → frozen payment block.
    if (
        payment_details_are_locked(inv)
        and snap
        and isinstance(snap.get("payment"), dict)
    ):
        sp = snap["payment"]
        snap_wire = [w for w in (sp.get("wire_instructions") or []) if str(w).strip()]
        if snapshot_payment_has_data(sp):
            pay_instr = {
                "beneficiary": sp.get("beneficiary"),
                "bank_name": sp.get("bank_name"),
                "account_number": sp.get("account_number"),
                "ifsc": sp.get("ifsc"),
                "upi_id": sp.get("upi_id") or pay.get("upi_id"),
                "paypal_id": sp.get("paypal_id"),
                "wire_instructions": snap_wire,
                "has_any": True,
            }
            if sp.get("upi_qr_url"):
                pay["upi_qr_url"] = sp.get("upi_qr_url")
        elif sp.get("upi_qr_url") and not pay.get("upi_qr_url"):
            pay["upi_qr_url"] = sp.get("upi_qr_url")

    client = inv.client
    project = inv.project
    parent = _loaded_parent_invoice(inv)
    project_ref = None
    if project and getattr(project, "id", None):
        project_ref = str(project.id).replace("prj_", "PRJ-").upper()
    # Paid only: project ref from freeze. Unpaid: live project.
    if (
        doc_locked
        and snap
        and isinstance(snap.get("project"), dict)
        and snap["project"].get("ref")
    ):
        project_ref = snap["project"]["ref"]

    snap_client = (snap or {}).get("client") if isinstance(snap, dict) else None
    snap_company = (snap or {}).get("company") if isinstance(snap, dict) else None
    snap_terms = (snap or {}).get("terms") if isinstance(snap, dict) else None
    # Unpaid invoices always use live company / client (ignore old freeze).
    use_snap_company = doc_locked and isinstance(snap_company, dict)
    use_snap_client = doc_locked and isinstance(snap_client, dict)

    company_gstin = None
    if use_snap_company:
        company_gstin = snap_company.get("gstin")
    elif settings:
        company_gstin = getattr(settings, "company_gstin", None) or None
        if not company_gstin and getattr(settings, "gst_unregistered", False):
            company_gstin = "N/A"

    payments_out: List[InvoicePaymentResponse] = []
    try:
        unloaded = sa_inspect(inv).unloaded
    except Exception:
        unloaded = set()
    if "payments" not in unloaded:
        for p in inv.payments or []:
            if getattr(p, "deleted_at", None):
                continue
            payments_out.append(
                InvoicePaymentResponse(
                    id=p.id,
                    amount_paise=int(p.amount_paise or 0),
                    method=p.method,
                    reference=p.reference,
                    paid_at=p.paid_at,
                    note=p.note,
                    status=getattr(p, "status", None) or "verified",
                    rejection_reason=getattr(p, "rejection_reason", None),
                    recorded_by=p.recorded_by,
                    verified_by=getattr(p, "verified_by", None),
                    verified_at=getattr(p, "verified_at", None),
                    rejected_by=getattr(p, "rejected_by", None),
                    rejected_at=getattr(p, "rejected_at", None),
                    has_proof=bool(getattr(p, "proof_object_key", None)),
                )
            )

    if doc_locked and isinstance(snap_terms, list) and snap_terms:
        resolved_terms = [str(t) for t in snap_terms if str(t).strip()]
    else:
        resolved_terms = resolve_invoice_terms(
            invoice_type=inv.invoice_type,
            invoice_notes=inv.notes,
            settings=settings,
            status=effective_status,
        )

    balance_due = max(0, total - amount_paid)
    if effective_status in ("paid", "cancelled", "void", "credit_note") or inv.invoice_type == "credit_note":
        if effective_status == "paid":
            balance_due = 0

    live_gst = gst_is_enabled(settings) if settings else False
    inv_gst_override = getattr(inv, "gst_enabled", None)
    if use_snap_company:
        effective_gst = bool(snap_company.get("gst_enabled"))
    elif inv_gst_override is not None:
        effective_gst = bool(inv_gst_override)
    else:
        effective_gst = live_gst
    resp = InvoiceResponse(
        id=inv.id,
        invoice_number=inv.invoice_number,
        invoice_type=inv.invoice_type,
        ledger=getattr(inv, "ledger", None) or "development",
        client_id=inv.client_id,
        client_name=(snap_client or {}).get("name") if use_snap_client else (client.name if client else None),
        # Document bill-to is company-only; contact person is CC on email, not printed.
        client_attn=None,
        client_designation=None,
        client_email=(snap_client or {}).get("email") if use_snap_client else (getattr(client, "email", None) if client else None),
        client_phone=(snap_client or {}).get("phone") if use_snap_client else (getattr(client, "phone", None) if client else None),
        client_address=(snap_client or {}).get("address") if use_snap_client else (getattr(client, "address", None) if client else None),
        client_city=(snap_client or {}).get("city") if use_snap_client else (getattr(client, "city", None) if client else None),
        client_state=(snap_client or {}).get("state") if use_snap_client else (getattr(client, "state", None) if client else None),
        client_pincode=(snap_client or {}).get("pincode") if use_snap_client else (getattr(client, "pincode", None) if client else None),
        client_gstin=(snap_client or {}).get("gstin") if use_snap_client else (getattr(client, "gstin", None) if client else None),
        client_pan=(snap_client or {}).get("pan") if use_snap_client else (getattr(client, "pan", None) if client else None),
        client_state_code=(snap_client or {}).get("state_code") if use_snap_client else (getattr(client, "state_code", None) if client else None),
        project_id=inv.project_id,
        project_name=(
            (snap.get("project") or {}).get("name")
            if doc_locked and snap
            else (project.name if project else None)
        ),
        project_ref=project_ref,
        status=effective_status,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        subtotal_paise=inv.subtotal_paise,
        tax_rate_percent=float(inv.tax_rate_percent),
        tax_paise=inv.tax_paise,
        total_paise=inv.total_paise,
        place_of_supply=getattr(inv, "place_of_supply", None),
        supply_type=getattr(inv, "supply_type", None),
        cgst_paise=int(getattr(inv, "cgst_paise", 0) or 0),
        sgst_paise=int(getattr(inv, "sgst_paise", 0) or 0),
        igst_paise=int(getattr(inv, "igst_paise", 0) or 0),
        discount_paise=int(getattr(inv, "discount_paise", 0) or 0),
        round_off_paise=int(getattr(inv, "round_off_paise", 0) or 0),
        amount_paid_paise=amount_paid,
        balance_due_paise=balance_due,
        po_number=getattr(inv, "po_number", None),
        amount_in_words=amount_in_words_inr(inv.total_paise),
        notes=inv.notes,
        resolved_terms=resolved_terms,
        snapshot_frozen_at=getattr(inv, "snapshot_frozen_at", None) if doc_locked else None,
        # Locked only after payment / cancel / void — unpaid stays editable.
        document_locked=doc_locked,
        payments=payments_out,
        upi_id_override=getattr(inv, "upi_id_override", None),
        upi_qr_url=getattr(inv, "upi_qr_url", None),
        paid_at=inv.paid_at,
        period_key=inv.period_key,
        parent_invoice_id=inv.parent_invoice_id,
        parent_invoice_number=getattr(parent, "invoice_number", None) if parent else None,
        reminder_count=inv.reminder_count,
        last_reminder_at=inv.last_reminder_at,
        overdue_notified_at=inv.overdue_notified_at,
        email_status=inv.email_status,
        email_error=inv.email_error,
        email_retry_count=inv.email_retry_count,
        last_emailed_at=inv.last_emailed_at,
        period_months=inv.period_months or 1,
        covers_from=inv.covers_from,
        covers_to=inv.covers_to,
        monthly_rate_paise=inv.monthly_rate_paise,
        payment_proof_filename=getattr(inv, "payment_proof_filename", None),
        payment_proof_content_type=getattr(inv, "payment_proof_content_type", None),
        payment_proof_uploaded_at=getattr(inv, "payment_proof_uploaded_at", None),
        payment_proof_note=getattr(inv, "payment_proof_note", None),
        has_payment_proof=bool(getattr(inv, "payment_proof_object_key", None)),
        payment_verified_at=getattr(inv, "payment_verified_at", None),
        created_at=inv.created_at,
        items=[InvoiceItemResponse.model_validate(it) for it in (inv.items or [])],
        payment_upi_id=pay_instr.get("upi_id") or pay.get("upi_id"),
        payment_upi_qr_url=pay.get("upi_qr_url"),
        payment_bank_beneficiary_name=pay_instr.get("beneficiary"),
        payment_bank_name=pay_instr.get("bank_name"),
        payment_bank_account_number=pay_instr.get("account_number"),
        payment_bank_ifsc=pay_instr.get("ifsc"),
        payment_paypal_id=pay_instr.get("paypal_id"),
        payment_bank_upi_details=(
            (snap.get("payment") or {}).get("bank_upi_details")
            if doc_locked
            and snap
            and snapshot_payment_has_data((snap or {}).get("payment"))
            else (getattr(settings, "bank_upi_details", None) if settings else None)
        ),
        payment_bank_wire_instructions=(
            "\n".join(pay_instr.get("wire_instructions") or [])
            or (getattr(settings, "bank_wire_instructions", None) if settings else None)
        ),
        payment_wire_lines=list(pay_instr.get("wire_instructions") or []),
        gst_enabled=effective_gst,
        gst_enabled_override=None if use_snap_company else inv_gst_override,
        can_change_gst=bool(can_change_gst),
        gst_toggle_preview_number=gst_toggle_preview_number,
        can_delete=bool(can_delete),
        company_name=(snap_company or {}).get("name") if use_snap_company else (getattr(settings, "company_name", None) if settings else None),
        company_address=(snap_company or {}).get("address") if use_snap_company else (getattr(settings, "company_address", None) if settings else None),
        company_gstin=company_gstin if effective_gst else None,
        company_email=(snap_company or {}).get("email") if use_snap_company else (getattr(settings, "smtp_from_email", None) if settings else None),
        company_phone=(snap_company or {}).get("phone") if use_snap_company else (getattr(settings, "company_phone", None) if settings else None),
        company_website=(snap_company or {}).get("website") if use_snap_company else (getattr(settings, "company_website", None) if settings else None),
        company_tagline=(snap_company or {}).get("tagline") if use_snap_company else (getattr(settings, "company_tagline", None) if settings else None),
        company_logo_url=(snap_company or {}).get("logo_url") if use_snap_company else (getattr(settings, "company_logo_url", None) if settings else None),
        company_pan=(
            ((snap_company or {}).get("pan") if use_snap_company else getattr(settings, "company_pan", None))
            if effective_gst
            else None
        ),
        company_state=(snap_company or {}).get("state") if use_snap_company else (getattr(settings, "company_state", None) if settings else None),
        company_state_code=(snap_company or {}).get("state_code") if use_snap_company else (getattr(settings, "company_state_code", None) if settings else None),
        gst_unregistered=(
            (
                bool(snap_company.get("gst_unregistered"))
                if use_snap_company
                else bool(getattr(settings, "gst_unregistered", False)) if settings else False
            )
            if effective_gst
            else False
        ),
        default_sac_code=(
            getattr(settings, "default_sac_code", None) if settings and effective_gst else None
        ),
        payment_terms=getattr(settings, "payment_terms", None) if settings else None,
    )
    if not effective_gst:
        resp.client_gstin = None
        resp.client_pan = None
    return resp


async def _send_paid_receipt_email(db: AsyncSession, inv: Invoice, st: Optional[Settings]) -> None:
    """Best-effort paid receipt email after status is already committed as paid."""
    if not st or not st.smtp_host or not st.smtp_from_email:
        return
    if not inv.client or not inv.client.email:
        return
    from ..services.email_html import compose_invoice_client_email
    from ..services.invoice_mail import invoice_mail_targets
    from ..services.invoice_snapshot import (
        company_branding_from_snapshot,
        freeze_document_snapshot,
        get_snapshot,
    )

    freeze_document_snapshot(
        inv,
        client=inv.client,
        settings=st,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )
    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=st,
        items=inv.items,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )

    brand = company_branding_from_snapshot(get_snapshot(inv), st)
    snap = get_snapshot(inv) or {}
    snap_company = snap.get("company") if isinstance(snap.get("company"), dict) else {}
    to_email, cc_email = invoice_mail_targets(inv.client, st)
    _kind, subject, html, plain, attachment_name = compose_invoice_client_email(
        company_name=brand["company_name"] or st.company_name,
        company_address=brand["company_address"] or "",
        company_gstin=brand["company_gstin"] or "",
        company_logo_url=(snap_company.get("logo_url") if snap_company else None)
        or st.company_logo_url
        or "/brand/krws-mark.png",
        client_name=inv.client.name,
        invoice_number=inv.invoice_number,
        total_paise=inv.total_paise,
        due_date=inv.due_date,
        issue_date=inv.issue_date,
        paid_at=inv.paid_at,
        bank_upi_details=st.bank_upi_details or "",
        invoice_type=inv.invoice_type or "invoice",
        project_name=(inv.project.name if inv.project else "") or "",
        notes=inv.notes or "",
        status="paid",
        force_kind="paid",
    )
    try:
        await send_email_async(
            to_email=to_email,
            subject=subject,
            body=html,
            plain_body=plain,
            settings=st,
            attachment_bytes=pdf_bytes,
            attachment_filename=attachment_name,
            cc_email=cc_email,
            is_html=True,
        )
        inv.email_status = "sent"
        inv.last_emailed_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception:
        # Paid status is already committed; do not roll back settlement
        pass


def _assert_invoice_access(inv: Invoice, user: User) -> None:
    if user.role == "worker":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workers cannot view invoices"
        )
    if user.role == "client":
        if inv.client_id != user.client_id or inv.status == "draft":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return
    if not is_privileged(user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


# ----------------- Invoices -----------------

@router.get("", response_model=List[InvoiceResponse])
async def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workers cannot view invoices")
    if current_user.role != "client" and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(not_deleted(Invoice))
        .order_by(Invoice.created_at.desc())
    )

    if current_user.role == "client":
        if not current_user.client_id:
            return []
        # Client only sees own invoices, and MUST NEVER see draft invoices
        stmt = stmt.where(Invoice.client_id == current_user.client_id, Invoice.status != "draft")

    if type_filter and type_filter != "all":
        stmt = stmt.where(Invoice.invoice_type == type_filter)

    res = await db.execute(stmt)
    invoices = res.scalars().all()
    st = await _load_settings(db)

    from ..services.invoice_numbers import can_delete_with_tips, live_series_tips

    tips = await live_series_tips(db)
    formatted = [
        format_invoice_response(
            inv, st, can_delete=can_delete_with_tips(inv, tips)
        )
        for inv in invoices
    ]
    if status_filter and status_filter != "all":
        formatted = [inv for inv in formatted if inv.status == status_filter]

    return formatted

@router.get("/recurring", response_model=List[RecurringInvoiceResponse])
async def list_recurring_invoices(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = (
        select(RecurringInvoice)
        .join(Project, Project.id == RecurringInvoice.project_id)
        .options(
            selectinload(RecurringInvoice.client),
            selectinload(RecurringInvoice.project),
        )
        .where(
            not_deleted(RecurringInvoice),
            not_deleted(Project),
        )
        .order_by(RecurringInvoice.created_at.desc())
    )
    res = await db.execute(stmt)
    recs = res.scalars().unique().all()

    output = []
    for r in recs:
        resp = RecurringInvoiceResponse.model_validate(r)
        resp.client_name = r.client.name if r.client else None
        resp.project_name = r.project.name if r.project else None
        output.append(resp)
    return output

@router.post("/recurring", response_model=RecurringInvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_recurring_invoice(
    payload: RecurringInvoiceCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    from ..services.client_project import create_maintenance_plan

    rec = await create_maintenance_plan(
        db,
        project_id=payload.project_id,
        client_id=payload.client_id,
        amount_paise=payload.amount_paise,
        send_day=payload.send_day,
        due_after_days=payload.due_after_days,
        reminder_every_days=payload.reminder_every_days,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        line_items_template=payload.line_items_template,
        tax_rate_percent=payload.tax_rate_percent,
        day_of_month=payload.day_of_month,
        next_run_date=payload.next_run_date,
        active=payload.active,
        notes=payload.notes,
    )
    await db.commit()

    stmt = select(RecurringInvoice).options(selectinload(RecurringInvoice.client)).where(RecurringInvoice.id == rec.id)
    ref = (await db.execute(stmt)).scalars().first()
    resp = RecurringInvoiceResponse.model_validate(ref)
    resp.client_name = ref.client.name if ref.client else None
    return resp

@router.patch("/recurring/{recurring_id}", response_model=RecurringInvoiceResponse)
async def update_recurring_invoice(
    recurring_id: str,
    payload: RecurringInvoiceUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = (
        select(RecurringInvoice)
        .options(selectinload(RecurringInvoice.client))
        .where(RecurringInvoice.id == recurring_id, not_deleted(RecurringInvoice))
    )
    ref = (await db.execute(stmt)).scalars().first()
    if not ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ref, k, v)

    await db.commit()
    await db.refresh(ref)
    resp = RecurringInvoiceResponse.model_validate(ref)
    resp.client_name = ref.client.name if ref.client else None
    return resp

@router.delete("/recurring/{recurring_id}", status_code=status.HTTP_200_OK)
async def delete_recurring_invoice(
    recurring_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = select(RecurringInvoice).where(
        RecurringInvoice.id == recurring_id, not_deleted(RecurringInvoice)
    )
    ref = (await db.execute(stmt)).scalars().first()
    if not ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice not found")
    ref.active = False
    ref.soft_delete()
    await db.commit()
    return {"message": "Recurring invoice schedule deleted successfully"}

@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = (
        select(Invoice)
        .options(*_invoice_eager_options(include_parent=True))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    res = await db.execute(stmt)
    inv = res.scalars().first()

    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    _assert_invoice_access(inv, current_user)

    st = await _load_settings(db)
    from ..services.invoice_snapshot import ensure_issued_snapshot

    # Paid missing freeze → freeze; unpaid with stale freeze → clear.
    before_snap = getattr(inv, "document_snapshot", None)
    ensure_issued_snapshot(
        inv,
        client=inv.client,
        settings=st,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )
    if before_snap is not getattr(inv, "document_snapshot", None):
        await db.commit()
        await db.refresh(inv)
    from ..services.invoice_numbers import can_delete_invoice

    can_gst, toggle_preview = await _gst_toggle_response_fields(db, inv, st)
    can_del = await can_delete_invoice(db, inv)
    return format_invoice_response(
        inv,
        st,
        can_change_gst=can_gst,
        gst_toggle_preview_number=toggle_preview,
        can_delete=can_del,
    )

@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    from ..services.client_project import create_invoice_guards, assert_clients_exist

    await assert_clients_exist(db, [payload.client_id])
    await create_invoice_guards(
        db, project_id=payload.project_id, client_id=payload.client_id
    )

    # ATOMIC SEQUENCE INCREMENT with SELECT ... FOR UPDATE
    settings_stmt = select(Settings).where(Settings.id == "default").with_for_update()
    settings_res = await db.execute(settings_stmt)
    st = settings_res.scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settings record not found")

    from ..services.invoice_numbers import assign_unique_invoice_number
    from ..services.invoice_typing import normalize_line_kind, resolve_invoice_typing

    raw_type, ledger, number_hint = resolve_invoice_typing(
        invoice_type=payload.invoice_type,
        items=payload.items,
    )

    # Calculate subtotal from items; GST only when enabled in Settings
    subtotal_paise = sum(int(round(float(item.quantity) * item.unit_rate_paise)) for item in payload.items)
    from ..models import Client as ClientModel

    client_row = (
        await db.execute(select(ClientModel).where(ClientModel.id == payload.client_id))
    ).scalars().first()
    from ..services.gst_registrations import resolve_settings_for_invoice

    tax_settings, gst_reg = await resolve_settings_for_invoice(
        db, st, client=client_row, issue_date=payload.issue_date
    )
    # None = follow Settings; False forces a non-GST invoice.
    inv_gst = payload.gst_enabled
    settings_gst = gst_is_enabled(tax_settings) if tax_settings else False
    effective_gst = settings_gst if inv_gst is None else bool(inv_gst)
    tax_rate_in = float(payload.tax_rate_percent or 0) if effective_gst else 0.0
    tax_snap = apply_invoice_tax_snapshot(
        settings=tax_settings,
        client=client_row,
        subtotal_paise=subtotal_paise,
        tax_rate_percent=tax_rate_in,
        discount_paise=payload.discount_paise or 0,
    )
    tax_rate = float(tax_snap["tax_rate_percent"]) if effective_gst else 0.0
    tax_paise = int(tax_snap["tax_paise"]) if effective_gst else 0
    total_paise = (
        (int(tax_snap["total_paise"]) + int(tax_snap.get("round_off_paise", 0) or 0))
        if effective_gst
        else max(0, subtotal_paise - int(payload.discount_paise or 0))
    )

    initial_status = (payload.status or "draft").strip().lower()
    if initial_status not in ("draft", "sent", "paid", "overdue"):
        initial_status = "draft"
    paid_at = payload.paid_at
    if initial_status == "paid" and paid_at is None:
        paid_at = datetime.combine(payload.issue_date, datetime.min.time(), tzinfo=timezone.utc)

    invoice = Invoice(
        invoice_number="",
        invoice_type=raw_type,
        ledger=ledger,
        client_id=payload.client_id,
        project_id=payload.project_id,
        status=initial_status,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        subtotal_paise=subtotal_paise,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        gst_enabled=inv_gst,
        place_of_supply=tax_snap.get("place_of_supply") if effective_gst else None,
        supply_type=tax_snap.get("supply_type") if effective_gst else None,
        cgst_paise=int(tax_snap.get("cgst_paise") or 0) if effective_gst else 0,
        sgst_paise=int(tax_snap.get("sgst_paise") or 0) if effective_gst else 0,
        igst_paise=int(tax_snap.get("igst_paise") or 0) if effective_gst else 0,
        discount_paise=int(payload.discount_paise or 0),
        round_off_paise=0,
        amount_paid_paise=total_paise if initial_status == "paid" else 0,
        po_number=(payload.po_number or None),
        notes=payload.notes,
        upi_id_override=(payload.upi_id_override or None),
        upi_qr_url=(payload.upi_qr_url or None),
        period_months=payload.period_months or 1,
        covers_from=payload.covers_from,
        covers_to=payload.covers_to,
        monthly_rate_paise=payload.monthly_rate_paise,
        paid_at=paid_at,
    )
    try:
        await assign_unique_invoice_number(
            db,
            invoice,
            invoice_type=number_hint,
            issue_date=payload.issue_date,
            settings_row=st,
            gst_enabled=effective_gst,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    default_sac = (getattr(st, "default_sac_code", None) or "").strip() or None
    for item in payload.items:
        amt = int(round(float(item.quantity) * item.unit_rate_paise))
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            description=item.description,
            quantity=item.quantity,
            unit_rate_paise=item.unit_rate_paise,
            amount_paise=amt,
            line_kind=normalize_line_kind(item.line_kind, raw_type if raw_type != "mixed" else "custom_dev"),
            hsn_sac=(None if not effective_gst else (item.hsn_sac or default_sac)),
            unit=(item.unit or None),
        ))

    if (raw_type in ("amc", "mixed")) and payload.project_id and payload.covers_to:
        await _hold_maintenance_emails(db, payload.project_id, payload.covers_to)

    # Freeze only when created already paid. Unpaid (draft/sent/overdue) stays live.
    if initial_status == "paid":
        from ..services.invoice_snapshot import freeze_document_snapshot
        from ..models import Project as ProjectModel

        project_row = (
            await db.execute(select(ProjectModel).where(ProjectModel.id == payload.project_id))
        ).scalars().first()
        freeze_document_snapshot(
            invoice,
            client=client_row,
            settings=st,
            project=project_row,
            gst_registration=gst_reg,
            force=True,
        )

    await write_audit(
        db,
        current_user,
        "invoices",
        invoice.id,
        "create",
        after=snapshot_entity(invoice, ["invoice_number", "status", "total_paise", "client_id", "project_id"]),
        request=request,
    )
    await db.commit()

    # Re-fetch with loaded items
    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.client),
            selectinload(Invoice.project),
            selectinload(Invoice.items),
            selectinload(Invoice.payments),
        )
        .where(Invoice.id == invoice.id)
    )
    res = await db.execute(stmt)
    created_inv = res.scalars().first()
    from ..services.invoice_numbers import can_delete_invoice

    can_gst, toggle_preview = await _gst_toggle_response_fields(db, created_inv, st)
    can_del = await can_delete_invoice(db, created_inv) if created_inv else False
    return format_invoice_response(
        created_inv,
        st,
        can_change_gst=can_gst,
        gst_toggle_preview_number=toggle_preview,
        can_delete=can_del,
    )

@router.post("/{invoice_id}/cancel", response_model=InvoiceResponse)
async def cancel_invoice(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    before = {"status": inv.status}
    assert_can_cancel(inv)
    apply_status(inv, "cancelled")
    # Cancelled = permanent freeze (same as paid): number + details locked.
    st = await _load_settings(db)
    from ..services.invoice_snapshot import freeze_document_snapshot

    freeze_document_snapshot(
        inv,
        client=inv.client,
        settings=st,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
        force=True,
    )
    await write_audit(
        db, current_user, "invoices", inv.id, "cancel", before=before, after={"status": inv.status}, request=request
    )
    await db.commit()
    await db.refresh(inv)
    return format_invoice_response(inv, st, can_change_gst=False, can_delete=False)


@router.post("/{invoice_id}/credit-note", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_credit_note(
    invoice_id: str,
    payload: CreditNoteCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    parent = (await db.execute(stmt)).scalars().first()
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if parent.status in ("cancelled", "void", "draft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credit notes can only be issued against sent, overdue, or paid invoices",
        )
    if parent.invoice_type == "credit_note":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot credit a credit note")

    settings_stmt = select(Settings).where(Settings.id == "default").with_for_update()
    st = (await db.execute(settings_stmt)).scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settings record not found")

    # Credit amount is ex-GST subtotal magnitude (positive). Cap to parent subtotal.
    credit_sub = (
        int(payload.amount_paise)
        if payload.amount_paise is not None
        else int(parent.subtotal_paise or 0)
    )
    credit_sub = max(0, min(credit_sub, int(parent.subtotal_paise or 0)))
    if credit_sub <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credit amount must be greater than zero")

    from ..services.gst_registrations import resolve_settings_for_invoice

    today = date.today()
    tax_settings, gst_reg = await resolve_settings_for_invoice(
        db, st, client=parent.client, issue_date=today
    )
    tax_snap = apply_invoice_tax_snapshot(
        settings=tax_settings,
        client=parent.client,
        subtotal_paise=credit_sub,
        tax_rate_percent=float(parent.tax_rate_percent or 0),
        discount_paise=0,
    )
    tax_rate = float(tax_snap["tax_rate_percent"])
    tax_paise = int(tax_snap["tax_paise"])
    total_paise = int(tax_snap["total_paise"])

    from ..services.invoice_numbers import assign_unique_invoice_number
    from ..services.invoice_snapshot import freeze_document_snapshot
    from ..services.project_money import ledger_for

    reason = (payload.reason or "").strip()
    notes = f"Credit note for {parent.invoice_number}"
    if reason:
        notes = f"{notes}. {reason}"

    # Paid parent → credit note marked paid (refund applied). Unpaid → sent credit reduces outstanding.
    cn_status = "paid" if parent.status == "paid" else "sent"
    credit = Invoice(
        invoice_number="",
        invoice_type="credit_note",
        ledger=ledger_for(parent.invoice_type or "custom_dev"),
        client_id=parent.client_id,
        project_id=parent.project_id,
        parent_invoice_id=parent.id,
        status=cn_status,
        issue_date=today,
        due_date=today,
        subtotal_paise=credit_sub,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        place_of_supply=tax_snap.get("place_of_supply"),
        supply_type=tax_snap.get("supply_type"),
        cgst_paise=int(tax_snap.get("cgst_paise") or 0),
        sgst_paise=int(tax_snap.get("sgst_paise") or 0),
        igst_paise=int(tax_snap.get("igst_paise") or 0),
        amount_paid_paise=total_paise if cn_status == "paid" else 0,
        notes=notes,
        paid_at=datetime.now(timezone.utc) if cn_status == "paid" else None,
        upi_id_override=parent.upi_id_override,
        upi_qr_url=parent.upi_qr_url,
        gst_enabled=getattr(parent, "gst_enabled", None),
    )
    try:
        await assign_unique_invoice_number(
            db,
            credit,
            invoice_type="credit_note",
            issue_date=today,
            settings_row=st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    db.add(
        InvoiceItem(
            invoice_id=credit.id,
            description=f"Credit against {parent.invoice_number}",
            quantity=1,
            unit_rate_paise=credit_sub,
            amount_paise=credit_sub,
        )
    )
    freeze_document_snapshot(
        credit,
        client=parent.client,
        settings=st,
        project=parent.project,
        parent_invoice=parent,
        gst_registration=gst_reg,
    )
    await db.commit()

    ref = (
        await db.execute(
            select(Invoice)
            .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
            .where(Invoice.id == credit.id)
        )
    ).scalars().first()
    return format_invoice_response(ref, st)


@router.post("/{invoice_id}/debit-note", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_debit_note(
    invoice_id: str,
    payload: DebitNoteCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    """Issue a debit note (additional charge) referencing a parent invoice."""
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    parent = (await db.execute(stmt)).scalars().first()
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if parent.status in ("cancelled", "void", "draft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debit notes can only be issued against sent, overdue, or paid invoices",
        )
    if parent.invoice_type in ("credit_note", "debit_note"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot debit a credit/debit note",
        )

    settings_stmt = select(Settings).where(Settings.id == "default").with_for_update()
    st = (await db.execute(settings_stmt)).scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settings record not found")

    debit_sub = max(0, int(payload.amount_paise or 0))
    if debit_sub <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Debit amount must be greater than zero")

    from ..services.gst_registrations import resolve_settings_for_invoice

    today = date.today()
    tax_settings, gst_reg = await resolve_settings_for_invoice(
        db, st, client=parent.client, issue_date=today
    )
    tax_snap = apply_invoice_tax_snapshot(
        settings=tax_settings,
        client=parent.client,
        subtotal_paise=debit_sub,
        tax_rate_percent=float(parent.tax_rate_percent or 0),
        discount_paise=0,
    )
    tax_rate = float(tax_snap["tax_rate_percent"])
    tax_paise = int(tax_snap["tax_paise"])
    total_paise = int(tax_snap["total_paise"])

    from ..services.invoice_numbers import assign_unique_invoice_number
    from ..services.invoice_snapshot import freeze_document_snapshot
    from ..services.project_money import ledger_for

    reason = (payload.reason or "").strip()
    notes = f"Debit note for {parent.invoice_number}"
    if reason:
        notes = f"{notes}. {reason}"
    line_desc = (payload.description or "").strip() or f"Additional charge on {parent.invoice_number}"

    debit = Invoice(
        invoice_number="",
        invoice_type="debit_note",
        ledger=ledger_for(parent.invoice_type or "custom_dev"),
        client_id=parent.client_id,
        project_id=parent.project_id,
        parent_invoice_id=parent.id,
        status="sent",
        issue_date=today,
        due_date=today,
        subtotal_paise=debit_sub,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        place_of_supply=tax_snap.get("place_of_supply"),
        supply_type=tax_snap.get("supply_type"),
        cgst_paise=int(tax_snap.get("cgst_paise") or 0),
        sgst_paise=int(tax_snap.get("sgst_paise") or 0),
        igst_paise=int(tax_snap.get("igst_paise") or 0),
        amount_paid_paise=0,
        notes=notes,
        upi_id_override=parent.upi_id_override,
        upi_qr_url=parent.upi_qr_url,
        gst_enabled=getattr(parent, "gst_enabled", None),
    )
    try:
        await assign_unique_invoice_number(
            db,
            debit,
            invoice_type="debit_note",
            issue_date=today,
            settings_row=st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    db.add(
        InvoiceItem(
            invoice_id=debit.id,
            description=line_desc,
            quantity=1,
            unit_rate_paise=debit_sub,
            amount_paise=debit_sub,
        )
    )
    freeze_document_snapshot(
        debit,
        client=parent.client,
        settings=st,
        project=parent.project,
        parent_invoice=parent,
        gst_registration=gst_reg,
    )
    await db.commit()

    ref = (
        await db.execute(
            select(Invoice)
            .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
            .where(Invoice.id == debit.id)
        )
    ).scalars().first()
    return format_invoice_response(ref, st)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: str,
    payload: InvoiceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    from ..models import InvoiceAuditLog

    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.items),
            selectinload(Invoice.payments),
            selectinload(Invoice.client),
            selectinload(Invoice.project),
        )
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    old_subtotal = int(inv.subtotal_paise or 0)
    was_sent = inv.status in ("sent", "overdue")
    before = snapshot_entity(
        inv,
        ["status", "notes", "due_date", "issue_date", "tax_rate_percent", "subtotal_paise", "total_paise"],
    )
    update_dict = payload.model_dump(exclude_unset=True)
    requested_status = update_dict.pop("status", None)
    # Defense in depth if a future field sneaks onto the schema
    if "project_id" in update_dict or "client_id" in update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id and client_id cannot be changed on an existing invoice",
        )
    new_items = update_dict.pop("items", None)

    _SOFT_PATCH = {"notes", "due_date", "upi_id_override", "upi_qr_url"}
    _ALLOWED_INVOICE_PATCH = {
        "invoice_type",
        "issue_date",
        "due_date",
        "tax_rate_percent",
        "gst_enabled",
        "notes",
        "status",
        "upi_id_override",
        "upi_qr_url",
        "period_months",
        "covers_from",
        "covers_to",
        "monthly_rate_paise",
        "discount_paise",
        "po_number",
    }
    hard_keys = set(update_dict.keys()) - _SOFT_PATCH
    content_change = bool(hard_keys) or new_items is not None
    if content_change:
        assert_content_editable(inv.status)
    else:
        assert_soft_editable(inv.status)

    gst_flag_changing = "gst_enabled" in update_dict
    st = await _load_settings(db)

    # GST series switch: archive → hard-delete → new number on other series.
    if gst_flag_changing and content_change:
        from ..services.invoice_numbers import (
            GST_TOGGLE_BLOCKED_TIP,
            can_delete_invoice,
            parse_series_key,
        )
        from ..services.gst_series_switch import (
            reload_invoice_detail,
            switch_invoice_gst_series,
        )

        target_gst = bool(update_dict.get("gst_enabled"))
        # Treat explicit null as follow-settings
        if update_dict.get("gst_enabled") is None:
            target_gst = bool(getattr(st, "gst_enabled", False))
        parsed = parse_series_key(inv.invoice_number)
        series_would_change = (
            parsed is not None and bool(parsed["gst_enabled"]) != bool(target_gst)
        )
        if series_would_change:
            try:
                new_inv = await switch_invoice_gst_series(
                    db,
                    inv,
                    target_gst_enabled=target_gst,
                    settings_row=st,
                    update_fields=update_dict,
                    new_items=new_items,
                    deleted_by=current_user,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc) or GST_TOGGLE_BLOCKED_TIP,
                ) from exc

            await write_audit(
                db,
                current_user,
                "invoices",
                new_inv.id,
                "gst_series_switch",
                before=before,
                after=snapshot_entity(
                    new_inv,
                    [
                        "invoice_number",
                        "status",
                        "gst_enabled",
                        "tax_rate_percent",
                        "subtotal_paise",
                        "total_paise",
                    ],
                ),
                request=request,
            )
            await db.commit()
            refreshed = await reload_invoice_detail(db, new_inv.id)
            can_gst, toggle_preview = await _gst_toggle_response_fields(
                db, refreshed or new_inv, st
            )
            can_del = await can_delete_invoice(db, refreshed or new_inv)
            return format_invoice_response(
                refreshed or new_inv,
                st,
                can_change_gst=can_gst,
                gst_toggle_preview_number=toggle_preview,
                can_delete=can_del,
            )

    for k, v in update_dict.items():
        if k not in _ALLOWED_INVOICE_PATCH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field '{k}' cannot be updated on an invoice",
            )
        setattr(inv, k, v)

    if requested_status is not None:
        apply_status(inv, requested_status)
        # Freeze only when becoming paid (or cancel/void). Unpaid stays live.
        if requested_status in ("paid", "cancelled", "void"):
            from ..services.invoice_snapshot import freeze_document_snapshot

            freeze_document_snapshot(
                inv,
                client=inv.client,
                settings=st,
                project=inv.project,
                parent_invoice=_loaded_parent_invoice(inv),
                force=True,
            )

    if new_items is not None:
        from ..services.invoice_typing import normalize_line_kind, resolve_invoice_typing

        # Delete existing items and add new items
        for old_it in inv.items:
            await db.delete(old_it)
        await db.flush()

        subtotal_paise = 0
        for item_data in new_items:
            qty = float(item_data.get("quantity", 1.0))
            rate = int(item_data.get("unit_rate_paise", 0))
            amt = int(round(qty * rate))
            subtotal_paise += amt
            db.add(InvoiceItem(
                invoice_id=inv.id,
                description=item_data.get("description", ""),
                quantity=qty,
                unit_rate_paise=rate,
                amount_paise=amt,
                line_kind=normalize_line_kind(
                    item_data.get("line_kind"),
                    inv.invoice_type or "custom_dev",
                ),
                hsn_sac=(
                    None
                    if inv.gst_enabled is False
                    else (item_data.get("hsn_sac") or None)
                ),
                unit=(item_data.get("unit") or None),
            ))
        inv.subtotal_paise = subtotal_paise
        header_type, header_ledger, _ = resolve_invoice_typing(
            invoice_type=update_dict.get("invoice_type") or inv.invoice_type,
            items=new_items,
        )
        inv.invoice_type = header_type
        inv.ledger = header_ledger

    # Resolve effective GST for tax math (override or Settings).
    if inv.gst_enabled is False:
        inv.tax_rate_percent = 0
        inv.tax_paise = 0
        inv.cgst_paise = 0
        inv.sgst_paise = 0
        inv.igst_paise = 0
        inv.place_of_supply = None
        inv.supply_type = None
        inv.total_paise = max(
            0,
            int(inv.subtotal_paise or 0) - int(getattr(inv, "discount_paise", 0) or 0),
        )
        if new_items is None:
            for it in inv.items or []:
                it.hsn_sac = None
    elif (
        "tax_rate_percent" in update_dict
        or new_items is not None
        or "discount_paise" in update_dict
        or "gst_enabled" in update_dict
    ):
        # Ensure client is available for place-of-supply / GST split
        if "client" in sa_inspect(inv).unloaded:
            await db.refresh(inv, attribute_names=["client"])
        tax_snap = apply_invoice_tax_snapshot(
            settings=st,
            client=inv.client,
            subtotal_paise=int(inv.subtotal_paise or 0),
            tax_rate_percent=float(inv.tax_rate_percent or 0),
            discount_paise=int(getattr(inv, "discount_paise", 0) or 0),
        )
        inv.tax_rate_percent = float(tax_snap["tax_rate_percent"])
        inv.tax_paise = int(tax_snap["tax_paise"])
        inv.total_paise = int(tax_snap["total_paise"])
        inv.place_of_supply = tax_snap.get("place_of_supply")
        inv.supply_type = tax_snap.get("supply_type")
        inv.cgst_paise = int(tax_snap.get("cgst_paise") or 0)
        inv.sgst_paise = int(tax_snap.get("sgst_paise") or 0)
        inv.igst_paise = int(tax_snap.get("igst_paise") or 0)

    if inv.invoice_type in ("maintenance", "amc"):
        inv.invoice_type = "amc"
        inv.ledger = "maintenance"
        if inv.project_id and inv.covers_to:
            await _hold_maintenance_emails(db, inv.project_id, inv.covers_to)
    elif inv.invoice_type == "mixed" and inv.project_id and inv.covers_to:
        await _hold_maintenance_emails(db, inv.project_id, inv.covers_to)

    if was_sent and int(inv.subtotal_paise or 0) != old_subtotal:
        db.add(InvoiceAuditLog(
            invoice_id=inv.id,
            actor_id=current_user.id,
            action="amount_edited",
            old_subtotal_paise=old_subtotal,
            new_subtotal_paise=int(inv.subtotal_paise or 0),
            note="Re-send the updated invoice to the client.",
        ))

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "update",
        before=before,
        after=snapshot_entity(
            inv,
            ["status", "notes", "due_date", "issue_date", "tax_rate_percent", "subtotal_paise", "total_paise"],
        ),
        request=request,
    )
    await db.commit()

    # Re-fetch
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    res = await db.execute(stmt)
    updated_inv = res.scalars().first()
    from ..services.invoice_numbers import can_delete_invoice

    can_gst, toggle_preview = await _gst_toggle_response_fields(db, updated_inv, st)
    can_del = await can_delete_invoice(db, updated_inv) if updated_inv else False
    return format_invoice_response(
        updated_inv,
        st,
        can_change_gst=can_gst,
        gst_toggle_preview_number=toggle_preview,
        can_delete=can_del,
    )

@router.delete("/{invoice_id}", status_code=status.HTTP_200_OK)
async def delete_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    from ..services.deleted_invoice_archive import (
        DELETED_INVOICE_LOAD_OPTIONS,
        archive_deleted_invoice,
    )
    from ..services.gst_series_switch import hard_delete_invoice_completely
    from ..services.invoice_numbers import reclaim_sequence_if_tip

    stmt = (
        select(Invoice)
        .options(*DELETED_INVOICE_LOAD_OPTIONS)
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    from ..services.invoice_numbers import assert_can_delete_invoice

    await assert_can_delete_invoice(db, inv)

    # Archive for founder/admin DB reference, then permanently delete + free the number.
    archive_deleted_invoice(
        db, inv, deleted_by=current_user, deleted_reason="user_delete"
    )
    await db.flush()

    st = await _load_settings(db)
    st_locked = (
        await db.execute(
            select(Settings).where(Settings.id == "default").with_for_update()
        )
    ).scalar_one_or_none() or st
    old_number = await hard_delete_invoice_completely(db, inv)
    if st_locked:
        reclaim_sequence_if_tip(st_locked, old_number)
    await db.commit()
    return {
        "message": "Invoice permanently deleted",
        "revoked_invoice_number": old_number,
    }

@router.get("/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: str,
    paper_size: Optional[str] = Query(
        None,
        description="Page size: a3, a4, a5, a6, letter, legal, tabloid, executive, b4, b5",
    ),
    orientation: Optional[str] = Query(
        "portrait",
        description="Page orientation: portrait or landscape (applies to all paper sizes).",
    ),
    download: bool = Query(
        True,
        description="If true, Content-Disposition is attachment (save as invoice number).",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from ..services.file_mime import (
        attachment_content_disposition,
        inline_content_disposition,
    )
    from ..services.pdf import PAPER_SIZES, resolve_paper_size

    stmt = (
        select(Invoice)
        .options(*_invoice_eager_options(include_parent=True))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    _assert_invoice_access(inv, current_user)

    orient_raw = (orientation or "portrait").strip().lower().replace(" ", "_")
    if orient_raw not in {"portrait", "landscape", "l", "horizontal", "p", "vertical"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported orientation. Use portrait or landscape.",
        )

    if paper_size is not None and str(paper_size).strip():
        raw = str(paper_size).strip().lower().replace(" ", "_")
        resolved_key, _ = resolve_paper_size(raw, orient_raw)
        # Reject unknown values (resolve falls back to a4 for garbage).
        known = set(PAPER_SIZES) | {
            "us_letter",
            "us-letter",
            "us_legal",
            "us-legal",
            "ledger",
            "11x17",
        }
        if raw not in known and resolved_key == "a4" and raw != "a4":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported paper_size. Use one of: {', '.join(sorted(PAPER_SIZES))}",
            )

    st_res = await db.execute(select(Settings).where(Settings.id == "default"))
    settings_obj = st_res.scalars().first()

    from ..services.invoice_snapshot import ensure_issued_snapshot

    before_snap = getattr(inv, "document_snapshot", None)
    ensure_issued_snapshot(
        inv,
        client=inv.client,
        settings=settings_obj,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )
    if before_snap is not getattr(inv, "document_snapshot", None):
        await db.commit()

    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=settings_obj,
        items=inv.items,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
        paper_size=paper_size,
        orientation=orient_raw,
    )

    raw_number = (inv.invoice_number or invoice_id).strip()
    safe_number = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in raw_number
    ) or "invoice"
    filename = f"{safe_number}.pdf"
    disposition = (
        attachment_content_disposition(filename)
        if download
        else inline_content_disposition(filename)
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(pdf_bytes)),
        },
    )

@router.post("/{invoice_id}/send")
async def send_invoice_email(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    stmt = (
        select(Invoice)
        .options(*_invoice_eager_options(include_parent=True))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if not inv.client or not (inv.client.email or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client company has no email address",
        )
    assert_can_send(inv)

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()

    if not st or not st.smtp_host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SMTP is not configured in settings")

    from ..services.invoice_snapshot import (
        company_branding_from_snapshot,
        document_details_are_locked,
        get_snapshot,
    )
    from ..services.gst_registrations import resolve_settings_for_invoice
    from ..services.email_html import compose_invoice_client_email
    from ..services.invoice_mail import invoice_mail_targets

    _tax_settings, gst_reg = await resolve_settings_for_invoice(
        db, st, client=inv.client, issue_date=inv.issue_date
    )
    # Unpaid send uses live company/client — freeze only happens on paid.

    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=st,
        items=inv.items,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )

    locked = document_details_are_locked(inv)
    brand = company_branding_from_snapshot(
        get_snapshot(inv), st, prefer_live=not locked
    )
    snap = get_snapshot(inv) or {}
    snap_company = snap.get("company") if isinstance(snap.get("company"), dict) else {}
    to_email, cc_email = invoice_mail_targets(inv.client, st)
    kind, subject, html, plain, attachment_name = compose_invoice_client_email(
        company_name=brand["company_name"] or st.company_name,
        company_address=brand["company_address"] or "",
        company_gstin=brand["company_gstin"] or "",
        company_logo_url=(
            (snap_company.get("logo_url") if locked and snap_company else None)
            or st.company_logo_url
            or "/brand/krws-mark.png"
        ),
        client_name=inv.client.name,
        invoice_number=inv.invoice_number,
        total_paise=inv.total_paise,
        due_date=inv.due_date,
        issue_date=inv.issue_date,
        paid_at=inv.paid_at,
        bank_upi_details=st.bank_upi_details or "Please contact accounts for payment info.",
        invoice_type=inv.invoice_type or "invoice",
        project_name=(inv.project.name if inv.project else "") or "",
        notes=inv.notes or "",
        status=inv.status or "draft",
    )

    try:
        await send_email_async(
            to_email=to_email,
            subject=subject,
            body=html,
            plain_body=plain,
            settings=st,
            attachment_bytes=pdf_bytes,
            attachment_filename=attachment_name,
            is_html=True,
            cc_email=cc_email,
        )
    except Exception as e:
        inv.email_status = "failed"
        inv.email_error = str(e)[:500]
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SMTP send failed: {e}",
        )

    inv.email_status = "sent"
    inv.email_error = None
    inv.last_emailed_at = datetime.now(timezone.utc)
    before_status = inv.status
    # Never flip a paid invoice back to "sent"
    if inv.status == "draft":
        apply_status(inv, "sent")
    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "send",
        before={"status": before_status, "email_status": "pending"},
        after={"status": inv.status, "email_status": inv.email_status},
        request=request,
    )
    await db.commit()

    kind_label = {
        "paid": "paid receipt",
        "overdue": "overdue notice",
        "late_fee": "late fee invoice",
        "sent": "invoice",
        "reminder": "payment reminder",
    }.get(kind, "invoice")
    msg = f"{kind_label.capitalize()} for {inv.invoice_number} sent to {to_email}"
    if cc_email:
        msg += f" (CC: {cc_email})"
    return {
        "message": msg,
        "email_kind": kind,
    }

@router.get("/{invoice_id}/payments", response_model=List[InvoicePaymentResponse])
async def list_invoice_payments(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "worker":
        raise HTTPException(status_code=403, detail="Workers cannot view invoices")
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.payments), selectinload(Invoice.client))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_invoice_access(inv, current_user)
    out: List[InvoicePaymentResponse] = []
    for p in inv.payments or []:
        if getattr(p, "deleted_at", None):
            continue
        out.append(
            InvoicePaymentResponse(
                id=p.id,
                amount_paise=int(p.amount_paise or 0),
                method=p.method,
                reference=p.reference,
                paid_at=p.paid_at,
                note=p.note,
                status=getattr(p, "status", None) or "verified",
                rejection_reason=getattr(p, "rejection_reason", None),
                recorded_by=p.recorded_by,
                verified_by=getattr(p, "verified_by", None),
                verified_at=getattr(p, "verified_at", None),
                rejected_by=getattr(p, "rejected_by", None),
                rejected_at=getattr(p, "rejected_at", None),
                has_proof=bool(getattr(p, "proof_object_key", None)),
            )
        )
    return out


@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
async def create_invoice_payment(
    invoice_id: str,
    payload: InvoicePaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.client),
            selectinload(Invoice.project),
            selectinload(Invoice.items),
            selectinload(Invoice.payments),
        )
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    assert_payment_mutable(inv)
    await assert_unique_reference(db, invoice_id=inv.id, reference=payload.reference)

    now = payload.paid_at or datetime.now(timezone.utc)
    status_val = "verified" if payload.mark_verified else "pending"
    pay = InvoicePayment(
        invoice_id=inv.id,
        amount_paise=int(payload.amount_paise),
        method=payload.method or "bank_transfer",
        reference=(payload.reference or "").strip() or None,
        note=(payload.note or "").strip() or None,
        recorded_by=current_user.id,
        paid_at=now,
        status=status_val,
        verified_by=current_user.id if status_val == "verified" else None,
        verified_at=now if status_val == "verified" else None,
    )
    db.add(pay)
    await db.flush()
    await recalc_amount_paid(db, inv)
    newly_paid = maybe_mark_invoice_paid(inv, actor_id=current_user.id)
    if newly_paid:
        st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        if st:
            from ..services.invoice_snapshot import freeze_document_snapshot

            freeze_document_snapshot(
                inv,
                client=inv.client,
                settings=st,
                project=inv.project,
                parent_invoice=_loaded_parent_invoice(inv),
                force=True,
            )
        if inv.ledger == "maintenance" and inv.project_id and inv.covers_to:
            await _hold_maintenance_emails(db, inv.project_id, inv.covers_to)

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "create_payment",
        after={
            "payment_id": pay.id,
            "amount_paise": pay.amount_paise,
            "status": pay.status,
            "invoice_status": inv.status,
        },
        request=request,
    )
    await db.commit()
    refreshed = (
        await db.execute(
            select(Invoice)
            .options(*_invoice_eager_options())
            .where(Invoice.id == invoice_id, not_deleted(Invoice))
        )
    ).scalars().first()
    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    return format_invoice_response(refreshed or inv, st)


@router.post("/{invoice_id}/payments/{payment_id}/verify", response_model=InvoiceResponse)
async def verify_invoice_payment(
    invoice_id: str,
    payment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.client),
            selectinload(Invoice.project),
            selectinload(Invoice.items),
            selectinload(Invoice.payments),
        )
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    pay = next((p for p in (inv.payments or []) if p.id == payment_id and not p.deleted_at), None)
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    if pay.status == "verified":
        st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        return format_invoice_response(inv, st)
    if pay.status == "rejected":
        raise HTTPException(status_code=400, detail="Cannot verify a rejected payment")

    now = datetime.now(timezone.utc)
    pay.status = "verified"
    pay.verified_by = current_user.id
    pay.verified_at = now
    await recalc_amount_paid(db, inv)
    newly_paid = maybe_mark_invoice_paid(inv, actor_id=current_user.id)
    if newly_paid:
        st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        if st:
            from ..services.invoice_snapshot import freeze_document_snapshot

            freeze_document_snapshot(
                inv,
                client=inv.client,
                settings=st,
                project=inv.project,
                parent_invoice=_loaded_parent_invoice(inv),
                force=True,
            )
    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "verify_payment_row",
        after={"payment_id": pay.id, "invoice_status": inv.status},
        request=request,
    )
    await db.commit()
    refreshed = (
        await db.execute(
            select(Invoice)
            .options(*_invoice_eager_options())
            .where(Invoice.id == invoice_id, not_deleted(Invoice))
        )
    ).scalars().first()
    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    return format_invoice_response(refreshed or inv, st)


@router.post("/{invoice_id}/payments/{payment_id}/reject", response_model=InvoiceResponse)
async def reject_invoice_payment(
    invoice_id: str,
    payment_id: str,
    payload: InvoicePaymentReject,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.client),
            selectinload(Invoice.project),
            selectinload(Invoice.items),
            selectinload(Invoice.payments),
        )
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == "paid":
        raise HTTPException(
            status_code=400,
            detail="Cannot reject payments on a paid invoice; issue a credit note instead",
        )
    pay = next((p for p in (inv.payments or []) if p.id == payment_id and not p.deleted_at), None)
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")
    if pay.status == "rejected":
        st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
        return format_invoice_response(inv, st)

    was_verified = pay.status == "verified"
    now = datetime.now(timezone.utc)
    pay.status = "rejected"
    pay.rejection_reason = payload.reason.strip()
    pay.rejected_by = current_user.id
    pay.rejected_at = now
    pay.verified_by = None
    pay.verified_at = None
    if was_verified:
        await recalc_amount_paid(db, inv)

    # If invoice was awaiting proof and no other pending proofs, return to sent/overdue
    if inv.status == "proof_pending":
        still_pending = any(
            (getattr(p, "status", None) == "pending") and not p.deleted_at
            for p in (inv.payments or [])
            if p.id != pay.id
        )
        if not still_pending and not inv.payment_proof_object_key:
            target = "overdue" if inv.due_date and inv.due_date < date.today() else "sent"
            apply_status(inv, target)
        elif not still_pending and inv.payment_proof_object_key:
            # Keep proof_pending if invoice-level proof still awaits — founder should re-verify
            pass

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "reject_payment",
        after={
            "payment_id": pay.id,
            "reason": pay.rejection_reason,
            "invoice_status": inv.status,
        },
        request=request,
    )
    await db.commit()
    refreshed = (
        await db.execute(
            select(Invoice)
            .options(*_invoice_eager_options())
            .where(Invoice.id == invoice_id, not_deleted(Invoice))
        )
    ).scalars().first()
    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    return format_invoice_response(refreshed or inv, st)


@router.post("/{invoice_id}/pay", response_model=InvoiceResponse)
@router.post("/{invoice_id}/mark-paid", response_model=InvoiceResponse)
async def mark_invoice_as_paid(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    before_status = inv.status
    assert_can_pay(inv)
    if inv.status != "paid":
        apply_status(inv, "paid")
        inv.paid_at = datetime.now(timezone.utc)
        remaining = max(
            0,
            int(inv.total_paise or 0) - int(getattr(inv, "amount_paid_paise", 0) or 0),
        )
        if remaining > 0:
            db.add(
                _record_invoice_payment(
                    inv=inv,
                    amount_paise=remaining,
                    method="bank_transfer",
                    reference=None,
                    note="Marked paid (no proof)",
                    recorded_by=current_user.id,
                    paid_at=inv.paid_at,
                )
            )
    if inv.ledger == "maintenance" and inv.project_id and inv.covers_to:
        await _hold_maintenance_emails(db, inv.project_id, inv.covers_to)

    # Automatically waive any pending or scheduled late-fee actions for this invoice
    act_stmt = select(LateFeeAction).where(
        LateFeeAction.invoice_id == invoice_id,
        LateFeeAction.status.in_(["pending", "scheduled"])
    )
    pending_actions = (await db.execute(act_stmt)).scalars().all()
    for act in pending_actions:
        act.status = "waived"
        act.decided_by = current_user.id
        act.decided_at = datetime.now(timezone.utc)

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    if st:
        from ..services.invoice_snapshot import freeze_document_snapshot

        # Freeze current company / client / payment at the moment of payment.
        freeze_document_snapshot(
            inv,
            client=inv.client,
            settings=st,
            project=inv.project,
            parent_invoice=_loaded_parent_invoice(inv),
            force=True,
        )

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "pay",
        before={"status": before_status},
        after={"status": inv.status, "paid_at": inv.paid_at},
        request=request,
    )
    await db.commit()

    refreshed = (
        await db.execute(
            select(Invoice)
            .options(*_invoice_eager_options())
            .where(Invoice.id == invoice_id, not_deleted(Invoice))
        )
    ).scalars().first()
    await _send_paid_receipt_email(db, refreshed or inv, st)
    return format_invoice_response(refreshed or inv, st)


@router.post("/{invoice_id}/payment-proof", response_model=InvoiceResponse)
async def upload_payment_proof(
    invoice_id: str,
    request: Request,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Client (or founder) uploads bank/UPI payment proof. Status → proof_pending."""
    if current_user.role == "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workers cannot upload payment proof")
    if current_user.role != "client" and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    from ..services.invoice_payment_proof import save_payment_proof

    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    _assert_invoice_access(inv, current_user)
    allow_paid_archive = is_privileged(current_user.role)
    assert_can_upload_payment_proof(inv, allow_paid_archive=allow_paid_archive)

    key, original, content_type, _size = await save_payment_proof(inv.id, file)
    before = {"status": inv.status, "has_payment_proof": bool(inv.payment_proof_object_key)}
    inv.payment_proof_object_key = key
    inv.payment_proof_filename = original
    inv.payment_proof_content_type = content_type
    inv.payment_proof_uploaded_at = datetime.now(timezone.utc)
    inv.payment_proof_uploaded_by = current_user.id
    inv.payment_proof_note = (note or "").strip() or None
    # Unpaid → await founder verify; paid archive upload keeps paid + marks verified
    if inv.status in ("sent", "overdue"):
        inv.payment_verified_at = None
        inv.payment_verified_by = None
        apply_status(inv, "proof_pending")
    elif inv.status == "paid":
        inv.payment_verified_at = inv.payment_verified_at or datetime.now(timezone.utc)
        inv.payment_verified_by = inv.payment_verified_by or current_user.id

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "upload_payment_proof",
        before=before,
        after={
            "status": inv.status,
            "payment_proof_filename": inv.payment_proof_filename,
            "has_payment_proof": True,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    st = await _load_settings(db)
    return format_invoice_response(inv, st)


@router.get("/{invoice_id}/payment-proof")
async def download_payment_proof(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download payment proof file (client own invoice or founder/admin)."""
    if current_user.role == "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if current_user.role != "client" and not is_privileged(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    from ..services.invoice_payment_proof import read_payment_proof

    stmt = select(Invoice).where(Invoice.id == invoice_id, not_deleted(Invoice))
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    _assert_invoice_access(inv, current_user)
    if not inv.payment_proof_object_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No payment proof uploaded")

    from ..services.file_mime import inline_content_disposition, resolve_media_type

    data = read_payment_proof(inv.payment_proof_object_key, inv.id)
    filename = inv.payment_proof_filename or "payment-proof"
    media = resolve_media_type(
        inv.payment_proof_content_type,
        inv.payment_proof_filename,
        inv.payment_proof_object_key,
    )
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": inline_content_disposition(filename)},
    )


@router.post("/{invoice_id}/reject-payment-proof", response_model=InvoiceResponse)
async def reject_payment_proof(
    invoice_id: str,
    payload: InvoicePaymentReject,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Reject invoice-level payment proof; preserve file + note history on the invoice."""
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status != "proof_pending":
        raise HTTPException(status_code=400, detail="Invoice is not awaiting payment verification")
    if not inv.payment_proof_object_key:
        raise HTTPException(status_code=400, detail="No payment proof to reject")

    before = {"status": inv.status, "payment_proof_note": inv.payment_proof_note}
    reason = payload.reason.strip()
    prior_note = (inv.payment_proof_note or "").strip()
    inv.payment_proof_note = (
        f"[REJECTED] {reason}" + (f" | prior: {prior_note}" if prior_note else "")
    )
    inv.payment_verified_at = None
    inv.payment_verified_by = None
    target = "overdue" if inv.due_date and inv.due_date < date.today() else "sent"
    apply_status(inv, target)
    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "reject_payment_proof",
        before=before,
        after={"status": inv.status, "reason": reason},
        request=request,
    )
    await db.commit()
    await db.refresh(inv)
    st = await _load_settings(db)
    return format_invoice_response(inv, st)


@router.post("/{invoice_id}/verify-payment", response_model=InvoiceResponse)
async def verify_payment_and_mark_paid(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Founder/admin verifies uploaded proof → paid + send paid invoice receipt email."""
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    assert_can_verify_payment(inv)

    before_status = inv.status
    if inv.status != "paid":
        apply_status(inv, "paid")
        inv.paid_at = datetime.now(timezone.utc)
        remaining = max(
            0,
            int(inv.total_paise or 0) - int(getattr(inv, "amount_paid_paise", 0) or 0),
        )
        if remaining > 0:
            db.add(
                _record_invoice_payment(
                    inv=inv,
                    amount_paise=remaining,
                    method="upi",
                    reference=None,
                    note=getattr(inv, "payment_proof_note", None) or "Payment verified",
                    recorded_by=current_user.id,
                    paid_at=inv.paid_at,
                )
            )
    inv.payment_verified_at = datetime.now(timezone.utc)
    inv.payment_verified_by = current_user.id

    if inv.ledger == "maintenance" and inv.project_id and inv.covers_to:
        await _hold_maintenance_emails(db, inv.project_id, inv.covers_to)

    act_stmt = select(LateFeeAction).where(
        LateFeeAction.invoice_id == invoice_id,
        LateFeeAction.status.in_(["pending", "scheduled"]),
    )
    for act in (await db.execute(act_stmt)).scalars().all():
        act.status = "waived"
        act.decided_by = current_user.id
        act.decided_at = datetime.now(timezone.utc)

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    if st:
        from ..services.invoice_snapshot import freeze_document_snapshot

        freeze_document_snapshot(
            inv,
            client=inv.client,
            settings=st,
            project=inv.project,
            parent_invoice=_loaded_parent_invoice(inv),
            force=True,
        )

    await write_audit(
        db,
        current_user,
        "invoices",
        inv.id,
        "verify_payment",
        before={"status": before_status},
        after={
            "status": inv.status,
            "paid_at": inv.paid_at,
            "payment_verified_at": inv.payment_verified_at,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(inv)

    await _send_paid_receipt_email(db, inv, st)
    return format_invoice_response(inv, st)


# ----------------- Late Fee Actions (Founder CEO Only) -----------------

@router.get("/late-fee-actions/pending", response_model=List[LateFeeActionResponse])
async def list_pending_late_fee_actions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = (
        select(LateFeeAction)
        .options(selectinload(LateFeeAction.invoice).selectinload(Invoice.client))
        .where(LateFeeAction.status == "pending")
        .order_by(LateFeeAction.created_at.desc())
    )
    actions = (await db.execute(stmt)).scalars().all()
    results = []
    for a in actions:
        resp = LateFeeActionResponse.model_validate(a)
        if a.invoice:
            resp.invoice_number = a.invoice.invoice_number
            if a.invoice.client:
                resp.client_name = a.invoice.client.name
        results.append(resp)
    return results

async def execute_late_fee_decision(
    action: LateFeeAction,
    payload: LateFeeDecisionRequest,
    db: AsyncSession,
    current_user: User
) -> LateFeeActionResponse:
    inv_stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project))
        .where(Invoice.id == action.invoice_id)
    )
    inv = (await db.execute(inv_stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Associated invoice not found")

    # Calculate fee
    if payload.fee_type == "percent":
        calc_fee = int(round(inv.total_paise * (payload.fee_value / 100.0)))
    else:
        calc_fee = int(round(payload.fee_value * 100))
    action.fee_paise = max(0, calc_fee)
    action.fee_type = payload.fee_type
    action.fee_value = payload.fee_value
    action.reason = payload.reason or "Payment not received by due date"
    action.repeat_every_days = payload.repeat_every_days
    if payload.reminder_every_hours is not None:
        action.reminder_every_hours = payload.reminder_every_hours
        action.next_reminder_at = datetime.now(timezone.utc) + timedelta(
            hours=int(payload.reminder_every_hours)
        )
    action.decided_by = current_user.id
    action.decided_at = datetime.now(timezone.utc)

    if payload.action == "waive":
        action.status = "waived"
        action.next_reminder_at = None
        await db.commit()
        await db.refresh(action)

    elif payload.action == "extend":
        # Give extra payment time — clear overdue, push due date
        days = int(payload.extend_days or 5)
        inv.due_date = max(inv.due_date, date.today()) + timedelta(days=days)
        if inv.status == "overdue":
            inv.status = "sent"
        inv.overdue_notified_at = None
        action.status = "extended"
        action.reason = payload.reason or f"Extended due date by {days} day(s)"
        # Keep overdue reminder cadence if founder set hours
        if action.reminder_every_hours:
            action.next_reminder_at = datetime.now(timezone.utc) + timedelta(
                hours=int(action.reminder_every_hours)
            )
        else:
            action.next_reminder_at = None
        await db.commit()
        await db.refresh(action)

    elif payload.action == "schedule":
        action.status = "scheduled"
        action.send_on = payload.send_on or (date.today() + timedelta(days=3))
        await db.commit()
        await db.refresh(action)

    elif payload.action == "send_now":
        # Create and emit late-fee invoice immediately
        st_stmt = select(Settings).where(Settings.id == "default").with_for_update()
        st = (await db.execute(st_stmt)).scalar_one_or_none()
        if not st:
            raise HTTPException(status_code=500, detail="Default settings missing")

        from ..services.invoice_numbers import assign_unique_invoice_number

        subtotal = action.fee_paise
        tax_rate = effective_tax_rate(st)
        tax_paise = compute_tax_paise(subtotal, tax_rate)
        total_paise = subtotal + tax_paise
        today = date.today()

        lf_invoice = Invoice(
            invoice_number="",
            invoice_type="late_fee",
            parent_invoice_id=inv.id,
            client_id=inv.client_id,
            project_id=inv.project_id,
            status="draft",
            issue_date=today,
            due_date=today + timedelta(days=5),
            subtotal_paise=subtotal,
            tax_rate_percent=tax_rate,
            tax_paise=tax_paise,
            total_paise=total_paise,
            email_status="pending",
            notes=f"Late payment fee for Invoice {inv.invoice_number}. {action.reason}",
            gst_enabled=getattr(inv, "gst_enabled", None),
        )
        await assign_unique_invoice_number(
            db,
            lf_invoice,
            invoice_type="late_fee",
            issue_date=today,
            settings_row=st,
        )

        db.add(InvoiceItem(
            invoice_id=lf_invoice.id,
            description=f"Late Payment Fee for {inv.invoice_number}: {action.reason}",
            quantity=1.0,
            unit_rate_paise=subtotal,
            amount_paise=subtotal
        ))

        action.status = "sent"
        action.late_fee_invoice_id = lf_invoice.id
        await db.commit()
        await db.refresh(action)

        # Attempt sending PDF (respect automatic-send toggle)
        if (
            st.smtp_host
            and st.smtp_from_email
            and bool(getattr(st, "smtp_auto_send_enabled", True))
            and inv.client
            and inv.client.email
        ):
            try:
                pdf_bytes = generate_invoice_pdf(
                    invoice=lf_invoice,
                    client=inv.client,
                    settings=st,
                    items=[InvoiceItem(description=f"Late Fee for {inv.invoice_number}", quantity=1.0, unit_rate_paise=subtotal, amount_paise=subtotal)],
                    project=inv.project,
                    parent_invoice=inv
                )
                from ..services.email_html import build_invoice_email
                from ..services.invoice_mail import invoice_mail_targets

                to_email, cc_email = invoice_mail_targets(inv.client, st)
                subject, html, plain = build_invoice_email(
                    company_name=st.company_name,
                    company_address=st.company_address or "",
                    company_gstin=st.company_gstin or "",
                    company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
                    client_name=inv.client.name,
                    invoice_number=lf_invoice.invoice_number,
                    total_paise=total_paise,
                    due_date=lf_invoice.due_date,
                    issue_date=lf_invoice.issue_date,
                    bank_upi_details=st.bank_upi_details or "",
                    invoice_type="late_fee",
                    notes=action.reason or "",
                    variant="late_fee",
                )
                await send_email_async(
                    to_email=to_email,
                    subject=subject,
                    body=html,
                    plain_body=plain,
                    settings=st,
                    attachment_bytes=pdf_bytes,
                    attachment_filename=f"{lf_invoice.invoice_number}.pdf",
                    cc_email=cc_email,
                    is_html=True,
                )
                lf_invoice.status = "sent"
                lf_invoice.email_status = "sent"
                await db.commit()
            except Exception:
                pass
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid action. Must be 'send_now', 'schedule', 'waive', or 'extend'",
        )

    resp = LateFeeActionResponse.model_validate(action)
    resp.invoice_number = inv.invoice_number
    if inv.client:
        resp.client_name = inv.client.name
    return resp

@router.post("/late-fee-actions/{action_id}/decide", response_model=LateFeeActionResponse)
async def decide_late_fee_action_by_id(
    action_id: str,
    payload: LateFeeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    act_stmt = (
        select(LateFeeAction)
        .options(selectinload(LateFeeAction.invoice).selectinload(Invoice.client))
        .where(LateFeeAction.id == action_id)
    )
    action = (await db.execute(act_stmt)).scalars().first()
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Late fee action not found")

    return await execute_late_fee_decision(action, payload, db, current_user)

@router.post("/{invoice_id}/late-fee-action", response_model=LateFeeActionResponse)
async def decide_late_fee_action(
    invoice_id: str,
    payload: LateFeeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    inv_stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(inv_stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    # Find pending action or create one
    act_stmt = select(LateFeeAction).where(
        LateFeeAction.invoice_id == invoice_id,
        LateFeeAction.status == "pending"
    )
    action = (await db.execute(act_stmt)).scalars().first()
    if not action:
        action = LateFeeAction(
            invoice_id=inv.id,
            fee_paise=0,
            fee_type=payload.fee_type,
            fee_value=payload.fee_value,
            reason=payload.reason,
            send_on=date.today() + timedelta(days=3),
            status="pending"
        )
        db.add(action)
        await db.flush()

    return await execute_late_fee_decision(action, payload, db, current_user)
