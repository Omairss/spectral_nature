from __future__ import annotations


from datetime import datetime, timezone
import json
import uuid
from typing import Any

from psycopg.errors import UndefinedTable

from . import auth_store


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    columns = [item.name if hasattr(item, "name") else item[0] for item in cursor.description or []]
    return {str(key): value for key, value in zip(columns, row)}


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return fallback
        if isinstance(parsed, type(fallback)):
            return parsed
    return fallback


def _payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for key, fallback in (
        ("client_info_json", {}),
        ("oauth_metadata_json", {}),
        ("tool_catalog_json", []),
        ("server_info_json", {}),
        ("request_summary_json", {}),
        ("result_summary_json", {}),
        ("account_summary_json", {}),
        ("positions_json", []),
        ("timeseries_json", []),
        ("details_json", {}),
    ):
        if key in out:
            out[key.removesuffix("_json")] = _json_value(out.pop(key), fallback)
    for key in (
        "id",
        "user_id",
        "portfolio_id",
        "connection_id",
        "client_registration_id",
        "updated_by",
        "ref_id",
    ):
        if key in out and out.get(key) is not None:
            out[key] = str(out[key])
    return out


def _ensure_schema(conn: Any) -> None:
    auth_store.ensure_auth_schema_on_connection(conn)
    schema = auth_store.auth_store_schema_name()
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_mcp_clients (
            id UUID PRIMARY KEY,
            provider TEXT NOT NULL,
            resource_url TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            client_id TEXT NOT NULL,
            client_info_json JSONB NOT NULL,
            oauth_metadata_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (provider, resource_url, redirect_uri)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_connections (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            resource_url TEXT NOT NULL,
            environment TEXT NOT NULL,
            client_registration_id UUID NULL REFERENCES {schema}.brokerage_mcp_clients(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            access_token_ciphertext TEXT NULL,
            refresh_token_ciphertext TEXT NULL,
            device_token_ciphertext TEXT NULL,
            token_type TEXT NULL,
            scope TEXT NULL,
            token_expires_at TIMESTAMPTZ NULL,
            tool_catalog_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            server_info_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            connected_at TIMESTAMPTZ NULL,
            last_verified_at TIMESTAMPTZ NULL,
            disconnected_at TIMESTAMPTZ NULL,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (user_id, provider, resource_url, environment)
        )
        """,
        f"ALTER TABLE {schema}.brokerage_connections ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'production'",
        f"ALTER TABLE {schema}.brokerage_connections ADD COLUMN IF NOT EXISTS device_token_ciphertext TEXT NULL",
        f"ALTER TABLE {schema}.brokerage_connections DROP CONSTRAINT IF EXISTS brokerage_connections_user_id_provider_resource_url_key",
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_brokerage_connections_identity
        ON {schema}.brokerage_connections (user_id, provider, resource_url, environment)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_brokerage_connections_user
        ON {schema}.brokerage_connections (user_id, provider, environment, updated_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_oauth_flows (
            id UUID PRIMARY KEY,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            client_registration_id UUID NOT NULL REFERENCES {schema}.brokerage_mcp_clients(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            resource_url TEXT NOT NULL,
            environment TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            issuer TEXT NOT NULL,
            authorization_endpoint TEXT NOT NULL,
            token_endpoint TEXT NOT NULL,
            state_hash TEXT NOT NULL UNIQUE,
            code_verifier_ciphertext TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ NULL,
            error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"ALTER TABLE {schema}.brokerage_oauth_flows ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'production'",
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_brokerage_oauth_flows_user_created
        ON {schema}.brokerage_oauth_flows (user_id, created_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_mcp_audit_events (
            id UUID PRIMARY KEY,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            intent TEXT NOT NULL,
            candidate_id TEXT NULL,
            request_summary_json JSONB NOT NULL,
            result_summary_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status TEXT NOT NULL,
            error TEXT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ NULL
        )
        """,
        f"ALTER TABLE {schema}.brokerage_mcp_audit_events ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'production'",
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_brokerage_mcp_audit_user_started
        ON {schema}.brokerage_mcp_audit_events (user_id, started_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_order_tickets (
            id UUID PRIMARY KEY,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            review_tool_name TEXT NOT NULL,
            execution_tool_name TEXT NOT NULL,
            arguments_hash TEXT NOT NULL,
            ref_id UUID NOT NULL,
            request_summary_json JSONB NOT NULL,
            result_summary_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status TEXT NOT NULL,
            error TEXT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            confirmed_at TIMESTAMPTZ NULL,
            completed_at TIMESTAMPTZ NULL,
            UNIQUE (environment, ref_id)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_brokerage_order_tickets_user_created
        ON {schema}.brokerage_order_tickets (user_id, environment, created_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.brokerage_portfolio_snapshots (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            account_summary_json JSONB NOT NULL,
            positions_json JSONB NOT NULL,
            timeseries_json JSONB NOT NULL,
            details_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_updated_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (portfolio_id, connection_id, environment)
        )
        """,
        f"ALTER TABLE {schema}.brokerage_portfolio_snapshots ADD COLUMN IF NOT EXISTS details_json JSONB NOT NULL DEFAULT '{{}}'::jsonb",
        f"ALTER TABLE {schema}.brokerage_portfolio_snapshots DROP CONSTRAINT IF EXISTS brokerage_portfolio_snapshots_portfolio_id_environment_key",
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_brokerage_snapshots_identity ON {schema}.brokerage_portfolio_snapshots (portfolio_id, connection_id, environment)",
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_brokerage_portfolio_snapshots_connection
        ON {schema}.brokerage_portfolio_snapshots (connection_id, updated_at DESC)
        """,
        f"""CREATE TABLE IF NOT EXISTS {schema}.paper_trading_calls (
            id TEXT PRIMARY KEY, user_id UUID NOT NULL REFERENCES {schema}.users(id),
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id),
            environment TEXT NOT NULL, candidate_id TEXT NOT NULL,
            parent_call_id TEXT NULL REFERENCES {schema}.paper_trading_calls(id),
            intent TEXT NOT NULL, candidate_json JSONB NOT NULL,
            proposed_at TIMESTAMPTZ NOT NULL, valid_until TIMESTAMPTZ NULL,
            decision TEXT NOT NULL DEFAULT 'pending', decided_at TIMESTAMPTZ NULL,
            performance_json JSONB NOT NULL DEFAULT '{{}}',
            UNIQUE(user_id,portfolio_id,environment,candidate_id))""",
        f"""CREATE INDEX IF NOT EXISTS idx_{schema}_paper_calls_owner
            ON {schema}.paper_trading_calls(user_id,portfolio_id,environment,proposed_at DESC,id)""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.paper_trading_policy (
            user_id UUID NOT NULL REFERENCES {schema}.users(id),
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id),
            environment TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'approval',
            entries_paused BOOLEAN NOT NULL DEFAULT FALSE, risk_json JSONB NOT NULL DEFAULT '{{}}',
            revision BIGINT NOT NULL DEFAULT 1, account_binding TEXT NULL, updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(user_id,portfolio_id,environment))""",
        f"ALTER TABLE {schema}.paper_trading_policy ADD COLUMN IF NOT EXISTS account_binding TEXT NULL",
        f"""CREATE TABLE IF NOT EXISTS {schema}.paper_trading_orders (
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id),
            user_id UUID NOT NULL REFERENCES {schema}.users(id),
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id), environment TEXT NOT NULL,
            client_order_id TEXT NOT NULL, broker_order_id TEXT NULL,
            call_id TEXT NOT NULL REFERENCES {schema}.paper_trading_calls(id),
            action_id TEXT NOT NULL REFERENCES {schema}.paper_trading_calls(id),
            ticket_id UUID NOT NULL REFERENCES {schema}.brokerage_order_tickets(id),
            order_json JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(connection_id,client_order_id))""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.paper_trading_events (
            id TEXT PRIMARY KEY, user_id UUID NOT NULL REFERENCES {schema}.users(id),
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id),
            environment TEXT NOT NULL, connection_id UUID NULL REFERENCES {schema}.brokerage_connections(id),
            call_id TEXT NULL REFERENCES {schema}.paper_trading_calls(id),
            kind TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, payload_json JSONB NOT NULL)""",
        f"""CREATE INDEX IF NOT EXISTS idx_{schema}_paper_events_owner
            ON {schema}.paper_trading_events(user_id,portfolio_id,environment,occurred_at DESC,id DESC)""",
    ]
    with conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def ensure_brokerage_mcp_schema() -> bool:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    with conn:
        _ensure_schema(conn)
    return True


def get_client_registration(*, provider: str, resource_url: str, redirect_uri: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    SELECT id, provider, resource_url, redirect_uri, client_id,
                           client_info_json, oauth_metadata_json, created_at, updated_at
                    FROM {schema}.brokerage_mcp_clients
                    WHERE provider = %s AND resource_url = %s AND redirect_uri = %s
                    LIMIT 1
                    """,
                (provider, resource_url, redirect_uri),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row)


def upsert_client_registration(
    *,
    provider: str,
    resource_url: str,
    redirect_uri: str,
    client_id: str,
    client_info: dict[str, Any],
    oauth_metadata: dict[str, Any],
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_mcp_clients (
                        id, provider, resource_url, redirect_uri, client_id,
                        client_info_json, oauth_metadata_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    ON CONFLICT (provider, resource_url, redirect_uri) DO UPDATE SET
                        client_id = EXCLUDED.client_id,
                        client_info_json = EXCLUDED.client_info_json,
                        oauth_metadata_json = EXCLUDED.oauth_metadata_json,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id, provider, resource_url, redirect_uri, client_id,
                              client_info_json, oauth_metadata_json, created_at, updated_at
                    """,
                (
                    uuid.uuid4(), provider, resource_url, redirect_uri, client_id,
                    json.dumps(client_info), json.dumps(oauth_metadata), now, now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row) or {}


def create_oauth_flow(
    *,
    user_id: str,
    provider: str,
    resource_url: str,
    environment: str,
    client_registration_id: str,
    redirect_uri: str,
    issuer: str,
    authorization_endpoint: str,
    token_endpoint: str,
    state_hash: str,
    code_verifier_ciphertext: str,
    expires_at: datetime,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    connection_id = uuid.uuid4()
    flow_id = uuid.uuid4()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_connections (
                        id, user_id, provider, resource_url, environment, client_registration_id,
                        status, created_at, updated_at
                    ) VALUES (%s, %s::uuid, %s, %s, %s, %s::uuid, %s, %s, %s)
                    ON CONFLICT (user_id, provider, resource_url, environment) DO UPDATE SET
                        client_registration_id = EXCLUDED.client_registration_id,
                        status = EXCLUDED.status,
                        last_error = NULL,
                        disconnected_at = NULL,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                (
                    connection_id, user_id, provider, resource_url, environment,
                    client_registration_id, "authorizing", now, now,
                ),
            )
            connection_id = cursor.fetchone()[0]
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_oauth_flows
                    SET status = %s, updated_at = %s
                    WHERE user_id = %s::uuid AND provider = %s AND environment = %s AND status = %s
                    """,
                ("superseded", now, user_id, provider, environment, "pending"),
            )
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_oauth_flows (
                        id, connection_id, user_id, client_registration_id,
                        provider, resource_url, environment, redirect_uri, issuer,
                        authorization_endpoint, token_endpoint, state_hash,
                        code_verifier_ciphertext, status, expires_at,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s::uuid,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    RETURNING id, connection_id, user_id, client_registration_id,
                              provider, resource_url, redirect_uri, issuer,
                              authorization_endpoint, token_endpoint, status,
                              expires_at, created_at, updated_at
                    """,
                (
                    flow_id, connection_id, user_id, client_registration_id,
                    provider, resource_url, environment, redirect_uri, issuer,
                    authorization_endpoint, token_endpoint, state_hash,
                    code_verifier_ciphertext, "pending", expires_at, now, now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row) or {}


def consume_oauth_flow(*, state_hash: str, user_id: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_oauth_flows
                    SET status = %s, updated_at = %s
                    WHERE state_hash = %s
                      AND user_id = %s::uuid
                      AND environment = %s
                      AND status = %s
                      AND expires_at > %s
                    RETURNING id, connection_id, user_id, client_registration_id,
                              provider, resource_url, redirect_uri, issuer,
                              authorization_endpoint, token_endpoint, state_hash,
                              code_verifier_ciphertext, status, expires_at,
                              created_at, updated_at
                    """,
                ("consuming", now, state_hash, user_id, environment, "pending", now),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row)


def finish_oauth_flow(*, flow_id: str, status: str, error: str = "") -> None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_oauth_flows
                    SET status = %s, error = NULLIF(%s, ''),
                        completed_at = CASE WHEN %s = 'completed' THEN %s ELSE completed_at END,
                        updated_at = %s
                    WHERE id = %s::uuid
                    """,
                (status, error[:1000], status, now, now, flow_id),
            )


def save_connection_tokens(
    *,
    connection_id: str,
    access_token_ciphertext: str,
    refresh_token_ciphertext: str,
    token_type: str,
    scope: str,
    token_expires_at: datetime | None,
) -> None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_connections
                    SET access_token_ciphertext = %s,
                        refresh_token_ciphertext = NULLIF(%s, ''),
                        token_type = %s,
                        scope = %s,
                        token_expires_at = %s,
                        status = %s,
                        connected_at = COALESCE(connected_at, %s),
                        disconnected_at = NULL,
                        last_error = NULL,
                        updated_at = %s
                    WHERE id = %s::uuid
                    """,
                (
                    access_token_ciphertext, refresh_token_ciphertext, token_type,
                    scope, token_expires_at, "connected", now, now, connection_id,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError("Brokerage connection was not found.")


def save_direct_connection(
    *,
    user_id: str,
    provider: str,
    resource_url: str,
    environment: str,
    access_token_ciphertext: str,
    refresh_token_ciphertext: str,
    device_token_ciphertext: str,
    token_type: str,
    scope: str,
    token_expires_at: datetime | None,
    server_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_connections (
                        id, user_id, provider, resource_url, environment,
                        status, access_token_ciphertext, refresh_token_ciphertext,
                        device_token_ciphertext, token_type, scope, token_expires_at,
                        server_info_json, connected_at, last_verified_at,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s::uuid, %s, %s, %s,
                        %s, %s, NULLIF(%s, ''),
                        NULLIF(%s, ''), %s, %s, %s,
                        %s::jsonb, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (user_id, provider, resource_url, environment) DO UPDATE SET
                        status = EXCLUDED.status,
                        access_token_ciphertext = EXCLUDED.access_token_ciphertext,
                        refresh_token_ciphertext = EXCLUDED.refresh_token_ciphertext,
                        device_token_ciphertext = EXCLUDED.device_token_ciphertext,
                        token_type = EXCLUDED.token_type,
                        scope = EXCLUDED.scope,
                        token_expires_at = EXCLUDED.token_expires_at,
                        server_info_json = EXCLUDED.server_info_json,
                        connected_at = COALESCE(brokerage_connections.connected_at, EXCLUDED.connected_at),
                        last_verified_at = EXCLUDED.last_verified_at,
                        disconnected_at = NULL,
                        last_error = NULL,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id, user_id, provider, resource_url, environment,
                              status, token_type, scope, token_expires_at,
                              server_info_json, connected_at, last_verified_at,
                              created_at, updated_at
                    """,
                (
                    uuid.uuid4(), user_id, provider, resource_url, environment,
                    "connected", access_token_ciphertext, refresh_token_ciphertext,
                    device_token_ciphertext, token_type, scope, token_expires_at,
                    json.dumps(server_info or {}), now, now, now, now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row) or {}


def get_connection(
    *,
    user_id: str,
    provider: str,
    resource_url: str,
    environment: str,
) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT c.id, c.user_id, c.provider, c.resource_url, c.environment,
                           c.client_registration_id, c.status,
                           c.access_token_ciphertext, c.refresh_token_ciphertext,
                           c.device_token_ciphertext,
                           c.token_type, c.scope, c.token_expires_at,
                           c.tool_catalog_json, c.server_info_json,
                           c.connected_at, c.last_verified_at, c.disconnected_at,
                           c.last_error, c.created_at, c.updated_at,
                           cl.client_id, cl.client_info_json, cl.oauth_metadata_json,
                           cl.redirect_uri
                    FROM {schema}.brokerage_connections c
                    LEFT JOIN {schema}.brokerage_mcp_clients cl
                      ON cl.id = c.client_registration_id
                    WHERE c.user_id = %s::uuid
                      AND c.provider = %s
                      AND c.resource_url = %s
                      AND c.environment = %s
                    LIMIT 1
                    """,
                    (user_id, provider, resource_url, environment),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _payload(row)
    except UndefinedTable:
        return None


def get_connection_by_id(*, connection_id: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, provider, resource_url, environment,
                           client_registration_id, status,
                           access_token_ciphertext, refresh_token_ciphertext,
                           device_token_ciphertext, token_type, scope,
                           token_expires_at, server_info_json,
                           connected_at, last_verified_at, disconnected_at,
                           last_error, created_at, updated_at
                    FROM {schema}.brokerage_connections
                    WHERE id = %s::uuid AND environment = %s
                    LIMIT 1
                    """,
                    (connection_id, environment),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _payload(row)
    except UndefinedTable:
        return None


def save_portfolio_snapshot(
    *,
    portfolio_id: str,
    connection_id: str,
    provider: str,
    environment: str,
    account_summary: dict[str, Any],
    positions: list[dict[str, Any]],
    timeseries: list[dict[str, Any]],
    source_updated_at: datetime,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            # Serialize only the short persistence transaction. Provider IO
            # stays outside the connection pool; separate refreshes may finish
            # out of order or succeed for different components.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                           (json.dumps(["brokerage_snapshot", portfolio_id, connection_id, environment]),))
            cursor.execute(f'''SELECT account_summary_json, positions_json, timeseries_json,
                details_json, source_updated_at FROM {schema}.brokerage_portfolio_snapshots
                WHERE portfolio_id=%s::uuid AND connection_id=%s::uuid AND environment=%s
                FOR UPDATE''', (portfolio_id, connection_id, environment))
            previous = _row_dict(cursor, cursor.fetchone())
            if previous and (details or {}).get('coverage'):
                details = dict(details)
                old_details = _json_value(previous.get('details_json'), {})
                coverage = dict(details.get('coverage') or {})
                old_coverage = old_details.get('coverage') or {}
                for component in ('account', 'positions', 'history', 'orders','activities','intraday'):
                    old_stamp, new_stamp = old_coverage.get(component), coverage.get(component)
                    if old_stamp and (not new_stamp or datetime.fromisoformat(old_stamp) > datetime.fromisoformat(new_stamp)):
                        coverage[component] = old_stamp
                        if component == 'account':
                            account_summary = _json_value(previous['account_summary_json'], {})
                        elif component == 'positions':
                            positions = _json_value(previous['positions_json'], [])
                        elif component == 'history':
                            timeseries = _json_value(previous['timeseries_json'], [])
                        elif component == 'intraday':
                            details['intraday']=old_details.get('intraday') or []
                        elif component == 'activities':
                            details['activities_sync']=old_details.get('activities_sync') or {}
                            details['activities_complete']=old_details.get('activities_complete',False)
                            details['new_activities']=old_details.get('new_activities') or []
                        else:
                            details['orders'] = old_details.get('orders') or []
                details['coverage'] = coverage
                if previous['source_updated_at'] > source_updated_at:
                    source_updated_at = previous['source_updated_at']
                    details['errors'] = old_details.get('errors') or {}
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_portfolio_snapshots (
                        id, portfolio_id, connection_id, provider, environment,
                        account_summary_json, positions_json, timeseries_json, details_json,
                        source_updated_at, created_at, updated_at
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s, %s
                    )
                    ON CONFLICT (portfolio_id, connection_id, environment) DO UPDATE SET
                        connection_id = EXCLUDED.connection_id,
                        provider = EXCLUDED.provider,
                        account_summary_json = EXCLUDED.account_summary_json,
                        positions_json = EXCLUDED.positions_json,
                        timeseries_json = EXCLUDED.timeseries_json,
                        details_json = EXCLUDED.details_json,
                        source_updated_at = EXCLUDED.source_updated_at,
                        updated_at = EXCLUDED.updated_at
                    WHERE brokerage_portfolio_snapshots.source_updated_at <= EXCLUDED.source_updated_at
                    RETURNING id, portfolio_id, connection_id, provider, environment,
                              account_summary_json, positions_json, timeseries_json, details_json,
                              source_updated_at, created_at, updated_at
                    """,
                (
                    uuid.uuid4(), portfolio_id, connection_id, provider, environment,
                    json.dumps(account_summary), json.dumps(positions), json.dumps(timeseries),
                    json.dumps(details or {}),
                    source_updated_at, now, now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
            if not row:
                cursor.execute(f'''SELECT id, portfolio_id, connection_id, provider, environment,
                    account_summary_json, positions_json, timeseries_json, details_json,
                    source_updated_at, created_at, updated_at
                    FROM {schema}.brokerage_portfolio_snapshots
                    WHERE portfolio_id=%s::uuid AND connection_id=%s::uuid AND environment=%s''',
                    (portfolio_id, connection_id, environment))
                row = _row_dict(cursor, cursor.fetchone())
            if not row:
                raise RuntimeError('The brokerage snapshot could not be retained.')
    return _payload(row) or {}


def get_portfolio_snapshot(*, portfolio_id: str, connection_id: str, user_id: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT s.id, s.portfolio_id, s.connection_id, s.provider, s.environment,
                           s.account_summary_json, s.positions_json, s.timeseries_json, s.details_json,
                           s.source_updated_at, s.created_at, s.updated_at
                    FROM {schema}.brokerage_portfolio_snapshots s
                    JOIN {schema}.brokerage_connections c ON c.id = s.connection_id
                    WHERE s.portfolio_id = %s::uuid AND s.connection_id = %s::uuid
                      AND c.user_id = %s::uuid AND c.environment = %s AND s.environment = c.environment
                      AND s.provider = c.provider AND c.status = 'connected'
                    LIMIT 1
                    """,
                    (portfolio_id, connection_id, user_id, environment),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _payload(row)
    except UndefinedTable:
        return None


def get_portfolio_brokerage(*, portfolio_id: str) -> dict[str, Any] | None:
    normalized_id = str(portfolio_id or "").strip()
    if not normalized_id:
        return None
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    with conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    SELECT id, slug, name, brokerage_source,
                           brokerage_account_ref, status, updated_at
                    FROM {schema}.portfolios
                    WHERE id = %s::uuid AND status = %s
                    LIMIT 1
                    """,
                (normalized_id, "active"),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row)


def set_portfolio_brokerage(
    *,
    portfolio_id: str,
    brokerage_source: str,
    brokerage_account_ref: str | None,
) -> dict[str, Any]:
    normalized_id = str(portfolio_id or "").strip()
    normalized_source = str(brokerage_source or "").strip().lower()
    normalized_ref = str(brokerage_account_ref or "").strip()
    if not normalized_id or not normalized_source:
        raise ValueError("Portfolio and brokerage source are required.")
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.portfolios
                    SET brokerage_source = %s,
                        brokerage_account_ref = NULLIF(%s, ''),
                        updated_at = %s
                    WHERE id = %s::uuid AND status = %s
                    RETURNING id, slug, name, brokerage_source,
                              brokerage_account_ref, status, updated_at
                    """,
                (normalized_source, normalized_ref, now, normalized_id, "active"),
            )
            row = _row_dict(cursor, cursor.fetchone())
            if not row:
                raise ValueError("Active portfolio was not found.")
    return _payload(row) or {}


def update_connection_catalog(
    *,
    connection_id: str,
    tools: list[dict[str, Any]],
    server_info: dict[str, Any],
) -> None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_connections
                    SET tool_catalog_json = %s::jsonb,
                        server_info_json = %s::jsonb,
                        status = %s,
                        last_verified_at = %s,
                        last_error = NULL,
                        updated_at = %s
                    WHERE id = %s::uuid
                    """,
                (json.dumps(tools), json.dumps(server_info), "connected", now, now, connection_id),
            )


def mark_connection_error(*, connection_id: str, error: str, status: str = "error") -> None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_connections
                    SET status = %s, last_error = %s, updated_at = %s
                    WHERE id = %s::uuid
                    """,
                (status, error[:1000], now, connection_id),
            )


def disconnect_connection(
    *,
    user_id: str,
    provider: str,
    resource_url: str,
    environment: str,
) -> bool:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_connections
                    SET status = %s,
                        access_token_ciphertext = NULL,
                        refresh_token_ciphertext = NULL,
                        device_token_ciphertext = NULL,
                        token_expires_at = NULL,
                        tool_catalog_json = '[]'::jsonb,
                        server_info_json = '{{}}'::jsonb,
                        disconnected_at = %s,
                        last_error = NULL,
                        updated_at = %s
                    WHERE user_id = %s::uuid AND provider = %s AND resource_url = %s AND environment = %s
                    """,
                ("disconnected", now, now, user_id, provider, resource_url, environment),
            )
            disconnected = int(cursor.rowcount or 0) == 1
            if disconnected:
                cursor.execute(
                    f"""
                        UPDATE {schema}.brokerage_order_tickets
                        SET status = %s, error = %s, completed_at = %s
                        WHERE user_id = %s::uuid
                          AND provider = %s
                          AND environment = %s
                          AND status = %s
                        """,
                    (
                        "expired",
                        "Robinhood connection was disconnected.",
                        now,
                        user_id,
                        provider,
                        environment,
                        "reviewed",
                    ),
                )
            return disconnected


def start_audit_event(
    *,
    connection_id: str,
    user_id: str,
    provider: str,
    environment: str,
    tool_name: str,
    intent: str,
    request_summary: dict[str, Any],
    candidate_id: str = "",
) -> str:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    audit_id = str(uuid.uuid4())
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_mcp_audit_events (
                        id, connection_id, user_id, provider, environment, tool_name, intent,
                        candidate_id, request_summary_json, status, started_at
                    ) VALUES (
                        %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s,
                        NULLIF(%s, ''), %s::jsonb, %s, %s
                    )
                    """,
                (
                    audit_id, connection_id, user_id, provider, environment, tool_name,
                    intent, candidate_id, json.dumps(request_summary), "started", now,
                ),
            )
    return audit_id


def finish_audit_event(
    *,
    audit_id: str,
    status: str,
    result_summary: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_mcp_audit_events
                    SET status = %s,
                        result_summary_json = %s::jsonb,
                        error = NULLIF(%s, ''),
                        completed_at = %s
                    WHERE id = %s::uuid
                    """,
                (status, json.dumps(result_summary or {}), error[:1000], now, audit_id),
            )


def create_order_ticket(
    *,
    connection_id: str,
    user_id: str,
    provider: str,
    environment: str,
    review_tool_name: str,
    execution_tool_name: str,
    arguments_hash: str,
    ref_id: str,
    request_summary: dict[str, Any],
    expires_at: datetime,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    ticket_id = str(uuid.uuid4())
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_order_tickets
                    SET status = %s, completed_at = %s
                    WHERE user_id = %s::uuid
                      AND provider = %s
                      AND environment = %s
                      AND status = %s
                    """,
                ("expired", now, user_id, provider, environment, "reviewed"),
            )
            cursor.execute(
                f"""
                    INSERT INTO {schema}.brokerage_order_tickets (
                        id, connection_id, user_id, provider, environment,
                        review_tool_name, execution_tool_name, arguments_hash,
                        ref_id, request_summary_json, status, expires_at, created_at
                    ) VALUES (
                        %s::uuid, %s::uuid, %s::uuid, %s, %s,
                        %s, %s, %s, %s::uuid, %s::jsonb, %s, %s, %s
                    )
                    RETURNING id, connection_id, user_id, provider, environment,
                              review_tool_name, execution_tool_name, arguments_hash,
                              ref_id, request_summary_json, status, expires_at, created_at
                    """,
                (
                    ticket_id,
                    connection_id,
                    user_id,
                    provider,
                    environment,
                    review_tool_name,
                    execution_tool_name,
                    arguments_hash,
                    ref_id,
                    json.dumps(request_summary),
                    "reviewed",
                    expires_at,
                    now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row) or {}


def consume_order_ticket(
    *,
    ticket_id: str,
    user_id: str,
    provider: str,
    environment: str,
    arguments_hash: str,
) -> dict[str, Any] | None:
    """Atomically claim one unexpired ticket bound to the exact reviewed payload."""
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_order_tickets
                    SET status = %s, confirmed_at = %s
                    WHERE id = %s::uuid
                      AND user_id = %s::uuid
                      AND provider = %s
                      AND environment = %s
                      AND arguments_hash = %s
                      AND status = %s
                      AND expires_at > %s
                    RETURNING id, connection_id, user_id, provider, environment,
                              review_tool_name, execution_tool_name, arguments_hash,
                              ref_id, request_summary_json, status, expires_at,
                              created_at, confirmed_at
                    """,
                (
                    "executing",
                    now,
                    ticket_id,
                    user_id,
                    provider,
                    environment,
                    arguments_hash,
                    "reviewed",
                    now,
                ),
            )
            row = _row_dict(cursor, cursor.fetchone())
    return _payload(row)


def get_order_ticket(*, ticket_id: str, user_id: str, provider: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError('Application database is unavailable.')
    schema = auth_store.auth_store_schema_name()
    with conn:
        with conn.cursor() as cursor:
            cursor.execute(f'''SELECT id, connection_id, user_id, provider, environment,
                arguments_hash, request_summary_json, result_summary_json, status, expires_at
                FROM {schema}.brokerage_order_tickets
                WHERE id=%s::uuid AND user_id=%s::uuid AND provider=%s AND environment=%s''',
                (ticket_id, user_id, provider, environment))
            return _payload(_row_dict(cursor, cursor.fetchone()))


def cancel_order_ticket(
    *,
    ticket_id: str,
    user_id: str,
    provider: str,
    environment: str,
) -> bool:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_order_tickets
                    SET status = %s, completed_at = %s
                    WHERE id = %s::uuid
                      AND user_id = %s::uuid
                      AND provider = %s
                      AND environment = %s
                      AND status = %s
                    """,
                ("expired", now, ticket_id, user_id, provider, environment, "reviewed"),
            )
            return int(cursor.rowcount or 0) == 1


def finish_order_ticket(
    *,
    ticket_id: str,
    status: str,
    result_summary: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if status not in {"executed", "failed", "unknown"}:
        raise ValueError("Invalid terminal order-ticket status.")
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with conn:
        _ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {schema}.brokerage_order_tickets
                    SET status = %s,
                        result_summary_json = %s::jsonb,
                        error = NULLIF(%s, ''),
                        completed_at = %s
                    WHERE id = %s::uuid AND (status = %s OR (status = 'unknown' AND %s = 'executed'))
                    """,
                (
                    status,
                    json.dumps(result_summary or {}),
                    error[:1000],
                    now,
                    ticket_id,
                    "executing",
                    status,
                ),
            )


__all__ = [
    "cancel_order_ticket",
    "consume_order_ticket",
    "consume_oauth_flow",
    "create_order_ticket",
    "create_oauth_flow",
    "disconnect_connection",
    "ensure_brokerage_mcp_schema",
    "finish_audit_event",
    "finish_oauth_flow",
    "finish_order_ticket",
    "get_client_registration",
    "get_connection",
    "get_connection_by_id",
    "get_portfolio_brokerage",
    "get_portfolio_snapshot",
    "mark_connection_error",
    "save_direct_connection",
    "save_connection_tokens",
    "save_portfolio_snapshot",
    "set_portfolio_brokerage",
    "start_audit_event",
    "update_connection_catalog",
    "upsert_client_registration",
]


# Paper Trading shares the existing brokerage database and ownership boundary.
# These are durable call/order records, not a second source of account balances.
def _paper_scope(context: Any, environment: str) -> tuple[str, str, str]:
    return context.user_id, context.portfolio_id, environment


def _paper_connection(*, initialize: bool = False):
    from contextlib import contextmanager

    @contextmanager
    def opened():
        conn = auth_store.open_auth_store_connection()
        if conn is None:
            raise RuntimeError("The paper trading database is unavailable.")
        try:
            with conn:
                if initialize:
                    _ensure_schema(conn)
                yield conn
        finally:
            conn.close()

    return opened()


def _paper_rows(cursor: Any) -> list[dict[str, Any]]:
    return [
        json.loads(json.dumps(_row_dict(cursor, row), default=str))
        for row in cursor.fetchall()
    ]


def paper_state(
    context: Any,
    *,
    environment: str,
    include_activities: bool = False,
    before: tuple[str, str] | None = None,
) -> dict[str, Any]:
    schema = auth_store.auth_store_schema_name()
    scope = _paper_scope(context, environment)
    try:
        with _paper_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT id,candidate_id,parent_call_id,intent,proposed_at,valid_until,decision,decided_at,
                (performance_json - 'fills') || jsonb_build_object('fill_count',jsonb_array_length(COALESCE(performance_json->'fills','[]'::jsonb))) AS performance,
                jsonb_build_object('candidate_id',candidate_id,'ticker',candidate_json->'ticker',
                    'stance',candidate_json->'stance','hypothesis',candidate_json->'hypothesis',
                    'setup',candidate_json->'setup','horizon_label',candidate_json->'horizon_label',
                    'horizon_key',candidate_json->'horizon_key','reason',candidate_json->'reason',
                    'quantity_fraction',candidate_json->'quantity_fraction','quantity',candidate_json->'quantity',
                    'proposal_source',candidate_json->'proposal_source',
                    'reference_price',candidate_json#>'{{forecast,reference_price}}') AS candidate
                FROM {schema}.paper_trading_calls WHERE user_id=%s::uuid AND portfolio_id=%s::uuid
                AND environment=%s ORDER BY proposed_at DESC,id""",
                scope,
            )
            calls = _paper_rows(cur)
            cur.execute(
                f"""SELECT mode,entries_paused,risk_json AS risk_policy,revision,account_binding,updated_at
                FROM {schema}.paper_trading_policy WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s""",
                scope,
            )
            policies = _paper_rows(cur)
            cur.execute(
                f"""SELECT client_order_id,broker_order_id,call_id,action_id,order_json AS data,updated_at
                FROM {schema}.paper_trading_orders WHERE user_id=%s::uuid AND portfolio_id=%s::uuid
                AND environment=%s ORDER BY updated_at DESC""",
                scope,
            )
            orders = _paper_rows(cur)
            cursor_clause = (
                "AND (occurred_at,id)<(%s::timestamptz,%s)" if before else ""
            )
            cur.execute(
                f"""SELECT id,kind,call_id,occurred_at,payload_json AS payload
                FROM {schema}.paper_trading_events WHERE user_id=%s::uuid AND portfolio_id=%s::uuid
                AND environment=%s AND kind NOT IN ('call_mark','broker_activity','broker_revision') {cursor_clause}
                ORDER BY occurred_at DESC,id DESC LIMIT 51""",
                (*scope, *(before or ())),
            )
            events = _paper_rows(cur)
            activities = []
            if include_activities:
                cur.execute(
                    f"""SELECT payload_json AS activity FROM {schema}.paper_trading_events
                    WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s
                    AND kind='broker_activity' ORDER BY occurred_at,id""",
                    scope,
                )
                activities = [row["activity"] for row in _paper_rows(cur)]
        return {
            "status": "available",
            "calls": calls,
            "orders": orders,
            "events": events[:50],
            "has_earlier_activity": len(events) > 50,
            "policy": policies[0] if policies else {},
            "activities": activities,
        }
    except UndefinedTable:
        return {
            "status": "pending",
            "calls": [],
            "orders": [],
            "events": [],
            "policy": {},
            "activities": [],
        }


def paper_policy(context: Any, *, environment: str) -> dict:
    schema = auth_store.auth_store_schema_name()
    try:
        with _paper_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT mode,entries_paused,risk_json AS risk_policy,revision,account_binding,updated_at FROM {schema}.paper_trading_policy WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s",
                _paper_scope(context, environment),
            )
            rows = _paper_rows(cur)
        return rows[0] if rows else {}
    except UndefinedTable:
        return {}


def paper_call(
    context: Any, *, environment: str, identity: str
) -> dict[str, Any] | None:
    schema = auth_store.auth_store_schema_name()
    try:
        with _paper_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT *,candidate_json AS candidate,performance_json AS performance
                FROM {schema}.paper_trading_calls WHERE user_id=%s::uuid AND portfolio_id=%s::uuid
                AND environment=%s AND (id=%s OR candidate_id=%s)""",
                (*_paper_scope(context, environment), identity, identity),
            )
            rows = _paper_rows(cur)
        return rows[0] if rows else None
    except UndefinedTable:
        return None


def retain_paper_calls(
    context: Any, *, environment: str, calls: list[dict[str, Any]]
) -> None:
    schema = auth_store.auth_store_schema_name()
    with _paper_connection(initialize=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {schema}.paper_trading_policy(user_id,portfolio_id,environment,updated_at)
            VALUES(%s::uuid,%s::uuid,%s,%s) ON CONFLICT DO NOTHING""",
            (*_paper_scope(context, environment), _now_utc()),
        )
        for call in calls:
            parent = call.get("parent_call_id")
            if parent:
                cur.execute(
                    f"""SELECT id FROM {schema}.paper_trading_calls WHERE id=%s
                    AND user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s""",
                    (parent, *_paper_scope(context, environment)),
                )
                if cur.fetchone() is None:
                    raise ValueError("The original paper call is unavailable.")
            cur.execute(
                f"""INSERT INTO {schema}.paper_trading_calls
                (id,user_id,portfolio_id,environment,candidate_id,parent_call_id,intent,candidate_json,
                 proposed_at,valid_until,decision)
                VALUES(%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT(user_id,portfolio_id,environment,candidate_id) DO NOTHING""",
                (
                    call["id"],
                    *_paper_scope(context, environment),
                    call["candidate"]["candidate_id"],
                    parent,
                    call["intent"],
                    json.dumps(call["candidate"], default=str),
                    call["proposed_at"],
                    call.get("valid_until"),
                    call.get("decision", "pending"),
                ),
            )


def paper_event(
    context: Any,
    *,
    environment: str,
    identity: str,
    kind: str,
    occurred_at: Any,
    payload: dict[str, Any],
    call_id: str | None = None,
    connection_id: str | None = None,
) -> None:
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {schema}.paper_trading_events
            (id,user_id,portfolio_id,environment,connection_id,call_id,kind,occurred_at,payload_json)
            VALUES(%s,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s::jsonb)
            ON CONFLICT(id) DO UPDATE SET occurred_at=EXCLUDED.occurred_at,payload_json=EXCLUDED.payload_json
            WHERE paper_trading_events.kind='call_mark' AND paper_trading_events.occurred_at<=EXCLUDED.occurred_at""",
            (
                identity,
                *_paper_scope(context, environment),
                connection_id,
                call_id,
                kind,
                occurred_at,
                json.dumps(payload, default=str),
            ),
        )


def decide_paper_call(
    context: Any, *, environment: str, identity: str, decision: str
) -> None:
    if decision not in {"denied", "submitted", "superseded"}:
        raise ValueError("Invalid paper call decision.")
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {schema}.paper_trading_calls SET decision=%s,decided_at=%s
            WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s AND id=%s
            AND decision IN ('pending',%s) RETURNING id""",
            (
                decision,
                _now_utc(),
                *_paper_scope(context, environment),
                identity,
                decision,
            ),
        )
        if cur.fetchone() is None:
            raise ValueError("This call was already decided or is unavailable.")


