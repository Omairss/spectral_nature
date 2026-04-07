from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import json
from typing import Any

import numpy as np
import pandas as pd

from services.entity_taxonomy import business_focus_label_from_taxonomy_row, load_entity_taxonomy_frame


HORIZON_PERIODS: dict[str, int] = {
    "1d": 1,
    "1w": 5,
    "1mo": 21,
    "3mo": 63,
    "1yr": 252,
}

HORIZON_ALIASES: dict[str, str] = {
    "1d": "1d",
    "1day": "1d",
    "1w": "1w",
    "1wk": "1w",
    "1week": "1w",
    "1m": "1mo",
    "1mo": "1mo",
    "1mon": "1mo",
    "1month": "1mo",
    "3m": "3mo",
    "3mo": "3mo",
    "3mon": "3mo",
    "3month": "3mo",
    "3months": "3mo",
    "1y": "1yr",
    "1yr": "1yr",
    "1year": "1yr",
}

SENSITIVITY_PRESETS: dict[str, dict[str, float | str]] = {
    "aggressive": {
        "label": "Aggressive",
        "residual_zscore_threshold": 1.25,
        "min_attention_score": 25.0,
        "high_priority_threshold": 65.0,
    },
    "balanced": {
        "label": "Balanced",
        "residual_zscore_threshold": 2.0,
        "min_attention_score": 40.0,
        "high_priority_threshold": 75.0,
    },
    "conservative": {
        "label": "Conservative",
        "residual_zscore_threshold": 2.75,
        "min_attention_score": 55.0,
        "high_priority_threshold": 85.0,
    },
}

EXPECTATION_COLUMNS = [
    "asof_time_utc",
    "symbol",
    "horizon",
    "close",
    "observed_return_pct",
    "trend_expected_return_pct",
    "peer_expected_return_pct",
    "benchmark_expected_return_pct",
    "blended_expected_return_pct",
    "residual_return_pct",
    "residual_zscore",
    "trend_zscore",
    "peer_zscore",
    "benchmark_zscore",
    "vol_20_ann_pct",
    "momentum_score",
    "momentum_roc_score",
    "correlation_now",
    "correlation_roc",
    "peer_group_id",
    "peer_group_name",
    "benchmark",
    "trajectory_model_version",
    "peer_model_version",
    "schema_version",
]

ANOMALY_EVENT_COLUMNS = [
    "event_id",
    "asof_time_utc",
    "entity_type",
    "entity_id",
    "parent_entity_type",
    "parent_entity_id",
    "horizon",
    "anomaly_type",
    "direction",
    "observed_value",
    "expected_value",
    "residual_value",
    "residual_zscore",
    "severity_score",
    "impact_score",
    "relevance_score",
    "confidence_score",
    "attention_score",
    "attention_score_v2_shadow",
    "attention_score_v2",
    "persistence_score",
    "novelty_score",
    "macro_alignment_score",
    "macro_conflict_score",
    "macro_signal_count",
    "macro_data_fresh",
    "portfolio_exposure_weight",
    "peer_group_id",
    "peer_group_name",
    "benchmark",
    "regime_label",
    "why_now_code",
    "why_now_text",
    "supporting_datasets",
    "linked_news_count",
    "linked_news_ids",
    "drilldown_section",
    "drilldown_params_json",
    "status",
    "schema_version",
]

ATTENTION_ROLLUP_COLUMNS = [
    "asof_time_utc",
    "rollup_type",
    "rollup_id",
    "rollup_name",
    "active_event_count",
    "high_priority_event_count",
    "top_event_id",
    "top_attention_score",
    "breadth_positive",
    "breadth_negative",
    "mean_residual_zscore",
    "net_attention_score",
    "summary_text",
    "schema_version",
]

ATTENTION_FEED_COLUMNS = [
    "feed_rank",
    "event_id",
    "asof_time_utc",
    "card_type",
    "title",
    "subtitle",
    "entity_type",
    "entity_id",
    "horizon",
    "direction",
    "peer_group_name",
    "regime_label",
    "attention_score",
    "severity_score",
    "impact_score",
    "confidence_score",
    "observed_value",
    "expected_value",
    "residual_value",
    "residual_zscore",
    "story_text",
    "why_now_text",
    "expected_vs_observed_text",
    "next_best_action",
    "drilldown_section",
    "drilldown_params_json",
    "linked_news_count",
    "status",
    "schema_version",
]

TAXONOMY_PEER_GROUP_CATALOG_COLUMNS = [
    "asof_time_utc",
    "peer_group_id",
    "peer_group_name",
    "peer_group_type",
    "benchmark",
    "entity_type",
    "member_count",
    "sample_entity_ids_json",
    "source",
    "schema_version",
]


@dataclass(frozen=True)
class ExpectationConfig:
    residual_lookback_days: int = 252
    horizons: tuple[str, ...] = ("1d", "1w", "1mo", "3mo", "1yr")
    trend_weight: float = 0.40
    peer_weight: float = 0.35
    benchmark_weight: float = 0.25
    min_history_rows: int = 21
    schema_version: str = "v1"


@dataclass(frozen=True)
class AttentionConfig:
    residual_zscore_threshold: float = 2.0
    residual_zscore_thresholds: dict[str, float] | None = None
    min_attention_score: float = 0.0
    high_priority_threshold: float = 75.0
    news_lookback_days: int = 3
    persistence_periods: int = 2
    macro_shadow_enabled: bool = True
    macro_live_enabled: bool = False
    macro_shadow_weight: float = 0.12
    macro_staleness_hours: float = 48.0
    schema_version: str = "v1"


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _ensure_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = False if column == "macro_data_fresh" else np.nan
    return out


