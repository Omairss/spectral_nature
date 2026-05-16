from __future__ import annotations

from typing import Any

from data_access.contracts import QueryRequest, coerce_object
from data_access.query_service import QueryService
from . import omnibar_research


def tool_schema(
    params: list[str] | tuple[str, ...],
    *,
    required: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": ["string", "number", "boolean", "object", "array", "null"]}
            for name in list(params or [])
        },
        "required": list(required or []),
        "additionalProperties": False,
    }


def _schema_from_capability(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("param_schema")
    if isinstance(schema, dict):
        return dict(schema)
    return tool_schema(
        list(spec.get("params") or []),
        required=list(spec.get("required_params") or []),
    )


def _hypothesis_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "hypothesis.verify",
            "description": (
                "Grade a hypothesis against collected evidence. Returns verdict "
                "(supported/weak/conflicting/unsupported), confidence, supporting and "
                "contradicting claims, and gap_queries — concrete search queries to fill "
                "evidence holes. Call this after collecting evidence to decide if the "
                "hypothesis is solid or needs more research."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim_text": {"type": "string"},
                                "source": {"type": "string"},
                                "is_same_day": {"type": "boolean"},
                            },
                            "required": ["claim_text"],
                        },
                    },
                    "beats": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sentence": {"type": "string"},
                                "symbols": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["sentence"],
                        },
                    },
                },
                "required": ["hypothesis", "claims"],
                "additionalProperties": False,
            },
        },
    ]


