from __future__ import annotations

import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
import time
from typing import Any, Callable

import pandas as pd

from .aql_zopedia_gateway import generate_json_via_aql_zopedia_gateway
from .json_utils import to_jsonable, to_list
from .llm import (
    LLMAPIError,
    NARRATIVE_STYLE_RULE,
    get_prompt,
    register_narrative_prompt,
)


_TRADING_AGENT_SYSTEM_PROMPT = register_narrative_prompt(
    name="Trading Agent Experiment",
    file="services/trading_agent.py",
    group="Trading Agent",
    prompt=(
        f"You are the Spectral Nature market research watchlist experiment. {NARRATIVE_STYLE_RULE} "
        "Use only the supplied summaries and evidence. Do not assume access to unseen quotes or news. "
        "Follow this process: broad economic wind, momentum, hypothesis, validation, tail risk, watchlist candidate. "
        "Return research watchlist suggestions for human review. "
        "Evaluate upside/watch setups and downside/risk-control setups. Include downside or avoid candidates only when the supplied evidence supports fading momentum or risk-control logic. "
        "Every candidate needs an invalidation trigger and at least one tail risk."
    ),
)

_TRADING_AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "regime_read": {"type": "string"},
        "portfolio_posture": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ticker": {"type": "string"},
                    "direction": {"type": "string", "enum": ["long", "short", "watch", "avoid"]},
                    "setup": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "invalidation": {"type": "string"},
                    "tail_risks": {"type": "array", "items": {"type": "string"}},
                    "suggested_horizon": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": [
                    "ticker",
                    "direction",
                    "setup",
                    "hypothesis",
                    "evidence",
                    "invalidation",
                    "tail_risks",
                    "suggested_horizon",
                    "confidence",
                ],
            },
        },
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["regime_read", "portfolio_posture", "candidates", "data_gaps"],
}

AgentRunner = Callable[..., dict[str, Any]]

TRADING_AGENT_HORIZON_SPECS: tuple[dict[str, str], ...] = (
    {"key": "1w", "column": "return_7d_pct", "label": "1 Week"},
    {"key": "1m", "column": "return_1m_pct", "label": "1 Month"},
    {"key": "3m", "column": "return_3m_pct", "label": "3 Month"},
    {"key": "1y", "column": "return_1y_pct", "label": "1 Year"},
    {"key": "5y", "column": "return_5y_pct", "label": "5 Year"},
)

TRADING_AGENT_RUN_COLUMNS = [
    "trading_agent_run_id",
    "run_id",
    "horizon_key",
    "horizon_label",
    "selected_horizon_col",
    "business_filter",
    "status",
    "regime_read",
    "portfolio_posture",
    "candidate_count",
    "data_gaps_json",
    "error",
    "aql_agent_json",
    "context_json",
    "result_json",
    "asof_time_utc",
    "generated_at_utc",
]

TRADING_AGENT_CANDIDATE_COLUMNS = [
    "candidate_id",
    "trading_agent_run_id",
    "run_id",
    "horizon_key",
    "horizon_label",
    "selected_horizon_col",
    "business_filter",
    "rank",
    "ticker",
    "direction",
    "setup",
    "hypothesis",
    "evidence_json",
    "invalidation",
    "tail_risks_json",
    "suggested_horizon",
    "confidence",
    "decision_status",
    "candidate_json",
    "context_json",
    "asof_time_utc",
    "generated_at_utc",
]

TRADING_AGENT_OUTCOME_COLUMNS = [
    "outcome_id",
    "candidate_id",
    "trading_agent_run_id",
    "run_id",
    "horizon_key",
    "horizon_label",
    "selected_horizon_col",
    "business_filter",
    "rank",
    "ticker",
    "direction",
    "scoring_direction",
    "setup",
    "hypothesis",
    "confidence",
    "suggested_horizon",
    "evidence_json",
    "invalidation",
    "tail_risks_json",
    "candidate_generated_at_utc",
    "candidate_asof_time_utc",
    "entry_ts",
    "entry_close",
    "target_exit_ts",
    "actual_exit_ts",
    "exit_close",
    "raw_return_pct",
    "signed_return_pct",
    "benchmark_return_pct",
    "excess_signed_return_pct",
    "outcome_status",
    "is_win",
    "is_matured",
    "price_bar_count",
    "source_price_min_ts",
    "source_price_max_ts",
    "candidate_json",
    "context_json",
    "evaluation_generated_at_utc",
]

TRADING_AGENT_RESEARCH_REVIEW_COLUMNS = [
    "review_id",
    "outcome_id",
    "candidate_id",
    "trading_agent_run_id",
    "run_id",
    "horizon_key",
    "ticker",
    "direction",
    "review_status",
    "outcome_status",
    "is_win",
    "is_matured",
    "signed_return_pct",
    "raw_return_pct",
    "review_generated_at_utc",
    "outcome_summary",
    "thesis_verdict",
    "direction_verdict",
    "horizon_verdict",
    "evidence_verdict",
    "primary_failure_mode",
    "primary_success_factor",
    "missing_evidence_slots_json",
    "recommended_contract_change",
    "recommended_prompt_change",
    "recommended_retrieval_change",
    "recommended_candidate_selection_change",
    "source_refs_json",
    "review_input_json",
    "review_json",
    "error",
]

_TRADING_AGENT_HORIZON_DAYS = {
    "1w": 7,
    "1m": 30,
    "3m": 91,
    "1y": 365,
    "5y": 1825,
}

_TRADING_AGENT_DIRECTION_MULTIPLIER = {
    "long": 1.0,
    "watch": 1.0,
    "short": -1.0,
    "avoid": -1.0,
}

_TRADING_AGENT_REVIEW_SYSTEM_PROMPT = register_narrative_prompt(
    name="Trading Agent Research Review",
    file="services/trading_agent.py",
    group="Trading Agent",
    prompt=(
        f"You are reviewing a completed or interim Spectral Nature Trading Agent research call. {NARRATIVE_STYLE_RULE} "
        "Use only the supplied candidate, evidence, and measured outcome. Do not make a new trading recommendation. "
        "Judge whether the stated thesis, evidence, direction, and horizon were supported. "
        "Name concrete research contract, prompt, retrieval, or candidate-selection improvements without mutating memory, prompts, rankings, or broker behavior."
    ),
)

_TRADING_AGENT_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "review_status": {
            "type": "string",
            "enum": ["completed", "insufficient_context"],
        },
        "outcome_summary": {"type": "string"},
        "thesis_verdict": {
            "type": "string",
            "enum": [
                "validated",
                "contradicted",
                "unrelated_move",
                "too_early",
                "insufficient_evidence",
            ],
        },
        "direction_verdict": {
            "type": "string",
            "enum": ["right_direction", "wrong_direction", "direction_unproven"],
        },
        "horizon_verdict": {
            "type": "string",
            "enum": ["too_short", "too_long", "appropriate", "unproven"],
        },
        "evidence_verdict": {
            "type": "string",
            "enum": ["strong", "mixed", "weak", "stale", "missing_key_slot"],
        },
        "primary_failure_mode": {"type": "string"},
        "primary_success_factor": {"type": "string"},
        "missing_evidence_slots": {"type": "array", "items": {"type": "string"}},
        "recommended_contract_change": {"type": "string"},
        "recommended_prompt_change": {"type": "string"},
        "recommended_retrieval_change": {"type": "string"},
        "recommended_candidate_selection_change": {"type": "string"},
        "source_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "review_status",
        "outcome_summary",
        "thesis_verdict",
        "direction_verdict",
        "horizon_verdict",
        "evidence_verdict",
        "primary_failure_mode",
        "primary_success_factor",
        "missing_evidence_slots",
        "recommended_contract_change",
        "recommended_prompt_change",
        "recommended_retrieval_change",
        "recommended_candidate_selection_change",
        "source_refs",
    ],
}


