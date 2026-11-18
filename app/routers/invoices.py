from datetime import date, datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, get_current_user, require_founder_ceo
from ..models import Invoice, InvoiceItem, Client, Project, Settings, EmailTemplate, RecurringInvoice, User, LateFeeAction
from ..schemas import (
    InvoiceResponse, InvoiceCreate, InvoiceUpdate, InvoiceItemResponse,
    RecurringInvoiceResponse, RecurringInvoiceCreate, RecurringInvoiceUpdate,
    LateFeeActionResponse, LateFeeDecisionRequest, CreditNoteCreate,
)
from ..services.pdf import generate_invoice_pdf, format_inr
from ..services.email import send_email_async
from ..services.tax_payroll import (
    effective_tax_rate,
    compute_tax_paise,
    gst_is_enabled,
    resolve_payment_upi,
)

router = APIRouter(prefix="/invoices", tags=["Invoices"])

async def _hold_maintenance_emails(db: AsyncSession, project_id: str, covers_to: date) -> None:
    """Push the next automatic maintenance email to the day after this coverage."""
    rec = (
        await db.execute(select(RecurringInvoice).where(RecurringInvoice.project_id == project_id))
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


def format_invoice_response(inv: Invoice, settings: Optional[Settings] = None) -> InvoiceResponse:
    # Query-time overdue calculation
    effective_status = inv.status
    if effective_status == "sent" and inv.due_date and inv.due_date < date.today():
        effective_status = "overdue"

    pay = resolve_payment_upi(settings, inv) if settings else {"upi_id": None, "upi_qr_url": None}

    resp = InvoiceResponse(
        id=inv.id,
        invoice_number=inv.invoice_number,
        invoice_type=inv.invoice_type,
        client_id=inv.client_id,
        client_name=inv.client.name if inv.client else None,
        project_id=inv.project_id,
        project_name=inv.project.name if inv.project else None,
        status=effective_status,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        subtotal_paise=inv.subtotal_paise,
        tax_rate_percent=float(inv.tax_rate_percent),
        tax_paise=inv.tax_paise,
        total_paise=inv.total_paise,
        notes=inv.notes,
        upi_id_override=getattr(inv, "upi_id_override", None),
        upi_qr_url=getattr(inv, "upi_qr_url", None),
        paid_at=inv.paid_at,
        period_key=inv.period_key,
        parent_invoice_id=inv.parent_invoice_id,
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
        created_at=inv.created_at,
        items=[InvoiceItemResponse.model_validate(it) for it in (inv.items or [])],
        payment_upi_id=pay.get("upi_id"),
        payment_upi_qr_url=pay.get("upi_qr_url"),
        payment_bank_beneficiary_name=getattr(settings, "bank_beneficiary_name", None) if settings else None,
        payment_bank_name=getattr(settings, "bank_name", None) if settings else None,
        payment_bank_account_number=getattr(settings, "bank_account_number", None) if settings else None,
        payment_bank_ifsc=getattr(settings, "bank_ifsc", None) if settings else None,
        payment_bank_upi_details=getattr(settings, "bank_upi_details", None) if settings else None,
        gst_enabled=gst_is_enabled(settings) if settings else False,
    )
    return resp

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

    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
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

    formatted = [format_invoice_response(inv, st) for inv in invoices]
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
        .options(selectinload(RecurringInvoice.client))
        .order_by(RecurringInvoice.created_at.desc())
    )
    res = await db.execute(stmt)
    recs = res.scalars().all()

    output = []
    for r in recs:
        resp = RecurringInvoiceResponse.model_validate(r)
        resp.client_name = r.client.name if r.client else None
        output.append(resp)
    return output

