from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd

from .alpaca_api import AlpacaAPI


def _pick_numeric(snapshot: dict | None, *paths: tuple[str, ...]) -> float:
    for path in paths:
        current = snapshot or {}
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        value = pd.to_numeric(current, errors="coerce")
        if not pd.isna(value):
            return float(value)
    return np.nan


def _with_snapshot_fields(contracts: pd.DataFrame, snapshots: dict[str, dict]) -> pd.DataFrame:
    if contracts.empty:
        return contracts

    frame = contracts.copy()
    symbols = frame["contractSymbol"].astype(str)

    frame["bid"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("latestQuote", "bp")))
    frame["ask"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("latestQuote", "ap")))
    frame["impliedVolatility"] = symbols.map(
        lambda symbol: _pick_numeric(snapshots.get(symbol), ("impliedVolatility",))
    )
    frame["delta"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("greeks", "delta"), ("delta",)))
    frame["gamma"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("greeks", "gamma"), ("gamma",)))
    frame["theta"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("greeks", "theta"), ("theta",)))
    frame["vega"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("greeks", "vega"), ("vega",)))
    frame["rho"] = symbols.map(lambda symbol: _pick_numeric(snapshots.get(symbol), ("greeks", "rho"), ("rho",)))
    frame["volume"] = symbols.map(
        lambda symbol: _pick_numeric(snapshots.get(symbol), ("dailyBar", "v"), ("latestTrade", "s"))
    )

    open_interest = symbols.map(
        lambda symbol: _pick_numeric(snapshots.get(symbol), ("openInterest",), ("open_interest",))
    )
    if "openInterest" in frame.columns:
        frame["openInterest"] = pd.to_numeric(frame["openInterest"], errors="coerce").fillna(open_interest)
    else:
        frame["openInterest"] = open_interest

    last_price = symbols.map(
        lambda symbol: _pick_numeric(
            snapshots.get(symbol),
            ("latestTrade", "p"),
            ("dailyBar", "c"),
        )
    )
    if "lastPrice" in frame.columns:
        frame["lastPrice"] = pd.to_numeric(frame["lastPrice"], errors="coerce").fillna(last_price)
    else:
        frame["lastPrice"] = last_price

    for col in [
        "strike",
        "lastPrice",
        "bid",
        "ask",
        "impliedVolatility",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
        "volume",
        "openInterest",
    ]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    return frame


def _effective_premium(frame: pd.DataFrame) -> pd.Series:
    bid = pd.to_numeric(frame.get("bid"), errors="coerce")
    ask = pd.to_numeric(frame.get("ask"), errors="coerce")
    last = pd.to_numeric(frame.get("lastPrice"), errors="coerce")

    mid = (bid + ask) / 2.0
    valid_mid = bid.notna() & ask.notna() & bid.gt(0) & ask.gt(0)
    return mid.where(valid_mid, last).where(lambda value: value.gt(0))


