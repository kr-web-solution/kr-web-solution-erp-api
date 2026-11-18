from typing import List, Optional
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core.dependencies import get_db, require_founder_ceo
from ..core.security import encrypt_secret
from ..models import Settings, EmailTemplate, User
from ..schemas import (
    SettingsResponse, SettingsUpdate, SMTPSettingsUpdate,
    TestEmailRequest, EmailTemplateResponse, EmailTemplateUpdate
)
from ..services.email import send_email_async

router = APIRouter(prefix="/settings", tags=["Settings (Founder Only)"])

BANK_FIELD_KEYS = (
    "bank_beneficiary_name",
    "bank_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_upi_id",
)


def compose_bank_upi_details(st: Settings) -> Optional[str]:
    """Build the legacy printable bank string from separate fields."""
    parts: list[str] = []
    if st.bank_beneficiary_name:
        parts.append(f"Beneficiary: {st.bank_beneficiary_name.strip()}")
    if st.bank_name:
        parts.append(f"Bank: {st.bank_name.strip()}")
    if st.bank_account_number:
        parts.append(f"A/C: {st.bank_account_number.strip()}")
    if st.bank_ifsc:
        parts.append(f"IFSC: {st.bank_ifsc.strip().upper()}")
    if st.bank_upi_id:
        parts.append(f"UPI: {st.bank_upi_id.strip()}")
    return " | ".join(parts) if parts else None


def hydrate_bank_fields_from_blob(st: Settings) -> bool:
    """Fill empty structured bank fields from a legacy bank_upi_details blob."""
    blob = (st.bank_upi_details or "").strip()
    if not blob:
        return False
    if any(
        getattr(st, key)
        for key in BANK_FIELD_KEYS
    ):
        return False

    parsed: dict[str, str] = {}
    for chunk in blob.split("|"):
        part = chunk.strip()
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        parsed[key.strip().lower()] = value.strip()

    mapping = {
        "beneficiary": "bank_beneficiary_name",
        "bank": "bank_name",
        "a/c": "bank_account_number",
        "ac": "bank_account_number",
        "account": "bank_account_number",
        "ifsc": "bank_ifsc",
        "upi": "bank_upi_id",
    }
    changed = False
    for label, attr in mapping.items():
        if label in parsed and not getattr(st, attr):
            setattr(st, attr, parsed[label])
            changed = True
    return changed


