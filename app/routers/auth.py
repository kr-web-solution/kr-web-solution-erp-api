from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core.database import AsyncSessionLocal
from ..core.dependencies import get_db, get_current_user
from ..core.config import settings
from ..core.security import (
    verify_password, get_password_hash,
    create_access_token, create_password_reset_token, verify_password_reset_token
)
from ..models import User, Settings, EmailTemplate
from ..schemas import (
    LoginRequest, GoogleLoginRequest, LoginResponse, UserResponse, MeUpdate,
    ForgotPasswordRequest, ResetPasswordRequest
)
from ..services.email import send_email_async
from ..services.user_identity import to_user_response, get_user_with_party

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_auth_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=access_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        expires=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        samesite="lax",
        secure=settings.PRODUCTION,
    )


async def _login_response_for(
    user: User, response: Response, db: AsyncSession
) -> LoginResponse:
    loaded = await get_user_with_party(db, user.id) or user
    access_token = create_access_token(
        subject=loaded.id,
        role=loaded.role,
        extra_claims={
            "client_id": loaded.client_id,
            "lead_id": loaded.lead_id,
            "party_type": loaded.party_type,
            "email": loaded.email,
        },
    )
    _set_auth_cookie(response, access_token)
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=to_user_response(loaded) if loaded.role_row is not None else UserResponse(
            id=loaded.id,
            email=loaded.email,
            full_name=loaded.full_name,
            role=loaded.role,
            role_id=loaded.role_id,
            party_type=loaded.party_type,
            client_id=loaded.client_id,
            lead_id=loaded.lead_id,
            is_active=loaded.is_active,
            phone=loaded.phone,
            designation=loaded.designation or loaded.job_title,
            created_at=loaded.created_at,
        ),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == credentials.email.lower().strip()))
    user = result.scalars().first()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )

    return await _login_response_for(user, response, db)


@router.post("/google", response_model=LoginResponse)
async def login_with_google(
    payload: GoogleLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Verify Google ID token and issue the same JWT cookie as password login.

    Only existing active users may sign in — no auto-registration.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        idinfo = google_id_token.verify_oauth2_token(
            payload.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google sign-in token",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify Google sign-in",
        )

    if idinfo.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token issuer",
        )

    email = (idinfo.get("email") or "").lower().strip()
    email_verified = bool(idinfo.get("email_verified"))
    if not email or not email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No KR Workspace account for this Google email. Ask a founder to invite you first.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    # Refresh display name from Google if ours is empty/generic
    google_name = (idinfo.get("name") or "").strip()
    if google_name and (not user.full_name or user.full_name.lower() in ("founder ceo", "user")):
        user.full_name = google_name
        await db.commit()
        await db.refresh(user)

    return await _login_response_for(user, response, db)

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.PRODUCTION
    )
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return to_user_response(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: MeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Any logged-in user can update their own name / password."""
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data and data["full_name"] is not None:
        name = str(data["full_name"]).strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty")
        current_user.full_name = name

    if data.get("password"):
        new_password = str(data["password"])
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters",
            )
        current_pw = data.get("current_password") or ""
        if current_pw and not verify_password(current_pw, current_user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )
        current_user.password_hash = get_password_hash(new_password)

    await db.commit()
    loaded = await get_user_with_party(db, current_user.id) or current_user
    return to_user_response(loaded)


@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    email = request.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if not user:
        # Prevent user enumeration: respond with success even if not found
        return {"message": "If that email is registered, a password reset link has been dispatched."}

    reset_token = create_password_reset_token(email)
    reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

    # Load email settings & template
    st_res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = st_res.scalars().first()

    tpl_res = await db.execute(select(EmailTemplate).where(EmailTemplate.id == "password_reset"))
    tpl = tpl_res.scalars().first()

    if st and st.smtp_host:
        from ..services.email_html import build_password_reset_email, fill_placeholders

        subject_default, html, plain = build_password_reset_email(
            company_name=st.company_name,
            user_name=user.full_name,
            reset_link=reset_link,
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
                to_email=email,
                subject=subject,
                body=html,
                plain_body=plain,
                settings=st,
                is_html=True,
            )
        except Exception:
            # Don't crash if SMTP is not configured in dev
            pass

    return {
        "message": "If that email is registered, a password reset link has been dispatched.",
        "dev_reset_link": reset_link if not settings.PRODUCTION else None
    }

@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    email = verify_password_reset_token(request.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token"
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    user.password_hash = get_password_hash(request.new_password)
    await db.commit()
    return {"message": "Password updated successfully. You can now log in."}

