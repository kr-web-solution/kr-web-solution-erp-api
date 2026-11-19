from __future__ import annotations

from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Documented insecure placeholders — never allowed outside local/development.
_INSECURE_SECRET_DEFAULTS = frozenset(
    {
        "5678402f1e2851e0d04db3f7f6117e188360cb31c07262cf4f6722eeaa0510d7",
        "change-this-to-a-very-secure-random-secret-key-32-chars-min",
        "changeme",
        "secret",
        "dev-secret",
    }
)
_INSECURE_ENCRYPTION_DEFAULTS = frozenset(
    {
        "Qlhs7DPpurqcfoT6JENui-kFxgVjkywqzwC-iEFcWwE=",
        "32-character-base64-key-for-encrypting-smtp-passwords==",
    }
)
_LOCAL_ENVS = frozenset({"local", "development", "dev", "test"})
_MIN_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "KR Workspace CRM + ERP API"
    API_VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    PRODUCTION: bool = False

    # Security & JWT — no usable hardcoded secrets; set via environment.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    ALGORITHM: str = "HS256"
    COOKIE_NAME: str = "kr_access_token"
    ENCRYPTION_KEY: str = ""

    # Bootstrap super admin (created only when users table is empty)
    SUPER_ADMIN_EMAIL: Optional[str] = None
    SUPER_ADMIN_PASSWORD: Optional[str] = None
    SUPER_ADMIN_NAME: Optional[str] = "KR WEB SOLUTION ADMIN"
    # Deprecated aliases (still accepted from old .env files)
    FIRST_SUPERUSER_EMAIL: Optional[str] = None
    FIRST_SUPERUSER_PASSWORD: Optional[str] = None
    FIRST_SUPERUSER_NAME: Optional[str] = None

    # Invoices & Notifications
    INVOICE_CC_EMAIL: Optional[str] = None

    # SMTP Configuration
    SMTP_SERVER: Optional[str] = None
    SMTP_PORT: int = 465
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_CONNECTION: Optional[str] = "SSL"
    BILLING_MAIL_NAME: Optional[str] = "KR Web Solution – Billing Team"
    BILLING_FROM_MAIL: Optional[str] = "billing@krwebsolution.com"

    # Database: Pure PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://postgres:udhaya@localhost:5432/kr-web-solution-erp"

    # CORS & Web
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    FRONTEND_URL: str = "http://localhost:3000"

    # Object storage (S3-compatible)
    STORAGE_BACKEND: str = "local"
    S3_ENDPOINT: Optional[str] = None
    S3_BUCKET: Optional[str] = None
    S3_PUBLIC_BUCKET: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_REGION: str = "ap-south-1"
    S3_CONSOLE_CORS_ORIGINS: Optional[str] = None

    # Google OAuth (Sign in with Google — ID token verification)
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None

    # Microsoft Entra ID / Outlook (invite-only ID token verification)
    MICROSOFT_CLIENT_ID: Optional[str] = None
    MICROSOFT_TENANT_ID: str = "common"
    MICROSOFT_CLIENT_SECRET: Optional[str] = None

    @property
    def is_local_environment(self) -> bool:
        if self.PRODUCTION:
            return False
        return (self.ENVIRONMENT or "").strip().lower() in _LOCAL_ENVS

    @property
    def bootstrap_admin_email(self) -> Optional[str]:
        return self.SUPER_ADMIN_EMAIL or self.FIRST_SUPERUSER_EMAIL

    @property
    def bootstrap_admin_password(self) -> Optional[str]:
        return self.SUPER_ADMIN_PASSWORD or self.FIRST_SUPERUSER_PASSWORD

    @property
    def bootstrap_admin_name(self) -> str:
        return (
            self.SUPER_ADMIN_NAME
            or self.FIRST_SUPERUSER_NAME
            or "KR WEB SOLUTION ADMIN"
        )

    @model_validator(mode="after")
    def validate_security_keys(self) -> "Settings":
        """Refuse insecure or missing keys outside local/dev/test."""
        errors = validate_security_keys(
            secret_key=self.SECRET_KEY,
            encryption_key=self.ENCRYPTION_KEY,
            environment=self.ENVIRONMENT,
            production=self.PRODUCTION,
        )
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validate_security_keys(
    *,
    secret_key: str,
    encryption_key: str,
    environment: str,
    production: bool,
) -> list[str]:
    """
    Return human-readable problems (variable names only — never key values).

    Local/dev/test may start with empty keys only if you set them in .env;
    we still reject known insecure published defaults everywhere.
    Non-local environments always require strong keys.
    """
    env = (environment or "").strip().lower()
    is_local = (not production) and env in _LOCAL_ENVS
    errors: list[str] = []

    secret = (secret_key or "").strip()
    enc = (encryption_key or "").strip()

    if secret in _INSECURE_SECRET_DEFAULTS:
        errors.append(
            "SECRET_KEY is set to a known insecure default; generate a new one "
            "(openssl rand -hex 32)"
        )
    elif not secret:
        if not is_local:
            errors.append(
                "SECRET_KEY is missing; set a strong value "
                "(openssl rand -hex 32) before starting in this environment"
            )
    elif len(secret) < _MIN_SECRET_LEN:
        errors.append(
            f"SECRET_KEY is too short (minimum {_MIN_SECRET_LEN} characters)"
        )

    if enc in _INSECURE_ENCRYPTION_DEFAULTS:
        errors.append(
            "ENCRYPTION_KEY is set to a known insecure default; generate a Fernet key "
            "(python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
        )
    elif not enc:
        if not is_local:
            errors.append(
                "ENCRYPTION_KEY is missing; set a Fernet key before starting in this environment"
            )
    else:
        # Validate Fernet format when provided
        try:
            from cryptography.fernet import Fernet

            Fernet(enc.encode("utf-8"))
        except Exception:
            errors.append(
                "ENCRYPTION_KEY is not a valid Fernet key "
                "(python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
            )

    return errors


settings = Settings()
