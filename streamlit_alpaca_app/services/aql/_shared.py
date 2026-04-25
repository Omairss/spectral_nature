"""
AQL shared utilities — pure helper functions with no dependencies on other aql sub-modules.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from compute.signal_extraction import _history_correlation_map, _return_series_from_bars
from ..runtime_policy import source_authority_policy
from .constants import (
    CAUSAL_LANGUAGE_PATTERNS,
    GENERIC_GRAPH_BUCKETS,
    IRRELEVANT_NEWS_PATTERNS,
    LOW_SIGNAL_CLAIM_MARKERS,
    LOW_SIGNAL_PHRASES,
    RATE_TRANSMISSION_PATTERNS,
    RESEARCH_PROVIDER_ERROR_MARKERS,
    YIELD_RELEVANT_TAGS,
)


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


def _is_low_signal_claim_text(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    if _is_provider_error_text(clean):
        return True
    if _is_irrelevant_news_text(clean):
        return True
    return any(marker in clean for marker in LOW_SIGNAL_CLAIM_MARKERS)


def _looks_like_generic_market_activity_title(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    patterns = (
        r"\bmoves? sharply today\b",
        r"\bmoved sharply today\b",
        r"\brises on today'?s market activity\b",
        r"\bfalls on today'?s market activity\b",
        r"\bon today'?s market activity\b",
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


def _what_changed_fallback(candidate: dict[str, Any]) -> str:
    symbol = _normalize_symbol(candidate.get("symbol"))
    move = _coerce_float(candidate.get("change_pct"), 0.0)
    direction = _move_direction(move)
    if direction == "up":
        return f"{symbol} moved higher today."
    if direction == "down":
        return f"{symbol} moved lower today."
    return f"{symbol} was little changed today."


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


def _sign_from_word(value: object) -> int:
    normalized = _coerce_text(value).lower()
    if normalized in {"positive", "up", "+", "1", "plus"}:
        return 1
    if normalized in {"negative", "down", "-", "-1", "minus"}:
        return -1
    return 0


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