def update_paper_policy(
    context: Any,
    *,
    environment: str,
    expected_revision: int,
    mode: str,
    entries_paused: bool,
    risk_policy: dict,
    account_binding: str | None = None,
) -> dict:
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    with _paper_connection(initialize=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (json.dumps(["paper_policy", *_paper_scope(context, environment)]),),
        )
        cur.execute(
            f"""SELECT revision FROM {schema}.paper_trading_policy WHERE
            user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s FOR UPDATE""",
            _paper_scope(context, environment),
        )
        previous = cur.fetchone()
        if int(previous[0] if previous else 0) != expected_revision:
            raise ValueError("Trading settings changed. Reload them before saving.")
        revision = expected_revision + 1
        cur.execute(
            f"""INSERT INTO {schema}.paper_trading_policy
            (user_id,portfolio_id,environment,mode,entries_paused,risk_json,revision,account_binding,updated_at)
            VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s)
            ON CONFLICT(user_id,portfolio_id,environment) DO UPDATE SET mode=EXCLUDED.mode,
                entries_paused=EXCLUDED.entries_paused,risk_json=EXCLUDED.risk_json,
                revision=EXCLUDED.revision,account_binding=EXCLUDED.account_binding,updated_at=EXCLUDED.updated_at""",
            (
                *_paper_scope(context, environment),
                mode,
                entries_paused,
                json.dumps(risk_policy),
                revision,
                account_binding,
                now,
            ),
        )
        payload = {
            "mode": mode,
            "entries_paused": entries_paused,
            "risk_policy": risk_policy,
            "revision": revision,
        }
        cur.execute(
            f"""INSERT INTO {schema}.paper_trading_events
            (id,user_id,portfolio_id,environment,kind,occurred_at,payload_json)
            VALUES(%s,%s::uuid,%s::uuid,%s,'policy_changed',%s,%s::jsonb)""",
            (
                str(uuid.uuid4()),
                *_paper_scope(context, environment),
                now,
                json.dumps(payload),
            ),
        )
    return {**payload, "updated_at": now.isoformat()}


