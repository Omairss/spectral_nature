from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import api_auth, auth_service


def _context() -> auth_service.UserContext:
    return auth_service.UserContext(
        user_id="u1",
        email="user@example.com",
        first_name="User",
        last_name="Example",
        display_name="User Example",
        role="investor",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="investor",
        share_fraction=0.3,
        can_view_full_portfolio=False,
    )


def test_issue_and_resolve_access_token_roundtrip(monkeypatch):
    context = _context()

    monkeypatch.setattr(api_auth, "_access_token_secret", lambda: "test-secret")
    monkeypatch.setattr(
        auth_service,
        "authenticate_user",
        lambda **kwargs: {
            "ok": True,
            "context": context,
            "session_token": "refresh-token",
        },
    )
    monkeypatch.setattr(
        api_auth.auth_store,
        "get_user_context_for_session",
        lambda token_hash: {
            **context.to_dict(),
            "expires_at": datetime.now(timezone.utc),
        },
    )

    issued = api_auth.issue_user_tokens_from_password(email="user@example.com", password="StrongPass123")

    assert issued["ok"] is True
    assert str(issued.get("access_token") or "").count(".") == 2

    principal = api_auth.principal_from_access_token(str(issued.get("access_token") or ""))
    assert principal is not None
    assert principal.principal_type == "user"
    assert principal.user_context is not None
    assert principal.user_context.email == "user@example.com"
    assert api_auth.SCOPE_QUERY_EXECUTE in set(principal.scopes)
    assert api_auth.SCOPE_OMNIBAR_RESOLVE in set(principal.scopes)


def test_agent_key_scopes_are_normalized(monkeypatch):
    monkeypatch.setattr(
        api_auth.auth_store,
        "create_agent_api_key",
        lambda **kwargs: {
            "id": "k1",
            "name": kwargs["name"],
            "key_prefix": kwargs["key_prefix"],
            "scopes": kwargs["scopes"],
            "status": "active",
            "expires_at": kwargs.get("expires_at"),
            "revoked_at": None,
            "created_by": kwargs.get("created_by") or "",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "last_used_at": None,
            "notes": kwargs.get("notes") or "",
        },
    )
    created = api_auth.create_agent_api_key(
        name="agent-one",
        scopes=[
            "not-allowed",
            api_auth.SCOPE_MCP_INVOKE,
            api_auth.SCOPE_QUERY_EXECUTE,
            api_auth.SCOPE_OMNIBAR_RESOLVE,
        ],
    )
    assert created["api_key"].startswith("snak_")
    assert created["key"]["scopes"] == [
        api_auth.SCOPE_MCP_INVOKE,
        api_auth.SCOPE_OMNIBAR_RESOLVE,
        api_auth.SCOPE_QUERY_EXECUTE,
    ]


def test_access_token_secret_requires_dedicated_secret(monkeypatch):
    monkeypatch.setattr(api_auth, "resolve_secret_value", lambda *args, **kwargs: "")
    monkeypatch.delenv("API_ALLOW_EPHEMERAL_ACCESS_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "legacy-password-should-not-be-used")

    with pytest.raises(RuntimeError, match="API access token signing secret is not configured"):
        api_auth._access_token_secret()


def test_access_token_secret_can_use_ephemeral_fallback_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(api_auth, "resolve_secret_value", lambda *args, **kwargs: "")
    monkeypatch.setenv("API_ALLOW_EPHEMERAL_ACCESS_TOKEN_SECRET", "1")

    assert api_auth._access_token_secret()


def test_legacy_session_bearer_is_disabled_by_default(monkeypatch):
    context = _context()
    monkeypatch.delenv("API_ALLOW_LEGACY_SESSION_BEARER", raising=False)
    monkeypatch.setattr(
        api_auth.auth_store,
        "get_user_context_for_session",
        lambda token_hash: {
            **context.to_dict(),
            "expires_at": datetime.now(timezone.utc),
        },
    )

    principal = api_auth.principal_from_legacy_session_token("refresh-token")

    assert principal is None


def test_legacy_session_bearer_requires_explicit_opt_in(monkeypatch):
    context = _context()
    monkeypatch.setenv("API_ALLOW_LEGACY_SESSION_BEARER", "1")
    monkeypatch.setattr(
        api_auth.auth_store,
        "get_user_context_for_session",
        lambda token_hash: {
            **context.to_dict(),
            "expires_at": datetime.now(timezone.utc),
        },
    )

    principal = api_auth.principal_from_legacy_session_token("refresh-token")

    assert principal is not None
    assert principal.auth_source == "legacy_session_token"
