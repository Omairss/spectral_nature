"""Versioned task routing and freshness policy, independent of application callers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from fnmatch import fnmatchcase
import hashlib
import json
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
    # Budget exhaustion is a completion mode, independent of task routing or
    # a whole-run configuration rollback. Deployment overrides may predate it.
    defaults = json.loads((Path(__file__).parents[1] / "config/inference_policy.json").read_text())
    value["best_effort_model"] = deepcopy(document.get("best_effort_model", defaults["best_effort_model"]))
    value["core_tasks"] = deepcopy(document.get("core_tasks", defaults["core_tasks"]))
    value["profile"] = name
    value["version"] = document["version"]
    value["not_before_pacific"] = not_before
    return value


def policy_identity() -> str:
    return hashlib.sha256(json.dumps(policy(), sort_keys=True).encode()).hexdigest()


def is_core_task(*, surface: str, purpose: str) -> bool:
    return any(fnmatchcase(surface, rule["surface"]) and fnmatchcase(purpose, rule["purpose"])
               for rule in policy()["core_tasks"])


def client_for_task(client, *, surface: str, purpose: str, best_effort: bool = False):
    """Route declared tasks; preserve the full configured model by default.

    Alternate providers must supply their own prefixed configuration and key.
    A provider credential is never implicitly forwarded to a different host.
    """
    from . import llm
    selected = policy()
    route = next((r for r in selected.get("routes", [])
                  if fnmatchcase(surface, r["surface"]) and fnmatchcase(purpose, r["purpose"])), None)
    if route is None and not best_effort:
        return client, {"profile": selected["profile"], "role": "full"}
    role = "best_effort" if best_effort else route["role"]
    definition = selected["best_effort_model"] if best_effort else selected["models"][role]
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
