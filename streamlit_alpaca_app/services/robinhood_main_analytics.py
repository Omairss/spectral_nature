from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

from . import robinhood_main_store


LOOKBACK_OPTIONS = ("1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "All")
_ANNUAL_TRADING_DAYS = 252.0
_MIN_RISK_OBSERVATIONS = 20


def lookback_start(lookback: str, *, now: datetime | None = None) -> datetime | None:
    current = pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC")
    normalized = str(lookback or "").strip().upper()
    if normalized == "ALL":
        return None
    if normalized == "YTD":
        return datetime(current.year, 1, 1, tzinfo=timezone.utc)
    offsets = {
        "1D": pd.DateOffset(days=1),
        "1W": pd.DateOffset(weeks=1),
        "1M": pd.DateOffset(months=1),
        "3M": pd.DateOffset(months=3),
        "1Y": pd.DateOffset(years=1),
        "5Y": pd.DateOffset(years=5),
    }
    offset = offsets.get(normalized)
    return (current - offset).to_pydatetime() if offset is not None else None


def filter_lookback(frame: pd.DataFrame, lookback: str) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns:
        return frame.copy()
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
    start = lookback_start(lookback, now=out["timestamp"].max().to_pydatetime() if not out.empty else None)
    if start is not None:
        out = out[out["timestamp"] >= start]
    return out.reset_index(drop=True)


def _price_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = [
        {
            "timestamp": row.get("observed_at"),
            "symbol": str(row.get("series_key") or ""),
            "close": (row.get("values") or {}).get("close"),
        }
        for row in rows
    ]
    frame = pd.DataFrame(records)
    if frame.empty:
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "symbol", "close"])
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return pd.DataFrame()
    # Building the wide frame one symbol at a time avoids pandas MultiIndex-code
    # overflow on catalogs with hundreds of symbols while preserving one value
    # per timestamp and symbol.
    return pd.concat(
        {
            symbol: rows.groupby("timestamp", sort=True)["close"].last()
            for symbol, rows in frame.groupby("symbol", sort=True)
        },
        axis=1,
    ).sort_index()


def _history_start(lookback: str) -> datetime | None:
    selected = lookback_start(lookback)
    risk_floor = (pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=1)).to_pydatetime()
    if selected is None:
        return None
    return min(selected, risk_floor)


def _slice_prices(prices: pd.DataFrame, start: datetime | None) -> pd.DataFrame:
    if prices.empty or start is None:
        return prices.copy()
    return prices.loc[prices.index >= pd.Timestamp(start)].copy()


def _daily_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices.copy()
    return prices.resample("B").last().ffill(limit=3)