def _trading_agent_write_policy(default: str = "safe_auto") -> str:
    fallback = str(default or "safe_auto").strip().lower()
    policy = str(os.getenv("TRADING_AGENT_ZOPEDIA_WRITE_POLICY") or fallback).strip().lower()
    if policy in {"off", "disabled", "read_only", "readonly"}:
        return "none"
    if policy in {"proposal", "review"}:
        return "propose"
    if policy in {"auto", "commit", "safe", "safe-auto"}:
        return "safe_auto"
    if policy in {"none", "propose", "safe_auto"}:
        return policy
    return fallback if fallback in {"none", "propose", "safe_auto"} else "safe_auto"


def _clean(text: object) -> str:
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except Exception:
        pass
    out = " ".join(str(text).split()).strip()
    return "" if out.lower() == "nan" else out


def _text_key(text: object) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in _clean(text)).split()
    )


def _append_distinct_text(items: list[str], text: object, *, similarity: float = 0.88) -> None:
    clean = _clean(text)
    key = _text_key(clean)
    if not key:
        return
    for existing in items:
        existing_key = _text_key(existing)
        if not existing_key:
            continue
        if key == existing_key or key in existing_key or existing_key in key:
            return
        if SequenceMatcher(None, key, existing_key).ratio() >= similarity:
            return
    items.append(clean)


def _compact_json(value: Any, *, limit: int = 18000) -> str:
    try:
        text = json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _json_text(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True, default=str)


