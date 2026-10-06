from __future__ import annotations

import os
from typing import Any

import pandas as pd

from . import pipeline_store


def _filter_frame(frame: pd.DataFrame, filters: dict[str, object]) -> pd.DataFrame:
    out = frame.copy()
    for column, expected in filters.items():
        if column not in out.columns:
            return pd.DataFrame(columns=out.columns)
        if column == "symbol":
            mask = out[column].astype(str).str.upper().str.strip() == str(expected or "").upper().strip()
        else:
            mask = out[column] == expected
        out = out[mask].copy()
        if out.empty:
            break
    return out.reset_index(drop=True)


def _full_scan_row_limit() -> int:
    try:
        return max(int(os.getenv("PIPELINE_SLICE_FULL_SCAN_MAX_ROWS", "250000")), 0)
    except (TypeError, ValueError):
        return 250_000


def load_latest_dataset_frame_slice(
    dataset_name: str,
    *,
    filters: dict[str, object],
) -> tuple[pd.DataFrame, Any | None]:
    """Load an exact-match slice without pulling a large global legacy artifact.

    Current runtimes delegate to the partition-aware pipeline store. The bounded
    fallback keeps older API images safe during staggered rollouts: cached frames and
    small artifacts remain readable, while large unpartitioned artifacts return empty
    so the data-access layer can use its existing on-demand source.
    """
    normalized = {str(key).strip(): value for key, value in filters.items() if str(key).strip()}
    if not normalized:
        raise ValueError("At least one dataset slice filter is required.")

    native_loader = getattr(pipeline_store, "load_latest_dataset_frame_slice", None)
    if callable(native_loader):
        return native_loader(dataset_name, filters=normalized)

    metadata = pipeline_store.latest_dataset_metadata(dataset_name)
    if metadata is None:
        return pd.DataFrame(), None
    cached_reader = getattr(pipeline_store, "_read_local_frame_cache", None)
    cached = cached_reader(metadata) if callable(cached_reader) else None
    if isinstance(cached, pd.DataFrame):
        return _filter_frame(cached, normalized), metadata
    if int(getattr(metadata, "row_count", 0) or 0) > _full_scan_row_limit():
        return pd.DataFrame(), metadata
    frame, resolved_metadata = pipeline_store.load_latest_dataset_frame(dataset_name)
    return _filter_frame(frame, normalized), resolved_metadata


__all__ = ["load_latest_dataset_frame_slice"]
