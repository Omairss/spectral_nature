"""Independent schedule ingestion; does not construct or regenerate a brief."""
from datetime import datetime, time, timedelta
import json
import os

import pandas as pd

from services.alpha_vantage import load_alpha_vantage_key
from services.alpaca_api import AlpacaAPI
from services.config import load_config
from services.fred import load_fred_api_key
from services.market_calendar import DATASET, NY, SourceResult, calendar_from_frame, collect_reported_earnings, collect_sources, merge_calendar
from services.market_calendar_reactions import fetch_reaction_prices, observe_reactions


def run_market_calendar_refresh(ctx, conn, *, persist_dataset_fn, load_materialized_frame_fn):
    reference = ctx.asof.astimezone(NY).date()
    start, end = reference - timedelta(days=30), reference + timedelta(days=90)
    previous = calendar_from_frame(load_materialized_frame_fn(DATASET))
    alpha_key, fred_key = load_alpha_vantage_key(), load_fred_api_key()
    # Retained company market-cap values rank earnings without a fixed ticker list.
    ranks = {}
    companies, priorities = {}, {}
    universe = load_materialized_frame_fn("universe_snapshot")
    if not universe.empty and {"symbol", "rank"}.issubset(universe.columns):
        liquidity = pd.to_numeric(universe["rank"], errors="coerce").rank(pct=True, ascending=False)
        ranks = {str(symbol).upper(): 0.35 + 0.65 * float(rank) for symbol, rank in zip(universe["symbol"], liquidity) if pd.notna(rank)}
        companies = {str(row["symbol"]).upper(): str(row.get("security_name") or row["symbol"])
                     for row in universe.to_dict("records") if not row.get("is_etf", False)}
        priorities = dict(ranks)
    baseline = load_materialized_frame_fn("attention_ticker_background_snapshots")
    if not baseline.empty and {"symbol", "market_cap"}.issubset(baseline.columns):
        caps = pd.to_numeric(baseline["market_cap"], errors="coerce")
        ranked = caps.where(caps > 0).rank(pct=True)
        ranks.update({str(symbol).upper(): 0.35 + 0.65 * float(rank) for symbol, rank in zip(baseline["symbol"], ranked) if pd.notna(rank)})
        priorities.update({str(symbol).upper(): float(cap) for symbol, cap in zip(baseline["symbol"], caps) if pd.notna(cap) and cap > 0})
    sources = collect_sources(start, end, alpha_key=alpha_key, fred_key=fred_key, ranks=ranks)
    try:
        sources.append(collect_reported_earnings(alpha_key, now=ctx.asof, start=start, end=end,
            companies=companies, ranks=ranks, priorities=priorities,
            previous_events=previous.get("events", []) + [e for s in sources for e in s.events]))
    except Exception as exc:
        sources.append(SourceResult("alpha_vantage_history", status="failed", error=type(exc).__name__))
    for source in sources:
        print(f"[info] calendar source={source.source} status={source.status} events={len(source.events)}")
        if source.status != "available":
            ctx.degraded_components["calendar_" + source.source] = source.status + (":" + source.error if source.error else "")
    if not previous and not any(source.events for source in sources):
        raise RuntimeError("No calendar source supplied usable records")
    state = merge_calendar(previous, sources, now=ctx.asof, start=start, end=end)
    state["market_sessions"] = previous.get("market_sessions", [])
    api = None
    try:
        config = load_config()
        if config is not None:
            api = AlpacaAPI(config)
            state["market_sessions"] = api.get_market_calendar(
                start=(start - timedelta(days=10)).isoformat(), end=(reference + timedelta(days=7)).isoformat())
        else:
            ctx.degraded_components["calendar_sessions"] = "not_configured"
    except Exception as exc:
        ctx.degraded_components["calendar_sessions"] = type(exc).__name__
    benchmark = os.getenv("MARKET_CALENDAR_REACTION_BENCHMARK", "SPY").strip().upper()
    symbols = sorted({benchmark, *[e["ticker"] for e in state["events"] if e.get("ticker")
        and not e.get("cancelled") and not e.get("market_reaction") and e["date"] < reference.isoformat()]})
    prices = pd.DataFrame()
    try:
        if api is None:
            raise RuntimeError("Alpaca is not configured")
        prices = fetch_reaction_prices(api, symbols,
            start=datetime.combine(start - timedelta(days=10), time(), NY),
            end=ctx.asof - timedelta(minutes=15))
        if prices.empty:
            ctx.degraded_components["calendar_reactions"] = "no_consolidated_closes"
    except Exception as exc:
        ctx.degraded_components["calendar_reactions"] = type(exc).__name__
    state = observe_reactions(state, prices, as_of=ctx.asof,
        price_version="alpaca:sip:" + ctx.asof.isoformat(), benchmark=benchmark)
    print(f"[info] calendar history={sum(e.get('confirmation_status') == 'reported' for e in state['events'])} reactions={sum(bool(e.get('market_reaction')) for e in state['events'])}")
    persist_dataset_fn(DATASET, pd.DataFrame([{
        "schema_version": 1, "as_of": ctx.asof.isoformat(),
        "calendar_json": json.dumps(state, ensure_ascii=False, allow_nan=False),
    }]), ctx, conn)
    return state
