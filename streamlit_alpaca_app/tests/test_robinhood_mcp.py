from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
import pytest

from services import robinhood_mcp


@pytest.fixture(autouse=True)
def _robinhood_environment(monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "http://localhost:8501")
    monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.delenv("ROBINHOOD_MCP_URL", raising=False)
    robinhood_mcp._cipher.cache_clear()
    yield
    robinhood_mcp._cipher.cache_clear()


def _oauth_metadata() -> dict[str, object]:
    return {
        "resource": robinhood_mcp.DEFAULT_RESOURCE_URL,
        "scope": "internal",
        "issuer": robinhood_mcp.DEFAULT_RESOURCE_URL,
        "authorization_endpoint": "https://robinhood.com/oauth",
        "token_endpoint": "https://api.robinhood.com/oauth2/token/",
        "registration_endpoint": "https://agent.robinhood.com/oauth/trading/register",
        "authorization_server": {},
        "protected_resource": {},
    }


def test_begin_authorization_persists_one_time_state_and_encrypted_pkce(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(robinhood_mcp, "discover_oauth_metadata", _oauth_metadata)
    monkeypatch.setattr(
        robinhood_mcp,
        "_client_registration",
        lambda **kwargs: {"id": "11111111-1111-1111-1111-111111111111", "client_id": "client-123"},
    )

    def _capture_flow(**kwargs):
        captured.update(kwargs)
        return {"id": "flow-1"}

    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "create_oauth_flow", _capture_flow)

    result = robinhood_mcp.begin_authorization(
        user_id="22222222-2222-2222-2222-222222222222"
    )

    params = parse_qs(urlparse(result["authorization_url"]).query)
    state = params["state"][0]
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://localhost:8501/?robinhood_oauth=callback"]
    assert params["resource"] == [robinhood_mcp.DEFAULT_RESOURCE_URL]
    assert captured["state_hash"] == hashlib.sha256(state.encode("utf-8")).hexdigest()
    assert captured["environment"] == "local"
    encrypted_verifier = str(captured["code_verifier_ciphertext"])
    assert encrypted_verifier
    assert robinhood_mcp._decrypt(encrypted_verifier)
    assert robinhood_mcp._decrypt(encrypted_verifier) not in result["authorization_url"]


def test_complete_authorization_encrypts_tokens_and_discovers_tools(monkeypatch):
    saved: dict[str, object] = {}
    flow = {
        "id": "flow-1",
        "connection_id": "33333333-3333-3333-3333-333333333333",
        "issuer": robinhood_mcp.DEFAULT_RESOURCE_URL,
        "token_endpoint": "https://api.robinhood.com/oauth2/token/",
        "redirect_uri": "http://localhost:8501/?robinhood_oauth=callback",
        "resource_url": robinhood_mcp.DEFAULT_RESOURCE_URL,
        "code_verifier_ciphertext": robinhood_mcp._encrypt("verifier"),
    }
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "consume_oauth_flow",
        lambda **kwargs: dict(flow),
    )
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "get_connection",
        lambda **kwargs: {"client_id": "client-123"},
    )
    monkeypatch.setattr(
        robinhood_mcp,
        "_exchange_authorization_code",
        lambda **kwargs: {
            "access_token": "access-plain",
            "refresh_token": "refresh-plain",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "internal",
        },
    )
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "save_connection_tokens",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "finish_oauth_flow", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "mark_connection_error", lambda **kwargs: None)
    monkeypatch.setattr(
        robinhood_mcp,
        "refresh_connection",
        lambda **kwargs: {"tool_count": 52, "server_info": {"name": "Robinhood"}},
    )

    result = robinhood_mcp.complete_authorization(
        user_id="22222222-2222-2222-2222-222222222222",
        code="authorization-code",
        state="state-value",
        issuer=robinhood_mcp.DEFAULT_RESOURCE_URL,
    )

    assert result["status"] == "connected"
    assert result["tool_count"] == 52
    assert saved["access_token_ciphertext"] != "access-plain"
    assert saved["refresh_token_ciphertext"] != "refresh-plain"
    assert robinhood_mcp._decrypt(saved["access_token_ciphertext"]) == "access-plain"
    assert robinhood_mcp._decrypt(saved["refresh_token_ciphertext"]) == "refresh-plain"


