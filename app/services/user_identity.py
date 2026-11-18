"""Helpers to attach role + party (staff / client / lead) on users."""

from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.roles_catalog import (
    ROLE_CODES,
    party_type_for_role,
    role_id_for_code,
)
from ..core.lookups_catalog import PARTY_TYPES
from ..models import User, Role, Client, Lead
from ..schemas import (
    UserResponse,
    RoleBrief,
    ClientPartyBrief,
    LeadPartyBrief,
)

PARTY_ID_BY_CODE = {r["code"]: r["id"] for r in PARTY_TYPES}


async def ensure_roles_seeded(db: AsyncSession) -> None:
    from ..services.lookups_seed import seed_all_lookups

    await seed_all_lookups(db)


def apply_role_to_user(
    user: User,
    role_code: str,
    *,
    client_id: Optional[str] = None,
    lead_id: Optional[str] = None,
) -> None:
    if role_code not in ROLE_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role must be one of: {', '.join(ROLE_CODES)}",
        )
    party = party_type_for_role(role_code)
    user.role = role_code
    user.role_id = role_id_for_code(role_code)
    user.party_type = party
    user.party_type_id = PARTY_ID_BY_CODE[party]

    if party == "client":
        cid = client_id if client_id is not None else user.client_id
        if not cid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Client users must be linked to a client company (client_id).",
            )
        user.client_id = cid
        user.lead_id = None
    elif party == "lead":
        lid = lead_id if lead_id is not None else user.lead_id
        if not lid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Lead users must be linked to a lead record (lead_id).",
            )
        user.lead_id = lid
        user.client_id = None
    else:
        # staff
        if client_id is not None:
            user.client_id = client_id
        else:
            user.client_id = None
        if lead_id is not None:
            user.lead_id = lead_id
        else:
            user.lead_id = None


def to_user_response(user: User) -> UserResponse:
    from .profile_completion import (
        is_profile_complete,
        missing_profile_fields,
        password_is_set,
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        role_id=user.role_id,
        party_type=user.party_type,
        client_id=user.client_id,
        lead_id=user.lead_id,
        is_active=user.is_active,
        phone=user.phone,
        designation=user.designation or user.job_title,
        department=user.department,
        created_at=user.created_at,
        role_info=RoleBrief.model_validate(user.role_row) if user.role_row else None,
        client=ClientPartyBrief.model_validate(user.client) if user.client else None,
        lead=LeadPartyBrief.model_validate(user.lead) if user.lead else None,
        password_set=password_is_set(user),
        profile_completed=is_profile_complete(user),
        missing_profile_fields=missing_profile_fields(user),
    )


def user_load_options():
    return (
        selectinload(User.role_row),
        selectinload(User.client),
        selectinload(User.lead),
    )


async def get_user_with_party(db: AsyncSession, user_id: str) -> Optional[User]:
    result = await db.execute(
        select(User).options(*user_load_options()).where(User.id == user_id)
    )
    return result.scalars().first()
