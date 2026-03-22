from __future__ import annotations

import csv
from io import StringIO
import re
from typing import Any

import pandas as pd
import requests

from .alpaca_api import AlpacaAPI
from .market import BUSINESS_FOCUS_UNIVERSES


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
LISTING_COLUMNS = [
    "symbol",
    "exchange",
    "security_name",
    "is_etf",
    "is_test_issue",
    "source_file",
]
UNIVERSE_COLUMNS = LISTING_COLUMNS + [
    "close",
    "prev_close",
    "change_pct",
    "volume",
    "dollar_volume",
    "liquidity_rank",
    "selection_reason",
    "rank",
]
NON_COMMON_SECURITY_TOKENS = (
    " warrant",
    " warrants",
    " right",
    " rights",
    " unit",
    " units",
    " preferred",
    " depositary",
    " note",
    " notes",
)
SUPPORTED_EQUITY_SYMBOL_RE = re.compile(r"[A-Z]{1,6}(?:\.[A-Z])?")


def _parse_pipe_rows(raw_text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(StringIO(raw_text), delimiter="|"))
    if rows and any("File Creation Time" in str(value or "") for value in rows[-1].values()):
        rows = rows[:-1]
    return rows


def _normalize_symbol(symbol: object) -> str:
    return AlpacaAPI._normalize_symbol(str(symbol or ""))


def _is_supported_equity_symbol(symbol: object) -> bool:
    normalized = _normalize_symbol(symbol)
    return bool(normalized and SUPPORTED_EQUITY_SYMBOL_RE.fullmatch(normalized))


def _curated_equity_symbols() -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for symbols in BUSINESS_FOCUS_UNIVERSES.values():
        for symbol in symbols:
            normalized = _normalize_symbol(symbol)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _empty_listings() -> pd.DataFrame:
    return pd.DataFrame(columns=LISTING_COLUMNS)


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=UNIVERSE_COLUMNS)


def load_us_equity_listings(
    *,
    include_etfs: bool = False,
    include_non_common: bool = False,
    timeout: int = 30,
) -> pd.DataFrame:
    try:
        nasdaq_text = requests.get(NASDAQ_LISTED_URL, timeout=timeout).text
        other_text = requests.get(OTHER_LISTED_URL, timeout=timeout).text
    except Exception:
        return _empty_listings()

    rows: list[dict[str, object]] = []
    for row in _parse_pipe_rows(nasdaq_text):
        symbol = _normalize_symbol(row.get("Symbol"))
        if not _is_supported_equity_symbol(symbol):
            continue
        rows.append(
            {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "security_name": str(row.get("Security Name") or "").strip(),
                "is_etf": str(row.get("ETF") or "").strip().upper() == "Y",
                "is_test_issue": str(row.get("Test Issue") or "").strip().upper() == "Y",
                "source_file": "nasdaqlisted",
            }
        )

    for row in _parse_pipe_rows(other_text):
        if str(row.get("Exchange") or "").strip().upper() != "N":
            continue
        symbol = _normalize_symbol(row.get("ACT Symbol"))
        if not _is_supported_equity_symbol(symbol):
            continue
        rows.append(
            {
                "symbol": symbol,
                "exchange": "NYSE",
                "security_name": str(row.get("Security Name") or "").strip(),
                "is_etf": str(row.get("ETF") or "").strip().upper() == "Y",
                "is_test_issue": str(row.get("Test Issue") or "").strip().upper() == "Y",
                "source_file": "otherlisted",
            }
        )

    listings = pd.DataFrame(rows)
    if listings.empty:
        return _empty_listings()

    listings["security_name"] = listings["security_name"].astype(str)
    listings = listings[~listings["is_test_issue"]].copy()
    if not include_etfs:
        listings = listings[~listings["is_etf"]].copy()
    if not include_non_common:
        name_blob = listings["security_name"].str.lower()
        mask = pd.Series(False, index=listings.index)
        for token in NON_COMMON_SECURITY_TOKENS:
            mask = mask | name_blob.str.contains(token, regex=False)
        listings = listings[~mask].copy()
    listings = listings.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return listings[LISTING_COLUMNS]


