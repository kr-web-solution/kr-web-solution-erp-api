"""Progressive profile completion helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..models import User


STAFF_REQUIRED = ("phone", "designation", "department")
EXTERNAL_REQUIRED = ("phone", "designation")


def required_profile_fields(user: User) -> tuple[str, ...]:
    if user.party_type == "staff":
        return STAFF_REQUIRED
    return EXTERNAL_REQUIRED


def missing_profile_fields(user: User) -> list[str]:
    missing: list[str] = []
    for field in required_profile_fields(user):
        value = getattr(user, field, None)
        if field == "designation":
            value = user.designation or user.job_title
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def is_profile_complete(user: User) -> bool:
    if user.profile_completed_at is not None:
        return True
    return len(missing_profile_fields(user)) == 0


def mark_profile_complete_if_ready(user: User) -> bool:
    """Set profile_completed_at when all required fields present. Returns True if complete."""
    if missing_profile_fields(user):
        return False
    if user.profile_completed_at is None:
        user.profile_completed_at = datetime.now(timezone.utc)
    return True


def password_is_set(user: User) -> bool:
    return user.password_set_at is not None
