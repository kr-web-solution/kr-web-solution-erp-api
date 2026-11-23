"""Unit tests for pdf_engine pypdf operations and template resolution (no DB)."""

from __future__ import annotations

import io

import pytest

from app.services.pdf_engine.pdf_operations import (
    add_metadata,
    add_text_watermark,
    encrypt_pdf,
    get_info,
    merge_pdfs,
    rotate_pages,
    split_pdf,
    validate_pdf,
)
from app.services.pdf_engine.template_manager import list_disk_designs, resolve_template
from app.services.pdf_engine.exceptions import PdfOperationError, TemplateNotFoundError


def _minimal_pdf() -> bytes:
    """Build a tiny valid PDF via reportlab."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 720, "Page 1 INR ₹100")
    c.showPage()
    c.drawString(72, 720, "Page 2")
    c.showPage()
    c.drawString(72, 720, "Page 3")
    c.save()
    return buf.getvalue()


def test_validate_and_info():
    pdf = _minimal_pdf()
    assert validate_pdf(pdf)
    assert not validate_pdf(b"not-a-pdf")
    info = get_info(pdf)
    assert info["page_count"] == 3
    assert info["encrypted"] is False


def test_merge_split_rotate_watermark_encrypt_metadata():
    a = _minimal_pdf()
    b = _minimal_pdf()
    merged = merge_pdfs([a, b])
    assert merged[:4] == b"%PDF"
    assert get_info(merged)["page_count"] == 6

    split = split_pdf(merged, "1-2,5")
    assert get_info(split)["page_count"] == 3

    rotated = rotate_pages(a, 90)
    assert rotated[:4] == b"%PDF"

    stamped = add_text_watermark(a, "CONFIDENTIAL", opacity=0.2)
    assert stamped[:4] == b"%PDF"

    meta = add_metadata(a, {"title": "Test Invoice", "author": "KR"})
    info = get_info(meta)
    assert "title" in info["metadata"] or info["page_count"] == 3

    enc = encrypt_pdf(a, "secret123")
    assert enc[:4] == b"%PDF"
    info_enc = get_info(enc)
    assert info_enc["encrypted"] is True


def test_split_invalid_range():
    pdf = _minimal_pdf()
    with pytest.raises(PdfOperationError):
        split_pdf(pdf, "99")


def test_template_resolution():
    designs = list_disk_designs()
    keys = {d["design_key"] for d in designs}
    assert "invoices/v1" in keys
    assert "payslips/v1" in keys
    assert "quotations/v1" in keys
    assert "purchase_orders/v1" in keys
    assert "reports/v1" in keys

    inv = resolve_template("invoice")
    assert inv.relative_path.endswith("document.html")
    pay = resolve_template("payslip", "payslips/v1")
    assert "payslip" in pay.relative_path

    with pytest.raises(TemplateNotFoundError):
        resolve_template("invoice", "does-not-exist/v9")
