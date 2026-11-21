"""Effective GST rate and optional statutory payroll deductions from Settings."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple

_PAISE = Decimal("1")


def _percent_of_paise(base_paise: int, percent: Any) -> int:
    """ROUND_HALF_UP to whole paise (one rounding rule for tax and statutory)."""
    amount = Decimal(int(base_paise or 0)) * (Decimal(str(percent or 0)) / Decimal("100"))
    return int(amount.quantize(_PAISE, rounding=ROUND_HALF_UP))


def gst_is_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "gst_enabled", False))


def normalize_state_code(value: Any) -> Optional[str]:
    raw = (str(value).strip() if value is not None else "")
    if not raw:
        return None
    # Prefer first two digits of GSTIN when a full GSTIN is passed.
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 2 and len(raw) >= 10:
        return digits[:2].zfill(2)
    if len(raw) <= 4 and raw.isdigit():
        return raw.zfill(2)
    if len(digits) == 2:
        return digits.zfill(2)
    return raw[:4].upper() if raw else None


def effective_tax_rate(settings: Any, requested: float | None = None) -> float:
    """Return tax % to apply. Always 0 when GST is disabled."""
    if not gst_is_enabled(settings):
        return 0.0
    if requested is not None:
        return max(0.0, float(requested))
    return max(0.0, float(getattr(settings, "default_tax_rate", 0) or 0))


def compute_tax_paise(subtotal_paise: int, tax_rate_percent: float) -> int:
    return _percent_of_paise(subtotal_paise, tax_rate_percent)


def split_gst_paise(
    tax_paise: int,
    *,
    supplier_state_code: Optional[str] = None,
    customer_state_code: Optional[str] = None,
    gst_enabled: bool = False,
) -> Dict[str, Any]:
    """
    Snapshot CGST/SGST/IGST from total tax_paise.
    Only splits when GST is enabled and both state codes are known.
    Does not invent tax when codes are missing.
    """
    tax = max(0, int(tax_paise or 0))
    supplier = normalize_state_code(supplier_state_code)
    customer = normalize_state_code(customer_state_code)
    if not gst_enabled or tax <= 0 or not supplier or not customer:
        return {
            "supply_type": None,
            "cgst_paise": 0,
            "sgst_paise": 0,
            "igst_paise": 0,
        }
    if supplier == customer:
        half = tax // 2
        other = tax - half
        return {
            "supply_type": "intra",
            "cgst_paise": half,
            "sgst_paise": other,
            "igst_paise": 0,
        }
    return {
        "supply_type": "inter",
        "cgst_paise": 0,
        "sgst_paise": 0,
        "igst_paise": tax,
    }


def apply_invoice_tax_snapshot(
    *,
    settings: Any,
    client: Any | None,
    subtotal_paise: int,
    tax_rate_percent: float,
    discount_paise: int = 0,
) -> Dict[str, Any]:
    """Compute header tax fields for create/update."""
    taxable = max(0, int(subtotal_paise or 0) - max(0, int(discount_paise or 0)))
    rate = effective_tax_rate(settings, tax_rate_percent)
    tax_paise = compute_tax_paise(taxable, rate) if gst_is_enabled(settings) else 0
    supplier_code = normalize_state_code(getattr(settings, "company_state_code", None))
    if not supplier_code:
        supplier_code = normalize_state_code(getattr(settings, "company_gstin", None))
    customer_code = normalize_state_code(getattr(client, "state_code", None) if client else None)
    if not customer_code and client is not None:
        customer_code = normalize_state_code(getattr(client, "gstin", None))
    split = split_gst_paise(
        tax_paise,
        supplier_state_code=supplier_code,
        customer_state_code=customer_code,
        gst_enabled=gst_is_enabled(settings),
    )
    place = None
    if client is not None:
        place = (getattr(client, "state", None) or "").strip() or None
    total = taxable + tax_paise
    return {
        "taxable_paise": taxable,
        "tax_rate_percent": rate,
        "tax_paise": tax_paise,
        "total_paise": total,
        "place_of_supply": place,
        **split,
    }


def statutory_deductions_paise(settings: Any, gross_paise: int) -> Tuple[int, Dict[str, int]]:
    """
    Optional PF / ESI / PT from Settings flags.
    Returns (total_paise, breakdown_paise).
    """
    gross = max(0, int(gross_paise or 0))
    breakdown: Dict[str, int] = {}
    total = 0

    if getattr(settings, "pf_enabled", False):
        pct = getattr(settings, "pf_employee_percent", 0) or 0
        if Decimal(str(pct)) > 0:
            amt = _percent_of_paise(gross, pct)
            breakdown["pf"] = amt
            total += amt

    if getattr(settings, "esi_enabled", False):
        pct = getattr(settings, "esi_employee_percent", 0) or 0
        if Decimal(str(pct)) > 0:
            amt = _percent_of_paise(gross, pct)
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
