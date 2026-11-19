import logging
from typing import Dict, Optional
from email.message import EmailMessage
import aiosmtplib
from ..core.security import decrypt_secret
from ..models import Settings

logger = logging.getLogger(__name__)


async def send_email_async(
    to_email: str,
    subject: str,
    body: str,
    settings: Settings,
    attachment_bytes: Optional[bytes] = None,
    attachment_filename: Optional[str] = None,
    is_html: bool = False,
    cc_email: Optional[str] = None,
    plain_body: Optional[str] = None,
    inline_images: Optional[Dict[str, bytes]] = None,
) -> bool:
    """Send email asynchronously using the SMTP configuration stored in settings."""
    from .outbound import record, test_outbound_forced

    if test_outbound_forced():
        record(
            "email",
            to_email,
            subject=subject,
            cc=cc_email,
            has_attachment=bool(attachment_bytes),
            is_html=is_html,
        )
        logger.info("TEST outbound: recorded email to %s (SMTP not called)", to_email)
        return True

    if not settings.smtp_host or not settings.smtp_from_email:
        raise ValueError("SMTP host and sender email must be configured in settings.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name or 'KR Web Solution'} <{settings.smtp_from_email}>"
    msg["To"] = to_email

    # Add CC if provided or configured in settings
    effective_cc = cc_email or getattr(settings, "invoice_cc_email", None)
    if effective_cc:
        msg["Cc"] = effective_cc

    if is_html:
        # Multipart: plain fallback + HTML for clients that support it
        msg.set_content(plain_body or "Please view this email in an HTML-capable mail client.")
        msg.add_alternative(body, subtype="html")

        # Embed ONLY logo CIDs actually referenced in the HTML.
        # Never attach unused brand assets (Gmail lists them as extra attachments).
        images = dict(inline_images or {})
        try:
            from .email_html import LOGO_CID, WORDMARK_CID, load_inline_brand_images

            brand = load_inline_brand_images()
            for cid in (LOGO_CID, WORDMARK_CID):
                if f"cid:{cid}" in body and cid in brand:
                    images[cid] = brand[cid]
        except Exception:
            pass

        if images:
            html_part = msg.get_payload()[-1]
            for cid, data in images.items():
                html_part.add_related(
                    data,
                    maintype="image",
                    subtype="png",
                    cid=cid,
                    filename=f"{cid}.png",
                    disposition="inline",
                )
    else:
        msg.set_content(body)

    if attachment_bytes and attachment_filename:
        # Attach after body/alternative so clients still render HTML correctly
        maintype, subtype = "application", "pdf"
        lower = attachment_filename.lower()
        if lower.endswith(".png"):
            maintype, subtype = "image", "png"
        elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
            maintype, subtype = "image", "jpeg"
        msg.add_attachment(
            attachment_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=attachment_filename,
        )

    # Decrypt password if stored
    smtp_password = decrypt_secret(settings.smtp_password_encrypted) if settings.smtp_password_encrypted else ""

    is_ssl = (settings.smtp_port == 465)
    use_tls = is_ssl
    start_tls = settings.smtp_use_tls if not is_ssl else False
    port = settings.smtp_port or (465 if is_ssl else 587)
    send_kwargs = dict(
        hostname=settings.smtp_host,
        port=port,
        username=settings.smtp_username or None,
        password=smtp_password or None,
        use_tls=use_tls,
        start_tls=start_tls,
        timeout=30.0,
    )

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            await aiosmtplib.send(msg, **send_kwargs)
            logger.info(f"Email sent successfully to {to_email}")
            return True
        except aiosmtplib.SMTPConnectTimeoutError as e:
            last_error = e
            logger.warning(
                "SMTP connect timeout to %s:%s (attempt %s/2): %s",
                settings.smtp_host,
                port,
                attempt + 1,
                e,
            )
            if attempt == 0:
                continue
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            raise

    logger.error(f"Failed to send email to {to_email}: {last_error}")
    raise last_error  # type: ignore[misc]
