from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import uuid
from typing import Any

from .secrets import resolve_secret_value


try:
    import psycopg
except Exception:
    psycopg = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _schema_name() -> str:
    raw = (os.getenv("APP_ACCESS_SCHEMA") or "app_access").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw
    return "app_access"


def _postgres_connection_string() -> str:
    return resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )


def auth_store_configured() -> bool:
    return bool(_postgres_connection_string() and psycopg is not None)


def _db_connect() -> Any | None:
    conn_str = _postgres_connection_string()
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
    except Exception:
        return None


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    columns = [item.name if hasattr(item, "name") else item[0] for item in cursor.description or []]
    return {str(key): value for key, value in zip(columns, row)}


def _fetchone_dict(cursor: Any) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_dict(cursor, row)


def _fetchall_dicts(cursor: Any) -> list[dict[str, Any]]:
    return [_row_dict(cursor, row) for row in cursor.fetchall() or []]


def _normalize_access_dashboard_user(*, user_id: str | None = None, user_email: str | None = None) -> tuple[str, str]:
    normalized_user_id = ""
    raw_user_id = str(user_id or "").strip()
    if raw_user_id:
        try:
            normalized_user_id = str(uuid.UUID(raw_user_id))
        except Exception:
            normalized_user_id = ""
    normalized_user_email = str(user_email or "").strip().lower()
    return normalized_user_id, normalized_user_email


