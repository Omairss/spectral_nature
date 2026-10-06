from __future__ import annotations

from typing import Any

from data_access.contracts import frame_to_records, to_jsonable
from services import api_auth, auth_service, auth_store
from services.llm import list_config_params, list_narrative_prompts, load_llm_config, load_prompt_overrides
from services.pipeline_store import (
    connector_call_rollup,
    dataset_version_history,
    job_run_history,
    latest_dataset_status_table,
    latest_job_status_table,
    model_call_rollup,
)


def _register_llm_surfaces() -> None:
    import services.aql.constants  # noqa: F401
    import services.aql.summarizer  # noqa: F401
    import services.aql.writer  # noqa: F401
    import services.aql_zopedia_engine  # noqa: F401
    import services.attention_context_llm  # noqa: F401
    import services.attention_home_1d  # noqa: F401
    import services.attention_live_research  # noqa: F401
    import services.attention_market_events  # noqa: F401
    import services.zopedia_agent  # noqa: F401


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
    load_prompt_overrides()
    config = load_llm_config()
    access = auth_service.get_access_admin_dashboard(
        usage_window_days=history_days,
        security_window_days=history_days,
        active_window_minutes=30,
        recent_event_limit=100,
        sankey_user_limit=12,
    )
    prompts = [
        {
            "key": str(item.get("key") or ""),
            "name": str(item.get("name") or ""),
            "group": str(item.get("group") or ""),
            "file": str(item.get("file") or ""),
            "prompt": str(item.get("prompt") or ""),
            "is_override": bool(item.get("is_override")),
        }
        for item in list_narrative_prompts()
    ]
    return {
        "users": _safe_users(),
        "invites": to_jsonable(auth_service.list_pending_invites()),
        "access": to_jsonable(access),
        "llm": {
            "runtime": None if config is None else {
                "provider": config.provider,
                "model": config.model or config.deployment,
                "temperature": config.temperature,
                "reasoning_effort": config.reasoning_effort,
                "timeout_seconds": config.timeout_seconds,
                "embedding_model": config.embedding_model,
            },
            "prompts": prompts,
            "parameters": to_jsonable(list_config_params()),
        },
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
