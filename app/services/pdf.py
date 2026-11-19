import io
import re
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    Flowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_UPLOADS_BRAND = Path(__file__).resolve().parents[2] / "uploads" / "brand"
_LOGO_CANDIDATES = [
    _ASSETS / "krws-mark.png",
    _ASSETS / "kr-logo.png",
]

_INK = colors.HexColor("#111111")
_SECONDARY = colors.HexColor("#525252")
_MUTED = colors.HexColor("#737373")
_BORDER = colors.HexColor("#E5E5E5")
_CHARCOAL = colors.HexColor("#222222")
_ROW_ALT = colors.HexColor("#FAFAFA")
_WAVE = colors.HexColor("#EEEEEE")

_STATUS_BADGE = {
    "draft": ("DRAFT", "#FFC947", "#111111"),
    "proof_pending": ("PROOF PENDING", "#FFC947", "#111111"),
    "paid": ("PAID", "#16A34A", "#FFFFFF"),
    "overdue": ("OVERDUE", "#DC2626", "#FFFFFF"),
    "cancelled": ("CANCELLED", "#DC2626", "#FFFFFF"),
    "void": ("VOID", "#525252", "#FFFFFF"),
    "sent": ("SENT", "#333333", "#FFFFFF"),
}

_TYPE_LABELS = {
    "custom_dev": "Custom Development",
    "development": "Development",
    "project": "Project",
    "amc": "Maintenance / AMC",
    "maintenance": "Maintenance / AMC",
    "mixed": "Mixed Billing",
    "advance": "Advance",
    "balance": "Balance",
    "hosting": "Hosting",
    "services": "Services",
    "licensing": "Licensing",
    "late_fee": "Late Fee",
    "credit_note": "Credit Note",
}


def _indian_group(n: int) -> str:
    """Group integer digits in Indian style (e.g. 1,00,000)."""
    s = str(abs(n))
    if len(s) <= 3:
        return s
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while rest:
        parts.append(rest[-2:])
        rest = rest[:-2]
    return ",".join(reversed(parts)) + "," + last3


def format_inr(paise: int) -> str:
    """Indian currency formatting for PDF (Rs. + Indian grouping)."""
    sign = "-" if paise < 0 else ""
    rupees = abs(paise) // 100
    paise_part = abs(paise) % 100
    return f"{sign}Rs. {_indian_group(rupees)}.{paise_part:02d}"


def _logo_path() -> Optional[Path]:
    for p in _LOGO_CANDIDATES:
        if p.is_file():
            return p
    return None


def _resolve_upi_qr_path(qr_url: Optional[str]) -> Optional[Path]:
    if not qr_url:
        return None
    match = re.search(r"upi-qr-file/([^/?#]+)", qr_url)
    if not match:
        return None
    safe = Path(match.group(1)).name
    path = _UPLOADS_BRAND / safe
    return path if path.is_file() else None


def _type_label(invoice_type: Optional[str]) -> str:
    if not invoice_type:
        return "Invoice"
    return _TYPE_LABELS.get(invoice_type, invoice_type.replace("_", " ").title())


def _default_terms(status: str, invoice_type: Optional[str]) -> list[str]:
    if invoice_type == "credit_note":
        return [
            "This credit note adjusts the referenced parent invoice.",
            "No payment is requested against this credit note.",
            "Retain this document for your accounting records.",
        ]
    if status == "draft":
        return [
            "This is a draft invoice and is not a final demand for payment.",
            "Final terms apply once the invoice is issued to the client.",
            "Please contact KR Web Solution for clarifications before payment.",
        ]
    if status == "paid":
        return [
            "This invoice has been marked as paid. No further payment is due.",
            "Retain this document for your accounting records.",
            "For billing queries, contact billing@krwebsolution.com.",
        ]
    if status in {"cancelled", "void"}:
        return [
            "This invoice has been cancelled / voided and is not payable.",
            "Do not transfer funds against this document.",
            "Contact KR Web Solution if a replacement invoice is required.",
        ]
    if status == "overdue":
        return [
            "Payment is overdue. Please settle the outstanding balance immediately.",
            "Late fees may apply as per the agreed commercial terms.",
            "Share UTR / payment proof after transfer for faster reconciliation.",
        ]
    return [
        "Payment is due within the stated due date.",
        "Please include the invoice number in the payment reference / UTR.",
        "For billing queries, contact billing@krwebsolution.com.",
    ]


