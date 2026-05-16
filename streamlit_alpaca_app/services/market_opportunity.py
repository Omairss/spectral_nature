from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

MARKET_OPPORTUNITY_HORIZON_SPECS: tuple[dict[str, str], ...] = (
    {"key": "1d", "column": "return_1d_pct", "label": "1 Day"},
    {"key": "7d", "column": "return_7d_pct", "label": "7 Day"},
    {"key": "1m", "column": "return_1m_pct", "label": "1 Month"},
    {"key": "3m", "column": "return_3m_pct", "label": "3 Month"},
    {"key": "1yr", "column": "return_1y_pct", "label": "1 Year"},
    {"key": "5yr", "column": "return_5y_pct", "label": "5 Year"},
)


def _num(value: object, default: float = np.nan) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


def _normalize_symbols(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out = out[out["symbol"].ne("")]
    return out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)


def _normalize_symbol_list(symbols: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in list(symbols or []):
        symbol = str(raw or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _filter_symbols(frame: pd.DataFrame, symbols: list[str] | tuple[str, ...] | set[str] | None) -> pd.DataFrame:
    normalized = set(_normalize_symbol_list(symbols))
    if not normalized:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    source = _normalize_symbols(frame)
    if source.empty:
        return source
    return source[source["symbol"].isin(normalized)].reset_index(drop=True)


def _score_series(values: pd.Series, *, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.dropna().empty:
        return pd.Series(0.0, index=values.index)
    ranked = numeric.rank(pct=True, na_option="bottom")
    if not higher_is_better:
        ranked = 1.0 - ranked
    return ranked.fillna(0.0).astype(float)


def _direction_label(row: pd.Series, horizon_col: str) -> str:
    horizon = _num(row.get(horizon_col))
    roc = _num(row.get("momentum_roc_score"))
    daily = _num(row.get("daily_change_pct"))
    if pd.notna(horizon) and horizon > 0 and pd.notna(roc) and roc > 0:
        return "Up / accelerating"
    if pd.notna(horizon) and horizon > 0:
        return "Up / cooling"
    if pd.notna(horizon) and horizon < 0 and pd.notna(roc) and roc < 0:
        return "Down / worsening"
    if pd.notna(horizon) and horizon < 0:
        return "Down / stabilizing"
    if pd.notna(daily) and daily > 0:
        return "Positive daily move"
    if pd.notna(daily) and daily < 0:
        return "Negative daily move"
    return "Mixed"


def _opportunity_label(row: pd.Series, horizon_col: str) -> str:
    horizon = _num(row.get(horizon_col))
    roc = _num(row.get("momentum_roc_score"))
    trend_gap = _num(row.get("trend_fit_gap"))
    daily = _num(row.get("daily_change_pct"))
    if pd.notna(horizon) and horizon > 0 and pd.notna(roc) and roc > 0:
        return "Upside momentum"
    if pd.notna(horizon) and horizon < 0 and pd.notna(roc) and roc < 0:
        return "Downside pressure"
    if pd.notna(roc) and abs(roc) >= 0.65:
        return "Momentum rotation"
    if pd.notna(trend_gap) and trend_gap <= 0.12:
        return "Consistent trend"
    if pd.notna(daily) and abs(daily) >= 3.0:
        return "Daily dislocation"
    return "Watchlist"


def _format_pct(value: object) -> str:
    parsed = _num(value)
    if pd.isna(parsed):
        return "n/a"
    return f"{parsed:+.1f}%"


def _detail_text(row: pd.Series, horizon_col: str, horizon_label: str) -> str:
    parts = [
        f"1D {_format_pct(row.get('daily_change_pct'))}",
        f"{horizon_label} {_format_pct(row.get(horizon_col))}",
    ]
    roc = _num(row.get("momentum_roc_score"))
    if pd.notna(roc):
        parts.append(f"RoC {roc:+.2f}")
    trend_gap = _num(row.get("trend_fit_gap"))
    if pd.notna(trend_gap):
        parts.append(f"trend gap {trend_gap:.2f}")
    mover_volume = _num(row.get("volume"))
    if pd.notna(mover_volume) and mover_volume > 0:
        parts.append(f"volume {mover_volume:,.0f}")
    return " | ".join(parts)


def build_market_opportunity_feed(
    *,
    movers: pd.DataFrame,
    momentum: pd.DataFrame,
    selected_horizon_col: str = "return_1m_pct",
    selected_horizon_label: str = "1 Month",
    name_map: dict[str, str] | None = None,
    limit: int = 60,
) -> pd.DataFrame:
    """Merge market scanners into one ranked opportunity feed.

    The feed is a compression layer over existing source tables. It does not
    fetch data itself and does not replace the detail views.
    """
    mover_frame = _normalize_symbols(movers)
    momentum_frame = _normalize_symbols(momentum)
    horizon_col = str(selected_horizon_col or "return_1m_pct").strip() or "return_1m_pct"
    horizon_label = str(selected_horizon_label or horizon_col).strip() or horizon_col

    if mover_frame.empty and momentum_frame.empty:
        return pd.DataFrame()

    if not mover_frame.empty:
        mover_frame = mover_frame.rename(
            columns={
                "close": "daily_mover_close",
                "change_pct": "daily_mover_change_pct",
                "price": "daily_mover_price",
                "volume": "daily_mover_volume",
            }
        )
        mover_keep = [
            column
            for column in [
                "symbol",
                "daily_mover_change_pct",
                "daily_mover_close",
                "daily_mover_price",
                "daily_mover_volume",
            ]
            if column in mover_frame.columns
        ]
        mover_frame = mover_frame[mover_keep].copy()

    if momentum_frame.empty:
        combined = mover_frame.copy()
    elif mover_frame.empty:
        combined = momentum_frame.copy()
    else:
        combined = momentum_frame.merge(mover_frame, on="symbol", how="outer")

    if "daily_change_pct" not in combined.columns:
        combined["daily_change_pct"] = np.nan
    if "daily_mover_change_pct" in combined.columns:
        daily_change = pd.to_numeric(combined["daily_change_pct"], errors="coerce")
        mover_change = pd.to_numeric(combined["daily_mover_change_pct"], errors="coerce")
        combined["daily_change_pct"] = daily_change.fillna(mover_change)
    if "close" not in combined.columns:
        combined["close"] = combined.get("daily_mover_close", combined.get("daily_mover_price", np.nan))
    elif "daily_mover_close" in combined.columns:
        combined["close"] = pd.to_numeric(combined["close"], errors="coerce").fillna(
            pd.to_numeric(combined["daily_mover_close"], errors="coerce")
        )
    if "volume" not in combined.columns:
        combined["volume"] = combined.get("daily_mover_volume", np.nan)
    elif "daily_mover_volume" in combined.columns:
        existing_volume = pd.to_numeric(combined["volume"], errors="coerce")
        mover_volume = pd.to_numeric(combined["daily_mover_volume"], errors="coerce")
        combined["volume"] = mover_volume.fillna(existing_volume)
    if horizon_col not in combined.columns:
        combined[horizon_col] = np.nan

    for column in [
        "daily_change_pct",
        horizon_col,
        "momentum_score",
        "momentum_roc_score",
        "trend_fit_gap",
        "return_1w_pct",
        "return_3m_pct",
        "close",
    ]:
        if column not in combined.columns:
            combined[column] = np.nan

    combined["_horizon_abs_rank"] = _score_series(pd.to_numeric(combined[horizon_col], errors="coerce").abs())
    combined["_roc_abs_rank"] = _score_series(pd.to_numeric(combined["momentum_roc_score"], errors="coerce").abs())
    combined["_daily_abs_rank"] = _score_series(pd.to_numeric(combined["daily_change_pct"], errors="coerce").abs())
    combined["_trend_rank"] = _score_series(combined["trend_fit_gap"], higher_is_better=False)
    combined["opportunity_score"] = (
        combined["_horizon_abs_rank"] * 42.0
        + combined["_roc_abs_rank"] * 26.0
        + combined["_daily_abs_rank"] * 20.0
        + combined["_trend_rank"] * 12.0
    ).round(1)

    names = name_map or {}
    combined["company_name"] = [
        str(names.get(str(symbol).upper().strip()) or "").strip()
        for symbol in combined["symbol"]
    ]
    combined["opportunity"] = combined.apply(_opportunity_label, axis=1, horizon_col=horizon_col)
    combined["direction"] = combined.apply(_direction_label, axis=1, horizon_col=horizon_col)
    combined["details"] = combined.apply(_detail_text, axis=1, horizon_col=horizon_col, horizon_label=horizon_label)

    display_columns = list(dict.fromkeys([
        "symbol",
        "company_name",
        "opportunity",
        "direction",
        "opportunity_score",
        "sparkline_3m",
        "close",
        "volume",
        "daily_change_pct",
        horizon_col,
        "return_1w_pct",
        "return_3m_pct",
        "momentum_score",
        "momentum_roc_score",
        "trend_fit_gap",
        "details",
    ]))
    for column in display_columns:
        if column not in combined.columns:
            combined[column] = np.nan

    out = combined[display_columns].copy()
    out = out.sort_values(["opportunity_score", horizon_col], ascending=[False, False], na_position="last")
    out = out.head(max(int(limit), 1)).reset_index(drop=True)
    return out


def build_materialized_market_opportunity_feeds(
    *,
    movers: pd.DataFrame,
    momentum: pd.DataFrame,
    name_map: dict[str, str] | None = None,
    focus_symbol_map: dict[str, list[str]] | None = None,
    horizon_specs: tuple[dict[str, str], ...] = MARKET_OPPORTUNITY_HORIZON_SPECS,
    asof_time_utc: object = "",
    run_id: str = "",
    limit: int = 80,
) -> pd.DataFrame:
    """Build all fixed Market Opportunity feed variants for job materialization."""
    focuses = dict(focus_symbol_map or {})
    if "All Market" not in focuses:
        focuses = {"All Market": [], **focuses}

    rows: list[pd.DataFrame] = []
    for business_filter, symbols in focuses.items():
        focus_label = str(business_filter or "All Market").strip() or "All Market"
        normalized_symbols = _normalize_symbol_list(symbols)
        focus_movers = _filter_symbols(movers, normalized_symbols)
        focus_momentum = _filter_symbols(momentum, normalized_symbols)
        if focus_movers.empty and focus_momentum.empty:
            continue

        for spec in horizon_specs:
            horizon_key = str(spec.get("key") or spec.get("column") or "").strip()
            horizon_col = str(spec.get("column") or "return_1m_pct").strip() or "return_1m_pct"
            horizon_label = str(spec.get("label") or horizon_col).strip() or horizon_col
            feed = build_market_opportunity_feed(
                movers=focus_movers,
                momentum=focus_momentum,
                selected_horizon_col=horizon_col,
                selected_horizon_label=horizon_label,
                name_map=name_map,
                limit=limit,
            )
            if feed.empty:
                continue
            out = feed.copy()
            out.insert(0, "rank", range(1, len(out) + 1))
            out.insert(0, "selected_horizon_label", horizon_label)
            out.insert(0, "selected_horizon_col", horizon_col)
            out.insert(0, "horizon_key", horizon_key)
            out.insert(0, "business_filter", focus_label)
            out["asof_time_utc"] = pd.Timestamp(asof_time_utc).isoformat() if str(asof_time_utc or "").strip() else ""
            out["run_id"] = str(run_id or "")
            rows.append(out)

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def select_market_opportunity_feed(
    frame: pd.DataFrame,
    *,
    business_filter: str = "All Market",
    selected_horizon_col: str = "return_1m_pct",
    symbols: list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 80,
) -> pd.DataFrame:
    """Select one materialized Market Opportunity feed variant for presentation."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()

    out = frame.copy()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
        out = out[out["symbol"].ne("")]

    horizon_col = str(selected_horizon_col or "return_1m_pct").strip() or "return_1m_pct"
    if "selected_horizon_col" in out.columns:
        horizon_mask = out["selected_horizon_col"].astype(str).str.strip().eq(horizon_col)
        if horizon_mask.any():
            out = out[horizon_mask].copy()

    focus_label = str(business_filter or "All Market").strip() or "All Market"
    if "business_filter" in out.columns:
        focus_values = out["business_filter"].astype(str).str.strip()
        focus_mask = focus_values.str.casefold().eq(focus_label.casefold())
        if focus_mask.any():
            out = out[focus_mask].copy()
        elif _normalize_symbol_list(symbols):
            all_market_mask = focus_values.str.casefold().eq("all market")
            if all_market_mask.any():
                out = out[all_market_mask].copy()

    normalized_symbols = set(_normalize_symbol_list(symbols))
    if normalized_symbols and "symbol" in out.columns:
        out = out[out["symbol"].isin(normalized_symbols)].copy()

    if out.empty:
        return pd.DataFrame()

    if horizon_col not in out.columns:
        out[horizon_col] = np.nan
    if "rank" in out.columns:
        out["_sort_rank"] = pd.to_numeric(out["rank"], errors="coerce")
        out = out.sort_values(["_sort_rank", "opportunity_score"], ascending=[True, False], na_position="last")
        out = out.drop(columns=["_sort_rank"])
    elif "opportunity_score" in out.columns:
        out = out.sort_values("opportunity_score", ascending=False, na_position="last")

    return out.head(max(int(limit), 1)).reset_index(drop=True)


__all__ = [
    "MARKET_OPPORTUNITY_HORIZON_SPECS",
    "build_market_opportunity_feed",
    "build_materialized_market_opportunity_feeds",
    "select_market_opportunity_feed",
]
