"""Type-aware Terms & Conditions resolver for KR Web Solution invoices."""

from __future__ import annotations

import re
from typing import Any, Optional


def _parse_terms(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    chunks = re.split(r"\n+|(?<=\.)\s+(?=[A-Z0-9])", str(raw).strip())
    out: list[str] = []
    for chunk in chunks:
        line = re.sub(r"^\d+[\)\.\-\s]+", "", chunk).strip()
        if line:
            out.append(line)
    return out


DEFAULT_TERMS_BY_TYPE: dict[str, list[str]] = {
    "custom_dev": [
        "This invoice is issued by KR Web Solution for the services described herein.",
        "Deliverables are limited to the approved project scope and specifications.",
        "Additional features, modifications, integrations, and change requests may require separate estimates and approval.",
        "Project timelines depend on timely customer feedback, content, access credentials, and approvals.",
        "Payments must be made through the officially communicated bank / UPI channels only.",
        "Invoice discrepancies should be communicated within seven calendar days of receipt.",
        "Source code ownership and intellectual property rights are governed by the applicable project agreement.",
    ],
    "amc": [
        "Maintenance coverage applies only during the agreed service period.",
        "AMC covers only the services specified in the approved agreement.",
        "New features, redesigns, and out-of-scope development are chargeable separately.",
        "Support response times are subject to the agreed service-level arrangement.",
        "Third-party hosting, domain renewal, and software licenses are excluded unless explicitly included.",
        "Payments must be completed by the specified due date.",
    ],
    "maintenance": [
        "Maintenance coverage applies only during the agreed service period.",
        "Service covers only the items specified in the approved agreement.",
        "New features and out-of-scope work are chargeable separately.",
        "Customers should maintain appropriate backups of critical business data.",
        "Payments must be completed by the specified due date.",
    ],
    "hosting": [
        "Hosting and cloud services are subject to underlying infrastructure provider availability.",
        "Resource allowances follow the selected plan; excess usage may attract additional charges.",
        "Customers must maintain secure access credentials.",
        "Backup and restoration commitments are governed by the selected service plan.",
        "Payments must be completed by the specified due date.",
    ],
    "licensing": [
        "Software licenses and subscriptions are subject to their respective validity periods.",
        "Activation depends on the relevant software provider.",
        "Renewal charges are separate unless explicitly included.",
        "Refunds and transfers are governed by the provider's policies and applicable agreements.",
    ],
    "services": [
        "This invoice is issued by KR Web Solution for the services described herein.",
        "Services are governed by the mutually agreed quotation or service agreement.",
        "Payments must be made through the officially communicated payment channels.",
        "Invoice discrepancies should be communicated within seven calendar days of receipt.",
    ],
    "late_fee": [
        "This late-fee invoice relates to the overdue parent invoice referenced herein.",
        "Late payment charges apply only as agreed and specified.",
        "Outstanding balances remain payable according to the agreed payment schedule.",
        "Payments must be made through the officially communicated payment channels.",
    ],
    "credit_note": [
        "This credit note adjusts the referenced parent invoice.",
        "No payment is requested against this credit note.",
        "Retain this document for your accounting records.",
    ],
    "debit_note": [
        "This debit note raises an additional charge on the referenced parent invoice.",
        "Payment is due as stated on this debit note.",
        "Retain this document for your accounting records.",
    ],
    "advance": [
        "This advance invoice is adjustable against future project billing as agreed.",
        "Deliverables and timelines remain subject to the approved project scope.",
        "Payments must be made through the officially communicated payment channels.",
    ],
    "balance": [
        "This balance invoice settles remaining amounts against the referenced project billing.",
        "Previous advances and payments are accounted for as per the project ledger.",
        "Payments must be completed by the specified due date.",
    ],
    "mixed": [
        "This invoice covers multiple service categories as listed in the line items.",
        "Each service remains governed by its applicable project or service agreement.",
        "Payments must be made through the officially communicated payment channels.",
        "Invoice discrepancies should be communicated within seven calendar days of receipt.",
    ],
}

_ALIASES = {
    "development": "custom_dev",
    "dev": "custom_dev",
    "project": "custom_dev",
    "mnt": "amc",
}


def normalize_invoice_type(invoice_type: Optional[str]) -> str:
    key = (invoice_type or "services").strip().lower()
    return _ALIASES.get(key, key)


def resolve_invoice_terms(
    *,
    invoice_type: Optional[str],
    invoice_notes: Optional[str] = None,
    settings: Any = None,
    status: Optional[str] = None,
) -> list[str]:
    """
    Priority:
    1. Non-empty invoice.notes (parsed into clauses)
    2. settings.invoice_terms_by_type[type]
    3. Curated defaults for type
    4. settings.payment_terms
    """
    notes = (invoice_notes or "").strip()
    if notes:
        parsed = _parse_terms(notes)
        if parsed:
            return parsed[:16]

    key = normalize_invoice_type(invoice_type)
    by_type = {}
    if settings is not None:
        raw = getattr(settings, "invoice_terms_by_type", None) or {}
        if isinstance(raw, dict):
            by_type = raw

    configured = by_type.get(key) or by_type.get(invoice_type or "")
    if isinstance(configured, list) and configured:
        return [str(c).strip() for c in configured if str(c).strip()][:16]
    if isinstance(configured, str) and configured.strip():
        return _parse_terms(configured)[:16]

    defaults = DEFAULT_TERMS_BY_TYPE.get(key)
    if defaults:
        return list(defaults)

    payment_terms = getattr(settings, "payment_terms", None) if settings else None
    if payment_terms and str(payment_terms).strip():
        return _parse_terms(str(payment_terms))[:16]

    # Minimal fallback by status
    st = (status or "").lower()
    if st == "paid":
        return [
            "This invoice has been marked as paid. No further payment is due.",
            "Retain this document for your accounting records.",
        ]
    return [
        "This invoice is issued by KR Web Solution for the services described herein.",
        "Payments must be completed by the specified due date.",
    ]
