"""
Attention signal graph construction.

This module owns the structural relationship edges between attention candidates.
AQL can add claims and narratives later, but the graph itself is a signal artifact.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .common.market_activity import (
    _candidate_graph_tags,
    _candidate_taxonomy_context,
    _coerce_float,
    _coerce_text,
    _jaccard,
    _json_dumps,
    _move_direction,
    _normalize_symbol,
    _safe_list,
)
from .runtime_policy import attention_graph_policy


def _claim_entities(claims: list[dict[str, Any]]) -> set[str]:
    entities: set[str] = set()
    for item in claims:
        if not isinstance(item, dict):
            continue
        for entity in _safe_list(item.get("claim_entities")):
            clean = _coerce_text(entity).lower()
            if clean:
                entities.add(clean)
    return entities


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