@router.post("/recurring", response_model=RecurringInvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_recurring_invoice(
    payload: RecurringInvoiceCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    rec = RecurringInvoice(**payload.model_dump())
    db.add(rec)
    await db.commit()
    await db.refresh(rec)

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
    stmt = select(RecurringInvoice).options(selectinload(RecurringInvoice.client)).where(RecurringInvoice.id == recurring_id)
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
    stmt = select(RecurringInvoice).where(RecurringInvoice.id == recurring_id)
    ref = (await db.execute(stmt)).scalars().first()
    if not ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring invoice not found")
    await db.delete(ref)
    await db.commit()
    return {"message": "Recurring invoice schedule deleted successfully"}

@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workers cannot view invoices")

    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    res = await db.execute(stmt)
    inv = res.scalars().first()

    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if current_user.role == "client":
        if inv.client_id != current_user.client_id or inv.status == "draft":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    st = await _load_settings(db)
    return format_invoice_response(inv, st)

@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    # Verify client exists
    c_res = await db.execute(select(Client).where(Client.id == payload.client_id))
    client = c_res.scalars().first()
    if not client:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client not found")

    # ATOMIC SEQUENCE INCREMENT with SELECT ... FOR UPDATE
    settings_stmt = select(Settings).where(Settings.id == "default").with_for_update()
    settings_res = await db.execute(settings_stmt)
    st = settings_res.scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settings record not found")

    from ..services.invoice_numbers import allocate_invoice_number
    from ..services.project_money import ledger_for

    raw_type = payload.invoice_type or "custom_dev"
    if raw_type == "project":
        raw_type = "custom_dev"
    if raw_type == "maintenance":
        raw_type = "amc"

    try:
        invoice_number = await allocate_invoice_number(
            db,
            invoice_type=raw_type,
            issue_date=payload.issue_date,
            settings_row=st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # Calculate subtotal from items; GST only when enabled in Settings
    subtotal_paise = sum(int(round(float(item.quantity) * item.unit_rate_paise)) for item in payload.items)
    tax_rate = effective_tax_rate(st, payload.tax_rate_percent)
    tax_paise = compute_tax_paise(subtotal_paise, tax_rate)
    total_paise = subtotal_paise + tax_paise

    invoice = Invoice(
        invoice_number=invoice_number,
        invoice_type=raw_type,
        ledger=ledger_for(raw_type),
        client_id=payload.client_id,
        project_id=payload.project_id,
        status="draft",
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        subtotal_paise=subtotal_paise,
        tax_rate_percent=tax_rate,
        tax_paise=tax_paise,
        total_paise=total_paise,
        notes=payload.notes,
        upi_id_override=(payload.upi_id_override or None),
        upi_qr_url=(payload.upi_qr_url or None),
        period_months=payload.period_months or 1,
        covers_from=payload.covers_from,
        covers_to=payload.covers_to,
        monthly_rate_paise=payload.monthly_rate_paise,
    )
    db.add(invoice)
    await db.flush()

    for item in payload.items:
        amt = int(round(float(item.quantity) * item.unit_rate_paise))
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            description=item.description,
            quantity=item.quantity,
            unit_rate_paise=item.unit_rate_paise,
            amount_paise=amt
        ))

    if raw_type == "amc" and payload.project_id and payload.covers_to:
        await _hold_maintenance_emails(db, payload.project_id, payload.covers_to)

    await db.commit()

    # Re-fetch with loaded items
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice.id)
    )
    res = await db.execute(stmt)
    created_inv = res.scalars().first()
    return format_invoice_response(created_inv, st)

