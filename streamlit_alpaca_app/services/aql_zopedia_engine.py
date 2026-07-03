from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Callable

import pandas as pd

from .aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from .elevenlabs_tts import (
    ElevenLabsTTSClient,
    ElevenLabsTTSConfig,
    audio_file_extension,
    audio_mime_type,
    load_elevenlabs_tts_config,
)
from .json_utils import to_jsonable
from .llm import get_config_param, register_config_param
from .zopedia_runtime import ZopediaLLMClient, load_zopedia_llm_client


ProgressCallback = Callable[[dict[str, object]], None]

_WRITE_POLICIES = {"none", "propose", "safe_auto"}


_P_AGENT_MAX_TOOL_CALLS = register_config_param(
    "Engine agent max tool calls",
    group="AQL / Zopedia Engine",
    default=10,
    description="Default tool budget for direct AQL/Zopedia agent runs.",
)
_P_PAGE_SUMMARY_MAX_TOOL_CALLS = register_config_param(
    "Engine page summary max tool calls",
    group="AQL / Zopedia Engine",
    default=10,
    description="Tool budget for page summary evidence collection.",
)
_P_ATTENTION_SUMMARY_EVIDENCE_ENABLED = register_config_param(
    "Engine attention summary evidence enabled",
    group="AQL / Zopedia Engine",
    default=1,
    description="Whether scheduled attention summaries also run the shared Zopedia evidence pass.",
)
_P_ATTENTION_SUMMARY_MAX_TOOL_CALLS = register_config_param(
    "Engine attention summary max tool calls",
    group="AQL / Zopedia Engine",
    default=6,
    description="Tool budget for the scheduled attention homepage Zopedia evidence pass.",
)
_P_ATTENTION_SUMMARY_WRITE_POLICY = register_config_param(
    "Engine attention summary write policy",
    group="AQL / Zopedia Engine",
    default="safe_auto",
    description="Memory write policy for scheduled Attention summary AQL/Zopedia runs: none, propose, or safe_auto.",
)


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_write_policy(value: object, *, persist_findings: bool = True) -> str:
    policy = _clean(value).lower()
    if not policy:
        return "safe_auto" if persist_findings else "none"
    if policy in {"off", "disabled", "read_only", "readonly"}:
        return "none"
    if policy in {"proposal", "review"}:
        return "propose"
    if policy in {"auto", "commit", "safe", "safe-auto"}:
        return "safe_auto"
    if policy in _WRITE_POLICIES:
        return policy
    return "safe_auto" if persist_findings else "none"


def _compact_json(value: Any, *, limit: int = 12000) -> str:
    try:
        text = json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _int_param(key: str, *, minimum: int = 0) -> int:
    try:
        value = int(get_config_param(key))
    except Exception:
        value = 0
    return max(value, minimum)


def _env_int_or_none(name: str, *, minimum: int = 1) -> int | None:
    raw = _clean(os.getenv(name))
    if not raw:
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    return max(value, int(minimum))


def load_aql_zopedia_llm_client(
    *,
    surface: str,
    env_prefix: str = "",
    fallback_to_default: bool = False,
) -> ZopediaLLMClient | None:
    """Load the shared product LLM through the AQL/Zopedia boundary.

    `services.zopedia_runtime` remains the provider adapter. This function is
    the product-facing entrypoint used by jobs and feature modules so LLM access
    is visibly attached to the shared engine contract.
    """
    clean_surface = _clean(surface)
    if not clean_surface:
        raise ValueError("surface is required for AQL/Zopedia LLM access")
    return load_zopedia_llm_client(
        surface=clean_surface,
        env_prefix=env_prefix,
        fallback_to_default=fallback_to_default,
    )


