from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from .elevenlabs_tts import (
    ElevenLabsTTSClient,
    ElevenLabsTTSConfig,
    audio_file_extension,
    audio_mime_type,
    load_elevenlabs_tts_config,
)
from .json_utils import to_jsonable
from .llm_context import serialize_llm_context
from .llm_execution import LLMContextWindowExceeded, checkpoint_agent_research, checkpoint_structured_research
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
_P_AGENT_RUN_TIMEOUT = register_config_param(
    "Engine agent run timeout seconds",
    group="AQL / Zopedia Engine",
    default=300,
    description="Whole research budget, including preparation, synthesis and review.",
)
_P_TOOL_CALL_TIMEOUT_SECONDS = register_config_param(
    "Agent tool call timeout seconds",
    group="Zopedia",
    default=45,
    description="Maximum seconds for one shared research tool call.",
)
_P_LLM_STEP_TIMEOUT_SECONDS = register_config_param(
    "Agent LLM step timeout seconds",
    group="Zopedia",
    default=90,
    description="Maximum seconds for one shared research model step.",
)


@lru_cache(maxsize=1)
def research_engine_revision() -> str:
    """Identify the actual shared engine code loaded by an API or worker."""
    digest = hashlib.sha256()
    for name in (
        "aql_zopedia_engine.py",
        "aql/business_model_stack.py",
        "zopedia_agent.py",
        "zopedia_analysis.py",
        "zopedia_research.py",
        "agent_tools.py",
        "page_browsing.py",
        "agents/research_session.py",
        "aql_zopedia_gateway.py",
        "aql/evidence_pack.py",
        "llm_context.py",
        "llm_execution.py",
        "zopedia_runtime.py",
        "llm.py",
        "saa/storage.py",
        "saa/semantic_index.py",
        "pipeline_store.py",
        "database.py",
        "../data_access/query_service.py",
        "../data_access/contracts.py",
        "../presentation/plotly.py",
        "../presentation/visualization_artifacts.py",
        "agents/research_runs.py",
    ):
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:20]


def resolve_research_budget(**overrides: Any) -> dict[str, int | float]:
    """Resolve execution settings once for every caller, including queued chat."""
    settings = {
        "max_tool_calls": _int_param(_P_AGENT_MAX_TOOL_CALLS),
        "run_timeout_seconds": _env_int_or_none("AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS")
        or _int_param(_P_AGENT_RUN_TIMEOUT, minimum=1),
        "tool_call_timeout_seconds": _env_int_or_none(
            "AQL_ZOPEDIA_AGENT_TOOL_TIMEOUT_SECONDS"
        )
        or _int_param(_P_TOOL_CALL_TIMEOUT_SECONDS, minimum=1),
        "llm_step_timeout_seconds": _env_int_or_none(
            "AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS"
        )
        or _int_param(_P_LLM_STEP_TIMEOUT_SECONDS, minimum=1),
        "prefetch_timeout_seconds": _env_int_or_none(
            "AQL_ZOPEDIA_AGENT_PREFETCH_TIMEOUT_SECONDS"
        )
        or 15,
    }
    return {
        key: overrides.get(key) if overrides.get(key) is not None else value
        for key, value in settings.items()
    }


