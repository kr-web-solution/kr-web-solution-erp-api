from datetime import datetime, timezone, timedelta
from typing import Optional, Any
import bcrypt
import jwt
from cryptography.fernet import Fernet
from .config import settings

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(subject: str, role: str, expires_delta: Optional[timedelta] = None, extra_claims: Optional[dict] = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": str(subject),
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access_token"
    }
    if extra_claims:
        to_encode.update(extra_claims)
        
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access_token":
            return None
        return payload
    except Exception:
        return None

def create_password_reset_token(email: str) -> str:
    # 15 minutes short-lived signed token
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "sub": email,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "purpose": "password_reset"
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def verify_password_reset_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "password_reset":
            return None
        return payload.get("sub")
    except Exception:
        return None


def create_invite_token(email: str) -> str:
    """72h token for first-time set-password after invite."""
    expire = datetime.now(timezone.utc) + timedelta(hours=72)
    payload = {
        "sub": email,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "purpose": "invite_set_password",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_invite_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "invite_set_password":
            return None
        return payload.get("sub")
    except Exception:
        return None


def unusable_password_hash() -> str:
    """Hash that cannot match any user-typed password (invite pending)."""
    import secrets
    return get_password_hash(f"invite-pending-{secrets.token_urlsafe(32)}")

# Fernet encryption for SMTP password at rest
def _get_fernet() -> Fernet:
    key = (settings.ENCRYPTION_KEY or "").strip()
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not configured; cannot encrypt/decrypt secrets"
        )
    return Fernet(key.encode("utf-8"))

def encrypt_secret(plain_text: str) -> str:
    if not plain_text:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_text.encode('utf-8')).decode('utf-8')

def decrypt_secret(encrypted_text: str) -> str:
    if not encrypted_text:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except Exception:
        return ""

