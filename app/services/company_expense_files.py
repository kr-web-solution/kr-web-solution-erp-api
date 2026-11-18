"""Private-bucket storage for company expense receipts under company-expenses/."""

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


def uploads_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "uploads" / "company-expenses"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_filename(name: str) -> str:
    base = Path(name or "receipt").name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip("._ ")
    return base[:180] or "receipt"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def company_expense_receipt_key(stored_filename: str) -> str:
    return f"company-expenses/{Path(stored_filename).name}"


async def save_company_expense_receipt(file: UploadFile) -> Tuple[str, str, str]:
    """Persist a receipt; returns object_key, original_filename, content_type."""
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
    key = company_expense_receipt_key(stored)
    _put(key, data, content_type, local_dir=uploads_root(), local_name=stored)
    return key, original, content_type


def read_company_expense_receipt(key: str) -> bytes:
    from app.services.object_storage import get_bytes, storage_enabled

    if storage_enabled():
        return get_bytes(key)
    parts = key.split("/")
    if parts and parts[0] == "company-expenses" and len(parts) >= 2:
        path = uploads_root() / parts[-1]
        if path.exists():
            return path.read_bytes()
    raise HTTPException(status_code=404, detail="File not found")


def delete_company_expense_receipt(key: str) -> None:
    from app.services.object_storage import delete_object, storage_enabled

    if storage_enabled():
        try:
            delete_object(key)
        except Exception:
            pass
        return
    parts = key.split("/")
    if parts and parts[0] == "company-expenses" and len(parts) >= 2:
        path = uploads_root() / parts[-1]
        if path.exists() and path.is_file():
            path.unlink()


def _put(key: str, data: bytes, content_type: str, *, local_dir: Path, local_name: str) -> None:
    from app.services.object_storage import put_bytes, storage_enabled

    if storage_enabled():
        put_bytes(key, data, content_type)
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / local_name).write_bytes(data)
