"""Versioned task routing and freshness policy, independent of application callers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from fnmatch import fnmatchcase
import hashlib
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def policy() -> dict:
    document = json.loads(os.getenv("LLM_POLICY_JSON") or Path(os.getenv("LLM_POLICY_PATH") or
        Path(__file__).parents[1] / "config/inference_policy.json").read_text())
    name = os.getenv("LLM_POLICY_PROFILE", "baseline")
    if name not in document["profiles"]:
        raise ValueError(f"Unknown inference policy profile: {name}")
    not_before = os.getenv("LLM_POLICY_NOT_BEFORE", "")
    if not_before and datetime.now(ZoneInfo("America/Los_Angeles")).date() < date.fromisoformat(not_before):
        name = "baseline"
    value = deepcopy(document["profiles"][name])
    value["profile"] = name
    value["version"] = document["version"]
    value["not_before_pacific"] = not_before
    return value


def policy_identity() -> str:
    return hashlib.sha256(json.dumps(policy(), sort_keys=True).encode()).hexdigest()


def client_for_task(client, *, surface: str, purpose: str):
    """Route declared tasks; preserve the full configured model by default.

    Alternate providers must supply their own prefixed configuration and key.
    A provider credential is never implicitly forwarded to a different host.
    """
    from . import llm
    selected = policy()
    route = next((r for r in selected.get("routes", [])
                  if fnmatchcase(surface, r["surface"]) and fnmatchcase(purpose, r["purpose"])), None)
    if route is None:
        return client, {"profile": selected["profile"], "role": "full"}
    role = route["role"]
    definition = selected["models"][role]
    original = getattr(client, "config", None)
    if original is None:
        return client, {"profile": selected["profile"], "role": "full"}
    prefix = definition.get("env_prefix")
    if prefix:
        from .secrets import runtime_env_value
        model_name = runtime_env_value(prefix + "LLM_MODEL")
        if not all(runtime_env_value(prefix + field) for field in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL")):
            raise ValueError(f"Explicit provider, endpoint and model required for model role {role}")
        # Resolve only this role's credential, without default-key fallback.
        from .secrets import resolve_secret_value
        key = resolve_secret_value([prefix + "LLM_API_KEY"], secret_name_env=prefix + "LLM_API_KEY_SECRET_NAME")
        if not key:
            raise ValueError(f"Credential required for model role {role}")
        config = llm.load_llm_config(prefix, require_api_key=False)
        if config is None:
            raise ValueError(f"Model role unavailable: {role}")
        config = replace(config, api_key=key, model=model_name,
                         deployment=runtime_env_value(prefix + "LLM_DEPLOYMENT") or model_name)
    else:
        if original.provider != definition["provider"]:
            raise ValueError(f"Model role {role} requires its own provider configuration")
        config = replace(original, model=definition["model"], deployment=definition["model"],
                         thinking_mode=definition.get("thinking_mode", original.thinking_mode),
                         reasoning_effort=definition.get("reasoning_effort", original.reasoning_effort))
    routed = llm.client_from_config(config)
    routed._zopedia_surface = surface
    return routed, {"profile": selected["profile"], "role": role,
                    "policy_version": selected["version"], "policy_identity": policy_identity()}


def execute_with_fallback(handler, context, connection):
    """One candidate attempt; Azure's single replica retry uses baseline.

    Durable state also handles an Azure replica dying before Python can catch
    its failure. A failed rollout stays on baseline until its policy changes.
    """
    from . import pipeline_store
    from .work_scope import WorkScope
    selected = policy()
    rollout = selected.get("rollout", {})
    if context.name not in rollout.get("jobs", []):
        return handler(context, connection)
    namespace = "inference_rollout:" + pipeline_store.dataset_namespace()
    key = context.name + ":" + policy_identity()
    execution = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME") or context.run_id
    state = pipeline_store.read_execution_state(namespace, key, strict=True) or {}
    fallback = rollout["fallback_profile"]

    def save(value):
        if not pipeline_store.write_execution_state(namespace, key, value):
            raise RuntimeError("Inference rollout state could not be retained")

    def run_baseline():
        os.environ["LLM_POLICY_PROFILE"] = fallback
        with WorkScope(context.started_at_monotonic + rollout["attempt_seconds"]).activate():
            return handler(context, connection)

    if state.get("rollback") or state.get("status") == "started":
        save({**state, "rollback": True, "reason": state.get("reason") or "Previous replica did not complete"})
        print(f"[info] inference policy={fallback} retained rollback", flush=True)
        return run_baseline()
    save({"execution": execution, "status": "started", "profile": selected["profile"]})
    try:
        with WorkScope(context.started_at_monotonic + rollout["attempt_seconds"]).activate():
            result = handler(context, connection)
        if context.degraded_components.get("news_narratives"):
            raise RuntimeError("Candidate did not complete its required news/company narratives")
    except Exception as exc:
        try:
            save({"execution": execution, "status": "failed", "rollback": True, "reason": type(exc).__name__})
        except Exception as audit_error:
            # The previously committed 'started' state already selects baseline
            # after replica death. A cancelled audit write must not replace the
            # original failure and erase the diagnostic evidence.
            logging.getLogger(__name__).warning("Inference failure audit unavailable: %s; original=%s",
                                               type(audit_error).__name__, type(exc).__name__)
            exc.add_note("Rollout failure audit could not be written: " + type(audit_error).__name__)
        print(f"[warn] inference policy failed ({type(exc).__name__}); Azure replica retry will use {fallback}", flush=True)
        raise
    save({"execution": execution, "status": "completed", "profile": selected["profile"]})
    return result
