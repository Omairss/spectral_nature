from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "ret_5_pct",
    "ret_21_pct",
    "ret_63_pct",
    "rsi_14",
    "vol_20_ann_pct",
    "pullback_from_ath_pct",
    "channel_position",
    "dist_to_support_pct",
    "dist_to_resistance_pct",
]


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = pd.to_numeric(series, errors="coerce").diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _robust_scale(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = np.nanmedian(values, axis=0)
    q75 = np.nanpercentile(values, 75, axis=0)
    q25 = np.nanpercentile(values, 25, axis=0)
    scale = q75 - q25
    fallback = np.nanstd(values, axis=0)
    scale = np.where(np.isfinite(scale) & (scale > 1e-9), scale, fallback)
    scale = np.where(np.isfinite(scale) & (scale > 1e-9), scale, 1.0)
    return center, scale


def _future_log_path(log_close: np.ndarray, start_idx: int, horizon: int) -> np.ndarray | None:
    window = log_close[start_idx : start_idx + horizon + 1]
    if len(window) != horizon + 1 or not np.isfinite(window).all():
        return None
    return np.diff(window)


def build_signal_frame(price: pd.DataFrame, channel_window: int = 63) -> pd.DataFrame:
    if price.empty:
        return pd.DataFrame()

    frame = price.copy().sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in frame.columns:
            frame[col] = np.nan

    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["high"] = pd.to_numeric(frame["high"], errors="coerce").fillna(frame["close"])
    frame["low"] = pd.to_numeric(frame["low"], errors="coerce").fillna(frame["close"])
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "close"]).reset_index(drop=True)
    if frame.empty:
        return frame

    close = frame["close"]
    log_close = np.log(close.replace(0, np.nan))
    rolling_high = frame["high"].rolling(channel_window, min_periods=min(20, channel_window)).max()
    rolling_low = frame["low"].rolling(channel_window, min_periods=min(20, channel_window)).min()
    channel_width = rolling_high - rolling_low

    frame["ret_5_pct"] = close.pct_change(5) * 100.0
    frame["ret_21_pct"] = close.pct_change(21) * 100.0
    frame["ret_63_pct"] = close.pct_change(63) * 100.0
    frame["rsi_14"] = _rsi(close)
    frame["vol_20_ann_pct"] = log_close.diff().rolling(20).std(ddof=0) * np.sqrt(252.0) * 100.0
    frame["ath"] = close.cummax()
    frame["pullback_from_ath_pct"] = ((close / frame["ath"]) - 1.0) * 100.0
    frame["channel_support"] = rolling_low
    frame["channel_resistance"] = rolling_high
    frame["channel_mid"] = (rolling_high + rolling_low) / 2.0
    frame["channel_width_pct"] = np.where(rolling_low > 0, ((rolling_high / rolling_low) - 1.0) * 100.0, np.nan)
    frame["channel_position"] = np.where(channel_width > 0, (close - rolling_low) / channel_width, np.nan)
    frame["dist_to_support_pct"] = np.where(rolling_low > 0, ((close / rolling_low) - 1.0) * 100.0, np.nan)
    frame["dist_to_resistance_pct"] = np.where(close > 0, ((rolling_high / close) - 1.0) * 100.0, np.nan)
    return frame


