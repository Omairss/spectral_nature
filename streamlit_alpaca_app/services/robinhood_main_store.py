from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any, Iterable

from . import auth_store


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    def _default(item: Any) -> Any:
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, uuid.UUID):
            return str(item)
        raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")

    return json.dumps(value, allow_nan=False, separators=(",", ":"), default=_default)


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    columns = [item.name if hasattr(item, "name") else item[0] for item in cursor.description or []]
    return {str(key): value for key, value in zip(columns, row)}


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback
    return fallback


def _snapshot_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    out["id"] = str(out.get("id") or "")
    out["portfolio_id"] = str(out.get("portfolio_id") or "")
    out["connection_id"] = str(out.get("connection_id") or "")
    out["account_summary"] = _json_value(out.pop("account_summary_json", {}), {})
    out["positions"] = _json_value(out.pop("positions_json", []), [])
    out["details"] = _json_value(out.pop("details_json", {}), {})
    return out


def _resource_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for key in ("id", "portfolio_id", "connection_id"):
        out[key] = str(out.get(key) or "")
    out["payload"] = _json_value(out.pop("payload_json", {}), {})
    return out


def _request_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for key in ("id", "user_id", "portfolio_id", "connection_id"):
        out[key] = str(out.get(key) or "")
    out["result_summary"] = _json_value(out.pop("result_summary_json", {}), {})
    return out


