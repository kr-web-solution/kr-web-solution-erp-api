"""Send invite set-password emails."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.security import create_invite_token
from ..models import EmailTemplate, Settings as SettingsModel, User
from ..services.email import send_email_async
from ..services.email_html import build_user_invite_email, fill_placeholders

ROLE_LABELS = {
    "admin": "Admin",
    "founder_ceo": "Founder CEO",
    "worker": "Worker",
    "client": "Client",
    "lead": "Lead",
}


async def send_user_invite_email(db: AsyncSession, user: User) -> str:
    """Mint invite token, email set-password link. Returns invite link (for dev)."""
    token = create_invite_token(user.email)
    invite_link = f"{settings.FRONTEND_URL}/set-password?token={token}"

    st = (
        await db.execute(select(SettingsModel).where(SettingsModel.id == "default"))
    ).scalars().first()
    tpl = (
        await db.execute(select(EmailTemplate).where(EmailTemplate.id == "user_invite"))
    ).scalars().first()

    role_label = ROLE_LABELS.get(user.role, user.role)
    subject_default, html, plain = build_user_invite_email(
        company_name=(st.company_name if st else "KR Web Solution"),
        user_name=user.full_name,
        invite_link=invite_link,
        role_label=role_label,
        company_address=(st.company_address if st else "") or "",
        company_logo_url=(st.company_logo_url if st else "") or "/brand/krws-mark.png",
    )
    subject = (
        fill_placeholders(
            tpl.subject,
            {
                "company_name": st.company_name if st else "KR Web Solution",
                "user_name": user.full_name,
                "role_label": role_label,
            },
        )
        if tpl and tpl.subject
        else subject_default
    )

    if st and st.smtp_host:
        try:
            await send_email_async(
                to_email=user.email,
                subject=subject,
                body=html,
                plain_body=plain,
                settings=st,
                is_html=True,
            )
        except Exception:
            pass

    user.invite_sent_at = datetime.now(timezone.utc)
    return invite_link