def _decode_access_event_detail(detail_value: Any) -> dict[str, Any]:
    if isinstance(detail_value, dict):
        return detail_value
    if isinstance(detail_value, str):
        try:
            parsed = json.loads(detail_value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _hydrate_access_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["detail"] = _decode_access_event_detail(row.get("detail_json"))
        row.pop("detail_json", None)
    return rows


def _default_portfolio_slug() -> str:
    return (os.getenv("APP_DEFAULT_PORTFOLIO_SLUG") or "master-portfolio").strip() or "master-portfolio"


def _default_portfolio_name() -> str:
    return (os.getenv("APP_DEFAULT_PORTFOLIO_NAME") or "Master Portfolio").strip() or "Master Portfolio"


def _ensure_schema(conn: Any) -> None:
    schema = _schema_name()
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.portfolios (
            id UUID PRIMARY KEY,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            brokerage_source TEXT NOT NULL,
            brokerage_account_ref TEXT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_portfolios_slug_lower ON {schema}.portfolios ((lower(slug)))",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.users (
            id UUID PRIMARY KEY,
            email TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            display_name TEXT NULL,
            status TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            last_login_at TIMESTAMPTZ NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_users_email_lower ON {schema}.users ((lower(email)))",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.user_credentials (
            user_id UUID PRIMARY KEY REFERENCES {schema}.users(id) ON DELETE CASCADE,
            password_hash TEXT NOT NULL,
            password_set_at TIMESTAMPTZ NOT NULL,
            must_rotate_password BOOLEAN NOT NULL DEFAULT FALSE,
            failed_login_count INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMPTZ NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.user_invites (
            id UUID PRIMARY KEY,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            portfolio_id UUID NULL REFERENCES {schema}.portfolios(id) ON DELETE SET NULL,
            proposed_share_fraction NUMERIC(9,6) NULL,
            invite_token_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_by_user_id UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            created_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_user_invites_token_hash ON {schema}.user_invites (invite_token_hash)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.password_reset_tokens (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            reset_token_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ NULL,
            requested_ip TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_password_reset_tokens_hash ON {schema}.password_reset_tokens (reset_token_hash)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.portfolio_memberships (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            share_fraction NUMERIC(9,6) NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_to TIMESTAMPTZ NULL,
            can_view_full_portfolio BOOLEAN NOT NULL DEFAULT FALSE,
            created_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_portfolio_memberships_user ON {schema}.portfolio_memberships (user_id, effective_from DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_portfolio_memberships_portfolio ON {schema}.portfolio_memberships (portfolio_id, effective_from DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.user_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            session_token_hash TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NULL,
            user_agent TEXT NULL,
            ip_address TEXT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_user_sessions_token_hash ON {schema}.user_sessions (session_token_hash)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.access_events (
            id UUID PRIMARY KEY,
            user_id UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            email TEXT NULL,
            event_type TEXT NOT NULL,
            event_category TEXT NOT NULL,
            section_name TEXT NULL,
            session_token_hash TEXT NULL,
            ip_address TEXT NULL,
            user_agent TEXT NULL,
            detail_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_access_events_created_at ON {schema}.access_events (created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_access_events_user_created ON {schema}.access_events (user_id, created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_access_events_type_created ON {schema}.access_events (event_type, created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_access_events_category_created ON {schema}.access_events (event_category, created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_access_events_section_created ON {schema}.access_events (section_name, created_at DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.agent_api_keys (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            key_prefix TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            scopes_json JSONB NOT NULL,
            status TEXT NOT NULL,
            expires_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            created_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            last_used_at TIMESTAMPTZ NULL,
            notes TEXT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_agent_api_keys_token_hash ON {schema}.agent_api_keys (token_hash)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_agent_api_keys_status ON {schema}.agent_api_keys (status, created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_agent_api_keys_created_by ON {schema}.agent_api_keys (created_by, created_at DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.app_settings (
            key TEXT PRIMARY KEY,
            value_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.capital_requests (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            request_type TEXT NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL,
            reviewed_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            reviewed_at TIMESTAMPTZ NULL,
            settled_at TIMESTAMPTZ NULL,
            external_transfer_ref TEXT NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.capital_events (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            capital_request_id UUID NULL REFERENCES {schema}.capital_requests(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            effective_at TIMESTAMPTZ NOT NULL,
            created_by UUID NULL REFERENCES {schema}.users(id) ON DELETE SET NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.content_posts (
            id UUID PRIMARY KEY,
            author_user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            portfolio_id UUID NULL REFERENCES {schema}.portfolios(id) ON DELETE SET NULL,
            title TEXT NULL,
            body TEXT NOT NULL,
            visibility TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.content_comments (
            id UUID PRIMARY KEY,
            post_id UUID NOT NULL REFERENCES {schema}.content_posts(id) ON DELETE CASCADE,
            author_user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            parent_comment_id UUID NULL REFERENCES {schema}.content_comments(id) ON DELETE SET NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
    ]
    with conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    ensure_default_portfolio(conn)


def ensure_auth_schema() -> bool:
    conn = _db_connect()
    if conn is None:
        return False
    try:
        with conn:
            _ensure_schema(conn)
        return True
    finally:
        conn.close()


def ensure_default_portfolio(conn: Any) -> dict[str, Any]:
    schema = _schema_name()
    slug = _default_portfolio_slug()
    now = _now_utc()
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT id, slug, name, brokerage_source, status FROM {schema}.portfolios WHERE lower(slug) = lower(%s) LIMIT 1",
            (slug,),
        )
        row = _fetchone_dict(cursor)
        if row is not None:
            return row
        portfolio_id = uuid.uuid4()
        cursor.execute(
            f"""
            INSERT INTO {schema}.portfolios (
                id, slug, name, brokerage_source, brokerage_account_ref, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                portfolio_id,
                slug,
                _default_portfolio_name(),
                "alpaca",
                None,
                "active",
                now,
                now,
            ),
        )
        return {
            "id": portfolio_id,
            "slug": slug,
            "name": _default_portfolio_name(),
            "brokerage_source": "alpaca",
            "status": "active",
        }


def _active_membership_for_user(cursor: Any, user_id: str | uuid.UUID) -> dict[str, Any] | None:
    schema = _schema_name()
    cursor.execute(
        f"""
        SELECT
            pm.id,
            pm.portfolio_id,
            pm.user_id,
            pm.role AS membership_role,
            pm.share_fraction,
            pm.can_view_full_portfolio,
            pm.effective_from,
            pm.effective_to,
            p.slug AS portfolio_slug,
            p.name AS portfolio_name
        FROM {schema}.portfolio_memberships pm
        JOIN {schema}.portfolios p ON p.id = pm.portfolio_id
        WHERE pm.user_id = %s
          AND p.status = %s
          AND pm.effective_from <= %s
          AND (pm.effective_to IS NULL OR pm.effective_to > %s)
        ORDER BY pm.effective_from DESC
        LIMIT 1
        """,
        (user_id, "active", _now_utc(), _now_utc()),
    )
    return _fetchone_dict(cursor)


def _build_user_context_row(user_row: dict[str, Any], membership_row: dict[str, Any] | None) -> dict[str, Any]:
    membership = membership_row or {}
    first_name = str(user_row.get("first_name") or "").strip()
    last_name = str(user_row.get("last_name") or "").strip()
    display_name = str(user_row.get("display_name") or "").strip() or " ".join(part for part in [first_name, last_name] if part).strip()
    share_fraction = membership.get("share_fraction")
    try:
        share_value = float(share_fraction) if share_fraction is not None else 0.0
    except Exception:
        share_value = 0.0
    return {
        "user_id": str(user_row.get("id") or ""),
        "email": str(user_row.get("email") or "").strip(),
        "first_name": first_name,
        "last_name": last_name,
        "display_name": display_name,
        "role": str(user_row.get("role") or "").strip() or "investor",
        "status": str(user_row.get("status") or "").strip() or "active",
        "portfolio_id": str(membership.get("portfolio_id") or ""),
        "portfolio_slug": str(membership.get("portfolio_slug") or ""),
        "portfolio_name": str(membership.get("portfolio_name") or ""),
        "membership_role": str(membership.get("membership_role") or ""),
        "share_fraction": share_value,
        "can_view_full_portfolio": bool(membership.get("can_view_full_portfolio")),
    }


def has_users() -> bool:
    conn = _db_connect()
    if conn is None:
        return False
    schema = _schema_name()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT 1 FROM {schema}.users LIMIT 1")
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def get_user_for_login(email: str) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    u.id,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.display_name,
                    u.status,
                    u.role,
                    u.last_login_at,
                    c.password_hash,
                    c.must_rotate_password,
                    c.failed_login_count,
                    c.locked_until
                FROM {schema}.users u
                JOIN {schema}.user_credentials c ON c.user_id = u.id
                WHERE lower(u.email) = lower(%s)
                LIMIT 1
                """,
                (str(email or "").strip(),),
            )
            row = _fetchone_dict(cursor)
            if row is None:
                return None
            membership = _active_membership_for_user(cursor, row["id"])
            result = _build_user_context_row(row, membership)
            result["password_hash"] = str(row.get("password_hash") or "")
            result["must_rotate_password"] = bool(row.get("must_rotate_password"))
            result["failed_login_count"] = int(row.get("failed_login_count") or 0)
            result["locked_until"] = row.get("locked_until")
            return result
    finally:
        conn.close()


def record_failed_login(email: str, *, max_attempts: int, lockout_until: datetime | None) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        return {}
    schema = _schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_credentials
                    SET
                        failed_login_count = failed_login_count + 1,
                        locked_until = CASE
                            WHEN failed_login_count + 1 >= %s THEN %s
                            ELSE locked_until
                        END
                    WHERE user_id = (
                        SELECT id FROM {schema}.users WHERE lower(email) = lower(%s) LIMIT 1
                    )
                    RETURNING failed_login_count, locked_until
                    """,
                    (max_attempts, lockout_until, str(email or "").strip()),
                )
                row = _fetchone_dict(cursor) or {}
                return {
                    "failed_login_count": int(row.get("failed_login_count") or 0),
                    "locked_until": row.get("locked_until"),
                }
    finally:
        conn.close()


def clear_failed_login(user_id: str) -> None:
    conn = _db_connect()
    if conn is None:
        return
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_credentials
                    SET failed_login_count = 0, locked_until = NULL
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                cursor.execute(
                    f"UPDATE {schema}.users SET last_login_at = %s, updated_at = %s WHERE id = %s",
                    (now, now, user_id),
                )
    finally:
        conn.close()


def create_session(
    *,
    user_id: str,
    session_token_hash: str,
    expires_at: datetime,
    user_agent: str = "",
    ip_address: str = "",
) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    session_id = uuid.uuid4()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.user_sessions (
                        id, user_id, session_token_hash, expires_at, revoked_at, created_at, last_seen_at, user_agent, ip_address
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (session_id, user_id, session_token_hash, expires_at, None, now, now, user_agent or None, ip_address or None),
                )
        return {"id": str(session_id), "user_id": user_id, "expires_at": expires_at}
    finally:
        conn.close()


def get_user_context_for_session(session_token_hash: str) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        s.id AS session_id,
                        s.user_id,
                        s.expires_at,
                        u.id,
                        u.email,
                        u.first_name,
                        u.last_name,
                        u.display_name,
                        u.status,
                        u.role
                    FROM {schema}.user_sessions s
                    JOIN {schema}.users u ON u.id = s.user_id
                    WHERE s.session_token_hash = %s
                      AND s.revoked_at IS NULL
                      AND s.expires_at > %s
                      AND u.status = %s
                    LIMIT 1
                    """,
                    (session_token_hash, now, "active"),
                )
                row = _fetchone_dict(cursor)
                if row is None:
                    return None
                cursor.execute(
                    f"UPDATE {schema}.user_sessions SET last_seen_at = %s WHERE id = %s",
                    (now, row["session_id"]),
                )
                membership = _active_membership_for_user(cursor, row["id"])
                result = _build_user_context_row(row, membership)
                result["session_id"] = str(row.get("session_id") or "")
                result["expires_at"] = row.get("expires_at")
                return result
    finally:
        conn.close()


def revoke_session(session_token_hash: str) -> None:
    conn = _db_connect()
    if conn is None:
        return
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_sessions
                    SET revoked_at = COALESCE(revoked_at, %s)
                    WHERE session_token_hash = %s
                    """,
                    (now, session_token_hash),
                )
    finally:
        conn.close()


def revoke_user_sessions(user_id: str) -> None:
    conn = _db_connect()
    if conn is None:
        return
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_sessions
                    SET revoked_at = COALESCE(revoked_at, %s)
                    WHERE user_id = %s
                    """,
                    (now, user_id),
                )
    finally:
        conn.close()


def record_access_event(
    *,
    event_type: str,
    event_category: str,
    user_id: str | None = None,
    email: str | None = None,
    section_name: str | None = None,
    session_token_hash: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_event_type = str(event_type or "").strip().lower()
    normalized_category = str(event_category or "").strip().lower()
    if not normalized_event_type or not normalized_category:
        return {}

    conn = _db_connect()
    if conn is None:
        return {}

    schema = _schema_name()
    event_id = uuid.uuid4()
    now = _now_utc()
    normalized_user_id = str(user_id or "").strip()
    normalized_email = str(email or "").strip()
    normalized_section = str(section_name or "").strip()
    normalized_session_hash = str(session_token_hash or "").strip()
    normalized_ip = str(ip_address or "").strip()
    normalized_user_agent = str(user_agent or "").strip()
    serialized_detail = json.dumps(detail if isinstance(detail, dict) else {}, default=str)

    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.access_events (
                        id, user_id, email, event_type, event_category, section_name,
                        session_token_hash, ip_address, user_agent, detail_json, created_at
                    ) VALUES (
                        %s,
                        NULLIF(%s, '')::uuid,
                        NULLIF(%s, ''),
                        %s,
                        %s,
                        NULLIF(%s, ''),
                        NULLIF(%s, ''),
                        NULLIF(%s, ''),
                        NULLIF(%s, ''),
                        %s::jsonb,
                        %s
                    )
                    RETURNING
                        id,
                        user_id,
                        email,
                        event_type,
                        event_category,
                        section_name,
                        session_token_hash,
                        ip_address,
                        user_agent,
                        detail_json,
                        created_at
                    """,
                    (
                        event_id,
                        normalized_user_id,
                        normalized_email,
                        normalized_event_type,
                        normalized_category,
                        normalized_section,
                        normalized_session_hash,
                        normalized_ip,
                        normalized_user_agent,
                        serialized_detail,
                        now,
                    ),
                )
                row = _fetchone_dict(cursor) or {}
        detail_value = row.get("detail_json")
        if isinstance(detail_value, str):
            try:
                detail_value = json.loads(detail_value)
            except Exception:
                detail_value = {}
        return {
            "id": str(row.get("id") or event_id),
            "user_id": str(row.get("user_id") or ""),
            "email": str(row.get("email") or ""),
            "event_type": str(row.get("event_type") or normalized_event_type),
            "event_category": str(row.get("event_category") or normalized_category),
            "section_name": str(row.get("section_name") or ""),
            "session_token_hash": str(row.get("session_token_hash") or ""),
            "ip_address": str(row.get("ip_address") or ""),
            "user_agent": str(row.get("user_agent") or ""),
            "detail": detail_value if isinstance(detail_value, dict) else {},
            "created_at": row.get("created_at") or now,
        }
    finally:
        conn.close()


def _active_share_sum(cursor: Any, portfolio_id: str | uuid.UUID) -> float:
    schema = _schema_name()
    cursor.execute(
        f"""
        SELECT COALESCE(SUM(share_fraction), 0)
        FROM {schema}.portfolio_memberships
        WHERE portfolio_id = %s
          AND can_view_full_portfolio = FALSE
          AND effective_from <= %s
          AND (effective_to IS NULL OR effective_to > %s)
        """,
        (portfolio_id, _now_utc(), _now_utc()),
    )
    row = cursor.fetchone()
    try:
        return float(row[0] or 0.0) if row is not None else 0.0
    except Exception:
        return 0.0


def bootstrap_admin(
    *,
    email: str,
    password_hash: str,
    first_name: str,
    last_name: str,
    display_name: str = "",
) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    normalized_email = str(email or "").strip()
    now = _now_utc()
    try:
        with conn:
            _ensure_schema(conn)
            portfolio = ensure_default_portfolio(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT id, email, first_name, last_name, display_name, status, role FROM {schema}.users WHERE lower(email) = lower(%s) LIMIT 1",
                    (normalized_email,),
                )
                user_row = _fetchone_dict(cursor)
                if user_row is None:
                    user_id = uuid.uuid4()
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.users (
                            id, email, first_name, last_name, display_name, status, role, created_at, updated_at, last_login_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            normalized_email,
                            first_name,
                            last_name,
                            display_name or " ".join(part for part in [first_name, last_name] if part).strip(),
                            "active",
                            "admin",
                            now,
                            now,
                            None,
                        ),
                    )
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.user_credentials (
                            user_id, password_hash, password_set_at, must_rotate_password, failed_login_count, locked_until
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, password_hash, now, False, 0, None),
                    )
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.portfolio_memberships (
                            id, portfolio_id, user_id, role, share_fraction, effective_from, effective_to,
                            can_view_full_portfolio, created_by, notes, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            uuid.uuid4(),
                            portfolio["id"],
                            user_id,
                            "admin",
                            0.0,
                            now,
                            None,
                            True,
                            None,
                            "Bootstrap admin",
                            now,
                            now,
                        ),
                    )
                    return {
                        "user_id": str(user_id),
                        "email": normalized_email,
                        "portfolio_id": str(portfolio["id"]),
                        "portfolio_slug": str(portfolio.get("slug") or ""),
                        "share_fraction": 0.0,
                        "can_view_full_portfolio": True,
                        "role": "admin",
                    }

                cursor.execute(
                    f"""
                    UPDATE {schema}.users
                    SET first_name = %s, last_name = %s, display_name = %s, status = %s, role = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        first_name,
                        last_name,
                        display_name or " ".join(part for part in [first_name, last_name] if part).strip(),
                        "active",
                        "admin",
                        now,
                        user_row["id"],
                    ),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.user_credentials (
                        user_id, password_hash, password_set_at, must_rotate_password, failed_login_count, locked_until
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        password_set_at = EXCLUDED.password_set_at,
                        must_rotate_password = FALSE,
                        failed_login_count = 0,
                        locked_until = NULL
                    """,
                    (user_row["id"], password_hash, now, False, 0, None),
                )
                cursor.execute(
                    f"""
                    SELECT id FROM {schema}.portfolio_memberships
                    WHERE user_id = %s
                      AND portfolio_id = %s
                      AND role = %s
                      AND can_view_full_portfolio = TRUE
                      AND effective_to IS NULL
                    LIMIT 1
                    """,
                    (user_row["id"], portfolio["id"], "admin"),
                )
                existing_membership = cursor.fetchone()
                if existing_membership is None:
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.portfolio_memberships (
                            id, portfolio_id, user_id, role, share_fraction, effective_from, effective_to,
                            can_view_full_portfolio, created_by, notes, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            uuid.uuid4(),
                            portfolio["id"],
                            user_row["id"],
                            "admin",
                            0.0,
                            now,
                            None,
                            True,
                            None,
                            "Bootstrap admin",
                            now,
                            now,
                        ),
                    )
                return {
                    "user_id": str(user_row["id"]),
                    "email": normalized_email,
                    "portfolio_id": str(portfolio["id"]),
                    "portfolio_slug": str(portfolio.get("slug") or ""),
                    "share_fraction": 0.0,
                    "can_view_full_portfolio": True,
                    "role": "admin",
                }
    finally:
        conn.close()


def insert_invite(
    *,
    email: str,
    role: str,
    proposed_share_fraction: float | None,
    invite_token_hash: str,
    expires_at: datetime,
    created_by: str | None,
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    normalized_email = str(email or "").strip()
    normalized_role = str(role or "investor").strip().lower() or "investor"
    try:
        with conn:
            _ensure_schema(conn)
            portfolio = ensure_default_portfolio(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT id FROM {schema}.users WHERE lower(email) = lower(%s) LIMIT 1",
                    (normalized_email,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("An account with that email already exists.")
                share_fraction = None if proposed_share_fraction is None else max(float(proposed_share_fraction), 0.0)
                if normalized_role == "investor":
                    share_fraction = float(share_fraction or 0.0)
                    if share_fraction <= 0.0:
                        raise ValueError("Investor invites require a positive portfolio share.")
                    current_sum = _active_share_sum(cursor, portfolio["id"])
                    if current_sum + share_fraction > 1.000001:
                        raise ValueError("Active investor shares would exceed 100%.")
                else:
                    share_fraction = 0.0

                cursor.execute(
                    f"""
                    UPDATE {schema}.user_invites
                    SET status = %s, updated_at = %s
                    WHERE lower(email) = lower(%s)
                      AND status = %s
                    """,
                    ("revoked", now, normalized_email, "pending"),
                )
                invite_id = uuid.uuid4()
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.user_invites (
                        id, email, role, portfolio_id, proposed_share_fraction, invite_token_hash, status, expires_at,
                        accepted_by_user_id, created_by, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        invite_id,
                        normalized_email,
                        normalized_role,
                        portfolio["id"],
                        share_fraction,
                        invite_token_hash,
                        "pending",
                        expires_at,
                        None,
                        created_by,
                        now,
                        now,
                    ),
                )
                return {
                    "id": str(invite_id),
                    "email": normalized_email,
                    "role": normalized_role,
                    "portfolio_id": str(portfolio["id"]),
                    "portfolio_slug": str(portfolio.get("slug") or ""),
                    "proposed_share_fraction": share_fraction,
                    "expires_at": expires_at,
                }
    finally:
        conn.close()


def get_pending_invite_by_token_hash(invite_token_hash: str) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT i.id, i.email, i.role, i.portfolio_id, i.proposed_share_fraction, i.status, i.expires_at,
                       p.slug AS portfolio_slug, p.name AS portfolio_name
                FROM {schema}.user_invites i
                LEFT JOIN {schema}.portfolios p ON p.id = i.portfolio_id
                WHERE i.invite_token_hash = %s
                  AND i.status = %s
                  AND i.expires_at > %s
                LIMIT 1
                """,
                (invite_token_hash, "pending", now),
            )
            return _fetchone_dict(cursor)
    finally:
        conn.close()


def accept_invite(
    *,
    invite_token_hash: str,
    first_name: str,
    last_name: str,
    display_name: str,
    password_hash: str,
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, email, role, portfolio_id, proposed_share_fraction, status, expires_at
                    FROM {schema}.user_invites
                    WHERE invite_token_hash = %s
                    LIMIT 1
                    """,
                    (invite_token_hash,),
                )
                invite = _fetchone_dict(cursor)
                if invite is None:
                    raise ValueError("Invite is invalid.")
                if str(invite.get("status") or "") != "pending":
                    raise ValueError("Invite is no longer active.")
                if invite.get("expires_at") is not None and invite["expires_at"] <= now:
                    cursor.execute(
                        f"UPDATE {schema}.user_invites SET status = %s, updated_at = %s WHERE id = %s",
                        ("expired", now, invite["id"]),
                    )
                    raise ValueError("Invite has expired.")
                cursor.execute(
                    f"SELECT id FROM {schema}.users WHERE lower(email) = lower(%s) LIMIT 1",
                    (invite["email"],),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("An account already exists for this invite email.")

                share_fraction = float(invite.get("proposed_share_fraction") or 0.0)
                if share_fraction > 0.0:
                    current_sum = _active_share_sum(cursor, invite["portfolio_id"])
                    if current_sum + share_fraction > 1.000001:
                        raise ValueError("Active investor shares would exceed 100%.")

                user_id = uuid.uuid4()
                role = str(invite.get("role") or "investor").strip().lower() or "investor"
                resolved_display = str(display_name or "").strip() or " ".join(part for part in [first_name, last_name] if part).strip()

                cursor.execute(
                    f"""
                    INSERT INTO {schema}.users (
                        id, email, first_name, last_name, display_name, status, role, created_at, updated_at, last_login_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, invite["email"], first_name, last_name, resolved_display, "active", role, now, now, None),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.user_credentials (
                        user_id, password_hash, password_set_at, must_rotate_password, failed_login_count, locked_until
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, password_hash, now, False, 0, None),
                )
                can_view_full_portfolio = role == "admin"
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.portfolio_memberships (
                        id, portfolio_id, user_id, role, share_fraction, effective_from, effective_to,
                        can_view_full_portfolio, created_by, notes, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        invite["portfolio_id"],
                        user_id,
                        role,
                        0.0 if can_view_full_portfolio else share_fraction,
                        now,
                        None,
                        can_view_full_portfolio,
                        invite.get("created_by"),
                        "Accepted invite",
                        now,
                        now,
                    ),
                )
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_invites
                    SET status = %s, accepted_by_user_id = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    ("accepted", user_id, now, invite["id"]),
                )
                cursor.execute(
                    f"SELECT id, email, first_name, last_name, display_name, status, role FROM {schema}.users WHERE id = %s",
                    (user_id,),
                )
                user_row = _fetchone_dict(cursor)
                membership = _active_membership_for_user(cursor, user_id)
                return _build_user_context_row(user_row or {}, membership)
    finally:
        conn.close()


def issue_password_reset(
    *,
    user_id: str,
    reset_token_hash: str,
    expires_at: datetime,
    requested_ip: str = "",
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    token_id = uuid.uuid4()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.password_reset_tokens
                    SET status = %s
                    WHERE user_id = %s
                      AND status = %s
                    """,
                    ("revoked", user_id, "pending"),
                )
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.password_reset_tokens (
                        id, user_id, reset_token_hash, status, expires_at, used_at, requested_ip, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (token_id, user_id, reset_token_hash, "pending", expires_at, None, requested_ip or None, now),
                )
        return {"id": str(token_id), "user_id": user_id, "expires_at": expires_at}
    finally:
        conn.close()


def get_active_user_by_email(email: str) -> dict[str, Any] | None:
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, email, first_name, last_name, display_name, status, role
                FROM {schema}.users
                WHERE lower(email) = lower(%s)
                LIMIT 1
                """,
                (str(email or "").strip(),),
            )
            row = _fetchone_dict(cursor)
            if row is None or str(row.get("status") or "") != "active":
                return None
            membership = _active_membership_for_user(cursor, row["id"])
            return _build_user_context_row(row, membership)
    finally:
        conn.close()


def reset_password(
    *,
    reset_token_hash: str,
    password_hash: str,
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, status, expires_at
                    FROM {schema}.password_reset_tokens
                    WHERE reset_token_hash = %s
                    LIMIT 1
                    """,
                    (reset_token_hash,),
                )
                token_row = _fetchone_dict(cursor)
                if token_row is None:
                    raise ValueError("Reset token is invalid.")
                if str(token_row.get("status") or "") != "pending":
                    raise ValueError("Reset token is no longer active.")
                if token_row.get("expires_at") is not None and token_row["expires_at"] <= now:
                    cursor.execute(
                        f"UPDATE {schema}.password_reset_tokens SET status = %s WHERE id = %s",
                        ("expired", token_row["id"]),
                    )
                    raise ValueError("Reset token has expired.")
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_credentials
                    SET password_hash = %s, password_set_at = %s, must_rotate_password = FALSE,
                        failed_login_count = 0, locked_until = NULL
                    WHERE user_id = %s
                    """,
                    (password_hash, now, token_row["user_id"]),
                )
                cursor.execute(
                    f"""
                    UPDATE {schema}.password_reset_tokens
                    SET status = %s, used_at = %s
                    WHERE id = %s
                    """,
                    ("used", now, token_row["id"]),
                )
                cursor.execute(
                    f"""
                    UPDATE {schema}.user_sessions
                    SET revoked_at = COALESCE(revoked_at, %s)
                    WHERE user_id = %s
                    """,
                    (now, token_row["user_id"]),
                )
                cursor.execute(
                    f"SELECT id, email, first_name, last_name, display_name, status, role FROM {schema}.users WHERE id = %s",
                    (token_row["user_id"],),
                )
                user_row = _fetchone_dict(cursor)
                membership = _active_membership_for_user(cursor, token_row["user_id"])
                return _build_user_context_row(user_row or {}, membership)
    finally:
        conn.close()


def list_users() -> list[dict[str, Any]]:
    conn = _db_connect()
    if conn is None:
        return []
    schema = _schema_name()
    now = _now_utc()
    active_since = now - timedelta(minutes=30)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    u.id,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.display_name,
                    u.status,
                    u.role,
                    u.last_login_at,
                    COALESCE(c.failed_login_count, 0) AS failed_login_count,
                    c.locked_until,
                    COALESCE(sess.open_session_count, 0) AS open_session_count,
                    COALESCE(sess.active_session_count, 0) AS active_session_count,
                    sess.last_seen_at
                FROM {schema}.users u
                LEFT JOIN {schema}.user_credentials c
                    ON c.user_id = u.id
                LEFT JOIN (
                    SELECT
                        user_id,
                        COUNT(*) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                        ) AS open_session_count,
                        COUNT(*) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                              AND last_seen_at >= %s
                        ) AS active_session_count,
                        MAX(last_seen_at) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                        ) AS last_seen_at
                    FROM {schema}.user_sessions
                    GROUP BY user_id
                ) sess
                    ON sess.user_id = u.id
                ORDER BY u.created_at ASC, u.email ASC
                """
                ,
                (now, now, active_since, now),
            )
            rows = _fetchall_dicts(cursor)
            result: list[dict[str, Any]] = []
            for row in rows:
                membership = _active_membership_for_user(cursor, row["id"])
                merged = _build_user_context_row(row, membership)
                merged["last_login_at"] = row.get("last_login_at")
                merged["failed_login_count"] = int(row.get("failed_login_count") or 0)
                merged["locked_until"] = row.get("locked_until")
                merged["open_session_count"] = int(row.get("open_session_count") or 0)
                merged["active_session_count"] = int(row.get("active_session_count") or 0)
                merged["last_seen_at"] = row.get("last_seen_at")
                result.append(merged)
            return result
    finally:
        conn.close()


def list_pending_invites() -> list[dict[str, Any]]:
    conn = _db_connect()
    if conn is None:
        return []
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT i.id, i.email, i.role, i.proposed_share_fraction, i.status, i.expires_at,
                       p.slug AS portfolio_slug
                FROM {schema}.user_invites i
                LEFT JOIN {schema}.portfolios p ON p.id = i.portfolio_id
                WHERE i.status = %s
                ORDER BY i.created_at DESC
                """,
                ("pending",),
            )
            rows = _fetchall_dicts(cursor)
            for row in rows:
                if row.get("expires_at") is not None and row["expires_at"] <= now:
                    row["status"] = "expired"
            return rows
    finally:
        conn.close()


def update_pending_invite(
    *,
    invite_id: str,
    role: str | None = None,
    proposed_share_fraction: float | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    del updated_by
    normalized_id = str(invite_id or "").strip()
    if not normalized_id:
        raise ValueError("Invite id is required.")
    try:
        invite_uuid = str(uuid.UUID(normalized_id))
    except Exception:
        raise ValueError("Invite id is invalid.")

    normalized_role = str(role or "").strip().lower()
    if normalized_role and normalized_role not in {"investor", "viewer", "admin"}:
        raise ValueError("Invite role is invalid.")

    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        i.id,
                        i.email,
                        i.role,
                        i.portfolio_id,
                        i.proposed_share_fraction,
                        i.status,
                        i.expires_at,
                        p.slug AS portfolio_slug
                    FROM {schema}.user_invites i
                    LEFT JOIN {schema}.portfolios p ON p.id = i.portfolio_id
                    WHERE i.id = %s::uuid
                    LIMIT 1
                    """,
                    (invite_uuid,),
                )
                invite = _fetchone_dict(cursor)
                if invite is None:
                    raise ValueError("Invite not found.")
                if str(invite.get("status") or "").strip().lower() != "pending":
                    raise ValueError("Invite is no longer pending.")

                resolved_role = normalized_role or (str(invite.get("role") or "investor").strip().lower() or "investor")
                if proposed_share_fraction is None:
                    share_fraction = invite.get("proposed_share_fraction")
                else:
                    share_fraction = max(float(proposed_share_fraction), 0.0)
                if resolved_role == "investor":
                    share_fraction = float(share_fraction or 0.0)
                    if share_fraction <= 0.0:
                        raise ValueError("Investor invites require a positive portfolio share.")
                    current_sum = _active_share_sum(cursor, invite.get("portfolio_id"))
                    if current_sum + share_fraction > 1.000001:
                        raise ValueError("Active investor shares would exceed 100%.")
                else:
                    share_fraction = 0.0

                cursor.execute(
                    f"""
                    UPDATE {schema}.user_invites
                    SET role = %s,
                        proposed_share_fraction = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                      AND status = %s
                    RETURNING id, email, role, portfolio_id, proposed_share_fraction, status, expires_at
                    """,
                    (resolved_role, share_fraction, now, invite_uuid, "pending"),
                )
                updated = _fetchone_dict(cursor)
                if updated is None:
                    raise ValueError("Invite is no longer pending.")

                return {
                    "id": str(updated.get("id") or invite_uuid),
                    "email": str(updated.get("email") or invite.get("email") or ""),
                    "role": str(updated.get("role") or resolved_role),
                    "portfolio_id": str(updated.get("portfolio_id") or invite.get("portfolio_id") or ""),
                    "portfolio_slug": str(invite.get("portfolio_slug") or ""),
                    "proposed_share_fraction": float(updated.get("proposed_share_fraction") or 0.0),
                    "status": str(updated.get("status") or "pending"),
                    "expires_at": updated.get("expires_at") or invite.get("expires_at"),
                }
    finally:
        conn.close()


def get_access_admin_dashboard(
    *,
    usage_window_days: int = 14,
    security_window_days: int = 14,
    active_window_minutes: int = 30,
    recent_event_limit: int = 80,
    sankey_user_limit: int = 10,
    user_id: str = "",
    user_email: str = "",
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        return {
            "summary": {},
            "filtered_user_id": "",
            "filtered_user_email": "",
            "user_usage": [],
            "section_usage": [],
            "active_sessions": [],
            "recent_security_events": [],
            "selected_user_targets": [],
            "selected_user_activity": [],
            "usage_sankey": [],
            "admin_usage": [],
            "access_ips": [],
        }

    schema = _schema_name()
    now = _now_utc()
    usage_days = max(int(usage_window_days or 0), 1)
    security_days = max(int(security_window_days or 0), 1)
    active_minutes = max(int(active_window_minutes or 0), 1)
    event_limit = max(int(recent_event_limit or 0), 1)
    flow_user_limit = max(min(int(sankey_user_limit or 0), 20), 1)
    usage_since = now - timedelta(days=usage_days)
    security_since = now - timedelta(days=security_days)
    active_since = now - timedelta(minutes=active_minutes)
    filtered_user_id, filtered_user_email = _normalize_access_dashboard_user(
        user_id=user_id,
        user_email=user_email,
    )
    usage_target_label_sql = """
        COALESCE(
            NULLIF(e.detail_json->>'target_label', ''),
            NULLIF(e.detail_json->>'headline', ''),
            NULLIF(e.detail_json->>'symbol', ''),
            CASE
                WHEN e.event_type = 'section_view' THEN NULL
                ELSE e.event_type
            END
        )
    """
    usage_target_type_sql = """
        COALESCE(
            NULLIF(e.detail_json->>'target_type', ''),
            CASE
                WHEN e.event_type = 'section_view' THEN ''
                WHEN e.event_type = 'content_link_open' THEN 'content'
                WHEN e.event_type = 'ticker_open' THEN 'ticker'
                WHEN e.event_type = 'bundle_open' THEN 'bundle'
                ELSE e.event_type
            END
        )
    """
    usage_surface_sql = """
        COALESCE(
            NULLIF(e.section_name, ''),
            NULLIF(e.detail_json->>'surface', ''),
            'Unknown'
        )
    """
    usage_user_label_sql = """
        COALESCE(
            NULLIF(u.display_name, ''),
            NULLIF(u.email, ''),
            NULLIF(e.email, ''),
            e.user_id::text
        )
    """

    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS total_users FROM {schema}.users")
                total_users_row = _fetchone_dict(cursor) or {}

                session_summary_sql = f"""
                    SELECT
                        COUNT(*) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                        ) AS open_session_count,
                        COUNT(*) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                              AND last_seen_at >= %s
                        ) AS active_session_count,
                        COUNT(DISTINCT user_id) FILTER (
                            WHERE revoked_at IS NULL
                              AND expires_at > %s
                              AND last_seen_at >= %s
                        ) AS active_user_count
                    FROM {schema}.user_sessions
                """
                session_summary_params: list[Any] = [now, now, active_since, now, active_since]
                if filtered_user_id:
                    session_summary_sql += " WHERE user_id = %s::uuid"
                    session_summary_params.append(filtered_user_id)
                cursor.execute(session_summary_sql, tuple(session_summary_params))
                session_summary = _fetchone_dict(cursor) or {}

                locked_summary_sql = f"""
                    SELECT COUNT(*) AS locked_user_count
                    FROM {schema}.user_credentials
                    WHERE locked_until IS NOT NULL
                      AND locked_until > %s
                """
                locked_summary_params: list[Any] = [now]
                if filtered_user_id:
                    locked_summary_sql += " AND user_id = %s::uuid"
                    locked_summary_params.append(filtered_user_id)
                cursor.execute(locked_summary_sql, tuple(locked_summary_params))
                locked_summary = _fetchone_dict(cursor) or {}

                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS pending_invite_count
                    FROM {schema}.user_invites
                    WHERE status = %s
                      AND expires_at > %s
                    """,
                    ("pending", now),
                )
                pending_invite_summary = _fetchone_dict(cursor) or {}

                usage_summary_sql = f"""
                    SELECT
                        COUNT(*) FILTER (WHERE event_type = 'section_view') AS section_view_count,
                        COUNT(*) FILTER (WHERE event_type = 'login_success') AS login_success_count,
                        COUNT(DISTINCT user_id) FILTER (
                            WHERE user_id IS NOT NULL
                        ) AS active_user_count
                    FROM {schema}.access_events
                    WHERE event_category = %s
                      AND created_at >= %s
                """
                usage_summary_params: list[Any] = ["usage", usage_since]
                if filtered_user_id:
                    usage_summary_sql += " AND user_id = %s::uuid"
                    usage_summary_params.append(filtered_user_id)
                cursor.execute(usage_summary_sql, tuple(usage_summary_params))
                usage_summary = _fetchone_dict(cursor) or {}

                security_summary_sql = f"""
                    SELECT
                        COUNT(*) FILTER (WHERE event_type = 'login_failed') AS failed_login_count,
                        COUNT(*) FILTER (WHERE event_type = 'login_locked') AS login_lock_count,
                        COUNT(*) FILTER (WHERE event_type = 'password_reset_requested') AS password_reset_request_count,
                        COUNT(*) FILTER (WHERE event_type = 'password_reset_issued_admin') AS admin_password_reset_count,
                        COUNT(*) FILTER (WHERE event_type = 'password_reset_completed') AS password_reset_complete_count,
                        COUNT(DISTINCT NULLIF(ip_address, '')) AS unique_ip_count
                    FROM {schema}.access_events
                    WHERE event_category = %s
                      AND created_at >= %s
                """
                security_summary_params: list[Any] = ["security", security_since]
                security_actor_filters: list[str] = []
                if filtered_user_id:
                    security_actor_filters.append("user_id = %s::uuid")
                    security_summary_params.append(filtered_user_id)
                if filtered_user_email:
                    security_actor_filters.append("(user_id IS NULL AND lower(email) = %s)")
                    security_summary_params.append(filtered_user_email)
                if security_actor_filters:
                    security_summary_sql += " AND (" + " OR ".join(security_actor_filters) + ")"
                cursor.execute(security_summary_sql, tuple(security_summary_params))
                security_summary = _fetchone_dict(cursor) or {}

                user_usage_sql = f"""
                    WITH usage_rollup AS (
                        SELECT
                            user_id,
                            COUNT(*) FILTER (WHERE event_type = 'section_view') AS section_view_count,
                            COUNT(DISTINCT section_name) FILTER (
                                WHERE event_type = 'section_view'
                                  AND COALESCE(section_name, '') <> ''
                            ) AS distinct_section_count,
                            MAX(created_at) AS last_activity_at
                        FROM {schema}.access_events
                        WHERE user_id IS NOT NULL
                          AND created_at >= %s
                          {"AND user_id = %s::uuid" if filtered_user_id else ""}
                        GROUP BY user_id
                    ),
                    top_sections AS (
                        SELECT
                            ranked.user_id,
                            ranked.section_name,
                            ranked.view_count
                        FROM (
                            SELECT
                                user_id,
                                section_name,
                                COUNT(*) AS view_count,
                                MAX(created_at) AS last_view_at,
                                ROW_NUMBER() OVER (
                                    PARTITION BY user_id
                                    ORDER BY COUNT(*) DESC, MAX(created_at) DESC, section_name ASC
                                ) AS rank_number
                            FROM {schema}.access_events
                            WHERE user_id IS NOT NULL
                              AND event_type = 'section_view'
                              AND created_at >= %s
                              AND COALESCE(section_name, '') <> ''
                              {"AND user_id = %s::uuid" if filtered_user_id else ""}
                            GROUP BY user_id, section_name
                        ) ranked
                        WHERE ranked.rank_number = 1
                    ),
                    session_rollup AS (
                        SELECT
                            user_id,
                            COUNT(*) FILTER (
                                WHERE revoked_at IS NULL
                                  AND expires_at > %s
                            ) AS open_session_count,
                            COUNT(*) FILTER (
                                WHERE revoked_at IS NULL
                                  AND expires_at > %s
                                  AND last_seen_at >= %s
                            ) AS active_session_count,
                            MAX(last_seen_at) FILTER (
                                WHERE revoked_at IS NULL
                                  AND expires_at > %s
                            ) AS last_seen_at
                        FROM {schema}.user_sessions
                        {"WHERE user_id = %s::uuid" if filtered_user_id else ""}
                        GROUP BY user_id
                    )
                    SELECT
                        u.id AS user_id,
                        u.email,
                        u.display_name,
                        u.role,
                        u.status,
                        u.last_login_at,
                        COALESCE(sr.open_session_count, 0) AS open_session_count,
                        COALESCE(sr.active_session_count, 0) AS active_session_count,
                        sr.last_seen_at,
                        COALESCE(ur.section_view_count, 0) AS section_view_count,
                        COALESCE(ur.distinct_section_count, 0) AS distinct_section_count,
                        ur.last_activity_at,
                        COALESCE(ts.section_name, '') AS top_section,
                        COALESCE(ts.view_count, 0) AS top_section_view_count
                    FROM {schema}.users u
                    LEFT JOIN usage_rollup ur ON ur.user_id = u.id
                    LEFT JOIN top_sections ts ON ts.user_id = u.id
                    LEFT JOIN session_rollup sr ON sr.user_id = u.id
                    {"WHERE u.id = %s::uuid" if filtered_user_id else ""}
                    ORDER BY COALESCE(ur.last_activity_at, sr.last_seen_at, u.last_login_at, u.created_at) DESC, u.email ASC
                """
                user_usage_params: list[Any] = [usage_since]
                if filtered_user_id:
                    user_usage_params.append(filtered_user_id)
                user_usage_params.append(usage_since)
                if filtered_user_id:
                    user_usage_params.append(filtered_user_id)
                user_usage_params.extend([now, now, active_since, now])
                if filtered_user_id:
                    user_usage_params.append(filtered_user_id)
                if filtered_user_id:
                    user_usage_params.append(filtered_user_id)
                cursor.execute(user_usage_sql, tuple(user_usage_params))
                user_usage_rows = _fetchall_dicts(cursor)

                section_usage_sql = f"""
                    SELECT
                        section_name,
                        COUNT(*) AS view_count,
                        COUNT(DISTINCT user_id) AS unique_user_count,
                        MAX(created_at) AS last_view_at
                    FROM {schema}.access_events
                    WHERE event_type = 'section_view'
                      AND created_at >= %s
                      AND COALESCE(section_name, '') <> ''
                      {"AND user_id = %s::uuid" if filtered_user_id else ""}
                    GROUP BY section_name
                    ORDER BY view_count DESC, unique_user_count DESC, section_name ASC
                """
                section_usage_params: list[Any] = [usage_since]
                if filtered_user_id:
                    section_usage_params.append(filtered_user_id)
                cursor.execute(section_usage_sql, tuple(section_usage_params))
                section_usage_rows = _fetchall_dicts(cursor)

                usage_sankey_sql = f"""
                    WITH top_users AS (
                        SELECT
                            e.user_id,
                            COUNT(*) AS usage_event_count,
                            MAX(e.created_at) AS last_event_at
                        FROM {schema}.access_events e
                        WHERE e.event_category = %s
                          AND e.created_at >= %s
                          AND e.user_id IS NOT NULL
                          AND e.event_type NOT IN ('login_success', 'logout', 'session_restored')
                          {"AND e.user_id = %s::uuid" if filtered_user_id else ""}
                        GROUP BY e.user_id
                        ORDER BY usage_event_count DESC, last_event_at DESC, e.user_id ASC
                        LIMIT %s
                    ),
                    normalized_events AS (
                        SELECT
                            e.user_id,
                            {usage_user_label_sql} AS user_label,
                            {usage_surface_sql} AS section_label,
                            {usage_target_label_sql} AS target_label,
                            {usage_target_type_sql} AS target_type,
                            e.created_at
                        FROM {schema}.access_events e
                        JOIN top_users tu ON tu.user_id = e.user_id
                        LEFT JOIN {schema}.users u ON u.id = e.user_id
                        WHERE e.event_category = %s
                          AND e.created_at >= %s
                          AND e.event_type NOT IN ('login_success', 'logout', 'session_restored')
                    ),
                    flow_rollup AS (
                        SELECT
                            user_id,
                            user_label,
                            section_label,
                            target_label,
                            target_type,
                            COUNT(*) AS event_count,
                            MAX(created_at) AS last_event_at
                        FROM normalized_events
                        GROUP BY user_id, user_label, section_label, target_label, target_type
                    ),
                    ranked_flow AS (
                        SELECT
                            user_id,
                            user_label,
                            section_label,
                            target_label,
                            target_type,
                            event_count,
                            last_event_at,
                            ROW_NUMBER() OVER (
                                PARTITION BY user_id, section_label
                                ORDER BY event_count DESC, last_event_at DESC, COALESCE(target_label, '') ASC
                            ) AS target_rank
                        FROM flow_rollup
                    )
                    SELECT
                        user_id,
                        user_label,
                        section_label,
                        COALESCE(target_label, '') AS target_label,
                        COALESCE(target_type, '') AS target_type,
                        event_count,
                        last_event_at
                    FROM ranked_flow
                    WHERE COALESCE(target_label, '') = ''
                       OR target_rank <= 5
                    ORDER BY user_label ASC, section_label ASC, event_count DESC, target_label ASC
                """
                usage_sankey_params: list[Any] = ["usage", usage_since]
                if filtered_user_id:
                    usage_sankey_params.append(filtered_user_id)
                usage_sankey_params.append(flow_user_limit)
                usage_sankey_params.extend(["usage", usage_since])
                cursor.execute(usage_sankey_sql, tuple(usage_sankey_params))
                usage_sankey_rows = _fetchall_dicts(cursor)

                active_sessions_sql = f"""
                    SELECT
                        s.id,
                        s.user_id,
                        u.email,
                        u.display_name,
                        s.created_at,
                        s.last_seen_at,
                        s.expires_at,
                        s.ip_address,
                        s.user_agent,
                        CASE
                            WHEN s.last_seen_at >= %s THEN TRUE
                            ELSE FALSE
                        END AS is_active_now
                    FROM {schema}.user_sessions s
                    JOIN {schema}.users u ON u.id = s.user_id
                    WHERE s.revoked_at IS NULL
                      AND s.expires_at > %s
                      {"AND s.user_id = %s::uuid" if filtered_user_id else ""}
                    ORDER BY s.last_seen_at DESC NULLS LAST, s.created_at DESC
                    LIMIT 100
                """
                active_sessions_params: list[Any] = [active_since, now]
                if filtered_user_id:
                    active_sessions_params.append(filtered_user_id)
                cursor.execute(active_sessions_sql, tuple(active_sessions_params))
                active_session_rows = _fetchall_dicts(cursor)

                recent_security_sql = f"""
                    SELECT
                        e.id,
                        e.created_at,
                        e.event_type,
                        e.email,
                        u.email AS user_email,
                        u.display_name,
                        e.section_name,
                        e.ip_address,
                        e.user_agent,
                        e.detail_json
                    FROM {schema}.access_events e
                    LEFT JOIN {schema}.users u ON u.id = e.user_id
                    WHERE e.event_category = %s
                      {
                          "AND (" + " OR ".join(
                              clause for clause in [
                                  "e.user_id = %s::uuid" if filtered_user_id else "",
                                  "(e.user_id IS NULL AND lower(e.email) = %s)" if filtered_user_email else "",
                              ]
                              if clause
                          ) + ")"
                          if filtered_user_id or filtered_user_email
                          else ""
                      }
                      AND e.created_at >= %s
                    ORDER BY e.created_at DESC
                    LIMIT %s
                """
                recent_security_params: list[Any] = ["security"]
                if filtered_user_id:
                    recent_security_params.append(filtered_user_id)
                if filtered_user_email:
                    recent_security_params.append(filtered_user_email)
                recent_security_params.extend([security_since, event_limit])
                cursor.execute(recent_security_sql, tuple(recent_security_params))
                recent_security_rows = _fetchall_dicts(cursor)

                selected_user_targets: list[dict[str, Any]] = []
                selected_user_activity: list[dict[str, Any]] = []
                if filtered_user_id or filtered_user_email:
                    selected_user_targets_sql = f"""
                        SELECT
                            COALESCE(
                                NULLIF(e.detail_json->>'target_label', ''),
                                NULLIF(e.detail_json->>'headline', ''),
                                NULLIF(e.detail_json->>'symbol', ''),
                                NULLIF(e.section_name, ''),
                                e.event_type
                            ) AS target_label,
                            COALESCE(
                                NULLIF(e.detail_json->>'target_type', ''),
                                CASE
                                    WHEN e.event_type = 'section_view' THEN 'section'
                                    ELSE e.event_type
                                END
                            ) AS target_type,
                            COUNT(*) AS event_count,
                            MAX(e.created_at) AS last_event_at
                        FROM {schema}.access_events e
                        WHERE e.event_category = %s
                          AND e.created_at >= %s
                          AND (
                              {"e.user_id = %s::uuid" if filtered_user_id else "FALSE"}
                              {" OR (e.user_id IS NULL AND lower(e.email) = %s)" if filtered_user_email else ""}
                          )
                          AND e.event_type NOT IN ('login_success', 'logout', 'session_restored')
                        GROUP BY target_label, target_type
                        ORDER BY event_count DESC, last_event_at DESC, target_label ASC
                        LIMIT 25
                    """
                    selected_user_targets_params: list[Any] = ["usage", usage_since]
                    if filtered_user_id:
                        selected_user_targets_params.append(filtered_user_id)
                    if filtered_user_email:
                        selected_user_targets_params.append(filtered_user_email)
                    cursor.execute(selected_user_targets_sql, tuple(selected_user_targets_params))
                    selected_user_targets = _fetchall_dicts(cursor)

                    selected_user_activity_sql = f"""
                        SELECT
                            e.id,
                            e.created_at,
                            e.event_category,
                            e.event_type,
                            e.section_name,
                            e.email,
                            u.email AS user_email,
                            u.display_name,
                            e.ip_address,
                            e.user_agent,
                            e.detail_json,
                            COALESCE(
                                NULLIF(e.detail_json->>'target_label', ''),
                                NULLIF(e.detail_json->>'headline', ''),
                                NULLIF(e.detail_json->>'symbol', ''),
                                NULLIF(e.section_name, ''),
                                e.event_type
                            ) AS target_label,
                            COALESCE(NULLIF(e.detail_json->>'target_type', ''), '') AS target_type
                        FROM {schema}.access_events e
                        LEFT JOIN {schema}.users u ON u.id = e.user_id
                        WHERE (
                              (e.event_category = %s AND e.created_at >= %s)
                           OR (e.event_category = %s AND e.created_at >= %s)
                        )
                          AND (
                              {"e.user_id = %s::uuid" if filtered_user_id else "FALSE"}
                              {" OR (e.user_id IS NULL AND lower(e.email) = %s)" if filtered_user_email else ""}
                          )
                        ORDER BY e.created_at DESC
                        LIMIT %s
                    """
                    selected_user_activity_params: list[Any] = ["usage", usage_since, "security", security_since]
                    if filtered_user_id:
                        selected_user_activity_params.append(filtered_user_id)
                    if filtered_user_email:
                        selected_user_activity_params.append(filtered_user_email)
                    selected_user_activity_params.append(event_limit)
                    cursor.execute(selected_user_activity_sql, tuple(selected_user_activity_params))
                    selected_user_activity = _fetchall_dicts(cursor)

                admin_usage_sql = f"""
                    SELECT
                        u.email,
                        COALESCE(NULLIF(u.display_name, ''), u.email) AS label,
                        COUNT(*) FILTER (WHERE e.event_type = 'section_view') AS section_view_count,
                        COUNT(*) FILTER (WHERE e.event_type != 'section_view') AS other_event_count,
                        COUNT(*) AS total_event_count,
                        MAX(e.created_at) AS last_activity_at
                    FROM {schema}.access_events e
                    JOIN {schema}.users u ON u.id = e.user_id
                    WHERE u.role = 'admin'
                      AND e.event_category = 'usage'
                      AND e.created_at >= %s
                    GROUP BY u.id, u.email, u.display_name
                    ORDER BY total_event_count DESC, u.email ASC
                """
                cursor.execute(admin_usage_sql, (usage_since,))
                admin_usage_rows = _fetchall_dicts(cursor)

                access_ips_sql = f"""
                    SELECT
                        e.ip_address,
                        COUNT(*) AS event_count,
                        COUNT(DISTINCT e.user_id) AS unique_user_count,
                        COUNT(*) FILTER (WHERE e.event_category = 'security') AS security_event_count,
                        MAX(e.created_at) AS last_seen_at,
                        STRING_AGG(DISTINCT COALESCE(NULLIF(u.display_name, ''), u.email, NULLIF(e.email, '')), ', ') AS users
                    FROM {schema}.access_events e
                    LEFT JOIN {schema}.users u ON u.id = e.user_id
                    WHERE e.created_at >= %s
                      AND COALESCE(e.ip_address, '') <> ''
                    GROUP BY e.ip_address
                    ORDER BY event_count DESC, last_seen_at DESC
                    LIMIT 50
                """
                cursor.execute(access_ips_sql, (security_since,))
                access_ip_rows = _fetchall_dicts(cursor)

        recent_security_rows = _hydrate_access_event_rows(recent_security_rows)
        selected_user_activity = _hydrate_access_event_rows(selected_user_activity)

        return {
            "generated_at": now,
            "usage_window_days": usage_days,
            "security_window_days": security_days,
            "active_window_minutes": active_minutes,
            "filtered_user_id": filtered_user_id,
            "filtered_user_email": filtered_user_email,
            "summary": {
                "total_users": int(total_users_row.get("total_users") or 0),
                "active_users_window": int(usage_summary.get("active_user_count") or 0),
                "section_views_window": int(usage_summary.get("section_view_count") or 0),
                "login_success_window": int(usage_summary.get("login_success_count") or 0),
                "open_sessions": int(session_summary.get("open_session_count") or 0),
                "active_sessions": int(session_summary.get("active_session_count") or 0),
                "active_users_now": int(session_summary.get("active_user_count") or 0),
                "failed_logins_window": int(security_summary.get("failed_login_count") or 0),
                "login_locks_window": int(security_summary.get("login_lock_count") or 0),
                "locked_users_now": int(locked_summary.get("locked_user_count") or 0),
                "password_reset_requests_window": int(security_summary.get("password_reset_request_count") or 0),
                "admin_password_resets_window": int(security_summary.get("admin_password_reset_count") or 0),
                "password_resets_completed_window": int(security_summary.get("password_reset_complete_count") or 0),
                "unique_ips_window": int(security_summary.get("unique_ip_count") or 0),
                "pending_invites": int(pending_invite_summary.get("pending_invite_count") or 0),
            },
            "user_usage": user_usage_rows,
            "section_usage": section_usage_rows,
            "active_sessions": active_session_rows,
            "recent_security_events": recent_security_rows,
            "selected_user_targets": selected_user_targets,
            "selected_user_activity": selected_user_activity,
            "usage_sankey": usage_sankey_rows,
            "admin_usage": admin_usage_rows,
            "access_ips": access_ip_rows,
        }
    finally:
        conn.close()


def delete_pending_invite(*, invite_id: str, deleted_by: str | None = None) -> dict[str, Any]:
    del deleted_by
    normalized_id = str(invite_id or "").strip()
    if not normalized_id:
        raise ValueError("Invite id is required.")
    try:
        invite_uuid = str(uuid.UUID(normalized_id))
    except Exception:
        raise ValueError("Invite id is invalid.")

    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, email, role, status, expires_at
                    FROM {schema}.user_invites
                    WHERE id = %s::uuid
                    LIMIT 1
                    """,
                    (invite_uuid,),
                )
                invite = _fetchone_dict(cursor)
                if invite is None:
                    raise ValueError("Invite not found.")
                if str(invite.get("status") or "").strip().lower() != "pending":
                    raise ValueError("Invite is no longer pending.")

                cursor.execute(
                    f"""
                    UPDATE {schema}.user_invites
                    SET status = %s, updated_at = %s
                    WHERE id = %s::uuid
                      AND status = %s
                    """,
                    ("revoked", now, invite_uuid, "pending"),
                )
                if int(cursor.rowcount or 0) <= 0:
                    raise ValueError("Invite is no longer pending.")
                return {
                    "id": str(invite.get("id") or invite_uuid),
                    "email": str(invite.get("email") or ""),
                    "role": str(invite.get("role") or ""),
                    "status": "revoked",
                }
    finally:
        conn.close()


def get_app_setting(setting_key: str) -> dict[str, Any] | None:
    normalized_key = str(setting_key or "").strip()
    if not normalized_key:
        return None
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT key, value_json, updated_at, updated_by
                    FROM {schema}.app_settings
                    WHERE key = %s
                    LIMIT 1
                    """,
                    (normalized_key,),
                )
                row = _fetchone_dict(cursor)
        if not row:
            return None
        value = row.get("value_json")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = None
        return {
            "key": str(row.get("key") or ""),
            "value": value,
            "updated_at": row.get("updated_at"),
            "updated_by": str(row.get("updated_by") or ""),
        }
    finally:
        conn.close()


def _normalize_scopes_json(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = []
    else:
        parsed = value
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text)
    return out


def _agent_key_row_payload(row: dict[str, Any], *, include_token_hash: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "key_prefix": str(row.get("key_prefix") or ""),
        "scopes": _normalize_scopes_json(row.get("scopes_json")),
        "status": str(row.get("status") or ""),
        "expires_at": row.get("expires_at"),
        "revoked_at": row.get("revoked_at"),
        "created_by": str(row.get("created_by") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_used_at": row.get("last_used_at"),
        "notes": str(row.get("notes") or ""),
    }
    if include_token_hash:
        payload["token_hash"] = str(row.get("token_hash") or "")
    return payload


def create_agent_api_key(
    *,
    name: str,
    key_prefix: str,
    token_hash: str,
    scopes: list[str],
    created_by: str | None = None,
    expires_at: datetime | None = None,
    notes: str = "",
) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Agent key name is required.")
    normalized_prefix = str(key_prefix or "").strip()
    if not normalized_prefix:
        raise ValueError("Agent key prefix is required.")
    normalized_hash = str(token_hash or "").strip()
    if not normalized_hash:
        raise ValueError("Agent key token hash is required.")

    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    key_id = uuid.uuid4()
    scopes_json = json.dumps([str(item or "").strip() for item in list(scopes or []) if str(item or "").strip()])
    created_by_value = str(created_by or "").strip()
    notes_value = str(notes or "").strip()
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.agent_api_keys (
                        id, name, key_prefix, token_hash, scopes_json, status, expires_at, revoked_at,
                        created_by, created_at, updated_at, last_used_at, notes
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, NULLIF(%s, '')::uuid, %s, %s, %s, %s)
                    RETURNING id, name, key_prefix, token_hash, scopes_json, status, expires_at, revoked_at,
                              created_by, created_at, updated_at, last_used_at, notes
                    """,
                    (
                        key_id,
                        normalized_name,
                        normalized_prefix,
                        normalized_hash,
                        scopes_json,
                        "active",
                        expires_at,
                        None,
                        created_by_value,
                        now,
                        now,
                        None,
                        notes_value or None,
                    ),
                )
                row = _fetchone_dict(cursor) or {}
        return _agent_key_row_payload(row)
    finally:
        conn.close()


def get_agent_api_key_by_hash(token_hash: str, *, touch_last_used: bool = False) -> dict[str, Any] | None:
    normalized_hash = str(token_hash or "").strip()
    if not normalized_hash:
        return None
    conn = _db_connect()
    if conn is None:
        return None
    schema = _schema_name()
    now = _now_utc()
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        id, name, key_prefix, token_hash, scopes_json, status, expires_at, revoked_at,
                        created_by, created_at, updated_at, last_used_at, notes
                    FROM {schema}.agent_api_keys
                    WHERE token_hash = %s
                      AND status = %s
                      AND revoked_at IS NULL
                      AND (expires_at IS NULL OR expires_at > %s)
                    LIMIT 1
                    """,
                    (normalized_hash, "active", now),
                )
                row = _fetchone_dict(cursor)
                if row is None:
                    return None
                if touch_last_used:
                    cursor.execute(
                        f"""
                        UPDATE {schema}.agent_api_keys
                        SET last_used_at = %s, updated_at = %s
                        WHERE id = %s
                        """,
                        (now, now, row["id"]),
                    )
                    row["last_used_at"] = now
                    row["updated_at"] = now
                return _agent_key_row_payload(row, include_token_hash=True)
    finally:
        conn.close()


def list_agent_api_keys() -> list[dict[str, Any]]:
    conn = _db_connect()
    if conn is None:
        return []
    schema = _schema_name()
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        id, name, key_prefix, scopes_json, status, expires_at, revoked_at,
                        created_by, created_at, updated_at, last_used_at, notes
                    FROM {schema}.agent_api_keys
                    ORDER BY created_at DESC, name ASC
                    """
                )
                rows = _fetchall_dicts(cursor)
                return [_agent_key_row_payload(row) for row in rows]
    finally:
        conn.close()


def revoke_agent_api_key(*, key_id: str, revoked_by: str | None = None) -> dict[str, Any]:
    normalized_key_id = str(key_id or "").strip()
    if not normalized_key_id:
        raise ValueError("Agent key id is required.")
    try:
        key_uuid = str(uuid.UUID(normalized_key_id))
    except Exception:
        raise ValueError("Agent key id is invalid.")

    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    del revoked_by
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        id, name, key_prefix, scopes_json, status, expires_at, revoked_at,
                        created_by, created_at, updated_at, last_used_at, notes
                    FROM {schema}.agent_api_keys
                    WHERE id = %s::uuid
                    LIMIT 1
                    """,
                    (key_uuid,),
                )
                row = _fetchone_dict(cursor)
                if row is None:
                    raise ValueError("Agent key not found.")

                cursor.execute(
                    f"""
                    UPDATE {schema}.agent_api_keys
                    SET status = %s, revoked_at = COALESCE(revoked_at, %s), updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    ("revoked", now, now, key_uuid),
                )
                row["status"] = "revoked"
                row["revoked_at"] = row.get("revoked_at") or now
                row["updated_at"] = now
        return _agent_key_row_payload(row)
    finally:
        conn.close()


def set_app_setting(setting_key: str, value: Any, *, updated_by: str | None = None) -> dict[str, Any]:
    normalized_key = str(setting_key or "").strip()
    if not normalized_key:
        raise ValueError("Setting key is required.")
    conn = _db_connect()
    if conn is None:
        raise RuntimeError("Authentication store is unavailable.")
    schema = _schema_name()
    now = _now_utc()
    serialized_value = json.dumps(value if value is not None else {})
    updated_by_value = str(updated_by or "").strip()
    try:
        with conn:
            _ensure_schema(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.app_settings (key, value_json, updated_at, updated_by)
                    VALUES (%s, %s::jsonb, %s, NULLIF(%s, '')::uuid)
                    ON CONFLICT (key) DO UPDATE SET
                        value_json = EXCLUDED.value_json,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by
                    RETURNING key, value_json, updated_at, updated_by
                    """,
                    (normalized_key, serialized_value, now, updated_by_value),
                )
                row = _fetchone_dict(cursor) or {}
        value_out = row.get("value_json")
        if isinstance(value_out, str):
            try:
                value_out = json.loads(value_out)
            except Exception:
                value_out = None
        return {
            "key": str(row.get("key") or normalized_key),
            "value": value_out,
            "updated_at": row.get("updated_at") or now,
            "updated_by": str(row.get("updated_by") or ""),
        }
    finally:
        conn.close()


__all__ = [
    "accept_invite",
    "auth_store_configured",
    "bootstrap_admin",
    "clear_failed_login",
    "create_agent_api_key",
    "delete_pending_invite",
    "create_session",
    "ensure_auth_schema",
    "get_agent_api_key_by_hash",
    "get_active_user_by_email",
    "get_access_admin_dashboard",
    "get_app_setting",
    "get_pending_invite_by_token_hash",
    "get_user_context_for_session",
    "get_user_for_login",
    "has_users",
    "insert_invite",
    "issue_password_reset",
    "list_agent_api_keys",
    "list_pending_invites",
    "list_users",
    "record_access_event",
    "record_failed_login",
    "revoke_agent_api_key",
    "set_app_setting",
    "reset_password",
    "revoke_session",
    "revoke_user_sessions",
    "update_pending_invite",
]
