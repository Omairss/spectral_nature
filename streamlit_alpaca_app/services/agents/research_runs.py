from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any
import uuid

from services import pipeline_store
from services.agents.research_session import (
    PostgresResearchSessionStore,
    ResearchCancellationRequested,
)
from services.aql_zopedia_engine import run_aql_zopedia_agent


RUN_ID_PATTERN = re.compile(r"agrun_[a-zA-Z0-9_-]{8,64}")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}")
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}
DEFAULT_BUDGET = {
    "max_tool_calls": 8,
    "tool_call_timeout_seconds": 45,
    "llm_step_timeout_seconds": 90,
    "prefetch_timeout_seconds": 15,
}


class ResearchRunError(RuntimeError):
    pass


class ResearchRunNotFound(ResearchRunError):
    pass


class ResearchRunConflict(ResearchRunError):
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: object, *, limit: int = 4000) -> str:
    text = str(value if value is not None else "").strip()
    if text.lower() == "nan":
        return ""
    return text[:limit]


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []


def _environment() -> str:
    configured = _clean(os.getenv("APP_TRACK") or os.getenv("PIPELINE_DATASET_NAMESPACE"), limit=64).lower()
    if configured in {"prod", "production"}:
        return "production"
    return configured or "development"


def _validate_query(query: object) -> str:
    normalized = " ".join(str(query or "").split()).strip()
    if len(normalized) < 3:
        raise ValueError("Research query must contain at least 3 characters.")
    if len(normalized) > 4000:
        raise ValueError("Research query must not exceed 4000 characters.")
    return normalized


def _validate_idempotency_key(value: object) -> str:
    key = _clean(value, limit=128)
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise ValueError("Idempotency-Key must be 8-128 URL-safe characters.")
    return key


def _validate_run_id(value: object) -> str:
    run_id = _clean(value, limit=80)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid research run id.")
    return run_id


def _safe_url(value: object) -> str:
    url = _clean(value, limit=2000)
    if not (url.startswith("https://") or url.startswith("http://")):
        return ""
    return url


