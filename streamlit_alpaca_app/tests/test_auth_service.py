from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

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


def test_get_auth_email_delivery_status_reports_missing_public_base_url(monkeypatch):
    monkeypatch.delenv("APP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(
        auth_service,
        "email_delivery_status",
        lambda: {
            "configured": True,
            "message": "Email delivery is configured.",
        },
    )

    status = auth_service.get_auth_email_delivery_status()

    assert status["ready"] is False
    assert status["mail_configured"] is True
    assert status["public_base_url_present"] is False
    assert "APP_PUBLIC_BASE_URL" in status["message"]


def test_browser_session_cookie_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("UI_DISABLE_BROWSER_SESSION_COOKIE", raising=False)

    assert auth_service.allow_insecure_browser_session_cookie() is True
    assert auth_service.browser_session_persistence_mode() == "browser_cookie"
    assert auth_service.browser_session_persistence_message() == ""


def test_browser_session_cookie_disabled_via_opt_out_flag(monkeypatch):
    monkeypatch.setenv("UI_DISABLE_BROWSER_SESSION_COOKIE", "1")

    assert auth_service.allow_insecure_browser_session_cookie() is False
    assert auth_service.browser_session_persistence_mode() == "session_only"
    assert "disabled" in auth_service.browser_session_persistence_message().lower()


def test_browser_session_cookie_legacy_opt_in_still_works(monkeypatch):
    monkeypatch.delenv("UI_DISABLE_BROWSER_SESSION_COOKIE", raising=False)
    monkeypatch.setenv("UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE", "1")

    assert auth_service.allow_insecure_browser_session_cookie() is True
    assert auth_service.browser_session_persistence_mode() == "browser_cookie"


def test_browser_session_cookie_legacy_explicit_zero_disables(monkeypatch):
    monkeypatch.delenv("UI_DISABLE_BROWSER_SESSION_COOKIE", raising=False)
    monkeypatch.setenv("UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE", "0")

    assert auth_service.allow_insecure_browser_session_cookie() is False


