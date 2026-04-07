from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
from typing import Any, Callable

import numpy as np
import pandas as pd
try:
    import yaml
except Exception:
    yaml = None

from .llm import (
    AzureOpenAIChatJSONClient,
    AzureOpenAIEmbeddingClient,
    OpenAIChatJSONClient,
    OpenAIEmbeddingClient,
)
from .web_research import (
    SerpAPISearchClient,
    TavilySearchClient,
    WebResearchError,
    WebSearchResult,
    load_serpapi_config,
    load_tavily_config,
)
from .runtime_policy import attention_graph_policy, source_authority_policy


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient
EmbeddingClient = OpenAIEmbeddingClient | AzureOpenAIEmbeddingClient

DEFAULT_PROMPT_VERSION = (os.getenv("ATTENTION_PROMPT_VERSION") or "attention-bottom-up-v1").strip() or "attention-bottom-up-v1"
DEFAULT_WRITER_MODEL = "planner"
LOW_SIGNAL_PHRASES = (
    "other big stocks moving",
    "stocks moving higher",
    "stocks moving lower",
    "market today",
    "stock market today",
)
YIELD_RELEVANT_TAGS = {
    "rates",
    "duration",
    "credit",
    "inflation_proxy",
    "real_rates",
    "treasury",
    "yield",
    "yields",
}
CAUSAL_LANGUAGE_PATTERNS = (
    r"\bbecause\b",
    r"\bdue to\b",
    r"\bafter\b",
    r"\bamid\b",
    r"\bdriven by\b",
    r"\bsuggest(?:s|ing)\b",
    r"\bimply(?:s|ing)\b",
    r"\btherefore\b",
    r"\bwhich (?:lifted|helped|pressured|hurt|weighed|boosted)\b",
    r"\bpressure on\b",
    r"\bmargins?\b",
    r"\bdemand\b",
    r"\binflation\b",
    r"\binput costs?\b",
)
RATE_TRANSMISSION_PATTERNS = (
    r"\bborrow(?:ing|ed)? costs?\b",
    r"\bfinanc(?:e|ing|ed)\b",
    r"\bdiscount rates?\b",
    r"\bvaluation(?:s)?\b",
    r"\brisk appetite\b",
    r"\bcredit conditions?\b",
    r"\bmargins?\b",
    r"\bdemand\b",
    r"\bfuel costs?\b",
    r"\binput costs?\b",
)
RESEARCH_PROVIDER_ERROR_MARKERS = (
    "request failed status=",
    "exceeds your plan's set usage limit",
    "please upgrade your plan",
    "contact support@",
    "invalid api key",
    "unauthorized",
    "forbidden",
    "rate limit",
    "quota exceeded",
)
LOW_SIGNAL_CLAIM_MARKERS = (
    "top wall street analysts changed outlook on top names",
    "for all changes, including upgrades/downgrades",
    "see analyst ratings page",
    "analyst ratings page",
    "other analysts' views on",
    "other analysts&#39; views on",
    "other analysts views on",
)
IRRELEVANT_NEWS_PATTERNS = (
    r"\bdividend[- ]equivalent\b",
    r"\brsu rights?\b",
    r"\bform\s*4\b",
    r"\bbeneficial ownership\b",
    r"\btrust holdings?\b",
    r"\bdirector\b.*\b(adds?|gets?|gains?|disclos(?:e|es|ed)|holds?)\b.*\bshares?\b",
    r"\binsider\b.*\b(holdings?|transactions?|buys?|sells?)\b",
    r"\bspousal share stakes?\b",
)
GENERIC_GRAPH_BUCKETS = {
    "unknown",
    "market",
    "all market",
    "broad commodity market",
    "cluster",
    "macro anchor",
    "equities",
    "commodities",
    "assets",
}
_MACRO_RELEASE_SCHEMA_VERSION = "macro_release_events_1d.v1"
_MACRO_CAUSAL_GRAPH_SCHEMA_VERSION = "macro_causal_graph_edges_v1"
_MACRO_RELATIONSHIP_SCHEMA_VERSION = "macro_relationship_checks_1d.v1"
_MACRO_HYPOTHESES_SCHEMA_VERSION = "attention_hypotheses_1d.v1"
_ATTENTION_MACRO_PROFILE_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "attention_macro_signal_profile.v1.yaml"
_MACRO_RELEASE_EVENT_COLUMNS = [
    "run_id",
    "release_event_id",
    "release_type",
    "release_time_utc",
    "surprise_score",
    "importance_tier",
    "primary_nodes",
    "initial_hypothesis",
    "status",
    "asof_time_utc",
    "schema_version",
    "is_forced_macro_release",
    "promotion_reason",
    "source_dataset",
    "component_series_ids",
    "component_labels",
    "supporting_symbols",
    "surprise_source",
    "surprise_z",
    "cross_asset_reaction_pct",
    "release_direction",
]
_MACRO_RELATIONSHIP_CHECK_COLUMNS = [
    "run_id",
    "release_event_id",
    "release_type",
    "edge_id",
    "from_node",
    "to_node",
    "expected_sign",
    "observed_sign",
    "observed_strength",
    "consistency_status",
    "regime_used",
    "lag_window",
    "strength_weight",
    "confidence_prior",
    "evidence_symbols",
    "asof_time_utc",
    "schema_version",
]
_MACRO_CAUSAL_GRAPH_EDGE_COLUMNS = [
    "run_id",
    "edge_id",
    "from_node",
    "to_node",
    "expected_sign",
    "lag_window",
    "regime_filter",
    "min_abs_change_pct",
    "strength_weight",
    "confidence_prior",
    "target_symbols",
    "source",
    "schema_version",
]
_MACRO_HYPOTHESES_COLUMNS = [
    "run_id",
    "hypothesis_id",
    "candidate_id",
    "release_event_id",
    "hypothesis_text",
    "support_status",
    "support_score",
    "contradiction_score",
    "evidence_count",
    "asof_time_utc",
    "schema_version",
]
_ATTENTION_MACRO_CONTEXT_COLUMNS = [
    "run_id",
    "asof_time_utc",
    "symbol",
    "horizon",
    "macro_alignment_score",
    "macro_conflict_score",
    "macro_signal_count",
    "macro_staleness_hours",
    "schema_version",
]


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


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged.get(key) or {}, value)
        else:
            merged[key] = value
    return merged


def _load_attention_macro_signal_profile() -> dict[str, Any]:
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
        loaded = yaml.safe_load(raw) if yaml is not None else json.loads(raw)
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

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "research_subjects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["subject", "role"],
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["kind", "text"],
            },
        },
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["query", "rationale"],
            },
        },
        "official_routes": {"type": "array", "items": {"type": "string"}},
        "priority_entities": {"type": "array", "items": {"type": "string"}},
        "evidence_budget": {"type": "integer"},
    },
    "required": [
        "research_subjects",
        "hypotheses",
        "queries",
        "official_routes",
        "priority_entities",
        "evidence_budget",
    ],
}

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_text": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "claim_entities": {"type": "array", "items": {"type": "string"}},
                    "supports_hypothesis": {"type": "string"},
                    "freshness_class": {"type": "string"},
                    "relevance_score": {"type": "number"},
                    "causal_score": {"type": "number"},
                    "confidence_score": {"type": "number"},
                    "is_same_day": {"type": "boolean"},
                },
                "required": [
                    "claim_text",
                    "claim_type",
                    "claim_entities",
                    "supports_hypothesis",
                    "freshness_class",
                    "relevance_score",
                    "causal_score",
                    "confidence_score",
                    "is_same_day",
                ],
            },
        }
    },
    "required": ["claims"],
}

SEARCH_ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "use_tavily": {"type": "boolean"},
        "tavily_topic": {"type": "string", "enum": ["news", "general"]},
        "tavily_query": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["use_tavily", "tavily_topic", "tavily_query", "reason"],
}

SEARCH_RELEVANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevant_indices": {"type": "array", "items": {"type": "integer"}},
        "reason": {"type": "string"},
    },
    "required": ["relevant_indices", "reason"],
}

MACRO_HYPOTHESIS_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "result_id": {"type": "string"},
                    "label": {"type": "string", "enum": ["support", "contradict", "neutral"]},
                    "confidence": {"type": "number"},
                },
                "required": ["result_id", "label", "confidence"],
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["verdicts", "reason"],
}

SYMBOL_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "surface_summary": {"type": "string"},
        "what_changed_text": {"type": "string"},
        "why_today_text": {"type": "string"},
        "what_else_moved_text": {"type": "string"},
        "background_context_text": {"type": "string"},
    },
    "required": [
        "title",
        "surface_summary",
        "what_changed_text",
        "why_today_text",
        "what_else_moved_text",
        "background_context_text",
    ],
}

EVENT_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "surface_summary": {"type": "string"},
        "what_happened_text": {"type": "string"},
        "why_happened_text": {"type": "string"},
        "affected_assets_summary_text": {"type": "string"},
        "background_context_text": {"type": "string"},
    },
    "required": [
        "title",
        "surface_summary",
        "what_happened_text",
        "why_happened_text",
        "affected_assets_summary_text",
        "background_context_text",
    ],
}

EVENT_WRITER_SYSTEM_PROMPT = (
    "You are a senior cross-asset strategist writing for PMs. "
    "Return concise JSON only. Use only supplied facts and claims; do not invent facts. "
    "Write institutional-quality event summaries that are specific and mechanism-first but NOT wordy."
    "Use clear writing style like The Economist. Avoid using complicated work "
    "Keep surface_summary to at most 4 sentences. "
    "Critical rule for why_happened_text: lead with a causal chain in plain English before any numbers. "
    "Use this structure when evidence supports it: catalyst -> transmission channel -> market pricing reaction. "
    "Transmission channels must be concrete, such as input costs, margins, volumes, funding costs, duration, policy, operations, demand, or risk appetite. "
    "Do not write generic tape titles or generic cluster copy. Keep the title anchored to the strongest supported event theme. "
    "Avoid ticker and percentage tape recaps across all text fields. "
    "Do not list more than two tickers in why_happened_text. "
    "Never open why_happened_text with Treasury, yield, ticker, or percentage statistics. "
    "What_happened_text should summarize the directional relationship across the cluster, not enumerate the tape. "
    "Affected_assets_summary_text should focus on second-order spillover and cross-asset breadth, not restate what_happened_text. "
    "If causality is mixed, say what is uncertain and why in plain language. "
    "When Treasury yield context is relevant, summarize direction and transmission in plain language without quoting bp numbers unless the rate move itself is the event."
)


@dataclass
class AgenticAttentionArtifacts:
    home_payload: dict[str, Any]
    bundle_map: dict[str, dict[str, Any]]
    frames: dict[str, pd.DataFrame]


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    return _coerce_text(value).upper()


def _safe_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (set, frozenset, pd.Series, pd.Index, np.ndarray)):
        return list(value)
    return [value]


