from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import QueryRequest, QueryResponse, QueryValidationError, coerce_object
from data_access.query_service import QueryService
from presentation.visualization_artifacts import build_plotly_visualization_artifact
from services import agent_tools, api_auth, auth_service, auth_store, research_export, zopedia_resolver as zopedia_service
from services.agents.research_runs import (
    ResearchRunConflict,
    ResearchRunError,
    ResearchRunNotFound,
    ResearchRunRepository,
    dispatch_research_run,
)
from services.admin_view import build_admin_view, build_llm_settings_view, update_llm_settings
from services.home_view import build_public_home_audio, build_public_home_view
from api.feature_requests import create_feature_request_router
from api.paper_trading import create_paper_trading_router
from services.market_view import build_market_lenses_view
from services.portfolio_view import build_portfolio_view, build_portfolio_opportunity_view
from services.stock_view import build_stock_investigator_view
from services.trading_agent_view import build_trading_agent_view, record_trading_agent_decision
from services.zopedia_view import build_zopedia_workspace, build_zopedia_page_view, store_uploaded_zopedia_source


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
    if operation in {"chart", "visualization"}:
        return [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_CHART_READ]
    return [api_auth.SCOPE_QUERY_EXECUTE]


def _ensure_scopes(principal: api_auth.AuthPrincipal, required_scopes: list[str]) -> None:
    missing = [scope for scope in required_scopes if scope not in set(principal.scopes)]
    if missing:
        raise _error(f"Missing required scope(s): {', '.join(missing)}", status.HTTP_403_FORBIDDEN)


def _ensure_zopedia_resolve_scope(principal: api_auth.AuthPrincipal) -> None:
    scopes = set(principal.scopes)
    if api_auth.SCOPE_ZOPEDIA_RESOLVE in scopes:
        return
    raise _error(
        f"Missing required scope(s): {api_auth.SCOPE_ZOPEDIA_RESOLVE}",
        status.HTTP_403_FORBIDDEN,
    )


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


def _require_user_context(principal: api_auth.AuthPrincipal) -> auth_service.UserContext:
    if principal.user_context is None:
        raise _error("User session required.", status.HTTP_403_FORBIDDEN)
    return principal.user_context


