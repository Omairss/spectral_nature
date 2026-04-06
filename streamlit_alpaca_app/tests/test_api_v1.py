from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import api.main as api_main
from data_access.contracts import QueryRequest, QueryResponse


class _StubContext:
    def to_dict(self) -> dict[str, str]:
        return {"user_id": "u1", "email": "user@example.com"}


class _StubQueryService:
    def execute(self, request: QueryRequest) -> QueryResponse:
        query = request if isinstance(request, QueryRequest) else QueryRequest.from_dict(request)
        return QueryResponse(
            request=query,
            result_type="dataset",
            payload=[{"symbol": "AAPL"}],
            provenance=None,
        )


def test_query_endpoint_returns_response(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: False)
    monkeypatch.setattr(api_main.QueryService, "from_environment", lambda: _StubQueryService())

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/query",
        json={
            "operation": "dataset",
            "name": "price_history",
            "params": {"ticker": "AAPL"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_type"] == "dataset"
    assert payload["payload"][0]["symbol"] == "AAPL"


def test_capabilities_requires_auth_when_enabled(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(api_main.auth_service, "restore_user_from_session", lambda token: None)

    client = TestClient(api_main.app)
    response = client.get("/v1/capabilities")

    assert response.status_code == 401
    assert "Authentication required" in response.json().get("detail", "")


def test_login_success_returns_session_token(monkeypatch):
    monkeypatch.setattr(api_main, "_auth_enabled", lambda: True)
    monkeypatch.setattr(
        api_main.auth_service,
        "authenticate_user",
        lambda **kwargs: {
            "ok": True,
            "message": "",
            "session_token": "session-token",
            "context": _StubContext(),
        },
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "StrongPass123",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["session_token"] == "session-token"
    assert payload["context"]["email"] == "user@example.com"

