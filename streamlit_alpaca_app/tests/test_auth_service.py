from __future__ import annotations

import base64
from datetime import datetime, timezone

from services import auth_service


def test_hash_password_round_trip():
    password = "VeryStrongPass123"

    encoded = auth_service.hash_password(password)

    assert encoded.startswith("scrypt$")
    assert auth_service.verify_password(password, encoded) is True
    assert auth_service.verify_password("wrong-password", encoded) is False


def test_validate_password_strength_requires_length_and_character_mix():
    assert auth_service.validate_password_strength("short") == "Password must be at least 12 characters."
    assert auth_service.validate_password_strength("alllowercase123") == "Password must include an uppercase letter."
    assert auth_service.validate_password_strength("ALLUPPERCASE123") == "Password must include a lowercase letter."
    assert auth_service.validate_password_strength("NoDigitsHere!!") == "Password must include a number."
    assert auth_service.validate_password_strength("StrongEnough123") == ""


def test_build_action_link_uses_relative_path_without_base_url():
    link = auth_service.build_action_link(token_name="invite_token", token="abc123", base_url="")

    assert link == "?invite_token=abc123"


def test_user_context_admin_detection_prefers_full_access_flag():
    context = auth_service.UserContext(
        user_id="u1",
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        display_name="Admin User",
        role="investor",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="viewer",
        share_fraction=0.0,
        can_view_full_portfolio=True,
    )

    assert context.is_admin is True
    assert context.label == "Admin User"


def test_invite_email_html_includes_branding_logo_graph_and_cta():
    expires_at = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    html_body = auth_service._invite_email_html(
        invite_url="https://torres-cap.com/?invite_token=abc123",
        expires_at=expires_at,
        recipient_email="client@example.com",
        role="investor",
        logo_src="cid:sn_invite_logo",
        graph_src="cid:sn_invite_graph",
        theme=auth_service.default_invite_email_theme(),
    )

    assert "Spectral Nature" in html_body
    assert "by Torres Capital" in html_body
    assert "cid:sn_invite_logo" in html_body
    assert "cid:sn_invite_graph" in html_body
    assert "Activate account" in html_body


def test_invite_email_text_contains_link_and_expiry():
    expires_at = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    text_body = auth_service._invite_email_text(
        invite_url="https://torres-cap.com/?invite_token=abc123",
        expires_at=expires_at,
        theme=auth_service.default_invite_email_theme(),
    )

    assert "https://torres-cap.com/?invite_token=abc123" in text_body
    assert "2026-04-06 12:00 UTC" in text_body


def test_sanitize_invite_email_theme_normalizes_invalid_values():
    theme = auth_service.sanitize_invite_email_theme(
        {
            "headline": "Welcome Aboard",
            "background_color": "not-a-color",
            "button_color": "112233",
            "show_graph": "false",
        }
    )

    assert theme["headline"] == "Welcome Aboard"
    assert theme["background_color"] == auth_service.default_invite_email_theme()["background_color"]
    assert theme["button_color"] == "#112233"
    assert theme["show_graph"] is False


def test_invite_email_template_library_defaults_to_dark_active(monkeypatch):
    monkeypatch.setattr(auth_service.auth_store, "get_app_setting", lambda *_args, **_kwargs: None)
    library = auth_service.get_invite_email_template_library()

    assert library["active_template_id"] == auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID
    templates = {str(template.get("template_id")): template for template in library["templates"]}
    assert auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID in templates
    assert auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID in templates
    assert templates[auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID]["logo_variant"] == "white"
    assert templates[auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID]["chart_asset"]["name"] == "dark"
    assert templates[auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID]["logo_variant"] == "color"


def test_active_template_defaults_to_dark_logo_and_chart(monkeypatch):
    monkeypatch.setattr(auth_service.auth_store, "get_app_setting", lambda *_args, **_kwargs: None)
    template = auth_service.get_active_invite_email_template()

    assert template["template_id"] == auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID
    assert template["logo_variant"] == "white"
    assert template["chart_asset"]["kind"] == "builtin"
    assert template["chart_asset"]["name"] == "dark"


