"""Price observations attached to calendar events by the background producer."""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
import math

import pandas as pd

from services.market_calendar import NY

REACTION_BASIS = "previous_close_to_next_open"


def fetch_reaction_prices(api, symbols: list[str], *, start: datetime, end: datetime) -> pd.DataFrame:
    """Background-only consolidated closes; never substitute IEX-only quotes."""
    frames = api.get_stock_bars(symbols, start=start, end=end, feed="sip", adjustment="all", timeout_seconds=45)
    parts = [frame.assign(symbol=symbol)[["symbol", "timestamp", "open", "close"]]
             for symbol, frame in frames.items() if not frame.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def observe_reactions(state: dict, prices: pd.DataFrame, *, as_of: datetime,
                      price_version: str, benchmark: str = "SPY") -> dict:
    """Measure the next regular-session open against its preceding close."""
    today = as_of.astimezone(NY).date().isoformat()
    # Historical SIP is available with a 15-minute delay on the existing plan.
    # The opening price is usable before the rest of its daily bar is complete.
    cutoff = as_of - timedelta(minutes=15)
    completed = set()
    opened = set()
    for session in state.get("market_sessions", []):
        try:
            day = date.fromisoformat(session["date"])
            if session.get("close"):
                close = datetime.combine(day, time.fromisoformat(session["close"]), NY)
                if close <= cutoff:
                    completed.add(session["date"])
            elif session["date"] < today:
                completed.add(session["date"])
            if session.get("open"):
                opening = datetime.combine(day, time.fromisoformat(session["open"]), NY)
                if opening <= cutoff:
                    opened.add(session["date"])
            elif session["date"] < today:
                opened.add(session["date"])
        except (KeyError, TypeError, ValueError):
            continue
    observations: dict[str, dict[str, dict[str, float]]] = {}
    if {"symbol", "timestamp", "open", "close"}.issubset(prices.columns):
        frame = prices[["symbol", "timestamp", "open", "close"]].copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        for field in ("open", "close"):
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        frame = frame.dropna(subset=["symbol", "timestamp"]).sort_values("timestamp")
        frame["session"] = frame["timestamp"].dt.tz_convert(NY).dt.strftime("%Y-%m-%d")
        frame = frame[frame["session"].isin(completed | opened)]
        for row in frame.itertuples():
            values = {}
            for field in ("open", "close"):
                value = getattr(row, field)
                if pd.notna(value) and math.isfinite(value) and value > 0:
                    values[field] = float(value)
            if values:
                observations.setdefault(str(row.symbol).upper(), {})[row.session] = values
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
        # opening price versus its preceding close, irrespective of report session.
        end = bisect_right(sessions, event["date"])
        start, label = end - 1, "Next trading day open"
        if start < 0 or end >= len(sessions):
            continue
        first, last = sessions[start], sessions[end]
        if first not in completed or last not in opened:
            continue
        if previous and (previous.get("symbol"), previous.get("start_session"), previous.get("end_session"),
                         previous.get("price_feed"), previous.get("price_adjustment"),
                         previous.get("price_basis")) == (symbol, first, last, "sip", "all", REACTION_BASIS):
            event["market_reaction"] = previous
        values = observations.get(symbol or "", {})
        start_price = values.get(first, {}).get("close")
        end_price = values.get(last, {}).get("open")
        if start_price is None or end_price is None:
            continue
        event["market_reaction"] = {
            "symbol": symbol, "return_pct": (end_price / start_price - 1) * 100,
            "start_session": first, "end_session": last,
            "start_price": start_price, "end_price": end_price,
            "window_label": label, "price_basis": REACTION_BASIS,
            "price_source": "Alpaca consolidated adjusted daily bars",
            "price_feed": "sip", "price_adjustment": "all",
            "price_version": price_version, "observed_at": as_of.astimezone(timezone.utc).isoformat(),
        }
    return state
