"""Local disk storage for project quotation, agreement, and other files."""

import uuid
from pathlib import Path
from typing import Tuple

from fastapi import HTTPException, UploadFile

from .engagement_files import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    extension_of,
    sanitize_filename,
)

DOC_KINDS = {"quotation", "agreement", "other"}


def project_dir(project_id: str) -> Path:
    path = Path(__file__).resolve().parents[2] / "uploads" / "projects" / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


async def save_project_upload(project_id: str, file: UploadFile) -> Tuple[str, str, str, int]:
    original = sanitize_filename(file.filename or "document")
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
    from app.services.object_storage import put_bytes, storage_enabled

    if storage_enabled():
        put_bytes(object_key(project_id, stored), data, content_type)
    else:
        (project_dir(project_id) / stored).write_bytes(data)
    return stored, original, content_type, size


def object_key(project_id: str, stored_filename: str) -> str:
    safe = Path(stored_filename).name
    return f"projects/{project_id}/{safe}"


def read_stored(project_id: str, stored_filename: str) -> bytes:
    from app.services.object_storage import get_bytes, storage_enabled

    if storage_enabled():
        return get_bytes(object_key(project_id, stored_filename))
    path = project_dir(project_id) / Path(stored_filename).name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path.read_bytes()


def absolute_path(project_id: str, stored_filename: str) -> Path:
    safe = Path(stored_filename).name
    path = project_dir(project_id) / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return path


def delete_stored(project_id: str, stored_filename: str) -> None:
    from app.services.object_storage import delete_object, storage_enabled

    if storage_enabled():
        delete_object(object_key(project_id, stored_filename))
        return
    path = project_dir(project_id) / Path(stored_filename).name
    path.unlink(missing_ok=True)
