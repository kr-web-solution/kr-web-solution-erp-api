"""Private storage for invoice payment proof (client upload → founder verify)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, UploadFile

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/octet-stream",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def uploads_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "uploads" / "invoice_proofs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def invoice_proof_dir(invoice_id: str) -> Path:
    path = uploads_root() / invoice_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    base = Path(name or "payment-proof").name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip("._ ")
    return base[:180] or "payment-proof"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def proof_object_key(invoice_id: str, stored_filename: str) -> str:
    return f"invoice_proofs/{invoice_id}/{Path(stored_filename).name}"


async def save_payment_proof(invoice_id: str, file: UploadFile) -> Tuple[str, str, str, int]:
    """Persist payment proof. Returns object_key, original_filename, content_type, size."""
    original = sanitize_filename(file.filename or "payment-proof")
    ext = extension_of(original)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    from app.services.file_mime import resolve_media_type

    content_type = resolve_media_type(file.content_type, original)
    if content_type not in ALLOWED_CONTENT_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported content type")

    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    stored = f"{uuid.uuid4().hex}{ext}"
    key = proof_object_key(invoice_id, stored)
    _put(key, data, content_type, local_dir=invoice_proof_dir(invoice_id), local_name=stored)
    return key, original, content_type, size


def read_payment_proof(object_key: str, invoice_id: str) -> bytes:
    from app.services.object_storage import get_bytes, storage_enabled

    if storage_enabled():
        return get_bytes(object_key)
    # Local fallback from key or invoice dir
    parts = object_key.split("/")
    if parts and parts[0] == "invoice_proofs" and len(parts) >= 3:
        path = uploads_root().joinpath(*parts[1:])
        if path.exists() and path.is_file():
            return path.read_bytes()
    # Legacy: filename only under invoice dir
    name = Path(object_key).name
    fallback = invoice_proof_dir(invoice_id) / name
    if fallback.exists() and fallback.is_file():
        return fallback.read_bytes()
    raise HTTPException(status_code=404, detail="Payment proof not found")


def _put(key: str, data: bytes, content_type: str, *, local_dir: Path, local_name: str) -> None:
    from app.services.object_storage import put_bytes, storage_enabled

    if storage_enabled():
        put_bytes(key, data, content_type)
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / local_name).write_bytes(data)
