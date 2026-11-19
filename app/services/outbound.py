"""Outbound channel guards — tests must never hit real SMTP/SMS/webhooks."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class OutboundMessage:
    channel: str
    to: str
    subject: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


_OUTBOX: List[OutboundMessage] = []


def clear_outbox() -> None:
    _OUTBOX.clear()


def get_outbox() -> List[OutboundMessage]:
    return list(_OUTBOX)


def record(channel: str, to: str, subject: str = "", **meta: Any) -> OutboundMessage:
    msg = OutboundMessage(channel=channel, to=to, subject=subject, meta=meta)
    _OUTBOX.append(msg)
    return msg


def test_outbound_forced() -> bool:
    """True when ENVIRONMENT=test or FORCE_TEST_OUTBOUND=1."""
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    if env == "test":
        return True
    return os.environ.get("FORCE_TEST_OUTBOUND", "").strip() in {"1", "true", "yes"}
