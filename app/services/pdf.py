import io
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Tuple

from reportlab.lib.pagesizes import A3, A4, A5, A6, letter, legal
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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_UPLOADS_BRAND = Path(__file__).resolve().parents[2] / "uploads" / "brand"
_LOGO_CANDIDATES = [
    _ASSETS / "krws-mark.png",
    _ASSETS / "kr-logo.png",
]

# Portrait page sizes in points (1 pt = 1/72 in). Keys are API query values.
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "a3": A3,  # 297 × 420 mm
    "a4": A4,  # 210 × 297 mm
    "a5": A5,  # 148 × 210 mm
    "a6": A6,  # 105 × 148 mm
    "letter": letter,  # 215.9 × 279.4 mm
    "legal": legal,  # 215.9 × 355.6 mm
    "tabloid": (11 * inch, 17 * inch),  # 279.4 × 431.8 mm
    "executive": (7.25 * inch, 10.5 * inch),  # 184.15 × 266.7 mm
    "b4": (250 * mm, 353 * mm),
    "b5": (176 * mm, 250 * mm),
}
DEFAULT_PAPER_SIZE = "a4"


def resolve_paper_size(
    name: Optional[str],
    orientation: Optional[str] = None,
) -> tuple[str, tuple[float, float]]:
    key = (name or DEFAULT_PAPER_SIZE).strip().lower().replace(" ", "_")
    aliases = {
        "us_letter": "letter",
        "us-letter": "letter",
        "us_legal": "legal",
        "us-legal": "legal",
        "ledger": "tabloid",
        "11x17": "tabloid",
    }
    key = aliases.get(key, key)
    if key not in PAPER_SIZES:
        key = DEFAULT_PAPER_SIZE
    pagesize = PAPER_SIZES[key]
    orient = (orientation or "portrait").strip().lower().replace(" ", "_")
    if orient in {"landscape", "l", "horizontal"}:
        pagesize = (pagesize[1], pagesize[0])
    return key, pagesize


def _scale_for_page(page_w: float) -> float:
    """Typography/spacing scale relative to A4 width, clamped for readability."""
    return max(0.62, min(1.35, page_w / A4[0]))