def _trim(text: object, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _first_sentence(text: object) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return parts[0].strip() if parts else clean


def _normalized_text(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(text).lower()).strip()


def _token_set(text: object, *, min_len: int = 3) -> set[str]:
    tokens: set[str] = set()
    for token in re.split(r"[^a-z0-9]+", _coerce_text(text).lower()):
        if len(token) >= min_len:
            tokens.add(token)
    return tokens


def _text_overlap(left: object, right: object) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def _stat_marker_count(text: object) -> int:
    clean = _coerce_text(text)
    if not clean:
        return 0
    pct = len(re.findall(r"[+\-]?\d+(?:\.\d+)?%", clean))
    bps = len(re.findall(r"[+\-]?\d+(?:\.\d+)?\s*bps\b", clean.lower()))
    return pct + bps


def _ticker_like_count(text: object) -> int:
    clean = _coerce_text(text)
    if not clean:
        return 0
    tokens = re.findall(r"\b[A-Z]{2,5}\b", clean)
    return len(tokens)


def _has_causal_language(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    return any(re.search(pattern, clean) for pattern in CAUSAL_LANGUAGE_PATTERNS)


def _looks_like_stat_dump(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    lowered = clean.lower()
    stat_count = _stat_marker_count(clean)
    ticker_count = _ticker_like_count(clean)
    ticker_pct_pairs = len(re.findall(r"\b[A-Z]{2,5}\s*[+\-]\d+(?:\.\d+)?%", clean))
    if re.search(r"\bup:\b|\bdown:\b", lowered):
        return True
    if stat_count >= 6:
        return True
    if ticker_pct_pairs >= 3:
        return True
    if ticker_count >= 4 and stat_count >= 4:
        return True
    if ticker_count >= 6 and stat_count >= 3:
        return True
    if lowered.count(",") >= 6 and ticker_count >= 4:
        return True
    if "treasury yields:" in lowered and stat_count >= 4 and not _has_causal_language(clean):
        return True
    return False


def _is_yield_only_explanation(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    lowered = clean.lower()
    has_rates_markers = any(
        token in lowered
        for token in ("treasury", "yield", "2y", "10y", "30y", "2s10s", "curve", "bps")
    )
    if not has_rates_markers:
        return False
    return not any(re.search(pattern, lowered) for pattern in RATE_TRANSMISSION_PATTERNS)


def _best_why_claim_sentence(claims: list[dict[str, Any]]) -> str:
    if not claims:
        return ""
    ordered = sorted(
        claims,
        key=lambda item: (
            -int(bool(item.get("is_same_day"))),
            -_coerce_float(item.get("causal_score"), 0.0),
            -_coerce_float(item.get("confidence_score"), 0.0),
            -_coerce_float(item.get("relevance_score"), 0.0),
        ),
    )
    scored: list[tuple[float, str]] = []
    for item in ordered:
        sentence = _first_sentence(item.get("claim_text"))
        if not sentence:
            continue
        if _is_provider_error_text(sentence) or _is_low_signal_claim_text(sentence):
            continue
        score = 0.0
        if bool(item.get("is_same_day")):
            score += 2.0
        if _has_causal_language(sentence):
            score += 2.0
        if not _looks_like_stat_dump(sentence):
            score += 1.0
        if _is_yield_only_explanation(sentence):
            score -= 2.0
        score += _coerce_float(item.get("causal_score"), 0.0)
        score += 0.5 * _coerce_float(item.get("confidence_score"), 0.0)
        scored.append((score, sentence))
    if not scored:
        return ""
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _specific_why_fallback(
    retained_claims: list[dict[str, Any]],
    *,
    default_text: str = "",
) -> str:
    claim_seed = _best_why_claim_sentence(retained_claims)
    if not claim_seed:
        return _coerce_text(default_text)
    if _looks_like_stat_dump(claim_seed) and not _has_causal_language(claim_seed):
        return _coerce_text(default_text)
    return claim_seed


def _looks_like_generic_tape_title(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    patterns = (
        r"\bmoves? sharply today\b",
        r"\bmoved sharply today\b",
        r"\brises on today'?s tape\b",
        r"\bfalls on today'?s tape\b",
        r"\bon today'?s tape\b",
        r"\bmarket move today\b",
    )
    return any(re.search(pattern, clean) for pattern in patterns)


def _compose_surface_summary(what_changed: object, why_text: object, what_else: object = "", *, include_what_else: bool = False) -> str:
    first = _coerce_text(what_changed)
    second = _coerce_text(why_text)
    spillover = _coerce_text(what_else)
    parts = [part for part in [first, second] if part]
    if include_what_else and spillover and _text_overlap(" ".join(parts), spillover) < 0.62:
        parts.append(spillover)
    summary = " ".join(parts).strip()
    if not summary:
        return ""
    if _looks_like_stat_dump(summary) and _has_causal_language(second):
        compact = " ".join(part for part in [first, second] if part).strip()
        return compact or summary
    return summary


def _coerce_float(value: object, default: float = math.nan) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if np.isfinite(number) else default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _source_authority_bucket(source: object, url: object = "") -> tuple[str, int]:
    blob = f"{_coerce_text(source)} {_coerce_text(url)}".lower()
    policy = source_authority_policy()
    if any(token in blob for token in policy.official_tokens):
        return "official", 0
    if any(token in blob for token in policy.wire_tokens):
        return "wire", 1
    if any(token in blob for token in policy.press_tokens):
        return "press", 2
    return "web", 3


def _freshness_score(published_at: pd.Timestamp, asof_time_utc: pd.Timestamp) -> float:
    if pd.isna(published_at):
        return 0.1
    age_hours = max((asof_time_utc - published_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.75
    if age_hours <= 7 * 24:
        return 0.45
    return 0.15


def _is_low_signal(headline: object, snippet: object) -> bool:
    blob = f"{_coerce_text(headline)} {_coerce_text(snippet)}".lower()
    return any(token in blob for token in LOW_SIGNAL_PHRASES)


def _is_provider_error_text(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    if clean.startswith(("tavily request failed", "serpapi request failed", "web research request failed")):
        return True
    return any(marker in clean for marker in RESEARCH_PROVIDER_ERROR_MARKERS)


def _is_irrelevant_news_text(headline: object, snippet: object = "") -> bool:
    blob = f"{_coerce_text(headline)} {_coerce_text(snippet)}".lower()
    if not blob.strip():
        return False
    return any(re.search(pattern, blob) for pattern in IRRELEVANT_NEWS_PATTERNS)


def _is_low_signal_claim_text(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    if _is_provider_error_text(clean):
        return True
    if _is_irrelevant_news_text(clean):
        return True
    return any(marker in clean for marker in LOW_SIGNAL_CLAIM_MARKERS)


def _display_excerpt(text: object, headline: object = "", *, limit: int = 180) -> str:
    if (
        _is_provider_error_text(text)
        or _is_provider_error_text(headline)
        or _is_low_signal_claim_text(text)
        or _is_low_signal_claim_text(headline)
    ):
        return ""
    sentence = _trim(_first_sentence(text), limit=limit)
    if not sentence:
        return ""
    if _normalized_text(sentence) == _normalized_text(headline):
        return ""
    return sentence


def _tag_tokens(tags: list[str]) -> set[str]:
    tokens: set[str] = set()
    for tag in tags:
        for token in re.split(r"[^a-z0-9]+", _coerce_text(tag).lower()):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def _merge_text_values(*values: object) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _safe_list(value):
            text = _coerce_text(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(text)
    return out


def _candidate_text_value(candidate: dict[str, Any], *fields: str) -> str:
    for field in fields:
        text = _coerce_text(candidate.get(field)).strip()
        if text:
            return text
    return ""


def _is_generic_graph_bucket(value: object) -> bool:
    text = _coerce_text(value).strip().lower()
    if not text:
        return True
    candidates = {text}
    tail = re.split(r"[:/]", text)[-1].replace("_", " ").strip()
    if tail:
        candidates.add(tail)
    return any(candidate in GENERIC_GRAPH_BUCKETS for candidate in candidates)


def _informative_graph_label(value: object) -> str:
    text = _coerce_text(value).strip()
    return "" if _is_generic_graph_bucket(text) else text


def _candidate_taxonomy_context(candidate: dict[str, Any]) -> dict[str, str]:
    industry = _informative_graph_label(
        _candidate_text_value(candidate, "effective_industry", "taxonomy_industry", "industry")
    )
    sector = _informative_graph_label(
        _candidate_text_value(candidate, "effective_sector", "taxonomy_sector", "sector")
    )
    asset_class = _informative_graph_label(
        _candidate_text_value(candidate, "effective_asset_class", "taxonomy_asset_class", "asset_class")
    )
    peer_group_name = _informative_graph_label(
        _candidate_text_value(candidate, "effective_peer_group_name", "taxonomy_peer_group_name", "peer_group_name")
    )
    peer_group_id = _candidate_text_value(
        candidate,
        "effective_peer_group_id",
        "taxonomy_peer_group_id",
        "peer_group_id",
    ).strip()
    if _is_generic_graph_bucket(peer_group_id):
        peer_group_id = ""
    specific_peer_group = ""
    if peer_group_id and peer_group_id not in {industry, sector, asset_class}:
        specific_peer_group = peer_group_id
    elif peer_group_name and peer_group_name not in {industry, sector, asset_class}:
        specific_peer_group = peer_group_name
    return {
        "industry": industry,
        "sector": sector,
        "asset_class": asset_class,
        "peer_group": specific_peer_group,
    }


def _candidate_graph_tags(candidate: dict[str, Any]) -> set[str]:
    return _tag_tokens(
        _merge_text_values(
            candidate.get("macro_exposure_tags"),
            candidate.get("macro_role_tags"),
            candidate.get("taxonomy_macro_role_tags"),
            candidate.get("business_tags"),
            candidate.get("business_role_tags"),
            candidate.get("taxonomy_business_role_tags"),
            candidate.get("commodity_role"),
            candidate.get("rates_role"),
            candidate.get("defensive_role"),
        )
    )


def _candidate_claim_entities(candidate: dict[str, Any]) -> list[str]:
    taxonomy = _candidate_taxonomy_context(candidate)
    return _merge_text_values(
        _normalize_symbol(candidate.get("symbol")),
        candidate.get("company_name"),
        candidate.get("security_name"),
        taxonomy.get("industry"),
        taxonomy.get("peer_group"),
        taxonomy.get("sector"),
        candidate.get("macro_exposure_tags"),
        candidate.get("macro_role_tags"),
        candidate.get("taxonomy_macro_role_tags"),
        candidate.get("business_tags"),
        candidate.get("business_role_tags"),
        candidate.get("taxonomy_business_role_tags"),
        candidate.get("commodity_role"),
        candidate.get("rates_role"),
        candidate.get("defensive_role"),
    )


def _return_series_from_bars(frame: pd.DataFrame | None) -> pd.Series:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "timestamp" not in frame.columns or "close" not in frame.columns:
        return pd.Series(dtype=float)
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out[out["timestamp"].notna() & out["close"].notna()].sort_values("timestamp")
    if out.empty:
        return pd.Series(dtype=float)
    returns = out["close"].pct_change()
    series = pd.Series(returns.values, index=out["timestamp"])
    return series.dropna()


def _history_correlation_map(
    bars_by_symbol: dict[str, pd.DataFrame] | None,
    symbols: list[str],
    *,
    min_observations: int,
) -> dict[tuple[str, str], dict[str, float | int]]:
    if not isinstance(bars_by_symbol, dict) or not bars_by_symbol:
        return {}
    normalized_symbols = [
        _normalize_symbol(symbol)
        for symbol in list(symbols or [])
        if _normalize_symbol(symbol)
    ]
    if len(normalized_symbols) < 2:
        return {}
    series_by_symbol: dict[str, pd.Series] = {}
    min_obs = max(int(min_observations), 2)
    for symbol in normalized_symbols:
        series = _return_series_from_bars(bars_by_symbol.get(symbol))
        if len(series) >= min_obs:
            series_by_symbol[symbol] = series.rename(symbol)
    if len(series_by_symbol) < 2:
        return {}
    returns_wide = pd.concat(series_by_symbol.values(), axis=1, join="outer").sort_index()
    if returns_wide.empty:
        return {}
    observations = returns_wide.notna().astype(int).T.dot(returns_wide.notna().astype(int))
    correlations = returns_wide.corr(min_periods=min_obs)
    out: dict[tuple[str, str], dict[str, float | int]] = {}
    ordered = [column for column in correlations.columns if column in series_by_symbol]
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            corr_value = pd.to_numeric(correlations.at[left, right], errors="coerce")
            obs_value = int(pd.to_numeric(observations.at[left, right], errors="coerce") or 0)
            if pd.isna(corr_value) or obs_value < min_obs:
                continue
            out[tuple(sorted((left, right)))] = {
                "correlation": float(corr_value),
                "observations": obs_value,
            }
    return out


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = left & right
    if not overlap:
        return 0.0
    return len(overlap) / max(len(left | right), 1)


def _move_direction(value: object) -> str:
    move = _coerce_float(value, 0.0)
    if move > 0:
        return "up"
    if move < 0:
        return "down"
    return "flat"


def _move_label(value: object) -> str:
    move = _coerce_float(value, 0.0)
    return f"{move:+.1f}%"


def _what_changed_fallback(candidate: dict[str, Any]) -> str:
    symbol = _normalize_symbol(candidate.get("symbol"))
    move = _coerce_float(candidate.get("change_pct"), 0.0)
    direction = _move_direction(move)
    if direction == "up":
        return f"{symbol} moved higher today."
    if direction == "down":
        return f"{symbol} moved lower today."
    return f"{symbol} was little changed today."


def _quality_label(claims: list[dict[str, Any]], cause_status: str) -> tuple[str, str]:
    same_day = [item for item in claims if bool(item.get("is_same_day"))]
    strong = [
        item
        for item in same_day
        if float(item.get("causal_score") or 0.0) >= 0.62 and float(item.get("relevance_score") or 0.0) >= 0.6
    ]
    if cause_status == "supported" and len(strong) >= 2:
        evidence_quality = "High"
    elif cause_status == "supported" and strong:
        evidence_quality = "Medium"
    elif claims:
        evidence_quality = "Developing"
    else:
        evidence_quality = "Low"
    freshness_quality = "High" if same_day else ("Low" if claims else "Low")
    return evidence_quality, freshness_quality


def _judge_cause_status(claims: list[dict[str, Any]]) -> tuple[str, str]:
    if not claims:
        return "unresolved", "unresolved"
    same_day = [item for item in claims if bool(item.get("is_same_day"))]
    supported = [
        item
        for item in same_day
        if float(item.get("causal_score") or 0.0) >= 0.58 and float(item.get("relevance_score") or 0.0) >= 0.58
    ]
    if supported:
        themes = {_coerce_text(item.get("supports_hypothesis")) for item in supported if _coerce_text(item.get("supports_hypothesis"))}
        if len(themes) >= 2 and "unresolved" not in themes:
            return "conflicting", "conflicting"
        return "supported", "same_day_confirmation"
    background = [
        item
        for item in claims
        if not bool(item.get("is_same_day"))
        and float(item.get("causal_score") or 0.0) >= 0.55
        and float(item.get("relevance_score") or 0.0) >= 0.55
    ]
    if background:
        return "continuation", "continuation"
    return "unresolved", "unresolved"


def _top_sources(claims: list[dict[str, Any]], *, limit: int = 4) -> str:
    out: list[str] = []
    for item in claims:
        source = _coerce_text(item.get("source"))
        if source and source not in out:
            out.append(source)
        if len(out) >= limit:
            break
    return ", ".join(out)


def _document_importance_map(claims: list[dict[str, Any]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in claims:
        relevance = _coerce_float(item.get("relevance_score"), 0.0)
        causal = _coerce_float(item.get("causal_score"), 0.0)
        confidence = _coerce_float(item.get("confidence_score"), 0.0)
        same_day_bonus = 0.08 if bool(item.get("is_same_day")) else 0.0
        score = min(max((0.45 * confidence) + (0.3 * relevance) + (0.25 * causal) + same_day_bonus, 0.0), 1.0)
        for chunk_id in _safe_list(item.get("evidence_chunk_ids")):
            chunk_text = _coerce_text(chunk_id)
            if not chunk_text:
                continue
            document_id = chunk_text.split("::chunk::", 1)[0]
            if not document_id:
                continue
            scores[document_id] = max(scores.get(document_id, 0.0), score)
    return scores


def _importance_label(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.58:
        return "medium"
    return "low"


def _load_search_clients() -> tuple[SerpAPISearchClient | None, TavilySearchClient | None]:
    serp_cfg = load_serpapi_config()
    tavily_cfg = load_tavily_config()
    serp_client = SerpAPISearchClient(serp_cfg) if serp_cfg is not None else None
    tavily_client = TavilySearchClient(tavily_cfg) if tavily_cfg is not None else None
    return serp_client, tavily_client


def _candidate_company_name(candidate: dict[str, Any]) -> str:
    for field in ["company_name", "display_name", "name"]:
        text = _coerce_text(candidate.get(field))
        if text:
            return text
    return ""


def _candidate_subject(candidate: dict[str, Any]) -> str:
    company_name = _candidate_company_name(candidate)
    symbol = _normalize_symbol(candidate.get("symbol"))
    return f"{company_name} ({symbol})".strip() if company_name and symbol else company_name or symbol


def _evidence_text(snippet: object, headline: object) -> str:
    """Use snippet when present, otherwise fall back to headline so evidence survives chunking."""
    body = _coerce_text(snippet)
    if body:
        return body
    return _coerce_text(headline)


def _search_mention_score(text: str, symbol: str, company_name: str) -> float:
    lowered = f" {text.lower()} "
    score = 0.0
    if symbol and re.search(rf"(?<![A-Z0-9]){re.escape(symbol.lower())}(?![A-Z0-9])", lowered):
        score += 0.65
    company = company_name.lower().strip()
    if company and company in lowered:
        score += 0.45
    return min(score, 1.0)


def _passes_symbol_search_gate(headline: str, snippet: str, symbol: str, company_name: str) -> bool:
    title_score = _search_mention_score(headline, symbol, company_name)
    body_score = _search_mention_score(snippet, symbol, company_name)
    combined_score = _search_mention_score(f"{headline} {snippet}", symbol, company_name)
    if max(title_score, body_score, combined_score) < 0.45:
        return False
    if title_score < 0.45 and body_score < 0.75:
        return False
    return True


def _search_result_is_relevant(
    headline: str,
    snippet: str,
    *,
    symbol: str,
    company_name: str,
) -> bool:
    if not headline and not snippet:
        return False
    if _is_irrelevant_news_text(headline, snippet):
        return False
    if _is_provider_error_text(headline) or _is_provider_error_text(snippet):
        return False
    if symbol and not _passes_symbol_search_gate(headline, snippet, symbol, company_name):
        return False
    if _is_low_signal(headline, snippet):
        if not symbol:
            return False
        if _search_mention_score(headline, symbol, company_name) < 0.75:
            return False
    return True


def _default_tavily_general_query(query: str, symbol: str, company_name: str) -> str:
    company = _coerce_text(company_name)
    subject = f"{company} ({symbol})" if company and symbol else company or symbol or query
    return f"{subject} latest developments pipeline approvals clinical trial FDA partnership guidance"


def _llm_search_relevance_flags(
    *,
    query: str,
    symbol: str,
    company_name: str,
    items: list[dict[str, str]],
    llm_client: LLMClient | None,
) -> list[bool]:
    if not items:
        return []
    fallback = [
        _search_result_is_relevant(
            _coerce_text(item.get("title")),
            _coerce_text(item.get("snippet")),
            symbol=symbol,
            company_name=company_name,
        )
        for item in items
    ]
    if llm_client is None:
        return fallback
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are a relevance gate for company news research. "
                "Select only indices that are materially relevant to the target company and likely catalysts, "
                "including trials, approvals, guidance, partnerships, product/commercial updates, management actions, "
                "or major financial/company developments. Exclude noisy insider/form-4/dividend-equivalent chatter, "
                "routine ex-dividend notices, isolated analyst target tweaks, and generic stock-up/stock-down recaps "
                "that do not identify a concrete business catalyst."
            ),
            user_prompt=json.dumps(
                {
                    "query": query,
                    "symbol": symbol,
                    "company_name": company_name,
                    "results": [
                        {
                            "index": index,
                            "title": _coerce_text(item.get("title")),
                            "snippet": _coerce_text(item.get("snippet")),
                            "source": _coerce_text(item.get("source")),
                            "url": _coerce_text(item.get("url")),
                        }
                        for index, item in enumerate(items)
                    ],
                },
                ensure_ascii=False,
                default=str,
            ),
            schema_name="attention_search_relevance",
            schema=SEARCH_RELEVANCE_SCHEMA,
        )
        selected = {
            int(value)
            for value in list(data.get("relevant_indices") or [])
            if isinstance(value, (int, float)) and 0 <= int(value) < len(items)
        }
        return [index in selected for index in range(len(items))]
    except Exception:
        return fallback


def _llm_tavily_route_decision(
    *,
    query: str,
    symbol: str,
    company_name: str,
    serp_preview: list[dict[str, str]],
    heuristic_serp_relevant: bool,
    llm_client: LLMClient | None,
) -> tuple[bool, str, str, str]:
    default_tavily_query = _default_tavily_general_query(query, symbol, company_name)
    if llm_client is None:
        if heuristic_serp_relevant:
            return False, "news", query, "serp_results_relevant"
        return True, "general", default_tavily_query, "serp_results_not_relevant"
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You are a research-router for market analysis. "
                "Decide if SerpApi results are relevant enough to explain the move. "
                "If not relevant, call Tavily as fallback using topic='general' for broader RAG retrieval. "
                "Prefer Tavily when Serp results are sparse or mostly low-signal (insider/form-4, ex-dividend, "
                "analyst target-only notes, or generic price-action recaps without concrete company catalysts). "
                "When using Tavily, output a high-recall query that includes company identity and likely catalysts."
            ),
            user_prompt=json.dumps(
                {
                    "query": query,
                    "symbol": symbol,
                    "company_name": company_name,
                    "serp_preview": serp_preview[:4],
                    "heuristic_serp_relevant": bool(heuristic_serp_relevant),
                    "policy": "Use Tavily fallback when SerpApi evidence is sparse, generic, or off-topic.",
                    "default_tavily_query": default_tavily_query,
                },
                ensure_ascii=False,
                default=str,
            ),
            schema_name="attention_search_router",
            schema=SEARCH_ROUTER_SCHEMA,
        )
        use_tavily = bool(data.get("use_tavily"))
        topic = _coerce_text(data.get("tavily_topic")).lower() or "general"
        if topic not in {"news", "general"}:
            topic = "general"
        tavily_query = _coerce_text(data.get("tavily_query")) or (default_tavily_query if topic == "general" else query)
        reason = _trim(data.get("reason"), 140)
        if not reason:
            reason = "llm_router_decision"
        return use_tavily, topic, tavily_query, reason
    except Exception:
        if heuristic_serp_relevant:
            return False, "news", query, "serp_results_relevant_fallback"
        return True, "general", default_tavily_query, "serp_results_not_relevant_fallback"


def _to_article_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = pd.DataFrame(rows)
    for column in ["headline", "summary", "description", "source", "url"]:
        if column not in frame.columns:
            frame[column] = ""
    frame["published_at"] = pd.to_datetime(frame.get("published_at"), utc=True, errors="coerce")
    frame = frame.dropna(subset=["headline"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = frame.sort_values("published_at", ascending=False, na_position="last")
    return frame.drop_duplicates(subset=["headline", "url"], keep="first").reset_index(drop=True)


def search_symbol_news_payload(
    symbol: str,
    *,
    company_name: str = "",
    max_results: int = 8,
    serp_client: SerpAPISearchClient | None = None,
    tavily_client: TavilySearchClient | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    if not normalized_symbol:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

    if serp_client is None:
        cfg = load_serpapi_config()
        serp_client = SerpAPISearchClient(cfg) if cfg is not None else None
    if tavily_client is None:
        cfg = load_tavily_config()
        tavily_client = TavilySearchClient(cfg) if cfg is not None else None

    query_base = f"{normalized_symbol} stock today"
    if company_name:
        query_base = f"{company_name} {normalized_symbol} stock today"

    article_rows: list[dict[str, Any]] = []
    sources: list[str] = []
    errors: list[str] = []
    serp_preview: list[dict[str, str]] = []
    serp_relevant_count = 0
    serp_candidates: list[dict[str, str]] = []

    if serp_client is not None:
        try:
            for item in serp_client.search(query_base, news=True, num=max(max_results, 3)):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title and not snippet:
                    continue
                serp_candidates.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "source": _coerce_text(item.source) or "SerpApi",
                        "url": _coerce_text(item.url),
                        "published_at": _coerce_text(item.published_at),
                    }
                )
            if hasattr(serp_client, "search_ai_overview"):
                try:
                    ai_overview = serp_client.search_ai_overview(query_base)  # type: ignore[attr-defined]
                except Exception:
                    ai_overview = None
                if isinstance(ai_overview, WebSearchResult):
                    serp_candidates.append(
                        {
                            "title": _coerce_text(ai_overview.title) or f"{normalized_symbol} AI Overview",
                            "snippet": _coerce_text(ai_overview.snippet),
                            "source": _coerce_text(ai_overview.source) or "Google AI Overview",
                            "url": _coerce_text(ai_overview.url),
                            "published_at": _coerce_text(ai_overview.published_at),
                        }
                    )
            serp_flags = _llm_search_relevance_flags(
                query=query_base,
                symbol=normalized_symbol,
                company_name=company_name,
                items=serp_candidates,
                llm_client=llm_client,
            )
            for row, keep in zip(serp_candidates, serp_flags):
                serp_preview.append({"title": row.get("title", ""), "snippet": row.get("snippet", ""), "source": row.get("source", "")})
                if not bool(keep):
                    continue
                if _is_irrelevant_news_text(row.get("title"), row.get("snippet")):
                    continue
                serp_relevant_count += 1
                article_rows.append(
                    {
                        "headline": _coerce_text(row.get("title")),
                        "summary": _evidence_text(row.get("snippet"), row.get("title")),
                        "description": _evidence_text(row.get("snippet"), row.get("title")),
                        "source": _coerce_text(row.get("source")) or "SerpApi",
                        "provider": "serpapi",
                        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                        "url": _coerce_text(row.get("url")),
                    }
                )
            sources.append("serpapi")
        except WebResearchError as exc:
            errors.append(str(exc))

    use_tavily = bool(tavily_client is not None and serp_client is None)
    tavily_topic = "news"
    tavily_query = query_base
    if tavily_client is not None and serp_client is not None:
        use_tavily, tavily_topic, tavily_query, _ = _llm_tavily_route_decision(
            query=query_base,
            symbol=normalized_symbol,
            company_name=company_name,
            serp_preview=serp_preview,
            heuristic_serp_relevant=serp_relevant_count > 0,
            llm_client=llm_client,
        )
        if serp_relevant_count <= 0:
            use_tavily = True

    if tavily_client is not None and use_tavily:
        try:
            tavily_candidates: list[dict[str, str]] = []
            for item in tavily_client.search(tavily_query, max_results=max(max_results // 2, 3), topic=tavily_topic):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title and not snippet:
                    continue
                tavily_candidates.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "source": _coerce_text(item.source) or "Tavily",
                        "url": _coerce_text(item.url),
                        "published_at": _coerce_text(item.published_at),
                    }
                )
            tavily_flags = _llm_search_relevance_flags(
                query=tavily_query,
                symbol=normalized_symbol,
                company_name=company_name,
                items=tavily_candidates,
                llm_client=llm_client,
            )
            for row, keep in zip(tavily_candidates, tavily_flags):
                if not bool(keep):
                    continue
                if _is_irrelevant_news_text(row.get("title"), row.get("snippet")):
                    continue
                article_rows.append(
                    {
                        "headline": _coerce_text(row.get("title")) or f"{normalized_symbol} web result",
                        "summary": _evidence_text(row.get("snippet"), row.get("title")),
                        "description": _evidence_text(row.get("snippet"), row.get("title")),
                        "source": _coerce_text(row.get("source")) or "Tavily",
                        "provider": "tavily",
                        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                        "url": _coerce_text(row.get("url")),
                    }
                )
            sources.append("tavily")
        except WebResearchError as exc:
            errors.append(str(exc))

    frame = _to_article_frame(article_rows).head(max(int(max_results), 1))
    sanitized_errors = [error for error in errors if not _is_provider_error_text(error)]
    fallback_summary = sanitized_errors[0] if sanitized_errors and frame.empty else None
    return {"articles": frame, "fallback_summary": fallback_summary, "source": "+".join(sources) if sources else None}


def _clean_cluster_label(value: Any) -> str:
    text = _coerce_text(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"unknown", "market", "cluster", "macro anchor", "equities", "commodities", "assets"}:
        return ""
    return text


def _humanize_cluster_tag(value: Any) -> str:
    text = _coerce_text(value).strip().lower()
    if not text:
        return ""
    if text in {"equities", "consumer", "growth", "risk", "commodities"}:
        return ""
    return text.replace("_", " ").strip().title()


def _top_symbol_label(rows: pd.DataFrame, *, limit: int = 2) -> str:
    if rows.empty:
        return ""
    ranked = rows.copy()
    if "candidate_score" in ranked.columns:
        ranked["_candidate_score"] = pd.to_numeric(ranked.get("candidate_score"), errors="coerce").fillna(0.0)
    else:
        ranked["_candidate_score"] = 0.0
    if "abs_change_pct" in ranked.columns:
        ranked["_abs_change_pct"] = pd.to_numeric(ranked.get("abs_change_pct"), errors="coerce").fillna(0.0)
    else:
        ranked["_abs_change_pct"] = pd.to_numeric(ranked.get("change_pct"), errors="coerce").abs().fillna(0.0)
    ranked = ranked.sort_values(["_candidate_score", "_abs_change_pct"], ascending=[False, False])
    symbols = [
        _normalize_symbol(row.get("symbol"))
        for _, row in ranked.iterrows()
        if _normalize_symbol(row.get("symbol"))
    ]
    symbols = list(dict.fromkeys(symbols))[: max(int(limit), 1)]
    if not symbols:
        return ""
    if len(symbols) == 1:
        return symbols[0]
    if len(symbols) == 2:
        return f"{symbols[0]} and {symbols[1]}"
    return f"{', '.join(symbols[:-1])}, and {symbols[-1]}"


def _dominant_cluster_label(rows: pd.DataFrame) -> str:
    if rows.empty:
        return ""
    for column in ("peer_group_name", "industry", "sector", "source_label"):
        values = [_clean_cluster_label(value) for value in rows.get(column, pd.Series(dtype=object)).tolist()]
        values = [value for value in values if value]
        if not values:
            continue
        counts = pd.Series(values).value_counts()
        if counts.empty:
            continue
        if len(values) == 1 or len(counts) == 1 or int(counts.iloc[0]) >= 2:
            return _coerce_text(counts.index[0]).strip()
    tag_values: list[str] = []
    for tags in rows.get("macro_exposure_tags", pd.Series(dtype=object)).tolist():
        tag_values.extend([_humanize_cluster_tag(tag) for tag in _safe_list(tags)])
    tag_values = [value for value in tag_values if value]
    if tag_values:
        tag_counts = pd.Series(tag_values).value_counts()
        if not tag_counts.empty and (len(tag_values) == 1 or len(tag_counts) == 1 or int(tag_counts.iloc[0]) >= 2):
            return _coerce_text(tag_counts.index[0]).strip()
    return _top_symbol_label(rows, limit=2)


def _event_title_from_cluster(cluster_rows: pd.DataFrame) -> str:
    if cluster_rows.empty:
        return "Market move today"
    ranked = cluster_rows.copy()
    ranked["_change_pct"] = pd.to_numeric(ranked.get("change_pct"), errors="coerce").fillna(0.0)
    lower_rows = ranked[ranked["_change_pct"] < 0].copy()
    higher_rows = ranked[ranked["_change_pct"] >= 0].copy()
    lower_label = _dominant_cluster_label(lower_rows)
    higher_label = _dominant_cluster_label(higher_rows)
    cluster_label = _dominant_cluster_label(ranked)
    anchor_direction = _move_direction(
        ranked.sort_values(
            ["candidate_score", "abs_change_pct"],
            ascending=[False, False],
        ).iloc[0].get("change_pct")
    )
    if lower_label and higher_label and lower_label != higher_label:
        return f"{lower_label} lower, {higher_label} higher"
    if cluster_label:
        if anchor_direction == "down":
            return f"{cluster_label} lower today"
        if anchor_direction == "up":
            return f"{cluster_label} higher today"
        return f"{cluster_label} active today"
    return "Market move today"


def _generic_query_candidates(candidate: dict[str, Any], peer_symbols: list[str]) -> list[dict[str, str]]:
    subject = _candidate_subject(candidate)
    symbol = _normalize_symbol(candidate.get("symbol"))
    sector = _coerce_text(candidate.get("sector"))
    industry = _coerce_text(candidate.get("industry"))
    tags = [tag for tag in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags")) if _coerce_text(tag)]
    tag_blob = " ".join(dict.fromkeys(str(tag) for tag in tags[:3]))
    queries: list[dict[str, str]] = []
    base = subject or symbol
    if base:
        queries.append({"query": f"{base} move today", "rationale": "Look for same-day coverage tied to the observed move."})
        queries.append({"query": f"{base} news today", "rationale": "Capture straightforward same-day news about the subject."})
    if industry:
        queries.append({"query": f"{base} {industry} today", "rationale": "Check for industry or peer context linked to the move."})
    elif sector:
        queries.append({"query": f"{base} {sector} today", "rationale": "Check for sector context linked to the move."})
    if tag_blob:
        queries.append({"query": f"{base} {tag_blob} today", "rationale": "Check macro and business-exposure context derived from the subject metadata."})
    if peer_symbols:
        queries.append({"query": f"{base} {' '.join(peer_symbols[:3])} today", "rationale": "Check whether peers or spillover names are moving on the same narrative."})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in queries:
        query = _trim(item.get("query"), 160)
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        deduped.append({"query": query, "rationale": item.get("rationale", "")})
    return deduped[:4]


def _yield_context_relevant(candidate: dict[str, Any]) -> bool:
    if _coerce_text(candidate.get("rates_role")):
        return True
    tags = {
        _coerce_text(tag).lower()
        for tag in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags"))
        if _coerce_text(tag)
    }
    return bool(tags & YIELD_RELEVANT_TAGS)


def _latest_yield_facts(yield_curve_facts_frame: pd.DataFrame | None) -> dict[str, Any]:
    if not isinstance(yield_curve_facts_frame, pd.DataFrame) or yield_curve_facts_frame.empty:
        return {}
    row = yield_curve_facts_frame.copy().iloc[-1].to_dict()
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"source_dataset", "asof_time_utc", "latest_date", "updated_at_utc"}:
            out[key] = _coerce_text(pd.to_datetime(value, utc=True, errors="coerce").isoformat() if key.endswith("_utc") and pd.notna(pd.to_datetime(value, utc=True, errors="coerce")) else value)
            continue
        numeric = _coerce_float(value)
        out[key] = None if math.isnan(numeric) else round(float(numeric), 2)
    return out


def _yield_fact_summary_text(yield_facts: dict[str, Any]) -> str:
    if not yield_facts:
        return ""

    two_delta = _coerce_float(yield_facts.get("ust_2y_1d_bps"))
    ten_delta = _coerce_float(yield_facts.get("ust_10y_1d_bps"))
    curve_2s10s_delta = _coerce_float(yield_facts.get("curve_2s10s_1d_bps"))

    parts: list[str] = []
    if np.isfinite(two_delta) and np.isfinite(ten_delta):
        if two_delta < 0 and ten_delta > 0:
            parts.append("Rates context: front-end yields fell while long-end yields rose.")
        elif two_delta > 0 and ten_delta < 0:
            parts.append("Rates context: front-end yields rose while long-end yields fell.")
        else:
            direction = "higher" if (two_delta + ten_delta) / 2.0 > 0 else "lower"
            parts.append(f"Rates context: yields moved {direction}.")

    if np.isfinite(curve_2s10s_delta):
        slope_word = "steepened" if curve_2s10s_delta > 0 else ("flattened" if curve_2s10s_delta < 0 else "was little changed")
        parts.append(f"The curve {slope_word}.")
    return " ".join(parts).strip()


def _fallback_research_plan(candidate: dict[str, Any], peer_symbols: list[str]) -> dict[str, Any]:
    routes = ["sec"] if _coerce_text(candidate.get("security_type")).lower() == "common_stock" else []
    if _safe_list(candidate.get("macro_exposure_tags")) or _coerce_text(candidate.get("rates_role")) or _coerce_text(candidate.get("commodity_role")):
        routes.append("fred")
    if _yield_context_relevant(candidate):
        routes.append("treasury")
    routes.append("news")
    routes = list(dict.fromkeys(route for route in routes if route))
    subject = _candidate_subject(candidate)
    tags = [str(tag) for tag in _safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags")) if _coerce_text(tag)]
    hypotheses = []
    if _coerce_text(candidate.get("security_type")).lower() == "common_stock":
        hypotheses.append({"kind": "company_specific", "text": f"Company-specific news may explain why {subject} moved today."})
    if tags:
        hypotheses.append({"kind": "macro", "text": f"Macro or cross-asset context linked to {'/'.join(tags[:2])} may explain the move."})
    hypotheses.append({"kind": "unresolved", "text": f"There may be no clear same-day catalyst for {subject}."})
    priority_entities = [subject, _normalize_symbol(candidate.get("symbol")), _coerce_text(candidate.get("sector")), _coerce_text(candidate.get("industry"))]
    priority_entities.extend(tags[:4])
    priority_entities.extend(peer_symbols[:3])
    return {
        "research_subjects": [{"subject": subject or _normalize_symbol(candidate.get("symbol")), "role": "primary"}]
        + [{"subject": symbol, "role": "peer"} for symbol in peer_symbols[:3]],
        "hypotheses": hypotheses[:3],
        "queries": _generic_query_candidates(candidate, peer_symbols),
        "official_routes": routes,
        "priority_entities": [entity for entity in dict.fromkeys(entity for entity in priority_entities if _coerce_text(entity))],
        "evidence_budget": 8,
    }


def _plan_candidate_research(candidate: dict[str, Any], peer_symbols: list[str], llm_client: LLMClient | None) -> dict[str, Any]:
    if llm_client is None:
        return _fallback_research_plan(candidate, peer_symbols)
    fallback = _fallback_research_plan(candidate, peer_symbols)
    system_prompt = (
        "You are planning bottom-up market-move research. "
        "Use only the supplied facts. Do not use canned oil/rates/risk templates. "
        "Return compact JSON with queries and official routes."
    )
    user_prompt = json.dumps(
        {
            "candidate": {
                "symbol": _normalize_symbol(candidate.get("symbol")),
                "subject": _candidate_subject(candidate),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
                "change_pct": _coerce_float(candidate.get("change_pct")),
                "expected_move_pct": _coerce_float(candidate.get("expected_move_pct")),
                "surprise_z": _coerce_float(candidate.get("surprise_z")),
                "macro_exposure_tags": _safe_list(candidate.get("macro_exposure_tags")),
                "business_tags": _safe_list(candidate.get("business_tags")),
                "peer_symbols": peer_symbols[:5],
            },
            "fallback": fallback,
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_research_plan",
            schema=PLANNER_SCHEMA,
        )
        queries = [item for item in data.get("queries", []) if _coerce_text((item or {}).get("query"))]
        if not queries:
            return fallback
        return {
            "research_subjects": data.get("research_subjects") or fallback["research_subjects"],
            "hypotheses": data.get("hypotheses") or fallback["hypotheses"],
            "queries": queries[:4],
            "official_routes": data.get("official_routes") or fallback["official_routes"],
            "priority_entities": data.get("priority_entities") or fallback["priority_entities"],
            "evidence_budget": int(data.get("evidence_budget") or fallback["evidence_budget"] or 8),
        }
    except Exception:
        return fallback


def _peer_candidates(candidate: dict[str, Any], candidates: pd.DataFrame, *, limit: int = 5) -> list[str]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    industry = _coerce_text(candidate.get("industry"))
    sector = _coerce_text(candidate.get("sector"))
    peers: list[tuple[str, float]] = []
    for _, row in candidates.iterrows():
        peer_symbol = _normalize_symbol(row.get("symbol"))
        if not peer_symbol or peer_symbol == symbol:
            continue
        match = 0.0
        if industry and industry != "Unknown" and _coerce_text(row.get("industry")) == industry:
            match += 1.0
        elif sector and sector != "Unknown" and _coerce_text(row.get("sector")) == sector:
            match += 0.6
        else:
            shared_tags = _jaccard(
                _tag_tokens(_safe_list(candidate.get("macro_exposure_tags")) + _safe_list(candidate.get("business_tags"))),
                _tag_tokens(_safe_list(row.get("macro_exposure_tags")) + _safe_list(row.get("business_tags"))),
            )
            match += shared_tags
        if match <= 0:
            continue
        peers.append((peer_symbol, match + abs(_coerce_float(row.get("change_pct"), 0.0)) / 20.0))
    peers.sort(key=lambda item: (-item[1], item[0]))
    return [symbol for symbol, _ in peers[:limit]]


def _search_query_results(
    query: str,
    *,
    candidate_id: str,
    symbol: str,
    company_name: str,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    serp_client: SerpAPISearchClient | None,
    tavily_client: TavilySearchClient | None,
    llm_client: LLMClient | None,
    budget: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    query_id = f"query::{hashlib.sha1(f'{candidate_id}|{query}'.encode('utf-8')).hexdigest()[:16]}"
    normalized_symbol = _normalize_symbol(symbol)
    normalized_company = _coerce_text(company_name)
    serp_preview: list[dict[str, str]] = []
    serp_relevant_count = 0

    if serp_client is not None:
        request_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": candidate_id,
                "query_id": query_id,
                "provider": "serpapi",
                "query": query,
                "route_mode": "primary",
            }
        )
        try:
            results = serp_client.search(query, news=True, num=max(min(int(budget), 6), 1))
        except Exception as exc:
            error_text = _trim(str(exc), 180)
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "serpapi",
                    "result_id": f"{query_id}::serpapi::error",
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "error_text": error_text,
                    "result_kind": "error",
                    "source": "serpapi",
                    "published_at": "",
                    "authority_bucket": "web",
                    "authority_rank": 3,
                }
            )
            results = []
        serp_candidates: list[dict[str, str]] = []
        for item in list(results or [])[: max(min(int(budget), 6), 1)]:
            if not isinstance(item, WebSearchResult):
                continue
            serp_candidates.append(
                {
                    "title": _coerce_text(item.title),
                    "snippet": _coerce_text(item.snippet),
                    "source": _coerce_text(item.source),
                    "url": _coerce_text(item.url),
                    "published_at": _coerce_text(item.published_at),
                }
            )
        serp_flags = _llm_search_relevance_flags(
            query=query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            items=serp_candidates,
            llm_client=llm_client,
        )
        for row, keep in zip(serp_candidates, serp_flags):
            title = _coerce_text(row.get("title"))
            snippet = _coerce_text(row.get("snippet"))
            serp_preview.append(
                {
                    "title": title,
                    "snippet": snippet,
                    "source": _coerce_text(row.get("source")),
                }
            )
            if bool(keep) and not _is_irrelevant_news_text(title, snippet):
                serp_relevant_count += 1
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "serpapi",
                    "result_id": f"{query_id}::serpapi::{hashlib.sha1((_coerce_text(row.get('url')) or title).encode('utf-8')).hexdigest()[:12]}",
                    "title": title,
                    "url": _coerce_text(row.get("url")),
                    "snippet": snippet,
                    "error_text": "",
                    "result_kind": "result",
                    "source": _coerce_text(row.get("source")) or "serpapi",
                    "published_at": _coerce_text(row.get("published_at")),
                    "authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                }
            )

    use_tavily = bool(tavily_client is not None and serp_client is None)
    tavily_topic = "news"
    tavily_query = query
    route_reason = "serp_unavailable" if use_tavily else "serp_results_relevant"
    if tavily_client is not None and serp_client is not None:
        use_tavily, tavily_topic, tavily_query, route_reason = _llm_tavily_route_decision(
            query=query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            serp_preview=serp_preview,
            heuristic_serp_relevant=serp_relevant_count > 0,
            llm_client=llm_client,
        )
        if serp_relevant_count <= 0:
            use_tavily = True

    if tavily_client is not None and use_tavily:
        request_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": candidate_id,
                "query_id": query_id,
                "provider": "tavily",
                "query": tavily_query,
                "route_mode": "rag_fallback" if serp_client is not None else "primary",
                "route_reason": route_reason,
                "topic": tavily_topic,
            }
        )
        try:
            results = tavily_client.search(tavily_query, max_results=max(min(int(budget), 4), 1), topic=tavily_topic)
        except Exception as exc:
            error_text = _trim(str(exc), 180)
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "tavily",
                    "result_id": f"{query_id}::tavily::error",
                    "title": "",
                    "url": "",
                    "snippet": "",
                    "error_text": error_text,
                    "result_kind": "error",
                    "source": "tavily",
                    "published_at": "",
                    "authority_bucket": "web",
                    "authority_rank": 3,
                }
            )
            results = []
        tavily_candidates: list[dict[str, str]] = []
        for item in list(results or [])[: max(min(int(budget), 6), 1)]:
            if not isinstance(item, WebSearchResult):
                continue
            tavily_candidates.append(
                {
                    "title": _coerce_text(item.title),
                    "snippet": _coerce_text(item.snippet),
                    "source": _coerce_text(item.source),
                    "url": _coerce_text(item.url),
                    "published_at": _coerce_text(item.published_at),
                }
            )
        tavily_flags = _llm_search_relevance_flags(
            query=tavily_query,
            symbol=normalized_symbol,
            company_name=normalized_company,
            items=tavily_candidates,
            llm_client=llm_client,
        )
        for row, keep in zip(tavily_candidates, tavily_flags):
            if not bool(keep):
                continue
            title = _coerce_text(row.get("title"))
            snippet = _coerce_text(row.get("snippet"))
            if _is_irrelevant_news_text(title, snippet):
                continue
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            result_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": candidate_id,
                    "query_id": query_id,
                    "provider": "tavily",
                    "result_id": f"{query_id}::tavily::{hashlib.sha1((_coerce_text(row.get('url')) or title).encode('utf-8')).hexdigest()[:12]}",
                    "title": title,
                    "url": _coerce_text(row.get("url")),
                    "snippet": snippet,
                    "error_text": "",
                    "result_kind": "result",
                    "source": _coerce_text(row.get("source")) or "tavily",
                    "published_at": _coerce_text(row.get("published_at")),
                    "authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                }
            )
    return request_rows, result_rows


def _candidate_context_documents(
    candidate: dict[str, Any],
    *,
    news_payloads: dict[str, dict[str, Any]] | None,
    context_payloads: dict[str, dict[str, Any]] | None,
    filings_frame: pd.DataFrame | None,
    fred_summary_frame: pd.DataFrame | None,
    yield_curve_facts_frame: pd.DataFrame | None,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    official_routes: list[str],
    priority_entities: list[str],
) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    documents: list[dict[str, Any]] = []
    payload = dict((news_payloads or {}).get(symbol) or {})
    articles = payload.get("articles")
    if isinstance(articles, pd.DataFrame) and not articles.empty:
        for _, row in articles.iterrows():
            news_headline = _coerce_text(row.get("headline"))
            news_summary = _coerce_text(row.get("summary") or row.get("description"))
            news_text = _evidence_text(news_summary, news_headline)
            if _is_irrelevant_news_text(news_headline, news_text):
                continue
            search_provider = _coerce_text(row.get("provider") or payload.get("source"))
            authority_bucket, authority_rank = _source_authority_bucket(row.get("source"), row.get("url"))
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::news::{hashlib.sha1((_coerce_text(row.get('url')) or _coerce_text(row.get('headline'))).encode('utf-8')).hexdigest()[:12]}",
                    "source_kind": "news",
                    "source_provider": _coerce_text(row.get("source")),
                    "source_authority_bucket": authority_bucket,
                    "authority_rank": authority_rank,
                    "title": _coerce_text(row.get("headline")),
                    "url": _coerce_text(row.get("url")),
                    "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                    "raw_text": news_text,
                    "display_excerpt": _display_excerpt(news_text, row.get("headline")),
                    "search_provider": search_provider,
                    "source_trace": _json_dumps({"source": "news_payloads", "provider": search_provider}),
                }
            )
    context = dict((context_payloads or {}).get(symbol) or {})
    context_snippets = [
        ("context_summary", context.get("llm_summary_text")),
        ("context_why_now", context.get("llm_why_now")),
        ("context_headline", context.get("llm_headline")),
    ]
    for label, text in context_snippets:
        clean = _trim(text, 400)
        if not clean:
            continue
        authority_bucket, authority_rank = _source_authority_bucket(context.get("llm_source_line") or "attention_context")
        documents.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "bundle_subject": symbol,
                "document_id": f"doc::{symbol}::{label}",
                "source_kind": "context",
                "source_provider": _coerce_text(context.get("llm_source_line") or "Attention Context"),
                "source_authority_bucket": authority_bucket,
                "authority_rank": authority_rank,
                "title": f"{symbol} context",
                "url": "",
                "published_at": pd.NaT,
                "raw_text": clean,
                "display_excerpt": _display_excerpt(clean),
                "source_trace": _json_dumps({"source": "attention_context_bundle"}),
            }
        )
    if "sec" in {route.lower() for route in official_routes} and isinstance(filings_frame, pd.DataFrame) and not filings_frame.empty:
        filings = filings_frame.copy()
        if "symbol" in filings.columns:
            filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
            filings = filings[filings["symbol"] == symbol].copy()
        filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
        filings = filings.sort_values("filing_date", ascending=False, na_position="last").head(4)
        for _, row in filings.iterrows():
            text = _coerce_text(row.get("filing_excerpt") or row.get("document_text") or row.get("primary_doc_description"))
            if not text:
                continue
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::sec::{hashlib.sha1((_coerce_text(row.get('filing_url')) or text).encode('utf-8')).hexdigest()[:12]}",
                    "source_kind": "sec",
                    "source_provider": "SEC EDGAR",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": f"{_coerce_text(row.get('form'))} • {_coerce_text(row.get('primary_doc_description') or row.get('items'))}",
                    "url": _coerce_text(row.get("filing_url")),
                    "published_at": pd.to_datetime(row.get("filing_date"), utc=True, errors="coerce"),
                    "raw_text": text,
                    "display_excerpt": _display_excerpt(text, row.get("primary_doc_description")),
                    "source_trace": _json_dumps({"source": "edgar_filings"}),
                }
            )
    if "fred" in {route.lower() for route in official_routes} and isinstance(fred_summary_frame, pd.DataFrame) and not fred_summary_frame.empty:
        fred = fred_summary_frame.copy()
        fred["label"] = fred.get("label", pd.Series(dtype=str)).astype(str)
        tokens = _tag_tokens(priority_entities)
        if tokens:
            fred["_match"] = fred["label"].map(lambda value: len(tokens & _tag_tokens([value])))
            fred = fred[fred["_match"] > 0].sort_values(["_match"], ascending=False).head(3)
        else:
            fred = fred.head(2)
        for _, row in fred.iterrows():
            summary = (
                f"{_coerce_text(row.get('label') or row.get('series_id'))}: latest {_coerce_text(row.get('latest_value'))}"
                f", delta {_coerce_text(row.get('prev_delta'))}, yoy {_coerce_text(row.get('yoy_pct'))}."
            )
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::fred::{_coerce_text(row.get('series_id'))}",
                    "source_kind": "fred",
                    "source_provider": "FRED",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": _coerce_text(row.get("label") or row.get("series_id")),
                    "url": "",
                    "published_at": pd.NaT,
                    "raw_text": summary,
                    "display_excerpt": _display_excerpt(summary),
                    "source_trace": _json_dumps({"source": "fred_summary"}),
                }
            )
    if "treasury" in {route.lower() for route in official_routes} and _yield_context_relevant(candidate):
        yield_facts = _latest_yield_facts(yield_curve_facts_frame)
        summary = _yield_fact_summary_text(yield_facts)
        if summary:
            documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(candidate.get("candidate_id")),
                    "bundle_subject": symbol,
                    "document_id": f"doc::{symbol}::treasury::yield_curve",
                    "source_kind": "treasury",
                    "source_provider": "U.S. Treasury",
                    "source_authority_bucket": "official",
                    "authority_rank": 0,
                    "title": "Treasury Yield Curve",
                    "url": "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed",
                    "published_at": pd.to_datetime(yield_facts.get("latest_date"), errors="coerce"),
                    "raw_text": summary,
                    "display_excerpt": _display_excerpt(summary),
                    "source_trace": _json_dumps({"source": "yield_curve_facts_1d"}),
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in documents:
        key = (_coerce_text(item.get("url")).lower(), _coerce_text(item.get("title")).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _documents_from_search_results(
    candidate: dict[str, Any],
    result_rows: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in result_rows:
        title = _coerce_text(row.get("title"))
        snippet = _coerce_text(row.get("snippet"))
        snippet_or_title = _evidence_text(snippet, title)
        if not title and not snippet:
            continue
        if _is_irrelevant_news_text(title, snippet):
            continue
        if _is_provider_error_text(title) or _is_provider_error_text(snippet):
            continue
        if _is_low_signal(title, snippet) and not _normalize_symbol(candidate.get("symbol")) in f"{title} {snippet}".upper():
            continue
        doc_id = f"doc::{_coerce_text(row.get('result_id'))}"
        out.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "bundle_subject": _normalize_symbol(candidate.get("symbol")),
                "document_id": doc_id,
                "source_kind": "search",
                "source_provider": _coerce_text(row.get("source") or row.get("provider")),
                "source_authority_bucket": _coerce_text(row.get("authority_bucket")) or "web",
                "authority_rank": int(row.get("authority_rank") or 3),
                "title": title,
                "url": _coerce_text(row.get("url")),
                "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                "raw_text": snippet_or_title,
                "display_excerpt": _display_excerpt(snippet_or_title, title),
                "search_provider": _coerce_text(row.get("provider")),
                "source_trace": _json_dumps({"source": "search", "query_id": _coerce_text(row.get("query_id"))}),
            }
        )
    return out