def summarize_signal_frame(frame: pd.DataFrame) -> dict[str, float | str]:
    if frame.empty:
        return {}

    latest = frame.dropna(subset=["close"]).iloc[-1]
    ret_21 = float(pd.to_numeric(latest.get("ret_21_pct"), errors="coerce"))
    ret_63 = float(pd.to_numeric(latest.get("ret_63_pct"), errors="coerce"))
    channel_position = float(pd.to_numeric(latest.get("channel_position"), errors="coerce"))
    pullback = float(pd.to_numeric(latest.get("pullback_from_ath_pct"), errors="coerce"))
    rsi = float(pd.to_numeric(latest.get("rsi_14"), errors="coerce"))
    dist_to_resistance = float(pd.to_numeric(latest.get("dist_to_resistance_pct"), errors="coerce"))
    dist_to_support = float(pd.to_numeric(latest.get("dist_to_support_pct"), errors="coerce"))

    if np.isfinite(ret_21) and np.isfinite(channel_position) and ret_21 > 5 and channel_position >= 0.7:
        regime = "Trend breakout"
    elif np.isfinite(pullback) and np.isfinite(rsi) and pullback <= -10 and rsi <= 45:
        regime = "Deep pullback"
    elif np.isfinite(dist_to_resistance) and dist_to_resistance <= 2.0:
        regime = "Resistance probe"
    elif np.isfinite(dist_to_support) and dist_to_support <= 2.0:
        regime = "Support test"
    elif np.isfinite(ret_63) and ret_63 > 0:
        regime = "Trend continuation"
    else:
        regime = "Range / consolidation"

    return {
        "close": float(pd.to_numeric(latest.get("close"), errors="coerce")),
        "ath": float(pd.to_numeric(latest.get("ath"), errors="coerce")),
        "pullback_from_ath_pct": pullback,
        "channel_support": float(pd.to_numeric(latest.get("channel_support"), errors="coerce")),
        "channel_resistance": float(pd.to_numeric(latest.get("channel_resistance"), errors="coerce")),
        "channel_position": channel_position,
        "dist_to_support_pct": dist_to_support,
        "dist_to_resistance_pct": dist_to_resistance,
        "ret_5_pct": float(pd.to_numeric(latest.get("ret_5_pct"), errors="coerce")),
        "ret_21_pct": ret_21,
        "ret_63_pct": ret_63,
        "rsi_14": rsi,
        "vol_20_ann_pct": float(pd.to_numeric(latest.get("vol_20_ann_pct"), errors="coerce")),
        "regime": regime,
    }