def _return_frame(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    return returns.dropna(how="all")


def _series_return(series: pd.Series) -> float | None:
    usable = pd.to_numeric(series, errors="coerce").dropna()
    if len(usable) < 2 or float(usable.iloc[0]) <= 0:
        return None
    return float(usable.iloc[-1] / usable.iloc[0] - 1.0)


def _max_drawdown(series: pd.Series) -> tuple[float | None, float | None]:
    usable = pd.to_numeric(series, errors="coerce").dropna()
    if len(usable) < 2:
        return None, None
    drawdown = usable / usable.cummax() - 1.0
    return float(drawdown.min()), float(drawdown.iloc[-1])


def _tail_loss(returns: pd.Series) -> float | None:
    usable = pd.to_numeric(returns, errors="coerce").dropna()
    if len(usable) < 10:
        return None
    cutoff = float(usable.quantile(0.05))
    tail = usable[usable <= cutoff]
    return float(-tail.mean()) if not tail.empty else None


def _robust_latest_zscore(returns: pd.Series) -> float | None:
    usable = pd.to_numeric(returns, errors="coerce").dropna()
    if len(usable) < 20:
        return None
    history = usable.iloc[:-1]
    if history.empty:
        return None
    median = float(history.median())
    mad = float((history - median).abs().median())
    scale = 1.4826 * mad
    if scale <= 1e-12:
        return None
    return float((usable.iloc[-1] - median) / scale)


def _momentum_regime(series: pd.Series) -> tuple[str, float | None]:
    usable = pd.to_numeric(series, errors="coerce").dropna()
    if len(usable) < 12:
        return "Insufficient history", None
    window = min(21, max(5, (len(usable) - 1) // 3))
    if len(usable) < 2 * window + 1:
        window = max(5, (len(usable) - 1) // 2)
    if window < 5 or len(usable) < 2 * window + 1:
        return "Insufficient history", None
    recent = float(usable.iloc[-1] / usable.iloc[-window - 1] - 1.0)
    prior = float(usable.iloc[-window - 1] / usable.iloc[-2 * window - 1] - 1.0)
    change = recent - prior
    if recent >= 0 and prior >= 0:
        label = "Accelerating" if change >= 0 else "Cooling"
    elif recent < 0 <= prior:
        label = "Reversal"
    elif prior < 0 <= recent:
        label = "Recovery"
    else:
        label = "Stabilizing" if change >= 0 else "Weakening"
    return label, abs(change)


def _cluster_correlation(correlation: pd.DataFrame) -> pd.DataFrame:
    if correlation.empty or len(correlation) < 3:
        return correlation
    usable = correlation.fillna(0.0).clip(-1.0, 1.0)
    np.fill_diagonal(usable.values, 1.0)
    distance = np.sqrt(np.maximum(0.0, 0.5 * (1.0 - usable.to_numpy(dtype=float))))
    try:
        order = leaves_list(linkage(squareform(distance, checks=False), method="average"))
    except (ValueError, FloatingPointError):
        return correlation
    labels = [correlation.index[int(index)] for index in order]
    return correlation.loc[labels, labels]


def _principal_components(correlation: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float | None]]:
    if correlation.empty:
        return pd.DataFrame(), {
            "first_component_share": None,
            "effective_risk_bets": None,
            "average_correlation": None,
        }
    usable = correlation.dropna(axis=0, how="all").dropna(axis=1, how="all")
    common = usable.index.intersection(usable.columns)
    usable = usable.loc[common, common].fillna(0.0).clip(-1.0, 1.0)
    if usable.empty:
        return pd.DataFrame(), {
            "first_component_share": None,
            "effective_risk_bets": None,
            "average_correlation": None,
        }
    np.fill_diagonal(usable.values, 1.0)
    eigenvalues, eigenvectors = np.linalg.eigh(usable.to_numpy(dtype=float))
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    total = float(eigenvalues.sum())
    if total <= 0:
        return pd.DataFrame(), {
            "first_component_share": None,
            "effective_risk_bets": None,
            "average_correlation": None,
        }
    pc1 = eigenvectors[:, 0] * math.sqrt(float(eigenvalues[0]))
    if float(pc1.sum()) < 0:
        pc1 *= -1
    pc2 = (
        eigenvectors[:, 1] * math.sqrt(float(eigenvalues[1]))
        if len(eigenvalues) > 1
        else np.zeros(len(usable))
    )
    if len(pc2) and float(pc2[np.argmax(np.abs(pc2))]) < 0:
        pc2 *= -1
    mask = ~np.eye(len(usable), dtype=bool)
    off_diagonal = usable.to_numpy(dtype=float)[mask]
    return (
        pd.DataFrame({"symbol": usable.index, "pc1": pc1, "pc2": pc2}),
        {
            "first_component_share": float(eigenvalues[0] / total),
            "effective_risk_bets": float(total * total / np.square(eigenvalues).sum()),
            "average_correlation": float(off_diagonal.mean()) if len(off_diagonal) else None,
        },
    )


def _aggregate_equity_positions(holdings: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = holdings.copy() if isinstance(holdings, pd.DataFrame) else pd.DataFrame(holdings)
    if frame.empty:
        return pd.DataFrame()
    if "asset_class" in frame.columns:
        frame = frame[frame["asset_class"].astype(str).str.lower() == "equity"].copy()
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame = frame[frame["symbol"] != ""]
    for column in ("market_value", "daily_pl", "unrealized_pl", "change_today"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    records: list[dict[str, Any]] = []
    for symbol, rows in frame.groupby("symbol", sort=True):
        raw_value = rows["market_value"].fillna(0.0)
        direction = rows.get("direction", pd.Series("long", index=rows.index)).astype(str).str.lower()
        signed_value = raw_value.where(direction != "short", -raw_value.abs())
        gross_value = float(raw_value.abs().sum())
        weighted_move = (
            float((rows["change_today"].fillna(0.0) * raw_value.abs()).sum() / gross_value)
            if gross_value > 0
            else None
        )
        records.append(
            {
                "symbol": symbol,
                "gross_market_value": gross_value,
                "net_market_value": float(signed_value.sum()),
                "daily_pl": float(rows["daily_pl"].fillna(0.0).sum()),
                "unrealized_pl": float(rows["unrealized_pl"].fillna(0.0).sum()),
                "change_today": weighted_move,
                "accounts": int(rows.get("account", pd.Series(index=rows.index, dtype=object)).nunique()),
            }
        )
    result = pd.DataFrame(records)
    total = float(result["gross_market_value"].sum()) if not result.empty else 0.0
    result["allocation"] = result["gross_market_value"] / total if total > 0 else 0.0
    return result


def equity_position_analytics(
    *,
    portfolio_id: str,
    environment: str,
    holdings: list[dict[str, Any]] | pd.DataFrame,
    lookback: str,
) -> dict[str, Any]:
    positions = _aggregate_equity_positions(holdings)
    empty = {
        "positions": positions,
        "correlation": pd.DataFrame(),
        "components": pd.DataFrame(),
        "stats": {},
        "lookback": lookback,
    }
    if positions.empty:
        return empty
    symbols = positions["symbol"].tolist()
    requested_symbols = [*symbols]
    if "SPY" not in requested_symbols:
        requested_symbols.append("SPY")
    rows = robinhood_main_store.get_series_points(
        portfolio_id=portfolio_id,
        environment=environment,
        series_kind="equity_history",
        series_keys=requested_symbols,
        start_at=_history_start(lookback),
    )
    prices = _price_frame(rows)
    if prices.empty:
        positions["history_status"] = "Unavailable"
        return empty | {"positions": positions}

    latest_timestamp = prices.index.max().to_pydatetime()
    selected_prices = _slice_prices(prices, lookback_start(lookback, now=latest_timestamp))
    risk_prices = _daily_prices(
        _slice_prices(prices, (pd.Timestamp(latest_timestamp) - pd.DateOffset(years=1)).to_pydatetime())
    )
    risk_returns = _return_frame(risk_prices)
    position_returns = risk_returns.reindex(columns=[symbol for symbol in symbols if symbol in risk_returns.columns])
    eligible = [
        symbol
        for symbol in position_returns.columns
        if int(position_returns[symbol].notna().sum()) >= _MIN_RISK_OBSERVATIONS
    ]
    position_returns = position_returns[eligible]
    correlation = position_returns.corr(min_periods=_MIN_RISK_OBSERVATIONS) if eligible else pd.DataFrame()
    correlation = _cluster_correlation(correlation)
    components, component_stats = _principal_components(correlation)

    contribution_by_symbol: dict[str, float] = {}
    risk_share_by_symbol: dict[str, float] = {}
    portfolio_volatility: float | None = None
    portfolio_tail_loss: float | None = None
    portfolio_beta: float | None = None
    if eligible:
        common = position_returns[eligible].dropna()
        if len(common) >= _MIN_RISK_OBSERVATIONS:
            covered = positions.set_index("symbol").loc[eligible]
            gross_covered = float(covered["gross_market_value"].sum())
            weights = (
                covered["net_market_value"].to_numpy(dtype=float) / gross_covered
                if gross_covered > 0
                else np.zeros(len(covered))
            )
            covariance = common.cov().to_numpy(dtype=float) * _ANNUAL_TRADING_DAYS
            portfolio_variance = float(weights @ covariance @ weights)
            if portfolio_variance > 0:
                portfolio_volatility = math.sqrt(portfolio_variance)
                volatility_contribution = weights * (covariance @ weights) / portfolio_volatility
                variance_share = weights * (covariance @ weights) / portfolio_variance
                contribution_by_symbol = dict(zip(eligible, volatility_contribution))
                risk_share_by_symbol = dict(zip(eligible, variance_share))
            portfolio_returns = pd.Series(common.to_numpy(dtype=float) @ weights, index=common.index)
            portfolio_tail_loss = _tail_loss(portfolio_returns)
            if "SPY" in risk_returns.columns:
                aligned = pd.concat(
                    [portfolio_returns.rename("portfolio"), risk_returns["SPY"].rename("SPY")],
                    axis=1,
                ).dropna()
                benchmark_variance = float(aligned["SPY"].var()) if len(aligned) > 2 else 0.0
                if benchmark_variance > 0:
                    portfolio_beta = float(aligned.cov().loc["portfolio", "SPY"] / benchmark_variance)

    diagnostic_rows: list[dict[str, Any]] = []
    for row in positions.to_dict(orient="records"):
        symbol = str(row["symbol"])
        selected = selected_prices[symbol] if symbol in selected_prices.columns else pd.Series(dtype=float)
        risk_series = risk_returns[symbol] if symbol in risk_returns.columns else pd.Series(dtype=float)
        annualized_volatility = (
            float(risk_series.std() * math.sqrt(_ANNUAL_TRADING_DAYS))
            if int(risk_series.notna().sum()) >= _MIN_RISK_OBSERVATIONS
            else None
        )
        negative_returns = risk_series[risk_series < 0].dropna()
        downside_deviation = (
            float(negative_returns.std() * math.sqrt(_ANNUAL_TRADING_DAYS))
            if len(negative_returns) > 1
            else None
        )
        max_drawdown, current_drawdown = _max_drawdown(selected)
        regime, regime_intensity = (
            _momentum_regime(risk_prices[symbol])
            if symbol in risk_prices.columns
            else ("Insufficient history", None)
        )
        beta = None
        if symbol in risk_returns.columns and "SPY" in risk_returns.columns:
            aligned = risk_returns[[symbol, "SPY"]].dropna()
            benchmark_variance = float(aligned["SPY"].var()) if len(aligned) > 2 else 0.0
            if benchmark_variance > 0:
                beta = float(aligned.cov().loc[symbol, "SPY"] / benchmark_variance)
        diagnostic_rows.append(
            row
            | {
                "period_return": _series_return(selected),
                "annualized_volatility": annualized_volatility,
                "downside_deviation": downside_deviation,
                "max_drawdown": max_drawdown,
                "current_drawdown": current_drawdown,
                "tail_loss_5pct": _tail_loss(risk_series),
                "beta_vs_spy": beta,
                "latest_move_zscore": _robust_latest_zscore(risk_series),
                "momentum_regime": regime,
                "regime_intensity": regime_intensity,
                "volatility_contribution": contribution_by_symbol.get(symbol),
                "risk_share": risk_share_by_symbol.get(symbol),
                "history_status": "Ready" if symbol in eligible else "Unavailable",
            }
        )
    diagnostics = pd.DataFrame(diagnostic_rows)
    total_market_value = float(positions["gross_market_value"].sum())
    covered_market_value = float(
        positions[positions["symbol"].isin(eligible)]["gross_market_value"].sum()
    )
    allocation = positions["allocation"].to_numpy(dtype=float)
    effective_positions = float(1.0 / np.square(allocation).sum()) if np.square(allocation).sum() > 0 else None
    stats = {
        "history_coverage": covered_market_value / total_market_value if total_market_value > 0 else None,
        "covered_symbols": len(eligible),
        "position_symbols": len(symbols),
        "effective_positions": effective_positions,
        "portfolio_volatility": portfolio_volatility,
        "portfolio_tail_loss_5pct": portfolio_tail_loss,
        "portfolio_beta_vs_spy": portfolio_beta,
        **component_stats,
    }
    return {
        "positions": diagnostics,
        "correlation": correlation,
        "components": components,
        "stats": stats,
        "lookback": lookback,
    }


def opportunity_analytics(
    *,
    portfolio_id: str,
    environment: str,
    lookback: str,
    group_keys: list[str] | None = None,
) -> dict[str, Any]:
    catalog = robinhood_main_store.get_resource(
        portfolio_id=portfolio_id, environment=environment,
        resource_kind="opportunity_catalog", resource_key="catalog",
    )
    if catalog and isinstance(catalog.get("payload"), dict):
        active_sets = list(catalog["payload"].get("sets") or [])
    else:
        snapshot = robinhood_main_store.get_snapshot(portfolio_id=portfolio_id, environment=environment) or {}
        active_sets = list(dict(snapshot.get("details") or {}).get("opportunity_sets") or [])
    active_keys = {str(item["set_key"]) for item in active_sets if isinstance(item, dict) and item.get("set_key")}
    requested_keys = active_keys if group_keys is None else active_keys.intersection(group_keys)
    if not requested_keys:
        return {"series": pd.DataFrame(), "rankings": pd.DataFrame(), "contributions": pd.DataFrame(),
                "correlation": pd.DataFrame(), "groups": [], "lookback": lookback}
    resources = robinhood_main_store.list_resources(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind="opportunity_group",
        resource_keys=sorted(requested_keys),
    )
    group_by_key = {
        str(resource.get("resource_key") or ""): dict(resource.get("payload") or {})
        for resource in resources
        if isinstance(resource.get("payload"), dict)
        and str((resource.get("payload") or {}).get("status") or "") == "ready"
        and str((resource.get("payload") or {}).get("analytics_status") or "") == "ready"
    }
    # Stable IDs own series and correlation. Qualify genuinely identical names
    # for display without letting a name collapse two separate groups.
    named_keys: dict[str, list[str]] = {}
    for key, group in group_by_key.items():
        named_keys.setdefault(str(group.get("name") or "Untitled"), []).append(key)
    for name, keys in named_keys.items():
        if len(keys) > 1:
            for index, key in enumerate(sorted(keys), start=1):
                kind = str(group_by_key[key].get("kind") or "group").replace("_", " ").title()
                group_by_key[key]["name"] = f"{name} · {kind} {index}"
    symbols = sorted(
        {
            str(member.get("symbol") or "").upper()
            for group in group_by_key.values()
            for member in list(group.get("members") or [])
            if isinstance(member, dict) and member.get("symbol")
        }
    )
    rows = robinhood_main_store.get_series_points(
        portfolio_id=portfolio_id,
        environment=environment,
        series_kind="equity_history",
        series_keys=symbols,
        start_at=_history_start(lookback),
    )
    prices = _price_frame(rows)
    series_records: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    group_return_series: dict[str, pd.Series] = {}
    if prices.empty:
        return {
            "series": pd.DataFrame(),
            "rankings": pd.DataFrame(),
            "contributions": pd.DataFrame(),
            "correlation": pd.DataFrame(),
            "groups": resources,
            "lookback": lookback,
        }
    latest_timestamp = prices.index.max().to_pydatetime()
    selected_start = lookback_start(lookback, now=latest_timestamp)
    risk_start = (pd.Timestamp(latest_timestamp) - pd.DateOffset(years=1)).to_pydatetime()
    selected_all = _slice_prices(prices, selected_start)
    risk_all = _daily_prices(_slice_prices(prices, risk_start))

    for group_key, group in group_by_key.items():
        group_symbols = list(
            dict.fromkeys(
                str(member.get("symbol") or "").upper()
                for member in list(group.get("members") or [])
                if isinstance(member, dict)
                and str(member.get("symbol") or "").upper() in prices.columns
            )
        )
        if not group_symbols:
            continue
        selected_prices = selected_all[group_symbols].copy().ffill(limit=3)
        normalized = pd.DataFrame(index=selected_prices.index)
        member_returns: dict[str, float] = {}
        for symbol in group_symbols:
            series = selected_prices[symbol].dropna()
            member_return = _series_return(series)
            if member_return is None:
                continue
            normalized[symbol] = selected_prices[symbol] / float(series.iloc[0]) * 100.0
            member_returns[symbol] = member_return
        if normalized.empty:
            continue
        group_index = normalized.mean(axis=1, skipna=True).dropna()
        if len(group_index) < 2:
            continue
        name = str(group.get("name") or "Untitled")
        for timestamp, value in group_index.items():
            series_records.append(
                {"timestamp": timestamp, "group_key": group_key, "group": name, "index": float(value)}
            )

        risk_returns = _return_frame(risk_all[group_symbols])
        eligible = [
            symbol
            for symbol in group_symbols
            if symbol in risk_returns.columns
            and int(risk_returns[symbol].notna().sum()) >= _MIN_RISK_OBSERVATIONS
        ]
        risk_returns = risk_returns[eligible]
        group_daily = risk_returns.mean(axis=1, skipna=True).dropna()
        group_return_series[group_key] = group_daily
        volatility = (
            float(group_daily.std() * math.sqrt(_ANNUAL_TRADING_DAYS))
            if len(group_daily) >= _MIN_RISK_OBSERVATIONS
            else None
        )
        drawdown = group_index / group_index.cummax() - 1.0
        group_return = float(group_index.iloc[-1] / group_index.iloc[0] - 1.0)
        positive_count = sum(value > 0 for value in member_returns.values())
        member_return_values = np.asarray(list(member_returns.values()), dtype=float)
        absolute_contributions = np.abs(member_return_values / len(member_return_values)) if len(member_return_values) else np.array([])
        top_three_concentration = (
            float(np.sort(absolute_contributions)[-3:].sum() / absolute_contributions.sum())
            if len(absolute_contributions) and float(absolute_contributions.sum()) > 0
            else None
        )
        risk_correlation = (
            risk_returns.corr(min_periods=_MIN_RISK_OBSERVATIONS)
            if len(eligible) >= 2
            else pd.DataFrame()
        )
        _, risk_structure = _principal_components(risk_correlation)
        average_member_volatility = (
            float(risk_returns.std().mean() * math.sqrt(_ANNUAL_TRADING_DAYS))
            if not risk_returns.empty
            else None
        )
        diversification_ratio = (
            average_member_volatility / volatility
            if average_member_volatility is not None and volatility not in {None, 0.0}
            else None
        )
        group_regime, _ = _momentum_regime((1.0 + group_daily).cumprod())
        rankings.append(
            {
                "group_key": group_key,
                "group": name,
                "kind": str(group.get("kind") or "list"),
                "return": group_return,
                "breadth": positive_count / len(member_returns) if member_returns else None,
                "volatility": volatility,
                "downside_deviation": (
                    float(group_daily[group_daily < 0].std() * math.sqrt(_ANNUAL_TRADING_DAYS))
                    if len(group_daily[group_daily < 0]) > 1
                    else None
                ),
                "tail_loss_5pct": _tail_loss(group_daily),
                "max_drawdown": float(drawdown.min()) if not drawdown.empty else None,
                "current_drawdown": float(drawdown.iloc[-1]) if not drawdown.empty else None,
                "dispersion": float(np.std(member_return_values, ddof=1)) if len(member_return_values) > 1 else None,
                "top_three_concentration": top_three_concentration,
                "average_correlation": risk_structure.get("average_correlation"),
                "first_component_share": risk_structure.get("first_component_share"),
                "effective_risk_bets": risk_structure.get("effective_risk_bets"),
                "diversification_ratio": diversification_ratio,
                "regime": group_regime,
                "members": len(member_returns),
                "total_members": len(list(group.get("members") or [])),
                "history_coverage": len(member_returns) / len(group["members"]) if group.get("members") else None,
                "risk_members": len(eligible),
                "start_at": group_index.index[0],
                "end_at": group_index.index[-1],
            }
        )

        equal_weight = 1.0 / len(member_returns) if member_returns else 0.0
        risk_contribution: dict[str, float] = {}
        common = risk_returns.dropna()
        if len(common) >= _MIN_RISK_OBSERVATIONS and len(common.columns):
            weights = np.repeat(1.0 / len(common.columns), len(common.columns))
            covariance = common.cov().to_numpy(dtype=float) * _ANNUAL_TRADING_DAYS
            portfolio_variance = float(weights @ covariance @ weights)
            if portfolio_variance > 0:
                portfolio_volatility = math.sqrt(portfolio_variance)
                values = weights * (covariance @ weights) / portfolio_volatility
                risk_contribution = dict(zip(common.columns, values))
        for symbol, member_return in member_returns.items():
            regime, regime_intensity = (
                _momentum_regime(risk_all[symbol])
                if symbol in risk_all.columns
                else ("Insufficient history", None)
            )
            risk_value = risk_contribution.get(symbol)
            contributions.append(
                {
                    "group_key": group_key,
                    "group": name,
                    "symbol": symbol,
                    "return": member_return,
                    "contribution": member_return * equal_weight,
                    "return_contribution": member_return * equal_weight,
                    "direction": "Winners" if member_return >= 0 else "Detractors",
                    "risk_contribution": risk_value,
                    "risk_direction": (
                        "Risk amplifiers" if risk_value is not None and risk_value >= 0 else "Diversifiers"
                    ),
                    "regime": regime,
                    "regime_intensity": regime_intensity,
                }
            )

    group_correlation = pd.DataFrame(group_return_series).corr(min_periods=_MIN_RISK_OBSERVATIONS)
    group_correlation = _cluster_correlation(group_correlation)
    ranking_frame = pd.DataFrame(rankings)
    if not ranking_frame.empty:
        ranking_frame = ranking_frame.sort_values("return", ascending=False).reset_index(drop=True)
    return {
        "series": pd.DataFrame(series_records),
        "rankings": ranking_frame,
        "contributions": pd.DataFrame(contributions),
        "correlation": group_correlation,
        "groups": resources,
        "lookback": lookback,
    }


__all__ = [
    "LOOKBACK_OPTIONS",
    "equity_position_analytics",
    "filter_lookback",
    "lookback_start",
    "opportunity_analytics",
]