def _research_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "research.retained_context",
            "description": (
                "Look up retained narrative context already in Spectral Nature for the query. "
                "Best first tool for live analysis prompts."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_items": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.market_impact_map",
            "description": (
                "Expand a live event or macro query into likely impacted assets and spillover symbols."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_symbols": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.live_event_evidence",
            "description": (
                "Fetch fresh web evidence for the query, using event-level search and symbol-level search when relevant."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.search_evidence",
            "description": (
                "Search Spectral Nature's retained evidence store (SAA) for documents and "
                "chunks matching a query. This searches all previously collected research — "
                "news articles, event summaries, ticker backgrounds, and analysis — that "
                "have been retained from past pipeline runs and agent sessions. "
                "Use this FIRST for any question about recent market events, squeezes, "
                "earnings surprises, or themes that Spectral Nature may have already captured."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search query."},
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional ticker filter (e.g. ['CAR', 'BYND']).",
                    },
                    "max_results": {"type": "integer", "description": "Max results (default 10)."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.open_page",
            "description": (
                "Open one selected web page with Playwright when available, with a simple HTTP fallback."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    ]


def _investigator_tools() -> list[dict[str, Any]]:
    """Stock Investigator tools — technicals, forecast, company context, fundamentals, news."""
    return [
        {
            "name": "investigator.technical_signals",
            "description": (
                "Get technical signal summary for a ticker: current price, ATH, pullback "
                "from ATH, channel support/resistance/position, RSI, annualized volatility, "
                "and regime classification (e.g. Trend continuation, Deep pullback)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.forecast",
            "description": (
                "Get a Monte Carlo forecast for the next week for a ticker. Returns "
                "probability of being up, probability of breakout, median expected "
                "return, and confidence interval."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.company_context",
            "description": (
                "Get company context for a ticker: business description, recent narrative "
                "from filings/news, management signals, and what's driving attention now."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.fundamentals",
            "description": (
                "Get quarterly financial fundamentals for a ticker: income statement, "
                "balance sheet, and cash flow statement data. Returns the most recent "
                "quarters available."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.recent_news",
            "description": (
                "Get recent news headlines for a ticker. Returns the most recent articles "
                "with headline, source, date, and summary."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                    "days": {"type": "integer", "description": "Lookback days (default 14)"},
                    "limit": {"type": "integer", "description": "Max articles (default 8)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
    ]


def _anomaly_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "dataset.run_anomaly_check",
            "description": (
                "Run anomaly detection on ticker data. Returns the most significant "
                "anomaly events for the given symbols: first drop/rise date, magnitude, "
                "z-score, classification, and attention score. Uses pre-computed "
                "attention data when available, falls back to on-demand computation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of ticker symbols to check (e.g. ['ARCC', 'OBDC', 'MAIN']).",
                    },
                    "horizon": {
                        "type": "string",
                        "description": "Time horizon: 1d, 1w, 1mo, 3mo, or 1yr. Defaults to 1w.",
                    },
                },
                "required": ["symbols"],
                "additionalProperties": False,
            },
        },
    ]


def _scratchpad_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "scratchpad.write",
            "description": (
                "Write an entry to the hypothesis scratchpad. Use this to persist "
                "anomaly events, evidence claims, hypothesis drafts, and search queries "
                "between tool calls. Kind should be one of: anomaly, claim, hypothesis, "
                "search_query, note."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "content": {"type": "object"},
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "scratchpad.read",
            "description": (
                "Read entries from the hypothesis scratchpad. Optionally filter by "
                "kind (anomaly, claim, hypothesis, search_query, note) and limit to "
                "the last N entries."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": ["string", "null"]},
                    "last_n": {"type": ["integer", "null"]},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


def is_query_service_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    return name == "system.capabilities" or name.startswith("dataset.") or name.startswith("chart.")


def is_research_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("research.")


def is_hypothesis_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("hypothesis.")


def is_scratchpad_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("scratchpad.")


def is_anomaly_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() == "dataset.run_anomaly_check"


def is_investigator_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("investigator.")


def build_tool_catalog(service: QueryService) -> list[dict[str, Any]]:
    capabilities = service.list_capabilities()
    tools: list[dict[str, Any]] = [
        {
            "name": "system.capabilities",
            "description": "Return dataset and chart capability metadata.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]
    tools.extend(_research_tools())
    tools.extend(_hypothesis_tools())
    tools.extend(_scratchpad_tools())
    tools.extend(_anomaly_tools())
    tools.extend(_investigator_tools())
    for dataset_name, spec in dict(capabilities.get("datasets") or {}).items():
        desc = str(spec.get("description") or "").strip() or f"Fetch dataset '{dataset_name}'."
        tools.append(
            {
                "name": f"dataset.{dataset_name}",
                "description": desc,
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    for chart_name, spec in dict(capabilities.get("charts") or {}).items():
        desc = str(spec.get("description") or "").strip() or f"Build chart model '{chart_name}'."
        tools.append(
            {
                "name": f"chart.{chart_name}",
                "description": desc,
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    return tools


def build_query_request_for_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> QueryRequest:
    name = str(tool_name or "").strip()
    args = coerce_object(arguments, field_name="arguments")
    if name == "system.capabilities":
        return QueryRequest(operation="capabilities", name="", params={})
    if name == "query.execute":
        payload = {
            "operation": str(args.get("operation") or "").strip().lower(),
            "name": str(args.get("name") or ""),
            "params": coerce_object(args.get("params"), field_name="params"),
        }
        return QueryRequest.from_dict(payload)
    if name.startswith("dataset."):
        return QueryRequest(operation="dataset", name=name.split(".", 1)[1], params=args)
    if name.startswith("chart."):
        return QueryRequest(operation="chart", name=name.split(".", 1)[1], params=args)
    raise ValueError(f"Unsupported tool '{name}'.")


def _resolve_research_layer(service: QueryService) -> Any:
    data_access = getattr(service, "data_access", None)
    if data_access is not None and hasattr(data_access, "resolve_attention_home_1d"):
        return data_access
    return None


def _invoke_research_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    layer = _resolve_research_layer(service)
    if tool_name == "research.retained_context":
        payload = omnibar_research.retained_context(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_items=int(args.get("max_items") or 5),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("attention_home_1d", "attention_research_bundle", "attention_ticker_background")
    elif tool_name == "research.market_impact_map":
        payload = omnibar_research.market_impact_map(
            query=str(args.get("query") or ""),
            max_symbols=int(args.get("max_symbols") or 8),
        )
        datasets = ("attention_market_events", "commodity_proxy_profile")
    elif tool_name == "research.search_evidence":
        payload = omnibar_research.search_evidence(
            query=str(args.get("query") or ""),
            tickers=args.get("tickers"),
            max_results=int(args.get("max_results") or 10),
        )
        datasets = ("saa_evidence_chunks", "saa_documents")
    elif tool_name == "research.live_event_evidence":
        payload = omnibar_research.live_event_evidence(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_results=int(args.get("max_results") or 6),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("web_research", "attention_search_results")
    elif tool_name == "research.open_page":
        payload = omnibar_research.open_page(
            url=str(args.get("url") or ""),
            max_chars=int(args.get("max_chars") or 5000),
        )
        datasets = ("page_browsing",)
    else:
        raise ValueError(f"Unsupported tool '{tool_name}'.")

    return {
        "request": {"operation": "research", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": list(datasets),
            "details": {"tool_name": tool_name},
        },
        "messages": [],
    }


def _invoke_hypothesis_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "hypothesis.verify":
        from .agents import verify_hypothesis
        from .llm import load_llm_client

        llm_client = load_llm_client()
        if llm_client is None:
            return {
                "request": {"operation": "hypothesis", "name": tool_name, "params": args},
                "result_type": "error",
                "payload": {"error": "LLM runtime is not configured."},
                "provenance": {"mode": "error", "datasets": [], "details": {}},
                "messages": ["LLM runtime is not configured for hypothesis verification."],
            }

        claims = [
            {
                "claim_text": str(c.get("claim_text") or ""),
                "source": str(c.get("source") or ""),
                "is_same_day": bool(c.get("is_same_day")),
                "confidence_score": float(c.get("confidence_score") or 0.5),
            }
            for c in list(args.get("claims") or [])
            if str(c.get("claim_text") or "").strip()
        ]
        beats = [
            {
                "kind": "beat",
                "sentence": str(b.get("sentence") or ""),
                "symbols": list(b.get("symbols") or []),
            }
            for b in list(args.get("beats") or [])
        ]
        result = verify_hypothesis(
            hypothesis=str(args.get("hypothesis") or ""),
            claims=claims,
            beats=beats,
            llm_client=llm_client,
        )
        return {
            "request": {"operation": "hypothesis", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": result,
            "provenance": {
                "mode": "computed",
                "datasets": ("hypothesis_verification",),
                "details": {"tool_name": tool_name},
            },
            "messages": [],
        }
    raise ValueError(f"Unsupported tool '{tool_name}'.")


def _invoke_anomaly_tool(
    *,
    service: QueryService,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import pandas as pd
    from .market_data import (
        AttentionConfig,
        ExpectationConfig,
        build_attention_candidates,
        build_peer_group_membership,
        build_price_expectations,
        normalize_horizon,
        HORIZON_PERIODS,
    )

    args = coerce_object(arguments, field_name="arguments")
    raw_symbols = list(args.get("symbols") or [])
    symbols = [str(s).upper().strip() for s in raw_symbols if str(s).strip()]
    raw_horizon = str(args.get("horizon") or "1w")
    horizon = normalize_horizon(raw_horizon)
    # Fallback for unsupported aliases like "30d" — map to nearest canonical horizon
    if horizon not in HORIZON_PERIODS:
        horizon = _nearest_horizon(raw_horizon)

    if not symbols:
        return {
            "request": {"operation": "dataset", "name": "run_anomaly_check", "params": args},
            "result_type": "error",
            "payload": {"error": "No symbols provided."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["No symbols provided."],
        }

    data_access = getattr(service, "data_access", None)
    events: list[dict[str, Any]] = []

    # --- Step 1: Try materialized attention candidates ---
    if data_access is not None and hasattr(data_access, "resolve_attention_home_1d"):
        try:
            resolved = data_access.resolve_attention_home_1d()
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            if isinstance(payload, dict):
                candidates_frame = payload.get("attention_candidates_1d")
                if candidates_frame is None:
                    candidates_frame = payload.get("attention_feed")
                if isinstance(candidates_frame, pd.DataFrame) and not candidates_frame.empty:
                    frame = candidates_frame.copy()
                    if "entity_id" in frame.columns:
                        frame = frame[frame["entity_id"].str.upper().isin(symbols)]
                    if horizon and "horizon" in frame.columns:
                        frame = frame[frame["horizon"] == horizon]
                    frame = frame.sort_values("attention_score", ascending=False) if "attention_score" in frame.columns else frame
                    for _, row in frame.head(20).iterrows():
                        events.append(_event_from_row(row))
        except Exception:
            pass

    # --- Step 2: On-demand computation for missing symbols ---
    missing = [s for s in symbols if s not in {e["symbol"] for e in events}]
    if missing and data_access is not None and hasattr(data_access, "resolve_price_history"):
        try:
            # Fetch price history for all missing symbols + SPY as benchmark
            all_needed = list(set(missing + ["SPY"]))
            frames: list[pd.DataFrame] = []
            for sym in all_needed:
                try:
                    resolved = data_access.resolve_price_history(sym, days=400)
                    ph = resolved.payload if hasattr(resolved, "payload") else resolved
                    if isinstance(ph, pd.DataFrame) and not ph.empty:
                        ph = ph.copy()
                        if "symbol" not in ph.columns:
                            ph["symbol"] = sym
                        frames.append(ph)
                except Exception:
                    pass
            if frames:
                combined_prices = pd.concat(frames, ignore_index=True)
                peer_membership = build_peer_group_membership(
                    asof_time_utc=pd.Timestamp.now(tz="UTC"),
                    symbols=missing + ["SPY"],
                )
                # Momentum and correlation are optional — use empty frames as fallback
                momentum = pd.DataFrame()
                correlation = pd.DataFrame()
                try:
                    mp_resolved = data_access.resolve_momentum_profiles(symbols=missing + ["SPY"])
                    mp = mp_resolved.payload if hasattr(mp_resolved, "payload") else mp_resolved
                    if isinstance(mp, pd.DataFrame):
                        momentum = mp
                except Exception:
                    pass
                try:
                    cs_resolved = data_access.resolve_correlation_phase_shift(
                        benchmark="SPY", days=180, corr_window=60,
                        roc_window=20, momentum_window=60, symbols=missing + ["SPY"],
                    )
                    cs = cs_resolved.payload if hasattr(cs_resolved, "payload") else cs_resolved
                    if isinstance(cs, pd.DataFrame):
                        correlation = cs
                    elif isinstance(cs, dict):
                        correlation = cs.get("summary", pd.DataFrame())
                except Exception:
                    pass

                expectations = build_price_expectations(
                    combined_prices, momentum, correlation, peer_membership,
                    config=ExpectationConfig(horizons=(horizon,)),
                )
                if not expectations.empty:
                    candidates = build_attention_candidates(
                        expectations, config=AttentionConfig(residual_zscore_threshold=1.0),
                    )
                    if not candidates.empty:
                        candidates = candidates[candidates["entity_id"].isin(missing)].copy()
                        candidates = candidates.sort_values("attention_score", ascending=False)
                        for _, row in candidates.head(20).iterrows():
                            events.append(_event_from_row(row))
        except Exception:
            pass

    missing = [s for s in symbols if s not in {e["symbol"] for e in events}]
    return {
        "request": {"operation": "dataset", "name": "run_anomaly_check", "params": args},
        "result_type": "research",
        "payload": {
            "events": events,
            "symbols_checked": symbols,
            "symbols_with_anomalies": list({e["symbol"] for e in events}),
            "symbols_no_data": missing,
            "horizon": horizon,
            "llm_context_text": _anomaly_context_text(events, missing),
        },
        "provenance": {
            "mode": "computed",
            "datasets": ("attention_candidates_1d", "price_history"),
            "details": {"symbols": symbols, "horizon": horizon},
        },
        "messages": [],
    }


def _event_from_row(row: Any) -> dict[str, Any]:
    import pandas as pd
    return {
        "symbol": str(row.get("entity_id") or ""),
        "horizon": str(row.get("horizon") or ""),
        "direction": str(row.get("direction") or ""),
        "anomaly_type": str(row.get("anomaly_type") or ""),
        "observed_return_pct": float(pd.to_numeric(row.get("observed_value"), errors="coerce")),
        "expected_return_pct": float(pd.to_numeric(row.get("expected_value"), errors="coerce")),
        "residual_pct": float(pd.to_numeric(row.get("residual_value"), errors="coerce")),
        "residual_zscore": float(pd.to_numeric(row.get("residual_zscore"), errors="coerce")),
        "attention_score": float(pd.to_numeric(row.get("attention_score"), errors="coerce")),
        "severity_score": float(pd.to_numeric(row.get("severity_score"), errors="coerce")),
        "status": str(row.get("status") or ""),
        "why_now_text": str(row.get("why_now_text") or ""),
    }


def _nearest_horizon(raw: str) -> str:
    """Map non-standard horizon strings like '30d' to the nearest canonical horizon."""
    import re
    from .market_data import HORIZON_PERIODS
    text = str(raw or "").strip().lower()
    match = re.match(r"^(\d+)\s*(d|w|m|y)", text)
    if not match:
        return "1w"
    count = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        days = count
    elif unit == "w":
        days = count * 5
    elif unit == "m":
        days = count * 21
    else:
        days = count * 252
    # Find closest canonical horizon
    best = "1w"
    best_diff = abs(days - 5)
    for name, periods in HORIZON_PERIODS.items():
        diff = abs(days - periods)
        if diff < best_diff:
            best = name
            best_diff = diff
    return best


def _anomaly_context_text(events: list[dict[str, Any]], missing: list[str]) -> str:
    if not events and not missing:
        return "No anomaly data available."
    lines: list[str] = []
    for e in events:
        lines.append(
            f"{e['symbol']}: {e['direction']} {e['residual_zscore']:+.2f}σ "
            f"(observed {e['observed_return_pct']:+.2f}% vs expected {e['expected_return_pct']:+.2f}%), "
            f"type={e['anomaly_type']}, attention={e['attention_score']:.0f}"
        )
    if missing:
        lines.append(f"No anomaly data for: {', '.join(missing)}")
    return "\n".join(lines)


def _invoke_investigator_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import pandas as pd

    args = coerce_object(arguments, field_name="arguments")
    data_access = getattr(service, "data_access", None)
    if data_access is None:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": "Data access layer is not available."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["Data access layer is not available."],
        }

    ticker = str(args.get("ticker") or "").upper().strip()
    if not ticker:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": "No ticker provided."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["No ticker provided."],
        }

    try:
        if tool_name == "investigator.technical_signals":
            resolved = data_access.resolve_technical_signal_summary(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            summary = dict(payload) if isinstance(payload, dict) else {}
            text_lines = [f"Technical signals for {ticker}:"]
            for key in ["close", "ath", "pullback_from_ath_pct", "channel_support", "channel_resistance",
                         "channel_position", "dist_to_support_pct", "dist_to_resistance_pct",
                         "rsi_14", "vol_20_ann_pct", "regime"]:
                val = summary.get(key)
                if val is not None and str(val).strip() and str(val) != "nan":
                    label = key.replace("_", " ").replace("pct", "%").title()
                    text_lines.append(f"  {label}: {val}")
            summary["llm_context_text"] = "\n".join(text_lines) if len(text_lines) > 1 else f"No technical signal data for {ticker}."
            return _investigator_result(tool_name, args, summary, ("technical_signals_latest",))

        if tool_name == "investigator.forecast":
            resolved = data_access.resolve_forecast_next_week(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            forecast = dict(payload) if isinstance(payload, dict) else {}
            text_lines = [f"Next-week forecast for {ticker}:"]
            for key in ["up_probability", "breakout_probability", "median_return_pct",
                         "ci_low_pct", "ci_high_pct", "simulations"]:
                val = forecast.get(key)
                if val is not None and str(val).strip() and str(val) != "nan":
                    label = key.replace("_", " ").replace("pct", "%").title()
                    text_lines.append(f"  {label}: {val}")
            forecast["llm_context_text"] = "\n".join(text_lines) if len(text_lines) > 1 else f"No forecast data for {ticker}."
            return _investigator_result(tool_name, args, forecast, ("technical_forecast",))

        if tool_name == "investigator.company_context":
            resolved = data_access.resolve_attention_context(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            context = dict(payload) if isinstance(payload, dict) else {}
            parts = []
            for key in ["llm_headline", "llm_summary_text", "llm_narrative_text",
                         "llm_why_now", "llm_management_signal"]:
                val = str(context.get(key) or "").strip()
                if val:
                    parts.append(val)
            if not parts:
                story = str(context.get("context_story_text") or "").strip()
                if story:
                    parts.append(story)
            if not parts:
                try:
                    background_resolved = data_access.resolve_attention_ticker_background(ticker)
                    background_payload = (
                        background_resolved.payload if hasattr(background_resolved, "payload") else background_resolved
                    )
                    background = dict(background_payload) if isinstance(background_payload, dict) else {}
                    company_name = str(background.get("company_name") or "").strip()
                    business_lens = str(background.get("business_lens") or "").strip()
                    background_text = (
                        str(background.get("company_background_text") or "").strip()
                        or str(background.get("description_text") or "").strip()
                    )
                    if company_name and not str(context.get("company_name") or "").strip():
                        context["company_name"] = company_name
                    if business_lens:
                        context["business_lens"] = business_lens
                    if background_text:
                        parts.append(background_text)
                    for line in list(background.get("news_summary_lines") or [])[:3]:
                        text = str(line or "").strip()
                        if text:
                            parts.append(text)
                    trace = context.get("source_trace") if isinstance(context.get("source_trace"), dict) else {}
                    background_trace = background.get("source_trace") if isinstance(background.get("source_trace"), dict) else {}
                    if background_trace:
                        context["source_trace"] = {**trace, **background_trace}
                except Exception:
                    pass
            context["llm_context_text"] = "\n\n".join(parts) if parts else f"No company context for {ticker}."
            return _investigator_result(tool_name, args, context, ("attention_context_bundle", "attention_ticker_background", "company_baselines"))

        if tool_name == "investigator.fundamentals":
            resolved = data_access.resolve_quarterly_fundamentals(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            if isinstance(payload, dict):
                result: dict[str, Any] = {}
                text_lines = [f"Quarterly fundamentals for {ticker}:"]
                for statement_type in ["income", "balance", "cashflow"]:
                    frame = payload.get(statement_type)
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        # Take last 4 quarters
                        recent = frame.tail(4)
                        records = recent.to_dict(orient="records")
                        result[statement_type] = records
                        text_lines.append(f"  {statement_type.title()}: {len(records)} quarter(s)")
                result["llm_context_text"] = "\n".join(text_lines) if len(text_lines) > 1 else f"No fundamentals data for {ticker}."
                return _investigator_result(tool_name, args, result, ("quarterly_fundamentals",))
            return _investigator_result(tool_name, args, {"llm_context_text": f"No fundamentals data for {ticker}."}, ("quarterly_fundamentals",))

        if tool_name == "investigator.recent_news":
            days = int(args.get("days") or 14)
            limit = int(args.get("limit") or 8)
            resolved = data_access.resolve_recent_news(ticker, days=days, limit=limit)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            if isinstance(payload, pd.DataFrame) and not payload.empty:
                articles = []
                for _, row in payload.head(limit).iterrows():
                    articles.append({
                        "headline": str(row.get("headline") or row.get("title") or ""),
                        "source": str(row.get("source") or ""),
                        "published_at": str(row.get("published_at") or ""),
                        "summary": str(row.get("summary") or ""),
                    })
                text_lines = [f"Recent news for {ticker} ({len(articles)} articles):"]
                for a in articles:
                    text_lines.append(f"  - {a['headline']} ({a['source']}, {a['published_at']})")
                result = {
                    "articles": articles,
                    "llm_context_text": "\n".join(text_lines),
                }
                return _investigator_result(tool_name, args, result, ("news_articles",))
            return _investigator_result(tool_name, args, {"articles": [], "llm_context_text": f"No recent news for {ticker}."}, ("news_articles",))

    except Exception as exc:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": str(exc)},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": [str(exc)],
        }

    raise ValueError(f"Unsupported investigator tool '{tool_name}'.")


def _investigator_result(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any],
    datasets: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "request": {"operation": "investigator", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": list(datasets),
            "details": {"tool_name": tool_name},
        },
        "messages": [],
    }


def _invoke_scratchpad_tool(
    *,
    tool_name: str,
    run_id: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .agents import read_entries, read_summary, write_entry

    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "scratchpad.write":
        entry = write_entry(
            run_id=run_id,
            kind=str(args.get("kind") or "note"),
            content=coerce_object(args.get("content"), field_name="content"),
        )
        return {
            "request": {"operation": "scratchpad", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": {"status": "written", "entry": entry},
            "provenance": {"mode": "computed", "datasets": ("hypothesis_scratchpad",), "details": {"tool_name": tool_name}},
            "messages": [],
        }
    if tool_name == "scratchpad.read":
        entries = read_entries(
            run_id=run_id,
            kind=args.get("kind"),
            last_n=int(args["last_n"]) if args.get("last_n") is not None else None,
        )
        summary = read_summary(run_id=run_id)
        return {
            "request": {"operation": "scratchpad", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": {"summary": summary, "entries": entries},
            "provenance": {"mode": "computed", "datasets": ("hypothesis_scratchpad",), "details": {"tool_name": tool_name}},
            "messages": [],
        }
    raise ValueError(f"Unsupported tool '{tool_name}'.")


def invoke_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    if is_research_tool(tool_name):
        return _invoke_research_tool(service=service, tool_name=tool_name, arguments=arguments)
    if is_hypothesis_tool(tool_name):
        return _invoke_hypothesis_tool(tool_name=tool_name, arguments=arguments)
    if is_scratchpad_tool(tool_name):
        return _invoke_scratchpad_tool(tool_name=tool_name, run_id=run_id or "default", arguments=arguments)
    if is_anomaly_tool(tool_name):
        return _invoke_anomaly_tool(service=service, arguments=arguments)
    if is_investigator_tool(tool_name):
        return _invoke_investigator_tool(service=service, tool_name=tool_name, arguments=arguments)
    request = build_query_request_for_tool(tool_name=tool_name, arguments=arguments)
    return service.execute(request).to_dict()


__all__ = [
    "build_tool_catalog",
    "build_query_request_for_tool",
    "is_investigator_tool",
    "is_query_service_tool",
    "is_research_tool",
    "is_scratchpad_tool",
    "invoke_tool",
    "tool_schema",
]
