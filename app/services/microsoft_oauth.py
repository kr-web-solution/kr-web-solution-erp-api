"""Verify Microsoft Entra ID / Outlook ID tokens (invite-only login)."""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from ..core.config import settings


def _issuer_for_tenant(tenant: str) -> list[str]:
    # v2.0 issuers
    return [
        f"https://login.microsoftonline.com/{tenant}/v2.0",
        f"https://sts.windows.net/{tenant}/",
    ]


def verify_microsoft_id_token(id_token: str) -> dict[str, Any]:
    if not settings.MICROSOFT_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft sign-in is not configured",
        )

    tenant = (settings.MICROSOFT_TENANT_ID or "common").strip()
    jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    try:
        jwks_client = PyJWKClient(jwks_url, cache_keys=True)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        # For "common" / "organizations", accept any tenant issuer matching the token tid
        unverified = jwt.decode(id_token, options={"verify_signature": False})
        tid = unverified.get("tid") or tenant
        issuers = _issuer_for_tenant(tid) + _issuer_for_tenant(tenant)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.MICROSOFT_CLIENT_ID,
            options={"verify_iss": False},
        )
        iss = claims.get("iss") or ""
        if iss not in issuers and not iss.startswith("https://login.microsoftonline.com/"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Microsoft token issuer",
            )
        return claims
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify Microsoft sign-in",
        ) from exc


def email_from_microsoft_claims(claims: dict[str, Any]) -> str:
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    )
    return str(email).lower().strip()