def _normalize_symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def _coerce_timestamp(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _clamp(value: Any, low: float, high: float) -> float:
    numeric = float(pd.to_numeric(value, errors="coerce"))
    if not np.isfinite(numeric):
        return low
    return max(low, min(high, numeric))


def _macro_context_lookup(macro_context: pd.DataFrame | None) -> dict[tuple[str, str], dict[str, Any]]:
    if macro_context is None or macro_context.empty or "symbol" not in macro_context.columns:
        return {}
    frame = macro_context.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    if "horizon" in frame.columns:
        frame["horizon"] = frame["horizon"].map(normalize_horizon)
    else:
        frame["horizon"] = ""
    if "asof_time_utc" in frame.columns:
        frame["asof_time_utc"] = pd.to_datetime(frame["asof_time_utc"], utc=True, errors="coerce")
        frame = frame.sort_values("asof_time_utc", ascending=False, na_position="last")
    for column in ["macro_alignment_score", "macro_conflict_score", "macro_signal_count", "macro_staleness_hours"]:
        if column not in frame.columns:
            frame[column] = np.nan
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in frame.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        horizon = normalize_horizon(row.get("horizon"))
        if not symbol:
            continue
        key = (symbol, horizon)
        if key in lookup:
            continue
        lookup[key] = {
            "macro_alignment_score": float(pd.to_numeric(row.get("macro_alignment_score"), errors="coerce")),
            "macro_conflict_score": float(pd.to_numeric(row.get("macro_conflict_score"), errors="coerce")),
            "macro_signal_count": int(pd.to_numeric(row.get("macro_signal_count"), errors="coerce")) if pd.notna(pd.to_numeric(row.get("macro_signal_count"), errors="coerce")) else 0,
            "macro_staleness_hours": float(pd.to_numeric(row.get("macro_staleness_hours"), errors="coerce")),
        }
        fallback_key = (symbol, "")
        if fallback_key not in lookup:
            lookup[fallback_key] = dict(lookup[key])
    return lookup


def _slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() else "_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _humanize_slug(value: Any) -> str:
    slug = _slugify(value)
    if not slug:
        return ""
    return " ".join(token.capitalize() for token in slug.split("_") if token)


def normalize_horizon(value: Any) -> str:
    token = str(value or "").strip().lower()
    return HORIZON_ALIASES.get(token, token)


def normalize_horizons(values: list[str] | tuple[str, ...] | set[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_horizon(value)
        if not normalized or normalized not in HORIZON_PERIODS or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return tuple(out)


def attention_preset(name: str | None) -> dict[str, float | str]:
    key = str(name or "balanced").strip().lower()
    return dict(SENSITIVITY_PRESETS.get(key, SENSITIVITY_PRESETS["balanced"]))


def _attention_drilldown_params(
    symbol: Any,
    horizon: Any,
    *,
    entity_type: Any = "symbol",
    peer_group_name: Any = None,
) -> dict[str, str]:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_horizon = normalize_horizon(horizon)
    normalized_entity_type = str(entity_type or "symbol").strip().lower()
    normalized_peer_group = str(peer_group_name or "").strip()

    params: dict[str, str] = {"ticker": normalized_symbol}
    if normalized_horizon in HORIZON_PERIODS:
        params["horizon"] = normalized_horizon

    is_commodity = normalized_entity_type == "commodity_symbol"
    if is_commodity:
        params["market_view"] = "Commodity Section"
        params["commodity_focus"] = normalized_peer_group or "Broad Commodity Market"
        return params

    params["market_view"] = "Markets"
    if normalized_peer_group and normalized_peer_group not in {"All Market", "Market", "Unknown"}:
        params["business_filter"] = normalized_peer_group
    return params


def _cross_sectional_zscore(series: pd.Series) -> pd.Series:
    values = _coerce_numeric(series)
    valid = values.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index, dtype=float)
    std = float(valid.std(ddof=0))
    if not np.isfinite(std) or std <= 1e-9:
        return pd.Series(0.0, index=series.index, dtype=float)
    mean = float(valid.mean())
    return (values - mean) / std


def _weighted_blend(values: dict[str, float | int | None], weights: dict[str, float]) -> float:
    weighted_total = 0.0
    active_weight = 0.0
    for key, weight in weights.items():
        value = values.get(key)
        if value is None or not np.isfinite(value):
            continue
        weighted_total += float(value) * float(weight)
        active_weight += float(weight)
    if active_weight <= 0:
        return np.nan
    return weighted_total / active_weight


def _blend_with_optional_anchor(
    base_values: dict[str, float | int | None],
    base_weights: dict[str, float],
    *,
    anchor_value: float | int | None,
    anchor_weight: float,
) -> float:
    base = _weighted_blend(base_values, base_weights)
    if anchor_value is None or not np.isfinite(anchor_value):
        return base
    return _weighted_blend({"base": base, "anchor": anchor_value}, {"base": max(1.0 - float(anchor_weight), 0.0), "anchor": float(anchor_weight)})


def _trend_expectation(row: pd.Series, horizon: str) -> float:
    horizon = normalize_horizon(horizon)
    ret_1w = float(pd.to_numeric(row.get("return_1w_pct"), errors="coerce"))
    ret_1m = float(pd.to_numeric(row.get("return_1m_pct"), errors="coerce"))
    ret_3m = float(pd.to_numeric(row.get("return_3m_pct"), errors="coerce"))
    ret_1y = float(pd.to_numeric(row.get("return_1y_pct"), errors="coerce"))

    if horizon == "1d":
        return _blend_with_optional_anchor(
            {
                "ret_1w": ret_1w / 5.0 if np.isfinite(ret_1w) else np.nan,
                "ret_1m": ret_1m / 21.0 if np.isfinite(ret_1m) else np.nan,
                "ret_3m": ret_3m / 63.0 if np.isfinite(ret_3m) else np.nan,
            },
            {"ret_1w": 0.60, "ret_1m": 0.30, "ret_3m": 0.10},
            anchor_value=ret_1y / 252.0 if np.isfinite(ret_1y) else np.nan,
            anchor_weight=0.05,
        )
    if horizon == "1w":
        return _blend_with_optional_anchor(
            {
                "ret_1w": ret_1w,
                "ret_1m": ret_1m * (5.0 / 21.0) if np.isfinite(ret_1m) else np.nan,
                "ret_3m": ret_3m * (5.0 / 63.0) if np.isfinite(ret_3m) else np.nan,
            },
            {"ret_1w": 0.55, "ret_1m": 0.30, "ret_3m": 0.15},
            anchor_value=ret_1y * (5.0 / 252.0) if np.isfinite(ret_1y) else np.nan,
            anchor_weight=0.10,
        )
    if horizon == "1mo":
        return _blend_with_optional_anchor(
            {
                "ret_1w": ret_1w * (21.0 / 5.0) if np.isfinite(ret_1w) else np.nan,
                "ret_1m": ret_1m,
                "ret_3m": ret_3m * (21.0 / 63.0) if np.isfinite(ret_3m) else np.nan,
            },
            {"ret_1w": 0.20, "ret_1m": 0.50, "ret_3m": 0.30},
            anchor_value=ret_1y * (21.0 / 252.0) if np.isfinite(ret_1y) else np.nan,
            anchor_weight=0.15,
        )
    if horizon == "3mo":
        return _blend_with_optional_anchor(
            {
                "ret_1m": ret_1m * (63.0 / 21.0) if np.isfinite(ret_1m) else np.nan,
                "ret_3m": ret_3m,
            },
            {"ret_1m": 0.35, "ret_3m": 0.65},
            anchor_value=ret_1y * (63.0 / 252.0) if np.isfinite(ret_1y) else np.nan,
            anchor_weight=0.25,
        )
    if horizon == "1yr":
        return _weighted_blend(
            {
                "ret_3m": ret_3m * (252.0 / 63.0) if np.isfinite(ret_3m) else np.nan,
                "ret_1y": ret_1y,
            },
            {"ret_3m": 0.30, "ret_1y": 0.70},
        )
    return np.nan


def _latest_volatility(price_history: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "timestamp", "close"}
    if price_history.empty or not required.issubset(set(price_history.columns)):
        return pd.DataFrame(columns=["symbol", "vol_20_ann_pct"])

    frame = price_history.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = _coerce_numeric(frame["close"])
    frame = frame.dropna(subset=["symbol", "timestamp", "close"]).sort_values(["symbol", "timestamp"])
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "vol_20_ann_pct"])

    parts: list[dict[str, object]] = []
    for symbol, chunk in frame.groupby("symbol", sort=False):
        close = _coerce_numeric(chunk["close"]).replace(0, np.nan)
        log_ret = np.log(close).diff()
        vol = log_ret.rolling(20).std(ddof=0) * np.sqrt(252.0) * 100.0
        parts.append({"symbol": symbol, "vol_20_ann_pct": float(vol.dropna().iloc[-1]) if vol.dropna().any() else np.nan})
    return pd.DataFrame(parts)


def _observed_returns(price_history: pd.DataFrame, horizons: tuple[str, ...], min_history_rows: int) -> pd.DataFrame:
    required = {"symbol", "timestamp", "close"}
    if price_history.empty or not required.issubset(set(price_history.columns)):
        return pd.DataFrame(columns=["symbol", "horizon", "asof_time_utc", "close", "observed_return_pct"])

    frame = price_history.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = _coerce_numeric(frame["close"])
    frame = frame.dropna(subset=["symbol", "timestamp", "close"]).sort_values(["symbol", "timestamp"])
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "asof_time_utc", "close", "observed_return_pct"])

    rows: list[dict[str, object]] = []
    for symbol, chunk in frame.groupby("symbol", sort=False):
        chunk = chunk.sort_values("timestamp").reset_index(drop=True)
        if len(chunk) < min_history_rows:
            continue
        close = chunk["close"].to_numpy(dtype=float)
        latest_close = float(close[-1])
        asof_time_utc = pd.to_datetime(chunk["timestamp"].iloc[-1], utc=True, errors="coerce")
        for horizon in horizons:
            normalized_horizon = normalize_horizon(horizon)
            periods = HORIZON_PERIODS.get(normalized_horizon)
            if periods is None or len(chunk) <= periods:
                continue
            base = float(close[-(periods + 1)])
            observed = np.nan if base == 0 else ((latest_close / base) - 1.0) * 100.0
            rows.append(
                {
                    "symbol": symbol,
                    "horizon": normalized_horizon,
                    "asof_time_utc": asof_time_utc,
                    "close": latest_close,
                    "observed_return_pct": observed,
                }
            )
    return pd.DataFrame(rows)


