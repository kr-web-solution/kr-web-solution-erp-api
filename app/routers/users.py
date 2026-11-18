from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core.dependencies import get_db, require_founder_ceo
from ..core.database import not_deleted
from ..core.config import settings
from ..core.security import get_password_hash, create_invite_token, unusable_password_hash
from ..models import User, Client, Lead, Settings, EmailTemplate
from ..schemas import UserResponse, UserCreate, UserUpdate
from ..services.audit import audit_create, audit_soft_delete, audit_update, snapshot_entity
from ..services.email import send_email_async
from ..services.email_policy import validate_email_for_role, normalize_email
from ..services.user_identity import (
    apply_role_to_user,
    ensure_roles_seeded,
    to_user_response,
    user_load_options,
)

router = APIRouter(prefix="/users", tags=["Users (Founder Only)"])


async def _send_invite_email(db: AsyncSession, user: User, invite_link: str) -> None:
    st_res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = st_res.scalars().first()
    tpl_res = await db.execute(select(EmailTemplate).where(EmailTemplate.id == "user_invite"))
    tpl = tpl_res.scalars().first()

    if not st or not st.smtp_host:
        return

    from ..services.email_html import build_user_invite_email, fill_placeholders

    subject_default, html, plain = build_user_invite_email(
        company_name=st.company_name,
        user_name=user.full_name,
        invite_link=invite_link,
        role_label=user.role,
        company_address=st.company_address or "",
        company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
    )
    subject = (
        fill_placeholders(
            tpl.subject,
            {"company_name": st.company_name, "user_name": user.full_name},
        )
        if tpl and tpl.subject
        else subject_default
    )
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
        # Invite user still created; link returned in non-production
        pass


@router.get("", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    result = await db.execute(
        select(User)
        .options(*user_load_options())
        .where(not_deleted(User))
        .order_by(User.created_at.desc())
    )
    return [to_user_response(u) for u in result.scalars().all()]


@router.get("/roles", response_model=List[dict])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    from ..models import Role

    await ensure_roles_seeded(db)
    rows = (
        await db.execute(select(Role).order_by(Role.sort_order, Role.code))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "description": r.description,
            "party_type": r.party_type,
            "is_system": r.is_system,
            "sort_order": r.sort_order,
        }
        for r in rows
    ]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    """Invite a user for any role — no founder-typed password; set-password email instead."""
    await ensure_roles_seeded(db)
    email = normalize_email(str(payload.email))
    validate_email_for_role(email, payload.role)

    existing = await db.execute(select(User).where(User.email == email, not_deleted(User)))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        )

    if payload.client_id:
        cli = (
            await db.execute(select(Client).where(Client.id == payload.client_id, not_deleted(Client)))
        ).scalars().first()
        if not cli:
            raise HTTPException(status_code=400, detail="client_id not found")
    if payload.lead_id:
        led = (
            await db.execute(select(Lead).where(Lead.id == payload.lead_id, not_deleted(Lead)))
        ).scalars().first()
        if not led:
            raise HTTPException(status_code=400, detail="lead_id not found")

    now = datetime.now(timezone.utc)
    new_user = User(
        email=email,
        password_hash=unusable_password_hash(),
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        designation=payload.designation,
        is_active=True,
        password_set_at=None,
        profile_completed_at=None,
        invite_sent_at=now,
        role_id="rol_worker",
        role="worker",
        party_type="staff",
        party_type_id="pty_staff",
    )
    apply_role_to_user(
        new_user,
        payload.role,
        client_id=payload.client_id,
        lead_id=payload.lead_id,
    )
    db.add(new_user)
    await db.flush()
    await audit_create(db, new_user, actor_id=current_user.id, request=request)

    invite_token = create_invite_token(email)
    invite_link = f"{settings.FRONTEND_URL}/set-password?token={invite_token}"
    await _send_invite_email(db, new_user, invite_link)

    await db.commit()

    loaded = (
        await db.execute(
            select(User).options(*user_load_options()).where(User.id == new_user.id)
        )
    ).scalars().first()
    resp = to_user_response(loaded)
    if not settings.PRODUCTION:
        resp.dev_invite_link = invite_link
    return resp


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    await ensure_roles_seeded(db)
    result = await db.execute(
        select(User).options(*user_load_options()).where(User.id == user_id, not_deleted(User))
    )
    target_user = result.scalars().first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    before = snapshot_entity(target_user)
    if payload.full_name is not None:
        target_user.full_name = payload.full_name.strip()
    if payload.phone is not None:
        target_user.phone = payload.phone
    if payload.designation is not None:
        target_user.designation = payload.designation
    if payload.is_active is not None:
        if target_user.id == current_user.id and not payload.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account",
            )
        target_user.is_active = payload.is_active
    if payload.password:
        target_user.password_hash = get_password_hash(payload.password)
        target_user.password_set_at = datetime.now(timezone.utc)

    if payload.client_id is not None:
        if payload.client_id:
            cli = (
                await db.execute(select(Client).where(Client.id == payload.client_id, not_deleted(Client)))
            ).scalars().first()
            if not cli:
                raise HTTPException(status_code=400, detail="client_id not found")
        target_user.client_id = payload.client_id or None
    if payload.lead_id is not None:
        if payload.lead_id:
            led = (
                await db.execute(select(Lead).where(Lead.id == payload.lead_id, not_deleted(Lead)))
            ).scalars().first()
            if not led:
                raise HTTPException(status_code=400, detail="lead_id not found")
        target_user.lead_id = payload.lead_id or None

    if payload.role is not None:
        validate_email_for_role(target_user.email, payload.role)
        apply_role_to_user(
            target_user,
            payload.role,
            client_id=payload.client_id if payload.client_id is not None else target_user.client_id,
            lead_id=payload.lead_id if payload.lead_id is not None else target_user.lead_id,
        )

    await audit_update(db, target_user, before, actor_id=current_user.id, request=request)
    await db.commit()
    loaded = (
        await db.execute(
            select(User).options(*user_load_options()).where(User.id == user_id)
        )
    ).scalars().first()
    return to_user_response(loaded)


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    result = await db.execute(select(User).where(User.id == user_id, not_deleted(User)))
    target_user = result.scalars().first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_user.is_active = False
    target_user.soft_delete()
    await audit_soft_delete(db, target_user, actor_id=current_user.id, request=request)
    await db.commit()
    return {"message": "User deactivated successfully"}