def _parse_terms(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    chunks = re.split(r"\n+|(?<=\.)\s+(?=[A-Z0-9])", str(raw).strip())
    out = []
    for chunk in chunks:
        line = re.sub(r"^\d+[\)\.\-\s]+", "", chunk).strip()
        if line:
            out.append(line)
    return out


class _StatusBadge(Flowable):
    def __init__(self, label: str, bg: str, fg: str):
        super().__init__()
        self.label = label
        self.bg = colors.HexColor(bg)
        self.fg = colors.HexColor(fg)
        self.height = 18
        self.width = max(72, len(label) * 6.2 + 24)

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        self.canv.setFillColor(self.bg)
        self.canv.roundRect(0, 0, self.width, self.height, 3, stroke=0, fill=1)
        self.canv.setFillColor(self.fg)
        self.canv.setFont("Helvetica-Bold", 8)
        self.canv.drawCentredString(self.width / 2, 5.5, self.label)


class _WaveFooter(Flowable):
    def __init__(self, width: float):
        super().__init__()
        self.width = width
        self.height = 28

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(_WAVE)
        c.setFillAlpha(0.35)
        p = c.beginPath()
        p.moveTo(0, 8)
        p.curveTo(self.width * 0.2, 24, self.width * 0.4, 2, self.width * 0.55, 14)
        p.curveTo(self.width * 0.7, 24, self.width * 0.85, 4, self.width, 16)
        p.lineTo(self.width, 0)
        p.lineTo(0, 0)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
        c.setFillAlpha(1.0)
        c.setStrokeColor(_WAVE)
        c.setStrokeAlpha(0.45)
        c.setLineWidth(0.8)
        c.line(0, 10, self.width, 18)
        c.setStrokeAlpha(1.0)


def _draw_invoice_chrome(canvas: pdfcanvas.Canvas, doc, *, status: str, page_num: int, page_count: int, company_name: str):
    """Subtle logo watermark + wave footer chrome on every page."""
    canvas.saveState()
    page_w, page_h = A4
    logo = _logo_path()
    if logo is not None:
        try:
            img = ImageReader(str(logo))
            size = 220
            canvas.setFillAlpha(0.04)
            canvas.drawImage(
                img,
                (page_w - size) / 2.0,
                (page_h - size) / 2.0 - 20,
                width=size,
                height=size,
                mask="auto",
                preserveAspectRatio=True,
            )
            canvas.setFillAlpha(1.0)
        except Exception:
            pass

    # Footer line + page number overlay (text also in flow; chrome keeps multi-page consistent)
    canvas.setStrokeColor(_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 18 * mm, page_w - doc.rightMargin, 18 * mm)
    canvas.setFillColor(_MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(
        doc.leftMargin,
        12 * mm,
        f"This is a computer-generated invoice issued by {company_name}.",
    )
    canvas.drawRightString(
        page_w - doc.rightMargin,
        12 * mm,
        f"Page {page_num} of {page_count}",
    )
    canvas.restoreState()


def generate_invoice_pdf(
    invoice,
    client,
    settings,
    items,
    project=None,
    parent_invoice=None,
) -> bytes:
    buffer = io.BytesIO()
    status = (getattr(invoice, "status", "") or "").lower()
    is_credit = getattr(invoice, "invoice_type", None) == "credit_note"
    comp_name = settings.company_name if settings else "KR Web Solution"
    page_count_holder = {"n": 1}

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=22 * mm,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=_SECONDARY,
    )
    muted = ParagraphStyle("InvMuted", parent=body, fontSize=8, leading=11, textColor=_MUTED)
    th = ParagraphStyle(
        "InvTH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )
    th_r = ParagraphStyle("InvTHR", parent=th, alignment=TA_RIGHT)
    th_c = ParagraphStyle("InvTHC", parent=th, alignment=TA_CENTER)
    td = ParagraphStyle(
        "InvTD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=_INK,
    )
    td_r = ParagraphStyle("InvTDR", parent=td, alignment=TA_RIGHT)
    td_c = ParagraphStyle("InvTDC", parent=td, alignment=TA_CENTER)

    elements = []
    content_w = A4[0] - doc.leftMargin - doc.rightMargin

    # ——— Header ———
    logo = _logo_path()
    left_bits = []
    if logo is not None:
        try:
            left_bits.append(Image(str(logo), width=1.15 * inch, height=0.78 * inch, kind="proportional"))
        except Exception:
            pass

    tagline = (
        getattr(settings, "company_tagline", None)
        or "Web Development | Cloud | Software Solutions"
    )
    comp_addr = (getattr(settings, "company_address", None) or "").strip()
    comp_email = (
        getattr(settings, "smtp_from_email", None)
        or getattr(settings, "company_email", None)
        or "billing@krwebsolution.com"
    )
    comp_phone = (getattr(settings, "company_phone", None) or "").strip()
    comp_website = (
        getattr(settings, "company_website", None) or "www.krwebsolution.com"
    ).replace("https://", "").replace("http://", "")
    gst_on = bool(settings and getattr(settings, "gst_enabled", False) and settings.company_gstin)

    company_lines = [
        f'<font size="13"><b>{comp_name}</b></font>',
        f'<font size="7.5" color="#737373">{tagline}</font>',
    ]
    if comp_addr:
        company_lines.append(f'<font size="7.5" color="#525252">{comp_addr.replace(chr(10), "<br/>")}</font>')
    if comp_email:
        company_lines.append(f'<font size="7.5" color="#525252">{comp_email}</font>')
    if comp_website:
        company_lines.append(f'<font size="7.5" color="#525252">{comp_website}</font>')
    if comp_phone:
        company_lines.append(f'<font size="7.5" color="#525252">{comp_phone}</font>')
    if gst_on:
        company_lines.append(
            f'<font size="7" color="#737373">GSTIN: <b><font color="#111111">{settings.company_gstin}</font></b></font>'
        )

    company_block = Paragraph("<br/>".join(company_lines), body)
    left_col = Table(
        [[left_bits[0], company_block]] if left_bits else [[company_block]],
        colWidths=[1.25 * inch, 2.6 * inch] if left_bits else [3.85 * inch],
    )
    left_col.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    if is_credit:
        doc_title = "CREDIT NOTE"
    elif getattr(invoice, "invoice_type", None) == "late_fee":
        doc_title = "LATE FEE INVOICE"
    elif gst_on or float(getattr(invoice, "tax_paise", 0) or 0) > 0:
        doc_title = "TAX INVOICE"
    else:
        doc_title = "INVOICE"

    number_label = "Credit Note #:" if is_credit else "Invoice #:"
    meta_lines = [
        f'<font size="22"><b>{doc_title}</b></font>',
        f'<font size="8.5" color="#525252">{number_label} <b><font color="#111111">{invoice.invoice_number}</font></b></font>',
        f'<font size="8.5" color="#525252">Issue Date: <font color="#111111">{invoice.issue_date.strftime("%d %b %Y")}</font></font>',
    ]
    if not is_credit:
        meta_lines.append(
            f'<font size="8.5" color="#525252">Due Date: <font color="#111111">{invoice.due_date.strftime("%d %b %Y")}</font></font>'
        )
    if parent_invoice:
        meta_lines.append(
            f'<font size="8.5" color="#525252">Ref Invoice: <font color="#111111">{parent_invoice.invoice_number}</font></font>'
        )
    if status == "paid" and getattr(invoice, "paid_at", None):
        meta_lines.append(
            f'<font size="8.5" color="#525252">Paid On: <font color="#111111">{invoice.paid_at.strftime("%d %b %Y")}</font></font>'
        )

    right_stack = [[Paragraph("<br/>".join(meta_lines), ParagraphStyle("MetaR", parent=body, alignment=TA_RIGHT))]]
    badge = _STATUS_BADGE.get(status)
    if badge:
        badge_flow = _StatusBadge(*badge)
        right_stack.append(
            [
                Table(
                    [[badge_flow]],
                    colWidths=[badge_flow.width],
                )
            ]
        )
        right_stack[-1][0].setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )

    right_col = Table(right_stack, colWidths=[3.15 * inch])
    right_col.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    header = Table([[left_col, right_col]], colWidths=[3.85 * inch, 3.15 * inch])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(header)
    elements.append(Spacer(1, 10))

    divider = Table([[""]], colWidths=[content_w])
    divider.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, -1), 1.0, _CHARCOAL),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(divider)
    elements.append(Spacer(1, 12))

    # ——— Bill To / Project ———
    c_name = client.name if client else "Valued Client"
    bill_bits = [f'<b><font size="11" color="#111111">{c_name}</font></b>']
    if client:
        attn_parts = []
        if getattr(client, "primary_contact_name", None):
            attn_parts.append(client.primary_contact_name)
        if getattr(client, "designation", None):
            attn_parts.append(client.designation)
        if attn_parts:
            bill_bits.append(" · ".join(attn_parts))
        if getattr(client, "contact_email", None) or getattr(client, "email", None):
            bill_bits.append(client.contact_email or client.email)
        if getattr(client, "contact_phone", None) or getattr(client, "phone", None):
            bill_bits.append(client.contact_phone or client.phone)
        if getattr(client, "address", None):
            bill_bits.append(client.address.replace("\n", "<br/>"))
        place = ", ".join(
            p for p in [getattr(client, "city", None), getattr(client, "state", None), getattr(client, "pincode", None)] if p
        )
        if place:
            bill_bits.append(place)
        bill_bits.append("India")
        if getattr(client, "gstin", None):
            bill_bits.append(f'GSTIN: <b>{client.gstin}</b>')

    bill_html = (
        '<b><font size="8" color="#111111">BILL TO</font></b><br/>'
        + "<br/>".join(f'<font size="8" color="#525252">{b}</font>' if not b.startswith("<b>") else b for b in bill_bits)
    )

    inv_type = getattr(invoice, "invoice_type", None)
    proj_rows = ['<b><font size="8" color="#111111">PROJECT DETAILS</font></b>']
    if project and getattr(project, "name", None):
        proj_rows.append(
            f'<font size="8" color="#525252">Project Name: <b><font color="#111111">{project.name}</font></b></font>'
        )
    proj_rows.append(
        f'<font size="8" color="#525252">Type: <font color="#111111">{_type_label(inv_type)}</font></font>'
    )
    if project and getattr(project, "id", None):
        # Human-friendly short ref from project id when no dedicated code exists
        short = str(project.id).replace("prj_", "PRJ-").upper()
        proj_rows.append(
            f'<font size="8" color="#525252">Project Ref.: <font color="#111111">{short}</font></font>'
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
        proj_rows.append(
            f'<font size="8" color="#525252">Period: <font color="#111111">{period}</font></font>'
        )
    ledger = getattr(invoice, "ledger", None)
    if ledger and ledger != "development":
        proj_rows.append(
            f'<font size="8" color="#525252">Billing Category: <font color="#111111">{str(ledger).capitalize()}</font></font>'
        )

    info = Table(
        [
            [
                Paragraph(bill_html, body),
                Paragraph("<br/>".join(proj_rows), body),
            ]
        ],
        colWidths=[content_w * 0.55, content_w * 0.45],
    )
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(info)
    elements.append(Spacer(1, 14))

    # ——— Line items ———
    table_data = [
        [
            Paragraph("#", th),
            Paragraph("Item &amp; Description", th),
            Paragraph("Qty", th_c),
            Paragraph("Rate (INR)", th_r),
            Paragraph("Amount (INR)", th_r),
        ]
    ]
    for idx, itm in enumerate(items, 1):
        desc = (itm.description or "").strip()
        parts = [p.strip() for p in desc.split("\n") if p.strip()]
        if len(parts) > 1:
            desc_html = f"<b>{parts[0]}</b><br/><font color='#737373' size='7.5'>{' '.join(parts[1:])}</font>"
        else:
            desc_html = f"<b>{desc}</b>"
        table_data.append(
            [
                Paragraph(str(idx), td),
                Paragraph(desc_html, td),
                Paragraph(f"{float(itm.quantity):g}", td_c),
                Paragraph(format_inr(itm.unit_rate_paise), td_r),
                Paragraph(format_inr(itm.amount_paise), td_r),
            ]
        )

    col_widths = [
        content_w * 0.06,
        content_w * 0.50,
        content_w * 0.10,
        content_w * 0.16,
        content_w * 0.18,
    ]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _CHARCOAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (1, -1), "LEFT"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT]),
            ]
        )
    )
    elements.append(items_table)
    elements.append(Spacer(1, 12))

    # ——— Bank + totals ———
    tax_rate = float(invoice.tax_rate_percent or 0)
    show_gst = float(invoice.tax_paise or 0) > 0 or tax_rate > 0
    if is_credit:
        total_label = "CREDIT TOTAL"
    elif status == "paid":
        total_label = "TOTAL"
    elif status in {"cancelled", "void"}:
        total_label = "TOTAL (NOT PAYABLE)"
    else:
        total_label = "TOTAL DUE"

    totals_rows = [
        [
            Paragraph("Subtotal", muted),
            Paragraph(format_inr(invoice.subtotal_paise), ParagraphStyle("TotR", parent=td_r, textColor=_INK)),
        ]
    ]
    if show_gst:
        totals_rows.append(
            [
                Paragraph(f"GST ({tax_rate:g}%)", muted),
                Paragraph(format_inr(invoice.tax_paise), ParagraphStyle("TotR2", parent=td_r, textColor=_INK)),
            ]
        )
    totals_rows.append(
        [
            Paragraph(f"<b><font color='white'>{total_label}</font></b>", td),
            Paragraph(
                f"<b><font color='white'>{format_inr(invoice.total_paise)}</font></b>",
                ParagraphStyle("TotFinal", parent=td_r, fontName="Helvetica-Bold", fontSize=11, textColor=colors.white),
            ),
        ]
    )
    from .tax_payroll import resolve_payment_upi

    pay = resolve_payment_upi(settings, invoice) if settings else {"upi_id": None, "upi_qr_url": None}
    bank_rows = ['<b><font size="8" color="#111111">BANK &amp; PAYMENT DETAILS</font></b>']
    if settings and not is_credit:
        mapping = [
            ("Beneficiary Name", getattr(settings, "bank_beneficiary_name", None)),
            ("Bank", getattr(settings, "bank_name", None)),
            ("Account Number", getattr(settings, "bank_account_number", None)),
            ("IFSC Code", getattr(settings, "bank_ifsc", None)),
            ("UPI ID", pay.get("upi_id") or getattr(settings, "bank_upi_id", None)),
        ]
        for label_txt, val in mapping:
            if val:
                bank_rows.append(
                    f'<font size="8" color="#525252">{label_txt}: <font color="#111111">{val}</font></font>'
                )
        if len(bank_rows) == 1 and getattr(settings, "bank_upi_details", None):
            bank_rows.append(
                f'<font size="8" color="#525252">{settings.bank_upi_details.replace(chr(10), "<br/>")}</font>'
            )
    elif is_credit:
        bank_rows = [
            '<font size="8" color="#525252">Credit adjustment against parent invoice'
            + (f" {parent_invoice.invoice_number}" if parent_invoice else "")
            + ". No payment QR is issued for credit notes.</font>"
        ]

    # Keep bank + totals as a simple 2-col table (no nested KeepTogether —
    # ReportLab returns a sentinel ~16Mpt height and LayoutError when that
    # pattern overflows the frame). Nested totals colWidths must fit tot_w.
    bank_w = content_w * 0.54
    tot_w = content_w * 0.40
    totals_table = Table(totals_rows, colWidths=[tot_w * 0.48, tot_w * 0.52])
    last = len(totals_rows) - 1
    totals_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, last - 1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, last - 1), 3),
                ("TOPPADDING", (0, last), (-1, last), 10),
                ("BOTTOMPADDING", (0, last), (-1, last), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("BACKGROUND", (0, last), (-1, last), _CHARCOAL),
            ]
        )
    )
    left_bank = Paragraph("<br/>".join(bank_rows), body)
    summary = Table(
        [[left_bank, totals_table]],
        colWidths=[bank_w, tot_w],
    )
    summary.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    elements.append(summary)

    qr_path = None if is_credit else _resolve_upi_qr_path(pay.get("upi_qr_url"))
    if qr_path is not None:
        try:
            elements.append(Spacer(1, 6))
            elements.append(Image(str(qr_path), width=0.95 * inch, height=0.95 * inch))
        except Exception:
            pass

    elements.append(Spacer(1, 14))

    # ——— Terms ———
    notes_text = invoice.notes or (settings.payment_terms if settings else "")
    term_lines = _parse_terms(notes_text) or _default_terms(status, inv_type)
    if term_lines:
        # Cap extremely long notes so a single Paragraph cannot explode layout.
        capped = term_lines[:12]
        terms_html = '<b><font size="8" color="#111111">TERMS &amp; NOTES</font></b><br/>' + "<br/>".join(
            f'<font size="8" color="#525252">{i + 1}. {line}</font>' for i, line in enumerate(capped)
        )
        elements.append(Paragraph(terms_html, body))
        elements.append(Spacer(1, 10))

    elements.append(_WaveFooter(min(content_w, 500)))

    def _on_page_live(canvas, doc_):
        n = canvas.getPageNumber()
        page_count_holder["n"] = max(page_count_holder["n"], n)
        _draw_invoice_chrome(
            canvas,
            doc_,
            status=status,
            page_num=n,
            page_count=page_count_holder["n"],
            company_name=comp_name,
        )

    doc.build(elements, onFirstPage=_on_page_live, onLaterPages=_on_page_live)
    buffer.seek(0)
    return buffer.getvalue()


