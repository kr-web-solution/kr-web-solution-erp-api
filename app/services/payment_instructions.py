"""Build printable bank / UPI / wire payment instructions from company settings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _s(value: Any) -> Optional[str]:
    text = (str(value).strip() if value is not None else "")
    return text or None


def resolve_payment_instructions(
    settings: Any,
    *,
    upi_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return structured payment instruction fields for invoices.

    Keys: beneficiary, bank_name, account_number, ifsc, upi_id, paypal_id,
    wire_instructions (list[str]), has_any (bool)
    """
    if settings is None:
        return {
            "beneficiary": None,
            "bank_name": None,
            "account_number": None,
            "ifsc": None,
            "upi_id": None,
            "paypal_id": None,
            "wire_instructions": [],
            "has_any": False,
        }

    beneficiary = _s(getattr(settings, "bank_beneficiary_name", None))
    bank_name = _s(getattr(settings, "bank_name", None))
    account_number = _s(getattr(settings, "bank_account_number", None))
    ifsc = _s(getattr(settings, "bank_ifsc", None))
    resolved_upi = _s(upi_id) or _s(getattr(settings, "bank_upi_id", None))
    paypal_id = _s(getattr(settings, "bank_paypal_id", None))
    custom_wire = _s(getattr(settings, "bank_wire_instructions", None))

    wire: List[str] = []
    if custom_wire:
        for line in custom_wire.replace("\r\n", "\n").split("\n"):
            cleaned = line.strip()
            if cleaned:
                wire.append(cleaned)
    else:
        # Default KR Web Solution wire / transfer guidance when bank fields exist.
        if account_number or ifsc:
            wire.append(
                "For NEFT / RTGS / IMPS wire transfer, use the beneficiary name, "
                "account number, and IFSC exactly as shown above."
            )
        if resolved_upi:
            wire.append(
                "For UPI, pay to the UPI ID shown above (or scan the QR when available) "
                "and share the UTR / reference with accounts."
            )
        if paypal_id:
            wire.append(
                "For PayPal, send to the PayPal ID / email shown above and share the "
                "transaction ID with accounts."
            )
        if account_number or resolved_upi or paypal_id:
            wire.append(
                "Please mention the invoice number in the payment narration / remarks."
            )

    has_any = bool(
        beneficiary
        or bank_name
        or account_number
        or ifsc
        or resolved_upi
        or paypal_id
        or wire
    )
    return {
        "beneficiary": beneficiary,
        "bank_name": bank_name,
        "account_number": account_number,
        "ifsc": ifsc,
        "upi_id": resolved_upi,
        "paypal_id": paypal_id,
        "wire_instructions": wire,
        "has_any": has_any,
    }
