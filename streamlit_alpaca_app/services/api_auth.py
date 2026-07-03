from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

from . import auth_service, auth_store
from .secrets import resolve_secret_value


SCOPE_CAPABILITIES_READ = "capabilities:read"
SCOPE_DATASET_READ = "dataset:read"
SCOPE_CHART_READ = "chart:read"
SCOPE_QUERY_EXECUTE = "query:execute"
SCOPE_ZOPEDIA_RESOLVE = "zopedia:resolve"
SCOPE_MCP_INVOKE = "mcp:invoke"
SCOPE_AGENT_RUN = "agent:run"
SCOPE_AGENT_KEY_READ = "auth:agent_keys:read"
SCOPE_AGENT_KEY_WRITE = "auth:agent_keys:write"


AGENT_SCOPE_ALLOWLIST: set[str] = {
    SCOPE_CAPABILITIES_READ,
    SCOPE_DATASET_READ,
    SCOPE_CHART_READ,
    SCOPE_QUERY_EXECUTE,
    SCOPE_ZOPEDIA_RESOLVE,
    SCOPE_MCP_INVOKE,
    SCOPE_AGENT_RUN,
}

DEFAULT_USER_SCOPES: set[str] = set(AGENT_SCOPE_ALLOWLIST)
ADMIN_EXTRA_SCOPES: set[str] = {
    SCOPE_AGENT_KEY_READ,
    SCOPE_AGENT_KEY_WRITE,
}


_RUNTIME_FALLBACK_ACCESS_TOKEN_SECRET = secrets.token_urlsafe(48)
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthPrincipal:
    principal_type: str
    scopes: tuple[str, ...]
    auth_source: str
    user_context: auth_service.UserContext | None = None
    session_token_hash: str = ""
    agent_key_id: str = ""
    agent_key_name: str = ""

    @property
    def is_authenticated(self) -> bool:
        return self.principal_type in {"user", "agent"}

    @property
    def is_admin(self) -> bool:
        return bool(self.user_context is not None and self.user_context.is_admin)

    def has_scope(self, required_scope: str) -> bool:
        return required_scope in set(self.scopes)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps_compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _b64url_decode(payload: str) -> bytes:
    clean = str(payload or "").strip()
    if not clean:
        raise ValueError("Empty payload.")
    padding = "=" * ((4 - (len(clean) % 4)) % 4)
    return base64.urlsafe_b64decode((clean + padding).encode("ascii"))


def _session_ttl_seconds() -> int:
    raw = (os.getenv("AUTH_SESSION_TTL_SECONDS") or str(7 * 24 * 60 * 60)).strip()
    try:
        return max(int(raw), 300)
    except Exception:
        return 7 * 24 * 60 * 60


def _access_token_ttl_seconds() -> int:
    raw = (os.getenv("API_ACCESS_TOKEN_TTL_SECONDS") or "900").strip()
    try:
        return max(int(raw), 60)
    except Exception:
        return 900


def _access_token_issuer() -> str:
    return (os.getenv("API_ACCESS_TOKEN_ISSUER") or "spectral-nature-api").strip() or "spectral-nature-api"


def _allow_ephemeral_access_token_secret() -> bool:
    return (os.getenv("API_ALLOW_EPHEMERAL_ACCESS_TOKEN_SECRET") or "").strip().lower() in _TRUE_VALUES


def _allow_legacy_session_bearer_tokens() -> bool:
    return (os.getenv("API_ALLOW_LEGACY_SESSION_BEARER") or "").strip().lower() in _TRUE_VALUES


def _access_token_secret() -> str:
    configured = resolve_secret_value(
        ["API_ACCESS_TOKEN_SECRET"],
        secret_name_env="API_ACCESS_TOKEN_SECRET_NAME",
        default_secret_name="api-access-token-secret",
    )
    if configured:
        return configured
    if _allow_ephemeral_access_token_secret():
        return _RUNTIME_FALLBACK_ACCESS_TOKEN_SECRET
    raise RuntimeError(
        "API access token signing secret is not configured. "
        "Set API_ACCESS_TOKEN_SECRET or API_ACCESS_TOKEN_SECRET_NAME."
    )


def _scopes_for_user_context(context: auth_service.UserContext) -> list[str]:
    scopes = set(DEFAULT_USER_SCOPES)
    if context.is_admin:
        scopes.update(ADMIN_EXTRA_SCOPES)
    return sorted(scopes)


def normalize_agent_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = [str(item or "").strip() for item in list(scopes or [])]
    allowed = [item for item in requested if item in AGENT_SCOPE_ALLOWLIST]
    if not allowed:
        allowed = sorted(AGENT_SCOPE_ALLOWLIST)
    return sorted(set(allowed))


