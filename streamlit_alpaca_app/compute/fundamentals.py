from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import re

import pandas as pd


INCOME_METRICS = {
    "Total Revenue": ["Revenue", "Total Revenue", "Sales"],
    "Operating Income": ["Operating Income (Loss)", "Operating Income", "Operating Income (EBIT)", "EBIT"],
    "Net Income": ["Net Income", "Net Income (Common)"],
}
BALANCE_METRICS = {
    "Total Assets": ["Total Assets"],
    "Total Liabilities": ["Total Liabilities"],
    "Stockholders Equity": ["Total Equity", "Stockholders Equity"],
}
CASHFLOW_METRICS = {
    "Operating Cash Flow": ["Net Cash from Operating Activities", "Operating Cash Flow"],
    "Capital Expenditure": ["Change in Fixed Assets & Intangibles", "Capital Expenditures", "Capital Expenditure"],
}
SHARE_COUNT_METRICS = {
    "Shares Diluted": ["Shares (Diluted)", "Shares Diluted"],
    "Shares Basic": ["Shares (Basic)", "Shares Basic"],
}

STATEMENT_FILES = {
    "income": "us-income-quarterly.csv",
    "balance": "us-balance-quarterly.csv",
    "cashflow": "us-cashflow-quarterly.csv",
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _ticker_aliases(ticker: str) -> set[str]:
    base = str(ticker or "").upper().strip()
    aliases = {base}
    if base == "GOOGL":
        aliases.add("GOOG")
    if base == "GOOG":
        aliases.add("GOOGL")
    if base in {"META", "FB"}:
        aliases.update({"META", "FB"})
    if base in {"BRK.B", "BRK-B", "BRKB"}:
        aliases.update({"BRK.B", "BRK-B", "BRKB"})
    return {_normalized(alias) for alias in aliases}


def _candidate_data_dirs(data_dir: str | None = None) -> list[Path]:
    roots: list[Path] = []

    explicit_dir = str(data_dir or "").strip()
    if explicit_dir:
        roots.append(Path(explicit_dir).expanduser())

    env_dir = os.getenv("SIMFIN_DATA_DIR", "").strip()
    if env_dir:
        roots.append(Path(env_dir).expanduser())

    refresh_dir = os.getenv("SIMFIN_REFRESH_DATA_DIR", "").strip()
    if refresh_dir:
        roots.append(Path(refresh_dir).expanduser())

    here = Path(__file__).resolve()
    app_root = here.parents[1]
    roots.append(app_root / "cache" / "data" / "simfin_refresh")
    for parent in here.parents:
        roots.append(parent / "data" / "stock_fundamental")

    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


@lru_cache(maxsize=None)
def _statement_path(statement: str, data_dir: str = "") -> Path:
    filename = STATEMENT_FILES[statement]
    for root in _candidate_data_dirs(data_dir or None):
        path = root / filename
        if path.exists():
            return path
    searched = ", ".join(str(path) for path in _candidate_data_dirs(data_dir or None))
    raise FileNotFoundError(f"Quarterly fundamentals dataset '{filename}' not found. Checked: {searched}")


@lru_cache(maxsize=None)
def _load_statement(statement: str, data_dir: str = "") -> pd.DataFrame:
    return pd.read_csv(_statement_path(statement, data_dir), sep=";", low_memory=False)


def _resolve_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized_map = {_normalized(column): column for column in columns}
    for candidate in candidates:
        key = _normalized(candidate)
        if key in normalized_map:
            return normalized_map[key]
    for candidate in candidates:
        key = _normalized(candidate)
        for normalized_column, original_column in normalized_map.items():
            if key in normalized_column:
                return original_column
    return None


def _quarterly_rows(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if frame.empty or "Ticker" not in frame.columns:
        return pd.DataFrame()

    out = frame[frame["Ticker"].astype(str).map(_normalized).isin(_ticker_aliases(ticker))].copy()
    if out.empty:
        return out

    out["Report Date"] = pd.to_datetime(out.get("Report Date"), errors="coerce")
    out = out[out["Fiscal Period"].astype(str).isin(["Q1", "Q2", "Q3", "Q4"])]
    out = out.dropna(subset=["Report Date"])
    out["Fiscal Year"] = pd.to_numeric(out.get("Fiscal Year"), errors="coerce")
    out = out.sort_values("Report Date").drop_duplicates(subset=["Fiscal Year", "Fiscal Period"], keep="last")
    out["year_quarter"] = out["Fiscal Year"].fillna(0).astype(int).astype(str) + out["Fiscal Period"].astype(str)
    return out


def _build_metric_frame(rows: pd.DataFrame, ticker: str, statement: str, metric_name: str, values: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "report_date": rows["Report Date"],
            "year_quarter": rows["year_quarter"],
            "metric": metric_name,
            "value": pd.to_numeric(values, errors="coerce"),
            "ticker": ticker.upper(),
            "statement": statement,
        }
    )
    return frame.dropna(subset=["report_date", "value"])


def _statement_to_long(frame: pd.DataFrame, ticker: str, statement: str, metric_map: dict[str, list[str]]) -> pd.DataFrame:
    rows = _quarterly_rows(frame, ticker)
    if rows.empty:
        return pd.DataFrame()

    parts: list[pd.DataFrame] = []
    for metric_name, candidates in metric_map.items():
        column = _resolve_column(list(rows.columns), candidates)
        if column is None:
            continue
        parts.append(_build_metric_frame(rows, ticker, statement, metric_name, rows[column]))

    if statement == "cashflow":
        cfo_column = _resolve_column(list(rows.columns), CASHFLOW_METRICS["Operating Cash Flow"])
        capex_column = _resolve_column(list(rows.columns), CASHFLOW_METRICS["Capital Expenditure"])
        if cfo_column and capex_column:
            free_cash_flow = pd.to_numeric(rows[cfo_column], errors="coerce") + pd.to_numeric(rows[capex_column], errors="coerce")
            parts.append(_build_metric_frame(rows, ticker, statement, "Free Cash Flow", free_cash_flow))

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["report_date", "metric"]).drop_duplicates(subset=["report_date", "metric"], keep="last")
    return out.reset_index(drop=True)


