"""PDF engine API — generate, preview, templates registry, pypdf operations."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.database import not_deleted
from ..core.dependencies import get_db, require_founder_ceo, require_staff
from ..models import Invoice, Payslip, PdfTemplate, Settings, User
from ..services.file_mime import attachment_content_disposition, inline_content_disposition
from ..services.pdf import generate_invoice_pdf, generate_payslip_pdf
from ..services.pdf_engine import (
    PdfEngineError,
    cleanup_temp_pdfs,
    preview_document,
)
from ..services.pdf_engine.asset_manager import sanitize_filename
from ..services.pdf_engine.exceptions import (
    PdfOperationError,
    PreviewValidationError,
    TemplateNotFoundError,
)
from ..services.pdf_engine.pdf_operations import (
    MAX_PDF_BYTES,
    add_text_watermark,
    encrypt_pdf,
    get_info,
    merge_pdfs,
    split_pdf,
    validate_pdf,
)
from ..services.pdf_engine.schemas import (
    GenerateInvoiceRequest,
    GeneratePayslipRequest,
    GenerateReceiptRequest,
    PdfInfoOut,
    PdfTemplateCreate,
    PdfTemplateOut,
    PdfTemplateUpdate,
    PreviewRequest,
)
from ..services.pdf_engine.template_manager import list_disk_designs, resolve_template

logger = logging.getLogger("kr.pdf")

router = APIRouter(prefix="/pdf", tags=["PDF"])


def _pdf_response(
    data: bytes,
    filename: str,
    *,
    download: bool = True,
) -> Response:
    safe = sanitize_filename(filename)
    disposition = (
        attachment_content_disposition(safe)
        if download
        else inline_content_disposition(safe)
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


def _map_engine_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PreviewValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message)
    if isinstance(exc, TemplateNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, PdfOperationError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message)
    if isinstance(exc, PdfEngineError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message)
    logger.exception("Unexpected PDF engine error")
    return HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="PDF processing failed",
    )


async def _load_invoice(db: AsyncSession, invoice_id: str) -> Invoice:
    from ..routers.invoices import _invoice_eager_options

    stmt = (
        select(Invoice)
        .options(*_invoice_eager_options(include_parent=True))
        .where(Invoice.id == invoice_id, not_deleted(Invoice))
    )
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


async def _settings(db: AsyncSession) -> Optional[Settings]:
    return (
        await db.execute(select(Settings).where(Settings.id == "default"))
    ).scalars().first()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


@router.post("/generate/invoice")
async def generate_invoice(
    body: GenerateInvoiceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Generate invoice PDF from stored invoice (no financial mutations beyond snapshot ensure)."""
    from ..routers.invoices import _loaded_parent_invoice
    from ..services.invoice_snapshot import ensure_issued_snapshot

    inv = await _load_invoice(db, body.invoice_id)
    settings_obj = await _settings(db)
    before = getattr(inv, "document_snapshot", None)
    ensure_issued_snapshot(
        inv,
        client=inv.client,
        settings=settings_obj,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )
    if before is not getattr(inv, "document_snapshot", None):
        await db.commit()

    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=settings_obj,
        items=inv.items,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
        paper_size=body.paper_size,
        orientation=body.orientation,
    )
    name = sanitize_filename(f"{inv.invoice_number or inv.id}.pdf")
    return _pdf_response(pdf_bytes, name)


@router.post("/generate/receipt")
async def generate_receipt(
    body: GenerateReceiptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    from ..routers.invoices import _loaded_parent_invoice
    from ..services.invoice_snapshot import ensure_issued_snapshot

    inv = await _load_invoice(db, body.invoice_id)
    if (inv.status or "").lower() != "paid":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Receipt PDF requires a paid invoice",
        )
    settings_obj = await _settings(db)
    before = getattr(inv, "document_snapshot", None)
    ensure_issued_snapshot(
        inv,
        client=inv.client,
        settings=settings_obj,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
    )
    if before is not getattr(inv, "document_snapshot", None):
        await db.commit()

    pdf_bytes = generate_invoice_pdf(
        invoice=inv,
        client=inv.client,
        settings=settings_obj,
        items=inv.items,
        project=inv.project,
        parent_invoice=_loaded_parent_invoice(inv),
        paper_size=body.paper_size,
        orientation=body.orientation,
    )
    name = sanitize_filename(f"Receipt_{inv.invoice_number or inv.id}.pdf")
    return _pdf_response(pdf_bytes, name)


