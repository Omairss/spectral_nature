from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import pandas as pd
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
from .llm_execution import checkpoint_agent_research, checkpoint_structured_research
from .llm import get_config_param, register_config_param
from .llm_flow import configured_research, CHAT_KNOWLEDGE, resolve_research_client
from .zopedia_runtime import ZopediaLLMClient, load_zopedia_llm_client


ProgressCallback = Callable[[dict[str, object]], None]

_WRITE_POLICIES = {"none", "propose", "safe_auto"}


_P_AGENT_MAX_TOOL_CALLS = register_config_param(
    "Engine agent max tool calls",
    group="AQL / Zopedia Engine",
    stage="researcher",
    default=10,
    description="Default tool budget for direct AQL/Zopedia agent runs.",
    label="Tool calls",
)
_P_AGENT_RUN_TIMEOUT = register_config_param(
    "Engine agent run timeout seconds",
    group="AQL / Zopedia Engine",
    stage="researcher",
    default=300,
    description="Whole research budget, including preparation, synthesis and review.",
    label="Whole run (seconds)",
    environment_variable="AQL_ZOPEDIA_AGENT_RUN_TIMEOUT_SECONDS",
    minimum=1,
)
_P_TOOL_CALL_TIMEOUT_SECONDS = register_config_param(
    "Agent tool call timeout seconds",
    group="Zopedia",
    stage="researcher",
    default=45,
    description="Maximum seconds for one shared research tool call.",
    label="Tool timeout (seconds)", section="Step budgets",
    environment_variable="AQL_ZOPEDIA_AGENT_TOOL_TIMEOUT_SECONDS",
    minimum=1,
)
_P_LLM_STEP_TIMEOUT_SECONDS = register_config_param(
    "Agent LLM step timeout seconds",
    group="Zopedia",
    stage="researcher",
    default=90,
    description="Maximum seconds for one shared research model step.",
    label="Model step timeout (seconds)", section="Step budgets",
    environment_variable="AQL_ZOPEDIA_AGENT_LLM_STEP_TIMEOUT_SECONDS",
    minimum=1,
)


@lru_cache(maxsize=1)
def research_engine_revision() -> str:
    """Identify the actual shared engine code loaded by an API or worker."""
    digest = hashlib.sha256()
    for name in (
        "aql_zopedia_engine.py",
        "zopedia_agent.py",
        "zopedia_research.py",
        "agent_tools.py",
        "zopedia_analysis.py",
        "page_browsing.py",
        "agents/research_session.py",
        "aql_zopedia_gateway.py",
        "aql/evidence_pack.py",
        "llm_context.py",
        "llm_execution.py",
        "zopedia_runtime.py",
        "llm.py",
        "llm_flow.py",
        "aql/evidence_pack.py",
        "saa/storage.py",
        "saa/semantic_index.py",
        "pipeline_store.py",
        "agents/research_runs.py",
    ):
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:20]


