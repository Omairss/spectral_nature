"""
AQL writer — narrative bundle writing for symbols and events.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..llm import NARRATIVE_STYLE_RULE, get_prompt, register_narrative_prompt
from .constants import (
    EVENT_WRITER_SCHEMA,
    EVENT_WRITER_SYSTEM_PROMPT,
    LLMClient,
    SYMBOL_WRITER_SCHEMA,
    YIELD_RELEVANT_TAGS,
)
from ._shared import (
    _best_why_claim_sentence,
    _coerce_float,
    _coerce_text,
    _compose_surface_summary,
    _has_causal_language,
    _is_yield_only_explanation,
    _json_dumps,
    _looks_like_generic_market_activity_title,
    _looks_like_stat_dump,
    _move_direction,
    _normalize_symbol,
    _safe_list,
    _tag_tokens,
    _text_overlap,
    _top_sources,
)
from .extractor import _claim_entities
from .collector import _candidate_subject

SYMBOL_BUNDLE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Symbol Bundle (what_changed / why_happened / spillover)",
    file="services/aql/writer.py",
    group="AQL / Research",
    prompt=(
        f"{NARRATIVE_STYLE_RULE} "
        "You write plain-language market research summaries. "
        "Use only the supplied claims and facts. Keep the surface summary to at most two sentences. "
        "Do not invent causes. "
        "Do not write generic market-activity titles like 'moves sharply today' or 'rises today'. "
        "Use the most specific company, event, or business-development title supported by the supplied evidence. "
        "Avoid ticker/percent market recaps across all text fields. "
        "Explain mechanism: state why the move happened and how that transmits to prices, margins, demand, or risk appetite. "
        "Do not use ticker/percent lists as the main why-today explanation. "
        "Only same-day or very recent claims may support why-today text; older/background claims belong in background context. "
        "Do not narrate missing evidence. If no mechanism is supported, leave the why field empty "
        "instead of narrating missing evidence. "
        "What-else-moved text should describe spillover and avoid repeating what-changed text verbatim. "
        "When Treasury yield context is relevant, summarize direction and transmission in plain language without quoting bp numbers."
    ),
)

def _empty_symbol_writer(
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    cause_status: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, str]:
    subject = _candidate_subject(candidate) or _normalize_symbol(candidate.get("symbol"))
    title = _coerce_text(candidate.get("headline")) or subject or _normalize_symbol(candidate.get("symbol"))
    return {
        "title": title,
        "surface_summary": "",
        "what_changed_text": "",
        "why_today_text": "",
        "what_else_moved_text": "",
        "background_context_text": "",
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
    empty = _empty_symbol_writer(candidate, claims, peer_moves, cause_status, yield_facts=yield_facts)
    if llm_client is None or (not claims and not yield_facts):
        return empty
    system_prompt = get_prompt(SYMBOL_BUNDLE_SYSTEM_PROMPT)
    user_prompt = json.dumps(
        {
            "subject": _candidate_subject(candidate),
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "cause_status": cause_status,
            "observed_candidate": {
                "headline": _coerce_text(candidate.get("headline")),
                "what_changed_text": _coerce_text(candidate.get("what_changed_text")),
                "change_pct": _coerce_float(candidate.get("change_pct"), 0.0),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
            },
            "claims": [
                {
                    "claim_text": item.get("claim_text"),
                    "claim_type": item.get("claim_type"),
                    "is_same_day": item.get("is_same_day"),
                    "freshness_class": item.get("freshness_class"),
                    "source": item.get("source"),
                }
                for item in claims[:6]
            ],
            "yield_facts": yield_facts or {},
            "peer_moves": peer_moves[:4],
            "narrative_requirements": {
                "why_today_text": "causal chain first, numbers second; avoid pure move recaps",
                "what_else_moved_text": "spillover pattern, not a duplicated ticker list",
                "absence_language": "do not write no-clear/no-catalyst/no-company-news filler; leave unsupported why fields empty",
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
        title = _coerce_text(data.get("title")) or empty["title"]
        if _looks_like_generic_market_activity_title(title):
            title = empty["title"]
        what_changed = _coerce_text(data.get("what_changed_text"))
        why_today = _coerce_text(data.get("why_today_text"))
        what_else = _coerce_text(data.get("what_else_moved_text"))
        background_text = _coerce_text(data.get("background_context_text"))
        if _looks_like_stat_dump(what_changed):
            what_changed = ""
        if _looks_like_stat_dump(why_today) and not _has_causal_language(why_today):
            why_today = ""
        if _looks_like_stat_dump(what_else):
            what_else = ""
        if _text_overlap(what_changed, what_else) >= 0.62:
            what_else = ""
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
        return empty


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
    import re
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
    baseline: dict[str, str],
    yield_facts: dict[str, Any] | None = None,
    event_type: str = "",
    cause_status: str = "",
    evidence_quality: str = "",
    freshness_quality: str = "",
) -> dict[str, Any]:
    from ._shared import _dominant_cluster_label
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
        "baseline": baseline,
        "narrative_requirements": {
            "title": "specific supported event theme, not a generic market-activity or cluster recap",
            "what_happened_text": "one or two sentences on the directional relationship across the cluster; summarize the split, do not enumerate market activity",
            "why_happened_text": "lead with the supported trigger or business development -> transmission channel -> market pricing reaction; if evidence is mixed, state the uncertainty explicitly",
            "affected_assets_summary_text": "one sentence on second-order spillover and breadth without repeating what_happened_text",
            "absence_language": "do not write no-clear/no-catalyst/no-company-news filler; leave unsupported why fields empty",
        },
    }


def _empty_event_writer(
    event_title_seed: str,
    cluster_rows: pd.DataFrame,
    retained_claims: list[dict[str, Any]],
    yield_facts: dict[str, Any] | None = None,
    event_type: str = "",
) -> dict[str, str]:
    from ._shared import _dominant_cluster_label
    ranked = cluster_rows.copy()
    cluster_label = _dominant_cluster_label(ranked)
    return {
        "title": event_title_seed or cluster_label or "Market event",
        "surface_summary": "",
        "what_happened_text": "",
        "why_happened_text": "",
        "affected_assets_summary_text": "",
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
    empty = _empty_event_writer(
        event_title_seed,
        cluster_rows,
        retained_claims,
        yield_facts=yield_facts,
        event_type=event_type,
    )
    if llm_client is None or (not retained_claims and not yield_facts):
        return empty
    user_prompt = json.dumps(
        _build_event_writer_payload(
            event_title_seed,
            cluster_rows,
            retained_claims,
            baseline=empty,
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
            system_prompt=get_prompt(EVENT_WRITER_SYSTEM_PROMPT),
            user_prompt=user_prompt,
            schema_name="attention_event_writer",
            schema=EVENT_WRITER_SCHEMA,
        )
        title = _coerce_text(data.get("title")) or empty["title"]
        if _looks_like_generic_market_activity_title(title):
            title = empty["title"]
        what_happened = _coerce_text(data.get("what_happened_text"))
        why_happened = _coerce_text(data.get("why_happened_text"))
        affected = _coerce_text(data.get("affected_assets_summary_text"))
        background_text = _coerce_text(data.get("background_context_text"))
        if _looks_like_stat_dump(what_happened):
            what_happened = ""
        if _looks_like_stat_dump(why_happened) and not _has_causal_language(why_happened):
            why_happened = ""
        if event_type != "rates" and not _has_causal_language(why_happened):
            why_happened = ""
        if event_type != "rates" and _is_yield_only_explanation(why_happened):
            why_happened = ""
        if event_type != "rates" and (_looks_like_stat_dump(why_happened) or _is_yield_only_explanation(why_happened)):
            claim_seed = _best_why_claim_sentence(retained_claims)
            if (
                claim_seed
                and (_has_causal_language(claim_seed) or event_type == "rates")
                and not (_looks_like_stat_dump(claim_seed) and not _has_causal_language(claim_seed))
            ):
                why_happened = claim_seed
            else:
                why_happened = ""
        if event_type != "rates" and why_happened and not _has_causal_language(why_happened):
            why_happened = ""
        if _looks_like_stat_dump(affected):
            affected = ""
        if _text_overlap(what_happened, affected) >= 0.62:
            affected = ""
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
        return empty