@router.post("/generate/payslip")
async def generate_payslip(
    body: GeneratePayslipRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    slip = (
        await db.execute(
            select(Payslip)
            .options(selectinload(Payslip.user))
            .where(Payslip.id == body.payslip_id, not_deleted(Payslip))
        )
    ).scalars().first()
    if not slip:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payslip not found")
    settings_obj = await _settings(db)
    user = slip.user
    pdf_bytes = generate_payslip_pdf(
        employee_name=(user.full_name if user else "Employee"),
        employee_email=(user.email if user else ""),
        job_title=getattr(user, "job_title", None) if user else None,
        department=getattr(user, "department", None) if user else None,
        month_key=slip.month_key,
        gross_paise=slip.gross_paise,
        deductions_paise=slip.deductions_paise,
        net_paise=slip.net_paise,
        note=slip.note,
        company_name=(
            getattr(settings_obj, "company_name", None) if settings_obj else None
        )
        or "KR Web Solution",
        company_address=getattr(settings_obj, "company_address", None)
        if settings_obj
        else None,
        company_logo_url=getattr(settings_obj, "company_logo_url", None)
        if settings_obj
        else None,
        paper_size=body.paper_size,
        orientation=body.orientation,
    )
    return _pdf_response(pdf_bytes, f"payslip_{slip.month_key}.pdf")


# ---------------------------------------------------------------------------
# Preview (no financial side effects)
# ---------------------------------------------------------------------------


@router.post("/preview/{doc_type}")
async def preview_pdf(
    doc_type: str,
    body: PreviewRequest,
    current_user: User = Depends(require_founder_ceo),
):
    allowed = {
        "invoice",
        "receipt",
        "payslip",
        "quotation",
        "purchase_order",
        "report",
    }
    doc = (doc_type or "").strip().lower()
    if doc not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported doc_type. Use one of: {', '.join(sorted(allowed))}",
        )
    try:
        pdf_bytes, _temp = await preview_document(
            doc,
            body.payload or {},
            template_key=body.template_key,
            paper_size=body.paper_size or "a4",
            orientation=body.orientation or "portrait",
            persist_temp=True,
        )
    except Exception as exc:
        raise _map_engine_error(exc) from exc

    return _pdf_response(pdf_bytes, f"preview_{doc}.pdf", download=False)


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=list[PdfTemplateOut])
async def list_templates(
    doc_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    stmt = select(PdfTemplate)
    if doc_type:
        stmt = stmt.where(PdfTemplate.doc_type == doc_type.strip().lower())
    stmt = stmt.order_by(PdfTemplate.doc_type, PdfTemplate.design_key)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.get("/templates/disk")
async def list_disk_templates(
    current_user: User = Depends(require_founder_ceo),
):
    """List filesystem designs under app/templates/pdf/."""
    return list_disk_designs()


@router.get("/templates/{template_id}", response_model=PdfTemplateOut)
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    row = (
        await db.execute(select(PdfTemplate).where(PdfTemplate.id == template_id))
    ).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Template not found")
    # Verify design exists on disk
    try:
        resolve_template(row.doc_type, row.design_key)
    except TemplateNotFoundError:
        logger.warning("Registry points at missing design %s", row.design_key)
    return row


@router.post("/templates", response_model=PdfTemplateOut, status_code=201)
async def create_template(
    body: PdfTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    # Design must exist on disk
    try:
        resolve_template(body.doc_type, body.design_key)
    except TemplateNotFoundError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    if body.is_default:
        await db.execute(
            update(PdfTemplate)
            .where(PdfTemplate.doc_type == body.doc_type)
            .values(is_default=False)
        )

    row = PdfTemplate(
        doc_type=body.doc_type,
        design_key=body.design_key,
        version=body.version,
        label=body.label,
        active=body.active,
        is_default=body.is_default,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.put("/templates/{template_id}", response_model=PdfTemplateOut)
async def update_template(
    template_id: str,
    body: PdfTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    row = (
        await db.execute(select(PdfTemplate).where(PdfTemplate.id == template_id))
    ).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Template not found")
    if body.label is not None:
        row.label = body.label
    if body.active is not None:
        row.active = body.active
    if body.is_default is True:
        await db.execute(
            update(PdfTemplate)
            .where(PdfTemplate.doc_type == row.doc_type)
            .values(is_default=False)
        )
        row.is_default = True
    elif body.is_default is False:
        row.is_default = False
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    row = (
        await db.execute(select(PdfTemplate).where(PdfTemplate.id == template_id))
    ).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Template not found")
    await db.delete(row)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# pypdf operations
# ---------------------------------------------------------------------------


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF exceeds maximum allowed size",
        )
    if not validate_pdf(data):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid PDF upload")
    return data


@router.post("/merge")
async def merge_pdf_endpoint(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_founder_ceo),
):
    if len(files) < 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Upload at least two PDF files"
        )
    parts: list[bytes] = []
    for f in files:
        parts.append(await _read_upload(f))
    try:
        out = merge_pdfs(parts)
    except Exception as exc:
        raise _map_engine_error(exc) from exc
    return _pdf_response(out, "merged.pdf")


@router.post("/split")
async def split_pdf_endpoint(
    file: UploadFile = File(...),
    ranges: str = Form(...),
    current_user: User = Depends(require_founder_ceo),
):
    data = await _read_upload(file)
    try:
        out = split_pdf(data, ranges)
    except Exception as exc:
        raise _map_engine_error(exc) from exc
    return _pdf_response(out, "split.pdf")


@router.post("/watermark")
async def watermark_pdf_endpoint(
    file: UploadFile = File(...),
    text: str = Form("DRAFT"),
    opacity: float = Form(0.15),
    current_user: User = Depends(require_founder_ceo),
):
    data = await _read_upload(file)
    try:
        out = add_text_watermark(data, text, opacity=opacity)
    except Exception as exc:
        raise _map_engine_error(exc) from exc
    return _pdf_response(out, "watermarked.pdf")


@router.post("/encrypt")
async def encrypt_pdf_endpoint(
    file: UploadFile = File(...),
    user_password: str = Form(...),
    owner_password: Optional[str] = Form(None),
    current_user: User = Depends(require_founder_ceo),
):
    data = await _read_upload(file)
    try:
        out = encrypt_pdf(data, user_password, owner_password)
    except Exception as exc:
        raise _map_engine_error(exc) from exc
    return _pdf_response(out, "encrypted.pdf")


@router.post("/info", response_model=PdfInfoOut)
async def pdf_info_endpoint(
    file: UploadFile = File(...),
    current_user: User = Depends(require_staff),
):
    data = await _read_upload(file)
    try:
        info = get_info(data)
    except Exception as exc:
        raise _map_engine_error(exc) from exc
    return PdfInfoOut(**info)


@router.post("/cleanup-temp")
async def cleanup_temp(
    current_user: User = Depends(require_founder_ceo),
):
    removed = cleanup_temp_pdfs()
    return {"removed": removed}