def resolve_research_budget(**overrides: Any) -> dict[str, int | float]:
    """Resolve execution settings once for every caller, including queued chat."""
    settings = {
        "max_tool_calls": _int_param(_P_AGENT_MAX_TOOL_CALLS),
        "run_timeout_seconds": _int_param(_P_AGENT_RUN_TIMEOUT, minimum=1),
        "tool_call_timeout_seconds": _int_param(_P_TOOL_CALL_TIMEOUT_SECONDS, minimum=1),
        "llm_step_timeout_seconds": _int_param(_P_LLM_STEP_TIMEOUT_SECONDS, minimum=1),
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
    stage="researcher",
    default=10,
    description="Tool budget for page summary evidence collection.",
    label="Tool calls", section="Page summaries",
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
    raise ValueError(f"Invalid knowledge policy: {value}")


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
    return resolve_research_client(lambda: load_zopedia_llm_client(
        surface=clean_surface,
        env_prefix=env_prefix,
        fallback_to_default=fallback_to_default,
    ))


@configured_research
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
    output_stage: str = "synthesis",
    initial_answer: str = "",
    review_instructions: str = "",
) -> dict[str, Any]:
    """Shared AQL/Zopedia orchestration entrypoint for tool-using research.

    The Zopedia agent implementation owns the planner/tool loop behind this AQL/Zopedia engine boundary.
    """
    if initial_evidence is not None and (
        not isinstance(initial_evidence, list) or any(not isinstance(item, dict) for item in initial_evidence)
    ):
        raise ValueError("initial_evidence must be a list of source objects.")
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
    if write_policy is None and surface == "zopedia.chat":
        write_policy = str(get_config_param(CHAT_KNOWLEDGE))
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
        output_stage=output_stage,
        initial_answer=initial_answer,
        review_instructions=review_instructions,
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
) -> dict[str, Any] | None:
    answer = _clean(answer_markdown)
    if not answer or time.monotonic() >= deadline_monotonic:
        return None
    llm_client = llm_client or load_aql_zopedia_llm_client(surface=surface)
    if llm_client is None:
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
            ),
            user_prompt=(
                f"Schema name: {schema_name}\n\n"
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


@configured_research
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
    stage: str = "synthesis",
    initial_answer: dict[str, Any] | None = None,
    review_instructions: str = "",
) -> dict[str, Any]:
    """Run the shared Zopedia tool agent and require a structured JSON answer.

    This keeps product feature modules out of direct LLM calls while still
    giving them typed materialization contracts.
    """
    if initial_evidence is not None and (
        not isinstance(initial_evidence, list) or any(not isinstance(item, dict) for item in initial_evidence)
    ):
        raise ValueError("initial_evidence must be a list of source objects.")
    if initial_answer is not None:
        errors = list(Draft202012Validator(schema).iter_errors(initial_answer))
        if errors:
            raise ValueError("The supplied analysis does not match its artifact schema: " + errors[0].message)
        review_answer = True
    budget = resolve_research_budget(llm_step_timeout_seconds=llm_step_timeout_seconds)
    timeout = run_timeout_seconds if run_timeout_seconds is not None else budget["run_timeout_seconds"]
    if run_timeout_seconds is None and max_tool_calls is not None and int(max_tool_calls) <= 0 and llm_step_timeout_seconds is not None:
        timeout = min(float(timeout), float(llm_step_timeout_seconds))
    deadline = time.monotonic() + float(timeout)
    structured_query = (
        (f"Task instructions:\n{instructions}\n\n" if instructions else "")
        + f"{query}\n\n"
        f"Return only a valid JSON object matching schema `{schema_name}`. "
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
        if resolved_llm is not None:
            from .llm_flow import stage_client
            resolved_llm, route = stage_client(resolved_llm, stage)
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
        from .llm_flow import research_workload
        _, reasoning_route = stage_client(resolved_llm, "reasoning")
        allow_deep_analysis = stage == "synthesis" and research_workload() in {"news", "knowledge"} and schema.get("type") == "object" and any(route[key] != reasoning_route[key] for key in ("model", "thinking"))
        generation_schema = schema
        analysis_policy = ""
        if allow_deep_analysis:
            generation_schema = {**schema, "properties": {**schema.get("properties", {}), "__analysis_request": {"type": "string"}}}
            analysis_policy = (
                "\nRoutine preparation uses the economical model. Set __analysis_request to a concrete unresolved analytical question only "
                "when a consequential inference, conflicting evidence or a difficult research direction warrants deeper reasoning. "
                "Use an empty string for routine extraction, organization, summary or writing from resolved findings. "
                "Do not request escalation merely because information is incomplete. Preserve useful hypotheses and task premises."
            )
        deep_analysis_used = False
        try:
            if payload is None and partial_text:
                payload = parse_aql_zopedia_structured_answer(partial_text)
                if payload is None:
                    payload = _repair_structured_payload_from_agent_answer(
                        answer_markdown=partial_text, schema_name=schema_name, schema=schema,
                        llm_client=resolved_llm, surface=surface, task=task, deadline_monotonic=deadline,
                    )
                if payload is None:
                    raise ValueError("Retained structured draft could not be repaired.")
            if payload is None:
                payload = generate_json_via_aql_zopedia_gateway(
                    llm_client=resolved_llm,
                    surface=surface,
                    purpose=f"{_clean(task) or 'structured_answer'}.direct",
                    call_type=call_type,
                    stage=stage,
                    system_prompt=(instructions or (
                        "You are the Spectral Nature AQL/Zopedia structured synthesis engine. "
                        "Use the supplied prompt context as evidence. Do not call tools. "
                        "Return only the requested JSON object and do not invent facts."
                    )) + analysis_policy,
                    user_prompt=structured_query,
                    schema_name=schema_name,
                    schema=generation_schema,
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
            analysis_request = payload.get("__analysis_request") if allow_deep_analysis else ""
            if analysis_request:
                resolved_llm = stage_client(resolved_llm, "reasoning")[0]
                resolved = generate_json_via_aql_zopedia_gateway(
                    llm_client=resolved_llm, surface=surface, purpose=f"{_clean(task) or 'structured_answer'}.deep_reasoning",
                    stage="reasoning", call_type="research_grade",
                    system_prompt=(instructions or "Resolve the analytical question against the original evidence.")
                        + "\nAssess the draft yourself. Preserve explicit task assumptions; distinguish them from external assertions. "
                          "Resolve consequential claims against their actual basis and plausible alternatives. Return the requested artifact.",
                    user_prompt=structured_query + "\n\nAnalytical question:\n" + str(analysis_request)
                        + "\n\nExisting draft to assess:\n" + serialize_llm_context({key: value for key, value in payload.items() if not key.startswith("__")}),
                    schema_name=schema_name, schema=schema, deadline_monotonic=deadline,
                    metadata={**(operation_metadata or {}), "task": task, "max_tool_calls": 0},
                )
                payload = resolved
                deep_analysis_used = True
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
                    "tool_calls": [],
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
            "tool_calls": [],
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
                "deep_analysis_used": deep_analysis_used,
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
            payload = _repair_structured_payload_from_agent_answer(
                answer_markdown=retained_answer, schema_name=schema_name, schema=schema,
                llm_client=llm_client, surface=surface, task=task, deadline_monotonic=deadline)
        if payload is not None:
            return {"status": "completed", "payload": payload,
                    "agent_result": retained_agent_result, "retained_draft_recovered": True}
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
        output_stage=stage,
        initial_answer=json.dumps(initial_answer, ensure_ascii=False, default=str) if initial_answer is not None else "",
        review_instructions=review_instructions,
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
    if payload is None and agent_result.get("answer_markdown"):
        payload = _repair_structured_payload_from_agent_answer(
            answer_markdown=agent_result.get("answer_markdown"),
            schema_name=schema_name,
            schema=schema,
            llm_client=llm_client,
            surface=surface,
            task=task,
            deadline_monotonic=deadline,
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
    """Compatibility adapter to the single homepage research operation."""
    from .aql.summarizer import build_attention_agentic_summary_with_trace

    if llm_client is None:
        return {
            "headline": "Market Summary",
            "summary_text": "",
            "summary_status": "unavailable",
        }, {}
    return build_attention_agentic_summary_with_trace(
        home_payload,
        llm_client=llm_client,
        embedding_client=embedding_client,
        search_clients=search_clients,
        max_search_queries=max_search_queries,
        max_chars=max_chars,
        query_service=query_service,
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
    "build_aql_zopedia_attention_home_summary_with_trace",
    "load_aql_zopedia_llm_client",
    "parse_aql_zopedia_structured_answer",
    "resolve_aql_zopedia_followup_query",
    "run_aql_zopedia_agent",
    "run_aql_zopedia_page_summary_agent",
    "run_aql_zopedia_structured_agent",
]