def test_invite_email_template_save_load_delete_roundtrip(monkeypatch):
    settings_store: dict[str, object] = {}

    def fake_get(setting_key: str):
        if setting_key in settings_store:
            return {"key": setting_key, "value": settings_store[setting_key]}
        return None

    def fake_set(setting_key: str, value, *, updated_by: str | None = None):
        del updated_by
        settings_store[setting_key] = value
        return {"key": setting_key, "value": value}

    monkeypatch.setattr(auth_service.auth_store, "get_app_setting", fake_get)
    monkeypatch.setattr(auth_service.auth_store, "set_app_setting", fake_set)

    save_result = auth_service.save_invite_email_template(
        template_name="Client Dark Alt",
        theme={"headline": "Custom Headline", "background_color": "#090f1b"},
        logo_variant="white",
        chart_asset={"kind": "builtin", "name": "dark"},
        template_id=None,
    )

    assert save_result["created"] is True
    template = save_result["template"]
    assert isinstance(template, dict)
    template_id = str(template.get("template_id") or "")
    assert template_id not in {auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID, auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID}

    library = auth_service.get_invite_email_template_library()
    ids = {str(item.get("template_id") or "") for item in library["templates"]}
    assert template_id in ids

    active_result = auth_service.set_active_invite_email_template(auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID)
    assert active_result["active_template_id"] == auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID

    delete_result = auth_service.delete_invite_email_template(template_id)
    assert delete_result["ok"] is True
    assert delete_result["active_template_id"] == auth_service.INVITE_EMAIL_TEMPLATE_WHITE_ID

    delete_builtin_result = auth_service.delete_invite_email_template(auth_service.INVITE_EMAIL_TEMPLATE_DARK_ID)
    assert delete_builtin_result["ok"] is False


def test_invite_email_preview_supports_uploaded_gif_chart(monkeypatch):
    monkeypatch.setattr(auth_service.auth_store, "get_app_setting", lambda *_args, **_kwargs: None)
    gif_payload = base64.b64encode(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    ).decode("ascii")
    template_override = {
        "name": "Upload Test",
        "theme": auth_service.default_invite_email_theme(),
        "logo_variant": "color",
        "chart_asset": {
            "kind": "upload",
            "filename": "chart.gif",
            "mime_type": "image/gif",
            "data_b64": gif_payload,
        },
    }

    preview = auth_service.build_invite_email_preview(
        invite_url="https://torres-cap.com/?invite_token=abc123",
        recipient_email="client@example.com",
        role="investor",
        expires_at=datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc),
        template_override=template_override,
    )

    html_body = str(preview.get("html_body") or "")
    assert "data:image/gif;base64," in html_body


def test_delete_pending_invite_requires_admin():
    viewer = auth_service.UserContext(
        user_id="u-viewer",
        email="viewer@example.com",
        first_name="View",
        last_name="Only",
        display_name="View Only",
        role="viewer",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="viewer",
        share_fraction=0.0,
        can_view_full_portfolio=False,
    )

    result = auth_service.delete_pending_invite(invite_id="abc", requested_by=viewer)

    assert result["ok"] is False
    assert "admins" in str(result["message"]).lower()


def test_delete_pending_invite_calls_store(monkeypatch):
    admin = auth_service.UserContext(
        user_id="u-admin",
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        display_name="Admin User",
        role="admin",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="admin",
        share_fraction=0.0,
        can_view_full_portfolio=True,
    )

    called: dict[str, str] = {}

    def fake_delete_pending_invite(*, invite_id: str, deleted_by: str | None = None):
        called["invite_id"] = invite_id
        called["deleted_by"] = str(deleted_by or "")
        return {"id": invite_id, "status": "revoked"}

    monkeypatch.setattr(auth_service.auth_store, "delete_pending_invite", fake_delete_pending_invite)

    result = auth_service.delete_pending_invite(invite_id="invite-123", requested_by=admin)

    assert result["ok"] is True
    assert called["invite_id"] == "invite-123"
    assert called["deleted_by"] == "u-admin"
