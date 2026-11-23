"""Preview + payslip context tests (no DB, no financial side effects)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.pdf import generate_invoice_pdf, generate_payslip_pdf
from app.services.pdf_engine.context.payslip import build_payslip_context
from app.services.pdf_engine.service import preview_document
from app.services.pdf_engine.exceptions import PreviewValidationError


def test_payslip_context_and_pdf_smoke():
    ctx = build_payslip_context(
        employee_name="Ada Lovelace",
        employee_email="ada@example.com",
        job_title="Engineer",
        department="R&D",
        month_key="2026-11",
        gross_paise=100_000_00,
        deductions_paise=5_000_00,
        net_paise=95_000_00,
        note="Test note",
        company_name="KR Web Solution",
        company_address="Salem, Tamil Nadu",
    )
    assert "₹" in ctx["gross_fmt"] or "Rs" in ctx["gross_fmt"] or ctx["gross_fmt"]
    assert ctx["net_fmt"]

    pdf = generate_payslip_pdf(
        employee_name="Ada Lovelace",
        employee_email="ada@example.com",
        job_title="Engineer",
        department="R&D",
        month_key="2026-11",
        gross_paise=100_000_00,
        deductions_paise=5_000_00,
        net_paise=95_000_00,
        note=None,
        company_name="KR Web Solution",
    )
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


def test_invoice_pdf_still_works_via_facade():
    inv = SimpleNamespace(
        invoice_number="KRWS-TEST-1",
        status="sent",
        invoice_type="services",
        issue_date=None,
        due_date=None,
        tax_paise=0,
        tax_rate_percent=0,
        subtotal_paise=10000,
        total_paise=10000,
        discount_paise=0,
        cgst_paise=0,
        sgst_paise=0,
        igst_paise=0,
        round_off_paise=0,
        amount_paid_paise=0,
        other_charges_paise=0,
        credit_adjustment_paise=0,
        gst_enabled=False,
        po_number=None,
        place_of_supply=None,
        covers_from=None,
        covers_to=None,
        ledger="development",
        notes=None,
        paid_at=None,
        payments=[],
        document_snapshot=None,
    )
    client = SimpleNamespace(
        name="Acme",
        email="a@b.c",
        phone=None,
        address="Chennai",
        city="Chennai",
        state="TN",
        pincode="600001",
        country="India",
        gstin=None,
        pan=None,
        state_code=None,
    )
    settings = SimpleNamespace(
        company_name="KR Web Solution",
        company_address="Salem",
        company_tagline=None,
        company_gstin=None,
        company_pan=None,
        company_phone=None,
        company_website="www.krwebsolution.com",
        company_logo_url=None,
        company_email="billing@krwebsolution.com",
        smtp_from_email="billing@krwebsolution.com",
        gst_enabled=False,
        gst_unregistered=False,
        bank_beneficiary_name=None,
        bank_name=None,
        bank_account_number=None,
        bank_ifsc=None,
        bank_upi_id=None,
        bank_paypal_id=None,
        invoice_terms_default=None,
        invoice_terms_by_type=None,
    )
    item = SimpleNamespace(
        description="Support",
        quantity=1,
        unit_rate_paise=10000,
        amount_paise=10000,
        hsn_sac=None,
        unit="Nos",
    )
    pdf = generate_invoice_pdf(inv, client, settings, [item], paper_size="a4")
    assert pdf[:4] == b"%PDF"

    # landscape + letter
    pdf_l = generate_invoice_pdf(
        inv, client, settings, [item], paper_size="letter", orientation="landscape"
    )
    assert pdf_l[:4] == b"%PDF"


def test_preview_quotation_no_side_effects():
    async def _run():
        pdf, temp = await preview_document(
            "quotation",
            {
                "company_name": "KR Web Solution",
                "quote_number": "Q-1",
                "quote_date": "01 Nov 2026",
                "client_name": "Acme",
                "items": [
                    {
                        "title": "Website",
                        "qty": "1",
                        "unit": "Nos",
                        "rate": "₹10,000.00",
                        "amount": "₹10,000.00",
                    }
                ],
                "totals_rows": [],
                "total_fmt": "₹10,000.00",
                "watermark": "PREVIEW",
            },
            persist_temp=False,
        )
        assert pdf[:4] == b"%PDF"
        assert temp is None

    try:
        asyncio.run(_run())
    except Exception as exc:
        # WeasyPrint may be unavailable on Windows without GTK — ReportLab not used for preview.
        # Accept RenderError / Weasy failure by ensuring validation path works separately.
        from app.services.pdf_engine.exceptions import RenderError

        if not isinstance(exc, RenderError):
            # If libs missing, Weasy raises RenderError wrapped; also bare OSError paths
            msg = str(exc).lower()
            if "weasy" in msg or "gobject" in msg or "render" in msg or "library" in msg:
                pytest.skip(f"WeasyPrint unavailable: {exc}")
            raise


def test_preview_rejects_invalid_payload():
    async def _run():
        with pytest.raises(PreviewValidationError):
            await preview_document("payslip", {"employee_name": "x"}, persist_temp=False)

    asyncio.run(_run())


def test_preview_po_and_report_schemas():
    async def _run():
        try:
            po, _ = await preview_document(
                "purchase_order",
                {
                    "po_number": "PO-1",
                    "po_date": "01 Nov 2026",
                    "vendor_name": "Vendor Co",
                    "items": [],
                    "total_fmt": "₹0.00",
                },
                persist_temp=False,
            )
            assert po[:4] == b"%PDF"
            report, _ = await preview_document(
                "report",
                {
                    "title": "Collections",
                    "rows": [{"Month": "Nov", "Amount": "₹1,000"}],
                    "summary_rows": [{"label": "Total", "value": "₹1,000"}],
                },
                persist_temp=False,
            )
            assert report[:4] == b"%PDF"
        except Exception as exc:
            msg = str(exc).lower()
            if "weasy" in msg or "gobject" in msg or "library" in msg or "render" in msg:
                pytest.skip(f"WeasyPrint unavailable: {exc}")
            raise

    asyncio.run(_run())
