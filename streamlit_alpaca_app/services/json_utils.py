from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def to_jsonable(value: Any) -> Any:
    """Normalize supported data values recursively for JSON transport."""
    if isinstance(value, pd.DataFrame):
        return frame_to_records(value)
    if isinstance(value, (pd.Series, pd.Index, np.ndarray)):
        return to_jsonable(value.tolist())
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if pd.api.types.is_scalar(value) and pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (datetime, date, pd.Timedelta)):
        return value.isoformat()
    return value


def to_list(value: Any) -> list[Any]:
    """Convert common container-like values without relying on truthiness."""
    if isinstance(value, list):
        return value
    if isinstance(value, pd.DataFrame):
        return frame_to_records(value)
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    if isinstance(value, (pd.Series, pd.Index, np.ndarray)):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return []


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [to_jsonable(row) for row in frame.to_dict(orient="records")]


__all__ = ["frame_to_records", "to_jsonable", "to_list"]
