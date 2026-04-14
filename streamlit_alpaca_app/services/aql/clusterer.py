"""
AQL clusterer — graph edges, candidate clustering, and taxonomy horizon trends.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..runtime_policy import attention_graph_policy
from ._shared import (
    _augment_candidate_frame,
    _candidate_graph_tags,
    _candidate_taxonomy_context,
    _clean_cluster_label,
    _coerce_float,
    _coerce_text,
    _dominant_cluster_label,
    _event_title_from_cluster,
    _history_correlation_map,
    _humanize_cluster_tag,
    _informative_graph_label,
    _jaccard,
    _json_dumps,
    _merge_text_values,
    _move_direction,
    _normalize_symbol,
    _safe_list,
    _tag_tokens,
    _top_symbol_label,
)
from .extractor import _claim_entities, _claim_map_from_frame

# Horizon label/alias mappings
_TREND_HORIZON_LABELS = {
    "1d": "1 Day",
    "1w": "1 Week",
    "1mo": "1 Month",
    "3mo": "3 Month",
    "1yr": "1 Year",
}

_TREND_HORIZON_ALIASES = {
    "1d": "1d",
    "1day": "1d",
    "1w": "1w",
    "1wk": "1w",
    "1week": "1w",
    "1m": "1mo",
    "1mo": "1mo",
    "1mon": "1mo",
    "1month": "1mo",
    "3m": "3mo",
    "3mo": "3mo",
    "3mon": "3mo",
    "3month": "3mo",
    "3months": "3mo",
    "1y": "1yr",
    "1yr": "1yr",
    "1year": "1yr",
}

_TREND_HORIZON_ORDER = {"1d": 0, "1w": 1, "1mo": 2, "3mo": 3, "1yr": 4}


def _normalize_horizon_token(value: object) -> str:
    token = _coerce_text(value).lower()
    return _TREND_HORIZON_ALIASES.get(token, "")


def _graph_edges(
    candidates: pd.DataFrame,
    claim_map: dict[str, list[dict[str, Any]]],
    *,
    history_correlation_map: dict[tuple[str, str], dict[str, float | int]] | None = None,
    run_id: str,
    asof_time_utc: pd.Timestamp,
) -> pd.DataFrame:
    policy = attention_graph_policy()
    rows: list[dict[str, Any]] = []
    records = candidates.to_dict(orient="records")
    for idx, left in enumerate(records):
        for right in records[idx + 1 :]:
            left_symbol = _normalize_symbol(left.get("symbol"))
            right_symbol = _normalize_symbol(right.get("symbol"))
            if not left_symbol or not right_symbol:
                continue
            weight = 0.0
            reasons: list[str] = []
            left_taxonomy = _candidate_taxonomy_context(left)
            right_taxonomy = _candidate_taxonomy_context(right)
            shared_taxonomy = False
            if left_taxonomy["industry"] and left_taxonomy["industry"] == right_taxonomy["industry"]:
                weight += policy.peer_group_weight
                reasons.append("taxonomy_peer")
                shared_taxonomy = True
            elif left_taxonomy["peer_group"] and left_taxonomy["peer_group"] == right_taxonomy["peer_group"]:
                weight += policy.peer_group_weight
                reasons.append("taxonomy_peer")
                shared_taxonomy = True
            elif left_taxonomy["sector"] and left_taxonomy["sector"] == right_taxonomy["sector"]:
                weight += policy.sector_weight
                reasons.append("taxonomy_sector")
                shared_taxonomy = True
            shared_roles = [
                field
                for field in ["commodity_role", "rates_role", "defensive_role"]
                if _coerce_text(left.get(field))
                and _coerce_text(left.get(field)).lower() == _coerce_text(right.get(field)).lower()
            ]
            if shared_roles:
                weight += policy.role_match_weight
                reasons.append("role_match")
            left_tags = _candidate_graph_tags(left)
            right_tags = _candidate_graph_tags(right)
            tag_overlap = _jaccard(left_tags, right_tags)
            if tag_overlap > 0:
                weight += min(tag_overlap * policy.tag_overlap_mult, policy.tag_overlap_cap)
                reasons.append("tags")
            claim_overlap = _jaccard(_claim_entities(claim_map.get(left_symbol, [])), _claim_entities(claim_map.get(right_symbol, [])))
            if claim_overlap > 0:
                weight += min(claim_overlap * policy.claim_overlap_mult, policy.claim_overlap_cap)
                reasons.append("claims")
            history_corr = math.nan
            history_corr_obs = 0
            history_candidate = (
                (not shared_taxonomy and tag_overlap <= 0 and claim_overlap <= 0)
                or reasons == ["taxonomy_sector"]
            )
            history_info = (history_correlation_map or {}).get(tuple(sorted((left_symbol, right_symbol))))
            if history_candidate and isinstance(history_info, dict):
                history_corr = _coerce_float(history_info.get("correlation"), math.nan)
                history_corr_obs = int(history_info.get("observations") or 0)
                if (
                    history_corr_obs >= int(policy.history_corr_min_observations)
                    and math.isfinite(history_corr)
                    and history_corr >= float(policy.history_corr_min)
                ):
                    history_weight = min(
                        (history_corr - float(policy.history_corr_min)) * float(policy.history_corr_mult),
                        float(policy.history_corr_cap),
                    )
                    if history_weight > 0:
                        weight += history_weight
                        reasons.append("history_corr")
            has_relationship_signal = shared_taxonomy or tag_overlap > 0 or claim_overlap > 0 or "history_corr" in reasons
            if _move_direction(left.get("change_pct")) != _move_direction(right.get("change_pct")):
                weight += policy.opposite_direction_bonus if has_relationship_signal else 0.0
            else:
                weight += policy.same_direction_bonus if has_relationship_signal else 0.0
            if weight < policy.min_edge_weight:
                continue
            rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "left_candidate_id": _coerce_text(left.get("candidate_id")),
                    "right_candidate_id": _coerce_text(right.get("candidate_id")),
                    "left_symbol": left_symbol,
                    "right_symbol": right_symbol,
                    "edge_weight": round(weight, 3),
                    "edge_reasons_json": _json_dumps(reasons),
                    "history_correlation": round(history_corr, 3) if math.isfinite(history_corr) else math.nan,
                    "history_correlation_observations": int(history_corr_obs),
                }
            )
    return pd.DataFrame(rows)


def recompute_attention_candidate_graph(
    candidate_frame: pd.DataFrame,
    claims_frame: pd.DataFrame | None = None,
    *,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    price_history_frame: pd.DataFrame | None = None,
    run_id: str = "analysis",
    asof_time_utc: object | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = candidate_frame.copy() if isinstance(candidate_frame, pd.DataFrame) else pd.DataFrame()
    if candidates.empty:
        return candidates, pd.DataFrame()
    asof = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.isna(asof):
        asof = pd.Timestamp.utcnow()
    normalized_run_id = _coerce_text(run_id) or f"analysis::{asof.strftime('%Y%m%dT%H%M%SZ')}"
    augmented = _augment_candidate_frame(
        candidates,
        asof_time_utc=asof,
        run_id=normalized_run_id,
    )
    claim_map = _claim_map_from_frame(claims_frame)
    history_bars = bars_by_symbol
    if history_bars is None and isinstance(price_history_frame, pd.DataFrame) and not price_history_frame.empty:
        from ..attention_materialized import bars_by_symbol_from_price_history

        history_bars = bars_by_symbol_from_price_history(
            price_history_frame,
            augmented.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist(),
            asof_time_utc=asof,
            lookback_days=180,
        )
    history_corr_map = _history_correlation_map(
        history_bars,
        augmented.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist(),
        min_observations=attention_graph_policy().history_corr_min_observations,
    )
    edges = _graph_edges(
        augmented,
        claim_map,
        history_correlation_map=history_corr_map,
        run_id=normalized_run_id,
        asof_time_utc=asof,
    )
    return augmented, edges.reset_index(drop=True)


def _cluster_candidates(candidates: pd.DataFrame, edges: pd.DataFrame) -> list[list[str]]:
    if candidates.empty:
        return []
    symbols = [_normalize_symbol(item) for item in candidates.get("symbol", pd.Series(dtype=str)).tolist() if _normalize_symbol(item)]
    adjacency: dict[str, set[str]] = {symbol: set() for symbol in symbols}
    if isinstance(edges, pd.DataFrame) and not edges.empty:
        for _, row in edges.iterrows():
            left = _normalize_symbol(row.get("left_symbol"))
            right = _normalize_symbol(row.get("right_symbol"))
            if left and right:
                adjacency.setdefault(left, set()).add(right)
                adjacency.setdefault(right, set()).add(left)
    clusters: list[list[str]] = []
    seen: set[str] = set()
    for symbol in symbols:
        if symbol in seen or not adjacency.get(symbol):
            continue
        stack = [symbol]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(list(adjacency.get(current, set()) - seen))
        if len(component) >= 2:
            clusters.append(sorted(component))
    return clusters


def _taxonomy_horizon_trends(
    attention_rows: pd.DataFrame | None,
    candidates: pd.DataFrame,
    *,
    max_cohorts_per_horizon: int = 5,
) -> list[dict[str, Any]]:
    if not isinstance(attention_rows, pd.DataFrame) or attention_rows.empty:
        return []
    symbol_column = "entity_id" if "entity_id" in attention_rows.columns else ("symbol" if "symbol" in attention_rows.columns else "")
    if not symbol_column or "horizon" not in attention_rows.columns:
        return []

    frame = attention_rows.copy()
    frame["symbol"] = frame[symbol_column].map(_normalize_symbol)
    frame["horizon"] = frame["horizon"].map(_normalize_horizon_token)
    frame["peer_group_name"] = frame.get("peer_group_name", pd.Series(dtype=str)).map(
        lambda value: _coerce_text(value) or "All Market"
    )
    frame["attention_score"] = pd.to_numeric(frame.get("attention_score"), errors="coerce")
    frame["residual_zscore"] = pd.to_numeric(frame.get("residual_zscore"), errors="coerce")
    frame["observed_value"] = pd.to_numeric(frame.get("observed_value"), errors="coerce")
    frame["direction"] = frame.get("direction", pd.Series(dtype=str)).astype(str).str.lower().str.strip()
    frame["direction_score"] = frame["direction"].map({"up": 1, "down": -1}).fillna(0).astype(int)
    frame["abs_residual_zscore"] = frame["residual_zscore"].abs()

    candidate_symbols = {
        _normalize_symbol(value)
        for value in candidates.get("symbol", pd.Series(dtype=str)).tolist()
        if _normalize_symbol(value)
    }
    candidate_move_lookup = {
        _normalize_symbol(row.get("symbol")): _coerce_float(row.get("change_pct"))
        for _, row in candidates.iterrows()
        if _normalize_symbol(row.get("symbol"))
    }
    if candidate_symbols:
        frame = frame[frame["symbol"].isin(candidate_symbols)].copy()
    frame = frame[frame["symbol"].ne("") & frame["horizon"].ne("")].copy()
    if frame.empty:
        return []

    rows: list[dict[str, Any]] = []
    grouped = frame.groupby(["horizon", "peer_group_name"], dropna=False, sort=False)
    for (horizon, peer_group_name), group in grouped:
        ordered = group.sort_values(
            ["attention_score", "abs_residual_zscore", "symbol"],
            ascending=[False, False, True],
            na_position="last",
        )
        members = ordered["symbol"].dropna().astype(str).tolist()
        unique_members = list(dict.fromkeys([value for value in members if value]))
        if not unique_members:
            continue
        leader_symbol = unique_members[0]
        leader_row = ordered.iloc[0]
        leader_move = candidate_move_lookup.get(leader_symbol, _coerce_float(leader_row.get("observed_value")))
        breadth_up = int((ordered["direction_score"] > 0).sum())
        breadth_down = int((ordered["direction_score"] < 0).sum())
        mean_attention = float(pd.to_numeric(ordered["attention_score"], errors="coerce").mean(skipna=True))
        mean_abs_residual = float(pd.to_numeric(ordered["abs_residual_zscore"], errors="coerce").mean(skipna=True))
        if not np.isfinite(mean_attention):
            mean_attention = 0.0
        if not np.isfinite(mean_abs_residual):
            mean_abs_residual = 0.0
        ranking_score = mean_attention + mean_abs_residual * 8.0 + math.log1p(len(unique_members)) * 2.0
        rows.append(
            {
                "horizon": horizon,
                "peer_group_name": peer_group_name,
                "member_count": int(len(unique_members)),
                "breadth_up": breadth_up,
                "breadth_down": breadth_down,
                "mean_attention_score": round(mean_attention, 1),
                "mean_abs_residual_zscore": round(mean_abs_residual, 2),
                "leader_symbol": leader_symbol,
                "leader_move_pct": round(leader_move, 2) if np.isfinite(leader_move) else None,
                "ranking_score": ranking_score,
                "summary_text": (
                    f"{peer_group_name}: {len(unique_members)} names, "
                    f"{breadth_up} up / {breadth_down} down; leader {leader_symbol}"
                    + "."
                ),
            }
        )
    if not rows:
        return []

    trends = pd.DataFrame(rows)
    output: list[dict[str, Any]] = []
    for horizon in sorted(trends["horizon"].dropna().astype(str).unique().tolist(), key=lambda value: _TREND_HORIZON_ORDER.get(value, 99)):
        scoped = trends[trends["horizon"].astype(str) == horizon].copy()
        scoped = scoped.sort_values(["ranking_score", "member_count", "peer_group_name"], ascending=[False, False, True], na_position="last")
        scoped_rows = scoped.head(max(int(max_cohorts_per_horizon), 1)).copy()
        output.append(
            {
                "horizon": horizon,
                "horizon_label": _TREND_HORIZON_LABELS.get(horizon, horizon),
                "cohort_count": int(len(scoped_rows)),
                "cohorts": [
                    {
                        "peer_group_name": _coerce_text(row.get("peer_group_name")),
                        "member_count": int(row.get("member_count") or 0),
                        "breadth_up": int(row.get("breadth_up") or 0),
                        "breadth_down": int(row.get("breadth_down") or 0),
                        "mean_attention_score": float(row.get("mean_attention_score") or 0.0),
                        "mean_abs_residual_zscore": float(row.get("mean_abs_residual_zscore") or 0.0),
                        "leader_symbol": _coerce_text(row.get("leader_symbol")),
                        "leader_move_pct": row.get("leader_move_pct"),
                        "summary_text": _coerce_text(row.get("summary_text")),
                    }
                    for _, row in scoped_rows.iterrows()
                ],
            }
        )
    return output
