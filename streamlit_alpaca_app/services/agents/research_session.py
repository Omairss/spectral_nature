from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import threading
from typing import Any, Callable, Protocol
import uuid

from services import pipeline_store


ProgressCallback = Callable[[dict[str, Any]], None]

_SCHEMA_BOOTSTRAP_LOCK = threading.Lock()
_SCHEMA_BOOTSTRAPPED = False

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "prompt",
    "reasoning",
    "reasoning_content",
    "reasoning_trace",
    "secret",
    "system_prompt",
    "token",
    "user_prompt",
}


class ResearchCancellationRequested(RuntimeError):
    """Raised at a harness progress boundary when a durable run is cancelled."""


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _clean(raw_key)[:120]
            if not key:
                continue
            lowered = key.lower()
            if lowered in _SENSITIVE_KEYS or any(part in lowered for part in ("api_key", "secret", "token")):
                continue
            safe[key] = _safe_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        text = value.strip()
        return text if len(text) <= 4000 else text[:3997].rstrip() + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(value)[:1000]


@dataclass(frozen=True)
class ResearchSessionEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    stage: str
    message: str
    progress: float
    payload: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
        }


class ResearchSessionStore(Protocol):
    def start(
        self,
        *,
        run_id: str,
        surface: str,
        task: str,
        query: str,
        provider: str,
        model: str,
        metadata: dict[str, Any],
        started_at: datetime,
    ) -> None: ...

    def append(self, event: ResearchSessionEvent) -> None: ...

    def last_sequence(self, run_id: str) -> int: ...

    def finish(
        self,
        *,
        run_id: str,
        status: str,
        provider: str,
        model: str,
        result_summary: dict[str, Any],
        completed_at: datetime,
    ) -> None: ...

    def close(self) -> None: ...


class NullResearchSessionStore:
    def start(self, **_: Any) -> None:
        return None

    def append(self, event: ResearchSessionEvent) -> None:
        del event

    def last_sequence(self, run_id: str) -> int:
        del run_id
        return 0

    def finish(self, **_: Any) -> None:
        return None

    def close(self) -> None:
        return None


