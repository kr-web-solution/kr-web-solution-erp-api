"""pypdf-based PDF manipulation (merge, split, watermark, encrypt, info)."""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional

from .exceptions import PdfOperationError

logger = logging.getLogger("kr.pdf")

MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_PAGES = 200
MAX_MERGE_FILES = 20


def _reader(data: bytes):
    from pypdf import PdfReader

    if len(data) > MAX_PDF_BYTES:
        raise PdfOperationError("PDF exceeds maximum allowed size")
    try:
        return PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise PdfOperationError("Invalid or corrupted PDF") from exc


def _writer_bytes(writer) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def validate_pdf(data: bytes) -> bool:
    if not data or not data.startswith(b"%PDF"):
        return False
    try:
        r = _reader(data)
        return len(r.pages) >= 1
    except PdfOperationError:
        return False


def get_info(data: bytes) -> dict[str, Any]:
    reader = _reader(data)
    encrypted = bool(reader.is_encrypted)
    meta: dict[str, Any] = {}
    page_count = 0
    if encrypted:
        # Cannot read pages/metadata without a password.
        try:
            page_count = len(reader.pages)
        except Exception:
            page_count = 0
    else:
        page_count = len(reader.pages)
        try:
            if reader.metadata:
                for key in ("/Title", "/Author", "/Subject", "/Creator", "/Producer"):
                    val = reader.metadata.get(key)
                    if val:
                        meta[key.lstrip("/").lower()] = str(val)
        except Exception:
            meta = {}
    return {
        "page_count": page_count,
        "encrypted": encrypted,
        "metadata": meta,
    }


def merge_pdfs(parts: list[bytes]) -> bytes:
    from pypdf import PdfWriter

    if not parts:
        raise PdfOperationError("No PDFs to merge")
    if len(parts) > MAX_MERGE_FILES:
        raise PdfOperationError(f"Too many PDFs (max {MAX_MERGE_FILES})")
    writer = PdfWriter()
    total_pages = 0
    for raw in parts:
        reader = _reader(raw)
        total_pages += len(reader.pages)
        if total_pages > MAX_PAGES:
            raise PdfOperationError(f"Merged PDF would exceed {MAX_PAGES} pages")
        for page in reader.pages:
            writer.add_page(page)
    logger.info("pdf_merge files=%s pages=%s", len(parts), total_pages)
    return _writer_bytes(writer)


def _parse_ranges(ranges: str, page_count: int) -> list[int]:
    """Parse '1-3,5' into 0-based page indexes."""
    indexes: list[int] = []
    for part in ranges.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start = int(a.strip())
                end = int(b.strip())
            except ValueError as exc:
                raise PdfOperationError(f"Invalid page range: {part}") from exc
            if start < 1 or end < start or end > page_count:
                raise PdfOperationError(f"Page range out of bounds: {part}")
            indexes.extend(range(start - 1, end))
        else:
            try:
                n = int(part)
            except ValueError as exc:
                raise PdfOperationError(f"Invalid page number: {part}") from exc
            if n < 1 or n > page_count:
                raise PdfOperationError(f"Page out of bounds: {n}")
            indexes.append(n - 1)
    if not indexes:
        raise PdfOperationError("No pages selected")
    # de-dupe preserving order
    seen: set[int] = set()
    out: list[int] = []
    for i in indexes:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def split_pdf(data: bytes, ranges: str) -> bytes:
    from pypdf import PdfWriter

    reader = _reader(data)
    indexes = _parse_ranges(ranges, len(reader.pages))
    writer = PdfWriter()
    for i in indexes:
        writer.add_page(reader.pages[i])
    logger.info("pdf_split pages=%s", len(indexes))
    return _writer_bytes(writer)


def extract_pages(data: bytes, ranges: str) -> bytes:
    return split_pdf(data, ranges)


def rotate_pages(data: bytes, degrees: int = 90, ranges: Optional[str] = None) -> bytes:
    from pypdf import PdfWriter

    if degrees not in {90, 180, 270, -90, -180, -270}:
        raise PdfOperationError("Rotation must be a multiple of 90 degrees")
    reader = _reader(data)
    indexes = (
        _parse_ranges(ranges, len(reader.pages))
        if ranges
        else list(range(len(reader.pages)))
    )
    writer = PdfWriter()
    index_set = set(indexes)
    for i, page in enumerate(reader.pages):
        if i in index_set:
            page.rotate(degrees)
        writer.add_page(page)
    return _writer_bytes(writer)


def add_metadata(data: bytes, metadata: dict[str, str]) -> bytes:
    from pypdf import PdfWriter

    reader = _reader(data)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    clean = {
        k: str(v)[:200]
        for k, v in metadata.items()
        if k.lower() in {"title", "author", "subject", "creator", "producer"}
    }
    if clean:
        writer.add_metadata({f"/{k.title()}": v for k, v in clean.items()})
    return _writer_bytes(writer)


def encrypt_pdf(
    data: bytes,
    user_password: str,
    owner_password: Optional[str] = None,
) -> bytes:
    from pypdf import PdfWriter

    if not user_password or len(user_password) < 4:
        raise PdfOperationError("Password must be at least 4 characters")
    reader = _reader(data)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(
        user_password=user_password,
        owner_password=owner_password or user_password,
    )
    logger.info("pdf_encrypt pages=%s", len(reader.pages))
    return _writer_bytes(writer)


def add_text_watermark(data: bytes, text: str, opacity: float = 0.15) -> bytes:
    """Stamp a simple text watermark on every page via pypdf merge."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import Color

    text = re.sub(r"\s+", " ", (text or "DRAFT").strip())[:40] or "DRAFT"
    opacity = max(0.05, min(0.5, float(opacity)))
    # Clone into a writer first (required by pypdf >= 5 for merge_page).
    writer = PdfWriter(clone_from=io.BytesIO(data))

    for page in writer.pages:
        box = page.mediabox
        w = float(box.width)
        h = float(box.height)
        stamp_buf = io.BytesIO()
        c = rl_canvas.Canvas(stamp_buf, pagesize=(w, h))
        c.setFillColor(Color(0.4, 0.4, 0.4, alpha=opacity))
        c.saveState()
        c.translate(w / 2, h / 2)
        c.rotate(45)
        c.setFont("Helvetica-Bold", max(28, min(72, w / 8)))
        c.drawCentredString(0, 0, text)
        c.restoreState()
        c.save()
        stamp_buf.seek(0)
        stamp_page = PdfReader(stamp_buf).pages[0]
        page.merge_page(stamp_page)

    logger.info("pdf_watermark pages=%s", len(writer.pages))
    return _writer_bytes(writer)