def retain_paper_order(
    context: Any,
    *,
    environment: str,
    connection_id: str,
    call_id: str,
    action_id: str,
    ticket_id: str,
    order: dict,
) -> None:
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {schema}.paper_trading_orders
            (connection_id,user_id,portfolio_id,environment,client_order_id,broker_order_id,call_id,action_id,ticket_id,order_json,updated_at)
            VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid,%s::jsonb,%s)
            ON CONFLICT(connection_id,client_order_id) DO UPDATE SET
                broker_order_id=COALESCE(EXCLUDED.broker_order_id,paper_trading_orders.broker_order_id),
                order_json=EXCLUDED.order_json,updated_at=EXCLUDED.updated_at
            WHERE paper_trading_orders.call_id=EXCLUDED.call_id AND paper_trading_orders.action_id=EXCLUDED.action_id""",
            (
                connection_id,
                *_paper_scope(context, environment),
                order["client_order_id"],
                order.get("id"),
                call_id,
                action_id,
                ticket_id,
                json.dumps(order, default=str),
                _now_utc(),
            ),
        )


def save_paper_performance(
    context: Any, *, environment: str, call_id: str, performance: dict
) -> None:
    save_paper_performances(
        context, environment=environment, performances={call_id: performance}
    )


def save_paper_performances(
    context: Any, *, environment: str, performances: dict[str, dict]
) -> None:
    if not performances:
        return
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"""UPDATE {schema}.paper_trading_calls SET performance_json=%s::jsonb
            WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s AND id=%s
            AND (performance_json->>'as_of' IS NULL OR (performance_json->>'as_of')::timestamptz<=%s::timestamptz)""",
            [
                (
                    json.dumps(result, default=str),
                    *_paper_scope(context, environment),
                    call_id,
                    result.get("as_of"),
                )
                for call_id, result in performances.items()
            ],
        )


def paper_call_history(context: Any, *, environment: str, call_id: str) -> list[dict]:
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT id,kind,occurred_at,payload_json AS payload FROM {schema}.paper_trading_events
            WHERE user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s AND call_id=%s
            ORDER BY occurred_at,id""",
            (*_paper_scope(context, environment), call_id),
        )
        return _paper_rows(cur)


