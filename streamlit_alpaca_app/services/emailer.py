from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import os
import smtplib

from .secrets import resolve_secret_value


@dataclass(frozen=True)
class EmailDeliveryResult:
    sent: bool
    message: str


@dataclass(frozen=True)
class EmailInlineImage:
    content_id: str
    content: bytes
    mime_type: str = "image/png"
    filename: str = ""


def _smtp_host() -> str:
    return (os.getenv("APP_SMTP_HOST") or os.getenv("SMTP_HOST") or "").strip()


def _smtp_port() -> int:
    raw = (os.getenv("APP_SMTP_PORT") or os.getenv("SMTP_PORT") or "587").strip()
    try:
        return max(int(raw), 1)
    except Exception:
        return 587


def _smtp_username() -> str:
    return resolve_secret_value(
        ["APP_SMTP_USERNAME", "SMTP_USERNAME"],
        secret_name_env="APP_SMTP_USERNAME_SECRET",
        default_secret_name="app-smtp-username",
    )


def _smtp_password() -> str:
    return resolve_secret_value(
        ["APP_SMTP_PASSWORD", "SMTP_PASSWORD"],
        secret_name_env="APP_SMTP_PASSWORD_SECRET",
        default_secret_name="app-smtp-password",
    )


def _smtp_use_tls() -> bool:
    raw = (os.getenv("APP_SMTP_USE_TLS") or os.getenv("SMTP_USE_TLS") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _smtp_use_ssl() -> bool:
    raw = (os.getenv("APP_SMTP_USE_SSL") or os.getenv("SMTP_USE_SSL") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _from_address() -> str:
    return resolve_secret_value(
        ["APP_EMAIL_FROM", "EMAIL_FROM"],
        secret_name_env="APP_EMAIL_FROM_SECRET",
        default_secret_name="app-email-from",
    )


def email_delivery_configured() -> bool:
    return bool(_smtp_host() and _from_address())


def send_email(
    *,
    to_address: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    inline_images: list[EmailInlineImage] | None = None,
) -> EmailDeliveryResult:
    recipient = str(to_address or "").strip()
    sender = _from_address()
    if not recipient:
        return EmailDeliveryResult(False, "Missing recipient email address.")
    if not email_delivery_configured():
        return EmailDeliveryResult(False, "Email delivery is not configured.")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text_body or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")
        html_part = message.get_payload()[-1]
        for image in inline_images or []:
            content_id = str(image.content_id or "").strip()
            if not content_id:
                continue
            mime_type = str(image.mime_type or "image/png").strip().lower()
            if "/" in mime_type:
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "image", "png"
            filename = str(image.filename or "").strip() or f"{content_id}.{subtype}"
            html_part.add_related(
                image.content,
                maintype=maintype,
                subtype=subtype,
                cid=f"<{content_id}>",
                filename=filename,
                disposition="inline",
            )

    host = _smtp_host()
    port = _smtp_port()
    username = _smtp_username()
    password = _smtp_password()

    try:
        if _smtp_use_ssl():
            with smtplib.SMTP_SSL(host, port, timeout=15) as client:
                if username:
                    client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as client:
                if _smtp_use_tls():
                    client.starttls()
                if username:
                    client.login(username, password)
                client.send_message(message)
    except Exception as exc:
        return EmailDeliveryResult(False, f"Email send failed: {exc}")

    return EmailDeliveryResult(True, f"Email sent to {recipient}.")


__all__ = [
    "EmailInlineImage",
    "EmailDeliveryResult",
    "email_delivery_configured",
    "send_email",
]
