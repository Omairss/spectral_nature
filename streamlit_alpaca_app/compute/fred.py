from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FredSeriesSpec:
    category: str
    series_id: str
    label: str
    blurb: str


FRED_CATEGORY_BLURBS: dict[str, str] = {
    "Inflation": "Headline and core inflation gauges from BLS and BEA.",
    "Labor (BLS)": "Employment, unemployment, and wage pressure indicators from the BLS.",
    "Housing": "Construction and permit activity to track housing-cycle momentum.",
    "Credit Distress": "Delinquency and spread measures that tend to deteriorate before credit stress is obvious elsewhere.",
    "Money Supply": "Monetary aggregates showing liquidity expansion or contraction.",
}


FRED_SERIES_SPECS: tuple[FredSeriesSpec, ...] = (
    FredSeriesSpec("Inflation", "CPIAUCSL", "Headline CPI", "Consumer Price Index, all items."),
    FredSeriesSpec("Inflation", "CPILFESL", "Core CPI", "Consumer Price Index excluding food and energy."),
    FredSeriesSpec("Inflation", "PCEPI", "Headline PCE", "Fed-preferred broad inflation measure."),
    FredSeriesSpec("Inflation", "PCEPILFE", "Core PCE", "Fed-preferred inflation measure excluding food and energy."),
    FredSeriesSpec("Labor (BLS)", "UNRATE", "Unemployment Rate", "U-3 unemployment rate."),
    FredSeriesSpec("Labor (BLS)", "PAYEMS", "Nonfarm Payrolls", "Total nonfarm employees."),
    FredSeriesSpec("Labor (BLS)", "CES0500000003", "Avg Hourly Earnings", "Average hourly earnings for private employees."),
    FredSeriesSpec("Housing", "HOUST", "Housing Starts", "Privately owned housing units started."),
    FredSeriesSpec("Housing", "PERMIT", "Building Permits", "Privately owned housing permits issued."),
    FredSeriesSpec("Housing", "MORTGAGE30US", "30Y Mortgage Rate", "Average 30-year fixed mortgage rate."),
    FredSeriesSpec("Credit Distress", "DRCLACBS", "Consumer Loan Delinquency", "Delinquency rate on consumer loans."),
    FredSeriesSpec("Credit Distress", "DRCCLACBS", "Credit Card Delinquency", "Delinquency rate on credit card loans."),
    FredSeriesSpec("Credit Distress", "BAMLH0A0HYM2", "High Yield OAS", "Option-adjusted spread for U.S. high yield bonds."),
    FredSeriesSpec("Money Supply", "M1SL", "M1", "Narrow money stock."),
    FredSeriesSpec("Money Supply", "M2SL", "M2", "Broad money stock."),
    FredSeriesSpec("Money Supply", "WM2NS", "Weekly M2", "Weekly, non-seasonally adjusted M2."),
)


def fred_categories() -> list[str]:
    return list(FRED_CATEGORY_BLURBS.keys())


def fred_specs_by_category() -> dict[str, list[FredSeriesSpec]]:
    grouped = {category: [] for category in fred_categories()}
    for spec in FRED_SERIES_SPECS:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped


def utc_today_naive() -> pd.Timestamp:
    return pd.Timestamp.utcnow().tz_localize(None).normalize()


def _periods_per_year(frequency_short: str) -> int | None:
    key = str(frequency_short or "").upper()
    if key in {"D", "B"}:
        return 252
    if key in {"W", "WEF", "WETH", "WEW", "WETU", "WEM", "WESU", "WESA"}:
        return 52
    if key == "BW":
        return 26
    if key == "M":
        return 12
    if key == "Q":
        return 4
    if key == "SA":
        return 2
    if key == "A":
        return 1
    return None


def _latest_valid(frame: pd.DataFrame) -> pd.Series | None:
    if frame.empty:
        return None
    return frame.iloc[-1]


def _value_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return None
    return float(current) - float(previous)


def _pct_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0) or pd.isna(current) or pd.isna(previous):
        return None
    return float((float(current) / float(previous) - 1.0) * 100.0)


def _format_scaled_number(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}T"
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:,.2f}"


