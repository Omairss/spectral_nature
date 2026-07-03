"""
Agent Chat Log — durable persistence for Zopedia chat sessions.

Stores every agent run (query, tool calls, answer, metadata) in Postgres with
full payloads in Azure Blob Storage.  The Postgres row holds searchable metadata;
the blob holds the complete session JSON including all tool call arguments,
results, and the full answer markdown.

Storage layout:
  Postgres table: aql_chat_sessions (legacy run-log table)
  Postgres tables: saa_zopedia_chat_threads, saa_zopedia_chat_messages
  Blob path:      agents/chat_logs/{date}/{run_id}.json (full run payload)
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from services.secrets import build_azure_credential, postgres_connect_timeout_seconds, resolve_secret_value


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
        return psycopg.connect(conn_str, connect_timeout=postgres_connect_timeout_seconds())
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


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _thread_title(text: str, *, limit: int = 80) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return "New Zopedia chat"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 3, 1)].rstrip() + "..."


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def bootstrap_chat_log(conn: Any | None = None) -> bool:
    """Create agent run-log and Zopedia chat tables if they do not exist."""
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS saa_zopedia_chat_threads (
                    thread_id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_saa_zopedia_chat_threads_user_updated
                ON saa_zopedia_chat_threads (user_key, updated_at DESC)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS saa_zopedia_chat_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES saa_zopedia_chat_threads(thread_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT,
                    payload_json JSONB,
                    run_id TEXT,
                    sequence_index INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_saa_zopedia_chat_messages_thread_sequence
                ON saa_zopedia_chat_messages (thread_id, sequence_index)
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
# Durable Zopedia chat threads
# ---------------------------------------------------------------------------

def create_chat_thread(
    *,
    user_key: str = "default",
    title: str = "",
    metadata: dict[str, Any] | None = None,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """Create a durable Zopedia chat thread."""
    own_conn = conn is None
    if conn is None:
        conn = _db_connection()
    if conn is None:
        return None
    thread_id = f"zthread_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)
    safe_user_key = str(user_key or "default").strip() or "default"
    safe_title = _thread_title(title)
    try:
        bootstrap_chat_log(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO saa_zopedia_chat_threads (
                    thread_id, user_key, title, status, metadata_json, created_at, updated_at
                )
                VALUES (
                    %(thread_id)s, %(user_key)s, %(title)s, 'active',
                    %(metadata_json)s, %(created_at)s, %(updated_at)s
                )
                """,
                {
                    "thread_id": thread_id,
                    "user_key": safe_user_key,
                    "title": safe_title,
                    "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    "created_at": now,
                    "updated_at": now,
                },
            )
        if own_conn:
            conn.commit()
        return {
            "thread_id": thread_id,
            "user_key": safe_user_key,
            "title": safe_title,
            "status": "active",
            "metadata": metadata or {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    except Exception:
        if own_conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def append_chat_message(
    *,
    thread_id: str | None,
    role: str,
    content: str = "",
    payload: dict[str, Any] | None = None,
    run_id: str = "",
    user_key: str = "default",
    title: str = "",
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """Append a message to a durable Zopedia thread, creating the thread if needed."""
    own_conn = conn is None
    if conn is None:
        conn = _db_connection()
    if conn is None:
        return None
    safe_user_key = str(user_key or "default").strip() or "default"
    safe_thread_id = str(thread_id or "").strip()
    safe_role = str(role or "").strip().lower()
    if safe_role not in {"user", "assistant", "system"}:
        safe_role = "assistant"
    now = datetime.now(timezone.utc)
    try:
        bootstrap_chat_log(conn)
        if not safe_thread_id:
            created = create_chat_thread(
                user_key=safe_user_key,
                title=title or content,
                metadata={"source": "zopedia_chat"},
                conn=conn,
            )
            safe_thread_id = str((created or {}).get("thread_id") or "").strip()
        if not safe_thread_id:
            if own_conn:
                conn.rollback()
            return None
        safe_title = _thread_title(title or content)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO saa_zopedia_chat_threads (
                    thread_id, user_key, title, status, metadata_json, created_at, updated_at
                )
                VALUES (
                    %(thread_id)s, %(user_key)s, %(title)s, 'active',
                    %(metadata_json)s, %(created_at)s, %(updated_at)s
                )
                ON CONFLICT (thread_id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    title = CASE
                        WHEN saa_zopedia_chat_threads.title = 'New Zopedia chat' THEN EXCLUDED.title
                        ELSE saa_zopedia_chat_threads.title
                    END
                """,
                {
                    "thread_id": safe_thread_id,
                    "user_key": safe_user_key,
                    "title": safe_title,
                    "metadata_json": json.dumps({"source": "zopedia_chat"}, ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            cur.execute(
                "SELECT user_key FROM saa_zopedia_chat_threads WHERE thread_id = %s",
                (safe_thread_id,),
            )
            owner_row = cur.fetchone()
            if not owner_row or str(owner_row[0] or "") != safe_user_key:
                raise PermissionError("Thread does not belong to this user.")
            cur.execute(
                """
                SELECT COALESCE(MAX(sequence_index), -1) + 1
                FROM saa_zopedia_chat_messages
                WHERE thread_id = %s
                """,
                (safe_thread_id,),
            )
            row = cur.fetchone()
            sequence_index = int(row[0]) if row else 0
            message_id = f"zmsg_{uuid.uuid4().hex[:16]}"
            cur.execute(
                """
                INSERT INTO saa_zopedia_chat_messages (
                    message_id, thread_id, role, content, payload_json, run_id, sequence_index, created_at
                )
                VALUES (
                    %(message_id)s, %(thread_id)s, %(role)s, %(content)s,
                    %(payload_json)s, %(run_id)s, %(sequence_index)s, %(created_at)s
                )
                """,
                {
                    "message_id": message_id,
                    "thread_id": safe_thread_id,
                    "role": safe_role,
                    "content": str(content or ""),
                    "payload_json": json.dumps(payload or {}, ensure_ascii=False, default=str),
                    "run_id": str(run_id or ""),
                    "sequence_index": sequence_index,
                    "created_at": now,
                },
            )
            cur.execute(
                """
                UPDATE saa_zopedia_chat_threads
                SET updated_at = %(updated_at)s
                WHERE thread_id = %(thread_id)s
                """,
                {"updated_at": now, "thread_id": safe_thread_id},
            )
        if own_conn:
            conn.commit()
        return {
            "message_id": message_id,
            "thread_id": safe_thread_id,
            "role": safe_role,
            "content": str(content or ""),
            "payload": payload or {},
            "run_id": str(run_id or ""),
            "sequence_index": sequence_index,
            "created_at": now.isoformat(),
        }
    except Exception:
        if own_conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def list_chat_threads(
    *,
    user_key: str = "default",
    limit: int = 20,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    """List recent durable Zopedia chat threads for one user key."""
    own_conn = conn is None
    if conn is None:
        conn = _db_connection()
    if conn is None:
        return []
    safe_user_key = str(user_key or "default").strip() or "default"
    try:
        bootstrap_chat_log(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id, user_key, title, status, metadata_json, created_at, updated_at
                FROM saa_zopedia_chat_threads
                WHERE user_key = %s AND status <> 'deleted'
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (safe_user_key, max(int(limit), 1)),
            )
            rows = cur.fetchall()
        return [
            {
                "thread_id": row[0],
                "user_key": row[1],
                "title": row[2],
                "status": row[3],
                "metadata": _json_dict(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
                "updated_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def load_chat_thread(
    *,
    thread_id: str,
    user_key: str = "default",
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """Load one durable Zopedia chat thread and its messages."""
    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        return None
    own_conn = conn is None
    if conn is None:
        conn = _db_connection()
    if conn is None:
        return None
    safe_user_key = str(user_key or "default").strip() or "default"
    try:
        bootstrap_chat_log(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id, user_key, title, status, metadata_json, created_at, updated_at
                FROM saa_zopedia_chat_threads
                WHERE thread_id = %s AND user_key = %s AND status <> 'deleted'
                """,
                (safe_thread_id, safe_user_key),
            )
            thread_row = cur.fetchone()
            if thread_row is None:
                return None
            cur.execute(
                """
                SELECT message_id, role, content, payload_json, run_id, sequence_index, created_at
                FROM saa_zopedia_chat_messages
                WHERE thread_id = %s
                ORDER BY sequence_index ASC, created_at ASC
                """,
                (safe_thread_id,),
            )
            message_rows = cur.fetchall()
        return {
            "thread_id": thread_row[0],
            "user_key": thread_row[1],
            "title": thread_row[2],
            "status": thread_row[3],
            "metadata": _json_dict(thread_row[4]),
            "created_at": thread_row[5].isoformat() if thread_row[5] else None,
            "updated_at": thread_row[6].isoformat() if thread_row[6] else None,
            "messages": [
                {
                    "message_id": row[0],
                    "role": row[1],
                    "content": row[2],
                    "payload": _json_dict(row[3]),
                    "run_id": row[4],
                    "sequence_index": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                }
                for row in message_rows
            ],
        }
    except Exception:
        return None
    finally:
        if own_conn:
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
    "append_chat_message",
    "bootstrap_chat_log",
    "count_chat_sessions",
    "create_chat_thread",
    "list_chat_sessions",
    "list_chat_threads",
    "load_chat_session",
    "load_chat_thread",
    "log_chat_session",
]