def forecast_next_week(frame: pd.DataFrame, horizon: int = 5, simulations: int = 1500) -> dict[str, object]:
    if frame.empty or "close" not in frame.columns or len(frame) < max(140, horizon + 70):
        return {}

    features = frame[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    feature_mask = features.notna().all(axis=1).to_numpy()
    valid_positions = np.flatnonzero(feature_mask)
    if valid_positions.size == 0:
        return {}

    current_pos = int(valid_positions[-1])
    candidate_positions = valid_positions[valid_positions < len(frame) - horizon]
    if candidate_positions.size < 25:
        return {}

    log_close = np.log(pd.to_numeric(frame["close"], errors="coerce").replace(0, np.nan).to_numpy(dtype=float))
    current_vector = features.iloc[current_pos].to_numpy(dtype=float)

    candidate_vectors: list[np.ndarray] = []
    candidate_paths: list[np.ndarray] = []
    candidate_vols: list[float] = []
    candidate_dates: list[pd.Timestamp] = []
    for pos in candidate_positions.tolist():
        path = _future_log_path(log_close, pos, horizon)
        if path is None:
            continue
        candidate_vectors.append(features.iloc[pos].to_numpy(dtype=float))
        candidate_paths.append(path)
        candidate_vols.append(float(pd.to_numeric(frame.iloc[pos]["vol_20_ann_pct"], errors="coerce")))
        candidate_dates.append(pd.to_datetime(frame.iloc[pos]["timestamp"]))

    if len(candidate_paths) < 25:
        return {}

    features_matrix = np.vstack(candidate_vectors)
    paths = np.vstack(candidate_paths)
    vols = np.asarray(candidate_vols, dtype=float)
    center, scale = _robust_scale(features_matrix)
    current_z = (current_vector - center) / scale
    distances = np.sqrt(np.nanmean(((features_matrix - center) / scale - current_z) ** 2, axis=1))
    if not np.isfinite(distances).any():
        return {}

    nearest_count = min(80, len(distances))
    nearest_order = np.argsort(distances)[:nearest_count]
    nearest_distances = distances[nearest_order]
    sigma = float(np.nanpercentile(nearest_distances, 60))
    if not np.isfinite(sigma) or sigma <= 1e-6:
        positive = nearest_distances[nearest_distances > 0]
        sigma = float(np.nanmean(positive)) if positive.size else 1.0
    weights = np.exp(-0.5 * (nearest_distances / max(sigma, 1e-6)) ** 2)
    if not np.isfinite(weights).any() or weights.sum() == 0:
        weights = np.ones_like(nearest_distances)
    weights = weights / weights.sum()

    nearest_paths = paths[nearest_order]
    nearest_vols = vols[nearest_order]
    current_vol = float(pd.to_numeric(frame.iloc[current_pos]["vol_20_ann_pct"], errors="coerce"))
    if not np.isfinite(current_vol) or current_vol <= 0:
        current_vol = float(np.nanmedian(nearest_vols[np.isfinite(nearest_vols) & (nearest_vols > 0)])) if np.isfinite(nearest_vols).any() else 25.0
    vol_scale = np.where(
        np.isfinite(nearest_vols) & (nearest_vols > 0),
        np.clip(current_vol / nearest_vols, 0.7, 1.4),
        1.0,
    )
    adjusted_paths = nearest_paths * vol_scale[:, None]

    rng = np.random.default_rng(7)
    sample_ids = rng.choice(len(adjusted_paths), size=simulations, replace=True, p=weights)
    sampled_paths = adjusted_paths[sample_ids]
    residual_std = np.nanstd(adjusted_paths - np.average(adjusted_paths, axis=0, weights=weights), axis=0)
    residual_std = np.where(np.isfinite(residual_std), residual_std, 0.0)
    noise = rng.normal(0.0, residual_std * 0.15, size=sampled_paths.shape)
    simulated_log_paths = sampled_paths + noise

    last_close = float(pd.to_numeric(frame.iloc[current_pos]["close"], errors="coerce"))
    simulated_prices = last_close * np.exp(np.cumsum(simulated_log_paths, axis=1))
    future_dates = pd.bdate_range(pd.to_datetime(frame.iloc[current_pos]["timestamp"]) + pd.offsets.BDay(1), periods=horizon)

    percentiles = pd.DataFrame(
        {
            "timestamp": future_dates,
            "p10": np.percentile(simulated_prices, 10, axis=0),
            "p25": np.percentile(simulated_prices, 25, axis=0),
            "p50": np.percentile(simulated_prices, 50, axis=0),
            "p75": np.percentile(simulated_prices, 75, axis=0),
            "p90": np.percentile(simulated_prices, 90, axis=0),
        }
    )
    terminal_prices = simulated_prices[:, -1]
    support = float(pd.to_numeric(frame.iloc[current_pos]["channel_support"], errors="coerce"))
    resistance = float(pd.to_numeric(frame.iloc[current_pos]["channel_resistance"], errors="coerce"))
    weighted_expected_path = np.average(adjusted_paths, axis=0, weights=weights)

    return {
        "percentiles": percentiles,
        "simulated_prices": simulated_prices,
        "current_price": last_close,
        "support": support,
        "resistance": resistance,
        "up_probability": float(np.mean(terminal_prices > last_close)),
        "breakout_probability": float(np.mean(terminal_prices > resistance)) if np.isfinite(resistance) else np.nan,
        "support_break_probability": float(np.mean(terminal_prices < support)) if np.isfinite(support) else np.nan,
        "expected_return_pct": float((np.mean(terminal_prices) / last_close - 1.0) * 100.0),
        "median_return_pct": float((np.median(terminal_prices) / last_close - 1.0) * 100.0),
        "expected_path_return_pct": float((np.exp(np.cumsum(weighted_expected_path)[-1]) - 1.0) * 100.0),
        "analog_count": int(len(nearest_order)),
        "analog_dates": [candidate_dates[idx] for idx in nearest_order],
    }


__all__ = [
    "FEATURE_COLUMNS",
    "build_signal_frame",
    "forecast_next_week",
    "summarize_signal_frame",
]