def run_aql_zopedia_agent(
    *,
    query: str,
    task: str = "agent_answer",
    surface: str = "zopedia.chat",
    force_refresh: bool = False,
    max_tool_calls: int | None = None,
    service: Any | None = None,
    llm_client: ZopediaLLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    persist_findings: bool = True,
    write_policy: str | None = None,
    tool_call_timeout_seconds: int | None = None,
    llm_step_timeout_seconds: int | None = None,
    prefetch_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Shared AQL/Zopedia orchestration entrypoint for tool-using research.

    The Zopedia agent implementation owns the planner/tool loop behind this AQL/Zopedia engine boundary.
    """
    from .zopedia_agent import _run_zopedia_agent_loop

    resolved_budget = max_tool_calls
    if resolved_budget is None:
        resolved_budget = _int_param(_P_AGENT_MAX_TOOL_CALLS, minimum=1)
    if tool_call_timeout_seconds is None:
        tool_call_timeout_seconds = _env_int_or_none("AQL_ZOPEDIA_AGENT_TOOL_TIMEOUT_SECONDS", minimum=1)
    if llm_step_timeout_seconds is None:
        llm_step_timeout_seconds = _env_int_or_none("AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS", minimum=1)
    if prefetch_timeout_seconds is None:
        prefetch_timeout_seconds = _env_int_or_none("AQL_ZOPEDIA_AGENT_PREFETCH_TIMEOUT_SECONDS", minimum=1)
    resolved_write_policy = _normalize_write_policy(write_policy, persist_findings=persist_findings)

    result = _run_zopedia_agent_loop(
        query=query,
        task=task,
        surface=surface,
        force_refresh=force_refresh,
        max_tool_calls=int(resolved_budget),
        service=service,
        llm_client=llm_client,
        progress_callback=progress_callback,
        conversation_history=conversation_history,
        persist_findings=persist_findings,
        write_policy=resolved_write_policy,
        tool_call_timeout_seconds=tool_call_timeout_seconds,
        llm_step_timeout_seconds=llm_step_timeout_seconds,
        prefetch_timeout_seconds=prefetch_timeout_seconds,
    )
    if not isinstance(result, dict):
        result = {"status": "failed", "answer_markdown": "", "error": "Agent returned a non-dict result."}
    result.setdefault("engine", {})
    result["engine"].update(
        {
            "name": "aql_zopedia",
            "task": _clean(task) or "agent_answer",
            "surface": _clean(surface) or "zopedia.chat",
            "write_policy": resolved_write_policy,
            "max_tool_calls": int(resolved_budget),
            "tool_call_timeout_seconds": int(tool_call_timeout_seconds) if tool_call_timeout_seconds is not None else None,
            "llm_step_timeout_seconds": int(llm_step_timeout_seconds) if llm_step_timeout_seconds is not None else None,
            "prefetch_timeout_seconds": int(prefetch_timeout_seconds) if prefetch_timeout_seconds is not None else None,
        }
    )
    return result


def _strip_json_answer_fence(value: object) -> str:
    text = _clean(value)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_aql_zopedia_structured_answer(value: object) -> dict[str, Any] | None:
    """Parse a JSON object returned by the tool-using Zopedia agent."""
    text = _strip_json_answer_fence(value)
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def _repair_structured_payload_from_agent_answer(
    *,
    answer_markdown: object,
    schema_name: str,
    schema: dict[str, Any],
    llm_client: ZopediaLLMClient | None,
    surface: str,
    task: str,
) -> dict[str, Any] | None:
    answer = _clean(answer_markdown)
    if not answer or llm_client is None:
        return None
    try:
        repaired = generate_json_via_aql_zopedia_gateway(
            llm_client=llm_client,
            surface=surface,
            purpose=f"{_clean(task) or 'structured_answer'}.repair",
            call_type="schema_repair",
            system_prompt=(
                "You repair AQL/Zopedia agent output into the requested JSON schema. "
                "Use only facts and claims present in the agent answer. Do not add new evidence, "
                "new analysis, or new source references."
            ),
            user_prompt=(
                f"Schema name: {schema_name}\n\n"
                "Agent answer to convert:\n"
                f"{answer[:16000]}\n\n"
                "Return only the JSON object matching the schema."
            ),
            schema_name=f"{schema_name}_structured_repair",
            schema=schema,
            metadata={
                "engine": "aql_zopedia",
                "task": _clean(task) or "structured_answer",
                "source": "agent_answer_repair",
            },
        )
    except Exception:
        return None
    return repaired if isinstance(repaired, dict) else None


def run_aql_zopedia_structured_agent(
    *,
    query: str,
    schema_name: str,
    schema: dict[str, Any],
    task: str,
    surface: str,
    force_refresh: bool = False,
    max_tool_calls: int | None = None,
    service: Any | None = None,
    llm_client: ZopediaLLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    persist_findings: bool = False,
    write_policy: str | None = None,
    tool_call_timeout_seconds: int | None = None,
    llm_step_timeout_seconds: int | None = None,
    prefetch_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run the shared Zopedia tool agent and require a structured JSON answer.

    This keeps product feature modules out of direct LLM calls while still
    giving them typed materialization contracts.
    """
    structured_query = (
        f"{query}\n\n"
        f"Return only a valid JSON object matching schema `{schema_name}`. "
        "Do not wrap it in commentary. Do not use markdown except inside string values.\n\n"
        "JSON schema:\n"
        f"{_compact_json(schema, limit=8000)}"
    )
    if max_tool_calls is not None and int(max_tool_calls) <= 0:
        resolved_llm = llm_client or load_aql_zopedia_llm_client(surface=surface)
        if resolved_llm is None:
            return {
                "status": "unavailable",
                "payload": None,
                "agent_result": {
                    "status": "unavailable",
                    "answer_markdown": "",
                    "tool_calls": [],
                    "confidence": "low",
                    "error": "LLM runtime is unavailable.",
                    "engine": {
                        "name": "aql_zopedia",
                        "task": _clean(task) or "structured_answer",
                        "surface": _clean(surface) or "zopedia.structured",
                        "max_tool_calls": 0,
                        "structured_direct": True,
                    },
                },
                "error": "LLM runtime is unavailable.",
            }
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "structured_direct_start",
                    "message": "Running structured AQL/Zopedia synthesis from supplied context.",
                    "progress": 0.9,
                    "tool_call_count": 0,
                }
            )
        try:
            payload = generate_json_via_aql_zopedia_gateway(
                llm_client=resolved_llm,
                surface=surface,
                purpose=f"{_clean(task) or 'structured_answer'}.direct",
                call_type="formatter_over_aql",
                system_prompt=(
                    "You are the Spectral Nature AQL/Zopedia structured synthesis engine. "
                    "Use the supplied prompt context as evidence. Do not call tools. "
                    "Return only the requested JSON object and do not invent facts."
                ),
                user_prompt=structured_query,
                schema_name=schema_name,
                schema=schema,
                metadata={
                    "engine": "aql_zopedia",
                    "task": _clean(task) or "structured_answer",
                    "max_tool_calls": 0,
                    "source": "structured_direct",
                },
            )
        except Exception as exc:
            return {
                "status": "failed",
                "payload": None,
                "agent_result": {
                    "status": "failed",
                    "answer_markdown": "",
                    "tool_calls": [],
                    "confidence": "low",
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine": {
                        "name": "aql_zopedia",
                        "task": _clean(task) or "structured_answer",
                        "surface": _clean(surface) or "zopedia.structured",
                        "max_tool_calls": 0,
                        "structured_direct": True,
                    },
                },
                "error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(payload, dict):
            payload.pop("__reasoning_content", None)
        answer_markdown = json.dumps(to_jsonable(payload), ensure_ascii=True, sort_keys=True, default=str)
        agent_result = {
            "status": "completed",
            "answer_markdown": answer_markdown,
            "tool_calls": [],
            "confidence": "medium",
            "error": "",
            "engine": {
                "name": "aql_zopedia",
                "task": _clean(task) or "structured_answer",
                "surface": _clean(surface) or "zopedia.structured",
                "max_tool_calls": 0,
                "structured_direct": True,
            },
        }
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "structured_direct_complete",
                    "message": "Structured AQL/Zopedia synthesis completed from supplied context.",
                    "progress": 0.95,
                    "tool_call_count": 0,
                }
            )
        return {"status": "completed", "payload": payload, "agent_result": agent_result}
    agent_result = run_aql_zopedia_agent(
        query=structured_query,
        task=task,
        surface=surface,
        force_refresh=force_refresh,
        max_tool_calls=max_tool_calls,
        service=service,
        llm_client=llm_client,
        progress_callback=progress_callback,
        conversation_history=conversation_history,
        persist_findings=persist_findings,
        write_policy=write_policy,
        tool_call_timeout_seconds=tool_call_timeout_seconds,
        llm_step_timeout_seconds=llm_step_timeout_seconds,
        prefetch_timeout_seconds=prefetch_timeout_seconds,
    )
    payload = parse_aql_zopedia_structured_answer(agent_result.get("answer_markdown"))
    status = _clean(agent_result.get("status"))
    repair_used = False
    if payload is None and status == "completed":
        payload = _repair_structured_payload_from_agent_answer(
            answer_markdown=agent_result.get("answer_markdown"),
            schema_name=schema_name,
            schema=schema,
            llm_client=llm_client,
            surface=surface,
            task=task,
        )
        repair_used = payload is not None
    if status == "completed" and payload is not None:
        if repair_used:
            agent_result = dict(agent_result)
            engine = dict(agent_result.get("engine") or {})
            engine["structured_repair_used"] = True
            agent_result["engine"] = engine
        return {
            "status": "completed",
            "payload": payload,
            "agent_result": agent_result,
        }
    error = _clean(agent_result.get("error"))
    if payload is None:
        error = error or "AQL/Zopedia agent did not return valid structured JSON."
    return {
        "status": status or "failed",
        "payload": None,
        "agent_result": agent_result,
        "error": error,
    }


