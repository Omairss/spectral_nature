from __future__ import annotations

from services import emailer


def test_email_delivery_configured_supports_default_secret_names(monkeypatch):
    monkeypatch.setenv("APP_SMTP_HOST", "smtp.azurecomm.net")
    monkeypatch.delenv("APP_EMAIL_FROM", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.delenv("APP_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("APP_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    def fake_resolve(env_names, *, secret_name_env=None, default_secret_name=None, placeholders=None):
        values = {
            "app-email-from": "noreply@example.com",
            "app-smtp-username": "smtp-user",
            "app-smtp-password": "smtp-pass",
        }
        return values.get(default_secret_name, "")

    monkeypatch.setattr(emailer, "resolve_secret_value", fake_resolve)

    assert emailer._smtp_username() == "smtp-user"
    assert emailer._smtp_password() == "smtp-pass"
    assert emailer.email_delivery_configured() is True