def paper_contexts(*, environment: str) -> list[dict]:
    """Scheduled work uses existing connection/snapshot ownership and active admin membership."""
    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT DISTINCT u.id AS user_id,u.email,u.first_name,u.last_name,u.display_name,u.role,
            p.id AS portfolio_id,p.slug AS portfolio_slug,p.name AS portfolio_name,
            m.role AS membership_role,m.share_fraction,m.can_view_full_portfolio
            FROM {schema}.brokerage_connections c JOIN {schema}.users u ON u.id=c.user_id
            JOIN {schema}.brokerage_portfolio_snapshots s ON s.connection_id=c.id AND s.environment=c.environment
            JOIN {schema}.portfolios p ON p.id=s.portfolio_id
            JOIN {schema}.portfolio_memberships m ON m.user_id=u.id AND m.portfolio_id=p.id
            WHERE c.provider='alpaca_paper' AND c.environment=%s AND c.status='connected'
            AND u.status='active' AND p.status='active' AND m.role='admin'
            AND m.effective_from<=%s AND (m.effective_to IS NULL OR m.effective_to>%s)""",
            (environment, _now_utc(), _now_utc()),
        )
        return _paper_rows(cur)


def retain_paper_events(context: Any, *, environment: str, events: list[dict]) -> None:
    """Batch broker facts in one transaction; corrections retain the prior version."""
    import hashlib

    schema = auth_store.auth_store_schema_name()
    with _paper_connection() as conn, conn.cursor() as cur:
        for event in events:
            payload = json.dumps(event["payload"], sort_keys=True, default=str)
            if event["kind"] == "broker_activity":
                cur.execute(
                    f"""SELECT payload_json FROM {schema}.paper_trading_events WHERE id=%s
                    AND user_id=%s::uuid AND portfolio_id=%s::uuid AND environment=%s FOR UPDATE""",
                    (event["id"], *_paper_scope(context, environment)),
                )
                previous = cur.fetchone()
                if previous and previous[0] != event["payload"]:
                    revision = (
                        event["id"]
                        + ":revision:"
                        + hashlib.sha256(
                            json.dumps(
                                previous[0], sort_keys=True, default=str
                            ).encode()
                        ).hexdigest()
                    )
                    cur.execute(
                        f"""INSERT INTO {schema}.paper_trading_events
                        (id,user_id,portfolio_id,environment,connection_id,call_id,kind,occurred_at,payload_json)
                        VALUES(%s,%s::uuid,%s::uuid,%s,%s::uuid,%s,'broker_revision',%s,%s::jsonb)
                        ON CONFLICT DO NOTHING""",
                        (
                            revision,
                            *_paper_scope(context, environment),
                            event.get("connection_id"),
                            event.get("call_id"),
                            _now_utc(),
                            json.dumps(previous[0], default=str),
                        ),
                    )
            cur.execute(
                f"""INSERT INTO {schema}.paper_trading_events
                (id,user_id,portfolio_id,environment,connection_id,call_id,kind,occurred_at,payload_json)
                VALUES(%s,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s::jsonb)
                ON CONFLICT(id) DO UPDATE SET occurred_at=EXCLUDED.occurred_at,payload_json=EXCLUDED.payload_json,
                    call_id=COALESCE(EXCLUDED.call_id,paper_trading_events.call_id)
                WHERE paper_trading_events.kind='broker_activity' OR
                    (paper_trading_events.kind='call_mark' AND paper_trading_events.occurred_at<=EXCLUDED.occurred_at)""",
                (
                    event["id"],
                    *_paper_scope(context, environment),
                    event.get("connection_id"),
                    event.get("call_id"),
                    event["kind"],
                    event["occurred_at"],
                    payload,
                ),
            )


def paper_control_lock(account_id: str):
    """Policy/denial changes serialize with the broker submission boundary."""
    from contextlib import contextmanager

    @contextmanager
    def locked():
        with _paper_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("alpaca_paper:" + account_id,),
            )
            yield

    return locked()
