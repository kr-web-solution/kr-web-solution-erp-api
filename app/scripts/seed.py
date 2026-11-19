"""
Idempotent seeder for local/dev CLI only (python -m app.cli seed).

Production lifespan must NOT call this module's seed entrypoints.
Bootstrap admin: python -m app.cli bootstrap-admin
"""
from datetime import datetime, timezone

from sqlalchemy import select, func, text

from ..core.database import AsyncSessionLocal, init_db, not_deleted
from ..core.config import settings
from ..core.security import get_password_hash, encrypt_secret
from ..models import (
    User,
    Settings as SettingsModel,
    EmailTemplate,
)
from ..core.roles_catalog import ROLE_ADMIN
from ..core.lookups_catalog import PARTY_TYPES
from ..services.user_identity import ensure_roles_seeded, apply_role_to_user

PARTY_ID_BY_CODE = {r["code"]: r["id"] for r in PARTY_TYPES}

# Legacy dummy emails — soft-deleted only via explicit CLI seed (never on app start)
DUMMY_USER_EMAILS = (
    "admin@krwebsolution.com",
    "worker@krwebsolution.com",
    "client@asterretail.com",
    "founder@krwebsolution.com",
)


def _make_user(
    *,
    user_id: str | None = None,
    email: str,
    password: str,
    full_name: str,
    role_code: str,
    client_id: str | None = None,
    lead_id: str | None = None,
    phone: str | None = None,
    designation: str | None = None,
    password_set_at: datetime | None = None,
) -> User:
    kwargs = {}
    if user_id:
        kwargs["id"] = user_id
    user = User(
        **kwargs,
        email=email,
        password_hash=get_password_hash(password),
        full_name=full_name,
        phone=phone,
        designation=designation,
        is_active=True,
        password_set_at=password_set_at,
        profile_completed_at=None,
        role_id="rol_worker",
        role="worker",
        party_type="staff",
        party_type_id=PARTY_ID_BY_CODE["staff"],
    )
    apply_role_to_user(user, role_code, client_id=client_id, lead_id=lead_id)
    return user