def load_quarterly_fundamentals(ticker: str, *, data_dir: str | None = None) -> dict[str, pd.DataFrame]:
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}

    data_dir_key = str(data_dir or "").strip()
    income = _statement_to_long(_load_statement("income", data_dir_key), symbol, "income", INCOME_METRICS)
    balance = _statement_to_long(_load_statement("balance", data_dir_key), symbol, "balance", BALANCE_METRICS)
    cashflow = _statement_to_long(_load_statement("cashflow", data_dir_key), symbol, "cashflow", CASHFLOW_METRICS)
    return {"income": income, "balance": balance, "cashflow": cashflow}


def share_count_asof(
    ticker: str,
    *,
    asof_time_utc: object | None = None,
    diluted_preferred: bool = True,
    data_dir: str | None = None,
) -> tuple[float | None, pd.Timestamp | None, str | None]:
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return None, None, None
    return share_counts_asof(
        [symbol],
        asof_time_utc=asof_time_utc,
        diluted_preferred=diluted_preferred,
        data_dir=data_dir,
    ).get(symbol, (None, None, None))


def _best_share_count_from_rows(
    rows: pd.DataFrame,
    *,
    asof_cutoff: pd.Timestamp | None,
    metric_order: list[str],
) -> tuple[float | None, pd.Timestamp | None, str | None]:
    if rows.empty:
        return None, None, None
    scoped = rows.copy()
    if asof_cutoff is not None:
        scoped = scoped[scoped["Report Date"] <= asof_cutoff].copy()
        if scoped.empty:
            return None, None, None

    for metric_name in metric_order:
        column = _resolve_column(list(scoped.columns), SHARE_COUNT_METRICS[metric_name])
        if column is None:
            continue

        frame = pd.DataFrame(
            {
                "report_date": pd.to_datetime(scoped["Report Date"], errors="coerce"),
                "value": pd.to_numeric(scoped[column], errors="coerce"),
            }
        ).dropna(subset=["report_date", "value"])
        if frame.empty:
            continue

        latest = frame.sort_values("report_date").iloc[-1]
        return float(latest["value"]), pd.Timestamp(latest["report_date"]), metric_name
    return None, None, None


