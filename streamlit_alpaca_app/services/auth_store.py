from __future__ import annotations

from datetime import datetime, timezone
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
          AND pm.effective_from <= %s
          AND (pm.effective_to IS NULL OR pm.effective_to > %s)
        ORDER BY pm.effective_from DESC
        LIMIT 1
        """,
        (user_id, _now_utc(), _now_utc()),
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


def record_failed_login(email: str, *, max_attempts: int, lockout_until: datetime | None) -> None:
    conn = _db_connect()
    if conn is None:
        return
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
                    """,
                    (max_attempts, lockout_until, str(email or "").strip()),
                )
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
                    LIMIT 1
                    """,
                    (session_token_hash, now),
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
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, email, first_name, last_name, display_name, status, role, last_login_at
                FROM {schema}.users
                ORDER BY created_at ASC, email ASC
                """
            )
            rows = _fetchall_dicts(cursor)
            result: list[dict[str, Any]] = []
            for row in rows:
                membership = _active_membership_for_user(cursor, row["id"])
                merged = _build_user_context_row(row, membership)
                merged["last_login_at"] = row.get("last_login_at")
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


__all__ = [
    "accept_invite",
    "auth_store_configured",
    "bootstrap_admin",
    "clear_failed_login",
    "create_session",
    "ensure_auth_schema",
    "get_active_user_by_email",
    "get_pending_invite_by_token_hash",
    "get_user_context_for_session",
    "get_user_for_login",
    "has_users",
    "insert_invite",
    "issue_password_reset",
    "list_pending_invites",
    "list_users",
    "record_failed_login",
    "reset_password",
    "revoke_session",
    "revoke_user_sessions",
]
