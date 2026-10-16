from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlparse

import anyio
import requests
from jsonschema import Draft202012Validator

from . import brokerage_mcp_store
from .brokerage_credentials import (
    BrokerageCredentialError,
    connection_cipher,
    decrypt_secret,
    encrypt_secret,
)


PROVIDER = "robinhood_mcp"
DEFAULT_RESOURCE_URL = "https://agent.robinhood.com/mcp/trading"
DEFAULT_SCOPE = "internal"
OAUTH_FLOW_TTL_MINUTES = 10
EQUITY_REVIEW_TOOL = "review_equity_order"
EQUITY_EXECUTION_TOOL = "place_equity_order"


class RobinhoodMCPError(RuntimeError):
    pass


class RobinhoodMCPAuthorizationError(RobinhoodMCPError):
    pass


class RobinhoodMCPPolicyError(RobinhoodMCPError):
    pass


# This allowlist mirrors Robinhood's official external-agent tool catalog. A
# remote annotation is not enough to authorize a new capability: unknown tools
# stay closed until their behavior is reviewed here.
FOREGROUND_READ_TOOLS = {
    "get_accounts",
    "get_portfolio",
    "get_realized_pnl",
    "get_pnl_trade_history",
    "search",
    "get_limited_margin_upgrade_info",
    "get_trade_approvals",
    "get_trade_approval_setting",
    "get_watchlists",
    "get_watchlist_items",
    "get_option_watchlist",
    "get_popular_watchlists",
    "get_equity_historicals",
    "get_equity_fundamentals",
    "get_financials",
    "get_equity_price_book",
    "get_equity_technical_indicators",
    "get_earnings_results",
    "get_earnings_calendar",
    "get_indexes",
    "get_index_quotes",
    "get_equity_news",
    "get_sec_filing_index",
    "get_sec_filing",
    "get_sec_filing_facts",
    "get_sec_filing_facts_catalog",
    "get_index_historicals",
    "get_politician_trades",
    "get_equity_analyst_ratings",
    "get_equity_positions",
    "get_equity_tax_lots",
    "get_equity_quotes",
    "get_equity_orders",
    "get_equity_tradability",
    "get_option_level_upgrade_info",
    "get_option_chains",
    "get_option_historicals",
    "get_option_instruments",
    "get_option_quotes",
    "get_option_positions",
    "get_option_orders",
    "get_currency_pairs",
    "get_crypto_quotes",
    "get_crypto_positions",
    "get_crypto_orders",
    "get_crypto_account_onboarding_info",
    "get_crypto_tax_lots",
    "get_scans",
    "get_scanner_filter_specs",
    "run_scan",
    "get_scanner_datapoints",
    "preview_scan",
    "get_alerts",
    "get_alert_log",
    "get_advanced_orders",
    "get_legend_charts",
    "get_legend_indicators",
    "get_legend_indicator_catalog",
    "get_legend_layouts",
}

ORDER_REVIEW_TOOLS = {
    "review_equity_order",
    "review_option_order",
    "preview_crypto_order",
    "review_advanced_order",
}

EXECUTION_TOOLS = {
    "place_equity_order",
    "cancel_equity_order",
    "place_option_order",
    "cancel_option_order",
    "exercise_option",
    "cancel_option_exercise",
    "place_crypto_order",
    "cancel_crypto_order",
    "place_advanced_order",
    "cancel_advanced_order",
    "decline_trade_approval",
}