@router.get("", response_model=SettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settings not found")

    if hydrate_bank_fields_from_blob(st):
        await db.commit()
        await db.refresh(st)

    from ..services.invoice_numbers import company_code_from_settings, default_prefix_scheme

    resp = SettingsResponse.model_validate(st)
    resp.smtp_has_password = bool(st.smtp_password_encrypted)
    resp.invoice_sequences = dict(st.invoice_sequences or {})
    resp.invoice_prefix_scheme = default_prefix_scheme(company_code_from_settings(st))
    return resp

@router.put("", response_model=SettingsResponse)
async def update_settings(
    payload: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)

    data = payload.model_dump(exclude_unset=True)
    if "invoice_prefix" in data and data["invoice_prefix"] is not None:
        raw = str(data["invoice_prefix"]).strip().upper()
        data["invoice_prefix"] = "".join(ch for ch in raw if ch.isalnum()) or "KRWS"
    for k, v in data.items():
        setattr(st, k, v)

    # Refresh printable blob used by invoices / email templates
    if any(k in data for k in BANK_FIELD_KEYS):
        st.bank_upi_details = compose_bank_upi_details(st)
    elif any(getattr(st, k) for k in BANK_FIELD_KEYS):
        st.bank_upi_details = compose_bank_upi_details(st)

    await db.commit()
    await db.refresh(st)

    from ..services.invoice_numbers import company_code_from_settings, default_prefix_scheme

    resp = SettingsResponse.model_validate(st)
    resp.smtp_has_password = bool(st.smtp_password_encrypted)
    resp.invoice_sequences = dict(st.invoice_sequences or {})
    resp.invoice_prefix_scheme = default_prefix_scheme(company_code_from_settings(st))
    return resp

@router.put("/smtp", response_model=SettingsResponse)
async def update_smtp_settings(
    payload: SMTPSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)

    update_data = payload.model_dump(exclude_unset=True)
    if "smtp_password" in update_data:
        pwd = update_data.pop("smtp_password")
        if pwd:
            st.smtp_password_encrypted = encrypt_secret(pwd)

    for k, v in update_data.items():
        setattr(st, k, v)

    await db.commit()
    await db.refresh(st)

    resp = SettingsResponse.model_validate(st)
    resp.smtp_has_password = bool(st.smtp_password_encrypted)
    return resp

@router.post("/test-email")
async def send_test_email(
    payload: TestEmailRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st or not st.smtp_host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SMTP is not yet configured")

    try:
        from ..services.email_html import build_smtp_test_email

        subject, html, plain = build_smtp_test_email(
            company_name=st.company_name or "KR Web Solution",
            smtp_host=st.smtp_host or "",
            smtp_port=int(st.smtp_port or 587),
            company_logo_url=st.company_logo_url or "/brand/krws-mark.png",
        )
        await send_email_async(
            to_email=payload.to_email,
            subject=subject,
            body=html,
            plain_body=plain,
            settings=st,
            is_html=True,
        )
        return {"message": f"Test email successfully sent to {payload.to_email}"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SMTP test failed: {str(e)}"
        )

@router.post("/upi-qr", response_model=SettingsResponse)
async def upload_upi_qr(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    """Upload default UPI QR image shown on client invoices."""
    content_type = (file.content_type or "").lower()
    if content_type not in {"image/png", "image/jpeg", "image/webp", "image/jpg"}:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WebP image")
    raw = await file.read()
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="QR image must be under 2 MB")

    ext = Path(file.filename or "qr.png").suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        ext = ".png"
    root = Path(__file__).resolve().parents[2] / "uploads" / "brand"
    root.mkdir(parents=True, exist_ok=True)
    stored = f"upi-qr-{uuid.uuid4().hex[:10]}{ext}"
    (root / stored).write_bytes(raw)

    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)
    # Public-ish URL served via API static or relative path used by UI
    st.bank_upi_qr_url = f"/api/v1/settings/upi-qr-file/{stored}"
    await db.commit()
    await db.refresh(st)

    from ..services.invoice_numbers import company_code_from_settings, default_prefix_scheme

    resp = SettingsResponse.model_validate(st)
    resp.smtp_has_password = bool(st.smtp_password_encrypted)
    resp.invoice_sequences = dict(st.invoice_sequences or {})
    resp.invoice_prefix_scheme = default_prefix_scheme(company_code_from_settings(st))
    return resp


@router.get("/upi-qr-file/{filename}")
async def get_upi_qr_file(filename: str):
    """Serve uploaded UPI QR (no auth — clients need it on invoice pages)."""
    from fastapi.responses import FileResponse

    safe = Path(filename).name
    path = Path(__file__).resolve().parents[2] / "uploads" / "brand" / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="QR image not found")
    media = "image/png"
    if safe.lower().endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif safe.lower().endswith(".webp"):
        media = "image/webp"
    return FileResponse(path, media_type=media)


@router.get("/email-templates", response_model=List[EmailTemplateResponse])
async def list_email_templates(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(EmailTemplate).order_by(EmailTemplate.id.asc()))
    return [EmailTemplateResponse.model_validate(t) for t in res.scalars().all()]

@router.put("/email-templates/{template_id}", response_model=EmailTemplateResponse)
async def update_email_template(
    template_id: str,
    payload: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(EmailTemplate).where(EmailTemplate.id == template_id))
    tpl = res.scalars().first()
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    tpl.subject = payload.subject
    tpl.body = payload.body
    await db.commit()
    await db.refresh(tpl)
    return EmailTemplateResponse.model_validate(tpl)

