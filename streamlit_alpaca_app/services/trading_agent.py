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

from compute.fundamentals import fundamental_snapshots_by_symbol

from .llm_context import serialize_llm_context
from .llm_execution import current_research_access_scope

from .json_utils import to_jsonable, to_list
from .llm import (
    LLMAPIError,
    get_prompt,
    register_narrative_prompt,
)


_TRADING_AGENT_SYSTEM_PROMPT = register_narrative_prompt(
    name="Trading Agent Directional Calls",
    file="services/trading_agent.py",
    group="Trading Agent",
    prompt=(
        f"You are the Spectral Nature market research analyst. "
        "Produce ranked, falsifiable share-price calls for the exact requested horizon using the dated company research and market observations. "
        "Every selected company needs your preferred long or short direction, including close decisions. Confidence describes the strength of the reasoning, not permission to make a call. "
        "These are research ideas, not existing portfolio holdings or instructions to place orders. "
        "Explain the business mechanism, what the current share price appears to expect, and what can change those expectations before this horizon ends. "
        "Separate business quality from purchase price and incremental future changes from developments that have already happened. "
        "For each call identify the strongest opposing case, the assumptions that decide between the two, and an observable development that would reverse your view. "
        "Use comparable reporting periods and per-share economics when interpreting growth, financing and valuation. Financing provides cash as well as changing claims on the business; a transaction price does not guarantee a stock-price floor. "
        "A long-term business development is not a near-term price driver without a reason it can change expectations inside the requested horizon. "
        "Facts must come from the brief. Label forward assumptions as estimates, retain dates and distinguish a market snapshot's availability time from the actual price session date. "
        "Do not invent probabilities, price targets, valuations or quantities to make a direction sound precise. State material unknowns and lower confidence where assumptions carry the call. "
        "Research execution failure or unresolved identity is a failure, never a neutral forecast or a fabricated call. "
        "Write ordinary investor language. Each prose field is at most two short sentences; each list has at most three distinct, useful items. "
        "Evidence states dated facts; risks are events that would hurt this particular long or short call; research next states what to learn and why it could change the call. "
        "Keep these distinct and avoid repeating the same fact. The backdrop describes the market and shared exposure describes the ideas' common risks, without claiming they are held. "
        "Discuss companies and markets directly, without narrating records, inputs, tools, missing sources or the app's internal operation."
    ),
)

TRADING_AGENT_NARRATIVE_CONTRACT_VERSION = "directional_price_calls_v3"
TRADING_AGENT_LEGACY_NARRATIVE_CONTRACTS = {"plain_investor_research_v2"}

_TRADING_AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "regime_read": {
            "type": "string",
            "description": "At most two short sentences on the supported market backdrop. Say directly when the backdrop is unknown.",
        },
        "portfolio_posture": {
            "type": "string",
            "description": "One short sentence explaining shared risks among these ideas. Do not claim they are existing holdings or recite candidate counts.",
        },
        "candidates": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ticker": {"type": "string"},
                    "stance": {"type": "string", "enum": ["long", "short"]},
                    "forecast": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "pricing_assessment": {"type": "string", "description": "Why the observed share price leaves upside or downside at this horizon. Separate estimates from facts; do not invent a valuation when inputs are unknown. Two short sentences."},
                            "assumptions": {"type": "array", "maxItems": 3, "items": {"type": "string"}, "description": "The uncertain forward conditions on which the direction depends, stated as assumptions rather than facts."},
                            "opposing_case": {"type": "string", "description": "The strongest substantive argument for the other direction, including counterevidence. Two short sentences."},
                        },
                        "required": ["pricing_assessment", "assumptions", "opposing_case"],
                    },
                    "setup": {
                        "type": "string",
                        "description": "One short sentence explaining why the company is worth researching now and why the move may matter.",
                    },
                    "hypothesis": {
                        "type": "string",
                        "description": "A falsifiable company or market thesis in no more than two short sentences.",
                    },
                    "evidence": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                        "description": "Up to three distinct company, catalyst, filing, fundamental, or price facts with useful dates and plain explanations.",
                    },
                    "invalidation": {
                        "type": "string",
                        "description": "One or two short sentences naming an observable condition that would disprove the hypothesis.",
                    },
                    "tail_risks": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                        "description": "Up to three concrete market or business events that could hurt the thesis, never missing-data diagnostics.",
                    },
                    "evidence_limits": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                        "description": "Up to three forward-looking research questions or facts to obtain next and why they matter; never a description of input defects or an unsupported raw figure.",
                    },
                    "suggested_horizon": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": [
                    "ticker",
                    "stance",
                    "forecast",
                    "setup",
                    "hypothesis",
                    "evidence",
                    "invalidation",
                    "tail_risks",
                    "evidence_limits",
                    "suggested_horizon",
                    "confidence",
                ],
            },
        },
        "data_gaps": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string"},
            "description": "Run-level missing market facts, stated plainly and without system or storage terminology.",
        },
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
    "stance",
    "research_status",
    "setup",
    "hypothesis",
    "evidence_json",
    "invalidation",
    "tail_risks_json",
    "evidence_limits_json",
    "suggested_horizon",
    "confidence",
    "decision_status",
    "candidate_json",
    "context_json",
    "asof_time_utc",
    "generated_at_utc",
]

TRADING_AGENT_RESEARCH_PACKAGE_COLUMNS = [
    "research_package_id",
    "run_id",
    "ticker",
    "company_name",
    "status",
    "confidence",
    "answer_markdown",
    "limitations_json",
    "tool_calls_json",
    "evidence_pack_id",
    "horizon_keys_json",
    "horizon_labels_json",
    "selection_trace_json",
    "opportunity_context_json",
    "error",
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
    "stance",
    "research_status",
    "scoring_direction",
    "setup",
    "hypothesis",
    "confidence",
    "suggested_horizon",
    "evidence_json",
    "invalidation",
    "tail_risks_json",
    "evidence_limits_json",
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
    "short": -1.0,
}

_TRADING_AGENT_STANCES = {"long", "short", "no_call"}
_TRADING_AGENT_RESEARCH_STATUSES = {"supported", "watch", "avoid", "insufficient_evidence"}

