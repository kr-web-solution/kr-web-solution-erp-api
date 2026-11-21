from typing import List, Optional
from pathlib import Path
import hashlib
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from fastapi.responses import FileResponse

from ..core.dependencies import get_db, get_current_user, require_founder_ceo
from ..core.security import encrypt_secret
from ..models import Settings, EmailTemplate, User, GstRegistration
from ..services.audit import snapshot_entity, write_audit
from ..services.gst_registrations import load_gst_registrations
from ..services.tax_payroll import normalize_state_code
from ..schemas import (
    SettingsResponse, SettingsUpdate, SMTPSettingsUpdate,
    TestEmailRequest, EmailTemplateResponse, EmailTemplateUpdate,
    GstRegistrationResponse, GstRegistrationCreate, GstRegistrationUpdate,
)
from ..services.email import send_email_async

_BRAND_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "brand"

router = APIRouter(prefix="/settings", tags=["Settings (Founder Only)"])

BANK_FIELD_KEYS = (
    "bank_beneficiary_name",
    "bank_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_upi_id",
    "bank_paypal_id",
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
    if getattr(st, "bank_paypal_id", None):
        parts.append(f"PayPal: {st.bank_paypal_id.strip()}")
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
        "paypal": "bank_paypal_id",
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
    regs = await load_gst_registrations(db)
    resp.gst_registrations = [GstRegistrationResponse.model_validate(r) for r in regs]
    return resp


def _settings_response(st: Settings, regs: Optional[list] = None) -> SettingsResponse:
    from ..services.invoice_numbers import company_code_from_settings, default_prefix_scheme

    resp = SettingsResponse.model_validate(st)
    resp.smtp_has_password = bool(st.smtp_password_encrypted)
    resp.invoice_sequences = dict(st.invoice_sequences or {})
    resp.invoice_prefix_scheme = default_prefix_scheme(company_code_from_settings(st))
    if regs is not None:
        resp.gst_registrations = [GstRegistrationResponse.model_validate(r) for r in regs]
    return resp


@router.put("", response_model=SettingsResponse)
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)

    before = snapshot_entity(st)
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

    await write_audit(
        db,
        current_user,
        "settings",
        st.id,
        "update",
        before=before,
        after=snapshot_entity(st),
        request=request,
    )
    await db.commit()
    await db.refresh(st)
    regs = await load_gst_registrations(db)
    return _settings_response(st, regs)

@router.put("/smtp", response_model=SettingsResponse)
async def update_smtp_settings(
    payload: SMTPSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo)
):
    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)

    before = snapshot_entity(
        st,
        [
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_from_email",
            "smtp_use_tls",
            "smtp_auto_send_enabled",
        ],
    )
    update_data = payload.model_dump(exclude_unset=True)
    if "smtp_password" in update_data:
        pwd = update_data.pop("smtp_password")
        if pwd:
            st.smtp_password_encrypted = encrypt_secret(pwd)

    for k, v in update_data.items():
        setattr(st, k, v)

    await write_audit(
        db,
        current_user,
        "settings",
        st.id,
        "update",
        before=before,
        after={
            **snapshot_entity(
                st,
                [
                    "smtp_host",
                    "smtp_port",
                    "smtp_username",
                    "smtp_from_email",
                    "smtp_use_tls",
                    "smtp_auto_send_enabled",
                ],
            ),
            "smtp_password_changed": "smtp_password" in payload.model_dump(exclude_unset=True)
            and bool(payload.model_dump(exclude_unset=True).get("smtp_password")),
        },
        request=request,
    )
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
async def get_upi_qr_file(
    filename: str,
    _: User = Depends(get_current_user),
):
    """Serve uploaded UPI QR to authenticated users only.

    Choice: (a) session auth — QR is only shown in authenticated UI (settings +
    invoice pages); PDFs/emails do not fetch this HTTP endpoint for the image.
    """
    # Basename only — blocks path traversal (../, absolute paths, etc.)
    safe = Path(filename).name
    if not safe or safe != filename or ".." in filename or "/" in filename or "\\" in filename:
        # Do not reveal whether a traversal target existed
        raise HTTPException(status_code=404, detail="QR image not found")

    path = Path(__file__).resolve().parents[2] / "uploads" / "brand" / safe
    try:
        path.resolve().relative_to((Path(__file__).resolve().parents[2] / "uploads" / "brand").resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="QR image not found")

    if not path.is_file():
        raise HTTPException(status_code=404, detail="QR image not found")

    media = "image/png"
    if safe.lower().endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif safe.lower().endswith(".webp"):
        media = "image/webp"

    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/logo", response_model=SettingsResponse)
