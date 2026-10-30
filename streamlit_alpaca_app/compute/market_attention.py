"""Dated, market-wide observations for research selection; no narrative inference."""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


def build_market_attention_context(
    movers: pd.DataFrame,
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    asof_time_utc: object,
    universe: pd.DataFrame | None = None,
    window: int = 40,
    comparison_shift: int = 20,
    min_observations: int = 30,
    correlation_floor: float = .6,
    benchmark_symbol: str = "",
) -> dict[str, Any]:
    """Compare groups on common coverage, independently of headlines and name cuts.

    Complete linkage prevents a chain of weakly related names becoming a group.
    The two windows overlap deliberately; differences are observations, not a
    statistical claim of a regime change. Today's incomplete bar never enters
    daily correlations. Current snapshots remain separate from closed sessions.
    """
    if window < 3 or not 3 <= min_observations <= window or comparison_shift < 1:
        raise ValueError("Invalid market relationship windows")
    if not 0 < correlation_floor < 1:
        raise ValueError("Correlation floor must be between zero and one")
    asof = pd.to_datetime(asof_time_utc, utc=True)
    today = asof.tz_convert("America/New_York").date()
    current = movers.copy()
    if "symbol" not in current:
        current = pd.DataFrame(columns=["symbol"])
    current["symbol"] = current["symbol"].astype(str).str.upper().str.strip()
    current = current.drop_duplicates("symbol").set_index("symbol")
    identity = universe.drop_duplicates("symbol").set_index("symbol") if isinstance(universe, pd.DataFrame) and "symbol" in universe else pd.DataFrame()
    prices: dict[str, pd.Series] = {}
    current_prices: dict[str, pd.Series] = {}
    adjustment: dict[str, str] = {}
    for symbol, frame in bars_by_symbol.items():
        if (symbol not in current.index and symbol != benchmark_symbol) or not {"timestamp", "close"}.issubset(frame):
            continue
        dates = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        valid = dates.notna() & close.gt(0) & dates.le(asof)
        series = pd.Series(close[valid].to_numpy(), index=dates[valid].dt.tz_convert("America/New_York").dt.date)
        series = series.groupby(level=0).last().sort_index()
        current_prices[symbol] = series
        adjustment[symbol] = str(frame.attrs.get("price_adjustment") or "unknown")
        if symbol not in current.index:
            continue
        series = series[series.index < today]
        if not series.empty:
            prices[symbol] = series
            adjustment[symbol] = str(frame.attrs.get("price_adjustment") or "unknown")
    panel = pd.concat(prices, axis=1).sort_index() if prices else pd.DataFrame()
    # Missing sessions remain missing. Never bridge or forward-fill a halt/gap.
    returns = panel.pct_change(fill_method=None)
    recent = returns.tail(window)
    previous = returns.iloc[:-comparison_shift].tail(window)
    recent_ok = set(recent.columns[recent.count().ge(min_observations) & recent.std().gt(0)])
    previous_ok = set(previous.columns[previous.count().ge(min_observations) & previous.std().gt(0)])
    adjusted = {s for s, basis in adjustment.items() if basis == "all" and s in prices}
    common = sorted(recent_ok & previous_ok & adjusted)
    correlations = [part[common].corr(min_periods=min_observations) for part in (recent, previous)]
    # Require comparable pair coverage instead of inventing zero correlations.
    if common:
        missing = correlations[0].isna() | correlations[1].isna()
        while missing.to_numpy().any():
            common.remove(missing.sum().idxmax())
            correlations = [part[common].corr(min_periods=min_observations) for part in (recent, previous)]
            missing = correlations[0].isna() | correlations[1].isna()

    def group(corr: pd.DataFrame) -> tuple[dict[str, list[str]], dict[str, str]]:
        if len(common) < 2:
            return {}, {}
        distances = np.sqrt(np.maximum((1 - corr.to_numpy().clip(-1, 1)) / 2, 0))
        np.fill_diagonal(distances, 0)
        labels = fcluster(linkage(squareform(distances, checks=False), method="complete"),
                          t=np.sqrt((1 - correlation_floor) / 2), criterion="distance")
        groups: dict[str, list[str]] = {}
        membership: dict[str, str] = {}
        for label in sorted(set(labels)):
            members = [symbol for symbol, value in zip(common, labels) if value == label]
            if len(members) < 2:
                continue
            key = "returns:" + hashlib.sha256("|".join(members).encode()).hexdigest()[:12]
            groups[key] = members
            membership.update(dict.fromkeys(members, key))
        return groups, membership

    groups, membership = group(correlations[0])
    old_groups, old_membership = group(correlations[1])

    def performance(symbol: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        series = current_prices.get(symbol, pd.Series(dtype=float))
        if not series.empty and adjustment.get(symbol) == "all":
            result["history_through"] = str(series.index[-1])
            result["includes_current_snapshot"] = bool(series.index[-1] == today)
            expected = list(panel.index) + ([today] if series.index[-1] == today else [])
            if not expected or series.index[-1] != expected[-1]:
                return result
            for horizon in (5, 20):
                if len(series) >= horizon + 1:
                    # Use a shared session index so missing bars do not turn a
                    # five-session measure into a longer, misleading horizon.
                    if len(expected) >= horizon + 1 and expected[-horizon - 1] in series.index:
                        result[f"return_{horizon}d_pct"] = (series.iloc[-1] / series.loc[expected[-horizon - 1]] - 1) * 100
            week_start = pd.Timestamp(today) - pd.Timedelta(days=pd.Timestamp(today).weekday())
            anchor_dates = panel.index[panel.index < week_start.date()]
            if len(anchor_dates) and anchor_dates[-1] in series.index:
                result["week_pct"] = (series.iloc[-1] / series.loc[anchor_dates[-1]] - 1) * 100
            history = returns[symbol].dropna().tail(window) if symbol in returns else pd.Series(dtype=float)
            if series.index[-1] == today and len(history) >= min_observations and history.std() > 0:
                last_closed = panel[symbol].iloc[-1]
                if pd.notna(last_closed):
                    result["daily_return_z"] = ((series.iloc[-1] / last_closed - 1) - history.mean()) / history.std()
        return result

    records = []
    for symbol, snapshot in current.iterrows():
        meta = identity.loc[symbol] if symbol in identity.index else pd.Series(dtype=object)
        row: dict[str, Any] = {"symbol": symbol,
            "name": meta.get("security_name") or meta.get("name") or snapshot.get("security_name"),
            "sector": meta.get("sector"), "day_pct": snapshot.get("change_pct"),
            "bar_time_utc": snapshot.get("price_observed_at"),
            "observed_at_utc": snapshot.get("observed_at_utc"),
            "snapshot_price_adjustment": snapshot.get("price_adjustment"),
            "price_adjustment": adjustment.get(symbol, "missing"),
            "group": membership.get(symbol), "previous_group": old_membership.get(symbol)}
        row.update(performance(symbol))
        peers = [s for s in old_groups.get(old_membership.get(symbol, ""), []) if s != symbol]
        if peers:
            row["previous_peer_correlation"] = correlations[1].loc[symbol, peers].mean()
            row["current_peer_correlation"] = correlations[0].loc[symbol, peers].mean()
            row["peer_correlation_change"] = row["current_peer_correlation"] - row["previous_peer_correlation"]
        records.append(row)
    benchmark = {"symbol": benchmark_symbol, **performance(benchmark_symbol)}
    for row in records:
        for column in ("week_pct", "return_5d_pct", "return_20d_pct"):
            if column in row and column in benchmark:
                row[column.removesuffix("_pct") + "_excess_pp"] = row[column] - benchmark[column]
    frame = pd.DataFrame(records, columns=list(dict.fromkeys(key for row in records for key in row)) or ["symbol", "sector"])
    group_rows = []
    for key, members in groups.items():
        selected = frame[frame["symbol"].isin(members)]
        prior = {old_membership[s] for s in members if s in old_membership}
        group_rows.append({"id": key, "members": members,
            "previous_group_overlap": {p: sorted(set(members) & set(old_groups[p])) for p in sorted(prior)},
            "day_mean_pct": pd.to_numeric(selected.get("day_pct"), errors="coerce").mean(),
            "week_mean_pct": pd.to_numeric(selected.get("week_pct", pd.Series(dtype=float)), errors="coerce").mean()})
    sectors = []
    if "sector" in frame:
        for sector, members in frame.dropna(subset=["sector"]).groupby("sector"):
            sectors.append({"sector": sector, "count": len(members),
                **{f"{column}_mean": pd.to_numeric(members.get(column, pd.Series(dtype=float)), errors="coerce").mean()
                   for column in ("day_pct", "week_pct", "return_20d_pct")}})
    return {"asof_time_utc": asof.isoformat(),
        "coverage": {"observed_symbols": len(current), "history_symbols": len(prices),
            "comparable_relationship_symbols": len(common),
            "missing_history": sorted(set(current.index) - set(prices)),
            "unverified_adjustment": sorted(set(prices) - adjusted),
            "relationship_ineligible": sorted(set(current.index) - set(common)),
            "index_attribution": "unavailable: no dated benchmark membership and weights supplied"},
        "method": {"grouping": "complete-linkage positive return correlation",
            "window_sessions": window, "comparison_shift_sessions": comparison_shift,
            "minimum_pair_observations": min_observations, "correlation_floor": correlation_floor,
            "current_window": [str(recent.index.min()), str(recent.index.max())],
            "previous_window": [str(previous.index.min()), str(previous.index.max())],
            "basis": "all-adjusted daily returns through prior completed session; multi-day performance uses the latest adjusted snapshot bar when present",
            "window_comparison": "Same eligible instruments in both windows; group IDs identify exact member sets, not permanent economic themes. New or missing history is coverage, not a relationship break.",
            "interpretation": "Observed relationships, not evidence of investor flows or statistically established regime changes. Sector labels are independent context."},
        "benchmark": benchmark, "groups": group_rows, "previous_groups": old_groups, "sectors": sectors,
        "columns": list(frame.columns), "observations": frame.round(4).where(frame.notna(), None).values.tolist()}
