"""Price observations attached to calendar events by the background producer."""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
import math

import pandas as pd

from services.market_calendar import NY


def fetch_reaction_prices(api, symbols: list[str], *, start: datetime, end: datetime) -> pd.DataFrame:
    """Background-only consolidated closes; never substitute IEX-only quotes."""
    frames = api.get_stock_bars(symbols, start=start, end=end, feed="sip", adjustment="all", timeout_seconds=45)
    parts = [frame.assign(symbol=symbol)[["symbol", "timestamp", "close"]]
             for symbol, frame in frames.items() if not frame.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def observe_reactions(state: dict, prices: pd.DataFrame, *, as_of: datetime,
                      price_version: str, benchmark: str = "SPY") -> dict:
    """Measure complete regular sessions without inferring a release timestamp."""
    today = as_of.astimezone(NY).date().isoformat()
    # Historical SIP is available with a 15-minute delay on the existing plan.
    # A calendar date alone cannot establish that today's regular session ended.
    cutoff = as_of - timedelta(minutes=15)
    completed = set()
    for session in state.get("market_sessions", []):
        try:
            day = date.fromisoformat(session["date"])
            if session.get("close"):
                close = datetime.combine(day, time.fromisoformat(session["close"]), NY)
                if close <= cutoff:
                    completed.add(session["date"])
            elif session["date"] < today:
                completed.add(session["date"])
        except (KeyError, TypeError, ValueError):
            continue
    observations: dict[str, dict[str, float]] = {}
    if {"symbol", "timestamp", "close"}.issubset(prices.columns):
        frame = prices[["symbol", "timestamp", "close"]].copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna().sort_values("timestamp")
        frame["session"] = frame["timestamp"].dt.tz_convert(NY).dt.strftime("%Y-%m-%d")
        frame = frame[frame["session"].isin(completed) & (frame["close"] > 0)]
        for row in frame.itertuples():
            if math.isfinite(row.close):
                observations.setdefault(str(row.symbol).upper(), {})[row.session] = float(row.close)
    # Prices cannot establish whether a missing date was a holiday or a data gap.
    sessions = sorted({row["date"] for row in state.get("market_sessions", [])})
    for event in state.get("events", []):
        previous = event.pop("market_reaction", None)
        if event.get("cancelled") or event["date"] >= today or not sessions:
            continue
        is_earnings = event.get("type") == "earnings"
        # Price observations do not depend on the separate EPS/history lookup.
        # Preserve the source's expected/reported status; prices cannot confirm it.
        symbol = event.get("ticker") if is_earnings else benchmark
        # One owner-selected convention for every event: the next trading day's
        # close versus its preceding close, irrespective of report session.
        end = bisect_right(sessions, event["date"])
        start, label = end - 1, "Next trading day"
        if start < 0 or end >= len(sessions):
            continue
        first, last = sessions[start], sessions[end]
        if first not in completed or last not in completed:
            continue
        if previous and (previous.get("symbol"), previous.get("start_session"), previous.get("end_session"),
                         previous.get("price_feed"), previous.get("price_adjustment")) == (symbol, first, last, "sip", "all"):
            event["market_reaction"] = previous
        values = observations.get(symbol or "", {})
        if first not in values or last not in values:
            continue
        event["market_reaction"] = {
            "symbol": symbol, "return_pct": (values[last] / values[first] - 1) * 100,
            "start_session": first, "end_session": last,
            "start_price": values[first], "end_price": values[last],
            "window_label": label, "price_source": "Alpaca consolidated adjusted daily closes",
            "price_feed": "sip", "price_adjustment": "all",
            "price_version": price_version, "observed_at": as_of.astimezone(timezone.utc).isoformat(),
        }
    return state
