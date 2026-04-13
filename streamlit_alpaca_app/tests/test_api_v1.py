from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import api.main as api_main
from data_access.contracts import QueryRequest, QueryResponse
from services import api_auth, auth_service


def _user_context(email: str = "user@example.com", *, is_admin: bool = False) -> auth_service.UserContext:
    return auth_service.UserContext(
        user_id="u1",
        email=email,
        first_name="User",
        last_name="Example",
        display_name="User Example",
        role="admin" if is_admin else "investor",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="admin" if is_admin else "investor",
        share_fraction=0.2,
        can_view_full_portfolio=is_admin,
    )


class _StubQueryService:
    def list_capabilities(self) -> dict[str, dict[str, dict[str, object]]]:
        return {
            "datasets": {
                "price_history": {"params": ["ticker", "days"], "resolution": "materialized_first"}
            },
            "charts": {
                "technical_price_channel": {"params": ["ticker", "days"], "resolution": "computed"}
            },
        }

    def execute(self, request: QueryRequest) -> QueryResponse:
        query = request if isinstance(request, QueryRequest) else QueryRequest.from_dict(request)
        return QueryResponse(
            request=query,
            result_type="dataset",
            payload=[{"symbol": "AAPL"}],
            provenance=None,
        )


def _agent_principal(*scopes: str) -> api_auth.AuthPrincipal:
    return api_auth.AuthPrincipal(
        principal_type="agent",
        scopes=tuple(scopes),
        auth_source="agent_api_key",
        agent_key_id="k1",
        agent_key_name="integration-agent",
    )


def _admin_user_principal(*scopes: str) -> api_auth.AuthPrincipal:
    context = _user_context(email="admin@example.com", is_admin=True)
    return api_auth.AuthPrincipal(
        principal_type="user",
        scopes=tuple(scopes),
        auth_source="access_token",
        user_context=context,
        session_token_hash="session-hash",
    )


