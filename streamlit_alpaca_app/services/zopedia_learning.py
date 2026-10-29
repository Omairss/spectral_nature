from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from data_access.query_service import QueryService
from services.agents.chat_log import load_chat_session, load_chat_thread
from services.saa.zopedia import apply_zopedia_typed_mutation
from services.saa.storage import _db_connection
from services.aql_zopedia_engine import load_aql_zopedia_llm_client


LEARNING_EVENT_TABLE = "saa_zopedia_learning_events"
LEARNING_EVENT_EVIDENCE_TABLE = "saa_zopedia_learning_event_evidence"

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL_CASE_DIR = (
    APP_ROOT
    / "documents"
    / "architecture"
    / "new_features"
    / "zopedia"
    / "eval_cases"
    / "generated"
)


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(value, minimum)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = _clean(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = _clean(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [text]
    return parsed if isinstance(parsed, list) else [parsed]


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(_clean(part) for part in parts).encode("utf-8")).hexdigest()[:24]
    return f"zlearn_{digest}"


def _slug(value: object, *, default: str = "learning") -> str:
    text = re.sub(r"[^a-z0-9]+", "_", _clean(value).lower()).strip("_")
    return (text or default)[:96]


def _assistant_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = dict(message.get("payload") or {}) if isinstance(message.get("payload"), dict) else {}
    if payload:
        return payload
    return dict(message)


def _first_user_question(thread: dict[str, Any]) -> str:
    for message in list(thread.get("messages") or []):
        if isinstance(message, dict) and _clean(message.get("role")).lower() == "user":
            content = _clean(message.get("content"))
            if content:
                return content
    return _clean(thread.get("title"))


def bootstrap_zopedia_learning_storage(conn: Any, *, commit: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_learning_events (
                event_id TEXT PRIMARY KEY,
                user_key TEXT NOT NULL,
                thread_id TEXT,
                status TEXT NOT NULL,
                severity TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                original_question TEXT,
                failed_claim TEXT,
                correction_summary TEXT,
                successful_path_json JSONB,
                evidence_plan_json JSONB,
                proposed_change_type TEXT,
                proposal_id TEXT,
                mutation_id TEXT,
                eval_case_path TEXT,
                eval_status TEXT,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_learning_events_status
            ON saa_zopedia_learning_events (status, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_learning_events_user
            ON saa_zopedia_learning_events (user_key, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_learning_event_evidence (
                evidence_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                ref TEXT NOT NULL,
                payload_json JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_learning_evidence_event
            ON saa_zopedia_learning_event_evidence (event_id)
            """
        )
    if commit:
        conn.commit()


def detect_learning_events_from_payload(
    thread: dict[str, Any],
    *,
    user_key: str = "",
    run_payloads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Offer a versioned conversation episode to the existing semantic critic."""
    messages = [message for message in thread.get("messages") or [] if isinstance(message, dict)]
    if not any(_clean(message.get("role")).lower() == "assistant" for message in messages):
        return []
    question = _first_user_question(thread)
    if not question:
        return []
    safe_user_key = _clean(user_key or thread.get("user_key")) or "default"
    thread_id = _clean(thread.get("thread_id"))
    episode = {"messages": messages, "runs": run_payloads or []}
    fingerprint = hashlib.sha256(_json_dumps(episode).encode("utf-8")).hexdigest()
    return [{
        "event_id": _event_id(safe_user_key, thread_id, fingerprint),
        "user_key": safe_user_key,
        "thread_id": thread_id,
        "status": "detected",
        "severity": "unassessed",
        "trigger_type": "conversation_episode",
        "root_cause": "",
        "original_question": question,
        "failed_claim": "",
        "correction_summary": "",
        "successful_path": [],
        "evidence_plan": {},
        "proposed_change_type": "",
        "proposal_id": "",
        "mutation_id": "",
        "eval_case_path": "",
        "eval_status": "not_generated",
        "confidence": 0.0,
        "metadata": {"episode_fingerprint": fingerprint},
    }]


def _load_run_payloads_for_thread(thread: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in list(thread.get("messages") or []):
        if not isinstance(message, dict):
            continue
        run_id = _clean(message.get("run_id") or (_assistant_payload(message).get("agent_result") or {}).get("run_id"))
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        loaded = load_chat_session(run_id)
        if isinstance(loaded, dict):
            payloads.append(loaded)
    return payloads


def detect_learning_events_for_thread(
    thread_id: str,
    user_key: str,
    *,
    conn: Any | None = None,
) -> list[dict[str, Any]]:
    thread = load_chat_thread(thread_id=thread_id, user_key=user_key, conn=conn)
    if not thread:
        return []
    run_payloads = _load_run_payloads_for_thread(thread)
    return detect_learning_events_from_payload(thread, user_key=user_key, run_payloads=run_payloads)


_CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_id": {"type": "string"},
        "root_cause": {"type": "string"},
        "failed_assumption": {"type": "string"},
        "failed_claim": {"type": "string"},
        "successful_path": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                    "why_it_worked": {"type": "string"},
                },
                "required": ["tool", "arguments", "why_it_worked"],
            },
        },
        "should_try_earlier": {"type": "array", "items": {"type": "string"}},
        "forbidden_future_claims": {"type": "array", "items": {"type": "string"}},
        "required_future_evidence": {"type": "array", "items": {"type": "string"}},
        "proposed_change_type": {
            "type": "string",
            "enum": ["no_change", "eval_only", "tool_affordance_memory", "planner_contract", "safe_memory_update", "manual_review"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "event_id",
        "root_cause",
        "failed_assumption",
        "failed_claim",
        "successful_path",
        "should_try_earlier",
        "forbidden_future_claims",
        "required_future_evidence",
        "proposed_change_type",
        "confidence",
    ],
}


def critique_learning_event(
    event: dict[str, Any],
    thread: dict[str, Any] | None = None,
    run_payloads: list[dict[str, Any]] | None = None,
    llm_client: Any | None = None,
    previous: dict[str, Any] | None = None,
    on_pending: Any | None = None,
    run_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    from .aql_zopedia_engine import research_engine_revision, run_aql_zopedia_structured_agent

    # Full episodes remain available without duplicating nested research traces
    # into the initial critic request. These sources never enter shared memory.
    records = {"episode": {"title": "Detected learning episode", "metadata": {"kind": "episode"},
               "content": {key: value for key, value in event.items() if key not in {"metadata", "status"}}}}
    for index, message in enumerate((thread or {}).get("messages") or []):
        records[f"message:{index}"] = {"title": f"Conversation message {index}", "metadata": {
            "kind": "message", "role": message.get("role"), "message_id": message.get("message_id"),
            "created_at": message.get("created_at")}, "content": message}
    for index, run in enumerate(run_payloads or []):
        records[f"run:{index}"] = {"title": f"Research execution {index}", "metadata": {
            "kind": "run", "run_id": run.get("run_id"), "status": run.get("status")}, "content": run}
    for key, record in (event.get("metadata", {}).get("evidence_records") or {}).items():
        records[f"source:{key}"] = record
    service = QueryService.from_environment().with_supplied_evidence(records)
    instructions = (
        "You are the Zopedia learning critic. Review the original episode and conversation, then inspect relevant "
        "execution records using the supplied evidence tools and JSON Pointers. Every original remains available. "
        "Decide whether this episode contains a useful correction or lesson; ordinary successful work can need no_change. "
        "Sources, prior answers and user corrections can all be wrong. Assess them against the evidence rather than "
        "assuming a failure or accepting a correction. Identify which conclusions depend on a disputed detail and preserve "
        "independent findings. A noisy detail or incomplete review need not invalidate useful analysis. Distinguish "
        "execution failures from research quality, and return a compact diagnosis with reusable learning guidance. "
        "Choose no_change when the evidence does not support a lesson. "
        "Do not invent successful paths, confidence or completed verification. "
        "Future claims/evidence are semantic guidance for review, never literal phrase bans or mandatory tool sequences."
        " For outcome reviews, distinguish information available at the decision from later observations. A profitable "
        "outcome does not establish sound reasoning, and a loss does not establish a reasoning error. Account for "
        "benchmark moves, uncertainty and the original horizon. Do not turn hindsight into a decision-time fact."
    )
    from services.llm import get_active_narrative_style_rule
    config = getattr(llm_client, "config", None)
    contract = {"evidence": service.supplied_evidence_manifest, "instructions": instructions,
                "schema": _CRITIC_SCHEMA, "engine": research_engine_revision(),
                "model_contract": {key: getattr(config, key, None) for key in (
                    "provider", "base_url", "deployment", "api_version", "model", "api_style", "thinking_mode", "reasoning_effort", "temperature")},
                "style": get_active_narrative_style_rule(), "source_access": ["supplied"], "write_policy": "none"}
    fingerprint = hashlib.sha256(_json_dumps(contract).encode()).hexdigest()
    execution = {"input_fingerprint": fingerprint, "status": "pending", "attempted_at": _utc_now().isoformat()}
    previous = previous or {}
    if (previous.get("_execution") or {}).get("input_fingerprint") == fingerprint and (previous.get("_execution") or {}).get("status") == "completed":
        return {**previous, "_reused": True}
    if on_pending is not None:
        on_pending()
    if llm_client is None:
        return {"event_id": _clean(event.get("event_id")), "_execution": {**execution, "error": "LLM runtime unavailable"}}
    try:
        operation = run_aql_zopedia_structured_agent(
            query=f"Review learning episode {_clean(event.get('event_id'))}. Original question: {_clean(event.get('original_question'))}\n"
                  + "Evidence manifest: " + _json_dumps(service.supplied_evidence_manifest),
            instructions=instructions, schema_name="zopedia_learning_critic", schema=_CRITIC_SCHEMA,
            task="zopedia_learning_critic", surface="services.zopedia_learning", llm_client=llm_client,
            run_timeout_seconds=run_timeout_seconds,
            service=service, initial_evidence=[{"title": "Evidence navigation", **service.supplied_evidence_manifest}],
            request_context={"allow_web": False, "allowed_source_access": ["supplied"]}, persist_findings=False, write_policy="none",
        )
        if operation.get("status") != "completed" or not isinstance(operation.get("payload"), dict):
            raise RuntimeError(operation.get("error") or "Learning critique did not complete")
        return {**operation["payload"], "event_id": _clean(event.get("event_id")),
                "_execution": {**execution, "status": "completed"}}
    except Exception as exc:
        return {"event_id": _clean(event.get("event_id")),
                "_execution": {**execution, "error": f"{type(exc).__name__}: {_clean(exc)[:1200]}"}}


def build_regression_eval_case(event: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    question = _clean(event.get("original_question")) or "Replay the corrected Zopedia query."
    required_tools = []
    for item in list(critique.get("successful_path") or event.get("successful_path") or []):
        if isinstance(item, dict) and _clean(item.get("tool")):
            required_tools.append(_clean(item.get("tool")))
    required_tools = list(dict.fromkeys(required_tools))
    name = f"{_slug(question, default='zopedia_learning')}_{_clean(event.get('event_id'))[-8:]}"
    root_cause = _clean(critique.get("root_cause") or event.get("root_cause"))
    return {
        "name": name,
        "thread_source": _clean(event.get("thread_id")),
        "learning_event_id": _clean(event.get("event_id")),
        "question": question,
        "observed_successful_tools": required_tools,
        "review_guidance": {"future_claims_to_reconsider": list(critique.get("forbidden_future_claims") or []),
                            "useful_evidence": list(critique.get("required_future_evidence") or []),
                            "failed_assumption": critique.get("failed_assumption")},
        "root_cause": root_cause,
        "benchmark_answer_summary": _clean(event.get("correction_summary")),
        "user_key": _clean(event.get("user_key")),
        "evidence_records": (event.get("metadata") or {}).get("evidence_records") or {},
    }


def persist_regression_eval_case(eval_case: dict[str, Any], *, conn: Any | None = None,
                                 base_dir: str | Path | None = None) -> str:
    """Keep scheduled reviews in their durable event; local files are exports."""
    if conn is not None:
        event_id = _clean(eval_case.get("learning_event_id"))
        if not event_id:
            raise ValueError("A durable learning review requires its event identity.")
        _merge_learning_event_metadata(conn, event_id, {"eval_case": eval_case})
        return "zopedia_learning_event:" + event_id
    directory = Path(base_dir or os.getenv("ZOPEDIA_LEARNING_EVAL_CASE_DIR") or DEFAULT_EVAL_CASE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    name = _slug(eval_case.get("name"), default="zopedia_learning_eval")
    path = directory / f"{name}.json"
    path.write_text(json.dumps(eval_case, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")
    return str(path)


def build_tool_affordance_update(event: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    tool_lines = []
    for item in list(critique.get("successful_path") or []):
        if not isinstance(item, dict):
            continue
        tool = _clean(item.get("tool"))
        if not tool:
            continue
        tool_lines.append(f"- `{tool}` with arguments `{json.dumps(item.get('arguments') or {}, sort_keys=True)}`.")
    event_id = _clean(event.get("event_id"))
    title = f"Tool Affordance Learning: {event_id}"
    body = "\n".join(
        [
            "# Tool Affordance Learning",
            "",
            f"Learning event: `{event_id}`",
            f"Root cause: `{_clean(critique.get('root_cause') or event.get('root_cause'))}`",
            "",
            "## Failed Assumption",
            _clean(critique.get("failed_assumption")),
            "",
            "## Earlier Tool Paths To Try",
            *tool_lines,
            "",
            "## Forbidden Future Claims",
            *[f"- {claim}" for claim in list(critique.get("forbidden_future_claims") or [])],
            "",
            "## Required Future Evidence",
            *[f"- {claim}" for claim in list(critique.get("required_future_evidence") or [])],
        ]
    ).strip()
    page = {
        "page_type": "concept",
        "title": title,
        "slug": f"tool-affordance-learning-{event_id}",
        "summary": _clean(critique.get("failed_assumption"))[:360],
        "body_markdown": body,
        "entity_refs": ["zopedia_learning", "tool_affordance"],
        "source_urls": [],
        "metadata": {
            "source": "zopedia_learning",
            "learning_event_id": event_id,
            "root_cause": _clean(critique.get("root_cause") or event.get("root_cause")),
        },
    }
    return {"mutation_type": "upsert_pages", "pages": [page], "rationale": "Safe tool-affordance memory from verified chat evidence."}


def _persist_learning_event(conn: Any, event: dict[str, Any], *, commit: bool = True, insert_only: bool = False) -> dict[str, Any]:
    timestamp = _utc_now()
    event_id = _clean(event.get("event_id")) or _event_id(event.get("thread_id"), event.get("original_question"))
    with conn.cursor() as cur:
        statement = """
            INSERT INTO saa_zopedia_learning_events (
                event_id, user_key, thread_id, status, severity, trigger_type, root_cause,
                original_question, failed_claim, correction_summary, successful_path_json,
                evidence_plan_json, proposed_change_type, proposal_id, mutation_id,
                eval_case_path, eval_status, confidence, created_at_utc, updated_at_utc,
                metadata_json
            ) VALUES (
                %(event_id)s, %(user_key)s, %(thread_id)s, %(status)s, %(severity)s, %(trigger_type)s, %(root_cause)s,
                %(original_question)s, %(failed_claim)s, %(correction_summary)s, %(successful_path_json)s::jsonb,
                %(evidence_plan_json)s::jsonb, %(proposed_change_type)s, %(proposal_id)s, %(mutation_id)s,
                %(eval_case_path)s, %(eval_status)s, %(confidence)s, %(created_at_utc)s, %(updated_at_utc)s,
                %(metadata_json)s::jsonb
            )
            ON CONFLICT (event_id) DO UPDATE SET
                status = EXCLUDED.status,
                severity = EXCLUDED.severity,
                trigger_type = EXCLUDED.trigger_type,
                root_cause = EXCLUDED.root_cause,
                failed_claim = EXCLUDED.failed_claim,
                correction_summary = EXCLUDED.correction_summary,
                successful_path_json = EXCLUDED.successful_path_json,
                evidence_plan_json = EXCLUDED.evidence_plan_json,
                proposed_change_type = EXCLUDED.proposed_change_type,
                proposal_id = COALESCE(NULLIF(EXCLUDED.proposal_id, ''), saa_zopedia_learning_events.proposal_id),
                mutation_id = COALESCE(NULLIF(EXCLUDED.mutation_id, ''), saa_zopedia_learning_events.mutation_id),
                eval_case_path = COALESCE(NULLIF(EXCLUDED.eval_case_path, ''), saa_zopedia_learning_events.eval_case_path),
                eval_status = EXCLUDED.eval_status,
                confidence = EXCLUDED.confidence,
                updated_at_utc = EXCLUDED.updated_at_utc,
                metadata_json = EXCLUDED.metadata_json
            """
        if insert_only:
            statement = statement.split("ON CONFLICT")[0] + "ON CONFLICT (event_id) DO NOTHING"
        cur.execute(statement,
            {
                "event_id": event_id,
                "user_key": _clean(event.get("user_key")) or "default",
                "thread_id": _clean(event.get("thread_id")) or None,
                "status": _clean(event.get("status")) or "detected",
                "severity": _clean(event.get("severity")) or "medium",
                "trigger_type": _clean(event.get("trigger_type")) or "user_rescue",
                "root_cause": _clean(event.get("root_cause")) or "missing_fallback",
                "original_question": _clean(event.get("original_question")),
                "failed_claim": _clean(event.get("failed_claim")),
                "correction_summary": _clean(event.get("correction_summary")),
                "successful_path_json": _json_dumps(list(event.get("successful_path") or [])),
                "evidence_plan_json": _json_dumps(dict(event.get("evidence_plan") or {})),
                "proposed_change_type": _clean(event.get("proposed_change_type")),
                "proposal_id": _clean(event.get("proposal_id")),
                "mutation_id": _clean(event.get("mutation_id")),
                "eval_case_path": _clean(event.get("eval_case_path")),
                "eval_status": _clean(event.get("eval_status")) or "not_generated",
                "confidence": 0.0 if event.get("status") == "critique_pending" else float(event.get("confidence") or 0.0),
                "created_at_utc": timestamp,
                "updated_at_utc": timestamp,
                "metadata_json": _json_dumps(dict(event.get("metadata") or {})),
            },
        )
    if commit:
        conn.commit()
    saved = dict(event)
    saved["event_id"] = event_id
    return saved


def _update_learning_event_fields(conn: Any, event_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status",
        "proposal_id",
        "mutation_id",
        "eval_case_path",
        "eval_status",
        "metadata_json",
    }
    assignments: list[str] = ["updated_at_utc = %(updated_at_utc)s"]
    params: dict[str, Any] = {"event_id": event_id, "updated_at_utc": _utc_now()}
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = %({key})s")
        params[key] = _json_dumps(value) if key == "metadata_json" else value
    if len(assignments) == 1:
        return
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE saa_zopedia_learning_events SET {', '.join(assignments)} WHERE event_id = %(event_id)s",
            params,
        )


def _merge_learning_event_metadata(conn: Any, event_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metadata_json FROM saa_zopedia_learning_events WHERE event_id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    existing = _json_dict(row[0] if row else {})
    merged = {**existing, **metadata}
    _update_learning_event_fields(conn, event_id, metadata_json=merged)
    return merged


def apply_safe_learning_update(
    event: dict[str, Any],
    update: dict[str, Any],
    *,
    conn: Any | None = None,
) -> dict[str, Any]:
    if _clean(update.get("mutation_type")) != "upsert_pages":
        return {"status": "proposal_required", "reason": "Only upsert_pages is considered safe for automatic learning updates."}
    result = apply_zopedia_typed_mutation(
        mutation_type="upsert_pages",
        pages=list(update.get("pages") or []),
        evidence_refs=[
            {
                "kind": "zopedia_learning_event",
                "ref": _clean(event.get("event_id")),
                "thread_id": _clean(event.get("thread_id")),
            }
        ],
        rationale=_clean(update.get("rationale")) or "Safe Zopedia learning update.",
        actor="zopedia-learning",
        source="zopedia.learning",
        conn=conn,
    )
    return result if isinstance(result, dict) else {"status": "unknown"}


def enqueue_learning_episode(conn: Any, *, user_key: str, namespace: str, source_id: str,
                             question: str, evidence_records: dict[str, Any]) -> str:
    """Retain a non-conversation episode without resetting a completed review.

    The producer bootstraps storage once per batch and owns the transaction.
    Identity fixes the source in its environment and owner; retries cannot rewrite it.
    """
    from services.pipeline_store import normalize_dataset_namespace
    namespace = normalize_dataset_namespace(namespace)
    if not all(_clean(value) for value in (user_key, source_id, question)) or not evidence_records:
        raise ValueError("A learning episode requires an owner, source and original evidence.")
    event = {"event_id": _event_id(namespace, user_key, source_id), "user_key": user_key,
             "status": "detected", "severity": "unassessed", "trigger_type": "outcome_episode",
             "root_cause": "unassessed", "original_question": question,
             "metadata": {"namespace": namespace, "source_id": source_id, "evidence_records": evidence_records}}
    _persist_learning_event(conn, event, commit=False, insert_only=True)
    return event["event_id"]


def _recent_threads(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    from services.pipeline_store import dataset_namespace
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT thread_id, user_key, episode FROM (
              SELECT t.thread_id, t.user_key, NULL::jsonb AS episode,
                     pending.retry_at, t.updated_at AS observed_at
              FROM saa_zopedia_chat_threads t
              LEFT JOIN LATERAL (
                SELECT min(e.updated_at_utc) AS retry_at FROM saa_zopedia_learning_events e
                WHERE e.thread_id=t.thread_id AND e.status IN ('critique_pending','review_pending')
              ) pending ON true
              WHERE t.status <> 'deleted'
              UNION ALL
              SELECT e.thread_id, e.user_key, jsonb_build_object(
                  'event_id', e.event_id, 'user_key', e.user_key, 'original_question', e.original_question,
                  'trigger_type', e.trigger_type, 'metadata', e.metadata_json),
                  e.updated_at_utc, e.created_at_utc
              FROM saa_zopedia_learning_events e
              WHERE e.thread_id IS NULL AND e.metadata_json->>'namespace'=%s
                AND e.metadata_json ? 'evidence_records'
                AND e.status IN ('detected','critique_pending','review_pending','eval_generated','memory_updated','proposal_created')
            ) inputs
            ORDER BY (retry_at IS NOT NULL) DESC, retry_at ASC NULLS LAST, observed_at DESC
            LIMIT %s
            """,
            (dataset_namespace(), max(int(limit), 1)),
        )
        rows = cur.fetchall()
    return [{"thread_id": _clean(row[0]), "user_key": _clean(row[1]) or "default",
             "episode": _json_dict(row[2])} for row in rows]


def run_zopedia_learning_job(limit: int = 25, conn: Any | None = None, *,
                             deadline_monotonic: float | None = None) -> dict[str, Any]:
    own_conn = conn is None
    db_conn = conn or _db_connection()
    if db_conn is None:
        return {
            "status": "no_database",
            "threads_scanned": 0,
            "events_detected": 0,
            "events_triaged": 0,
            "evals_generated": 0,
            "safe_updates_applied": 0,
            "verified": 0,
            "rejected": 0,
            "regressed": 0,
        }

    summary = {
        "status": "completed",
        "threads_scanned": 0,
        "events_detected": 0,
        "events_triaged": 0,
        "critiques_pending": 0,
        "critiques_reused": 0,
        "reviews_pending": 0,
        "evals_generated": 0,
        "safe_updates_applied": 0,
        "verified": 0,
        "rejected": 0,
        "regressed": 0,
    }
    generated_eval_events: dict[str, str] = {}
    from services.work_scope import WorkScope, WorkCancelled
    from services.llm_execution import ResearchWorkDeferred
    from services.aql_zopedia_engine import resolve_research_budget
    case_seconds = _env_int("ZOPEDIA_LEARNING_CASE_TIMEOUT_SECONDS",
                            int(resolve_research_budget()["run_timeout_seconds"]), minimum=1)
    reserve_seconds = _env_int("ZOPEDIA_LEARNING_FINALIZATION_RESERVE_SECONDS", 60, minimum=1)
    work_deadline = deadline_monotonic - reserve_seconds if deadline_monotonic is not None else float("inf")
    try:
        if db_conn.info.transaction_status != 0:
            raise ValueError("Zopedia learning requires a connection outside a transaction.")
        bootstrap_zopedia_learning_storage(db_conn)
        try:
            llm_client = load_aql_zopedia_llm_client(surface="zopedia.learning_job", fallback_to_default=True)
        except Exception:
            llm_client = None
        threads = _recent_threads(db_conn, limit=limit)
        db_conn.commit()
        pending_cases = []
        for row in threads:
            summary["threads_scanned"] += 1
            thread, run_payloads = None, []
            if row.get("episode"):
                events = [row["episode"]]
            else:
                thread = load_chat_thread(thread_id=row["thread_id"], user_key=row["user_key"], conn=db_conn)
                db_conn.commit()
                if not thread:
                    continue
                run_payloads = _load_run_payloads_for_thread(thread)
                events = detect_learning_events_from_payload(thread, user_key=row["user_key"], run_payloads=run_payloads)
            summary["events_detected"] += len(events)
            for event in events:
                with db_conn.cursor() as cur:
                    cur.execute("SELECT row_to_json(e) FROM saa_zopedia_learning_events e WHERE event_id = %s", (event["event_id"],))
                    prior_row = cur.fetchone()
                prior = dict(prior_row[0]) if prior_row else {}
                prior_metadata = _json_dict(prior.get("metadata_json"))
                db_conn.commit()
                if not prior:
                    event["status"] = "critique_pending"
                    _persist_learning_event(db_conn, event)
                pending_cases.append((event, thread, run_payloads, prior, prior_metadata))
        # Discover and journal the batch before any model work can consume the
        # window. Every deferred case is visible to the next scheduled run.
        for index, (event, thread, run_payloads, prior, prior_metadata) in enumerate(pending_cases):
            if time.monotonic() >= work_deadline:
                summary["cases_deferred"] = len(pending_cases) - index
                summary["status"] = "partial"
                break
            # Save pending state before inference, so process death cannot masquerade as success.
            event["status"] = "critique_pending"
            event["metadata"] = {**dict(event.get("metadata") or {}), **prior_metadata}
            def retain_pending():
                _persist_learning_event(db_conn, event)
                if work_deadline - time.monotonic() < case_seconds:
                    raise ResearchWorkDeferred("The remaining learning window cannot fund another complete case.")
            critique = {}
            try:
                # Reusing a completed critic is allowed without buying a
                # new case. Only its on_pending boundary admits inference.
                with WorkScope(min(deadline_monotonic if deadline_monotonic is not None else float("inf"), time.monotonic() + case_seconds)).activate():
                    critique = critique_learning_event(event, thread, run_payloads, llm_client,
                        previous=prior_metadata.get("critic"), on_pending=retain_pending, run_timeout_seconds=case_seconds)
            except (ResearchWorkDeferred, WorkCancelled) as exc:
                if not critique:
                    critique = {"_execution": {"status": "pending", "deferred": True, "error": str(exc)}}
            if critique.get("_reused"):
                summary["critiques_reused"] += 1
            if (critique.get("_execution") or {}).get("status") != "completed":
                _merge_learning_event_metadata(db_conn, event["event_id"], {"critic": critique})
                db_conn.commit()
                summary["critiques_pending"] += 1
                continue
            # A previously committed update/evaluation must not be applied or bought twice.
            if critique.get("_reused"):
                if prior.get("eval_case_path") and prior.get("eval_status") not in {"reviewed", "verified", "not_required"}:
                    # Older replicas retained only a local path. Reconstruct
                    # once from the saved event/critic, never from a new chat
                    # interpretation, then persist before starting a replay.
                    saved_case = prior_metadata.get("eval_case")
                    if not isinstance(saved_case, dict):
                        saved_case = build_regression_eval_case(
                            {**prior, "metadata": prior_metadata,
                             "successful_path": _json_list(prior.get("successful_path_json"))}, critique)
                    reference = persist_regression_eval_case(saved_case, conn=db_conn)
                    _update_learning_event_fields(db_conn, event["event_id"], eval_case_path=reference)
                    db_conn.commit()
                    generated_eval_events[event["event_id"]] = reference
                continue
            event["status"] = "triaged"
            event["root_cause"] = _clean(critique.get("root_cause")) or event.get("root_cause")
            event["failed_claim"] = _clean(critique.get("failed_claim"))
            event["successful_path"] = list(critique.get("successful_path") or [])
            event["correction_summary"] = _clean(critique.get("root_cause"))
            event["proposed_change_type"] = _clean(critique.get("proposed_change_type")) or event.get("proposed_change_type")
            event["confidence"] = float(critique.get("confidence") or 0.0)
            event["metadata"] = {**dict(event.get("metadata") or {}), "critic": critique}
            saved = _persist_learning_event(db_conn, event, commit=False)
            summary["events_triaged"] += 1
            if event["proposed_change_type"] == "no_change":
                _update_learning_event_fields(db_conn, saved["event_id"], status="reviewed_no_change", eval_status="not_required")
                db_conn.commit()
                continue

            eval_case = build_regression_eval_case(saved, critique)
            eval_path = persist_regression_eval_case(eval_case, conn=db_conn)
            _update_learning_event_fields(
                db_conn,
                saved["event_id"],
                status="eval_generated",
                eval_case_path=eval_path,
                eval_status="generated",
            )
            summary["evals_generated"] += 1
            generated_eval_events[saved["event_id"]] = eval_path

            if event["proposed_change_type"] == "tool_affordance_memory" and event["confidence"] >= 0.7:
                update = build_tool_affordance_update(saved, critique)
                mutation_result = apply_safe_learning_update(saved, update, conn=db_conn)
                mutation_id = _clean((mutation_result.get("mutation_audit") or {}).get("mutation_id"))
                if mutation_result.get("status") == "committed":
                    _update_learning_event_fields(
                        db_conn,
                        saved["event_id"],
                        status="safe_update_applied",
                        mutation_id=mutation_id,
                        eval_status="generated",
                    )
                    summary["safe_updates_applied"] += 1
                else:
                    proposal_id = _clean(mutation_result.get("proposal_id"))
                    _update_learning_event_fields(
                        db_conn,
                        saved["event_id"],
                        status="proposal_created" if proposal_id else "eval_generated",
                        proposal_id=proposal_id,
                    )
            # Each event is durable before another critic/model call starts.
            db_conn.commit()
        db_conn.commit()

        if _env_bool("ZOPEDIA_LEARNING_VERIFY_EVALS", True):
            verify_limit = _env_int("ZOPEDIA_LEARNING_VERIFY_LIMIT", 3, minimum=0)
            for event_id, eval_path in list(generated_eval_events.items())[:verify_limit]:
                if work_deadline - time.monotonic() < case_seconds:
                    summary["status"] = "partial"
                    break
                try:
                    with WorkScope(min(work_deadline, time.monotonic() + case_seconds)).activate():
                        replay = replay_learning_eval(eval_path, run_agent=True, conn=db_conn, run_timeout_seconds=case_seconds)
                except Exception as exc:
                    _merge_learning_event_metadata(
                        db_conn,
                        event_id,
                        {"verification_error": f"{type(exc).__name__}: {_clean(exc)[:500]}"},
                    )
                    _update_learning_event_fields(
                        db_conn,
                        event_id,
                        status="review_pending",
                        eval_status="replay_error",
                    )
                    summary["status"] = "partial"
                    db_conn.commit()
                    continue
                replay_status = _clean(replay.get("status"))
                if replay_status != "reviewed":
                    summary["status"] = "partial"
                else:
                    generated_eval_events.pop(event_id)
                _merge_learning_event_metadata(db_conn, event_id, {"verification": {
                    "status": replay_status, "quality_review": replay.get("quality_review") or {},
                    "tool_names": list(replay.get("tool_names") or []),
                }, "verification_error": None})
                _update_learning_event_fields(db_conn, event_id, status="reviewed" if replay_status == "reviewed" else "review_pending", eval_status=replay_status)
                db_conn.commit()
            db_conn.commit()
        summary["reviews_pending"] = len(generated_eval_events)
        for event_id in generated_eval_events:
            _update_learning_event_fields(db_conn, event_id, status="review_pending")
        db_conn.commit()
        if summary["critiques_pending"] or summary["reviews_pending"] or summary["status"] == "partial":
            summary.update(status="partial", job_status="Warning", job_message=(
                f"{summary['critiques_pending']} learning critiques and {summary['reviews_pending']} reviews remain pending; "
                f"{summary.get('cases_deferred', 0)} cases deferred; completed work is retained."))
        return summary
    except Exception:
        try:
            db_conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if own_conn:
            db_conn.close()


def replay_learning_eval(eval_case_path: str, *, run_agent: bool = False, conn: Any | None = None,
                         run_timeout_seconds: float | None = None) -> dict[str, Any]:
    if eval_case_path.startswith("zopedia_learning_event:"):
        if conn is None:
            raise ValueError("A database connection is required to read a durable learning review.")
        with conn.cursor() as cur:
            cur.execute("SELECT metadata_json->'eval_case' FROM saa_zopedia_learning_events WHERE event_id = %s",
                        (eval_case_path.split(":", 1)[1],))
            row = cur.fetchone()
        payload = _json_dict(row[0] if row else {})
        if not payload:
            raise ValueError("The learning event has no retained review input.")
        conn.commit()  # Model/tool work never retains the input read transaction.
    else:
        payload = json.loads(Path(eval_case_path).read_text())
    if not run_agent:
        return {"status": "loaded", "eval_case": payload}
    from services.aql_zopedia_engine import run_aql_zopedia_agent

    records = payload.get("evidence_records") or {}
    context = {"task_context": {"learning_review_guidance": payload.get("review_guidance") or {}},
               "user_key": _clean(payload.get("user_key"))}
    if records:
        # An outcome episode is a retrospective review, never a historical
        # forecast scored against information acquired after the decision.
        context.update(allow_web=False, allowed_source_access=["supplied"])
        context["task_context"]["review_mode"] = "retrospective reasoning review; not forecast-accuracy validation"
    result = run_aql_zopedia_agent(
        query=_clean(payload.get("question")),
        request_context=context,
        service=QueryService.from_environment().with_supplied_evidence(records) if records else None,
        initial_evidence=[{"title": "Evidence navigation", "records": list(records)}] if records else None,
        task="learning_eval_replay",
        run_timeout_seconds=run_timeout_seconds,
        surface="zopedia.learning",
        max_tool_calls=8,
        persist_findings=False,
        write_policy="none",
    )
    review = result.get("quality_review") or {}
    return {
        "status": "execution_failed" if result.get("status") != "completed" else "reviewed" if review.get("status") == "completed" else "review_pending",
        "eval_case": payload,
        "tool_names": [_clean(call.get("tool_name")) for call in result.get("tool_calls") or [] if isinstance(call, dict)],
        "quality_review": review,
        "agent_result": result,
    }


__all__ = [
    "bootstrap_zopedia_learning_storage",
    "build_regression_eval_case",
    "build_tool_affordance_update",
    "critique_learning_event",
    "detect_learning_events_for_thread",
    "detect_learning_events_from_payload",
    "persist_regression_eval_case",
    "apply_safe_learning_update",
    "replay_learning_eval",
    "run_zopedia_learning_job",
]
