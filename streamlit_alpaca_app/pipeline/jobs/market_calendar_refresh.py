"""Independent schedule ingestion; does not construct or regenerate a brief."""
from datetime import timedelta
import json

import pandas as pd

from services.alpha_vantage import load_alpha_vantage_key
from services.fred import load_fred_api_key
from services.market_calendar import DATASET, NY, calendar_from_frame, collect_sources, merge_calendar


def run_market_calendar_refresh(ctx, conn, *, persist_dataset_fn, load_materialized_frame_fn):
    reference = ctx.asof.astimezone(NY).date()
    start, end = reference - timedelta(days=30), reference + timedelta(days=90)
    previous = calendar_from_frame(load_materialized_frame_fn(DATASET))
    alpha_key, fred_key = load_alpha_vantage_key(), load_fred_api_key()
    # Retained company market-cap values rank earnings without a fixed ticker list.
    ranks = {}
    universe = load_materialized_frame_fn("universe_snapshot")
    if not universe.empty and {"symbol", "rank"}.issubset(universe.columns):
        liquidity = pd.to_numeric(universe["rank"], errors="coerce").rank(pct=True, ascending=False)
        ranks = {str(symbol).upper(): 0.35 + 0.65 * float(rank) for symbol, rank in zip(universe["symbol"], liquidity) if pd.notna(rank)}
    baseline = load_materialized_frame_fn("attention_ticker_background_snapshots")
    if not baseline.empty and {"symbol", "market_cap"}.issubset(baseline.columns):
        caps = pd.to_numeric(baseline["market_cap"], errors="coerce")
        ranked = caps.where(caps > 0).rank(pct=True)
        ranks.update({str(symbol).upper(): 0.35 + 0.65 * float(rank) for symbol, rank in zip(baseline["symbol"], ranked) if pd.notna(rank)})
    sources = collect_sources(start, end, alpha_key=alpha_key, fred_key=fred_key, ranks=ranks)
    for source in sources:
        print(f"[info] calendar source={source.source} status={source.status} events={len(source.events)}")
        if source.status != "available":
            ctx.degraded_components["calendar_" + source.source] = source.status + (":" + source.error if source.error else "")
    if not previous and not any(source.events for source in sources):
        raise RuntimeError("No calendar source supplied usable records")
    state = merge_calendar(previous, sources, now=ctx.asof, start=start, end=end)
    persist_dataset_fn(DATASET, pd.DataFrame([{
        "schema_version": 1, "as_of": ctx.asof.isoformat(),
        "calendar_json": json.dumps(state, ensure_ascii=False, allow_nan=False),
    }]), ctx, conn)
    return state