def share_counts_asof(
    tickers: list[str],
    *,
    asof_time_utc: object | None = None,
    diluted_preferred: bool = True,
    data_dir: str | None = None,
) -> dict[str, tuple[float | None, pd.Timestamp | None, str | None]]:
    symbols = [str(ticker or "").upper().strip() for ticker in list(tickers or []) if str(ticker or "").strip()]
    symbols = list(dict.fromkeys(symbols))
    out: dict[str, tuple[float | None, pd.Timestamp | None, str | None]] = {
        symbol: (None, None, None) for symbol in symbols
    }
    if not symbols:
        return out

    metric_order = ["Shares Diluted", "Shares Basic"] if diluted_preferred else ["Shares Basic", "Shares Diluted"]
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    asof_cutoff = asof_ts.tz_localize(None) if pd.notna(asof_ts) else None
    alias_to_symbols: dict[str, list[str]] = {}
    for symbol in symbols:
        for alias in _ticker_aliases(symbol):
            alias_to_symbols.setdefault(alias, []).append(symbol)
    alias_set = set(alias_to_symbols)

    data_dir_key = str(data_dir or "").strip()
    for statement in ["income", "balance", "cashflow"]:
        try:
            statement_frame = _load_statement(statement, data_dir_key)
        except Exception:
            statement_frame = pd.DataFrame()
        if statement_frame.empty or "Ticker" not in statement_frame.columns:
            continue
        rows = statement_frame.copy()
        rows["_ticker_norm"] = rows["Ticker"].astype(str).map(_normalized)
        rows = rows[rows["_ticker_norm"].isin(alias_set)].copy()
        if rows.empty:
            continue
        rows["Report Date"] = pd.to_datetime(rows.get("Report Date"), errors="coerce")
        rows = rows[rows["Fiscal Period"].astype(str).isin(["Q1", "Q2", "Q3", "Q4"])]
        rows = rows.dropna(subset=["Report Date"])
        if rows.empty:
            continue
        rows["Fiscal Year"] = pd.to_numeric(rows.get("Fiscal Year"), errors="coerce")
        rows = rows.sort_values("Report Date").drop_duplicates(
            subset=["_ticker_norm", "Fiscal Year", "Fiscal Period"],
            keep="last",
        )
        for alias, alias_rows in rows.groupby("_ticker_norm", sort=False):
            candidate = _best_share_count_from_rows(alias_rows, asof_cutoff=asof_cutoff, metric_order=metric_order)
            value, report_date, metric_name = candidate
            if value is None or report_date is None or metric_name is None:
                continue
            for symbol in alias_to_symbols.get(str(alias), []):
                best_value, best_date, best_metric = out.get(symbol, (None, None, None))
                if best_date is None or report_date > best_date or (
                    report_date == best_date and best_metric == "Shares Basic" and metric_name == "Shares Diluted"
                ):
                    out[symbol] = (value, report_date, metric_name)
    return out


def latest_share_count(
    ticker: str,
    *,
    diluted_preferred: bool = True,
    data_dir: str | None = None,
) -> tuple[float | None, pd.Timestamp | None, str | None]:
    return share_count_asof(ticker, diluted_preferred=diluted_preferred, data_dir=data_dir)


__all__ = [
    "BALANCE_METRICS",
    "CASHFLOW_METRICS",
    "INCOME_METRICS",
    "SHARE_COUNT_METRICS",
    "STATEMENT_FILES",
    "latest_share_count",
    "load_quarterly_fundamentals",
    "share_count_asof",
    "share_counts_asof",
]
