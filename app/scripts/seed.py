"""
Idempotent seeder: settings, email templates, and four-role login accounts.
No demo projects / invoices / pipeline / leads.
"""
import os
from sqlalchemy import select, text
from ..core.database import AsyncSessionLocal, init_db
from ..core.config import settings
from ..core.security import get_password_hash, encrypt_secret
from ..models import (
    User,
    Client,
    Settings as SettingsModel,
    EmailTemplate,
)
from ..core.roles_catalog import ROLE_ADMIN, ROLE_FOUNDER, ROLE_WORKER, ROLE_CLIENT
from ..core.lookups_catalog import PARTY_TYPES
from ..services.user_identity import ensure_roles_seeded, apply_role_to_user

PARTY_ID_BY_CODE = {r["code"]: r["id"] for r in PARTY_TYPES}


# Fixed demo login accounts (documented in README)
ADMIN_EMAIL = "admin@krwebsolution.com"
ADMIN_PASSWORD = "AdminKR@2026"
ADMIN_USER_ID = "usr_admin"
WORKER_EMAIL = "worker@krwebsolution.com"
WORKER_PASSWORD = "WorkerKR@2026"
CLIENT_EMAIL = "client@asterretail.com"
CLIENT_PASSWORD = "ClientKR@2026"
CLIENT_ID = "cli_aster"
WORKER_ID = "usr_worker"
CLIENT_USER_ID = "usr_client_aster"


async def purge_business_data(session) -> None:
    """Remove all CRM/ERP operational rows; keep settings, templates, and four login users/client."""
    # Order respects FK dependencies (children first)
    await session.execute(text("DELETE FROM engagement_documents"))
    await session.execute(text("DELETE FROM engagement_checklist_items"))
    await session.execute(text("DELETE FROM project_feature_docs"))
    await session.execute(text("DELETE FROM project_features"))
    await session.execute(text("DELETE FROM project_checklist_items"))
    await session.execute(text("DELETE FROM project_workers"))
    await session.execute(text("DELETE FROM late_fee_actions"))
    await session.execute(text("DELETE FROM invoice_items"))
    # Clear project invoice FKs before deleting invoices
    await session.execute(
        text(
            "UPDATE projects SET advance_invoice_id = NULL, balance_invoice_id = NULL, engagement_id = NULL"
        )
    )
    await session.execute(
        text(
            "UPDATE engagements SET advance_invoice_id = NULL, final_invoice_id = NULL, "
            "project_id = NULL, recurring_invoice_id = NULL"
        )
    )
    await session.execute(text("UPDATE invoices SET parent_invoice_id = NULL, recurring_id = NULL"))
    await session.execute(text("DELETE FROM invoices"))
    await session.execute(text("DELETE FROM recurring_invoices"))
    await session.execute(text("DELETE FROM engagements"))
    await session.execute(text("DELETE FROM projects"))
    await session.execute(text("DELETE FROM notes"))
    await session.execute(text("DELETE FROM leads"))
    # Extra clients beyond the portal login client
    await session.execute(text("DELETE FROM clients WHERE id <> :cid"), {"cid": CLIENT_ID})
    # Keep only the four role logins (admin, founder, worker, client)
    await session.execute(
        text(
            "DELETE FROM users WHERE email NOT IN (:f, :a, :w, :c)"
        ),
        {
            "f": settings.FIRST_SUPERUSER_EMAIL or "founder@krwebsolution.com",
            "a": ADMIN_EMAIL,
            "w": WORKER_EMAIL,
            "c": CLIENT_EMAIL,
        },
    )
    # Reset invoice counter
    await session.execute(
        text("UPDATE settings SET invoice_next_number = 1001 WHERE id = 'default'")
    )


def _make_user(
    *,
    user_id: str,
    email: str,
    password: str,
    full_name: str,
    role_code: str,
    client_id: str | None = None,
    lead_id: str | None = None,
    phone: str | None = None,
    designation: str | None = None,
) -> User:
    user = User(
        id=user_id,
        email=email,
        password_hash=get_password_hash(password),
        full_name=full_name,
        phone=phone,
        designation=designation,
        is_active=True,
        role_id="rol_worker",
        role="worker",
        party_type="staff",
        party_type_id=PARTY_ID_BY_CODE["staff"],
    )
    apply_role_to_user(user, role_code, client_id=client_id, lead_id=lead_id)
    return user