def _threshold_for_horizon(config: AttentionConfig, horizon: str, threshold_override: float | None = None, threshold_overrides: dict[str, float] | None = None) -> float:
    normalized_horizon = normalize_horizon(horizon)
    if threshold_overrides:
        override = threshold_overrides.get(normalized_horizon)
        if override is not None and np.isfinite(override):
            return max(float(override), 0.0)
    config_overrides = config.residual_zscore_thresholds or {}
    configured = config_overrides.get(normalized_horizon)
    if configured is not None and np.isfinite(configured):
        return max(float(configured), 0.0)
    if threshold_override is not None and np.isfinite(threshold_override):
        return max(float(threshold_override), 0.0)
    return max(float(config.residual_zscore_threshold), 0.0)


def _primary_peer_membership(peer_group_membership: pd.DataFrame) -> pd.DataFrame:
    required = {"entity_id", "peer_group_id", "peer_group_name"}
    if peer_group_membership.empty or not required.issubset(set(peer_group_membership.columns)):
        return pd.DataFrame(columns=["symbol", "peer_group_id", "peer_group_name", "benchmark"])

    frame = peer_group_membership.copy()
    frame["symbol"] = frame["entity_id"].map(_normalize_symbol)
    frame["peer_group_name"] = frame["peer_group_name"].astype(str)
    frame["_fallback_rank"] = frame["peer_group_name"].isin({"All Market", "Broad Commodity Market"}).astype(int)
    frame = frame.sort_values(["symbol", "_fallback_rank", "peer_group_name"]).drop_duplicates(subset=["symbol"], keep="first")
    keep = [col for col in ["symbol", "peer_group_id", "peer_group_name", "benchmark"] if col in frame.columns]
    return frame[keep].reset_index(drop=True)


def _peer_expected_returns(observed: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    if observed.empty or membership.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "peer_expected_return_pct", "peer_group_id", "peer_group_name", "benchmark"])

    base = observed.merge(membership, on="symbol", how="left")
    if base.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "peer_expected_return_pct", "peer_group_id", "peer_group_name", "benchmark"])

    rows: list[dict[str, object]] = []
    grouped = base.dropna(subset=["peer_group_id"]).groupby(["peer_group_id", "horizon"], sort=False)
    for (peer_group_id, horizon), chunk in grouped:
        values = _coerce_numeric(chunk["observed_return_pct"])
        total = float(values.sum(skipna=True))
        count = int(values.notna().sum())
        for _, row in chunk.iterrows():
            current = float(pd.to_numeric(row.get("observed_return_pct"), errors="coerce"))
            finite_current = np.isfinite(current)
            denominator = count - (1 if finite_current else 0)
            if denominator <= 0:
                peer_expected = np.nan
            else:
                peer_expected = (total - current) / denominator if finite_current else total / denominator
            rows.append(
                {
                    "symbol": row["symbol"],
                    "horizon": horizon,
                    "peer_expected_return_pct": peer_expected,
                    "peer_group_id": row.get("peer_group_id"),
                    "peer_group_name": row.get("peer_group_name"),
                    "benchmark": row.get("benchmark"),
                }
            )
    return pd.DataFrame(rows)