def test_complete_password_reset_requires_token_before_store_call(monkeypatch):
    called = False

    def fake_reset_password(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("reset_password should not be called without a token")

    monkeypatch.setattr(auth_service.auth_store, "reset_password", fake_reset_password)

    result = auth_service.complete_password_reset(reset_token="", new_password="StrongEnough123")

    assert result == {"ok": False, "message": "Reset token is required."}
    assert called is False


def test_complete_password_reset_does_not_create_login_session(monkeypatch):
    user_row = {
        "user_id": "user-1",
        "email": "investor@example.com",
        "first_name": "Ivy",
        "last_name": "Investor",
        "display_name": "Ivy Investor",
        "role": "investor",
        "portfolio_id": "portfolio-1",
        "portfolio_slug": "master-portfolio",
        "portfolio_name": "Master Portfolio",
        "membership_role": "viewer",
        "share_fraction": 0.25,
        "can_view_full_portfolio": False,
    }

    monkeypatch.setattr(auth_service.auth_store, "reset_password", lambda **_kwargs: user_row)
    monkeypatch.setattr(auth_service.auth_store, "record_access_event", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        auth_service.auth_store,
        "create_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("password reset must not create a session")),
    )

    result = auth_service.complete_password_reset(reset_token="valid-token", new_password="StrongEnough123")

    assert result["ok"] is True
    assert result["message"] == "Password reset complete. Please log in with your new password."
    assert "session_token" not in result


def test_authenticate_user_rejects_active_user_without_membership(monkeypatch):
    user_row = {
        "user_id": "user-1",
        "email": "investor@example.com",
        "first_name": "Ivy",
        "last_name": "Investor",
        "display_name": "Ivy Investor",
        "role": "investor",
        "status": "active",
        "portfolio_id": "",
        "portfolio_slug": "",
        "portfolio_name": "",
        "membership_role": "",
        "share_fraction": 0.0,
        "can_view_full_portfolio": False,
        "password_hash": auth_service.hash_password("StrongEnough123"),
        "locked_until": None,
    }
    events: list[dict[str, object]] = []

    monkeypatch.setattr(auth_service.auth_store, "get_user_for_login", lambda email: user_row)
    monkeypatch.setattr(auth_service.auth_store, "record_access_event", lambda **kwargs: events.append(dict(kwargs)) or {"ok": True})
    monkeypatch.setattr(
        auth_service.auth_store,
        "create_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("missing membership must not create a session")),
    )

    result = auth_service.authenticate_user(email="investor@example.com", password="StrongEnough123")

    assert result == {"ok": False, "message": "Account is missing an active portfolio membership."}
    assert events[-1]["detail"] == {"reason": "missing_active_membership"}


def test_issue_invite_returns_specific_delivery_message_when_sender_secret_missing(monkeypatch):
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

    monkeypatch.setattr(auth_service, "generate_token", lambda: "invite-token")
    monkeypatch.setattr(
        auth_service.auth_store,
        "insert_invite",
        lambda **kwargs: {"id": "invite-1", "email": kwargs["email"], "role": kwargs["role"]},
    )
    monkeypatch.setattr(
        auth_service,
        "get_auth_email_delivery_status",
        lambda **kwargs: {
            "ready": False,
            "configured": False,
            "mail_configured": False,
            "public_base_url_present": True,
            "message": "Sender address secret `app-email-from` was not found in Key Vault `snpipelinekv03130136`.",
            "user_message": "Email delivery is not available right now. Contact an administrator for a reset link.",
            "mail_status": {},
        },
    )

    result = auth_service.issue_invite(
        email="investor@example.com",
        role="investor",
        share_fraction=0.25,
        created_by=admin,
        base_url="https://torres-cap.com",
    )

    assert result["ok"] is True
    assert result["email_sent"] is False
    assert "app-email-from" in str(result["email_message"])
    assert result["invite_url"] == "https://torres-cap.com/?invite_token=invite-token"


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


def test_update_pending_invite_requires_admin():
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

    result = auth_service.update_pending_invite(
        invite_id="invite-123",
        share_fraction=0.2,
        requested_by=viewer,
    )

    assert result["ok"] is False
    assert "admins" in str(result["message"]).lower()


def test_update_pending_invite_calls_store(monkeypatch):
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

    called: dict[str, object] = {}

    def fake_update_pending_invite(*, invite_id: str, role: str | None = None, proposed_share_fraction: float | None = None, updated_by: str | None = None):
        called["invite_id"] = invite_id
        called["role"] = role
        called["proposed_share_fraction"] = proposed_share_fraction
        called["updated_by"] = updated_by
        return {
            "id": invite_id,
            "role": "investor",
            "proposed_share_fraction": proposed_share_fraction,
            "status": "pending",
        }

    monkeypatch.setattr(auth_service.auth_store, "update_pending_invite", fake_update_pending_invite)

    result = auth_service.update_pending_invite(
        invite_id="invite-123",
        share_fraction=0.275,
        requested_by=admin,
    )

    assert result["ok"] is True
    assert called["invite_id"] == "invite-123"
    assert called["role"] is None
    assert called["proposed_share_fraction"] == 0.275
    assert called["updated_by"] == "u-admin"


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


def test_resend_pending_invite_requires_admin():
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

    result = auth_service.resend_pending_invite(invite_id="invite-123", requested_by=viewer)

    assert result["ok"] is False
    assert "admins" in str(result["message"]).lower()


def test_resend_pending_invite_reissues_selected_invite(monkeypatch):
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

    monkeypatch.setattr(
        auth_service.auth_store,
        "list_pending_invites",
        lambda: [
            {
                "id": "invite-123",
                "email": "investor@example.com",
                "role": "investor",
                "proposed_share_fraction": 0.125,
                "status": "pending",
            }
        ],
    )

    called: dict[str, object] = {}

    def fake_issue_invite(**kwargs):
        called.update(kwargs)
        return {
            "ok": True,
            "invite": {"id": "invite-456"},
            "invite_url": "https://torres-cap.com/?invite_token=abc123",
            "email_sent": True,
            "email_message": "Email sent to investor@example.com.",
        }

    monkeypatch.setattr(auth_service, "issue_invite", fake_issue_invite)

    result = auth_service.resend_pending_invite(
        invite_id="invite-123",
        requested_by=admin,
        base_url="https://torres-cap.com",
    )

    assert result["ok"] is True
    assert result["message"] == "Invite resent."
    assert called["email"] == "investor@example.com"
    assert called["role"] == "investor"
    assert called["share_fraction"] == 0.125
    assert called["created_by"] == admin
    assert called["base_url"] == "https://torres-cap.com"


def test_record_access_event_hashes_session_token_before_store(monkeypatch):
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

    captured: dict[str, object] = {}

    def fake_record_access_event(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(auth_service.auth_store, "record_access_event", fake_record_access_event)

    auth_service.record_access_event(
        event_type="section_view",
        event_category="usage",
        user=admin,
        section_name="Home",
        session_token="session-token",
        ip_address="203.0.113.10",
        user_agent="pytest-agent",
        detail={"app_track": "dev"},
    )

    assert captured["user_id"] == "u-admin"
    assert captured["email"] == "admin@example.com"
    assert captured["section_name"] == "Home"
    assert captured["session_token_hash"] == auth_service.token_digest("session-token")
    assert captured["event_type"] == "section_view"
    assert captured["event_category"] == "usage"


def test_authenticate_user_records_login_success_event(monkeypatch):
    context = auth_service.UserContext(
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
    user_row = {
        **context.to_dict(),
        "status": "active",
        "password_hash": auth_service.hash_password("VeryStrongPass123"),
        "locked_until": None,
    }

    events: list[dict[str, object]] = []

    monkeypatch.setattr(auth_service.auth_store, "get_user_for_login", lambda email: user_row)
    monkeypatch.setattr(auth_service.auth_store, "clear_failed_login", lambda user_id: None)
    monkeypatch.setattr(auth_service.auth_store, "create_session", lambda **kwargs: {"id": "session-1"})
    monkeypatch.setattr(auth_service.auth_store, "record_access_event", lambda **kwargs: events.append(dict(kwargs)) or {"ok": True})
    monkeypatch.setattr(auth_service, "generate_token", lambda: "session-token")

    result = auth_service.authenticate_user(
        email="admin@example.com",
        password="VeryStrongPass123",
        user_agent="pytest-agent",
        ip_address="203.0.113.10",
    )

    assert result["ok"] is True
    assert result["session_token"] == "session-token"
    assert len(events) == 1
    assert events[0]["event_type"] == "login_success"
    assert events[0]["event_category"] == "usage"
    assert events[0]["session_token_hash"] == auth_service.token_digest("session-token")


def test_authenticate_user_records_failed_login_and_lock_events(monkeypatch):
    context = auth_service.UserContext(
        user_id="u-investor",
        email="investor@example.com",
        first_name="Investor",
        last_name="User",
        display_name="Investor User",
        role="investor",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="investor",
        share_fraction=0.25,
        can_view_full_portfolio=False,
    )
    user_row = {
        **context.to_dict(),
        "status": "active",
        "password_hash": auth_service.hash_password("CorrectHorse123"),
        "locked_until": None,
    }
    locked_until = datetime.now(timezone.utc) + timedelta(hours=1)
    events: list[dict[str, object]] = []

    monkeypatch.setattr(auth_service.auth_store, "get_user_for_login", lambda email: user_row)
    monkeypatch.setattr(
        auth_service.auth_store,
        "record_failed_login",
        lambda *args, **kwargs: {"failed_login_count": 5, "locked_until": locked_until},
    )
    monkeypatch.setattr(auth_service.auth_store, "record_access_event", lambda **kwargs: events.append(dict(kwargs)) or {"ok": True})

    result = auth_service.authenticate_user(
        email="investor@example.com",
        password="WrongPassword123",
        user_agent="pytest-agent",
        ip_address="203.0.113.77",
    )

    assert result["ok"] is False
    assert [event["event_type"] for event in events] == ["login_failed", "login_locked"]
    assert events[0]["detail"]["failed_login_count"] == 5
    assert events[1]["detail"]["locked_until"] == locked_until.isoformat()


def test_get_access_admin_dashboard_delegates_to_store(monkeypatch):
    monkeypatch.setattr(
        auth_service.auth_store,
        "get_access_admin_dashboard",
        lambda **kwargs: {"summary": {"total_users": 3}, "kwargs": kwargs},
    )
    monkeypatch.setattr(
        auth_service.admin_security_status,
        "get_admin_cloud_security_status",
        lambda: {"available": True, "summary": {"healthy_count": 4}},
    )

    payload = auth_service.get_access_admin_dashboard(
        usage_window_days=30,
        security_window_days=7,
        active_window_minutes=60,
        sankey_user_limit=5,
        user_id="user-1",
        user_email="ivy@example.com",
    )

    assert payload["summary"]["total_users"] == 3
    assert payload["kwargs"]["usage_window_days"] == 30
    assert payload["kwargs"]["security_window_days"] == 7
    assert payload["kwargs"]["active_window_minutes"] == 60
    assert payload["kwargs"]["sankey_user_limit"] == 5
    assert payload["kwargs"]["user_id"] == "user-1"
    assert payload["kwargs"]["user_email"] == "ivy@example.com"
    assert payload["cloud_security_status"]["summary"]["healthy_count"] == 4