async def seed_login_accounts(session) -> None:
    """Ensure admin + client org + worker + client-portal logins exist."""
    await ensure_roles_seeded(session)

    existing_client = (
        await session.execute(select(Client).where(Client.id == CLIENT_ID))
    ).scalars().first()
    if not existing_client:
        session.add(
            Client(
                id=CLIENT_ID,
                name="Aster Retail Technologies",
                primary_contact_name="Priya Natarajan",
                email=CLIENT_EMAIL,
                phone="+91 98765 41001",
                address="42, Brough Road, Erode, Tamil Nadu 638001",
                gstin="33AABCA1234D1Z5",
                notes="Client portal login company.",
            )
        )
        await session.flush()

    admin = (
        await session.execute(select(User).where(User.email == ADMIN_EMAIL))
    ).scalars().first()
    if not admin:
        session.add(
            _make_user(
                user_id=ADMIN_USER_ID,
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                full_name="Workspace Admin",
                role_code=ROLE_ADMIN,
            )
        )
    else:
        admin.password_hash = get_password_hash(ADMIN_PASSWORD)
        admin.full_name = "Workspace Admin"
        apply_role_to_user(admin, ROLE_ADMIN)
        admin.is_active = True

    worker = (
        await session.execute(select(User).where(User.email == WORKER_EMAIL))
    ).scalars().first()
    if not worker:
        session.add(
            _make_user(
                user_id=WORKER_ID,
                email=WORKER_EMAIL,
                password=WORKER_PASSWORD,
                full_name="Vignesh Kumar",
                role_code=ROLE_WORKER,
                phone="+91 90000 10002",
                designation="Developer",
            )
        )
    else:
        worker.password_hash = get_password_hash(WORKER_PASSWORD)
        apply_role_to_user(worker, ROLE_WORKER)
        worker.is_active = True

    client_user = (
        await session.execute(select(User).where(User.email == CLIENT_EMAIL))
    ).scalars().first()
    if not client_user:
        session.add(
            _make_user(
                user_id=CLIENT_USER_ID,
                email=CLIENT_EMAIL,
                password=CLIENT_PASSWORD,
                full_name="Priya Natarajan",
                role_code=ROLE_CLIENT,
                client_id=CLIENT_ID,
                phone="+91 98765 41001",
                designation="Operations Lead",
            )
        )
    else:
        client_user.password_hash = get_password_hash(CLIENT_PASSWORD)
        apply_role_to_user(client_user, ROLE_CLIENT, client_id=CLIENT_ID)
        client_user.is_active = True


async def seed_database_async():
    """
    Initializes:
    1. Settings singleton
    2. Core email templates
    3. Founder CEO from env
    4. Admin + worker + client login accounts (no demo CRM/ERP rows)
    """
    await init_db()

    async with AsyncSessionLocal() as session:
        # 1. Settings
        res_settings = await session.execute(select(SettingsModel).where(SettingsModel.id == "default"))
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
                smtp_password_encrypted=encrypt_secret(settings.SMTP_PASSWORD) if settings.SMTP_PASSWORD else None,
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
                db_settings.smtp_use_tls = (settings.SMTP_CONNECTION != "SSL")
                if settings.BILLING_MAIL_NAME:
                    db_settings.smtp_from_name = settings.BILLING_MAIL_NAME
                if settings.BILLING_FROM_MAIL:
                    db_settings.smtp_from_email = settings.BILLING_FROM_MAIL

        # 2. Email templates (HTML layout is rendered in code; subjects stay editable)
        from ..services.email_html import DEFAULT_EMAIL_TEMPLATES

        for t_id, subj, body in DEFAULT_EMAIL_TEMPLATES:
            t_res = await session.execute(select(EmailTemplate).where(EmailTemplate.id == t_id))
            existing = t_res.scalars().first()
            if not existing:
                session.add(EmailTemplate(id=t_id, subject=subj, body=body))
            else:
                # Upgrade legacy plain-text bodies to managed HTML markers
                if not (existing.body or "").startswith("HTML_MANAGED:"):
                    existing.subject = subj
                    existing.body = body

        await ensure_roles_seeded(session)

        # 3. Founder CEO
        founder_email = settings.FIRST_SUPERUSER_EMAIL or os.environ.get("FIRST_SUPERUSER_EMAIL")
        founder_pwd = settings.FIRST_SUPERUSER_PASSWORD or os.environ.get("FIRST_SUPERUSER_PASSWORD")
        if founder_email and founder_pwd:
            u_res = await session.execute(select(User).where(User.email == founder_email))
            founder = u_res.scalars().first()
            if not founder:
                session.add(
                    _make_user(
                        user_id="usr_founder",
                        email=founder_email,
                        password=founder_pwd,
                        full_name=settings.FIRST_SUPERUSER_NAME or "Karthik Raja",
                        role_code=ROLE_FOUNDER,
                        designation="Founder CEO",
                    )
                )
            else:
                founder.password_hash = get_password_hash(founder_pwd)
                apply_role_to_user(founder, ROLE_FOUNDER)
                founder.is_active = True
            await session.flush()

        # 4. Login accounts only (no demo projects / invoices / pipeline)
        await seed_login_accounts(session)

        await session.commit()


async def wipe_and_reseed_logins_async() -> None:
    """One-shot: delete all business data, keep settings + four login roles."""
    await init_db()
    async with AsyncSessionLocal() as session:
        await purge_business_data(session)
        # Ensure founder still exists after purge
        founder_email = settings.FIRST_SUPERUSER_EMAIL or os.environ.get("FIRST_SUPERUSER_EMAIL")
        founder_pwd = settings.FIRST_SUPERUSER_PASSWORD or os.environ.get("FIRST_SUPERUSER_PASSWORD")
        if founder_email and founder_pwd:
            u_res = await session.execute(select(User).where(User.email == founder_email))
            founder = u_res.scalars().first()
            if not founder:
                session.add(
                    _make_user(
                        user_id="usr_founder",
                        email=founder_email,
                        password=founder_pwd,
                        full_name=settings.FIRST_SUPERUSER_NAME or "Karthik Raja",
                        role_code=ROLE_FOUNDER,
                        designation="Founder CEO",
                    )
                )
            else:
                founder.password_hash = get_password_hash(founder_pwd)
                apply_role_to_user(founder, ROLE_FOUNDER)
                founder.is_active = True
        await seed_login_accounts(session)
        await session.commit()


# Back-compat alias used by older imports / tests
async def seed_demo_business_data(session) -> None:
    await seed_login_accounts(session)
