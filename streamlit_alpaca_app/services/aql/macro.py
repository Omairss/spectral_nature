"""
AQL macro — macro release event detection, causal graph edges, and hypothesis verification.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from ..attention_macro_signals import _build_macro_relationship_checks
from ..common.contracts import _MACRO_RELATIONSHIP_CHECK_COLUMNS
from .constants import (
    LLMClient,
    MACRO_HYPOTHESIS_EVIDENCE_SCHEMA,
    _ATTENTION_MACRO_CONTEXT_COLUMNS,
    _MACRO_CAUSAL_GRAPH_EDGE_COLUMNS,
    _MACRO_CAUSAL_GRAPH_SCHEMA_VERSION,
    _MACRO_HYPOTHESES_COLUMNS,
    _MACRO_HYPOTHESES_SCHEMA_VERSION,
    _MACRO_RELEASE_EVENT_COLUMNS,
    _MACRO_RELEASE_SCHEMA_VERSION,
)
from ..llm import LLMAPIError
from ..aql_zopedia_engine import load_aql_zopedia_llm_client
from ._shared import (
    _coerce_float,
    _coerce_text,
    _compose_surface_summary,
    _evidence_text,
    _is_provider_error_text,
    _json_dumps,
    _normalize_symbol,
    _normalized_text,
    _safe_list,
    _text_overlap,
    _trim,
)


def _empty_macro_release_events_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_MACRO_RELEASE_EVENT_COLUMNS)


def _empty_macro_causal_graph_edges_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_MACRO_CAUSAL_GRAPH_EDGE_COLUMNS)


def _empty_macro_relationship_checks_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_MACRO_RELATIONSHIP_CHECK_COLUMNS)


def _empty_attention_hypotheses_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_MACRO_HYPOTHESES_COLUMNS)


def _empty_attention_macro_context_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_ATTENTION_MACRO_CONTEXT_COLUMNS)


def _macro_release_tier_priority(value: object, *, tier_priority: dict[str, Any]) -> int:
    return int(tier_priority.get(_coerce_text(value).lower(), 0))


def _macro_release_max_tier(values: list[object], *, tier_priority: dict[str, Any]) -> str:
    tiers = [_coerce_text(value).lower() for value in values if _coerce_text(value).lower() in tier_priority]
    if not tiers:
        return "low"
    return sorted(tiers, key=lambda value: _macro_release_tier_priority(value, tier_priority=tier_priority), reverse=True)[0]


def _macro_release_timestamp(row: pd.Series) -> pd.Timestamp:
    for column in ["release_time_utc", "published_at", "last_updated", "asof_time_utc"]:
        parsed = pd.to_datetime(row.get(column), utc=True, errors="coerce")
        if pd.notna(parsed):
            return parsed
    latest_date = pd.to_datetime(row.get("latest_date"), utc=True, errors="coerce")
    if pd.notna(latest_date):
        # FRED summary rows often carry date-only values; use a fixed morning UTC anchor.
        return latest_date.floor("D") + pd.Timedelta(hours=13, minutes=30)
    return pd.NaT


def _format_release_value(value: object, units_short: object) -> str:
    numeric = _coerce_float(value)
    if not np.isfinite(numeric):
        return "n/a"
    units = _coerce_text(units_short).lower()
    if "percent" in units:
        return f"{numeric:.2f}%"
    if "thousand" in units:
        return f"{numeric:,.0f}k"
    if "million" in units:
        return f"{numeric:,.2f}m"
    if "billion" in units:
        return f"{numeric:,.2f}b"
    if abs(numeric) >= 1000:
        return f"{numeric:,.0f}"
    return f"{numeric:.2f}"


def _format_release_delta(value: object, units_short: object) -> str:
    numeric = _coerce_float(value)
    if not np.isfinite(numeric):
        return "n/a"
    units = _coerce_text(units_short).lower()
    if "percent" in units:
        return f"{numeric:+.2f} pp"
    if "thousand" in units:
        return f"{numeric:+,.0f}k"
    if "million" in units:
        return f"{numeric:+,.2f}m"
    if "billion" in units:
        return f"{numeric:+,.2f}b"
    return f"{numeric:+.2f}"


def _macro_release_symbol_groups(profile: dict[str, Any]) -> dict[str, set[str]]:
    groups_raw = profile.get("surprise_symbol_groups")
    if not isinstance(groups_raw, dict):
        return {}
    groups: dict[str, set[str]] = {}
    for key, values in groups_raw.items():
        key_text = _coerce_text(key).lower()
        normalized_values = {_normalize_symbol(value) for value in _safe_list(values) if _normalize_symbol(value)}
        if key_text and normalized_values:
            groups[key_text] = normalized_values
    return groups


def _macro_release_anchor_rows(candidates: pd.DataFrame, *, symbol_groups: dict[str, set[str]]) -> pd.DataFrame:
    if not isinstance(candidates, pd.DataFrame) or candidates.empty:
        return pd.DataFrame(columns=["symbol", "change_pct", "abs_change_pct", "source_label", "candidate_score"])
    rows = candidates.copy()
    rows["symbol"] = rows.get("symbol", pd.Series(dtype=str)).map(_normalize_symbol)
    rows["change_pct"] = pd.to_numeric(rows.get("change_pct"), errors="coerce")
    rows["abs_change_pct"] = rows["change_pct"].abs()
    if "candidate_score" in rows.columns:
        rows["candidate_score"] = pd.to_numeric(rows.get("candidate_score"), errors="coerce")
    else:
        rows["candidate_score"] = math.nan
    if "source_label" in rows.columns:
        mask = rows["source_label"].astype(str).str.lower().eq("macro anchor")
        scoped = rows[mask].copy()
        if not scoped.empty:
            return scoped
    symbol_pool = set().union(*symbol_groups.values()) if symbol_groups else set()
    if symbol_pool:
        scoped = rows[rows["symbol"].isin(symbol_pool)].copy()
        return scoped if not scoped.empty else rows.copy()
    return rows.copy()


def _macro_release_cross_asset_surprise(
    candidates: pd.DataFrame,
    *,
    symbol_groups: dict[str, set[str]],
    reaction_cap_move: float,
) -> tuple[float, float]:
    anchors = _macro_release_anchor_rows(candidates, symbol_groups=symbol_groups)
    if anchors.empty:
        return 0.0, 0.0
    bucket_moves: list[float] = []
    for symbols in symbol_groups.values():
        scoped = anchors[anchors["symbol"].isin(symbols)]["abs_change_pct"].dropna()
        if not scoped.empty:
            bucket_moves.append(float(scoped.max()))
    if bucket_moves:
        reaction_pct = float(np.mean(bucket_moves))
    else:
        reaction_series = anchors["abs_change_pct"].dropna().sort_values(ascending=False).head(3)
        if reaction_series.empty:
            return 0.0, 0.0
        reaction_pct = float(reaction_series.mean())
    cap_move = max(float(reaction_cap_move), 0.1)
    surprise_score = min(max(reaction_pct / cap_move, 0.0), 1.0) * 100.0
    return round(surprise_score, 2), round(reaction_pct, 4)


def _row_first_finite(row: pd.Series, columns: list[str]) -> float:
    for column in columns:
        value = _coerce_float(row.get(column))
        if np.isfinite(value):
            return value
    return math.nan


def _macro_release_surprise_metrics(
    row: pd.Series,
    candidates: pd.DataFrame,
    *,
    symbol_groups: dict[str, set[str]],
    reaction_cap_move: float,
) -> dict[str, Any]:
    actual = _row_first_finite(row, ["actual", "actual_value", "latest_value"])
    consensus = _row_first_finite(row, ["consensus", "consensus_value", "survey_median", "forecast"])
    sigma = _row_first_finite(row, ["forecast_error_std_rolling", "consensus_std", "series_min_sigma"])
    if np.isfinite(actual) and np.isfinite(consensus):
        denominator = max(abs(sigma), 1e-6) if np.isfinite(sigma) else 1e-6
        surprise_z = abs(actual - consensus) / denominator
        surprise_score = min(max(surprise_z / 2.5, 0.0), 1.0) * 100.0
        return {
            "surprise_score": round(surprise_score, 2),
            "surprise_z": round(float(surprise_z), 4),
            "surprise_source": "consensus",
            "cross_asset_reaction_pct": math.nan,
        }
    surprise_score, reaction_pct = _macro_release_cross_asset_surprise(
        candidates,
        symbol_groups=symbol_groups,
        reaction_cap_move=reaction_cap_move,
    )
    return {
        "surprise_score": surprise_score,
        "surprise_z": math.nan,
        "surprise_source": "cross_asset_reaction",
        "cross_asset_reaction_pct": reaction_pct,
    }


def _release_direction_from_components(components: list[dict[str, Any]]) -> int:
    deltas = [_coerce_float(item.get("prev_delta")) for item in components]
    finite = [value for value in deltas if np.isfinite(value)]
    if not finite:
        return 0
    mean_delta = float(np.mean(finite))
    if mean_delta > 0:
        return 1
    if mean_delta < 0:
        return -1
    return 0


def _macro_release_component_rows(
    fred_summary_frame: pd.DataFrame | None,
    candidates: pd.DataFrame,
    *,
    asof_time_utc: pd.Timestamp,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(fred_summary_frame, pd.DataFrame) or fred_summary_frame.empty:
        return []
    release_components_raw = profile.get("release_components")
    release_components = release_components_raw if isinstance(release_components_raw, dict) else {}
    release_rules = profile.get("release_rules") if isinstance(profile.get("release_rules"), dict) else {}
    freshness_hours_limit = max(_coerce_float(release_rules.get("freshness_hours"), 36.0), 1.0)
    release_time_lookahead_hours = _coerce_float(release_rules.get("release_time_lookahead_hours"), 2.0)
    symbol_groups = _macro_release_symbol_groups(profile)
    reaction_cap_move = max(_coerce_float(release_rules.get("reaction_cap_move"), 1.5), 0.1)
    frame = fred_summary_frame.copy()
    frame["series_id"] = frame.get("series_id", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
    rows: list[dict[str, Any]] = []
    for _, item in frame.iterrows():
        series_id = _coerce_text(item.get("series_id")).upper()
        spec = release_components.get(series_id)
        if not spec:
            continue
        release_time_utc = _macro_release_timestamp(item)
        if pd.isna(release_time_utc):
            continue
        freshness_hours = float((asof_time_utc - release_time_utc).total_seconds() / 3600.0)
        if freshness_hours < -release_time_lookahead_hours or freshness_hours > freshness_hours_limit:
            continue
        surprise = _macro_release_surprise_metrics(
            item,
            candidates,
            symbol_groups=symbol_groups,
            reaction_cap_move=reaction_cap_move,
        )
        rows.append(
            {
                "series_id": series_id,
                "release_type": _coerce_text(spec.get("release_type")),
                "component_label": _coerce_text(spec.get("component_label")) or series_id,
                "importance_tier": _coerce_text(spec.get("importance_tier")).lower() or "low",
                "primary_nodes": list(spec.get("primary_nodes") or []),
                "release_time_utc": release_time_utc,
                "freshness_hours": round(freshness_hours, 2),
                "latest_value": item.get("latest_value"),
                "prev_delta": item.get("prev_delta"),
                "units_short": item.get("units_short"),
                "surprise_score": float(surprise.get("surprise_score") or 0.0),
                "surprise_z": surprise.get("surprise_z"),
                "surprise_source": _coerce_text(surprise.get("surprise_source")) or "cross_asset_reaction",
                "cross_asset_reaction_pct": surprise.get("cross_asset_reaction_pct"),
            }
        )
    return rows


def _macro_release_supporting_rows(
    candidates: pd.DataFrame,
    *,
    symbol_groups: dict[str, set[str]],
    limit: int = 6,
) -> pd.DataFrame:
    anchors = _macro_release_anchor_rows(candidates, symbol_groups=symbol_groups)
    if anchors.empty:
        return pd.DataFrame(columns=["symbol", "change_pct", "abs_change_pct"])
    return anchors.sort_values(["abs_change_pct", "candidate_score"], ascending=[False, False], na_position="last").head(max(int(limit), 1)).copy()


_LLM_MACRO_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_market_moving": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_market_moving", "reason"],
}
_macro_market_moving_cache: dict[str, bool] = {}


def _llm_macro_release_is_market_moving(
    *,
    release_name: str,
    importance_tier: str,
    surprise_score: float,
    surprise_threshold: float,
    component_text: str,
) -> bool | None:
    """Use LLM to assess whether a macro release warrants promotion to top events.

    Returns None if LLM is unavailable or errors — caller should fall back to rule.
    """
    cache_key = f"{release_name}::{importance_tier}::{round(surprise_score, 1)}"
    if cache_key in _macro_market_moving_cache:
        return _macro_market_moving_cache[cache_key]
    llm_client = load_aql_zopedia_llm_client(surface="aql.macro_release_assessment")
    if llm_client is None:
        return None
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "You are a macro analyst. Assess whether a macro data release warrants immediate market attention. "
                "Consider the release importance tier, surprise score relative to threshold, and the component data. "
                "Return true if the release is likely to be market-moving and should be promoted to top events."
            ),
            user_prompt=(
                f"Release: {release_name}\n"
                f"Importance tier: {importance_tier}\n"
                f"Surprise score: {surprise_score:.1f} (threshold: {surprise_threshold:.1f})\n"
                f"Components: {component_text[:400] if component_text else 'N/A'}"
            ),
            schema_name="macro_market_moving",
            schema=_LLM_MACRO_ASSESSMENT_SCHEMA,
        )
        is_market_moving = bool(result.get("is_market_moving"))
    except (LLMAPIError, Exception):
        return None
    _macro_market_moving_cache[cache_key] = is_market_moving
    return is_market_moving


def _build_macro_release_events(
    *,
    fred_summary_frame: pd.DataFrame | None,
    candidates: pd.DataFrame,
    asof_time_utc: pd.Timestamp,
    run_id: str,
    profile: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    components = _macro_release_component_rows(
        fred_summary_frame,
        candidates,
        asof_time_utc=asof_time_utc,
        profile=profile,
    )
    if not components:
        return _empty_macro_release_events_frame(), []
    tier_priority = profile.get("release_type_priority") if isinstance(profile.get("release_type_priority"), dict) else {}
    release_display_names = profile.get("release_display_names") if isinstance(profile.get("release_display_names"), dict) else {}
    release_base_scores = profile.get("release_type_base_score") if isinstance(profile.get("release_type_base_score"), dict) else {}
    release_rules = profile.get("release_rules") if isinstance(profile.get("release_rules"), dict) else {}
    surprise_threshold = _coerce_float(release_rules.get("surprise_threshold"), 60.0)
    supporting_symbol_limit = max(int(_coerce_float(release_rules.get("supporting_symbol_limit"), 6)), 1)
    symbol_groups = _macro_release_symbol_groups(profile)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in components:
        grouped.setdefault(_coerce_text(item.get("release_type")), []).append(item)

    bundles: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    for release_type, items in grouped.items():
        if not release_type:
            continue
        items_sorted = sorted(
            items,
            key=lambda value: (
                -float(value.get("surprise_score") or 0.0),
                -_macro_release_tier_priority(value.get("importance_tier"), tier_priority=tier_priority),
                _coerce_text(value.get("series_id")),
            ),
        )
        release_time_utc = max(pd.to_datetime(value.get("release_time_utc"), utc=True, errors="coerce") for value in items_sorted)
        if pd.isna(release_time_utc):
            continue
        release_time_label = release_time_utc.strftime("%Y%m%dT%H%M%SZ")
        release_event_id = f"macro_release::{release_type}::{release_time_label}"
        bundle_id = f"event::{release_event_id}"
        component_labels = [str(value.get("component_label") or value.get("series_id")) for value in items_sorted]
        component_series_ids = [str(value.get("series_id") or "") for value in items_sorted if str(value.get("series_id") or "").strip()]
        importance_tier = _macro_release_max_tier([value.get("importance_tier") for value in items_sorted], tier_priority=tier_priority)
        surprise_score = max(float(value.get("surprise_score") or 0.0) for value in items_sorted)
        surprise_source = _coerce_text(items_sorted[0].get("surprise_source")) or "cross_asset_reaction"
        surprise_z = items_sorted[0].get("surprise_z")
        cross_asset_reaction_pct = items_sorted[0].get("cross_asset_reaction_pct")
        release_direction = _release_direction_from_components(items_sorted)
        primary_nodes = list(
            dict.fromkeys(
                node
                for value in items_sorted
                for node in _safe_list(value.get("primary_nodes"))
                if _coerce_text(node)
            )
        )
        supporting_rows = _macro_release_supporting_rows(
            candidates,
            symbol_groups=symbol_groups,
            limit=supporting_symbol_limit,
        )
        supporting_symbols = supporting_rows.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist()
        beneficiary_symbols = (
            supporting_rows[supporting_rows["change_pct"] >= 0]["symbol"].dropna().astype(str).head(3).tolist()
            if "change_pct" in supporting_rows.columns
            else []
        )
        loser_symbols = (
            supporting_rows[supporting_rows["change_pct"] < 0]["symbol"].dropna().astype(str).head(3).tolist()
            if "change_pct" in supporting_rows.columns
            else []
        )
        driver_symbols = supporting_rows.get("symbol", pd.Series(dtype=str)).dropna().astype(str).head(3).tolist()
        release_name = _coerce_text(release_display_names.get(release_type) or release_type.replace("_", " ").title())
        component_text = ", ".join(
            f"{_coerce_text(component.get('component_label'))}: {_format_release_value(component.get('latest_value'), component.get('units_short'))}"
            f" ({_format_release_delta(component.get('prev_delta'), component.get('units_short'))} vs prior)"
            for component in items_sorted[:2]
        )
        what_happened_text = _trim(
            f"{release_name} updated. {component_text}" if component_text else f"{release_name} updated.",
            320,
        )
        rule_forced = importance_tier == "high" and surprise_score >= surprise_threshold
        # For borderline cases (high tier, score within 20% below threshold), ask LLM.
        in_ambiguous_zone = (
            importance_tier == "high"
            and not rule_forced
            and surprise_score >= surprise_threshold * 0.8
        )
        if in_ambiguous_zone:
            llm_assessment = _llm_macro_release_is_market_moving(
                release_name=release_name,
                importance_tier=importance_tier,
                surprise_score=surprise_score,
                surprise_threshold=surprise_threshold,
                component_text=component_text,
            )
            is_forced = llm_assessment if llm_assessment is not None else rule_forced
        else:
            is_forced = rule_forced
        promotion_reason = (
            f"Forced into top events: {importance_tier} importance and surprise_score {surprise_score:.1f} >= {surprise_threshold:.1f}."
            if is_forced
            else f"Tracked release: surprise_score {surprise_score:.1f} below promotion threshold {surprise_threshold:.1f} or lower importance."
        )
        why_happened_text = _trim(
            (
                f"{release_name} is high importance and early cross-asset reaction is elevated, "
                f"so this release is promoted to top events for immediate review."
            )
            if is_forced
            else (
                f"{release_name} is tracked for macro context. Early reaction is still developing; "
                "this release remains available in release diagnostics."
            ),
            320,
        )
        if supporting_symbols:
            affected_assets_summary_text = _trim(
                f"Initial reaction is concentrated in {', '.join(supporting_symbols[:6])}.",
                220,
            )
        else:
            affected_assets_summary_text = "Cross-asset reaction is still developing."
        event_score = min(
            _coerce_float(release_base_scores.get(importance_tier), 42.0) + surprise_score * 0.35 + min(len(supporting_symbols), 6) * 1.4,
            100.0,
        )
        hypothesis = _trim(
            f"{release_name} shock should transmit through {', '.join(primary_nodes[:4])} and can be validated against near-live cross-asset moves.",
            240,
        )
        bundle = {
            "bundle_id": bundle_id,
            "bundle_type": "event",
            "event_id": release_event_id,
            "run_id": run_id,
            "event_type": "macro_release",
            "event_title": release_name,
            "event_score": round(event_score, 1),
            "what_happened_text": what_happened_text,
            "why_happened_text": why_happened_text,
            "affected_assets_summary_text": affected_assets_summary_text,
            "surface_summary_text": _compose_surface_summary(what_happened_text, why_happened_text),
            "cause_status": "supported" if is_forced else "continuation",
            "confidence_label": "High" if is_forced else "Developing",
            "source_summary": "FRED",
            "source_count": 1,
            "evidence_count": len(component_series_ids),
            "same_day_evidence_count": 0,
            "supporting_symbols": list(dict.fromkeys(supporting_symbols)),
            "driver_symbols": list(dict.fromkeys(driver_symbols)),
            "beneficiary_symbols": list(dict.fromkeys(beneficiary_symbols)),
            "loser_symbols": list(dict.fromkeys(loser_symbols)),
            "claims": [],
            "supporting_claim_ids": [],
            "release_type": release_type,
            "release_time_utc": release_time_utc.isoformat(),
            "importance_tier": importance_tier,
            "surprise_score": round(surprise_score, 2),
            "surprise_source": surprise_source,
            "surprise_z": surprise_z if np.isfinite(_coerce_float(surprise_z)) else None,
            "cross_asset_reaction_pct": cross_asset_reaction_pct if np.isfinite(_coerce_float(cross_asset_reaction_pct)) else None,
            "primary_nodes": primary_nodes,
            "release_direction": release_direction,
            "initial_hypothesis": hypothesis,
            "status": "supported" if is_forced else "continuation",
            "is_forced_macro_release": bool(is_forced),
            "promotion_reason": promotion_reason,
            "source_dataset": "fred_summary",
            "component_series_ids": component_series_ids,
            "component_labels": component_labels,
        }
        bundles.append(bundle)
        frame_rows.append(
            {
                "run_id": run_id,
                "release_event_id": release_event_id,
                "release_type": release_type,
                "release_time_utc": release_time_utc,
                "surprise_score": round(surprise_score, 2),
                "importance_tier": importance_tier,
                "primary_nodes": _json_dumps(primary_nodes),
                "initial_hypothesis": hypothesis,
                "status": "supported" if is_forced else "continuation",
                "asof_time_utc": asof_time_utc,
                "schema_version": _MACRO_RELEASE_SCHEMA_VERSION,
                "is_forced_macro_release": bool(is_forced),
                "promotion_reason": promotion_reason,
                "source_dataset": "fred_summary",
                "component_series_ids": _json_dumps(component_series_ids),
                "component_labels": _json_dumps(component_labels),
                "supporting_symbols": _json_dumps(supporting_symbols),
                "surprise_source": surprise_source,
                "surprise_z": surprise_z if np.isfinite(_coerce_float(surprise_z)) else math.nan,
                "cross_asset_reaction_pct": cross_asset_reaction_pct if np.isfinite(_coerce_float(cross_asset_reaction_pct)) else math.nan,
                "release_direction": release_direction,
            }
        )
    if not bundles:
        return _empty_macro_release_events_frame(), []
    bundles = sorted(
        bundles,
        key=lambda item: (
            0 if bool(item.get("is_forced_macro_release")) else 1,
            -_coerce_float(item.get("surprise_score"), 0.0),
            -_coerce_float(item.get("event_score"), 0.0),
            _coerce_text(item.get("release_type")),
        ),
    )
    frame = pd.DataFrame(frame_rows)
    for column in _MACRO_RELEASE_EVENT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    frame = frame[_MACRO_RELEASE_EVENT_COLUMNS].copy()
    return frame.reset_index(drop=True), bundles


def _sign_from_word(value: object) -> int:
    normalized = _coerce_text(value).lower()
    if normalized in {"positive", "up", "+", "1", "plus"}:
        return 1
    if normalized in {"negative", "down", "-", "-1", "minus"}:
        return -1
    return 0


def _materialize_macro_causal_graph_edges(
    *,
    asof_time_utc: pd.Timestamp,
    run_id: str,
    profile: dict[str, Any],
) -> pd.DataFrame:
    del asof_time_utc
    relationship_cfg = profile.get("relationship_checks") if isinstance(profile.get("relationship_checks"), dict) else {}
    edges = relationship_cfg.get("edges")
    if not isinstance(edges, list) or not edges:
        return _empty_macro_causal_graph_edges_frame()
    rows: list[dict[str, Any]] = []
    for item in edges:
        if not isinstance(item, dict):
            continue
        from_node = _coerce_text(item.get("from_node")).lower()
        to_node = _coerce_text(item.get("to_node")).lower()
        edge_id = _coerce_text(item.get("edge_id")) or f"{from_node}_to_{to_node or 'unknown'}"
        if not from_node or not to_node or not edge_id:
            continue
        rows.append(
            {
                "run_id": run_id,
                "edge_id": edge_id,
                "from_node": from_node,
                "to_node": to_node,
                "expected_sign": _sign_from_word(item.get("expected_sign")),
                "lag_window": _coerce_text(item.get("lag_window")) or "same_day",
                "regime_filter": _coerce_text(item.get("regime_filter")) or "all",
                "min_abs_change_pct": _coerce_float(item.get("min_abs_change_pct"), math.nan),
                "strength_weight": _coerce_float(item.get("strength_weight"), 1.0),
                "confidence_prior": _coerce_float(item.get("confidence_prior"), 0.5),
                "target_symbols": _json_dumps([_normalize_symbol(value) for value in _safe_list(item.get("target_symbols")) if _normalize_symbol(value)]),
                "source": "attention_macro_signal_profile.v1",
                "schema_version": _MACRO_CAUSAL_GRAPH_SCHEMA_VERSION,
            }
        )
    if not rows:
        return _empty_macro_causal_graph_edges_frame()
    frame = pd.DataFrame(rows)
    for column in _MACRO_CAUSAL_GRAPH_EDGE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    return frame[_MACRO_CAUSAL_GRAPH_EDGE_COLUMNS].reset_index(drop=True)


def _build_attention_hypotheses_from_relationship_checks(
    *,
    macro_release_bundles: list[dict[str, Any]],
    relationship_checks_frame: pd.DataFrame,
    asof_time_utc: pd.Timestamp,
    run_id: str,
    profile: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    if not macro_release_bundles:
        return _empty_attention_hypotheses_frame(), {}
    hypothesis_cfg = profile.get("hypothesis_rules") if isinstance(profile.get("hypothesis_rules"), dict) else {}
    supported_min = _coerce_float(hypothesis_cfg.get("supported_min_support_score"), 0.7)
    supported_max_contradiction = _coerce_float(hypothesis_cfg.get("supported_max_contradiction_score"), 0.25)
    conflicting_min = _coerce_float(hypothesis_cfg.get("conflicting_min_contradiction_score"), 0.45)
    continuation_min = _coerce_float(hypothesis_cfg.get("continuation_min_support_score"), 0.35)
    checks = relationship_checks_frame.copy() if isinstance(relationship_checks_frame, pd.DataFrame) else _empty_macro_relationship_checks_frame()
    rows: list[dict[str, Any]] = []
    summary_by_release: dict[str, dict[str, Any]] = {}
    for bundle in macro_release_bundles:
        release_event_id = _coerce_text(bundle.get("event_id"))
        if not release_event_id:
            continue
        scoped = checks[checks["release_event_id"].astype(str) == release_event_id].copy() if not checks.empty else pd.DataFrame()
        holding_count = int(scoped["consistency_status"].astype(str).str.lower().eq("holding").sum()) if not scoped.empty else 0
        mixed_count = int(scoped["consistency_status"].astype(str).str.lower().eq("mixed").sum()) if not scoped.empty else 0
        broken_count = int(scoped["consistency_status"].astype(str).str.lower().eq("broken").sum()) if not scoped.empty else 0
        unresolved_count = int(scoped["consistency_status"].astype(str).str.lower().eq("unresolved").sum()) if not scoped.empty else 0
        evidence_count = holding_count + mixed_count + broken_count
        if evidence_count <= 0:
            support_score = 0.0
            contradiction_score = 0.0
            support_status = "unresolved"
        else:
            support_score = (holding_count + 0.5 * mixed_count) / evidence_count
            contradiction_score = broken_count / evidence_count
            if contradiction_score >= conflicting_min:
                support_status = "conflicting"
            elif support_score >= supported_min and contradiction_score <= supported_max_contradiction:
                support_status = "supported"
            elif support_score >= continuation_min:
                support_status = "continuation"
            else:
                support_status = "unresolved"
        release_name = _coerce_text(bundle.get("event_title") or bundle.get("release_type") or "Macro release")
        hypothesis_text = _trim(
            (
                f"{release_name}: transmission checks show {holding_count} holding, {mixed_count} mixed, "
                f"and {broken_count} broken relationships across linked macro nodes."
            ),
            260,
        )
        hypothesis_id = f"hypothesis::{release_event_id}"
        row = {
            "run_id": run_id,
            "hypothesis_id": hypothesis_id,
            "candidate_id": _coerce_text(bundle.get("bundle_id")) or _coerce_text(bundle.get("event_id")),
            "release_event_id": release_event_id,
            "hypothesis_text": hypothesis_text,
            "support_status": support_status,
            "support_score": round(float(support_score), 4),
            "contradiction_score": round(float(contradiction_score), 4),
            "evidence_count": int(evidence_count),
            "asof_time_utc": asof_time_utc,
            "schema_version": _MACRO_HYPOTHESES_SCHEMA_VERSION,
        }
        rows.append(row)
        summary_by_release[release_event_id] = {
            "support_status": support_status,
            "support_score": row["support_score"],
            "contradiction_score": row["contradiction_score"],
            "evidence_count": int(evidence_count),
            "holding_count": holding_count,
            "mixed_count": mixed_count,
            "broken_count": broken_count,
            "unresolved_count": unresolved_count,
            "hypothesis_text": hypothesis_text,
        }
    if not rows:
        return _empty_attention_hypotheses_frame(), {}
    frame = pd.DataFrame(rows)
    for column in _MACRO_HYPOTHESES_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    return frame[_MACRO_HYPOTHESES_COLUMNS].reset_index(drop=True), summary_by_release


def _hypothesis_status_from_scores(
    *,
    support_score: float,
    contradiction_score: float,
    supported_min: float,
    supported_max_contradiction: float,
    conflicting_min: float,
    continuation_min: float,
) -> str:
    if contradiction_score >= conflicting_min:
        return "conflicting"
    if support_score >= supported_min and contradiction_score <= supported_max_contradiction:
        return "supported"
    if support_score >= continuation_min:
        return "continuation"
    return "unresolved"


def _macro_hypothesis_query(bundle: dict[str, Any], row: pd.Series) -> str:
    release_name = _coerce_text(bundle.get("event_title") or bundle.get("release_type") or "macro release")
    release_type = _coerce_text(bundle.get("release_type")).replace("_", " ")
    support_symbols = [_normalize_symbol(value) for value in _safe_list(bundle.get("supporting_symbols")) if _normalize_symbol(value)]
    symbol_text = " ".join(support_symbols[:3])
    primary_nodes = [_coerce_text(value) for value in _safe_list(bundle.get("primary_nodes")) if _coerce_text(value)]
    node_text = " ".join(primary_nodes[:3])
    release_time = _coerce_text(bundle.get("release_time_utc"))
    date_text = release_time.split("T", 1)[0] if "T" in release_time else release_time
    hypothesis_text = _coerce_text(row.get("hypothesis_text"))
    components = [
        release_name,
        release_type,
        date_text,
        node_text,
        symbol_text,
        hypothesis_text,
        "market reaction",
    ]
    return _trim(" ".join(part for part in components if part), 240)


def _llm_macro_evidence_verdicts(
    *,
    llm_client: LLMClient | None,
    hypothesis_text: str,
    result_rows: list[dict[str, Any]],
) -> dict[str, str]:
    verdict_map: dict[str, str] = {}
    if llm_client is None or not result_rows:
        return verdict_map
    user_prompt = json.dumps(
        {
            "hypothesis_text": hypothesis_text,
            "results": [
                {
                    "result_id": _coerce_text(row.get("result_id")),
                    "title": _coerce_text(row.get("title")),
                    "snippet": _coerce_text(row.get("snippet")),
                    "source": _coerce_text(row.get("source")),
                }
                for row in result_rows[:8]
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    system_prompt = (
        "Classify each result against the hypothesis as support, contradict, or neutral. "
        "Use only the provided text and return strict JSON."
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="macro_hypothesis_evidence",
            schema=MACRO_HYPOTHESIS_EVIDENCE_SCHEMA,
        )
    except Exception:
        return verdict_map
    for item in list(data.get("verdicts") or []):
        if not isinstance(item, dict):
            continue
        result_id = _coerce_text(item.get("result_id"))
        label = _coerce_text(item.get("label")).lower()
        if not result_id or label not in {"support", "contradict", "neutral"}:
            continue
        verdict_map[result_id] = label
    return verdict_map


def _verify_macro_hypotheses_with_web_evidence(
    *,
    hypotheses_frame: pd.DataFrame,
    macro_release_bundles: list[dict[str, Any]],
    asof_time_utc: pd.Timestamp,
    run_id: str,
    profile: dict[str, Any],
    llm_client: LLMClient | None,
    serp_client: object | None,
    tavily_client: object | None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from .collector import _search_query_results
    from .extractor import _serialize_claims_frame

    if not isinstance(hypotheses_frame, pd.DataFrame) or hypotheses_frame.empty:
        return _empty_attention_hypotheses_frame(), {}, [], [], []
    bundle_by_release_id = {
        _coerce_text(item.get("event_id")): dict(item or {})
        for item in macro_release_bundles
        if _coerce_text(item.get("event_id"))
    }
    hypothesis_cfg = profile.get("hypothesis_rules") if isinstance(profile.get("hypothesis_rules"), dict) else {}
    supported_min = _coerce_float(hypothesis_cfg.get("supported_min_support_score"), 0.7)
    supported_max_contradiction = _coerce_float(hypothesis_cfg.get("supported_max_contradiction_score"), 0.25)
    conflicting_min = _coerce_float(hypothesis_cfg.get("conflicting_min_contradiction_score"), 0.45)
    continuation_min = _coerce_float(hypothesis_cfg.get("continuation_min_support_score"), 0.35)

    verified_rows: list[dict[str, Any]] = []
    summary_by_release: dict[str, dict[str, Any]] = {}
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    macro_claim_rows: list[dict[str, Any]] = []

    for _, row in hypotheses_frame.iterrows():
        row_data = row.to_dict()
        release_event_id = _coerce_text(row.get("release_event_id"))
        hypothesis_id = _coerce_text(row.get("hypothesis_id"))
        bundle = bundle_by_release_id.get(release_event_id, {})
        if not release_event_id or not hypothesis_id:
            continue
        query = _macro_hypothesis_query(bundle, row)
        query_requests, query_results = _search_query_results(
            query,
            candidate_id=release_event_id,
            symbol=_normalize_symbol(next(iter(_safe_list(bundle.get("supporting_symbols"))), "")) or "SPY",
            company_name=_coerce_text(bundle.get("event_title") or bundle.get("release_type")),
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            serp_client=serp_client,
            tavily_client=tavily_client,
            llm_client=llm_client,
            budget=4,
        )
        request_rows.extend(query_requests)
        result_rows.extend(query_results)
        usable_results = [
            item
            for item in query_results
            if _coerce_text(item.get("result_kind")) == "result"
            and not _is_provider_error_text(item.get("title"))
            and not _is_provider_error_text(item.get("snippet"))
        ]
        verdict_by_result = _llm_macro_evidence_verdicts(
            llm_client=llm_client,
            hypothesis_text=_coerce_text(row.get("hypothesis_text")),
            result_rows=usable_results,
        )
        support_hits = 0
        contradict_hits = 0
        for result in usable_results:
            result_id = _coerce_text(result.get("result_id"))
            title = _coerce_text(result.get("title"))
            snippet = _coerce_text(result.get("snippet"))
            verdict = verdict_by_result.get(result_id) or "neutral"
            if verdict == "support":
                support_hits += 1
            elif verdict == "contradict":
                contradict_hits += 1
            if verdict in {"support", "contradict"}:
                claim_hash = hashlib.sha1(f"{hypothesis_id}|{result_id}".encode("utf-8")).hexdigest()[:16]
                macro_claim_rows.append(
                    {
                        "claim_id": f"claim::macro::{claim_hash}",
                        "run_id": run_id,
                        "bundle_subject": _coerce_text(bundle.get("release_type")) or "macro_release",
                        "claim_text": _trim(_evidence_text(snippet, title), 260),
                        "claim_type": "macro_support" if verdict == "support" else "macro_contradiction",
                        "claim_entities": [_coerce_text(bundle.get("release_type")) or "macro_release"],
                        "supports_hypothesis": hypothesis_id,
                        "freshness_class": "same_day",
                        "relevance_score": 0.7,
                        "causal_score": 0.6,
                        "confidence_score": 0.65,
                        "evidence_chunk_ids": [],
                        "is_same_day": True,
                        "source_authority_bucket": _coerce_text(result.get("authority_bucket")) or "web",
                        "source": _coerce_text(result.get("source") or result.get("provider")),
                    }
                )
        evidence_count = len(usable_results)
        web_support = support_hits / max(evidence_count, 1)
        web_contradiction = contradict_hits / max(evidence_count, 1)
        prior_support = _coerce_float(row.get("support_score"), 0.0)
        prior_contradiction = _coerce_float(row.get("contradiction_score"), 0.0)
        if evidence_count > 0:
            support_score = max(min(0.65 * prior_support + 0.35 * web_support, 1.0), 0.0)
            contradiction_score = max(min(0.65 * prior_contradiction + 0.35 * web_contradiction, 1.0), 0.0)
        else:
            support_score = prior_support
            contradiction_score = prior_contradiction
        support_status = _hypothesis_status_from_scores(
            support_score=support_score,
            contradiction_score=contradiction_score,
            supported_min=supported_min,
            supported_max_contradiction=supported_max_contradiction,
            conflicting_min=conflicting_min,
            continuation_min=continuation_min,
        )
        row_data["support_score"] = round(float(support_score), 4)
        row_data["contradiction_score"] = round(float(contradiction_score), 4)
        row_data["support_status"] = support_status
        row_data["evidence_count"] = int(evidence_count)
        verified_rows.append(row_data)
        summary_by_release[release_event_id] = {
            "support_status": support_status,
            "support_score": row_data["support_score"],
            "contradiction_score": row_data["contradiction_score"],
            "evidence_count": int(evidence_count),
        }
    if not verified_rows:
        return _empty_attention_hypotheses_frame(), {}, request_rows, result_rows, macro_claim_rows
    verified_frame = pd.DataFrame(verified_rows)
    for column in _MACRO_HYPOTHESES_COLUMNS:
        if column not in verified_frame.columns:
            verified_frame[column] = pd.Series(dtype="object")
    return verified_frame[_MACRO_HYPOTHESES_COLUMNS].reset_index(drop=True), summary_by_release, request_rows, result_rows, macro_claim_rows


def _apply_macro_diagnostics_to_release_bundles(
    *,
    macro_release_bundles: list[dict[str, Any]],
    hypothesis_summary_by_release: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not macro_release_bundles:
        return []
    out: list[dict[str, Any]] = []
    for item in macro_release_bundles:
        bundle = dict(item or {})
        release_event_id = _coerce_text(bundle.get("event_id"))
        diag = dict(hypothesis_summary_by_release.get(release_event_id) or {})
        if diag:
            support_status = _coerce_text(diag.get("support_status")) or "unresolved"
            bundle["hypothesis_status"] = support_status
            bundle["support_score"] = _coerce_float(diag.get("support_score"), 0.0)
            bundle["contradiction_score"] = _coerce_float(diag.get("contradiction_score"), 0.0)
            bundle["relationship_holding_count"] = int(diag.get("holding_count") or 0)
            bundle["relationship_mixed_count"] = int(diag.get("mixed_count") or 0)
            bundle["relationship_broken_count"] = int(diag.get("broken_count") or 0)
            bundle["relationship_unresolved_count"] = int(diag.get("unresolved_count") or 0)
            bundle["status"] = support_status
            if support_status in {"supported", "continuation", "conflicting", "unresolved"}:
                bundle["cause_status"] = support_status
                bundle["confidence_label"] = (
                    "High"
                    if support_status == "supported"
                    else ("Low" if support_status in {"conflicting", "unresolved"} else "Developing")
                )
        out.append(bundle)
    return out


def _build_attention_macro_context_frame(
    *,
    candidates: pd.DataFrame,
    attention_rows: pd.DataFrame | None,
    macro_release_bundles: list[dict[str, Any]],
    asof_time_utc: pd.Timestamp,
    run_id: str,
) -> pd.DataFrame:
    if not isinstance(candidates, pd.DataFrame) or candidates.empty:
        return _empty_attention_macro_context_frame()
    if not macro_release_bundles:
        return _empty_attention_macro_context_frame()

    horizons: list[str] = []
    if isinstance(attention_rows, pd.DataFrame) and not attention_rows.empty and "horizon" in attention_rows.columns:
        for value in attention_rows["horizon"].tolist():
            token = _coerce_text(value)
            if token:
                horizons.append(token)
    if not horizons:
        horizons = ["1d"]
    horizons = list(dict.fromkeys(horizons))

    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        symbol = _normalize_symbol(candidate.get("symbol"))
        if not symbol:
            continue
        observed_direction = 1 if _coerce_float(candidate.get("change_pct"), 0.0) >= 0 else -1
        tag_tokens = {
            _coerce_text(value).lower()
            for value in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags"))
            if _coerce_text(value)
        }
        tag_tokens.update(
            {
                _coerce_text(candidate.get("rates_role")).lower(),
                _coerce_text(candidate.get("commodity_role")).lower(),
            }
        )
        tag_tokens = {token for token in tag_tokens if token}

        alignment_raw = 0.0
        conflict_raw = 0.0
        signal_count = 0
        staleness_candidates: list[float] = []
        for release in macro_release_bundles:
            primary_nodes = {
                _coerce_text(value).lower()
                for value in _safe_list(release.get("primary_nodes"))
                if _coerce_text(value)
            }
            if primary_nodes and tag_tokens and not (primary_nodes & tag_tokens):
                continue
            surprise_score = _coerce_float(release.get("surprise_score"), 0.0)
            release_direction = int(_coerce_float(release.get("release_direction"), 0.0))
            if release_direction == 0:
                release_direction = 1
            if observed_direction == release_direction:
                alignment_raw += surprise_score
            else:
                conflict_raw += surprise_score
            signal_count += 1
            release_time = pd.to_datetime(release.get("release_time_utc"), utc=True, errors="coerce")
            if pd.notna(release_time):
                staleness_hours = float((asof_time_utc - release_time).total_seconds() / 3600.0)
                staleness_candidates.append(max(staleness_hours, 0.0))
        if signal_count <= 0:
            continue
        alignment_score = min(alignment_raw / signal_count, 100.0)
        conflict_score = min(conflict_raw / signal_count, 100.0)
        macro_staleness_hours = min(staleness_candidates) if staleness_candidates else math.nan
        for horizon in horizons:
            rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "symbol": symbol,
                    "horizon": _coerce_text(horizon) or "1d",
                    "macro_alignment_score": round(float(max(alignment_score, 0.0)), 2),
                    "macro_conflict_score": round(float(max(conflict_score, 0.0)), 2),
                    "macro_signal_count": int(signal_count),
                    "macro_staleness_hours": round(float(macro_staleness_hours), 2) if np.isfinite(_coerce_float(macro_staleness_hours)) else math.nan,
                    "schema_version": "attention_macro_context_1d.v1",
                }
            )
    if not rows:
        return _empty_attention_macro_context_frame()
    frame = pd.DataFrame(rows)
    for column in _ATTENTION_MACRO_CONTEXT_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    return frame[_ATTENTION_MACRO_CONTEXT_COLUMNS].reset_index(drop=True)
