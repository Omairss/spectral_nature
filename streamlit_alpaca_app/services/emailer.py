from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import os
import smtplib
from typing import Any

from .secrets import describe_secret_resolution, resolve_secret_value


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


def _secret_resolution_message(label: str, resolution: dict[str, Any]) -> str:
    secret_name = str(resolution.get("secret_name") or "").strip()
    vault_name = str(resolution.get("vault_name") or "").strip()
    reason = str(resolution.get("reason") or "").strip()
    error_type = str(resolution.get("error_type") or "").strip()

    if reason == "secret_name_missing":
        return f"{label} is missing. Set it directly or configure its secret name."
    if reason == "vault_url_missing":
        return f"{label} is missing because Key Vault is not configured."
    if reason == "azure_sdk_unavailable":
        return f"{label} is missing because Azure Key Vault support is not installed in this runtime."
    if reason == "azure_credentials_unavailable":
        return f"{label} is missing because Azure credentials are unavailable in this runtime."
    if reason == "secret_value_missing":
        if secret_name:
            return f"{label} secret `{secret_name}` is empty."
        return f"{label} is empty."
    if reason == "key_vault_lookup_failed":
        if secret_name and vault_name and error_type in {"SecretNotFound", "ResourceNotFoundError"}:
            return f"{label} secret `{secret_name}` was not found in Key Vault `{vault_name}`."
        if secret_name and vault_name:
            return f"{label} secret `{secret_name}` could not be read from Key Vault `{vault_name}`."
        return f"{label} could not be read from Key Vault."
    return f"{label} is missing."


def email_delivery_status() -> dict[str, Any]:
    smtp_host = _smtp_host()
    from_address = describe_secret_resolution(
        ["APP_EMAIL_FROM", "EMAIL_FROM"],
        secret_name_env="APP_EMAIL_FROM_SECRET",
        default_secret_name="app-email-from",
    )
    smtp_username = describe_secret_resolution(
        ["APP_SMTP_USERNAME", "SMTP_USERNAME"],
        secret_name_env="APP_SMTP_USERNAME_SECRET",
        default_secret_name="app-smtp-username",
    )
    smtp_password = describe_secret_resolution(
        ["APP_SMTP_PASSWORD", "SMTP_PASSWORD"],
        secret_name_env="APP_SMTP_PASSWORD_SECRET",
        default_secret_name="app-smtp-password",
    )

    configured = bool(smtp_host and bool(from_address.get("resolved")))
    message = "Email delivery is configured."
    if not smtp_host:
        message = "SMTP host is missing. Set `APP_SMTP_HOST`."
    elif not bool(from_address.get("resolved")):
        message = _secret_resolution_message("Sender address", from_address)

    return {
        "configured": configured,
        "message": message,
        "smtp_host_present": bool(smtp_host),
        "smtp_host": smtp_host,
        "from_address_present": bool(from_address.get("resolved")),
        "from_address_source": str(from_address.get("source") or ""),
        "from_address": from_address,
        "smtp_username_present": bool(smtp_username.get("resolved")),
        "smtp_username": smtp_username,
        "smtp_password_present": bool(smtp_password.get("resolved")),
        "smtp_password": smtp_password,
        "smtp_auth_configured": bool(smtp_username.get("resolved")) and bool(smtp_password.get("resolved")),
    }


def email_delivery_configured() -> bool:
    return bool(email_delivery_status().get("configured"))


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
    status = email_delivery_status()
    if not bool(status.get("configured")):
        return EmailDeliveryResult(False, str(status.get("message") or "Email delivery is not configured."))

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
    "email_delivery_status",
    "email_delivery_configured",
    "send_email",
]