def _chunk_source_documents(
    documents: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    embedding_client: EmbeddingClient | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for doc in documents:
        raw_text = _coerce_text(doc.get("raw_text"))
        if not raw_text:
            continue
        pieces = [piece.strip() for piece in re.split(r"\n\s*\n+|(?<=[.!?])\s+", raw_text) if piece.strip()]
        if not pieces:
            pieces = [raw_text]
        chunk_texts = []
        chunk_rows = []
        for idx, piece in enumerate(pieces[:3]):
            chunk_id = f"{_coerce_text(doc.get('document_id'))}::chunk::{idx + 1}"
            display_excerpt = _display_excerpt(piece, doc.get("title"))
            chunk_text = _trim(piece, 700)
            if not chunk_text or _is_low_signal_claim_text(chunk_text):
                continue
            chunk_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(doc.get("candidate_id")),
                    "bundle_subject": _coerce_text(doc.get("bundle_subject")),
                    "document_id": _coerce_text(doc.get("document_id")),
                    "chunk_id": chunk_id,
                    "chunk_text": chunk_text,
                    "display_excerpt": display_excerpt or _trim(chunk_text, 180),
                    "source_provider": _coerce_text(doc.get("source_provider")),
                    "source_authority_bucket": _coerce_text(doc.get("source_authority_bucket")),
                    "authority_rank": int(doc.get("authority_rank") or 3),
                    "title": _coerce_text(doc.get("title")),
                    "url": _coerce_text(doc.get("url")),
                    "published_at": pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce"),
                    "embedding_model": "",
                    "embedding_vector_json": "",
                }
            )
            chunk_texts.append(chunk_text)
        if embedding_client is not None and chunk_rows:
            try:
                vectors = embedding_client.generate_embeddings(chunk_texts)
            except Exception:
                vectors = []
            for row, vector in zip(chunk_rows, vectors):
                row["embedding_model"] = getattr(getattr(embedding_client, "config", object()), "embedding_model", "") or ""
                row["embedding_vector_json"] = _json_dumps(vector)
        rows.extend(chunk_rows)
    return pd.DataFrame(rows)


