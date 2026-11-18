"""Email domain rules for staff vs client/lead invites and OAuth."""

from __future__ import annotations

from fastapi import HTTPException, status

from ..core.config import settings
from ..core.roles_catalog import ROLE_ADMIN, ROLE_CLIENT, ROLE_FOUNDER, ROLE_LEAD, ROLE_WORKER

COMPANY_DOMAIN = "krwebsolution.com"
STAFF_ROLES = {ROLE_ADMIN, ROLE_FOUNDER, ROLE_WORKER}
EXTERNAL_ROLES = {ROLE_CLIENT, ROLE_LEAD}


def normalize_email(email: str) -> str:
    return (email or "").lower().strip()


def email_domain(email: str) -> str:
    e = normalize_email(email)
    if "@" not in e:
        return ""
    return e.rsplit("@", 1)[-1]


def is_company_email(email: str) -> bool:
    return email_domain(email) == COMPANY_DOMAIN


def is_bootstrap_super_admin_email(email: str) -> bool:
    """Bootstrap SUPER_ADMIN may use any domain (e.g. Gmail) forever."""
    bootstrap = normalize_email(settings.bootstrap_admin_email or "")
    if not bootstrap:
        return False
    return normalize_email(email) == bootstrap


def validate_email_for_role(email: str, role: str) -> None:
    """Raise HTTP 400 if email/role combination violates domain policy."""
    e = normalize_email(email)
    if not e or "@" not in e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email address is required",
        )

    if is_bootstrap_super_admin_email(e):
        return

    if role in STAFF_ROLES:
        if not is_company_email(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Staff accounts must use @{COMPANY_DOMAIN}",
            )
        return

    if role in EXTERNAL_ROLES:
        if is_company_email(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Client and lead accounts must not use @{COMPANY_DOMAIN}",
            )
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown role for email policy: {role}",
    )


def assert_oauth_email_allowed(email: str, role: str) -> None:
    """Same domain rules after invite-only OAuth email match."""
    validate_email_for_role(email, role)
