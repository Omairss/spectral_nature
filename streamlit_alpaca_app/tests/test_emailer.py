from __future__ import annotations

from services import emailer


def test_email_delivery_status_supports_default_secret_names(monkeypatch):
    monkeypatch.setenv("APP_SMTP_HOST", "smtp.azurecomm.net")

    def fake_describe(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        del env_names, secret_name_env, placeholders
        values = {
            "app-email-from": "noreply@example.com",
            "app-smtp-username": "smtp-user",
            "app-smtp-password": "smtp-pass",
        }
        return {
            "resolved": True,
            "value": values.get(default_secret_name, ""),
            "source": "key_vault",
            "secret_name": str(default_secret_name or ""),
            "vault_name": "spectral-nature-kvault",
            "reason": "",
            "error_type": "",
            "error_message": "",
        }

    monkeypatch.setattr(emailer, "describe_secret_resolution", fake_describe)

    status = emailer.email_delivery_status()
    assert status["configured"] is True
    assert status["smtp_auth_configured"] is True
    assert emailer.email_delivery_configured() is True


def test_email_delivery_status_reports_missing_sender_secret(monkeypatch):
    monkeypatch.setenv("APP_SMTP_HOST", "smtp.azurecomm.net")

    def fake_describe(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        del env_names, secret_name_env, placeholders
        if default_secret_name == "app-email-from":
            return {
                "resolved": False,
                "value": "",
                "source": "",
                "secret_name": "app-email-from",
                "vault_name": "snpipelinekv03130136",
                "reason": "key_vault_lookup_failed",
                "error_type": "SecretNotFound",
                "error_message": "Secret was not found.",
            }
        return {
            "resolved": True,
            "value": "configured",
            "source": "key_vault",
            "secret_name": str(default_secret_name or ""),
            "vault_name": "spectral-nature-kvault",
            "reason": "",
            "error_type": "",
            "error_message": "",
        }

    monkeypatch.setattr(emailer, "describe_secret_resolution", fake_describe)

    status = emailer.email_delivery_status()

    assert status["configured"] is False
    assert "app-email-from" in status["message"]
    assert "snpipelinekv03130136" in status["message"]


def test_send_email_supports_inline_images(monkeypatch):
    monkeypatch.setenv("APP_SMTP_HOST", "smtp.azurecomm.net")
    monkeypatch.setenv("APP_SMTP_PORT", "587")
    monkeypatch.setenv("APP_SMTP_USE_TLS", "false")
    monkeypatch.setenv("APP_SMTP_USE_SSL", "false")

    def fake_resolve(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        values = {
            "app-email-from": "noreply@example.com",
            "app-smtp-username": "smtp-user",
            "app-smtp-password": "smtp-pass",
        }
        return values.get(default_secret_name, "")

    monkeypatch.setattr(emailer, "resolve_secret_value", fake_resolve)

    def fake_describe(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        del env_names, secret_name_env, placeholders
        values = {
            "app-email-from": "noreply@example.com",
            "app-smtp-username": "smtp-user",
            "app-smtp-password": "smtp-pass",
        }
        return {
            "resolved": True,
            "value": values.get(default_secret_name, ""),
            "source": "key_vault",
            "secret_name": str(default_secret_name or ""),
            "vault_name": "spectral-nature-kvault",
            "reason": "",
            "error_type": "",
            "error_message": "",
        }

    monkeypatch.setattr(emailer, "describe_secret_resolution", fake_describe)

    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["username"] = username
            sent["password"] = password

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)

    result = emailer.send_email(
        to_address="client@example.com",
        subject="Invite",
        text_body="Fallback text",
        html_body="<html><body><img src='cid:sn_invite_logo'></body></html>",
        inline_images=[
            emailer.EmailInlineImage(
                content_id="sn_invite_logo",
                content=b"mock-image-bytes",
                mime_type="image/png",
                filename="logo.png",
            ),
        ],
    )

    assert result.sent is True
    assert sent.get("host") == "smtp.azurecomm.net"
    assert sent.get("username") == "smtp-user"
    assert sent.get("starttls") is None

    message = sent["message"]
    image_content_ids = [part.get("Content-ID") for part in message.walk() if part.get_content_maintype() == "image"]
    assert "<sn_invite_logo>" in image_content_ids


def test_send_email_returns_specific_status_message_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        emailer,
        "email_delivery_status",
        lambda: {
            "configured": False,
            "message": "Sender address secret `app-email-from` was not found in Key Vault `snpipelinekv03130136`.",
        },
    )

    result = emailer.send_email(
        to_address="client@example.com",
        subject="Invite",
        text_body="Fallback text",
    )

    assert result.sent is False
    assert "app-email-from" in result.message
