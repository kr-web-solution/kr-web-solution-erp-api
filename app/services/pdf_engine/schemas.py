"""Pydantic schemas for the PDF engine API and preview payloads."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

DocType = Literal[
    "invoice",
    "receipt",
    "payslip",
    "quotation",
    "purchase_order",
    "report",
]


class PageConfig(BaseModel):
    paper_size: str = "a4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    margin_mm: Optional[float] = None


class GenerateInvoiceRequest(BaseModel):
    invoice_id: str
    paper_size: Optional[str] = "a4"
    orientation: Optional[Literal["portrait", "landscape"]] = "portrait"
    template_key: Optional[str] = None


class GenerateReceiptRequest(BaseModel):
    invoice_id: str
    paper_size: Optional[str] = "a4"
    orientation: Optional[Literal["portrait", "landscape"]] = "portrait"
    template_key: Optional[str] = None


class GeneratePayslipRequest(BaseModel):
    payslip_id: str
    paper_size: Optional[str] = "a4"
    orientation: Optional[Literal["portrait", "landscape"]] = "portrait"
    template_key: Optional[str] = None


class PreviewLineItem(BaseModel):
    title: str
    detail: Optional[str] = None
    qty: str = "1"
    unit: str = "Nos"
    rate: str
    amount: str
    hsn: Optional[str] = None


class PreviewTotalsRow(BaseModel):
    label: str
    value: str


class InvoicePreviewPayload(BaseModel):
    """Precomputed display strings only — no GST/math in templates."""

    doc_title: str = "INVOICE"
    invoice_number: str = "PREVIEW"
    number_label: str = "Invoice #:"
    issue_date: str = "—"
    due_date: Optional[str] = None
    company_name: str = "KR Web Solution"
    company_tagline: Optional[str] = None
    company_address: Optional[str] = None
    company_email: Optional[str] = None
    company_website: Optional[str] = None
    company_phone: Optional[str] = None
    company_gstin: Optional[str] = None
    company_pan: Optional[str] = None
    client_name: str = "Client"
    client_contact: Optional[str] = None
    client_address: Optional[str] = None
    client_place: Optional[str] = None
    client_country: Optional[str] = None
    items: list[PreviewLineItem] = Field(default_factory=list)
    totals_rows: list[PreviewTotalsRow] = Field(default_factory=list)
    total_label: str = "TOTAL DUE"
    total_fmt: str = "₹0.00"
    amount_in_words: Optional[str] = None
    terms: list[str] = Field(default_factory=list)
    watermark: Optional[str] = "PREVIEW"
    show_hsn: bool = False
    show_unit: bool = True


class PayslipPreviewPayload(BaseModel):
    company_name: str = "KR Web Solution"
    employee_name: str
    employee_email: str = ""
    job_title: Optional[str] = None
    department: Optional[str] = None
    month_key: str
    gross_fmt: str
    deductions_fmt: str
    net_fmt: str
    note: Optional[str] = None
    watermark: Optional[str] = "PREVIEW"


class QuotationPreviewPayload(BaseModel):
    company_name: str = "KR Web Solution"
    company_address: Optional[str] = None
    quote_number: str = "Q-PREVIEW"
    quote_date: str = "—"
    valid_until: Optional[str] = None
    client_name: str = "Client"
    client_address: Optional[str] = None
    items: list[PreviewLineItem] = Field(default_factory=list)
    totals_rows: list[PreviewTotalsRow] = Field(default_factory=list)
    total_fmt: str = "₹0.00"
    notes: list[str] = Field(default_factory=list)
    watermark: Optional[str] = "PREVIEW"


class PurchaseOrderPreviewPayload(BaseModel):
    company_name: str = "KR Web Solution"
    company_address: Optional[str] = None
    po_number: str = "PO-PREVIEW"
    po_date: str = "—"
    vendor_name: str = "Vendor"
    vendor_address: Optional[str] = None
    items: list[PreviewLineItem] = Field(default_factory=list)
    totals_rows: list[PreviewTotalsRow] = Field(default_factory=list)
    total_fmt: str = "₹0.00"
    notes: list[str] = Field(default_factory=list)
    watermark: Optional[str] = "PREVIEW"


class ReportPreviewPayload(BaseModel):
    company_name: str = "KR Web Solution"
    title: str = "Financial Report"
    period_label: Optional[str] = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    summary_rows: list[PreviewTotalsRow] = Field(default_factory=list)
    watermark: Optional[str] = "PREVIEW"


class PreviewRequest(BaseModel):
    template_key: Optional[str] = None
    paper_size: Optional[str] = "a4"
    orientation: Optional[Literal["portrait", "landscape"]] = "portrait"
    payload: dict[str, Any] = Field(default_factory=dict)


class PdfTemplateCreate(BaseModel):
    doc_type: DocType
    design_key: str = Field(..., min_length=1, max_length=80)
    version: str = Field(default="v1", max_length=20)
    label: str = Field(..., min_length=1, max_length=120)
    active: bool = True
    is_default: bool = False

    @field_validator("design_key", "version")
    @classmethod
    def _safe_key(cls, v: str) -> str:
        cleaned = v.strip().lower().replace(" ", "_")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-/")
        if not cleaned or any(c not in allowed for c in cleaned):
            raise ValueError("keys must be lowercase alphanumeric with _ - /")
        if ".." in cleaned or cleaned.startswith("/"):
            raise ValueError("invalid key path")
        return cleaned


class PdfTemplateUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    active: Optional[bool] = None
    is_default: Optional[bool] = None


class PdfTemplateOut(BaseModel):
    id: str
    doc_type: str
    design_key: str
    version: str
    label: str
    active: bool
    is_default: bool

    model_config = {"from_attributes": True}


class PdfInfoOut(BaseModel):
    page_count: int
    encrypted: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class WatermarkOptions(BaseModel):
    text: str = "DRAFT"
    opacity: float = Field(default=0.15, ge=0.05, le=0.5)


class EncryptOptions(BaseModel):
    user_password: str = Field(..., min_length=4, max_length=128)
    owner_password: Optional[str] = Field(None, min_length=4, max_length=128)


class SplitOptions(BaseModel):
    """1-based inclusive page ranges, e.g. '1-2,4'."""

    ranges: str = Field(..., min_length=1, max_length=200)