def _execute_query(service: QueryService, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        query = QueryRequest.from_dict(payload)
        response = service.execute(query)
    except (QueryValidationError, ValueError) as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        raise _error(f"{type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response.to_dict()


def _execute_visualization(service: QueryService, *, name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        resolved = service.build_chart(name, params)
    except (QueryValidationError, ValueError) as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    try:
        artifact = build_plotly_visualization_artifact(
            resolved.payload,
            provenance=resolved.provenance,
        )
        response = QueryResponse(
            request=QueryRequest(operation="visualization", name=name, params=params),
            result_type="visualization",
            payload=artifact,
            provenance=resolved.provenance,
        )
    except Exception:
        raise _error("Visualization could not be built.", status.HTTP_500_INTERNAL_SERVER_ERROR)
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
    elif agent_tools.is_zopedia_tool(tool_name):
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


class ZopediaResolveRequest(BaseModel):
    query: str
    preferred_mode: str = "auto"
    force_refresh: bool = False


class ResearchExportRequest(BaseModel):
    start_date: str
    end_date: str


class ResearchRunCreateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    parent_run_id: str = Field(default="", max_length=80)
    conversation_id: str = Field(default="", max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    asof_time_utc: str = Field(default="", max_length=80)
    allow_web: bool | None = None
    visible_artifacts: list[dict[str, Any]] | None = Field(default=None, max_length=20)


class TradingAgentDecisionRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=20)


class AdminInviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=20)
    share_fraction: float | None = None


class AdminPasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class AccountActivationRequest(BaseModel):
    invite_token: str = Field(min_length=32, max_length=256)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class PasswordResetRequest(BaseModel):
    reset_token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=1, max_length=1024)


class ResearchReviewResponse(BaseModel):
    status: str = ""
    verdict: str = ""


class ResearchRunResultResponse(BaseModel):
    status: str
    answer_markdown: str
    visualizations: list[dict[str, Any]] = Field(default_factory=list)
    answer_status: str = ""
    quality_review: ResearchReviewResponse = Field(default_factory=ResearchReviewResponse)
    confidence: str
    limitations: list[str] = Field(default_factory=list)
    citations: list[dict[str, str]] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    evidence_pack_id: str = ""
    engine_revision: str = ""
    failure: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ResearchPublicContextResponse(BaseModel):
    timezone: str = "UTC"
    asof_time_utc: str = ""
    allow_web: bool = True
    visible_artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ResearchRunMetadataResponse(BaseModel):
    parent_run_id: str = ""
    engine_revision: str = ""
    request_context: ResearchPublicContextResponse = Field(
        default_factory=ResearchPublicContextResponse
    )


class ResearchRunResponse(BaseModel):
    run_id: str
    conversation_id: str = ""
    turn_id: str = ""
    surface: str
    task: str
    query: str
    metadata: ResearchRunMetadataResponse = Field(
        default_factory=ResearchRunMetadataResponse
    )
    status: str
    result: ResearchRunResultResponse | None = None
    last_event_sequence: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    attempt_count: int
    dispatch_status: str
    error_code: str = ""
    write_policy: str


class ResearchRunEventResponse(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    stage: str
    message: str
    progress: float
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ResearchRunEventsResponse(BaseModel):
    run_id: str
    events: list[ResearchRunEventResponse] = Field(default_factory=list)
    last_event_sequence: int


class ResearchConversationSummaryResponse(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    latest_run_id: str
    status: str
    turn_count: int


class ResearchTurnResponse(BaseModel):
    turn_id: str
    query: str
    created_at: datetime
    attempts: list[ResearchRunResponse]


class ResearchConversationResponse(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    turns: list[ResearchTurnResponse]


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


@app.get("/v1/home")
def public_home(response: Response, date: str = "") -> dict[str, Any]:
    response.headers["Cache-Control"] = (
        "public, max-age=3600, stale-while-revalidate=86400"
        if date
        else "public, max-age=30, stale-while-revalidate=120"
    )
    try:
        return build_public_home_view(target_date=date)
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("Home data is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/home/audio")
def public_home_audio(date: str = "") -> Response:
    try:
        result = build_public_home_audio(target_date=date)
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("Home audio is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    if result is None:
        raise _error("Home audio is unavailable.", status.HTTP_404_NOT_FOUND)
    audio, mime_type = result
    return Response(
        content=audio,
        media_type=mime_type,
        headers={
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400" if date else "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/v1/investigator/{ticker}")
def stock_investigator_view(
    ticker: str,
    response: Response,
    days: int = 365,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ])
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_stock_investigator_view(ticker, days=days, service=_query_service())
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("Stock Investigator data is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/market-lenses")
def market_lenses_view(
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ])
    response.headers["Cache-Control"] = "private, max-age=300"
    try:
        return build_market_lenses_view()
    except Exception:
        raise _error("Market lenses are unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/portfolio")
def portfolio_view(
    response: Response,
    period: str = "5Y",
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    context = _require_user_context(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_portfolio_view(context, period=period, service=_query_service())
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("Portfolio data is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/portfolio/opportunities")
def portfolio_opportunities(
    response: Response, period: str = "5Y",
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    context = _require_user_context(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_portfolio_opportunity_view(context, period=period)
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("List analytics are unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/trading-agent")
def trading_agent_view(
    response: Response,
    period: str = Query(default="1M", pattern=r"^(1D|1W|1M|3M|1Y|ALL)$"),
    cursor: str = Query(default="", max_length=1024),
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_trading_agent_view(_require_user_context(principal),period=period,cursor=cursor)
    except Exception:
        raise _error("Trading Agent data is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.post("/v1/trading-agent/decisions")
def trading_agent_decision(
    body: TradingAgentDecisionRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    context = _require_user_context(principal)
    try:
        return record_trading_agent_decision(
            candidate_id=body.candidate_id,
            action=body.action,
            context=context,
        )
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except RuntimeError as exc:
        raise _error(str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)




@app.get("/v1/trading-agent/calls/{tracking_id}")
def trading_agent_tracked_call(
    tracking_id: str,
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    context = _require_user_context(principal)
    response.headers["Cache-Control"] = "private, no-store"
    from services.trading_call_tracking import tracking_view
    try:
        return tracking_view(owner_id=context.user_id, tracking_id=tracking_id)["calls"][0]
    except LookupError:
        raise _error("Tracked call not found.", status.HTTP_404_NOT_FOUND)
    except Exception:
        raise _error("Call tracking is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)



@app.get("/v1/admin")
def admin_view(
    response: Response,
    days: int = 7,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_admin_view(days=days)
    except Exception:
        raise _error("Admin data is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/admin/health")
def admin_operational_health(
    response: Response,
    hours: int = 24,
    resource: str = "",
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    response.headers["Cache-Control"] = "private, no-store"
    from services.operational_health import build_operational_health_view

    try:
        return build_operational_health_view(hours=hours, resource_name=resource)
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)


@app.post("/v1/admin/invites")
def admin_invite(
    body: AdminInviteRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    context = _require_user_context(principal)
    result = auth_service.issue_invite(
        email=body.email,
        role=body.role,
        share_fraction=body.share_fraction,
        created_by=context,
    )
    if not result.get("ok"):
        raise _error(str(result.get("message") or "Invite could not be created."), status.HTTP_400_BAD_REQUEST)
    return result


class AdminLLMSettingsRequest(BaseModel):
    changes: dict[str, str | int | float | None] = Field(min_length=1, max_length=200)


@app.get("/v1/admin/llm")
def admin_llm_settings(response: Response, principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    _require_admin_user(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return build_llm_settings_view()
    except RuntimeError as exc:
        raise _error(str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)


@app.patch("/v1/admin/llm")
def admin_update_llm_settings(
    body: AdminLLMSettingsRequest,
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return update_llm_settings(body.changes, context=_require_user_context(principal))
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except RuntimeError as exc:
        raise _error(str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)


@app.post("/v1/admin/password-resets")
def admin_password_reset(
    body: AdminPasswordResetRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _require_admin_user(principal)
    context = _require_user_context(principal)
    result = auth_service.admin_issue_password_reset(email=body.email, requested_by=context)
    if not result.get("ok"):
        raise _error(str(result.get("message") or "Password reset could not be issued."), status.HTTP_400_BAD_REQUEST)
    return result


@app.get("/v1/auth/status")
def auth_status() -> dict[str, Any]:
    initialized = auth_service.initialize_auth_system(include_email_delivery=True)
    auth_status_payload = _auth_status()
    return {
        "available": bool(initialized.get("available")),
        "ready": bool(initialized.get("ready")),
        "has_users": bool(initialized.get("has_users")),
        "bootstrapped": bool(initialized.get("bootstrapped")),
        "email_delivery": bool(initialized.get("email_delivery")),
        "mode": auth_service.auth_mode(),
        "database_auth_enabled": bool(auth_status_payload["enabled"]),
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
        if bool(result.get("unavailable")):
            raise _error("Sign-in is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
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


@app.post("/v1/auth/invites/accept")
def activate_account(payload: AccountActivationRequest, request: Request, response: Response) -> dict[str, Any]:
    _require_auth_backend()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = auth_service.accept_invite(
            **payload.model_dump(), user_agent=request.headers.get("user-agent", ""),
            ip_address=request.client.host if request.client else "",
        )
        if not result.get("ok"):
            raise _error(str(result.get("message") or "Account activation failed."),
                         503 if result.get("unavailable") else 400)
        # Issue the normal API envelope for the newly created native session.
        tokens = api_auth.refresh_user_tokens(refresh_token=result["session_token"], rotate_refresh_token=False)
        if not tokens.get("ok"):
            raise _error("Account created. Please sign in.", 503)
        return {key: tokens[key] for key in ("ok", "access_token", "refresh_token", "access_token_expires_at", "refresh_token_expires_at")}
    except HTTPException:
        raise
    except Exception:
        raise _error("Account activation is unavailable.", 503)


@app.post("/v1/auth/password-reset/request")
def request_password_reset(payload: AdminPasswordResetRequest, request: Request, response: Response) -> dict[str, Any]:
    _require_auth_backend()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = auth_service.request_password_reset(email=payload.email,
            requested_ip=request.client.host if request.client else "")
        if not result.get("ok"):
            raise _error("Account recovery is unavailable.", 503)
        # Never expose matched-user, SMTP diagnostics, or the generated reset URL.
        return {"ok": True, "message": "Reset instructions requested."}
    except HTTPException:
        raise
    except Exception:
        raise _error("Account recovery is unavailable.", 503)


@app.post("/v1/auth/password-reset/complete")
def complete_password_reset(payload: PasswordResetRequest, response: Response) -> dict[str, Any]:
    _require_auth_backend()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = auth_service.complete_password_reset(**payload.model_dump())
        if not result.get("ok"):
            raise _error(str(result.get("message") or "Password reset failed."),
                         503 if result.get("unavailable") else 400)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        raise _error("Password reset is unavailable.", 503)


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


@app.post("/v1/zopedia/resolve")
def resolve_zopedia(
    payload: ZopediaResolveRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, object]:
    _ensure_zopedia_resolve_scope(principal)
    try:
        return zopedia_service.resolve_zopedia(
            query=payload.query,
            preferred_mode=payload.preferred_mode,
            force_refresh=bool(payload.force_refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(f"Failed to resolve Zopedia request: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/v1/zopedia/workspace")
def get_zopedia_workspace(
    response: Response,
    q: str = "",
    limit: int = 60,
    folder: str = "",
    offset: int = 0,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    try:
        return build_zopedia_workspace(query=q, is_admin=context.is_admin, limit=limit, folder=folder[:300], offset=max(0, min(offset, 100_000)))
    except Exception:
        raise _error("Zopedia workspace is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.get("/v1/zopedia/pages")
def get_zopedia_page(page_id: str, response: Response, principal: api_auth.AuthPrincipal = Depends(_require_principal)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    if len(page_id) > 240:
        raise _error("Invalid page identifier.", status.HTTP_400_BAD_REQUEST)
    try:
        page = build_zopedia_page_view(page_id)
    except Exception:
        raise _error("Knowledge page is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    if page is None:
        raise _error("Knowledge page was not found.", status.HTTP_404_NOT_FOUND)
    return page


@app.post("/v1/zopedia/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_zopedia_source(
    source: UploadFile = File(...),
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    filename = str(source.filename or "uploaded-source").strip()[:255]
    if not filename:
        raise _error("A source filename is required.", status.HTTP_400_BAD_REQUEST)
    content = await source.read(5_242_881)
    if not content:
        raise _error("The source file is empty.", status.HTTP_400_BAD_REQUEST)
    if len(content) > 5_242_880:
        raise _error("Source files must be 5 MB or smaller.", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        return store_uploaded_zopedia_source(
            filename=filename,
            content=content,
            content_type=str(source.content_type or "")[:160],
            owner_id=context.user_id,
            owner_email=context.email,
        )
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except Exception:
        raise _error("The source could not be stored.", status.HTTP_503_SERVICE_UNAVAILABLE)


@app.post("/v1/research-runs", response_model=ResearchRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_research_run(
    payload: ResearchRunCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        run, created = repository.create(
            owner_id=context.user_id,
            owner_email=context.email,
            query=payload.query,
            idempotency_key=idempotency_key,
            parent_run_id=payload.parent_run_id,
            conversation_id=payload.conversation_id,
            request_context={
                "timezone": payload.timezone,
                "asof_time_utc": payload.asof_time_utc,
                "allow_web": payload.allow_web,
                "visible_artifacts": payload.visible_artifacts,
            },
        )
        if run["status"] == "queued" and (created or run["dispatch_status"] in {"pending", "failed"}):
            dispatch_research_run(repository, run_id=run["run_id"])
            run = repository.get_for_user(run_id=run["run_id"], owner_id=context.user_id)
        return run
    except ValueError as exc:
        raise _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except ResearchRunConflict as exc:
        raise _error(str(exc), status.HTTP_409_CONFLICT)
    except ResearchRunNotFound:
        raise _error("Research conversation not found.", status.HTTP_404_NOT_FOUND)
    except ResearchRunError:
        raise _error("Research service is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        if repository is not None:
            repository.close()


@app.get(
    "/v1/research-conversations",
    response_model=list[ResearchConversationSummaryResponse],
)
def list_research_conversations(
    response: Response,
    limit: int = 50,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        return repository.list_conversations(owner_id=context.user_id, limit=limit)
    except ResearchRunError:
        raise _error(
            "Research conversations are unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    finally:
        if repository is not None:
            repository.close()


@app.get(
    "/v1/research-conversations/{conversation_id}",
    response_model=ResearchConversationResponse,
)
def get_research_conversation(
    conversation_id: str,
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        return repository.get_conversation(
            conversation_id=conversation_id, owner_id=context.user_id
        )
    except (ValueError, ResearchRunNotFound):
        raise _error("Research conversation not found.", status.HTTP_404_NOT_FOUND)
    except ResearchRunError:
        raise _error(
            "Research conversation is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE
        )
    finally:
        if repository is not None:
            repository.close()


@app.get("/v1/research-runs", response_model=list[ResearchRunResponse])
def list_research_runs(
    response: Response,
    limit: int = 50,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    _ensure_zopedia_resolve_scope(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        return repository.list_for_user(owner_id=context.user_id, limit=limit)
    except (ValueError, ResearchRunError):
        raise _error("Research threads are unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        if repository is not None:
            repository.close()


@app.get("/v1/research-runs/{run_id}", response_model=ResearchRunResponse)
def get_research_run(
    run_id: str,
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        return repository.get_for_user(run_id=run_id, owner_id=context.user_id)
    except (ValueError, ResearchRunNotFound):
        raise _error("Research run not found.", status.HTTP_404_NOT_FOUND)
    except ResearchRunError:
        raise _error("Research service is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        if repository is not None:
            repository.close()


@app.get("/v1/research-runs/{run_id}/events", response_model=ResearchRunEventsResponse)
def get_research_run_events(
    run_id: str,
    response: Response,
    after_sequence: int = 0,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        events = repository.list_events_for_user(
            run_id=run_id,
            owner_id=context.user_id,
            after_sequence=max(int(after_sequence), 0),
        )
        run = repository.get_for_user(run_id=run_id, owner_id=context.user_id)
        return {
            "run_id": run["run_id"],
            "events": events,
            "last_event_sequence": run["last_event_sequence"],
        }
    except (ValueError, ResearchRunNotFound):
        raise _error("Research run not found.", status.HTTP_404_NOT_FOUND)
    except ResearchRunError:
        raise _error("Research service is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        if repository is not None:
            repository.close()


@app.post("/v1/research-runs/{run_id}/cancel", response_model=ResearchRunResponse)
def cancel_research_run(
    run_id: str,
    response: Response,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    context = _require_user_context(principal)
    repository: ResearchRunRepository | None = None
    try:
        repository = ResearchRunRepository.from_environment()
        return repository.request_cancel(run_id=run_id, owner_id=context.user_id)
    except (ValueError, ResearchRunNotFound):
        raise _error("Research run not found.", status.HTTP_404_NOT_FOUND)
    except ResearchRunError:
        raise _error("Research service is unavailable.", status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        if repository is not None:
            repository.close()


@app.get("/v1/zopedia/suggestions")
def zopedia_suggestions(
    limit: int = 8,
    force_refresh: bool = False,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, object]:
    _ensure_zopedia_resolve_scope(principal)
    try:
        return zopedia_service.list_zopedia_suggestions(
            limit=limit,
            force_refresh=bool(force_refresh),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(f"Failed to build Zopedia suggestions: {type(exc).__name__}: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)


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


@app.post("/v1/visualization/{name}")
def visualization(
    name: str,
    body: QueryBody,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    service = _query_service()
    request_dict = {
        "operation": "visualization",
        "name": name,
        "params": body.params,
    }
    _ensure_scopes(principal, _required_scopes_for_query(request_dict))
    return _execute_visualization(service, name=name, params=body.params)


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


# ---------------------------------------------------------------------------
# Research export
# ---------------------------------------------------------------------------


@app.post("/v1/research/export")
def start_research_export(
    payload: ResearchExportRequest,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ])
    start = str(payload.start_date or "").strip()
    end = str(payload.end_date or "").strip()
    if not start or not end:
        raise _error("start_date and end_date are required.", status.HTTP_400_BAD_REQUEST)
    created_by = None
    if principal.user_context is not None:
        created_by = principal.user_context.user_id
    return research_export.create_export_job(
        start_date=start,
        end_date=end,
        created_by=created_by,
    )


@app.get("/v1/research/export/{job_id}")
def get_research_export(
    job_id: str,
    principal: api_auth.AuthPrincipal = Depends(_require_principal),
) -> dict[str, Any]:
    _ensure_scopes(principal, [api_auth.SCOPE_QUERY_EXECUTE, api_auth.SCOPE_DATASET_READ])
    result = research_export.get_export_job(job_id)
    if result is None:
        raise _error(f"Export job '{job_id}' not found.", status.HTTP_404_NOT_FOUND)
    return result


app.include_router(create_feature_request_router(_require_principal))
app.include_router(create_paper_trading_router(_require_principal))
