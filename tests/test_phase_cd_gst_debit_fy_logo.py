"""Phase C/D unit smoke: multi-GSTIN, Indian FY numbers, debit note typing, logo hash freeze."""

from datetime import date
from types import SimpleNamespace

from app.services.gst_registrations import (
    select_gst_registration,
    settings_overlay_from_registration,
    registration_to_dict,
)
from app.services.invoice_numbers import (
    indian_fy_bounds,
    indian_fy_label,
    format_invoice_number,
    service_code_for,
)
from app.services.invoice_snapshot import build_document_snapshot
from app.services.invoice_terms import resolve_invoice_terms, DEFAULT_TERMS_BY_TYPE
from app.services.invoice_typing import normalize_line_kind


def _reg(**overrides):
    base = dict(
        id="gst_1",
        gstin="33AAAAA0000A1Z5",
        legal_name="KR WS TN",
        address="Salem HQ",
        state="Tamil Nadu",
        state_code="33",
        effective_from=date(2024, 4, 1),
        effective_to=None,
        is_primary=True,
        is_active=True,
        deleted_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_indian_fy_apr_mar_labels():
    assert indian_fy_label(date(2025, 4, 1)) == "2526"
    assert indian_fy_label(date(2026, 3, 31)) == "2526"
    assert indian_fy_label(date(2026, 4, 1)) == "2627"
    start, end, label = indian_fy_bounds(date(2026, 1, 15))
    assert start == date(2025, 4, 1)
    assert end == date(2026, 3, 31)
    assert label == "2526"


def test_invoice_number_format_uses_calendar_year():
    num = format_invoice_number("KRWS", "SVC", "2026", 1001, gst_enabled=True)
    assert num == "KRWS-SVC-2026-1001"
    num_off = format_invoice_number("KRWS", "MNT", "2026", 1001, gst_enabled=False)
    assert num_off == "KRWS-MNT-2026-G-1001"
    assert service_code_for("debit_note") == "DBN"
    assert service_code_for("credit_note") == "CRN"
    assert service_code_for("services") == "SVC"


def test_select_gst_registration_prefers_customer_state():
    tn = _reg(id="gst_tn", state_code="33", is_primary=True)
    ka = _reg(
        id="gst_ka",
        gstin="29BBBBB0000B1Z5",
        state="Karnataka",
        state_code="29",
        is_primary=False,
        legal_name="KR WS KA",
    )
    chosen = select_gst_registration(
        [tn, ka],
        issue_date=date(2026, 5, 1),
        customer_state_code="29",
    )
    assert chosen is not None
    assert chosen.id == "gst_ka"


def test_select_gst_registration_falls_back_to_primary():
    tn = _reg(id="gst_tn", state_code="33", is_primary=True)
    ka = _reg(
        id="gst_ka",
        gstin="29BBBBB0000B1Z5",
        state_code="29",
        is_primary=False,
        effective_from=date(2025, 4, 1),
    )
    chosen = select_gst_registration(
        [tn, ka],
        issue_date=date(2026, 5, 1),
        customer_state_code="27",  # Maharashtra — no match
    )
    assert chosen is not None
    assert chosen.id == "gst_tn"


def test_select_gst_registration_respects_effective_window():
    old = _reg(
        id="gst_old",
        effective_from=date(2023, 4, 1),
        effective_to=date(2024, 3, 31),
        is_primary=True,
    )
    current = _reg(
        id="gst_new",
        gstin="33CCCCC0000C1Z5",
        effective_from=date(2024, 4, 1),
        is_primary=False,
    )
    chosen = select_gst_registration(
        [old, current],
        issue_date=date(2025, 6, 1),
        customer_state_code="33",
    )
    assert chosen is not None
    assert chosen.id == "gst_new"


def test_settings_overlay_and_snapshot_freeze_logo_hash():
    settings = SimpleNamespace(
        company_name="KR Web Solution",
        company_address="12 Old Street",
        company_gstin="33AAAAA0000A1Z5",
        company_pan="AAAAA0000A",
        company_state="Tamil Nadu",
        company_state_code="33",
        company_phone=None,
        company_website=None,
        company_tagline=None,
        company_logo_url="/api/v1/settings/logo-file/logo-abcdef0123456789.png",
        company_logo_content_hash="a" * 64,
        gst_enabled=True,
        gst_unregistered=False,
        smtp_from_email="billing@example.com",
        bank_beneficiary_name="KR Web Solution",
        bank_name="HDFC",
        bank_account_number="123",
        bank_ifsc="HDFC0001",
        bank_upi_id="kr@upi",
        bank_upi_details=None,
        bank_upi_qr_url=None,
        bank_wire_instructions=None,
        payment_terms=None,
        invoice_terms_by_type={},
        default_sac_code="998314",
        default_tax_rate=18,
        invoice_prefix="KRWS",
        invoice_sequences={},
        invoice_next_number=1,
    )
    ka = _reg(
        id="gst_ka",
        gstin="29BBBBB0000B1Z5",
        legal_name="KR WS KA",
        address="Bengaluru office",
        state="Karnataka",
        state_code="29",
        is_primary=False,
    )
    overlay = settings_overlay_from_registration(settings, ka)
    assert overlay.company_gstin == "29BBBBB0000B1Z5"
    assert overlay.company_state_code == "29"
    assert overlay.company_logo_content_hash == "a" * 64

    invoice = SimpleNamespace(
        invoice_number="KRWS-DBN-2526-001",
        invoice_type="debit_note",
        status="sent",
        notes="Debit note for KRWS-DEV-2526-010",
        upi_id_override=None,
        upi_qr_url=None,
    )
    client = SimpleNamespace(
        name="Acme",
        email="a@x.com",
        phone=None,
        address="1 Main",
        city="Bengaluru",
        state="Karnataka",
        pincode="560001",
        gstin="29CCCCC0000C1Z5",
        pan=None,
        state_code="29",
    )
    snap = build_document_snapshot(
        invoice=invoice,
        client=client,
        settings=settings,
        gst_registration=ka,
    )
    assert snap["company"]["gstin"] == "29BBBBB0000B1Z5"
    assert snap["company"]["logo_content_hash"] == "a" * 64
    assert snap["company"]["logo_url"].startswith("/api/v1/settings/logo-file/")
    assert snap["gst_registration"]["gstin"] == "29BBBBB0000B1Z5"
    assert registration_to_dict(ka)["state_code"] == "29"


def test_debit_note_type_and_terms():
    assert normalize_line_kind("debit_note") == "debit_note"
    assert "debit_note" in DEFAULT_TERMS_BY_TYPE
    terms = resolve_invoice_terms(
        invoice_type="debit_note",
        invoice_notes=None,
        settings=None,
        status="sent",
    )
    assert any("additional charge" in t.lower() for t in terms)
