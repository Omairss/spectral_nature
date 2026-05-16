from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def _jsonable_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _jsonable_scalar(value.item())
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def to_jsonable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return frame_to_records(value)
    if isinstance(value, pd.Series):
        return [_jsonable_scalar(item) for item in value.tolist()]
    if isinstance(value, np.ndarray):
        return [to_jsonable(item) for item in value.tolist()]
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return _jsonable_scalar(value)


def to_list(value: Any) -> list[Any]:
    """Convert common container-like values without relying on truthiness."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, pd.Series):
        return value.tolist()
    if isinstance(value, pd.Index):
        return value.tolist()
    if isinstance(value, np.ndarray):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    jsonable = to_jsonable(value)
    return jsonable if isinstance(jsonable, list) else []


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append({str(key): to_jsonable(value) for key, value in row.items()})
    return records


__all__ = ["frame_to_records", "to_jsonable", "to_list"]