def _fallback_claims_from_chunks(
    candidate: dict[str, Any],
    chunks: pd.DataFrame,
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    hypotheses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    company_name = _candidate_company_name(candidate).upper()
    out: list[dict[str, Any]] = []
    hypothesis_names = [_coerce_text(item.get("kind")) for item in hypotheses if _coerce_text(item.get("kind"))]
    for _, row in chunks.head(6).iterrows():
        text = _coerce_text(row.get("display_excerpt") or row.get("chunk_text"))
        if not text:
            continue
        title = _coerce_text(row.get("title"))
        if (
            _is_provider_error_text(text)
            or _is_provider_error_text(title)
            or _is_low_signal_claim_text(text)
            or _is_low_signal_claim_text(title)
        ):
            continue
        title_blob = f"{title} {text}".upper()
        published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
        freshness = _freshness_score(published_at, asof_time_utc)
        authority_rank = int(row.get("authority_rank") or 3)
        relevance = 0.4
        if symbol and symbol in title_blob:
            relevance += 0.2
        if company_name and company_name in title_blob:
            relevance += 0.2
        if authority_rank <= 1:
            relevance += 0.12
        elif authority_rank == 2:
            relevance += 0.08
        if any(token in _normalized_text(title_blob) for token in ("earnings", "guidance", "deal", "approval", "trial", "margin", "checkout", "commentary", "de escalation", "de-escalation", "supply", "yield", "treasury")):
            relevance += 0.1
        if _is_low_signal(row.get("title"), text):
            relevance -= 0.15
        causal = min(0.35 + freshness * 0.35 + max(0.0, 0.2 - authority_rank * 0.05), 0.92)
        claim_type = "cause" if freshness >= 0.75 else "background"
        chunk_id = _coerce_text(row.get("chunk_id"))
        claim_hash = hashlib.sha1(f"{chunk_id}|{text}".encode("utf-8")).hexdigest()[:16]
        out.append(
            {
                "claim_id": f"claim::{claim_hash}",
                "run_id": run_id,
                "bundle_subject": symbol,
                "claim_text": text,
                "claim_type": claim_type,
                "claim_entities": _candidate_claim_entities(candidate),
                "supports_hypothesis": hypothesis_names[0] if hypothesis_names else "unresolved",
                "freshness_class": "same_day" if freshness >= 0.95 else "background",
                "relevance_score": round(min(max(relevance, 0.0), 1.0), 3),
                "causal_score": round(min(max(causal, 0.0), 1.0), 3),
                "confidence_score": round(min(max((relevance + causal) / 2.0, 0.0), 1.0), 3),
                "evidence_chunk_ids": [_coerce_text(row.get("chunk_id"))],
                "is_same_day": bool(freshness >= 0.95),
                "source_authority_bucket": _coerce_text(row.get("source_authority_bucket")) or "web",
                "source": _coerce_text(row.get("source_provider")),
            }
        )
    return out


def _extract_claims(
    candidate: dict[str, Any],
    chunks: pd.DataFrame,
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    hypotheses: list[dict[str, Any]],
    llm_client: LLMClient | None,
) -> list[dict[str, Any]]:
    if chunks.empty:
        return []
    fallback = _fallback_claims_from_chunks(
        candidate,
        chunks,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        hypotheses=hypotheses,
    )
    if llm_client is None:
        return fallback
    system_prompt = (
        "You extract structured market claims from evidence chunks. "
        "Only retain high-signal claims. Prefer same-day explanations over stale context. "
        "Do not emit generic filing labels as claims."
    )
    user_prompt = json.dumps(
        {
            "subject": _candidate_subject(candidate),
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "hypotheses": hypotheses[:4],
            "chunks": [
                {
                    "chunk_id": _coerce_text(row.get("chunk_id")),
                    "title": _coerce_text(row.get("title")),
                    "text": _coerce_text(row.get("chunk_text")),
                    "published_at": _coerce_text(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")) else ""),
                    "authority_bucket": _coerce_text(row.get("source_authority_bucket")),
                }
                for _, row in chunks.head(6).iterrows()
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_claims",
            schema=CLAIM_SCHEMA,
        )
    except Exception:
        return fallback
    claims: list[dict[str, Any]] = []
    chunk_lookup = {
        _coerce_text(row.get("chunk_id")): row
        for _, row in chunks.iterrows()
        if _coerce_text(row.get("chunk_id"))
    }
    for item in list(data.get("claims") or [])[:8]:
        if not isinstance(item, dict):
            continue
        claim_text = _trim(item.get("claim_text"), 260)
        if (
            not claim_text
            or _is_provider_error_text(claim_text)
            or _is_low_signal_claim_text(claim_text)
            or re.match(r"^(?:form\s+)?(?:8-k|10-k|10-q|20-f|6-k)\b", claim_text, flags=re.IGNORECASE)
        ):
            continue
        linked_chunk_id = next(iter(chunk_lookup.keys()), "")
        linked_chunk = chunk_lookup.get(linked_chunk_id, {})
        claims.append(
            {
                "claim_id": f"claim::{hashlib.sha1(f'{linked_chunk_id}|{claim_text}'.encode('utf-8')).hexdigest()[:16]}",
                "run_id": run_id,
                "bundle_subject": _normalize_symbol(candidate.get("symbol")),
                "claim_text": claim_text,
                "claim_type": _coerce_text(item.get("claim_type")) or "cause",
                "claim_entities": _merge_text_values(item.get("claim_entities"), _candidate_claim_entities(candidate)),
                "supports_hypothesis": _coerce_text(item.get("supports_hypothesis")) or "unresolved",
                "freshness_class": _coerce_text(item.get("freshness_class")) or ("same_day" if item.get("is_same_day") else "background"),
                "relevance_score": round(min(max(float(item.get("relevance_score") or 0.0), 0.0), 1.0), 3),
                "causal_score": round(min(max(float(item.get("causal_score") or 0.0), 0.0), 1.0), 3),
                "confidence_score": round(min(max(float(item.get("confidence_score") or 0.0), 0.0), 1.0), 3),
                "evidence_chunk_ids": [linked_chunk_id] if linked_chunk_id else [],
                "is_same_day": bool(item.get("is_same_day")),
                "source_authority_bucket": _coerce_text(linked_chunk.get("source_authority_bucket")) or "web",
                "source": _coerce_text(linked_chunk.get("source_provider")),
            }
        )
    return claims or fallback


def _serialize_claims_frame(claims: list[dict[str, Any]], *, asof_time_utc: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for item in claims:
        row = dict(item)
        row["asof_time_utc"] = asof_time_utc
        row["claim_entities_json"] = _json_dumps(item.get("claim_entities") or [])
        row["evidence_chunk_ids_json"] = _json_dumps(item.get("evidence_chunk_ids") or [])
        rows.append(row)
    return pd.DataFrame(rows)


def _claim_entities(claims: list[dict[str, Any]]) -> set[str]:
    entities: set[str] = set()
    for item in claims:
        for entity in _safe_list(item.get("claim_entities")):
            clean = _coerce_text(entity).lower()
            if clean:
                entities.add(clean)
    return entities


def _claim_entities_from_value(value: object) -> list[str]:
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            maybe = json.loads(text)
        except Exception:
            maybe = text
        parsed = maybe
    return [entity for entity in _merge_text_values(parsed) if _coerce_text(entity)]


def _claim_map_from_frame(claims_frame: pd.DataFrame | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(claims_frame, pd.DataFrame) or claims_frame.empty:
        return {}
    claim_map: dict[str, list[dict[str, Any]]] = {}
    for _, row in claims_frame.iterrows():
        symbol = _normalize_symbol(row.get("bundle_subject") or row.get("symbol"))
        if not symbol:
            continue
        entities = _claim_entities_from_value(row.get("claim_entities"))
        if not entities:
            entities = _claim_entities_from_value(row.get("claim_entities_json"))
        claim_map.setdefault(symbol, []).append({"claim_entities": entities})
    return claim_map


def _fallback_symbol_writer(
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    cause_status: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, str]:
    subject = _candidate_subject(candidate) or _normalize_symbol(candidate.get("symbol"))
    title = _coerce_text(candidate.get("headline")) or subject or _normalize_symbol(candidate.get("symbol"))
    what_changed = _coerce_text(candidate.get("what_changed_text")) or _what_changed_fallback(candidate)
    retained = [item for item in claims if bool(item.get("is_same_day"))]
    background = [item for item in claims if not bool(item.get("is_same_day"))]
    retained_why = _specific_why_fallback(retained, default_text="")
    background_why = _specific_why_fallback(background, default_text="")
    if cause_status == "supported":
        why_today = retained_why or background_why
    elif cause_status == "continuation":
        why_today = background_why or retained_why
    else:
        why_today = retained_why or background_why
    if _yield_context_relevant(candidate):
        yield_summary = _yield_fact_summary_text(yield_facts or {})
        if yield_summary:
            why_today = f"{why_today.rstrip('.')} {yield_summary}".strip() if why_today else yield_summary
    if peer_moves:
        preview = ", ".join(_normalize_symbol(item.get("symbol")) for item in peer_moves[:3] if _normalize_symbol(item.get("symbol")))
        same_direction = sum(
            1
            for item in peer_moves[:3]
            if _move_direction(item.get("change_pct")) == _move_direction(candidate.get("change_pct"))
        )
        if same_direction >= 2:
            what_else = f"Peers moved in the same direction, suggesting spillover across related names ({preview})." if preview else "Peers moved in the same direction, suggesting spillover across related names."
        else:
            what_else = f"Peer reactions were mixed across related names ({preview})." if preview else "Peer reactions were mixed across related names."
    else:
        what_else = ""
    background_text = _first_sentence(background[0].get("claim_text")) if background else ""
    surface_summary = _compose_surface_summary(what_changed, why_today)
    return {
        "title": title,
        "surface_summary": surface_summary,
        "what_changed_text": what_changed,
        "why_today_text": why_today,
        "what_else_moved_text": what_else,
        "background_context_text": background_text,
    }


def _write_symbol_bundle(
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    cause_status: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, str]:
    fallback = _fallback_symbol_writer(candidate, claims, peer_moves, cause_status, yield_facts=yield_facts)
    if llm_client is None:
        return fallback
    system_prompt = (
        "You write plain-language market research summaries. "
        "Use only the supplied claims and facts. Keep the surface summary to at most two sentences. "
        "Do not invent causes. Avoid jargon. "
        "Do not write generic tape titles like 'moves sharply today' or 'rises on today's tape'. "
        "Use the most specific company or catalyst title supported by the supplied evidence. "
        "Avoid ticker/percent tape recaps across all text fields. "
        "Explain mechanism, not tape recap: state why the move happened and how that transmits to prices, margins, demand, or risk appetite. "
        "Do not use ticker/percent lists as the main why-today explanation. "
        "What-else-moved text should describe spillover and avoid repeating what-changed text verbatim. "
        "When Treasury yield context is relevant, summarize direction and transmission in plain language without quoting bp numbers."
    )
    user_prompt = json.dumps(
        {
            "subject": _candidate_subject(candidate),
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "cause_status": cause_status,
            "what_changed_text": fallback["what_changed_text"],
            "claims": [
                {
                    "claim_text": item.get("claim_text"),
                    "claim_type": item.get("claim_type"),
                    "is_same_day": item.get("is_same_day"),
                    "source": item.get("source"),
                }
                for item in claims[:6]
            ],
            "yield_facts": yield_facts or {},
            "peer_moves": peer_moves[:4],
            "fallback": fallback,
            "narrative_requirements": {
                "why_today_text": "causal chain first, numbers second; avoid pure move recaps",
                "what_else_moved_text": "spillover pattern, not a duplicated ticker tape",
            },
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_symbol_writer",
            schema=SYMBOL_WRITER_SCHEMA,
        )
        title = _coerce_text(data.get("title")) or fallback["title"]
        if _looks_like_generic_tape_title(title):
            title = fallback["title"]
        what_changed = _coerce_text(data.get("what_changed_text")) or fallback["what_changed_text"]
        why_today = _coerce_text(data.get("why_today_text")) or fallback["why_today_text"]
        what_else = _coerce_text(data.get("what_else_moved_text")) or fallback["what_else_moved_text"]
        background_text = _coerce_text(data.get("background_context_text")) or fallback["background_context_text"]
        if _looks_like_stat_dump(what_changed):
            what_changed = fallback["what_changed_text"]
        if _looks_like_stat_dump(why_today) and not _has_causal_language(why_today):
            why_today = fallback["why_today_text"]
        if _looks_like_stat_dump(what_else):
            what_else = fallback["what_else_moved_text"]
        if _text_overlap(what_changed, what_else) >= 0.62:
            fallback_what_else = _coerce_text(fallback.get("what_else_moved_text"))
            what_else = fallback_what_else if _text_overlap(what_changed, fallback_what_else) < 0.62 else ""
        surface_summary = _coerce_text(data.get("surface_summary"))
        if not surface_summary or _looks_like_stat_dump(surface_summary):
            surface_summary = _compose_surface_summary(what_changed, why_today)
        elif _text_overlap(surface_summary, what_changed) >= 0.9 and _text_overlap(surface_summary, why_today) < 0.35:
            surface_summary = _compose_surface_summary(what_changed, why_today)
        return {
            "title": title,
            "surface_summary": surface_summary,
            "what_changed_text": what_changed,
            "why_today_text": why_today,
            "what_else_moved_text": what_else,
            "background_context_text": background_text,
        }
    except Exception:
        return fallback


def _infer_event_type(cluster_tokens: set[str], cluster_rows: pd.DataFrame) -> str:
    commodity_roles = [
        _coerce_text(value).lower()
        for value in cluster_rows.get("commodity_role", pd.Series(dtype=str)).tolist()
        if _coerce_text(value)
    ]
    if commodity_roles:
        preferred = pd.Series(commodity_roles).value_counts()
        if not preferred.empty:
            top = _coerce_text(preferred.index[0]).lower()
            if top.startswith("oil"):
                return "oil"
            if top in {"gold", "silver", "platinum", "palladium"}:
                return "defensives"
            return top
    rates_roles = [
        _coerce_text(value).lower()
        for value in cluster_rows.get("rates_role", pd.Series(dtype=str)).tolist()
        if _coerce_text(value)
    ]
    if rates_roles:
        return "rates"
    defensive_roles = [
        _coerce_text(value).lower()
        for value in cluster_rows.get("defensive_role", pd.Series(dtype=str)).tolist()
        if _coerce_text(value)
    ]
    if defensive_roles:
        return "defensives"
    macro_tag_counts: dict[str, int] = {}
    for tags in cluster_rows.get("macro_exposure_tags", pd.Series(dtype=object)).tolist():
        for tag in _safe_list(tags):
            normalized = _coerce_text(tag).lower()
            if not normalized:
                continue
            macro_tag_counts[normalized] = macro_tag_counts.get(normalized, 0) + 1
    for preferred_tag in ("oil", "rates", "defensive", "broad_risk", "energy", "travel", "credit", "duration"):
        if macro_tag_counts.get(preferred_tag):
            if preferred_tag in {"energy"} and macro_tag_counts.get("oil"):
                continue
            if preferred_tag in {"duration", "credit"}:
                return "rates"
            if preferred_tag == "broad_risk":
                return "risk"
            if preferred_tag == "defensive":
                return "defensives"
            return preferred_tag
    filtered_tokens = {
        token
        for token in cluster_tokens
        if token
        and len(token) > 3
        and not re.fullmatch(r"[a-z]{1,4}", token)
        and token not in {"equities", "stocks", "shares", "today", "move", "moves", "higher", "lower", "market"}
    }
    if filtered_tokens:
        preferred = sorted(filtered_tokens)
        if preferred:
            return preferred[0]
    if cluster_tokens:
        preferred = sorted(cluster_tokens)
        if preferred:
            return preferred[0]
    sectors = [sector for sector in cluster_rows.get("sector", pd.Series(dtype=str)).astype(str).tolist() if sector and sector != "Unknown"]
    return sectors[0].lower().replace(" ", "_") if sectors else "cluster"


def _cluster_uses_yield_context(cluster_rows: pd.DataFrame) -> bool:
    if "rates_role" in cluster_rows.columns and not cluster_rows["rates_role"].dropna().empty:
        return True
    raw_tags: list[str] = []
    for value in cluster_rows.get("macro_exposure_tags", pd.Series(dtype=object)).tolist():
        raw_tags.extend([_coerce_text(item).lower() for item in _safe_list(value) if _coerce_text(item)])
    for value in cluster_rows.get("business_tags", pd.Series(dtype=object)).tolist():
        raw_tags.extend([_coerce_text(item).lower() for item in _safe_list(value) if _coerce_text(item)])
    return bool(set(raw_tags) & YIELD_RELEVANT_TAGS)


def _top_ranked_values(series: pd.Series, *, limit: int = 4) -> list[str]:
    if not isinstance(series, pd.Series):
        return []
    cleaned = [_coerce_text(value) for value in series.tolist() if _coerce_text(value)]
    if not cleaned:
        return []
    counts = pd.Series(cleaned).value_counts()
    return [_coerce_text(index) for index in counts.index[: max(int(limit), 1)] if _coerce_text(index)]


def _cluster_tag_summary(cluster_rows: pd.DataFrame, *, limit: int = 6) -> list[str]:
    counts: dict[str, int] = {}
    for column in ("macro_exposure_tags", "business_tags"):
        for value in cluster_rows.get(column, pd.Series(dtype=object)).tolist():
            for item in _safe_list(value):
                tag = _coerce_text(item).lower()
                if not tag:
                    continue
                counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ordered[: max(int(limit), 1)]]


def _ordered_event_claims(claims: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    prioritized = sorted(
        claims or [],
        key=lambda item: (
            0 if bool(item.get("is_same_day")) else 1,
            -_coerce_float(item.get("causal_score"), 0.0),
            -_coerce_float(item.get("confidence_score"), 0.0),
            -_coerce_float(item.get("relevance_score"), 0.0),
            _coerce_text(item.get("claim_text")),
        ),
    )
    return [dict(item) for item in prioritized[: max(int(limit), 1)]]


def _build_event_writer_payload(
    event_title_seed: str,
    cluster_rows: pd.DataFrame,
    retained_claims: list[dict[str, Any]],
    *,
    fallback: dict[str, str],
    yield_facts: dict[str, Any] | None = None,
    event_type: str = "",
    cause_status: str = "",
    evidence_quality: str = "",
    freshness_quality: str = "",
) -> dict[str, Any]:
    ranked = cluster_rows.copy()
    ranked["change_pct"] = pd.to_numeric(ranked.get("change_pct"), errors="coerce").fillna(0.0)
    ranked["candidate_score"] = pd.to_numeric(ranked.get("candidate_score"), errors="coerce").fillna(0.0)
    ranked["abs_change_pct"] = pd.to_numeric(ranked.get("abs_change_pct"), errors="coerce")
    ranked["abs_change_pct"] = ranked["abs_change_pct"].fillna(ranked["change_pct"].abs())
    ranked = ranked.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).reset_index(drop=True)

    anchor_row = ranked.iloc[0] if not ranked.empty else pd.Series(dtype=object)
    up_rows = ranked[ranked["change_pct"] >= 0].copy()
    down_rows = ranked[ranked["change_pct"] < 0].copy()
    ordered_claims = _ordered_event_claims(retained_claims, limit=8)
    event_label = _dominant_cluster_label(ranked)

    return {
        "event_title_seed": event_title_seed,
        "event_type": event_type,
        "cause_status": cause_status,
        "evidence_quality": evidence_quality,
        "freshness_quality": freshness_quality,
        "cluster_context": {
            "event_label": event_label,
            "anchor_symbol": _normalize_symbol(anchor_row.get("symbol")),
            "anchor_direction": _move_direction(anchor_row.get("change_pct")),
            "supporting_symbols": [
                _normalize_symbol(row.get("symbol"))
                for _, row in ranked.head(8).iterrows()
                if _normalize_symbol(row.get("symbol"))
            ],
            "driver_symbols": [
                _normalize_symbol(row.get("symbol"))
                for _, row in down_rows.head(4).iterrows()
                if _normalize_symbol(row.get("symbol"))
            ],
            "beneficiary_symbols": [
                _normalize_symbol(row.get("symbol"))
                for _, row in up_rows.head(4).iterrows()
                if _normalize_symbol(row.get("symbol"))
            ],
            "dominant_sectors": _top_ranked_values(ranked.get("sector", pd.Series(dtype=str))),
            "dominant_industries": _top_ranked_values(ranked.get("industry", pd.Series(dtype=str))),
            "dominant_tags": _cluster_tag_summary(ranked),
            "asset_classes": _top_ranked_values(ranked.get("asset_class", pd.Series(dtype=str))),
            "source_summary": _top_sources(ordered_claims),
            "same_day_claim_count": sum(1 for item in ordered_claims if bool(item.get("is_same_day"))),
            "claim_count": len(ordered_claims),
        },
        "members": [
            {
                "symbol": _normalize_symbol(row.get("symbol")),
                "sector": _coerce_text(row.get("sector")),
                "industry": _coerce_text(row.get("industry")),
                "asset_class": _coerce_text(row.get("asset_class")),
                "change_pct": _coerce_float(row.get("change_pct"), 0.0),
                "candidate_score": _coerce_float(row.get("candidate_score"), 0.0),
                "macro_exposure_tags": [_coerce_text(item) for item in _safe_list(row.get("macro_exposure_tags")) if _coerce_text(item)],
                "business_tags": [_coerce_text(item) for item in _safe_list(row.get("business_tags")) if _coerce_text(item)],
                "rates_role": _coerce_text(row.get("rates_role")),
                "commodity_role": _coerce_text(row.get("commodity_role")),
            }
            for _, row in ranked.head(8).iterrows()
        ],
        "claims": [
            {
                "claim_text": _coerce_text(item.get("claim_text")),
                "claim_type": _coerce_text(item.get("claim_type")),
                "source": _coerce_text(item.get("source")),
                "source_authority_bucket": _coerce_text(item.get("source_authority_bucket")),
                "is_same_day": bool(item.get("is_same_day")),
                "freshness_class": _coerce_text(item.get("freshness_class")),
                "supports_hypothesis": _coerce_text(item.get("supports_hypothesis")),
                "claim_entities": [_coerce_text(entity) for entity in _safe_list(item.get("claim_entities")) if _coerce_text(entity)],
                "relevance_score": round(_coerce_float(item.get("relevance_score"), 0.0), 3),
                "causal_score": round(_coerce_float(item.get("causal_score"), 0.0), 3),
                "confidence_score": round(_coerce_float(item.get("confidence_score"), 0.0), 3),
            }
            for item in ordered_claims
        ],
        "yield_facts": yield_facts or {},
        "fallback": fallback,
        "narrative_requirements": {
            "title": "specific supported event theme, not generic tape or cluster copy",
            "what_happened_text": "one or two sentences on the directional relationship across the cluster; summarize the split, do not enumerate the tape",
            "why_happened_text": "lead with catalyst -> transmission channel -> market pricing reaction; if evidence is mixed, state the uncertainty explicitly",
            "affected_assets_summary_text": "one sentence on second-order spillover and breadth without repeating what_happened_text",
        },
    }


def _fallback_event_writer(
    event_title_seed: str,
    cluster_rows: pd.DataFrame,
    retained_claims: list[dict[str, Any]],
    yield_facts: dict[str, Any] | None = None,
    event_type: str = "",
) -> dict[str, str]:
    ranked = cluster_rows.copy()
    ranked["_change_pct"] = pd.to_numeric(ranked.get("change_pct"), errors="coerce").fillna(0.0)
    lower_rows = ranked[ranked["_change_pct"] < 0].copy()
    higher_rows = ranked[ranked["_change_pct"] >= 0].copy()
    lower_label = _dominant_cluster_label(lower_rows)
    higher_label = _dominant_cluster_label(higher_rows)
    cluster_label = _dominant_cluster_label(ranked)
    if lower_label and higher_label and lower_label != higher_label:
        what_happened = f"{lower_label} moved lower while {higher_label} moved higher today."
    elif cluster_label:
        anchor_direction = _move_direction(
            ranked.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).iloc[0].get("change_pct")
        )
        if anchor_direction == "down":
            what_happened = f"{cluster_label} moved lower today."
        elif anchor_direction == "up":
            what_happened = f"{cluster_label} moved higher today."
        else:
            what_happened = f"{cluster_label} were active today."
    else:
        what_happened = "A linked group of assets moved sharply today."
    ranked["_abs_change"] = ranked["_change_pct"].abs()
    ranked_ordered = ranked.sort_values(["_abs_change", "candidate_score"], ascending=[False, False])
    leading_down = [
        _normalize_symbol(row.get("symbol"))
        for _, row in ranked_ordered.iterrows()
        if _coerce_float(row.get("_change_pct"), 0.0) < 0 and _normalize_symbol(row.get("symbol"))
    ][:2]
    leading_up = [
        _normalize_symbol(row.get("symbol"))
        for _, row in ranked_ordered.iterrows()
        if _coerce_float(row.get("_change_pct"), 0.0) >= 0 and _normalize_symbol(row.get("symbol"))
    ][:2]
    if leading_down and leading_up:
        what_happened = (
            f"{what_happened.rstrip('.')} "
            f"Leaders included declines in {', '.join(leading_down)} and gains in {', '.join(leading_up)}."
        )
    elif leading_down:
        what_happened = f"{what_happened.rstrip('.')} Largest declines included {', '.join(leading_down)}."
    elif leading_up:
        what_happened = f"{what_happened.rstrip('.')} Largest gains included {', '.join(leading_up)}."
    claim_seed = _best_why_claim_sentence(retained_claims)
    if claim_seed and (_has_causal_language(claim_seed) or event_type == "rates"):
        why_happened = claim_seed
        if _looks_like_stat_dump(why_happened) and not _has_causal_language(why_happened):
            why_happened = _specific_why_fallback(
                retained_claims,
                default_text="",
            )
    elif claim_seed:
        why_happened = _specific_why_fallback(retained_claims)
    else:
        why_happened = ""
    yield_summary = _yield_fact_summary_text(yield_facts or {})
    if yield_summary and event_type == "rates" and _cluster_uses_yield_context(cluster_rows):
        why_happened = f"{why_happened.rstrip('.')} {yield_summary}".strip()
    up = []
    down = []
    for _, row in ranked.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        move = _coerce_float(row.get("change_pct"), 0.0)
        if not symbol:
            continue
        if move >= 0:
            up.append(symbol)
        else:
            down.append(symbol)
    if up and down:
        affected = (
            "Spillover was split across the tape: "
            f"gainers included {', '.join(up[:3])}, while laggards included {', '.join(down[:3])}."
        )
    elif up:
        affected = f"Spillover was mostly on the upside, including {', '.join(up[:4])}."
    elif down:
        affected = f"Spillover was mostly on the downside, including {', '.join(down[:4])}."
    else:
        affected = ""
    surface_summary = _compose_surface_summary(what_happened, why_happened)
    return {
        "title": event_title_seed or cluster_label or "Market event",
        "surface_summary": surface_summary,
        "what_happened_text": what_happened,
        "why_happened_text": why_happened,
        "affected_assets_summary_text": affected,
        "background_context_text": "",
    }


def _write_event_bundle(
    event_title_seed: str,
    cluster_rows: pd.DataFrame,
    retained_claims: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    yield_facts: dict[str, Any] | None = None,
    event_type: str = "",
    cause_status: str = "",
    evidence_quality: str = "",
    freshness_quality: str = "",
) -> dict[str, str]:
    fallback = _fallback_event_writer(
        event_title_seed,
        cluster_rows,
        retained_claims,
        yield_facts=yield_facts,
        event_type=event_type,
    )
    if llm_client is None:
        return fallback
    user_prompt = json.dumps(
        _build_event_writer_payload(
            event_title_seed,
            cluster_rows,
            retained_claims,
            fallback=fallback,
            yield_facts=yield_facts,
            event_type=event_type,
            cause_status=cause_status,
            evidence_quality=evidence_quality,
            freshness_quality=freshness_quality,
        ),
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=EVENT_WRITER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema_name="attention_event_writer",
            schema=EVENT_WRITER_SCHEMA,
        )
        title = _coerce_text(data.get("title")) or fallback["title"]
        if _looks_like_generic_tape_title(title):
            title = fallback["title"]
        what_happened = _coerce_text(data.get("what_happened_text")) or fallback["what_happened_text"]
        why_happened = _coerce_text(data.get("why_happened_text")) or fallback["why_happened_text"]
        affected = _coerce_text(data.get("affected_assets_summary_text")) or fallback["affected_assets_summary_text"]
        background_text = _coerce_text(data.get("background_context_text")) or fallback["background_context_text"]
        if _looks_like_stat_dump(what_happened):
            what_happened = fallback["what_happened_text"]
        if _looks_like_stat_dump(why_happened) and not _has_causal_language(why_happened):
            why_happened = fallback["why_happened_text"]
        if event_type != "rates" and not _has_causal_language(why_happened):
            why_happened = fallback["why_happened_text"]
        if event_type != "rates" and _is_yield_only_explanation(why_happened):
            why_happened = fallback["why_happened_text"]
        if event_type != "rates" and (_looks_like_stat_dump(why_happened) or _is_yield_only_explanation(why_happened)):
            claim_seed = _best_why_claim_sentence(retained_claims)
            if (
                claim_seed
                and (_has_causal_language(claim_seed) or event_type == "rates")
                and not (_looks_like_stat_dump(claim_seed) and not _has_causal_language(claim_seed))
            ):
                why_happened = claim_seed
            else:
                why_happened = _specific_why_fallback(retained_claims)
        if event_type != "rates" and why_happened and not _has_causal_language(why_happened):
            why_happened = _specific_why_fallback(retained_claims)
        if _looks_like_stat_dump(affected):
            affected = fallback["affected_assets_summary_text"]
        if _text_overlap(what_happened, affected) >= 0.62:
            fallback_affected = _coerce_text(fallback.get("affected_assets_summary_text"))
            affected = fallback_affected if _text_overlap(what_happened, fallback_affected) < 0.62 else ""
        surface_summary = _coerce_text(data.get("surface_summary"))
        if not surface_summary or _looks_like_stat_dump(surface_summary):
            surface_summary = _compose_surface_summary(what_happened, why_happened)
        elif _text_overlap(surface_summary, what_happened) >= 0.9 and _text_overlap(surface_summary, why_happened) < 0.35:
            surface_summary = _compose_surface_summary(what_happened, why_happened)
        return {
            "title": title,
            "surface_summary": surface_summary,
            "what_happened_text": what_happened,
            "why_happened_text": why_happened,
            "affected_assets_summary_text": affected,
            "background_context_text": background_text,
        }
    except Exception:
        return fallback


def _augment_candidate_frame(
    candidates: pd.DataFrame,
    *,
    asof_time_utc: pd.Timestamp,
    run_id: str,
) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    out = candidates.copy()
    out["run_id"] = run_id
    out["asof_time_utc"] = asof_time_utc
    out["entity_type"] = out.get("security_type", pd.Series(dtype=str)).fillna("").astype(str).replace("", "symbol")
    out["price"] = pd.to_numeric(out.get("close", pd.Series(index=out.index, dtype=float)), errors="coerce")
    out["liquidity_rank"] = pd.to_numeric(
        out.get("dollar_volume", pd.Series(index=out.index, dtype=float)),
        errors="coerce",
    ).rank(ascending=False, method="dense")
    out["peer_group_id"] = out.apply(
        lambda row: _coerce_text(row.get("peer_group_id"))
        or _informative_graph_label(row.get("peer_group_name"))
        or (
            _coerce_text(row.get("industry"))
            if _coerce_text(row.get("industry")) and _coerce_text(row.get("industry")) != "Unknown"
            else _coerce_text(row.get("sector")) or _coerce_text(row.get("asset_class"))
        ),
        axis=1,
    )
    out["macro_exposure_tags"] = out.apply(
        lambda row: _merge_text_values(row.get("macro_exposure_tags"), row.get("macro_role_tags")),
        axis=1,
    )
    out["business_tags"] = out.apply(
        lambda row: _merge_text_values(row.get("business_tags"), row.get("business_role_tags")),
        axis=1,
    )
    return out


def _candidate_bundle_item(bundle: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_id": _coerce_text(bundle.get("bundle_id")),
        "symbol": _normalize_symbol(candidate.get("symbol")),
        "headline": _coerce_text(bundle.get("headline")),
        "what_changed_text": _coerce_text(bundle.get("what_changed_text")),
        "why_now_text": _coerce_text(bundle.get("why_now_text")),
        "what_else_moved_text": _coerce_text(bundle.get("what_else_moved_text")),
        "cause_status": _coerce_text(bundle.get("cause_status")) or "unresolved",
        "confidence_label": _coerce_text(bundle.get("confidence_label")) or "Developing",
        "candidate_score": _coerce_float(candidate.get("candidate_score")),
        "change_pct": _coerce_float(candidate.get("change_pct")),
        "expected_move_pct": _coerce_float(candidate.get("expected_move_pct")),
        "surprise_z": _coerce_float(candidate.get("surprise_z")),
        "sector": _coerce_text(candidate.get("sector")),
        "industry": _coerce_text(candidate.get("industry")),
        "source_label": _coerce_text(candidate.get("source_label")),
        "top_source": _coerce_text(bundle.get("source_summary") or candidate.get("top_source")),
        "best_authority_rank": int(bundle.get("best_authority_rank") or candidate.get("best_authority_rank") or 9),
        "source_count": int(bundle.get("source_count") or candidate.get("source_count") or 0),
        "evidence_count": int(bundle.get("evidence_count") or candidate.get("evidence_count") or 0),
        "same_day_evidence_count": int(bundle.get("same_day_evidence_count") or candidate.get("same_day_evidence_count") or 0),
        "surface_summary_text": _coerce_text(bundle.get("surface_summary_text")),
        "surface_what_changed_text": _coerce_text(bundle.get("what_changed_text")),
        "surface_why_text": _coerce_text(bundle.get("why_now_text")),
        "surface_what_else_moved_text": _coerce_text(bundle.get("what_else_moved_text")),
        "surface_cause_status": _coerce_text(bundle.get("cause_status")),
        "surface_evidence_quality": _coerce_text(bundle.get("evidence_quality")),
        "surface_freshness_quality": _coerce_text(bundle.get("freshness_quality")),
        "surface_source_summary": _coerce_text(bundle.get("source_summary")),
        "surface_confidence_label": _coerce_text(bundle.get("confidence_label")),
    }


def _event_item(bundle: dict[str, Any], cluster_rows: pd.DataFrame, event_type: str, *, event_score: float) -> dict[str, Any]:
    has_cluster_rows = isinstance(cluster_rows, pd.DataFrame) and not cluster_rows.empty
    if has_cluster_rows:
        drivers = [
            _normalize_symbol(row.get("symbol"))
            for _, row in cluster_rows.sort_values("candidate_score", ascending=False).head(3).iterrows()
            if _normalize_symbol(row.get("symbol"))
        ]
        up = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) >= 0 and _normalize_symbol(row.get("symbol"))]
        down = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) < 0 and _normalize_symbol(row.get("symbol"))]
        anchor_row = cluster_rows.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).iloc[0]
        anchor_symbol = _normalize_symbol(anchor_row.get("symbol"))
        anchor_direction = _move_direction(anchor_row.get("change_pct"))
        supporting_symbols = list(
            dict.fromkeys(
                [
                    _normalize_symbol(row.get("symbol"))
                    for _, row in cluster_rows.iterrows()
                    if _normalize_symbol(row.get("symbol"))
                ]
            )
        )
    else:
        drivers = [_normalize_symbol(value) for value in _safe_list(bundle.get("driver_symbols")) if _normalize_symbol(value)]
        up = [_normalize_symbol(value) for value in _safe_list(bundle.get("beneficiary_symbols")) if _normalize_symbol(value)]
        down = [_normalize_symbol(value) for value in _safe_list(bundle.get("loser_symbols")) if _normalize_symbol(value)]
        supporting_symbols = [_normalize_symbol(value) for value in _safe_list(bundle.get("supporting_symbols")) if _normalize_symbol(value)]
        anchor_symbol = _normalize_symbol(bundle.get("anchor_symbol")) or (supporting_symbols[0] if supporting_symbols else "")
        anchor_direction = _coerce_text(bundle.get("anchor_direction")).lower()
        if anchor_direction not in {"up", "down"}:
            anchor_direction = "up" if up else ("down" if down else "")
    surprise_score = _coerce_float(bundle.get("surprise_score"))
    surprise_z = _coerce_float(bundle.get("surprise_z"))
    reaction_pct = _coerce_float(bundle.get("cross_asset_reaction_pct"))
    support_score = _coerce_float(bundle.get("support_score"))
    contradiction_score = _coerce_float(bundle.get("contradiction_score"))
    return {
        "bundle_id": _coerce_text(bundle.get("bundle_id")),
        "market_event_id": _coerce_text(bundle.get("event_id")),
        "event_type": event_type,
        "event_title": _coerce_text(bundle.get("event_title")),
        "event_score": round(event_score, 1),
        "anchor_symbol": anchor_symbol,
        "anchor_direction": anchor_direction,
        "what_happened_text": _coerce_text(bundle.get("what_happened_text")),
        "why_happened_text": _coerce_text(bundle.get("why_happened_text")),
        "affected_assets_summary_text": _coerce_text(bundle.get("affected_assets_summary_text")),
        "driver_symbols": drivers,
        "beneficiary_symbols": up[:6],
        "loser_symbols": down[:6],
        "supporting_symbols": supporting_symbols[:8],
        "cause_status": _coerce_text(bundle.get("cause_status")) or "unresolved",
        "confidence_label": _coerce_text(bundle.get("confidence_label")) or "Developing",
        "source_count": int(bundle.get("source_count") or 0),
        "evidence_count": int(bundle.get("evidence_count") or 0),
        "surface_summary_text": _coerce_text(bundle.get("surface_summary_text")),
        "surface_what_changed_text": _coerce_text(bundle.get("what_happened_text")),
        "surface_why_text": _coerce_text(bundle.get("why_happened_text")),
        "surface_what_else_moved_text": _coerce_text(bundle.get("affected_assets_summary_text")),
        "surface_cause_status": _coerce_text(bundle.get("cause_status")),
        "surface_evidence_quality": _coerce_text(bundle.get("evidence_quality")),
        "surface_freshness_quality": _coerce_text(bundle.get("freshness_quality")),
        "surface_source_summary": _coerce_text(bundle.get("source_summary")),
        "surface_confidence_label": _coerce_text(bundle.get("confidence_label")),
        "importance_tier": _coerce_text(bundle.get("importance_tier")),
        "surprise_score": round(surprise_score, 2) if np.isfinite(surprise_score) else None,
        "surprise_source": _coerce_text(bundle.get("surprise_source")),
        "surprise_z": round(surprise_z, 4) if np.isfinite(surprise_z) else None,
        "cross_asset_reaction_pct": round(reaction_pct, 4) if np.isfinite(reaction_pct) else None,
        "release_type": _coerce_text(bundle.get("release_type")),
        "release_time_utc": _coerce_text(bundle.get("release_time_utc")),
        "promotion_reason": _coerce_text(bundle.get("promotion_reason")),
        "is_forced_macro_release": bool(bundle.get("is_forced_macro_release")),
        "status": _coerce_text(bundle.get("status")),
        "hypothesis_status": _coerce_text(bundle.get("hypothesis_status")),
        "support_score": round(support_score, 4) if np.isfinite(support_score) else None,
        "contradiction_score": round(contradiction_score, 4) if np.isfinite(contradiction_score) else None,
        "relationship_holding_count": int(bundle.get("relationship_holding_count") or 0),
        "relationship_mixed_count": int(bundle.get("relationship_mixed_count") or 0),
        "relationship_broken_count": int(bundle.get("relationship_broken_count") or 0),
        "relationship_unresolved_count": int(bundle.get("relationship_unresolved_count") or 0),
        "primary_nodes": list(_safe_list(bundle.get("primary_nodes"))),
        "source_dataset": _coerce_text(bundle.get("source_dataset")),
    }


