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
    research_request_context,
)
from services.aql_zopedia_engine import (
    run_aql_zopedia_agent,
    resolve_research_budget,
    research_engine_revision,
)
from services.agents.chat_log import bootstrap_chat_log

RUN_ID_PATTERN = re.compile(r"agrun_[a-zA-Z0-9_-]{8,64}")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}")
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}



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
    normalized = str(query or "").strip()
    if not normalized:
        raise ValueError("Research query must not be empty.")
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
    review = _json_object(raw.get("quality_review"))
    return {
        "status": "completed" if status == "completed" and answer else "failed",
        "answer_markdown": answer,
        "answer_status": _clean(raw.get("answer_status"), limit=32),
        "quality_review": {
            "status": _clean(review.get("status"), limit=32),
            "verdict": _clean(review.get("verdict"), limit=32),
        },
        "confidence": _clean(raw.get("confidence"), limit=32).lower() or "low",
        "limitations": limitations,
        "citations": citations,
        "visualizations": list(raw.get("visualizations") or []),
        "provider": _clean(raw.get("provider"), limit=80),
        "model": _clean(raw.get("model"), limit=160),
        "evidence_pack_id": _clean(raw.get("aql_evidence_pack_id"), limit=160),
        "engine_revision": _clean(
            _json_object(raw.get("engine")).get("revision"), limit=80
        ),
        "failure": _json_object(raw.get("failure")),
        "error": (
            ""
            if status == "completed" and answer
            else "Research did not produce a usable answer."
        ),
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
        "conversation_id",
        "turn_id",
        "owner_id",
        "environment",
    )

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.event_store = PostgresResearchSessionStore(connection)
        if not bootstrap_chat_log(connection):
            raise ResearchRunError("Conversation persistence is unavailable.")

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
            "conversation_id": _clean(record.get("conversation_id"), limit=80),
            "turn_id": _clean(record.get("turn_id"), limit=80),
            "owner_id": _clean(record.get("owner_id"), limit=160),
            "environment": _clean(record.get("environment"), limit=40),
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
            "dispatch_status": (
                "failed"
                if _clean(record.get("dispatch_error"))
                else (
                    "dispatched"
                    if record.get("dispatched_at") is not None
                    else "pending"
                )
            ),
            "error_code": _clean(record.get("error_code"), limit=80),
            "write_policy": _clean(record.get("write_policy"), limit=32) or "none",
            "budget": _json_object(record.get("budget_json")),
        }

    def _select_run(self, *, run_id: str, owner_id: str | None = None, for_update: bool = False) -> dict[str, Any] | None:
        where = "run_id = %s"
        params: list[Any] = [run_id]
        if owner_id is not None:
            where += (
                " AND owner_id = %s AND environment = %s AND surface = 'zopedia.chat'"
            )
            params.extend([owner_id, _environment()])
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
        conversation_id: str = "",
        request_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        owner = _clean(owner_id, limit=160)
        if not owner:
            raise ValueError("Authenticated user id is required.")
        query = _validate_query(query)
        key = _validate_idempotency_key(idempotency_key)
        parent_id = _validate_run_id(parent_run_id) if parent_run_id else ""
        conversation_id = (
            self._validate_conversation_id(conversation_id) if conversation_id else ""
        )
        self.migrate_conversations(owner_id=owner)
        now = _now_utc()
        budget = resolve_research_budget()
        supplied_context = {
            k: v
            for k, v in (request_context or {}).items()
            if v is not None and v != ""
        }
        try:
            with self.connection.cursor() as cur:
                # Serialize idempotent requests before allocating a message or thread.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (owner + ":" + key,),
                )
                cur.execute(
                    "SELECT run_id FROM aql_research_sessions WHERE owner_id=%s AND idempotency_key=%s AND environment=%s",
                    (owner, key, _environment()),
                )
                existing = cur.fetchone()
                if existing:
                    run = self._select_run(
                        run_id=existing[0], owner_id=owner, for_update=True
                    )
                    old_context = run["metadata"].get("request_context") or {}
                    if (
                        run["query"] != query
                        or (
                            conversation_id
                            and run["conversation_id"] != conversation_id
                        )
                        or run["metadata"].get("parent_run_id", "") != parent_id
                        or any(
                            old_context.get(k) != v for k, v in supplied_context.items()
                        )
                    ):
                        raise ResearchRunConflict(
                            "Idempotency-Key was already used for a different request."
                        )
                    self.connection.commit()
                    return run, False
                if parent_id:
                    parent = self._select_run(
                        run_id=parent_id, owner_id=owner, for_update=True
                    )
                    if not parent or (
                        conversation_id and parent["conversation_id"] != conversation_id
                    ):
                        raise ResearchRunNotFound(
                            "Parent research conversation not found."
                        )
                    conversation_id = parent["conversation_id"]
                if conversation_id:
                    cur.execute(
                        "SELECT thread_id FROM saa_zopedia_chat_threads WHERE thread_id=%s AND user_key=%s AND environment=%s AND status<>'deleted' FOR UPDATE",
                        (conversation_id, owner, _environment()),
                    )
                    if not cur.fetchone():
                        raise ResearchRunNotFound("Research conversation not found.")
                    cur.execute(
                        "SELECT run_id,status,metadata_json FROM aql_research_sessions WHERE conversation_id=%s AND owner_id=%s AND environment=%s ORDER BY started_at DESC LIMIT 1",
                        (conversation_id, owner, _environment()),
                    )
                    previous = cur.fetchone()
                    if previous and previous[1] in ACTIVE_STATUSES:
                        raise ResearchRunConflict(
                            "Research is already running in this conversation."
                        )
                    inherited = (
                        _json_object(previous[2]).get("request_context", {})
                        if previous
                        else {}
                    )
                else:
                    conversation_id = "zthread_" + uuid.uuid4().hex[:24]
                    inherited = {}
                    cur.execute(
                        "INSERT INTO saa_zopedia_chat_threads(thread_id,user_key,environment,title,status,metadata_json,created_at,updated_at) VALUES(%s,%s,%s,%s,'active','{}'::jsonb,%s,%s)",
                        (conversation_id, owner, _environment(), query[:160], now, now),
                    )
                # Source scope and workspace references survive a follow-up that omits them.
                context = {
                    k: inherited[k]
                    for k in ("allow_web", "timezone", "visible_artifacts")
                    if k in inherited
                }
                context.update(supplied_context)
                context = research_request_context(query, context, write_policy="none")
                metadata = {
                    "request_context": context,
                    "engine_revision": research_engine_revision(),
                }
                if parent_id:
                    metadata["parent_run_id"] = parent_id
                run_id = "agrun_" + uuid.uuid4().hex[:24]
                turn_id = "zturn_" + uuid.uuid4().hex[:24]
                cur.execute(
                    """
                    INSERT INTO aql_research_sessions (
                        run_id,surface,task,query,query_sha256,status,metadata_json,
                        last_event_sequence,started_at,updated_at,owner_type,owner_id,owner_email,
                        idempotency_key,environment,write_policy,budget_json,attempt_count,conversation_id,turn_id
                    ) VALUES (%s,'zopedia.chat','agent_answer',%s,%s,'queued',%s::jsonb,0,%s,%s,'user',%s,%s,%s,%s,'none',%s::jsonb,0,%s,%s)
                """,
                    (
                        run_id,
                        query,
                        hashlib.sha256(query.encode()).hexdigest(),
                        json.dumps(metadata),
                        now,
                        now,
                        owner,
                        _clean(owner_email, limit=320).lower(),
                        key,
                        _environment(),
                        json.dumps(budget),
                        conversation_id,
                        turn_id,
                    ),
                )
                self._append_turn(
                    cur,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    run_id=run_id,
                    query=query,
                    created_at=now,
                )
                self._append_event(
                    cur,
                    run_id=run_id,
                    event_type="lifecycle",
                    stage="queued",
                    message="Queued",
                    progress=0.0,
                    created_at=now,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self._select_run(run_id=run_id, owner_id=owner), True

    @staticmethod
    def _validate_conversation_id(value: str) -> str:
        if not re.fullmatch(r"zthread_[a-zA-Z0-9_-]{8,64}", str(value)):
            raise ValueError("Invalid research conversation id.")
        return value

    def _append_turn(
        self,
        cur: Any,
        *,
        conversation_id: str,
        turn_id: str,
        run_id: str,
        query: str,
        created_at: Any,
        answer: str = "",
    ) -> None:
        cur.execute(
            "SELECT COALESCE(MAX(sequence_index),-1)+1 FROM saa_zopedia_chat_messages WHERE thread_id=%s",
            (conversation_id,),
        )
        sequence = cur.fetchone()[0]
        for offset, (role, content) in enumerate(
            (("user", query), ("assistant", answer))
        ):
            cur.execute(
                """
                INSERT INTO saa_zopedia_chat_messages(message_id,thread_id,role,content,payload_json,run_id,sequence_index,created_at)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT(message_id) DO NOTHING
            """,
                (
                    turn_id + "_" + role,
                    conversation_id,
                    role,
                    content,
                    json.dumps({"turn_id": turn_id}),
                    run_id,
                    sequence + offset,
                    created_at,
                ),
            )
        cur.execute(
            "UPDATE saa_zopedia_chat_threads SET updated_at=GREATEST(updated_at,%s) WHERE thread_id=%s",
            (created_at, conversation_id),
        )

    def migrate_conversations(self, *, owner_id: str) -> int:
        """Idempotently attach legacy run chains, without crossing owner or environment."""
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM aql_research_sessions WHERE owner_id=%s AND environment=%s AND surface='zopedia.chat' AND conversation_id IS NULL)",
                (owner_id, _environment()),
            )
            if not cur.fetchone()[0]:
                self.connection.commit()
                return 0
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("research-conversations:" + owner_id + ":" + _environment(),),
            )
            cur.execute(
                "SELECT run_id,query,metadata_json,started_at,updated_at,result_json,conversation_id FROM aql_research_sessions WHERE owner_id=%s AND environment=%s AND surface='zopedia.chat' ORDER BY started_at,run_id",
                (owner_id, _environment()),
            )
            rows = cur.fetchall()
            if not any(not row[6] for row in rows):
                self.connection.commit()
                return 0
            by_id = {row[0]: row for row in rows}
            migrated = 0
            for row in rows:
                if row[6]:
                    continue
                root = row
                visited = set()
                while _json_object(root[2]).get("parent_run_id") in by_id:
                    if root[0] in visited:
                        raise ResearchRunError("Research lineage contains a cycle.")
                    visited.add(root[0])
                    root = by_id[_json_object(root[2])["parent_run_id"]]
                conversation_id = root[6] or "zthread_" + root[0].removeprefix("agrun_")
                turn_id = "zturn_" + row[0].removeprefix("agrun_")
                cur.execute(
                    "INSERT INTO saa_zopedia_chat_threads(thread_id,user_key,environment,title,status,metadata_json,created_at,updated_at) VALUES (%s,%s,%s,%s,'active','{}'::jsonb,%s,%s) ON CONFLICT(thread_id) DO NOTHING",
                    (
                        conversation_id,
                        owner_id,
                        _environment(),
                        root[1][:160],
                        root[3],
                        row[4],
                    ),
                )
                cur.execute(
                    "SELECT thread_id FROM saa_zopedia_chat_threads WHERE thread_id=%s AND user_key=%s AND environment=%s FOR UPDATE",
                    (conversation_id, owner_id, _environment()),
                )
                if not cur.fetchone():
                    raise ResearchRunError(
                        "Research conversation ownership is inconsistent."
                    )
                cur.execute(
                    "UPDATE aql_research_sessions SET conversation_id=%s,turn_id=%s WHERE run_id=%s",
                    (conversation_id, turn_id, row[0]),
                )
                self._append_turn(
                    cur,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    run_id=row[0],
                    query=row[1],
                    created_at=row[3],
                    answer=_json_object(row[5]).get("answer_markdown", ""),
                )
                cur.execute(
                    "UPDATE saa_zopedia_chat_threads SET updated_at=GREATEST(updated_at,%s) WHERE thread_id=%s",
                    (row[4], conversation_id),
                )
                migrated += 1
        self.connection.commit()
        return migrated

    def list_conversations(
        self, *, owner_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        self.migrate_conversations(owner_id=owner_id)
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT t.thread_id,t.title,t.created_at,t.updated_at,latest.run_id,latest.status,
                    (SELECT count(DISTINCT r.turn_id) FROM aql_research_sessions r WHERE r.conversation_id=t.thread_id) AS turns
                FROM saa_zopedia_chat_threads t
                JOIN LATERAL (SELECT run_id,status FROM aql_research_sessions r WHERE r.conversation_id=t.thread_id ORDER BY started_at DESC,run_id DESC LIMIT 1) latest ON true
                WHERE t.user_key=%s AND t.environment=%s AND t.status<>'deleted'
                ORDER BY t.updated_at DESC LIMIT %s
            """,
                (owner_id, _environment(), max(1, min(int(limit), 100))),
            )
            rows = cur.fetchall()
        self.connection.commit()
        return [
            dict(
                zip(
                    (
                        "conversation_id",
                        "title",
                        "created_at",
                        "updated_at",
                        "latest_run_id",
                        "status",
                        "turn_count",
                    ),
                    row,
                )
            )
            for row in rows
        ]

    def get_conversation(
        self, *, conversation_id: str, owner_id: str
    ) -> dict[str, Any]:
        self._validate_conversation_id(conversation_id)
        self.migrate_conversations(owner_id=owner_id)
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT title,created_at,updated_at FROM saa_zopedia_chat_threads WHERE thread_id=%s AND user_key=%s AND environment=%s AND status<>'deleted'",
                (conversation_id, owner_id, _environment()),
            )
            thread = cur.fetchone()
            if not thread:
                raise ResearchRunNotFound("Research conversation not found.")
            cur.execute(
                f"SELECT {', '.join(self._RUN_COLUMNS)} FROM aql_research_sessions WHERE conversation_id=%s AND owner_id=%s AND environment=%s ORDER BY started_at,run_id",
                (conversation_id, owner_id, _environment()),
            )
            runs = [self._row_to_run(row) for row in cur.fetchall()]
        self.connection.commit()
        turns = {}
        for run in runs:
            if run["status"] in ACTIVE_STATUSES:
                self.reconcile_stale(run_id=run["run_id"], owner_id=owner_id)
                run = self._select_run(run_id=run["run_id"], owner_id=owner_id) or run
            turn = turns.setdefault(
                run["turn_id"],
                {
                    "turn_id": run["turn_id"],
                    "query": run["query"],
                    "created_at": run["created_at"],
                    "attempts": [],
                },
            )
            turn["attempts"].append(run)
        return {
            "conversation_id": conversation_id,
            "title": thread[0],
            "created_at": thread[1],
            "updated_at": thread[2],
            "turns": list(turns.values()),
        }

    def conversation_context(
        self, run: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        conversation = self.get_conversation(
            conversation_id=run["conversation_id"], owner_id=run["owner_id"]
        )
        prior = []
        for turn in conversation["turns"]:
            if turn["turn_id"] == run["turn_id"]:
                break
            prior.append(turn)
        run_ids = [attempt["run_id"] for turn in prior for attempt in turn["attempts"]]
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT run_id,checkpoint_json FROM aql_research_sessions WHERE run_id=ANY(%s) AND owner_id=%s AND environment=%s",
                (run_ids, run["owner_id"], _environment()),
            )
            checkpoints = {row[0]: _json_object(row[1]) for row in cur.fetchall()}
            cur.execute(
                """SELECT e.run_id,e.stage,e.message,e.created_at
                FROM aql_research_session_events e JOIN aql_research_sessions r ON r.run_id=e.run_id
                WHERE r.run_id=ANY(%s) AND r.owner_id=%s AND r.environment=%s AND e.stage NOT LIKE '%%heartbeat%%'
                ORDER BY r.started_at,e.sequence""",
                (run_ids, run["owner_id"], _environment()),
            )
            events = {}
            for record_id, stage, message, created_at in cur.fetchall():
                events.setdefault(record_id, []).append(
                    {"stage": stage, "message": message, "created_at": str(created_at)}
                )
        self.connection.commit()
        history, outcomes = [], []
        inherited = {}
        for turn in prior:
            history.append(
                {"role": "user", "content": turn["query"], "turn_id": turn["turn_id"]}
            )
            for previous in turn["attempts"]:
                answer = _json_object(previous.get("result")).get("answer_markdown", "")
                if answer:
                    history.append(
                        {
                            "role": "assistant",
                            "content": answer,
                            "turn_id": turn["turn_id"],
                        }
                    )
                checkpoint = checkpoints.get(previous["run_id"])
                if checkpoint:
                    inherited = {
                        **checkpoint,
                        "tool_calls": [
                            {
                                **call,
                                "origin_run_id": call.get("origin_run_id")
                                or previous["run_id"],
                                "origin_turn_id": call.get("origin_turn_id")
                                or previous["turn_id"],
                            }
                            for call in checkpoint.get("tool_calls") or []
                        ],
                    }
                outcomes.append(
                    {
                        "run_id": previous["run_id"],
                        "turn_id": previous["turn_id"],
                        "query": turn["query"],
                        "status": previous["status"],
                        "error_code": previous["error_code"],
                        "error": _json_object(previous.get("result")).get("error", ""),
                        "submitted_at": str(previous["created_at"]),
                        "started_at": (
                            str(previous["claimed_at"])
                            if previous["claimed_at"]
                            else None
                        ),
                        "completed_at": (
                            str(previous["completed_at"])
                            if previous["completed_at"]
                            else None
                        ),
                        "failure_stage": _json_object(
                            _json_object(previous.get("result")).get("failure")
                        ).get("stage")
                        or previous["metadata"].get("failure_stage"),
                        "events": events.get(previous["run_id"], []),
                    }
                )
        resume = self.event_store.load_checkpoint(run["run_id"])
        if not resume and inherited:
            resume = {
                **inherited,
                "continued_request": True,
                "draft_answer": "",
                "pending_action": None,
            }
        return history, resume, outcomes

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
                WHERE owner_id = %s AND surface = 'zopedia.chat' AND environment = %s
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (resolved_owner, _environment(), safe_limit),
            )
            rows = cur.fetchall()
        self.connection.commit()
        return [run for row in rows if (run := self._row_to_run(row)) is not None]

    def get_for_user(self, *, run_id: str, owner_id: str) -> dict[str, Any]:
        self.migrate_conversations(owner_id=owner_id)
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
                    WHERE run_id = %s AND owner_id = %s AND environment = %s
                    FOR UPDATE
                    """,
                    (resolved_run_id, _clean(owner_id, limit=160), _environment()),
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
                cur.execute(
                    "UPDATE saa_zopedia_chat_messages SET content=%s WHERE run_id=%s AND role='assistant'",
                    (safe["answer_markdown"], run_id),
                )
                cur.execute(
                    "UPDATE saa_zopedia_chat_threads SET updated_at=%s WHERE thread_id=(SELECT conversation_id FROM aql_research_sessions WHERE run_id=%s)",
                    (now, run_id),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        resolved = self._select_run(run_id=run_id)
        if resolved is None:
            raise ResearchRunNotFound("Research run not found after completion.")
        return resolved

    def fail(
        self,
        *,
        run_id: str,
        error_code: str,
        cancelled: bool = False,
        failure_stage: str = "",
    ) -> None:
        now = _now_utc()
        status = "cancelled" if cancelled else "failed"
        message = "Research run cancelled." if cancelled else "Research run failed safely."
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE aql_research_sessions
                    SET status = %s, error_code = %s, completed_at = %s, updated_at = %s,
                        metadata_json=COALESCE(metadata_json,'{}'::jsonb) || %s::jsonb
                    WHERE run_id = %s AND status NOT IN ('completed', 'cancelled')
                    """,
                    (
                        status,
                        _clean(error_code, limit=80),
                        now,
                        now,
                        json.dumps({"failure_stage": failure_stage or None}),
                        run_id,
                    ),
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
                WHERE run_id = %s AND owner_id = %s AND environment = %s
                """,
                (run_id, _clean(owner_id, limit=160), _environment()),
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

        budget = resolve_research_budget(**_json_object(run.get("budget")))
        metadata = _json_object(run.get("metadata"))
        history, resume, outcomes = repository.conversation_context(run)
        context = {
            **_json_object(metadata.get("request_context")),
            "conversation_id": run["conversation_id"],
            "turn_id": run["turn_id"],
            "conversation_runs": outcomes,
        }
        try:
            result = run_aql_zopedia_agent(
                run_id=run_id,
                query=run["query"],
                conversation_history=history,
                request_context=context,
                resume_state=resume,
                task="agent_answer",
                surface="zopedia.chat",
                max_tool_calls=int(budget["max_tool_calls"]),
                run_timeout_seconds=float(budget["run_timeout_seconds"]),
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
