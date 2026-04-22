"""Dense signal extraction for LLM-consumable market and macro summaries.

Provides a shared vocabulary of trend, momentum, and regime signals that both
the Market Explorer UI and the homepage agentic summary consume.  All functions
are stateless and operate on plain pandas Series / DataFrames — no API calls.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Primitives (extracted from services/market.py for reuse)
# ---------------------------------------------------------------------------

def log_slope(series: pd.Series, window: int) -> float:
    """Log-linear slope over the trailing *window* observations.
    Returns NaN when the series is too short or contains non-positive values."""
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < max(window, 2) or (values <= 0).any():
        return np.nan
    y = np.log(values.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    try:
        slope, _ = np.polyfit(x, y, 1)
    except np.linalg.LinAlgError:
        return np.nan
    return float(slope)


def linear_slope(series: pd.Series, window: int) -> float:
    """Linear slope over the trailing *window* observations.
    Works for series that can be zero or negative (e.g. rates, deltas)."""
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < max(window, 2):
        return np.nan
    y = values.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    try:
        slope, _ = np.polyfit(x, y, 1)
    except np.linalg.LinAlgError:
        return np.nan
    return float(slope)


def trend_r2(series: pd.Series, window: int) -> float:
    """R-squared of a log-linear fit over the trailing *window* observations.
    Falls back to linear fit when values are non-positive."""
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window:
        return np.nan
    raw = values.to_numpy(dtype=float)
    if (raw > 0).all():
        y = np.log(raw)
    else:
        y = raw
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot == 0:
        return 1.0
    r2 = 1.0 - (ss_res / ss_tot)
    return float(np.clip(r2, 0.0, 1.0))


def window_return_pct(series: pd.Series, window: int) -> float:
    """Percentage return over the trailing *window* observations."""
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window:
        return np.nan
    start = float(values.iloc[0])
    end = float(values.iloc[-1])
    if start == 0:
        return np.nan
    return ((end / start) - 1.0) * 100.0


def ratio_minus_one(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator - 1.0)


def mean_finite(values: list[float]) -> float:
    finite = [float(v) for v in values if np.isfinite(v)]
    if not finite:
        return np.nan
    return float(np.mean(finite))


def rolling_compound(returns: pd.Series, window: int) -> pd.Series:
    clean = pd.to_numeric(returns, errors="coerce")
    if window <= 1:
        return clean
    return (1.0 + clean).rolling(window).apply(np.prod, raw=True) - 1.0


def zscore_series(values: pd.Series) -> pd.Series:
    """Z-score each element relative to the full series distribution."""
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if len(valid) < 2:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    std = float(valid.std(ddof=0))
    if std == 0:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    return (numeric - float(valid.mean())) / std


def zscore_scalar(series: pd.Series, lookback: int) -> float:
    """Z-score of the latest value relative to the trailing *lookback* window."""
    values = pd.to_numeric(series, errors="coerce").dropna().tail(lookback)
    if len(values) < 2:
        return np.nan
    std = float(values.std(ddof=0))
    if std == 0:
        return 0.0
    return float((float(values.iloc[-1]) - float(values.mean())) / std)


def rolling_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int,
) -> pd.Series:
    cov = pd.to_numeric(asset_returns, errors="coerce").rolling(window).cov(
        pd.to_numeric(benchmark_returns, errors="coerce")
    )
    var = pd.to_numeric(benchmark_returns, errors="coerce").rolling(window).var()
    beta = cov / var.replace(0, np.nan)
    return beta.replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------------------
# Window presets by frequency
# ---------------------------------------------------------------------------

_WINDOW_PRESETS: dict[str, dict[str, int | tuple[int, ...]]] = {
    "daily": {"short": 5, "mid": 21, "long": 63, "windows": (5, 21, 63), "zscore_lookback": 504},
    "weekly": {"short": 4, "mid": 13, "long": 52, "windows": (4, 13, 52), "zscore_lookback": 104},
    "monthly": {"short": 1, "mid": 3, "long": 12, "windows": (1, 3, 12), "zscore_lookback": 24},
    "quarterly": {"short": 1, "mid": 2, "long": 4, "windows": (1, 2, 4), "zscore_lookback": 8},
}


def adapt_windows(frequency: str) -> dict[str, Any]:
    return dict(_WINDOW_PRESETS.get(frequency, _WINDOW_PRESETS["daily"]))


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

_SLOPE_FLAT_THRESHOLD = 1e-5
_ACCEL_STEADY_THRESHOLD = 0.15  # ratio of slope change considered "steady"


def _classify_trend_dir(slope: float) -> str:
    if not np.isfinite(slope):
        return "unknown"
    if abs(slope) < _SLOPE_FLAT_THRESHOLD:
        return "flat"
    return "rising" if slope > 0 else "falling"


def _classify_acceleration(slope_short: float, slope_long: float) -> tuple[str, float]:
    """Compare short-window slope to long-window slope to detect acceleration."""
    if not np.isfinite(slope_short) or not np.isfinite(slope_long):
        return "unknown", np.nan
    if abs(slope_long) < _SLOPE_FLAT_THRESHOLD:
        diff = slope_short - slope_long
        return ("accelerating" if diff > _SLOPE_FLAT_THRESHOLD else
                "decelerating" if diff < -_SLOPE_FLAT_THRESHOLD else "steady"), diff
    ratio = slope_short / slope_long
    diff = slope_short - slope_long
    if abs(ratio - 1.0) < _ACCEL_STEADY_THRESHOLD:
        return "steady", diff
    return ("accelerating" if ratio > 1.0 else "decelerating"), diff


def classify_regime(trend_dir: str, trend_accel: str) -> str:
    if trend_dir == "flat":
        return "stable"
    if trend_dir == "unknown" or trend_accel == "unknown":
        return "unknown"
    return f"{_REGIME_EXPANSION_MAP.get(trend_dir, trend_dir)}_{trend_accel}"


_REGIME_EXPANSION_MAP = {
    "rising": "expanding",
    "falling": "contracting",
}


def _estimate_regime_duration(
    series: pd.Series,
    current_trend_dir: str,
    window_long: int,
    step: int | None = None,
) -> int:
    """Estimate how many periods the current trend direction has held.
    Walks backwards through the series in steps, checking where the trend flips."""
    if current_trend_dir in ("unknown", "flat"):
        return 0
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < window_long * 2:
        return len(values)
    if step is None:
        step = max(window_long // 3, 1)
    duration = len(values)
    pos = len(values) - window_long - step
    while pos >= 0:
        chunk = values.iloc[pos : pos + window_long]
        if len(chunk) < window_long:
            break
        if (chunk > 0).all():
            slope = log_slope(chunk, window_long)
        else:
            slope = linear_slope(chunk, window_long)
        direction = _classify_trend_dir(slope)
        if direction != current_trend_dir:
            duration = len(values) - pos - window_long
            break
        pos -= step
    return max(duration, 0)


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_series_signals(
    series: pd.Series,
    *,
    series_id: str = "",
    category: str = "",
    windows: tuple[int, ...] | None = None,
    zscore_lookback: int | None = None,
    frequency: str = "daily",
    allow_log: bool | None = None,
) -> dict[str, object]:
    """Extract a compact signal dict from a time series.

    Parameters
    ----------
    series : pd.Series
        Values indexed by date (or any ordered index).
    series_id : str
        Identifier for the series (symbol or FRED series ID).
    category : str
        Grouping label (e.g. "equity", "inflation", "labor").
    windows : tuple[int, ...]
        (short, mid, long) observation counts.  Auto-adapted from *frequency* if omitted.
    zscore_lookback : int
        Number of observations for z-score context.  Auto-adapted if omitted.
    frequency : str
        One of "daily", "weekly", "monthly", "quarterly".
    allow_log : bool | None
        Whether to use log-slopes.  Auto-detected from data when None.
    """
    preset = adapt_windows(frequency)
    if windows is None:
        windows = preset["windows"]
    if zscore_lookback is None:
        zscore_lookback = preset["zscore_lookback"]

    w_short, w_mid, w_long = windows[0], windows[1], windows[2] if len(windows) > 2 else windows[-1]

    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return _empty_signals(series_id, category)

    use_log = allow_log if allow_log is not None else bool((values > 0).all())
    slope_fn = log_slope if use_log else linear_slope

    # Latest value
    latest = float(values.iloc[-1])
    latest_date = str(values.index[-1])[:10] if hasattr(values.index[-1], "isoformat") else str(values.index[-1])

    # Slopes at multiple windows
    slope_short = slope_fn(values, w_short) if len(values) >= w_short else np.nan
    slope_mid = slope_fn(values, w_mid) if len(values) >= w_mid else np.nan
    slope_long = slope_fn(values, w_long) if len(values) >= w_long else np.nan

    # Trend direction and consistency (based on long window)
    primary_slope = slope_long if np.isfinite(slope_long) else slope_mid
    trend_dir = _classify_trend_dir(primary_slope)
    r2 = trend_r2(values, w_long) if len(values) >= w_long else np.nan

    # Acceleration: compare short slope to long slope
    accel_ref_short = slope_mid if np.isfinite(slope_mid) else slope_short
    accel_ref_long = slope_long if np.isfinite(slope_long) else slope_mid
    trend_accel, trend_accel_value = _classify_acceleration(accel_ref_short, accel_ref_long)

    # Rate of change
    roc_short = window_return_pct(values, w_short) if len(values) >= w_short else np.nan
    roc_mid = window_return_pct(values, w_mid) if len(values) >= w_mid else np.nan
    roc_long = window_return_pct(values, w_long) if len(values) >= w_long else np.nan

    # RoC of RoC (momentum acceleration)
    roc_of_roc = np.nan
    if np.isfinite(slope_short) and np.isfinite(slope_long) and slope_long != 0:
        roc_of_roc = ratio_minus_one(slope_short, slope_long)

    # Z-score
    z = zscore_scalar(values, zscore_lookback)

    # Regime
    regime = classify_regime(trend_dir, trend_accel)
    regime_duration = _estimate_regime_duration(values, trend_dir, w_long)

    return {
        "series_id": series_id,
        "category": category,
        "latest": _round_safe(latest),
        "latest_date": latest_date,
        "trend_dir": trend_dir,
        "trend_slope": _round_safe(primary_slope, 6),
        "trend_r2": _round_safe(r2, 3),
        "trend_accel": trend_accel,
        "trend_accel_value": _round_safe(trend_accel_value, 6),
        "roc_short_pct": _round_safe(roc_short, 2),
        "roc_mid_pct": _round_safe(roc_mid, 2),
        "roc_long_pct": _round_safe(roc_long, 2),
        "roc_of_roc": _round_safe(roc_of_roc, 4),
        "momentum_score": _round_safe(slope_long, 6),
        "zscore": _round_safe(z, 2),
        "regime": regime,
        "regime_duration": regime_duration,
    }


def _empty_signals(series_id: str, category: str) -> dict[str, object]:
    return {
        "series_id": series_id,
        "category": category,
        "latest": None,
        "latest_date": None,
        "trend_dir": "unknown",
        "trend_slope": None,
        "trend_r2": None,
        "trend_accel": "unknown",
        "trend_accel_value": None,
        "roc_short_pct": None,
        "roc_mid_pct": None,
        "roc_long_pct": None,
        "roc_of_roc": None,
        "momentum_score": None,
        "zscore": None,
        "regime": "unknown",
        "regime_duration": 0,
    }


def _round_safe(value: float | None, decimals: int = 4) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), decimals)


# ---------------------------------------------------------------------------
# Cross-series signal enrichment
# ---------------------------------------------------------------------------

def extract_cross_series_signals(
    phase_shift_row: dict[str, object],
) -> dict[str, object]:
    """Enrich an existing correlation-phase-shift summary row with trend signals
    on the correlation series itself.

    *phase_shift_row* is expected to have keys from the pipeline's
    ``correlation_phase_shift_summary`` dataset (correlation_now, correlation_roc,
    phase_regime, decoupling_score, etc.).
    """
    return {
        "symbol": phase_shift_row.get("symbol", ""),
        "benchmark": phase_shift_row.get("benchmark", ""),
        "correlation_now": _round_safe(_float(phase_shift_row.get("correlation_now")), 3),
        "correlation_roc": _round_safe(_float(phase_shift_row.get("correlation_roc")), 4),
        "phase_regime": str(phase_shift_row.get("phase_regime", "")),
        "decoupling_score": _round_safe(_float(phase_shift_row.get("decoupling_score")), 2),
        "beta_breakout_score": _round_safe(_float(phase_shift_row.get("beta_breakout_score")), 2),
        "correlation_break_score": _round_safe(_float(phase_shift_row.get("correlation_break_score")), 2),
        "compound_1m_pct": _round_safe(_float(phase_shift_row.get("compound_1m_pct")), 2),
        "compound_3m_pct": _round_safe(_float(phase_shift_row.get("compound_3m_pct")), 2),
        "compounding_momentum_pct": _round_safe(_float(phase_shift_row.get("compounding_momentum_pct")), 2),
    }


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def extract_signals_from_bars(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    category: str = "equity",
    min_observations: int = 63,
) -> list[dict[str, object]]:
    """Run extract_series_signals over a dict of {symbol: bars_frame} where each
    frame has a 'close' column."""
    results: list[dict[str, object]] = []
    for symbol, frame in bars_by_symbol.items():
        if not isinstance(frame, pd.DataFrame) or frame.empty or "close" not in frame.columns:
            continue
        close = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if len(close) < min_observations:
            continue
        signals = extract_series_signals(
            close,
            series_id=symbol,
            category=category,
            frequency="daily",
        )
        results.append(signals)
    return results


def extract_signals_from_phase_shift_summary(
    summary: pd.DataFrame,
) -> list[dict[str, object]]:
    """Convert a correlation_phase_shift_summary DataFrame to cross-series signal dicts."""
    if not isinstance(summary, pd.DataFrame) or summary.empty:
        return []
    results: list[dict[str, object]] = []
    for _, row in summary.iterrows():
        results.append(extract_cross_series_signals(row.to_dict()))
    return results


def extract_fred_signals(
    observations: pd.DataFrame,
    specs: list[Any],
    *,
    metadata_by_id: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Run extract_series_signals over FRED observation data.

    Parameters
    ----------
    observations : pd.DataFrame
        Must have columns: series_id, date, value.
    specs : list
        FredSeriesSpec instances (need .series_id, .category, .label attributes).
    metadata_by_id : dict | None
        Optional metadata with frequency_short per series.
    """
    if not isinstance(observations, pd.DataFrame) or observations.empty:
        return []
    if "series_id" not in observations.columns or "value" not in observations.columns:
        return []

    metadata_by_id = metadata_by_id or {}
    results: list[dict[str, object]] = []

    for spec in specs:
        sid = spec.series_id
        mask = observations["series_id"] == sid
        frame = observations.loc[mask, ["date", "value"]].copy()
        if frame.empty:
            continue
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
        if frame.empty:
            continue

        meta = metadata_by_id.get(sid, {})
        freq_short = str(meta.get("frequency_short") or meta.get("frequency") or "M").strip().upper()
        frequency = _fred_frequency_to_signal_frequency(freq_short)

        series = frame.set_index("date")["value"]
        signals = extract_series_signals(
            series,
            series_id=sid,
            category=spec.category,
            frequency=frequency,
        )
        signals["indicator"] = spec.label
        results.append(signals)

    return results