def _safe_result(raw: dict[str, Any]) -> dict[str, Any]:
    evidence_pack = _json_object(raw.get("aql_evidence_pack"))
    citations: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in _json_list(evidence_pack.get("citations"))[:40]:
        if not isinstance(item, dict):
            continue
        url = _safe_url(item.get("url"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        citations.append(
            {
                "url": url,
                "label": _clean(item.get("label"), limit=240) or url,
            }
        )
        if len(citations) >= 24:
            break

    limitations = [
        _clean(item, limit=600)
        for item in list(raw.get("limitations") or [])[:16]
        if _clean(item, limit=600)
    ]
    status = _clean(raw.get("status"), limit=32).lower()
    answer = _clean(raw.get("answer_markdown"), limit=50_000)
    return {
        "status": "completed" if status == "completed" and answer else "failed",
        "answer_markdown": answer,
        "confidence": _clean(raw.get("confidence"), limit=32).lower() or "low",
        "limitations": limitations,
        "citations": citations,
        "provider": _clean(raw.get("provider"), limit=80),
        "model": _clean(raw.get("model"), limit=160),
        "evidence_pack_id": _clean(raw.get("aql_evidence_pack_id"), limit=160),
        "error": "" if status == "completed" and answer else "Research did not produce a usable answer.",
    }


class ResearchRunRepository:
    _RUN_COLUMNS = (
        "run_id",
        "surface",
        "task",
        "query",
        "metadata_json",
        "status",
        "provider",
        "model",
        "result_summary_json",
        "result_json",
        "last_event_sequence",
        "started_at",
        "updated_at",
        "completed_at",
        "cancel_requested_at",
        "claimed_at",
        "dispatched_at",
        "attempt_count",
        "dispatch_error",
        "error_code",
        "write_policy",
        "budget_json",
    )

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.event_store = PostgresResearchSessionStore(connection)

    @classmethod
    def from_environment(cls) -> "ResearchRunRepository":
        connection = pipeline_store.open_database_connection()
        if connection is None:
            raise ResearchRunError("Research persistence is unavailable.")
        return cls(connection)

    def close(self) -> None:
        self.event_store.close()

    def _row_to_run(self, row: object) -> dict[str, Any] | None:
        if not isinstance(row, (list, tuple)) or len(row) != len(self._RUN_COLUMNS):
            return None
        record = dict(zip(self._RUN_COLUMNS, row))
        return {
            "run_id": _clean(record.get("run_id"), limit=80),
            "surface": _clean(record.get("surface"), limit=120),
            "task": _clean(record.get("task"), limit=80),
            "query": _clean(record.get("query"), limit=4000),
            "metadata": _json_object(record.get("metadata_json")),
            "status": _clean(record.get("status"), limit=32),
            "provider": _clean(record.get("provider"), limit=80),
            "model": _clean(record.get("model"), limit=160),
            "result_summary": _json_object(record.get("result_summary_json")),
            "result": _json_object(record.get("result_json")) or None,
            "last_event_sequence": int(record.get("last_event_sequence") or 0),
            "created_at": record.get("started_at"),
            "updated_at": record.get("updated_at"),
            "completed_at": record.get("completed_at"),
            "cancel_requested_at": record.get("cancel_requested_at"),
            "claimed_at": record.get("claimed_at"),
            "dispatched_at": record.get("dispatched_at"),
            "attempt_count": int(record.get("attempt_count") or 0),
            "dispatch_status": "failed" if _clean(record.get("dispatch_error")) else (
                "dispatched" if record.get("dispatched_at") is not None else "pending"
            ),
            "error_code": _clean(record.get("error_code"), limit=80),
            "write_policy": _clean(record.get("write_policy"), limit=32) or "none",
            "budget": _json_object(record.get("budget_json")),
        }

    def _select_run(self, *, run_id: str, owner_id: str | None = None, for_update: bool = False) -> dict[str, Any] | None:
        where = "run_id = %s"
        params: list[Any] = [run_id]
        if owner_id is not None:
            where += " AND owner_id = %s"
            params.append(owner_id)
        suffix = " FOR UPDATE" if for_update else ""
        columns = ", ".join(self._RUN_COLUMNS)
        with self.connection.cursor() as cur:
            cur.execute(f"SELECT {columns} FROM aql_research_sessions WHERE {where}{suffix}", tuple(params))
            run = self._row_to_run(cur.fetchone())
        if not for_update:
            self.connection.commit()
        return run

    def _append_event(
        self,
        cur: Any,
        *,
        run_id: str,
        event_type: str,
        stage: str,
        message: str,
        progress: float,
        payload: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> int:
        timestamp = created_at or _now_utc()
        cur.execute(
            "SELECT last_event_sequence FROM aql_research_sessions WHERE run_id = %s FOR UPDATE",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ResearchRunNotFound("Research run not found.")
        sequence = int(row[0] or 0) + 1
        cur.execute(
            """
            INSERT INTO aql_research_session_events (
                event_id, run_id, sequence, event_type, stage,
                message, progress, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"arse_{uuid.uuid4().hex}",
                run_id,
                sequence,
                _clean(event_type, limit=80) or "lifecycle",
                _clean(stage, limit=120) or "unknown",
                _clean(message, limit=1000),
                max(0.0, min(float(progress or 0.0), 1.0)),
                json.dumps(payload or {}, ensure_ascii=True, default=str),
                timestamp,
            ),
        )
        cur.execute(
            """
            UPDATE aql_research_sessions
            SET last_event_sequence = %s, updated_at = %s
            WHERE run_id = %s
            """,
            (sequence, timestamp, run_id),
        )
        return sequence

    def create(
        self,
        *,
        owner_id: str,
        owner_email: str,
        query: str,
        idempotency_key: str,
        parent_run_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        resolved_owner = _clean(owner_id, limit=160)
        if not resolved_owner:
            raise ValueError("Authenticated user id is required.")
        normalized_query = _validate_query(query)
        normalized_key = _validate_idempotency_key(idempotency_key)
        normalized_parent_id = _validate_run_id(parent_run_id) if _clean(parent_run_id, limit=80) else ""
        now = _now_utc()
        run_id = f"agrun_{uuid.uuid4().hex[:24]}"
        budget = dict(DEFAULT_BUDGET)
        try:
            with self.connection.cursor() as cur:
                if normalized_parent_id:
                    cur.execute(
                        """
                        SELECT run_id
                        FROM aql_research_sessions
                        WHERE run_id = %s AND owner_id = %s
                        """,
                        (normalized_parent_id, resolved_owner),
                    )
                    if not cur.fetchone():
                        raise ResearchRunNotFound("Parent research run not found.")
                metadata = {"parent_run_id": normalized_parent_id} if normalized_parent_id else {}
                cur.execute(
                    """
                    INSERT INTO aql_research_sessions (
                        run_id, surface, task, query, query_sha256, status,
                        metadata_json, last_event_sequence, started_at, updated_at,
                        owner_type, owner_id, owner_email, idempotency_key,
                        environment, write_policy, budget_json, attempt_count
                    ) VALUES (
                        %s, 'zopedia.chat', 'agent_answer', %s, %s, 'queued',
                        %s::jsonb, 0, %s, %s,
                        'user', %s, %s, %s,
                        %s, 'none', %s, 0
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING run_id
                    """,
                    (
                        run_id,
                        normalized_query,
                        hashlib.sha256(normalized_query.encode("utf-8", errors="ignore")).hexdigest(),
                        json.dumps(metadata, ensure_ascii=True),
                        now,
                        now,
                        resolved_owner,
                        _clean(owner_email, limit=320).lower(),
                        normalized_key,
                        _environment(),
                        json.dumps(budget, ensure_ascii=True),
                    ),
                )
                inserted = cur.fetchone()
                created = bool(inserted)
                if created:
                    self._append_event(
                        cur,
                        run_id=run_id,
                        event_type="lifecycle",
                        stage="queued",
                        message="Research run queued.",
                        progress=0.0,
                        payload={"task": "agent_answer"},
                        created_at=now,
                    )
                else:
                    cur.execute(
                        """
                        SELECT run_id, query, task, metadata_json
                        FROM aql_research_sessions
                        WHERE owner_id = %s AND idempotency_key = %s
                        """,
                        (resolved_owner, normalized_key),
                    )
                    existing = cur.fetchone()
                    if not existing:
                        raise ResearchRunConflict("Idempotent research run could not be resolved.")
                    run_id = _clean(existing[0], limit=80)
                    existing_parent_id = _clean(_json_object(existing[3]).get("parent_run_id"), limit=80)
                    if (
                        _clean(existing[1], limit=4000) != normalized_query
                        or _clean(existing[2], limit=80) != "agent_answer"
                        or existing_parent_id != normalized_parent_id
                    ):
                        raise ResearchRunConflict("Idempotency-Key was already used for a different request.")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        run = self._select_run(run_id=run_id, owner_id=resolved_owner)
        if run is None:
            raise ResearchRunError("Research run was created but could not be read.")
        return run, created

    def list_for_user(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        resolved_owner = _clean(owner_id, limit=160)
        if not resolved_owner:
            raise ValueError("Authenticated user id is required.")
        safe_limit = max(1, min(int(limit or 50), 100))
        columns = ", ".join(self._RUN_COLUMNS)
        with self.connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT {columns}
                FROM aql_research_sessions
                WHERE owner_id = %s AND surface = 'zopedia.chat'
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (resolved_owner, safe_limit),
            )
            rows = cur.fetchall()
        self.connection.commit()
        return [run for row in rows if (run := self._row_to_run(row)) is not None]

    def get_for_user(self, *, run_id: str, owner_id: str) -> dict[str, Any]:
        resolved_run_id = _validate_run_id(run_id)
        self.reconcile_stale(run_id=resolved_run_id, owner_id=owner_id)
        run = self._select_run(run_id=resolved_run_id, owner_id=_clean(owner_id, limit=160))
        if run is None:
            raise ResearchRunNotFound("Research run not found.")
        return run

    def list_events_for_user(self, *, run_id: str, owner_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        run = self.get_for_user(run_id=run_id, owner_id=owner_id)
        after = max(int(after_sequence or 0), 0)
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, run_id, sequence, event_type, stage, message,
                       progress, payload_json, created_at
                FROM aql_research_session_events
                WHERE run_id = %s AND sequence > %s
                ORDER BY sequence ASC
                LIMIT 250
                """,
                (run["run_id"], after),
            )
            rows = cur.fetchall()
        self.connection.commit()
        return [
            {
                "event_id": _clean(row[0], limit=80),
                "run_id": _clean(row[1], limit=80),
                "sequence": int(row[2] or 0),
                "event_type": _clean(row[3], limit=80),
                "stage": _clean(row[4], limit=120),
                "message": _clean(row[5], limit=1000),
                "progress": max(0.0, min(float(row[6] or 0.0), 1.0)),
                "payload": _json_object(row[7]),
                "created_at": row[8],
            }
            for row in rows
        ]

    def request_cancel(self, *, run_id: str, owner_id: str) -> dict[str, Any]:
        resolved_run_id = _validate_run_id(run_id)
        now = _now_utc()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT status FROM aql_research_sessions
                    WHERE run_id = %s AND owner_id = %s
                    FOR UPDATE
                    """,
                    (resolved_run_id, _clean(owner_id, limit=160)),
                )
                row = cur.fetchone()
                if not row:
                    raise ResearchRunNotFound("Research run not found.")
                current = _clean(row[0], limit=32)
                if current in TERMINAL_STATUSES:
                    self.connection.commit()
                    return self.get_for_user(run_id=resolved_run_id, owner_id=owner_id)
                next_status = "cancelled" if current == "queued" else "cancel_requested"
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = %s,
                        cancel_requested_at = %s,
                        completed_at = CASE WHEN %s = 'cancelled' THEN %s ELSE completed_at END,
                        updated_at = %s
                    WHERE run_id = %s
                    """,
                    (next_status, now, next_status, now, now, resolved_run_id),
                )
                self._append_event(
                    cur,
                    run_id=resolved_run_id,
                    event_type="lifecycle",
                    stage=next_status,
                    message="Research run cancelled." if next_status == "cancelled" else "Cancellation requested.",
                    progress=1.0 if next_status == "cancelled" else 0.0,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_for_user(run_id=resolved_run_id, owner_id=owner_id)

    def mark_dispatched(self, *, run_id: str) -> None:
        now = _now_utc()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET dispatched_at = %s, dispatch_error = NULL, updated_at = %s
                    WHERE run_id = %s AND status = 'queued'
                    """,
                    (now, now, run_id),
                )
                if cur.rowcount:
                    self._append_event(
                        cur,
                        run_id=run_id,
                        event_type="lifecycle",
                        stage="dispatched",
                        message="Research worker requested.",
                        progress=0.0,
                        created_at=now,
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def mark_dispatch_failed(self, *, run_id: str, error: object) -> None:
        now = _now_utc()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET dispatch_error = %s, updated_at = %s
                    WHERE run_id = %s AND status = 'queued'
                    """,
                    (_clean(error, limit=1000), now, run_id),
                )
                if cur.rowcount:
                    self._append_event(
                        cur,
                        run_id=run_id,
                        event_type="lifecycle",
                        stage="dispatch_delayed",
                        message="Research worker dispatch is temporarily unavailable.",
                        progress=0.0,
                        created_at=now,
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def claim_next(self, *, executor_id: str) -> dict[str, Any] | None:
        now = _now_utc()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id
                    FROM aql_research_sessions
                    WHERE environment = %s AND status = 'queued'
                    ORDER BY started_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (_environment(),),
                )
                row = cur.fetchone()
                if not row:
                    self.connection.commit()
                    return None
                run_id = _clean(row[0], limit=80)
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = 'running', claimed_at = %s, executor_id = %s,
                        attempt_count = attempt_count + 1, updated_at = %s
                    WHERE run_id = %s
                    """,
                    (now, _clean(executor_id, limit=240), now, run_id),
                )
                self._append_event(
                    cur,
                    run_id=run_id,
                    event_type="lifecycle",
                    stage="running",
                    message="Research worker started.",
                    progress=0.01,
                    created_at=now,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self._select_run(run_id=run_id)

    def cancellation_requested(self, *, run_id: str) -> bool:
        with self.connection.cursor() as cur:
            cur.execute("SELECT status FROM aql_research_sessions WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        self.connection.commit()
        return bool(row and _clean(row[0], limit=32) in {"cancel_requested", "cancelled"})

    def finish(self, *, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        safe = _safe_result(result)
        status = safe["status"]
        now = _now_utc()
        summary = {
            "status": status,
            "confidence": safe["confidence"],
            "answer_chars": len(safe["answer_markdown"]),
            "citation_count": len(safe["citations"]),
        }
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = %s, provider = %s, model = %s,
                        result_json = %s, result_summary_json = %s,
                        error_code = %s, completed_at = %s, updated_at = %s
                    WHERE run_id = %s
                    """,
                    (
                        status,
                        safe["provider"],
                        safe["model"],
                        json.dumps(safe, ensure_ascii=True),
                        json.dumps(summary, ensure_ascii=True),
                        "no_usable_answer" if status == "failed" else None,
                        now,
                        now,
                        run_id,
                    ),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        resolved = self._select_run(run_id=run_id)
        if resolved is None:
            raise ResearchRunNotFound("Research run not found after completion.")
        return resolved

    def fail(self, *, run_id: str, error_code: str, cancelled: bool = False) -> None:
        now = _now_utc()
        status = "cancelled" if cancelled else "failed"
        message = "Research run cancelled." if cancelled else "Research run failed safely."
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = %s, error_code = %s, completed_at = %s, updated_at = %s
                    WHERE run_id = %s AND status NOT IN ('completed', 'cancelled')
                    """,
                    (status, _clean(error_code, limit=80), now, now, run_id),
                )
                if cur.rowcount:
                    self._append_event(
                        cur,
                        run_id=run_id,
                        event_type="lifecycle",
                        stage=status,
                        message=message,
                        progress=1.0,
                        payload={"error_code": _clean(error_code, limit=80)} if not cancelled else {},
                        created_at=now,
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def reconcile_stale(self, *, run_id: str, owner_id: str) -> None:
        try:
            timeout_minutes = max(int(os.getenv("RESEARCH_RUN_STALE_MINUTES", "45")), 10)
        except Exception:
            timeout_minutes = 45
        cutoff = _now_utc() - timedelta(minutes=timeout_minutes)
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT status, claimed_at
                FROM aql_research_sessions
                WHERE run_id = %s AND owner_id = %s
                """,
                (run_id, _clean(owner_id, limit=160)),
            )
            row = cur.fetchone()
        self.connection.commit()
        if not row or _clean(row[0], limit=32) not in {"running", "cancel_requested"}:
            return
        claimed_at = row[1]
        if claimed_at is None or claimed_at >= cutoff:
            return
        self.fail(
            run_id=run_id,
            error_code="executor_lost",
            cancelled=_clean(row[0], limit=32) == "cancel_requested",
        )


def dispatch_research_run(repository: ResearchRunRepository, *, run_id: str) -> tuple[bool, str]:
    job_name = _clean(os.getenv("RESEARCH_RUN_JOB_NAME"), limit=160)
    if not job_name:
        repository.mark_dispatch_failed(run_id=run_id, error="RESEARCH_RUN_JOB_NAME is not configured")
        return False, "Research worker is not configured."
    ok, detail = pipeline_store.start_container_app_job(job_name)
    if ok:
        repository.mark_dispatched(run_id=run_id)
        return True, "Research worker requested."
    repository.mark_dispatch_failed(run_id=run_id, error=detail)
    return False, "Research worker dispatch is temporarily unavailable."


def execute_next_queued_run(*, executor_id: str = "") -> dict[str, Any]:
    repository = ResearchRunRepository.from_environment()
    run: dict[str, Any] | None = None
    try:
        run = repository.claim_next(executor_id=executor_id or f"worker_{uuid.uuid4().hex[:12]}")
        if run is None:
            return {"status": "idle", "run_id": ""}
        run_id = run["run_id"]

        def _check_cancel(_: dict[str, Any]) -> None:
            if repository.cancellation_requested(run_id=run_id):
                raise ResearchCancellationRequested("Research cancellation requested.")

        budget = dict(DEFAULT_BUDGET)
        budget.update(_json_object(run.get("budget")))
        execution_query = run["query"]
        parent_run_id = _clean(_json_object(run.get("metadata")).get("parent_run_id"), limit=80)
        if parent_run_id:
            parent = repository._select_run(run_id=parent_run_id)
            if parent is not None:
                parent_answer = _clean(_json_object(parent.get("result")).get("answer_markdown"), limit=16_000)
                if parent_answer:
                    execution_query = (
                        "Continue this research conversation using the prior result as context. "
                        "Re-evaluate evidence when the follow-up requires it; do not assume the prior result is complete.\n\n"
                        f"Prior question:\n{_clean(parent.get('query'), limit=3000)}\n\n"
                        f"Prior result:\n{parent_answer}\n\n"
                        f"Follow-up question:\n{run['query']}"
                    )
        try:
            result = run_aql_zopedia_agent(
                run_id=run_id,
                query=execution_query,
                task="agent_answer",
                surface="zopedia.chat",
                max_tool_calls=int(budget["max_tool_calls"]),
                progress_callback=_check_cancel,
                persist_findings=True,
                write_policy="none",
                tool_call_timeout_seconds=int(budget["tool_call_timeout_seconds"]),
                llm_step_timeout_seconds=int(budget["llm_step_timeout_seconds"]),
                prefetch_timeout_seconds=int(budget["prefetch_timeout_seconds"]),
            )
        except ResearchCancellationRequested:
            repository.fail(run_id=run_id, error_code="cancelled_by_user", cancelled=True)
            return {"status": "cancelled", "run_id": run_id}
        except BaseException as exc:
            repository.fail(run_id=run_id, error_code=f"executor_{type(exc).__name__.lower()}")
            return {"status": "failed", "run_id": run_id}
        completed = repository.finish(run_id=run_id, result=result)
        return {"status": completed["status"], "run_id": run_id}
    finally:
        repository.close()


__all__ = [
    "ACTIVE_STATUSES",
    "ResearchRunConflict",
    "ResearchRunError",
    "ResearchRunNotFound",
    "ResearchRunRepository",
    "TERMINAL_STATUSES",
    "dispatch_research_run",
    "execute_next_queued_run",
]
