from __future__ import annotations

import json
from typing import Any

import pandas as pd

from services.json_utils import frame_to_records, to_jsonable, to_list
from services.auth_service import UserContext
from services.pipeline_store import (
    load_latest_dataset_frame,
    load_recent_dataset_frames,
    record_trading_agent_action,
    trading_agent_actions_table,
)
from services.trading_agent import TRADING_AGENT_NARRATIVE_CONTRACT_VERSION, TRADING_AGENT_LEGACY_NARRATIVE_CONTRACTS, normalize_trading_agent_candidate
from services.trading_call_tracking import tracking_view


def _text(value: object, limit: int = 4000) -> str:
    text = str(value if value is not None else "").strip()
    return "" if not text or text.lower() == "nan" else text[:limit]


def _json(value: object, fallback: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    text = _text(value, 1_000_000)
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback


def _latest_output() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, object | None]:
    actions = trading_agent_actions_table(limit=500)
    try:
        run_versions = load_recent_dataset_frames("trading_agent_runs", limit=8)
    except Exception:
        run_versions = []
    try:
        candidate_versions = load_recent_dataset_frames("trading_agent_candidates", limit=8)
    except Exception:
        candidate_versions = []
    for runs, metadata in run_versions:
        if not isinstance(runs, pd.DataFrame) or runs.empty or "run_id" not in runs.columns:
            continue
        run_ids = {_text(value) for value in runs["run_id"].tolist() if _text(value)}
        for candidates, _ in candidate_versions:
            if not isinstance(candidates, pd.DataFrame) or candidates.empty or "run_id" not in candidates.columns:
                continue
            scoped = candidates[candidates["run_id"].astype(str).map(_text).isin(run_ids)].copy()
            if not scoped.empty:
                return runs.copy(), scoped, actions, metadata
    try:
        runs, metadata = load_latest_dataset_frame("trading_agent_runs")
    except Exception:
        runs, metadata = pd.DataFrame(), None
    try:
        candidates, _ = load_latest_dataset_frame("trading_agent_candidates")
    except Exception:
        candidates = pd.DataFrame()
    return runs, candidates, actions, metadata


def _candidate(row: pd.Series) -> dict[str, Any]:
    source = _json(row.get("candidate_json"), {})
    candidate = dict(source) if isinstance(source, dict) else {}
    narrative_version = _text(candidate.get("narrative_contract_version"), 120)
    candidate.update(
        {
            "candidate_id": _text(row.get("candidate_id"), 160),
            "trading_agent_run_id": _text(row.get("trading_agent_run_id"), 160),
            "run_id": _text(row.get("run_id"), 160),
            "horizon_key": _text(row.get("horizon_key"), 20),
            "horizon_label": _text(row.get("horizon_label"), 60),
            "selected_horizon_col": _text(row.get("selected_horizon_col"), 80),
            "ticker": _text(candidate.get("ticker") or row.get("ticker"), 20).upper(),
            "direction": _text(candidate.get("direction") or row.get("direction"), 40),
            "stance": _text(candidate.get("stance") or row.get("stance"), 40),
            "research_status": _text(candidate.get("research_status") or row.get("research_status"), 40),
            "setup": _text(candidate.get("setup") or row.get("setup")),
            "hypothesis": _text(candidate.get("hypothesis") or row.get("hypothesis")),
            "invalidation": _text(candidate.get("invalidation") or row.get("invalidation")),
            "suggested_horizon": _text(candidate.get("suggested_horizon") or row.get("suggested_horizon"), 120),
            "confidence": _text(candidate.get("confidence") or row.get("confidence") or "low", 20),
        }
    )
    candidate = normalize_trading_agent_candidate(candidate)
    candidate["narrative_contract_version"] = narrative_version
    for target, source_key in (
        ("evidence", "evidence_json"),
        ("tail_risks", "tail_risks_json"),
        ("evidence_limits", "evidence_limits_json"),
    ):
        values = to_list(candidate.get(target)) or to_list(_json(row.get(source_key), []))
        candidate[target] = [_text(value, 1200) for value in values if _text(value, 1200)][:3]
    return to_jsonable(candidate)