_TRADING_AGENT_REVIEW_SYSTEM_PROMPT = register_narrative_prompt(
    name="Trading Agent Research Review",
    file="services/trading_agent.py",
    group="Trading Agent",
    prompt=(
        f"You are reviewing a completed or interim Spectral Nature Trading Agent research call. "
        "Use only the supplied candidate, evidence, and measured outcome. Evaluate the original call without creating a new one. "
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
    # Preserve all model context; share exact repeated values by reference.
    return serialize_llm_context(value)


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


def _prompt_number(value: object) -> float | None:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return None
    return float(parsed)


def _research_summary_for_prompt(summary: object) -> dict[str, Any]:
    if not isinstance(summary, dict) or not summary:
        return {}
    payload = {
        "headline": _clean(summary.get("headline")),
        "summary": _clean(summary.get("summary_markdown")),
        "items_to_watch": [
            _clean(item)
            for item in to_list(summary.get("watch_items"))
            if _clean(item)
        ][:5],
        "confidence": _clean(summary.get("confidence")),
    }
    return {key: value for key, value in payload.items() if value not in ("", None, [])}


def _market_observation_for_prompt(
    opportunity: object,
    *,
    horizon_label: object = "",
    horizon_column: object = "",
) -> dict[str, Any]:
    if not isinstance(opportunity, dict) or not opportunity:
        return {}
    selected_column = _clean(horizon_column or opportunity.get("selected_horizon_col"))
    selected_label = _clean(
        horizon_label
        or opportunity.get("selected_horizon_label")
        or selected_column
    )
    payload = {
        "as_of": _clean(opportunity.get("asof_time_utc")),
        "screen_reason": _clean(opportunity.get("opportunity")),
        "price_pattern": _clean(opportunity.get("direction")),
        "daily_return_pct": _prompt_number(opportunity.get("daily_change_pct")),
        "horizon": selected_label,
        "horizon_return_pct": _prompt_number(opportunity.get(selected_column)) if selected_column else None,
    }
    price = _prompt_number(opportunity.get("close"))
    if price is not None and price > 0:
        payload["reference_price"] = price
        payload["reference_price_basis"] = "last daily close observed by the market snapshot"
    return {key: value for key, value in payload.items() if value not in ("", None)}


def _ticker_research_brief(ticker_context: dict[str, Any]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    observation_keys: set[str] = set()
    horizon_items = [
        item
        for item in to_list(ticker_context.get("horizon_opportunities"))
        if isinstance(item, dict)
    ]
    for item in horizon_items:
        observation = _market_observation_for_prompt(
            item.get("market_opportunity"),
            horizon_label=item.get("horizon_label"),
            horizon_column=item.get("selected_horizon_col"),
        )
        key = _json_text(observation)
        if observation and key not in observation_keys:
            observations.append(observation)
            observation_keys.add(key)
    if not observations:
        observation = _market_observation_for_prompt(ticker_context.get("market_opportunity"))
        if observation:
            observations.append(observation)

    attention_context = ticker_context.get("attention_context")
    recent_context = []
    if isinstance(attention_context, dict):
        recent_context = [
            _clean(attention_context.get(key))
            for key in ["llm_headline", "llm_why_now", "llm_management_signal"]
            if _clean(attention_context.get(key))
        ][:3]

    payload = {
        "ticker": _clean(ticker_context.get("ticker")).upper(),
        "company_name": _clean(ticker_context.get("company_name")),
        "research_horizons": [
            _clean(item)
            for item in to_list(ticker_context.get("horizon_labels"))
            if _clean(item)
        ],
        "market_observations": observations,
        "company_summary": _research_summary_for_prompt(ticker_context.get("stock_summary")),
        "financials": ticker_context.get("financials") or {},
        "recent_company_context": recent_context,
        "recent_news": [
            _clean(item)
            for item in to_list(ticker_context.get("news_summary_lines"))
            if _clean(item)
        ][:5],
    }
    return {key: value for key, value in payload.items() if value not in ("", None, [])}


def _research_prompt_context(
    context: dict[str, Any],
    ticker_context: dict[str, Any],
) -> dict[str, Any]:
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    source_summaries = context.get("source_summaries") if isinstance(context, dict) else {}
    if not isinstance(source_summaries, dict):
        source_summaries = {}
    return {
        "research_scope": {
            "business_focus": _clean(controls.get("business_filter")),
            "forecast_windows": context.get("forecast_windows") or [context.get("forecast_window") or {}],
            "selected_horizon": _clean(
                controls.get("selected_horizon_label") or controls.get("momentum_horizon")
            ),
        },
        "market_context": {
            "market": _research_summary_for_prompt(source_summaries.get("market_explorer")),
            "economy": _research_summary_for_prompt(source_summaries.get("broad_economy")),
        },
        "company": _ticker_research_brief(ticker_context),
    }


def _synthesis_prompt_context(
    context: dict[str, Any],
    ticker_contexts: list[dict[str, Any]],
    ticker_packages: list[dict[str, Any]],
) -> dict[str, Any]:
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    source_summaries = context.get("source_summaries") if isinstance(context, dict) else {}
    if not isinstance(source_summaries, dict):
        source_summaries = {}
    findings = []
    for package in ticker_packages:
        if not isinstance(package, dict) or _clean(package.get("status")) != "completed":
            continue
        findings.append(
            {
                "ticker": _clean(package.get("ticker")).upper(),
                "company_name": _clean(package.get("company_name")),
                "research_findings": _clean(package.get("answer_markdown")),
                "confidence": _clean(package.get("confidence")),
            }
        )
    return {
        "research_scope": {
            "business_focus": _clean(controls.get("business_filter")),
            "forecast_window": context.get("forecast_window") or {},
            "selected_horizon": _clean(
                controls.get("selected_horizon_label") or controls.get("momentum_horizon")
            ),
        },
        "market_context": {
            "market": _research_summary_for_prompt(source_summaries.get("market_explorer")),
            "economy": _research_summary_for_prompt(source_summaries.get("broad_economy")),
        },
        "companies": [_ticker_research_brief(item) for item in ticker_contexts],
        "research_findings": findings,
    }


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
    max_candidates = max(
        int(controls.get("research_candidate_limit") or controls.get("max_candidates") or 4),
        1,
    )
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
            "financials": {},
            "attention_context": {},
            "news_summary_lines": [],
        }
        if isinstance(base, dict):
            merged["financials"] = base.get("financials") if isinstance(base.get("financials"), dict) else {}
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


def _candidate_semantics(candidate: dict[str, Any]) -> tuple[str, str, str]:
    contract = candidate.get("narrative_contract_version") or _json_object_from_text(candidate.get("candidate_json")).get("narrative_contract_version")
    if contract == TRADING_AGENT_NARRATIVE_CONTRACT_VERSION:
        stance = _clean(candidate.get("stance")).lower()
        if stance not in _TRADING_AGENT_DIRECTION_MULTIPLIER:
            raise ValueError("A price forecast must choose long or short.")
        return stance, "forecast", stance
    direction = _fallback_direction(candidate.get("direction"))
    stance = _clean(candidate.get("stance")).lower()
    if stance not in _TRADING_AGENT_STANCES:
        stance = direction if direction in _TRADING_AGENT_DIRECTION_MULTIPLIER else "no_call"

    research_status = _clean(candidate.get("research_status")).lower()
    if research_status not in _TRADING_AGENT_RESEARCH_STATUSES:
        if stance in _TRADING_AGENT_DIRECTION_MULTIPLIER:
            research_status = "supported"
        elif direction == "avoid":
            research_status = "avoid"
        else:
            research_status = "watch"

    if stance in _TRADING_AGENT_DIRECTION_MULTIPLIER and research_status != "supported":
        stance = "no_call"
    if stance == "no_call" and research_status == "supported":
        research_status = "watch"

    if stance in _TRADING_AGENT_DIRECTION_MULTIPLIER:
        direction = stance
    else:
        direction = "avoid" if research_status == "avoid" else "watch"
    return stance, research_status, direction


def normalize_trading_agent_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return one candidate with internally consistent call semantics."""
    normalized = dict(candidate or {})
    stance, research_status, direction = _candidate_semantics(normalized)
    normalized["stance"] = stance
    normalized["research_status"] = research_status
    normalized["direction"] = direction
    normalized["evidence_limits"] = [
        _clean(item)
        for item in to_list(normalized.get("evidence_limits"))
        if _clean(item)
    ][:5]
    normalized["narrative_contract_version"] = normalized.get("narrative_contract_version") or "plain_investor_research_v2"
    return normalized


def _fallback_trading_agent_suggestions(
    _context: dict[str, Any],
    *,
    reason: str,
    aql_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed when research cannot support a generated candidate."""
    return {
        "status": "unavailable",
        "narrative_contract_version": TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
        "regime_read": "",
        "portfolio_posture": "",
        "candidates": [],
        "data_gaps": [],
        "error": _clean(reason),
        "aql_agent": aql_context or {},
    }


def empty_trading_agent_run_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_RUN_COLUMNS)


def empty_trading_agent_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_CANDIDATE_COLUMNS)


