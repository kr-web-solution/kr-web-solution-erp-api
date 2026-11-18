"""Private-bucket storage for staff documents, expense receipts, and payslip PDFs."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, UploadFile

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/octet-stream",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
DOC_KINDS = {"offer", "id", "other"}


def uploads_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "uploads" / "staff"
    root.mkdir(parents=True, exist_ok=True)
    return root


def staff_dir(user_id: str) -> Path:
    path = uploads_root() / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    base = Path(name or "document").name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip("._ ")
    return base[:180] or "document"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def staff_object_key(user_id: str, stored_filename: str) -> str:
    return f"staff/{user_id}/{Path(stored_filename).name}"


def payslip_object_key(user_id: str, month_key: str) -> str:
    return f"payslips/{user_id}/{month_key}.pdf"


def expense_receipt_key(user_id: str, stored_filename: str) -> str:
    return f"staff/{user_id}/expenses/{Path(stored_filename).name}"


async def save_staff_upload(user_id: str, file: UploadFile) -> Tuple[str, str, str, int]:
    """Persist a staff document; returns stored_filename, original, content_type, size."""
    original = sanitize_filename(file.filename or "document")
    ext = extension_of(original)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported content type")

    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    stored = f"{uuid.uuid4().hex}{ext}"
    content_type = content_type or "application/octet-stream"
    key = staff_object_key(user_id, stored)
    _put(key, data, content_type, local_dir=staff_dir(user_id), local_name=stored)
    return stored, original, content_type, size


async def save_expense_receipt(user_id: str, file: UploadFile) -> Tuple[str, str, str]:
    """Persist an expense receipt; returns object_key, original_filename, content_type."""
    original = sanitize_filename(file.filename or "receipt")
    ext = extension_of(original)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    stored = f"{uuid.uuid4().hex}{ext}"
    content_type = content_type or "application/octet-stream"
    key = expense_receipt_key(user_id, stored)
    expense_dir = staff_dir(user_id) / "expenses"
    expense_dir.mkdir(parents=True, exist_ok=True)
    _put(key, data, content_type, local_dir=expense_dir, local_name=stored)
    return key, original, content_type


def save_payslip_pdf(user_id: str, month_key: str, pdf_bytes: bytes) -> str:
    key = payslip_object_key(user_id, month_key)
    payslip_dir = uploads_root().parent / "payslips" / user_id
    payslip_dir.mkdir(parents=True, exist_ok=True)
    _put(key, pdf_bytes, "application/pdf", local_dir=payslip_dir, local_name=f"{month_key}.pdf")
    return key


def read_object(key: str, *, local_fallback: Path | None = None) -> bytes:
    from app.services.object_storage import get_bytes, storage_enabled

    if storage_enabled():
        return get_bytes(key)
    if local_fallback is not None and local_fallback.exists() and local_fallback.is_file():
        return local_fallback.read_bytes()
    # Try common local layouts from key
    parts = key.split("/")
    if parts and parts[0] == "staff" and len(parts) >= 3:
        path = uploads_root().joinpath(*parts[1:])
        if path.exists():
            return path.read_bytes()
    if parts and parts[0] == "payslips" and len(parts) >= 3:
        path = uploads_root().parent.joinpath(*parts)
        if path.exists():
            return path.read_bytes()
    raise HTTPException(status_code=404, detail="File not found")


def read_staff_document(user_id: str, stored_filename: str) -> bytes:
    key = staff_object_key(user_id, stored_filename)
    return read_object(key, local_fallback=staff_dir(user_id) / Path(stored_filename).name)


def _put(key: str, data: bytes, content_type: str, *, local_dir: Path, local_name: str) -> None:
    from app.services.object_storage import put_bytes, storage_enabled

    if storage_enabled():
        put_bytes(key, data, content_type)
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / local_name).write_bytes(data)
