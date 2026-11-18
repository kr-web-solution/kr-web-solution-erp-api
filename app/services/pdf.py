import io
from datetime import date
from typing import Optional
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

def format_inr(paise: int) -> str:
    rupees = paise / 100.0
    return f"Rs. {rupees:,.2f}"

def generate_invoice_pdf(
    invoice,
    client,
    settings,
    items,
    project=None,
    parent_invoice=None
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#E32B2B'),
        alignment=TA_RIGHT
    )

    company_title_style = ParagraphStyle(
        'CompanyTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#12141C'),
    )

    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#64748B'),
        alignment=TA_RIGHT
    )

    meta_val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#0F172A'),
        alignment=TA_RIGHT
    )

    body_style = ParagraphStyle(
        'NormalBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155'),
    )

    body_bold = ParagraphStyle(
        'NormalBodyBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#0F172A'),
    )

    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.white,
    )

    table_header_right = ParagraphStyle(
        'TableHeaderRight',
        parent=table_header_style,
        alignment=TA_RIGHT
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1E293B'),
    )

    table_cell_right = ParagraphStyle(
        'TableCellRight',
        parent=table_cell_style,
        alignment=TA_RIGHT
    )

    elements = []

    # 1. Header Grid: Company Info (Left) and INVOICE Meta (Right)
    comp_name = settings.company_name if settings else "KR Web Solution"
    comp_addr = (settings.company_address or "").replace("\n", "<br/>") if settings else ""
    gst_on = bool(settings and getattr(settings, "gst_enabled", False) and settings.company_gstin)
    comp_gstin = f"<br/><b>GSTIN:</b> {settings.company_gstin}" if gst_on else ""
    comp_email = f"<br/><b>Email:</b> {settings.smtp_from_email or 'contact@krwebsolution.com'}" if settings else ""

    left_company_html = f"""
    <b><font size="14" color="#12141C">{comp_name}</font></b><br/>
    <font size="8" color="#475569">{comp_addr}{comp_gstin}{comp_email}</font>
    """

    status_color = "#16A34A" if invoice.status == "paid" else ("#B91C1C" if invoice.status == "overdue" else "#2563EB")
    if invoice.invoice_type == "credit_note":
        doc_title = "CREDIT NOTE"
    elif invoice.invoice_type == "late_fee":
        doc_title = "LATE FEE INVOICE"
    elif gst_on or float(invoice.tax_paise or 0) > 0:
        doc_title = "TAX INVOICE"
    else:
        doc_title = "INVOICE"
    parent_info = f"<br/><b>Ref Invoice:</b> {parent_invoice.invoice_number}" if parent_invoice else ""
    number_label = "Credit note #" if invoice.invoice_type == "credit_note" else "Invoice #"
    right_meta_html = f"""
    <b><font size="20" color="#E32B2B">{doc_title}</font></b><br/><br/>
    <b>{number_label}:</b> {invoice.invoice_number}<br/>
    <b>Date:</b> {invoice.issue_date.strftime('%d %b %Y')}<br/>
    <b>Due Date:</b> {invoice.due_date.strftime('%d %b %Y')}<br/>
    <b>Status:</b> <font color="{status_color}"><b>{invoice.status.upper()}</b></font>{parent_info}
    """

    header_data = [
        [
            Paragraph(left_company_html, body_style),
            Paragraph(right_meta_html, ParagraphStyle('RMeta', parent=body_style, alignment=TA_RIGHT))
        ]
    ]

    header_table = Table(header_data, colWidths=[3.25 * inch, 3.75 * inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 10))

    # Prominent PAID Receipt Stamp or OVERDUE Notice
    if invoice.status == "paid":
        paid_date_str = invoice.paid_at.strftime('%d %b %Y, %I:%M %p') if invoice.paid_at else "CONFIRMED"
        paid_stamp_html = f"""
        <div align="center">
            <b><font size="15" color="#16A34A">★ PAID IN FULL ★</font></b><br/>
            <font size="9" color="#15803D">Payment Confirmed on {paid_date_str}</font>
        </div>
        """
        paid_table = Table([[Paragraph(paid_stamp_html, ParagraphStyle('PaidStamp', parent=body_style, alignment=TA_CENTER))]], colWidths=[7.0 * inch])
        paid_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#E8F7EE')),
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#16A34A')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(paid_table)
        elements.append(Spacer(1, 10))
    elif invoice.status == "overdue":
        overdue_stamp_html = f"""
        <div align="center">
            <b><font size="13" color="#B91C1C">⚠️ PAYMENT OVERDUE</font></b><br/>
            <font size="8.5" color="#991B1B">Please settle the outstanding balance immediately to avoid additional late fees.</font>
        </div>
        """
        overdue_table = Table([[Paragraph(overdue_stamp_html, ParagraphStyle('OverdueStamp', parent=body_style, alignment=TA_CENTER))]], colWidths=[7.0 * inch])
        overdue_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FDECEC')),
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#B91C1C')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(overdue_table)
        elements.append(Spacer(1, 10))

    # Horizontal Brand Red Divider Line
    divider = Table([['']], colWidths=[7.0 * inch])
    divider.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, -1), 2.0, colors.HexColor('#E32B2B')),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(divider)
    elements.append(Spacer(1, 12))

    # 2. Bill To & Project Info
    client_name = client.name if client else "Valued Client"
    attn = ""
    if client and client.primary_contact_name:
        role = f", {client.designation}" if getattr(client, "designation", None) else ""
        attn = f"<br/><b>Attn:</b> {client.primary_contact_name}{role}"
    place_bits = []
    if client:
        place_bits = [p for p in [getattr(client, "city", None), getattr(client, "state", None), getattr(client, "pincode", None)] if p]
    client_place = f"<br/>{', '.join(place_bits)}" if place_bits else ""
    client_addr = f"<br/>{client.address.replace(chr(10), '<br/>')}" if (client and client.address) else ""
    client_gstin = f"<br/><b>GSTIN:</b> {client.gstin}" if (client and client.gstin) else ""
    client_email = f"<br/><b>Company email:</b> {client.email}" if (client and client.email) else ""
    client_phone = f"<br/><b>Company phone:</b> {client.phone}" if (client and client.phone) else ""
    contact_email = f"<br/><b>Contact email:</b> {client.contact_email}" if (client and getattr(client, "contact_email", None)) else ""
    contact_phone = f"<br/><b>Contact phone:</b> {client.contact_phone}" if (client and getattr(client, "contact_phone", None)) else ""
    client_contact = attn

    bill_to_html = f"""
    <b><font size="8" color="#64748B">BILLED TO:</font></b><br/>
    <b><font size="11" color="#0F172A">{client_name}</font></b>
    <font size="8" color="#475569">{client_contact}{contact_email}{contact_phone}{client_addr}{client_place}{client_gstin}{client_email}{client_phone}</font>
    """

    inv_type_display = (getattr(invoice, "invoice_type", None) or "standard").capitalize()
    project_info_html = ""
    if project:
        project_info_html = f"""
        <b><font size="8" color="#64748B">PROJECT DETAILS:</font></b><br/>
        <b><font size="10" color="#0F172A">{project.name}</font></b><br/>
        <font size="8" color="#475569">Type: {inv_type_display} Billing</font>
        """
    else:
        project_info_html = f"""
        <b><font size="8" color="#64748B">BILLING TYPE:</font></b><br/>
        <b><font size="10" color="#0F172A">{inv_type_display} Invoice</font></b>
        """

    info_data = [
        [
            Paragraph(bill_to_html, body_style),
            Paragraph(project_info_html, ParagraphStyle('RProj', parent=body_style, alignment=TA_RIGHT))
        ]
    ]

    info_table = Table(info_data, colWidths=[4.2 * inch, 2.8 * inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 16))

    # 3. Line Items Table
    table_data = [
        [
            Paragraph("#", table_header_style),
            Paragraph("Item & Description", table_header_style),
            Paragraph("Qty", table_header_right),
            Paragraph("Rate", table_header_right),
            Paragraph("Amount", table_header_right),
        ]
    ]

    for idx, itm in enumerate(items, 1):
        table_data.append([
            Paragraph(str(idx), table_cell_style),
            Paragraph(itm.description, table_cell_style),
            Paragraph(f"{float(itm.quantity):g}", table_cell_right),
            Paragraph(format_inr(itm.unit_rate_paise), table_cell_right),
            Paragraph(format_inr(itm.amount_paise), table_cell_right),
        ])

    col_widths = [0.4 * inch, 3.6 * inch, 0.7 * inch, 1.15 * inch, 1.15 * inch]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#12141C')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')])
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 10))

    # 4. Totals Block & Notes Block Side by Side
    tax_rate = float(invoice.tax_rate_percent or 0)
    show_gst = float(invoice.tax_paise or 0) > 0 or tax_rate > 0
    total_label = "CREDIT TOTAL:" if invoice.invoice_type == "credit_note" else "TOTAL DUE:"
    totals_data = [
        [Paragraph("Subtotal:", body_bold), Paragraph(format_inr(invoice.subtotal_paise), table_cell_right)],
    ]
    if show_gst:
        totals_data.append(
            [Paragraph(f"GST ({tax_rate:g}%):", body_style), Paragraph(format_inr(invoice.tax_paise), table_cell_right)]
        )
    totals_data.append(
        [
            Paragraph(
                f"<b>{total_label}</b>",
                ParagraphStyle('TotalL', parent=body_bold, fontSize=11, textColor=colors.HexColor('#E32B2B')),
            ),
            Paragraph(
                f"<b>{format_inr(invoice.total_paise)}</b>",
                ParagraphStyle(
                    'TotalR',
                    parent=table_cell_right,
                    fontSize=11,
                    fontName='Helvetica-Bold',
                    textColor=colors.HexColor('#E32B2B'),
                ),
            ),
        ]
    )
    totals_table = Table(totals_data, colWidths=[1.5 * inch, 1.4 * inch])
    last_row = len(totals_data) - 1
    gst_row = 1 if show_gst else None
    totals_style = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, last_row), (-1, last_row), colors.HexColor('#FEF6D8')),
    ]
    if gst_row is not None:
        totals_style.append(('LINEBELOW', (0, gst_row), (-1, gst_row), 0.5, colors.HexColor('#CBD5E1')))
    totals_table.setStyle(TableStyle(totals_style))

    notes_text = invoice.notes or (settings.payment_terms if settings else "")
    from .tax_payroll import resolve_payment_upi
    pay = resolve_payment_upi(settings, invoice) if settings else {"upi_id": None, "upi_qr_url": None}
    bank_details = settings.bank_upi_details if settings and settings.bank_upi_details else ""
    if pay.get("upi_id") and pay["upi_id"] not in (bank_details or ""):
        bank_details = (bank_details + " | " if bank_details else "") + f"UPI: {pay['upi_id']}"

    left_notes_html = f"""
    <b><font size="8" color="#64748B">BANK &amp; PAYMENT DETAILS:</font></b><br/>
    <font size="8" color="#334155">{bank_details.replace(chr(10), '<br/>') if bank_details else 'Contact accounts for transfer details.'}</font><br/><br/>
    <b><font size="8" color="#64748B">TERMS &amp; NOTES:</font></b><br/>
    <font size="8" color="#475569">{notes_text.replace(chr(10), '<br/>') if notes_text else 'Thank you for your business!'}</font>
    """

    summary_data = [
        [
            Paragraph(left_notes_html, body_style),
            totals_table
        ]
    ]
    summary_table = Table(summary_data, colWidths=[4.1 * inch, 2.9 * inch])
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(KeepTogether([summary_table]))

    elements.append(Spacer(1, 25))

    # Footer
    footer_kind = "credit note" if invoice.invoice_type == "credit_note" else ("tax invoice" if show_gst else "invoice")
    footer_text = Paragraph(
        f"<font size='7' color='#94A3B8'>This is a computer-generated {footer_kind} issued by {comp_name}. Page 1 of 1</font>",
        ParagraphStyle('Footer', parent=styles['Normal'], alignment=TA_CENTER)
    )
    elements.append(footer_text)

    doc.build(elements)
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
        textColor=colors.HexColor("#E32B2B"),
        alignment=TA_LEFT,
    )
    body = ParagraphStyle(
        "PayslipBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#0F172A"),
    )
    muted = ParagraphStyle(
        "PayslipMuted",
        parent=body,
        textColor=colors.HexColor("#64748B"),
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
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
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