def _utc_iso(value: object | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return datetime.now(timezone.utc).isoformat()
    return parsed.isoformat()


def _stable_digest(value: Any, *, length: int = 20) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()[: max(int(length), 8)]


def _utc_timestamp(value: object) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def _maybe_iso(value: object) -> str:
    parsed = _utc_timestamp(value)
    return "" if parsed is None else parsed.isoformat()


def _json_array_from_text(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    text = _clean(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object_from_text(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = _clean(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _records(frame: pd.DataFrame, *, columns: list[str], limit: int) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    keep = [column for column in columns if column in frame.columns]
    if not keep:
        return []
    return to_jsonable(frame[keep].head(max(int(limit), 1)).to_dict("records"))


def _latest_summary_from_frame(
    frame: pd.DataFrame,
    *,
    surface: str,
    ticker: str = "",
) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "summary_json" not in frame.columns:
        return {}
    rows = frame.copy()
    if "surface" in rows.columns:
        rows = rows[rows["surface"].astype(str).str.strip().eq(str(surface or "").strip())].copy()
    if ticker:
        if "ticker" not in rows.columns:
            return {}
        target = str(ticker or "").upper().strip()
        ticker_rows = rows[rows["ticker"].astype(str).str.upper().str.strip().eq(target)].copy()
        if ticker_rows.empty:
            return {}
        rows = ticker_rows
    if rows.empty:
        return {}
    if "generated_at_utc" in rows.columns:
        rows = rows.assign(
            _generated_at=pd.to_datetime(rows["generated_at_utc"], utc=True, errors="coerce")
        ).sort_values("_generated_at", ascending=False, na_position="last")
    row = rows.iloc[0]
    try:
        payload = json.loads(_clean(row.get("summary_json")) or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    payload.setdefault("surface", _clean(row.get("surface")) or surface)
    payload.setdefault("headline", _clean(row.get("headline")))
    payload.setdefault("confidence", _clean(row.get("confidence")))
    payload["materialized"] = {
        "generated_at_utc": _clean(row.get("generated_at_utc")),
        "run_id": _clean(row.get("run_id")),
        "context_signature": _clean(row.get("context_signature")),
    }
    return payload


def _summary_payload_for_context(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict) or not summary:
        return {}
    return {
        "status": _clean(summary.get("status")),
        "headline": _clean(summary.get("headline")),
        "summary_markdown": _clean(summary.get("summary_markdown")),
        "watch_items": [_clean(item) for item in to_list(summary.get("watch_items")) if _clean(item)][:5],
        "data_gaps": [_clean(item) for item in to_list(summary.get("data_gaps")) if _clean(item)][:5],
        "confidence": _clean(summary.get("confidence")),
        "materialized": summary.get("materialized") if isinstance(summary.get("materialized"), dict) else {},
    }


def _feed_record_lookup(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        symbol = _clean(item.get("symbol") or item.get("ticker")).upper()
        if symbol and symbol not in lookup:
            lookup[symbol] = item
    return lookup


def _ticker_contexts_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    max_candidates = max(int(controls.get("max_candidates") or 4), 1)
    feed = [item for item in to_list((context or {}).get("market_opportunity_feed")) if isinstance(item, dict)]
    feed_lookup = _feed_record_lookup(feed)

    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(symbol_value: object, *, base: dict[str, Any] | None = None) -> None:
        symbol = _clean(symbol_value).upper()
        if not symbol or symbol in seen:
            return
        feed_record = feed_lookup.get(symbol, {})
        merged = {
            "ticker": symbol,
            "company_name": _clean((base or {}).get("company_name") or feed_record.get("company_name")),
            "market_opportunity": feed_record,
            "stock_summary": {},
            "attention_context": {},
            "news_summary_lines": [],
        }
        if isinstance(base, dict):
            stock_summary = base.get("stock_summary")
            if isinstance(stock_summary, dict):
                merged["stock_summary"] = stock_summary
            attention_context = base.get("attention_context")
            if isinstance(attention_context, dict):
                merged["attention_context"] = attention_context
            merged["news_summary_lines"] = [
                _clean(item)
                for item in to_list(base.get("news_summary_lines"))
                if _clean(item)
            ][:5]
            if not merged["company_name"]:
                merged["company_name"] = _clean(base.get("security_name") or base.get("name"))
        seen.add(symbol)
        contexts.append(merged)

    for item in to_list((context or {}).get("ticker_evidence")):
        if isinstance(item, dict):
            add(item.get("ticker") or item.get("symbol"), base=item)
            if len(contexts) >= max_candidates:
                break
    if len(contexts) < max_candidates:
        for item in feed:
            add(item.get("symbol") or item.get("ticker"), base=item)
            if len(contexts) >= max_candidates:
                break
    return contexts[:max_candidates]


def _candidate_id(
    *,
    run_id: str,
    horizon_key: str,
    ticker: str,
    rank: int,
    candidate: dict[str, Any],
) -> str:
    payload = {
        "run_id": _clean(run_id),
        "horizon_key": _clean(horizon_key),
        "ticker": _clean(ticker).upper(),
        "rank": int(rank),
        "setup": _clean((candidate or {}).get("setup")),
        "hypothesis": _clean((candidate or {}).get("hypothesis")),
    }
    digest = hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()[:20]
    return f"tag_{digest}"


def _format_metric(value: object, *, suffix: str = "") -> str:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return ""
    return f"{float(parsed):.2f}{suffix}"


def _fallback_direction(value: object) -> str:
    raw = _clean(value).lower()
    if raw in {"long", "short", "avoid", "watch"}:
        return raw
    if any(fragment in raw for fragment in ["avoid", "fade", "risk"]):
        return "avoid"
    if any(fragment in raw for fragment in ["short", "downside", "bear"]):
        return "short"
    return "watch"


def _fallback_trading_agent_suggestions(
    context: dict[str, Any],
    *,
    reason: str,
    aql_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build conservative candidates from materialized inputs when AQL times out."""
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    horizon_label = _clean(controls.get("selected_horizon_label") or controls.get("momentum_horizon")) or "Selected horizon"
    horizon_col = _clean(controls.get("selected_horizon_col"))
    max_candidates = max(int(controls.get("max_candidates") or 4), 1)
    feed = [item for item in to_list((context or {}).get("market_opportunity_feed")) if isinstance(item, dict)]
    ticker_evidence = {
        _clean(item.get("ticker")).upper(): item
        for item in to_list((context or {}).get("ticker_evidence"))
        if isinstance(item, dict) and _clean(item.get("ticker"))
    }

    candidates: list[dict[str, Any]] = []
    for item in feed[:max_candidates]:
        ticker = _clean(item.get("symbol") or item.get("ticker")).upper()
        if not ticker:
            continue
        opportunity = _clean(item.get("opportunity")) or "Materialized opportunity watch"
        details = _clean(item.get("details"))
        score = _format_metric(item.get("opportunity_score"))
        horizon_return = _format_metric(item.get(horizon_col), suffix="%") if horizon_col else ""
        daily_change = _format_metric(item.get("daily_change_pct"), suffix="%")
        evidence = []
        if score:
            evidence.append(f"Materialized opportunity score: {score}.")
        if horizon_return:
            evidence.append(f"{horizon_label} return from the opportunity feed: {horizon_return}.")
        if daily_change:
            evidence.append(f"Latest daily change from the opportunity feed: {daily_change}.")
        if details:
            evidence.append(details)
        stock_payload = ticker_evidence.get(ticker, {}).get("stock_summary")
        if isinstance(stock_payload, dict):
            headline = _clean(stock_payload.get("headline"))
            if headline:
                evidence.append(f"Materialized stock summary: {headline}.")
            evidence.extend(
                _clean(value)
                for value in to_list(stock_payload.get("watch_items"))
                if _clean(value)
            )
        if not evidence:
            evidence.append("Ticker appeared in the latest materialized market opportunity feed.")

        candidates.append(
            {
                "ticker": ticker,
                "direction": _fallback_direction(item.get("direction")),
                "setup": opportunity,
                "hypothesis": (
                    f"{ticker} remains a {horizon_label} research watch candidate based on the "
                    "materialized opportunity feed. Re-run AQL validation before treating this as actionable."
                ),
                "evidence": evidence[:5],
                "invalidation": (
                    "Invalidate if the next materialized refresh removes the ticker from the opportunity feed "
                    "or the selected-horizon momentum reverses."
                ),
                "tail_risks": [
                    "AQL validation was unavailable for this horizon.",
                    "Fresh news or price action may have changed after the materialized snapshot.",
                ],
                "suggested_horizon": horizon_label,
                "confidence": "low",
            }
        )

    data_gaps = [
        _clean(reason),
        "Fallback candidates use materialized opportunity data only; AQL evidence validation did not complete.",
    ]
    return {
        "status": "fallback",
        "regime_read": "Fallback read from the materialized Market Opportunity feed.",
        "portfolio_posture": "Research-only, log-only review. Validate AQL evidence before any broker workflow.",
        "candidates": candidates,
        "data_gaps": [item for item in data_gaps if item][:6],
        "error": _clean(reason),
        "aql_agent": aql_context or {},
    }


def empty_trading_agent_run_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_RUN_COLUMNS)


def empty_trading_agent_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_CANDIDATE_COLUMNS)


def empty_trading_agent_outcome_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_OUTCOME_COLUMNS)


def empty_trading_agent_research_review_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_RESEARCH_REVIEW_COLUMNS)


def build_trading_agent_context(
    *,
    opportunity_feed: pd.DataFrame,
    page_summaries: pd.DataFrame,
    horizon_key: str,
    horizon_col: str,
    horizon_label: str,
    business_filter: str = "All Market",
    max_candidates: int = 4,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Build one horizon's Trading Agent context from materialized datasets."""
    from .market_opportunity import select_market_opportunity_feed

    selected_feed = select_market_opportunity_feed(
        opportunity_feed if isinstance(opportunity_feed, pd.DataFrame) else pd.DataFrame(),
        business_filter=business_filter,
        selected_horizon_col=horizon_col,
        limit=max(int(max_candidates), 1) * 4,
    )
    market_summary = _latest_summary_from_frame(page_summaries, surface="Market Explorer")
    broad_summary = _latest_summary_from_frame(page_summaries, surface="Broad Economy")

    symbols = []
    if isinstance(selected_feed, pd.DataFrame) and not selected_feed.empty and "symbol" in selected_feed.columns:
        symbols = [
            _clean(symbol).upper()
            for symbol in selected_feed["symbol"].astype(str).tolist()
            if _clean(symbol)
        ][: max(int(max_candidates), 1)]
    feed_context_records = _records(
        selected_feed,
        columns=[
            "symbol",
            "company_name",
            "opportunity",
            "direction",
            "opportunity_score",
            "sparkline_3m",
            "daily_change_pct",
            horizon_col,
            "selected_horizon_col",
            "selected_horizon_label",
            "asof_time_utc",
            "run_id",
            "momentum_roc_score",
            "trend_fit_gap",
            "details",
        ],
        limit=max(int(max_candidates), 1) * 2,
    )
    feed_lookup = _feed_record_lookup(feed_context_records)

    stock_summaries: list[dict[str, Any]] = []
    ticker_evidence: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_summary = _latest_summary_from_frame(page_summaries, surface="Stock Investigator", ticker=symbol)
        stock_payload = _summary_payload_for_context(stock_summary)
        market_opportunity = feed_lookup.get(symbol, {})
        if stock_payload:
            stock_payload["ticker"] = symbol
            stock_summaries.append(stock_payload)
        ticker_evidence.append(
            {
                "ticker": symbol,
                "company_name": _clean(market_opportunity.get("company_name")),
                "market_opportunity": market_opportunity,
                "stock_summary": stock_payload,
                "attention_context": {},
                "news_summary_lines": to_list(stock_payload.get("watch_items")) if stock_payload else [],
            }
        )

    context = {
        "philosophy": [
            "observe broad economic patterns",
            "observe momentum",
            "build hypothesis",
            "validate hypothesis",
            "identify tail risk",
            "research watchlist candidate",
        ],
        "controls": {
            "business_filter": _clean(business_filter) or "All Market",
            "momentum_horizon": _clean(horizon_label),
            "horizon_key": _clean(horizon_key),
            "selected_horizon_col": _clean(horizon_col),
            "selected_horizon_label": _clean(horizon_label),
            "max_candidates": max(int(max_candidates), 1),
            "include_macro": True,
        },
        "source_summaries": {
            "market_explorer": _summary_payload_for_context(market_summary),
            "broad_economy": _summary_payload_for_context(broad_summary),
            "stock_investigator": stock_summaries,
        },
        "market_opportunity_feed": feed_context_records,
        "ticker_evidence": ticker_evidence,
    }
    return context, selected_feed


def build_trading_agent_materialized_frames(
    *,
    opportunity_feed: pd.DataFrame,
    page_summaries: pd.DataFrame,
    llm_client: Any | None,
    run_id: str,
    asof_time_utc: object,
    business_filter: str = "All Market",
    max_candidates: int = 4,
    horizon_specs: tuple[dict[str, str], ...] = TRADING_AGENT_HORIZON_SPECS,
    suggestion_builder: Callable[..., dict[str, Any]] | None = None,
    aql_agent_runner: AgentRunner | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the Trading Agent for every fixed horizon and return persisted frames."""
    run_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    builder = suggestion_builder or build_trading_agent_suggestions
    clean_run_id = _clean(run_id) or f"trading_agent_{hashlib.sha256(_utc_iso(asof_time_utc).encode('utf-8')).hexdigest()[:12]}"
    asof_iso = _utc_iso(asof_time_utc)
    generated_iso = datetime.now(timezone.utc).isoformat()
    candidate_limit = max(int(max_candidates), 1)

    for spec in horizon_specs:
        horizon_key = _clean(spec.get("key")) or _clean(spec.get("column"))
        horizon_col = _clean(spec.get("column")) or "return_1m_pct"
        horizon_label = _clean(spec.get("label")) or horizon_key or horizon_col
        trading_agent_run_id = f"{clean_run_id}:{horizon_key}"
        context, _selected_feed = build_trading_agent_context(
            opportunity_feed=opportunity_feed,
            page_summaries=page_summaries,
            horizon_key=horizon_key,
            horizon_col=horizon_col,
            horizon_label=horizon_label,
            business_filter=business_filter,
            max_candidates=candidate_limit,
        )
        try:
            result = builder(
                context=context,
                llm_client=llm_client,
                aql_agent_runner=aql_agent_runner,
            )
        except Exception as exc:
            error_text = f"Trading Agent materialization failed: {type(exc).__name__}: {exc}"
            if _looks_like_timeout_error(error_text):
                result = _fallback_trading_agent_suggestions(context, reason=error_text)
            else:
                result = {
                    "status": "error",
                    "regime_read": "",
                    "portfolio_posture": "",
                    "candidates": [],
                    "data_gaps": [error_text],
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if not isinstance(result, dict):
            result = {
                "status": "error",
                "regime_read": "",
                "portfolio_posture": "",
                "candidates": [],
                "data_gaps": ["Trading Agent materialization returned an invalid payload."],
                "error": "Invalid Trading Agent payload.",
            }
        candidates = [item for item in to_list(result.get("candidates")) if isinstance(item, dict)]
        context_json = _json_text(context)
        run_rows.append(
            {
                "trading_agent_run_id": trading_agent_run_id,
                "run_id": clean_run_id,
                "horizon_key": horizon_key,
                "horizon_label": horizon_label,
                "selected_horizon_col": horizon_col,
                "business_filter": _clean(business_filter) or "All Market",
                "status": _clean(result.get("status")) or "unknown",
                "regime_read": _clean(result.get("regime_read")),
                "portfolio_posture": _clean(result.get("portfolio_posture")),
                "candidate_count": int(len(candidates)),
                "data_gaps_json": _json_text([_clean(item) for item in to_list(result.get("data_gaps")) if _clean(item)]),
                "error": _clean(result.get("error")),
                "aql_agent_json": _json_text(result.get("aql_agent") if isinstance(result.get("aql_agent"), dict) else {}),
                "context_json": context_json,
                "result_json": _json_text(result),
                "asof_time_utc": asof_iso,
                "generated_at_utc": generated_iso,
            }
        )
        for rank, candidate in enumerate(candidates[:candidate_limit], start=1):
            ticker = _clean(candidate.get("ticker")).upper()
            candidate_id = _candidate_id(
                run_id=clean_run_id,
                horizon_key=horizon_key,
                ticker=ticker,
                rank=rank,
                candidate=candidate,
            )
            candidate_rows.append(
                {
                    "candidate_id": candidate_id,
                    "trading_agent_run_id": trading_agent_run_id,
                    "run_id": clean_run_id,
                    "horizon_key": horizon_key,
                    "horizon_label": horizon_label,
                    "selected_horizon_col": horizon_col,
                    "business_filter": _clean(business_filter) or "All Market",
                    "rank": int(rank),
                    "ticker": ticker,
                    "direction": _clean(candidate.get("direction")),
                    "setup": _clean(candidate.get("setup")),
                    "hypothesis": _clean(candidate.get("hypothesis")),
                    "evidence_json": _json_text([_clean(item) for item in to_list(candidate.get("evidence")) if _clean(item)]),
                    "invalidation": _clean(candidate.get("invalidation")),
                    "tail_risks_json": _json_text([_clean(item) for item in to_list(candidate.get("tail_risks")) if _clean(item)]),
                    "suggested_horizon": _clean(candidate.get("suggested_horizon")),
                    "confidence": _clean(candidate.get("confidence")) or "low",
                    "decision_status": "open",
                    "candidate_json": _json_text(candidate),
                    "context_json": context_json,
                    "asof_time_utc": asof_iso,
                    "generated_at_utc": generated_iso,
                }
            )

    runs = pd.DataFrame(run_rows, columns=TRADING_AGENT_RUN_COLUMNS)
    candidates = pd.DataFrame(candidate_rows, columns=TRADING_AGENT_CANDIDATE_COLUMNS)
    if runs.empty:
        runs = empty_trading_agent_run_frame()
    if candidates.empty:
        candidates = empty_trading_agent_candidate_frame()
    return runs, candidates


def _prepare_trading_agent_price_history(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "timestamp", "close"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame.copy()
    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    if "symbol" not in out.columns:
        out["symbol"] = ""
    if "timestamp" not in out.columns:
        out["timestamp"] = pd.NaT
    if "close" not in out.columns:
        out["close"] = pd.NA
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out[columns].dropna(subset=["symbol", "timestamp", "close"])
    if out.empty:
        return pd.DataFrame(columns=columns)
    return (
        out.sort_values(["symbol", "timestamp"])
        .drop_duplicates(subset=["symbol", "timestamp"], keep="last")
        .reset_index(drop=True)
    )


def _candidate_anchor_ts(row: pd.Series) -> pd.Timestamp | None:
    candidate_time = _clean(row.get("asof_time_utc")) or _clean(row.get("generated_at_utc"))
    return _utc_timestamp(candidate_time)


def _scoring_direction(direction: object) -> str:
    clean = _clean(direction).lower()
    if clean in _TRADING_AGENT_DIRECTION_MULTIPLIER:
        return clean
    return _fallback_direction(clean)


def _horizon_days(row: pd.Series) -> int:
    key = _clean(row.get("horizon_key")).lower()
    if key in _TRADING_AGENT_HORIZON_DAYS:
        return _TRADING_AGENT_HORIZON_DAYS[key]
    label = _clean(row.get("horizon_label") or row.get("suggested_horizon")).lower()
    for horizon_key, days in _TRADING_AGENT_HORIZON_DAYS.items():
        if horizon_key in label:
            return days
    if "week" in label:
        return 7
    if "month" in label:
        return 30
    if "quarter" in label:
        return 91
    if "year" in label:
        return 365
    return 30


def _numeric_or_na(value: object) -> float | Any:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.NA if pd.isna(parsed) else float(parsed)


def _int_or_na(value: object) -> int | Any:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return pd.NA if pd.isna(parsed) else int(parsed)


def _stable_outcome_id(row: pd.Series, target_exit_ts: str) -> str:
    candidate_id = _clean(row.get("candidate_id"))
    payload = {
        "candidate_id": candidate_id,
        "run_id": _clean(row.get("run_id")),
        "trading_agent_run_id": _clean(row.get("trading_agent_run_id")),
        "horizon_key": _clean(row.get("horizon_key")),
        "ticker": _clean(row.get("ticker")).upper(),
        "target_exit_ts": target_exit_ts,
    }
    return f"tao_{_stable_digest(payload)}"


def build_trading_agent_outcomes(
    *,
    candidates: pd.DataFrame,
    price_history: pd.DataFrame,
    runs: pd.DataFrame | None = None,
    evaluation_time_utc: object | None = None,
) -> pd.DataFrame:
    """Score Trading Agent candidates against post-candidate price bars."""
    if not isinstance(candidates, pd.DataFrame) or candidates.empty:
        return empty_trading_agent_outcome_frame()

    candidate_frame = candidates.copy()
    for column in TRADING_AGENT_CANDIDATE_COLUMNS:
        if column not in candidate_frame.columns:
            candidate_frame[column] = ""
    if "candidate_id" in candidate_frame.columns:
        candidate_frame["candidate_id"] = candidate_frame["candidate_id"].apply(_clean)
        candidate_frame = candidate_frame[candidate_frame["candidate_id"].astype(bool)].copy()
        candidate_frame = candidate_frame.drop_duplicates(subset=["candidate_id"], keep="last")
    if candidate_frame.empty:
        return empty_trading_agent_outcome_frame()

    prepared_prices = _prepare_trading_agent_price_history(price_history)
    prices_by_symbol = {
        str(symbol): symbol_prices.drop(columns=["symbol"]).reset_index(drop=True)
        for symbol, symbol_prices in prepared_prices.groupby("symbol", sort=False)
    }
    evaluation_iso = _utc_iso(evaluation_time_utc)
    row_items: list[dict[str, Any]] = []

    for _, row in candidate_frame.iterrows():
        ticker = _clean(row.get("ticker")).upper()
        anchor_ts = _candidate_anchor_ts(row)
        anchor_day = anchor_ts.normalize() if anchor_ts is not None else None
        target_ts = ""
        if anchor_day is not None:
            target_ts = (anchor_day + pd.Timedelta(days=_horizon_days(row))).isoformat()

        direction = _clean(row.get("direction")).lower()
        scoring_direction = _scoring_direction(direction)
        direction_multiplier = _TRADING_AGENT_DIRECTION_MULTIPLIER.get(scoring_direction, 1.0)
        ticker_prices = prices_by_symbol.get(ticker, pd.DataFrame(columns=["timestamp", "close"])).copy()
        source_min_ts = _maybe_iso(ticker_prices["timestamp"].min()) if not ticker_prices.empty else ""
        source_max_ts = _maybe_iso(ticker_prices["timestamp"].max()) if not ticker_prices.empty else ""

        entry_ts = ""
        entry_close: float | Any = pd.NA
        actual_exit_ts = ""
        exit_close: float | Any = pd.NA
        raw_return_pct: float | Any = pd.NA
        signed_return_pct: float | Any = pd.NA
        outcome_status = "missing_price"
        is_win: bool | Any = pd.NA
        is_matured = False

        if anchor_day is None:
            outcome_status = "missing_entry"
        elif not ticker_prices.empty:
            ticker_prices["_bar_day"] = ticker_prices["timestamp"].dt.normalize()
            entry_candidates = ticker_prices[ticker_prices["_bar_day"] >= anchor_day].copy()
            if entry_candidates.empty:
                outcome_status = "missing_entry"
            else:
                entry = entry_candidates.iloc[0]
                entry_ts = _maybe_iso(entry.get("timestamp"))
                entry_close = _numeric_or_na(entry.get("close"))
                future = ticker_prices[ticker_prices["timestamp"] > entry.get("timestamp")].copy()
                if future.empty:
                    outcome_status = "no_future_bar"
                else:
                    target_day = pd.to_datetime(target_ts, utc=True, errors="coerce").normalize()
                    target_candidates = future[future["_bar_day"] >= target_day].copy()
                    if not target_candidates.empty:
                        exit_row = target_candidates.iloc[0]
                        outcome_status = "matured"
                        is_matured = True
                    else:
                        exit_row = future.iloc[-1]
                        outcome_status = "mark_to_market"
                    actual_exit_ts = _maybe_iso(exit_row.get("timestamp"))
                    exit_close = _numeric_or_na(exit_row.get("close"))
                    if (
                        not pd.isna(entry_close)
                        and not pd.isna(exit_close)
                        and float(entry_close) != 0.0
                    ):
                        raw_return_pct = ((float(exit_close) / float(entry_close)) - 1.0) * 100.0
                        signed_return_pct = raw_return_pct * direction_multiplier
                        is_win = bool(signed_return_pct > 0.0)

        outcome_id = _stable_outcome_id(row, target_ts)
        row_items.append(
            {
                "outcome_id": outcome_id,
                "candidate_id": _clean(row.get("candidate_id")),
                "trading_agent_run_id": _clean(row.get("trading_agent_run_id")),
                "run_id": _clean(row.get("run_id")),
                "horizon_key": _clean(row.get("horizon_key")),
                "horizon_label": _clean(row.get("horizon_label")),
                "selected_horizon_col": _clean(row.get("selected_horizon_col")),
                "business_filter": _clean(row.get("business_filter")),
                "rank": _int_or_na(row.get("rank")),
                "ticker": ticker,
                "direction": direction,
                "scoring_direction": scoring_direction,
                "setup": _clean(row.get("setup")),
                "hypothesis": _clean(row.get("hypothesis")),
                "confidence": _clean(row.get("confidence")),
                "suggested_horizon": _clean(row.get("suggested_horizon")),
                "evidence_json": _clean(row.get("evidence_json")),
                "invalidation": _clean(row.get("invalidation")),
                "tail_risks_json": _clean(row.get("tail_risks_json")),
                "candidate_generated_at_utc": _maybe_iso(row.get("generated_at_utc")),
                "candidate_asof_time_utc": _maybe_iso(row.get("asof_time_utc")),
                "entry_ts": entry_ts,
                "entry_close": entry_close,
                "target_exit_ts": target_ts,
                "actual_exit_ts": actual_exit_ts,
                "exit_close": exit_close,
                "raw_return_pct": raw_return_pct,
                "signed_return_pct": signed_return_pct,
                "benchmark_return_pct": pd.NA,
                "excess_signed_return_pct": pd.NA,
                "outcome_status": outcome_status,
                "is_win": is_win,
                "is_matured": bool(is_matured),
                "price_bar_count": int(len(ticker_prices)),
                "source_price_min_ts": source_min_ts,
                "source_price_max_ts": source_max_ts,
                "candidate_json": _clean(row.get("candidate_json")),
                "context_json": _clean(row.get("context_json")),
                "evaluation_generated_at_utc": evaluation_iso,
            }
        )

    outcomes = pd.DataFrame(row_items, columns=TRADING_AGENT_OUTCOME_COLUMNS)
    return outcomes if not outcomes.empty else empty_trading_agent_outcome_frame()


def _lookup_records(frame: pd.DataFrame | None, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or key not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for record in to_jsonable(frame.to_dict("records")):
        if not isinstance(record, dict):
            continue
        record_key = _clean(record.get(key))
        if record_key:
            out[record_key] = record
    return out


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    return text in {"true", "1", "yes", "y"}


def _review_input_payload(
    *,
    outcome: dict[str, Any],
    candidate: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    evidence_items = _json_array_from_text(candidate.get("evidence_json") or outcome.get("evidence_json"))
    tail_risks = _json_array_from_text(candidate.get("tail_risks_json") or outcome.get("tail_risks_json"))
    candidate_payload = _json_object_from_text(candidate.get("candidate_json") or outcome.get("candidate_json"))
    return {
        "review_contract": {
            "scope": "post-call research review only",
            "may_mutate_prompt_or_memory": False,
            "may_change_ranking_or_execution": False,
            "broker_execution_mode": "unchanged_log_only",
        },
        "outcome": {
            key: outcome.get(key)
            for key in [
                "candidate_id",
                "ticker",
                "direction",
                "scoring_direction",
                "horizon_key",
                "horizon_label",
                "outcome_status",
                "is_win",
                "is_matured",
                "entry_ts",
                "entry_close",
                "target_exit_ts",
                "actual_exit_ts",
                "exit_close",
                "raw_return_pct",
                "signed_return_pct",
                "benchmark_return_pct",
                "excess_signed_return_pct",
            ]
        },
        "candidate": {
            "setup": _clean(candidate.get("setup") or outcome.get("setup")),
            "hypothesis": _clean(candidate.get("hypothesis") or outcome.get("hypothesis")),
            "confidence": _clean(candidate.get("confidence") or outcome.get("confidence")),
            "suggested_horizon": _clean(candidate.get("suggested_horizon") or outcome.get("suggested_horizon")),
            "evidence": [_clean(item) for item in evidence_items if _clean(item)],
            "invalidation": _clean(candidate.get("invalidation") or outcome.get("invalidation")),
            "tail_risks": [_clean(item) for item in tail_risks if _clean(item)],
            "raw_candidate": candidate_payload,
        },
        "run": {
            "regime_read": _clean(run.get("regime_read")),
            "portfolio_posture": _clean(run.get("portfolio_posture")),
            "data_gaps": _json_array_from_text(run.get("data_gaps_json")),
            "status": _clean(run.get("status")),
            "aql_agent": _json_object_from_text(run.get("aql_agent_json")),
        },
    }


def _eligible_review_outcomes(
    outcomes: pd.DataFrame,
    *,
    max_reviews: int,
    include_mark_to_market: bool,
) -> pd.DataFrame:
    if not isinstance(outcomes, pd.DataFrame) or outcomes.empty or max_reviews <= 0:
        return empty_trading_agent_outcome_frame()
    out = outcomes.copy()
    if "outcome_status" not in out.columns:
        return empty_trading_agent_outcome_frame()
    allowed_statuses = {"matured"}
    if include_mark_to_market:
        allowed_statuses.add("mark_to_market")
    out = out[out["outcome_status"].astype(str).str.lower().isin(allowed_statuses)].copy()
    if out.empty:
        return empty_trading_agent_outcome_frame()
    out["_is_matured_sort"] = out.get("is_matured", False).apply(_boolish) if "is_matured" in out.columns else False
    if "signed_return_pct" in out.columns:
        out["_abs_signed_return"] = pd.to_numeric(out["signed_return_pct"], errors="coerce").abs()
    else:
        out["_abs_signed_return"] = 0.0
    if "candidate_asof_time_utc" in out.columns:
        out["_candidate_time"] = pd.to_datetime(out["candidate_asof_time_utc"], utc=True, errors="coerce")
    else:
        out["_candidate_time"] = pd.NaT
    out = out.sort_values(
        ["_is_matured_sort", "_candidate_time", "_abs_signed_return"],
        ascending=[False, False, False],
        na_position="last",
    )
    return out.drop(columns=[col for col in out.columns if col.startswith("_")]).head(max_reviews).reset_index(drop=True)


def _review_unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        "review_status": "unavailable",
        "outcome_summary": "",
        "thesis_verdict": "",
        "direction_verdict": "",
        "horizon_verdict": "",
        "evidence_verdict": "",
        "primary_failure_mode": "",
        "primary_success_factor": "",
        "missing_evidence_slots": [],
        "recommended_contract_change": "",
        "recommended_prompt_change": "",
        "recommended_retrieval_change": "",
        "recommended_candidate_selection_change": "",
        "source_refs": [],
        "error": reason,
    }


def _call_trading_agent_research_review(
    *,
    llm_client: Any | None,
    review_input: dict[str, Any],
) -> dict[str, Any]:
    if llm_client is None or not hasattr(llm_client, "generate_json"):
        return _review_unavailable_payload("Trading Agent research review requires an LLM runtime.")
    try:
        payload = generate_json_via_aql_zopedia_gateway(
            llm_client=llm_client,
            surface="trading_agent.review",
            purpose="research_review",
            call_type="research_grade",
            system_prompt=_TRADING_AGENT_REVIEW_SYSTEM_PROMPT,
            user_prompt=(
                "Review this Trading Agent call as a research-quality post-mortem. "
                "Return only the schema fields. Do not recommend an immediate trade.\n\n"
                f"Review input JSON:\n{_compact_json(review_input, limit=12000)}"
            ),
            schema_name="trading_agent_research_review",
            schema=_TRADING_AGENT_REVIEW_SCHEMA,
            metadata={
                "candidate_id": _clean((review_input.get("candidate") or {}).get("candidate_id"))
                if isinstance(review_input.get("candidate"), dict)
                else "",
                "ticker": _clean((review_input.get("outcome") or {}).get("ticker"))
                if isinstance(review_input.get("outcome"), dict)
                else "",
                "horizon_key": _clean((review_input.get("outcome") or {}).get("horizon_key"))
                if isinstance(review_input.get("outcome"), dict)
                else "",
            },
        )
    except Exception as exc:
        unavailable = _review_unavailable_payload(_safe_error_text(f"{type(exc).__name__}: {exc}"))
        unavailable["review_status"] = "error"
        return unavailable
    return payload if isinstance(payload, dict) else _review_unavailable_payload("Invalid research review payload.")


def build_trading_agent_research_reviews(
    *,
    outcomes: pd.DataFrame,
    candidates: pd.DataFrame | None = None,
    runs: pd.DataFrame | None = None,
    llm_client: Any | None = None,
    max_reviews: int = 8,
    include_mark_to_market: bool = False,
    review_time_utc: object | None = None,
) -> pd.DataFrame:
    """Build bounded qualitative reviews for mature Trading Agent outcomes."""
    eligible = _eligible_review_outcomes(
        outcomes,
        max_reviews=max(int(max_reviews), 0),
        include_mark_to_market=include_mark_to_market,
    )
    if eligible.empty:
        return empty_trading_agent_research_review_frame()

    candidate_lookup = _lookup_records(candidates, "candidate_id")
    run_lookup = _lookup_records(runs, "trading_agent_run_id")
    review_iso = _utc_iso(review_time_utc)
    review_rows: list[dict[str, Any]] = []

    for outcome_record in to_jsonable(eligible.to_dict("records")):
        if not isinstance(outcome_record, dict):
            continue
        candidate_id = _clean(outcome_record.get("candidate_id"))
        trading_agent_run_id = _clean(outcome_record.get("trading_agent_run_id"))
        candidate_record = candidate_lookup.get(candidate_id, {})
        run_record = run_lookup.get(trading_agent_run_id, {})
        review_input = _review_input_payload(
            outcome=outcome_record,
            candidate=candidate_record,
            run=run_record,
        )
        review_payload = _call_trading_agent_research_review(
            llm_client=llm_client,
            review_input=review_input,
        )
        if not isinstance(review_payload, dict):
            review_payload = _review_unavailable_payload("Invalid research review payload.")
        review_id = f"tarr_{_stable_digest({'outcome_id': outcome_record.get('outcome_id'), 'candidate_id': candidate_id})}"
        review_rows.append(
            {
                "review_id": review_id,
                "outcome_id": _clean(outcome_record.get("outcome_id")),
                "candidate_id": candidate_id,
                "trading_agent_run_id": trading_agent_run_id,
                "run_id": _clean(outcome_record.get("run_id")),
                "horizon_key": _clean(outcome_record.get("horizon_key")),
                "ticker": _clean(outcome_record.get("ticker")).upper(),
                "direction": _clean(outcome_record.get("direction")),
                "review_status": _clean(review_payload.get("review_status")) or "unknown",
                "outcome_status": _clean(outcome_record.get("outcome_status")),
                "is_win": outcome_record.get("is_win"),
                "is_matured": outcome_record.get("is_matured"),
                "signed_return_pct": _numeric_or_na(outcome_record.get("signed_return_pct")),
                "raw_return_pct": _numeric_or_na(outcome_record.get("raw_return_pct")),
                "review_generated_at_utc": review_iso,
                "outcome_summary": _clean(review_payload.get("outcome_summary")),
                "thesis_verdict": _clean(review_payload.get("thesis_verdict")),
                "direction_verdict": _clean(review_payload.get("direction_verdict")),
                "horizon_verdict": _clean(review_payload.get("horizon_verdict")),
                "evidence_verdict": _clean(review_payload.get("evidence_verdict")),
                "primary_failure_mode": _clean(review_payload.get("primary_failure_mode")),
                "primary_success_factor": _clean(review_payload.get("primary_success_factor")),
                "missing_evidence_slots_json": _json_text(
                    [_clean(item) for item in to_list(review_payload.get("missing_evidence_slots")) if _clean(item)]
                ),
                "recommended_contract_change": _clean(review_payload.get("recommended_contract_change")),
                "recommended_prompt_change": _clean(review_payload.get("recommended_prompt_change")),
                "recommended_retrieval_change": _clean(review_payload.get("recommended_retrieval_change")),
                "recommended_candidate_selection_change": _clean(
                    review_payload.get("recommended_candidate_selection_change")
                ),
                "source_refs_json": _json_text(
                    [_clean(item) for item in to_list(review_payload.get("source_refs")) if _clean(item)]
                ),
                "review_input_json": _json_text(review_input),
                "review_json": _json_text(review_payload),
                "error": _clean(review_payload.get("error")),
            }
        )

    reviews = pd.DataFrame(review_rows, columns=TRADING_AGENT_RESEARCH_REVIEW_COLUMNS)
    return reviews if not reviews.empty else empty_trading_agent_research_review_frame()


def _looks_like_provider_policy_error(text: object) -> bool:
    lowered = _clean(text).lower()
    return "invalid_prompt" in lowered or "usage policy" in lowered or "potentially violating" in lowered


def _looks_like_transient_transport_error(text: object) -> bool:
    lowered = _clean(text).lower()
    return any(
        fragment in lowered
        for fragment in [
            "connectionerror",
            "connection aborted",
            "remotedisconnected",
            "remote end closed connection",
            "read timed out",
            "connection reset",
            "temporarily unavailable",
        ]
    )


def _looks_like_timeout_error(text: object) -> bool:
    lowered = _clean(text).lower()
    return "timeout" in lowered or "timed out" in lowered or "exceeded" in lowered


def _safe_error_text(text: object, *, fallback: str = "The model rejected the generated research prompt.") -> str:
    clean = _clean(text)
    if not clean:
        return fallback
    if _looks_like_provider_policy_error(clean):
        return (
            "The model rejected the generated research prompt before producing an answer. "
            "This is usually caused by wording that looks too much like direct trading advice, even when the app intended a research-only watchlist."
        )
    if _looks_like_transient_transport_error(clean):
        return (
            "A research source connection dropped while the AQL agent was gathering evidence. "
            "This is usually transient; rerun the experiment to retry the evidence fetch."
        )
    return clean


def _default_aql_agent_runner(**kwargs: Any) -> dict[str, Any]:
    from .aql_zopedia_engine import run_aql_zopedia_agent

    kwargs.setdefault("task", "trading_agent")
    kwargs.setdefault("surface", "trading_agent")
    return run_aql_zopedia_agent(**kwargs)


def _aql_ticker_query(context: dict[str, Any], ticker_context: dict[str, Any]) -> str:
    ticker = _clean(ticker_context.get("ticker")).upper()
    company_name = _clean(ticker_context.get("company_name"))
    identity_line = f"{ticker} = {company_name}" if company_name else ticker
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    source_summaries = context.get("source_summaries") if isinstance(context.get("source_summaries"), dict) else {}
    prompt_context = {
        "controls": controls,
        "source_summaries": {
            "market_explorer": source_summaries.get("market_explorer", {}),
            "broad_economy": source_summaries.get("broad_economy", {}),
        },
        "ticker_context": ticker_context,
    }
    return (
        "Use the shared AQL research tool harness to build one grounded Trading Agent research package. "
        f"Focus ticker: {identity_line}. Research this ticker only; use peers, sector, or macro only when they directly explain this ticker. "
        "Zopedia pages and retained context are useful memory when present, but their absence is not a failure; "
        "ground the package with the supplied market-opportunity row, company context, filings, fundamentals, recent news, and source search as available. "
        "Do not synthesize a final portfolio view. The package may complete with an unknown or watch-only verdict when evidence is thin; "
        "do not exhaust the budget trying to fill every missing source. Verify company identity, "
        "gather high-signal business/current evidence when available, then write what is supported and name exact gaps. "
        "Refer to supplied app records as materialized context, not user-supplied evidence. Use exact dates from evidence when available; do not infer weekdays. "
        "Prefer unknown over unsupported claims, and keep the output framed as market research for human review.\n\n"
        "Single-ticker research context JSON:\n"
        f"{_compact_json(prompt_context, limit=9000)}"
    )


def _aql_agent_context(result: dict[str, Any]) -> dict[str, Any]:
    tool_calls = []
    for call in to_list((result or {}).get("tool_calls"))[:10]:
        if not isinstance(call, dict):
            continue
        tool_calls.append(
            {
                "tool_name": _clean(call.get("tool_name")),
                "status": _clean(call.get("status")),
                "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                "preview": _clean((call.get("result_summary") or {}).get("preview_text"))[:500]
                if isinstance(call.get("result_summary"), dict)
                else "",
            }
        )
    return {
        "run_id": _clean((result or {}).get("run_id")),
        "evidence_pack_id": _clean((result or {}).get("aql_evidence_pack_id")),
        "evidence_pack": result.get("aql_evidence_pack") if isinstance(result.get("aql_evidence_pack"), dict) else {},
        "status": _clean((result or {}).get("status")),
        "answer_markdown": _clean((result or {}).get("answer_markdown")),
        "confidence": _clean((result or {}).get("confidence")),
        "limitations": [_clean(item) for item in to_list((result or {}).get("limitations")) if _clean(item)][:8],
        "tool_calls": tool_calls,
    }


def _run_aql_with_retry(
    runner: AgentRunner,
    *,
    query: str,
    llm_client: Any,
    max_tool_calls: int = 6,
    attempts: int = 2,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, max(int(attempts), 1) + 1):
        try:
            return runner(
                query=query,
                force_refresh=False,
                max_tool_calls=max(int(max_tool_calls), 0),
                llm_client=llm_client,
                persist_findings=False,
                write_policy=_trading_agent_write_policy(),
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= max(int(attempts), 1) or not _looks_like_transient_transport_error(
                f"{type(exc).__name__}: {exc}"
            ):
                raise
            time.sleep(0.8 * attempt)
    if last_exc is not None:
        raise last_exc
    return {}


def _macro_package_from_context(context: dict[str, Any]) -> dict[str, Any]:
    source_summaries = context.get("source_summaries") if isinstance(context, dict) else {}
    if not isinstance(source_summaries, dict):
        source_summaries = {}
    return {
        "status": "materialized",
        "market_explorer": source_summaries.get("market_explorer") if isinstance(source_summaries.get("market_explorer"), dict) else {},
        "broad_economy": source_summaries.get("broad_economy") if isinstance(source_summaries.get("broad_economy"), dict) else {},
    }


def _ticker_package_from_result(
    *,
    ticker_context: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    aql_context = _aql_agent_context(result if isinstance(result, dict) else {})
    status = "completed" if aql_context.get("status") == "completed" and aql_context.get("answer_markdown") else "failed"
    return {
        "ticker": _clean(ticker_context.get("ticker")).upper(),
        "company_name": _clean(ticker_context.get("company_name")),
        "status": status,
        "answer_markdown": _clean(aql_context.get("answer_markdown")),
        "confidence": _clean(aql_context.get("confidence")) or "low",
        "limitations": [_clean(item) for item in to_list(aql_context.get("limitations")) if _clean(item)][:6],
        "tool_calls": aql_context.get("tool_calls") if isinstance(aql_context.get("tool_calls"), list) else [],
        "evidence_pack_id": _clean(aql_context.get("evidence_pack_id")),
        "error": _safe_error_text((result or {}).get("error"), fallback="") if isinstance(result, dict) else "",
        "market_opportunity": ticker_context.get("market_opportunity") if isinstance(ticker_context.get("market_opportunity"), dict) else {},
        "stock_summary": ticker_context.get("stock_summary") if isinstance(ticker_context.get("stock_summary"), dict) else {},
    }


def _failed_ticker_package(
    *,
    ticker_context: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    return {
        "ticker": _clean(ticker_context.get("ticker")).upper(),
        "company_name": _clean(ticker_context.get("company_name")),
        "status": "failed",
        "answer_markdown": "",
        "confidence": "low",
        "limitations": [_clean(error)] if _clean(error) else [],
        "tool_calls": [],
        "evidence_pack_id": "",
        "error": _clean(error),
        "market_opportunity": ticker_context.get("market_opportunity") if isinstance(ticker_context.get("market_opportunity"), dict) else {},
        "stock_summary": ticker_context.get("stock_summary") if isinstance(ticker_context.get("stock_summary"), dict) else {},
    }


def _run_ticker_aql_packages(
    runner: AgentRunner,
    *,
    context: dict[str, Any],
    ticker_contexts: list[dict[str, Any]],
    llm_client: Any,
) -> list[dict[str, Any]]:
    if not ticker_contexts:
        return []
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    parallelism = max(int(controls.get("ticker_aql_parallelism") or 4), 1)
    try:
        package_tool_budget = int(controls.get("ticker_aql_max_tool_calls") or os.getenv("TRADING_AGENT_TICKER_AQL_MAX_TOOL_CALLS") or 8)
    except Exception:
        package_tool_budget = 8
    package_tool_budget = min(max(package_tool_budget, 4), 16)
    max_workers = min(parallelism, len(ticker_contexts))

    def run_one(index: int, ticker_context: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            result = _run_aql_with_retry(
                runner,
                query=_aql_ticker_query(context, ticker_context),
                llm_client=llm_client,
                max_tool_calls=package_tool_budget,
            )
            return index, _ticker_package_from_result(ticker_context=ticker_context, result=result)
        except Exception as exc:
            safe_message = _safe_error_text(f"AQL agent failed: {type(exc).__name__}: {exc}")
            return index, _failed_ticker_package(ticker_context=ticker_context, error=safe_message)

    packages: list[dict[str, Any] | None] = [None] * len(ticker_contexts)
    if max_workers <= 1:
        for index, ticker_context in enumerate(ticker_contexts):
            result_index, package = run_one(index, ticker_context)
            packages[result_index] = package
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_one, index, ticker_context): index
                for index, ticker_context in enumerate(ticker_contexts)
            }
            for future in as_completed(futures):
                result_index, package = future.result()
                packages[result_index] = package
    return [package for package in packages if isinstance(package, dict)]


def _trading_aql_context_from_packages(
    *,
    context: dict[str, Any],
    ticker_packages: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [package for package in ticker_packages if _clean(package.get("status")) == "completed"]
    limitations: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    answer_parts: list[str] = []
    for package in ticker_packages:
        ticker = _clean(package.get("ticker")).upper()
        answer = _clean(package.get("answer_markdown"))
        if answer:
            answer_parts.append(f"{ticker}: {answer}")
        for item in to_list(package.get("limitations")):
            text = _safe_error_text(item)
            _append_distinct_text(limitations, text)
        if _clean(package.get("error")):
            text = _safe_error_text(package.get("error"))
            _append_distinct_text(limitations, text)
        for call in to_list(package.get("tool_calls")):
            if isinstance(call, dict):
                tool_calls.append(call)
    status = "completed" if completed else "failed"
    return {
        "run_id": "",
        "evidence_pack_id": "",
        "status": status,
        "answer_markdown": "\n\n".join(answer_parts),
        "confidence": "medium" if completed else "low",
        "limitations": limitations[:8],
        "tool_calls": tool_calls[:12],
        "macro_package": _macro_package_from_context(context),
        "ticker_packages": ticker_packages,
    }


def build_trading_agent_suggestions(
    *,
    context: dict[str, Any],
    llm_client: Any | None,
    aql_agent_runner: AgentRunner | None = None,
) -> dict[str, Any]:
    if llm_client is None:
        return {
            "status": "unavailable",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": ["LLM runtime is not configured."],
            "error": "LLM runtime is not configured.",
        }

    runner = aql_agent_runner or _default_aql_agent_runner
    normalized_context = context or {}
    ticker_contexts = _ticker_contexts_from_context(normalized_context)
    if not ticker_contexts:
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": ["Trading Agent context did not include any focus tickers."],
            "error": "Trading Agent context did not include any focus tickers.",
        }

    ticker_packages = _run_ticker_aql_packages(
        runner,
        context=normalized_context,
        ticker_contexts=ticker_contexts,
        llm_client=llm_client,
    )
    aql_context = _trading_aql_context_from_packages(
        context=normalized_context,
        ticker_packages=ticker_packages,
    )
    if aql_context["status"] != "completed" or not aql_context["answer_markdown"]:
        limitations = [
            _safe_error_text(item)
            for item in to_list(aql_context.get("limitations"))
            if _clean(item)
        ]
        if any(_looks_like_timeout_error(item) for item in limitations):
            return _fallback_trading_agent_suggestions(
                normalized_context,
                reason=limitations[0] if limitations else "AQL agent timed out before producing grounded trading synthesis.",
                aql_context=aql_context,
            )
        error_text = limitations[0] if limitations else "AQL agent did not produce grounded trading synthesis."
        return {
            "status": "error" if aql_context["status"] != "unavailable" else "unavailable",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [
                item
                for item in [
                    "AQL agent did not produce grounded trading synthesis.",
                    *limitations,
                ]
                if _clean(item)
            ][:8],
            "error": error_text or "AQL agent did not produce grounded trading synthesis.",
            "aql_agent": aql_context,
        }

    synthesis_context = dict(normalized_context)
    synthesis_context["ticker_evidence"] = ticker_contexts
    synthesis_context["market_opportunity_feed"] = [
        item.get("market_opportunity")
        for item in ticker_contexts
        if isinstance(item.get("market_opportunity"), dict) and item.get("market_opportunity")
    ]
    user_prompt = (
        "Research context JSON:\n"
        f"{_compact_json(synthesis_context)}\n\n"
        "AQL agent result JSON:\n"
        f"{_compact_json(aql_context, limit=12000)}\n\n"
        "Return the strongest watchlist candidates only, using the single-ticker AQL packages as the evidence boundary. "
        "Prefer no candidate over weak evidence. Use only the supplied context and AQL agent result. "
        "Use direction='watch' when the setup needs confirmation. "
        "Use downside or avoid classifications when the evidence points to fading momentum or unacceptable risk. "
        "Refer to app records as materialized context, not user-supplied evidence. Use exact dates from evidence; do not infer weekdays."
    )
    try:
        payload = generate_json_via_aql_zopedia_gateway(
            llm_client=llm_client,
            surface="trading_agent.synthesis",
            purpose="candidate_synthesis",
            call_type="formatter_over_aql",
            system_prompt=get_prompt(_TRADING_AGENT_SYSTEM_PROMPT),
            user_prompt=user_prompt,
            schema_name="trading_agent_experiment",
            schema=_TRADING_AGENT_SCHEMA,
            metadata={
                "horizon_label": _clean((normalized_context.get("controls") or {}).get("selected_horizon_label"))
                if isinstance(normalized_context.get("controls"), dict)
                else "",
                "horizon_column": _clean((normalized_context.get("controls") or {}).get("selected_horizon_col"))
                if isinstance(normalized_context.get("controls"), dict)
                else "",
                "ticker_count": len(ticker_contexts),
                "aql_status": _clean(aql_context.get("status")),
            },
        )
    except LLMAPIError as exc:
        safe_message = _safe_error_text(exc)
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [safe_message],
            "error": safe_message,
        }
    except Exception as exc:
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [f"{type(exc).__name__}: {exc}"],
            "error": f"{type(exc).__name__}: {exc}",
        }

    allowed_tickers = {_clean(item.get("ticker")).upper() for item in ticker_contexts if _clean(item.get("ticker"))}
    candidates: list[dict[str, Any]] = []
    for item in to_list(payload.get("candidates")):
        if not isinstance(item, dict):
            continue
        ticker = _clean(item.get("ticker")).upper()
        if not ticker:
            continue
        if allowed_tickers and ticker not in allowed_tickers:
            continue
        direction = _clean(item.get("direction")).lower()
        if direction not in {"long", "short", "watch", "avoid"}:
            direction = "watch"
        candidates.append(
            {
                "ticker": ticker,
                "direction": direction,
                "setup": _clean(item.get("setup")),
                "hypothesis": _clean(item.get("hypothesis")),
                "evidence": [_clean(value) for value in to_list(item.get("evidence")) if _clean(value)][:5],
                "invalidation": _clean(item.get("invalidation")),
                "tail_risks": [_clean(value) for value in to_list(item.get("tail_risks")) if _clean(value)][:5],
                "suggested_horizon": _clean(item.get("suggested_horizon")),
                "confidence": _clean(item.get("confidence")) or "low",
            }
        )

    data_gaps = []
    for item in [
        *[_clean(item) for item in to_list(payload.get("data_gaps")) if _clean(item)],
        *[_clean(item) for item in to_list(aql_context.get("limitations")) if _clean(item)],
    ]:
        _append_distinct_text(data_gaps, item)

    return {
        "status": "ok",
        "regime_read": _clean(payload.get("regime_read")),
        "portfolio_posture": _clean(payload.get("portfolio_posture")),
        "candidates": candidates[:8],
        "data_gaps": data_gaps[:6],
        "error": "",
        "aql_agent": aql_context,
    }


__all__ = [
    "TRADING_AGENT_CANDIDATE_COLUMNS",
    "TRADING_AGENT_HORIZON_SPECS",
    "TRADING_AGENT_RUN_COLUMNS",
    "build_trading_agent_context",
    "build_trading_agent_materialized_frames",
    "build_trading_agent_suggestions",
    "empty_trading_agent_candidate_frame",
    "empty_trading_agent_run_frame",
]
