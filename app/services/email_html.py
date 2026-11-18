"""Branded HTML email templates for KR Web Solution billing & system mail."""

from __future__ import annotations

from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# KR brand theme (matches kr-erp-ui/app/globals.css)
BRAND_RED = "#E32B2B"
BRAND_RED_HOVER = "#C81F26"
BRAND_RED_TINT = "#FFF1F0"
BRAND_LOGO_RED = "#FF3330"
INK = "#12141C"
BG = "#F6F7F9"
CARD = "#FFFFFF"
TEXT = "#0F172A"
MUTED = "#64748B"
SUBTLE = "#94A3B8"
BORDER = "#E6E8EE"
SUCCESS = "#16A34A"
SUCCESS_BG = "#E8F7EE"
WARN = "#B45309"
WARN_BG = "#FEF6D8"
DANGER = "#B91C1C"
DANGER_BG = "#FDECEC"

LOGO_CID = "krws-mark"
WORDMARK_CID = "krws-wordmark"
DEFAULT_MARK_PATH = "/brand/krws-mark.png"
DEFAULT_WORDMARK_PATH = "/brand/krws-wordmark-trimmed.png"


def fill_placeholders(template: str, values: Dict[str, Any]) -> str:
    """Replace {key} placeholders without raising on missing keys."""
    out = template or ""
    for key, value in values.items():
        out = out.replace("{" + key + "}", "" if value is None else str(value))
    return out


def _e(value: Any) -> str:
    return escape("" if value is None else str(value))


def _money_inr(paise: int) -> str:
    return f"₹{(paise or 0) / 100:,.2f}"


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def _frontend_base() -> str:
    try:
        from ..core.config import settings

        return (settings.FRONTEND_URL or "http://localhost:3000").rstrip("/")
    except Exception:
        return "http://localhost:3000"


def _brand_public_dir() -> Path:
    # kr-erp-api/app/services → repo root → kr-erp-ui/public/brand
    return Path(__file__).resolve().parents[3] / "kr-erp-ui" / "public" / "brand"


def absolute_asset_url(path_or_url: Optional[str], fallback: str = DEFAULT_MARK_PATH) -> str:
    raw = (path_or_url or fallback or "").strip()
    if not raw:
        raw = fallback
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    return f"{_frontend_base()}{raw}"


def load_inline_brand_images() -> Dict[str, bytes]:
    """Load logo PNGs for CID embedding (works even when FRONTEND_URL is local)."""
    brand_dir = _brand_public_dir()
    images: Dict[str, bytes] = {}
    mark = brand_dir / "krws-mark.png"
    if not mark.exists():
        mark = brand_dir / "kr-logo.png"
    wordmark = brand_dir / "krws-wordmark-trimmed.png"
    try:
        if mark.exists():
            images[LOGO_CID] = mark.read_bytes()
        if wordmark.exists():
            images[WORDMARK_CID] = wordmark.read_bytes()
    except OSError:
        pass
    return images


def resolve_logo_src(company_logo_url: Optional[str] = None) -> str:
    """
    Prefer a public HTTPS URL so Gmail does not list the logo as an attachment.
    Fall back to CID only for local/dev (http://localhost).
    Never use the large wordmark in email bodies.
    """
    custom = (company_logo_url or "").strip()
    if custom.startswith("https://"):
        return custom
    if custom.startswith("http://") and "localhost" not in custom and "127.0.0.1" not in custom:
        return custom

    hosted = absolute_asset_url(custom or None, DEFAULT_MARK_PATH)
    if hosted.startswith("https://"):
        return hosted

    inline = load_inline_brand_images()
    if LOGO_CID in inline:
        return f"cid:{LOGO_CID}"
    return hosted


def resolve_wordmark_src() -> str:
    # Wordmark is intentionally not used in emails (shows as a large attachment in Gmail).
    return absolute_asset_url(DEFAULT_WORDMARK_PATH)


