#!/usr/bin/env python3
"""Read production/development manifests and bounded Home artifacts before cutover.

Checks transport/data contracts only. Model selection quality still needs personal review.
No database writes, provider requests, model calls, or global history downloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", choices=("production", "development"), default="production")
    args = parser.parse_args()
    os.environ["PIPELINE_DATASET_NAMESPACE"] = args.namespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services import pipeline_store
    from services.attention_materialized import deserialize_attention_home_payload
    from services.home_view import _daily_datapoints_view, _financial_trends

    names = ("attention_home_1d", "attention_ticker_snapshots_1d", "daily_market_datapoints_1d",
             "technical_signal_history", "price_history", "fred_observations")
    metadata = {name: pipeline_store._stable_latest_manifest_metadata(name) for name in names}
    gaps, datasets = [], []
    for name, item in metadata.items():
        partitions = item.partitioning if item else {}
        datasets.append({"dataset": name, "asof": item.asof_time_utc if item else None,
                         "rows": item.row_count if item else None,
                         "partitions": len(partitions.get("paths", {}))})
        if item is None or item.row_count <= 0:
            gaps.append(f"{name}: missing or empty artifact")
        elif name in ("price_history", "technical_signal_history") and item.row_count > int(os.getenv("PIPELINE_SLICE_FULL_SCAN_MAX_ROWS", "250000")):
            if partitions.get("strategy") != pipeline_store.SYMBOL_HASH_PARTITION_STRATEGY or partitions.get("column") != "symbol" or not partitions.get("paths"):
                gaps.append(f"{name}: bounded symbol partitions are missing")

    def read_small(name: str):
        item = metadata[name]
        if item is None:
            return None
        if item.row_count > 10_000:
            gaps.append(f"{name}: unexpected read-model size")
            return None
        return pipeline_store._read_blob_parquet(item.blob_path)

    home_frame = read_small("attention_home_1d")
    home = deserialize_attention_home_payload(home_frame) if home_frame is not None else {}
    daily_frame = read_small("daily_market_datapoints_1d")
    daily_rows = daily_frame.to_dict("records") if daily_frame is not None else []
    points = _daily_datapoints_view(daily_rows, str(home.get("run_id") or ""))
    points = points or _daily_datapoints_view(home.get("daily_datapoints"), str(home.get("run_id") or ""))
    if len(points) != 5:
        gaps.append("Daily Brief: five selected charts do not match the current Home identity")
    ticker_frame = read_small("attention_ticker_snapshots_1d")
    trend_coverage = {}
    if ticker_frame is not None:
        for row in ticker_frame.to_dict("records"):
            for metric, history in _financial_trends(row.get("financial_trends_json")).items():
                if len(history) > 1:
                    trend_coverage[metric] = trend_coverage.get(metric, 0) + 1
    for metric in ("revenue", "net_income", "free_cash_flow"):
        if not trend_coverage.get(metric):
            gaps.append(f"Attention: no {metric} history is materialized")
    print(json.dumps({"namespace": args.namespace, "data_contract_ready": not gaps, "gaps": gaps,
                      "datasets": datasets, "financial_trend_coverage": trend_coverage,
                      "selected_charts": [{"title": point["title"], "significance": point["significance"],
                                           "observations": len(point["points"])} for point in points]}, indent=2))
    raise SystemExit(1 if gaps else 0)


if __name__ == "__main__":
    main()
