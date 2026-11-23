"""Invoice Jinja context for the PDF engine (moved from invoice_pdf_weasy)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from ...pdf import (
    PAPER_SIZES,
    _type_label,
    format_inr,
    resolve_paper_size,
)
from .. import asset_manager


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


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%d %b %Y")
    except Exception:
        return str(value)


def _split_description(description: str) -> tuple[str, Optional[str]]:
    parts = [p.strip() for p in (description or "").split("\n") if p.strip()]
    if len(parts) <= 1:
        return (description or "").strip() or "—", None
    return parts[0], " ".join(parts[1:])


def _scale_for_key(paper_key: str) -> float:
    from reportlab.lib.pagesizes import A4

    _, pagesize = resolve_paper_size(paper_key, "portrait")
    return max(0.78, min(1.35, pagesize[0] / A4[0]))


_STATUS_BADGE = {
    "draft": ("DRAFT", "#F5B942", "#172554"),
    "proof_pending": ("PROOF PENDING", "#F97316", "#FFFFFF"),
    "paid": ("PAID", "#16A34A", "#FFFFFF"),
    "partially_paid": ("PARTIALLY PAID", "#F97316", "#FFFFFF"),
    "unpaid": ("UNPAID", "#DC2626", "#FFFFFF"),
    "overdue": ("OVERDUE", "#DC2626", "#FFFFFF"),
    "cancelled": ("CANCELLED", "#64748B", "#FFFFFF"),
    "void": ("VOID", "#64748B", "#FFFFFF"),
    "sent": ("UNPAID", "#DC2626", "#FFFFFF"),
}


def _file_data_uri(path: Optional[Path]) -> Optional[str]:
    if not path or not path.is_file():
        return None
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%d %b %Y")
    except Exception:
        return str(value)


def _split_description(description: str) -> tuple[str, Optional[str]]:
    parts = [p.strip() for p in (description or "").split("\n") if p.strip()]
    if len(parts) <= 1:
        return (description or "").strip() or "—", None
    return parts[0], " ".join(parts[1:])


def _scale_for_key(paper_key: str) -> float:
    from reportlab.lib.pagesizes import A4

    _, pagesize = resolve_paper_size(paper_key, "portrait")
    return max(0.78, min(1.35, pagesize[0] / A4[0]))


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def build_invoice_context(
    invoice,
    client,
    settings,
    items,
    project=None,
    parent_invoice=None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> dict[str, Any]:
    from ...amount_in_words import amount_in_words_inr
    from ...invoice_snapshot import (
        client_view_from_snapshot,
        get_snapshot,
        payment_details_are_locked,
        settings_view_from_snapshot,
    )
    from ...invoice_terms import resolve_invoice_terms
    from ...payment_instructions import resolve_payment_instructions
    from ...tax_payroll import resolve_payment_upi

    snap = get_snapshot(invoice)
    status = (getattr(invoice, "status", "") or "").lower()
    inv_type_raw = (getattr(invoice, "invoice_type", None) or "").lower()
    is_credit = inv_type_raw == "credit_note"
    is_debit = inv_type_raw == "debit_note"
    frozen_terms = None

    if snap:
        prefer_live = not payment_details_are_locked(invoice)
        settings = settings_view_from_snapshot(
            snap, settings, prefer_live_payment=prefer_live
        )
        client = client_view_from_snapshot(snap, client, prefer_live=prefer_live)
        if (
            not prefer_live
            and isinstance(snap.get("terms"), list)
            and snap["terms"]
        ):
            frozen_terms = [str(t) for t in snap["terms"] if str(t).strip()]
        if isinstance(snap.get("project"), dict) and project is None:
            project = SimpleNamespace(
                name=snap["project"].get("name"),
                id=snap["project"].get("ref") or "prj_snap",
            )
        if isinstance(snap.get("parent_invoice_number"), str) and parent_invoice is None:
            parent_invoice = SimpleNamespace(
                invoice_number=snap["parent_invoice_number"]
            )

    paper_key, pagesize = resolve_paper_size(paper_size, orientation)
    scale = _scale_for_key(paper_key)
    landscape = pagesize[0] > pagesize[1]
    page_css = _PAGE_CSS.get(paper_key, "A4")
    if landscape and paper_key in {"a3", "a4", "a5", "a6", "b4", "b5"}:
        page_css = f"{page_css} landscape"
    elif landscape and paper_key in PAPER_SIZES:
        # Custom sizes: swap via explicit mm if needed — WeasyPrint accepts "landscape" keyword
        # for named sizes; for inch sizes leave as-is width×height already swapped in pagesize.
        w_in = pagesize[0] / 72.0
        h_in = pagesize[1] / 72.0
        page_css = f"{w_in:.3f}in {h_in:.3f}in"

    settings_gst = bool(settings and getattr(settings, "gst_enabled", False))
    inv_gst_override = getattr(invoice, "gst_enabled", None)
    gst_on = settings_gst if inv_gst_override is None else bool(inv_gst_override)
    gst_unreg = bool(settings and getattr(settings, "gst_unregistered", False)) and gst_on
    company_gstin = (
        (getattr(settings, "company_gstin", None) or "").strip() if settings else ""
    )
    company_pan = (
        (getattr(settings, "company_pan", None) or "").strip() if settings else ""
    )

    tax_paise_val = float(getattr(invoice, "tax_paise", 0) or 0)
    tax_rate = float(getattr(invoice, "tax_rate_percent", 0) or 0)
    if is_credit:
        doc_title = "CREDIT NOTE"
    elif is_debit:
        doc_title = "DEBIT NOTE"
    elif inv_type_raw == "proforma":
        doc_title = "PROFORMA INVOICE"
    elif inv_type_raw == "late_fee":
        doc_title = "LATE FEE INVOICE"
    elif status == "paid":
        doc_title = "PAYMENT RECEIPT"
    elif tax_paise_val > 0 or tax_rate > 0 or gst_on is True:
        doc_title = "TAX INVOICE"
    elif gst_on is False:
        doc_title = "BILL OF SUPPLY"
    else:
        doc_title = "INVOICE"

    amount_paid = int(getattr(invoice, "amount_paid_paise", 0) or 0)
    total_amt = int(getattr(invoice, "total_paise", 0) or 0)
    if status == "paid" and amount_paid <= 0:
        amount_paid = total_amt
    balance_due = max(0, total_amt - amount_paid)

    badge_key = status
    if status == "sent" and 0 < amount_paid < total_amt:
        badge_key = "partially_paid"
    elif status == "sent":
        badge_key = "unpaid"
    elif (
        status not in ("paid", "cancelled", "void", "overdue", "draft", "proof_pending")
        and 0 < amount_paid < total_amt
    ):
        badge_key = "partially_paid"
    badge = _STATUS_BADGE.get(badge_key) or _STATUS_BADGE.get(status)
    badge_label, badge_bg, badge_fg = badge if badge else (None, "#64748B", "#FFFFFF")

    if is_credit:
        total_label = "CREDIT TOTAL"
        number_label = "Credit Note #:"
    elif is_debit:
        total_label = "DEBIT TOTAL"
        number_label = "Debit Note #:"
    elif status == "paid":
        total_label = "TOTAL"
        number_label = "Invoice #:"
    elif status in {"cancelled", "void"}:
        total_label = "TOTAL (NOT PAYABLE)"
        number_label = "Invoice #:"
    else:
        total_label = "TOTAL DUE"
        number_label = "Invoice #:"

    comp_name = (
        getattr(settings, "company_name", None) if settings else None
    ) or "KR Web Solution"
    comp_email = (
        getattr(settings, "smtp_from_email", None)
        or getattr(settings, "company_email", None)
        or "billing@krwebsolution.com"
    )
    comp_website = (
        getattr(settings, "company_website", None) or "www.krwebsolution.com"
    ).replace("https://", "").replace("http://", "")
    logo_uri = asset_manager.file_data_uri(
        asset_manager.logo_path(getattr(settings, "company_logo_url", None) if settings else None)
    )

    # Client / bill-to
    c_name = client.name if client else "Client Name"
    contact = " · ".join(
        p
        for p in [
            getattr(client, "email", None) if client else None,
            getattr(client, "phone", None) if client else None,
        ]
        if p
    )
    addr = (getattr(client, "address", None) or "").strip() if client else ""
    city = (getattr(client, "city", None) or "").strip() if client else ""
    state = (getattr(client, "state", None) or "").strip() if client else ""
    pin = (getattr(client, "pincode", None) or "").strip() if client else ""
    place = ", ".join(p for p in [city, state, pin] if p)
    addr_blob = addr.lower()
    place_already = bool(place) and all(
        part.lower() in addr_blob for part in [city, state, pin] if part
    )
    client_meta = []
    if gst_on and client and getattr(client, "gstin", None):
        client_meta.append({"label": "GSTIN", "value": client.gstin})
    if gst_on and client and getattr(client, "pan", None):
        client_meta.append({"label": "PAN", "value": client.pan})
    if client and getattr(client, "state_code", None):
        client_meta.append({"label": "State code", "value": client.state_code})
    pos = (getattr(invoice, "place_of_supply", None) or "").strip()
    if pos:
        client_meta.append({"label": "Place of supply", "value": pos})

    # Project
    type_lbl = _type_label(getattr(invoice, "invoice_type", None))
    project_rows = []
    if project and getattr(project, "name", None):
        project_rows.append(
            {"label": "Project", "value": project.name, "bold": True, "mono": False}
        )
    if type_lbl and type_lbl != "Invoice":
        project_rows.append(
            {"label": "Type", "value": type_lbl, "bold": False, "mono": False}
        )
    if project and getattr(project, "id", None):
        short = str(project.id).replace("prj_", "PRJ-").upper()
        project_rows.append(
            {"label": "Reference", "value": short, "bold": False, "mono": True}
        )
    covers_from = getattr(invoice, "covers_from", None)
    covers_to = getattr(invoice, "covers_to", None)
    if covers_from or covers_to:
        period = " – ".join(
            [
                covers_from.strftime("%d %b %Y") if covers_from else "",
                covers_to.strftime("%d %b %Y") if covers_to else "",
            ]
        ).strip(" –")
        project_rows.append(
            {"label": "Period", "value": period, "bold": False, "mono": False}
        )
    ledger = getattr(invoice, "ledger", None)
    if ledger and ledger != "development":
        project_rows.append(
            {
                "label": "Category",
                "value": str(ledger).capitalize(),
                "bold": False,
                "mono": False,
            }
        )

    show_hsn = gst_on and any(
        (getattr(itm, "hsn_sac", None) or "").strip() for itm in items
    )
    show_unit = any((getattr(itm, "unit", None) or "").strip() for itm in items)
    line_items = []
    for itm in items:
        title, detail = _split_description(itm.description or "")
        qty = float(itm.quantity) if itm.quantity is not None else 0
        unit_val = (getattr(itm, "unit", None) or "").strip()
        if not unit_val and qty:
            unit_val = "Nos"
        hsn_val = (getattr(itm, "hsn_sac", None) or "—").strip() or "—"
        kind_raw = (getattr(itm, "line_kind", None) or "").strip()
        kind_lbl = _type_label(kind_raw) if kind_raw else None
        if kind_lbl and kind_lbl.lower() == "invoice":
            kind_lbl = kind_raw.replace("_", " ").upper() if kind_raw else None
        elif kind_raw and not kind_lbl:
            kind_lbl = kind_raw.replace("_", " ").upper()
        elif kind_lbl:
            kind_lbl = kind_lbl.upper()
        line_items.append(
            {
                "title": title,
                "detail": detail,
                "line_kind": kind_lbl,
                "hsn": hsn_val,
                "qty": f"{qty:g}",
                "unit": unit_val or "—",
                "rate": format_inr(int(getattr(itm, "unit_rate_paise", 0) or 0)),
                "amount": format_inr(int(getattr(itm, "amount_paise", 0) or 0)),
            }
        )

    show_gst = gst_on and (tax_paise_val > 0 or tax_rate > 0)
    discount_paise = int(getattr(invoice, "discount_paise", 0) or 0)
    cgst_paise = int(getattr(invoice, "cgst_paise", 0) or 0)
    sgst_paise = int(getattr(invoice, "sgst_paise", 0) or 0)
    igst_paise = int(getattr(invoice, "igst_paise", 0) or 0)
    round_off = int(getattr(invoice, "round_off_paise", 0) or 0)
    other_charges = int(getattr(invoice, "other_charges_paise", 0) or 0)
    credit_adj = int(getattr(invoice, "credit_adjustment_paise", 0) or 0)

    totals_rows = [
        {"label": "Subtotal", "value": format_inr(int(invoice.subtotal_paise or 0))}
    ]
    if discount_paise > 0:
        totals_rows.append(
            {"label": "Discount", "value": f"−{format_inr(discount_paise)}"}
        )
    if show_gst:
        if cgst_paise or sgst_paise or igst_paise:
            half = tax_rate / 2.0 if tax_rate else 0
            if cgst_paise:
                totals_rows.append(
                    {"label": f"CGST ({half:g}%)", "value": format_inr(cgst_paise)}
                )
            if sgst_paise:
                totals_rows.append(
                    {"label": f"SGST ({half:g}%)", "value": format_inr(sgst_paise)}
                )
            if igst_paise:
                totals_rows.append(
                    {"label": f"IGST ({tax_rate:g}%)", "value": format_inr(igst_paise)}
                )
        else:
            totals_rows.append(
                {
                    "label": f"GST ({tax_rate:g}%)",
                    "value": format_inr(int(invoice.tax_paise or 0)),
                }
            )
    if round_off:
        totals_rows.append({"label": "Round Off", "value": format_inr(round_off)})
    if other_charges > 0:
        totals_rows.append(
            {"label": "Other Charges", "value": format_inr(other_charges)}
        )
    if credit_adj > 0:
        totals_rows.append(
            {"label": "Credit Adjustment", "value": f"−{format_inr(credit_adj)}"}
        )

    after_total_rows = []
    if not is_credit and (amount_paid > 0 or status == "paid"):
        after_total_rows.append(
            {"label": "Amount Paid", "value": format_inr(amount_paid), "bold": False}
        )
        after_total_rows.append(
            {"label": "Balance Due", "value": format_inr(balance_due), "bold": True}
        )

    pay = (
        resolve_payment_upi(settings, invoice)
        if settings
        else {"upi_id": None, "upi_qr_url": None}
    )
    instr = resolve_payment_instructions(settings, upi_id=pay.get("upi_id"))
    show_bank = (
        settings
        and not is_credit
        and status not in ("cancelled", "void", "paid")
        and balance_due > 0
        and instr.get("has_any")
    )
    bank_cells = []
    if show_bank:
        mapping = [
            ("Beneficiary", instr.get("beneficiary")),
            ("Bank", instr.get("bank_name")),
            ("Account No.", instr.get("account_number")),
            ("IFSC", instr.get("ifsc")),
            ("UPI ID", instr.get("upi_id")),
            ("PayPal", instr.get("paypal_id")),
        ]
        bank_cells = [
            {"label": lbl, "value": val} for lbl, val in mapping if val
        ]
    bank_rows = []
    for i in range(0, len(bank_cells), 2):
        pair = bank_cells[i : i + 2]
        if len(pair) == 1:
            pair = [pair[0], None]
        bank_rows.append(pair)

    payment_lines = []
    try:
        payments = list(getattr(invoice, "payments", None) or [])
    except Exception:
        payments = []
    for p in payments:
        if getattr(p, "deleted_at", None):
            continue
        bits = []
        if getattr(p, "paid_at", None):
            bits.append(p.paid_at.strftime("%d %b %Y"))
        if getattr(p, "method", None):
            bits.append(str(p.method).replace("_", " ").title())
        bits.append(format_inr(int(getattr(p, "amount_paise", 0) or 0)))
        ref = (getattr(p, "reference", None) or "").strip()
        note = (getattr(p, "note", None) or "").strip()
        # Avoid duplicating UTR when note already embeds the reference
        if ref and note and ref in note:
            bits.append(note)
        else:
            if ref:
                bits.append(f"UTR/Ref: {ref}")
            if note:
                bits.append(note)
        payment_lines.append(" · ".join(bits))

    terms = frozen_terms or resolve_invoice_terms(
        invoice_type=getattr(invoice, "invoice_type", None),
        invoice_notes=getattr(invoice, "notes", None),
        settings=settings,
        status=status,
    )

    def fs(base: float) -> float:
        return round(max(6.0, min(28.0, base * scale)), 1)

    footer_meta = " · ".join(b for b in [comp_website or None, comp_email or None] if b)

    return {
        "doc_title": doc_title,
        "invoice_number": invoice.invoice_number or "",
        "number_label": number_label,
        "issue_date": _fmt_date(getattr(invoice, "issue_date", None)),
        "due_date": None if is_credit else _fmt_date(getattr(invoice, "due_date", None)),
        "po_number": (getattr(invoice, "po_number", None) or "").strip() or None,
        "parent_invoice_number": (
            parent_invoice.invoice_number if parent_invoice else None
        ),
        "paid_on": (
            _fmt_date(getattr(invoice, "paid_at", None))
            if (not is_credit)
            and getattr(invoice, "paid_at", None)
            and (status == "paid" or balance_due <= 0)
            else None
        ),
        "badge_label": badge_label,
        "badge_bg": badge_bg,
        "badge_fg": badge_fg,
        "company_name": comp_name,
        "company_tagline": getattr(settings, "company_tagline", None)
        or "Web Development | Cloud | Software Solutions",
        "company_address": (getattr(settings, "company_address", None) or "").strip(),
        "company_email": comp_email,
        "company_website": comp_website,
        "company_phone": (getattr(settings, "company_phone", None) or "").strip(),
        "company_gstin": company_gstin
        if gst_on and company_gstin
        else ("N/A" if gst_on and gst_unreg else None),
        "company_pan": company_pan if gst_on and company_pan else None,
        "logo_uri": logo_uri,
        "watermark_uri": logo_uri,  # grayscale CSS in template (matches preview)
        "footer_meta": footer_meta,
        "client_name": c_name,
        "client_contact": contact or None,
        "client_address": addr or None,
        "client_place": place if place and not place_already else None,
        "client_country": (
            (getattr(client, "country", None) or "").strip() if client else None
        )
        or None,
        "client_meta": client_meta,
        "project_rows": project_rows,
        "show_hsn": show_hsn,
        "show_unit": show_unit,
        "items": line_items,
        "show_bank": bool(show_bank),
        "bank_cells": bank_cells,
        "bank_rows": bank_rows,
        "wire_text": " · ".join(instr.get("wire_instructions") or [])
        if show_bank
        else None,
        "qr_uri": asset_manager.file_data_uri(asset_manager.upi_qr_path(pay.get("upi_qr_url")))
        if show_bank
        else None,
        "is_credit": is_credit,
        "show_payment_confirmation": (not is_credit)
        and (status == "paid" or balance_due <= 0),
        "amount_paid_fmt": format_inr(amount_paid),
        "payment_lines": payment_lines,
        "totals_rows": totals_rows,
        "total_label": total_label,
        "total_fmt": format_inr(total_amt),
        "after_total_rows": after_total_rows,
        "amount_in_words": amount_in_words_inr(total_amt),
        "terms": (terms or [])[:12],
        "page_css_size": page_css,
        "margin_side": round(8.5 * scale, 2),
        "margin_top": round(7.5 * scale, 2),
        "margin_bottom": round(14 * scale, 2),
        "body_font": fs(10.5),
        "meta_font": fs(9.5),
        "small_font": fs(9),
        "tiny_font": fs(7.5),
        "label_font": fs(9),
        "table_font": fs(10),
        "title_font": fs(22),
        "company_name_size": fs(14),
        "client_name_size": fs(12),
        "logo_h": fs(36),
        "logo_w": fs(54),
        "total_label_size": fs(11),
        "total_amount_size": fs(14),
    }



def render_invoice_via_engine(
    invoice,
    client,
    settings,
    items,
    project=None,
    parent_invoice=None,
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
    template_key: Optional[str] = None,
) -> bytes:
    from ..service import render_document_sync

    ctx = build_invoice_context(
        invoice,
        client,
        settings,
        items,
        project=project,
        parent_invoice=parent_invoice,
        paper_size=paper_size,
        orientation=orientation,
    )
    return render_document_sync(
        "invoice",
        ctx,
        template_key=template_key,
        paper_size=paper_size,
        orientation=orientation,
    )
