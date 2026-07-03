"""
AQL config — env helpers, profile loading, search client loading.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    yaml = None

from ..web_research import (
    SerperSearchClient,
    load_serper_config,
)
from .constants import _ATTENTION_MACRO_PROFILE_DEFAULT_PATH


def _parse_scalar_yaml_value(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return text.strip("'\"")


def _fallback_parse_mapping_yaml(raw: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in str(raw or "").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip("'\"")
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if value:
            parent[key] = _parse_scalar_yaml_value(value)
        else:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def _safe_float_env(name: str, default: float, *, min_value: float | None = None) -> float:
    try:
        parsed = float(os.getenv(name, str(default)))
    except Exception:
        parsed = float(default)
    if min_value is not None:
        parsed = max(parsed, min_value)
    return parsed


def _safe_int_env(name: str, default: int, *, min_value: int | None = None) -> int:
    try:
        parsed = int(os.getenv(name, str(default)))
    except Exception:
        parsed = int(default)
    if min_value is not None:
        parsed = max(parsed, min_value)
    return parsed


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged.get(key) or {}, value)
        else:
            merged[key] = value
    return merged


def _default_attention_macro_signal_profile() -> dict[str, Any]:
    return {
        "schema_version": "attention_macro_signal_profile.v1",
        "release_type_priority": {"high": 3, "medium": 2, "low": 1},
        "release_type_base_score": {"high": 82.0, "medium": 62.0, "low": 42.0},
        "release_display_names": {
            "jobs_report": "BLS jobs report",
            "cpi": "CPI inflation release",
            "pce": "PCE inflation release",
            "jolts": "JOLTS labor release",
            "housing_starts": "Housing starts release",
            "building_permits": "Building permits release",
        },
        "release_components": {
            "PAYEMS": {
                "release_type": "jobs_report",
                "component_label": "Nonfarm Payrolls",
                "importance_tier": "high",
                "primary_nodes": ["labor", "policy", "rates", "equity_duration", "usd"],
            },
            "UNRATE": {
                "release_type": "jobs_report",
                "component_label": "Unemployment Rate",
                "importance_tier": "high",
                "primary_nodes": ["labor", "policy", "rates", "equity_duration", "usd"],
            },
            "CES0500000003": {
                "release_type": "jobs_report",
                "component_label": "Average Hourly Earnings",
                "importance_tier": "medium",
                "primary_nodes": ["labor", "inflation", "policy", "rates"],
            },
            "CPIAUCSL": {
                "release_type": "cpi",
                "component_label": "CPI Headline",
                "importance_tier": "high",
                "primary_nodes": ["inflation", "policy", "rates", "usd", "equity_duration"],
            },
            "CPILFESL": {
                "release_type": "cpi",
                "component_label": "CPI Core",
                "importance_tier": "high",
                "primary_nodes": ["inflation", "policy", "rates", "usd", "equity_duration"],
            },
            "PCEPI": {
                "release_type": "pce",
                "component_label": "PCE Headline",
                "importance_tier": "high",
                "primary_nodes": ["inflation", "policy", "rates", "usd", "equity_duration"],
            },
            "PCEPILFE": {
                "release_type": "pce",
                "component_label": "PCE Core",
                "importance_tier": "high",
                "primary_nodes": ["inflation", "policy", "rates", "usd", "equity_duration"],
            },
            "JTSJOL": {
                "release_type": "jolts",
                "component_label": "JOLTS Openings",
                "importance_tier": "medium",
                "primary_nodes": ["labor", "policy", "rates"],
            },
            "HOUST": {
                "release_type": "housing_starts",
                "component_label": "Housing Starts",
                "importance_tier": "low",
                "primary_nodes": ["growth", "rates", "credit"],
            },
            "PERMIT": {
                "release_type": "building_permits",
                "component_label": "Building Permits",
                "importance_tier": "low",
                "primary_nodes": ["growth", "rates", "credit"],
            },
        },
        "surprise_symbol_groups": {
            "rates": ["TLT", "IEF", "SHY", "LQD", "HYG", "ZB", "ZN"],
            "usd": ["UUP", "UDN", "USDU", "DXY"],
            "equity": ["SPY", "QQQ", "IWM", "DIA"],
        },
        "release_rules": {
            "freshness_hours": _safe_float_env("ATTENTION_MACRO_RELEASE_FRESHNESS_HOURS", 36.0, min_value=1.0),
            "surprise_threshold": _safe_float_env("ATTENTION_MACRO_RELEASE_SURPRISE_THRESHOLD", 60.0, min_value=0.0),
            "force_limit": _safe_int_env("ATTENTION_MACRO_RELEASE_FORCE_LIMIT", 2, min_value=0),
            "reaction_cap_move": _safe_float_env("ATTENTION_MACRO_RELEASE_REACTION_CAP_MOVE", 1.5, min_value=0.1),
            "release_time_lookahead_hours": 2.0,
            "supporting_symbol_limit": 6,
        },
        "relationship_checks": {
            "min_abs_change_pct_default": 0.25,
            "edges": [
                {
                    "edge_id": "labor_to_rates_duration",
                    "from_node": "labor",
                    "to_node": "rates",
                    "expected_sign": "positive",
                    "target_symbols": ["TLT", "IEF", "ZB", "ZN"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.25,
                    "strength_weight": 1.0,
                    "confidence_prior": 0.68,
                },
                {
                    "edge_id": "labor_to_usd",
                    "from_node": "labor",
                    "to_node": "usd",
                    "expected_sign": "negative",
                    "target_symbols": ["UUP", "DXY", "USDU"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.2,
                    "strength_weight": 0.9,
                    "confidence_prior": 0.6,
                },
                {
                    "edge_id": "labor_to_equity_duration",
                    "from_node": "labor",
                    "to_node": "equity_duration",
                    "expected_sign": "positive",
                    "target_symbols": ["QQQ", "ARKK", "SPY"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.35,
                    "strength_weight": 0.8,
                    "confidence_prior": 0.55,
                },
                {
                    "edge_id": "inflation_to_rates",
                    "from_node": "inflation",
                    "to_node": "rates",
                    "expected_sign": "negative",
                    "target_symbols": ["TLT", "IEF", "ZB", "ZN"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.25,
                    "strength_weight": 1.0,
                    "confidence_prior": 0.72,
                },
                {
                    "edge_id": "inflation_to_usd",
                    "from_node": "inflation",
                    "to_node": "usd",
                    "expected_sign": "positive",
                    "target_symbols": ["UUP", "DXY", "USDU"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.2,
                    "strength_weight": 0.8,
                    "confidence_prior": 0.58,
                },
                {
                    "edge_id": "inflation_to_equity_duration",
                    "from_node": "inflation",
                    "to_node": "equity_duration",
                    "expected_sign": "negative",
                    "target_symbols": ["QQQ", "SPY"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.35,
                    "strength_weight": 0.8,
                    "confidence_prior": 0.57,
                },
                {
                    "edge_id": "policy_to_rates",
                    "from_node": "policy",
                    "to_node": "rates",
                    "expected_sign": "negative",
                    "target_symbols": ["TLT", "IEF", "ZB", "ZN"],
                    "lag_window": "same_day",
                    "regime_filter": "baseline",
                    "min_abs_change_pct": 0.2,
                    "strength_weight": 1.0,
                    "confidence_prior": 0.7,
                },
            ],
        },
        "hypothesis_rules": {
            "supported_min_support_score": 0.7,
            "supported_max_contradiction_score": 0.25,
            "conflicting_min_contradiction_score": 0.45,
            "continuation_min_support_score": 0.35,
        },
    }


def _load_attention_macro_signal_profile() -> dict[str, Any]:
    # Import _coerce_text here to avoid circular import; it's a simple pure function
    from ._shared import _coerce_text

    defaults = _default_attention_macro_signal_profile()
    configured_path = _coerce_text(os.getenv("ATTENTION_MACRO_SIGNAL_PROFILE_PATH"))
    profile_path = Path(configured_path) if configured_path else _ATTENTION_MACRO_PROFILE_DEFAULT_PATH
    if not profile_path.exists():
        return defaults
    try:
        raw = profile_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[warn] macro profile read failed path={profile_path}: {type(exc).__name__}: {exc}")
        return defaults
    try:
        loaded = yaml.safe_load(raw) if yaml is not None else _fallback_parse_mapping_yaml(raw)
    except Exception as exc:
        print(f"[warn] macro profile parse failed path={profile_path}: {type(exc).__name__}: {exc}")
        return defaults
    if not isinstance(loaded, dict):
        print(f"[warn] macro profile parse failed path={profile_path}: top-level object must be mapping")
        return defaults
    merged = _deep_merge_dict(defaults, loaded)
    release_components = merged.get("release_components")
    if isinstance(release_components, dict):
        merged["release_components"] = {str(k).upper(): dict(v or {}) for k, v in release_components.items()}
    return merged


def _load_search_clients() -> tuple[SerperSearchClient | None, None]:
    serper_cfg = load_serper_config()
    serper_client = SerperSearchClient(serper_cfg) if serper_cfg is not None else None
    return serper_client, None
