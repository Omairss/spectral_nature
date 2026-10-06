from __future__ import annotations

from typing import Any

import pandas as pd

from compute.analytics import performance_table
from data_access.contracts import frame_to_records, to_jsonable
from data_access.query_service import QueryService
from services import robinhood_main, robinhood_main_analytics
from services.auth_service import UserContext


PERIODS = frozenset({"1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"})
SUMMARY_FIELDS = (
    "cash", "day_pl", "equity", "status", "buying_power", "net_exposure", "account_count",
    "daytrade_count", "portfolio_value", "gross_long_exposure", "total_unrealized_pl",
    "gross_short_exposure", "equity_position_count", "option_position_count", "futures_position_count",
)
HOLDING_FIELDS = (
    "asset_class", "account", "symbol", "description", "strategy", "direction", "qty", "quantity",
    "available_quantity", "avg_entry_price", "average_open_price", "current_price", "market_value",
    "daily_pl", "unrealized_pl", "realized_pl", "unrealized_plpc", "change_today", "expiration",
    "expirations", "multiplier", "implied_volatility", "open_interest", "volume", "delta", "gamma",
    "theta", "vega", "rho",
)


def _frame(value: object) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(list(value))
    return pd.DataFrame()


def _records(frame: pd.DataFrame, fields: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    selected = frame
    if fields is not None:
        selected = frame[[field for field in fields if field in frame.columns]].copy()
    return frame_to_records(selected)


def _summary(value: object) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    return {field: to_jsonable(raw.get(field)) for field in SUMMARY_FIELDS if field in raw}


def _scale_legacy_view(
    summary: dict[str, Any],
    positions: pd.DataFrame,
    timeseries: pd.DataFrame,
    share: float,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    scale = min(max(float(share), 0.0), 1.0)
    dollar_fields = {
        "cash", "day_pl", "equity", "buying_power", "net_exposure", "portfolio_value",
        "gross_long_exposure", "total_unrealized_pl", "gross_short_exposure",
    }
    scaled_summary = dict(summary)
    for field in dollar_fields:
        try:
            if scaled_summary.get(field) is not None:
                scaled_summary[field] = float(scaled_summary[field]) * scale
        except (TypeError, ValueError):
            pass
    scaled_positions = positions.copy()
    for field in ("effective_qty", "qty", "market_value", "unrealized_pl", "cost_basis"):
        if field in scaled_positions.columns:
            scaled_positions[field] = pd.to_numeric(scaled_positions[field], errors="coerce") * scale
    scaled_timeseries = timeseries.copy()
    if "portfolio" in scaled_timeseries.columns:
        scaled_timeseries["portfolio"] = pd.to_numeric(scaled_timeseries["portfolio"], errors="coerce") * scale
    return scaled_summary, scaled_positions, scaled_timeseries


def _performance(raw: pd.DataFrame) -> dict[str, Any]:
    if raw.empty or "timestamp" not in raw.columns or "portfolio" not in raw.columns:
        return {"metrics": {}, "table": []}
    frame = raw.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["portfolio"] = pd.to_numeric(frame["portfolio"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "portfolio"]).sort_values("timestamp")
    if frame.empty:
        return {"metrics": {}, "table": []}
    first_value = float(frame["portfolio"].iloc[0])
    last_value = float(frame["portfolio"].iloc[-1])
    drawdown = frame["portfolio"] / frame["portfolio"].cummax() - 1.0
    daily = frame.set_index("timestamp")["portfolio"].resample("1D").last().dropna().pct_change().dropna()
    volatility = float(daily.std() * (252.0 ** 0.5)) if len(daily) > 1 else 0.0
    return {
        "metrics": {
            "start_value": first_value,
            "current_value": last_value,
            "change_value": last_value - first_value,
            "return": (last_value / first_value - 1.0) if first_value else None,
            "max_drawdown": float(drawdown.min()) if not drawdown.empty else None,
            "annualized_volatility": volatility,
        },
        "table": frame_to_records(performance_table(frame)),
    }


def build_portfolio_view(
    context: UserContext,
    *,
    period: str = "1Y",
    service: QueryService | None = None,
) -> dict[str, Any]:
    selected_period = str(period or "1Y").strip().upper()
    if selected_period not in PERIODS:
        raise ValueError("Portfolio period is invalid.")
    resolved_service = service or QueryService.from_environment()
    snapshot: dict[str, Any] | None = None
    try:
        snapshot = robinhood_main.portfolio_snapshot(
            user_id=context.user_id,
            portfolio_id=context.portfolio_id,
        )
    except robinhood_main.RobinhoodMainError:
        snapshot = None

    if snapshot is not None:
        details = dict(snapshot.get("details") or {})
        holdings = _frame(details.get("holdings"))
        if holdings.empty:
            holdings = _frame(snapshot.get("positions"))
            if not holdings.empty and "asset_class" not in holdings.columns:
                holdings["asset_class"] = "equity"
        timeseries = robinhood_main_analytics.filter_lookback(_frame(snapshot.get("timeseries")), selected_period)
        equities = holdings[holdings.get("asset_class", pd.Series(index=holdings.index, dtype=str)).astype(str).eq("equity")].copy()
        equity_analytics: dict[str, Any] = {"positions": [], "correlation": [], "components": [], "stats": {}}
        if not equities.empty:
            analytics = robinhood_main_analytics.equity_position_analytics(
                portfolio_id=context.portfolio_id,
                environment=robinhood_main.environment_name(),
                holdings=equities,
                lookback=selected_period,
            )
            correlation = analytics.get("correlation", pd.DataFrame())
            equity_analytics = {
                "positions": _records(analytics.get("positions", pd.DataFrame())),
                "correlation": {
                    "labels": list(correlation.columns) if isinstance(correlation, pd.DataFrame) else [],
                    "values": to_jsonable(correlation.to_numpy()) if isinstance(correlation, pd.DataFrame) else [],
                },
                "components": _records(analytics.get("components", pd.DataFrame())),
                "stats": to_jsonable(dict(analytics.get("stats") or {})),
            }
        return {
            "status": "available",
            "provider": "robinhood_main",
            "period": selected_period,
            "portfolio": {"id": context.portfolio_id, "name": context.portfolio_name, "share_fraction": context.share_fraction},
            "summary": _summary(snapshot.get("account_summary")),
            "holdings": _records(holdings, HOLDING_FIELDS),
            "timeseries": _records(timeseries),
            "equity_analytics": equity_analytics,
            "performance": _performance(timeseries),
            "account_performance": to_jsonable(list(details.get("performance_accounts") or [])),
            "opportunity_sets": to_jsonable(list(details.get("opportunity_sets") or [])),
            "refresh": to_jsonable(dict(details.get("refresh") or {})),
            "collector_errors": to_jsonable(dict(details.get("collector_errors") or {})),
            "capabilities": to_jsonable(dict(details.get("capabilities") or {})),
            "can_manage": context.is_admin,
            "execution_enabled": bool(context.is_admin and robinhood_main.execution_enabled()),
            "as_of": str(snapshot.get("source_updated_at") or ""),
        }

    account_resolved = resolved_service.data_access.resolve_account(force_refresh=False)
    positions_resolved = resolved_service.data_access.resolve_positions(force_refresh=False)
    timeseries_resolved = resolved_service.data_access.resolve_portfolio_timeseries(selected_period, force_refresh=False)
    summary = _summary(account_resolved.payload)
    positions = _frame(positions_resolved.payload)
    if not positions.empty and "asset_class" not in positions.columns:
        positions["asset_class"] = "equity"
    timeseries = _frame(timeseries_resolved.payload)
    if not context.can_view_full_portfolio:
        summary, positions, timeseries = _scale_legacy_view(summary, positions, timeseries, context.share_fraction)
    return {
        "status": "available" if summary or not positions.empty or not timeseries.empty else "unavailable",
        "provider": "alpaca",
        "period": selected_period,
        "portfolio": {"id": context.portfolio_id, "name": context.portfolio_name, "share_fraction": context.share_fraction},
        "summary": summary,
        "holdings": _records(positions, HOLDING_FIELDS),
        "timeseries": _records(timeseries),
        "equity_analytics": {"positions": [], "correlation": {"labels": [], "values": []}, "components": [], "stats": {}},
        "performance": _performance(timeseries),
        "account_performance": [],
        "opportunity_sets": [],
        "refresh": {},
        "collector_errors": {},
        "capabilities": {},
        "can_manage": context.is_admin,
        "execution_enabled": False,
        "as_of": "",
    }


__all__ = ["PERIODS", "build_portfolio_view"]