_INK = colors.HexColor("#0F172A")
_SECONDARY = colors.HexColor("#334155")
_MUTED = colors.HexColor("#64748B")
_BORDER = colors.HexColor("#E2E8F0")
_PRIMARY = colors.HexColor("#172554")
_ACCENT = colors.HexColor("#F5B942")
_CHARCOAL = colors.HexColor("#172554")
_ROW_ALT = colors.HexColor("#F8FAFC")
_STATUS_BADGE = {
    "draft": ("DRAFT", "#94A3B8", "#FFFFFF"),
    "proof_pending": ("PROOF PENDING", "#F5B942", "#172554"),
    "paid": ("PAID", "#16A34A", "#FFFFFF"),
    "partially_paid": ("PARTIALLY PAID", "#F97316", "#FFFFFF"),
    "unpaid": ("UNPAID", "#DC2626", "#FFFFFF"),
    "overdue": ("OVERDUE", "#DC2626", "#FFFFFF"),
    "cancelled": ("CANCELLED", "#64748B", "#FFFFFF"),
    "void": ("VOID", "#64748B", "#FFFFFF"),
    "sent": ("UNPAID", "#DC2626", "#FFFFFF"),
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
    "debit_note": "Debit Note",
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


_FONT_REG: Optional[Tuple[str, str]] = None


def _invoice_fonts() -> Tuple[str, str]:
    """Register Arial (matches PaperInvoice) so ₹ renders; fall back to Helvetica."""
    global _FONT_REG
    if _FONT_REG is not None:
        return _FONT_REG
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        (Path(windir) / "Fonts" / "arial.ttf", Path(windir) / "Fonts" / "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
         Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")),
        (Path(windir) / "Fonts" / "Nirmala.ttf", Path(windir) / "Fonts" / "NirmalaB.ttf"),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            try:
                pdfmetrics.registerFont(TTFont("InvSans", str(regular)))
                pdfmetrics.registerFont(TTFont("InvSans-Bold", str(bold)))
                _FONT_REG = ("InvSans", "InvSans-Bold")
                return _FONT_REG
            except Exception:
                continue
    _FONT_REG = ("Helvetica", "Helvetica-Bold")
    return _FONT_REG


def format_inr(paise: int) -> str:
    """Indian currency formatting for PDF (₹ + Indian grouping, matches UI)."""
    sign = "-" if paise < 0 else ""
    rupees = abs(paise) // 100
    paise_part = abs(paise) % 100
    font_reg, _ = _invoice_fonts()
    symbol = "₹" if font_reg != "Helvetica" else "Rs. "
    if symbol == "₹":
        return f"{sign}{symbol}{_indian_group(rupees)}.{paise_part:02d}"
    return f"{sign}{symbol}{_indian_group(rupees)}.{paise_part:02d}"


def _logo_path(logo_url: Optional[str] = None) -> Optional[Path]:
    """Prefer immutable uploaded logo from snapshot/settings URL; else bundled asset."""
    if logo_url:
        match = re.search(r"logo-file/([^/?#]+)", logo_url)
        if match:
            safe = Path(match.group(1)).name
            if safe.startswith("logo-"):
                path = _UPLOADS_BRAND / safe
                if path.is_file():
                    return path
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
    if invoice_type == "debit_note":
        return [
            "This debit note records an additional charge against the referenced invoice.",
            "Payment is due as per the terms stated on this document.",
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


def _draw_invoice_chrome(
    canvas: pdfcanvas.Canvas,
    doc,
    *,
    status: str,
    page_num: int,
    page_count: int,
    company_name: str,
    doc_title: str,
    invoice_number: str,
    pagesize: tuple[float, float],
    scale: float,
    logo_url: Optional[str] = None,
):
    """Watermark + bottom page footer text (no wave decoration)."""
    del status  # kept for call-site compatibility
    canvas.saveState()
    page_w, page_h = pagesize
    # PDF document title (viewer tab / properties) — set once on first page
    if page_num == 1:
        canvas.setTitle(f"{doc_title} {invoice_number}".strip())
        canvas.setAuthor(company_name)
        canvas.setSubject(f"{doc_title} {invoice_number}")

    logo = _logo_path(logo_url)
    if logo is not None:
        try:
            img = ImageReader(str(logo))
            size = 200 * scale
            canvas.setFillAlpha(0.025)
            canvas.drawImage(
                img,
                (page_w - size) / 2.0,
                (page_h - size) / 2.0 - (20 * scale),
                width=size,
                height=size,
                mask="auto",
                preserveAspectRatio=True,
            )
            canvas.setFillAlpha(1.0)
        except Exception:
            pass

    # Footer text pinned to the physical bottom of the page
    footer_y = max(8 * mm, 10 * mm * scale)
    line_y = footer_y + (5 * mm * scale)
    canvas.setStrokeColor(_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, line_y, page_w - doc.rightMargin, line_y)
    canvas.setFillColor(_MUTED)
    canvas.setFont("Helvetica", max(6, 7 * scale))
    canvas.drawString(
        doc.leftMargin,
        footer_y,
        f"This is a computer-generated invoice issued by {company_name}.",
    )
    canvas.drawRightString(
        page_w - doc.rightMargin,
        footer_y,
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
    paper_size: Optional[str] = None,
    orientation: Optional[str] = None,
) -> bytes:
    from .invoice_snapshot import (
        client_view_from_snapshot,
        get_snapshot,
        payment_details_are_locked,
        settings_view_from_snapshot,
    )

    snap = get_snapshot(invoice)
    status = (getattr(invoice, "status", "") or "").lower()
    inv_type_raw = (getattr(invoice, "invoice_type", None) or "").lower()
    is_credit = inv_type_raw == "credit_note"
    is_debit = inv_type_raw == "debit_note"
    frozen_terms = None
    if snap:
        prefer_live = not payment_details_are_locked(invoice)
        # Unpaid → live company/client/bank; paid/cancelled → frozen snapshot.
        settings = settings_view_from_snapshot(
            snap,
            settings,
            prefer_live_payment=prefer_live,
        )
        client = client_view_from_snapshot(snap, client, prefer_live=prefer_live)
        # Terms freeze only with paid/cancel/void — unpaid always resolves live terms.
        if (
            not prefer_live
            and isinstance(snap.get("terms"), list)
            and snap["terms"]
        ):
            frozen_terms = [str(t) for t in snap["terms"] if str(t).strip()]
        if prefer_live and isinstance(snap.get("project"), dict) and project is None:
            # Unpaid with live project relationship preferred; keep snap only as fallback.
            pass
        if isinstance(snap.get("project"), dict) and project is None:
            project = SimpleNamespace(
                name=snap["project"].get("name"),
                id=snap["project"].get("ref") or "prj_snap",
            )
        if isinstance(snap.get("parent_invoice_number"), str) and parent_invoice is None:
            parent_invoice = SimpleNamespace(invoice_number=snap["parent_invoice_number"])

    buffer = io.BytesIO()
    font_reg, font_bold = _invoice_fonts()
    comp_name = (getattr(settings, "company_name", None) if settings else None) or "KR Web Solution"
    page_count_holder = {"n": 1}

    _paper_key, pagesize = resolve_paper_size(paper_size, orientation)
    page_w, page_h = pagesize
    scale = _scale_for_page(page_w)

    gst_preview = bool(
        settings and getattr(settings, "gst_enabled", False) and settings.company_gstin
    )
    if is_credit:
        doc_title_early = "CREDIT NOTE"
    elif getattr(invoice, "invoice_type", None) == "late_fee":
        doc_title_early = "LATE FEE INVOICE"
    elif gst_preview or float(getattr(invoice, "tax_paise", 0) or 0) > 0:
        doc_title_early = "TAX INVOICE"
    else:
        doc_title_early = "INVOICE"
    pdf_meta_title = f"{doc_title_early} {invoice.invoice_number or ''}".strip()

    # Margins scale with page; keep printable area comfortable on small sheets.
    side_m = max(8 * mm, min(18 * mm, 14 * mm * scale))
    top_m = max(8 * mm, min(16 * mm, 12 * mm * scale))
    bottom_m = max(14 * mm, min(22 * mm, 18 * mm * scale))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=side_m,
        leftMargin=side_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title=pdf_meta_title,
        author=comp_name,
        subject=pdf_meta_title,
    )

    def fs(base: float, lo: float = 6.0, hi: float = 28.0) -> float:
        return max(lo, min(hi, base * scale))

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvBody",
        parent=styles["Normal"],
        fontName=font_reg,
        fontSize=fs(9),
        leading=fs(12),
        textColor=_SECONDARY,
    )
    muted = ParagraphStyle(
        "InvMuted",
        parent=body,
        fontSize=fs(8),
        leading=fs(11),
        textColor=_MUTED,
    )
    th = ParagraphStyle(
        "InvTH",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=fs(8),
        leading=fs(10),
        textColor=colors.white,
    )
    th_r = ParagraphStyle("InvTHR", parent=th, alignment=TA_RIGHT)
    th_c = ParagraphStyle("InvTHC", parent=th, alignment=TA_CENTER)
    td = ParagraphStyle(
        "InvTD",
        parent=styles["Normal"],
        fontName=font_reg,
        fontSize=fs(8.5),
        leading=fs(11),
        textColor=_INK,
    )
    td_r = ParagraphStyle("InvTDR", parent=td, alignment=TA_RIGHT)
    td_c = ParagraphStyle("InvTDC", parent=td, alignment=TA_CENTER)
    # Keep title leading tight so "TAX INVOICE" / "INVOICE" never clips in the header cell.
    title_style = ParagraphStyle(
        "InvDocTitle",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=fs(20, lo=12, hi=28),
        leading=fs(24, lo=14, hi=32),
        textColor=_INK,
        alignment=TA_RIGHT,
        spaceBefore=0,
        spaceAfter=2,
    )

    elements = []
    content_w = page_w - doc.leftMargin - doc.rightMargin
    left_w = content_w * 0.55
    right_w = content_w * 0.45

    # ——— Header (match PaperInvoice: logo + name/tagline row, then address below) ———
    logo = _logo_path(getattr(settings, "company_logo_url", None) if settings else None)
    # UI uses ~48px logo height / max ~72px width
    logo_max_h = max(28.0, min(48.0 * scale, 56.0))
    logo_max_w = max(36.0, min(72.0 * scale, 84.0))
    logo_img = None
    if logo is not None:
        try:
            reader = ImageReader(str(logo))
            iw, ih = reader.getSize()
            if iw > 0 and ih > 0:
                fit = min(logo_max_w / float(iw), logo_max_h / float(ih))
                logo_w = float(iw) * fit
                logo_h = float(ih) * fit
            else:
                logo_w, logo_h = logo_max_w * 0.75, logo_max_h
            logo_img = Image(str(logo), width=logo_w, height=logo_h)
        except Exception:
            logo_img = None

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
    settings_gst = bool(settings and getattr(settings, "gst_enabled", False))
    inv_gst_override = getattr(invoice, "gst_enabled", None)
    gst_on = settings_gst if inv_gst_override is None else bool(inv_gst_override)
    gst_unreg = bool(settings and getattr(settings, "gst_unregistered", False)) and gst_on
    company_gstin = (getattr(settings, "company_gstin", None) or "").strip() if settings else ""
    company_pan = (getattr(settings, "company_pan", None) or "").strip() if settings else ""

    brand_lines = [
        f'<font size="{fs(13):.1f}" color="#172554"><b>{comp_name}</b></font>',
        f'<font size="{fs(7.5):.1f}" color="#64748B">{tagline}</font>',
    ]
    brand_block = Paragraph("<br/>".join(brand_lines), body)

    addr_lines: list[str] = []
    if comp_addr:
        addr_lines.append(
            f'<font size="{fs(7.5):.1f}" color="#334155">{comp_addr.replace(chr(10), "<br/>")}</font>'
        )
    if comp_email:
        addr_lines.append(f'<font size="{fs(7.5):.1f}" color="#334155">{comp_email}</font>')
    if comp_website:
        addr_lines.append(f'<font size="{fs(7.5):.1f}" color="#334155">{comp_website}</font>')
    if comp_phone:
        addr_lines.append(f'<font size="{fs(7.5):.1f}" color="#334155">{comp_phone}</font>')
    if gst_on and company_gstin:
        addr_lines.append(
            f'<font size="{fs(7):.1f}" color="#64748B">GSTIN: <b><font color="#172554">{company_gstin}</font></b></font>'
        )
    elif gst_on and gst_unreg:
        addr_lines.append(
            f'<font size="{fs(7):.1f}" color="#64748B">GSTIN: <b><font color="#172554">N/A</font></b></font>'
        )
    if gst_on and company_pan:
        addr_lines.append(
            f'<font size="{fs(7):.1f}" color="#64748B">PAN: <b><font color="#172554">{company_pan}</font></b></font>'
        )
    addr_block = Paragraph("<br/>".join(addr_lines), body) if addr_lines else None

    if logo_img is not None:
        brand_gap = 10 * scale
        logo_col = max(logo_w, 24) + 2
        text_col = max(40, left_w - logo_col - brand_gap)
        brand_row = Table(
            [[logo_img, brand_block]],
            colWidths=[logo_col, text_col],
        )
        brand_row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, 0), 0),
                    ("RIGHTPADDING", (0, 0), (0, 0), brand_gap),
                    ("LEFTPADDING", (1, 0), (1, 0), 2),
                    ("RIGHTPADDING", (1, 0), (1, 0), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
    else:
        brand_row = brand_block

    left_stack_rows: list = [[brand_row]]
    left_stack_style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]
    if addr_block is not None:
        left_stack_rows.append([addr_block])
        left_stack_style.append(("TOPPADDING", (0, 1), (-1, 1), 8 * scale))
    left_col = Table(left_stack_rows, colWidths=[left_w])
    left_col.setStyle(TableStyle(left_stack_style))

    inv_type_key = inv_type_raw
    if is_credit or inv_type_key == "credit_note":
        doc_title = "CREDIT NOTE"
    elif is_debit or inv_type_key == "debit_note":
        doc_title = "DEBIT NOTE"
    elif inv_type_key == "proforma":
        doc_title = "PROFORMA INVOICE"
    elif inv_type_key == "late_fee":
        doc_title = "LATE FEE INVOICE"
    elif status == "paid":
        doc_title = "PAYMENT RECEIPT"
    elif gst_on and (company_gstin or float(getattr(invoice, "tax_paise", 0) or 0) > 0):
        doc_title = "TAX INVOICE"
    elif not gst_on:
        doc_title = "BILL OF SUPPLY"
    else:
        doc_title = "INVOICE"

    if is_credit:
        number_label = "Credit Note #:"
    elif is_debit:
        number_label = "Debit Note #:"
    else:
        number_label = "Invoice #:"
    meta_bits = [
        f'<font size="{fs(8.5):.1f}" color="#64748B">{number_label} <b><font color="#172554">{invoice.invoice_number}</font></b></font>',
        f'<font size="{fs(8.5):.1f}" color="#64748B">Issue Date: <font color="#0F172A">{invoice.issue_date.strftime("%d %b %Y")}</font></font>',
    ]
    if not is_credit:
        meta_bits.append(
            f'<font size="{fs(8.5):.1f}" color="#64748B">Due Date: <font color="#0F172A">{invoice.due_date.strftime("%d %b %Y")}</font></font>'
        )
    po_number = (getattr(invoice, "po_number", None) or "").strip()
    if po_number:
        meta_bits.append(
            f'<font size="{fs(8.5):.1f}" color="#64748B">PO Number: <font color="#0F172A">{po_number}</font></font>'
        )
    if parent_invoice:
        meta_bits.append(
            f'<font size="{fs(8.5):.1f}" color="#64748B">Ref Invoice: <font color="#0F172A">{parent_invoice.invoice_number}</font></font>'
        )
        parent_issue = getattr(parent_invoice, "issue_date", None)
        if parent_issue is not None:
            try:
                parent_issue_txt = parent_issue.strftime("%d %b %Y")
            except Exception:
                parent_issue_txt = str(parent_issue)
            meta_bits.append(
                f'<font size="{fs(8.5):.1f}" color="#64748B">Ref Inv Date: <font color="#0F172A">{parent_issue_txt}</font></font>'
            )
    if status == "paid" and getattr(invoice, "paid_at", None) and not is_credit:
        meta_bits.append(
            f'<font size="{fs(8.5):.1f}" color="#64748B">Paid On: <font color="#0F172A">{invoice.paid_at.strftime("%d %b %Y")}</font></font>'
        )

    right_stack = [
        [Paragraph(doc_title, title_style)],
        [
            Paragraph(
                "<br/>".join(meta_bits),
                ParagraphStyle("MetaR", parent=body, alignment=TA_RIGHT),
            )
        ],
    ]
    amount_paid = int(getattr(invoice, "amount_paid_paise", 0) or 0)
    total_amt = int(getattr(invoice, "total_paise", 0) or 0)
    badge_key = status
    if status == "sent":
        badge_key = "unpaid"
    elif status not in ("paid", "cancelled", "void", "overdue", "draft", "proof_pending") and 0 < amount_paid < total_amt:
        badge_key = "partially_paid"
    elif 0 < amount_paid < total_amt and status == "sent":
        badge_key = "partially_paid"
    badge = _STATUS_BADGE.get(badge_key) or _STATUS_BADGE.get(status)
    if badge:
        badge_flow = _StatusBadge(*badge)
        badge_flow.width = max(badge_flow.width * scale, badge_flow.width * 0.85)
        badge_tbl = Table([[badge_flow]], colWidths=[badge_flow.width])
        badge_tbl.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        right_stack.append([badge_tbl])

    right_col = Table(right_stack, colWidths=[right_w])
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

    header = Table([[left_col, right_col]], colWidths=[left_w, right_w])
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
    elements.append(Spacer(1, 10 * scale))

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
    elements.append(Spacer(1, 12 * scale))

    # ——— Bill To / Project ———
    # Company / billing party only — contact person is emailed on CC, not printed.
    c_name = client.name if client else "Valued Client"
    bill_bits = [
        f'<b><font size="{fs(11):.1f}" color="#111111">{c_name}</font></b>'
    ]
    if client:
        if getattr(client, "email", None):
            bill_bits.append(client.email)
        if getattr(client, "phone", None):
            bill_bits.append(client.phone)
        if getattr(client, "address", None):
            bill_bits.append(client.address.replace("\n", "<br/>"))
        place = ", ".join(
            p for p in [getattr(client, "city", None), getattr(client, "state", None), getattr(client, "pincode", None)] if p
        )
        if place:
            bill_bits.append(place)
        if getattr(client, "gstin", None):
            bill_bits.append(f'GSTIN: <b>{client.gstin}</b>')
        if getattr(client, "pan", None):
            bill_bits.append(f'PAN: <b>{client.pan}</b>')
        if getattr(client, "state_code", None):
            bill_bits.append(f'State Code: <b>{client.state_code}</b>')
        pos = (getattr(invoice, "place_of_supply", None) or "").strip()
        if pos:
            bill_bits.append(f'Place of Supply: <b>{pos}</b>')

    bill_html = (
        f'<b><font size="{fs(8):.1f}" color="#111111">BILL TO</font></b><br/>'
        + "<br/>".join(
            f'<font size="{fs(8):.1f}" color="#525252">{b}</font>'
            if not b.startswith("<b>")
            else b
            for b in bill_bits
        )
    )

    inv_type = getattr(invoice, "invoice_type", None)
    proj_rows = [f'<b><font size="{fs(8):.1f}" color="#111111">PROJECT DETAILS</font></b>']
    if project and getattr(project, "name", None):
        proj_rows.append(
            f'<font size="{fs(8):.1f}" color="#525252">Project Name: <b><font color="#111111">{project.name}</font></b></font>'
        )
    proj_rows.append(
        f'<font size="{fs(8):.1f}" color="#525252">Type: <font color="#111111">{_type_label(inv_type)}</font></font>'
    )
    if project and getattr(project, "id", None):
        # Human-friendly short ref from project id when no dedicated code exists
        short = str(project.id).replace("prj_", "PRJ-").upper()
        proj_rows.append(
            f'<font size="{fs(8):.1f}" color="#525252">Project Ref.: <font color="#111111">{short}</font></font>'
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
            f'<font size="{fs(8):.1f}" color="#525252">Period: <font color="#111111">{period}</font></font>'
        )
    ledger = getattr(invoice, "ledger", None)
    if ledger and ledger != "development":
        proj_rows.append(
            f'<font size="{fs(8):.1f}" color="#525252">Billing Category: <font color="#111111">{str(ledger).capitalize()}</font></font>'
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
                ("RIGHTPADDING", (0, 0), (-1, -1), 8 * scale),
            ]
        )
    )
    elements.append(info)
    elements.append(Spacer(1, 14 * scale))

    # ——— Line items ———
    # HSN/SAC only when GST is enabled in settings / snapshot.
    show_hsn = gst_on and any(
        (getattr(itm, "hsn_sac", None) or "").strip() for itm in items
    )
    show_unit = any((getattr(itm, "unit", None) or "").strip() for itm in items)
    header_cells = [
        Paragraph("#", th),
        Paragraph("Item &amp; Description", th),
    ]
    if show_hsn:
        header_cells.append(Paragraph("HSN/SAC", th_c))
    header_cells.append(Paragraph("Qty", th_c))
    if show_unit:
        header_cells.append(Paragraph("Unit", th_c))
    header_cells.extend(
        [
            Paragraph("Rate (INR)", th_r),
            Paragraph("Amount (INR)", th_r),
        ]
    )
    table_data = [header_cells]
    for idx, itm in enumerate(items, 1):
        desc = (itm.description or "").strip()
        parts = [p.strip() for p in desc.split("\n") if p.strip()]
        if len(parts) > 1:
            desc_html = f"<b>{parts[0]}</b><br/><font color='#64748B' size='7.5'>{' '.join(parts[1:])}</font>"
        else:
            desc_html = f"<b>{desc}</b>"
        row = [
            Paragraph(str(idx), td),
            Paragraph(desc_html, td),
        ]
        if show_hsn:
            hsn_val = (getattr(itm, "hsn_sac", None) or "—").strip() or "—"
            row.append(Paragraph(f"<b>{hsn_val}</b>", td_c))
        qty = float(itm.quantity) if itm.quantity is not None else 0
        row.append(Paragraph(f"{qty:g}", td_c))
        if show_unit:
            unit_val = (getattr(itm, "unit", None) or "").strip()
            if not unit_val and qty:
                unit_val = "Nos"
            row.append(Paragraph(unit_val or "—", td_c))
        row.extend(
            [
                Paragraph(format_inr(itm.unit_rate_paise), td_r),
                Paragraph(format_inr(itm.amount_paise), td_r),
            ]
        )
        table_data.append(row)

    # Dynamic column widths based on optional HSN/Unit columns
    if show_hsn and show_unit:
        col_widths = [
            content_w * 0.05,
            content_w * 0.36,
            content_w * 0.12,
            content_w * 0.08,
            content_w * 0.08,
            content_w * 0.15,
            content_w * 0.16,
        ]
    elif show_hsn:
        col_widths = [
            content_w * 0.05,
            content_w * 0.42,
            content_w * 0.12,
            content_w * 0.09,
            content_w * 0.15,
            content_w * 0.17,
        ]
    elif show_unit:
        col_widths = [
            content_w * 0.05,
            content_w * 0.44,
            content_w * 0.09,
            content_w * 0.09,
            content_w * 0.16,
            content_w * 0.17,
        ]
    else:
        col_widths = [
            content_w * 0.06,
            content_w * 0.50,
            content_w * 0.10,
            content_w * 0.16,
            content_w * 0.18,
        ]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    # Center qty/hsn/unit columns dynamically
    center_from = 2
    center_to = 1 + (1 if show_hsn else 0) + 1 + (1 if show_unit else 0)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _CHARCOAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (1, -1), "LEFT"),
                ("ALIGN", (center_from, 0), (center_to, -1), "CENTER"),
                ("ALIGN", (center_to + 1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), max(4, 7 * scale)),
                ("BOTTOMPADDING", (0, 0), (-1, -1), max(4, 7 * scale)),
                ("LEFTPADDING", (0, 0), (-1, -1), max(3, 6 * scale)),
                ("RIGHTPADDING", (0, 0), (-1, -1), max(3, 6 * scale)),
                ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT]),
            ]
        )
    )
    elements.append(items_table)
    elements.append(Spacer(1, 8 * scale))

    # ——— Bank + totals ———
    tax_rate = float(invoice.tax_rate_percent or 0)
    show_gst = gst_on and (float(invoice.tax_paise or 0) > 0 or tax_rate > 0)
    if is_credit:
        total_label = "CREDIT TOTAL"
    elif is_debit:
        total_label = "DEBIT TOTAL"
    elif status == "paid":
        total_label = "TOTAL"
    elif status in {"cancelled", "void"}:
        total_label = "TOTAL (NOT PAYABLE)"
    else:
        total_label = "TOTAL DUE"

    discount_paise = int(getattr(invoice, "discount_paise", 0) or 0)
    cgst_paise = int(getattr(invoice, "cgst_paise", 0) or 0)
    sgst_paise = int(getattr(invoice, "sgst_paise", 0) or 0)
    igst_paise = int(getattr(invoice, "igst_paise", 0) or 0)
    round_off = int(getattr(invoice, "round_off_paise", 0) or 0)
    amount_paid_paise = int(getattr(invoice, "amount_paid_paise", 0) or 0)
    if status == "paid" and amount_paid_paise <= 0:
        amount_paid_paise = int(invoice.total_paise or 0)
    balance_due = max(0, int(invoice.total_paise or 0) - amount_paid_paise)

    totals_rows = [
        [
            Paragraph("Subtotal", muted),
            Paragraph(format_inr(invoice.subtotal_paise), ParagraphStyle("TotR", parent=td_r, textColor=_INK)),
        ]
    ]
    if discount_paise > 0:
        totals_rows.append(
            [
                Paragraph("Discount", muted),
                Paragraph(f"-{format_inr(discount_paise)}", ParagraphStyle("TotDisc", parent=td_r, textColor=_INK)),
            ]
        )
    if show_gst:
        if cgst_paise or sgst_paise or igst_paise:
            half_rate = tax_rate / 2.0 if tax_rate else 0
            if cgst_paise:
                totals_rows.append(
                    [
                        Paragraph(f"CGST ({half_rate:g}%)", muted),
                        Paragraph(format_inr(cgst_paise), ParagraphStyle("TotCG", parent=td_r, textColor=_INK)),
                    ]
                )
            if sgst_paise:
                totals_rows.append(
                    [
                        Paragraph(f"SGST ({half_rate:g}%)", muted),
                        Paragraph(format_inr(sgst_paise), ParagraphStyle("TotSG", parent=td_r, textColor=_INK)),
                    ]
                )
            if igst_paise:
                totals_rows.append(
                    [
                        Paragraph(f"IGST ({tax_rate:g}%)", muted),
                        Paragraph(format_inr(igst_paise), ParagraphStyle("TotIG", parent=td_r, textColor=_INK)),
                    ]
                )
        else:
            totals_rows.append(
                [
                    Paragraph(f"GST ({tax_rate:g}%)", muted),
                    Paragraph(format_inr(invoice.tax_paise), ParagraphStyle("TotR2", parent=td_r, textColor=_INK)),
                ]
            )
    if round_off:
        totals_rows.append(
            [
                Paragraph("Round Off", muted),
                Paragraph(format_inr(round_off), ParagraphStyle("TotRO", parent=td_r, textColor=_INK)),
            ]
        )
    after_total_rows: list = []
    if not is_credit and (amount_paid_paise > 0 or status == "paid"):
        after_total_rows.append(
            [
                Paragraph("Amount Paid", muted),
                Paragraph(format_inr(amount_paid_paise), ParagraphStyle("TotPaid", parent=td_r, textColor=_INK)),
            ]
        )
        after_total_rows.append(
            [
                Paragraph("Balance Due", muted),
                Paragraph(format_inr(balance_due), ParagraphStyle("TotBal", parent=td_r, textColor=_INK)),
            ]
        )
    from .tax_payroll import resolve_payment_upi
    from .amount_in_words import amount_in_words_inr
    from .invoice_terms import resolve_invoice_terms
    from .payment_instructions import resolve_payment_instructions

    pay = resolve_payment_upi(settings, invoice) if settings else {"upi_id": None, "upi_qr_url": None}
    instr = resolve_payment_instructions(settings, upi_id=pay.get("upi_id"))
    show_payment_instructions = (
        settings
        and not is_credit
        and status not in ("cancelled", "void")
        and balance_due > 0
        and instr.get("has_any")
    )
    bank_rows: list[str] = []
    if show_payment_instructions:
        bank_rows.append(
            f'<b><font size="{fs(8):.1f}" color="#172554">PAYMENT DETAILS</font></b>'
        )
        mapping = [
            ("Beneficiary Name", instr.get("beneficiary")),
            ("Bank Name", instr.get("bank_name")),
            ("Account Number", instr.get("account_number")),
            ("IFSC Code", instr.get("ifsc")),
            ("UPI ID", instr.get("upi_id")),
            ("PayPal", instr.get("paypal_id")),
        ]
        for label_txt, val in mapping:
            if val:
                bank_rows.append(
                    f'<font size="{fs(8):.1f}" color="#334155">{label_txt}: <font color="#0F172A"><b>{val}</b></font></font>'
                )
        for line in instr.get("wire_instructions") or []:
            bank_rows.append(f'<font size="{fs(7.5):.1f}" color="#64748B">• {line}</font>')
    elif is_credit:
        # Match PaperInvoice: short note on the left; formal terms stay below.
        bank_rows = [
            f'<font size="{fs(8):.1f}" color="#334155">Credit adjustment against parent invoice'
            + (f" {parent_invoice.invoice_number}" if parent_invoice else "")
            + ". No payment is due on this credit note.</font>"
        ]
    elif status == "paid" or balance_due <= 0:
        bank_rows = [
            f'<b><font size="{fs(8):.1f}" color="#172554">PAYMENT CONFIRMATION</font></b>',
        ]
        if getattr(invoice, "paid_at", None):
            bank_rows.append(
                f'<font size="{fs(8):.1f}" color="#334155">Payment Date: <font color="#0F172A">{invoice.paid_at.strftime("%d %b %Y")}</font></font>'
            )
        bank_rows.append(
            f'<font size="{fs(8):.1f}" color="#334155">Amount Received: <font color="#0F172A">{format_inr(amount_paid_paise)}</font></font>'
        )
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
            if ref:
                bits.append(f"UTR/Ref: {ref}")
            bank_rows.append(
                f'<font size="{fs(7.5):.1f}" color="#334155">{" · ".join(bits)}</font>'
            )

    # Match UI 50/50 layout: left note/bank, right totals + navy total bar.
    bank_w = content_w * 0.50
    tot_w = content_w * 0.48
    label_w = tot_w * 0.48
    value_w = tot_w * 0.52

    line_items_table = Table(totals_rows, colWidths=[label_w, value_w])
    line_items_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    total_bar = Table(
        [
            [
                Paragraph(
                    f"<b><font color='white' size='{fs(10):.1f}'>{total_label}</font></b>",
                    ParagraphStyle("TotLbl", parent=td, fontName=font_bold, textColor=colors.white),
                ),
                Paragraph(
                    f"<b><font color='white' size='{fs(11):.1f}'>{format_inr(invoice.total_paise)}</font></b>",
                    ParagraphStyle(
                        "TotFinal",
                        parent=td_r,
                        fontName=font_bold,
                        fontSize=fs(11),
                        textColor=colors.white,
                    ),
                ),
            ]
        ],
        colWidths=[label_w, value_w],
    )
    total_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _CHARCOAL),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    right_stack_bits: list = [[line_items_table], [total_bar]]
    stack_style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
    ]
    if after_total_rows:
        after_tbl = Table(after_total_rows, colWidths=[label_w, value_w])
        after_tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        right_stack_bits.append([after_tbl])
        stack_style.append(("TOPPADDING", (0, 2), (-1, 2), 4))
    totals_stack = Table(right_stack_bits, colWidths=[tot_w])
    totals_stack.setStyle(TableStyle(stack_style))
    if bank_rows:
        left_bank = Paragraph("<br/>".join(bank_rows), body)
        summary = Table(
            [[left_bank, totals_stack]],
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
    else:
        wrap = Table([[totals_stack]], colWidths=[content_w])
        wrap.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(wrap)

    qr_path = (
        _resolve_upi_qr_path(pay.get("upi_qr_url")) if show_payment_instructions else None
    )
    if qr_path is not None and bank_rows:
        try:
            qr_size = max(0.7 * inch, min(1.1 * inch, 0.95 * inch * scale))
            elements.append(Spacer(1, 6 * scale))
            elements.append(Image(str(qr_path), width=qr_size, height=qr_size))
        except Exception:
            pass

    elements.append(Spacer(1, 8 * scale))
    elements.append(
        Paragraph(
            f'<font size="{fs(8):.1f}" color="#172554"><b>Amount in words:</b></font> '
            f'<font size="{fs(8):.1f}" color="#334155">{amount_in_words_inr(invoice.total_paise)}</font>',
            body,
        )
    )

    elements.append(Spacer(1, 10 * scale))

    # ——— Terms ———
    term_lines = frozen_terms or resolve_invoice_terms(
        invoice_type=inv_type,
        invoice_notes=getattr(invoice, "notes", None),
        settings=settings,
        status=status,
    )
    if term_lines:
        capped = term_lines[:12]
        terms_html = (
            f'<b><font size="{fs(8):.1f}" color="#172554">TERMS &amp; CONDITIONS</font></b><br/>'
            + "<br/>".join(
                f'<font size="{fs(8):.1f}" color="#334155">{i + 1}. {line}</font>'
                for i, line in enumerate(capped)
            )
        )
        elements.append(Paragraph(terms_html, body))

    logo_url_frozen = getattr(settings, "company_logo_url", None) if settings else None

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
            doc_title=doc_title,
            invoice_number=str(invoice.invoice_number or ""),
            pagesize=pagesize,
            scale=scale,
            logo_url=logo_url_frozen,
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
