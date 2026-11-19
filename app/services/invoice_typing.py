"""Resolve invoice header type/ledger from line kinds (supports mixed invoices)."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .invoice_numbers import service_code_for
from .project_money import AMC_TYPES, ledger_for

LINE_KIND_ALIASES = {
    "development": "custom_dev",
    "dev": "custom_dev",
    "custom_dev": "custom_dev",
    "project": "custom_dev",
    "advance": "advance",
    "balance": "balance",
    "maintenance": "amc",
    "amc": "amc",
    "mnt": "amc",
    "hosting": "hosting",
    "services": "services",
    "licensing": "licensing",
    "late_fee": "late_fee",
    "credit_note": "credit_note",
    "mixed": "mixed",
}


def normalize_line_kind(raw: Optional[str], fallback: str = "custom_dev") -> str:
    key = (raw or fallback or "custom_dev").strip().lower()
    if key == "maintenance":
        return "amc"
    if key in ("project", "development", "dev"):
        return "custom_dev"
    return LINE_KIND_ALIASES.get(key, key or fallback)


def _item_kind(item, default: str) -> str:
    if isinstance(item, dict):
        raw = item.get("line_kind")
    else:
        raw = getattr(item, "line_kind", None)
    return normalize_line_kind(raw, default)


def kinds_from_items(items: Sequence, *, default_type: str = "custom_dev") -> list[str]:
    default = normalize_line_kind(default_type)
    kinds = [_item_kind(item, default) for item in (items or [])]
    return kinds or [default]


def resolve_invoice_typing(
    *,
    invoice_type: Optional[str],
    items: Iterable = (),
) -> tuple[str, str, str]:
    """Return (invoice_type, ledger, number_service_hint).

    number_service_hint is passed to service_code_for / allocate.
    Mixed line kinds → type=mixed, ledger=mixed, number MIX.
    """
    raw = normalize_line_kind(invoice_type or "custom_dev")
    if raw == "mixed":
        return "mixed", "mixed", "mixed"

    kinds = kinds_from_items(list(items), default_type=raw)
    unique = sorted(set(kinds))
    if len(unique) > 1:
        return "mixed", "mixed", "mixed"

    kind = unique[0]
    header = "amc" if kind in AMC_TYPES else kind
    return header, ledger_for(header), header