def format_fred_value(value: float | None, units_short: str | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    units = str(units_short or "")
    numeric = float(value)
    if "Percent" in units:
        return f"{numeric:.2f}%"
    if "Dollars per Hour" in units:
        return f"${numeric:.2f}"
    if "Billions of Dollars" in units:
        return f"${numeric / 1000:.2f}T"
    if "Thousands of Persons" in units or "Thousands of Units" in units:
        return _format_scaled_number(numeric * 1000.0)
    return f"{numeric:,.2f}"


def format_fred_delta(value: float | None, units_short: str | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    units = str(units_short or "")
    numeric = float(value)
    if "Percent" in units:
        return f"{numeric:+.2f} pp"
    if "Dollars per Hour" in units:
        return f"{numeric:+.2f}"
    if "Billions of Dollars" in units:
        return f"{numeric:+,.0f}B"
    if "Thousands of Persons" in units or "Thousands of Units" in units:
        return f"{numeric:+,.0f}k"
    return f"{numeric:+.2f}"


def build_fred_series_summary(spec: FredSeriesSpec, metadata: dict[str, Any], frame: pd.DataFrame) -> dict[str, object]:
    latest_row = _latest_valid(frame)
    if latest_row is None:
        return {
            "category": spec.category,
            "series_id": spec.series_id,
            "indicator": spec.label,
            "units_short": metadata.get("units_short") or metadata.get("units"),
            "frequency_short": metadata.get("frequency_short"),
            "latest_date": pd.NaT,
            "latest_value": None,
            "prev_delta": None,
            "yoy_delta": None,
            "yoy_pct": None,
        }

    latest_value = float(latest_row["value"])
    previous_value = float(frame.iloc[-2]["value"]) if len(frame) >= 2 else None
    periods = _periods_per_year(str(metadata.get("frequency_short") or ""))
    yoy_value = float(frame.iloc[-(periods + 1)]["value"]) if periods is not None and len(frame) > periods else None
    return {
        "category": spec.category,
        "series_id": spec.series_id,
        "indicator": spec.label,
        "units_short": metadata.get("units_short") or metadata.get("units"),
        "frequency_short": metadata.get("frequency_short"),
        "latest_date": pd.to_datetime(latest_row["date"], errors="coerce"),
        "latest_value": latest_value,
        "prev_delta": _value_delta(latest_value, previous_value),
        "yoy_delta": _value_delta(latest_value, yoy_value),
        "yoy_pct": _pct_delta(latest_value, yoy_value),
        "source_title": metadata.get("title") or spec.label,
        "last_updated": pd.to_datetime(metadata.get("last_updated"), utc=True, errors="coerce"),
    }


def build_fred_dashboard_from_pipeline(
    summary: pd.DataFrame,
    observations: pd.DataFrame,
    years: int,
    *,
    series_index: pd.DataFrame | None = None,
    release_index: pd.DataFrame | None = None,
) -> dict[str, object]:
    summary_frame = summary.copy()
    obs_frame = observations.copy()
    series_index_frame = series_index.copy() if isinstance(series_index, pd.DataFrame) else pd.DataFrame()
    release_index_frame = release_index.copy() if isinstance(release_index, pd.DataFrame) else pd.DataFrame()

    if "date" in obs_frame.columns:
        obs_frame["date"] = pd.to_datetime(obs_frame["date"], errors="coerce")
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(years=max(int(years), 1))
        obs_frame = obs_frame[obs_frame["date"] >= cutoff].copy()
    if "value" in obs_frame.columns:
        obs_frame["value"] = pd.to_numeric(obs_frame["value"], errors="coerce")

    summary_frame["series_id"] = summary_frame.get("series_id", pd.Series(dtype=str)).astype(str)
    if "series_id" in series_index_frame.columns:
        series_index_frame["series_id"] = series_index_frame["series_id"].astype(str)

    summary_lookup = (
        summary_frame.drop_duplicates(subset=["series_id"], keep="first").set_index("series_id")
        if not summary_frame.empty and "series_id" in summary_frame.columns
        else pd.DataFrame()
    )
    observation_release_lookup = (
        obs_frame.dropna(subset=["series_id", "release_id"])[["series_id", "release_id"]]
        .drop_duplicates(subset=["series_id"], keep="first")
        .set_index("series_id")["release_id"]
        if not obs_frame.empty and {"series_id", "release_id"}.issubset(obs_frame.columns)
        else pd.Series(dtype=object)
    )

    if series_index_frame.empty:
        series_index_frame = pd.DataFrame({"series_id": summary_frame.get("series_id", pd.Series(dtype=str)).astype(str)})

    if "title" not in series_index_frame.columns:
        series_index_frame["title"] = pd.Series(pd.NA, index=series_index_frame.index, dtype="object")
    if "source_title" in series_index_frame.columns:
        series_index_frame["title"] = series_index_frame["title"].fillna(series_index_frame["source_title"])
    if "indicator" in series_index_frame.columns:
        series_index_frame["title"] = series_index_frame["title"].fillna(series_index_frame["indicator"])
    if not summary_lookup.empty:
        if "source_title" in summary_lookup.columns:
            series_index_frame["title"] = series_index_frame["title"].fillna(series_index_frame["series_id"].map(summary_lookup["source_title"]))
        if "indicator" in summary_lookup.columns:
            series_index_frame["title"] = series_index_frame["title"].fillna(series_index_frame["series_id"].map(summary_lookup["indicator"]))
    series_index_frame["title"] = series_index_frame["title"].fillna(series_index_frame["series_id"])

    if "frequency_short" not in series_index_frame.columns:
        series_index_frame["frequency_short"] = (
            series_index_frame["series_id"].map(summary_lookup["frequency_short"])
            if not summary_lookup.empty and "frequency_short" in summary_lookup.columns
            else pd.Series(pd.NA, index=series_index_frame.index, dtype="object")
        )
    if "frequency" not in series_index_frame.columns:
        series_index_frame["frequency"] = series_index_frame["frequency_short"]

    if "units_short" not in series_index_frame.columns:
        series_index_frame["units_short"] = (
            series_index_frame["series_id"].map(summary_lookup["units_short"])
            if not summary_lookup.empty and "units_short" in summary_lookup.columns
            else pd.Series(pd.NA, index=series_index_frame.index, dtype="object")
        )
    if "units" not in series_index_frame.columns:
        series_index_frame["units"] = series_index_frame["units_short"]

    if "notes" not in series_index_frame.columns:
        series_index_frame["notes"] = pd.Series(pd.NA, index=series_index_frame.index, dtype="object")
    if "last_updated" not in series_index_frame.columns and not summary_lookup.empty and "last_updated" in summary_lookup.columns:
        series_index_frame["last_updated"] = series_index_frame["series_id"].map(summary_lookup["last_updated"])
    if "release_id" not in series_index_frame.columns:
        series_index_frame["release_id"] = series_index_frame["series_id"].map(observation_release_lookup)

    if release_index_frame.empty:
        release_index_cols = [col for col in ["release_id", "release_name"] if col in series_index_frame.columns]
        if release_index_cols:
            release_index_frame = series_index_frame[release_index_cols].dropna(subset=["release_id"]).drop_duplicates(
                subset=["release_id"], keep="first"
            )
    if release_index_frame.empty and "release_id" in obs_frame.columns:
        release_ids = pd.to_numeric(obs_frame["release_id"], errors="coerce").dropna().astype(int).drop_duplicates().sort_values()
        if not release_ids.empty:
            release_index_frame = pd.DataFrame({"release_id": release_ids})

    if "release_name" not in series_index_frame.columns:
        series_index_frame["release_name"] = pd.Series(pd.NA, index=series_index_frame.index, dtype="object")
    if not summary_lookup.empty and "release_name" in summary_lookup.columns:
        series_index_frame["release_name"] = series_index_frame["release_name"].fillna(
            series_index_frame["series_id"].map(summary_lookup["release_name"])
        )
    if not release_index_frame.empty and {"release_id", "release_name"}.issubset(release_index_frame.columns):
        release_lookup = release_index_frame.drop_duplicates(subset=["release_id"], keep="first").set_index("release_id")["release_name"]
        series_index_frame["release_name"] = series_index_frame["release_name"].fillna(
            pd.to_numeric(series_index_frame["release_id"], errors="coerce").map(release_lookup)
        )
    if not release_index_frame.empty and "release_name" not in release_index_frame.columns:
        release_index_frame["release_name"] = pd.Series(pd.NA, index=release_index_frame.index, dtype="object")
    if not release_index_frame.empty and {"release_id", "release_name"}.issubset(release_index_frame.columns):
        release_name_lookup = (
            series_index_frame[["release_id", "release_name"]]
            .dropna(subset=["release_id"])
            .drop_duplicates(subset=["release_id"], keep="first")
            .set_index("release_id")["release_name"]
        )
        release_index_frame["release_name"] = release_index_frame["release_name"].fillna(
            pd.to_numeric(release_index_frame["release_id"], errors="coerce").map(release_name_lookup)
        )

    sort_cols = [col for col in ["release_name", "title", "series_id"] if col in series_index_frame.columns]
    if sort_cols:
        series_index_frame = (
            series_index_frame.sort_values(sort_cols, na_position="last")
            .drop_duplicates(subset=["series_id"], keep="first")
            .reset_index(drop=True)
        )

    metadata_by_id: dict[str, dict[str, object]] = {}
    for _, row in series_index_frame.iterrows():
        series_id = str(row.get("series_id") or "").strip()
        if not series_id:
            continue
        metadata_by_id[series_id] = row.dropna().to_dict()

    series_data: dict[str, pd.DataFrame] = {}
    if not obs_frame.empty and "series_id" in obs_frame.columns:
        for series_id, frame in obs_frame.groupby("series_id", sort=False):
            series_data[str(series_id)] = frame[[col for col in ["date", "value"] if col in frame.columns]].reset_index(drop=True)

    return {
        "summary": summary_frame.reset_index(drop=True),
        "series_data": series_data,
        "metadata": metadata_by_id,
        "specs_by_category": fred_specs_by_category(),
        "category_blurbs": FRED_CATEGORY_BLURBS,
        "series_index": series_index_frame.reset_index(drop=True),
        "observations": obs_frame.reset_index(drop=True),
        "release_index": release_index_frame.reset_index(drop=True),
    }


__all__ = [
    "FRED_CATEGORY_BLURBS",
    "FRED_SERIES_SPECS",
    "FredSeriesSpec",
    "build_fred_dashboard_from_pipeline",
    "build_fred_series_summary",
    "format_fred_delta",
    "format_fred_value",
    "fred_categories",
    "fred_specs_by_category",
    "utc_today_naive",
]