async def upload_company_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
    request: Request = None,
):
    """Upload company logo. Stored by content hash so historical URLs stay immutable."""
    content_type = (file.content_type or "").lower()
    if content_type not in {"image/png", "image/jpeg", "image/webp", "image/jpg"}:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WebP image")
    raw = await file.read()
    if not raw or len(raw) > 3 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 3 MB")

    digest = hashlib.sha256(raw).hexdigest()
    ext = Path(file.filename or "logo.png").suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        ext = ".png"
    _BRAND_ROOT.mkdir(parents=True, exist_ok=True)
    stored = f"logo-{digest[:16]}{ext}"
    path = _BRAND_ROOT / stored
    if not path.is_file():
        path.write_bytes(raw)

    res = await db.execute(select(Settings).where(Settings.id == "default"))
    st = res.scalars().first()
    if not st:
        st = Settings(id="default")
        db.add(st)
    before = snapshot_entity(st)
    st.company_logo_url = f"/api/v1/settings/logo-file/{stored}"
    st.company_logo_content_hash = digest
    await write_audit(
        db,
        current_user,
        "settings",
        st.id,
        "update",
        before=before,
        after=snapshot_entity(st),
        request=request,
    )
    await db.commit()
    await db.refresh(st)
    regs = await load_gst_registrations(db)
    return _settings_response(st, regs)


@router.get("/logo-file/{filename}")
async def get_company_logo_file(
    filename: str,
    _: User = Depends(get_current_user),
):
    safe = Path(filename).name
    if not safe or safe != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Logo not found")
    if not safe.startswith("logo-"):
        raise HTTPException(status_code=404, detail="Logo not found")
    path = _BRAND_ROOT / safe
    try:
        path.resolve().relative_to(_BRAND_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Logo not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Logo not found")
    media = "image/png"
    if safe.lower().endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif safe.lower().endswith(".webp"):
        media = "image/webp"
    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/gst-registrations", response_model=List[GstRegistrationResponse])
async def list_gst_registrations(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    regs = await load_gst_registrations(db)
    return [GstRegistrationResponse.model_validate(r) for r in regs]


@router.post("/gst-registrations", response_model=GstRegistrationResponse, status_code=201)
async def create_gst_registration(
    payload: GstRegistrationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    code = normalize_state_code(payload.state_code)
    if not code:
        raise HTTPException(status_code=400, detail="state_code is required")
    gstin = (payload.gstin or "").strip().upper()
    if len(gstin) < 15:
        raise HTTPException(status_code=400, detail="GSTIN must be 15 characters")
    if payload.effective_to and payload.effective_to < payload.effective_from:
        raise HTTPException(status_code=400, detail="effective_to must be on or after effective_from")

    if payload.is_primary:
        existing = await load_gst_registrations(db)
        for row in existing:
            if row.is_primary:
                row.is_primary = False

    row = GstRegistration(
        gstin=gstin,
        legal_name=(payload.legal_name or "").strip() or None,
        address=(payload.address or "").strip() or None,
        state=(payload.state or "").strip() or None,
        state_code=code,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        is_primary=bool(payload.is_primary),
        is_active=bool(payload.is_active),
    )
    db.add(row)
    await write_audit(
        db,
        current_user,
        "gst_registrations",
        row.id,
        "create",
        after=snapshot_entity(row),
        request=request,
    )
    await db.commit()
    await db.refresh(row)
    return GstRegistrationResponse.model_validate(row)


@router.patch("/gst-registrations/{reg_id}", response_model=GstRegistrationResponse)
async def update_gst_registration(
    reg_id: str,
    payload: GstRegistrationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    row = (
        await db.execute(
            select(GstRegistration).where(
                GstRegistration.id == reg_id,
                GstRegistration.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="GST registration not found")
    before = snapshot_entity(row)
    data = payload.model_dump(exclude_unset=True)
    if "state_code" in data and data["state_code"] is not None:
        data["state_code"] = normalize_state_code(data["state_code"]) or row.state_code
    if "gstin" in data and data["gstin"] is not None:
        data["gstin"] = str(data["gstin"]).strip().upper()
    if data.get("is_primary"):
        for other in await load_gst_registrations(db):
            if other.id != row.id and other.is_primary:
                other.is_primary = False
    for k, v in data.items():
        setattr(row, k, v)
    if row.effective_to and row.effective_from and row.effective_to < row.effective_from:
        raise HTTPException(status_code=400, detail="effective_to must be on or after effective_from")
    await write_audit(
        db,
        current_user,
        "gst_registrations",
        row.id,
        "update",
        before=before,
        after=snapshot_entity(row),
        request=request,
    )
    await db.commit()
    await db.refresh(row)
    return GstRegistrationResponse.model_validate(row)


@router.delete("/gst-registrations/{reg_id}", status_code=200)
async def delete_gst_registration(
    reg_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    row = (
        await db.execute(
            select(GstRegistration).where(
                GstRegistration.id == reg_id,
                GstRegistration.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="GST registration not found")
    before = snapshot_entity(row)
    row.soft_delete()
    await write_audit(
        db,
        current_user,
        "gst_registrations",
        row.id,
        "delete",
        before=before,
        after={"deleted": True},
        request=request,
    )
    await db.commit()
    return {"message": "GST registration deleted"}


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

