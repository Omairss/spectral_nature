from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

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

_UNAVAILABLE_CLAIM_RE = re.compile(
    r"\b("
    r"cannot\s+(?:query|access|get|retrieve)|"
    r"can't\s+(?:query|access|get|retrieve)|"
    r"data\s+(?:is\s+)?unavailable|"
    r"no\s+(?:market|stock|price|etf|sector)\s+data|"
    r"returned\s+empty.*no\s+data|"
    r"not\s+available"
    r")\b",
    flags=re.IGNORECASE,
)
_IMPLEMENTATION_LEAK_RE = re.compile(
    r"\b(tool[_ -]?call|run[_ -]?id|provider|debug|eval\.local|zthread_|zmsg_|agtc_)\b",
    flags=re.IGNORECASE,
)
_PRIMITIVE_DATA_TOOLS = {
    "dataset.price_history",
    "dataset.daily_movers",
    "dataset.yield_curve_facts_1d",
    "dataset.yield_curve_observations",
    "analysis.run_python",
}


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


def _tool_name(call: dict[str, Any]) -> str:
    return _clean(call.get("tool_name") or call.get("name"))


def _tool_status(call: dict[str, Any]) -> str:
    return _clean(call.get("status") or call.get("state")).lower()


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("arguments") or call.get("args") or {}
    return dict(args) if isinstance(args, dict) else {}


