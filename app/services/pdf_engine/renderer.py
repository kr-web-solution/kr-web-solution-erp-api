"""Jinja2 + WeasyPrint rendering with restricted local asset access."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse

from ..pdf import PAPER_SIZES, resolve_paper_size
from . import asset_manager, template_manager
from .exceptions import RenderError

logger = logging.getLogger("kr.pdf")

_PAGE_CSS = {
    "a3": "A3",
    "a4": "A4",
    "a5": "A5",
    "a6": "A6",
    "letter": "letter",
    "legal": "legal",
    "tabloid": "11in 17in",
    "executive": "7.25in 10.5in",
    "b4": "250mm 353mm",
    "b5": "176mm 250mm",
}


def page_css_size(paper_size: Optional[str], orientation: Optional[str]) -> tuple[str, str, float]:
    """Return (page_css_size, paper_key, scale)."""
    from reportlab.lib.pagesizes import A4

    paper_key, pagesize = resolve_paper_size(paper_size, orientation)
    scale = max(0.78, min(1.35, pagesize[0] / A4[0]))
    landscape = pagesize[0] > pagesize[1]
    page_css = _PAGE_CSS.get(paper_key, "A4")
    if landscape and paper_key in {"a3", "a4", "a5", "a6", "b4", "b5"}:
        page_css = f"{page_css} landscape"
    elif landscape and paper_key in PAPER_SIZES:
        w_in = pagesize[0] / 72.0
        h_in = pagesize[1] / 72.0
        page_css = f"{w_in:.3f}in {h_in:.3f}in"
    return page_css, paper_key, scale


def apply_page_defaults(
    context: dict[str, Any],
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> dict[str, Any]:
    """Ensure page geometry keys exist on context."""
    ctx = dict(context)
    if "page_css_size" not in ctx or paper_size or orientation:
        page_css, _, scale = page_css_size(paper_size or "a4", orientation or "portrait")
        ctx.setdefault("page_css_size", page_css)
        ctx.setdefault("margin_side", round(8.5 * scale, 2))
        ctx.setdefault("margin_top", round(7.5 * scale, 2))
        ctx.setdefault("margin_bottom", round(14 * scale, 2))

        def fs(base: float) -> float:
            return round(max(6.0, min(28.0, base * scale)), 1)

        ctx.setdefault("body_font", fs(10.5))
        ctx.setdefault("meta_font", fs(9.5))
        ctx.setdefault("small_font", fs(9))
        ctx.setdefault("tiny_font", fs(7.5))
        ctx.setdefault("label_font", fs(9))
        ctx.setdefault("table_font", fs(10))
        ctx.setdefault("title_font", fs(22))
        ctx.setdefault("company_name_size", fs(14))
        ctx.setdefault("client_name_size", fs(12))
        ctx.setdefault("logo_h", fs(36))
        ctx.setdefault("logo_w", fs(54))
        ctx.setdefault("total_label_size", fs(11))
        ctx.setdefault("total_amount_size", fs(14))
    return ctx


def _restricted_url_fetcher(url: str):
    """Allow only file: / data: URLs under allowlisted roots. Block remote HTTP."""
    from weasyprint import default_url_fetcher

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"http", "https", "ftp"}:
        raise RenderError("Remote URL fetching is disabled for PDF rendering")
    if scheme in {"", "file"}:
        path = parsed.path
        if scheme == "file":
            # file:///C:/... on Windows
            path = parsed.path
            if path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
        if path:
            try:
                asset_manager.resolve_safe_path(path)
            except Exception as exc:
                raise RenderError("Blocked asset path") from exc
    return default_url_fetcher(url)


def render_html_to_pdf(
    html: str,
    *,
    base_url: Optional[str] = None,
) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover
        raise RenderError(f"WeasyPrint unavailable: {exc}") from exc

    started = time.perf_counter()
    try:
        base = base_url or str(template_manager.templates_root())
        pdf = HTML(
            string=html,
            base_url=base,
            url_fetcher=_restricted_url_fetcher,
        ).write_pdf()
    except RenderError:
        raise
    except Exception as exc:
        logger.exception("WeasyPrint render failed")
        raise RenderError("PDF rendering failed") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "pdf_render_complete bytes=%s duration_ms=%.1f",
        len(pdf),
        elapsed_ms,
    )
    return pdf


def render_template_pdf(
    doc_type: str,
    context: dict[str, Any],
    *,
    template_key: Optional[str] = None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> bytes:
    resolved = template_manager.resolve_template(doc_type, template_key)
    ctx = apply_page_defaults(context, paper_size=paper_size, orientation=orientation)
    logger.info(
        "pdf_render_start doc_type=%s template=%s",
        doc_type,
        resolved.relative_path,
    )
    html = template_manager.render_html(resolved.relative_path, ctx)
    return render_html_to_pdf(html, base_url=str(template_manager.templates_root()))
