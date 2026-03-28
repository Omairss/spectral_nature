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

    here = Path(__file__).resolve()
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

    metric_order = ["Shares Diluted", "Shares Basic"] if diluted_preferred else ["Shares Basic", "Shares Diluted"]
    best_value: float | None = None
    best_date: pd.Timestamp | None = None
    best_metric: str | None = None
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    asof_cutoff = asof_ts.tz_localize(None) if pd.notna(asof_ts) else None

    data_dir_key = str(data_dir or "").strip()
    for statement in ["income", "balance", "cashflow"]:
        try:
            rows = _quarterly_rows(_load_statement(statement, data_dir_key), symbol)
        except Exception:
            rows = pd.DataFrame()
        if rows.empty:
            continue
        if asof_cutoff is not None:
            rows = rows[rows["Report Date"] <= asof_cutoff].copy()
            if rows.empty:
                continue

        for metric_name in metric_order:
            column = _resolve_column(list(rows.columns), SHARE_COUNT_METRICS[metric_name])
            if column is None:
                continue

            frame = pd.DataFrame(
                {
                    "report_date": pd.to_datetime(rows["Report Date"], errors="coerce"),
                    "value": pd.to_numeric(rows[column], errors="coerce"),
                }
            ).dropna(subset=["report_date", "value"])
            if frame.empty:
                continue

            latest = frame.sort_values("report_date").iloc[-1]
            report_date = pd.Timestamp(latest["report_date"])
            value = float(latest["value"])
            if best_date is None or report_date > best_date or (
                report_date == best_date and best_metric == "Shares Basic" and metric_name == "Shares Diluted"
            ):
                best_value = value
                best_date = report_date
                best_metric = metric_name

    return best_value, best_date, best_metric


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
]
