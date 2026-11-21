"""Invoice email addressing: company To, contact person CC."""

from __future__ import annotations

from typing import Any, Optional, Tuple


def _norm(email: Optional[str]) -> Optional[str]:
    value = (email or "").strip()
    return value.lower() if value else None


def invoice_mail_targets(
    client: Any,
    settings: Any = None,
) -> Tuple[str, Optional[str]]:
    """
    Return (to_email, cc_email) for invoice mail.

    - To: company / billing email (client.email) — required by callers
    - CC: contact person email + optional settings.invoice_cc_email
      (deduped; never duplicates To)
    """
    to_raw = getattr(client, "email", None) if client is not None else None
    to_email = (to_raw or "").strip()
    to_key = _norm(to_email)

    cc_parts: list[str] = []
    seen: set[str] = set()
    if to_key:
        seen.add(to_key)

    def _add(raw: Optional[str]) -> None:
        value = (raw or "").strip()
        key = _norm(value)
        if not value or not key or key in seen:
            return
        seen.add(key)
        cc_parts.append(value)

    if client is not None:
        _add(getattr(client, "contact_email", None))

    if settings is not None:
        _add(getattr(settings, "invoice_cc_email", None))

    return to_email, (", ".join(cc_parts) if cc_parts else None)