async def soft_delete_dummy_users(session) -> None:
    """Ensure legacy demo logins cannot sign in (CLI seed only)."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(User).where(
            func.lower(User.email).in_([e.lower() for e in DUMMY_USER_EMAILS]),
            not_deleted(User),
        )
    )
    for user in result.scalars().all():
        user.is_active = False
        user.soft_delete()
        if user.deleted_at is None:
            user.deleted_at = now

    await session.execute(
        text(
            """
            UPDATE clients
            SET deleted_at = COALESCE(deleted_at, NOW())
            WHERE id = 'cli_aster'
              AND deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM users
                WHERE client_id = 'cli_aster'
                  AND deleted_at IS NULL
                  AND is_active = true
              )
            """
        )
    )


async def _admin_exists(session) -> bool:
    count = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(not_deleted(User), User.role == ROLE_ADMIN)
        )
    ).scalar_one()
    return bool(count and count > 0)


async def bootstrap_admin_if_missing(session) -> bool:
    """Create SUPER_ADMIN only when no admin exists. Never modifies other users.

    Returns True if a new admin row was inserted.
    """
    await ensure_roles_seeded(session)
    if await _admin_exists(session):
        return False

    email = (settings.bootstrap_admin_email or "").lower().strip()
    password = settings.bootstrap_admin_password
    name = (settings.bootstrap_admin_name or "KR WEB SOLUTION ADMIN").strip()
    if not email or not password:
        return False

    # Email already taken → do not modify that user
    existing = (
        await session.execute(select(User).where(func.lower(User.email) == email))
    ).scalars().first()
    if existing:
        return False

    now = datetime.now(timezone.utc)
    session.add(
        _make_user(
            user_id="usr_super_admin",
            email=email,
            password=password,
            full_name=name,
            role_code=ROLE_ADMIN,
            password_set_at=now,
        )
    )
    return True


async def bootstrap_admin_cli_async() -> bool:
    """CLI entry: idempotent admin create; guarded by caller."""
    await init_db()
    async with AsyncSessionLocal() as session:
        created = await bootstrap_admin_if_missing(session)
        await session.commit()
        return created


async def seed_database_async():
    """
    Local/dev CLI seed only:
    1. Settings singleton
    2. Core email templates
    3. Soft-delete dummy seed logins
    4. SUPER_ADMIN bootstrap when no admin exists
    """
    from ..core.db_target_guard import assert_migration_or_seed_allowed

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="seed")
    await init_db()

    async with AsyncSessionLocal() as session:
        res_settings = await session.execute(
            select(SettingsModel).where(SettingsModel.id == "default")
        )
        db_settings = res_settings.scalars().first()
        if not db_settings:
            db_settings = SettingsModel(
                id="default",
                company_name="KR Web Solution",
                company_address="14, Fairlands Main Road, Salem, Tamil Nadu 636016, India",
                company_gstin="33ABCDE1234F1Z5",
                company_logo_url="/brand/krws-mark.png",
                invoice_prefix="KRWS",
                invoice_next_number=1,
                invoice_sequences={},
                default_tax_rate=0.00,
                gst_enabled=False,
                pf_enabled=False,
                pf_employee_percent=0,
                esi_enabled=False,
                esi_employee_percent=0,
                pt_enabled=False,
                pt_paise=0,
                payment_terms="Payment due within 15 calendar days from the date of invoice. Late payments attract 1.5% interest per month.",
                bank_beneficiary_name="KR Web Solution",
                bank_name="HDFC Bank",
                bank_account_number="50200012345678",
                bank_ifsc="HDFC0001234",
                bank_upi_id="krwebsolution@hdfcbank",
                bank_upi_details="Beneficiary: KR Web Solution | Bank: HDFC Bank | A/C: 50200012345678 | IFSC: HDFC0001234 | UPI: krwebsolution@hdfcbank",
                smtp_host=settings.SMTP_SERVER or "smtp.krwebsolution.com",
                smtp_port=settings.SMTP_PORT or 465,
                smtp_username=settings.SMTP_USERNAME or "krwebsolution_own_smtp",
                smtp_password_encrypted=encrypt_secret(settings.SMTP_PASSWORD)
                if settings.SMTP_PASSWORD
                else None,
                smtp_use_tls=(settings.SMTP_CONNECTION != "SSL"),
                smtp_from_name=settings.BILLING_MAIL_NAME or "KR Web Solution – Billing Team",
                smtp_from_email=settings.BILLING_FROM_MAIL or "billing@krwebsolution.com",
            )
            session.add(db_settings)
        else:
            if settings.SMTP_SERVER:
                db_settings.smtp_host = settings.SMTP_SERVER
                db_settings.smtp_port = settings.SMTP_PORT or 465
                db_settings.smtp_username = settings.SMTP_USERNAME
                if settings.SMTP_PASSWORD:
                    db_settings.smtp_password_encrypted = encrypt_secret(settings.SMTP_PASSWORD)
                db_settings.smtp_use_tls = settings.SMTP_CONNECTION != "SSL"
                if settings.BILLING_MAIL_NAME:
                    db_settings.smtp_from_name = settings.BILLING_MAIL_NAME
                if settings.BILLING_FROM_MAIL:
                    db_settings.smtp_from_email = settings.BILLING_FROM_MAIL

        from ..services.email_html import DEFAULT_EMAIL_TEMPLATES

        for t_id, subj, body in DEFAULT_EMAIL_TEMPLATES:
            t_res = await session.execute(
                select(EmailTemplate).where(EmailTemplate.id == t_id)
            )
            existing = t_res.scalars().first()
            if not existing:
                session.add(EmailTemplate(id=t_id, subject=subj, body=body))
            else:
                if not (existing.body or "").startswith("HTML_MANAGED:"):
                    existing.subject = subj
                    existing.body = body

        await ensure_roles_seeded(session)
        await soft_delete_dummy_users(session)
        await bootstrap_admin_if_missing(session)
        await session.commit()


# Back-compat aliases used by older tests/scripts
async def bootstrap_super_admin_if_empty(session) -> None:
    await bootstrap_admin_if_missing(session)


async def wipe_and_reseed_logins_async() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        await soft_delete_dummy_users(session)
        await bootstrap_admin_if_missing(session)
        await session.commit()


async def seed_demo_business_data(session) -> None:
    await soft_delete_dummy_users(session)
    await bootstrap_admin_if_missing(session)
