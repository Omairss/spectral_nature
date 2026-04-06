from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import QueryRequest
from data_access.query_service import QueryService
from services import auth_service


def _auth_enabled() -> bool:
    try:
        return bool(auth_service.database_auth_enabled())
    except Exception:
        return False


def _extract_bearer_token(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    parts = raw.split(" ", 1)
    if len(parts) == 2 and parts[0].strip().lower() == "bearer":
        return parts[1].strip()
    return ""


def _error(message: str, code: int) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _query_service() -> QueryService:
    try:
        return QueryService.from_environment()
    except Exception as exc:
        raise _error(f"Failed to initialize query service: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


def _execute_query(service: QueryService, payload: dict[str, Any]) -> dict[str, Any]:
    query = QueryRequest.from_dict(payload)
    try:
        response = service.execute(query)
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        raise _error(f"{type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response.to_dict()


def _require_user_context(
    authorization: str | None = Header(default=None),
) -> auth_service.UserContext | None:
    token = _extract_bearer_token(authorization)
    if not token:
        if _auth_enabled():
            raise _error("Authentication required.", status.HTTP_401_UNAUTHORIZED)
        return None

    context = auth_service.restore_user_from_session(token)
    if context is None:
        raise _error("Invalid or expired session token.", status.HTTP_401_UNAUTHORIZED)
    return context


class QueryBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class QueryRequestBody(BaseModel):
    operation: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: str
    password: str


app = FastAPI(
    title="Spectral Nature API",
    version="0.1.0",
    description="Thin API surface over the shared query service for non-Streamlit clients.",
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/auth/status")
def auth_status() -> dict[str, Any]:
    initialized = auth_service.initialize_auth_system()
    return {
        **initialized,
        "mode": auth_service.auth_mode(),
        "database_auth_enabled": _auth_enabled(),
    }


@app.post("/v1/auth/login")
def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    if not _auth_enabled():
        raise _error("Database authentication is not enabled for this environment.", status.HTTP_409_CONFLICT)

    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client is not None else ""
    result = auth_service.authenticate_user(
        email=payload.email,
        password=payload.password,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    if not bool(result.get("ok")):
        raise _error(str(result.get("message") or "Invalid email or password."), status.HTTP_401_UNAUTHORIZED)
    context = result.get("context")
    return {
        "ok": True,
        "session_token": str(result.get("session_token") or ""),
        "context": context.to_dict() if hasattr(context, "to_dict") else None,
    }


@app.post("/v1/auth/logout")
def logout(
    context: auth_service.UserContext | None = Depends(_require_user_context),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    del context
    token = _extract_bearer_token(authorization)
    if token:
        auth_service.logout_session(token)
    return {"ok": True}


@app.get("/v1/me")
def me(context: auth_service.UserContext | None = Depends(_require_user_context)) -> dict[str, Any]:
    if context is None:
        return {"authenticated": False, "context": None}
    return {"authenticated": True, "context": context.to_dict()}


@app.get("/v1/capabilities")
def capabilities(context: auth_service.UserContext | None = Depends(_require_user_context)) -> dict[str, Any]:
    del context
    service = _query_service()
    return _execute_query(
        service,
        {
            "operation": "capabilities",
            "name": "",
            "params": {},
        },
    )


@app.post("/v1/query")
def query(payload: QueryRequestBody, context: auth_service.UserContext | None = Depends(_require_user_context)) -> dict[str, Any]:
    del context
    service = _query_service()
    return _execute_query(service, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())


@app.post("/v1/dataset/{name}")
def dataset(
    name: str,
    body: QueryBody,
    context: auth_service.UserContext | None = Depends(_require_user_context),
) -> dict[str, Any]:
    del context
    service = _query_service()
    return _execute_query(
        service,
        {
            "operation": "dataset",
            "name": name,
            "params": body.params,
        },
    )


@app.post("/v1/chart/{name}")
def chart(
    name: str,
    body: QueryBody,
    context: auth_service.UserContext | None = Depends(_require_user_context),
) -> dict[str, Any]:
    del context
    service = _query_service()
    return _execute_query(
        service,
        {
            "operation": "chart",
            "name": name,
            "params": body.params,
        },
    )

