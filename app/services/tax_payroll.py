"""Effective GST rate and optional statutory payroll deductions from Settings."""

from __future__ import annotations

from typing import Any, Dict, Tuple


def gst_is_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "gst_enabled", False))


def effective_tax_rate(settings: Any, requested: float | None = None) -> float:
    """Return tax % to apply. Always 0 when GST is disabled."""
    if not gst_is_enabled(settings):
        return 0.0
    if requested is not None:
        return max(0.0, float(requested))
    return max(0.0, float(getattr(settings, "default_tax_rate", 0) or 0))


def compute_tax_paise(subtotal_paise: int, tax_rate_percent: float) -> int:
    return int(round(int(subtotal_paise or 0) * (float(tax_rate_percent or 0) / 100.0)))


def statutory_deductions_paise(settings: Any, gross_paise: int) -> Tuple[int, Dict[str, int]]:
    """
    Optional PF / ESI / PT from Settings flags.
    Returns (total_paise, breakdown_paise).
    """
    gross = max(0, int(gross_paise or 0))
    breakdown: Dict[str, int] = {}
    total = 0

    if getattr(settings, "pf_enabled", False):
        pct = float(getattr(settings, "pf_employee_percent", 0) or 0)
        if pct > 0:
            amt = int(round(gross * (pct / 100.0)))
            breakdown["pf"] = amt
            total += amt

    if getattr(settings, "esi_enabled", False):
        pct = float(getattr(settings, "esi_employee_percent", 0) or 0)
        if pct > 0:
            amt = int(round(gross * (pct / 100.0)))
            breakdown["esi"] = amt
            total += amt

    if getattr(settings, "pt_enabled", False):
        amt = max(0, int(getattr(settings, "pt_paise", 0) or 0))
        if amt > 0:
            breakdown["pt"] = amt
            total += amt

    return total, breakdown


def resolve_payment_upi(
    settings: Any,
    invoice: Any | None = None,
) -> Dict[str, str | None]:
    """Effective UPI id + QR URL for an invoice (override → company default)."""
    upi_id = None
    qr_url = None
    if invoice is not None:
        upi_id = (getattr(invoice, "upi_id_override", None) or "").strip() or None
        qr_url = (getattr(invoice, "upi_qr_url", None) or "").strip() or None
    if not upi_id:
        upi_id = (getattr(settings, "bank_upi_id", None) or "").strip() or None
    if not qr_url:
        qr_url = (getattr(settings, "bank_upi_qr_url", None) or "").strip() or None
    return {"upi_id": upi_id, "upi_qr_url": qr_url}