class PostgresResearchSessionStore:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        global _SCHEMA_BOOTSTRAPPED
        with _SCHEMA_BOOTSTRAP_LOCK:
            if _SCHEMA_BOOTSTRAPPED:
                return
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name IN ('aql_research_sessions', 'aql_research_session_events')
                    """
                )
                existing_columns: dict[str, set[str]] = {}
                for table_name, column_name in cur.fetchall():
                    existing_columns.setdefault(str(table_name), set()).add(str(column_name))
            self.connection.commit()
            required_session_columns = {
                "run_id", "surface", "task", "query", "query_sha256", "status", "provider", "model",
                "metadata_json", "result_summary_json", "last_event_sequence", "started_at", "updated_at",
                "completed_at", "owner_type", "owner_id", "owner_email", "idempotency_key", "environment",
                "write_policy", "budget_json", "result_json", "error_code", "dispatch_error",
                "cancel_requested_at", "claimed_at", "dispatched_at", "executor_id", "attempt_count",
            }
            required_event_columns = {
                "event_id", "run_id", "sequence", "event_type", "stage", "message", "progress",
                "payload_json", "created_at",
            }
            if (
                required_session_columns.issubset(existing_columns.get("aql_research_sessions", set()))
                and required_event_columns.issubset(existing_columns.get("aql_research_session_events", set()))
            ):
                _SCHEMA_BOOTSTRAPPED = True
                return

            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS aql_research_sessions (
                        run_id TEXT PRIMARY KEY,
                        surface TEXT NOT NULL,
                        task TEXT NOT NULL,
                        query TEXT NOT NULL,
                        query_sha256 TEXT NOT NULL,
                        status TEXT NOT NULL,
                        provider TEXT,
                        model TEXT,
                        metadata_json JSONB,
                        result_summary_json JSONB,
                        last_event_sequence INTEGER NOT NULL DEFAULT 0,
                        started_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        completed_at TIMESTAMPTZ
                    )
                    """
                )
                for statement in (
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS owner_type TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS owner_id TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS owner_email TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS idempotency_key TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS environment TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS write_policy TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS budget_json JSONB",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS result_json JSONB",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS error_code TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS dispatch_error TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS executor_id TEXT",
                    "ALTER TABLE aql_research_sessions ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
                ):
                    cur.execute(statement)
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_aql_research_sessions_surface_status
                    ON aql_research_sessions (surface, status, started_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_aql_research_sessions_queue
                    ON aql_research_sessions (environment, status, started_at ASC)
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_aql_research_sessions_owner_idempotency
                    ON aql_research_sessions (owner_id, idempotency_key)
                    WHERE owner_id IS NOT NULL AND idempotency_key IS NOT NULL
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS aql_research_session_events (
                        event_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES aql_research_sessions(run_id) ON DELETE CASCADE,
                        sequence INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        message TEXT,
                        progress REAL,
                        payload_json JSONB,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (run_id, sequence)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_aql_research_session_events_run_sequence
                    ON aql_research_session_events (run_id, sequence)
                    """
                )
            self.connection.commit()
            _SCHEMA_BOOTSTRAPPED = True

    def start(
        self,
        *,
        run_id: str,
        surface: str,
        task: str,
        query: str,
        provider: str,
        model: str,
        metadata: dict[str, Any],
        started_at: datetime,
    ) -> None:
        with self._lock:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO aql_research_sessions (
                        run_id, surface, task, query, query_sha256, status,
                        provider, model, metadata_json, started_at, updated_at
                    )
                    VALUES (
                        %(run_id)s, %(surface)s, %(task)s, %(query)s, %(query_sha256)s, 'running',
                        %(provider)s, %(model)s, %(metadata_json)s, %(started_at)s, %(started_at)s
                    )
                    ON CONFLICT (run_id) DO UPDATE
                    SET provider = COALESCE(NULLIF(EXCLUDED.provider, ''), aql_research_sessions.provider),
                        model = COALESCE(NULLIF(EXCLUDED.model, ''), aql_research_sessions.model),
                        metadata_json = COALESCE(aql_research_sessions.metadata_json, '{}'::jsonb) || EXCLUDED.metadata_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "run_id": run_id,
                        "surface": surface,
                        "task": task,
                        "query": query,
                        "query_sha256": hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest(),
                        "provider": provider,
                        "model": model,
                        "metadata_json": json.dumps(metadata, ensure_ascii=True, default=str),
                        "started_at": started_at,
                    },
                )
            self.connection.commit()

    def last_sequence(self, run_id: str) -> int:
        with self._lock:
            with self.connection.cursor() as cur:
                cur.execute(
                    "SELECT last_event_sequence FROM aql_research_sessions WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        if not row:
            return 0
        try:
            return max(int(row[0] or 0), 0)
        except Exception:
            return 0

    def append(self, event: ResearchSessionEvent) -> None:
        with self._lock:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT last_event_sequence
                    FROM aql_research_sessions
                    WHERE run_id = %s
                    FOR UPDATE
                    """,
                    (event.run_id,),
                )
                row = cur.fetchone()
                last_sequence = int(row[0] or 0) if row else 0
                sequence = max(int(event.sequence), last_sequence + 1)
                cur.execute(
                    """
                    INSERT INTO aql_research_session_events (
                        event_id, run_id, sequence, event_type, stage,
                        message, progress, payload_json, created_at
                    )
                    VALUES (
                        %(event_id)s, %(run_id)s, %(sequence)s, %(event_type)s, %(stage)s,
                        %(message)s, %(progress)s, %(payload_json)s, %(created_at)s
                    )
                    ON CONFLICT (run_id, sequence) DO NOTHING
                    """,
                    {
                        "event_id": event.event_id,
                        "run_id": event.run_id,
                        "sequence": sequence,
                        "event_type": event.event_type,
                        "stage": event.stage,
                        "message": event.message,
                        "progress": event.progress,
                        "payload_json": json.dumps(event.payload, ensure_ascii=True, default=str),
                        "created_at": event.created_at,
                    },
                )
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET last_event_sequence = GREATEST(last_event_sequence, %(sequence)s),
                        updated_at = %(created_at)s
                    WHERE run_id = %(run_id)s
                    """,
                    {
                        "run_id": event.run_id,
                        "sequence": sequence,
                        "created_at": event.created_at,
                    },
                )
            self.connection.commit()

    def finish(
        self,
        *,
        run_id: str,
        status: str,
        provider: str,
        model: str,
        result_summary: dict[str, Any],
        completed_at: datetime,
    ) -> None:
        with self._lock:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = %(status)s,
                        provider = COALESCE(NULLIF(%(provider)s, ''), provider),
                        model = COALESCE(NULLIF(%(model)s, ''), model),
                        result_summary_json = %(result_summary_json)s,
                        completed_at = %(completed_at)s,
                        updated_at = %(completed_at)s
                    WHERE run_id = %(run_id)s
                    """,
                    {
                        "run_id": run_id,
                        "status": status,
                        "provider": provider,
                        "model": model,
                        "result_summary_json": json.dumps(result_summary, ensure_ascii=True, default=str),
                        "completed_at": completed_at,
                    },
                )
            self.connection.commit()

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass


class ResearchSession:
    def __init__(
        self,
        *,
        run_id: str,
        surface: str,
        task: str,
        query: str,
        provider: str = "",
        model: str = "",
        metadata: dict[str, Any] | None = None,
        store: ResearchSessionStore | None = None,
    ) -> None:
        self.run_id = _clean(run_id)
        self.surface = _clean(surface) or "zopedia.chat"
        self.task = _clean(task) or "agent_answer"
        self.query = _clean(query)
        self.provider = _clean(provider)
        self.model = _clean(model)
        self.store = store or NullResearchSessionStore()
        self.events: list[ResearchSessionEvent] = []
        self.durable = not isinstance(self.store, NullResearchSessionStore)
        self._sequence = 0
        self._finished = False
        self._lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc)
        safe_metadata = _safe_value(metadata or {})
        if not isinstance(safe_metadata, dict):
            safe_metadata = {}
        try:
            self.store.start(
                run_id=self.run_id,
                surface=self.surface,
                task=self.task,
                query=self.query,
                provider=self.provider,
                model=self.model,
                metadata=safe_metadata,
                started_at=self.started_at,
            )
            last_sequence = getattr(self.store, "last_sequence", None)
            if callable(last_sequence):
                self._sequence = max(int(last_sequence(self.run_id) or 0), 0)
        except Exception:
            self.durable = False
        self.record(
            event_type="lifecycle",
            stage="session_started",
            message="Research session started.",
            progress=0.0,
            payload={"surface": self.surface, "task": self.task},
        )

    def record(
        self,
        *,
        event_type: str,
        stage: str,
        message: str,
        progress: float,
        payload: dict[str, Any] | None = None,
    ) -> ResearchSessionEvent:
        with self._lock:
            self._sequence += 1
            safe_payload = _safe_value(payload or {})
            if not isinstance(safe_payload, dict):
                safe_payload = {}
            event = ResearchSessionEvent(
                event_id=f"arse_{uuid.uuid4().hex}",
                run_id=self.run_id,
                sequence=self._sequence,
                event_type=_clean(event_type) or "progress",
                stage=_clean(stage) or "unknown",
                message=_clean(message),
                progress=max(0.0, min(float(progress or 0.0), 1.0)),
                payload=safe_payload,
                created_at=datetime.now(timezone.utc),
            )
            self.events.append(event)
            if self.durable:
                try:
                    self.store.append(event)
                except Exception:
                    self.durable = False
            return event

    def progress_callback(self, callback: ProgressCallback | None) -> ProgressCallback:
        def _record_progress(raw_event: dict[str, Any]) -> None:
            sanitized = _safe_value(dict(raw_event or {}))
            event = dict(sanitized) if isinstance(sanitized, dict) else {}
            stage_name = _clean(event.get("stage"))
            if stage_name == "planner_reasoning":
                event["message"] = "Planner selected the next research step."
            elif stage_name == "model_reasoning_trace":
                event["message"] = "Model planning step completed."
            if callback is not None:
                try:
                    callback(dict(event))
                except ResearchCancellationRequested:
                    raise
                except Exception:
                    pass
            stage = _clean(event.pop("stage", "")) or "progress"
            message = _clean(event.pop("message", ""))
            try:
                progress = float(event.pop("progress", 0.0) or 0.0)
            except Exception:
                progress = 0.0
            self.record(
                event_type="progress",
                stage=stage,
                message=message,
                progress=progress,
                payload=event,
            )

        return _record_progress

    def finish(self, *, result: dict[str, Any] | None = None, error: BaseException | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        result = dict(result or {})
        status = _clean(result.get("status")) or ("failed" if error is not None else "completed")
        model = _clean(result.get("model")) or self.model
        provider = _clean(result.get("provider")) or self.provider
        summary = {
            "status": status,
            "confidence": _clean(result.get("confidence")),
            "tool_call_count": len(list(result.get("tool_calls") or [])),
            "answer_chars": len(_clean(result.get("answer_markdown"))),
            "error": _clean(result.get("error")) or (_clean(error) if error is not None else ""),
        }
        self.record(
            event_type="lifecycle",
            stage="session_finished",
            message="Research session finished.",
            progress=1.0,
            payload=summary,
        )
        if self.durable:
            try:
                self.store.finish(
                    run_id=self.run_id,
                    status=status,
                    provider=provider,
                    model=model,
                    result_summary=summary,
                    completed_at=datetime.now(timezone.utc),
                )
            except Exception:
                self.durable = False

    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "event_count": len(self.events),
            "durable_event_log": self.durable,
            "resumable": False,
        }

    def close(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass


def create_research_session(
    *,
    run_id: str,
    surface: str,
    task: str,
    query: str,
    provider: str = "",
    model: str = "",
    metadata: dict[str, Any] | None = None,
    store: ResearchSessionStore | None = None,
) -> ResearchSession:
    resolved_store = store
    if resolved_store is None:
        connection = pipeline_store.open_database_connection()
        if connection is not None:
            try:
                resolved_store = PostgresResearchSessionStore(connection)
            except Exception:
                try:
                    connection.close()
                except Exception:
                    pass
    return ResearchSession(
        run_id=run_id,
        surface=surface,
        task=task,
        query=query,
        provider=provider,
        model=model,
        metadata=metadata,
        store=resolved_store or NullResearchSessionStore(),
    )


__all__ = [
    "NullResearchSessionStore",
    "PostgresResearchSessionStore",
    "ResearchSession",
    "ResearchCancellationRequested",
    "ResearchSessionEvent",
    "ResearchSessionStore",
    "create_research_session",
]
