"""High-level PDF engine: render, preview, concurrency, temp cleanup."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import asset_manager, pdf_operations, renderer
from .exceptions import (
    GenerationBusyError,
    GenerationTimeoutError,
    PdfEngineError,
    PreviewValidationError,
    RenderError,
)
from .schemas import (
    InvoicePreviewPayload,
    PayslipPreviewPayload,
    PurchaseOrderPreviewPayload,
    QuotationPreviewPayload,
    ReportPreviewPayload,
)

logger = logging.getLogger("kr.pdf")

_RENDER_SEM = asyncio.Semaphore(2)
_RENDER_TIMEOUT_SEC = 45.0
_PREVIEW_TTL_SEC = 3600


def cleanup_temp_pdfs(*, older_than_sec: int = _PREVIEW_TTL_SEC) -> int:
    """Delete stale preview PDFs under uploads/tmp/pdf/."""
    tmp = asset_manager.tmp_dir()
    cutoff = time.time() - older_than_sec
    removed = 0
    for path in tmp.glob("*.pdf"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("pdf_temp_cleanup removed=%s", removed)
    return removed


async def render_document_async(
    doc_type: str,
    context: dict[str, Any],
    *,
    template_key: Optional[str] = None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> bytes:
    if _RENDER_SEM.locked() and _RENDER_SEM._value == 0:  # noqa: SLF001
        # Soft signal when both slots busy; still queue with timeout
        pass

    async with _RENDER_SEM:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    renderer.render_template_pdf,
                    doc_type,
                    context,
                    template_key=template_key,
                    paper_size=paper_size,
                    orientation=orientation,
                ),
                timeout=_RENDER_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise GenerationTimeoutError() from exc


def render_document_sync(
    doc_type: str,
    context: dict[str, Any],
    *,
    template_key: Optional[str] = None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> bytes:
    """Sync render for call sites that are not async (invoice facade)."""
    return renderer.render_template_pdf(
        doc_type,
        context,
        template_key=template_key,
        paper_size=paper_size,
        orientation=orientation,
    )


def _validate_preview(doc_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        if doc_type == "invoice" or doc_type == "receipt":
            return InvoicePreviewPayload.model_validate(payload).model_dump()
        if doc_type == "payslip":
            return PayslipPreviewPayload.model_validate(payload).model_dump()
        if doc_type == "quotation":
            return QuotationPreviewPayload.model_validate(payload).model_dump()
        if doc_type == "purchase_order":
            return PurchaseOrderPreviewPayload.model_validate(payload).model_dump()
        if doc_type == "report":
            return ReportPreviewPayload.model_validate(payload).model_dump()
    except Exception as exc:
        raise PreviewValidationError(str(exc)) from exc
    raise PreviewValidationError(f"Unsupported doc_type: {doc_type}")


async def preview_document(
    doc_type: str,
    payload: dict[str, Any],
    *,
    template_key: Optional[str] = None,
    paper_size: Optional[str] = "a4",
    orientation: Optional[str] = "portrait",
    persist_temp: bool = True,
) -> tuple[bytes, Optional[str]]:
    """
    Render a preview PDF from a validated payload.
    Never touches invoice numbers or financial records.
    Returns (pdf_bytes, optional temp filename).
    """
    cleanup_temp_pdfs()
    ctx = _validate_preview(doc_type, payload)
    ctx.setdefault("watermark", "PREVIEW")
    ctx.setdefault("is_preview", True)
    pdf = await render_document_async(
        doc_type,
        ctx,
        template_key=template_key,
        paper_size=paper_size,
        orientation=orientation,
    )
    temp_name = None
    if persist_temp:
        temp_name = f"preview_{doc_type}_{uuid.uuid4().hex[:12]}.pdf"
        path = asset_manager.tmp_dir() / temp_name
        path.write_bytes(pdf)
    return pdf, temp_name


def read_temp_pdf(filename: str) -> bytes:
    safe = asset_manager.sanitize_filename(filename)
    path = asset_manager.resolve_safe_path(asset_manager.tmp_dir() / safe)
    if not path.is_file():
        raise PdfEngineError("Temporary PDF not found")
    return path.read_bytes()
