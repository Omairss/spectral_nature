"""
AQL writer — narrative bundle writing for symbols and events.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..llm import get_prompt, register_narrative_prompt
from ..llm_context import render_research_prompt
from ..llm_execution import latest_research_artifact
from .constants import (
    EVENT_WRITER_SCHEMA,
    EVENT_WRITER_SYSTEM_PROMPT,
    LLMClient,
    SYMBOL_WRITER_SCHEMA,
    YIELD_RELEVANT_TAGS,
)
from ._shared import _top_sources
from services.common.market_activity import (
    _coerce_float,
    _coerce_text,
    _move_direction,
    _normalize_symbol,
    _safe_list,
)
from .collector import _candidate_subject

SYMBOL_BUNDLE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Symbol Bundle (what_changed / why_happened / spillover)",
    file="services/aql/writer.py",
    group="AQL / Research",
    prompt=(
        "You write plain-language market research summaries. "
        "Use the supplied observations, claims and established business context to develop useful explanations. "
        "Favor high recall, indirect relationships and reasonable hypotheses. Keep the surface summary to at most two sentences. "
        "Distinguish reported facts, established context and your interpretation. Do not invent reported developments. "
        "Do not write generic market-activity titles like 'moves sharply today' or 'rises today'. "
        "Use the most specific company, event, or business-development title supported by the supplied evidence. "
        "Avoid ticker/percent market recaps across all text fields. "
        "Explain likely mechanisms, competing explanations and potential effects on prices, margins or demand. "
        "Do not use ticker/percent lists as the main why-today explanation. "
        "Use actual dates and keep older business context distinct from new reports. Incomplete evidence can still "
        "support useful hypotheses; describe the connection's strength without filling fields with generic caveats. "
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
    if llm_client is None:
        return empty
    system_prompt = get_prompt(SYMBOL_BUNDLE_SYSTEM_PROMPT)
    user_prompt = json.dumps(
        {
            "subject": _candidate_subject(candidate),
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "cause_status": cause_status,
            "instrument_context": candidate.get("instrument_context_json"),
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
                "research_value": "retain useful indirect connections and hypotheses; explain their strength and avoid missing-evidence filler",
            },
        },
        ensure_ascii=False,
        default=str,
    )
    prior = latest_research_artifact(access_scope="public_company_research",
        subject="company:" + _normalize_symbol(candidate.get("symbol")), task="ticker_business_model_research_dossier")
    user_prompt = render_research_prompt(evidence={"completed_research": prior}, request=json.loads(user_prompt), instructions=(
        "Use the completed investigation to explain the underlying business and relevant relationships. "
        "It is dated, fallible model analysis, not independent corroboration. Compare new claims against it, preserve useful "
        "connections and distinguish earlier observations from new developments. Do not call a fact unavailable merely "
        "because it is absent from the newly acquired snippets when the completed investigation contains it."))
    try:
        from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=user_prompt,
            instructions=system_prompt,
            schema_name="attention_symbol_writer",
            schema=SYMBOL_WRITER_SCHEMA,
            task="attention_symbol_writer",
            surface="aql.symbol_writer",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="research_grade",
            operation_metadata=None,
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
        title = _coerce_text(data.get("title")) or empty["title"]
        what_changed = _coerce_text(data.get("what_changed_text"))
        why_today = _coerce_text(data.get("why_today_text"))
        what_else = _coerce_text(data.get("what_else_moved_text"))
        background_text = _coerce_text(data.get("background_context_text"))
        surface_summary = _coerce_text(data.get("surface_summary"))
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
            "why_happened_text": "explain likely mechanisms and useful hypotheses using reports, observations and established business context",
            "affected_assets_summary_text": "one sentence on second-order spillover and breadth without repeating what_happened_text",
            "research_value": "retain useful indirect connections and hypotheses; express their strength without missing-evidence filler",
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
    if llm_client is None:
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
        from ..aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=user_prompt,
            instructions=get_prompt(EVENT_WRITER_SYSTEM_PROMPT),
            schema_name="attention_event_writer",
            schema=EVENT_WRITER_SCHEMA,
            task="attention_event_writer",
            surface="services.aql.writer",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="formatter_over_aql",
            operation_metadata=None,
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        data = engine_operation["payload"]
        title = _coerce_text(data.get("title")) or empty["title"]
        what_happened = _coerce_text(data.get("what_happened_text"))
        why_happened = _coerce_text(data.get("why_happened_text"))
        affected = _coerce_text(data.get("affected_assets_summary_text"))
        background_text = _coerce_text(data.get("background_context_text"))
        surface_summary = _coerce_text(data.get("surface_summary"))
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
