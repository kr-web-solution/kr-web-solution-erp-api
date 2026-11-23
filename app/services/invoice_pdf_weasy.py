"""Backward-compatible invoice WeasyPrint entry — delegates to pdf_engine."""

from __future__ import annotations

from typing import Optional

from .pdf_engine.context.invoice import (  # noqa: F401
    build_invoice_context,
    render_invoice_via_engine,
)


def render_invoice_pdf_weasy(
    invoice,
    client,
    settings,
    items,
    project=None,
    parent_invoice=None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> bytes:
    """Render invoice PDF via the shared PDF engine (WeasyPrint)."""
    return render_invoice_via_engine(
        invoice,
        client,
        settings,
        items,
        project=project,
        parent_invoice=parent_invoice,
        paper_size=paper_size,
        orientation=orientation,
    )
