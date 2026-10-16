"""The shared research steps, their editable policy, and per-run configuration.

The admin editor projects this contract. Execution resolves the same stage IDs;
there is no second workflow definition in the browser.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from typing import Any

from . import llm


FLOW_KEY = "flow:configuration"
_SNAPSHOT: ContextVar[dict | None] = ContextVar("research_flow_snapshot", default=None)
_ROUTE: ContextVar[dict | None] = ContextVar("research_flow_route", default=None)
_CLIENT: ContextVar[Any] = ContextVar("research_provider_client", default=None)

# Data direction and labels are owned here, alongside the executor's stage IDs.
STAGES = [
    dict(id="researcher", name="Researcher", kind="Analysis", inputs=["Question and conversation", "Sources and observations", "Review research requests"], outputs=["Research plan", "Analysis and source references"], x=5, y=40),
    dict(id="reasoning", name="Deep reasoning", kind="Analysis", inputs=["Research question", "Original evidence", "Material unresolved questions"], outputs=["Resolved analysis", "Further research direction"], x=24, y=40),
    dict(id="monitor", name="Monitor", kind="Research guidance", inputs=["Question", "Research actions and findings"], outputs=["Continue, redirect or stop guidance"], x=5, y=9),
    dict(id="search", name="Search", kind="Evidence lookup", inputs=["Research objective", "Query hints and dates"], outputs=["Queries", "Live and retained candidates"], x=5, y=78),
    dict(id="read", name="Read", kind="Evidence lookup", inputs=["Source or capture reference"], outputs=["Original text and capture coverage"], x=43, y=78),
    dict(id="relevance", name="Relevance", kind="Evidence lookup", inputs=["Research objective", "Candidate sources"], outputs=["Ranked sources and useful connections"], x=24, y=78),
    dict(id="summary", name="Summarize", kind="Evidence lookup", inputs=["Original source text"], outputs=["First-pass summary", "Original source references"], x=62, y=78),
    dict(id="synthesis", name="Synthesis", kind="Analysis", inputs=["Analysis and original evidence", "Reviewer corrections"], outputs=["Resolved analysis and answer"], x=43, y=40),
    dict(id="review", name="Reviewers", kind="Parallel review", inputs=["Same draft and original evidence"], outputs=["Corrections", "Research requests"], x=65, y=40),
    dict(id="answer", name="Answer", kind="Presentation", inputs=["Completed answer and sources"], outputs=["User answer"], inference=False, x=87, y=40),
    dict(id="knowledge", name="Knowledge updates", kind="Memory", inputs=["Resolved findings and provenance", "Authenticated write policy"], outputs=["Audited updates or proposals"], x=83, y=78),
    dict(id="repair", name="Output repair", kind="Contract recovery", inputs=["Existing model output", "Requested schema"], outputs=["Same content in the required schema"], auxiliary=True),
]
EDGES = [
    dict(source="researcher", target="reasoning", label="Deeper analysis"),
    dict(source="reasoning", target="researcher", label="Analysis and direction"),
    dict(source="reasoning", target="synthesis", label="Resolved findings"),
    dict(source="researcher", target="monitor", label="Progress"),
    dict(source="monitor", target="researcher", label="Guidance"),
    dict(source="researcher", target="search", label="Research requests"),
    dict(source="search", target="relevance", label="Candidate sources"),
    dict(source="relevance", target="researcher", label="Ranked candidates"),
    dict(source="researcher", target="read", label="Sources to read"),
    dict(source="read", target="summary", label="Captured text"),
    dict(source="summary", target="researcher", label="Sources + observations"),
    dict(source="researcher", target="synthesis", label="Findings + originals"),
    dict(source="synthesis", target="review", label="Draft"),
    dict(source="review", target="synthesis", label="Corrections"),
    dict(source="review", target="researcher", label="More research"),
    dict(source="synthesis", target="answer", label="Resolved answer"),
    dict(source="answer", target="knowledge", label="Findings"),
    dict(source="knowledge", target="search", label="Retained knowledge"),
]

REVIEW_CYCLES = llm.register_config_param("Review cycles", "Research flow", 1, "Draft review, follow-up and application of corrections per cycle.", stage="review", maximum=8, label="Maximum review cycles")
MONITOR_CADENCE = llm.register_config_param("Monitor every tool calls", "Research flow", 2, "Check after this many research actions; zero leaves checks to the researcher.", stage="monitor", maximum=50)
SEARCH_QUERIES = llm.register_config_param("Search queries per lookup", "Research flow", 5, "Maximum expanded queries in one evidence lookup.", stage="search", minimum=1, maximum=30, label="Retained queries per lookup")
SEARCH_RESULTS = llm.register_config_param("Search results per query", "Research flow", 40, "Candidates acquired for each query.", stage="search", minimum=1, maximum=100, label="Retained results per query")
SEARCH_PARALLEL = llm.register_config_param("Parallel search queries", "Research flow", 4, "Concurrent queries within one lookup.", stage="search", minimum=1, maximum=8, label="Parallel retained queries")
MONITOR_FREEDOM = llm.register_config_param("Research freedom", "Research flow", "balanced", "How freely research can follow adjacent leads.", choices=("focused", "balanced", "exploratory"), stage="monitor")
CHAT_KNOWLEDGE = llm.register_config_param("Chat knowledge policy", "Research flow", "none", "Server-authorized maximum; the request may narrow it.", choices=("none", "propose", "safe_auto"), stage="knowledge")
DEEP_REASONING_PROMPT = llm.register_narrative_prompt(
    name="Deep reasoning", file="services/llm_flow.py", group="Research flow", stage="reasoning",
    prompt=(
        "Resolve the analytical question against original evidence and the user's task premises. "
        "Assess earlier model interpretations and reviewer suggestions yourself. Preserve useful hypotheses, "
        "distinguish observations from their possible explanations, and choose further research when it would help. "
        "Ground recommendations in the user's objective and the available evidence."
    ),
)

# Producer controls use the same saved settings and run snapshot as the agent.
# Defaults match the existing worker inputs; registering them does not change them.
WORKLOADS = {
    "interactive": "Ad hoc and research", "home": "Home", "trading": "Trading Agent",
    "news": "News and companies", "knowledge": "Knowledge and macro",
}
_WORKLOAD_CONTROLS = {
    "trading": [
        ("candidates", "Recommendations per horizon", 4, "TRADING_AGENT_CANDIDATE_LIMIT", "synthesis", "Output", 1),
        ("shortlist", "Shortlist per horizon", 6, "TRADING_AGENT_SCOUT_LIMIT_PER_HORIZON", "researcher", "Coverage", 1),
        ("research_pool", "Companies researched", 8, "TRADING_AGENT_RESEARCH_POOL_LIMIT", "researcher", "Coverage", 1),
        ("tool_calls", "Research actions per company", 4, "TRADING_AGENT_RESEARCH_MAX_TOOL_CALLS", "researcher", "Depth", 1),
        ("attempts", "Research attempts", 2, "TRADING_AGENT_RESEARCH_ATTEMPTS", "researcher", "Recovery", 1),
        ("parallelism", "Parallel company research", 2, "TRADING_AGENT_RESEARCH_PARALLELISM", "researcher", "Execution", 1),
    ],
    "news": [
        ("companies", "Company investigations", 25, "ZOPEDIA_TICKER_BUSINESS_STACK_LIMIT", "researcher", "Coverage", 1),
        ("stories", "News interpretations", 12, "ZOPEDIA_NEWS_BUSINESS_LIMIT", "synthesis", "Coverage", 1),
        ("queries", "Queries per company", 24, "ZOPEDIA_TICKER_BUSINESS_RESEARCH_QUERY_LIMIT", "search", "Company search", 1),
        ("results", "Results per company query", 4, "ZOPEDIA_TICKER_BUSINESS_RESEARCH_RESULTS_PER_QUERY", "search", "Company search", 1),
        ("page_opens", "Pages opened per company query", 2, "ZOPEDIA_TICKER_BUSINESS_OPEN_PAGES_PER_QUERY", "read", "Company reading", 1),
        ("open_attempts", "Page open attempts per query", 4, "ZOPEDIA_TICKER_BUSINESS_OPEN_ATTEMPTS_PER_QUERY", "read", "Company reading", 1),
        ("page_characters", "Company source read characters", 8000, "ZOPEDIA_TICKER_BUSINESS_OPEN_PAGE_CHARS", "read", "Company reading", 1200),
        ("research_seconds", "Company research time (seconds)", 3600, "ZOPEDIA_TICKER_BUSINESS_RESEARCH_BUDGET_SECONDS", "researcher", "Execution", 60),
    ],
    "home": [
        ("research_pool", "Research candidates", 40, "ATTENTION_HOME_RESEARCH_LIMIT", "researcher", "Coverage", 1),
        ("enrichment_pool", "Company enrichment", 12, "ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT", "researcher", "Coverage", 1),
        ("events_displayed", "Top events displayed", 5, "ATTENTION_HOME_TOP_EVENTS_DISPLAY_LIMIT", "answer", "Output", 1),
        ("must_read_displayed", "Must-read stories displayed", 10, "ATTENTION_HOME_MUST_READ_DISPLAY_LIMIT", "answer", "Output", 1),
        ("unresolved_displayed", "Open questions displayed", 5, "ATTENTION_HOME_UNRESOLVED_DISPLAY_LIMIT", "answer", "Output", 1),
        ("events_reviewed", "Top events reviewed", 15, "ATTENTION_HOME_TOP_EVENTS_REVIEW_LIMIT", "review", "Coverage", 1),
        ("parallelism", "Parallel enrichment", 4, "ATTENTION_HOME_ZOPEDIA_ENRICHMENT_MAX_WORKERS", "researcher", "Execution", 1),
        ("revisions", "Enrichment revisions", 1, "ATTENTION_HOME_ZOPEDIA_ENRICHMENT_QUALITY_REVISIONS", "review", "Enrichment", 1),
        ("retries", "Enrichment retries", 1, "ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LLM_RETRIES", "researcher", "Recovery", 1),
    ],
}
WORKLOAD_PARAMETERS = {
    workload: {key: llm.register_config_param(
        f"{WORKLOADS[workload]} {key}", "Research workloads", default, label,
        label=label, stage=stage, section=section, minimum=minimum,
        environment_variable=environment, workload=workload,
        default_resolver=(lambda: max(workload_value("home", "events_displayed") * 3, 12)) if workload == "home" and key == "events_reviewed" else None,
    ) for key, label, default, environment, stage, section, minimum in controls}
    for workload, controls in _WORKLOAD_CONTROLS.items()
}


def workload_value(workload: str, name: str) -> int:
    return int(llm.get_config_param(WORKLOAD_PARAMETERS[workload][name]))


def research_workload(surface: str = "") -> str:
    frozen = _SNAPSHOT.get()
    if frozen is not None:
        return frozen.get("workload", "interactive")
    job = os.getenv("PIPELINE_JOB_NAME", "")
    if job.startswith("news-ingest-and-features") or surface.startswith("pipeline.ticker_business") or surface.startswith("pipeline.news"):
        return "news"
    if job.startswith("attention-home") or surface.startswith("attention"):
        return "home"
    if job.startswith("trading-agent") or surface.startswith("trading_agent"):
        return "trading"
    if job.startswith(("zopedia-learning", "macro-fred")):
        return "knowledge"
    return "interactive"


def register_llm_surfaces() -> None:
    from .aql import constants, summarizer, writer  # noqa: F401
    from . import (aql_zopedia_engine, attention_context_llm, attention_home_1d,
                   attention_live_research, attention_market_events, zopedia_agent,
                   zopedia_research, trading_agent, page_agentic_summary, web_research,
                   daily_market_datapoints)  # noqa: F401


def default_flow_configuration() -> dict:
    config = llm.load_llm_config(require_api_key=False)
    model = (config.deployment if config.provider == "azure_openai" else config.model) if config else ""
    routes = {}
    analytical = {"reasoning", "synthesis"}
    thinking = config.reasoning_effort if config and config.reasoning_effort in {"none", "low", "high", "max"} else "high" if config and config.thinking_mode == "enabled" else "none"
    for stage in STAGES:
        name = stage["id"]
        routes[name] = {
            "profile": "analysis" if name in analytical else "economy",
            "thinking": thinking,
            "timeout_seconds": 0,
            "max_output_tokens": 0,
        }
    flow = {
        "version": 1,
        "models": {
            "analysis": {"name": "Analysis", "model": model},
            "economy": {"name": "Economy", "model": model},
        },
        "routes": routes,
        "workloads": {},
        "reviewers": [{"id": "reviewer_1", "name": "Reviewer 1", "profile": "economy", "thinking": thinking, "focus": "Evidence, reasoning and clarity"}],
    }
    legacy_monitor_effort = llm.active_llm_overrides().get("param:trajectory_monitor_reasoning_effort")
    if legacy_monitor_effort in {"none", "low", "high", "max"}:
        flow["routes"]["monitor"]["thinking"] = legacy_monitor_effort
    return flow


def validate_flow_configuration(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) - {"version", "models", "routes", "reviewers", "workloads"} or not {"version", "models", "routes", "reviewers"} <= set(value) or value["version"] != 1:
        raise ValueError("Unsupported research flow configuration.")
    value = deepcopy(value)
    # Existing saved routes and reviewer counts retain their explicit values.
    value["routes"].setdefault("reasoning", deepcopy(value["routes"].get("synthesis", {})))
    value.setdefault("workloads", {})
    models = value["models"]
    if not isinstance(models, dict) or not 1 <= len(models) <= 12:
        raise ValueError("Define between 1 and 12 model profiles.")
    for key, profile in models.items():
        if not isinstance(key, str) or not key or len(key) > 80 or not isinstance(profile, dict) or set(profile) != {"name", "model"}:
            raise ValueError("Invalid model profile.")
        if any(not isinstance(profile[k], str) or not profile[k].strip() or len(profile[k]) > 200 for k in ("name", "model")):
            raise ValueError("Each model profile needs a name and model ID.")
    routes = value["routes"]
    if not isinstance(routes, dict) or set(routes) != {s["id"] for s in STAGES}:
        raise ValueError("Every research step needs a model route.")
    for route in routes.values():
        if not isinstance(route, dict) or set(route) - {"profile", "thinking", "timeout_seconds", "max_output_tokens"}:
            raise ValueError("Invalid research model route.")
    reviewers = value["reviewers"]
    if not isinstance(reviewers, list) or len(reviewers) > 8:
        raise ValueError("At most 8 reviewers can run in a cycle.")
    workloads = value["workloads"]
    if not isinstance(workloads, dict) or set(workloads) - set(WORKLOADS):
        raise ValueError("Unknown research workload.")
    overrides = []
    for workload_routes in workloads.values():
        if not isinstance(workload_routes, dict) or set(workload_routes) - set(routes):
            raise ValueError("Unknown workload step.")
        overrides.extend(workload_routes.values())
    for route in [*routes.values(), *reviewers, *overrides]:
        if not isinstance(route, dict) or set(route) - {"profile", "thinking", "timeout_seconds", "max_output_tokens", "id", "name", "focus"}:
            raise ValueError("Unknown model route setting.")
        if not isinstance(route, dict) or not isinstance(route.get("profile"), str) or route.get("profile") not in models or not isinstance(route.get("thinking"), str) or route.get("thinking") not in {"none", "low", "high", "max"}:
            raise ValueError("Select a configured model and thinking level for every step.")
        for key, maximum in (("timeout_seconds", 1800), ("max_output_tokens", 262144)):
            limit = route.get(key, 0)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= maximum:
                raise ValueError(f"Invalid step {key}.")
    ids = set()
    for reviewer in reviewers:
        if set(reviewer) - {"id", "name", "profile", "thinking", "focus", "timeout_seconds", "max_output_tokens"} or any(not isinstance(reviewer.get(k), str) or not reviewer[k].strip() for k in ("id", "name", "focus")):
            raise ValueError("Each reviewer needs a unique ID, name and focus.")
        if reviewer["id"] in ids or len(reviewer["focus"]) > 20000:
            raise ValueError("Invalid reviewer identity or focus.")
        ids.add(reviewer["id"])
    return deepcopy(value)


def flow_configuration() -> dict:
    snapshot = _SNAPSHOT.get()
    if snapshot is not None:
        return deepcopy(snapshot["flow"])
    value = llm.active_llm_overrides().get(FLOW_KEY)
    return validate_flow_configuration(json.loads(value)) if value else default_flow_configuration()


def capture_research_configuration(*, workload: str | None = None) -> dict:
    if _SNAPSHOT.get() is not None:
        return deepcopy(_SNAPSHOT.get())
    # Register shared prompts before freezing defaults as well as overrides.
    register_llm_surfaces()
    overrides = llm.snapshot_llm_settings(refresh=True)
    with llm.frozen_llm_settings(overrides):
        config = llm.load_llm_config(require_api_key=False)
        snapshot = {"version": 1, "scope": llm.llm_settings_scope(), "workload": workload or research_workload(), "overrides": overrides, "flow": flow_configuration(),
                    "runtime": {k: getattr(config, k) for k in ("provider", "model", "deployment", "temperature", "timeout_seconds", "reasoning_effort", "thinking_mode")} if config else {}}
    snapshot["revision"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:20]
    return snapshot


@contextmanager
def research_configuration(snapshot: dict | None = None, *, client: Any = None):
    existing = _SNAPSHOT.get()
    snapshot = existing if existing is not None else deepcopy(snapshot or capture_research_configuration())
    if snapshot.get("scope") != llm.llm_settings_scope():
        raise ValueError("Research settings belong to a different environment.")
    if existing is None:
        snapshot["flow"] = validate_flow_configuration(snapshot["flow"])
        snapshot["revision"] = hashlib.sha256(json.dumps({key: value for key, value in snapshot.items() if key != "revision"}, sort_keys=True).encode()).hexdigest()[:20]
    token = _SNAPSHOT.set(snapshot)
    client_token = _CLIENT.set(client if client is not None else _CLIENT.get() if existing is not None else None)
    try:
        with llm.frozen_llm_settings(snapshot["overrides"]):
            yield snapshot
    finally:
        _CLIENT.reset(client_token)
        _SNAPSHOT.reset(token)


def resolve_research_client(loader):
    client = _CLIENT.get()
    if client is None:
        client = loader()
        if _SNAPSHOT.get() is not None:
            _CLIENT.set(client)
    return client


def configured_research(function=None, *, workload: str | None = None, prepare=None):
    if function is None:
        return lambda target: configured_research(target, workload=workload, prepare=prepare)
    @wraps(function)
    def run(*args, **kwargs):
        if prepare is not None and _SNAPSHOT.get() is None:
            prepare()
        supplied = kwargs.pop("configuration_snapshot", None)
        resume = kwargs.get("resume_state") or {}
        if supplied is None and kwargs.get("run_id") == resume.get("run_id"):
            supplied = resume.get("configuration_snapshot")
        supplied = supplied or (_SNAPSHOT.get() if _SNAPSHOT.get() is not None else capture_research_configuration(workload=workload or research_workload(kwargs.get("surface", ""))))
        if workload and _SNAPSHOT.get() is None:
            from .llm_execution import current_research_access_scope, _read_state, _write_state
            access = current_research_access_scope()
            execution = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME")
            if access and execution:
                from .pipeline_store import write_execution_state
                namespace = f"research_configuration:{llm.llm_settings_scope()}:{access}"
                key = workload + ":" + execution
                original = _read_state(namespace, key)
                if original:
                    supplied = original
                else:
                    write_execution_state(namespace, key, supplied, immutable=True)
                    supplied = _read_state(namespace, key) or supplied
            with llm.frozen_llm_settings(supplied["overrides"]):
                controls = [item for item in llm.list_config_params() if item.get("workload") == workload]
            _write_state(f"research_workload_settings:{llm.llm_settings_scope()}", workload, {
                "parameters": {item["key"]: item["value"] for item in controls},
                "defaults": {item["key"]: item["default"] for item in controls},
                "revision": supplied["revision"],
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "job": os.getenv("PIPELINE_JOB_NAME"),
            })
        with research_configuration(supplied, client=kwargs.get("llm_client")) as snapshot:
            result = function(*args, **kwargs)
            if isinstance(result, dict):
                result.setdefault("runtime", {})["configuration_revision"] = snapshot["revision"]
            return result
    return run


@contextmanager
def reviewer_route(reviewer: dict):
    token = _ROUTE.set(reviewer)
    try:
        yield
    finally:
        _ROUTE.reset(token)


def stage_client(client: Any, stage: str) -> tuple[Any, dict]:
    if stage not in {s["id"] for s in STAGES}:
        raise ValueError(f"Unknown research step: {stage}")
    flow = flow_configuration()
    route = _ROUTE.get() if stage == "review" and _ROUTE.get() is not None else flow.get("workloads", {}).get(research_workload(), {}).get(stage, flow["routes"][stage])
    profile = flow["models"][route["profile"]]
    metadata = {"stage": stage, "workload": research_workload(), "profile": route["profile"], "model": profile["model"], "thinking": route["thinking"], "max_output_tokens": route.get("max_output_tokens", 0), "configuration_revision": (_SNAPSHOT.get() or {}).get("revision", "")}
    config = getattr(client, "config", None)
    if not isinstance(config, llm.LLMConfig):
        return client, metadata  # Non-provider adapters implement the same boundary.
    frozen_runtime = (_SNAPSHOT.get() or {}).get("runtime") or {}
    if frozen_runtime.get("provider") == config.provider:
        config = replace(config, **{k: v for k, v in frozen_runtime.items() if k != "provider"})
    # Stage routing is an explicit saved policy. Without one, preserve the
    # configured transport exactly, including provider-default thinking effort.
    if llm.active_llm_overrides().get(FLOW_KEY) or flow != default_flow_configuration():
        config = replace(config, model=profile["model"], deployment=profile["model"] if config.provider == "azure_openai" else config.deployment,
                         reasoning_effort=route["thinking"], thinking_mode="disabled" if route["thinking"] == "none" else "enabled",
                         timeout_seconds=route.get("timeout_seconds") or config.timeout_seconds)
    else:
        metadata.update(thinking=config.reasoning_effort, thinking_mode=config.thinking_mode)
    routed = llm.client_from_config(config, session=getattr(client, "session", None))
    setattr(routed, "_zopedia_surface", getattr(client, "_zopedia_surface", ""))
    return routed, metadata


def flow_contract() -> dict:
    snapshot = capture_research_configuration()
    return {"key": FLOW_KEY, "revision": snapshot["revision"], "configuration": snapshot["flow"], "stages": STAGES, "edges": EDGES, "workloads": WORKLOADS}