def _payload_rows(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("rows", "sample", "records", "data"):
            child = value.get(key)
            if isinstance(child, list):
                return len(child)
        row_count = value.get("row_count") or value.get("rows_count")
        try:
            return int(row_count)
        except Exception:
            return 1 if value else 0
    return 0


def _tool_has_rows(call: dict[str, Any]) -> bool:
    summary = call.get("result_summary")
    if isinstance(summary, dict):
        if _payload_rows(summary.get("payload")) > 0 or _payload_rows(summary.get("preview")) > 0:
            return True
        row_count = summary.get("row_count")
        try:
            if int(row_count) > 0:
                return True
        except Exception:
            pass
    result = call.get("result")
    if isinstance(result, dict):
        if _payload_rows(result.get("payload")) > 0:
            return True
        row_count = result.get("row_count")
        try:
            if int(row_count) > 0:
                return True
        except Exception:
            pass
    return False


def _assistant_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = dict(message.get("payload") or {}) if isinstance(message.get("payload"), dict) else {}
    if payload:
        return payload
    return dict(message)


def _message_content(message: dict[str, Any]) -> str:
    payload = _assistant_payload(message)
    return _clean(
        message.get("content")
        or payload.get("content")
        or payload.get("answer")
        or payload.get("answer_markdown")
        or (payload.get("agent_result") or {}).get("answer_markdown")
    )


def _agent_result(message: dict[str, Any]) -> dict[str, Any]:
    payload = _assistant_payload(message)
    agent_result = payload.get("agent_result")
    if isinstance(agent_result, dict):
        return dict(agent_result)
    return payload


def _tool_calls_from_thread(thread: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in list(thread.get("messages") or []):
        if not isinstance(message, dict) or _clean(message.get("role")).lower() != "assistant":
            continue
        agent_result = _agent_result(message)
        for call in list(agent_result.get("tool_calls") or []):
            if isinstance(call, dict):
                calls.append(dict(call))
    return calls


def _first_user_question(thread: dict[str, Any]) -> str:
    for message in list(thread.get("messages") or []):
        if isinstance(message, dict) and _clean(message.get("role")).lower() == "user":
            content = _clean(message.get("content"))
            if content:
                return content
    return _clean(thread.get("title"))


def _failed_claims(thread: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for message in list(thread.get("messages") or []):
        if not isinstance(message, dict) or _clean(message.get("role")).lower() != "assistant":
            continue
        content = _message_content(message)
        if not content:
            continue
        if _UNAVAILABLE_CLAIM_RE.search(content):
            failed.append(
                {
                    "root_cause": "premature_unavailable_claim",
                    "failed_claim": content[:1200],
                    "trigger_type": "tool_path_later_succeeded",
                }
            )
        if _IMPLEMENTATION_LEAK_RE.search(content):
            failed.append(
                {
                    "root_cause": "ui_or_answer_surface_leak",
                    "failed_claim": content[:1200],
                    "trigger_type": "implementation_vocabulary_exposed",
                }
            )
    return failed


def _successful_tool_paths(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for call in tool_calls:
        name = _tool_name(call)
        status = _tool_status(call)
        if status and status not in {"completed", "success", "succeeded"}:
            continue
        if name not in _PRIMITIVE_DATA_TOOLS:
            continue
        if not _tool_has_rows(call) and name != "analysis.run_python":
            continue
        paths.append(
            {
                "tool": name,
                "arguments": _tool_arguments(call),
                "why_it_worked": "The tool returned usable structured evidence after an earlier failed or insufficient path.",
            }
        )
    return paths


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
    """Detect durable learning candidates from a chat thread payload.

    The detector looks for a generic failure shape: an answer made an
    unavailable/debug claim while the same thread or later run found usable
    primitive evidence. Domain interpretation is left to the critic.
    """

    safe_user_key = _clean(user_key or thread.get("user_key")) or "default"
    thread_id = _clean(thread.get("thread_id"))
    question = _first_user_question(thread)
    tool_calls = _tool_calls_from_thread(thread)
    for payload in list(run_payloads or []):
        if not isinstance(payload, dict):
            continue
        for call in list(payload.get("tool_calls") or []):
            if isinstance(call, dict):
                tool_calls.append(dict(call))

    failed = _failed_claims(thread)
    successful_paths = _successful_tool_paths(tool_calls)
    if not failed or not successful_paths:
        return []

    events: list[dict[str, Any]] = []
    for item in failed:
        root_cause = _clean(item.get("root_cause")) or "missing_fallback"
        failed_claim = _clean(item.get("failed_claim"))
        trigger_type = _clean(item.get("trigger_type")) or "user_rescue"
        event = {
            "event_id": _event_id(thread_id, question, root_cause, failed_claim[:240]),
            "user_key": safe_user_key,
            "thread_id": thread_id,
            "status": "detected",
            "severity": "high" if root_cause == "premature_unavailable_claim" else "medium",
            "trigger_type": trigger_type,
            "root_cause": root_cause,
            "original_question": question,
            "failed_claim": failed_claim,
            "correction_summary": "A later tool path returned usable evidence, so future answers should not stop at the earlier failed path.",
            "successful_path": successful_paths[:8],
            "evidence_plan": {
                "question": question,
                "required_slots": [],
                "fallbacks_attempted": [],
                "cannot_claim": [
                    "Do not claim data is unavailable until primitive evidence paths have been tried.",
                    "Do not expose implementation IDs or debug refs in product answers.",
                ],
            },
            "proposed_change_type": "tool_affordance_memory",
            "proposal_id": "",
            "mutation_id": "",
            "eval_case_path": "",
            "eval_status": "not_generated",
            "confidence": 0.72,
            "metadata": {"detector": "zopedia_learning_v1", "tool_count": len(tool_calls)},
        }
        events.append(event)
    return events


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
        "root_cause": {
            "type": "string",
            "enum": [
                "tool_mismatch",
                "premature_unavailable_claim",
                "missing_fallback",
                "input_contract_failure",
                "code_generation_failure",
                "model_synthesis_failure",
                "source_trace_gap",
                "confidence_overreach",
                "ui_or_answer_surface_leak",
            ],
        },
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
            "enum": ["eval_only", "tool_affordance_memory", "planner_contract", "safe_memory_update", "manual_review"],
        },
        "confidence": {"type": "number"},
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


def _fallback_critique(event: dict[str, Any]) -> dict[str, Any]:
    successful_path = [dict(item) for item in list(event.get("successful_path") or []) if isinstance(item, dict)]
    tool_names = [_clean(item.get("tool")) for item in successful_path if _clean(item.get("tool"))]
    forbidden = [
        "stock data is unavailable",
        "cannot query actual stocks",
        "daily movers returned empty, so no market data",
        "no price data available today",
    ]
    if _clean(event.get("root_cause")) == "ui_or_answer_surface_leak":
        forbidden = ["tool-call ID", "run ID", "provider name", "debug reference"]
    return {
        "event_id": _clean(event.get("event_id")),
        "root_cause": _clean(event.get("root_cause")) or "missing_fallback",
        "failed_assumption": "The answer treated an earlier failed or insufficient path as final.",
        "failed_claim": _clean(event.get("failed_claim")),
        "successful_path": successful_path,
        "should_try_earlier": tool_names,
        "forbidden_future_claims": forbidden,
        "required_future_evidence": [
            "Use primitive evidence paths before saying data is unavailable.",
            "Carry tool empty-result diagnostics into the final answer when relevant.",
        ],
        "proposed_change_type": _clean(event.get("proposed_change_type")) or "tool_affordance_memory",
        "confidence": float(event.get("confidence") or 0.72),
    }


def critique_learning_event(
    event: dict[str, Any],
    thread: dict[str, Any] | None = None,
    run_payloads: list[dict[str, Any]] | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    if llm_client is None:
        return _fallback_critique(event)
    system_prompt = (
        "You are the Zopedia learning critic. Review a failed chat episode and produce a compact, "
        "auditable root-cause record. Do not write user-facing narrative. Focus on tool affordances, "
        "fallback paths, source coverage, and forbidden future claims."
    )
    user_prompt = _json_dumps(
        {
            "event": event,
            "thread_messages": list((thread or {}).get("messages") or [])[-12:],
            "run_payloads": list(run_payloads or [])[-3:],
        }
    )
    try:
        result = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="zopedia_learning_critic",
            schema=_CRITIC_SCHEMA,
        )
    except Exception:
        return _fallback_critique(event)
    if not isinstance(result, dict):
        return _fallback_critique(event)
    result.setdefault("event_id", _clean(event.get("event_id")))
    return result


def build_regression_eval_case(event: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    question = _clean(event.get("original_question")) or "Replay the corrected Zopedia query."
    required_tools = []
    for item in list(critique.get("successful_path") or event.get("successful_path") or []):
        if isinstance(item, dict) and _clean(item.get("tool")):
            required_tools.append(_clean(item.get("tool")))
    required_tools = list(dict.fromkeys(required_tools))
    name = f"{_slug(question, default='zopedia_learning')}_{_clean(event.get('event_id'))[-8:]}"
    root_cause = _clean(critique.get("root_cause") or event.get("root_cause"))
    forbidden_patterns = [_clean(item) for item in list(critique.get("forbidden_future_claims") or []) if _clean(item)]
    if root_cause == "ui_or_answer_surface_leak":
        forbidden_patterns.extend(["tool_call_id", "run_id", "provider", "debug", "eval.local"])
    else:
        forbidden_patterns.extend(
            [
                "stock data is unavailable",
                "cannot query actual stocks",
                "daily movers returned empty, so no market data",
                "no price data available today",
            ]
        )
    forbidden_patterns = list(dict.fromkeys(forbidden_patterns))
    return {
        "name": name,
        "thread_source": _clean(event.get("thread_id")),
        "learning_event_id": _clean(event.get("event_id")),
        "question": question,
        "required_tool_patterns": required_tools,
        "forbidden_answer_patterns": forbidden_patterns,
        "required_answer_claims": list(critique.get("required_future_evidence") or []),
        "confidence_rule": "confidence must be capped if required evidence slots remain unfilled",
        "root_cause": root_cause,
        "benchmark_answer_summary": _clean(event.get("correction_summary")),
    }


def persist_regression_eval_case(eval_case: dict[str, Any], *, base_dir: str | Path | None = None) -> str:
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
    if not tool_lines:
        tool_lines.append("- Use the primitive evidence path named in the learning event before saying data is unavailable.")
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


def _persist_learning_event(conn: Any, event: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
    timestamp = _utc_now()
    event_id = _clean(event.get("event_id")) or _event_id(event.get("thread_id"), event.get("original_question"))
    with conn.cursor() as cur:
        cur.execute(
            """
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
            """,
            {
                "event_id": event_id,
                "user_key": _clean(event.get("user_key")) or "default",
                "thread_id": _clean(event.get("thread_id")),
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
                "confidence": float(event.get("confidence") or 0.0),
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


def _recent_threads(conn: Any, *, limit: int) -> list[dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT thread_id, user_key
            FROM saa_zopedia_chat_threads
            WHERE status <> 'deleted'
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (max(int(limit), 1),),
        )
        rows = cur.fetchall()
    return [{"thread_id": _clean(row[0]), "user_key": _clean(row[1]) or "default"} for row in rows]


def run_zopedia_learning_job(limit: int = 25, conn: Any | None = None) -> dict[str, Any]:
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
        "evals_generated": 0,
        "safe_updates_applied": 0,
        "verified": 0,
        "rejected": 0,
        "regressed": 0,
    }
    generated_eval_events: list[tuple[str, str]] = []
    try:
        bootstrap_zopedia_learning_storage(db_conn, commit=False)
        try:
            llm_client = load_aql_zopedia_llm_client(surface="zopedia.learning_job", fallback_to_default=True)
        except Exception:
            llm_client = None
        for row in _recent_threads(db_conn, limit=limit):
            summary["threads_scanned"] += 1
            thread = load_chat_thread(thread_id=row["thread_id"], user_key=row["user_key"], conn=db_conn)
            if not thread:
                continue
            run_payloads = _load_run_payloads_for_thread(thread)
            events = detect_learning_events_from_payload(thread, user_key=row["user_key"], run_payloads=run_payloads)
            summary["events_detected"] += len(events)
            for event in events:
                critique = critique_learning_event(event, thread, run_payloads, llm_client)
                event["status"] = "triaged"
                event["root_cause"] = _clean(critique.get("root_cause")) or event.get("root_cause")
                event["proposed_change_type"] = _clean(critique.get("proposed_change_type")) or event.get("proposed_change_type")
                event["confidence"] = float(critique.get("confidence") or event.get("confidence") or 0.0)
                event["metadata"] = {**dict(event.get("metadata") or {}), "critic": critique}
                saved = _persist_learning_event(db_conn, event, commit=False)
                summary["events_triaged"] += 1

                eval_case = build_regression_eval_case(saved, critique)
                eval_path = persist_regression_eval_case(eval_case)
                _update_learning_event_fields(
                    db_conn,
                    saved["event_id"],
                    status="eval_generated",
                    eval_case_path=eval_path,
                    eval_status="generated",
                )
                summary["evals_generated"] += 1
                generated_eval_events.append((saved["event_id"], eval_path))

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
        db_conn.commit()

        if _env_bool("ZOPEDIA_LEARNING_VERIFY_EVALS", True):
            verify_limit = _env_int("ZOPEDIA_LEARNING_VERIFY_LIMIT", 3, minimum=0)
            for event_id, eval_path in generated_eval_events[:verify_limit]:
                try:
                    replay = replay_learning_eval(eval_path, run_agent=True)
                except Exception as exc:
                    _merge_learning_event_metadata(
                        db_conn,
                        event_id,
                        {"verification_error": f"{type(exc).__name__}: {_clean(exc)[:500]}"},
                    )
                    _update_learning_event_fields(
                        db_conn,
                        event_id,
                        status="regressed",
                        eval_status="replay_error",
                    )
                    summary["regressed"] += 1
                    continue
                replay_status = _clean(replay.get("status"))
                verification_metadata = {
                    "verification": {
                        "status": replay_status,
                        "tool_names": list(replay.get("tool_names") or []),
                        "forbidden_hits": list(replay.get("forbidden_hits") or []),
                        "required_tool_hits": list(replay.get("required_tool_hits") or []),
                    }
                }
                _merge_learning_event_metadata(db_conn, event_id, verification_metadata)
                if replay_status == "passed":
                    _update_learning_event_fields(
                        db_conn,
                        event_id,
                        status="verified",
                        eval_status="verified",
                    )
                    summary["verified"] += 1
                else:
                    _update_learning_event_fields(
                        db_conn,
                        event_id,
                        status="regressed",
                        eval_status="regressed",
                    )
                    summary["regressed"] += 1
            db_conn.commit()
        return summary
    except Exception:
        try:
            db_conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if own_conn:
            try:
                db_conn.close()
            except Exception:
                pass


def replay_learning_eval(eval_case_path: str, *, run_agent: bool = False) -> dict[str, Any]:
    path = Path(eval_case_path)
    payload = json.loads(path.read_text())
    if not run_agent:
        return {"status": "loaded", "eval_case": payload}
    from services.aql_zopedia_engine import run_aql_zopedia_agent

    result = run_aql_zopedia_agent(
        query=_clean(payload.get("question")),
        task="learning_eval_replay",
        surface="zopedia.learning",
        max_tool_calls=8,
        persist_findings=False,
    )
    answer = _clean(result.get("answer_markdown"))
    tool_names = [_clean(call.get("tool_name")) for call in list(result.get("tool_calls") or []) if isinstance(call, dict)]
    forbidden_hits = [
        pattern
        for pattern in list(payload.get("forbidden_answer_patterns") or [])
        if _clean(pattern) and _clean(pattern).lower() in answer.lower()
    ]
    required_tool_hits = [
        pattern
        for pattern in list(payload.get("required_tool_patterns") or [])
        if any(_clean(pattern).split()[0] in name for name in tool_names)
    ]
    return {
        "status": "passed" if not forbidden_hits and required_tool_hits else "failed",
        "eval_case": payload,
        "tool_names": tool_names,
        "forbidden_hits": forbidden_hits,
        "required_tool_hits": required_tool_hits,
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
