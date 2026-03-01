from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .alpaca_api import AlpacaAPI


DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "PYPL", "SHOP", "UBER",
    "XOM", "CVX", "SLB", "COP", "PFE", "JNJ", "LLY", "UNH", "MRK", "ABBV",
]


def _unique_symbols(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value).upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


BUSINESS_FOCUS_UNIVERSES: dict[str, list[str]] = {
    "All Market": DEFAULT_UNIVERSE,
    "Housing": [
        "HD", "LOW", "DHI", "LEN", "PHM", "TOL", "NVR", "KBH", "BLD", "SHW", "WHR", "Z", "RDFN",
    ],
    "Retail": [
        "AMZN", "WMT", "COST", "TGT", "TJX", "ROST", "BURL", "DG", "DLTR", "BBY", "FIVE", "ETSY", "SHOP",
    ],
    "Media": [
        "DIS", "CMCSA", "CHTR", "WBD", "PARA", "FOXA", "NYT", "ROKU",
    ],
    "Social Media & Entertainment": [
        "META", "NFLX", "SNAP", "PINS", "SPOT", "RBLX", "EA", "TTWO", "DIS",
    ],
    "Advertising": [
        "GOOGL", "META", "TTD", "APP", "ROKU", "SNAP", "PINS", "OMC", "IPG", "MGNI", "CRTO",
    ],
    "Payments & Commerce": [
        "V", "MA", "PYPL", "SHOP", "AMZN", "SQ", "COIN", "AFRM",
    ],
    "Travel & Mobility": [
        "UBER", "ABNB", "BKNG", "EXPE", "DAL", "UAL", "MAR", "HLT",
    ],
    "Healthcare & Life Sciences": [
        "PFE", "JNJ", "LLY", "UNH", "MRK", "ABBV", "ISRG", "TMO",
    ],
}

BUSINESS_FOCUS_DESCRIPTIONS: dict[str, str] = {
    "All Market": "Broad liquid universe across major consumer, technology, finance, energy, and healthcare names.",
    "Housing": "Homebuilding, renovation, housing transactions, and home-linked product businesses.",
    "Retail": "Businesses that primarily sell goods to end consumers through stores or digital storefronts.",
    "Media": "Content distribution, cable, studios, streaming platforms, and broad media networks.",
    "Social Media & Entertainment": "Audience attention businesses driven by social graphs, streaming, music, and interactive entertainment.",
    "Advertising": "Businesses monetizing demand generation, ad spend, ad software, or audience targeting.",
    "Payments & Commerce": "Transaction rails, merchant tooling, checkout, and adjacent commerce enablement.",
    "Travel & Mobility": "Ride-sharing, travel booking, airlines, hotels, and travel demand platforms.",
    "Healthcare & Life Sciences": "Drug makers, managed care, medical tools, and life-science suppliers.",
}

_all_market_symbols = _unique_symbols(
    DEFAULT_UNIVERSE + [symbol for name, symbols in BUSINESS_FOCUS_UNIVERSES.items() if name != "All Market" for symbol in symbols]
)
BUSINESS_FOCUS_UNIVERSES["All Market"] = _all_market_symbols


def business_focus_options() -> list[str]:
    return list(BUSINESS_FOCUS_UNIVERSES.keys())


def business_focus_description(name: str) -> str:
    return BUSINESS_FOCUS_DESCRIPTIONS.get(str(name), "")


def business_focus_universe(name: str) -> list[str]:
    label = str(name or "All Market")
    if label not in BUSINESS_FOCUS_UNIVERSES:
        label = "All Market"
    return list(BUSINESS_FOCUS_UNIVERSES[label])