@router.post("/{invoice_id}/cancel", response_model=InvoiceResponse)
async def cancel_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if inv.status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a paid invoice. Issue a credit note instead.",
        )
    if inv.status in ("cancelled", "void"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invoice is already cancelled")
    if inv.invoice_type == "credit_note":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cancel the credit note by deleting it if still unpaid")

    inv.status = "cancelled"
    await db.commit()
    await db.refresh(inv)
    st = await _load_settings(db)
    return format_invoice_response(inv, st)


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
        .where(Invoice.id == invoice_id)
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

    tax_rate = effective_tax_rate(st, float(parent.tax_rate_percent or 0))
    tax_paise = compute_tax_paise(credit_sub, tax_rate)
    total_paise = credit_sub + tax_paise

    from ..services.invoice_numbers import allocate_invoice_number
    from ..services.project_money import ledger_for

    today = date.today()
    try:
        invoice_number = await allocate_invoice_number(
            db,
            invoice_type="credit_note",
            issue_date=today,
            settings_row=st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    reason = (payload.reason or "").strip()
    notes = f"Credit note for {parent.invoice_number}"
    if reason:
        notes = f"{notes}. {reason}"

    # Paid parent → credit note marked paid (refund applied). Unpaid → sent credit reduces outstanding.
    cn_status = "paid" if parent.status == "paid" else "sent"
    credit = Invoice(
        invoice_number=invoice_number,
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
        notes=notes,
        paid_at=datetime.now(timezone.utc) if cn_status == "paid" else None,
        upi_id_override=parent.upi_id_override,
        upi_qr_url=parent.upi_qr_url,
    )
    db.add(credit)
    await db.flush()
    db.add(
        InvoiceItem(
            invoice_id=credit.id,
            description=f"Credit against {parent.invoice_number}",
            quantity=1,
            unit_rate_paise=credit_sub,
            amount_paise=credit_sub,
        )
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


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: str,
    payload: InvoiceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    from ..models import InvoiceAuditLog

    stmt = select(Invoice).options(selectinload(Invoice.items)).where(Invoice.id == invoice_id)
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if inv.status in ("cancelled", "void"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit a cancelled invoice")
    if inv.status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Paid invoices are locked. Issue a credit note instead.",
        )

    old_subtotal = int(inv.subtotal_paise or 0)
    was_sent = inv.status in ("sent", "overdue")
    update_dict = payload.model_dump(exclude_unset=True)
    new_items = update_dict.pop("items", None)

    for k, v in update_dict.items():
        setattr(inv, k, v)

    if new_items is not None:
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
                amount_paise=amt
            ))
        inv.subtotal_paise = subtotal_paise

    st = await _load_settings(db)
    if "tax_rate_percent" in update_dict or new_items is not None:
        tax_rate = effective_tax_rate(st, float(inv.tax_rate_percent or 0))
        inv.tax_rate_percent = tax_rate
        inv.tax_paise = compute_tax_paise(int(inv.subtotal_paise or 0), tax_rate)
        inv.total_paise = int(inv.subtotal_paise or 0) + inv.tax_paise

    if inv.invoice_type in ("maintenance", "amc"):
        inv.invoice_type = "amc"
        inv.ledger = "maintenance"
        if inv.project_id and inv.covers_to:
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

    await db.commit()

    # Re-fetch
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    res = await db.execute(stmt)
    updated_inv = res.scalars().first()
    return format_invoice_response(updated_inv, st)

@router.delete("/{invoice_id}", status_code=status.HTTP_200_OK)
async def delete_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if inv.status == "paid":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete paid invoice")

    await db.delete(inv)
    await db.commit()
    return {"message": "Invoice deleted successfully"}

@router.get("/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "worker":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if current_user.role == "client":
        if inv.client_id != current_user.client_id or inv.status == "draft":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    st_res = await db.execute(select(Settings).where(Settings.id == "default"))
    settings_obj = st_res.scalars().first()

    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=settings_obj,
        items=inv.items,
        project=inv.project
    )

    filename = f"{inv.invoice_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"'
        }
    )

