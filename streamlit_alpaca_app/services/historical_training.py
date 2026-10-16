"""Frozen historical inputs and separately addressable return targets.

This module consumes immutable acquisition receipts. It never searches the live
web, calls a model, assigns narrative labels, or fits on evaluation observations.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from io import BytesIO
from pathlib import Path
from importlib.metadata import version
from typing import Any, Iterator
import gzip
import hashlib
import itertools
import json
import time
import platform

import pandas as pd

from services.historical_corpus import HistoricalCorpus, json_bytes, utcnow, normalize_historical_availability
from services.pipeline_store import ImmutableBlobArchive


SPLITS = [
    {"name": "train", "start": "2018-01-01", "end_exclusive": "2022-01-01"},
    {"name": "development", "start": "2022-01-01", "end_exclusive": "2024-01-01"},
    {"name": "test", "start": "2024-01-01", "end_exclusive": "2026-10-08"},
]
HORIZONS = (1, 5, 21, 63, 126, 252)


def forward_labels(bars: pd.DataFrame) -> pd.DataFrame:
    """Next observed session open to horizon close, known after target ends.

    The decision uses the preceding completed daily bar. Filling at that bar's
    already elapsed close would give the strategy an untradeable entry price.
    Horizons count observed provider sessions; dates and gaps remain explicit.
    No assumed zero return is introduced for missing or delisted instruments.
    """
    bars = bars.drop_duplicates(["symbol", "observation_time"]).sort_values(
        ["symbol", "observation_time"]).reset_index(drop=True)
    pieces = []
    for symbol, frame in bars.groupby("symbol", sort=False):
        frame = frame.reset_index(drop=True)
        decision = pd.to_datetime(frame["available_at"], utc=True)
        entry_time = frame["observation_time"].shift(-1)
        entry = pd.to_numeric(frame["open"], errors="coerce").shift(-1)
        for horizon in HORIZONS:
            end_time = frame["observation_time"].shift(-horizon)
            known_at = pd.to_datetime(frame["available_at"], utc=True).shift(-horizon)
            last = pd.to_numeric(frame["close"], errors="coerce").shift(-horizon)
            future_low = pd.to_numeric(frame["low"], errors="coerce").shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).min().iloc[::-1]
            future_high = pd.to_numeric(frame["high"], errors="coerce").shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1]
            valid = entry.gt(0) & last.gt(0) & entry_time.ge(decision)
            result = pd.DataFrame({"symbol": symbol, "decision_at": decision,
                "venue": frame["venue"].iloc[0] if "venue" in frame else "alpaca",
                "entry_session": entry_time, "entry_adjusted_open": entry,
                "horizon_observed_sessions": horizon, "target_end_session": end_time,
                "label_available_at": known_at, "forward_return": (last / entry - 1).where(valid),
                "maximum_adverse_return": (future_low / entry - 1).where(valid),
                "maximum_favorable_return": (future_high / entry - 1).where(valid),
                "target_complete": valid, "source_last_session": frame["observation_time"].iloc[-1]})
            result["elapsed_calendar_days"] = (end_time - entry_time).dt.total_seconds() / 86400
            result["split"] = "outside"
            result["fit_eligible"] = False
            for split in SPLITS:
                lower, upper = pd.Timestamp(split["start"], tz="UTC"), pd.Timestamp(split["end_exclusive"], tz="UTC")
                selected = decision.ge(lower) & decision.lt(upper)
                result.loc[selected, "split"] = split["name"]
                # Keep crossing targets for retrospective analysis, but purge
                # them from fitting and held-out scoring in this split.
                result.loc[selected, "fit_eligible"] = (valid & known_at.lt(upper))[selected]
            pieces.append(result)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def task_results(corpus: HistoricalCorpus, tasks: list[dict]) -> list[dict]:
    results = []
    for task in tasks:
        path = corpus._receipt_path(task)
        result = json.loads(path.read_text()) if path.exists() else {"task": task, "status": "pending", "outputs": []}
        # Include checkpointed pages even when the enclosing request failed or
        # was interrupted. Their receipt still says which task is incomplete.
        pages = path.with_suffix("")
        if result["status"] != "complete" and pages.is_dir():
            result["outputs"] = [o for p in sorted(pages.glob("*.json")) for o in json.loads(p.read_text())["outputs"]]
            if result["status"] == "pending":
                result["status"] = "running"
        results.append(result)
    return results


def publish_training_release(corpus: HistoricalCorpus, tasks: list[dict], catalog: dict) -> dict:
    results = task_results(corpus, tasks)
    objects = {o["sha256"]: o for r in results for o in r["outputs"]}
    canonical = corpus.state / "canonical"
    canonical.mkdir(exist_ok=True)
    canonical_map = {}
    canonical_counts = Counter()
    canonical_started = time.monotonic()
    to_convert = []

    def canonical_path(obj):
        schema = "_treasury2" if obj["dataset"].startswith("daily_treasury_") else ""
        return canonical / f"availability2{schema}_{obj['sha256']}.json"

    def convert(obj):
        source = pd.read_parquet(BytesIO(corpus.archive.get(obj)))
        if obj["dataset"].startswith("daily_treasury_"):
            # Reparse retained XML so old bill tables recover both their actual
            # observation dates and the feed-declared numeric/date data types.
            from services.treasury_yields import parse_treasury_rate_feed
            raw_sha = source["raw_sha256"].dropna().unique()
            if len(raw_sha) != 1:
                raise ValueError("Treasury partition must identify exactly one retained XML source")
            source = parse_treasury_rate_feed(corpus.archive.get(objects[raw_sha[0]]).decode(),
                date_field="INDEX_DATE" if obj["dataset"] == "daily_treasury_bill_rates" else "NEW_DATE")
            source["raw_sha256"] = raw_sha[0]
        replacement = corpus.table(source, dataset=obj["dataset"], visibility=obj["visibility"])
        replacement["source_partition_sha256"] = obj["sha256"]
        replacement["transformation"] = "source_local_date_availability_v2"
        corpus._save(canonical_path(obj), replacement)
        return replacement

    for sha, obj in list(objects.items()):
        dataset = obj.get("dataset", "")
        local_dates = dataset.startswith(("stock_bars_", "daily_treasury_", "company_")) or dataset in {"macro_vintage_observations", "corporate_actions", "sec_company_facts", "crypto_daily_bars"}
        needs_schema = dataset.startswith("daily_treasury_") and obj.get("treasury_schema_version", 0) < 2
        if not local_dates or (obj.get("availability_contract_version", 0) >= 2 and not needs_schema) or not obj.get("rows"):
            continue
        path = canonical_path(obj)
        if path.exists():
            canonical_map[sha] = json.loads(path.read_text())
        else:
            to_convert.append(obj)
        del objects[sha]
    remaining = iter(to_convert)
    with ThreadPoolExecutor(max_workers=6) as pool:
        running = {}
        for obj in list(itertools.islice(remaining, 6)):
            running[pool.submit(convert, obj)] = obj
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                obj = running.pop(future)
                canonical_map[obj["sha256"]] = future.result()
                if time.monotonic() - canonical_started < 60:
                    next_obj = next(remaining, None)
                    if next_obj:
                        running[pool.submit(convert, next_obj)] = next_obj
    canonical_counts["pending"] = sum(obj["sha256"] not in canonical_map for obj in to_convert)
    for replacement in canonical_map.values():
        objects[replacement["sha256"]] = replacement
        canonical_counts["complete"] += 1
    derivations = corpus.state / "derived"
    derivations.mkdir(exist_ok=True)
    derived_counts = Counter()
    derive_started = time.monotonic()
    for result in results:
        if result["status"] != "complete" or result["task"]["kind"] not in {"bars", "crypto", "binance_series"}:
            continue
        inputs = [canonical_map.get(o["sha256"], o) for o in result["outputs"] if o.get("dataset") in {"stock_bars_adjusted_outcomes", "crypto_daily_bars", "crypto_exchange_daily_bars"} and o["rows"]]
        if not inputs:
            continue
        if any(o["dataset"] == "stock_bars_adjusted_outcomes" and o.get("availability_contract_version", 0) < 2 for o in inputs):
            derived_counts["pending"] += 1
            continue
        key = hashlib.sha256(json_bytes({"version": 4, "inputs": [o["sha256"] for o in inputs],
            "horizons": HORIZONS, "splits": SPLITS})).hexdigest()
        path = derivations / f"{key}.json"
        if path.exists():
            receipt = json.loads(path.read_text())
        else:
            if time.monotonic() - derive_started > 60:
                derived_counts["pending"] += 1
                continue
            bars = pd.concat([pd.read_parquet(BytesIO(corpus.archive.get(o))) for o in inputs], ignore_index=True)
            bars = bars[(pd.to_datetime(bars["observation_time"], utc=True) >= corpus.start) & (pd.to_datetime(bars["observation_time"], utc=True) < corpus.end)].copy()
            bars = normalize_historical_availability(bars, inputs[0]["dataset"])
            labels = forward_labels(bars)
            dataset = "stock_forward_returns" if result["task"]["kind"] == "bars" else "crypto_forward_returns"
            receipt = corpus.table(labels, dataset=dataset, visibility="outcomes_only")
            receipt["derivation"] = {"version": 4, "input_sha256": [o["sha256"] for o in inputs],
                "entry": "next_observed_session_open", "horizon": "observed_sessions", "price_adjustment": "all" if dataset.startswith("stock") else "raw"}
            corpus._save(path, receipt)
            print(json.dumps({"derived": dataset, "rows": len(labels), "input_partitions": len(inputs)}), flush=True)
        objects[receipt["sha256"]] = receipt
        derived_counts["complete"] += 1
    frames = []
    for episode in catalog["episodes"]:
        onset = pd.Timestamp(episode["onset"], tz="UTC")
        through = pd.Timestamp(episode["through"], tz="UTC") + pd.Timedelta(days=1)
        start, end = max(corpus.start, onset - pd.Timedelta(days=365)), min(corpus.end, through + pd.Timedelta(days=365))
        cutoffs = set(pd.date_range(start, end, freq="7D")) | {onset, min(through, corpus.end), end}
        for field, value in episode.items():
            if isinstance(value, str) and (field.endswith("anchor") or field in {"market_acceleration", "adoption_publication"}):
                cutoffs.add(pd.Timestamp(value, tz="UTC"))
        for cutoff in sorted(c for c in cutoffs if start <= c <= end):
            frame_id = hashlib.sha256(f"{episode['id']}:{cutoff.isoformat()}".encode()).hexdigest()[:24]
            frames.append({"id": frame_id, "episode_id": episode["id"], "cutoff_utc": cutoff.isoformat(),
                "history_start_utc": corpus.start.isoformat(), "episode_window_start_utc": start.isoformat(),
                "phase": "before" if cutoff < onset else "during" if cutoff < through else "after",
                "split": next((s["name"] for s in SPLITS if pd.Timestamp(s["start"], tz="UTC") <= cutoff < pd.Timestamp(s["end_exclusive"], tz="UTC")), "outside")})
    counts = Counter(r["status"] for r in results)
    rows = Counter()
    for obj in objects.values():
        if obj.get("dataset"):
            rows[obj["dataset"]] += obj["rows"]
    manifest = {"schema_version": 1, "created_at_utc": utcnow(), "namespace": corpus.spec["namespace"],
        "availability_contract_version": 2, "label_contract_version": 4,
        "reader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "acquisition_spec_sha256": corpus.spec_hash, "collection_specification": corpus.spec,
        "episode_catalog": catalog, "task_counts": dict(counts), "dataset_rows": dict(rows),
        "status": "complete" if counts.get("complete") == len(tasks) and not derived_counts["pending"] and not canonical_counts["pending"] else "partial",
        "derivation_counts": dict(derived_counts),
        "canonicalization_counts": dict(canonical_counts),
        "unique_payload_bytes": sum(o["bytes"] for o in objects.values()),
        "objects": list(objects.values()), "tasks": results, "frames": frames, "splits": SPLITS,
        "input_policy": {"strict": "only_rows_with_available_at_at_or_before_cutoff", "reconstructed": "also_admits_current_archive_text_by_publication_date_without_engagement", "outcomes": "separate_access_only"},
        "training_objectives": catalog["objectives"],
        "research_feedback": "Forward returns and risk direct research selection; usefulness, explanations and causal claims require separate review. Unperformed investigations are unlabeled."}
    manifest["documentation"] = {}
    for name in ["HISTORICAL_SOURCE_TODO_2026-10-07.md", "HISTORICAL_CORPUS_RUNBOOK_2026-10-07.md", "HISTORICAL_CORPUS_REVIEW_2026-10-07.json"]:
        path = Path("documents/training") / name
        if path.exists():
            manifest["documentation"][name] = corpus.archive.put(path.read_bytes(), suffix=path.suffix.lstrip("."), content_type="application/json" if path.suffix == ".json" else "text/markdown; charset=utf-8")
    manifest["implementation"] = {"python": platform.python_version(),
        "packages": {name: version(name) for name in ["pandas", "pyarrow", "lxml", "trafilatura", "pypdf", "azure-storage-blob"]}, "sources": {}}
    for name in ["services/historical_corpus.py", "services/historical_training.py", "services/alpaca_api.py", "services/fred.py", "services/edgar.py", "services/treasury_yields.py", "services/web_research.py", "scripts/collect_historical_corpus.py", "requirements.txt"]:
        path = Path(name)
        manifest["implementation"]["sources"][name] = corpus.archive.put(path.read_bytes(), suffix=path.suffix.lstrip("."), content_type="text/plain; charset=utf-8")
    receipt = corpus.archive.put(gzip.compress(json_bytes(manifest), mtime=0), suffix="training-release.json.gz", content_type="application/gzip")
    summary = {k: manifest[k] for k in ["created_at_utc", "status", "task_counts", "derivation_counts", "canonicalization_counts", "dataset_rows", "unique_payload_bytes"]}
    summary.update(manifest=receipt, frozen_frames=len(frames))
    summary["latest_blob_pointer"] = corpus.archive.point("latest_training_release.json", summary)
    corpus._save(corpus.state / "training_release_latest_receipt.json", summary)
    status_path = Path("documents/training/HISTORICAL_CORPUS_STATUS_2026-10-07.json")
    corpus._save(status_path, summary)
    return summary


class FrozenTrainingCorpus:
    """Partition reader for the shared research harness; no live-source fallback."""
    def __init__(self, archive: ImmutableBlobArchive, receipt: dict):
        self.archive = archive
        self.manifest = json.loads(gzip.decompress(archive.get(receipt)))

    def partitions(self, dataset: str, *, frame_id: str, mode: str = "strict") -> Iterator[pd.DataFrame]:
        if mode not in {"strict", "reconstructed"}:
            raise ValueError("Unknown historical input policy")
        view = next(f for f in self.manifest["frames"] if f["id"] == frame_id)
        cutoff = pd.Timestamp(view["cutoff_utc"])
        macro_parts = []
        seen_ids: set[str] = set()
        for obj in self.manifest["objects"]:
            if obj.get("dataset") != dataset or obj.get("visibility") in {"outcomes_only", "catalog_asof_only", "revised_history_only"}:
                continue
            bounds = obj.get("time_bounds", {}).get("available_at")
            if bounds and pd.Timestamp(bounds[0]) > cutoff:
                continue
            frame = normalize_historical_availability(pd.read_parquet(BytesIO(self.archive.get(obj))), dataset)
            if frame.empty:
                continue
            if "available_at" in frame:
                frame = frame[pd.to_datetime(frame["available_at"], utc=True, errors="coerce") <= cutoff].copy()
            elif mode == "reconstructed" and "published_at" in frame:
                frame = frame[pd.to_datetime(frame["published_at"], utc=True, errors="coerce") <= cutoff].copy()
                frame = frame.drop(columns=[c for c in ["points", "num_comments", "score", "children", "acquired_at_utc"] if c in frame])
            else:
                continue
            if dataset == "macro_vintage_observations":
                macro_parts.append(frame)
                continue
            identity = next((c for c in ["objectID", "GLOBALEVENTID", "id"] if c in frame), None)
            if identity:
                frame = frame[~frame[identity].astype(str).isin(seen_ids)].drop_duplicates(identity).copy()
                seen_ids.update(frame[identity].astype(str))
            if not frame.empty:
                yield frame
        if macro_parts:
            # A revision can be in a later API page/vintage interval. Select
            # across the complete frozen source set, not independently per page.
            frame = pd.concat(macro_parts, ignore_index=True).sort_values("available_at").drop_duplicates(["series_id", "observation_date"], keep="last")
            for _, series in frame.groupby("series_id", sort=False):
                yield series

    def labels(self, dataset: str, *, split: str, eligible_only: bool = True) -> Iterator[pd.DataFrame]:
        if not dataset.endswith("_forward_returns"):
            raise ValueError("Labels must come from a separate forward-return dataset")
        for obj in self.manifest["objects"]:
            if obj.get("dataset") == dataset:
                if obj.get("derivation", {}).get("version", 0) < 4:
                    raise ValueError("Target partition predates the current availability contract; open the latest release")
                frame = pd.read_parquet(BytesIO(self.archive.get(obj)))
                selected = frame["split"].eq(split)
                if eligible_only:
                    selected &= frame["fit_eligible"]
                yield frame[selected].copy()
