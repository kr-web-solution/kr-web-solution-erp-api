"""Smoke tests: 0% GST default, credit-note money math, cancel, statutory flags off."""

from types import SimpleNamespace

from app.services.project_money import summarize_invoices, bucket_for
from app.services.tax_payroll import (
    effective_tax_rate,
    compute_tax_paise,
    statutory_deductions_paise,
    gst_is_enabled,
)


def test_gst_disabled_forces_zero_tax():
    st = SimpleNamespace(gst_enabled=False, default_tax_rate=18)
    assert gst_is_enabled(st) is False
    assert effective_tax_rate(st) == 0.0
    assert effective_tax_rate(st, 18) == 0.0
    assert compute_tax_paise(100_00, effective_tax_rate(st)) == 0


def test_gst_enabled_uses_default_or_requested():
    st = SimpleNamespace(gst_enabled=True, default_tax_rate=18)
    assert effective_tax_rate(st) == 18.0
    assert effective_tax_rate(st, 5) == 5.0
    assert compute_tax_paise(10_000_00, 18) == 1_800_00


def test_statutory_off_by_default():
    st = SimpleNamespace(
        pf_enabled=False,
        pf_employee_percent=12,
        esi_enabled=False,
        esi_employee_percent=0.75,
        pt_enabled=False,
        pt_paise=200_00,
    )
    total, breakdown = statutory_deductions_paise(st, 50_000_00)
    assert total == 0
    assert breakdown == {}


def test_statutory_on_applies_breakdown():
    st = SimpleNamespace(
        pf_enabled=True,
        pf_employee_percent=12,
        esi_enabled=True,
        esi_employee_percent=1,
        pt_enabled=True,
        pt_paise=200_00,
    )
    total, breakdown = statutory_deductions_paise(st, 10_000_00)
    assert breakdown["pf"] == 1_200_00
    assert breakdown["esi"] == 100_00
    assert breakdown["pt"] == 200_00
    assert total == 1_500_00


def test_credit_note_bucket():
    assert bucket_for("credit_note") == "credit_note"


def test_credit_note_reduces_project_money():
    project = SimpleNamespace(budget_paise=100_000_00, maintenance_amount_paise=0, recurring_invoices=[])
    invoices = [
        SimpleNamespace(
            status="paid",
            invoice_type="advance",
            ledger="development",
            subtotal_paise=40_000_00,
            tax_paise=0,
            notes="",
            covers_from=None,
            covers_to=None,
            period_months=1,
        ),
        SimpleNamespace(
            status="paid",
            invoice_type="credit_note",
            ledger="development",
            subtotal_paise=10_000_00,
            tax_paise=0,
            notes="Credit note for INV",
            covers_from=None,
            covers_to=None,
            period_months=1,
        ),
    ]
    summary = summarize_invoices(project, invoices)
    assert summary["development"]["total_invoiced"] == 30_000_00
    assert summary["development"]["total_paid"] == 30_000_00