def _ensure_schema(conn: Any) -> None:
    auth_store.ensure_auth_schema_on_connection(conn)
    schema = auth_store.auth_store_schema_name()
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.robinhood_main_snapshots (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            environment TEXT NOT NULL,
            account_summary_json JSONB NOT NULL,
            positions_json JSONB NOT NULL,
            details_json JSONB NOT NULL,
            source_updated_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (portfolio_id, environment)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_robinhood_main_snapshots_connection
        ON {schema}.robinhood_main_snapshots (connection_id, environment, updated_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.robinhood_main_series_points (
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            environment TEXT NOT NULL,
            series_kind TEXT NOT NULL,
            series_key TEXT NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            values_json JSONB NOT NULL,
            source_updated_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (portfolio_id, environment, series_kind, series_key, observed_at)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_robinhood_main_series_read
        ON {schema}.robinhood_main_series_points
            (portfolio_id, environment, series_kind, series_key, observed_at)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.robinhood_main_resources (
            id UUID PRIMARY KEY,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            environment TEXT NOT NULL,
            resource_kind TEXT NOT NULL,
            resource_key TEXT NOT NULL,
            payload_json JSONB NOT NULL,
            status TEXT NOT NULL,
            source_updated_at TIMESTAMPTZ NOT NULL,
            refresh_after TIMESTAMPTZ NOT NULL,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (portfolio_id, environment, resource_kind, resource_key)
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_robinhood_main_resources_refresh
        ON {schema}.robinhood_main_resources
            (portfolio_id, environment, resource_kind, refresh_after)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.robinhood_main_refresh_requests (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE,
            portfolio_id UUID NOT NULL REFERENCES {schema}.portfolios(id) ON DELETE CASCADE,
            connection_id UUID NOT NULL REFERENCES {schema}.brokerage_connections(id) ON DELETE CASCADE,
            environment TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL,
            not_before TIMESTAMPTZ NOT NULL,
            started_at TIMESTAMPTZ NULL,
            lease_until TIMESTAMPTZ NULL,
            completed_at TIMESTAMPTZ NULL,
            error TEXT NULL,
            result_summary_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_robinhood_main_refresh_queue
        ON {schema}.robinhood_main_refresh_requests (environment, status, not_before, requested_at)
        """,
    ]
    with conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def ensure_schema() -> bool:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    try:
        with conn:
            _ensure_schema(conn)
        return True
    finally:
        conn.close()


def schema_ready() -> bool:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    schema = auth_store.auth_store_schema_name()
    required = (
        "robinhood_main_snapshots",
        "robinhood_main_series_points",
        "robinhood_main_resources",
        "robinhood_main_refresh_requests",
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s) IS NOT NULL, to_regclass(%s) IS NOT NULL, "
                "to_regclass(%s) IS NOT NULL, to_regclass(%s) IS NOT NULL",
                tuple(f"{schema}.{table}" for table in required),
            )
            row = cursor.fetchone()
        return bool(row and all(bool(value) for value in row))
    finally:
        conn.close()


def save_snapshot(
    *,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    account_summary: dict[str, Any],
    positions: list[dict[str, Any]],
    details: dict[str, Any],
    source_updated_at: datetime,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.robinhood_main_snapshots (
                        id, portfolio_id, connection_id, environment,
                        account_summary_json, positions_json, details_json,
                        source_updated_at, created_at, updated_at
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s, %s
                    )
                    ON CONFLICT (portfolio_id, environment) DO UPDATE SET
                        connection_id = EXCLUDED.connection_id,
                        account_summary_json = EXCLUDED.account_summary_json,
                        positions_json = EXCLUDED.positions_json,
                        details_json = EXCLUDED.details_json,
                        source_updated_at = EXCLUDED.source_updated_at,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id, portfolio_id, connection_id, environment,
                              account_summary_json, positions_json, details_json,
                              source_updated_at, created_at, updated_at
                    """,
                    (
                        uuid.uuid4(), portfolio_id, connection_id, environment,
                        _json(account_summary), _json(positions), _json(details),
                        source_updated_at, now, now,
                    ),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _snapshot_payload(row) or {}
    finally:
        conn.close()


def get_snapshot(*, portfolio_id: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, portfolio_id, connection_id, environment,
                           account_summary_json, positions_json, details_json,
                           source_updated_at, created_at, updated_at
                    FROM {schema}.robinhood_main_snapshots
                    WHERE portfolio_id = %s::uuid AND environment = %s
                    LIMIT 1
                    """,
                    (portfolio_id, environment),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _snapshot_payload(row)
    finally:
        conn.close()


def upsert_series_batch(
    *,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    series_kind: str,
    points: Iterable[dict[str, Any]],
    source_updated_at: datetime,
) -> int:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    statement = f"""
        INSERT INTO {schema}.robinhood_main_series_points (
            portfolio_id, connection_id, environment, series_kind, series_key,
            observed_at, values_json, source_updated_at, created_at, updated_at
        ) VALUES (
            %s::uuid, %s::uuid, %s, %s, %s,
            %s, %s::jsonb, %s, %s, %s
        )
        ON CONFLICT (portfolio_id, environment, series_kind, series_key, observed_at)
        DO UPDATE SET
            connection_id = EXCLUDED.connection_id,
            values_json = {schema}.robinhood_main_series_points.values_json || EXCLUDED.values_json,
            source_updated_at = EXCLUDED.source_updated_at,
            updated_at = EXCLUDED.updated_at
    """
    persisted = 0
    batch: list[tuple[Any, ...]] = []
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                for point in points:
                    observed_at = point.get("observed_at") or point.get("timestamp")
                    series_key = str(point.get("series_key") or "").strip()
                    values = point.get("values") if isinstance(point.get("values"), dict) else {
                        key: value
                        for key, value in point.items()
                        if key not in {"observed_at", "timestamp", "series_key"}
                    }
                    if not observed_at or not series_key or not values:
                        continue
                    batch.append(
                        (
                            portfolio_id,
                            connection_id,
                            environment,
                            series_kind,
                            series_key,
                            observed_at,
                            _json(values),
                            source_updated_at,
                            now,
                            now,
                        )
                    )
                    if len(batch) >= 2_000:
                        cursor.executemany(statement, batch)
                        persisted += len(batch)
                        batch.clear()
                if batch:
                    cursor.executemany(statement, batch)
                    persisted += len(batch)
        return persisted
    finally:
        conn.close()


def upsert_series_points(
    *,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    series_kind: str,
    series_key: str,
    points: Iterable[dict[str, Any]],
    source_updated_at: datetime,
) -> int:
    return upsert_series_batch(
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        series_kind=series_kind,
        points=(dict(point, series_key=series_key) for point in points),
        source_updated_at=source_updated_at,
    )


def get_series_points(
    *,
    portfolio_id: str,
    environment: str,
    series_kind: str,
    series_keys: list[str] | None = None,
    start_at: datetime | None = None,
) -> list[dict[str, Any]]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return []
    schema = auth_store.auth_store_schema_name()
    clauses = ["portfolio_id = %s::uuid", "environment = %s", "series_kind = %s"]
    params: list[Any] = [portfolio_id, environment, series_kind]
    if series_keys:
        clauses.append("series_key = ANY(%s)")
        params.append(series_keys)
    if start_at is not None:
        clauses.append("observed_at >= %s")
        params.append(start_at)
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT series_key, observed_at, values_json, source_updated_at
                    FROM {schema}.robinhood_main_series_points
                    WHERE {' AND '.join(clauses)}
                    ORDER BY series_key, observed_at
                    """,
                    tuple(params),
                )
                rows = [_row_dict(cursor, row) for row in cursor.fetchall()]
        return [
            {
                "series_key": str(row.get("series_key") or ""),
                "observed_at": row.get("observed_at"),
                "values": _json_value(row.get("values_json"), {}),
                "source_updated_at": row.get("source_updated_at"),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_series_coverage(
    *,
    portfolio_id: str,
    environment: str,
    series_kind: str,
    series_keys: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return compact per-series coverage without loading the stored observations."""
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return {}
    schema = auth_store.auth_store_schema_name()
    clauses = ["portfolio_id = %s::uuid", "environment = %s", "series_kind = %s"]
    params: list[Any] = [portfolio_id, environment, series_kind]
    if series_keys:
        clauses.append("series_key = ANY(%s)")
        params.append(series_keys)
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT series_key,
                           MIN(observed_at) AS first_observed_at,
                           MAX(observed_at) AS last_observed_at,
                           COUNT(*) AS point_count
                    FROM {schema}.robinhood_main_series_points
                    WHERE {' AND '.join(clauses)}
                    GROUP BY series_key
                    ORDER BY series_key
                    """,
                    tuple(params),
                )
                rows = [_row_dict(cursor, row) for row in cursor.fetchall()]
        return {
            str(row.get("series_key") or ""): {
                "first_observed_at": row.get("first_observed_at"),
                "last_observed_at": row.get("last_observed_at"),
                "point_count": int(row.get("point_count") or 0),
            }
            for row in rows
            if row.get("series_key")
        }
    finally:
        conn.close()


def upsert_resource(
    *,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    resource_kind: str,
    resource_key: str,
    payload: dict[str, Any],
    source_updated_at: datetime,
    refresh_after: datetime,
    status: str = "ready",
    last_error: str = "",
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.robinhood_main_resources (
                        id, portfolio_id, connection_id, environment,
                        resource_kind, resource_key, payload_json, status,
                        source_updated_at, refresh_after, last_error, created_at, updated_at
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s,
                        %s, %s, %s::jsonb, %s,
                        %s, %s, NULLIF(%s, ''), %s, %s
                    )
                    ON CONFLICT (portfolio_id, environment, resource_kind, resource_key)
                    DO UPDATE SET
                        connection_id = EXCLUDED.connection_id,
                        payload_json = EXCLUDED.payload_json,
                        status = EXCLUDED.status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        refresh_after = EXCLUDED.refresh_after,
                        last_error = EXCLUDED.last_error,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id, portfolio_id, connection_id, environment,
                              resource_kind, resource_key, payload_json, status,
                              source_updated_at, refresh_after, last_error, created_at, updated_at
                    """,
                    (
                        uuid.uuid4(), portfolio_id, connection_id, environment,
                        resource_kind, resource_key, _json(payload), status,
                        source_updated_at, refresh_after, last_error, now, now,
                    ),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _resource_payload(row) or {}
    finally:
        conn.close()


def get_resource(
    *, portfolio_id: str, environment: str, resource_kind: str, resource_key: str
) -> dict[str, Any] | None:
    rows = list_resources(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind=resource_kind,
        resource_keys=[resource_key],
    )
    return rows[0] if rows else None


def mark_resource_error(
    *,
    portfolio_id: str,
    environment: str,
    resource_kind: str,
    resource_key: str,
    error: str,
    retry_after: datetime,
) -> bool:
    """Record failed collection without replacing the last successful payload."""
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.robinhood_main_resources
                    SET status = 'stale',
                        refresh_after = %s,
                        last_error = NULLIF(%s, ''),
                        updated_at = %s
                    WHERE portfolio_id = %s::uuid
                      AND environment = %s
                      AND resource_kind = %s
                      AND resource_key = %s
                    """,
                    (
                        retry_after,
                        str(error or "")[:500],
                        now,
                        portfolio_id,
                        environment,
                        resource_kind,
                        resource_key,
                    ),
                )
                return cursor.rowcount == 1
    finally:
        conn.close()


def list_resources(
    *,
    portfolio_id: str,
    environment: str,
    resource_kind: str,
    resource_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return []
    schema = auth_store.auth_store_schema_name()
    clauses = ["portfolio_id = %s::uuid", "environment = %s", "resource_kind = %s"]
    params: list[Any] = [portfolio_id, environment, resource_kind]
    if resource_keys:
        clauses.append("resource_key = ANY(%s)")
        params.append(resource_keys)
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, portfolio_id, connection_id, environment,
                           resource_kind, resource_key, payload_json, status,
                           source_updated_at, refresh_after, last_error, created_at, updated_at
                    FROM {schema}.robinhood_main_resources
                    WHERE {' AND '.join(clauses)}
                    ORDER BY resource_key
                    """,
                    tuple(params),
                )
                rows = [_resource_payload(_row_dict(cursor, row)) for row in cursor.fetchall()]
        return [row for row in rows if row is not None]
    finally:
        conn.close()


def enqueue_refresh(
    *,
    user_id: str,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    reason: str,
    not_before: datetime | None = None,
) -> dict[str, Any]:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        raise RuntimeError("Application database is unavailable.")
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    available_at = not_before or now
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, portfolio_id, connection_id, environment,
                           reason, status, requested_at, not_before, started_at,
                           lease_until, completed_at, error, result_summary_json
                    FROM {schema}.robinhood_main_refresh_requests
                    WHERE portfolio_id = %s::uuid
                      AND environment = %s
                      AND status IN ('queued', 'running')
                    ORDER BY requested_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (portfolio_id, environment),
                )
                existing = _request_payload(_row_dict(cursor, cursor.fetchone()))
                if existing:
                    return existing
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.robinhood_main_refresh_requests (
                        id, user_id, portfolio_id, connection_id, environment,
                        reason, status, requested_at, not_before, result_summary_json
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s::uuid, %s,
                        %s, 'queued', %s, %s, '{{}}'::jsonb
                    )
                    RETURNING id, user_id, portfolio_id, connection_id, environment,
                              reason, status, requested_at, not_before, started_at,
                              lease_until, completed_at, error, result_summary_json
                    """,
                    (
                        uuid.uuid4(), user_id, portfolio_id, connection_id, environment,
                        str(reason or "manual")[:64], now, available_at,
                    ),
                )
                row = _request_payload(_row_dict(cursor, cursor.fetchone()))
        return row or {}
    finally:
        conn.close()


def latest_refresh(*, portfolio_id: str, environment: str) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, portfolio_id, connection_id, environment,
                           reason, status, requested_at, not_before, started_at,
                           lease_until, completed_at, error, result_summary_json
                    FROM {schema}.robinhood_main_refresh_requests
                    WHERE portfolio_id = %s::uuid AND environment = %s
                    ORDER BY requested_at DESC
                    LIMIT 1
                    """,
                    (portfolio_id, environment),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _request_payload(row)
    finally:
        conn.close()


def enqueue_due_refreshes(
    *, environment: str, due_before: datetime, reason: str = "scheduled"
) -> int:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return 0
    schema = auth_store.auth_store_schema_name()
    linked: list[dict[str, Any]] = []
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT p.id AS portfolio_id,
                           c.id AS connection_id,
                           c.user_id,
                           snapshot.source_updated_at
                    FROM {schema}.portfolios AS p
                    JOIN {schema}.brokerage_connections AS c
                      ON p.brokerage_account_ref = c.id::text
                    LEFT JOIN {schema}.robinhood_main_snapshots AS snapshot
                      ON snapshot.portfolio_id = p.id
                     AND snapshot.environment = c.environment
                    WHERE p.status = 'active'
                      AND p.brokerage_source = 'robinhood_main'
                      AND c.provider = 'robinhood_main'
                      AND c.environment = %s
                      AND c.status = 'connected'
                      AND (
                          snapshot.source_updated_at IS NULL
                          OR snapshot.source_updated_at <= %s
                      )
                    """,
                    (environment, due_before),
                )
                linked = [_row_dict(cursor, row) for row in cursor.fetchall()]
    finally:
        conn.close()
    enqueued = 0
    for item in linked:
        request = enqueue_refresh(
            user_id=str(item.get("user_id") or ""),
            portfolio_id=str(item.get("portfolio_id") or ""),
            connection_id=str(item.get("connection_id") or ""),
            environment=environment,
            reason=reason,
        )
        if str(request.get("status") or "") == "queued":
            enqueued += 1
    return enqueued


def claim_next_refresh(*, environment: str, lease_seconds: int = 1800) -> dict[str, Any] | None:
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return None
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    lease_until = now + timedelta(seconds=max(60, min(int(lease_seconds), 7200)))
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    WITH candidate AS (
                        SELECT id
                        FROM {schema}.robinhood_main_refresh_requests
                        WHERE environment = %s
                          AND not_before <= %s
                          AND (
                              status = 'queued'
                              OR (status = 'running' AND lease_until < %s)
                          )
                        ORDER BY requested_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE {schema}.robinhood_main_refresh_requests AS request
                    SET status = 'running',
                        started_at = COALESCE(request.started_at, %s),
                        lease_until = %s,
                        error = NULL
                    FROM candidate
                    WHERE request.id = candidate.id
                    RETURNING request.id, request.user_id, request.portfolio_id,
                              request.connection_id, request.environment, request.reason,
                              request.status, request.requested_at, request.not_before,
                              request.started_at, request.lease_until, request.completed_at,
                              request.error, request.result_summary_json
                    """,
                    (environment, now, now, now, lease_until),
                )
                row = _row_dict(cursor, cursor.fetchone())
        return _request_payload(row)
    finally:
        conn.close()


def finish_refresh(
    *, request_id: str, status: str, result_summary: dict[str, Any] | None = None, error: str = ""
) -> bool:
    terminal = status if status in {"succeeded", "failed"} else "failed"
    conn = auth_store.open_auth_store_connection()
    if conn is None:
        return False
    schema = auth_store.auth_store_schema_name()
    now = _now_utc()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {schema}.robinhood_main_refresh_requests
                    SET status = %s,
                        completed_at = %s,
                        lease_until = NULL,
                        error = NULLIF(%s, ''),
                        result_summary_json = %s::jsonb
                    WHERE id = %s::uuid AND status = 'running'
                    """,
                    (terminal, now, str(error or "")[:500], _json(result_summary or {}), request_id),
                )
                return cursor.rowcount == 1
    finally:
        conn.close()


__all__ = [
    "claim_next_refresh",
    "enqueue_due_refreshes",
    "enqueue_refresh",
    "ensure_schema",
    "finish_refresh",
    "get_resource",
    "get_series_points",
    "get_snapshot",
    "latest_refresh",
    "list_resources",
    "mark_resource_error",
    "save_snapshot",
    "schema_ready",
    "upsert_resource",
    "upsert_series_batch",
    "upsert_series_points",
]
