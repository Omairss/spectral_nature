from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_datetime64tz_dtype

from .fred import FRED_CATEGORY_BLURBS, fred_specs_by_category


APP_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = APP_ROOT / "cache"
CACHE_DATA_ROOT = CACHE_ROOT / "data"
CACHE_POLICY_PATH = CACHE_ROOT / "cache_policy.json"

DEFAULT_CACHE_POLICY = {
    "default_stale_minutes": 60,
    "datasets": {
        "account": 5,
        "positions": 5,
        "portfolio_timeseries": 30,
        "holding_roc": 30,
        "daily_movers": 10,
        "momentum_profiles": 30,
        "correlation_phase_shift": 30,
        "commodity_regime": 30,
        "price_history": 60,
        "option_chain": 15,
        "option_surface": 15,
        "quarterly_fundamentals": 1440,
        "asset_metadata": 10080,
        "recent_news": 60,
        "fred_dashboard": 1440,
    },
}


@dataclass(frozen=True)
class CacheTarget:
    dataset: str
    cache_key: str

    @property
    def bundle_dir(self) -> Path:
        return CACHE_DATA_ROOT / _slug(self.dataset) / _slug(self.cache_key)

    @property
    def meta_path(self) -> Path:
        return self.bundle_dir / "_meta.json"


def cache_data_root() -> Path:
    return CACHE_DATA_ROOT


def cache_policy_path() -> Path:
    return CACHE_POLICY_PATH


def cache_bundle_dir(dataset: str, cache_key: str) -> Path:
    return CacheTarget(dataset, cache_key).bundle_dir


def cache_bundle_exists(dataset: str, cache_key: str, required_files: list[str] | None = None) -> bool:
    if _cache_disabled():
        return False
    bundle_dir = cache_bundle_dir(dataset, cache_key)
    required = required_files or []
    if required:
        return all((bundle_dir / name).exists() for name in required)
    return bundle_dir.exists() and any(bundle_dir.iterdir())


