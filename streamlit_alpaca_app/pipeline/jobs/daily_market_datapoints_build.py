from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable
import pandas as pd

from services.attention_materialized import deserialize_attention_home_payload
from services.daily_market_datapoints import build_daily_datapoint_catalog, select_daily_market_datapoints


def run_daily_market_datapoints_build(ctx: Any, conn: Any | None, *, persist_dataset_fn: Callable, job_progress_fn: Callable, load_materialized_frame_fn: Callable) -> pd.DataFrame:
    job_progress_fn(ctx, conn, stage="loading_inputs", message="Loading daily market context and chart candidates.", progress_pct=10)
    home = deserialize_attention_home_payload(load_materialized_frame_fn("attention_home_1d"))
    snapshots = load_materialized_frame_fn("attention_ticker_snapshots_1d")
    if not home or snapshots.empty:
        raise RuntimeError("Daily datapoints require published Home and ticker snapshots.")
    brief_asof = home.get("generated_at_utc") or ctx.asof
    catalog = build_daily_datapoint_catalog(snapshots=snapshots, fred_observations=load_materialized_frame_fn("fred_observations"), fred_summary=load_materialized_frame_fn("fred_summary"), asof=brief_asof)
    job_progress_fn(ctx, conn, stage="selecting_datapoints", message=f"AQL/Zopedia selecting five datapoints from {len(catalog)} chart candidates.", progress_pct=45)
    selected = select_daily_market_datapoints(home=home, catalog=catalog, asof=brief_asof, run_id=ctx.run_id)
    selection_context = replace(ctx, asof=pd.to_datetime(brief_asof, utc=True).to_pydatetime())
    persist_dataset_fn("daily_market_datapoints_1d", selected, selection_context, conn)
    job_progress_fn(ctx, conn, stage="completed", message="Published five daily market datapoints.", progress_pct=100, status="Succeeded")
    return selected