_P_PAGE_SUMMARY_MAX_TOOL_CALLS = register_config_param(
    "Engine page summary max tool calls",
    group="AQL / Zopedia Engine",
    default=10,
    description="Tool budget for page summary evidence collection.",
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
    # Preserve all model context; share exact repeated values by reference.
    return serialize_llm_context(value)


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


@checkpoint_agent_research
def run_aql_zopedia_agent(
    *,
    query: str,
    run_id: str | None = None,
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
    run_timeout_seconds: float | None = None,
    initial_evidence: list[dict[str, Any]] | None = None,
    response_schema: dict[str, Any] | None = None,
    response_schema_name: str | None = None,
    request_context: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
    answer_review_owner: str = "agent",
    checkpoint_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Shared AQL/Zopedia orchestration entrypoint for tool-using research.

    The Zopedia agent implementation owns the planner/tool loop behind this AQL/Zopedia engine boundary.
    """
    from .zopedia_agent import _run_zopedia_agent_loop

    settings = resolve_research_budget(
        max_tool_calls=max_tool_calls,
        run_timeout_seconds=run_timeout_seconds,
        tool_call_timeout_seconds=tool_call_timeout_seconds,
        llm_step_timeout_seconds=llm_step_timeout_seconds,
        prefetch_timeout_seconds=prefetch_timeout_seconds,
    )
    resolved_budget = int(settings["max_tool_calls"])
    run_timeout_seconds = float(settings["run_timeout_seconds"])
    tool_call_timeout_seconds = int(settings["tool_call_timeout_seconds"])
    llm_step_timeout_seconds = int(settings["llm_step_timeout_seconds"])
    prefetch_timeout_seconds = int(settings["prefetch_timeout_seconds"])
    resolved_write_policy = _normalize_write_policy(write_policy, persist_findings=persist_findings)

    result = _run_zopedia_agent_loop(
        query=query,
        run_id=run_id,
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
        run_timeout_seconds=run_timeout_seconds,
        initial_evidence=initial_evidence,
        response_schema=response_schema,
        response_schema_name=response_schema_name,
        request_context=request_context,
        resume_state=resume_state,
        answer_review_owner=answer_review_owner,
        checkpoint_callback=checkpoint_callback,
    )
    if not isinstance(result, dict):
        result = {
            "status": "failed",
            "answer_markdown": "",
            "error": "Agent returned a non-dict result.",
        }
    result.setdefault("engine", {})
    result["engine"].update(
        {
            "name": "aql_zopedia",
            "revision": research_engine_revision(),
            "task": _clean(task) or "agent_answer",
            "surface": _clean(surface) or "zopedia.chat",
            "write_policy": resolved_write_policy,
            "max_tool_calls": int(resolved_budget),
            "tool_call_timeout_seconds": (
                int(tool_call_timeout_seconds)
                if tool_call_timeout_seconds is not None
                else None
            ),
            "llm_step_timeout_seconds": (
                int(llm_step_timeout_seconds)
                if llm_step_timeout_seconds is not None
                else None
            ),
            "prefetch_timeout_seconds": (
                int(prefetch_timeout_seconds)
                if prefetch_timeout_seconds is not None
                else None
            ),
            "run_timeout_seconds": run_timeout_seconds,
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
    deadline_monotonic: float,
    original_request: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    answer = _clean(answer_markdown)
    if not answer or llm_client is None or time.monotonic() >= deadline_monotonic:
        return None
    parsed_answer = parse_aql_zopedia_structured_answer(answer)
    violations = [
        {"path": list(error.absolute_path), "rule": error.validator, "expected": error.validator_value}
        for error in Draft202012Validator(schema).iter_errors(parsed_answer)
    ] if parsed_answer is not None else []
    try:
        repaired = generate_json_via_aql_zopedia_gateway(
            llm_client=llm_client,
            surface=surface,
            purpose=f"{_clean(task) or 'structured_answer'}.repair",
            call_type="schema_repair",
            inference_effort="none",
            literal_output=True,
            system_prompt=(
                "You repair AQL/Zopedia agent output into the requested JSON schema. "
                "Use only facts and claims present in the agent answer. Do not add new evidence, "
                "new analysis, or new source references. Preserve existing strings, numbers, arrays "
                "and source references exactly wherever the schema permits. Change only the fields "
                "or types required to satisfy the schema; do not rewrite or shorten prose."
                + (" Use the original request and its collected evidence to interpret field meanings and recover the intended task. "
                   "Preserve the research goal; do not substitute a goal about repairing output or turn intermediate protocol arguments into artifact content. "
                   "Do not invent placeholders or unsupported facts to fill required fields." if original_request else "")
            ),
            user_prompt=(
                f"Schema name: {schema_name}\n\n"
                + (f"Original request and collected evidence:\n{serialize_llm_context(original_request)}\n\n" if original_request else "")
                +
                "Agent answer to convert:\n"
                f"{answer}\n\n"
                "Schema violations to correct:\n"
                f"{json.dumps(violations, ensure_ascii=False, default=str)}\n\n"
                "Return only the JSON object matching the schema."
            ),
            schema_name=f"{schema_name}_structured_repair",
            schema=schema,
            deadline_monotonic=deadline_monotonic,
            metadata={
                "engine": "aql_zopedia",
                "task": _clean(task) or "structured_answer",
                "source": "agent_answer_repair",
            },
        )
    except Exception:
        return None
    if isinstance(repaired, dict):
        repaired = {key: value for key, value in repaired.items() if not key.startswith("__")}
        if not list(Draft202012Validator(schema).iter_errors(repaired)):
            return repaired
    return None


@checkpoint_structured_research
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
    run_timeout_seconds: float | None = None,
    initial_evidence: list[dict[str, Any]] | None = None,
    call_type: str = "formatter_over_aql",
    review_answer: bool = False,
    reuse_scope: str = "",
    research_subject: str = "",
    inference_effort: str = "",
    answer_review_owner: str = "agent",
    reuse_identity: str = "",
    retained_partial_payload: dict[str, Any] | None = None,
    retained_partial_text: str = "",
    artifact_context: dict[str, Any] | None = None,
    retained_agent_result: dict[str, Any] | None = None,
    instructions: str = "",
    operation_metadata: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
    result_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the shared Zopedia tool agent and require a structured JSON answer.

    This keeps product feature modules out of direct LLM calls while still
    giving them typed materialization contracts.
    """
    budget = resolve_research_budget(llm_step_timeout_seconds=llm_step_timeout_seconds)
    timeout = run_timeout_seconds if run_timeout_seconds is not None else budget["run_timeout_seconds"]
    if run_timeout_seconds is None and max_tool_calls is not None and int(max_tool_calls) <= 0 and llm_step_timeout_seconds is not None:
        timeout = min(float(timeout), float(llm_step_timeout_seconds))
    deadline = time.monotonic() + float(timeout)
    original_request = {"query": query, "instructions": instructions, "initial_evidence": initial_evidence,
                        "artifact_context": artifact_context, "conversation_history": conversation_history}
    structured_query = (
        (f"Task instructions:\n{instructions}\n\n" if instructions else "")
        + query
    )
    if max_tool_calls is not None and int(max_tool_calls) <= 0 and review_answer:
        # This no-tool editor already owns the artifact contract. Tool-using
        # planners own the research-step contract; synthesis receives the full
        # artifact schema separately through response_schema.
        structured_query += (
            f"\n\nReturn only a valid JSON object matching schema `{schema_name}`. "
            "Do not wrap it in commentary. Do not use markdown except inside string values.\n\n"
            "JSON schema:\n"
            f"{_compact_json(schema, limit=8000)}"
        )
    if max_tool_calls is not None and int(max_tool_calls) <= 0 and not review_answer:
        structured_query = query
    if initial_evidence and max_tool_calls is not None and int(max_tool_calls) <= 0 and not review_answer:
        structured_query += "\nSupplied evidence:\n" + serialize_llm_context(initial_evidence)
    if max_tool_calls is not None and int(max_tool_calls) <= 0 and not review_answer:
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
                        "revision": research_engine_revision(),
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
        payload = retained_partial_payload
        partial_text = retained_partial_text
        context_reads: list[dict[str, Any]] = []
        working_context: dict[str, Any] = {}
        try:
            if payload is None and partial_text:
                payload = parse_aql_zopedia_structured_answer(partial_text)
                if payload is None:
                    payload = _repair_structured_payload_from_agent_answer(
                        answer_markdown=partial_text, schema_name=schema_name, schema=schema,
                        llm_client=resolved_llm, surface=surface, task=task, deadline_monotonic=deadline,
                        original_request=original_request,
                    )
                if payload is None:
                    raise ValueError("Retained structured draft could not be repaired.")
            if payload is None:
                allowed_sources = (request_context or {}).get("allowed_source_access")
                supplied_allowed = allowed_sources is None or "supplied" in allowed_sources
                read_supplied = bool(service is not None and getattr(service, "supplied_evidence", {}) and supplied_allowed)
                direct_system = instructions or (
                    "You are the Spectral Nature AQL/Zopedia structured synthesis engine. "
                    + ("Use the supplied context and its evidence reader. " if read_supplied else "Use the supplied prompt context as evidence. Do not call tools. ")
                    + "Return only the requested JSON object and do not invent facts."
                )
                def direct_call():
                    return generate_json_via_aql_zopedia_gateway(
                        llm_client=resolved_llm,
                        surface=surface,
                        purpose=f"{_clean(task) or 'structured_answer'}.direct",
                        call_type=call_type,
                        system_prompt=direct_system,
                        user_prompt=structured_query,
                        schema_name=schema_name,
                        schema=schema,
                        deadline_monotonic=deadline,
                        metadata={
                            **(operation_metadata or {}),
                            "engine": "aql_zopedia",
                            "task": _clean(task) or "structured_answer",
                            "max_tool_calls": 0,
                            "source": (
                                "retained_partial_repair"
                                if retained_partial_payload
                                else "structured_direct"
                            ),
                        },
                    )
                capacity_feedback = ""
                if not read_supplied:
                    try:
                        payload = direct_call()
                    except LLMContextWindowExceeded as exc:
                        capacity_feedback = str(exc)
                if read_supplied or capacity_feedback:
                    from data_access.query_service import QueryService
                    from .zopedia_agent import _run_evidence_model_step, _tool_history_prompt

                    payload = _run_evidence_model_step(
                        llm_client=resolved_llm, service=service or QueryService.from_environment(),
                        tool_calls=context_reads, context_state=working_context,
                        prompt=lambda visible: structured_query + "\n\nRead evidence:\n" + _tool_history_prompt(visible),
                        system_prompt=direct_system, schema_name=schema_name, schema=schema,
                        surface=surface, purpose=f"{_clean(task) or 'structured_answer'}.direct",
                        deadline_monotonic=deadline, metadata=operation_metadata or {},
                        progress_callback=progress_callback, capacity_feedback=capacity_feedback, call_type=call_type,
                        query=query,
                        supplied_evidence_allowed=supplied_allowed,
                    )
            payload = {key: value for key, value in payload.items() if not key.startswith("__")}
            errors = list(Draft202012Validator(schema).iter_errors(payload))
            if errors:
                # JSON syntax and JSON schema are separate contracts. Preserve
                # the complete model answer for the same bounded conversion
                # used by tool-backed research, rather than losing its content.
                repaired = _repair_structured_payload_from_agent_answer(
                    answer_markdown=json.dumps(to_jsonable(payload), ensure_ascii=True, default=str),
                    schema_name=schema_name,
                    schema=schema,
                    llm_client=resolved_llm,
                    surface=surface,
                    task=task,
                    deadline_monotonic=deadline,
                    original_request=original_request,
                )
                if repaired is None:
                    raise ValueError("Structured response violated its schema: " + errors[0].message)
                payload = repaired
                structured_repair_used = True
            else:
                structured_repair_used = False
        except Exception as exc:
            partial_text = getattr(exc, "raw_content", "") or partial_text
            if isinstance(payload, dict):
                partial_text = json.dumps(to_jsonable(payload), ensure_ascii=True, default=str)
            return {
                "status": "failed",
                "payload": None,
                "agent_result": {
                    "status": "failed",
                    "answer_markdown": partial_text,
                    "tool_calls": context_reads,
                    "working_context": working_context,
                    "confidence": "low",
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine": {
                        "name": "aql_zopedia",
                        "revision": research_engine_revision(),
                        "task": _clean(task) or "structured_answer",
                        "surface": _clean(surface) or "zopedia.structured",
                        "max_tool_calls": 0,
                        "structured_direct": True,
                    },
                },
                "partial_payload": payload if isinstance(payload, dict) else None,
                "partial_text": partial_text,
                "parse_error": (
                    str(exc.__cause__)
                    if isinstance(exc.__cause__, json.JSONDecodeError)
                    else None
                ),
                "error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(payload, dict):
            payload.pop("__reasoning_content", None)
        answer_markdown = json.dumps(to_jsonable(payload), ensure_ascii=True, sort_keys=True, default=str)
        agent_result = {
            "status": "completed",
            "model": str(getattr(getattr(resolved_llm, "config", None), "model", "")),
            "provider": str(
                getattr(getattr(resolved_llm, "config", None), "provider", "")
            ),
            "answer_markdown": answer_markdown,
            "tool_calls": context_reads,
            "working_context": working_context,
            "confidence": "medium",
            "error": "",
            "engine": {
                "name": "aql_zopedia",
                "revision": research_engine_revision(),
                "task": _clean(task) or "structured_answer",
                "surface": _clean(surface) or "zopedia.structured",
                "max_tool_calls": 0,
                "structured_direct": True,
                "structured_repair_used": structured_repair_used,
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
    retained_answer = (retained_agent_result or {}).get("answer_markdown") or ""
    if retained_answer:
        payload = parse_aql_zopedia_structured_answer(retained_answer)
        if payload is None or list(Draft202012Validator(schema).iter_errors(payload)):
            llm_client = llm_client or load_aql_zopedia_llm_client(surface="zopedia.agent")
            payload = _repair_structured_payload_from_agent_answer(
                answer_markdown=retained_answer, schema_name=schema_name, schema=schema,
                llm_client=llm_client, surface=surface, task=task, deadline_monotonic=deadline,
                original_request={**original_request, "collected_evidence": (retained_agent_result or {}).get("aql_evidence_pack")})
        if payload is not None:
            return {"status": "completed", "payload": payload,
                    "agent_result": retained_agent_result, "retained_draft_recovered": True}
    # Preserve the agent's existing default client and share it with recovery.
    # Valid retained answers above need no inference client.
    llm_client = llm_client or load_aql_zopedia_llm_client(surface="zopedia.agent")

    def checkpoint(state: dict[str, Any]) -> None:
        payload = parse_aql_zopedia_structured_answer(state.get("draft_answer"))
        if payload is not None and list(Draft202012Validator(schema).iter_errors(payload)):
            payload = None
        if not state.get("draft_answer") and not state.get("tool_calls"):
            return
        # Hand completed model work to the caller before optional review or
        # checkpoint persistence can consume the remaining execution time.
        agent = deepcopy({key: state.get(key) for key in (
            "run_id", "tool_calls", "confidence", "research_state", "quality_review",
        )})
        agent.update(
            status="completed" if payload is not None else "partial",
            answer_markdown=state.get("draft_answer") or "",
            model=str(llm_client.config.model), provider=str(llm_client.config.provider),
            query=structured_query, original_query=query,
            engine={"name": "aql_zopedia", "revision": research_engine_revision(),
                    "task": task, "surface": surface,
                    "max_tool_calls": resolve_research_budget(max_tool_calls=max_tool_calls)["max_tool_calls"]},
            runtime={"returned_from_checkpoint": True},
        )
        result_callback({"status": agent["status"], "payload": payload, "agent_result": agent})

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
        request_context=request_context,
        persist_findings=persist_findings,
        write_policy=write_policy,
        tool_call_timeout_seconds=tool_call_timeout_seconds,
        llm_step_timeout_seconds=llm_step_timeout_seconds,
        prefetch_timeout_seconds=prefetch_timeout_seconds,
        run_timeout_seconds=max(deadline - time.monotonic(), 0),
        initial_evidence=initial_evidence,
        response_schema=schema,
        response_schema_name=schema_name,
        answer_review_owner=answer_review_owner,
        checkpoint_callback=checkpoint if result_callback is not None else None,
        resume_state=(
            resume_state
            if resume_state is not None
            else (retained_agent_result or {}).get("continuation_state")
        ),
    )
    payload = parse_aql_zopedia_structured_answer(agent_result.get("answer_markdown"))
    if payload is not None and list(Draft202012Validator(schema).iter_errors(payload)):
        payload = None
    status = _clean(agent_result.get("status"))
    # Review refines research; its verdict is not an admission rule. Preserve a
    # valid answer and the actual review state, including incomplete review.
    repair_used = False
    if (payload is None and agent_result.get("answer_markdown")
            and not agent_result.get("structured_repair_attempted")):
        payload = _repair_structured_payload_from_agent_answer(
            answer_markdown=agent_result.get("answer_markdown"),
            schema_name=schema_name,
            schema=schema,
            llm_client=llm_client,
            surface=surface,
            task=task,
            deadline_monotonic=deadline,
            original_request={**original_request, "collected_evidence": agent_result.get("aql_evidence_pack")},
        )
        repair_used = payload is not None
    if payload is not None:
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
        "status": "failed" if status == "completed" else status or "failed",
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
    "load_aql_zopedia_llm_client",
    "parse_aql_zopedia_structured_answer",
    "resolve_aql_zopedia_followup_query",
    "run_aql_zopedia_agent",
    "run_aql_zopedia_page_summary_agent",
    "run_aql_zopedia_structured_agent",
]