def render_shell(
    *,
    company_name: str,
    preheader: str,
    title: str,
    eyebrow: str = "",
    body_html: str,
    footer_note: str = "",
    company_address: str = "",
    company_gstin: str = "",
    company_logo_url: str = "",
    accent: str = BRAND_RED,
    status_label: str = "",
    status_tone: str = "neutral",
) -> str:
    """Professional corporate billing email shell (email-client safe)."""
    company = _e(company_name or "KR Web Solution")
    address = _e(company_address or "")
    gstin = _e(company_gstin or "")
    pre = _e(preheader)
    logo_src = _e(resolve_logo_src(company_logo_url or None))

    # Neutral status label — no red/green highlight chips
    status_html = ""
    if status_label or eyebrow:
        label = status_label or eyebrow
        status_html = (
            f'<span style="display:inline-block;padding:4px 9px;border-radius:4px;'
            f'background:{BG};border:1px solid {BORDER};color:{MUTED};'
            f'font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">'
            f'{_e(label)}</span>'
        )

    address_html = _anti_autolink_text(address) if address else ""
    gstin_html = _anti_autolink_text(f"GSTIN {gstin}") if gstin else ""

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light only" />
  <meta name="format-detection" content="telephone=no,address=no,email=no,date=no,url=no" />
  <title>{_e(title)}</title>
