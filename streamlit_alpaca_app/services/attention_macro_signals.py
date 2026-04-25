"""
Attention macro signal checks.

These functions materialize observed-vs-expected macro relationships from raw
candidate moves. AQL consumes the checks later to write and verify hypotheses.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from .common.contracts import _MACRO_RELATIONSHIP_CHECK_COLUMNS, _MACRO_RELATIONSHIP_SCHEMA_VERSION
from .common.market_activity import _coerce_float, _coerce_text, _json_dumps, _normalize_symbol, _safe_list


def _empty_macro_relationship_checks_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_MACRO_RELATIONSHIP_CHECK_COLUMNS)


def _build_macro_relationship_checks(
    *,
    macro_release_bundles: list[dict[str, Any]],
    macro_causal_graph_edges_frame: pd.DataFrame | None,
    candidates: pd.DataFrame,
    asof_time_utc: pd.Timestamp,
    run_id: str,
) -> pd.DataFrame:
    if not macro_release_bundles:
        return _empty_macro_relationship_checks_frame()
    edges_frame = (
        macro_causal_graph_edges_frame.copy()
        if isinstance(macro_causal_graph_edges_frame, pd.DataFrame)
        else pd.DataFrame()
    )
    if edges_frame.empty:
        return _empty_macro_relationship_checks_frame()
    default_min_abs_change = 0.25
    edges_frame["expected_sign"] = pd.to_numeric(edges_frame.get("expected_sign"), errors="coerce").fillna(0.0)
    candidate_moves = pd.DataFrame()
    if isinstance(candidates, pd.DataFrame) and not candidates.empty:
        candidate_moves = candidates.copy()
        candidate_moves["symbol"] = candidate_moves.get("symbol", pd.Series(dtype=str)).map(_normalize_symbol)
        candidate_moves["change_pct"] = pd.to_numeric(candidate_moves.get("change_pct"), errors="coerce")
    rows: list[dict[str, Any]] = []
    for bundle in macro_release_bundles:
        release_event_id = _coerce_text(bundle.get("event_id"))
        release_type = _coerce_text(bundle.get("release_type"))
        primary_nodes = {_coerce_text(node).lower() for node in _safe_list(bundle.get("primary_nodes")) if _coerce_text(node)}
        release_direction = int(_coerce_float(bundle.get("release_direction"), 0.0))
        release_direction_sign = 1 if release_direction >= 0 else -1
        if not release_event_id or not primary_nodes:
            continue
        for _, edge in edges_frame.iterrows():
            from_node = _coerce_text(edge.get("from_node")).lower()
            to_node = _coerce_text(edge.get("to_node")).lower()
            if not from_node or from_node not in primary_nodes:
                continue
            edge_id = _coerce_text(edge.get("edge_id")) or f"{from_node}_to_{to_node or 'unknown'}"
            expected_sign = int(_coerce_float(edge.get("expected_sign"), 0.0))
            target_symbols_value = edge.get("target_symbols")
            if isinstance(target_symbols_value, str):
                try:
                    parsed_symbols = json.loads(target_symbols_value)
                except Exception:
                    parsed_symbols = [token.strip() for token in target_symbols_value.split(",")]
                target_symbols = {_normalize_symbol(value) for value in _safe_list(parsed_symbols) if _normalize_symbol(value)}
            else:
                target_symbols = {_normalize_symbol(value) for value in _safe_list(target_symbols_value) if _normalize_symbol(value)}
            min_abs_change = max(_coerce_float(edge.get("min_abs_change_pct"), default_min_abs_change), 0.0)
            observed_values = pd.Series(dtype=float)
            if not candidate_moves.empty and target_symbols:
                observed_values = candidate_moves[candidate_moves["symbol"].isin(target_symbols)]["change_pct"].dropna()
            evidence_symbols = (
                candidate_moves[candidate_moves["symbol"].isin(target_symbols)]["symbol"].dropna().astype(str).tolist()
                if not candidate_moves.empty and target_symbols
                else []
            )
            if observed_values.empty:
                observed_sign = 0
                observed_strength = math.nan
                consistency_status = "unresolved"
            else:
                observed_mean = float(observed_values.mean())
                observed_sign = 1 if observed_mean > 0 else (-1 if observed_mean < 0 else 0)
                observed_strength = float(observed_values.abs().mean())
                effective_expected_sign = expected_sign * release_direction_sign if expected_sign != 0 else 0
                if effective_expected_sign == 0:
                    consistency_status = "mixed"
                elif abs(observed_mean) < min_abs_change:
                    consistency_status = "mixed"
                elif observed_sign == effective_expected_sign:
                    consistency_status = "holding"
                else:
                    consistency_status = "broken"
            rows.append(
                {
                    "run_id": run_id,
                    "release_event_id": release_event_id,
                    "release_type": release_type,
                    "edge_id": edge_id,
                    "from_node": from_node,
                    "to_node": to_node,
                    "expected_sign": expected_sign,
                    "observed_sign": observed_sign,
                    "observed_strength": observed_strength,
                    "consistency_status": consistency_status,
                    "regime_used": _coerce_text(edge.get("regime_filter")) or "all",
                    "lag_window": _coerce_text(edge.get("lag_window")) or "same_day",
                    "strength_weight": _coerce_float(edge.get("strength_weight"), 1.0),
                    "confidence_prior": _coerce_float(edge.get("confidence_prior"), 0.5),
                    "evidence_symbols": _json_dumps(list(dict.fromkeys(evidence_symbols))),
                    "asof_time_utc": asof_time_utc,
                    "schema_version": _MACRO_RELATIONSHIP_SCHEMA_VERSION,
                }
            )
    if not rows:
        return _empty_macro_relationship_checks_frame()
    frame = pd.DataFrame(rows)
    for column in _MACRO_RELATIONSHIP_CHECK_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    return frame[_MACRO_RELATIONSHIP_CHECK_COLUMNS].reset_index(drop=True)
