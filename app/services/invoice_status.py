"""Canonical invoice status transitions and lock rules."""

from __future__ import annotations

from typing import Any, Optional

from ..core.exceptions import BusinessRuleError, ConflictException

TERMINAL_STATUSES = frozenset({"paid", "cancelled", "void"})
LOCKED_STATUSES = TERMINAL_STATUSES  # cannot edit or soft-delete

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"sent", "paid", "cancelled", "void"}),
    "sent": frozenset({"paid", "overdue", "cancelled", "void"}),
    "overdue": frozenset({"paid", "sent", "cancelled", "void"}),
    "paid": frozenset(),
    "cancelled": frozenset(),
    "void": frozenset(),
}


class InvoiceStatusError(BusinessRuleError):
    """Illegal invoice status transition or lock violation."""


def is_locked(status: Optional[str]) -> bool:
    return (status or "") in LOCKED_STATUSES


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    if can_transition(current, target):
        return
    if current in TERMINAL_STATUSES:
        raise InvoiceStatusError(f"Cannot change a {current} invoice to {target}")
    raise InvoiceStatusError(f"Cannot transition invoice from {current} to {target}")


def assert_editable(status: str) -> None:
    if status == "paid":
        raise ConflictException("Paid invoices are locked. Issue a credit note instead.")
    if status in ("cancelled", "void"):
        raise InvoiceStatusError("Cannot edit a cancelled invoice")


def assert_deletable(status: str) -> None:
    if status == "paid":
        raise InvoiceStatusError("Cannot delete paid invoice")
    if status in ("cancelled", "void"):
        raise InvoiceStatusError("Cannot delete a cancelled invoice")


def apply_status(invoice: Any, target: str) -> None:
    assert_transition(invoice.status, target)
    invoice.status = target


def assert_can_pay(invoice: Any) -> None:
    if invoice.status == "paid":
        return
    if invoice.status in ("cancelled", "void"):
        raise InvoiceStatusError("Cannot pay a cancelled invoice")
    assert_transition(invoice.status, "paid")


def assert_can_cancel(invoice: Any) -> None:
    if getattr(invoice, "invoice_type", None) == "credit_note":
        raise InvoiceStatusError("Cancel the credit note by deleting it if still unpaid")
    if invoice.status == "paid":
        raise InvoiceStatusError("Cannot cancel a paid invoice. Issue a credit note instead.")
    if invoice.status in ("cancelled", "void"):
        raise InvoiceStatusError("Invoice is already cancelled")
    assert_transition(invoice.status, "cancelled")


def assert_can_send(invoice: Any) -> None:
    if invoice.status in ("cancelled", "void"):
        raise InvoiceStatusError("Cannot send a cancelled invoice")
