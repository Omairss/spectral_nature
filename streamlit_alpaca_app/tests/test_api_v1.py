from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import api.main as api_main
from data_access.contracts import (
    ChartModel,
    ChartTraceModel,
    DataProvenance,
    QueryRequest,
    QueryResponse,
    ResolvedPayload,
)
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

    def build_chart(self, name: str, params: dict[str, object]) -> ResolvedPayload:
        return ResolvedPayload(
            payload=ChartModel(
                chart_id=name,
                title="AAPL Price Channel",
                datasets={"primary": [{"timestamp": "2026-04-01T00:00:00", "close": 100.0}]},
                traces=[ChartTraceModel(trace_type="line", name="Close", x="timestamp", y="close")],
                metadata={"ticker": str(params.get("ticker") or "")},
            ),
            provenance=DataProvenance(
                mode="materialized",
                datasets=("technical_signal_history",),
                details={"dataset_version_id": "tech-v1"},
            ),
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


def test_recovery_public_response_never_exposes_token_or_email_diagnostics(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(api_main.auth_service, "request_password_reset", lambda **_kwargs: {
        "ok": True, "reset_url": "private-token", "email_message": "SMTP password diagnostic", "email_sent": True,
    })
    response = TestClient(api_main.app).post("/v1/auth/password-reset/request", json={"email": "user@example.com"})
    assert response.json() == {"ok": True, "message": "Reset instructions requested."}
    assert response.headers["cache-control"] == "no-store"


def test_activation_exchanges_the_existing_native_session_without_exposing_context(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(api_main.auth_service, "accept_invite", lambda **_kwargs: {"ok": True, "session_token": "native-session", "context": _user_context()})
    def exchange(**kwargs):
        assert kwargs == {"refresh_token": "native-session", "rotate_refresh_token": False}
        return {"ok": True, "access_token": "access", "refresh_token": "refresh", "access_token_expires_at": "2026-10-07T09:00:00Z", "refresh_token_expires_at": "2026-11-07T09:00:00Z"}
    monkeypatch.setattr(api_main.api_auth, "refresh_user_tokens", exchange)
    response = TestClient(api_main.app).post("/v1/auth/invites/accept", json={"invite_token": "a"*43, "first_name": "A", "last_name": "B", "password": "StrongEnough123"})
    assert response.status_code == 200
    assert response.json()["refresh_token"] == "refresh"
    assert "context" not in response.json()


def test_password_reset_completion_drops_private_context_and_preserves_outage_status(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(api_main.auth_service, "complete_password_reset", lambda **_kwargs: {"ok": True, "context": _user_context()})
    client = TestClient(api_main.app)
    body = {"reset_token": "a"*43, "new_password": "StrongEnough123"}
    assert client.post("/v1/auth/password-reset/complete", json=body).json() == {"ok": True}
    monkeypatch.setattr(api_main.auth_service, "complete_password_reset", lambda **_kwargs: {"ok": False, "unavailable": True, "message": "Password reset is unavailable."})
    assert client.post("/v1/auth/password-reset/complete", json=body).status_code == 503


def test_public_auth_status_omits_secret_diagnostics(monkeypatch):
    monkeypatch.setattr(
        api_main.auth_service,
        "initialize_auth_system",
        lambda **_kwargs: {
            "available": True,
            "ready": True,
            "has_users": True,
            "bootstrapped": False,
            "email_delivery": True,
            "email_delivery_status": {
                "mail_status": {
                    "smtp_username": {"value": "must-not-leak"},
                    "smtp_password": {"value": "must-not-leak"},
                }
            },
            "message": "",
        },
    )
    monkeypatch.setattr(api_main, "_auth_status", lambda: {"enabled": True, "error": "private diagnostic"})
    monkeypatch.setattr(api_main.auth_service, "auth_mode", lambda: "database")

    client = TestClient(api_main.app)
    response = client.get("/v1/auth/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "ready": True,
        "has_users": True,
        "bootstrapped": False,
        "email_delivery": True,
        "mode": "database",
        "database_auth_enabled": True,
    }


def test_login_returns_service_unavailable_when_shared_auth_store_is_down(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(
        api_main.api_auth,
        "issue_user_tokens_from_password",
        lambda **_kwargs: {
            "ok": False,
            "unavailable": True,
            "message": "Sign-in is unavailable.",
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "StrongPass123"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Sign-in is unavailable."}


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


def test_zopedia_endpoint_rejects_missing_scope(monkeypatch):
    monkeypatch.setattr(api_main.api_auth, "principal_from_agent_api_key", lambda token: _agent_principal(api_auth.SCOPE_QUERY_EXECUTE))

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/zopedia/resolve",
        headers={"X-API-Key": "snak_test"},
        json={"query": "AAPL"},
    )

    assert response.status_code == 403
    assert "Missing required scope" in response.json().get("detail", "")


def test_zopedia_endpoint_returns_resolution(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_ZOPEDIA_RESOLVE),
    )
    monkeypatch.setattr(
        api_main.zopedia_service,
        "resolve_zopedia",
        lambda **kwargs: {
            "request_id": "zop_123",
            "intent": "navigate",
            "policy_version": "streamlit-zopedia-resolver-v1",
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
        "/v1/zopedia/resolve",
        headers={"X-API-Key": "snak_test"},
        json={"query": "AAPL", "preferred_mode": "auto"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "zop_123"
    assert payload["intent"] == "navigate"
    assert payload["policy_version"] == "streamlit-zopedia-resolver-v1"
    assert payload["confidence_band"] == "high"
    assert payload["search_results"][0]["ref"] == "AAPL"


def test_zopedia_suggestions_endpoint_returns_suggestions(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_ZOPEDIA_RESOLVE),
    )
    monkeypatch.setattr(
        api_main.zopedia_service,
        "list_zopedia_suggestions",
        lambda **kwargs: {
            "policy_version": "streamlit-zopedia-resolver-v1",
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
        "/v1/zopedia/suggestions",
        headers={"X-API-Key": "snak_test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_version"] == "streamlit-zopedia-resolver-v1"
    assert payload["suggestions"][0]["query"] == "cpi"


def test_old_resolver_endpoint_is_not_available(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_ZOPEDIA_RESOLVE),
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/omnibar/resolve",
        headers={"X-API-Key": "snak_test"},
        json={"query": "AAPL"},
    )

    assert response.status_code == 404


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


def test_visualization_endpoint_returns_versioned_plotly_artifact(monkeypatch):
    monkeypatch.setattr(
        api_main.api_auth,
        "principal_from_agent_api_key",
        lambda token: _agent_principal(api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_CHART_READ),
    )
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/visualization/technical_price_channel",
        headers={"X-API-Key": "snak_test"},
        json={"params": {"ticker": "AAPL", "days": 365}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_type"] == "visualization"
    assert payload["request"]["operation"] == "visualization"
    assert payload["payload"]["schema_version"] == "visualization.v1"
    assert payload["payload"]["renderer_contract"] == "plotly.figure.v1"
    assert payload["payload"]["datasets"]["primary"]["row_count"] == 1
    assert payload["payload"]["provenance"]["details"]["dataset_version_id"] == "tech-v1"


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