def _encode_access_token_payload(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64url_encode(_json_dumps_compact(header).encode("utf-8"))
    encoded_payload = _b64url_encode(_json_dumps_compact(payload).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(_access_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_signature = _b64url_encode(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def _decode_access_token_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token.")
    header_b64, payload_b64, signature_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_signature = hmac.new(_access_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_signature = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise ValueError("Invalid token signature.")

    header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    if str(header.get("alg") or "") != "HS256":
        raise ValueError("Unsupported token algorithm.")
    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid token payload.")
    return payload


def _build_access_token(
    *,
    context: auth_service.UserContext,
    session_token_hash: str,
    scopes: list[str],
) -> tuple[str, datetime]:
    now = _now_utc()
    expires_at = now + timedelta(seconds=_access_token_ttl_seconds())
    payload = {
        "iss": _access_token_issuer(),
        "sub": context.user_id,
        "email": context.email,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
        "scopes": list(scopes),
        "sth": session_token_hash,
        "role": context.role,
        "membership_role": context.membership_role,
        "portfolio_id": context.portfolio_id,
    }
    return _encode_access_token_payload(payload), expires_at


def _session_row_from_refresh_token(refresh_token: str) -> dict[str, Any] | None:
    token = str(refresh_token or "").strip()
    if not token:
        return None
    return auth_store.get_user_context_for_session(auth_service.token_digest(token))


def issue_user_tokens_from_password(
    *,
    email: str,
    password: str,
    user_agent: str = "",
    ip_address: str = "",
) -> dict[str, Any]:
    login_result = auth_service.authenticate_user(
        email=email,
        password=password,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    if not bool(login_result.get("ok")):
        return {
            "ok": False,
            "message": str(login_result.get("message") or "Invalid email or password."),
        }

    context = login_result.get("context")
    if not isinstance(context, auth_service.UserContext):
        return {"ok": False, "message": "Login context could not be resolved."}
    refresh_token = str(login_result.get("session_token") or "").strip()
    if not refresh_token:
        return {"ok": False, "message": "Refresh token could not be issued."}

    session_token_hash = auth_service.token_digest(refresh_token)
    access_scopes = _scopes_for_user_context(context)
    access_token, access_expires_at = _build_access_token(
        context=context,
        session_token_hash=session_token_hash,
        scopes=access_scopes,
    )
    session_row = auth_store.get_user_context_for_session(session_token_hash)
    refresh_expires_at = session_row.get("expires_at") if isinstance(session_row, dict) else (_now_utc() + timedelta(seconds=_session_ttl_seconds()))
    return {
        "ok": True,
        "message": "",
        "token_type": "bearer",
        "access_token": access_token,
        "access_token_expires_at": access_expires_at,
        "refresh_token": refresh_token,
        "refresh_token_expires_at": refresh_expires_at,
        "scopes": access_scopes,
        "context": context,
    }


def refresh_user_tokens(
    *,
    refresh_token: str,
    user_agent: str = "",
    ip_address: str = "",
    rotate_refresh_token: bool = True,
) -> dict[str, Any]:
    session_row = _session_row_from_refresh_token(refresh_token)
    if not isinstance(session_row, dict):
        return {"ok": False, "message": "Refresh token is invalid or expired."}

    context = auth_service.UserContext.from_dict(session_row)
    if context is None:
        return {"ok": False, "message": "Session context is invalid."}

    refresh_out = str(refresh_token or "").strip()
    refresh_expires_out = session_row.get("expires_at")
    session_token_hash = auth_service.token_digest(refresh_out)
    if rotate_refresh_token:
        new_refresh_token = auth_service.generate_token()
        session_token_hash = auth_service.token_digest(new_refresh_token)
        refresh_expires_out = _now_utc() + timedelta(seconds=_session_ttl_seconds())
        auth_store.create_session(
            user_id=context.user_id,
            session_token_hash=session_token_hash,
            expires_at=refresh_expires_out,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        auth_store.revoke_session(auth_service.token_digest(refresh_out))
        refresh_out = new_refresh_token

    access_scopes = _scopes_for_user_context(context)
    access_token, access_expires_at = _build_access_token(
        context=context,
        session_token_hash=session_token_hash,
        scopes=access_scopes,
    )
    return {
        "ok": True,
        "message": "",
        "token_type": "bearer",
        "access_token": access_token,
        "access_token_expires_at": access_expires_at,
        "refresh_token": refresh_out,
        "refresh_token_expires_at": refresh_expires_out,
        "scopes": access_scopes,
        "context": context,
    }


def principal_from_access_token(access_token: str) -> AuthPrincipal | None:
    try:
        payload = _decode_access_token_payload(access_token)
    except Exception:
        return None
    if str(payload.get("typ") or "") != "access":
        return None
    if str(payload.get("iss") or "") != _access_token_issuer():
        return None
    now_epoch = int(_now_utc().timestamp())
    if int(payload.get("exp") or 0) <= now_epoch:
        return None
    session_token_hash = str(payload.get("sth") or "").strip()
    if not session_token_hash:
        return None

    session_row = auth_store.get_user_context_for_session(session_token_hash)
    if not isinstance(session_row, dict):
        return None
    context = auth_service.UserContext.from_dict(session_row)
    if context is None:
        return None
    if str(payload.get("sub") or "") and str(payload.get("sub") or "") != context.user_id:
        return None
    token_scopes = [str(item or "").strip() for item in list(payload.get("scopes") or []) if str(item or "").strip()]
    fallback_scopes = _scopes_for_user_context(context)
    scopes = sorted(set(token_scopes or fallback_scopes))
    return AuthPrincipal(
        principal_type="user",
        scopes=tuple(scopes),
        auth_source="access_token",
        user_context=context,
        session_token_hash=session_token_hash,
    )


def principal_from_legacy_session_token(session_token: str) -> AuthPrincipal | None:
    if not _allow_legacy_session_bearer_tokens():
        return None
    row = _session_row_from_refresh_token(session_token)
    if not isinstance(row, dict):
        return None
    context = auth_service.UserContext.from_dict(row)
    if context is None:
        return None
    return AuthPrincipal(
        principal_type="user",
        scopes=tuple(_scopes_for_user_context(context)),
        auth_source="legacy_session_token",
        user_context=context,
        session_token_hash=auth_service.token_digest(session_token),
    )


def create_agent_api_key(
    *,
    name: str,
    scopes: list[str] | None,
    created_by: str | None = None,
    expires_at: datetime | None = None,
    notes: str = "",
) -> dict[str, Any]:
    normalized_scopes = normalize_agent_scopes(scopes)
    raw_key = f"snak_{secrets.token_urlsafe(40)}"
    key_row = auth_store.create_agent_api_key(
        name=name,
        key_prefix=raw_key[:14],
        token_hash=auth_service.token_digest(raw_key),
        scopes=normalized_scopes,
        created_by=created_by,
        expires_at=expires_at,
        notes=notes,
    )
    return {
        "api_key": raw_key,
        "key": key_row,
    }


def principal_from_agent_api_key(api_key: str) -> AuthPrincipal | None:
    raw = str(api_key or "").strip()
    if not raw:
        return None
    key_row = auth_store.get_agent_api_key_by_hash(
        auth_service.token_digest(raw),
        touch_last_used=True,
    )
    if not isinstance(key_row, dict):
        return None
    scopes = normalize_agent_scopes(list(key_row.get("scopes") or []))
    return AuthPrincipal(
        principal_type="agent",
        scopes=tuple(scopes),
        auth_source="agent_api_key",
        agent_key_id=str(key_row.get("id") or ""),
        agent_key_name=str(key_row.get("name") or ""),
    )


def list_agent_api_keys() -> list[dict[str, Any]]:
    return auth_store.list_agent_api_keys()


def revoke_agent_api_key(*, key_id: str, revoked_by: str | None = None) -> dict[str, Any]:
    return auth_store.revoke_agent_api_key(key_id=key_id, revoked_by=revoked_by)


__all__ = [
    "ADMIN_EXTRA_SCOPES",
    "AGENT_SCOPE_ALLOWLIST",
    "AuthPrincipal",
    "DEFAULT_USER_SCOPES",
    "SCOPE_AGENT_KEY_READ",
    "SCOPE_AGENT_KEY_WRITE",
    "SCOPE_AGENT_RUN",
    "SCOPE_CAPABILITIES_READ",
    "SCOPE_CHART_READ",
    "SCOPE_DATASET_READ",
    "SCOPE_MCP_INVOKE",
    "SCOPE_ZOPEDIA_RESOLVE",
    "SCOPE_QUERY_EXECUTE",
    "create_agent_api_key",
    "issue_user_tokens_from_password",
    "list_agent_api_keys",
    "normalize_agent_scopes",
    "principal_from_access_token",
    "principal_from_agent_api_key",
    "principal_from_legacy_session_token",
    "refresh_user_tokens",
    "revoke_agent_api_key",
]