def generate_payslip_pdf(
    *,
    employee_name: str,
    employee_email: str,
    job_title: Optional[str],
    department: Optional[str],
    month_key: str,
    gross_paise: int,
    deductions_paise: int,
    net_paise: int,
    note: Optional[str],
    company_name: str,
) -> bytes:
    """Simple earnings / deductions / net payslip (no PF/ESI/TDS)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=48,
        bottomMargin=48,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PayslipTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=_INK,
        alignment=TA_LEFT,
    )
    body = ParagraphStyle(
        "PayslipBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=_INK,
    )
    muted = ParagraphStyle(
        "PayslipMuted",
        parent=body,
        textColor=_MUTED,
        fontSize=9,
    )

    elements = [
        Paragraph(company_name or "KR Web Solution", title_style),
        Spacer(1, 6),
        Paragraph(f"<b>Payslip</b> — {month_key}", body),
        Spacer(1, 16),
        Paragraph(f"<b>{employee_name}</b>", body),
        Paragraph(employee_email, muted),
    ]
    if job_title or department:
        elements.append(
            Paragraph(
                f"{job_title or '—'} · {department or '—'}",
                muted,
            )
        )
    elements.append(Spacer(1, 20))

    rows = [
        [
            Paragraph("<b>Description</b>", body),
            Paragraph("<b>Amount</b>", body),
        ],
        [Paragraph("Gross earnings", body), Paragraph(format_inr(gross_paise), body)],
        [Paragraph("Deductions", body), Paragraph(format_inr(deductions_paise), body)],
        [Paragraph("<b>Net pay</b>", body), Paragraph(f"<b>{format_inr(net_paise)}</b>", body)],
    ]
    table = Table(rows, colWidths=[4.5 * inch, 2.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F5F5")),
                ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(table)
    if note:
        elements.append(Spacer(1, 16))
        elements.append(Paragraph(f"<b>Note:</b> {note}", muted))
    elements.append(Spacer(1, 28))
    elements.append(
        Paragraph(
            "This slip shows earnings, deductions, and net only. "
            "It does not calculate PF, ESI, or income tax.",
            muted,
        )
    )
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
