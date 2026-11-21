"""Infer browser-viewable Content-Type from filename when uploads store octet-stream."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

EXT_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".txt": "text/plain",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def resolve_media_type(
    content_type: Optional[str],
    *filenames: Optional[str],
) -> str:
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw and raw not in ("application/octet-stream", "binary/octet-stream"):
        return raw
    for name in filenames:
        mime = EXT_MIME.get(extension_of(name or ""))
        if mime:
            return mime
    return raw or "application/octet-stream"


def _safe_disposition_filename(filename: str) -> tuple[str, str]:
    safe = (filename or "file").replace('"', "").replace("\n", " ").replace("\r", " ")
    ascii_name = "".join(ch if ord(ch) < 128 else "_" for ch in safe) or "file"
    return ascii_name, quote(safe)


def inline_content_disposition(filename: str) -> str:
    """RFC 5987-friendly inline disposition so browsers preview instead of Save As."""
    ascii_name, encoded = _safe_disposition_filename(filename)
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def attachment_content_disposition(filename: str) -> str:
    """RFC 5987-friendly attachment disposition so browsers use the given download name."""
    ascii_name, encoded = _safe_disposition_filename(filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