def _fred_frequency_to_signal_frequency(freq_short: str) -> str:
    freq_short = freq_short.upper().strip()
    if freq_short in ("D", "DAILY"):
        return "daily"
    if freq_short in ("W", "WEEKLY", "WK"):
        return "weekly"
    if freq_short in ("M", "MONTHLY", "MO"):
        return "monthly"
    if freq_short in ("Q", "QUARTERLY", "QTR"):
        return "quarterly"
    if freq_short in ("A", "ANNUAL", "ANNUALLY", "Y", "YEARLY"):
        return "quarterly"  # use quarterly windows for annual (sparse) data
    return "monthly"  # default for FRED


# ---------------------------------------------------------------------------
# Formatting for LLM prompt injection
# ---------------------------------------------------------------------------

def format_signals_for_prompt(
    signals: list[dict[str, object]],
    *,
    label: str = "signals",
    max_entries: int = 60,
) -> str:
    """Format a list of signal dicts into a compact text block for LLM context."""
    if not signals:
        return ""
    lines = [f"## {label} ({len(signals[:max_entries])} series)"]
    for sig in signals[:max_entries]:
        parts = [str(sig.get("series_id", ""))]
        indicator = sig.get("indicator")
        if indicator:
            parts[0] = f"{indicator} ({parts[0]})"
        cat = sig.get("category", "")
        if cat:
            parts.append(f"cat={cat}")
        latest = sig.get("latest")
        if latest is not None:
            parts.append(f"latest={latest}")
        td = sig.get("trend_dir", "")
        if td and td != "unknown":
            r2 = sig.get("trend_r2")
            r2_str = f" r2={r2}" if r2 is not None else ""
            parts.append(f"trend={td}{r2_str}")
        ta = sig.get("trend_accel", "")
        if ta and ta != "unknown":
            parts.append(f"accel={ta}")
        for key in ("roc_short_pct", "roc_mid_pct", "roc_long_pct"):
            val = sig.get(key)
            if val is not None:
                short_key = key.replace("_pct", "").replace("roc_", "")
                parts.append(f"roc_{short_key}={val}%")
        z = sig.get("zscore")
        if z is not None:
            parts.append(f"z={z}")
        regime = sig.get("regime", "")
        if regime and regime != "unknown":
            dur = sig.get("regime_duration", 0)
            parts.append(f"regime={regime}" + (f"({dur}p)" if dur else ""))
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def format_cross_signals_for_prompt(
    signals: list[dict[str, object]],
    *,
    label: str = "Cross-series signals",
    max_entries: int = 40,
) -> str:
    """Format cross-series signal dicts for LLM context."""
    if not signals:
        return ""
    lines = [f"## {label} ({len(signals[:max_entries])} pairs)"]
    for sig in signals[:max_entries]:
        parts = [f"{sig.get('symbol', '')} vs {sig.get('benchmark', '')}"]
        corr = sig.get("correlation_now")
        if corr is not None:
            parts.append(f"corr={corr}")
        corr_roc = sig.get("correlation_roc")
        if corr_roc is not None:
            parts.append(f"corr_roc={corr_roc}")
        regime = sig.get("phase_regime", "")
        if regime:
            parts.append(f"phase={regime}")
        for key in ("decoupling_score", "beta_breakout_score"):
            val = sig.get(key)
            if val is not None:
                short_key = key.replace("_score", "")
                parts.append(f"{short_key}={val}")
        mom = sig.get("compounding_momentum_pct")
        if mom is not None:
            parts.append(f"mom={mom}%")
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)