</head>
<body style="margin:0;padding:0;background:{BG};color:{TEXT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{pre}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG};">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:{CARD};border:1px solid {BORDER};border-radius:6px;overflow:hidden;">
          <!-- Brand header (light surface + brand-red accent) -->
          <tr>
            <td style="height:3px;background:{BRAND_RED};font-size:0;line-height:0;">&nbsp;</td>
          </tr>
          <tr>
            <td style="background:{CARD};padding:18px 24px;border-bottom:1px solid {BORDER};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="middle" style="padding-right:14px;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="background:{BG};border:1px solid {BORDER};border-radius:10px;">
                            <tr>
                              <td style="padding:8px;line-height:0;">
                                <img src="{logo_src}" width="40" height="40" alt="{company}" style="display:block;width:40px;height:40px;border:0;outline:none;object-fit:contain;" />
                              </td>
                            </tr>
                          </table>
                        </td>
                        <td valign="middle">
                          <div style="font-size:16px;font-weight:700;color:{TEXT};letter-spacing:-0.01em;line-height:1.25;">{company}</div>
                          <div style="margin-top:3px;font-size:11px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{BRAND_RED};">Accounts &amp; Billing</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Title -->
          <tr>
            <td style="padding:24px 24px 4px 24px;">
              {f'<div style="margin:0 0 10px 0;">{status_html}</div>' if status_html else ""}
              <h1 style="margin:0;font-size:20px;line-height:1.35;font-weight:700;color:{TEXT};letter-spacing:-0.02em;">{_e(title)}</h1>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:10px 24px 8px 24px;font-size:14px;line-height:1.55;color:{TEXT};">
              {body_html}
            </td>
          </tr>
          <!-- Sign-off + company footer (single block) -->
          <tr>
            <td style="padding:8px 24px 20px 24px;">
              <p style="margin:0 0 18px 0;font-size:13px;line-height:1.5;color:{MUTED};">
                Kind regards,<br/>
                <span style="color:{TEXT};font-weight:600;">{company}</span>
                <span style="color:{SUBTLE};"> · Accounts Team</span>
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {BORDER};">
                <tr>
                  <td style="padding-top:16px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="top" width="44" style="padding-right:12px;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="background:{BG};border:1px solid {BORDER};border-radius:8px;">
                            <tr>
                              <td style="padding:6px;line-height:0;">
                                <img src="{logo_src}" width="28" height="28" alt="" style="display:block;width:28px;height:28px;border:0;outline:none;object-fit:contain;" />
                              </td>
                            </tr>
                          </table>
                        </td>
                        <td valign="top">
                          <div style="font-size:12px;font-weight:700;color:{TEXT};line-height:1.3;">{company}</div>
                          {f'<div style="margin-top:3px;font-size:11px;line-height:1.45;color:{MUTED};text-decoration:none;">{address_html}</div>' if address_html else ""}
                          {f'<div style="margin-top:2px;font-size:11px;line-height:1.45;color:{MUTED};font-family:ui-monospace,Consolas,monospace;">{gstin_html}</div>' if gstin_html else ""}
                        </td>
                      </tr>
                    </table>
                    <p style="margin:14px 0 0 0;font-size:11px;line-height:1.45;color:{SUBTLE};">
                      {_e(footer_note or "Automated billing message from KR Web Solution. Do not share OTP or card details by email.")}
                    </p>
                    <p style="margin:6px 0 0 0;font-size:11px;color:{SUBTLE};">© {_e(datetime.now().year)} {company}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _summary_card(
    *,
    amount_label: str,
    amount: str,
    rows: Sequence[Tuple[str, str]],
    tone: str = "neutral",
    headline: str = "",
    subhead: str = "",
) -> str:
    """Neutral corporate summary panel — white/gray only, no color washes."""
    _ = tone  # kept for API compatibility; visual tone is intentionally neutral

    head = ""
    if headline:
        head = f"""
        <tr>
          <td style="padding:14px 18px 10px 18px;background:{CARD};border-bottom:1px solid {BORDER};">
            <div style="font-size:13px;font-weight:700;color:{TEXT};letter-spacing:0.01em;">{_e(headline)}</div>
            {f'<div style="margin-top:3px;font-size:12px;color:{MUTED};">{_e(subhead)}</div>' if subhead else ""}
          </td>
        </tr>"""

    amount_block = ""
    if amount_label and amount:
        amount_block = f"""
      <tr>
        <td style="padding:16px 18px;background:{BG};border-bottom:1px solid {BORDER};">
          <div style="font-size:11px;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">{_e(amount_label)}</div>
          <div style="margin-top:4px;font-size:28px;line-height:1.15;font-weight:700;color:{TEXT};letter-spacing:-0.03em;">{_e(amount)}</div>
        </td>
      </tr>"""

    fact_rows = []
    for i, (label, value) in enumerate(rows):
        border = f"border-top:1px solid {BORDER};" if i > 0 else ""
        fact_rows.append(
            f"""<tr>
              <td style="padding:11px 0;{border}font-size:12px;color:{MUTED};width:44%;">{_e(label)}</td>
              <td style="padding:11px 0;{border}font-size:13px;font-weight:600;color:{TEXT};text-align:right;">{value}</td>
            </tr>"""
        )

    facts_block = ""
    if fact_rows:
        facts_block = f"""
      <tr>
        <td style="padding:4px 18px 8px 18px;background:{CARD};">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            {''.join(fact_rows)}
          </table>
        </td>
      </tr>"""

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:18px 0;border:1px solid {BORDER};border-radius:6px;overflow:hidden;">
      {head}
      {amount_block}
      {facts_block}
    </table>"""


def _facts_table(rows: Sequence[Tuple[str, str]]) -> str:
    return _summary_card(amount_label="", amount="", rows=rows)


def _amount_banner(label: str, amount: str, tone: str = "neutral") -> str:
    # Kept for compatibility; prefer _summary_card in new templates
    return _summary_card(amount_label=label, amount=amount, rows=[], tone=tone)


def _anti_autolink_text(text: str) -> str:
    """Reduce Gmail blue auto-links on addresses / GSTIN / phone-like strings."""
    safe = _e(text)
    # Zero-width space after digits and separators breaks detectors
    out: list[str] = []
    for ch in safe:
        out.append(ch)
        if ch.isdigit() or ch in {",", ".", "-", "/"}:
            out.append("&#8203;")
    return "".join(out)


def _info_box(title: str, content_html: str, tone: str = "neutral") -> str:
    _ = tone
    return f"""
    <tr>
      <td style="padding:12px 14px;border-top:1px solid {BORDER};">
        <div style="font-size:10px;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;color:{SUBTLE};margin:0 0 5px 0;">{_e(title)}</div>
        <div style="font-size:13px;line-height:1.5;color:{TEXT};">{content_html}</div>
      </td>
    </tr>"""


def _supporting_details(*, bank: str = "", notes: str = "") -> str:
    """Single clean panel for bank + notes (avoids stacked gray boxes)."""
    rows = []
    if bank:
        rows.append(_info_box("Bank / UPI", _anti_autolink_text(bank)))
    if notes:
        rows.append(_info_box("Notes", _e(notes)))
    if not rows:
        return ""
    # First row should not double the top border visually against outer card
    first = rows[0].replace(f"border-top:1px solid {BORDER};", "", 1)
    rest = "".join(rows[1:])
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 0 0;border:1px solid {BORDER};border-radius:6px;overflow:hidden;">
      {first}{rest}
    </table>"""


def _greeting(name: str) -> str:
    safe = _e(name or "there")
    return f'<p style="margin:0 0 10px 0;font-size:14px;color:{TEXT};">Dear {safe},</p>'


def _p(text: str) -> str:
    return f'<p style="margin:0 0 10px 0;font-size:14px;line-height:1.55;color:{MUTED};">{text}</p>'