def test_login_returns_access_and_refresh_tokens(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(
        api_main.api_auth,
        "issue_user_tokens_from_password",
        lambda **kwargs: {
            "ok": True,
            "token_type": "bearer",
            "access_token": "access-token",
            "access_token_expires_at": "2026-04-07T00:00:00Z",
            "refresh_token": "refresh-token",
            "refresh_token_expires_at": "2026-04-14T00:00:00Z",
            "scopes": [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ],
            "context": _user_context(),
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "StrongPass123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["access_token"] == "access-token"
    assert payload["refresh_token"] == "refresh-token"
    assert payload["context"]["email"] == "user@example.com"


def test_refresh_returns_rotated_tokens(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(
        api_main.api_auth,
        "refresh_user_tokens",
        lambda **kwargs: {
            "ok": True,
            "token_type": "bearer",
            "access_token": "new-access-token",
            "access_token_expires_at": "2026-04-07T00:10:00Z",
            "refresh_token": "new-refresh-token",
            "refresh_token_expires_at": "2026-04-14T00:00:00Z",
            "scopes": [api_auth.SCOPE_QUERY_EXECUTE],
            "context": _user_context(),
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "old-refresh-token", "rotate_refresh_token": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"] == "new-access-token"
    assert payload["refresh_token"] == "new-refresh-token"


def test_dataset_endpoint_rejects_missing_scope(monkeypatch):
    monkeypatch.setattr(api_main.api_auth, "principal_from_agent_api_key", lambda token: _agent_principal(api_auth.SCOPE_MCP_INVOKE))
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/dataset/price_history",
        headers={"X-API-Key": "snak_test"},
        json={"params": {"ticker": "AAPL"}},
    )

    assert response.status_code == 403
    assert "Missing required scope" in response.json().get("detail", "")


def test_omnibar_endpoint_rejects_missing_scope(monkeypatch):
    monkeypatch.setattr(api_main.api_auth, "principal_from_agent_api_key", lambda token: _agent_principal(api_auth.SCOPE_QUERY_EXECUTE))

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/omnibar/resolve",
        headers={"X-API-Key": "snak_test"},
        json={"query": "AAPL"},
    )

    assert response.status_code == 403
    assert "Missing required scope" in response.json().get("detail", "")


def test_omnibar_endpoint_returns_resolution(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_OMNIBAR_RESOLVE),
    )
    monkeypatch.setattr(
        api_main.omnibar_service,
        "resolve_omnibar",
        lambda **kwargs: {
            "request_id": "omni_123",
            "intent": "navigate",
            "policy_version": "streamlit-agentic-omnibar-v1",
            "confidence_band": "high",
            "confidence": 1.0,
            "query_echo": kwargs["query"],
            "search_results": [
                {
                    "result_id": "sr_1",
                    "kind": "symbol",
                    "ref": "AAPL",
                    "label": "AAPL",
                    "subtitle": "Apple Inc.",
                }
            ],
            "agent_action": {"suggested_message_blocks": [{"type": "text", "text": kwargs["query"]}]},
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/omnibar/resolve",
        headers={"X-API-Key": "snak_test"},
        json={"query": "AAPL", "preferred_mode": "auto"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "omni_123"
    assert payload["intent"] == "navigate"
    assert payload["policy_version"] == "streamlit-agentic-omnibar-v1"
    assert payload["confidence_band"] == "high"
    assert payload["search_results"][0]["ref"] == "AAPL"


def test_omnibar_suggestions_endpoint_returns_suggestions(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_OMNIBAR_RESOLVE),
    )
    monkeypatch.setattr(
        api_main.omnibar_service,
        "list_omnibar_suggestions",
        lambda **kwargs: {
            "policy_version": "streamlit-agentic-omnibar-v1",
            "suggestions": [
                {
                    "kind": "macro_release",
                    "query": "cpi",
                    "label": "CPI Release",
                    "subtitle": "Inflation release context and price-level signals in Broad Economy.",
                }
            ],
        },
    )

    client = TestClient(api_main.app)
    response = client.get(
        "/v1/omnibar/suggestions",
        headers={"X-API-Key": "snak_test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_version"] == "streamlit-agentic-omnibar-v1"
    assert payload["suggestions"][0]["query"] == "cpi"


def test_protected_endpoint_fails_closed_when_auth_is_disabled(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: False)

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/dataset/price_history",
        json={"params": {"ticker": "AAPL"}},
    )

    assert response.status_code == 503
    assert "disabled for this environment" in response.json().get("detail", "")


def test_protected_endpoint_fails_closed_when_auth_backend_errors(monkeypatch):
    def _raise():
        raise RuntimeError("database offline")

    monkeypatch.setattr(api_main, "_auth_enabled", _raise)

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/dataset/price_history",
        json={"params": {"ticker": "AAPL"}},
    )

    assert response.status_code == 503
    assert "Authentication backend unavailable" in response.json().get("detail", "")


def test_agent_rpc_tools_call_executes_dataset(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(
            api_auth.SCOPE_MCP_INVOKE,
            api_auth.SCOPE_QUERY_EXECUTE,
            api_auth.SCOPE_DATASET_READ,
            api_auth.SCOPE_CAPABILITIES_READ,
        ),
    )
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/agent/rpc",
        headers={"X-API-Key": "snak_test"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.tools.call",
            "params": {
                "name": "dataset.price_history",
                "arguments": {"ticker": "AAPL", "days": 90},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    content = payload["result"]["content"]
    assert content[0]["json"]["payload"][0]["symbol"] == "AAPL"


def test_agent_tool_invoke_rejects_stringified_query_params(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(
            api_auth.SCOPE_MCP_INVOKE,
            api_auth.SCOPE_QUERY_EXECUTE,
            api_auth.SCOPE_DATASET_READ,
        ),
    )
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/agent/tools/query.execute/invoke",
        headers={"X-API-Key": "snak_test"},
        json={
            "arguments": {
                "operation": "dataset",
                "name": "fred_dashboard",
                "params": "{\"years\": 1}",
            }
        },
    )

    assert response.status_code == 400
    assert "params must be an object" in response.json()["detail"]


def test_list_agent_tools_omits_generic_query_execute(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(
            api_auth.SCOPE_MCP_INVOKE,
            api_auth.SCOPE_CAPABILITIES_READ,
        ),
    )
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.get(
        "/v1/agent/tools",
        headers={"X-API-Key": "snak_test"},
    )

    assert response.status_code == 200
    tool_names = {tool["name"] for tool in response.json()["tools"]}
    assert "query.execute" not in tool_names
    assert "dataset.price_history" in tool_names


def test_admin_can_create_agent_key(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_access_token",
        lambda token: _admin_user_principal(api_auth.SCOPE_AGENT_KEY_WRITE, api_auth.SCOPE_AGENT_KEY_READ),
    )
    monkeypatch.setattr(
        api_main.api_auth,
        "create_agent_api_key",
        lambda **kwargs: {
            "api_key": "snak_generated",
            "key": {"id": "k1", "name": kwargs.get("name"), "scopes": kwargs.get("scopes")},
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/auth/agent-keys",
        headers={"Authorization": "Bearer access-token"},
        json={"name": "automation-agent", "scopes": [api_auth.SCOPE_MCP_INVOKE, api_auth.SCOPE_QUERY_EXECUTE]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["api_key"].startswith("snak_")
    assert payload["key"]["name"] == "automation-agent"