def test_complete_authorization_rejects_issuer_mixup(monkeypatch):
    flow = {
        "id": "flow-1",
        "connection_id": "33333333-3333-3333-3333-333333333333",
        "issuer": robinhood_mcp.DEFAULT_RESOURCE_URL,
    }
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "consume_oauth_flow",
        lambda **kwargs: dict(flow),
    )
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "finish_oauth_flow", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "mark_connection_error", lambda **kwargs: None)

    with pytest.raises(robinhood_mcp.RobinhoodMCPAuthorizationError, match="issuer"):
        robinhood_mcp.complete_authorization(
            user_id="22222222-2222-2222-2222-222222222222",
            code="authorization-code",
            state="state-value",
            issuer="https://attacker.example/mcp",
        )


def test_execution_tool_cannot_run_through_generic_invoker():
    with pytest.raises(robinhood_mcp.RobinhoodMCPPolicyError, match="requires intent 'execution'"):
        robinhood_mcp.invoke_tool(
            user_id="user-1",
            tool_name="place_equity_order",
            arguments={},
            intent="foreground_read",
        )
    with pytest.raises(robinhood_mcp.RobinhoodMCPPolicyError, match="reviewed-order confirmation"):
        robinhood_mcp.invoke_tool(
            user_id="user-1",
            tool_name="place_equity_order",
            arguments={},
            intent="execution",
        )


def test_unknown_remote_tool_fails_closed():
    assert robinhood_mcp.classify_tool("new_surprise_order") == "unknown"
    with pytest.raises(robinhood_mcp.RobinhoodMCPPolicyError, match="not enabled"):
        robinhood_mcp.invoke_tool(
            user_id="user-1",
            tool_name="new_surprise_order",
            arguments={},
        )


def test_connection_status_never_returns_token_ciphertext(monkeypatch):
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "get_connection",
        lambda **kwargs: {
            "status": "connected",
            "access_token_ciphertext": "ciphertext",
            "refresh_token_ciphertext": "ciphertext",
            "tool_catalog": [{"name": "get_accounts"}],
            "token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "server_info": {"name": "Robinhood"},
        },
    )

    status = robinhood_mcp.connection_status(user_id="user-1")

    assert status["status"] == "connected"
    assert status["tool_count"] == 1
    assert "access_token_ciphertext" not in status
    assert "refresh_token_ciphertext" not in status


def test_account_identifiers_are_hashed_in_audit_arguments():
    redacted = robinhood_mcp._redacted_arguments(
        {"account_number": "123456789", "symbol": "AAPL", "access_token": "secret"}
    )
    assert redacted["account_number"].startswith("account:")
    assert "123456789" not in redacted["account_number"]
    assert redacted["symbol"] == "AAPL"
    assert redacted["access_token"] == "[redacted]"


def _equity_tool_catalog() -> list[dict[str, object]]:
    review_schema = {
        "type": "object",
        "required": ["account_number", "side", "symbol", "type", "market_hours", "time_in_force"],
        "properties": {
            key: {"type": "string"}
            for key in (
                "account_number",
                "side",
                "symbol",
                "type",
                "quantity",
                "dollar_amount",
                "limit_price",
                "stop_price",
                "market_hours",
                "time_in_force",
            )
        },
        "additionalProperties": False,
    }
    placement_schema = {
        **review_schema,
        "required": [*review_schema["required"], "ref_id"],
        "properties": {**review_schema["properties"], "ref_id": {"type": "string"}},
    }
    return [
        {"name": "review_equity_order", "inputSchema": review_schema},
        {"name": "place_equity_order", "inputSchema": placement_schema},
    ]


def _market_buy() -> dict[str, str]:
    return {
        "account_number": "123456789",
        "side": "buy",
        "symbol": "AAPL",
        "type": "market",
        "quantity": "1.25",
        "market_hours": "regular_hours",
        "time_in_force": "gfd",
    }


