from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Security & JWT
    SECRET_KEY: str = "5678402f1e2851e0d04db3f7f6117e188360cb31c07262cf4f6722eeaa0510d7"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    ALGORITHM: str = "HS256"
    COOKIE_NAME: str = "kr_access_token"
    ENCRYPTION_KEY: str = "Qlhs7DPpurqcfoT6JENui-kFxgVjkywqzwC-iEFcWwE="

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


settings = Settings()
