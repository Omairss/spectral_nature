"""Shared market activity helpers.

These helpers are used by Attention and AQL. They live outside either module so
neither layer has to import the other's private helpers.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .contracts import CAUSAL_LANGUAGE_PATTERNS, GENERIC_GRAPH_BUCKETS, RATE_TRANSMISSION_PATTERNS


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


def _coerce_float(value: object, default: float = math.nan) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if np.isfinite(number) else default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


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
    if re.search(
        r"\b(shares?|stock)\s+(rose|fell|gained|dropped|surged|slid|jumped|plunged)\s+[+\-]?\d+(?:\.\d+)?%\s+(?:on\s+)?(today|monday|tuesday|wednesday|thursday|friday)\b",
        lowered,
    ):
        return True
    if (
        re.search(
            r"\b(shares?|stock)\s+(rose|fell|gained|dropped|surged|slid|jumped|plunged)\s+[+\-]?\d+(?:\.\d+)?%\b",
            lowered,
        )
        and not _has_causal_language(clean)
    ):
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


def _looks_like_generic_market_activity_title(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    patterns = (
        r"\bmoves? sharply today\b",
        r"\bmoved sharply today\b",
        r"\bshares?\s+(rose|fell|gained|dropped|surged|slid|jumped|plunged)\s+[+\-]?\d+(?:\.\d+)?%\b",
        r"\brises on today'?s market activity\b",
        r"\bfalls on today'?s market activity\b",
        r"\bon today'?s market activity\b",
        r"\bmarket move today\b",
    )
    return any(re.search(pattern, clean) for pattern in patterns)


def _merge_text_values(*values: object) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in _safe_list(value):
            text = _coerce_text(item)
            if text:
                merged.append(text)
    return merged


def _candidate_text_value(candidate: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _coerce_text(candidate.get(key))
        if text:
            return text
    return ""


def _tag_tokens(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in re.split(r"[,|;/]", _coerce_text(value)):
            clean = re.sub(r"[^a-z0-9_ -]+", " ", token.lower()).strip()
            if clean:
                tokens.add(clean)
    return tokens


def _is_generic_graph_bucket(value: object) -> bool:
    text = _coerce_text(value).lower().strip()
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


def _claim_authority_rank(item: dict[str, Any]) -> int:
    rank = _coerce_float(item.get("authority_rank"), math.nan)
    if math.isfinite(rank):
        return int(rank)
    bucket = _coerce_text(item.get("source_authority_bucket") or item.get("authority_bucket")).lower()
    return {"official": 0, "wire": 1, "press": 2, "web": 3}.get(bucket, 4)


def _quality_label(claims: list[dict[str, Any]], cause_status: str) -> tuple[str, str]:
    same_day = [item for item in claims if bool(item.get("is_same_day"))]
    strong = [
        item
        for item in same_day
        if float(item.get("causal_score") or 0.0) >= 0.62 and float(item.get("relevance_score") or 0.0) >= 0.6
    ]
    authoritative_same_day = [
        item
        for item in same_day
        if _claim_authority_rank(item) <= 2 and float(item.get("relevance_score") or 0.0) >= 0.55
    ]
    if cause_status == "supported" and len(strong) >= 2:
        evidence_quality = "High"
    elif cause_status == "supported" and (strong or authoritative_same_day):
        evidence_quality = "Medium"
    elif claims and min((_claim_authority_rank(item) for item in claims), default=4) <= 2:
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
        return "continuation", "background_context"
    return "unresolved", "unresolved"
