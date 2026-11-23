"""Filesystem + DB registry resolution for PDF document templates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .exceptions import TemplateNotFoundError

logger = logging.getLogger("kr.pdf")

_TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "templates" / "pdf"

# doc_type -> relative template path under templates/pdf/
_DEFAULT_DESIGNS: dict[str, str] = {
    "invoice": "invoices/v1/document.html",
    "receipt": "receipts/v1/document.html",  # includes invoice design; paid chrome via context
    "payslip": "payslips/v1/document.html",
    "quotation": "quotations/v1/document.html",
    "purchase_order": "purchase_orders/v1/document.html",
    "report": "reports/v1/document.html",
}

_DESIGN_ALIASES: dict[str, str] = {
    "invoice/v1": "invoices/v1/document.html",
    "invoices/v1": "invoices/v1/document.html",
    "receipt/v1": "invoices/v1/document.html",
    "receipts/v1": "invoices/v1/document.html",
    "payslip/v1": "payslips/v1/document.html",
    "payslips/v1": "payslips/v1/document.html",
    "quotation/v1": "quotations/v1/document.html",
    "quotations/v1": "quotations/v1/document.html",
    "purchase_order/v1": "purchase_orders/v1/document.html",
    "purchase_orders/v1": "purchase_orders/v1/document.html",
    "report/v1": "reports/v1/document.html",
    "reports/v1": "reports/v1/document.html",
}


@dataclass(frozen=True)
class ResolvedTemplate:
    doc_type: str
    design_key: str
    relative_path: str
    absolute_path: Path


_jinja_env: Optional[Environment] = None


def templates_root() -> Path:
    return _TEMPLATES_ROOT


def jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
            autoescape=select_autoescape(["html", "xml"]),
            auto_reload=False,
        )
    return _jinja_env


def list_disk_designs() -> list[dict[str, str]]:
    """Scan templates/pdf for document.html files."""
    out: list[dict[str, str]] = []
    if not _TEMPLATES_ROOT.is_dir():
        return out
    for path in sorted(_TEMPLATES_ROOT.rglob("document.html")):
        rel = path.relative_to(_TEMPLATES_ROOT).as_posix()
        parts = rel.split("/")
        doc_folder = parts[0] if parts else "unknown"
        version = parts[1] if len(parts) > 1 else "v1"
        out.append(
            {
                "doc_folder": doc_folder,
                "version": version,
                "design_key": f"{doc_folder}/{version}",
                "relative_path": rel,
            }
        )
    return out


def resolve_template(
    doc_type: str,
    template_key: Optional[str] = None,
) -> ResolvedTemplate:
    doc = (doc_type or "").strip().lower()
    key = (template_key or "").strip().lower() or None

    if key:
        # Allow "invoices/v1/document.html" or alias "invoice/v1"
        if key.endswith(".html"):
            rel = key.lstrip("/")
        else:
            rel = _DESIGN_ALIASES.get(key)
            if not rel:
                # try design_key/version convention: invoices/v1
                candidate = Path(_TEMPLATES_ROOT) / key / "document.html"
                if candidate.is_file():
                    rel = candidate.relative_to(_TEMPLATES_ROOT).as_posix()
                else:
                    raise TemplateNotFoundError(key)
        abs_path = (_TEMPLATES_ROOT / rel).resolve()
        try:
            abs_path.relative_to(_TEMPLATES_ROOT.resolve())
        except ValueError as exc:
            raise TemplateNotFoundError(key) from exc
        if not abs_path.is_file():
            raise TemplateNotFoundError(key)
        return ResolvedTemplate(
            doc_type=doc,
            design_key=key,
            relative_path=rel,
            absolute_path=abs_path,
        )

    rel = _DEFAULT_DESIGNS.get(doc)
    if not rel:
        raise TemplateNotFoundError(doc or "(empty)")
    abs_path = _TEMPLATES_ROOT / rel
    if not abs_path.is_file():
        raise TemplateNotFoundError(f"{doc}:{rel}")
    return ResolvedTemplate(
        doc_type=doc,
        design_key=f"{doc}/v1",
        relative_path=rel,
        absolute_path=abs_path,
    )


def render_html(relative_path: str, context: dict) -> str:
    return jinja_env().get_template(relative_path).render(**context)