def _benchmark_expected_returns(observed: pd.DataFrame, phase_summary: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    if observed.empty or membership.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "benchmark_expected_return_pct", "correlation_now", "correlation_roc"])

    benchmark_rows = observed.rename(
        columns={
            "symbol": "benchmark",
            "observed_return_pct": "benchmark_observed_return_pct",
        }
    )
    benchmark_rows = benchmark_rows.dropna(subset=["benchmark", "horizon"]).copy()
    if benchmark_rows.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "benchmark_expected_return_pct", "correlation_now", "correlation_roc"])
    benchmark_rows["horizon"] = benchmark_rows["horizon"].astype(str)

    phase = phase_summary.copy()
    if not phase.empty:
        phase["symbol"] = phase["symbol"].map(_normalize_symbol)
        phase["benchmark"] = phase["benchmark"].map(_normalize_symbol)
    merged = membership.merge(phase, on=["symbol", "benchmark"], how="left") if not phase.empty else membership.copy()
    merged = merged.merge(benchmark_rows[["benchmark", "horizon", "benchmark_observed_return_pct"]], on="benchmark", how="left")
    if merged.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "benchmark_expected_return_pct", "correlation_now", "correlation_roc"])

    rows: list[dict[str, object]] = []
    for _, row in merged.iterrows():
        corr_now = float(pd.to_numeric(row.get("correlation_now"), errors="coerce"))
        benchmark_return = float(pd.to_numeric(row.get("benchmark_observed_return_pct"), errors="coerce"))
        expected = corr_now * benchmark_return if np.isfinite(corr_now) and np.isfinite(benchmark_return) else np.nan
        rows.append(
            {
                "symbol": row.get("symbol"),
                "horizon": str(row.get("horizon") or "").strip(),
                "benchmark_expected_return_pct": expected,
                "correlation_now": corr_now,
                "correlation_roc": float(pd.to_numeric(row.get("correlation_roc"), errors="coerce")),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["symbol", "horizon", "benchmark_expected_return_pct", "correlation_now", "correlation_roc"])
    out = out[out["horizon"].astype(str).str.len() > 0].copy()
    return out


def _position_weights(positions: pd.DataFrame | None) -> dict[str, float]:
    if positions is None or positions.empty or "symbol" not in positions.columns:
        return {}
    frame = positions.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    if "market_value" in frame.columns:
        frame["market_value"] = _coerce_numeric(frame["market_value"]).abs()
    elif "current_price" in frame.columns and "qty" in frame.columns:
        frame["market_value"] = _coerce_numeric(frame["current_price"]) * _coerce_numeric(frame["qty"]).abs()
    else:
        frame["market_value"] = 1.0
    frame = frame.dropna(subset=["symbol", "market_value"])
    total = float(frame["market_value"].sum())
    if total <= 0:
        return {}
    return {row["symbol"]: float(row["market_value"]) / total for _, row in frame.iterrows()}


def _symbols_from_payload(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("|", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        symbol = _normalize_symbol(item)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def _article_uid(row: pd.Series) -> str:
    url = str(row.get("url") or "").strip()
    if url:
        return sha1(url.encode("utf-8")).hexdigest()
    headline = str(row.get("headline") or row.get("title") or "").strip()
    published = str(row.get("published_at") or "").strip()
    source = str(row.get("source") or "").strip()
    return sha1(f"{headline}|{published}|{source}".encode("utf-8")).hexdigest()


def _recent_news_lookup(news_symbol_map: pd.DataFrame | None, asof_time_utc: pd.Timestamp, lookback_days: int) -> dict[str, dict[str, object]]:
    if news_symbol_map is None or news_symbol_map.empty:
        return {}

    frame = news_symbol_map.copy()
    frame["published_at"] = pd.to_datetime(frame.get("published_at"), utc=True, errors="coerce")
    cutoff = asof_time_utc - pd.Timedelta(days=max(int(lookback_days), 0))
    if "published_at" in frame.columns:
        frame = frame[(frame["published_at"].isna()) | (frame["published_at"] >= cutoff)].copy()

    lookup: dict[str, dict[str, object]] = {}
    for _, row in frame.iterrows():
        for symbol in _symbols_from_payload(row.get("symbols")):
            bucket = lookup.setdefault(symbol, {"count": 0, "ids": []})
            bucket["count"] = int(bucket["count"]) + 1
            bucket["ids"].append(_article_uid(row))
    return lookup


def _supporting_datasets(
    technical_signals_latest: pd.DataFrame | None,
    news_symbol_map: pd.DataFrame | None,
    positions: pd.DataFrame | None,
    macro_context: pd.DataFrame | None,
) -> str:
    datasets = ["price_expectations"]
    if technical_signals_latest is not None and not technical_signals_latest.empty:
        datasets.append("technical_signals_latest")
    if news_symbol_map is not None and not news_symbol_map.empty:
        datasets.append("news_symbol_map")
    if positions is not None and not positions.empty:
        datasets.append("positions")
    if macro_context is not None and not macro_context.empty:
        datasets.append("attention_macro_context_1d")
    return ",".join(datasets)


def _regime_lookup(technical_signals_latest: pd.DataFrame | None) -> dict[str, str]:
    if technical_signals_latest is None or technical_signals_latest.empty or "symbol" not in technical_signals_latest.columns:
        return {}
    frame = technical_signals_latest.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    return {
        str(row["symbol"]): str(row.get("regime") or "").strip()
        for _, row in frame.drop_duplicates(subset=["symbol"], keep="last").iterrows()
    }


def _technical_confirmation_score(direction: str, regime_label: str) -> float:
    label = str(regime_label or "").strip()
    if not label:
        return 40.0
    if direction == "up" and label in {"Trend breakout", "Trend continuation", "Resistance probe"}:
        return 100.0
    if direction == "down" and label in {"Deep pullback", "Support test"}:
        return 100.0
    return 65.0


def _classify_anomaly(row: pd.Series) -> str:
    residual = float(pd.to_numeric(row.get("residual_value"), errors="coerce"))
    corr_roc = float(pd.to_numeric(row.get("correlation_roc"), errors="coerce"))
    momentum_roc = float(pd.to_numeric(row.get("momentum_roc_score"), errors="coerce"))
    linked_news_count = int(row.get("linked_news_count") or 0)

    if linked_news_count > 0 and np.isfinite(residual) and abs(residual) >= 3.0:
        return "news_confirmed_move"
    if np.isfinite(corr_roc) and corr_roc <= -0.20 and np.isfinite(residual) and residual > 0:
        return "decoupling"
    if np.isfinite(corr_roc) and abs(corr_roc) >= 0.20:
        return "correlation_break"
    if np.isfinite(momentum_roc) and momentum_roc >= 0.15:
        return "momentum_acceleration"
    if np.isfinite(momentum_roc) and momentum_roc <= -0.15:
        return "momentum_deceleration"
    return "price_residual"


def _status_from_scores(residual_zscore: float, threshold: float) -> str:
    if not np.isfinite(residual_zscore):
        return "resolved"
    if abs(residual_zscore) >= threshold + 1.0:
        return "active"
    if abs(residual_zscore) >= threshold:
        return "cooling"
    return "resolved"


def _next_best_action(anomaly_type: str, entity_id: str, horizon: str) -> str:
    if anomaly_type in {"correlation_break", "decoupling"}:
        return f"Open Market Opportunity for {entity_id} and inspect peer/benchmark structure over {horizon}."
    if anomaly_type in {"momentum_acceleration", "momentum_deceleration", "technical_regime_shift"}:
        return f"Open Technical Strategizer for {entity_id} and review the channel and pullback setup."
    if anomaly_type == "news_confirmed_move":
        return f"Open Market Opportunity for {entity_id} and compare the move against recent headlines."
    return f"Open Market Opportunity for {entity_id} and compare observed vs expected behavior."


def _signed_percent_text(value: Any) -> str:
    numeric = float(pd.to_numeric(value, errors="coerce"))
    if not np.isfinite(numeric):
        return "n/a"
    return f"{numeric:+.2f}%"


def _build_attention_story(row: pd.Series) -> str:
    entity_id = str(row.get("entity_id") or "").strip().upper()
    horizon = str(row.get("horizon") or "").strip()
    direction = str(row.get("direction") or "").strip().lower()
    anomaly_type = str(row.get("anomaly_type") or "").strip().lower()
    peer_group_name = str(row.get("peer_group_name") or "").strip()
    regime_label = str(row.get("regime_label") or "").strip()
    linked_news_raw = pd.to_numeric(row.get("linked_news_count"), errors="coerce")
    linked_news_count = int(linked_news_raw) if pd.notna(linked_news_raw) else 0
    portfolio_exposure_weight = float(pd.to_numeric(row.get("portfolio_exposure_weight"), errors="coerce"))

    if not entity_id:
        return ""

    if peer_group_name and peer_group_name not in {"All Market", "Broad Commodity Market"}:
        expectation_anchor = f"its {peer_group_name} peers"
    elif peer_group_name == "Broad Commodity Market":
        expectation_anchor = "the broader commodity tape"
    else:
        expectation_anchor = "its recent path and the broader tape"

    lead = (
        f"{entity_id} is trading stronger than {expectation_anchor} implied."
        if direction == "up"
        else f"{entity_id} is trading weaker than {expectation_anchor} implied."
    )

    if anomaly_type == "news_confirmed_move":
        driver = (
            f"{linked_news_count} fresh headline{'s are' if linked_news_count != 1 else ' is'} reinforcing the move, "
            "so this looks more idiosyncratic than market-wide."
        )
    elif anomaly_type in {"correlation_break", "decoupling"}:
        driver = "It is breaking from its usual benchmark or peer correlation, which points to a stock-specific move."
    elif anomaly_type in {"momentum_acceleration", "momentum_deceleration"}:
        driver = "Momentum is shifting faster than the prior trajectory suggested, so the move is starting to compound."
    else:
        driver = "The gap versus expectation is wide enough that this looks like more than ordinary beta noise."

    context_parts: list[str] = []
    if regime_label:
        context_parts.append(f"Price action still looks like a {regime_label.lower()} setup")
    if horizon:
        context_parts.append(f"That separation is showing up over the {horizon} window")
    if portfolio_exposure_weight > 0:
        context_parts.append("Portfolio overlap raises the priority")

    if context_parts:
        return f"{lead} {driver} {'; '.join(context_parts)}."
    return f"{lead} {driver}"


def build_peer_group_membership(*, asof_time_utc: pd.Timestamp, symbols: list[str] | None = None) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    covered: set[str] = set()
    requested_symbols = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    asof = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")

    taxonomy = load_entity_taxonomy_frame(sorted(requested_symbols) if requested_symbols else None)
    if not taxonomy.empty:
        taxonomy = taxonomy.copy()
        taxonomy["symbol"] = taxonomy["symbol"].astype(str).str.upper().str.strip()
        taxonomy = taxonomy[taxonomy["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first")
        if requested_symbols:
            taxonomy = taxonomy[taxonomy["symbol"].isin(requested_symbols)].copy()

    for _, row in taxonomy.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        covered.add(symbol)
        industry = str(row.get("industry") or "").strip()
        sector = str(row.get("sector") or "").strip()
        label = str(business_focus_label_from_taxonomy_row(row.to_dict()) or "").strip()

        if industry and industry not in {"Unknown", "Market", "All Market"}:
            peer_group_id = f"industry:{_slugify(industry)}"
            peer_group_name = industry
            peer_group_type = "industry"
        elif sector and sector not in {"Unknown", "Market", "All Market"}:
            peer_group_id = f"sector:{_slugify(sector)}"
            peer_group_name = sector
            peer_group_type = "sector"
        elif label and label not in {"Unknown", "Market", "All Market"}:
            peer_group_id = f"business_role:{_slugify(label)}"
            peer_group_name = label
            peer_group_type = "business_role"
        else:
            peer_group_id = "market:all_market"
            peer_group_name = "All Market"
            peer_group_type = "market"

        rows.append(
            {
                "asof_time_utc": asof,
                "entity_type": "symbol",
                "entity_id": symbol,
                "peer_group_id": peer_group_id,
                "peer_group_name": peer_group_name,
                "peer_group_type": peer_group_type,
                "benchmark": "SPY",
                "membership_weight": 1.0,
                "source": "entity_taxonomy_v1",
                "schema_version": "v1",
            }
        )

    for symbol in sorted(requested_symbols):
        if not symbol or symbol in covered:
            continue
        rows.append(
            {
                "asof_time_utc": asof,
                "entity_type": "symbol",
                "entity_id": symbol,
                "peer_group_id": "market:all_market",
                "peer_group_name": "All Market",
                "peer_group_type": "market",
                "benchmark": "SPY",
                "membership_weight": 1.0,
                "source": "entity_taxonomy_v1",
                "schema_version": "v1",
            }
        )

    return pd.DataFrame(rows)


def build_commodity_peer_group_membership(
    *,
    asof_time_utc: pd.Timestamp,
    symbols: list[str] | None = None,
    default_benchmark: str = "DBC",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    covered: set[str] = set()
    requested_symbols = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    broad_market_name = "Broad Commodity Market"
    normalized_default_benchmark = _normalize_symbol(default_benchmark) or "DBC"
    asof = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")

    taxonomy = load_entity_taxonomy_frame(sorted(requested_symbols) if requested_symbols else None)
    if not taxonomy.empty:
        taxonomy = taxonomy.copy()
        taxonomy["symbol"] = taxonomy["symbol"].astype(str).str.upper().str.strip()
        taxonomy = taxonomy[taxonomy["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first")
        if requested_symbols:
            taxonomy = taxonomy[taxonomy["symbol"].isin(requested_symbols)].copy()

    def _benchmark_for(symbol: str) -> str:
        normalized = _normalize_symbol(symbol)
        if normalized == normalized_default_benchmark:
            return "PDBC"
        return normalized_default_benchmark

    def _coerce_tags(value: Any) -> list[str]:
        if isinstance(value, list):
            raw = value
        elif isinstance(value, tuple):
            raw = list(value)
        else:
            text = str(value or "").strip()
            if not text:
                return []
            if text.startswith("[") and text.endswith("]"):
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = [text]
                raw = parsed if isinstance(parsed, list) else [parsed]
            else:
                raw = [token.strip() for token in text.split(",")]
        out: list[str] = []
        for item in raw:
            tag = _slugify(item)
            if tag:
                out.append(tag)
        return out

    for _, row in taxonomy.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        covered.add(symbol)
        commodity_role = _slugify(row.get("commodity_role"))
        rates_role = _slugify(row.get("rates_role"))
        defensive_role = _slugify(row.get("defensive_role"))
        macro_tags = _coerce_tags(row.get("macro_role_tags"))

        if commodity_role:
            peer_group_id = f"commodity_role:{commodity_role}"
            peer_group_name = _humanize_slug(commodity_role)
            peer_group_type = "commodity_role"
        elif macro_tags:
            tag = macro_tags[0]
            peer_group_id = f"macro_role:{tag}"
            peer_group_name = _humanize_slug(tag)
            peer_group_type = "macro_role"
        elif rates_role:
            peer_group_id = f"rates_role:{rates_role}"
            peer_group_name = _humanize_slug(rates_role)
            peer_group_type = "rates_role"
        elif defensive_role:
            peer_group_id = f"defensive_role:{defensive_role}"
            peer_group_name = _humanize_slug(defensive_role)
            peer_group_type = "defensive_role"
        else:
            peer_group_id = "commodity_focus:broad_commodity_market"
            peer_group_name = broad_market_name
            peer_group_type = "commodity_focus"

        rows.append(
            {
                "asof_time_utc": asof,
                "entity_type": "commodity_symbol",
                "entity_id": symbol,
                "peer_group_id": peer_group_id,
                "peer_group_name": peer_group_name or broad_market_name,
                "peer_group_type": peer_group_type,
                "benchmark": _benchmark_for(symbol),
                "membership_weight": 1.0,
                "source": "entity_taxonomy_v1",
                "schema_version": "v1",
            }
        )

    for symbol in sorted(requested_symbols):
        normalized = _normalize_symbol(symbol)
        if not normalized or normalized in covered:
            continue
        rows.append(
            {
                "asof_time_utc": asof,
                "entity_type": "commodity_symbol",
                "entity_id": normalized,
                "peer_group_id": "commodity_focus:broad_commodity_market",
                "peer_group_name": broad_market_name,
                "peer_group_type": "commodity_focus",
                "benchmark": _benchmark_for(normalized),
                "membership_weight": 1.0,
                "source": "entity_taxonomy_v1",
                "schema_version": "v1",
            }
        )

    return pd.DataFrame(rows)


def build_taxonomy_peer_group_catalog(peer_group_membership: pd.DataFrame) -> pd.DataFrame:
    required = {"peer_group_id", "peer_group_name", "peer_group_type", "entity_type", "entity_id"}
    if peer_group_membership.empty or not required.issubset(set(peer_group_membership.columns)):
        return _empty_frame(TAXONOMY_PEER_GROUP_CATALOG_COLUMNS)

    frame = peer_group_membership.copy()
    frame["entity_id"] = frame["entity_id"].map(_normalize_symbol)
    frame["peer_group_id"] = frame["peer_group_id"].astype(str).str.strip()
    frame["peer_group_name"] = frame["peer_group_name"].astype(str).str.strip()
    frame = frame[frame["peer_group_id"].ne("") & frame["entity_id"].ne("")].copy()
    if frame.empty:
        return _empty_frame(TAXONOMY_PEER_GROUP_CATALOG_COLUMNS)

    rows: list[dict[str, object]] = []
    for _, chunk in frame.groupby(["peer_group_id", "peer_group_name", "peer_group_type", "entity_type"], dropna=False, sort=False):
        ordered = chunk.sort_values("entity_id")
        members = ordered["entity_id"].astype(str).tolist()
        sample = members[:20]
        benchmark = str(ordered.get("benchmark", pd.Series(dtype=str)).dropna().astype(str).iloc[0]).strip() if "benchmark" in ordered.columns and not ordered.get("benchmark", pd.Series(dtype=str)).dropna().empty else ""
        source = str(ordered.get("source", pd.Series(dtype=str)).dropna().astype(str).iloc[0]).strip() if "source" in ordered.columns and not ordered.get("source", pd.Series(dtype=str)).dropna().empty else ""
        asof = pd.to_datetime(ordered.get("asof_time_utc", pd.Series(dtype="datetime64[ns, UTC]")), utc=True, errors="coerce")
        asof_value = asof.max() if isinstance(asof, pd.Series) and not asof.dropna().empty else pd.NaT
        rows.append(
            {
                "asof_time_utc": asof_value,
                "peer_group_id": str(ordered["peer_group_id"].iloc[0]),
                "peer_group_name": str(ordered["peer_group_name"].iloc[0]),
                "peer_group_type": str(ordered["peer_group_type"].iloc[0]),
                "benchmark": benchmark,
                "entity_type": str(ordered["entity_type"].iloc[0]),
                "member_count": int(len(members)),
                "sample_entity_ids_json": json.dumps(sample, ensure_ascii=False),
                "source": source,
                "schema_version": "v1",
            }
        )
    return pd.DataFrame(rows, columns=TAXONOMY_PEER_GROUP_CATALOG_COLUMNS)


def _normalize_symbol_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = _normalize_symbol(value)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def build_price_expectations(
    price_history: pd.DataFrame,
    momentum_profiles: pd.DataFrame,
    correlation_phase_shift_summary: pd.DataFrame,
    peer_group_membership: pd.DataFrame,
    *,
    config: ExpectationConfig,
) -> pd.DataFrame:
    observed = _observed_returns(price_history, normalize_horizons(config.horizons), config.min_history_rows)
    if observed.empty:
        return _empty_frame(EXPECTATION_COLUMNS)

    primary_membership = _primary_peer_membership(peer_group_membership)
    if primary_membership.empty:
        return _empty_frame(EXPECTATION_COLUMNS)

    momentum = momentum_profiles.copy()
    if not momentum.empty:
        momentum["symbol"] = momentum["symbol"].map(_normalize_symbol)
    volatility = _latest_volatility(price_history)

    expected = observed.merge(primary_membership, on="symbol", how="inner")
    if expected.empty:
        return _empty_frame(EXPECTATION_COLUMNS)

    peer_expected = _peer_expected_returns(observed, primary_membership)
    benchmark_expected = _benchmark_expected_returns(observed, correlation_phase_shift_summary, primary_membership)

    expected = expected.merge(peer_expected, on=["symbol", "horizon", "peer_group_id", "peer_group_name", "benchmark"], how="left")
    expected = expected.merge(benchmark_expected, on=["symbol", "horizon"], how="left")
    expected = expected.merge(momentum, on="symbol", how="left", suffixes=("", "_momentum"))
    expected = expected.merge(volatility, on="symbol", how="left")

    expected["trend_expected_return_pct"] = expected.apply(lambda row: _trend_expectation(row, str(row.get("horizon") or "")), axis=1)

    blend_weights = {
        "trend_expected_return_pct": config.trend_weight,
        "peer_expected_return_pct": config.peer_weight,
        "benchmark_expected_return_pct": config.benchmark_weight,
    }
    expected["blended_expected_return_pct"] = expected.apply(
        lambda row: _weighted_blend(
            {
                "trend_expected_return_pct": pd.to_numeric(row.get("trend_expected_return_pct"), errors="coerce"),
                "peer_expected_return_pct": pd.to_numeric(row.get("peer_expected_return_pct"), errors="coerce"),
                "benchmark_expected_return_pct": pd.to_numeric(row.get("benchmark_expected_return_pct"), errors="coerce"),
            },
            blend_weights,
        ),
        axis=1,
    )
    expected["residual_return_pct"] = _coerce_numeric(expected["observed_return_pct"]) - _coerce_numeric(expected["blended_expected_return_pct"])

    for horizon, chunk in expected.groupby("horizon", sort=False):
        idx = chunk.index
        expected.loc[idx, "residual_zscore"] = _cross_sectional_zscore(chunk["residual_return_pct"]).to_numpy(dtype=float)
        expected.loc[idx, "trend_zscore"] = _cross_sectional_zscore(chunk["trend_expected_return_pct"]).to_numpy(dtype=float)
        expected.loc[idx, "peer_zscore"] = _cross_sectional_zscore(chunk["peer_expected_return_pct"]).to_numpy(dtype=float)
        expected.loc[idx, "benchmark_zscore"] = _cross_sectional_zscore(chunk["benchmark_expected_return_pct"]).to_numpy(dtype=float)

    expected["trajectory_model_version"] = "trend_blend_v1"
    expected["peer_model_version"] = "business_lens_peer_v1"
    expected["schema_version"] = config.schema_version

    for column in [
        "close",
        "observed_return_pct",
        "trend_expected_return_pct",
        "peer_expected_return_pct",
        "benchmark_expected_return_pct",
        "blended_expected_return_pct",
        "residual_return_pct",
        "residual_zscore",
        "trend_zscore",
        "peer_zscore",
        "benchmark_zscore",
        "vol_20_ann_pct",
        "momentum_score",
        "momentum_roc_score",
        "correlation_now",
        "correlation_roc",
    ]:
        expected[column] = _coerce_numeric(expected.get(column, pd.Series(dtype=float)))

    return expected[EXPECTATION_COLUMNS].sort_values(["asof_time_utc", "horizon", "residual_zscore"], ascending=[True, True, False]).reset_index(drop=True)


def build_attention_candidates(
    price_expectations: pd.DataFrame,
    technical_signals_latest: pd.DataFrame | None = None,
    news_symbol_map: pd.DataFrame | None = None,
    positions: pd.DataFrame | None = None,
    macro_context: pd.DataFrame | None = None,
    *,
    config: AttentionConfig,
) -> pd.DataFrame:
    if price_expectations.empty:
        return _empty_frame(ANOMALY_EVENT_COLUMNS)

    frame = price_expectations.copy()
    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    frame["asof_time_utc"] = pd.to_datetime(frame["asof_time_utc"], utc=True, errors="coerce")

    position_weights = _position_weights(positions)
    news_lookup = _recent_news_lookup(news_symbol_map, pd.to_datetime(frame["asof_time_utc"].max(), utc=True, errors="coerce"), config.news_lookback_days)
    regime_lookup = _regime_lookup(technical_signals_latest)
    datasets_used = _supporting_datasets(technical_signals_latest, news_symbol_map, positions, macro_context)
    macro_lookup = _macro_context_lookup(macro_context)

    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        horizon = str(row.get("horizon") or "")
        observed_value = float(pd.to_numeric(row.get("observed_return_pct"), errors="coerce"))
        expected_value = float(pd.to_numeric(row.get("blended_expected_return_pct"), errors="coerce"))
        residual_value = float(pd.to_numeric(row.get("residual_return_pct"), errors="coerce"))
        residual_zscore = float(pd.to_numeric(row.get("residual_zscore"), errors="coerce"))
        direction = "up" if residual_value >= 0 else "down"
        portfolio_exposure_weight = float(position_weights.get(symbol, 0.0))
        linked_news = news_lookup.get(symbol, {"count": 0, "ids": []})
        linked_news_count = int(linked_news.get("count") or 0)
        linked_news_ids = ",".join(dict.fromkeys(str(value) for value in linked_news.get("ids") or []))
        regime_label = regime_lookup.get(symbol, "")
        macro_row = dict(macro_lookup.get((symbol, horizon)) or macro_lookup.get((symbol, "")) or {})
        macro_alignment_score = _clamp(macro_row.get("macro_alignment_score", 0.0), 0.0, 100.0)
        macro_conflict_score = _clamp(macro_row.get("macro_conflict_score", 0.0), 0.0, 100.0)
        macro_signal_count = max(int(macro_row.get("macro_signal_count") or 0), 0)
        macro_staleness_hours = float(pd.to_numeric(macro_row.get("macro_staleness_hours"), errors="coerce"))
        macro_data_fresh = bool(
            macro_signal_count > 0
            and (not np.isfinite(macro_staleness_hours) or macro_staleness_hours <= float(max(config.macro_staleness_hours, 0.0)))
        )

        severity_score = min(abs(residual_zscore) / 4.0, 1.0) * 100.0 if np.isfinite(residual_zscore) else 0.0
        move_component = min(abs(observed_value) / 10.0, 1.0) * 100.0 if np.isfinite(observed_value) else 0.0
        residual_component = min(abs(residual_value) / 8.0, 1.0) * 100.0 if np.isfinite(residual_value) else 0.0
        exposure_component = min(max(portfolio_exposure_weight, 0.0), 1.0) * 100.0
        impact_score = 0.45 * move_component + 0.35 * residual_component + 0.20 * exposure_component

        peer_group_name = str(row.get("peer_group_name") or "").strip()
        relevance_score = 100.0 if portfolio_exposure_weight > 0 else (70.0 if peer_group_name and peer_group_name != "All Market" else 40.0)

        horizon_persistence = {"1d": 35.0, "1w": 55.0, "1mo": 70.0, "3mo": 82.5, "1yr": 92.5}.get(horizon, 50.0)
        anomaly_threshold = _threshold_for_horizon(config, horizon)
        persistence_bonus = max(int(config.persistence_periods) - 1, 0) * 5.0
        persistence_score = min(horizon_persistence + max(abs(residual_zscore) - anomaly_threshold, 0.0) * 10.0 + persistence_bonus, 100.0)
        news_confirmation = min(linked_news_count * 25.0, 100.0)
        technical_confirmation = _technical_confirmation_score(direction, regime_label)
        confidence_score = 0.35 * technical_confirmation + 0.35 * news_confirmation + 0.30 * persistence_score

        novelty_score = severity_score
        anomaly_type = _classify_anomaly(
            pd.Series(
                {
                    "residual_value": residual_value,
                    "correlation_roc": row.get("correlation_roc"),
                    "momentum_roc_score": row.get("momentum_roc_score"),
                    "linked_news_count": linked_news_count,
                }
            )
        )
        attention_score = (
            0.40 * severity_score
            + 0.25 * impact_score
            + 0.20 * relevance_score
            + 0.15 * confidence_score
        )
        macro_net_signal = (macro_alignment_score - macro_conflict_score) / 100.0 if macro_data_fresh else 0.0
        macro_adjustment = float(config.macro_shadow_weight) * macro_net_signal * 100.0 if bool(config.macro_shadow_enabled) else 0.0
        attention_score_v2_shadow = _clamp(attention_score + macro_adjustment, 0.0, 100.0)
        attention_score_v2 = attention_score_v2_shadow if bool(config.macro_live_enabled) else attention_score
        attention_score_output = attention_score_v2 if bool(config.macro_live_enabled) else attention_score

        why_now_text = (
            f"{symbol} moved {observed_value:.2f}% over {horizon} versus an expected {expected_value:.2f}%, "
            f"leaving a residual of {residual_value:.2f}% ({residual_zscore:.2f} z)."
        )
        event_id = sha1(
            f"{symbol}|{horizon}|{row.get('asof_time_utc')}|{anomaly_type}|{direction}".encode("utf-8")
        ).hexdigest()

        rows.append(
            {
                "event_id": event_id,
                "asof_time_utc": pd.to_datetime(row.get("asof_time_utc"), utc=True, errors="coerce"),
                "entity_type": "symbol",
                "entity_id": symbol,
                "parent_entity_type": "peer_group",
                "parent_entity_id": row.get("peer_group_id"),
                "horizon": horizon,
                "anomaly_type": anomaly_type,
                "direction": direction,
                "observed_value": observed_value,
                "expected_value": expected_value,
                "residual_value": residual_value,
                "residual_zscore": residual_zscore,
                "severity_score": severity_score,
                "impact_score": impact_score,
                "relevance_score": relevance_score,
                "confidence_score": confidence_score,
                "attention_score": attention_score_output,
                "attention_score_v2_shadow": attention_score_v2_shadow,
                "attention_score_v2": attention_score_v2,
                "persistence_score": persistence_score,
                "novelty_score": novelty_score,
                "macro_alignment_score": macro_alignment_score,
                "macro_conflict_score": macro_conflict_score,
                "macro_signal_count": macro_signal_count,
                "macro_data_fresh": macro_data_fresh,
                "portfolio_exposure_weight": portfolio_exposure_weight,
                "peer_group_id": row.get("peer_group_id"),
                "peer_group_name": row.get("peer_group_name"),
                "benchmark": row.get("benchmark"),
                "regime_label": regime_label,
                "why_now_code": anomaly_type,
                "why_now_text": why_now_text,
                "supporting_datasets": datasets_used,
                "linked_news_count": linked_news_count,
                "linked_news_ids": linked_news_ids,
                "drilldown_section": "Market Opportunity",
                "drilldown_params_json": json.dumps(
                    _attention_drilldown_params(
                        symbol,
                        horizon,
                        entity_type="symbol",
                        peer_group_name=row.get("peer_group_name"),
                    ),
                    sort_keys=True,
                ),
                "status": _status_from_scores(residual_zscore, anomaly_threshold),
                "schema_version": config.schema_version,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return _empty_frame(ANOMALY_EVENT_COLUMNS)
    out = _ensure_columns(out, ANOMALY_EVENT_COLUMNS)
    return out[ANOMALY_EVENT_COLUMNS].sort_values(["attention_score", "severity_score"], ascending=False).reset_index(drop=True)


def filter_attention_events(
    attention_events: pd.DataFrame,
    *,
    config: AttentionConfig | None = None,
    horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    entity_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    min_attention_score: float | None = None,
    residual_zscore_threshold: float | None = None,
    residual_zscore_thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    if attention_events.empty:
        return _empty_frame(ANOMALY_EVENT_COLUMNS)

    config = config or AttentionConfig()
    out = attention_events.copy()
    out["horizon"] = out.get("horizon", pd.Series(dtype=str)).map(normalize_horizon)
    out["entity_id"] = out.get("entity_id", pd.Series(dtype=str)).map(_normalize_symbol)
    out["residual_zscore"] = _coerce_numeric(out.get("residual_zscore", pd.Series(dtype=float)))
    out["attention_score"] = _coerce_numeric(out.get("attention_score", pd.Series(dtype=float)))

    selected_horizons = set(normalize_horizons(horizons))
    if selected_horizons:
        out = out[out["horizon"].isin(selected_horizons)].copy()

    if entity_ids:
        allowed_entities = {_normalize_symbol(value) for value in entity_ids if _normalize_symbol(value)}
        out = out[out["entity_id"].isin(allowed_entities)].copy()

    if out.empty:
        return _empty_frame(ANOMALY_EVENT_COLUMNS)

    thresholds = {
        horizon: _threshold_for_horizon(
            config,
            horizon,
            threshold_override=residual_zscore_threshold,
            threshold_overrides=residual_zscore_thresholds,
        )
        for horizon in sorted(out["horizon"].dropna().astype(str).unique().tolist())
    }
    out["_dynamic_threshold"] = out["horizon"].map(lambda value: thresholds.get(str(value), _threshold_for_horizon(config, str(value), threshold_override=residual_zscore_threshold, threshold_overrides=residual_zscore_thresholds)))
    out["status"] = [
        _status_from_scores(float(residual) if pd.notna(residual) else np.nan, float(threshold))
        for residual, threshold in zip(out["residual_zscore"], out["_dynamic_threshold"])
    ]
    out = out[out["residual_zscore"].abs() >= out["_dynamic_threshold"]].copy()

    effective_min_score = config.min_attention_score if min_attention_score is None else float(min_attention_score)
    if np.isfinite(effective_min_score) and effective_min_score > 0:
        out = out[out["attention_score"] >= float(effective_min_score)].copy()

    if statuses:
        allowed_statuses = {str(value).strip().lower() for value in statuses if str(value).strip()}
        out = out[out["status"].astype(str).str.lower().isin(allowed_statuses)].copy()

    if out.empty:
        return _empty_frame(ANOMALY_EVENT_COLUMNS)

    out = out.drop(columns=["_dynamic_threshold"], errors="ignore")
    out = _ensure_columns(out, ANOMALY_EVENT_COLUMNS)
    return out[ANOMALY_EVENT_COLUMNS].sort_values(["attention_score", "severity_score"], ascending=False).reset_index(drop=True)


def detect_anomaly_events(
    price_expectations: pd.DataFrame,
    technical_signals_latest: pd.DataFrame | None = None,
    news_symbol_map: pd.DataFrame | None = None,
    positions: pd.DataFrame | None = None,
    macro_context: pd.DataFrame | None = None,
    *,
    config: AttentionConfig,
) -> pd.DataFrame:
    candidates = build_attention_candidates(
        price_expectations,
        technical_signals_latest=technical_signals_latest,
        news_symbol_map=news_symbol_map,
        positions=positions,
        macro_context=macro_context,
        config=config,
    )
    return filter_attention_events(
        candidates,
        config=config,
        statuses=["active", "cooling"],
    )


def build_attention_rollups(
    anomaly_events: pd.DataFrame,
    peer_group_membership: pd.DataFrame,
    *,
    high_priority_threshold: float = 75.0,
) -> pd.DataFrame:
    if anomaly_events.empty:
        return _empty_frame(ATTENTION_ROLLUP_COLUMNS)

    events = anomaly_events.copy()
    events["asof_time_utc"] = pd.to_datetime(events["asof_time_utc"], utc=True, errors="coerce")
    rows: list[dict[str, object]] = []

    def _append_rollup(group: pd.DataFrame, *, rollup_type: str, rollup_id: str, rollup_name: str) -> None:
        if group.empty:
            return
        ordered = group.sort_values("attention_score", ascending=False)
        top = ordered.iloc[0]
        active_count = int((group["status"] != "resolved").sum())
        rows.append(
            {
                "asof_time_utc": pd.to_datetime(group["asof_time_utc"].max(), utc=True, errors="coerce"),
                "rollup_type": rollup_type,
                "rollup_id": rollup_id,
                "rollup_name": rollup_name,
                "active_event_count": active_count,
                "high_priority_event_count": int((pd.to_numeric(group["attention_score"], errors="coerce") >= float(high_priority_threshold)).sum()),
                "top_event_id": top.get("event_id"),
                "top_attention_score": float(pd.to_numeric(top.get("attention_score"), errors="coerce")),
                "breadth_positive": int((group["direction"] == "up").sum()),
                "breadth_negative": int((group["direction"] == "down").sum()),
                "mean_residual_zscore": float(pd.to_numeric(group["residual_zscore"], errors="coerce").mean()),
                "net_attention_score": float(pd.to_numeric(group["attention_score"], errors="coerce").sum()),
                "summary_text": f"{rollup_name} has {active_count} active anomaly event(s); top item is {top.get('entity_id')} at {float(pd.to_numeric(top.get('attention_score'), errors='coerce')):.1f}.",
                "schema_version": "v1",
            }
        )

    _append_rollup(events, rollup_type="market", rollup_id="market", rollup_name="Market")

    held = events[pd.to_numeric(events["portfolio_exposure_weight"], errors="coerce") > 0].copy()
    if not held.empty:
        _append_rollup(held, rollup_type="portfolio", rollup_id="portfolio", rollup_name="Portfolio")

    for (peer_group_id, peer_group_name), group in events.groupby(["peer_group_id", "peer_group_name"], dropna=False, sort=False):
        if not str(peer_group_id or "").strip():
            continue
        _append_rollup(
            group,
            rollup_type="business_lens",
            rollup_id=str(peer_group_id),
            rollup_name=str(peer_group_name or peer_group_id),
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return _empty_frame(ATTENTION_ROLLUP_COLUMNS)
    return out[ATTENTION_ROLLUP_COLUMNS].sort_values(["net_attention_score", "active_event_count"], ascending=False).reset_index(drop=True)


def build_attention_feed(
    anomaly_events: pd.DataFrame,
    attention_rollups: pd.DataFrame,
    *,
    top_n: int = 20,
) -> pd.DataFrame:
    if anomaly_events.empty:
        return _empty_frame(ATTENTION_FEED_COLUMNS)

    events = anomaly_events.copy().sort_values(["attention_score", "severity_score"], ascending=False).head(max(int(top_n), 1)).reset_index(drop=True)
    rows: list[dict[str, object]] = []
    for idx, row in events.iterrows():
        entity_id = str(row.get("entity_id") or "").strip()
        horizon = str(row.get("horizon") or "").strip()
        observed = float(pd.to_numeric(row.get("observed_value"), errors="coerce"))
        expected = float(pd.to_numeric(row.get("expected_value"), errors="coerce"))
        residual = float(pd.to_numeric(row.get("residual_value"), errors="coerce"))
        title_prefix = "Portfolio attention" if float(pd.to_numeric(row.get("portfolio_exposure_weight"), errors="coerce")) > 0 else entity_id
        title = (
            f"{title_prefix}: {entity_id} is {'outperforming' if residual >= 0 else 'underperforming'} expectation"
            if title_prefix != entity_id
            else f"{entity_id} is {'outperforming' if residual >= 0 else 'underperforming'} expectation"
        )
        subtitle = f"{str(row.get('anomaly_type') or '').replace('_', ' ').title()} over {horizon}"
        story_text = _build_attention_story(row)
        expected_vs_observed_text = (
            f"The chart shows how the realized move separated from the model baseline over {horizon or 'the selected window'}."
        )
        rows.append(
            {
                "feed_rank": idx + 1,
                "event_id": row.get("event_id"),
                "asof_time_utc": pd.to_datetime(row.get("asof_time_utc"), utc=True, errors="coerce"),
                "card_type": row.get("anomaly_type"),
                "title": title,
                "subtitle": subtitle,
                "entity_type": row.get("entity_type"),
                "entity_id": entity_id,
                "horizon": horizon,
                "direction": row.get("direction"),
                "peer_group_name": row.get("peer_group_name"),
                "regime_label": row.get("regime_label"),
                "attention_score": float(pd.to_numeric(row.get("attention_score"), errors="coerce")),
                "severity_score": float(pd.to_numeric(row.get("severity_score"), errors="coerce")),
                "impact_score": float(pd.to_numeric(row.get("impact_score"), errors="coerce")),
                "confidence_score": float(pd.to_numeric(row.get("confidence_score"), errors="coerce")),
                "observed_value": observed,
                "expected_value": expected,
                "residual_value": residual,
                "residual_zscore": float(pd.to_numeric(row.get("residual_zscore"), errors="coerce")),
                "story_text": story_text,
                "why_now_text": row.get("why_now_text"),
                "expected_vs_observed_text": expected_vs_observed_text,
                "next_best_action": _next_best_action(str(row.get("anomaly_type") or ""), entity_id, horizon),
                "drilldown_section": row.get("drilldown_section"),
                "drilldown_params_json": json.dumps(
                    _attention_drilldown_params(
                        entity_id,
                        horizon,
                        entity_type=row.get("entity_type"),
                        peer_group_name=row.get("peer_group_name"),
                    ),
                    sort_keys=True,
                ),
                "linked_news_count": int(row.get("linked_news_count") or 0),
                "status": row.get("status"),
                "schema_version": row.get("schema_version") or "v1",
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return _empty_frame(ATTENTION_FEED_COLUMNS)
    return out[ATTENTION_FEED_COLUMNS]


__all__ = [
    "ANOMALY_EVENT_COLUMNS",
    "ATTENTION_FEED_COLUMNS",
    "ATTENTION_ROLLUP_COLUMNS",
    "EXPECTATION_COLUMNS",
    "TAXONOMY_PEER_GROUP_CATALOG_COLUMNS",
    "AttentionConfig",
    "ExpectationConfig",
    "HORIZON_PERIODS",
    "SENSITIVITY_PRESETS",
    "attention_preset",
    "build_attention_feed",
    "build_attention_candidates",
    "build_attention_rollups",
    "build_commodity_peer_group_membership",
    "build_peer_group_membership",
    "build_price_expectations",
    "build_taxonomy_peer_group_catalog",
    "detect_anomaly_events",
    "filter_attention_events",
    "normalize_horizon",
    "normalize_horizons",
]
