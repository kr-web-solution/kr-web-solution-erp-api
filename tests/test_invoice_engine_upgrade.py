"""Unit smoke for invoice engine: GST split, amount in words, terms, PDF."""

from types import SimpleNamespace
from datetime import date

from app.services.amount_in_words import amount_in_words_inr
from app.services.invoice_terms import resolve_invoice_terms, DEFAULT_TERMS_BY_TYPE
from app.services.tax_payroll import split_gst_paise, apply_invoice_tax_snapshot
from app.services.pdf import generate_invoice_pdf


def test_amount_in_words_indian_numbering():
    text = amount_in_words_inr(12_34_56_789)  # 1,23,456.789 → wait paise
    # 123456789 paise = ₹12,34,567.89
    assert "Twelve Lakh" in text or "Twelve Lakh" in amount_in_words_inr(123456789)
    assert "Only" in amount_in_words_inr(100)
    assert amount_in_words_inr(0).lower().startswith("rupees zero") or "Zero" in amount_in_words_inr(0)


def test_gst_split_intra_inter_and_unknown():
    intra = split_gst_paise(
        18000,
        supplier_state_code="33",
        customer_state_code="33",
        gst_enabled=True,
    )
    assert intra["supply_type"] == "intra"
    assert intra["cgst_paise"] + intra["sgst_paise"] == 18000
    assert intra["igst_paise"] == 0

    inter = split_gst_paise(
        18000,
        supplier_state_code="33",
        customer_state_code="29",
        gst_enabled=True,
    )
    assert inter["supply_type"] == "inter"
    assert inter["igst_paise"] == 18000

    unknown = split_gst_paise(
        18000,
        supplier_state_code="33",
        customer_state_code=None,
        gst_enabled=True,
    )
    assert unknown["cgst_paise"] == 0
    assert unknown["igst_paise"] == 0
    assert unknown["supply_type"] is None


def test_apply_invoice_tax_snapshot_intra():
    settings = SimpleNamespace(
        gst_enabled=True,
        default_tax_rate=18,
        company_state_code="33",
        company_gstin="33AAAAA0000A1Z5",
    )
    client = SimpleNamespace(
        state_code="33",
        state="Tamil Nadu",
        gstin="33BBBBB0000B1Z5",
    )
    snap = apply_invoice_tax_snapshot(
        settings=settings,
        client=client,
        subtotal_paise=10_00_000,
        tax_rate_percent=18,
    )
    assert snap["tax_paise"] == 1_80_000
    assert snap["total_paise"] == 11_80_000
    assert snap["cgst_paise"] + snap["sgst_paise"] == 1_80_000
    assert snap["place_of_supply"] == "Tamil Nadu"


def test_terms_resolver_priority():
    notes_terms = resolve_invoice_terms(
        invoice_type="amc",
        invoice_notes="Custom note one.\nCustom note two.",
        settings=None,
        status="sent",
    )
    assert notes_terms[0].startswith("Custom note")

    settings = SimpleNamespace(
        invoice_terms_by_type={"amc": ["Settings clause for AMC"]},
        payment_terms="Fallback payment terms",
    )
    from_settings = resolve_invoice_terms(
        invoice_type="amc",
        invoice_notes=None,
        settings=settings,
        status="sent",
    )
    assert from_settings == ["Settings clause for AMC"]

    defaults = resolve_invoice_terms(
        invoice_type="custom_dev",
        invoice_notes=None,
        settings=SimpleNamespace(invoice_terms_by_type={}, payment_terms=None),
        status="sent",
    )
    assert defaults == DEFAULT_TERMS_BY_TYPE["custom_dev"]


def test_pdf_smoke_a4_with_and_without_gst():
    settings_gst = SimpleNamespace(
        company_name="KR Web Solution",
        company_address="Salem, Tamil Nadu",
        company_gstin="33AAAAA0000A1Z5",
        company_pan="AAAAA0000A",
        company_state="Tamil Nadu",
        company_state_code="33",
        gst_enabled=True,
        gst_unregistered=False,
        company_phone="+91 90000 00000",
        company_website="https://krwebsolution.com",
        company_tagline="Web Development",
        company_logo_url=None,
        bank_beneficiary_name="KR Web Solution",
        bank_name="HDFC Bank",
        bank_account_number="1234567890",
        bank_ifsc="HDFC0000001",
        bank_upi_id="krws@upi",
        bank_upi_details=None,
        bank_upi_qr_url=None,
        payment_terms="Payment due in 15 days.",
        invoice_terms_by_type={},
        smtp_from_email="billing@krwebsolution.com",
        default_sac_code="998314",
    )
    client = SimpleNamespace(
        name="Acme Corp",
        address="12 Main Street",
        city="Chennai",
        state="Tamil Nadu",
        pincode="600001",
        gstin="33CCCCC0000C1Z5",
        pan="CCCCC0000C",
        state_code="33",
        email="ap@acme.test",
        phone=None,
        primary_contact_name="Priya",
        designation="Director",
        contact_email=None,
        contact_phone=None,
    )
    invoice = SimpleNamespace(
        invoice_number="KRWS-DEV-2026-001",
        invoice_type="custom_dev",
        status="sent",
        issue_date=date(2026, 3, 20),
        due_date=date(2026, 4, 4),
        subtotal_paise=10_00_000,
        tax_rate_percent=18.0,
        tax_paise=1_80_000,
        total_paise=11_80_000,
        cgst_paise=90_000,
        sgst_paise=90_000,
        igst_paise=0,
        discount_paise=0,
        round_off_paise=0,
        amount_paid_paise=0,
        place_of_supply="Tamil Nadu",
        supply_type="intra",
        po_number="PO-100",
        notes=None,
        paid_at=None,
        covers_from=None,
        covers_to=None,
        period_key=None,
        ledger="development",
        upi_id_override=None,
        upi_qr_url=None,
    )
    items = [
        SimpleNamespace(
            description="Website redesign\nPhase 1 deliverables",
            quantity=1,
            unit_rate_paise=10_00_000,
            amount_paise=10_00_000,
            hsn_sac="998314",
            unit="Nos",
        )
    ]
    pdf_gst = generate_invoice_pdf(
        invoice=invoice,
        client=client,
        settings=settings_gst,
        items=items,
        paper_size="a4",
    )
    assert pdf_gst[:4] == b"%PDF"
    assert len(pdf_gst) > 1500

    settings_off = SimpleNamespace(**{**settings_gst.__dict__, "gst_enabled": False, "company_gstin": None, "gst_unregistered": True})
    invoice_off = SimpleNamespace(**{**invoice.__dict__, "tax_paise": 0, "tax_rate_percent": 0, "total_paise": 10_00_000, "cgst_paise": 0, "sgst_paise": 0})
    pdf_off = generate_invoice_pdf(
        invoice=invoice_off,
        client=client,
        settings=settings_off,
        items=items,
        paper_size="a4",
    )
    assert pdf_off[:4] == b"%PDF"
    assert len(pdf_off) > 1500
