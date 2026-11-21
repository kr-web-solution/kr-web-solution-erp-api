"""Project money is derived from invoices. Agreed terms stay on the project."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Iterable, Optional

from sqlalchemy import inspect as sa_inspect

DEV_TYPES = {
    "advance",
    "balance",
    "custom_dev",
    "project",
    "hosting",
    "services",
    "licensing",
}
AMC_TYPES = {"amc", "maintenance"}
EXCLUDED = {"cancelled", "void"}


def ledger_for(invoice_type: str) -> str:
    if invoice_type in AMC_TYPES:
        return "maintenance"
    if (invoice_type or "").strip().lower() == "mixed":
        return "mixed"
    return "development"


def bucket_for(invoice_type: str, notes: str = "", description: str = "") -> str:
    if invoice_type in AMC_TYPES:
        return "amc"
    if invoice_type == "advance":
        return "advance"
    if invoice_type == "balance":
        return "balance"
    if invoice_type == "credit_note":
        return "credit_note"
    if invoice_type == "late_fee":
        return "late_fee"
    text = f"{notes} {description}".lower()
    if "advance" in text:
        return "advance"
    if "balance" in text:
        return "balance"
    return "custom_dev"


def _active(inv) -> bool:
    if getattr(inv, "deleted_at", None) is not None:
        return False
    return (getattr(inv, "status", "") or "") not in EXCLUDED


def _subtotal(inv) -> int:
    return int(getattr(inv, "subtotal_paise", 0) or 0)


def _tax(inv) -> int:
    return int(getattr(inv, "tax_paise", 0) or 0)


def _paid(inv) -> bool:
    return getattr(inv, "status", "") == "paid"


def summarize_invoices(project, invoices: Iterable) -> dict:
    """Live development and maintenance figures. Amounts are ex-GST subtotals."""
    agreed_total = int(getattr(project, "budget_paise", 0) or 0)
    advance_agreed = 0
    monthly_rate = int(getattr(project, "maintenance_amount_paise", 0) or 0)
    # maintenance_amount lives on the recurring row; callers may set it on a simple namespace
    if not monthly_rate:
        recs = getattr(project, "recurring_invoices", None) or []
        if recs:
            monthly_rate = int(getattr(recs[0], "amount_paise", 0) or 0)

    dev = {
        "agreed_total": agreed_total,
        "advance_agreed": advance_agreed,
        "advance_invoiced": 0,
        "advance_paid": 0,
        "balance_invoiced": 0,
        "balance_paid": 0,
        "custom_invoiced": 0,
        "custom_paid": 0,
        "total_invoiced": 0,
        "total_paid": 0,
        "gst_invoiced": 0,
        "gst_paid": 0,
    }
    maint = {
        "monthly_rate": monthly_rate,
        "total_invoiced": 0,
        "total_paid": 0,
        "gst_invoiced": 0,
        "gst_paid": 0,
        "billed_through": None,
        "paid_through": None,
        "paid_from": None,
        "paid_months": 0,
    }
    billed_through: Optional[date] = None
    paid_through: Optional[date] = None
    paid_from: Optional[date] = None
    paid_months = 0

    for inv in invoices:
        if not _active(inv):
            continue
        kind = bucket_for(
            getattr(inv, "invoice_type", "") or "",
            getattr(inv, "notes", "") or "",
            "",
        )
        sub = _subtotal(inv)
        tax = _tax(inv)
        paid = _paid(inv)
        ledger = getattr(inv, "ledger", None) or ledger_for(getattr(inv, "invoice_type", "") or "")
        # Avoid async MissingGreenlet: only use items when already eager-loaded.
        try:
            unloaded = sa_inspect(inv).unloaded
            items = [] if "items" in unloaded else list(getattr(inv, "items", None) or [])
        except Exception:
            items = []
        has_line_kinds = any(getattr(it, "line_kind", None) for it in items)

        # Mixed / multi-kind invoice: allocate by line_kind (tax proportional to line share)
        if (getattr(inv, "invoice_type", "") or "") == "mixed" or (
            has_line_kinds and len({(getattr(it, "line_kind", None) or kind) for it in items}) > 1
        ):
            for it in items:
                line_amt = int(getattr(it, "amount_paise", 0) or 0)
                if line_amt <= 0 or sub <= 0:
                    continue
                line_tax = int(round(tax * (line_amt / sub))) if tax else 0
                lk = (getattr(it, "line_kind", None) or "").strip().lower()
                if lk in AMC_TYPES or lk in ("maintenance", "amc", "mnt"):
                    maint["total_invoiced"] += line_amt
                    maint["gst_invoiced"] += line_tax
                    if paid:
                        maint["total_paid"] += line_amt
                        maint["gst_paid"] += line_tax
                else:
                    dev["total_invoiced"] += line_amt
                    dev["gst_invoiced"] += line_tax
                    if paid:
                        dev["total_paid"] += line_amt
                        dev["gst_paid"] += line_tax
                    if lk == "advance":
                        dev["advance_invoiced"] += line_amt
                        if paid:
                            dev["advance_paid"] += line_amt
                    elif lk == "balance":
                        dev["balance_invoiced"] += line_amt
                        if paid:
                            dev["balance_paid"] += line_amt
                    else:
                        dev["custom_invoiced"] += line_amt
                        if paid:
                            dev["custom_paid"] += line_amt
            covers_to = getattr(inv, "covers_to", None)
            if isinstance(covers_to, date) and (billed_through is None or covers_to > billed_through):
                billed_through = covers_to
            if paid and isinstance(covers_to, date) and (paid_through is None or covers_to > paid_through):
                paid_through = covers_to
                paid_from = getattr(inv, "covers_from", None)
                paid_months = int(getattr(inv, "period_months", 0) or 0)
            continue

        if kind == "amc" or ledger == "maintenance":
            maint["total_invoiced"] += sub
            maint["gst_invoiced"] += tax
            if paid:
                maint["total_paid"] += sub
                maint["gst_paid"] += tax
            covers_to = getattr(inv, "covers_to", None)
            if isinstance(covers_to, date) and (billed_through is None or covers_to > billed_through):
                billed_through = covers_to
            if paid and isinstance(covers_to, date) and (paid_through is None or covers_to > paid_through):
                paid_through = covers_to
                paid_from = getattr(inv, "covers_from", None)
                paid_months = int(getattr(inv, "period_months", 0) or 0)
            continue
        if kind == "late_fee":
            continue
        # Credit notes store positive magnitude but reduce billed/collected
        if kind == "credit_note":
            if ledger == "maintenance":
                maint["total_invoiced"] -= sub
                maint["gst_invoiced"] -= tax
                if paid:
                    maint["total_paid"] -= sub
                    maint["gst_paid"] -= tax
            else:
                dev["total_invoiced"] -= sub
                dev["gst_invoiced"] -= tax
                if paid:
                    dev["total_paid"] -= sub
                    dev["gst_paid"] -= tax
                dev["custom_invoiced"] -= sub
                if paid:
                    dev["custom_paid"] -= sub
            continue
        dev["total_invoiced"] += sub
        dev["gst_invoiced"] += tax
        if paid:
            dev["total_paid"] += sub
            dev["gst_paid"] += tax
        if kind == "advance":
            dev["advance_invoiced"] += sub
            if paid:
                dev["advance_paid"] += sub
        elif kind == "balance":
            dev["balance_invoiced"] += sub
            if paid:
                dev["balance_paid"] += sub
        else:
            dev["custom_invoiced"] += sub
            if paid:
                dev["custom_paid"] += sub

    dev["remaining_to_invoice"] = agreed_total - dev["total_invoiced"]
    dev["outstanding"] = dev["total_invoiced"] - dev["total_paid"]
    maint["outstanding"] = maint["total_invoiced"] - maint["total_paid"]
    maint["billed_through"] = billed_through.isoformat() if billed_through else None
    maint["paid_through"] = paid_through.isoformat() if paid_through else None
    maint["paid_from"] = paid_from.isoformat() if isinstance(paid_from, date) else None
    maint["paid_months"] = paid_months
    # Next automatic bill is the day after the latest invoice coverage, paid or not.
    maint["next_billing_date"] = (
        (billed_through + timedelta(days=1)).isoformat() if billed_through else None
    )

    variance = None
    messages = []
    if dev["total_invoiced"] > agreed_total:
        messages.append("Development invoiced is above the agreed total.")
    if messages:
        variance = {"development": messages}

    return {"development": dev, "maintenance": maint, "variance": variance}


def month_covered(invoices: Iterable, year: int, month: int) -> bool:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    for inv in invoices:
        if not _active(inv):
            continue
        kind = bucket_for(getattr(inv, "invoice_type", "") or "", getattr(inv, "notes", "") or "")
        ledger = getattr(inv, "ledger", None) or ""
        if kind != "amc" and ledger != "maintenance":
            continue
        cf = getattr(inv, "covers_from", None)
        ct = getattr(inv, "covers_to", None)
        if isinstance(cf, date) and isinstance(ct, date) and cf <= start and ct >= end:
            return True
    return False
