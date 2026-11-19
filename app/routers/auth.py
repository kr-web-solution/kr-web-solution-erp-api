import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger("kr.api")

from ..core.dependencies import get_db, get_current_user
from ..core.config import settings
from ..core.database import not_deleted
from ..core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_password_reset_token,
    verify_password_reset_token,
    create_invite_token,
    verify_invite_token,
)
from ..models import User, Settings, EmailTemplate
from ..schemas import (
    LoginRequest,
    GoogleLoginRequest,
    MicrosoftLoginRequest,
    OAuthProvidersResponse,
    LoginResponse,
    UserResponse,
    MeUpdate,
    MeProfileUpdate,
    SetPasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from ..services.audit import write_audit
from ..services.email import send_email_async
from ..services.email_policy import assert_oauth_email_allowed
from ..services.microsoft_oauth import verify_microsoft_id_token, email_from_microsoft_claims
from ..services.profile_completion import mark_profile_complete_if_ready, password_is_set
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
        user=to_user_response(loaded),
    )


async def _get_active_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.email == email.lower().strip(), not_deleted(User))
    )
    return result.scalars().first()


@router.get("/oauth-providers", response_model=OAuthProvidersResponse)
async def oauth_providers():
    """Public client IDs for Google / Microsoft SPA buttons. Secrets stay on the API only."""
    google = (settings.GOOGLE_CLIENT_ID or "").strip() or None
    microsoft = (settings.MICROSOFT_CLIENT_ID or "").strip() or None
    tenant = (settings.MICROSOFT_TENANT_ID or "common").strip() or "common"
    return OAuthProvidersResponse(
        google_client_id=google,
        microsoft_client_id=microsoft,
        microsoft_tenant_id=tenant,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _get_active_user_by_email(db, credentials.email)

    if not user or not password_is_set(user) or not verify_password(
        credentials.password, user.password_hash
    ):
        await write_audit(
            db,
            None,
            "auth",
            user.id if user else "unknown",
            "login_failure",
            after={"email": (credentials.email or "")[:120], "reason": "bad_credentials"},
            request=request,
        )
        await db.commit()
        if user and not password_is_set(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please set your password using the invite link sent to your email",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        await write_audit(
            db,
            user,
            "auth",
            user.id,
            "login_failure",
            after={"email": user.email, "reason": "inactive"},
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    await write_audit(
        db,
        user,
        "auth",
        user.id,
        "login_success",
        after={"email": user.email},
        request=request,
    )
    return await _login_response_for(user, response, db)


async def _oauth_login_by_email(
    *,
    db: AsyncSession,
    response: Response,
    email: str,
    display_name: Optional[str] = None,
) -> LoginResponse:
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OAuth account email is missing",
        )
    user = await _get_active_user_by_email(db, email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No KR Workspace account for this email. Ask an admin to invite you first.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    assert_oauth_email_allowed(email, user.role)

    if display_name and (not user.full_name or user.full_name.lower() in ("user", "founder ceo")):
        user.full_name = display_name

    # OAuth proves mailbox control — treat password as set for invite pending accounts
    if user.password_set_at is None:
        user.password_set_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)
    return await _login_response_for(user, response, db)


@router.post("/google", response_model=LoginResponse)
async def login_with_google(
    payload: GoogleLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Verify Google ID token — invite-only, no auto-registration."""
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
    except ValueError as e:
        logger.warning("Google token verify failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google sign-in token",
        )
    except Exception as e:
        logger.exception("Google token verify error: %s", e)
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
    if not email or not bool(idinfo.get("email_verified")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    return await _oauth_login_by_email(
        db=db,
        response=response,
        email=email,
        display_name=(idinfo.get("name") or "").strip() or None,
    )


@router.post("/microsoft", response_model=LoginResponse)
async def login_with_microsoft(
    payload: MicrosoftLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Verify Microsoft Entra ID token — invite-only."""
    claims = verify_microsoft_id_token(payload.id_token)
    email = email_from_microsoft_claims(claims)
    name = (claims.get("name") or "").strip() or None
    return await _oauth_login_by_email(
        db=db,
        response=response,
        email=email,
        display_name=name,
    )


@router.post("/set-password")
async def set_password_from_invite(
    payload: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    email = verify_invite_token(payload.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite token",
        )
    user = await _get_active_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )
    user.password_hash = get_password_hash(payload.new_password)
    user.password_set_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Password set successfully. You can now log in."}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.PRODUCTION,
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
        current_user.password_set_at = datetime.now(timezone.utc)

    await db.commit()
    loaded = await get_user_with_party(db, current_user.id) or current_user
    return to_user_response(loaded)


@router.patch("/me/profile", response_model=UserResponse)
async def update_me_profile(
    payload: MeProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Progressive onboarding — fill remaining profile fields."""
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data and data["full_name"] is not None:
        name = str(data["full_name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        current_user.full_name = name
    if "phone" in data and data["phone"] is not None:
        current_user.phone = str(data["phone"]).strip() or None
    if "designation" in data and data["designation"] is not None:
        current_user.designation = str(data["designation"]).strip() or None
    if "department" in data and data["department"] is not None:
        current_user.department = str(data["department"]).strip() or None

    mark_profile_complete_if_ready(current_user)
    await db.commit()
    loaded = await get_user_with_party(db, current_user.id) or current_user
    return to_user_response(loaded)


@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    email = request.email.lower().strip()
    user = await _get_active_user_by_email(db, email)

    if not user:
        return {"message": "If that email is registered, a password reset link has been dispatched."}

    pending_invite = user.password_set_at is None
    if pending_invite:
        reset_token = create_invite_token(email)
        reset_link = f"{settings.FRONTEND_URL}/set-password?token={reset_token}"
        tpl_id = "user_invite"
    else:
        reset_token = create_password_reset_token(email)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"
        tpl_id = "password_reset"

    st_res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = st_res.scalars().first()

    tpl_res = await db.execute(select(EmailTemplate).where(EmailTemplate.id == tpl_id))
    tpl = tpl_res.scalars().first()

    if st and st.smtp_host:
        from ..services.email_html import (
            build_password_reset_email,
            build_user_invite_email,
            fill_placeholders,
        )

        if pending_invite:
            subject_default, html, plain = build_user_invite_email(
                company_name=st.company_name,
                user_name=user.full_name,
                invite_link=reset_link,
                role_label=user.role,
                company_address=st.company_address or "",
                company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
            )
        else:
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
            pass

    return {
        "message": "If that email is registered, a password reset link has been dispatched.",
        "dev_reset_link": reset_link if not settings.PRODUCTION else None,
    }


@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    email = verify_password_reset_token(request.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    user = await _get_active_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password_hash = get_password_hash(request.new_password)
    user.password_set_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Password updated successfully. You can now log in."}
