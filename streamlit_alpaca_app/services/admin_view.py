from __future__ import annotations

from typing import Any

from data_access.contracts import frame_to_records, to_jsonable
from services import api_auth, auth_service, auth_store
from services.llm import (
    NARRATIVE_STYLE_RULE, get_active_narrative_style_rule, llm_settings_scope,
    list_config_params, list_narrative_prompts, load_llm_config, load_prompt_overrides,
    save_prompt_override_changes, validate_llm_setting_changes,
    active_llm_overrides,
)
from services.pipeline_store import (
    connector_call_rollup,
    dataset_version_history,
    job_run_history,
    latest_dataset_status_table,
    latest_job_status_table,
    model_call_rollup,
    research_stage_usage,
    read_execution_states,
)
from services.llm_flow import flow_contract, REVIEW_CYCLES, register_llm_surfaces, research_configuration


def _register_llm_surfaces() -> None:
    register_llm_surfaces()


def build_llm_settings_view() -> dict[str, Any]:
    _register_llm_surfaces()
    if not load_prompt_overrides(force=True):
        raise RuntimeError("Saved LLM settings could not be loaded.")
    saved_keys = set(active_llm_overrides())
    from services.llm_flow import WORKLOADS
    workers = read_execution_states(f"research_workload_settings:{llm_settings_scope()}", list(WORKLOADS))
    with research_configuration():
        config = load_llm_config(require_api_key=False)
        style = get_active_narrative_style_rule()
        parameters = list_config_params()
        for parameter in parameters:
            worker = workers.get(parameter.get("workload"), {})
            observed = worker.get("parameters", {})
            if parameter["key"] in observed:
                parameter["default"] = worker.get("defaults", observed).get(parameter["key"], parameter["default"])
                parameter["observed_at_utc"] = worker.get("observed_at_utc")
                if parameter["key"] not in saved_keys:
                    parameter["value"] = parameter["default"]
                    parameter["source"] = "worker"
        if next((p["value"] for p in parameters if p["key"] == "param:agent_answer_judge_enabled"), 1) == 0:
            for parameter in parameters:
                if parameter["key"] == REVIEW_CYCLES:
                    parameter["value"] = 0
        return {
            "scope": llm_settings_scope(),
            "runtime": None if config is None else {
                "provider": config.provider,
                "model": config.deployment if config.provider == "azure_openai" else config.model,
                "model_editable": config.provider != "azure_openai",
                "temperature": config.temperature,
                "reasoning_effort": config.reasoning_effort,
                "thinking_mode": config.thinking_mode,
                "timeout_seconds": config.timeout_seconds,
                "embedding_model": config.embedding_model,
            },
            "style": {"key": "__narrative_style_rule__", "value": style,
                      "default": NARRATIVE_STYLE_RULE, "is_override": style != NARRATIVE_STYLE_RULE},
            "prompts": list_narrative_prompts(),
            "parameters": to_jsonable(parameters),
            "flow": flow_contract(),
            "stage_usage": to_jsonable(research_stage_usage(scope=llm_settings_scope())),
        }


def update_llm_settings(changes: dict[str, Any], *, context: auth_service.UserContext) -> dict[str, Any]:
    if not context.is_admin:
        raise PermissionError("Admin access is required.")
    _register_llm_surfaces()
    changes = dict(changes)
    if REVIEW_CYCLES in changes:
        # The count replaces the old UI switch, while respecting old saves.
        changes["param:agent_answer_judge_enabled"] = 1 if changes[REVIEW_CYCLES] is None or int(changes[REVIEW_CYCLES]) > 0 else 0
    validated = validate_llm_setting_changes(changes)
    if not save_prompt_override_changes(validated):
        raise RuntimeError("LLM settings could not be saved.")
    auth_service.record_access_event(
        event_type="llm_settings_updated", event_category="admin", user=context,
        section_name="admin", detail={"scope": llm_settings_scope(), "keys": sorted(validated)},
    )
    return build_llm_settings_view()


def _safe_users() -> list[dict[str, Any]]:
    allowed = (
        "user_id", "email", "first_name", "last_name", "display_name", "status", "role",
        "portfolio_id", "portfolio_slug", "portfolio_name", "membership_role", "share_fraction",
        "can_view_full_portfolio", "last_login_at", "failed_login_count", "locked_until",
        "open_session_count", "active_session_count", "last_seen_at",
    )
    return [
        {key: to_jsonable(row.get(key)) for key in allowed}
        for row in auth_service.list_users()
    ]


def build_admin_view(*, days: int = 7) -> dict[str, Any]:
    history_days = min(max(int(days), 1), 30)
    _register_llm_surfaces()
    llm = build_llm_settings_view()
    access = auth_service.get_access_admin_dashboard(
        usage_window_days=history_days,
        security_window_days=history_days,
        active_window_minutes=30,
        recent_event_limit=100,
        sankey_user_limit=12,
    )
    return {
        "users": _safe_users(),
        "invites": to_jsonable(auth_service.list_pending_invites()),
        "access": to_jsonable(access),
        "llm": llm,
        "agent_keys": to_jsonable(auth_store.list_agent_api_keys()),
        "system": {
            "job_status": frame_to_records(latest_job_status_table()),
            "dataset_status": frame_to_records(latest_dataset_status_table()),
            "job_history": frame_to_records(job_run_history(days=history_days)),
            "dataset_history": frame_to_records(dataset_version_history(days=history_days)),
            "connector_calls": frame_to_records(connector_call_rollup(days=history_days)),
            "model_calls": frame_to_records(model_call_rollup(days=history_days)),
        },
        "auth": {
            "email_delivery": to_jsonable(auth_service.get_auth_email_delivery_status()),
            "scopes": sorted(api_auth.AGENT_SCOPE_ALLOWLIST),
        },
        "history_days": history_days,
    }


__all__ = ["build_admin_view"]
