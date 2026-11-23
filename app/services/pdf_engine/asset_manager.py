"""Safe local asset resolution for logos, fonts, and QR codes."""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional

from .exceptions import AssetAccessError

logger = logging.getLogger("kr.pdf")

_APP_ROOT = Path(__file__).resolve().parents[2]  # app/
_REPO_ROOT = _APP_ROOT.parent
_ASSETS = _APP_ROOT / "assets"
_UPLOADS_BRAND = _REPO_ROOT / "uploads" / "brand"
_FONTS = _APP_ROOT / "templates" / "pdf" / "static" / "fonts"
_TMP = _REPO_ROOT / "uploads" / "tmp" / "pdf"

_ALLOWED_ROOTS = (
    _ASSETS.resolve(),
    _UPLOADS_BRAND.resolve(),
    _FONTS.resolve(),
    (_APP_ROOT / "templates" / "pdf").resolve(),
    _TMP.resolve(),
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._\-]+$")


def allowed_roots() -> tuple[Path, ...]:
    return _ALLOWED_ROOTS


def ensure_tmp_dir() -> Path:
    _TMP.mkdir(parents=True, exist_ok=True)
    return _TMP


def tmp_dir() -> Path:
    return ensure_tmp_dir()


def resolve_safe_path(path: Path | str) -> Path:
    """Resolve path and ensure it stays under an allowlisted root."""
    candidate = Path(path).resolve()
    for root in _ALLOWED_ROOTS:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise AssetAccessError("Path is outside allowed asset roots")


def file_data_uri(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    try:
        safe = resolve_safe_path(path)
    except AssetAccessError:
        return None
    if not safe.is_file():
        return None
    mime = mimetypes.guess_type(str(safe))[0] or "application/octet-stream"
    data = base64.b64encode(safe.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def logo_path(logo_url: Optional[str] = None) -> Optional[Path]:
    """Resolve company logo from uploads/brand or bundled assets."""
    if logo_url:
        m = re.search(r"logo-file/([^/?#]+)", str(logo_url))
        if m:
            name = m.group(1)
            if _SAFE_NAME.match(name):
                candidate = _UPLOADS_BRAND / name
                if candidate.is_file():
                    return candidate
        # bare filename
        bare = Path(str(logo_url)).name
        if _SAFE_NAME.match(bare):
            candidate = _UPLOADS_BRAND / bare
            if candidate.is_file():
                return candidate
    for cand in (_ASSETS / "krws-mark.png", _ASSETS / "kr-logo.png"):
        if cand.is_file():
            return cand
    return None


def upi_qr_path(upi_qr_url: Optional[str] = None) -> Optional[Path]:
    if not upi_qr_url:
        return None
    m = re.search(r"upi-qr-file/([^/?#]+)", str(upi_qr_url))
    name = m.group(1) if m else Path(str(upi_qr_url)).name
    if not _SAFE_NAME.match(name):
        return None
    candidate = _UPLOADS_BRAND / name
    return candidate if candidate.is_file() else None


def generate_qr_png_bytes(data: str, box_size: int = 6, border: int = 2) -> bytes:
    """Generate a QR PNG via qrcode + Pillow."""
    import qrcode

    qr = qrcode.QRCode(version=None, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_data_uri(data: str) -> str:
    raw = generate_qr_png_bytes(data)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


def sanitize_filename(name: str, default: str = "document.pdf") -> str:
    base = Path(name or default).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or default
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base[:180]
