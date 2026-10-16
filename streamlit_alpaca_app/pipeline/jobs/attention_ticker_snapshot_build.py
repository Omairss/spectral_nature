from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from services.attention_materialized import deserialize_attention_home_payload
from services.attention_ticker_snapshots import (
    build_attention_ticker_snapshot_frame,
    collect_attention_ticker_symbols,
    prefetch_attention_intraday_prices,
)


LoadFrameFn = Callable[[str], pd.DataFrame]
PersistDatasetFn = Callable[[str, pd.DataFrame, Any, Any | None], None]
JobProgressFn = Callable[..., None]


class AttentionTickerSnapshotBuildError(RuntimeError):
    pass


def run_attention_ticker_snapshot_build(
    ctx: Any,
    conn: Any | None,
    *,
    persist_dataset_fn: PersistDatasetFn,
    job_progress_fn: JobProgressFn,
    load_materialized_frame_fn: LoadFrameFn,
) -> pd.DataFrame:
    """Materialize Home ticker market data without running the agentic narrative build."""

    job_progress_fn(
        ctx,
        conn,
        stage="loading_inputs",
        message="Loading Home symbols, price history, universe, and quarterly fundamentals.",
        progress_pct=10.0,
    )
    home_frame = load_materialized_frame_fn("attention_home_1d")
    if not isinstance(home_frame, pd.DataFrame) or home_frame.empty:
        raise AttentionTickerSnapshotBuildError(
            "Ticker snapshot build requires a materialized attention_home_1d dataset."
        )

    home_payload = deserialize_attention_home_payload(home_frame)
    symbols = collect_attention_ticker_symbols(home_payload, max_symbols=120)
    if not symbols:
        raise AttentionTickerSnapshotBuildError(
            "Ticker snapshot build found no public symbols in attention_home_1d."
        )

    required_frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for dataset_name in ("price_history", "universe_snapshot", "quarterly_fundamentals"):
        frame = load_materialized_frame_fn(dataset_name)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            missing.append(dataset_name)
        else:
            required_frames[dataset_name] = frame
    if missing:
        raise AttentionTickerSnapshotBuildError(
            f"Ticker snapshot build cannot proceed because required datasets are missing or empty: {missing}."
        )

    job_progress_fn(ctx, conn, stage="prefetching_intraday_prices",
                    message=f"Prefetching intraday price charts for {len(symbols)} Home symbols.", progress_pct=35.0)
    intraday = prefetch_attention_intraday_prices(symbols, asof_time_utc=ctx.asof)
    job_progress_fn(
        ctx,
        conn,
        stage="materializing_ticker_snapshots",
        message=f"Materializing price and fundamental snapshots for {len(symbols)} Home symbols.",
        progress_pct=45.0,
    )
    snapshots = build_attention_ticker_snapshot_frame(
        symbols,
        price_history_frame=required_frames["price_history"],
        universe_snapshot_frame=required_frames["universe_snapshot"],
        quarterly_fundamentals_frame=required_frames["quarterly_fundamentals"],
        intraday_prices=intraday,
        asof_time_utc=ctx.asof,
        run_id=ctx.run_id,
    )
    if snapshots.empty:
        raise AttentionTickerSnapshotBuildError(
            "Ticker snapshot build produced no rows from the current Home symbols."
        )

    persist_dataset_fn("attention_ticker_snapshots_1d", snapshots, ctx, conn)
    fundamental_columns = [
        column
        for column in ("revenue", "net_income", "free_cash_flow")
        if column in snapshots.columns
    ]
    fundamental_rows = (
        int(snapshots[fundamental_columns].notna().any(axis=1).sum())
        if fundamental_columns
        else 0
    )
    job_progress_fn(
        ctx,
        conn,
        stage="completed",
        message=(
            "Materialized attention ticker snapshots "
            f"rows={len(snapshots)} with_fundamentals={fundamental_rows}."
        ),
        progress_pct=100.0,
        status="Succeeded",
    )
    return snapshots


__all__ = [
    "AttentionTickerSnapshotBuildError",
    "run_attention_ticker_snapshot_build",
]