def _log_slope(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window or (values <= 0).any():
        return np.nan
    y = np.log(values.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def _window_return_pct(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window:
        return np.nan
    start = float(values.iloc[0])
    end = float(values.iloc[-1])
    if start == 0:
        return np.nan
    return ((end / start) - 1.0) * 100.0


def _ratio_minus_one(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator - 1.0)


def _mean_finite(values: list[float]) -> float:
    finite = [float(value) for value in values if np.isfinite(value)]
    if not finite:
        return np.nan
    return float(np.mean(finite))


def _rolling_compound(returns: pd.Series, window: int) -> pd.Series:
    clean = pd.to_numeric(returns, errors="coerce")
    if window <= 1:
        return clean
    return (1.0 + clean).rolling(window).apply(np.prod, raw=True) - 1.0


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if len(valid) < 2:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    std = float(valid.std(ddof=0))
    if std == 0:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    return (numeric - float(valid.mean())) / std


def _phase_regime(compounding_momentum_pct: float, momentum_roc_pct: float, correlation_roc: float) -> str:
    if compounding_momentum_pct >= 0 and correlation_roc < 0:
        return "Decoupling leader"
    if compounding_momentum_pct >= 0 and momentum_roc_pct >= 0 and correlation_roc >= 0:
        return "Beta-linked breakout"
    if compounding_momentum_pct < 0 and correlation_roc >= 0:
        return "Crowded unwind"
    if compounding_momentum_pct < 0 and correlation_roc < 0:
        return "Washout reset"
    return "Transition"



def scan_daily_movers(api: AlpacaAPI, symbols: list[str] | None = None) -> pd.DataFrame:
    universe = symbols or DEFAULT_UNIVERSE
    snapshots = api.get_snapshots(universe, feed="iex")

    rows = []
    for sym, blob in snapshots.items():
        daily = (blob or {}).get("dailyBar") or {}
        prev = (blob or {}).get("prevDailyBar") or {}

        close = pd.to_numeric(daily.get("c"), errors="coerce")
        prev_close = pd.to_numeric(prev.get("c"), errors="coerce")
        if pd.isna(close) or pd.isna(prev_close) or prev_close == 0:
            continue

        pct = ((close / prev_close) - 1.0) * 100.0
        rows.append(
            {
                "symbol": sym,
                "close": close,
                "prev_close": prev_close,
                "change_pct": pct,
                "volume": pd.to_numeric(daily.get("v"), errors="coerce"),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    return out.sort_values("change_pct", ascending=False)



def load_price_history(api: AlpacaAPI, symbol: str, days: int = 365) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = api.get_stock_bars([symbol], start=start, end=end, timeframe="1Day", feed="iex")
    frame = bars.get(symbol.upper(), pd.DataFrame())
    if frame.empty:
        return frame
    keep = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in frame.columns]
    return frame[keep].dropna(subset=["timestamp", "close"]).sort_values("timestamp")


def scan_momentum_profiles(
    api: AlpacaAPI,
    symbols: list[str] | None = None,
    days: int = 180,
) -> pd.DataFrame:
    universe = symbols or DEFAULT_UNIVERSE
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = api.get_stock_bars(universe, start=start, end=end, timeframe="1Day", feed="iex")

    rows: list[dict[str, float | str]] = []
    for symbol in universe:
        frame = bars.get(symbol.upper(), pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        close = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if len(close) < 63:
            continue

        slope_1w = _log_slope(close, 5)
        slope_1m = _log_slope(close, 21)
        slope_3m = _log_slope(close, 63)
        if pd.isna(slope_1m) or pd.isna(slope_3m):
            continue

        roc_1w_to_1m = _ratio_minus_one(slope_1m, slope_1w)
        roc_1m_to_3m = _ratio_minus_one(slope_3m, slope_1m)

        rows.append(
            {
                "symbol": symbol,
                "close": float(close.iloc[-1]),
                "return_1m_pct": _window_return_pct(close, 21),
                "return_3m_pct": _window_return_pct(close, 63),
                "momentum_1w": slope_1w,
                "momentum_1m": slope_1m,
                "momentum_3m": slope_3m,
                "roc_1w_to_1m": roc_1w_to_1m,
                "roc_1m_to_3m": roc_1m_to_3m,
                "momentum_score": slope_3m,
                "momentum_roc_score": _mean_finite([roc_1w_to_1m, roc_1m_to_3m]),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    numeric_cols = [col for col in out.columns if col != "symbol"]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values(["momentum_score", "momentum_roc_score"], ascending=False, na_position="last").reset_index(drop=True)


def scan_correlation_phase_shifts(
    api: AlpacaAPI,
    symbols: list[str] | None = None,
    benchmark: str = "SPY",
    days: int = 252,
    corr_window: int = 20,
    roc_window: int = 10,
    momentum_window: int = 63,
) -> dict[str, pd.DataFrame | str]:
    universe = [str(symbol).upper().strip() for symbol in (symbols or DEFAULT_UNIVERSE) if str(symbol).strip()]
    benchmark_symbol = str(benchmark or "SPY").upper().strip()
    universe = [symbol for symbol in universe if symbol != benchmark_symbol]
    if not universe:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(int(days), momentum_window + corr_window + roc_window + 30))
    bars = api.get_stock_bars(
        [benchmark_symbol] + universe,
        start=start,
        end=end,
        timeframe="1Day",
        feed="iex",
    )

    benchmark_frame = bars.get(benchmark_symbol, pd.DataFrame())
    if benchmark_frame.empty or "close" not in benchmark_frame.columns:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}

    benchmark_price = (
        benchmark_frame[["timestamp", "close"]]
        .dropna(subset=["timestamp", "close"])
        .sort_values("timestamp")
        .rename(columns={"close": "benchmark_close"})
    )
    benchmark_price["benchmark_return"] = pd.to_numeric(benchmark_price["benchmark_close"], errors="coerce").pct_change()
    benchmark_price["benchmark_norm"] = benchmark_price["benchmark_close"] / float(benchmark_price["benchmark_close"].iloc[0])

    history_rows: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    for symbol in universe:
        frame = bars.get(symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        asset_price = (
            frame[["timestamp", "close"]]
            .dropna(subset=["timestamp", "close"])
            .sort_values("timestamp")
            .rename(columns={"close": "close"})
        )
        merged = benchmark_price.merge(asset_price, on="timestamp", how="inner")
        if len(merged) < max(corr_window + roc_window + 5, momentum_window + roc_window + 5):
            continue

        merged["asset_return"] = pd.to_numeric(merged["close"], errors="coerce").pct_change()
        merged["asset_norm"] = merged["close"] / float(merged["close"].iloc[0])
        merged = merged.dropna(subset=["benchmark_return", "asset_return"]).reset_index(drop=True)
        if len(merged) < max(corr_window + roc_window + 3, momentum_window + roc_window + 3):
            continue

        merged["rolling_correlation"] = merged["asset_return"].rolling(corr_window).corr(merged["benchmark_return"])
        merged["correlation_roc"] = merged["rolling_correlation"].diff(roc_window)
        merged["compound_1m"] = _rolling_compound(merged["asset_return"], max(21, corr_window))
        merged["compound_3m"] = _rolling_compound(merged["asset_return"], momentum_window)
        merged["compounding_momentum"] = (
            (1.0 + merged["compound_1m"].clip(lower=-0.99))
            * (1.0 + merged["compound_3m"].clip(lower=-0.99))
            - 1.0
        )
        merged["momentum_roc"] = merged["compounding_momentum"].diff(roc_window)
        merged["symbol"] = symbol
        merged["benchmark"] = benchmark_symbol

        history_rows.append(
            merged[
                [
                    "timestamp",
                    "symbol",
                    "benchmark",
                    "close",
                    "benchmark_close",
                    "asset_norm",
                    "benchmark_norm",
                    "rolling_correlation",
                    "correlation_roc",
                    "compound_1m",
                    "compound_3m",
                    "compounding_momentum",
                    "momentum_roc",
                ]
            ].copy()
        )

        latest = merged.iloc[-1]
        summary_rows.append(
            {
                "symbol": symbol,
                "benchmark": benchmark_symbol,
                "close": float(latest["close"]),
                "correlation_now": float(latest["rolling_correlation"]) if pd.notna(latest["rolling_correlation"]) else np.nan,
                "correlation_roc": float(latest["correlation_roc"]) if pd.notna(latest["correlation_roc"]) else np.nan,
                "compound_1m_pct": float(latest["compound_1m"] * 100.0) if pd.notna(latest["compound_1m"]) else np.nan,
                "compound_3m_pct": float(latest["compound_3m"] * 100.0) if pd.notna(latest["compound_3m"]) else np.nan,
                "compounding_momentum_pct": (
                    float(latest["compounding_momentum"] * 100.0) if pd.notna(latest["compounding_momentum"]) else np.nan
                ),
                "momentum_roc_pct": float(latest["momentum_roc"] * 100.0) if pd.notna(latest["momentum_roc"]) else np.nan,
            }
        )

    history = pd.concat(history_rows, ignore_index=True) if history_rows else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    if summary.empty:
        return {"summary": summary, "history": history, "benchmark": benchmark_symbol}

    corr_roc_z = _zscore(summary["correlation_roc"])
    comp_mom_z = _zscore(summary["compounding_momentum_pct"])
    mom_roc_z = _zscore(summary["momentum_roc_pct"])
    corr_now_z = _zscore(summary["correlation_now"])

    summary["decoupling_score"] = (comp_mom_z * 0.45 + mom_roc_z * 0.35 - corr_roc_z * 0.20) * 100.0
    summary["beta_breakout_score"] = (comp_mom_z * 0.40 + mom_roc_z * 0.25 + corr_roc_z * 0.25 + corr_now_z * 0.10) * 100.0
    summary["correlation_break_score"] = (corr_roc_z.abs() * 0.65 + mom_roc_z.abs() * 0.35) * 100.0
    summary["phase_regime"] = [
        _phase_regime(comp_mom, mom_roc, corr_roc)
        for comp_mom, mom_roc, corr_roc in zip(
            summary["compounding_momentum_pct"],
            summary["momentum_roc_pct"],
            summary["correlation_roc"],
        )
    ]
    summary = summary.sort_values(["decoupling_score", "beta_breakout_score"], ascending=False, na_position="last").reset_index(drop=True)
    return {"summary": summary, "history": history, "benchmark": benchmark_symbol}
