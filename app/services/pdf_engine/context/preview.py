"""Helpers for preview-only document contexts (no DB / financial mutations)."""

from __future__ import annotations

from typing import Any

from ..schemas import (
    InvoicePreviewPayload,
    PayslipPreviewPayload,
    PurchaseOrderPreviewPayload,
    QuotationPreviewPayload,
    ReportPreviewPayload,
)


def coerce_preview_payload(doc_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a preview payload by document type."""
    if doc_type in {"invoice", "receipt"}:
        return InvoicePreviewPayload.model_validate(payload).model_dump()
    if doc_type == "payslip":
        return PayslipPreviewPayload.model_validate(payload).model_dump()
    if doc_type == "quotation":
        return QuotationPreviewPayload.model_validate(payload).model_dump()
    if doc_type == "purchase_order":
        return PurchaseOrderPreviewPayload.model_validate(payload).model_dump()
    if doc_type == "report":
        return ReportPreviewPayload.model_validate(payload).model_dump()
    raise ValueError(f"Unsupported doc_type: {doc_type}")
