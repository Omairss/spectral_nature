from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import QueryRequest, QueryValidationError, coerce_object
from data_access.query_service import QueryService
from services import agent_tools, api_auth, auth_service, auth_store, omnibar as omnibar_service


def _auth_enabled() -> bool:
    return bool(auth_service.database_auth_enabled())


def _auth_status() -> dict[str, Any]:
    try:
        return {"enabled": _auth_enabled(), "error": ""}
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def _require_auth_backend() -> None:
    try:
        enabled = _auth_enabled()
    except Exception as exc:
        raise _error(
            f"Authentication backend unavailable: {type(exc).__name__}: {exc}",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not enabled:
        raise _error("API authentication is disabled for this environment.", status.HTTP_503_SERVICE_UNAVAILABLE)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _error(message: str, code: int) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _query_service() -> QueryService:
    try:
        return QueryService.from_environment()
    except Exception as exc:
        raise _error(f"Failed to initialize query service: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


def _extract_bearer_token(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    parts = raw.split(" ", 1)
    if len(parts) == 2 and parts[0].strip().lower() == "bearer":
        return parts[1].strip()
    return ""


def _required_scopes_for_query(request_dict: dict[str, Any]) -> list[str]:
    operation = str(request_dict.get("operation") or "").strip().lower()
    if operation == "capabilities":
        return [api_auth.SCOPE_CAPABILITIES_READ]
    if operation == "dataset":
        return [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ]
    if operation == "chart":
        return [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_CHART_READ]
    return [api_auth.SCOPE_QUERY_EXECUTE]


def _ensure_scopes(principal: api_auth.AuthPrincipal, required_scopes: list[str]) -> None:
    missing = [scope for scope in required_scopes if scope not in set(principal.scopes)]
    if missing:
        raise _error(f"Missing required scope(s): {', '.join(missing)}", status.HTTP_403_FORBIDDEN)


def _resolve_principal(
    *,
    authorization: str | None,
    x_api_key: str | None,
) -> api_auth.AuthPrincipal:
    bearer = _extract_bearer_token(authorization)
    api_key = str(x_api_key or "").strip()
    if not api_key and bearer.startswith("snak_"):
        api_key = bearer
        bearer = ""

    if api_key:
        principal = api_auth.principal_from_agent_api_key(api_key)
        if principal is None:
            raise _error("Invalid or expired API key.", status.HTTP_401_UNAUTHORIZED)
        return principal

    if bearer:
        principal = api_auth.principal_from_access_token(bearer)
        if principal is None:
            principal = api_auth.principal_from_legacy_session_token(bearer)
        if principal is None:
            raise _error("Invalid or expired bearer token.", status.HTTP_401_UNAUTHORIZED)
        return principal

    try:
        auth_enabled = _auth_enabled()
    except Exception as exc:
        raise _error(
            f"Authentication backend unavailable: {type(exc).__name__}: {exc}",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if auth_enabled:
        raise _error("Authentication required.", status.HTTP_401_UNAUTHORIZED)

    raise _error("API authentication is disabled for this environment.", status.HTTP_503_SERVICE_UNAVAILABLE)


def _require_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> api_auth.AuthPrincipal:
    return _resolve_principal(authorization=authorization, x_api_key=x_api_key)


def _require_admin_user(principal: api_auth.AuthPrincipal) -> None:
    if principal.user_context is None:
        raise _error("Admin user session required.", status.HTTP_403_FORBIDDEN)
    if not principal.user_context.is_admin:
        raise _error("Admin role required.", status.HTTP_403_FORBIDDEN)


def _execute_query(service: QueryService, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        query = QueryRequest.from_dict(payload)
        response = service.execute(query)
    except (QueryValidationError, ValueError) as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        raise _error(f"{type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response.to_dict()


def _build_tool_catalog(service: QueryService) -> list[dict[str, Any]]:
    return agent_tools.build_tool_catalog(service)


def _invoke_tool(
    *,
    service: QueryService,
    principal: api_auth.AuthPrincipal,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    query = None
    if agent_tools.is_query_service_tool(tool_name):
        try:
            query = agent_tools.build_query_request_for_tool(tool_name=tool_name, arguments=arguments)
        except (QueryValidationError, ValueError) as exc:
            raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
        if query.operation == "capabilities":
            _ensure_scopes(principal, [api_auth.SCOPE_CAPABILITIES_READ])
        else:
            _ensure_scopes(principal, _required_scopes_for_query(query.to_dict()))
    elif agent_tools.is_research_tool(tool_name):
        _ensure_scopes(principal, [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ])
    else:
        raise _error(f"Unsupported tool '{tool_name}'.", status.HTTP_400_BAD_REQUEST)
    try:
        if query is not None:
            return service.execute(query).to_dict()
        return agent_tools.invoke_tool(service=service, tool_name=tool_name, arguments=arguments)
    except (QueryValidationError, ValueError) as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        raise _error(f"{type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


def _rpc_response(result_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": result_id, "result": result}


def _rpc_error(result_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": result_id, "error": payload}


def _handle_rpc_message(
    *,
    message: dict[str, Any],
    principal: api_auth.AuthPrincipal,
    service: QueryService,
) -> dict[str, Any] | None:
    method = str(message.get("method") or "").strip()
    if not method:
        return _rpc_error(message.get("id"), -32600, "Invalid Request: missing method.")
    result_id = message.get("id")
    is_notification = result_id is None

    try:
        params_obj = coerce_object(message.get("params"), field_name="params")
        if method in {"rpc.ping", "health.ping"}:
            result = {"status": "ok"}
        elif method in {"mcp.initialize", "initialize"}:
            result = {
                "protocolVersion": "2026-04-07",
                "serverInfo": {"name": "spectral-nature-agent-gateway", "version": "1.0.0"},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "authentication": {"principalTypes": ["user", "agent"]},
                },
            }
        elif method in {"mcp.tools.list", "tools.list", "tools/list"}:
            _ensure_scopes(principal, [api_auth.SCOPE_MCP_INVOKE, api_auth.SCOPE_CAPABILITIES_READ])
            result = {"tools": _build_tool_catalog(service)}
        elif method in {"mcp.tools.call", "tools.call", "tools/call"}:
            _ensure_scopes(principal, [api_auth.SCOPE_MCP_INVOKE])
            tool_name = str(params_obj.get("name") or "").strip()
            if not tool_name:
                raise _error("Tool call requires `name`.", status.HTTP_400_BAD_REQUEST)
            arguments = coerce_object(params_obj.get("arguments"), field_name="arguments")
            tool_result = _invoke_tool(
                service=service,
                principal=principal,
                tool_name=tool_name,
                arguments=arguments,
            )
            result = {
                "content": [{"type": "json", "json": tool_result}],
                "isError": False,
            }
        else:
            return _rpc_error(result_id, -32601, f"Method not found: {method}")
    except QueryValidationError as exc:
        return _rpc_error(result_id, -32602, str(exc))
    except HTTPException as exc:
        return _rpc_error(result_id, int(exc.status_code), str(exc.detail))
    except Exception as exc:
        return _rpc_error(result_id, -32603, f"{type(exc).__name__}: {exc}")

    if is_notification:
        return None
    return _rpc_response(result_id, result)


class QueryBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class QueryRequestBody(BaseModel):
    operation: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str
    rotate_refresh_token: bool = True


class LogoutRequest(BaseModel):
    refresh_token: str = ""


class AgentKeyCreateRequest(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = None
    notes: str = ""


class ToolInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class OmnibarResolveRequest(BaseModel):
    query: str
    preferred_mode: str = "auto"
    force_refresh: bool = False


app = FastAPI(
    title="Spectral Nature API",
    version="1.0.0",
    description=(
        "Unified application and agent gateway with scoped auth, query endpoints, "
        "and MCP-compatible JSON-RPC tool invocation."
    ),
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/auth/status")
def auth_status() -> dict[str, Any]:
    initialized = auth_service.initialize_auth_system()
    auth_status_payload = _auth_status()
    return {
        **initialized,
        "mode": auth_service.auth_mode(),
        "database_auth_enabled": auth_status_payload["enabled"],
        "database_auth_error": auth_status_payload["error"],
    }


@app.post("/v1/auth/login")
def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    _require_auth_backend()
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client is not None else ""
    result = api_auth.issue_user_tokens_from_password(
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
        "token_type": result.get("token_type"),
        "access_token": result.get("access_token"),
        "access_token_expires_at": result.get("access_token_expires_at"),
        "refresh_token": result.get("refresh_token"),
        "refresh_token_expires_at": result.get("refresh_token_expires_at"),
        "scopes": list(result.get("scopes") or []),
        "context": context.to_dict() if isinstance(context, auth_service.UserContext) else None,
    }


@app.post("/v1/auth/refresh")
def refresh_tokens(payload: RefreshRequest, request: Request) -> dict[str, Any]:
    _require_auth_backend()
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client is not None else ""
    result = api_auth.refresh_user_tokens(
        refresh_token=payload.refresh_token,
        user_agent=user_agent,
        ip_address=ip_address,
        rotate_refresh_token=bool(payload.rotate_refresh_token),
    )
    if not bool(result.get("ok")):
        raise _error(str(result.get("message") or "Refresh token is invalid."), status.HTTP_401_UNAUTHORIZED)
    context = result.get("context")
    return {
        "ok": True,
        "token_type": result.get("token_type"),
        "access_token": result.get("access_token"),
        "access_token_expires_at": result.get("access_token_expires_at"),
        "refresh_token": result.get("refresh_token"),
        "refresh_token_expires_at": result.get("refresh_token_expires_at"),
        "scopes": list(result.get("scopes") or []),
        "context": context.to_dict() if isinstance(context, auth_service.UserContext) else None,
    }


@app.post("/v1/auth/logout")
def logout(
    payload: LogoutRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    if principal.user_context is None:
        raise _error("User session required for logout.", status.HTTP_403_FORBIDDEN)
    if principal.session_token_hash:
        auth_store.revoke_session(principal.session_token_hash)
    if str(payload.refresh_token or "").strip():
        auth_service.logout_session(str(payload.refresh_token or "").strip())
    return {"ok": True}


@app.get("/v1/me")
def me(principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    if not principal.is_authenticated:
        return {
            "authenticated": False,
            "principal_type": principal.principal_type,
            "scopes": list(principal.scopes),
        }
    if principal.user_context is not None:
        return {
            "authenticated": True,
            "principal_type": "user",
            "context": principal.user_context.to_dict(),
            "scopes": list(principal.scopes),
            "auth_source": principal.auth_source,
        }
    return {
        "authenticated": True,
        "principal_type": "agent",
        "agent_key_id": principal.agent_key_id,
        "agent_key_name": principal.agent_key_name,
        "scopes": list(principal.scopes),
        "auth_source": principal.auth_source,
    }


@app.get("/v1/auth/agent-keys")
def list_agent_keys(principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    _require_admin_user(principal)
    _ensure_scopes(principal, [api_auth.SCOPE_AGENT_KEY_READ])
    return {"keys": api_auth.list_agent_api_keys()}


@app.post("/v1/auth/agent-keys")
def create_agent_key(
    payload: AgentKeyCreateRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    _ensure_scopes(principal, [api_auth.SCOPE_AGENT_KEY_WRITE])
    expires_at = None
    if payload.expires_in_days is not None:
        days = max(int(payload.expires_in_days), 1)
        expires_at = _now_utc() + timedelta(days=days)
    key_payload = api_auth.create_agent_api_key(
        name=payload.name,
        scopes=payload.scopes,
        created_by=principal.user_context.user_id if principal.user_context is not None else None,
        expires_at=expires_at,
        notes=payload.notes,
    )
    return {"ok": True, **key_payload}


@app.post("/v1/auth/agent-keys/{key_id}/revoke")
def revoke_agent_key(
    key_id: str,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    _ensure_scopes(principal, [api_auth.SCOPE_AGENT_KEY_WRITE])
    row = api_auth.revoke_agent_api_key(
        key_id=key_id,
        revoked_by=principal.user_context.user_id if principal.user_context is not None else None,
    )
    return {"ok": True, "key": row}


@app.post("/v1/omnibar/resolve")
def resolve_omnibar(
    payload: OmnibarResolveRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, object]:
    _ensure_scopes(principal, [api_auth.SCOPE_OMNIBAR_RESOLVE])
    try:
        return omnibar_service.resolve_omnibar(
            query=payload.query,
            preferred_mode=payload.preferred_mode,
            force_refresh=bool(payload.force_refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(f"Failed to resolve omnibar request: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/v1/omnibar/suggestions")
def omnibar_suggestions(
    limit: int = 8,
    force_refresh: bool = False,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, object]:
    _ensure_scopes(principal, [api_auth.SCOPE_OMNIBAR_RESOLVE])
    try:
        return omnibar_service.list_omnibar_suggestions(
            limit=limit,
            force_refresh=bool(force_refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(f"Failed to build omnibar suggestions: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/v1/capabilities")
def capabilities(principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_CAPABILITIES_READ])
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
def query(payload: QueryRequestBody, principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    service = _query_service()
    request_dict = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    _ensure_scopes(principal, _required_scopes_for_query(request_dict))
    return _execute_query(service, request_dict)


@app.post("/v1/dataset/{name}")
def dataset(
    name: str,
    body: QueryBody,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    service = _query_service()
    request_dict = {
        "operation": "dataset",
        "name": name,
        "params": body.params,
    }
    _ensure_scopes(principal, _required_scopes_for_query(request_dict))
    return _execute_query(service, request_dict)


@app.post("/v1/chart/{name}")
def chart(
    name: str,
    body: QueryBody,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    service = _query_service()
    request_dict = {
        "operation": "chart",
        "name": name,
        "params": body.params,
    }
    _ensure_scopes(principal, _required_scopes_for_query(request_dict))
    return _execute_query(service, request_dict)


@app.get("/v1/agent/tools")
def list_agent_tools(principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_MCP_INVOKE, api_auth.SCOPE_CAPABILITIES_READ])
    service = _query_service()
    return {"tools": _build_tool_catalog(service)}


@app.post("/v1/agent/tools/{tool_name}/invoke")
def invoke_agent_tool(
    tool_name: str,
    payload: ToolInvokeRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_MCP_INVOKE])
    service = _query_service()
    return _invoke_tool(
        service=service,
        principal=principal,
        tool_name=tool_name,
        arguments=payload.arguments,
    )


@app.post("/v1/agent/rpc", response_model=None)
async def agent_rpc(
    request: Request,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> Any:
    _ensure_scopes(principal, [api_auth.SCOPE_MCP_INVOKE])
    service = _query_service()
    body = await request.json()
    if isinstance(body, list):
        responses: list[dict[str, Any]] = []
        for message in body:
            if not isinstance(message, dict):
                responses.append(_rpc_error(None, -32600, "Invalid Request"))
                continue
            item = _handle_rpc_message(message=message, principal=principal, service=service)
            if item is not None:
                responses.append(item)
        if not responses:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return responses
    if not isinstance(body, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    response = _handle_rpc_message(message=body, principal=principal, service=service)
    if response is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return response
