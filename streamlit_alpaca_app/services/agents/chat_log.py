"""
Agent Chat Log — durable persistence for omnibar agent sessions.

Stores every agent run (query, tool calls, answer, metadata) in Postgres with
full payloads in Azure Blob Storage.  The Postgres row holds searchable metadata;
the blob holds the complete session JSON including all tool call arguments,
results, and the full answer markdown.

Storage layout:
  Postgres table: aql_chat_sessions (legacy table name, searchable index)
  Blob path:      agents/chat_logs/{date}/{run_id}.json (full payload)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from services.secrets import build_azure_credential, resolve_secret_value


try:
    import psycopg
except Exception:
    psycopg = None

try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None


# ---------------------------------------------------------------------------
# Infrastructure helpers (mirror SAA pattern)
# ---------------------------------------------------------------------------

def _db_connection() -> Any | None:
    conn_str = resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
    except Exception:
        return None


def _blob_service_client() -> Any | None:
    if BlobServiceClient is None:
        return None
    account_url = (os.getenv("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url:
        return None
    credential = build_azure_credential()
    if credential is None:
        return None
    try:
        return BlobServiceClient(account_url=account_url, credential=credential)
    except Exception:
        return None


def _storage_container() -> str:
    return (os.getenv("AZURE_STORAGE_CONTAINER") or "datasets").strip() or "datasets"


def _upload_blob_json(blob_path: str, payload: dict[str, Any]) -> bool:
    """Upload a JSON payload to blob storage. Returns True on success."""
    client = _blob_service_client()
    if client is None:
        return False
    try:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        blob.upload_blob(data, overwrite=True, content_type="application/json")
        return True
    except Exception:
        return False


def _read_blob_json(blob_path: str) -> dict[str, Any] | None:
    """Read a JSON payload from blob storage."""
    client = _blob_service_client()
    if client is None or not blob_path:
        return None
    try:
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        data = blob.download_blob().readall()
        parsed = json.loads(data.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def bootstrap_chat_log(conn: Any | None = None) -> bool:
    """Create the aql_chat_sessions table if it doesn't exist. Returns True on success."""
    own_conn = conn is None
    if conn is None:
        conn = _db_connection()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS aql_chat_sessions (
                    run_id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    query_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT,
                    confidence TEXT,
                    answer_preview TEXT,
                    tool_call_count INTEGER NOT NULL DEFAULT 0,
                    tool_names_json JSONB,
                    symbols_json JSONB,
                    symbols_key TEXT,
                    error_text TEXT,
                    limitations_json JSONB,
                    blob_path TEXT,
                    blob_chars BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    duration_seconds REAL
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_aql_chat_sessions_created_at
                ON aql_chat_sessions (created_at DESC)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_aql_chat_sessions_symbols_key
                ON aql_chat_sessions (symbols_key)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_aql_chat_sessions_status
                ON aql_chat_sessions (status, created_at DESC)
                """
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _extract_symbols(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Pull all unique ticker symbols from tool call arguments."""
    symbols: set[str] = set()
    for call in tool_calls:
        args = dict(call.get("arguments") or {})
        for key in ("symbols", "focus_symbols"):
            val = args.get(key)
            if isinstance(val, list):
                symbols.update(str(s).upper().strip() for s in val if str(s).strip())
        for key in ("ticker",):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                symbols.add(val.upper().strip())
    return sorted(symbols)


def _extract_tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Pull unique tool names from tool calls, in order of first use."""
    seen: set[str] = set()
    names: list[str] = []
    for call in tool_calls:
        name = str(call.get("tool_name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _symbols_key(symbols: list[str]) -> str:
    return "|".join(sorted(set(symbols))) if symbols else ""


def log_chat_session(
    *,
    run_id: str,
    query: str,
    status: str,
    model: str,
    confidence: str,
    answer_markdown: str,
    tool_calls: list[dict[str, Any]],
    limitations: list[str] | None = None,
    error: str | None = None,
    duration_seconds: float | None = None,
) -> bool:
    """
    Persist a complete agent session to blob + Postgres.

    Returns True if at least the Postgres row was written.
    Blob upload failure is non-fatal — the row stores enough to be useful alone.
    """
    symbols = _extract_symbols(tool_calls)
    tool_names = _extract_tool_names(tool_calls)
    now = datetime.now(timezone.utc)
    date_slug = now.strftime("%Y-%m-%d")

    # --- Full payload to blob ---
    full_payload = {
        "run_id": run_id,
        "query": query,
        "status": status,
        "model": model,
        "confidence": confidence,
        "answer_markdown": answer_markdown,
        "tool_calls": tool_calls,
        "symbols": symbols,
        "tool_names": tool_names,
        "limitations": limitations or [],
        "error": error,
        "created_at": now.isoformat(),
        "duration_seconds": duration_seconds,
    }
    blob_path = f"agents/chat_logs/{date_slug}/{run_id}.json"
    payload_json = json.dumps(full_payload, ensure_ascii=False, default=str)
    blob_chars = len(payload_json)
    blob_ok = _upload_blob_json(blob_path, full_payload)
    if not blob_ok:
        blob_path = ""
        blob_chars = 0

    # --- Searchable row to Postgres ---
    conn = _db_connection()
    if conn is None:
        return False
    try:
        bootstrap_chat_log(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aql_chat_sessions (
                    run_id, query, query_sha256, status, model, confidence,
                    answer_preview, tool_call_count, tool_names_json, symbols_json,
                    symbols_key, error_text, limitations_json,
                    blob_path, blob_chars, created_at, duration_seconds
                )
                VALUES (
                    %(run_id)s, %(query)s, %(query_sha256)s, %(status)s, %(model)s, %(confidence)s,
                    %(answer_preview)s, %(tool_call_count)s, %(tool_names_json)s, %(symbols_json)s,
                    %(symbols_key)s, %(error_text)s, %(limitations_json)s,
                    %(blob_path)s, %(blob_chars)s, %(created_at)s, %(duration_seconds)s
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    confidence = EXCLUDED.confidence,
                    answer_preview = EXCLUDED.answer_preview,
                    blob_path = EXCLUDED.blob_path,
                    blob_chars = EXCLUDED.blob_chars
                """,
                {
                    "run_id": run_id,
                    "query": query,
                    "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    "status": status,
                    "model": model,
                    "confidence": confidence,
                    "answer_preview": answer_markdown,
                    "tool_call_count": len(tool_calls),
                    "tool_names_json": json.dumps(tool_names, ensure_ascii=False),
                    "symbols_json": json.dumps(symbols, ensure_ascii=False),
                    "symbols_key": _symbols_key(symbols),
                    "error_text": error,
                    "limitations_json": json.dumps(limitations or [], ensure_ascii=False),
                    "blob_path": blob_path,
                    "blob_chars": blob_chars,
                    "created_at": now,
                    "duration_seconds": duration_seconds,
                },
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def load_chat_session(run_id: str) -> dict[str, Any] | None:
    """
    Load the full session payload for a run_id.

    Tries blob first (full payload with tool call details).
    Falls back to the Postgres row if blob is unavailable.
    """
    # Try blob
    conn = _db_connection()
    blob_path = ""
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT blob_path FROM aql_chat_sessions WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                if row:
                    blob_path = str(row[0] or "")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if blob_path:
        payload = _read_blob_json(blob_path)
        if payload is not None:
            return payload

    # Fallback: reconstruct from Postgres row
    conn = _db_connection()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, query, status, model, confidence, answer_preview,
                       tool_call_count, tool_names_json, symbols_json,
                       error_text, limitations_json, created_at, duration_seconds
                FROM aql_chat_sessions WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "run_id": row[0],
                "query": row[1],
                "status": row[2],
                "model": row[3],
                "confidence": row[4],
                "answer_markdown": row[5],
                "tool_call_count": row[6],
                "tool_names": _json_list(row[7]),
                "symbols": _json_list(row[8]),
                "error": row[9],
                "limitations": _json_list(row[10]),
                "created_at": row[11].isoformat() if row[11] else None,
                "duration_seconds": row[12],
                "source": "postgres_fallback",
            }
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_chat_sessions(
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    symbol: str | None = None,
    query_contains: str | None = None,
) -> list[dict[str, Any]]:
    """
    List recent chat sessions from Postgres, newest first.

    Filters:
      status — match exact status (e.g. 'completed', 'failed')
      symbol — match sessions that mention this ticker
      query_contains — case-insensitive substring match on the query text
    """
    conn = _db_connection()
    if conn is None:
        return []
    try:
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": max(int(limit), 1),
            "offset": max(int(offset), 0),
        }
        if status:
            clauses.append("status = %(status)s")
            params["status"] = status
        if symbol:
            target = symbol.upper().strip()
            clauses.append("symbols_key LIKE %(symbol_pattern)s")
            params["symbol_pattern"] = f"%{target}%"
        if query_contains:
            clauses.append("query ILIKE %(query_pattern)s")
            params["query_pattern"] = f"%{query_contains}%"

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT run_id, query, status, model, confidence,
                   tool_call_count, tool_names_json, symbols_json,
                   created_at, duration_seconds, blob_chars
            FROM aql_chat_sessions
            {where}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append({
                "run_id": row[0],
                "query": row[1],
                "status": row[2],
                "model": row[3],
                "confidence": row[4],
                "tool_call_count": row[5],
                "tool_names": _json_list(row[6]),
                "symbols": _json_list(row[7]),
                "created_at": row[8].isoformat() if row[8] else None,
                "duration_seconds": row[9],
                "blob_chars": row[10],
            })
        return results
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def count_chat_sessions(
    *,
    status: str | None = None,
    symbol: str | None = None,
) -> int:
    """Count total chat sessions matching the filters."""
    conn = _db_connection()
    if conn is None:
        return 0
    try:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if status:
            clauses.append("status = %(status)s")
            params["status"] = status
        if symbol:
            target = symbol.upper().strip()
            clauses.append("symbols_key LIKE %(symbol_pattern)s")
            params["symbol_pattern"] = f"%{target}%"

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM aql_chat_sessions {where}"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


__all__ = [
    "bootstrap_chat_log",
    "count_chat_sessions",
    "list_chat_sessions",
    "load_chat_session",
    "log_chat_session",
]
