from __future__ import annotations

import re
from typing import Any

import pandas as pd


_URL_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)(20\d{2})/([01]\d)/([0-3]\d)(?:/|$)"),
    re.compile(r"(?:^|/)(20\d{2})-([01]\d)-([0-3]\d)(?:[/?#._-]|$)"),
    re.compile(r"(?:[/?#._-])(20\d{2})[_-]([01]\d)[_-]([0-3]\d)(?:[/?#._-]|$)"),
    re.compile(r"(?:[/?#._-])(20\d{2})([01]\d)([0-3]\d)(?:[/?#._-]|$)"),
)


def _coerce_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _as_utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return pd.NaT
    return ts


def _relative_published_at(value: Any, *, asof_time_utc: Any = None) -> pd.Timestamp:
    text = _coerce_text(value).lower()
    if not text:
        return pd.NaT
    asof_ts = _as_utc_timestamp(asof_time_utc)
    if pd.isna(asof_ts):
        asof_ts = pd.Timestamp.utcnow()
    if text in {"just now", "now"}:
        return asof_ts
    if text in {"yesterday", "1 day ago", "a day ago"}:
        return asof_ts - pd.Timedelta(days=1)
    match = re.search(
        r"\b(?P<count>\d+|a|an)\s+(?P<unit>minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago\b",
        text,
    )
    if not match:
        return pd.NaT
    raw_count = match.group("count")
    count = 1 if raw_count in {"a", "an"} else int(raw_count)
    unit = match.group("unit")
    if unit.startswith("minute"):
        return asof_ts - pd.Timedelta(minutes=count)
    if unit.startswith("hour"):
        return asof_ts - pd.Timedelta(hours=count)
    if unit.startswith("day"):
        return asof_ts - pd.Timedelta(days=count)
    if unit.startswith("week"):
        return asof_ts - pd.Timedelta(weeks=count)
    if unit.startswith("month"):
        return asof_ts - pd.Timedelta(days=30 * count)
    return asof_ts - pd.Timedelta(days=365 * count)


def infer_published_at_from_url(url: Any) -> pd.Timestamp:
    text = _coerce_text(url)
    if not text:
        return pd.NaT
    for pattern in _URL_DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year, month, day = match.groups()
        ts = _as_utc_timestamp(f"{year}-{month}-{day}")
        if pd.notna(ts):
            return ts
    return pd.NaT


def coerce_article_published_at(
    value: Any,
    *,
    url: Any = "",
    asof_time_utc: Any = None,
) -> pd.Timestamp:
    published_at = _as_utc_timestamp(value)
    if pd.isna(published_at):
        published_at = _relative_published_at(value, asof_time_utc=asof_time_utc)
    if pd.isna(published_at):
        published_at = infer_published_at_from_url(url)
    if pd.isna(published_at):
        return pd.NaT
    asof_ts = _as_utc_timestamp(asof_time_utc) if asof_time_utc is not None else pd.NaT
    if pd.notna(asof_ts) and published_at > asof_ts + pd.Timedelta(days=2):
        return pd.NaT
    return published_at


def article_age_hours(published_at: Any, *, asof_time_utc: Any) -> float | None:
    published_ts = _as_utc_timestamp(published_at)
    asof_ts = _as_utc_timestamp(asof_time_utc)
    if pd.isna(published_ts) or pd.isna(asof_ts):
        return None
    return max(float((asof_ts - published_ts).total_seconds()) / 3600.0, 0.0)


def is_recent_for_attention(
    published_at: Any,
    *,
    asof_time_utc: Any,
    max_age_days: int = 21,
    include_undated: bool = True,
) -> bool:
    age_hours = article_age_hours(published_at, asof_time_utc=asof_time_utc)
    if age_hours is None:
        return bool(include_undated)
    return age_hours <= max(float(max_age_days), 0.0) * 24.0


def is_current_for_attention(
    published_at: Any,
    *,
    asof_time_utc: Any,
    max_age_hours: int = 72,
    include_undated: bool = False,
) -> bool:
    age_hours = article_age_hours(published_at, asof_time_utc=asof_time_utc)
    if age_hours is None:
        return bool(include_undated)
    return age_hours <= max(float(max_age_hours), 0.0)


__all__ = [
    "article_age_hours",
    "coerce_article_published_at",
    "infer_published_at_from_url",
    "is_current_for_attention",
    "is_recent_for_attention",
]
