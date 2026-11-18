"""Local disk storage for engagement enquiry documents."""

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
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/octet-stream",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
DOC_KINDS = {"enquiry", "brief", "quote", "other"}


def uploads_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "uploads" / "engagements"
    root.mkdir(parents=True, exist_ok=True)
    return root


def engagement_dir(engagement_id: str) -> Path:
    path = uploads_root() / engagement_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    base = Path(name or "document").name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip("._ ")
    return base[:180] or "document"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


async def save_upload(engagement_id: str, file: UploadFile) -> Tuple[str, str, str, int]:
    """Persist upload; returns stored_filename, original_filename, content_type, size_bytes."""
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
    key = object_key(engagement_id, stored)
    from app.services.object_storage import put_bytes, storage_enabled

    if storage_enabled():
        put_bytes(key, data, content_type)
    else:
        (engagement_dir(engagement_id) / stored).write_bytes(data)
    return stored, original, content_type, size


def object_key(engagement_id: str, stored_filename: str) -> str:
    safe = Path(stored_filename).name
    return f"engagements/{engagement_id}/{safe}"


def read_stored(engagement_id: str, stored_filename: str) -> bytes:
    from app.services.object_storage import get_bytes, storage_enabled

    if storage_enabled():
        return get_bytes(object_key(engagement_id, stored_filename))
    path = engagement_dir(engagement_id) / Path(stored_filename).name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path.read_bytes()


def absolute_path(engagement_id: str, stored_filename: str) -> Path:
    # Local fallback only. Object storage reads go through read_stored.
    safe = Path(stored_filename).name
    path = engagement_dir(engagement_id) / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return path


def delete_stored(engagement_id: str, stored_filename: str) -> None:
    from app.services.object_storage import delete_object, storage_enabled

    if storage_enabled():
        delete_object(object_key(engagement_id, stored_filename))
        return
    try:
        path = absolute_path(engagement_id, stored_filename)
        path.unlink(missing_ok=True)
    except HTTPException:
        pass