OTHER_MUTATION_TOOLS = {
    "create_watchlist",
    "update_watchlist",
    "follow_watchlist",
    "unfollow_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "add_option_to_watchlist",
    "remove_option_from_watchlist",
    "create_scan",
    "update_scan_filters",
    "update_scan_config",
    "create_alert",
    "update_alert",
    "delete_alert",
    "mark_alerts_read",
    "set_legend_chart_settings",
    "add_legend_indicator",
    "update_legend_indicator",
    "remove_legend_indicator",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def environment_name() -> str:
    value = (os.getenv("APP_TRACK") or "local").strip().lower() or "local"
    if not all(char.isalnum() or char in {"-", "_"} for char in value):
        raise RobinhoodMCPError("APP_TRACK contains an invalid environment name.")
    return value


def execution_enabled() -> bool:
    return str(os.getenv("ROBINHOOD_EXECUTION_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _order_ticket_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("ROBINHOOD_ORDER_CONFIRMATION_TTL_SECONDS") or "120")
    except ValueError:
        configured = 120
    return min(max(configured, 30), 600)


def resource_url() -> str:
    value = (os.getenv("ROBINHOOD_MCP_URL") or DEFAULT_RESOURCE_URL).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "agent.robinhood.com" or parsed.path != "/mcp/trading":
        raise RobinhoodMCPError("ROBINHOOD_MCP_URL must be Robinhood's official trading MCP endpoint.")
    return value


def public_base_url() -> str:
    value = (os.getenv("APP_PUBLIC_BASE_URL") or "http://localhost:3000").strip().rstrip("/")
    parsed = urlparse(value)
    is_local = parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme not in ({"http", "https"} if is_local else {"https"}) or not parsed.netloc:
        raise RobinhoodMCPError("APP_PUBLIC_BASE_URL must be an absolute HTTPS URL (HTTP is allowed on localhost).")
    if parsed.query or parsed.fragment:
        raise RobinhoodMCPError("APP_PUBLIC_BASE_URL cannot contain a query string or fragment.")
    return value


def oauth_callback_url() -> str:
    return f"{public_base_url()}/?robinhood_oauth=callback"


def _trusted_robinhood_url(value: object, *, field: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "robinhood.com" or host.endswith(".robinhood.com")):
        raise RobinhoodMCPError(f"Robinhood OAuth metadata returned an untrusted {field}.")
    return url


def _response_json(response: requests.Response, *, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if response.status_code < 200 or response.status_code >= 300:
        error_text = ""
        if isinstance(payload, dict):
            error_text = str(payload.get("error_description") or payload.get("error") or "").strip()
        raise RobinhoodMCPError(
            f"Robinhood {operation} failed ({response.status_code})"
            + (f": {error_text[:300]}" if error_text else ".")
        )
    if not isinstance(payload, dict):
        raise RobinhoodMCPError(f"Robinhood {operation} returned an invalid response.")
    return payload


def discover_oauth_metadata() -> dict[str, Any]:
    endpoint = resource_url()
    parsed = urlparse(endpoint)
    protected_metadata_url = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource{parsed.path}"
    try:
        protected_response = requests.get(protected_metadata_url, timeout=15)
    except requests.RequestException as exc:
        raise RobinhoodMCPError(f"Robinhood authorization discovery failed: {type(exc).__name__}") from exc
    protected = _response_json(protected_response, operation="authorization discovery")
    if str(protected.get("resource") or "").rstrip("/") != endpoint:
        raise RobinhoodMCPError("Robinhood protected-resource metadata did not match the MCP endpoint.")
    authorization_servers = protected.get("authorization_servers")
    if not isinstance(authorization_servers, list) or len(authorization_servers) != 1:
        raise RobinhoodMCPError("Robinhood did not advertise exactly one authorization server.")
    issuer = _trusted_robinhood_url(authorization_servers[0], field="authorization server")
    issuer_parsed = urlparse(issuer)
    metadata_url = (
        f"{issuer_parsed.scheme}://{issuer_parsed.netloc}"
        f"/.well-known/oauth-authorization-server{issuer_parsed.path}"
    )
    try:
        metadata_response = requests.get(metadata_url, timeout=15)
    except requests.RequestException as exc:
        raise RobinhoodMCPError(f"Robinhood OAuth metadata request failed: {type(exc).__name__}") from exc
    metadata = _response_json(metadata_response, operation="OAuth metadata request")
    if str(metadata.get("issuer") or "").rstrip("/") != issuer.rstrip("/"):
        raise RobinhoodMCPError("Robinhood OAuth issuer metadata did not match the advertised issuer.")
    for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        metadata[field] = _trusted_robinhood_url(metadata.get(field), field=field)
    if "S256" not in list(metadata.get("code_challenge_methods_supported") or []):
        raise RobinhoodMCPError("Robinhood OAuth does not advertise PKCE S256 support.")
    if "authorization_code" not in list(metadata.get("grant_types_supported") or []):
        raise RobinhoodMCPError("Robinhood OAuth does not advertise the authorization-code grant.")
    if "none" not in list(metadata.get("token_endpoint_auth_methods_supported") or []):
        raise RobinhoodMCPError("Robinhood OAuth does not support public clients.")
    return {
        "resource": endpoint,
        "scope": DEFAULT_SCOPE,
        "protected_resource": protected,
        "issuer": issuer,
        "authorization_endpoint": metadata["authorization_endpoint"],
        "token_endpoint": metadata["token_endpoint"],
        "registration_endpoint": metadata["registration_endpoint"],
        "authorization_server": metadata,
    }


def _register_client(*, metadata: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    registration_payload = {
        "client_name": "Spectral Nature",
        "client_uri": public_base_url(),
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": DEFAULT_SCOPE,
    }
    try:
        response = requests.post(
            metadata["registration_endpoint"],
            json=registration_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RobinhoodMCPError(f"Robinhood client registration failed: {type(exc).__name__}") from exc
    client_info = _response_json(response, operation="client registration")
    client_id = str(client_info.get("client_id") or "").strip()
    if not client_id:
        raise RobinhoodMCPError("Robinhood client registration did not return a client id.")
    if client_info.get("client_secret"):
        raise RobinhoodMCPError("Robinhood unexpectedly issued a client secret to a public client.")
    client_info.pop("registration_access_token", None)
    client_info.pop("registration_client_uri", None)
    return brokerage_mcp_store.upsert_client_registration(
        provider=PROVIDER,
        resource_url=resource_url(),
        redirect_uri=redirect_uri,
        client_id=client_id,
        client_info=client_info,
        oauth_metadata=metadata,
    )


def _client_registration(*, metadata: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    existing = brokerage_mcp_store.get_client_registration(
        provider=PROVIDER,
        resource_url=resource_url(),
        redirect_uri=redirect_uri,
    )
    if existing and str(existing.get("client_id") or "").strip():
        return existing
    return _register_client(metadata=metadata, redirect_uri=redirect_uri)


_cipher = connection_cipher


def _encrypt(value: str) -> str:
    try:
        return encrypt_secret(value)
    except BrokerageCredentialError as exc:
        raise RobinhoodMCPError(str(exc)) from exc


def _decrypt(value: object) -> str:
    try:
        return decrypt_secret(value)
    except BrokerageCredentialError as exc:
        raise RobinhoodMCPAuthorizationError("Stored Robinhood authorization could not be decrypted.") from exc


def begin_authorization(*, user_id: str) -> dict[str, Any]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise RobinhoodMCPAuthorizationError("A signed-in Spectral Nature user is required.")
    _cipher()
    metadata = discover_oauth_metadata()
    redirect_uri = oauth_callback_url()
    client = _client_registration(metadata=metadata, redirect_uri=redirect_uri)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(40)
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    expires_at = _now_utc() + timedelta(minutes=OAUTH_FLOW_TTL_MINUTES)
    flow = brokerage_mcp_store.create_oauth_flow(
        user_id=normalized_user_id,
        provider=PROVIDER,
        resource_url=resource_url(),
        environment=environment_name(),
        client_registration_id=str(client.get("id") or ""),
        redirect_uri=redirect_uri,
        issuer=str(metadata["issuer"]),
        authorization_endpoint=str(metadata["authorization_endpoint"]),
        token_endpoint=str(metadata["token_endpoint"]),
        state_hash=state_hash,
        code_verifier_ciphertext=_encrypt(verifier),
        expires_at=expires_at,
    )
    authorization_url = f"{metadata['authorization_endpoint']}?" + urlencode(
        {
            "response_type": "code",
            "client_id": str(client.get("client_id") or ""),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "scope": DEFAULT_SCOPE,
            "resource": resource_url(),
        }
    )
    return {
        "authorization_url": authorization_url,
        "expires_at": expires_at,
        "flow_id": str(flow.get("id") or ""),
    }


def _token_expiry(payload: dict[str, Any]) -> datetime | None:
    try:
        expires_in = int(payload.get("expires_in") or 0)
    except Exception:
        expires_in = 0
    return _now_utc() + timedelta(seconds=max(expires_in, 0)) if expires_in > 0 else None


def _exchange_authorization_code(*, flow: dict[str, Any], code: str, client_id: str) -> dict[str, Any]:
    try:
        response = requests.post(
            str(flow.get("token_endpoint") or ""),
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": _decrypt(flow.get("code_verifier_ciphertext")),
                "redirect_uri": str(flow.get("redirect_uri") or ""),
                "resource": str(flow.get("resource_url") or resource_url()),
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RobinhoodMCPAuthorizationError(f"Robinhood token exchange failed: {type(exc).__name__}") from exc
    return _response_json(response, operation="token exchange")


def complete_authorization(*, user_id: str, code: str, state: str, issuer: str) -> dict[str, Any]:
    normalized_code = str(code or "").strip()
    normalized_state = str(state or "").strip()
    if not normalized_code or not normalized_state:
        raise RobinhoodMCPAuthorizationError("Robinhood did not return a complete authorization response.")
    state_hash = hashlib.sha256(normalized_state.encode("utf-8")).hexdigest()
    flow = brokerage_mcp_store.consume_oauth_flow(
        state_hash=state_hash,
        user_id=user_id,
        environment=environment_name(),
    )
    if not flow:
        raise RobinhoodMCPAuthorizationError("Robinhood authorization state is invalid, expired, or already used.")
    flow_id = str(flow.get("id") or "")
    connection_id = str(flow.get("connection_id") or "")
    try:
        expected_issuer = str(flow.get("issuer") or "").rstrip("/")
        if not issuer or str(issuer).rstrip("/") != expected_issuer:
            raise RobinhoodMCPAuthorizationError("Robinhood authorization issuer did not match the requested connection.")
        connection = brokerage_mcp_store.get_connection(
            user_id=user_id,
            provider=PROVIDER,
            resource_url=resource_url(),
            environment=environment_name(),
        )
        client_id = str((connection or {}).get("client_id") or "").strip()
        if not client_id:
            raise RobinhoodMCPAuthorizationError("Robinhood OAuth client registration is unavailable.")
        token_payload = _exchange_authorization_code(flow=flow, code=normalized_code, client_id=client_id)
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise RobinhoodMCPAuthorizationError("Robinhood token exchange did not return an access token.")
        brokerage_mcp_store.save_connection_tokens(
            connection_id=connection_id,
            access_token_ciphertext=_encrypt(access_token),
            refresh_token_ciphertext=_encrypt(str(token_payload.get("refresh_token") or "").strip()),
            token_type=str(token_payload.get("token_type") or "Bearer"),
            scope=str(token_payload.get("scope") or DEFAULT_SCOPE),
            token_expires_at=_token_expiry(token_payload),
        )
        brokerage_mcp_store.finish_oauth_flow(flow_id=flow_id, status="completed")
        catalog = refresh_connection(user_id=user_id)
        return {"status": "connected", **catalog}
    except Exception as exc:
        brokerage_mcp_store.finish_oauth_flow(flow_id=flow_id, status="failed", error=str(exc))
        if connection_id:
            brokerage_mcp_store.mark_connection_error(
                connection_id=connection_id,
                status="reauth_required",
                error=str(exc),
            )
        raise


def _refresh_access_token(connection: dict[str, Any]) -> str:
    refresh_token = _decrypt(connection.get("refresh_token_ciphertext"))
    if not refresh_token:
        raise RobinhoodMCPAuthorizationError("Robinhood authorization has expired. Reconnect the account.")
    metadata = connection.get("oauth_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    token_endpoint = _trusted_robinhood_url(metadata.get("token_endpoint"), field="token endpoint")
    client_id = str(connection.get("client_id") or "").strip()
    try:
        response = requests.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "scope": str(connection.get("scope") or DEFAULT_SCOPE),
                "resource": resource_url(),
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RobinhoodMCPAuthorizationError(f"Robinhood token refresh failed: {type(exc).__name__}") from exc
    payload = _response_json(response, operation="token refresh")
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RobinhoodMCPAuthorizationError("Robinhood token refresh did not return an access token.")
    replacement_refresh = str(payload.get("refresh_token") or "").strip() or refresh_token
    brokerage_mcp_store.save_connection_tokens(
        connection_id=str(connection.get("id") or ""),
        access_token_ciphertext=_encrypt(access_token),
        refresh_token_ciphertext=_encrypt(replacement_refresh),
        token_type=str(payload.get("token_type") or connection.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or connection.get("scope") or DEFAULT_SCOPE),
        token_expires_at=_token_expiry(payload),
    )
    return access_token


def _access_token_for_user(user_id: str) -> tuple[dict[str, Any], str]:
    connection = brokerage_mcp_store.get_connection(
        user_id=user_id,
        provider=PROVIDER,
        resource_url=resource_url(),
        environment=environment_name(),
    )
    if not connection or str(connection.get("status") or "") not in {"connected", "error"}:
        raise RobinhoodMCPAuthorizationError("Robinhood is not connected for this user.")
    expiry = connection.get("token_expires_at")
    if isinstance(expiry, datetime) and expiry <= _now_utc() + timedelta(seconds=60):
        try:
            return connection, _refresh_access_token(connection)
        except Exception as exc:
            brokerage_mcp_store.mark_connection_error(
                connection_id=str(connection.get("id") or ""),
                error=str(exc),
                status="reauth_required",
            )
            raise
    access_token = _decrypt(connection.get("access_token_ciphertext"))
    if not access_token:
        raise RobinhoodMCPAuthorizationError("Robinhood authorization is unavailable. Reconnect the account.")
    return connection, access_token


async def _list_tools_async(access_token: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp_types import PaginatedRequestParams

    tools: list[dict[str, Any]] = []
    timeout = httpx2.Timeout(30.0, connect=10.0, read=45.0, write=30.0, pool=10.0)
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    ) as http_client:
        async with streamable_http_client(resource_url(), http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=45.0) as session:
                initialized = await session.initialize()
                cursor: str | None = None
                while True:
                    result = await session.list_tools(
                        params=PaginatedRequestParams(cursor=cursor) if cursor else None
                    )
                    tools.extend(
                        item.model_dump(by_alias=True, exclude_none=True)
                        for item in list(result.tools or [])
                    )
                    cursor = result.next_cursor
                    if not cursor:
                        break
                server_info = initialized.server_info.model_dump(by_alias=True, exclude_none=True)
                return tools, server_info


async def _call_tool_async(access_token: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    timeout = httpx2.Timeout(30.0, connect=10.0, read=60.0, write=30.0, pool=10.0)
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    ) as http_client:
        async with streamable_http_client(resource_url(), http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=60.0) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return result.model_dump(by_alias=True, exclude_none=True)


async def _call_tools_async(
    access_token: str,
    calls: list[tuple[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    results: dict[str, dict[str, Any]] = {}
    timeout = httpx2.Timeout(30.0, connect=10.0, read=60.0, write=30.0, pool=10.0)
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    ) as http_client:
        async with streamable_http_client(resource_url(), http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=60.0) as session:
                await session.initialize()
                for tool_name, arguments in calls:
                    try:
                        result = await session.call_tool(tool_name, arguments)
                        results[tool_name] = result.model_dump(by_alias=True, exclude_none=True)
                    except Exception as exc:
                        results[tool_name] = {
                            "isError": True,
                            "content": [],
                            "localError": f"{type(exc).__name__}: {exc}",
                        }
    return results


def connection_status(*, user_id: str) -> dict[str, Any]:
    connection = brokerage_mcp_store.get_connection(
        user_id=user_id,
        provider=PROVIDER,
        resource_url=resource_url(),
        environment=environment_name(),
    )
    if not connection:
        return {"status": "disconnected", "tool_count": 0}
    return {
        "status": str(connection.get("status") or "disconnected"),
        "tool_count": len(list(connection.get("tool_catalog") or [])),
        "connected_at": connection.get("connected_at"),
        "last_verified_at": connection.get("last_verified_at"),
        "token_expires_at": connection.get("token_expires_at"),
        "last_error": str(connection.get("last_error") or ""),
        "server_info": dict(connection.get("server_info") or {}),
    }


def refresh_connection(*, user_id: str) -> dict[str, Any]:
    connection, access_token = _access_token_for_user(user_id)
    try:
        tools, server_info = anyio.run(_list_tools_async, access_token)
    except Exception as exc:
        brokerage_mcp_store.mark_connection_error(
            connection_id=str(connection.get("id") or ""),
            error=f"{type(exc).__name__}: {exc}",
            status="error",
        )
        raise RobinhoodMCPError(f"Robinhood MCP verification failed: {type(exc).__name__}: {exc}") from exc
    brokerage_mcp_store.update_connection_catalog(
        connection_id=str(connection.get("id") or ""),
        tools=tools,
        server_info=server_info,
    )
    return {"tool_count": len(tools), "server_info": server_info}


def disconnect(*, user_id: str) -> bool:
    return brokerage_mcp_store.disconnect_connection(
        user_id=user_id,
        provider=PROVIDER,
        resource_url=resource_url(),
        environment=environment_name(),
    )


def classify_tool(tool_name: object) -> str:
    name = str(tool_name or "").strip()
    if name in FOREGROUND_READ_TOOLS:
        return "foreground_read"
    if name in ORDER_REVIEW_TOOLS:
        return "order_review"
    if name in EXECUTION_TOOLS:
        return "execution"
    if name in OTHER_MUTATION_TOOLS:
        return "mutation"
    return "unknown"


def _catalog_tool(connection: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    for item in list(connection.get("tool_catalog") or []):
        if isinstance(item, dict) and str(item.get("name") or "") == tool_name:
            return item
    return None


def _catalog_input_schema(connection: dict[str, Any], tool_name: str) -> dict[str, Any]:
    tool = _catalog_tool(connection, tool_name)
    if tool is None:
        raise RobinhoodMCPPolicyError(
            f"Robinhood tool '{tool_name}' is not present in the connected server catalog."
        )
    schema = tool.get("inputSchema") or tool.get("input_schema")
    if not isinstance(schema, dict) or not schema:
        raise RobinhoodMCPPolicyError(
            f"Robinhood tool '{tool_name}' did not publish a usable input schema."
        )
    return schema


def _validate_catalog_arguments(
    connection: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    schema = _catalog_input_schema(connection, tool_name)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(arguments),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path)
        location = f" at '{path}'" if path else ""
        raise RobinhoodMCPPolicyError(
            f"Robinhood rejected the {tool_name} input shape{location}: {errors[0].message}"
        )


def _positive_decimal_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        numeric = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise RobinhoodMCPPolicyError(f"{field} must be a positive number.") from exc
    if not numeric.is_finite() or numeric <= 0:
        raise RobinhoodMCPPolicyError(f"{field} must be a positive number.")
    return format(numeric, "f")


def normalize_equity_order_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Create one canonical equity order payload shared by review and placement."""
    allowed = {
        "account_number",
        "side",
        "symbol",
        "type",
        "dollar_amount",
        "limit_price",
        "market_hours",
        "quantity",
        "stop_price",
        "tax_lots",
        "time_in_force",
    }
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise RobinhoodMCPPolicyError(f"Unsupported equity order field: {unknown[0]}")
    normalized: dict[str, Any] = {
        "account_number": str(arguments.get("account_number") or "").strip(),
        "side": str(arguments.get("side") or "").strip().lower(),
        "symbol": str(arguments.get("symbol") or "").strip().upper(),
        "type": str(arguments.get("type") or "").strip().lower(),
    }
    if not normalized["account_number"]:
        raise RobinhoodMCPPolicyError("An agent-enabled Robinhood account is required.")
    if normalized["side"] not in {"buy", "sell"}:
        raise RobinhoodMCPPolicyError("Equity order side must be buy or sell.")
    if not normalized["symbol"] or len(normalized["symbol"]) > 32:
        raise RobinhoodMCPPolicyError("A valid equity symbol is required.")
    if normalized["type"] not in {"market", "limit", "stop_market", "stop_limit"}:
        raise RobinhoodMCPPolicyError("Unsupported equity order type.")

    quantity = str(arguments.get("quantity") or "").strip()
    dollar_amount = str(arguments.get("dollar_amount") or "").strip()
    if bool(quantity) == bool(dollar_amount):
        raise RobinhoodMCPPolicyError("Choose exactly one order size: shares or dollars.")
    if quantity:
        normalized["quantity"] = _positive_decimal_text(quantity, field="Quantity")
    else:
        if normalized["type"] != "market":
            raise RobinhoodMCPPolicyError("Dollar-sized equity orders must use market order type.")
        normalized["dollar_amount"] = _positive_decimal_text(dollar_amount, field="Dollar amount")

    for key, label in (("limit_price", "Limit price"), ("stop_price", "Stop price")):
        value = str(arguments.get(key) or "").strip()
        if value:
            normalized[key] = _positive_decimal_text(value, field=label)
    if normalized["type"] in {"limit", "stop_limit"} and "limit_price" not in normalized:
        raise RobinhoodMCPPolicyError("This order type requires a limit price.")
    if normalized["type"] in {"stop_market", "stop_limit"} and "stop_price" not in normalized:
        raise RobinhoodMCPPolicyError("This order type requires a stop price.")

    market_hours = str(arguments.get("market_hours") or "regular_hours").strip().lower()
    if market_hours not in {"regular_hours", "extended_hours", "all_day_hours"}:
        raise RobinhoodMCPPolicyError("Unsupported market-hours setting.")
    if market_hours != "regular_hours" and normalized["type"] != "limit":
        raise RobinhoodMCPPolicyError("Extended and all-day orders must use limit order type.")
    normalized["market_hours"] = market_hours

    time_in_force = str(arguments.get("time_in_force") or "gfd").strip().lower()
    if time_in_force not in {"gfd", "gtc"}:
        raise RobinhoodMCPPolicyError("Unsupported time-in-force setting.")
    normalized["time_in_force"] = time_in_force
    tax_lots = arguments.get("tax_lots")
    if tax_lots not in (None, [], {}):
        if normalized["side"] != "sell" or not isinstance(tax_lots, list):
            raise RobinhoodMCPPolicyError("Tax lots require a share-sized sell order.")
        normalized["tax_lots"] = tax_lots
    return normalized


def _arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redacted_arguments(value: Any, *, key: str = "") -> Any:
    lower_key = key.lower()
    if any(marker in lower_key for marker in ("token", "secret", "authorization", "code_verifier")):
        return "[redacted]"
    if "account" in lower_key and value not in (None, ""):
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
        return f"account:{digest}"
    if isinstance(value, dict):
        return {str(k): _redacted_arguments(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted_arguments(item, key=key) for item in value[:50]]
    return value


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") if isinstance(result, dict) else []
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    return {
        "is_error": bool(result.get("isError")) if isinstance(result, dict) else True,
        "content_types": sorted(
            {
                str(item.get("type") or "unknown")
                for item in list(content or [])
                if isinstance(item, dict)
            }
        ),
        "content_count": len(list(content or [])),
        "structured_keys": sorted(str(key) for key in structured.keys())[:50]
        if isinstance(structured, dict)
        else [],
    }


def invoke_tool(
    *,
    user_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    intent: str = "foreground_read",
    candidate_id: str = "",
) -> dict[str, Any]:
    normalized_name = str(tool_name or "").strip()
    policy = classify_tool(normalized_name)
    if policy == "unknown" or policy == "mutation":
        raise RobinhoodMCPPolicyError(f"Robinhood tool '{normalized_name}' is not enabled in Spectral Nature.")
    if policy != intent:
        raise RobinhoodMCPPolicyError(
            f"Robinhood tool '{normalized_name}' requires intent '{policy}', not '{intent}'."
        )
    if policy == "execution":
        raise RobinhoodMCPPolicyError(
            "Robinhood execution is only available through an unexpired reviewed-order confirmation."
        )
    connection, access_token = _access_token_for_user(user_id)
    if _catalog_tool(connection, normalized_name) is None:
        raise RobinhoodMCPPolicyError(
            f"Robinhood tool '{normalized_name}' is not present in the connected server catalog."
        )
    normalized_arguments = dict(arguments or {})
    audit_id = brokerage_mcp_store.start_audit_event(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        tool_name=normalized_name,
        intent=intent,
        request_summary={"arguments": _redacted_arguments(normalized_arguments)},
        candidate_id=candidate_id,
    )
    try:
        result = anyio.run(_call_tool_async, access_token, normalized_name, normalized_arguments)
        summary = _result_summary(result)
        status = "failed" if summary["is_error"] else "completed"
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_id,
            status=status,
            result_summary=summary,
        )
        return result
    except Exception as exc:
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise RobinhoodMCPError(
            f"Robinhood tool '{normalized_name}' failed: {type(exc).__name__}: {exc}"
        ) from exc


def _masked_account_number(value: object) -> str:
    text = str(value or "").strip()
    return f"••••{text[-4:]}" if text else ""


def _safe_provider_payload(value: Any, *, account_number: str) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lower_key = str(key).lower()
            if any(marker in lower_key for marker in ("token", "secret", "authorization")):
                continue
            if "account" in lower_key and item not in (None, "") and not isinstance(item, (dict, list)):
                safe[str(key)] = _masked_account_number(item)
            else:
                safe[str(key)] = _safe_provider_payload(item, account_number=account_number)
        return safe
    if isinstance(value, list):
        return [_safe_provider_payload(item, account_number=account_number) for item in value[:100]]
    if isinstance(value, str) and account_number:
        return value.replace(account_number, _masked_account_number(account_number))
    return value


def _equity_order_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "account": _masked_account_number(arguments.get("account_number")),
        "side": arguments.get("side"),
        "symbol": arguments.get("symbol"),
        "type": arguments.get("type"),
        "market_hours": arguments.get("market_hours"),
        "time_in_force": arguments.get("time_in_force"),
    }
    for key in ("quantity", "dollar_amount", "limit_price", "stop_price"):
        if arguments.get(key) not in (None, ""):
            summary[key] = arguments[key]
    return summary


def review_equity_order(*, user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMCPPolicyError("Robinhood order execution is disabled in this environment.")
    normalized = normalize_equity_order_arguments(arguments)
    connection, _ = _access_token_for_user(user_id)
    _validate_catalog_arguments(connection, EQUITY_REVIEW_TOOL, normalized)
    reference_id = str(uuid.uuid4())
    placement_arguments = {**normalized, "ref_id": reference_id}
    _validate_catalog_arguments(connection, EQUITY_EXECUTION_TOOL, placement_arguments)
    review_result = invoke_tool(
        user_id=user_id,
        tool_name=EQUITY_REVIEW_TOOL,
        arguments=normalized,
        intent="order_review",
    )
    if bool(review_result.get("isError")):
        raise RobinhoodMCPError("Robinhood could not review this order.")
    expires_at = _now_utc() + timedelta(seconds=_order_ticket_ttl_seconds())
    summary = _equity_order_summary(normalized)
    ticket = brokerage_mcp_store.create_order_ticket(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        review_tool_name=EQUITY_REVIEW_TOOL,
        execution_tool_name=EQUITY_EXECUTION_TOOL,
        arguments_hash=_arguments_hash(normalized),
        ref_id=reference_id,
        request_summary=summary,
        expires_at=expires_at,
    )
    return {
        "ticket_id": str(ticket.get("id") or ""),
        "expires_at": ticket.get("expires_at") or expires_at,
        "order": summary,
        "review": _safe_provider_payload(
            tool_result_payload(review_result),
            account_number=str(normalized.get("account_number") or ""),
        ),
    }


def confirm_equity_order(
    *,
    user_id: str,
    ticket_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMCPPolicyError("Robinhood order execution is disabled in this environment.")
    normalized = normalize_equity_order_arguments(arguments)
    connection, access_token = _access_token_for_user(user_id)
    ticket = brokerage_mcp_store.consume_order_ticket(
        ticket_id=str(ticket_id or "").strip(),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        arguments_hash=_arguments_hash(normalized),
    )
    if not ticket:
        raise RobinhoodMCPPolicyError(
            "This order confirmation is invalid, expired, changed, or already used. Review the order again."
        )
    if str(ticket.get("connection_id") or "") != str(connection.get("id") or ""):
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=str(ticket.get("id") or ""),
            status="failed",
            error="The Robinhood connection changed after order review.",
        )
        raise RobinhoodMCPPolicyError("The Robinhood connection changed. Review the order again.")
    placement_arguments = {**normalized, "ref_id": str(ticket.get("ref_id") or "")}
    try:
        _validate_catalog_arguments(connection, EQUITY_EXECUTION_TOOL, placement_arguments)
    except Exception as exc:
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=str(ticket.get("id") or ""),
            status="failed",
            error=str(exc),
        )
        raise
    try:
        audit_id = brokerage_mcp_store.start_audit_event(
            connection_id=str(connection.get("id") or ""),
            user_id=user_id,
            provider=PROVIDER,
            environment=environment_name(),
            tool_name=EQUITY_EXECUTION_TOOL,
            intent="execution",
            request_summary={"arguments": _redacted_arguments(placement_arguments)},
            candidate_id=str(ticket.get("id") or ""),
        )
    except Exception as exc:
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=str(ticket.get("id") or ""),
            status="failed",
            error=f"Execution audit could not be started: {exc}",
        )
        raise RobinhoodMCPError("The order was not sent because its execution audit could not be started.") from exc
    try:
        result = anyio.run(
            _call_tool_async,
            access_token,
            EQUITY_EXECUTION_TOOL,
            placement_arguments,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_id,
            status="unknown",
            error=message,
        )
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=str(ticket.get("id") or ""),
            status="unknown",
            error=message,
        )
        raise RobinhoodMCPError(
            "Robinhood order status is unknown after a connection failure. Check open orders before taking another action."
        ) from exc
    result_summary = _result_summary(result)
    terminal_status = "failed" if result_summary["is_error"] else "executed"
    brokerage_mcp_store.finish_audit_event(
        audit_id=audit_id,
        status=terminal_status,
        result_summary=result_summary,
    )
    brokerage_mcp_store.finish_order_ticket(
        ticket_id=str(ticket.get("id") or ""),
        status=terminal_status,
        result_summary=result_summary,
    )
    return {
        "status": terminal_status,
        "order": _equity_order_summary(normalized),
        "result": _safe_provider_payload(
            tool_result_payload(result),
            account_number=str(normalized.get("account_number") or ""),
        ),
    }


def cancel_equity_order_review(*, user_id: str, ticket_id: str) -> bool:
    return brokerage_mcp_store.cancel_order_ticket(
        ticket_id=str(ticket_id or "").strip(),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
    )


def tool_result_payload(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if structured not in (None, {}, []):
        return structured
    for item in list((result or {}).get("content") or []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except Exception:
            return text
    return None


def _agent_enabled_accounts(value: Any) -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        normalized_keys = {
            "".join(char for char in str(key).lower() if char.isalnum()): key
            for key in item
        }
        account_key = normalized_keys.get("accountnumber")
        agentic_key = normalized_keys.get("agenticallowed")
        if account_key is not None and agentic_key is not None:
            account_number = str(item.get(account_key) or "").strip()
            agentic_value = item.get(agentic_key)
            is_agentic = agentic_value is True or str(agentic_value).strip().lower() == "true"
            if account_number and is_agentic and account_number not in seen:
                seen.add(account_number)
                account_type = ""
                for candidate in ("accounttype", "brokerageaccounttype", "type"):
                    source_key = normalized_keys.get(candidate)
                    if source_key is not None:
                        account_type = str(item.get(source_key) or "").strip()
                        if account_type:
                            break
                accounts.append(
                    {
                        "account_number": account_number,
                        "label": " ".join(
                            part for part in (account_type, _masked_account_number(account_number)) if part
                        ),
                    }
                )
        for child in item.values():
            walk(child)

    walk(value)
    return accounts


def load_account_snapshot(*, user_id: str) -> dict[str, Any]:
    connection, access_token = _access_token_for_user(user_id)
    available = {
        str(item.get("name") or "")
        for item in list(connection.get("tool_catalog") or [])
        if isinstance(item, dict)
    }
    tool_names = [
        tool_name
        for tool_name in (
            "get_accounts",
            "get_portfolio",
            "get_equity_positions",
            "get_trade_approval_setting",
        )
        if tool_name in available and classify_tool(tool_name) == "foreground_read"
    ]
    snapshot: dict[str, Any] = {"results": {}, "errors": {}, "agentic_accounts": []}
    if not tool_names:
        return snapshot
    audit_ids = {
        tool_name: brokerage_mcp_store.start_audit_event(
            connection_id=str(connection.get("id") or ""),
            user_id=user_id,
            provider=PROVIDER,
            environment=environment_name(),
            tool_name=tool_name,
            intent="foreground_read",
            request_summary={"arguments": {}},
        )
        for tool_name in tool_names
    }
    try:
        batch_results = anyio.run(
            _call_tools_async,
            access_token,
            [(tool_name, {}) for tool_name in tool_names],
        )
    except Exception as exc:
        for tool_name, audit_id in audit_ids.items():
            message = f"{type(exc).__name__}: {exc}"
            brokerage_mcp_store.finish_audit_event(
                audit_id=audit_id,
                status="failed",
                error=message,
            )
            snapshot["errors"][tool_name] = message
        return snapshot
    for tool_name, result in batch_results.items():
        summary = _result_summary(result)
        error = str(result.get("localError") or "")
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_ids[tool_name],
            status="failed" if summary["is_error"] else "completed",
            result_summary=summary,
            error=error,
        )
        if summary["is_error"]:
            snapshot["errors"][tool_name] = error or "Robinhood returned an error."
        else:
            payload = tool_result_payload(result)
            if tool_name == "get_accounts":
                snapshot["agentic_accounts"] = _agent_enabled_accounts(payload)
            snapshot["results"][tool_name] = _safe_provider_payload(payload, account_number="")
    return snapshot


__all__ = [
    "RobinhoodMCPAuthorizationError",
    "RobinhoodMCPError",
    "RobinhoodMCPPolicyError",
    "begin_authorization",
    "cancel_equity_order_review",
    "classify_tool",
    "confirm_equity_order",
    "complete_authorization",
    "connection_status",
    "disconnect",
    "discover_oauth_metadata",
    "environment_name",
    "execution_enabled",
    "invoke_tool",
    "load_account_snapshot",
    "normalize_equity_order_arguments",
    "oauth_callback_url",
    "refresh_connection",
    "review_equity_order",
    "resource_url",
    "tool_result_payload",
]