def load_cache_policy() -> dict[str, Any]:
    policy = {
        "default_stale_minutes": int(DEFAULT_CACHE_POLICY["default_stale_minutes"]),
        "datasets": dict(DEFAULT_CACHE_POLICY["datasets"]),
    }
    if not CACHE_POLICY_PATH.exists():
        return policy

    try:
        raw = json.loads(CACHE_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return policy

    default_minutes = raw.get("default_stale_minutes")
    if isinstance(default_minutes, (int, float)) and default_minutes >= 0:
        policy["default_stale_minutes"] = int(default_minutes)

    dataset_policy = raw.get("datasets", {})
    if isinstance(dataset_policy, dict):
        for name, minutes in dataset_policy.items():
            if isinstance(minutes, (int, float)) and minutes >= 0:
                policy["datasets"][str(name)] = int(minutes)
    return policy


def stale_after_minutes(dataset: str) -> int:
    policy = load_cache_policy()
    datasets = policy.get("datasets", {})
    if dataset in datasets:
        return int(datasets[dataset])
    return int(policy.get("default_stale_minutes", DEFAULT_CACHE_POLICY["default_stale_minutes"]))


def dataset_scope(label: str, secret_value: str) -> str:
    digest = hashlib.sha1(str(secret_value or "").encode("utf-8")).hexdigest()[:10]
    return f"{_slug(label)}-{digest}"


def cached_frame(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], pd.DataFrame],
    *,
    force_refresh: bool = False,
    version: int = 1,
) -> pd.DataFrame:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return (bundle_dir / "data.csv").exists()

    def read_cache(bundle_dir: Path) -> pd.DataFrame:
        return _read_frame(bundle_dir / "data.csv")

    def write_cache(bundle_dir: Path, payload: pd.DataFrame) -> None:
        _write_frame(bundle_dir / "data.csv", payload)

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def cached_scalar_dict(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], dict[str, Any]],
    *,
    force_refresh: bool = False,
    version: int = 1,
) -> dict[str, Any]:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return (bundle_dir / "values.csv").exists()

    def read_cache(bundle_dir: Path) -> dict[str, Any]:
        return _read_scalar_dict(bundle_dir / "values.csv")

    def write_cache(bundle_dir: Path, payload: dict[str, Any]) -> None:
        _write_scalar_dict(bundle_dir / "values.csv", payload)

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def cached_frame_dict(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], dict[str, pd.DataFrame]],
    *,
    keys: list[str],
    force_refresh: bool = False,
    version: int = 1,
) -> dict[str, pd.DataFrame]:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return any((bundle_dir / f"{_slug(name)}.csv").exists() for name in keys)

    def read_cache(bundle_dir: Path) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for name in keys:
            path = bundle_dir / f"{_slug(name)}.csv"
            out[name] = _read_frame(path) if path.exists() else pd.DataFrame()
        return out

    def write_cache(bundle_dir: Path, payload: dict[str, pd.DataFrame]) -> None:
        for name in keys:
            _write_frame(bundle_dir / f"{_slug(name)}.csv", payload.get(name, pd.DataFrame()))

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def cached_option_chain(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], tuple[list[str], pd.DataFrame, pd.DataFrame]],
    *,
    force_refresh: bool = False,
    version: int = 1,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return (bundle_dir / "expirations.csv").exists()

    def read_cache(bundle_dir: Path) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
        expirations = _read_list(bundle_dir / "expirations.csv")
        calls = _read_frame(bundle_dir / "calls.csv") if (bundle_dir / "calls.csv").exists() else pd.DataFrame()
        puts = _read_frame(bundle_dir / "puts.csv") if (bundle_dir / "puts.csv").exists() else pd.DataFrame()
        return expirations, calls, puts

    def write_cache(bundle_dir: Path, payload: tuple[list[str], pd.DataFrame, pd.DataFrame]) -> None:
        expirations, calls, puts = payload
        _write_list(bundle_dir / "expirations.csv", expirations)
        _write_frame(bundle_dir / "calls.csv", calls)
        _write_frame(bundle_dir / "puts.csv", puts)

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def cached_news_payload(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], dict[str, object]],
    *,
    force_refresh: bool = False,
    version: int = 1,
) -> dict[str, object]:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return (bundle_dir / "values.csv").exists()

    def read_cache(bundle_dir: Path) -> dict[str, object]:
        values = _read_scalar_dict(bundle_dir / "values.csv")
        articles_path = bundle_dir / "articles.csv"
        articles = _read_frame(articles_path) if articles_path.exists() else pd.DataFrame()
        return {
            "articles": articles,
            "fallback_summary": values.get("fallback_summary"),
            "source": values.get("source"),
        }

    def write_cache(bundle_dir: Path, payload: dict[str, object]) -> None:
        _write_scalar_dict(
            bundle_dir / "values.csv",
            {
                "fallback_summary": payload.get("fallback_summary"),
                "source": payload.get("source"),
            },
        )
        _write_frame(bundle_dir / "articles.csv", payload.get("articles", pd.DataFrame()))

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def cached_fred_dashboard(
    dataset: str,
    cache_key: str,
    fetcher: Callable[[], dict[str, object]],
    *,
    force_refresh: bool = False,
    version: int = 1,
) -> dict[str, object]:
    target = CacheTarget(dataset, cache_key)

    def has_cache(bundle_dir: Path) -> bool:
        return (bundle_dir / "summary.csv").exists() and (bundle_dir / "observations.csv").exists()

    def read_cache(bundle_dir: Path) -> dict[str, object]:
        summary = _read_frame(bundle_dir / "summary.csv")
        series_index = _read_frame(bundle_dir / "series_index.csv")
        observations = _read_frame(bundle_dir / "observations.csv")
        release_index_path = bundle_dir / "release_index.csv"
        release_index = _read_frame(release_index_path) if release_index_path.exists() else pd.DataFrame()

        metadata_by_id = {
            str(row["series_id"]): {key: value for key, value in row.dropna().to_dict().items()}
            for _, row in series_index.iterrows()
            if "series_id" in row
        }

        series_data: dict[str, pd.DataFrame] = {}
        if not observations.empty and "series_id" in observations.columns:
            for series_id, frame in observations.groupby("series_id", dropna=False):
                series_data[str(series_id)] = frame[["date", "value"]].reset_index(drop=True)

        specs_by_category = fred_specs_by_category()
        for specs in specs_by_category.values():
            for spec in specs:
                series_data.setdefault(spec.series_id, pd.DataFrame(columns=["date", "value"]))
                metadata_by_id.setdefault(spec.series_id, {})

        return {
            "summary": summary,
            "series_index": series_index,
            "observations": observations,
            "release_index": release_index,
            "metadata": metadata_by_id,
            "series_data": series_data,
            "specs_by_category": specs_by_category,
            "category_blurbs": dict(FRED_CATEGORY_BLURBS),
        }

    def write_cache(bundle_dir: Path, payload: dict[str, object]) -> None:
        _write_frame(bundle_dir / "summary.csv", payload.get("summary", pd.DataFrame()))
        _write_frame(bundle_dir / "series_index.csv", payload.get("series_index", pd.DataFrame()))
        _write_frame(bundle_dir / "observations.csv", payload.get("observations", pd.DataFrame()))
        _write_frame(bundle_dir / "release_index.csv", payload.get("release_index", pd.DataFrame()))

    return _load_or_refresh(target, fetcher, read_cache, write_cache, has_cache, force_refresh, version)


