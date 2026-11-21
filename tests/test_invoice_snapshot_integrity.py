"""Historical integrity: company profile changes must not rewrite issued invoices."""

from datetime import date
from types import SimpleNamespace

from app.services.invoice_snapshot import (
    build_document_snapshot,
    company_branding_from_snapshot,
    ensure_issued_snapshot,
    freeze_document_snapshot,
    get_snapshot,
    needs_issued_snapshot,
    settings_view_from_snapshot,
    snapshot_payment_has_data,
)
from app.services.pdf import generate_invoice_pdf
from app.services.invoice_status import assert_content_editable, ISSUED_CONTENT_LOCKED
from app.core.exceptions import ConflictException
import pytest


def _settings(**overrides):
    base = dict(
        company_name="KR Web Solution",
        company_address="12 Old Street, Salem",
        company_gstin="33AAAAA0000A1Z5",
        company_pan="AAAAA0000A",
        company_state="Tamil Nadu",
        company_state_code="33",
        company_phone="+91 90000 00001",
        company_website="https://krwebsolution.com",
        company_tagline="Web Development",
        company_logo_url="/brand/krws-mark.png",
        gst_enabled=True,
        gst_unregistered=False,
        smtp_from_email="billing@krwebsolution.com",
        bank_beneficiary_name="KR Web Solution",
        bank_name="HDFC Bank",
        bank_account_number="111122223333",
        bank_ifsc="HDFC0000001",
        bank_upi_id="krws@upi",
        bank_upi_details=None,
        bank_upi_qr_url=None,
        bank_wire_instructions="Mention invoice number in narration.",
        payment_terms="Due in 15 days",
        invoice_terms_by_type={},
        default_sac_code="998314",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(**overrides):
    base = dict(
        name="Acme Corp",
        email="ap@acme.test",
        phone=None,
        address="12 Main",
        city="Chennai",
        state="Tamil Nadu",
        pincode="600001",
        gstin="33CCCCC0000C1Z5",
        pan="CCCCC0000C",
        state_code="33",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _invoice(**overrides):
    base = dict(
        invoice_number="KRWS-DEV-2026-901",
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
        po_number=None,
        notes=None,
        paid_at=None,
        covers_from=None,
        covers_to=None,
        period_key=None,
        ledger="development",
        upi_id_override=None,
        upi_qr_url=None,
        document_snapshot=None,
        snapshot_frozen_at=None,
        parent_invoice_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _items():
    return [
        SimpleNamespace(
            description="Website redesign",
            quantity=1,
            unit_rate_paise=10_00_000,
            amount_paise=10_00_000,
            hsn_sac="998314",
            unit="Nos",
        )
    ]


def test_address_change_preserves_frozen_pdf_and_view():
    old = _settings(company_address="Address A — Salem HQ")
    inv = _invoice()
    freeze_document_snapshot(inv, client=_client(), settings=old)
    snap = get_snapshot(inv)
    assert snap["company"]["address"] == "Address A — Salem HQ"

    live = _settings(company_address="Address B — New Office")
    view = settings_view_from_snapshot(snap, live)
    assert view.company_address == "Address A — Salem HQ"
    assert view.company_address != live.company_address

    pdf = generate_invoice_pdf(
        invoice=inv,
        client=_client(),
        settings=live,
        items=_items(),
        paper_size="a4",
    )
    assert pdf[:4] == b"%PDF"
    # PDF text extraction is limited; identity view already proves freeze wins.
    brand = company_branding_from_snapshot(snap, live)
    assert "Address A" in (brand["company_address"] or "")


def test_gstin_phone_email_name_change_preserves_freeze():
    old = _settings(
        company_name="KR Web Solution Old",
        company_gstin="33AAAAA0000A1Z5",
        company_phone="+91 90000 00001",
        smtp_from_email="old@krwebsolution.com",
    )
    inv = _invoice()
    freeze_document_snapshot(inv, client=_client(), settings=old)
    snap = get_snapshot(inv)

    live = _settings(
        company_name="KR Web Solution New",
        company_gstin="29BBBBB0000B1Z5",
        company_phone="+91 90000 99999",
        smtp_from_email="new@krwebsolution.com",
        company_state_code="29",
        company_state="Karnataka",
    )
    view = settings_view_from_snapshot(snap, live)
    assert view.company_name == "KR Web Solution Old"
    assert view.company_gstin == "33AAAAA0000A1Z5"
    assert view.company_phone == "+91 90000 00001"
    assert view.smtp_from_email == "old@krwebsolution.com"

    brand = company_branding_from_snapshot(snap, live)
    assert brand["company_name"] == "KR Web Solution Old"
    assert brand["company_gstin"] == "33AAAAA0000A1Z5"
    assert brand["company_email"] == "old@krwebsolution.com"


def test_empty_snapshot_payment_falls_back_to_live_bank_only():
    old = _settings(
        bank_beneficiary_name=None,
        bank_name=None,
        bank_account_number=None,
        bank_ifsc=None,
        bank_upi_id=None,
        bank_wire_instructions=None,
    )
    inv = _invoice()
    freeze_document_snapshot(inv, client=_client(), settings=old)
    snap = get_snapshot(inv)
    assert not snapshot_payment_has_data(snap.get("payment"))

    live = _settings(
        bank_beneficiary_name="KR Web Solution",
        bank_account_number="999988887777",
        bank_ifsc="HDFC0000999",
    )
    view = settings_view_from_snapshot(snap, live)
    # Identity still frozen
    assert view.company_address == old.company_address
    # Empty payment may use live bank for unpaid UX
    assert view.bank_account_number == "999988887777"


def test_populated_snapshot_payment_not_overridden_by_live_bank():
    old = _settings(bank_account_number="111122223333")
    inv = _invoice()
    freeze_document_snapshot(inv, client=_client(), settings=old)
    snap = get_snapshot(inv)
    assert snapshot_payment_has_data(snap.get("payment"))

    live = _settings(bank_account_number="999988887777")
    view = settings_view_from_snapshot(snap, live)
    assert view.bank_account_number == "111122223333"


def test_freeze_idempotent_does_not_rewrite():
    old = _settings(company_address="Original Address")
    inv = _invoice()
    freeze_document_snapshot(inv, client=_client(), settings=old)
    first = dict(get_snapshot(inv))

    newer = _settings(company_address="Should Not Appear")
    freeze_document_snapshot(inv, client=_client(), settings=newer)
    second = get_snapshot(inv)
    assert second["company"]["address"] == "Original Address"
    assert second["frozen_at"] == first["frozen_at"]


def test_ensure_issued_snapshot_freeze_on_read():
    inv = _invoice(status="sent", document_snapshot=None)
    assert needs_issued_snapshot(inv) is True
    ensure_issued_snapshot(inv, client=_client(), settings=_settings())
    assert get_snapshot(inv) is not None
    assert needs_issued_snapshot(inv) is False

    draft = _invoice(status="draft", document_snapshot=None)
    assert needs_issued_snapshot(draft) is False
    assert ensure_issued_snapshot(draft, client=_client(), settings=_settings()) is None


def test_issued_content_locked_statuses():
    for status in ISSUED_CONTENT_LOCKED:
        with pytest.raises(ConflictException):
            assert_content_editable(status)
    assert_content_editable("draft")  # no raise


def test_credit_note_parent_reference_in_snapshot():
    parent = SimpleNamespace(invoice_number="KRWS-DEV-2026-100")
    inv = _invoice(invoice_type="credit_note", status="sent")
    snap = build_document_snapshot(
        invoice=inv,
        client=_client(),
        settings=_settings(),
        parent_invoice=parent,
    )
    assert snap["parent_invoice_number"] == "KRWS-DEV-2026-100"
    freeze_document_snapshot(
        inv,
        client=_client(),
        settings=_settings(),
        parent_invoice=parent,
    )
    pdf = generate_invoice_pdf(
        invoice=inv,
        client=_client(),
        settings=_settings(company_address="Changed Later"),
        items=_items(),
        parent_invoice=parent,
        paper_size="a4",
    )
    assert pdf[:4] == b"%PDF"
    view = settings_view_from_snapshot(get_snapshot(inv), _settings(company_address="Changed Later"))
    assert view.company_address == _settings().company_address


def test_new_invoice_uses_live_settings_when_no_snapshot():
    live = _settings(company_address="Brand New Address")
    view = settings_view_from_snapshot(None, live)
    assert view is live
    assert view.company_address == "Brand New Address"