def build_liquidity_ranked_equity_universe(
    api: Any,
    *,
    target_size: int = 1000,
    include_etfs: bool = False,
    include_non_common: bool = False,
    min_price: float = 5.0,
    min_volume: float = 100_000.0,
    min_dollar_volume: float = 5_000_000.0,
    feed: str = "iex",
    pinned_symbols: list[str] | None = None,
    listings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    base = listings.copy() if listings is not None else load_us_equity_listings(
        include_etfs=include_etfs,
        include_non_common=include_non_common,
    )
    if base.empty:
        return _empty_universe()

    base["symbol"] = base["symbol"].map(_normalize_symbol)
    base = base[base["symbol"].map(_is_supported_equity_symbol)].copy()
    base = base.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    if base.empty:
        return _empty_universe()

    snapshots = api.get_snapshots(base["symbol"].tolist(), feed=feed)
    rows: list[dict[str, object]] = []
    for _, row in base.iterrows():
        symbol = str(row.get("symbol") or "").strip().upper()
        snapshot = snapshots.get(symbol, {}) if isinstance(snapshots, dict) else {}
        daily = (snapshot or {}).get("dailyBar") or {}
        previous = (snapshot or {}).get("prevDailyBar") or {}
        latest_trade = (snapshot or {}).get("latestTrade") or {}
        close = pd.to_numeric(daily.get("c", latest_trade.get("p")), errors="coerce")
        prev_close = pd.to_numeric(previous.get("c"), errors="coerce")
        volume = pd.to_numeric(daily.get("v"), errors="coerce")
        dollar_volume = close * volume if pd.notna(close) and pd.notna(volume) else pd.NA
        change_pct = pd.NA
        if pd.notna(close) and pd.notna(prev_close) and float(prev_close) != 0.0:
            change_pct = ((float(close) / float(prev_close)) - 1.0) * 100.0
        rows.append(
            {
                "symbol": symbol,
                "exchange": row.get("exchange"),
                "security_name": row.get("security_name"),
                "is_etf": bool(row.get("is_etf")),
                "is_test_issue": bool(row.get("is_test_issue")),
                "source_file": row.get("source_file"),
                "close": close,
                "prev_close": prev_close,
                "change_pct": change_pct,
                "volume": volume,
                "dollar_volume": dollar_volume,
            }
        )

    ranked = pd.DataFrame(rows)
    if ranked.empty:
        return _empty_universe()

    for column in ["close", "prev_close", "change_pct", "volume", "dollar_volume"]:
        ranked[column] = pd.to_numeric(ranked[column], errors="coerce")
    ranked["selection_reason"] = "liquidity"

    liquid = ranked[
        (ranked["close"] >= float(min_price))
        & (ranked["volume"] >= float(min_volume))
        & (ranked["dollar_volume"] >= float(min_dollar_volume))
    ].copy()
    if liquid.empty:
        liquid = ranked.copy()

    liquid = liquid.sort_values(
        ["dollar_volume", "volume", "close", "symbol"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)
    liquid["liquidity_rank"] = range(1, len(liquid) + 1)
    ranked = ranked.merge(liquid[["symbol", "liquidity_rank"]], on="symbol", how="left")
    ranked = ranked.sort_values(
        ["dollar_volume", "volume", "close", "symbol"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)

    pinned = [_normalize_symbol(symbol) for symbol in (pinned_symbols or _curated_equity_symbols()) if _normalize_symbol(symbol)]
    pinned_frame = ranked[ranked["symbol"].isin(pinned)].copy()
    if not pinned_frame.empty:
        pinned_frame["selection_reason"] = "pinned_curated"
        pinned_frame = pinned_frame.sort_values(
            ["dollar_volume", "volume", "close", "symbol"],
            ascending=[False, False, False, True],
            na_position="last",
        )
        pinned_frame["liquidity_rank"] = pd.to_numeric(pinned_frame["liquidity_rank"], errors="coerce")

    missing_pinned = [symbol for symbol in pinned if symbol not in set(pinned_frame.get("symbol", pd.Series(dtype=str)).tolist())]
    if missing_pinned:
        fallback = base[base["symbol"].isin(missing_pinned)].copy()
        if not fallback.empty:
            fallback["close"] = pd.NA
            fallback["prev_close"] = pd.NA
            fallback["change_pct"] = pd.NA
            fallback["volume"] = pd.NA
            fallback["dollar_volume"] = pd.NA
            fallback["liquidity_rank"] = pd.NA
            fallback["selection_reason"] = "pinned_curated"
            pinned_frame = pd.concat([pinned_frame, fallback], ignore_index=True, sort=False)

    remaining = liquid[~liquid["symbol"].isin(set(pinned))].copy()
    out = pd.concat([pinned_frame, remaining], ignore_index=True, sort=False)
    out = out.drop_duplicates(subset=["symbol"], keep="first")
    if len(out) < max(int(target_size), 1):
        fill = ranked[~ranked["symbol"].isin(set(out["symbol"].tolist()))].copy()
        if not fill.empty:
            fill["selection_reason"] = "liquidity_fallback"
            out = pd.concat([out, fill], ignore_index=True, sort=False).drop_duplicates(subset=["symbol"], keep="first")
    out = out.head(max(int(target_size), len(pinned_frame), 1)).reset_index(drop=True)
    if out.empty:
        return _empty_universe()

    out["rank"] = range(1, len(out) + 1)
    keep = [
        "symbol",
        "exchange",
        "security_name",
        "is_etf",
        "source_file",
        "close",
        "prev_close",
        "change_pct",
        "volume",
        "dollar_volume",
        "liquidity_rank",
        "selection_reason",
        "rank",
    ]
    for column in keep:
        if column not in out.columns:
            out[column] = pd.NA
    return out[keep]