def test_review_creates_payload_bound_ticket_after_provider_review(monkeypatch):
    monkeypatch.setenv("ROBINHOOD_EXECUTION_ENABLED", "true")
    connection = {
        "id": "33333333-3333-3333-3333-333333333333",
        "tool_catalog": _equity_tool_catalog(),
    }
    monkeypatch.setattr(robinhood_mcp, "_access_token_for_user", lambda user_id: (connection, "token"))
    monkeypatch.setattr(
        robinhood_mcp,
        "invoke_tool",
        lambda **kwargs: {"isError": False, "structuredContent": {"estimated_cost": "250.00"}},
    )
    captured: dict[str, object] = {}

    def _create_ticket(**kwargs):
        captured.update(kwargs)
        return {"id": "44444444-4444-4444-4444-444444444444", "expires_at": kwargs["expires_at"]}

    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "create_order_ticket", _create_ticket)

    reviewed = robinhood_mcp.review_equity_order(user_id="user-1", arguments=_market_buy())

    assert reviewed["ticket_id"] == "44444444-4444-4444-4444-444444444444"
    assert reviewed["review"] == {"estimated_cost": "250.00"}
    assert captured["arguments_hash"] == robinhood_mcp._arguments_hash(_market_buy())
    assert captured["request_summary"]["account"] == "••••6789"
    assert "account_number" not in captured["request_summary"]


def test_confirmation_consumes_ticket_once_and_reuses_ticket_ref_id(monkeypatch):
    monkeypatch.setenv("ROBINHOOD_EXECUTION_ENABLED", "true")
    connection = {
        "id": "33333333-3333-3333-3333-333333333333",
        "tool_catalog": _equity_tool_catalog(),
    }
    monkeypatch.setattr(robinhood_mcp, "_access_token_for_user", lambda user_id: (connection, "token"))
    consumed: dict[str, object] = {}

    def _consume(**kwargs):
        consumed.update(kwargs)
        return {
            "id": "44444444-4444-4444-4444-444444444444",
            "connection_id": connection["id"],
            "ref_id": "55555555-5555-4555-8555-555555555555",
        }

    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "consume_order_ticket", _consume)
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "finish_order_ticket", lambda **kwargs: None)
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "start_audit_event", lambda **kwargs: "audit-1")
    monkeypatch.setattr(robinhood_mcp.brokerage_mcp_store, "finish_audit_event", lambda **kwargs: None)
    placed: dict[str, object] = {}

    async def _place(access_token, tool_name, arguments):
        placed.update(arguments)
        return {"isError": False, "structuredContent": {"state": "queued"}}

    monkeypatch.setattr(robinhood_mcp, "_call_tool_async", _place)

    outcome = robinhood_mcp.confirm_equity_order(
        user_id="user-1",
        ticket_id="44444444-4444-4444-4444-444444444444",
        arguments=_market_buy(),
    )

    assert outcome["status"] == "executed"
    assert placed["ref_id"] == "55555555-5555-4555-8555-555555555555"
    assert consumed["arguments_hash"] == robinhood_mcp._arguments_hash(_market_buy())


def test_confirmation_fails_closed_when_ticket_is_changed_expired_or_used(monkeypatch):
    monkeypatch.setenv("ROBINHOOD_EXECUTION_ENABLED", "true")
    connection = {
        "id": "33333333-3333-3333-3333-333333333333",
        "tool_catalog": _equity_tool_catalog(),
    }
    monkeypatch.setattr(robinhood_mcp, "_access_token_for_user", lambda user_id: (connection, "token"))
    monkeypatch.setattr(
        robinhood_mcp.brokerage_mcp_store,
        "consume_order_ticket",
        lambda **kwargs: None,
    )

    with pytest.raises(robinhood_mcp.RobinhoodMCPPolicyError, match="expired, changed, or already used"):
        robinhood_mcp.confirm_equity_order(
            user_id="user-1",
            ticket_id="44444444-4444-4444-4444-444444444444",
            arguments={**_market_buy(), "quantity": "2"},
        )


def test_only_agent_enabled_accounts_are_available_for_orders():
    accounts = robinhood_mcp._agent_enabled_accounts(
        {
            "accounts": [
                {"account_number": "11112222", "account_type": "individual", "agentic_allowed": True},
                {"account_number": "33334444", "account_type": "ira", "agentic_allowed": False},
            ]
        }
    )

    assert accounts == [{"account_number": "11112222", "label": "individual ••••2222"}]