def _build_candidate_bundle(
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    prompt_version: str,
    model_name: str,
    run_id: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cause_status, why_today_mode = _judge_cause_status(claims)
    evidence_quality, freshness_quality = _quality_label(claims, cause_status)
    written = _write_symbol_bundle(candidate, claims, peer_moves, llm_client=llm_client, cause_status=cause_status, yield_facts=yield_facts)
    document_importance = _document_importance_map(claims)
    evidence = []
    background = []
    for doc in documents:
        document_id = _coerce_text(doc.get("document_id"))
        source_kind = _coerce_text(doc.get("source_kind"))
        evidence_role = (
            "same_day"
            if _freshness_score(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce"), pd.to_datetime(candidate.get("asof_time_utc"), utc=True, errors="coerce")) >= 0.95
            else "background"
        )
        importance_score = float(document_importance.get(document_id, 0.0))
        importance_label = _importance_label(importance_score)
        is_important = importance_score >= 0.5
        if source_kind.lower() in {"news", "search"} and evidence_role == "same_day" and cause_status == "supported":
            is_important = is_important or importance_score >= 0.45
        if source_kind.lower() in {"news", "search"} and int(doc.get("authority_rank") or 3) <= 1:
            is_important = is_important or importance_score >= 0.42
        item = {
            "document_id": document_id,
            "source": _coerce_text(doc.get("source_provider")),
            "source_kind": source_kind,
            "search_provider": _coerce_text(doc.get("search_provider")),
            "authority_bucket": _coerce_text(doc.get("source_authority_bucket")),
            "headline": _coerce_text(doc.get("title")),
            "summary": _coerce_text(doc.get("display_excerpt") or doc.get("raw_text")),
            "display_excerpt": _coerce_text(doc.get("display_excerpt")),
            "url": _coerce_text(doc.get("url")),
            "published_at": _coerce_text(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce")) else ""),
            "evidence_role": evidence_role,
            "importance_score": round(importance_score, 3),
            "importance_label": importance_label,
            "is_important": bool(is_important),
        }
        if item["evidence_role"] == "same_day":
            evidence.append(item)
        else:
            background.append(item)
    top_claim_ids = [item["claim_id"] for item in claims[:4]]
    important_news_count = sum(
        1
        for item in evidence + background
        if bool(item.get("is_important")) and _coerce_text(item.get("source_kind")).lower() in {"news", "search"}
    )
    return {
        "bundle_id": _coerce_text(candidate.get("bundle_id")) or f"symbol::{_normalize_symbol(candidate.get('symbol'))}",
        "bundle_type": "symbol",
        "run_id": run_id,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "symbol": _normalize_symbol(candidate.get("symbol")),
        "headline": written["title"],
        "surface_summary_text": written["surface_summary"],
        "what_changed_text": written["what_changed_text"],
        "why_now_text": written["why_today_text"],
        "what_else_moved_text": written["what_else_moved_text"],
        "background_context_text": written["background_context_text"],
        "cause_status": cause_status,
        "why_today_mode": why_today_mode,
        "evidence_quality": evidence_quality,
        "freshness_quality": freshness_quality,
        "confidence_label": "High" if cause_status == "supported" and evidence_quality == "High" else ("Medium" if cause_status == "supported" else "Developing"),
        "sector": _coerce_text(candidate.get("sector")),
        "industry": _coerce_text(candidate.get("industry")),
        "source_summary": _top_sources(claims),
        "source_count": len({item.get("source") for item in claims if _coerce_text(item.get("source"))}),
        "evidence_count": len(documents),
        "same_day_evidence_count": len([item for item in claims if bool(item.get("is_same_day"))]),
        "important_news_count": int(important_news_count),
        "evidence": evidence[:6],
        "background_context": background[:4],
        "related_symbols": [],
        "peer_moves": peer_moves[:6],
        "claims": claims[:8],
        "supporting_claim_ids": top_claim_ids,
        "yield_facts": dict(yield_facts or {}),
        "source_trace": {
            "sources": list(dict.fromkeys(doc.get("source_kind") for doc in documents if _coerce_text(doc.get("source_kind")))),
            "macro_facts_dataset": "yield_curve_facts_1d" if yield_facts else "",
        },
    }


def _build_event_bundle(
    event_id: str,
    cluster_rows: pd.DataFrame,
    cluster_claims: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    prompt_version: str,
    model_name: str,
    run_id: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cluster_tokens = set()
    for _, row in cluster_rows.iterrows():
        cluster_tokens |= _tag_tokens(_safe_list(row.get("macro_exposure_tags")) + _safe_list(row.get("business_tags")))
    cluster_tokens |= _claim_entities(cluster_claims)
    event_type = _infer_event_type(cluster_tokens, cluster_rows)
    anchor_row = cluster_rows.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).iloc[0]
    event_title_seed = _event_title_from_cluster(cluster_rows)
    cause_status, why_today_mode = _judge_cause_status(cluster_claims)
    evidence_quality, freshness_quality = _quality_label(cluster_claims, cause_status)
    written = _write_event_bundle(
        event_title_seed,
        cluster_rows,
        cluster_claims,
        llm_client=llm_client,
        yield_facts=yield_facts,
        event_type=event_type,
        cause_status=cause_status,
        evidence_quality=evidence_quality,
        freshness_quality=freshness_quality,
    )
    event_score = float(cluster_rows["candidate_score"].fillna(0).sum()) + len(cluster_rows["asset_class"].astype(str).dropna().unique()) * 8.0
    up_symbols = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) >= 0]
    down_symbols = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) < 0]
    bundle = {
        "bundle_id": f"event::{event_id}",
        "bundle_type": "event",
        "run_id": run_id,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "event_id": event_id,
        "event_title": written["title"],
        "surface_summary_text": written["surface_summary"],
        "what_happened_text": written["what_happened_text"],
        "why_happened_text": written["why_happened_text"],
        "affected_assets_summary_text": written["affected_assets_summary_text"],
        "background_context_text": written["background_context_text"],
        "cause_status": cause_status,
        "why_today_mode": why_today_mode,
        "evidence_quality": evidence_quality,
        "freshness_quality": freshness_quality,
        "confidence_label": "High" if cause_status == "supported" and evidence_quality == "High" else ("Medium" if cause_status == "supported" else "Developing"),
        "source_summary": _top_sources(cluster_claims),
        "source_count": len({item.get("source") for item in cluster_claims if _coerce_text(item.get("source"))}),
        "evidence_count": len(cluster_claims),
        "same_day_evidence_count": len([item for item in cluster_claims if bool(item.get("is_same_day"))]),
        "supporting_symbols": list(dict.fromkeys([_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _normalize_symbol(row.get("symbol"))])),
        "driver_symbols": list(dict.fromkeys(down_symbols[:6] if _move_direction(anchor_row.get("change_pct")) == "down" else up_symbols[:6])),
        "beneficiary_symbols": list(dict.fromkeys(up_symbols[:6])),
        "loser_symbols": list(dict.fromkeys(down_symbols[:6])),
        "claims": cluster_claims[:10],
        "supporting_claim_ids": [item["claim_id"] for item in cluster_claims[:6]],
        "yield_facts": dict(yield_facts or {}) if _cluster_uses_yield_context(cluster_rows) else {},
        "peer_moves": [],
        "related_symbols": [
            {
                "symbol": _normalize_symbol(row.get("symbol")),
                "headline": _coerce_text(row.get("headline")),
                "change_pct": _coerce_float(row.get("change_pct")),
                "sector": _coerce_text(row.get("sector")),
                "industry": _coerce_text(row.get("industry")),
            }
            for _, row in cluster_rows.iterrows()
        ],
        "event_type": event_type,
        "event_score": round(event_score, 1),
    }
    return bundle


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
        from .attention_materialized import bars_by_symbol_from_price_history

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
        is_forced = importance_tier == "high" and surprise_score >= surprise_threshold
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
        else _empty_macro_causal_graph_edges_frame()
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