def _signoff(company_name: str) -> str:
    # Sign-off lives in the shell footer to avoid duplicate company blocks.
    _ = company_name
    return ""


def _plain_from_html_bits(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


# ---------- Public builders ----------

def build_invoice_email(
    *,
    company_name: str,
    company_address: str = "",
    company_gstin: str = "",
    company_logo_url: str = "",
    client_name: str,
    invoice_number: str,
    total_paise: int,
    due_date: Any,
    issue_date: Any = None,
    bank_upi_details: str = "",
    invoice_type: str = "invoice",
    project_name: str = "",
    notes: str = "",
    variant: str = "sent",  # sent | reminder | overdue | late_fee
) -> Tuple[str, str, str]:
    """Return (subject, html, plain)."""
    amount = _money_inr(total_paise)
    due = _fmt_date(due_date)
    issued = _fmt_date(issue_date)
    type_label = {
        "late_fee": "Late fee invoice",
        "maintenance": "Maintenance invoice",
        "amc": "Maintenance invoice",
        "hosting": "Hosting invoice",
        "services": "Services invoice",
        "licensing": "Licensing invoice",
        "custom_dev": "Development invoice",
        "project": "Development invoice",
    }.get((invoice_type or "").lower(), "Tax invoice")

    facts = [
        ("Invoice number", f'<span style="font-family:ui-monospace,Consolas,monospace;">{_e(invoice_number)}</span>'),
        ("Due date", _e(due)),
    ]
    if issue_date:
        facts.insert(1, ("Issue date", _e(issued)))
    if project_name:
        facts.append(("Project", _e(project_name)))
    facts.append(("Document", "GST tax invoice PDF attached"))

    if variant == "overdue":
        title = "Payment overdue"
        status_label = "Overdue"
        status_tone = "danger"
        accent = DANGER
        intro = _p(
            f"Invoice <strong style=\"color:{TEXT};\">{_e(invoice_number)}</strong> for "
            f"<strong style=\"color:{TEXT};\">{_e(amount)}</strong> was due on "
            f"<strong style=\"color:{TEXT};\">{_e(due)}</strong> and remains unpaid."
        )
        summary = _summary_card(
            amount_label="Amount outstanding",
            amount=amount,
            rows=facts,
            tone="danger",
            headline="Payment required",
            subhead="Please clear this invoice to avoid late fees.",
        )
        subject = f"Overdue: Invoice {invoice_number} payment pending"
        tip = _info_box(
            "Next step",
            "Arrange payment using the bank details below. The tax invoice PDF is attached for your records.",
            "danger",
        )
        show_bank = True
    elif variant == "reminder":
        title = "Payment reminder"
        status_label = "Reminder"
        status_tone = "warn"
        accent = WARN
        intro = _p(
            f"This is a courtesy reminder for invoice "
            f"<strong style=\"color:{TEXT};\">{_e(invoice_number)}</strong> totaling "
            f"<strong style=\"color:{TEXT};\">{_e(amount)}</strong>, due on "
            f"<strong style=\"color:{TEXT};\">{_e(due)}</strong>."
        )
        summary = _summary_card(
            amount_label="Amount due",
            amount=amount,
            rows=facts,
            tone="warn",
            headline="Upcoming payment",
            subhead="If payment has already been made, please disregard this notice.",
        )
        subject = f"Reminder: Invoice {invoice_number} is due"
        tip = ""
        show_bank = True
    elif variant == "late_fee":
        title = "Late fee invoice issued"
        status_label = "Late fee"
        status_tone = "neutral"
        accent = BRAND_RED
        intro = _p(
            f"A late fee invoice <strong style=\"color:{TEXT};\">{_e(invoice_number)}</strong> "
            f"for <strong style=\"color:{TEXT};\">{_e(amount)}</strong> has been generated."
        )
        summary = _summary_card(
            amount_label="Late fee amount",
            amount=amount,
            rows=facts,
            tone="neutral",
            headline=type_label,
            subhead="Full GST details are in the attached PDF.",
        )
        subject = f"Late Fee Invoice {invoice_number} from {company_name}"
        tip = ""
        show_bank = True
    else:
        title = "Tax invoice"
        status_label = type_label
        status_tone = "neutral"
        accent = BRAND_RED
        intro = _p(
            f"Please find your tax invoice "
            f"<strong style=\"color:{TEXT};\">{_e(invoice_number)}</strong> for "
            f"<strong style=\"color:{TEXT};\">{_e(amount)}</strong>. "
            f"Payment is due by <strong style=\"color:{TEXT};\">{_e(due)}</strong>."
        )
        summary = _summary_card(
            amount_label="Total payable",
            amount=amount,
            rows=facts,
            tone="neutral",
            headline="Invoice summary",
            subhead="GST tax invoice PDF attached",
        )
        subject = f"Invoice {invoice_number} from {company_name}"
        tip = ""
        show_bank = True

    details = _supporting_details(
        bank=(bank_upi_details or "Please contact accounts for payment instructions.")
        if show_bank
        else "",
        notes=notes or "",
    )

    body = "".join(
        [
            _greeting(client_name),
            intro,
            summary,
            tip,
            details,
        ]
    )

    html = render_shell(
        company_name=company_name,
        company_address=company_address,
        company_gstin=company_gstin,
        company_logo_url=company_logo_url,
        preheader=f"{invoice_number} · {amount} · Due {due}",
        title=title,
        eyebrow=status_label,
        status_label=status_label,
        status_tone=status_tone,
        body_html=body,
        footer_note="Automated billing message. For payment queries, reply to this email.",
        accent=accent,
    )
    plain = _plain_from_html_bits(
        f"Dear {client_name},",
        f"Invoice {invoice_number} for {amount}. Due date: {due}.",
        f"Payment details: {bank_upi_details or 'Contact accounts.'}",
        f"Kind regards,\n{company_name}\nAccounts Team",
    )
    return subject, html, plain


def resolve_invoice_email_kind(
    *,
    status: Optional[str],
    invoice_type: Optional[str] = None,
    due_date: Any = None,
    today: Optional[date] = None,
) -> str:
    """
    Pick the client email template from invoice state.

    Returns one of: paid | overdue | reminder | late_fee | sent
    """
    st = (status or "").lower().strip()
    itype = (invoice_type or "").lower().strip()
    if st == "paid":
        return "paid"
    if itype == "late_fee":
        return "late_fee"
    if st == "overdue":
        return "overdue"
    ref = today or date.today()
    if st in ("sent", "overdue", "partially_paid") and due_date:
        d = due_date.date() if isinstance(due_date, datetime) else due_date
        if isinstance(d, date) and d < ref:
            return "overdue"
    return "sent"


def compose_invoice_client_email(
    *,
    company_name: str,
    company_address: str = "",
    company_gstin: str = "",
    company_logo_url: str = "",
    client_name: str,
    invoice_number: str,
    total_paise: int,
    due_date: Any = None,
    issue_date: Any = None,
    paid_at: Any = None,
    bank_upi_details: str = "",
    invoice_type: str = "invoice",
    project_name: str = "",
    notes: str = "",
    status: str = "draft",
    force_kind: Optional[str] = None,
) -> Tuple[str, str, str, str, str]:
    """
    Build the correct client email for this invoice.

    Returns (kind, subject, html, plain, attachment_filename)
    kind: paid | overdue | late_fee | sent
    """
    kind = force_kind or resolve_invoice_email_kind(
        status=status,
        invoice_type=invoice_type,
        due_date=due_date,
    )
    if kind == "paid":
        subject, html, plain = build_payment_received_email(
            company_name=company_name,
            company_address=company_address,
            company_gstin=company_gstin,
            company_logo_url=company_logo_url,
            client_name=client_name,
            invoice_number=invoice_number,
            total_paise=total_paise,
            paid_at=paid_at or datetime.now(),
            invoice_type=invoice_type,
            project_name=project_name,
        )
        return kind, subject, html, plain, f"Receipt_{invoice_number}.pdf"

    variant = {
        "overdue": "overdue",
        "late_fee": "late_fee",
        "reminder": "reminder",
        "sent": "sent",
    }.get(kind, "sent")
    subject, html, plain = build_invoice_email(
        company_name=company_name,
        company_address=company_address,
        company_gstin=company_gstin,
        company_logo_url=company_logo_url,
        client_name=client_name,
        invoice_number=invoice_number,
        total_paise=total_paise,
        due_date=due_date,
        issue_date=issue_date,
        bank_upi_details=bank_upi_details,
        invoice_type=invoice_type,
        project_name=project_name,
        notes=notes,
        variant=variant,
    )
    return kind, subject, html, plain, f"{invoice_number}.pdf"


def build_payment_received_email(
    *,
    company_name: str,
    company_address: str = "",
    company_gstin: str = "",
    company_logo_url: str = "",
    client_name: str,
    invoice_number: str,
    total_paise: int,
    paid_at: Any,
    invoice_type: str = "",
    project_name: str = "",
) -> Tuple[str, str, str]:
    amount = _money_inr(total_paise)
    paid = _fmt_date(paid_at)
    subject = f"Payment receipt — {invoice_number}"
    facts = [
        ("Invoice number", f'<span style="font-family:ui-monospace,Consolas,monospace;">{_e(invoice_number)}</span>'),
        ("Payment recorded", _e(paid)),
        ("Status", f'<span style="color:{TEXT};font-weight:700;">Paid</span>'),
        ("Document", "Official receipt PDF attached"),
    ]
    if project_name:
        facts.insert(1, ("Project", _e(project_name)))

    body = "".join(
        [
            _greeting(client_name),
            _p(
                f"Thank you. We have received payment for invoice "
                f"<strong style=\"color:{TEXT};\">{_e(invoice_number)}</strong>. "
                f"This invoice is now marked as paid in our accounts."
            ),
            _summary_card(
                amount_label="Amount received",
                amount=amount,
                rows=facts,
                tone="success",
                headline="Payment confirmed",
                subhead=f"Settled on {paid}",
            ),
            _supporting_details(
                notes="Official payment receipt PDF (with PAID stamp) is attached for your records and GST filing.",
            ),
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_address=company_address,
        company_gstin=company_gstin,
        company_logo_url=company_logo_url,
        preheader=f"Payment receipt · {invoice_number} · {amount}",
        title="Payment receipt",
        status_label="Paid",
        status_tone="success",
        body_html=body,
        accent=SUCCESS,
        footer_note="This receipt confirms settlement of the referenced invoice.",
    )
    plain = (
        f"Dear {client_name},\n\n"
        f"Payment receipt — invoice {invoice_number} ({amount}) settled on {paid}.\n\n"
        f"Kind regards,\n{company_name}\nAccounts Team"
    )
    return subject, html, plain


def build_user_invite_email(
    *,
    company_name: str,
    user_name: str,
    invite_link: str,
    role_label: str = "team member",
    company_address: str = "",
    company_logo_url: str = "",
) -> Tuple[str, str, str]:
    subject = f"You're invited to KR Workspace — set your password"
    link = _e(invite_link)
    body = "".join(
        [
            _greeting(user_name),
            _p(
                f"You have been invited to join <strong>{_e(company_name)}</strong> "
                f"KR Workspace as <strong>{_e(role_label)}</strong>."
            ),
            _p("Set your password to activate your account. You can also sign in with Google or Microsoft once your password is set."),
            f"""
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
              <tr>
                <td style="background:{BRAND_RED};border-radius:10px;">
                  <a href="{link}" style="display:inline-block;padding:12px 20px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">
                    Set password
                  </a>
                </td>
              </tr>
            </table>
            """,
            _p(f'Or copy this link:<br/><a href="{link}" style="color:{BRAND_RED};word-break:break-all;">{link}</a>'),
            _info_box(
                "Expires in 72 hours",
                "If this invite expires, ask your administrator to send a new one.",
                "warn",
            ),
            f'<p style="margin:18px 0 0 0;color:{MUTED};font-size:13px;">Regards,<br/><strong style="color:{TEXT};">{_e(company_name)}</strong></p>',
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_address=company_address,
        company_logo_url=company_logo_url,
        preheader="Set your KR Workspace password",
        title="You're invited",
        eyebrow="Account invite",
        body_html=body,
    )
    plain = (
        f"Hi {user_name},\n\n"
        f"You are invited to {company_name} KR Workspace as {role_label}.\n"
        f"Set your password: {invite_link}\n\n"
        f"This link expires in 72 hours.\n"
    )
    return subject, html, plain


def build_password_reset_email(
    *,
    company_name: str,
    user_name: str,
    reset_link: str,
    company_address: str = "",
    company_logo_url: str = "",
) -> Tuple[str, str, str]:
    subject = f"Password reset — {company_name}"
    link = _e(reset_link)
    body = "".join(
        [
            _greeting(user_name),
            _p("We received a request to reset your KR Workspace password."),
            f"""
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
              <tr>
                <td style="background:{BRAND_RED};border-radius:10px;">
                  <a href="{link}" style="display:inline-block;padding:12px 20px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;">
                    Reset password
                  </a>
                </td>
              </tr>
            </table>
            """,
            _p(f'Or copy this link:<br/><a href="{link}" style="color:{BRAND_RED};word-break:break-all;">{link}</a>'),
            _info_box(
                "Security note",
                "This link expires in 15 minutes. If you did not request a reset, you can safely ignore this email.",
                "warn",
            ),
            f'<p style="margin:18px 0 0 0;color:{MUTED};font-size:13px;">Regards,<br/><strong style="color:{TEXT};">{_e(company_name)}</strong> Security Team</p>',
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_address=company_address,
        company_logo_url=company_logo_url,
        preheader="Reset your KR Workspace password",
        title="Reset your password",
        eyebrow="Account security",
        body_html=body,
    )
    plain = (
        f"Hello {user_name},\n\n"
        f"Reset your password using this link (expires in 15 minutes):\n{reset_link}\n\n"
        f"If you did not request this, ignore this email.\n\n"
        f"Regards,\n{company_name}"
    )
    return subject, html, plain


def build_lead_welcome_email(
    *,
    company_name: str,
    contact_name: str,
    lead_name: str,
    company_address: str = "",
    company_logo_url: str = "",
) -> Tuple[str, str, str]:
    subject = f"Thank you for contacting {company_name}"
    body = "".join(
        [
            _greeting(contact_name),
            _p(
                f"Thank you for reaching out about <strong>{_e(lead_name)}</strong>. "
                "Our team has received your requirement."
            ),
            _info_box(
                "What happens next",
                "A specialist will review your request and contact you within <strong>24 business hours</strong>.",
            ),
            f'<p style="margin:18px 0 0 0;color:{MUTED};font-size:13px;">Best regards,<br/><strong style="color:{TEXT};">{_e(company_name)}</strong> Team</p>',
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_address=company_address,
        company_logo_url=company_logo_url,
        preheader="We received your enquiry",
        title="We've received your enquiry",
        eyebrow="Welcome",
        body_html=body,
    )
    plain = (
        f"Hello {contact_name},\n\n"
        f"Thank you for contacting us about '{lead_name}'. "
        f"We will get back within 24 business hours.\n\n"
        f"Best regards,\n{company_name}"
    )
    return subject, html, plain


def build_founder_overdue_email(
    *,
    company_name: str,
    founder_name: str,
    items: List[Dict[str, Any]],
    company_logo_url: str = "",
) -> Tuple[str, str, str]:
    subject = f"Action required: {len(items)} overdue invoice(s)"
    rows = []
    for item in items:
        rows.append(
            f"""<tr>
              <td style="padding:10px 0;border-bottom:1px solid {BORDER};font-family:Consolas,Monaco,monospace;font-size:12px;">{_e(item.get('invoice_number'))}</td>
              <td style="padding:10px 0;border-bottom:1px solid {BORDER};font-size:12px;">{_e(item.get('client_name'))}</td>
              <td style="padding:10px 0;border-bottom:1px solid {BORDER};font-size:12px;text-align:right;font-weight:700;">₹{float(item.get('total_rupees') or 0):,.2f}</td>
              <td style="padding:10px 0;border-bottom:1px solid {BORDER};font-size:12px;text-align:right;color:{DANGER};">{_e(item.get('due_date'))}</td>
            </tr>"""
        )
    table = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0;">
      <tr>
        <th align="left" style="padding:0 0 8px 0;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">Invoice</th>
        <th align="left" style="padding:0 0 8px 0;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">Client</th>
        <th align="right" style="padding:0 0 8px 0;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">Amount</th>
        <th align="right" style="padding:0 0 8px 0;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">Due</th>
      </tr>
      {''.join(rows)}
    </table>"""
    body = "".join(
        [
            _greeting(founder_name or "Founder"),
            _p("The following invoice(s) are overdue and need a late-fee decision."),
            table,
            _info_box(
                "Next step",
                "Open <strong>Needs Action</strong> in KR Workspace to schedule or waive late fees.",
                "warn",
            ),
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_logo_url=company_logo_url,
        preheader=f"{len(items)} overdue invoice(s) need a decision",
        title="Overdue invoices need a decision",
        eyebrow="Founder alert",
        body_html=body,
        accent=DANGER,
        footer_note="Internal KR Workspace automation notice.",
    )
    lines = [
        f"• {i.get('invoice_number')} - {i.get('client_name')} (₹{float(i.get('total_rupees') or 0):,.2f}, due {i.get('due_date')})"
        for i in items
    ]
    plain = (
        f"Hello {founder_name},\n\n"
        f"Overdue invoices:\n" + "\n".join(lines) + "\n\n"
        f"Open Needs Action in the dashboard.\n"
    )
    return subject, html, plain


def build_smtp_test_email(
    *,
    company_name: str,
    smtp_host: str,
    smtp_port: int,
    company_logo_url: str = "",
) -> Tuple[str, str, str]:
    subject = f"{company_name} — SMTP test successful"
    body = "".join(
        [
            _p("Your SMTP configuration is working correctly."),
            _facts_table(
                [
                    ("SMTP host", _e(smtp_host)),
                    ("Port", _e(smtp_port)),
                    ("Status", f'<span style="color:{TEXT};font-weight:700;">Connected</span>'),
                ]
            ),
            _p("You can now send invoices, reminders, and receipts from KR Workspace."),
        ]
    )
    html = render_shell(
        company_name=company_name,
        company_logo_url=company_logo_url,
        preheader="SMTP test verification passed",
        title="SMTP test successful",
        eyebrow="Email server",
        body_html=body,
        accent=SUCCESS,
    )
    plain = (
        f"SMTP test OK via {smtp_host}:{smtp_port}.\n"
        f"KR Workspace can send mail successfully."
    )
    return subject, html, plain


# Seed / Settings defaults (HTML bodies with placeholders)
DEFAULT_EMAIL_TEMPLATES: List[Tuple[str, str, str]] = [
    (
        "invoice_sent",
        "Invoice {invoice_number} from {company_name}",
        "HTML_MANAGED:invoice_sent",
    ),
    (
        "payment_reminder",
        "Payment reminder: Invoice {invoice_number}",
        "HTML_MANAGED:payment_reminder",
    ),
    (
        "invoice_overdue",
        "Overdue: Invoice {invoice_number} payment pending",
        "HTML_MANAGED:invoice_overdue",
    ),
    (
        "payment_received",
        "Receipt: Payment received for {invoice_number}",
        "HTML_MANAGED:payment_received",
    ),
    (
        "lead_welcome",
        "Thank you for contacting {company_name}",
        "HTML_MANAGED:lead_welcome",
    ),
    (
        "password_reset",
        "Password reset — {company_name}",
        "HTML_MANAGED:password_reset",
    ),
    (
        "user_invite",
        "You're invited to KR Workspace — set your password",
        "HTML_MANAGED:user_invite",
    ),
]


def template_preview_html(template_id: str, company_name: str = "KR Web Solution") -> str:
    """Static preview HTML for Settings UI / documentation."""
    if template_id in ("invoice_sent", "payment_reminder", "invoice_overdue"):
        variant = {
            "invoice_sent": "sent",
            "payment_reminder": "reminder",
            "invoice_overdue": "overdue",
        }[template_id]
        _, html, _ = build_invoice_email(
            company_name=company_name,
            client_name="{client_name}",
            invoice_number="{invoice_number}",
            total_paise=1180000,
            due_date=date.today(),
            bank_upi_details="{bank_upi_details}",
            variant=variant,
        )
        return html
    if template_id == "payment_received":
        _, html, _ = build_payment_received_email(
            company_name=company_name,
            client_name="{client_name}",
            invoice_number="{invoice_number}",
            total_paise=1180000,
            paid_at=datetime.now(),
        )
        return html
    if template_id == "password_reset":
        _, html, _ = build_password_reset_email(
            company_name=company_name,
            user_name="{user_name}",
            reset_link="{reset_link}",
        )
        return html
    if template_id == "user_invite":
        _, html, _ = build_user_invite_email(
            company_name=company_name,
            user_name="{user_name}",
            invite_link="{invite_link}",
            role_label="{role_label}",
        )
        return html
    if template_id == "lead_welcome":
        _, html, _ = build_lead_welcome_email(
            company_name=company_name,
            contact_name="{contact_name}",
            lead_name="{lead_name}",
        )
        return html
    return render_shell(
        company_name=company_name,
        preheader="Template",
        title=template_id,
        body_html=_p("Managed HTML template."),
    )