def _actions_by_candidate(actions: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if actions.empty or "candidate_id" not in actions.columns:
        return {}
    ordered = actions.copy()
    if "created_at_utc" in ordered.columns:
        ordered["_created"] = pd.to_datetime(ordered["created_at_utc"], utc=True, errors="coerce")
        ordered = ordered.sort_values("_created", ascending=False, na_position="last")
    result: dict[str, dict[str, Any]] = {}
    for row in ordered.to_dict(orient="records"):
        key = _text(row.get("candidate_id"), 160)
        if key and key not in result:
            result[key] = to_jsonable(row)
    return result


def build_trading_agent_view(context: UserContext | None = None) -> dict[str, Any]:
    tracking: dict[str, Any] = {"status": "available", "calls": [], "as_of": ""}
    if context is not None:
        try:
            tracking = tracking_view(owner_id=context.user_id)
        except Exception:
            tracking = {"status": "unavailable", "calls": [], "as_of": ""}
    tracking_fields = {"tracking_status": tracking["status"], "tracked_calls": tracking["calls"], "tracking_as_of": tracking["as_of"]}
    runs, candidates, actions, metadata = _latest_output()
    if runs.empty or candidates.empty:
        return {"status": "unavailable", "as_of": "", "horizons": [], "decisions": [], **tracking_fields}
    if context is not None and not actions.empty:
        actions = actions[actions["requested_by"].astype(str).eq(context.user_id)].copy()
    if "generated_at_utc" in runs.columns:
        generated = pd.to_datetime(runs["generated_at_utc"], utc=True, errors="coerce")
        if generated.notna().any():
            runs = runs[generated.eq(generated.max())].copy()
    latest_run_id = _text(runs.iloc[0].get("run_id"), 160) if not runs.empty else ""
    if latest_run_id and "run_id" in candidates.columns:
        candidates = candidates[candidates["run_id"].astype(str).eq(latest_run_id)].copy()
    action_map = _actions_by_candidate(actions)
    horizons: list[dict[str, Any]] = []
    order = {"1w": 0, "1m": 1, "3m": 2, "1y": 3, "5y": 4}
    for _, run in runs.sort_values("horizon_key", key=lambda values: values.map(lambda value: order.get(_text(value), 99))).iterrows():
        key = _text(run.get("horizon_key"), 20)
        result_source = _json(run.get("result_json"), {})
        result = dict(result_source) if isinstance(result_source, dict) else {}
        result.setdefault("status", _text(run.get("status"), 40))
        result.setdefault("regime_read", _text(run.get("regime_read")))
        result.setdefault("portfolio_posture", _text(run.get("portfolio_posture")))
        result.setdefault("data_gaps", _json(run.get("data_gaps_json"), []))
        context_source = _json(run.get("context_json"), {})
        context = dict(context_source) if isinstance(context_source, dict) else {}
        opportunity_rows = list(context.get("market_opportunity_feed") or [])
        opportunity_by_ticker = {
            _text(item.get("symbol"), 20).upper(): to_jsonable(item)
            for item in opportunity_rows
            if isinstance(item, dict) and _text(item.get("symbol"), 20)
        }
        horizon_rows = candidates[candidates["horizon_key"].astype(str).eq(key)].copy() if "horizon_key" in candidates.columns else candidates.copy()
        if "rank" in horizon_rows.columns:
            horizon_rows["_rank"] = pd.to_numeric(horizon_rows["rank"], errors="coerce")
            horizon_rows = horizon_rows.sort_values("_rank", na_position="last")
        candidate_views: list[dict[str, Any]] = []
        for _, row in horizon_rows.iterrows():
            item = _candidate(row)
            ticker = _text(item.get("ticker"), 20).upper()
            item["market"] = opportunity_by_ticker.get(ticker, {})
            item["decision"] = action_map.get(_text(item.get("candidate_id"), 160), {})
            candidate_views.append(item)
        if _text(result.get("narrative_contract_version"), 120) not in {"", TRADING_AGENT_NARRATIVE_CONTRACT_VERSION, *TRADING_AGENT_LEGACY_NARRATIVE_CONTRACTS}:
            candidate_views = []
        horizons.append(
            {
                "key": key,
                "label": _text(run.get("horizon_label"), 60) or key,
                "selected_horizon_col": _text(run.get("selected_horizon_col"), 80),
                "status": _text(result.get("status"), 40),
                "regime_read": _text(result.get("regime_read")),
                "portfolio_posture": _text(result.get("portfolio_posture")),
                "data_gaps": [_text(value, 800) for value in to_list(result.get("data_gaps")) if _text(value, 800)][:3],
                "candidates": candidate_views,
            }
        )
    as_of = _text(getattr(metadata, "asof_time_utc", ""), 80)
    if not as_of and "asof_time_utc" in runs.columns and not runs.empty:
        as_of = _text(runs.iloc[0].get("asof_time_utc"), 80)
    return {
        "status": "available" if horizons else "unavailable",
        "as_of": as_of,
        "run_id": latest_run_id,
        "horizons": horizons,
        "decisions": frame_to_records(actions.head(100)) if not actions.empty else [],
        **tracking_fields,
    }


def record_trading_agent_decision(
    *,
    candidate_id: str,
    action: str,
    context: UserContext,
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject.")
    target = str(candidate_id or "").strip()
    view = build_trading_agent_view(context)
    candidate = next(
        (
            item
            for horizon in view.get("horizons", [])
            for item in horizon.get("candidates", [])
            if str(item.get("candidate_id") or "") == target
        ),
        None,
    )
    if candidate is None:
        if normalized_action == "approve" and any(str(item.get("candidate_id") or "") == target for item in view.get("tracked_calls", [])):
            return {"status": "recorded", "candidate_id": target, "action": normalized_action, "message": "Candidate approved."}
        raise ValueError("Trading Agent candidate is no longer current.")
    ok, message = record_trading_agent_action(
        candidate=candidate,
        action=normalized_action,
        requested_by=context.user_id,
        requested_email=context.email,
    )
    if not ok:
        raise RuntimeError(message)
    return {"status": "recorded", "candidate_id": target, "action": normalized_action, "message": message}


__all__ = ["build_trading_agent_view", "record_trading_agent_decision"]