@router.post("/{invoice_id}/send")
async def send_invoice_email(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    if not inv.client or not inv.client.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client has no email address")

    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()

    if not st or not st.smtp_host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SMTP is not configured in settings")

    # Generate PDF (paid invoices get the PAID stamp from pdf renderer)
    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=st,
        items=inv.items,
        project=inv.project
    )

    from ..services.email_html import compose_invoice_client_email

    kind, subject, html, plain, attachment_name = compose_invoice_client_email(
        company_name=st.company_name,
        company_address=st.company_address or "",
        company_gstin=st.company_gstin or "",
        company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
        client_name=inv.client.primary_contact_name or inv.client.name,
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
            to_email=inv.client.email,
            subject=subject,
            body=html,
            plain_body=plain,
            settings=st,
            attachment_bytes=pdf_bytes,
            attachment_filename=attachment_name,
            is_html=True,
            cc_email=st.invoice_cc_email,
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
    # Never flip a paid invoice back to "sent"
    if inv.status == "draft":
        inv.status = "sent"
    await db.commit()

    kind_label = {
        "paid": "paid receipt",
        "overdue": "overdue notice",
        "late_fee": "late fee invoice",
        "sent": "invoice",
        "reminder": "payment reminder",
    }.get(kind, "invoice")
    return {
        "message": f"{kind_label.capitalize()} for {inv.invoice_number} sent to {inv.client.email}",
        "email_kind": kind,
    }

@router.post("/{invoice_id}/pay", response_model=InvoiceResponse)
@router.post("/{invoice_id}/mark-paid", response_model=InvoiceResponse)
async def mark_invoice_as_paid(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    stmt = (
        select(Invoice)
        .options(selectinload(Invoice.client), selectinload(Invoice.project), selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    inv.status = "paid"
    inv.paid_at = datetime.now(timezone.utc)
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

    await db.commit()
    await db.refresh(inv)

    # Send paid receipt email with PAID stamp PDF (if client has email and SMTP is configured)
    st = (await db.execute(select(Settings).where(Settings.id == "default"))).scalars().first()
    if st and st.smtp_host and st.smtp_from_email and inv.client and inv.client.email:
        pdf_bytes = generate_invoice_pdf(
            invoice=inv,
            client=inv.client,
            settings=st,
            items=inv.items,
            project=inv.project
        )
        from ..services.email_html import compose_invoice_client_email

        _kind, subject, html, plain, attachment_name = compose_invoice_client_email(
            company_name=st.company_name,
            company_address=st.company_address or "",
            company_gstin=st.company_gstin or "",
            company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
            client_name=inv.client.primary_contact_name or inv.client.name,
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
                to_email=inv.client.email,
                subject=subject,
                body=html,
                plain_body=plain,
                settings=st,
                attachment_bytes=pdf_bytes,
                attachment_filename=attachment_name,
                cc_email=st.invoice_cc_email,
                is_html=True,
            )
            inv.email_status = "sent"
            inv.last_emailed_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception:
            # Paid status is already committed; log notification warning
            pass

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

        from ..services.invoice_numbers import allocate_invoice_number

        lf_number = await allocate_invoice_number(
            db,
            invoice_type="late_fee",
            issue_date=date.today(),
            settings_row=st,
        )

        subtotal = action.fee_paise
        tax_rate = effective_tax_rate(st)
        tax_paise = compute_tax_paise(subtotal, tax_rate)
        total_paise = subtotal + tax_paise
        today = date.today()

        lf_invoice = Invoice(
            invoice_number=lf_number,
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
            notes=f"Late payment fee for Invoice {inv.invoice_number}. {action.reason}"
        )
        db.add(lf_invoice)
        await db.flush()

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

        # Attempt sending PDF
        if st.smtp_host and st.smtp_from_email and inv.client and inv.client.email:
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

                subject, html, plain = build_invoice_email(
                    company_name=st.company_name,
                    company_address=st.company_address or "",
                    company_gstin=st.company_gstin or "",
                    company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
                    client_name=inv.client.primary_contact_name or inv.client.name,
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
                    to_email=inv.client.email,
                    subject=subject,
                    body=html,
                    plain_body=plain,
                    settings=st,
                    attachment_bytes=pdf_bytes,
                    attachment_filename=f"{lf_invoice.invoice_number}.pdf",
                    cc_email=st.invoice_cc_email,
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
        .where(Invoice.id == invoice_id)
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