def _load_or_refresh(
    target: CacheTarget,
    fetcher: Callable[[], Any],
    read_cache: Callable[[Path], Any],
    write_cache: Callable[[Path, Any], None],
    has_cache: Callable[[Path], bool],
    force_refresh: bool,
    version: int,
) -> Any:
    if _cache_disabled():
        return fetcher()

    bundle_dir = target.bundle_dir
    cache_present = has_cache(bundle_dir)

    if cache_present and not force_refresh and _is_fresh(target, version):
        try:
            return read_cache(bundle_dir)
        except Exception:
            cache_present = False

    stale_payload = None
    stale_payload_loaded = False
    if cache_present:
        try:
            stale_payload = read_cache(bundle_dir)
            stale_payload_loaded = True
        except Exception:
            stale_payload = None

    try:
        payload = fetcher()
    except Exception:
        if stale_payload_loaded:
            return stale_payload
        raise

    bundle_dir.mkdir(parents=True, exist_ok=True)
    write_cache(bundle_dir, payload)
    _write_meta(target, version)
    return payload


def _is_fresh(target: CacheTarget, version: int) -> bool:
    meta = _read_meta(target.meta_path)
    if not meta:
        return False
    if int(meta.get("version", -1)) != int(version):
        return False

    cached_at = pd.to_datetime(meta.get("cached_at"), utc=True, errors="coerce")
    if pd.isna(cached_at):
        return False

    age_seconds = (pd.Timestamp.now(tz="UTC") - cached_at).total_seconds()
    return age_seconds <= stale_after_minutes(target.dataset) * 60


def _write_meta(target: CacheTarget, version: int) -> None:
    target.bundle_dir.mkdir(parents=True, exist_ok=True)
    target.meta_path.write_text(
        json.dumps(
            {
                "dataset": target.dataset,
                "cache_key": target.cache_key,
                "version": int(version),
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "stale_after_minutes": stale_after_minutes(target.dataset),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _read_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _frame_schema_path(path: Path) -> Path:
    return path.with_suffix(".schema.json")


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_frame = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    schema = _frame_schema(safe_frame)
    for column in schema["json_columns"]:
        if column in safe_frame.columns:
            safe_frame[column] = safe_frame[column].map(_serialize_optional_json)
    safe_frame.to_csv(path, index=False)
    _frame_schema_path(path).write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")


def _read_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    schema_path = _frame_schema_path(path)
    if not schema_path.exists():
        return frame

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return frame

    datetime_columns = schema.get("datetime_columns", []) or []
    timezone_columns = set(schema.get("timezone_aware_columns", []) or [])
    json_columns = schema.get("json_columns", []) or []

    for column in datetime_columns:
        if column not in frame.columns:
            continue
        if column in timezone_columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        else:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")

    for column in json_columns:
        if column in frame.columns:
            frame[column] = frame[column].map(_deserialize_optional_json)
    return frame


def _write_scalar_dict(path: Path, payload: dict[str, Any]) -> None:
    rows = [{"key": str(key), "value": json.dumps(value, default=_json_default)} for key, value in payload.items()]
    pd.DataFrame(rows, columns=["key", "value"]).to_csv(path, index=False)


def _read_scalar_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if frame.empty:
        return {}
    out: dict[str, Any] = {}
    for _, row in frame.iterrows():
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        raw = row.get("value")
        if pd.isna(raw):
            out[key] = None
            continue
        out[key] = _deserialize_optional_json(raw)
    return out


def _write_list(path: Path, values: list[str]) -> None:
    pd.DataFrame({"value": list(values or [])}).to_csv(path, index=False)


def _read_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    if frame.empty or "value" not in frame.columns:
        return []
    return [str(value) for value in frame["value"].dropna().tolist()]


def _frame_schema(frame: pd.DataFrame) -> dict[str, list[str]]:
    datetime_columns: list[str] = []
    timezone_aware_columns: list[str] = []
    json_columns: list[str] = []

    for column in frame.columns:
        series = frame[column]
        if is_datetime64tz_dtype(series):
            datetime_columns.append(str(column))
            timezone_aware_columns.append(str(column))
            continue
        if is_datetime64_any_dtype(series):
            datetime_columns.append(str(column))
            continue

        sample = _first_non_missing(series)
        if isinstance(sample, (dict, list, tuple)):
            json_columns.append(str(column))

    return {
        "datetime_columns": datetime_columns,
        "timezone_aware_columns": timezone_aware_columns,
        "json_columns": json_columns,
    }


def _first_non_missing(series: pd.Series) -> Any:
    for value in series.tolist():
        if _is_missing(value):
            continue
        return value
    return None


def _serialize_optional_json(value: Any) -> str:
    if _is_missing(value):
        return ""
    return json.dumps(value, default=_json_default)


def _deserialize_optional_json(value: Any) -> Any:
    if _is_missing(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return value


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return bool(pd.isna(value)) if not isinstance(value, (dict, list, tuple)) else False


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not text:
        text = "default"
    if len(text) > 96:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        text = f"{text[:80].rstrip('-._')}-{digest}"
    return text


def _cache_disabled() -> bool:
    raw = (os.getenv("APP_DISABLE_CACHE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