def resolve_aql_zopedia_followup_query(
    query: str,
    conversation_history: list[dict[str, Any]] | None,
) -> tuple[str, bool]:
    from .zopedia_agent import resolve_conversation_followup_query

    return resolve_conversation_followup_query(query, conversation_history)


def repair_aql_zopedia_analysis_arguments(
    *,
    original_args: dict[str, Any],
    failure_payload: dict[str, Any],
    dataset_capabilities: dict[str, Any] | None = None,
    analysis_input_profile: dict[str, Any] | None = None,
    repair_attempt: int = 1,
    llm_client: ZopediaLLMClient | None = None,
) -> dict[str, Any] | None:
    """Repair malformed analysis tool arguments through the shared engine.

    This keeps LLM-native code repair out of feature/tool modules while giving
    `analysis.run_python` a small bounded chance to recover from generated
    syntax or input-contract errors before the product answer is synthesized.
    """
    resolved_llm = llm_client or load_aql_zopedia_llm_client(surface="analysis.run_python.repair")
    if resolved_llm is None:
        return None
    schema = {
        "type": "object",
        "properties": {
            "objective": {"type": "string"},
            "code": {"type": "string"},
            "dataset_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "alias": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["name", "alias", "params"],
                    "additionalProperties": False,
                },
            },
            "inline_datasets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "rows": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["name", "rows"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["objective", "code", "dataset_refs", "inline_datasets", "notes"],
        "additionalProperties": False,
    }
    system_prompt = (
        "Repair a failed bounded Python analysis tool call. Return only the corrected tool arguments. "
        "Do not answer the user's research question. "
        "The code must be valid multiline Python for ast.parse(..., mode='exec'). "
        "Inputs are already fetched before execution. Use pandas DataFrames from `datasets['alias']` "
        "or variables named by each alias. Do not use load_dataset, get_dataset, context, globals, "
        "open, file writes, network, subprocess, statsmodels, or unapproved imports. "
        "Do not assume columns that are not present in the input profile. "
        "When joining date or timestamp columns, normalize both sides to the same timezone-naive "
        "date or datetime dtype before merging. "
        "Use add_metric, add_table, add_chart, or result to expose outputs. "
        "Every dataset_ref must be an object with name, alias, and params. "
        "Use explicit aliases when the same dataset is requested more than once. "
        "If a prior repair failed, fix that exact new failure rather than repeating the same shape."
    )
    user_prompt = json.dumps(
        {
            "original_arguments": original_args,
            "failure": {
                "status": failure_payload.get("status"),
                "error": failure_payload.get("error"),
                "metadata": failure_payload.get("metadata"),
                "llm_context_text": failure_payload.get("llm_context_text"),
                "input_refs": failure_payload.get("input_refs"),
            },
            "available_dataset_capabilities": dataset_capabilities or {},
            "actual_input_profile": analysis_input_profile or {},
            "repair_attempt": repair_attempt,
        },
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    try:
        result = generate_json_via_aql_zopedia_gateway(
            llm_client=resolved_llm,
            surface="analysis.run_python.repair",
            purpose="analysis_argument_repair",
            call_type="schema_repair",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="zopedia_analysis_repair",
            schema=schema,
            metadata={
                "engine": "aql_zopedia",
                "tool": "analysis.run_python",
                "repair_attempt": repair_attempt,
            },
        )
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def run_aql_zopedia_page_summary_agent(
    *,
    surface: str,
    query: str,
    force_refresh: bool = False,
    llm_client: ZopediaLLMClient | None = None,
    progress_callback: ProgressCallback | None = None,
    persist_findings: bool = False,
    write_policy: str | None = None,
) -> dict[str, Any]:
    return run_aql_zopedia_agent(
        query=query,
        task="page_summary",
        surface=surface,
        force_refresh=force_refresh,
        max_tool_calls=_int_param(_P_PAGE_SUMMARY_MAX_TOOL_CALLS, minimum=1),
        llm_client=llm_client,
        progress_callback=progress_callback,
        persist_findings=persist_findings,
        write_policy=write_policy,
    )


def _attention_home_engine_query(
    *,
    home_payload: dict[str, Any],
    summary_payload: dict[str, Any],
) -> str:
    return (
        "Use the shared AQL/Zopedia evidence path to verify and enrich this scheduled Attention homepage summary. "
        "Search retained evidence and Zopedia memory when useful, read relevant pages, use local market/macro datasets first, "
        "and identify meaningful evidence gaps. Do not write a new homepage article unless the evidence materially changes the summary. "
        "Return the strongest evidence-backed assessment.\n\n"
        "Draft summary JSON:\n"
        f"{_compact_json(summary_payload, limit=7000)}\n\n"
        "Attention home payload JSON:\n"
        f"{_compact_json(home_payload, limit=10000)}"
    )


def _engine_trace_frame(agent_result: dict[str, Any], *, research_scope: str) -> pd.DataFrame:
    evidence_pack = agent_result.get("aql_evidence_pack") if isinstance(agent_result, dict) else {}
    if not isinstance(evidence_pack, dict):
        evidence_pack = {}
    return pd.DataFrame(
        [
            {
                "run_id": _clean(agent_result.get("run_id")),
                "research_scope": research_scope,
                "engine_task": _clean((agent_result.get("engine") or {}).get("task"))
                if isinstance(agent_result.get("engine"), dict)
                else "",
                "status": _clean(agent_result.get("status")),
                "confidence": _clean(agent_result.get("confidence")),
                "evidence_pack_id": _clean(agent_result.get("aql_evidence_pack_id")),
                "tool_call_count": len(list(agent_result.get("tool_calls") or [])),
                "zopedia_page_count": len(list(evidence_pack.get("zopedia_pages") or [])),
                "retained_chunk_count": len(list(evidence_pack.get("retained_chunks") or [])),
                "live_evidence_count": len(list(evidence_pack.get("live_evidence") or [])),
                "citation_count": len(list(evidence_pack.get("citations") or [])),
                "limitations_json": json.dumps(list(agent_result.get("limitations") or []), ensure_ascii=True, default=str),
            }
        ]
    )


def build_aql_zopedia_attention_home_summary_with_trace(
    home_payload: dict[str, Any],
    *,
    llm_client: ZopediaLLMClient | None,
    embedding_client: Any | None = None,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
    query_service: Any | None = None,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    """Scheduled homepage summary mode of the shared AQL/Zopedia engine.

    The existing AQL summarizer still owns the materialized summary schema and
    trace frames. The shared engine adds the missing Zopedia memory/tool/evidence
    pass and attaches its evidence pack to the same product result.
    """
    from .aql.summarizer import (
        build_attention_agentic_summary_with_trace,
        build_attention_home_summary,
    )

    if llm_client is None:
        return build_attention_home_summary(home_payload), {}

    summary_payload, trace_frames = build_attention_agentic_summary_with_trace(
        home_payload,
        llm_client=llm_client,
        embedding_client=embedding_client,
        search_clients=search_clients,
        max_search_queries=max_search_queries,
        max_chars=max_chars,
        query_service=query_service,
    )
    trace_frames = dict(trace_frames or {})

    if _int_param(_P_ATTENTION_SUMMARY_EVIDENCE_ENABLED, minimum=0) <= 0:
        summary_payload["aql_zopedia_engine"] = {
            "status": "skipped",
            "reason": "Attention summary engine evidence pass is disabled by config.",
        }
        return summary_payload, trace_frames

    try:
        agent_result = run_aql_zopedia_agent(
            query=_attention_home_engine_query(home_payload=home_payload, summary_payload=summary_payload),
            task="attention_home_summary",
            surface="attention.home_summary",
            max_tool_calls=_int_param(_P_ATTENTION_SUMMARY_MAX_TOOL_CALLS, minimum=1),
            service=query_service,
            llm_client=llm_client,
            persist_findings=False,
            write_policy=_normalize_write_policy(get_config_param(_P_ATTENTION_SUMMARY_WRITE_POLICY), persist_findings=False),
        )
    except Exception as exc:
        summary_payload["aql_zopedia_engine"] = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        return summary_payload, trace_frames

    evidence_pack = agent_result.get("aql_evidence_pack") if isinstance(agent_result, dict) else {}
    summary_payload["aql_zopedia_engine"] = {
        "status": _clean(agent_result.get("status")),
        "run_id": _clean(agent_result.get("run_id")),
        "evidence_pack_id": _clean(agent_result.get("aql_evidence_pack_id")),
        "confidence": _clean(agent_result.get("confidence")),
        "tool_call_count": len(list(agent_result.get("tool_calls") or [])),
        "limitations": list(agent_result.get("limitations") or [])[:8],
        "answer_markdown": _clean(agent_result.get("answer_markdown"))[:4000],
        "evidence_pack": evidence_pack if isinstance(evidence_pack, dict) else {},
    }
    trace_frames["aql_zopedia_engine_runs"] = _engine_trace_frame(
        agent_result,
        research_scope="home_summary",
    )
    return summary_payload, trace_frames


def attach_aql_zopedia_summary_audio(
    summary_payload: dict[str, object],
    *,
    tts_config: ElevenLabsTTSConfig | None = None,
    tts_client: ElevenLabsTTSClient | None = None,
) -> dict[str, Any]:
    """Attach ElevenLabs audio through the shared engine contract."""
    payload = dict(summary_payload or {})
    audio_text = _clean(payload.get("audio_text"))
    if not audio_text:
        return payload

    resolved_config = tts_config or load_elevenlabs_tts_config()
    if resolved_config is None:
        return payload

    client = tts_client or ElevenLabsTTSClient(resolved_config)
    audio_bytes = client.synthesize(audio_text)
    payload.update(
        {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "audio_text_hash": hashlib.sha256(audio_text.encode("utf-8")).hexdigest(),
            "audio_mime_type": audio_mime_type(resolved_config.output_format),
            "audio_file_extension": audio_file_extension(resolved_config.output_format),
            "voice_id": resolved_config.voice_id,
            "model_id": resolved_config.model_id,
            "output_format": resolved_config.output_format,
            "aql_zopedia_audio": {
                "engine": "aql_zopedia",
                "provider": "elevenlabs",
                "text_hash": hashlib.sha256(audio_text.encode("utf-8")).hexdigest(),
            },
        }
    )
    return payload


__all__ = [
    "attach_aql_zopedia_summary_audio",
    "build_aql_zopedia_attention_home_summary_with_trace",
    "load_aql_zopedia_llm_client",
    "parse_aql_zopedia_structured_answer",
    "resolve_aql_zopedia_followup_query",
    "run_aql_zopedia_agent",
    "run_aql_zopedia_page_summary_agent",
    "run_aql_zopedia_structured_agent",
]