def empty_trading_agent_research_package_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADING_AGENT_RESEARCH_PACKAGE_COLUMNS)


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
    research_candidate_limit: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Build one horizon's Trading Agent context from materialized datasets."""
    from .market_opportunity import select_market_opportunity_feed

    output_limit = max(int(max_candidates), 1)
    scout_limit = max(int(research_candidate_limit or output_limit), output_limit)
    source_feed = opportunity_feed if isinstance(opportunity_feed, pd.DataFrame) else pd.DataFrame()
    all_signal_ranking = {
        "all_signal_rank",
        "all_signal_score",
    }.issubset(source_feed.columns)
    retrieval_rank_column = "all_signal_rank" if all_signal_ranking else "horizon_retrieval_rank"
    has_retrieval_trace = {
        "retrieval_method",
        retrieval_rank_column,
    }.issubset(source_feed.columns)

    selected_feed = select_market_opportunity_feed(
        source_feed,
        business_filter=business_filter,
        selected_horizon_col=horizon_col,
        require_horizon_metric=True,
        limit=len(source_feed) if has_retrieval_trace else scout_limit * 4,
    )
    retrieval_depth = scout_limit * 4
    if has_retrieval_trace and not selected_feed.empty:
        horizon_ranks = pd.to_numeric(selected_feed[retrieval_rank_column], errors="coerce")
        horizon_retrieval = selected_feed[horizon_ranks.le(retrieval_depth)].copy()
        if len(horizon_retrieval) >= scout_limit:
            horizon_retrieval["_opportunity_score"] = pd.to_numeric(
                horizon_retrieval.get("opportunity_score"),
                errors="coerce",
            )
            horizon_retrieval["_horizon_rank"] = pd.to_numeric(
                horizon_retrieval[retrieval_rank_column],
                errors="coerce",
            )
            selected_feed = horizon_retrieval.sort_values(
                ["_opportunity_score", "_horizon_rank", "symbol"],
                ascending=[False, True, True],
                na_position="last",
            ).drop(columns=["_opportunity_score", "_horizon_rank"])
    market_summary = _latest_summary_from_frame(page_summaries, surface="Market Explorer")
    broad_summary = _latest_summary_from_frame(page_summaries, surface="Broad Economy")

    symbols = []
    if isinstance(selected_feed, pd.DataFrame) and not selected_feed.empty and "symbol" in selected_feed.columns:
        symbols = [
            _clean(symbol).upper()
            for symbol in selected_feed["symbol"].astype(str).tolist()
            if _clean(symbol)
        ][:scout_limit]
    feed_context_records = _records(
        selected_feed,
        columns=[
            "symbol",
            "company_name",
            "opportunity",
            "direction",
            "opportunity_score",
            "retrieval_method",
            "retrieval_signal_count",
            "all_signal_score",
            "all_signal_rank",
            "all_signal_family_count",
            "all_signal_domain_count",
            "all_signal_signal_count",
            "all_signal_dataset_count",
            "all_signal_coverage_ratio",
            "all_signal_family_scores_json",
            "all_signal_domain_scores_json",
            "all_signal_datasets_json",
            "scanner_opportunity_score",
            "horizon_retrieval_rank",
            "momentum_change_retrieval_rank",
            "daily_move_retrieval_rank",
            "trend_quality_retrieval_rank",
            "sparkline_3m",
            "close",
            "daily_change_pct",
            horizon_col,
            "horizon_metric_available",
            "selected_horizon_col",
            "selected_horizon_label",
            "asof_time_utc",
            "run_id",
            "momentum_roc_score",
            "trend_fit_gap",
            "details",
        ],
        limit=scout_limit * 2,
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
            "max_candidates": output_limit,
            "research_candidate_limit": scout_limit,
            "candidate_retrieval_method": (
                "all_signal_rank"
                if all_signal_ranking
                else "horizon_then_rrf_v1"
                if has_retrieval_trace
                else "materialized_rank"
            ),
            "candidate_retrieval_depth": retrieval_depth,
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


def _build_trading_agent_horizon_contexts(
    *,
    opportunity_feed: pd.DataFrame,
    page_summaries: pd.DataFrame,
    business_filter: str,
    max_candidates: int,
    research_candidate_limit: int,
    horizon_specs: tuple[dict[str, str], ...],
    generation_mode: str,
    quarterly_fundamentals: pd.DataFrame | None = None,
    asof_time_utc: object = None,
) -> list[tuple[dict[str, str], dict[str, Any]]]:
    contexts: list[tuple[dict[str, str], dict[str, Any]]] = []
    for raw_spec in horizon_specs:
        spec = {
            "key": _clean(raw_spec.get("key")) or _clean(raw_spec.get("column")),
            "column": _clean(raw_spec.get("column")) or "return_1m_pct",
            "label": _clean(raw_spec.get("label")) or _clean(raw_spec.get("key")) or _clean(raw_spec.get("column")),
        }
        context, _selected_feed = build_trading_agent_context(
            opportunity_feed=opportunity_feed,
            page_summaries=page_summaries,
            horizon_key=spec["key"],
            horizon_col=spec["column"],
            horizon_label=spec["label"],
            business_filter=business_filter,
            max_candidates=max_candidates,
            research_candidate_limit=research_candidate_limit,
        )
        context["controls"]["generation_mode"] = _clean(generation_mode)
        start = pd.Timestamp(_utc_iso(asof_time_utc))
        context["forecast_window"] = {
            "horizon": spec["label"], "start_utc": start.isoformat(),
            "end_utc": (start + pd.Timedelta(days=_TRADING_AGENT_HORIZON_DAYS[spec["key"]])).isoformat(),
        }
        contexts.append((spec, context))
    symbols = list(dict.fromkeys(item["ticker"] for _, context in contexts for item in context["ticker_evidence"]))
    financials = fundamental_snapshots_by_symbol(quarterly_fundamentals, symbols, asof_time_utc=asof_time_utc)
    for _, context in contexts:
        for item in context["ticker_evidence"]:
            snapshot = dict(financials.get(item["ticker"]) or {})
            trends = _json_object_from_text(snapshot.pop("financial_trends_json", ""))
            item["financials"] = {**snapshot, "history": trends, "source": "quarterly_fundamentals"} if snapshot else {}
    return contexts


def _materialize_trading_agent_results(
    *,
    horizon_contexts: list[tuple[dict[str, str], dict[str, Any]]],
    result_builder: Callable[[dict[str, Any]], dict[str, Any]],
    run_id: str,
    asof_time_utc: object,
    business_filter: str,
    max_candidates: int,
    fallback_on_timeout: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    run_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    clean_run_id = _clean(run_id) or f"trading_agent_{hashlib.sha256(_utc_iso(asof_time_utc).encode('utf-8')).hexdigest()[:12]}"
    asof_iso = _utc_iso(asof_time_utc)
    generated_iso = datetime.now(timezone.utc).isoformat()
    candidate_limit = max(int(max_candidates), 1)

    for spec, context in horizon_contexts:
        horizon_key = spec["key"]
        horizon_col = spec["column"]
        horizon_label = spec["label"]
        trading_agent_run_id = f"{clean_run_id}:{horizon_key}"
        try:
            result = result_builder(context)
        except Exception as exc:
            error_text = f"Trading Agent materialization failed: {type(exc).__name__}: {exc}"
            if fallback_on_timeout and _looks_like_timeout_error(error_text):
                result = _fallback_trading_agent_suggestions(context, reason=error_text)
            else:
                result = {
                    "status": "error",
                    "regime_read": "",
                    "portfolio_posture": "",
                    "candidates": [],
                    "data_gaps": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if not isinstance(result, dict):
            result = {
                "status": "error",
                "regime_read": "",
                "portfolio_posture": "",
                "candidates": [],
                "data_gaps": [],
                "error": "Invalid Trading Agent payload.",
            }
        candidates = [
            normalize_trading_agent_candidate(item)
            for item in to_list(result.get("candidates"))
            if isinstance(item, dict)
        ]
        result = {**result, "candidates": candidates}
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
                    "stance": _clean(candidate.get("stance")) or "no_call",
                    "research_status": _clean(candidate.get("research_status")) or "insufficient_evidence",
                    "setup": _clean(candidate.get("setup")),
                    "hypothesis": _clean(candidate.get("hypothesis")),
                    "evidence_json": _json_text([_clean(item) for item in to_list(candidate.get("evidence")) if _clean(item)]),
                    "invalidation": _clean(candidate.get("invalidation")),
                    "tail_risks_json": _json_text([_clean(item) for item in to_list(candidate.get("tail_risks")) if _clean(item)]),
                    "evidence_limits_json": _json_text(
                        [_clean(item) for item in to_list(candidate.get("evidence_limits")) if _clean(item)]
                    ),
                    "suggested_horizon": _clean(candidate.get("suggested_horizon")),
                    "confidence": _clean(candidate.get("confidence")) or "low",
                    "decision_status": (
                        "open"
                        if _clean(candidate.get("stance")) in _TRADING_AGENT_DIRECTION_MULTIPLIER
                        else "research_only"
                    ),
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


def build_trading_agent_materialized_frames(
    *,
    opportunity_feed: pd.DataFrame,
    page_summaries: pd.DataFrame,
    llm_client: Any | None,
    run_id: str,
    asof_time_utc: object,
    quarterly_fundamentals: pd.DataFrame | None = None,
    business_filter: str = "All Market",
    max_candidates: int = 4,
    horizon_specs: tuple[dict[str, str], ...] = TRADING_AGENT_HORIZON_SPECS,
    suggestion_builder: Callable[..., dict[str, Any]] | None = None,
    aql_agent_runner: AgentRunner | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the legacy per-horizon Trading Agent for operational rollback."""
    candidate_limit = max(int(max_candidates), 1)
    contexts = _build_trading_agent_horizon_contexts(
        opportunity_feed=opportunity_feed,
        page_summaries=page_summaries,
        business_filter=business_filter,
        max_candidates=candidate_limit,
        research_candidate_limit=candidate_limit,
        horizon_specs=horizon_specs,
        generation_mode="legacy_per_horizon_v1",
        quarterly_fundamentals=quarterly_fundamentals, asof_time_utc=asof_time_utc,
    )
    builder = suggestion_builder or build_trading_agent_suggestions
    return _materialize_trading_agent_results(
        horizon_contexts=contexts,
        result_builder=lambda context: builder(
            context=context,
            llm_client=llm_client,
            aql_agent_runner=aql_agent_runner,
        ),
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        business_filter=business_filter,
        max_candidates=candidate_limit,
        fallback_on_timeout=True,
    )


def _prepare_trading_agent_price_history(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "timestamp", "close"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame[[column for column in ("symbol", "ticker", "timestamp", "close") if column in frame]].copy()
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
    observed = [_utc_timestamp(row.get(key)) for key in ("asof_time_utc", "generated_at_utc")]
    return max((value for value in observed if value is not None), default=None)


def _scoring_direction(*, stance: object, direction: object) -> str:
    clean_stance = _clean(stance).lower()
    if clean_stance in _TRADING_AGENT_DIRECTION_MULTIPLIER:
        return clean_stance
    clean_direction = _clean(direction).lower()
    if clean_direction in _TRADING_AGENT_DIRECTION_MULTIPLIER:
        return clean_direction
    return "no_call"


def _aligned_price_return(
    prices: pd.DataFrame,
    *,
    entry_ts: object,
    exit_ts: object,
) -> float | Any:
    if not isinstance(prices, pd.DataFrame) or prices.empty:
        return pd.NA
    entry_day = pd.to_datetime(entry_ts, utc=True, errors="coerce")
    exit_day = pd.to_datetime(exit_ts, utc=True, errors="coerce")
    if pd.isna(entry_day) or pd.isna(exit_day):
        return pd.NA
    aligned = prices.copy()
    aligned["_bar_day"] = aligned["timestamp"].dt.normalize()
    entry_candidates = aligned[aligned["_bar_day"] == entry_day.normalize()]
    exit_candidates = aligned[aligned["_bar_day"] == exit_day.normalize()]
    if entry_candidates.empty or exit_candidates.empty:
        return pd.NA
    entry_close = _numeric_or_na(entry_candidates.iloc[0].get("close"))
    exit_close = _numeric_or_na(exit_candidates.iloc[0].get("close"))
    if pd.isna(entry_close) or pd.isna(exit_close) or float(entry_close) == 0.0:
        return pd.NA
    return ((float(exit_close) / float(entry_close)) - 1.0) * 100.0


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
    benchmark_symbol: str = "SPY",
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

    from .trading_call_tracking import completed_daily_prices
    evaluation_iso = _utc_iso(evaluation_time_utc)
    prepared_prices = completed_daily_prices(_prepare_trading_agent_price_history(price_history), evaluation_iso)
    prices_by_symbol = {
        str(symbol): symbol_prices.drop(columns=["symbol"]).reset_index(drop=True)
        for symbol, symbol_prices in prepared_prices.groupby("symbol", sort=False)
    }
    benchmark_ticker = _clean(benchmark_symbol).upper() or "SPY"
    benchmark_prices = prices_by_symbol.get(
        benchmark_ticker,
        pd.DataFrame(columns=["timestamp", "close"]),
    ).copy()
    row_items: list[dict[str, Any]] = []

    for _, row in candidate_frame.iterrows():
        ticker = _clean(row.get("ticker")).upper()
        anchor_ts = _candidate_anchor_ts(row)
        anchor_day = anchor_ts.normalize() if anchor_ts is not None else None
        target_ts = ""
        if anchor_day is not None:
            target_ts = (anchor_day + pd.Timedelta(days=_horizon_days(row))).isoformat()

        stance, research_status, direction = _candidate_semantics(row.to_dict())
        scoring_direction = _scoring_direction(stance=stance, direction=direction)
        direction_multiplier = _TRADING_AGENT_DIRECTION_MULTIPLIER.get(scoring_direction)
        ticker_prices = prices_by_symbol.get(ticker, pd.DataFrame(columns=["timestamp", "close"])).copy()
        source_min_ts = _maybe_iso(ticker_prices["timestamp"].min()) if not ticker_prices.empty else ""
        source_max_ts = _maybe_iso(ticker_prices["timestamp"].max()) if not ticker_prices.empty else ""

        entry_ts = ""
        entry_close: float | Any = pd.NA
        actual_exit_ts = ""
        exit_close: float | Any = pd.NA
        raw_return_pct: float | Any = pd.NA
        signed_return_pct: float | Any = pd.NA
        benchmark_return_pct: float | Any = pd.NA
        excess_signed_return_pct: float | Any = pd.NA
        outcome_status = "not_scoreable" if direction_multiplier is None else "missing_price"
        is_win: bool | Any = pd.NA
        is_matured = False

        if direction_multiplier is None:
            pass
        elif anchor_day is None:
            outcome_status = "missing_entry"
        elif not ticker_prices.empty:
            ticker_prices["_bar_day"] = ticker_prices["timestamp"].dt.normalize()
            call_day = anchor_ts.tz_convert("America/New_York").strftime("%Y-%m-%d")
            entry_candidates = ticker_prices[ticker_prices["session_date"] > call_day].copy()
            if entry_candidates.empty:
                outcome_status = "missing_entry"
            else:
                entry = entry_candidates.iloc[0]
                entry_ts = _maybe_iso(entry.get("timestamp"))
                entry_close = _numeric_or_na(entry.get("close"))
                target_ts = (pd.Timestamp(entry.get("session_date")) + pd.Timedelta(days=_horizon_days(row))).tz_localize("America/New_York").tz_convert("UTC").isoformat()
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
                        benchmark_return_pct = _aligned_price_return(
                            benchmark_prices,
                            entry_ts=entry_ts,
                            exit_ts=actual_exit_ts,
                        )
                        if not pd.isna(benchmark_return_pct):
                            excess_signed_return_pct = (
                                (float(raw_return_pct) - float(benchmark_return_pct))
                                * direction_multiplier
                            )

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
                "stance": stance,
                "research_status": research_status,
                "scoring_direction": scoring_direction,
                "setup": _clean(row.get("setup")),
                "hypothesis": _clean(row.get("hypothesis")),
                "confidence": _clean(row.get("confidence")),
                "suggested_horizon": _clean(row.get("suggested_horizon")),
                "evidence_json": _clean(row.get("evidence_json")),
                "invalidation": _clean(row.get("invalidation")),
                "tail_risks_json": _clean(row.get("tail_risks_json")),
                "evidence_limits_json": _clean(row.get("evidence_limits_json")),
                "candidate_generated_at_utc": _maybe_iso(row.get("generated_at_utc")),
                "candidate_asof_time_utc": _maybe_iso(row.get("asof_time_utc")),
                "entry_ts": entry_ts,
                "entry_close": entry_close,
                "target_exit_ts": target_ts,
                "actual_exit_ts": actual_exit_ts,
                "exit_close": exit_close,
                "raw_return_pct": raw_return_pct,
                "signed_return_pct": signed_return_pct,
                "benchmark_return_pct": benchmark_return_pct,
                "excess_signed_return_pct": excess_signed_return_pct,
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
    evidence_limits = _json_array_from_text(
        candidate.get("evidence_limits_json") or outcome.get("evidence_limits_json")
    )
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
            "evidence_limits": [_clean(item) for item in evidence_limits if _clean(item)],
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
        from .aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=f"Review this Trading Agent call as a research-quality post-mortem. Return only the schema fields and evaluate the original call.\n\nReview input JSON:\n{_compact_json(review_input, limit=12000)}",
            instructions=_TRADING_AGENT_REVIEW_SYSTEM_PROMPT,
            schema_name="trading_agent_research_review",
            reuse_scope=current_research_access_scope(),
            schema=_TRADING_AGENT_REVIEW_SCHEMA,
            task="research_review", stage="review",
            surface="trading_agent.review",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="research_grade",
            operation_metadata={
                "candidate_id": (
                    _clean((review_input.get("candidate") or {}).get("candidate_id"))
                    if isinstance(review_input.get("candidate"), dict)
                    else ""
                ),
                "ticker": (
                    _clean((review_input.get("outcome") or {}).get("ticker"))
                    if isinstance(review_input.get("outcome"), dict)
                    else ""
                ),
                "horizon_key": (
                    _clean((review_input.get("outcome") or {}).get("horizon_key"))
                    if isinstance(review_input.get("outcome"), dict)
                    else ""
                ),
            },
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        payload = engine_operation["payload"]
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


def _looks_like_empty_model_response(text: object) -> bool:
    lowered = _clean(text).lower()
    return "returned empty content" in lowered or "empty model response" in lowered


def _looks_like_timeout_error(text: object) -> bool:
    lowered = _clean(text).lower()
    return "timeout" in lowered or "timed out" in lowered or "exceeded" in lowered


def _safe_error_text(text: object, *, fallback: str = "The model rejected the generated research prompt.") -> str:
    clean = _clean(text)
    if not clean:
        return fallback
    if _looks_like_provider_policy_error(clean):
        return "Research generation was rejected by the model."
    if _looks_like_empty_model_response(clean):
        return "Research model returned no answer."
    if _looks_like_transient_transport_error(clean):
        return "Research source connection failed."
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
    prompt_context = _research_prompt_context(context, ticker_context)
    return (
        "Use the shared AQL research tool harness to build one grounded Trading Agent research package. "
        f"Focus ticker: {identity_line}. Research this ticker only; use peers, sector, or macro only when they directly explain this ticker. "
        "Zopedia pages and retained context are useful memory when present, but their absence is not a failure; "
        "ground the research with the supplied market observation, company context, filings, fundamentals, recent news, and source search as available. "
        "Develop competing forward-looking bullish and bearish hypotheses for the requested horizons, with the business mechanism, "
        "supporting facts, assumptions, counterevidence and observable invalidation. State your preferred price direction for each requested horizon and why, even when the balance is close. "
        "This is research, not portfolio allocation or trade execution. An unresolved cause of a past price move, incomplete coverage, "
        "or missing long-term price history does not by itself rule out a useful forward-looking thesis. "
        "Uncertainty belongs in the strength of the forecast and its assumptions, not in a refusal to choose. "
        "Separate established facts from estimated scenarios. Assess what today's price already expects: a good business can still be overpriced. "
        "Compare like reporting periods, distinguish recurring operations from financing and one-off items, and explain how a development changes per-share value before the horizon ends. "
        "Check whether each invalidation actually opposes its thesis and applies to that horizon. Use a numerical threshold only when its economic basis can be explained. "
        "Identify the strongest competing mechanism and the observation most likely to change your preferred direction. "
        "Do not exhaust the budget filling every missing source. Verify company identity, "
        "gather high-signal business/current evidence when available, then write what is supported and name exact gaps. "
        "Write for an investor: discuss the company, catalyst, price move, and business facts, not the app or its research machinery. "
        "If a number has no useful definition or comparison, omit it from the analysis. Write limitations as forward-looking research questions or facts to obtain next, "
        "not as a description of the input or its collection. Never present missing information as an investment risk. "
        "Use exact dates from evidence when available; do not infer weekdays. "
        "Prefer unknown over unsupported claims.\n\n"
        "Single-company research brief JSON:\n"
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
    max_tool_calls: int = 4,
    attempts: int = 1,
    persist_findings: bool = False,
    tool_call_timeout_seconds: int = 45,
    llm_step_timeout_seconds: int = 90,
    prefetch_timeout_seconds: int = 15,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, max(int(attempts), 1) + 1):
        try:
            return runner(
                query=query,
                force_refresh=False,
                max_tool_calls=max(int(max_tool_calls), 0),
                llm_client=llm_client,
                persist_findings=bool(persist_findings),
                write_policy=_trading_agent_write_policy(),
                tool_call_timeout_seconds=max(int(tool_call_timeout_seconds), 1),
                llm_step_timeout_seconds=max(int(llm_step_timeout_seconds), 1),
                prefetch_timeout_seconds=max(int(prefetch_timeout_seconds), 1),
            )
        except Exception as exc:
            last_exc = exc
            error_text = f"{type(exc).__name__}: {exc}"
            retryable = _looks_like_transient_transport_error(
                error_text
            ) or _looks_like_empty_model_response(error_text)
            if attempt >= max(int(attempts), 1) or not retryable:
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
        "financials": ticker_context.get("financials") or {},
        "horizon_keys": [_clean(item) for item in to_list(ticker_context.get("horizon_keys")) if _clean(item)],
        "horizon_labels": [_clean(item) for item in to_list(ticker_context.get("horizon_labels")) if _clean(item)],
        "selection_trace": ticker_context.get("selection_trace")
        if isinstance(ticker_context.get("selection_trace"), dict)
        else {},
        "horizon_opportunities": [
            item for item in to_list(ticker_context.get("horizon_opportunities")) if isinstance(item, dict)
        ],
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
        "financials": ticker_context.get("financials") or {},
        "horizon_keys": [_clean(item) for item in to_list(ticker_context.get("horizon_keys")) if _clean(item)],
        "horizon_labels": [_clean(item) for item in to_list(ticker_context.get("horizon_labels")) if _clean(item)],
        "selection_trace": ticker_context.get("selection_trace")
        if isinstance(ticker_context.get("selection_trace"), dict)
        else {},
        "horizon_opportunities": [
            item for item in to_list(ticker_context.get("horizon_opportunities")) if isinstance(item, dict)
        ],
    }


def _run_ticker_aql_packages(
    runner: AgentRunner,
    *,
    context: dict[str, Any],
    ticker_contexts: list[dict[str, Any]],
    llm_client: Any,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if not ticker_contexts:
        return []
    controls = context.get("controls") if isinstance(context, dict) else {}
    if not isinstance(controls, dict):
        controls = {}
    parallelism = max(int(controls.get("ticker_aql_parallelism") or 2), 1)
    try:
        package_tool_budget = int(controls.get("ticker_aql_max_tool_calls") or 4)
    except Exception:
        package_tool_budget = 4
    package_tool_budget = min(max(package_tool_budget, 1), 16)
    try:
        attempts = max(int(controls.get("ticker_aql_attempts") or 1), 1)
    except Exception:
        attempts = 1
    try:
        tool_timeout_seconds = max(int(controls.get("ticker_aql_tool_timeout_seconds") or 45), 1)
    except Exception:
        tool_timeout_seconds = 45
    try:
        llm_timeout_seconds = max(int(controls.get("ticker_aql_llm_step_timeout_seconds") or 90), 1)
    except Exception:
        llm_timeout_seconds = 90
    try:
        prefetch_timeout_seconds = max(int(controls.get("ticker_aql_prefetch_timeout_seconds") or 15), 1)
    except Exception:
        prefetch_timeout_seconds = 15
    persist_findings = controls.get("persist_findings") is True
    max_workers = min(parallelism, len(ticker_contexts))

    def run_one(index: int, ticker_context: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            result = _run_aql_with_retry(
                runner,
                query=_aql_ticker_query(context, ticker_context),
                llm_client=llm_client,
                max_tool_calls=package_tool_budget,
                attempts=attempts,
                persist_findings=persist_findings,
                tool_call_timeout_seconds=tool_timeout_seconds,
                llm_step_timeout_seconds=llm_timeout_seconds,
                prefetch_timeout_seconds=prefetch_timeout_seconds,
            )
            return index, _ticker_package_from_result(ticker_context=ticker_context, result=result)
        except Exception as exc:
            safe_message = _safe_error_text(f"AQL agent failed: {type(exc).__name__}: {exc}")
            return index, _failed_ticker_package(ticker_context=ticker_context, error=safe_message)

    packages: list[dict[str, Any] | None] = [None] * len(ticker_contexts)
    completed_count = 0
    if max_workers <= 1:
        for index, ticker_context in enumerate(ticker_contexts):
            result_index, package = run_one(index, ticker_context)
            packages[result_index] = package
            completed_count += 1
            if progress_callback is not None:
                progress_callback(completed_count, len(ticker_contexts), package)
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="trading-agent-research") as executor:
            futures = {
                executor.submit(run_one, index, ticker_context): index
                for index, ticker_context in enumerate(ticker_contexts)
            }
            for future in as_completed(futures):
                result_index, package = future.result()
                packages[result_index] = package
                completed_count += 1
                if progress_callback is not None:
                    progress_callback(completed_count, len(ticker_contexts), package)
    return [package for package in packages if isinstance(package, dict)]


def _balanced_shared_research_pool(
    horizon_contexts: list[tuple[dict[str, str], dict[str, Any]]],
    *,
    research_pool_limit: int,
) -> list[dict[str, Any]]:
    limit = max(int(research_pool_limit), 1)
    per_horizon = [
        (spec, _ticker_contexts_from_context(context))
        for spec, context in horizon_contexts
    ]
    ranking: dict[str, dict[str, Any]] = {}
    max_depth = max((len(items) for _spec, items in per_horizon), default=0)
    for spec, ticker_contexts in per_horizon:
        for position, ticker_context in enumerate(ticker_contexts, start=1):
            ticker = _clean(ticker_context.get("ticker")).upper()
            if not ticker:
                continue
            record = ranking.setdefault(
                ticker,
                {
                    "horizon_ranks": {},
                    "rrf_score": 0.0,
                    "best_opportunity_score": float("-inf"),
                },
            )
            record["horizon_ranks"][spec["key"]] = position
            record["rrf_score"] += 1.0 / (60.0 + position)
            opportunity = ticker_context.get("market_opportunity")
            raw_score = opportunity.get("opportunity_score") if isinstance(opportunity, dict) else None
            parsed_score = pd.to_numeric(pd.Series([raw_score]), errors="coerce").iloc[0]
            if not pd.isna(parsed_score):
                record["best_opportunity_score"] = max(
                    float(record["best_opportunity_score"]),
                    float(parsed_score),
                )

    selected_symbols: list[str] = []
    selected_set: set[str] = set()
    for rank_index in range(max_depth):
        rank_tier = {
            _clean(ticker_contexts[rank_index].get("ticker")).upper()
            for _spec, ticker_contexts in per_horizon
            if rank_index < len(ticker_contexts)
        }
        eligible = [ticker for ticker in rank_tier if ticker and ticker not in selected_set]
        eligible.sort(
            key=lambda ticker: (
                -float(ranking[ticker]["rrf_score"]),
                -float(ranking[ticker]["best_opportunity_score"]),
                ticker,
            )
        )
        for ticker in eligible[: max(limit - len(selected_symbols), 0)]:
            selected_set.add(ticker)
            selected_symbols.append(ticker)
        if len(selected_symbols) >= limit:
            break

    merged: dict[str, dict[str, Any]] = {}
    for spec, ticker_contexts in per_horizon:
        for ticker_context in ticker_contexts:
            ticker = _clean(ticker_context.get("ticker")).upper()
            if ticker not in selected_set:
                continue
            if ticker not in merged:
                merged[ticker] = {
                    **ticker_context,
                    "ticker": ticker,
                    "horizon_keys": [],
                    "horizon_labels": [],
                    "horizon_opportunities": [],
                    "selection_trace": {},
                }
            package_seed = merged[ticker]
            if spec["key"] not in package_seed["horizon_keys"]:
                package_seed["horizon_keys"].append(spec["key"])
            if spec["label"] not in package_seed["horizon_labels"]:
                package_seed["horizon_labels"].append(spec["label"])
            opportunity = ticker_context.get("market_opportunity")
            package_seed["horizon_opportunities"].append(
                {
                    "horizon_key": spec["key"],
                    "horizon_label": spec["label"],
                    "selected_horizon_col": spec["column"],
                    "scout_rank": ranking[ticker]["horizon_ranks"].get(spec["key"]),
                    "market_opportunity": opportunity if isinstance(opportunity, dict) else {},
                }
            )
            if not package_seed.get("company_name") and ticker_context.get("company_name"):
                package_seed["company_name"] = ticker_context["company_name"]
            if not package_seed.get("stock_summary") and ticker_context.get("stock_summary"):
                package_seed["stock_summary"] = ticker_context["stock_summary"]
    for pool_rank, ticker in enumerate(selected_symbols, start=1):
        if ticker not in merged:
            continue
        record = ranking[ticker]
        merged[ticker]["selection_trace"] = {
            "method": "horizon_tier_rrf_v1",
            "pool_rank": pool_rank,
            "rrf_score": round(float(record["rrf_score"]), 8),
            "horizon_ranks": record["horizon_ranks"],
        }
    return [merged[ticker] for ticker in selected_symbols if ticker in merged]


def _shared_research_context(
    horizon_contexts: list[tuple[dict[str, str], dict[str, Any]]],
    *,
    ticker_contexts: list[dict[str, Any]],
    research_controls: dict[str, Any],
) -> dict[str, Any]:
    first_context = horizon_contexts[0][1] if horizon_contexts else {}
    source_summaries = first_context.get("source_summaries") if isinstance(first_context, dict) else {}
    if not isinstance(source_summaries, dict):
        source_summaries = {}
    return {
        "philosophy": first_context.get("philosophy") if isinstance(first_context, dict) else [],
        "controls": {
            "generation_mode": "shared_research_v2",
            "research_scope": "cross_horizon_company_package",
            "max_candidates": len(ticker_contexts),
            "research_candidate_limit": len(ticker_contexts),
            "include_macro": True,
            "persist_findings": False,
            **research_controls,
        },
        "source_summaries": source_summaries,
        "forecast_windows": [context.get("forecast_window") or {} for _, context in horizon_contexts],
        "market_opportunity_feed": [
            item.get("market_opportunity")
            for item in ticker_contexts
            if isinstance(item.get("market_opportunity"), dict)
        ],
        "ticker_evidence": ticker_contexts,
    }


def _research_packages_for_horizon(
    context: dict[str, Any],
    ticker_packages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    horizon_ticker_contexts = _ticker_contexts_from_context(context)
    package_lookup = {
        _clean(package.get("ticker")).upper(): package
        for package in ticker_packages
        if isinstance(package, dict) and _clean(package.get("ticker"))
    }
    selected_contexts: list[dict[str, Any]] = []
    selected_packages: list[dict[str, Any]] = []
    for ticker_context in horizon_ticker_contexts:
        ticker = _clean(ticker_context.get("ticker")).upper()
        package = package_lookup.get(ticker)
        if (
            package is None
            or _clean(package.get("status")).lower() != "completed"
            or not _clean(package.get("answer_markdown"))
        ):
            continue
        selected_contexts.append(ticker_context)
        selected_packages.append(package)
    return selected_contexts, selected_packages


def _research_package_frame(
    ticker_packages: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: object,
) -> pd.DataFrame:
    clean_run_id = _clean(run_id)
    asof_iso = _utc_iso(asof_time_utc)
    generated_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for package in ticker_packages:
        ticker = _clean(package.get("ticker")).upper()
        if not ticker:
            continue
        rows.append(
            {
                "research_package_id": f"tarp_{_stable_digest({'run_id': clean_run_id, 'ticker': ticker})}",
                "run_id": clean_run_id,
                "ticker": ticker,
                "company_name": _clean(package.get("company_name")),
                "status": _clean(package.get("status")) or "failed",
                "confidence": _clean(package.get("confidence")) or "low",
                "answer_markdown": _clean(package.get("answer_markdown")),
                "limitations_json": _json_text(
                    [_clean(item) for item in to_list(package.get("limitations")) if _clean(item)]
                ),
                "tool_calls_json": _json_text(
                    [item for item in to_list(package.get("tool_calls")) if isinstance(item, dict)]
                ),
                "evidence_pack_id": _clean(package.get("evidence_pack_id")),
                "horizon_keys_json": _json_text(
                    [_clean(item) for item in to_list(package.get("horizon_keys")) if _clean(item)]
                ),
                "horizon_labels_json": _json_text(
                    [_clean(item) for item in to_list(package.get("horizon_labels")) if _clean(item)]
                ),
                "selection_trace_json": _json_text(
                    package.get("selection_trace") if isinstance(package.get("selection_trace"), dict) else {}
                ),
                "opportunity_context_json": _json_text(
                    [item for item in to_list(package.get("horizon_opportunities")) if isinstance(item, dict)]
                ),
                "error": _clean(package.get("error")),
                "asof_time_utc": asof_iso,
                "generated_at_utc": generated_iso,
            }
        )
    frame = pd.DataFrame(rows, columns=TRADING_AGENT_RESEARCH_PACKAGE_COLUMNS)
    return frame if not frame.empty else empty_trading_agent_research_package_frame()


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


def _synthesize_trading_agent_suggestions(
    *,
    context: dict[str, Any],
    ticker_contexts: list[dict[str, Any]],
    ticker_packages: list[dict[str, Any]],
    llm_client: Any | None,
    allow_timeout_fallback: bool,
) -> dict[str, Any]:
    if llm_client is None:
        return {
            "status": "unavailable",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [],
            "error": "LLM runtime is not configured.",
        }

    normalized_context = context or {}
    if not ticker_contexts:
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [],
            "error": "Trading Agent context did not include any focus tickers.",
        }

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
        if allow_timeout_fallback and any(_looks_like_timeout_error(item) for item in limitations):
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
            "data_gaps": [],
            "error": error_text or "AQL agent did not produce grounded trading synthesis.",
            "aql_agent": aql_context,
        }

    synthesis_context = _synthesis_prompt_context(
        normalized_context,
        ticker_contexts,
        ticker_packages,
    )
    user_prompt = (
        "Investor research brief JSON:\n"
        f"{_compact_json(synthesis_context)}\n\n"
        "Rank up to the existing maximum number of companies as explicit long or short calls for the exact requested horizon. "
        "Use the completed company findings as the factual boundary and the market observations for the reference price; earlier reports can describe older prices. "
        "Earlier watch-only conclusions are not binding. Reassess their facts, pricing and competing arguments and choose your preferred direction even with low confidence. "
        "Explain what is already reflected in price, the incremental business or market change that supports the call, and why it can matter within this horizon. "
        "Use the explicit forecast_window dates. An event after end_utc cannot be this call's within-window trigger or invalidation; distinguish later research follow-up. "
        "State your critical assumptions and the strongest opposing case in forecast, instead of inventing numerical forecasts when valuation inputs are unknown. "
        "Hypothesis states the preferred forward outcome, invalidation must oppose that outcome, and tail risks must hurt the chosen position rather than reinforce it. "
        "Keep the brief concise and substantive: two short sentences per prose field, up to three distinct items per list. "
        "Missing historical returns or an unresolved explanation for a past move do not alone veto a forward-looking call; failed research and unresolved business identity remain failures. "
        "Write the backdrop and shared exposures from the calls actually returned, without assuming any are approved or held. "
        "Retain exact observation dates where known. A snapshot availability timestamp is not the trading session date of the last observed close."
    )
    try:
        from .aql_zopedia_engine import run_aql_zopedia_structured_agent

        engine_operation = run_aql_zopedia_structured_agent(
            query=user_prompt,
            instructions=get_prompt(_TRADING_AGENT_SYSTEM_PROMPT),
            schema_name="trading_agent_experiment",
            reuse_scope=current_research_access_scope(),
            schema=_TRADING_AGENT_SCHEMA,
            task="candidate_synthesis", stage='synthesis',
            surface="trading_agent.synthesis",
            llm_client=llm_client,
            max_tool_calls=0,
            persist_findings=False,
            write_policy="none",
            call_type="formatter_over_aql",
            operation_metadata={
                "horizon_label": (
                    _clean(
                        (normalized_context.get("controls") or {}).get(
                            "selected_horizon_label"
                        )
                    )
                    if isinstance(normalized_context.get("controls"), dict)
                    else ""
                ),
                "horizon_column": (
                    _clean(
                        (normalized_context.get("controls") or {}).get(
                            "selected_horizon_col"
                        )
                    )
                    if isinstance(normalized_context.get("controls"), dict)
                    else ""
                ),
                "ticker_count": len(ticker_contexts),
                "aql_status": _clean(aql_context.get("status")),
            },
        )
        if engine_operation.get("status") != "completed":
            raise RuntimeError(
                engine_operation.get("error") or "Shared AI operation failed"
            )
        payload = engine_operation["payload"]
    except LLMAPIError as exc:
        safe_message = _safe_error_text(exc)
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [],
            "error": safe_message,
        }
    except Exception as exc:
        return {
            "status": "error",
            "regime_read": "",
            "portfolio_posture": "",
            "candidates": [],
            "data_gaps": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    contexts_by_ticker = {_clean(item.get("ticker")).upper(): item for item in ticker_contexts if _clean(item.get("ticker"))}
    allowed_tickers = set(contexts_by_ticker)
    candidates: list[dict[str, Any]] = []
    for item in to_list(payload.get("candidates")):
        if not isinstance(item, dict):
            continue
        ticker = _clean(item.get("ticker")).upper()
        if not ticker:
            continue
        if allowed_tickers and ticker not in allowed_tickers:
            continue
        stance = _clean(item.get("stance")).lower()
        reference = _market_observation_for_prompt(contexts_by_ticker[ticker].get("market_opportunity"))
        forecast = {
            **(item.get("forecast") or {}),
            "reference_price": reference.get("reference_price"),
            "reference_snapshot_as_of": reference.get("as_of") or "",
            "reference_price_basis": reference.get("reference_price_basis") or "",
            "start_at_utc": (normalized_context.get("forecast_window") or {}).get("start_utc"),
            "end_at_utc": (normalized_context.get("forecast_window") or {}).get("end_utc"),
        }
        candidates.append(
            normalize_trading_agent_candidate({
                "narrative_contract_version": TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
                "ticker": ticker,
                "stance": stance,
                "forecast": forecast,
                "setup": _clean(item.get("setup")),
                "hypothesis": _clean(item.get("hypothesis")),
                "evidence": [_clean(value) for value in to_list(item.get("evidence")) if _clean(value)][:5],
                "invalidation": _clean(item.get("invalidation")),
                "tail_risks": [_clean(value) for value in to_list(item.get("tail_risks")) if _clean(value)][:5],
                "evidence_limits": [
                    _clean(value)
                    for value in to_list(item.get("evidence_limits"))
                    if _clean(value)
                ][:5],
                "suggested_horizon": _clean(item.get("suggested_horizon")),
                "confidence": _clean(item.get("confidence")) or "low",
            })
        )

    data_gaps = []
    for item in [_clean(item) for item in to_list(payload.get("data_gaps")) if _clean(item)]:
        _append_distinct_text(data_gaps, item)

    return {
        "status": "ok",
        "narrative_contract_version": TRADING_AGENT_NARRATIVE_CONTRACT_VERSION,
        "regime_read": _clean(payload.get("regime_read")),
        "portfolio_posture": _clean(payload.get("portfolio_posture")),
        "candidates": candidates[
            : max(
                int((normalized_context.get("controls") or {}).get("max_candidates") or 4)
                if isinstance(normalized_context.get("controls"), dict)
                else 4,
                1,
            )
        ],
        "data_gaps": data_gaps[:6],
        "error": "",
        "aql_agent": aql_context,
    }


def build_trading_agent_suggestions(
    *,
    context: dict[str, Any],
    llm_client: Any | None,
    aql_agent_runner: AgentRunner | None = None,
) -> dict[str, Any]:
    """Legacy per-horizon research and synthesis path kept for rollback."""
    normalized_context = dict(context or {})
    legacy_controls = dict(normalized_context.get("controls") or {})
    legacy_controls.setdefault("ticker_aql_attempts", 2)
    normalized_context["controls"] = legacy_controls
    ticker_contexts = _ticker_contexts_from_context(normalized_context)
    runner = aql_agent_runner or _default_aql_agent_runner
    ticker_packages = _run_ticker_aql_packages(
        runner,
        context=normalized_context,
        ticker_contexts=ticker_contexts,
        llm_client=llm_client,
    ) if llm_client is not None and ticker_contexts else []
    return _synthesize_trading_agent_suggestions(
        context=normalized_context,
        ticker_contexts=ticker_contexts,
        ticker_packages=ticker_packages,
        llm_client=llm_client,
        allow_timeout_fallback=True,
    )


def build_trading_agent_shared_research_frames(
    *,
    opportunity_feed: pd.DataFrame,
    page_summaries: pd.DataFrame,
    llm_client: Any | None,
    run_id: str,
    asof_time_utc: object,
    quarterly_fundamentals: pd.DataFrame | None = None,
    business_filter: str = "All Market",
    max_candidates: int = 4,
    research_candidate_limit: int = 6,
    research_pool_limit: int = 8,
    research_parallelism: int = 2,
    research_max_tool_calls: int = 4,
    research_attempts: int = 1,
    research_tool_timeout_seconds: int = 45,
    research_llm_step_timeout_seconds: int = 90,
    research_prefetch_timeout_seconds: int = 15,
    horizon_specs: tuple[dict[str, str], ...] = TRADING_AGENT_HORIZON_SPECS,
    aql_agent_runner: AgentRunner | None = None,
    research_progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Research each unique company once, then synthesize every horizon."""
    candidate_limit = max(int(max_candidates), 1)
    scout_limit = max(int(research_candidate_limit), candidate_limit)
    horizon_contexts = _build_trading_agent_horizon_contexts(
        opportunity_feed=opportunity_feed,
        page_summaries=page_summaries,
        business_filter=business_filter,
        max_candidates=candidate_limit,
        research_candidate_limit=scout_limit,
        horizon_specs=horizon_specs,
        generation_mode="shared_research_v2",
        quarterly_fundamentals=quarterly_fundamentals, asof_time_utc=asof_time_utc,
    )
    ticker_contexts = _balanced_shared_research_pool(
        horizon_contexts,
        research_pool_limit=max(int(research_pool_limit), 1),
    )
    pool_trace = [
        {
            "ticker": item["ticker"],
            **(item.get("selection_trace") if isinstance(item.get("selection_trace"), dict) else {}),
        }
        for item in ticker_contexts
    ]
    for _spec, context in horizon_contexts:
        context["research_pool"] = {
            "method": "horizon_tier_rrf_v1",
            "limit": max(int(research_pool_limit), 1),
            "selected": pool_trace,
        }
    research_context = _shared_research_context(
        horizon_contexts,
        ticker_contexts=ticker_contexts,
        research_controls={
            "ticker_aql_parallelism": max(int(research_parallelism), 1),
            "ticker_aql_max_tool_calls": max(int(research_max_tool_calls), 1),
            "ticker_aql_attempts": max(int(research_attempts), 1),
            "ticker_aql_tool_timeout_seconds": max(int(research_tool_timeout_seconds), 1),
            "ticker_aql_llm_step_timeout_seconds": max(int(research_llm_step_timeout_seconds), 1),
            "ticker_aql_prefetch_timeout_seconds": max(int(research_prefetch_timeout_seconds), 1),
        },
    )
    runner = aql_agent_runner or _default_aql_agent_runner
    ticker_packages = _run_ticker_aql_packages(
        runner,
        context=research_context,
        ticker_contexts=ticker_contexts,
        llm_client=llm_client,
        progress_callback=research_progress_callback,
    ) if llm_client is not None and ticker_contexts else []
    package_frame = _research_package_frame(
        ticker_packages,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )

    def build_horizon_result(context: dict[str, Any]) -> dict[str, Any]:
        selected_contexts, selected_packages = _research_packages_for_horizon(context, ticker_packages)
        return _synthesize_trading_agent_suggestions(
            context=context,
            ticker_contexts=selected_contexts,
            ticker_packages=selected_packages,
            llm_client=llm_client,
            allow_timeout_fallback=False,
        )

    runs, candidates = _materialize_trading_agent_results(
        horizon_contexts=horizon_contexts,
        result_builder=build_horizon_result,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        business_filter=business_filter,
        max_candidates=candidate_limit,
        fallback_on_timeout=False,
    )
    return runs, candidates, package_frame


__all__ = [
    "TRADING_AGENT_CANDIDATE_COLUMNS",
    "TRADING_AGENT_HORIZON_SPECS",
    "TRADING_AGENT_NARRATIVE_CONTRACT_VERSION",
    "TRADING_AGENT_RESEARCH_PACKAGE_COLUMNS",
    "TRADING_AGENT_RUN_COLUMNS",
    "build_trading_agent_context",
    "build_trading_agent_materialized_frames",
    "build_trading_agent_shared_research_frames",
    "build_trading_agent_suggestions",
    "empty_trading_agent_candidate_frame",
    "empty_trading_agent_research_package_frame",
    "empty_trading_agent_run_frame",
]