def _heuristic_macro_verdict(hypothesis_text: str, title: str, snippet: str) -> str:
    combined = _normalized_text(f"{title} {snippet}")
    if not combined:
        return "neutral"
    contradiction_markers = (
        "contrary",
        "unexpectedly",
        "despite",
        "reversed",
        "did not",
        "failed to",
        "against",
        "opposite",
    )
    if any(marker in combined for marker in contradiction_markers):
        return "contradict"
    overlap = _text_overlap(hypothesis_text, combined)
    if overlap >= 0.12:
        return "support"
    return "neutral"


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
    serp_client: SerpAPISearchClient | None,
    tavily_client: TavilySearchClient | None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
            verdict = verdict_by_result.get(result_id) or _heuristic_macro_verdict(
                _coerce_text(row.get("hypothesis_text")),
                title,
                snippet,
            )
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


def _build_home_payload(
    candidates: pd.DataFrame,
    bundle_map: dict[str, dict[str, Any]],
    event_bundles: list[dict[str, Any]],
    *,
    attention_rows: pd.DataFrame | None,
    generated_at_utc: pd.Timestamp,
    run_id: str,
    entity_master: pd.DataFrame,
    top_events_limit: int,
    must_read_limit: int,
    unresolved_limit: int,
    macro_release_bundles: list[dict[str, Any]] | None = None,
    macro_release_force_limit: int = 0,
    relationship_checks_frame: pd.DataFrame | None = None,
    hypotheses_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    top_event_items = [
        _event_item(
            bundle,
            candidates[candidates["symbol"].astype(str).str.upper().isin(set(bundle.get("supporting_symbols") or []))].copy(),
            _coerce_text(bundle.get("event_type")) or "cluster",
            event_score=_coerce_float(bundle.get("event_score"), 0.0),
        )
        for bundle in sorted(event_bundles, key=lambda item: -_coerce_float(item.get("event_score"), 0.0))[: max(int(top_events_limit), 1)]
    ]
    macro_release_items = list(macro_release_bundles or [])
    all_qualifying_macro_release_bundles = sorted(
        [item for item in macro_release_items if bool(item.get("is_forced_macro_release"))],
        key=lambda item: (
            -_coerce_float(item.get("surprise_score"), 0.0),
            -_coerce_float(item.get("event_score"), 0.0),
            _coerce_text(item.get("release_type")),
        ),
    )
    qualifying_macro_release_bundles = list(all_qualifying_macro_release_bundles)
    if macro_release_force_limit == 0:
        qualifying_macro_release_bundles = []
    elif macro_release_force_limit > 0:
        qualifying_macro_release_bundles = qualifying_macro_release_bundles[:macro_release_force_limit]
    existing_bundle_ids = {_coerce_text(item.get("bundle_id")) for item in top_event_items if _coerce_text(item.get("bundle_id"))}
    forced_macro_added = 0
    for bundle in qualifying_macro_release_bundles:
        bundle_id = _coerce_text(bundle.get("bundle_id"))
        if bundle_id and bundle_id in existing_bundle_ids:
            continue
        scoped_rows = candidates[candidates["symbol"].astype(str).str.upper().isin(set(bundle.get("supporting_symbols") or []))].copy()
        top_event_items.append(
            _event_item(
                bundle,
                scoped_rows,
                _coerce_text(bundle.get("event_type")) or "macro_release",
                event_score=_coerce_float(bundle.get("event_score"), 0.0),
            )
        )
        if bundle_id:
            existing_bundle_ids.add(bundle_id)
        forced_macro_added += 1
    absorbed_symbols = {
        _normalize_symbol(symbol)
        for event in top_event_items
        for symbol in _safe_list(event.get("supporting_symbols"))
        if _normalize_symbol(symbol)
    }
    candidate_rows = candidates.to_dict(orient="records")
    must_read: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        symbol = _normalize_symbol(candidate.get("symbol"))
        bundle = bundle_map.get(f"symbol::{symbol}", {})
        if not bundle or symbol in absorbed_symbols:
            continue
        item = _candidate_bundle_item(bundle, candidate)
        same_day = int(bundle.get("same_day_evidence_count") or 0)
        cause_status = _coerce_text(bundle.get("cause_status"))
        if cause_status == "supported" and same_day > 0:
            must_read.append(item)
        elif abs(_coerce_float(candidate.get("change_pct"), 0.0)) >= 4.0:
            unresolved.append(item)
    must_read = sorted(
        must_read,
        key=lambda item: (-int(item.get("same_day_evidence_count") or 0), -_coerce_float(item.get("candidate_score"), 0.0), -abs(_coerce_float(item.get("change_pct"), 0.0))),
    )[: max(int(must_read_limit), 1)]
    unresolved = sorted(
        unresolved,
        key=lambda item: (-_coerce_float(item.get("candidate_score"), 0.0), -abs(_coerce_float(item.get("change_pct"), 0.0))),
    )[: max(int(unresolved_limit), 1)]
    taxonomy_horizon_trends = _taxonomy_horizon_trends(attention_rows, candidates)
    event_impacts = []
    for event in top_event_items:
        for symbol in _safe_list(event.get("supporting_symbols")):
            symbol_key = _normalize_symbol(symbol)
            row = next((item for item in candidate_rows if _normalize_symbol(item.get("symbol")) == symbol_key), {})
            if not row:
                continue
            if symbol_key in set(_safe_list(event.get("driver_symbols"))):
                impact_role = "driver"
            elif _coerce_float(row.get("change_pct"), 0.0) >= 0:
                impact_role = "beneficiary"
            else:
                impact_role = "affected"
            event_impacts.append(
                {
                    "market_event_id": _coerce_text(event.get("market_event_id")),
                    "symbol": symbol_key,
                    "impact_role": impact_role,
                    "direction": _move_direction(row.get("change_pct")),
                    "change_pct": _coerce_float(row.get("change_pct")),
                    "sector": _coerce_text(row.get("sector")),
                    "industry": _coerce_text(row.get("industry")),
                    "bundle_id": f"symbol::{symbol_key}",
                }
            )
    coverage_summary = {
        "candidate_count": int(len(candidates)),
        "event_count": int(len(top_event_items)),
        "must_read_count": int(len(must_read)),
        "unresolved_count": int(len(unresolved)),
        "macro_anchor_count": int(candidates["source_label"].astype(str).str.lower().eq("macro anchor").sum()) if "source_label" in candidates.columns else 0,
        "news_backed_count": int(sum(1 for bundle in bundle_map.values() if int(bundle.get("same_day_evidence_count") or 0) > 0)),
        "portfolio_overlap_count": int(candidates.get("in_portfolio", pd.Series(dtype=bool)).fillna(False).sum()) if "in_portfolio" in candidates.columns else 0,
        "today_only": len(taxonomy_horizon_trends) == 0,
        "supports_multi_horizon": len(taxonomy_horizon_trends) > 0,
        "taxonomy_trend_horizon_count": int(len(taxonomy_horizon_trends)),
        "taxonomy_trend_cohort_count": int(sum(len(item.get("cohorts") or []) for item in taxonomy_horizon_trends)),
        "macro_release_detected_count": int(len(macro_release_items)),
        "macro_release_qualifying_count": int(len(all_qualifying_macro_release_bundles)),
        "macro_release_promoted_count": int(forced_macro_added),
        "macro_release_suppressed_count": int(max(len(all_qualifying_macro_release_bundles) - forced_macro_added, 0)),
        "macro_relationship_check_count": int(len(relationship_checks_frame)) if isinstance(relationship_checks_frame, pd.DataFrame) else 0,
        "macro_relationship_holding_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("holding").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_mixed_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("mixed").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_broken_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("broken").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_unresolved_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("unresolved").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_hypothesis_count": int(len(hypotheses_frame)) if isinstance(hypotheses_frame, pd.DataFrame) else 0,
        "macro_hypothesis_supported_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("supported").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_continuation_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("continuation").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_conflicting_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("conflicting").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_unresolved_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("unresolved").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "run_id": run_id,
    }
    return {
        "top_events": top_event_items,
        "must_read_movers": must_read,
        "unresolved_large_moves": unresolved,
        "generated_at_utc": generated_at_utc.isoformat(),
        "coverage_summary": coverage_summary,
        "taxonomy_horizon_trends": taxonomy_horizon_trends,
        "event_candidates_1d": candidates.to_dict(orient="records"),
        "event_impacts_1d": event_impacts,
        "entity_master": entity_master.to_dict(orient="records") if isinstance(entity_master, pd.DataFrame) else [],
        "run_id": run_id,
    }


def build_bottom_up_attention_artifacts(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    topology_universe_frame: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    generated_at_utc: datetime | str | None = None,
    filings_frame: pd.DataFrame | None = None,
    fred_summary_frame: pd.DataFrame | None = None,
    yield_curve_facts_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    run_id: str | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    top_events_limit: int = 5,
    must_read_limit: int = 10,
    unresolved_limit: int = 5,
    research_limit: int = 40,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> AgenticAttentionArtifacts:
    from .attention_home_1d import build_attention_entity_master, build_attention_event_candidates_1d
    from .attention_materialized import serialize_attention_home_payload, serialize_attention_research_bundles
    from .attention_graph_network import build_homepage_attention_graph_payload

    asof_time_utc = pd.to_datetime(generated_at_utc or datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(asof_time_utc):
        asof_time_utc = pd.Timestamp.now(tz="UTC")
    run_id = _coerce_text(run_id) or f"attention-run-{uuid.uuid4().hex[:12]}"
    macro_profile = _load_attention_macro_signal_profile()
    release_rules = macro_profile.get("release_rules") if isinstance(macro_profile.get("release_rules"), dict) else {}
    macro_release_force_limit = max(int(_coerce_float(release_rules.get("force_limit"), 2)), 0)
    entity_rows = entity_master if isinstance(entity_master, pd.DataFrame) else build_attention_entity_master(daily_movers.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist())
    base_candidates = build_attention_event_candidates_1d(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_rows,
        holdings=holdings,
        asof_time_utc=asof_time_utc.isoformat(),
    )
    candidates = _augment_candidate_frame(base_candidates, asof_time_utc=asof_time_utc, run_id=run_id)
    if candidates.empty:
        empty_payload = {
            "top_events": [],
            "must_read_movers": [],
            "unresolved_large_moves": [],
            "generated_at_utc": asof_time_utc.isoformat(),
            "coverage_summary": {"candidate_count": 0, "event_count": 0, "must_read_count": 0, "unresolved_count": 0, "run_id": run_id},
            "taxonomy_horizon_trends": [],
            "event_candidates_1d": [],
            "event_impacts_1d": [],
            "entity_master": entity_rows.to_dict(orient="records") if isinstance(entity_rows, pd.DataFrame) else [],
            "homepage_graph": {},
            "run_id": run_id,
        }
        frames = {
            "attention_candidates_1d": pd.DataFrame(),
            "attention_research_plans": pd.DataFrame(),
            "attention_search_requests": pd.DataFrame(),
            "attention_search_results": pd.DataFrame(),
            "attention_source_documents": pd.DataFrame(),
            "attention_evidence_chunks": pd.DataFrame(),
            "attention_claims": pd.DataFrame(),
            "attention_candidate_graph": pd.DataFrame(),
            "attention_event_clusters_1d": pd.DataFrame(),
            "macro_release_events_1d": _empty_macro_release_events_frame(),
            "attention_macro_context_1d": _empty_attention_macro_context_frame(),
            "macro_causal_graph_edges_v1": _empty_macro_causal_graph_edges_frame(),
            "macro_relationship_checks_1d": _empty_macro_relationship_checks_frame(),
            "attention_hypotheses_1d": _empty_attention_hypotheses_frame(),
            "attention_home_snapshots_1d": serialize_attention_home_payload(empty_payload),
            "attention_bundle_snapshots": serialize_attention_research_bundles({}, generated_at_utc=asof_time_utc),
        }
        return AgenticAttentionArtifacts(home_payload=empty_payload, bundle_map={}, frames=frames)

    model_name = getattr(getattr(llm_client, "config", object()), "model", DEFAULT_WRITER_MODEL) if llm_client is not None else "heuristic"
    serp_client, tavily_client = _load_search_clients()

    research_candidates = candidates.head(max(int(research_limit), 1)).to_dict(orient="records")
    plan_rows: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    chunk_frames: list[pd.DataFrame] = []
    claim_frames: list[pd.DataFrame] = []
    bundle_map: dict[str, dict[str, Any]] = {}
    claim_map: dict[str, list[dict[str, Any]]] = {}

    research_total = len(research_candidates)
    for index, candidate in enumerate(research_candidates, start=1):
        if progress_callback is not None:
            try:
                progress_callback(index, research_total, candidate)
            except Exception:
                pass
        peer_symbols = _peer_candidates(candidate, candidates, limit=5)
        plan = _plan_candidate_research(candidate, peer_symbols, llm_client)
        plan_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "symbol": _normalize_symbol(candidate.get("symbol")),
                "prompt_version": prompt_version,
                "model_name": model_name,
                "research_subjects_json": _json_dumps(plan.get("research_subjects") or []),
                "hypotheses_json": _json_dumps(plan.get("hypotheses") or []),
                "queries_json": _json_dumps(plan.get("queries") or []),
                "official_routes_json": _json_dumps(plan.get("official_routes") or []),
                "priority_entities_json": _json_dumps(plan.get("priority_entities") or []),
                "evidence_budget": int(plan.get("evidence_budget") or 8),
            }
        )
        per_query_budget = max(int(plan.get("evidence_budget") or 8) // max(len(plan.get("queries") or []), 1), 2)
        candidate_results: list[dict[str, Any]] = []
        for query in list(plan.get("queries") or [])[:4]:
            req_rows, res_rows = _search_query_results(
                _coerce_text((query or {}).get("query")),
                candidate_id=_coerce_text(candidate.get("candidate_id")),
                symbol=_normalize_symbol(candidate.get("symbol")),
                company_name=_candidate_company_name(candidate),
                run_id=run_id,
                asof_time_utc=asof_time_utc,
                serp_client=serp_client,
                tavily_client=tavily_client,
                llm_client=llm_client,
                budget=per_query_budget,
            )
            request_rows.extend(req_rows)
            result_rows.extend(res_rows)
            candidate_results.extend(res_rows)
        documents = _candidate_context_documents(
            candidate,
            news_payloads=news_payloads,
            context_payloads=context_payloads,
            filings_frame=filings_frame,
            fred_summary_frame=fred_summary_frame,
            yield_curve_facts_frame=yield_curve_facts_frame,
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            official_routes=[_coerce_text(route) for route in list(plan.get("official_routes") or [])],
            priority_entities=[_coerce_text(entity) for entity in list(plan.get("priority_entities") or []) if _coerce_text(entity)],
        )
        documents.extend(
            _documents_from_search_results(
                candidate,
                candidate_results,
                run_id=run_id,
                asof_time_utc=asof_time_utc,
            )
        )
        deduped_documents: list[dict[str, Any]] = []
        seen_doc_ids: set[str] = set()
        for item in documents:
            doc_id = _coerce_text(item.get("document_id"))
            if not doc_id or doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            deduped_documents.append(item)
        document_rows.extend(deduped_documents)
        chunks = _chunk_source_documents(
            deduped_documents,
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            embedding_client=embedding_client,
        )
        if not chunks.empty:
            chunk_frames.append(chunks)
        claims = _extract_claims(
            candidate,
            chunks,
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            hypotheses=list(plan.get("hypotheses") or []),
            llm_client=llm_client,
        )
        claim_map[_normalize_symbol(candidate.get("symbol"))] = claims
        claims_frame = _serialize_claims_frame(claims, asof_time_utc=asof_time_utc)
        if not claims_frame.empty:
            claim_frames.append(claims_frame)
        peer_moves = [
            {
                "symbol": peer,
                "change_pct": _coerce_float(candidates[candidates["symbol"] == peer].head(1)["change_pct"].iloc[0]) if not candidates[candidates["symbol"] == peer].empty else math.nan,
                "relationship": _coerce_text(candidates[candidates["symbol"] == peer].head(1)["peer_group_id"].iloc[0]) if not candidates[candidates["symbol"] == peer].empty else "",
                "headline": _coerce_text(candidates[candidates["symbol"] == peer].head(1)["headline"].iloc[0]) if not candidates[candidates["symbol"] == peer].empty else "",
            }
            for peer in peer_symbols[:4]
        ]
        bundle = _build_candidate_bundle(
            candidate,
            claims,
            peer_moves,
            deduped_documents,
            llm_client=llm_client,
            prompt_version=prompt_version,
            model_name=model_name,
            run_id=run_id,
            yield_facts=_latest_yield_facts(yield_curve_facts_frame) if _yield_context_relevant(candidate) else {},
        )
        bundle_map[bundle["bundle_id"]] = bundle

    for _, candidate_row in candidates.iterrows():
        symbol = _normalize_symbol(candidate_row.get("symbol"))
        bundle_id = f"symbol::{symbol}"
        if bundle_id in bundle_map:
            continue
        candidate = candidate_row.to_dict()
        bundle_map[bundle_id] = _build_candidate_bundle(
            candidate,
            [],
            [],
            [],
            llm_client=None,
            prompt_version=prompt_version,
            model_name="heuristic",
            run_id=run_id,
            yield_facts=_latest_yield_facts(yield_curve_facts_frame) if _yield_context_relevant(candidate) else {},
        )
        claim_map[symbol] = []

    history_corr_map = _history_correlation_map(
        bars_by_symbol,
        candidates.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist(),
        min_observations=attention_graph_policy().history_corr_min_observations,
    )
    graph = _graph_edges(
        candidates,
        claim_map,
        history_correlation_map=history_corr_map,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    clusters = _cluster_candidates(candidates, graph)
    event_bundles: list[dict[str, Any]] = []
    event_cluster_rows: list[dict[str, Any]] = []
    for index, symbols in enumerate(clusters, start=1):
        cluster_rows = candidates[candidates["symbol"].astype(str).str.upper().isin(set(symbols))].copy()
        cluster_claims: list[dict[str, Any]] = []
        for symbol in symbols:
            cluster_claims.extend(claim_map.get(symbol, []))
        cluster_claims = sorted(
            cluster_claims,
            key=lambda item: (
                -float(item.get("confidence_score") or 0.0),
                -float(item.get("causal_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
            ),
        )
        cluster_id = f"cluster-{index:02d}-{hashlib.sha1('|'.join(symbols).encode('utf-8')).hexdigest()[:10]}"
        bundle = _build_event_bundle(
            cluster_id,
            cluster_rows,
            cluster_claims,
            llm_client=llm_client,
            prompt_version=prompt_version,
            model_name=model_name,
            run_id=run_id,
            yield_facts=_latest_yield_facts(yield_curve_facts_frame),
        )
        bundle_map[bundle["bundle_id"]] = bundle
        event_bundles.append(bundle)
        event_cluster_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "event_id": cluster_id,
                "member_candidate_ids_json": _json_dumps(cluster_rows["candidate_id"].tolist()),
                "anchor_candidate_ids_json": _json_dumps(cluster_rows.sort_values("candidate_score", ascending=False).head(2)["candidate_id"].tolist()),
                "driver_symbols_json": _json_dumps(bundle.get("driver_symbols") or []),
                "beneficiary_symbols_json": _json_dumps(bundle.get("beneficiary_symbols") or []),
                "loser_symbols_json": _json_dumps(bundle.get("loser_symbols") or []),
                "event_facts_json": _json_dumps(
                    {
                        "members": [
                            {
                                "symbol": _normalize_symbol(row.get("symbol")),
                                "change_pct": _coerce_float(row.get("change_pct")),
                                "sector": _coerce_text(row.get("sector")),
                                "industry": _coerce_text(row.get("industry")),
                            }
                            for _, row in cluster_rows.iterrows()
                        ],
                        "yield_facts": bundle.get("yield_facts") or {},
                    }
                ),
                "supporting_claim_ids_json": _json_dumps(bundle.get("supporting_claim_ids") or []),
                "event_score": _coerce_float(bundle.get("event_score"), 0.0),
                "cause_status": _coerce_text(bundle.get("cause_status")),
                "event_type": _coerce_text(bundle.get("event_type")),
            }
        )

    macro_release_events_frame, macro_release_bundles = _build_macro_release_events(
        fred_summary_frame=fred_summary_frame,
        candidates=candidates,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    macro_causal_graph_edges_frame = _materialize_macro_causal_graph_edges(
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    macro_relationship_checks_frame = _build_macro_relationship_checks(
        macro_release_bundles=macro_release_bundles,
        macro_causal_graph_edges_frame=macro_causal_graph_edges_frame,
        candidates=candidates,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
    )
    attention_hypotheses_frame, hypothesis_summary_by_release = _build_attention_hypotheses_from_relationship_checks(
        macro_release_bundles=macro_release_bundles,
        relationship_checks_frame=macro_relationship_checks_frame,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    attention_hypotheses_frame, verification_summary_by_release, macro_verification_request_rows, macro_verification_result_rows, macro_claim_rows = _verify_macro_hypotheses_with_web_evidence(
        hypotheses_frame=attention_hypotheses_frame,
        macro_release_bundles=macro_release_bundles,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
        llm_client=llm_client,
        serp_client=serp_client,
        tavily_client=tavily_client,
    )
    hypothesis_summary_by_release = {
        **hypothesis_summary_by_release,
        **{
            release_id: {
                **dict(hypothesis_summary_by_release.get(release_id) or {}),
                **dict(summary or {}),
            }
            for release_id, summary in verification_summary_by_release.items()
        },
    }
    macro_release_bundles = _apply_macro_diagnostics_to_release_bundles(
        macro_release_bundles=macro_release_bundles,
        hypothesis_summary_by_release=hypothesis_summary_by_release,
    )
    for bundle in macro_release_bundles:
        bundle_id = _coerce_text(bundle.get("bundle_id"))
        if bundle_id:
            bundle_map[bundle_id] = bundle
    if macro_verification_request_rows:
        request_rows.extend(macro_verification_request_rows)
    if macro_verification_result_rows:
        result_rows.extend(macro_verification_result_rows)
    if macro_claim_rows:
        macro_claims_frame = _serialize_claims_frame(macro_claim_rows, asof_time_utc=asof_time_utc)
        if not macro_claims_frame.empty:
            claim_frames.append(macro_claims_frame)
    attention_macro_context_frame = _build_attention_macro_context_frame(
        candidates=candidates,
        attention_rows=attention_rows,
        macro_release_bundles=macro_release_bundles,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
    )

    home_payload = _build_home_payload(
        candidates,
        bundle_map,
        event_bundles,
        attention_rows=attention_rows,
        generated_at_utc=asof_time_utc,
        run_id=run_id,
        entity_master=entity_rows,
        top_events_limit=top_events_limit,
        must_read_limit=must_read_limit,
        unresolved_limit=unresolved_limit,
        macro_release_bundles=macro_release_bundles,
        macro_release_force_limit=macro_release_force_limit,
        relationship_checks_frame=macro_relationship_checks_frame,
        hypotheses_frame=attention_hypotheses_frame,
    )
    topology_universe = (
        topology_universe_frame.copy()
        if isinstance(topology_universe_frame, pd.DataFrame) and not topology_universe_frame.empty
        else entity_rows.copy()
    )
    cluster_frame = pd.DataFrame(event_cluster_rows)
    home_payload["homepage_graph"] = build_homepage_attention_graph_payload(
        candidates,
        graph,
        cluster_frame,
        universe_frame=topology_universe,
    )
    frames = {
        "attention_candidates_1d": candidates.reset_index(drop=True),
        "attention_research_plans": pd.DataFrame(plan_rows),
        "attention_search_requests": pd.DataFrame(request_rows),
        "attention_search_results": pd.DataFrame(result_rows),
        "attention_source_documents": pd.DataFrame(document_rows),
        "attention_evidence_chunks": pd.concat(chunk_frames, ignore_index=True, sort=False) if chunk_frames else pd.DataFrame(),
        "attention_claims": pd.concat(claim_frames, ignore_index=True, sort=False) if claim_frames else pd.DataFrame(),
        "attention_candidate_graph": graph.reset_index(drop=True) if isinstance(graph, pd.DataFrame) else pd.DataFrame(),
        "attention_event_clusters_1d": cluster_frame,
        "macro_release_events_1d": macro_release_events_frame,
        "attention_macro_context_1d": attention_macro_context_frame,
        "macro_causal_graph_edges_v1": macro_causal_graph_edges_frame,
        "macro_relationship_checks_1d": macro_relationship_checks_frame,
        "attention_hypotheses_1d": attention_hypotheses_frame,
        "attention_home_snapshots_1d": serialize_attention_home_payload(home_payload),
        "attention_bundle_snapshots": serialize_attention_research_bundles(bundle_map, generated_at_utc=asof_time_utc),
    }
    return AgenticAttentionArtifacts(home_payload=home_payload, bundle_map=bundle_map, frames=frames)


def build_bottom_up_attention_home(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    generated_at_utc: datetime | str | None = None,
    filings_frame: pd.DataFrame | None = None,
    fred_summary_frame: pd.DataFrame | None = None,
    yield_curve_facts_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    run_id: str | None = None,
    top_events_limit: int = 5,
    must_read_limit: int = 10,
    unresolved_limit: int = 5,
) -> dict[str, Any]:
    return build_bottom_up_attention_artifacts(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_master,
        holdings=holdings,
        generated_at_utc=generated_at_utc,
        filings_frame=filings_frame,
        fred_summary_frame=fred_summary_frame,
        yield_curve_facts_frame=yield_curve_facts_frame,
        llm_client=llm_client,
        embedding_client=embedding_client,
        run_id=run_id,
        top_events_limit=top_events_limit,
        must_read_limit=must_read_limit,
        unresolved_limit=unresolved_limit,
    ).home_payload


def build_bottom_up_attention_bundle(
    bundle_id: str,
    home_payload: dict[str, Any],
    *,
    bundle_snapshots_frame: pd.DataFrame | None = None,
    bundle_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_bundle_id = _coerce_text(bundle_id)
    if not normalized_bundle_id:
        return {}
    if isinstance(bundle_snapshots_frame, pd.DataFrame) and not bundle_snapshots_frame.empty and "bundle_id" in bundle_snapshots_frame.columns:
        scoped = bundle_snapshots_frame[bundle_snapshots_frame["bundle_id"].astype(str) == normalized_bundle_id].head(1)
        if not scoped.empty:
            payload_text = _coerce_text(scoped.iloc[0].get("payload_json"))
            if payload_text:
                try:
                    payload = json.loads(payload_text)
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    pass
    if isinstance(bundle_map, dict) and normalized_bundle_id in bundle_map:
        return dict(bundle_map.get(normalized_bundle_id) or {})

    def _normalized_stub(item: dict[str, Any], *, section: str) -> dict[str, Any]:
        payload = dict(item or {})
        if section == "top_events":
            payload.setdefault("bundle_type", "event")
            payload.setdefault("event_title", _coerce_text(payload.get("event_title")) or _coerce_text(payload.get("headline")))
            payload.setdefault("what_happened_text", _coerce_text(payload.get("what_happened_text")) or _coerce_text(payload.get("surface_what_changed_text")))
            payload.setdefault("why_happened_text", _coerce_text(payload.get("why_happened_text")) or _coerce_text(payload.get("surface_why_text")))
            payload.setdefault("affected_assets_summary_text", _coerce_text(payload.get("affected_assets_summary_text")) or _coerce_text(payload.get("surface_what_else_moved_text")))
        else:
            payload.setdefault("bundle_type", "symbol")
            payload.setdefault("headline", _coerce_text(payload.get("headline")) or _coerce_text(payload.get("event_title")))
            payload.setdefault("what_changed_text", _coerce_text(payload.get("what_changed_text")) or _coerce_text(payload.get("surface_what_changed_text")))
            payload.setdefault("why_now_text", _coerce_text(payload.get("why_now_text")) or _coerce_text(payload.get("surface_why_text")))
            payload.setdefault("what_else_moved_text", _coerce_text(payload.get("what_else_moved_text")) or _coerce_text(payload.get("surface_what_else_moved_text")))
        payload.setdefault("surface_summary_text", _coerce_text(payload.get("surface_summary_text")))
        payload.setdefault("cause_status", _coerce_text(payload.get("cause_status")) or _coerce_text(payload.get("surface_cause_status")) or "unresolved")
        payload.setdefault("confidence_label", _coerce_text(payload.get("confidence_label")) or _coerce_text(payload.get("surface_confidence_label")) or "Developing")
        payload.setdefault("evidence_quality", _coerce_text(payload.get("evidence_quality")) or _coerce_text(payload.get("surface_evidence_quality")))
        payload.setdefault("freshness_quality", _coerce_text(payload.get("freshness_quality")) or _coerce_text(payload.get("surface_freshness_quality")))
        payload.setdefault("source_summary", _coerce_text(payload.get("source_summary")) or _coerce_text(payload.get("surface_source_summary")))
        payload.setdefault("evidence", [])
        payload.setdefault("background_context", [])
        payload.setdefault("claims", [])
        return payload

    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        for item in list(home_payload.get(section) or []):
            if _coerce_text((item or {}).get("bundle_id")) == normalized_bundle_id:
                return _normalized_stub(dict(item or {}), section=section)
    return {}


__all__ = [
    "AgenticAttentionArtifacts",
    "build_bottom_up_attention_artifacts",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
]