def _rank_pct(series: pd.Series, ascending: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    mask = values.notna()
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if mask.any():
        out.loc[mask] = values.loc[mask].rank(pct=True, ascending=not ascending, method="average")
    return out


def load_option_surface(
    api: AlpacaAPI,
    ticker: str,
    underlying_price: float | None,
    expected_price: float | None,
    horizon_days: int,
    max_contracts: int = 450,
) -> pd.DataFrame:
    contracts = api.get_option_contracts(ticker)
    if contracts.empty:
        return contracts

    frame = contracts.copy()
    frame["expiration_dt"] = pd.to_datetime(frame.get("expiration"), errors="coerce")
    today = pd.Timestamp.now(tz=timezone.utc).tz_localize(None).normalize()
    frame["dte"] = (frame["expiration_dt"] - today).dt.days
    frame["strike"] = pd.to_numeric(frame.get("strike"), errors="coerce")
    frame = frame.dropna(subset=["expiration_dt", "dte", "strike"])
    frame = frame[frame["dte"] >= 0].copy()
    if frame.empty:
        return frame

    horizon_days = max(int(horizon_days), 1)
    valid_underlying = pd.to_numeric(pd.Series([underlying_price]), errors="coerce").iloc[0]
    valid_expected = pd.to_numeric(pd.Series([expected_price]), errors="coerce").iloc[0]
    if pd.notna(valid_underlying):
        reference_price = float(valid_underlying)
    elif pd.notna(valid_expected):
        reference_price = float(valid_expected)
    else:
        reference_price = float(frame["strike"].median())

    min_dte = max(0, horizon_days - 7)
    max_dte = max(horizon_days + 45, int(round(horizon_days * 1.8)))
    horizon_slice = frame[(frame["dte"] >= min_dte) & (frame["dte"] <= max_dte)].copy()
    if len(horizon_slice) >= min(len(frame), 40):
        frame = horizon_slice

    low_anchor = min(reference_price, float(valid_expected) if pd.notna(valid_expected) else reference_price)
    high_anchor = max(reference_price, float(valid_expected) if pd.notna(valid_expected) else reference_price)
    strike_low = low_anchor * 0.75
    strike_high = high_anchor * 1.25
    strike_slice = frame[(frame["strike"] >= strike_low) & (frame["strike"] <= strike_high)].copy()
    if len(strike_slice) >= min(len(frame), 40):
        frame = strike_slice

    frame["dte_gap"] = (frame["dte"] - horizon_days).abs()
    frame["strike_gap"] = (frame["strike"] - reference_price).abs()
    frame = frame.sort_values(["dte_gap", "strike_gap", "expiration_dt", "strike"]).head(max_contracts).copy()
    snapshots = api.get_option_snapshots(frame["contractSymbol"].astype(str).tolist(), feed="indicative")
    shaped = _with_snapshot_fields(frame, snapshots)
    shaped["premium"] = _effective_premium(shaped)
    return shaped.sort_values(["dte", "strike", "contractSymbol"]).reset_index(drop=True)


def analyze_option_candidates(
    surface: pd.DataFrame,
    underlying_price: float,
    expected_price: float,
    horizon_days: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if surface.empty:
        return surface, {}

    spot = float(underlying_price)
    target = float(expected_price)
    days = max(int(horizon_days), 1)
    move = target - spot
    preferred_side = "call" if move >= 0 else "put"

    frame = surface.copy()
    frame["type"] = frame["type"].astype(str).str.lower()
    frame["premium"] = pd.to_numeric(frame.get("premium"), errors="coerce")
    frame["dte"] = pd.to_numeric(frame.get("dte"), errors="coerce")
    frame["strike"] = pd.to_numeric(frame.get("strike"), errors="coerce")
    frame = frame.dropna(subset=["type", "premium", "dte", "strike"])
    frame = frame[frame["premium"] > 0].copy()
    if frame.empty:
        return frame, {"preferred_side": preferred_side}

    viable = frame[frame["dte"] >= max(days - 2, 1)].copy()
    if viable.empty:
        viable = frame.copy()

    side_frame = viable[viable["type"] == preferred_side].copy()
    if side_frame.empty:
        side_frame = viable.copy()

    side_frame["contracts_cost"] = side_frame["premium"] * 100.0
    side_frame["delta"] = pd.to_numeric(side_frame.get("delta"), errors="coerce")
    side_frame["gamma"] = pd.to_numeric(side_frame.get("gamma"), errors="coerce")
    side_frame["theta"] = pd.to_numeric(side_frame.get("theta"), errors="coerce")
    side_frame["vega"] = pd.to_numeric(side_frame.get("vega"), errors="coerce")
    side_frame["rho"] = pd.to_numeric(side_frame.get("rho"), errors="coerce")
    side_frame["openInterest"] = pd.to_numeric(side_frame.get("openInterest"), errors="coerce")
    side_frame["volume"] = pd.to_numeric(side_frame.get("volume"), errors="coerce")

    projected_move = (
        side_frame["delta"].fillna(0.0) * move
        + 0.5 * side_frame["gamma"].fillna(0.0) * (move ** 2)
        + side_frame["theta"].fillna(0.0) * np.minimum(side_frame["dte"].to_numpy(dtype=float), float(days))
    )
    side_frame["projected_value"] = (side_frame["premium"] + projected_move).clip(lower=0.01)
    side_frame["projected_pnl"] = (side_frame["projected_value"] - side_frame["premium"]) * 100.0
    side_frame["projected_return_pct"] = np.where(
        side_frame["premium"] > 0,
        ((side_frame["projected_value"] / side_frame["premium"]) - 1.0) * 100.0,
        np.nan,
    )
    side_frame["delta_leverage"] = np.where(
        side_frame["premium"] > 0,
        side_frame["delta"].abs().fillna(0.0) * spot / side_frame["premium"],
        np.nan,
    )
    side_frame["gamma_convexity"] = np.where(
        side_frame["premium"] > 0,
        side_frame["gamma"].abs().fillna(0.0) * (abs(move) ** 2) / side_frame["premium"],
        np.nan,
    )
    side_frame["theta_drag_pct"] = np.where(
        side_frame["premium"] > 0,
        side_frame["theta"].abs().fillna(0.0) * np.minimum(side_frame["dte"].to_numpy(dtype=float), float(days))
        / side_frame["premium"]
        * 100.0,
        np.nan,
    )
    side_frame["vega_risk_pct"] = np.where(
        side_frame["premium"] > 0,
        side_frame["vega"].abs().fillna(0.0) / side_frame["premium"] * 100.0,
        np.nan,
    )
    side_frame["liquidity_score"] = (
        side_frame["openInterest"].fillna(0.0) * 0.65 + side_frame["volume"].fillna(0.0) * 0.35
    )
    side_frame["breakeven"] = np.where(
        side_frame["type"] == "call",
        side_frame["strike"] + side_frame["premium"],
        side_frame["strike"] - side_frame["premium"],
    )
    side_frame["target_gap_pct"] = (side_frame["breakeven"] - target).abs() / spot * 100.0
    side_frame["horizon_fit_days"] = (side_frame["dte"] - days).abs()

    projected_rank = _rank_pct(side_frame["projected_return_pct"], ascending=False)
    leverage_rank = _rank_pct(side_frame["delta_leverage"], ascending=False)
    gamma_rank = _rank_pct(side_frame["gamma_convexity"], ascending=False)
    cost_rank = _rank_pct(side_frame["contracts_cost"], ascending=True)
    theta_rank = _rank_pct(side_frame["theta_drag_pct"], ascending=True)
    liquidity_rank = _rank_pct(side_frame["liquidity_score"], ascending=False)
    fit_rank = _rank_pct(side_frame["horizon_fit_days"], ascending=True)

    side_frame["selection_score"] = (
        projected_rank.fillna(0.0) * 0.40
        + leverage_rank.fillna(0.0) * 0.25
        + gamma_rank.fillna(0.0) * 0.10
        + theta_rank.fillna(0.0) * 0.10
        + cost_rank.fillna(0.0) * 0.10
        + liquidity_rank.fillna(0.0) * 0.03
        + fit_rank.fillna(0.0) * 0.02
    ) * 100.0

    side_frame = side_frame.sort_values(
        ["selection_score", "projected_return_pct", "delta_leverage"],
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)

    summary = {
        "preferred_side": preferred_side,
        "current_price": spot,
        "expected_price": target,
        "expected_move_pct": ((target / spot) - 1.0) * 100.0 if spot else np.nan,
        "horizon_days": days,
        "candidate_count": int(len(side_frame)),
    }
    return side_frame, summary


def load_option_chain(
    api: AlpacaAPI,
    ticker: str,
    expiration: str | None = None,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    contracts = api.get_option_contracts(ticker)
    if contracts.empty or "expiration" not in contracts.columns:
        return [], pd.DataFrame(), pd.DataFrame()

    expirations = sorted(contracts["expiration"].dropna().astype(str).unique().tolist())
    if not expirations:
        return [], pd.DataFrame(), pd.DataFrame()

    if expiration is None:
        return expirations, pd.DataFrame(), pd.DataFrame()

    selected_expiration = expiration if expiration in expirations else expirations[0]
    scoped = contracts[contracts["expiration"].astype(str) == selected_expiration].copy()
    if scoped.empty:
        return expirations, pd.DataFrame(), pd.DataFrame()

    snapshots = api.get_option_snapshots(scoped["contractSymbol"].astype(str).tolist(), feed="indicative")
    shaped = _with_snapshot_fields(scoped, snapshots)
    shaped = shaped.sort_values(["strike", "contractSymbol"]).reset_index(drop=True)

    if "type" not in shaped.columns:
        return expirations, pd.DataFrame(), pd.DataFrame()

    calls = shaped[shaped["type"].astype(str).str.lower() == "call"].copy()
    puts = shaped[shaped["type"].astype(str).str.lower() == "put"].copy()
    return expirations, calls, puts


def rank_options(df: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    for col in ["openInterest", "volume", "impliedVolatility"]:
        if col not in out.columns:
            out[col] = pd.NA

    out["score"] = (
        out["openInterest"].fillna(0) * 0.45
        + out["volume"].fillna(0) * 0.35
        + (1.0 / out["impliedVolatility"].replace(0, pd.NA)).fillna(0) * 100.0 * 0.20
    )
    cols = [
        "contractSymbol",
        "expiration",
        "strike",
        "lastPrice",
        "bid",
        "ask",
        "impliedVolatility",
        "volume",
        "openInterest",
        "score",
    ]
    cols = [col for col in cols if col in out.columns]
    return out[cols].sort_values("score", ascending=False).head(top_n)
