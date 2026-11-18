from collections.abc import AsyncGenerator
from typing import Optional, List, Callable
from fastapi import Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from .database import AsyncSessionLocal, not_deleted
from .config import settings
from .security import decode_access_token
from ..models import User

security_scheme = HTTPBearer(auto_error=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def get_current_user(
    request: Request,
    bearer_token: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    token: Optional[str] = None

    # 1. Try httpOnly cookie first
    if settings.COOKIE_NAME in request.cookies:
        token = request.cookies.get(settings.COOKIE_NAME)

    # 2. Try Authorization: Bearer <token>
    if not token and bearer_token:
        token = bearer_token.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload["sub"]
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.role_row),
            selectinload(User.client),
            selectinload(User.lead),
        )
        .where(User.id == user_id, not_deleted(User))
    )
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user

async def get_optional_current_user(
    request: Request,
    bearer_token: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    try:
        return await get_current_user(request, bearer_token, db)
    except HTTPException:
        return None

def require_role(*allowed_roles: str) -> Callable:
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: role '{current_user.role}' does not have required permissions"
            )
        return current_user
    return role_checker

def is_privileged(role: str) -> bool:
    """Admin and Founder CEO share full workspace privileges."""
    return role in ("admin", "founder_ceo")


# Convenience dependencies — Admin has the same access as Founder CEO
require_founder_ceo = require_role("founder_ceo", "admin")
require_staff = require_role("founder_ceo", "admin", "worker")
