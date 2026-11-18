"""Invoices are the source of truth for project money."""

from datetime import date
from types import SimpleNamespace

from app.services.project_money import month_covered, summarize_invoices


def _inv(**kwargs):
    base = dict(
        invoice_type="advance",
        ledger="development",
        status="sent",
        subtotal_paise=0,
        tax_paise=0,
        notes="",
        covers_from=None,
        covers_to=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _project(**kwargs):
    base = dict(budget_paise=5000000, advance_paise=1000000, recurring_invoices=[])
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_edit_advance_updates_development_only():
    project = _project()
    before = summarize_invoices(project, [_inv(subtotal_paise=1_000_000, tax_paise=180_000)])
    assert before["development"]["advance_invoiced"] == 1_000_000
    assert before["development"]["remaining_to_invoice"] == 4_000_000

    after = summarize_invoices(project, [_inv(subtotal_paise=3_000_000, tax_paise=540_000)])
    assert after["development"]["advance_invoiced"] == 3_000_000
    assert after["development"]["remaining_to_invoice"] == 2_000_000
    assert after["development"]["gst_invoiced"] == 540_000
    assert after["maintenance"]["total_invoiced"] == 0
    assert after["variance"] is None


def test_amc_edit_does_not_touch_development():
    project = _project()
    summary = summarize_invoices(
        project,
        [
            _inv(subtotal_paise=1_000_000),
            _inv(
                invoice_type="amc",
                ledger="maintenance",
                subtotal_paise=1_800_000,
                tax_paise=324_000,
                status="sent",
            ),
        ],
    )
    assert summary["development"]["total_invoiced"] == 1_000_000
    assert summary["maintenance"]["total_invoiced"] == 1_800_000
    assert summary["maintenance"]["outstanding"] == 1_800_000


def test_six_month_amc_paid_moves_billed_through():
    project = _project()
    summary = summarize_invoices(
        project,
        [
            _inv(
                invoice_type="amc",
                ledger="maintenance",
                status="paid",
                subtotal_paise=3_600_000,
                covers_from=date(2027, 1, 1),
                covers_to=date(2027, 6, 30),
                period_months=6,
            )
        ],
    )
    assert summary["maintenance"]["billed_through"] == "2027-06-30"
    assert summary["maintenance"]["paid_through"] == "2027-06-30"
    assert summary["maintenance"]["paid_months"] == 6
    assert summary["maintenance"]["next_billing_date"] == "2027-07-01"
    assert summary["maintenance"]["total_paid"] == 3_600_000
    assert summary["maintenance"]["outstanding"] == 0
    assert month_covered(summary and [
        _inv(
            invoice_type="amc",
            ledger="maintenance",
            status="paid",
            covers_from=date(2027, 1, 1),
            covers_to=date(2027, 6, 30),
        )
    ], 2027, 3)
    assert not month_covered([
        _inv(
            invoice_type="amc",
            ledger="maintenance",
            status="paid",
            covers_from=date(2027, 1, 1),
            covers_to=date(2027, 6, 30),
        )
    ], 2027, 7)


def test_cancelled_invoice_is_excluded():
    project = _project()
    summary = summarize_invoices(
        project,
        [
            _inv(subtotal_paise=1_000_000),
            _inv(subtotal_paise=2_000_000, status="cancelled"),
        ],
    )
    assert summary["development"]["total_invoiced"] == 1_000_000
