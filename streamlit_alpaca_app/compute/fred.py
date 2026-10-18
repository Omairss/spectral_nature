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
    "Growth": "Output, spending, and savings indicators to track real-economy momentum.",
    "Housing": "Activity, mortgage, and price indicators to track the housing cycle.",
    "Credit Distress": "Delinquency and spread measures that tend to deteriorate before credit stress is obvious elsewhere.",
    "Policy & Liquidity": "Rates, real yields, breakevens, dollar, bank credit, and liquidity series that shape financial conditions.",
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
    FredSeriesSpec("Labor (BLS)", "JTSJOL", "Job Openings", "JOLTS job openings, a useful labor-demand gauge."),
    FredSeriesSpec("Labor (BLS)", "CIVPART", "Labor Force Participation", "Share of the working-age population in the labor force."),
    FredSeriesSpec("Labor (BLS)", "ICSA", "Initial Jobless Claims", "Weekly initial unemployment insurance claims."),
    FredSeriesSpec("Growth", "INDPRO", "Industrial Production", "Factory, mining, and utility output."),
    FredSeriesSpec("Growth", "RSAFS", "Retail Sales", "Advance real-economy demand gauge from retail sales."),
    FredSeriesSpec("Growth", "PCEC96", "Real Consumption", "Inflation-adjusted personal consumption expenditures."),
    FredSeriesSpec("Growth", "PSAVERT", "Personal Saving Rate", "Household saving buffer and spending-cushion gauge."),
    FredSeriesSpec("Housing", "HOUST", "Housing Starts", "Privately owned housing units started."),
    FredSeriesSpec("Housing", "PERMIT", "Building Permits", "Privately owned housing permits issued."),
    FredSeriesSpec("Housing", "MORTGAGE30US", "30Y Mortgage Rate", "Average 30-year fixed mortgage rate."),
    FredSeriesSpec("Housing", "CSUSHPINSA", "Case-Shiller National", "National home-price index."),
    FredSeriesSpec("Credit Distress", "DRCLACBS", "Consumer Loan Delinquency", "Delinquency rate on consumer loans."),
    FredSeriesSpec("Credit Distress", "DRCCLACBS", "Credit Card Delinquency", "Delinquency rate on credit card loans."),
    FredSeriesSpec("Credit Distress", "BAMLH0A0HYM2", "High Yield OAS", "Option-adjusted spread for U.S. high yield bonds."),
    FredSeriesSpec("Credit Distress", "BAMLC0A4CBBB", "BBB OAS", "Investment-grade spread proxy for broad corporate credit stress."),
    FredSeriesSpec("Policy & Liquidity", "DGS2", "2Y Treasury Yield", "Front-end Treasury yield and policy-expectations proxy."),
    FredSeriesSpec("Policy & Liquidity", "DGS10", "10Y Treasury Yield", "Benchmark long Treasury yield for growth and inflation pricing."),
    FredSeriesSpec("Policy & Liquidity", "T10Y2Y", "10Y-2Y Curve", "Classic curve-slope and inversion signal."),
    FredSeriesSpec("Policy & Liquidity", "DFII10", "10Y Real Yield", "10-year TIPS real yield and discount-rate proxy."),
    FredSeriesSpec("Policy & Liquidity", "T5YIE", "5Y Breakeven", "Five-year market-implied inflation compensation."),
    FredSeriesSpec("Policy & Liquidity", "T10YIE", "10Y Breakeven", "Ten-year market-implied inflation compensation."),
    FredSeriesSpec("Policy & Liquidity", "FEDFUNDS", "Fed Funds Rate", "Effective fed funds target corridor anchor."),
    FredSeriesSpec("Policy & Liquidity", "DTWEXBGS", "Broad Dollar Index", "Trade-weighted broad U.S. dollar index."),
    FredSeriesSpec("Policy & Liquidity", "BUSLOANS", "Commercial Bank Loans", "Bank-credit growth and lending-transmission proxy."),
    FredSeriesSpec("Policy & Liquidity", "WALCL", "Fed Balance Sheet", "Total assets held by the Federal Reserve."),
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


def normalize_fred_frequency_short(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    raw = str(value).strip()
    if not raw:
        return ""

    key = raw.upper()
    if key in {"D", "B", "W", "BW", "M", "Q", "SA", "A"} or key.startswith("WE"):
        return key

    if key.startswith("DAILY"):
        return "D"
    if key.startswith("BUSINESS"):
        return "B"
    if key.startswith("WEEKLY"):
        return "W"
    if key.startswith("BIWEEKLY"):
        return "BW"
    if key.startswith("MONTHLY"):
        return "M"
    if key.startswith("QUARTERLY"):
        return "Q"
    if key.startswith("SEMI"):
        return "SA"
    if key.startswith("ANNUAL") or key.startswith("YEARLY"):
        return "A"

    return raw


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


def _is_rate_like_series(metadata: dict[str, Any], values: pd.Series | None = None) -> bool:
    units_blob = " ".join(
        str(metadata.get(key) or "")
        for key in ("units_short", "units", "indicator", "title")
    ).lower()
    rate_tokens = ("percent", "%", "rate", "ratio", "basis points", "bps", "spread")
    if any(token in units_blob for token in rate_tokens):
        return True
    numeric = pd.to_numeric(values, errors="coerce").dropna() if values is not None else pd.Series(dtype=float)
    return bool(not numeric.empty and (numeric <= 0).any())


def _series_rate_of_change(
    values: pd.Series,
    metadata: dict[str, Any],
) -> tuple[pd.Series, str, str]:
    numeric = pd.to_numeric(values, errors="coerce")
    if _is_rate_like_series(metadata, numeric):
        return numeric.diff(), "delta", "Obs-to-obs delta"
    return numeric.pct_change(fill_method=None) * 100.0, "percent_change", "Obs-to-obs % change"


def build_fred_derived_views(
    observations: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    minimum_correlation_observations: int = 8,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Add chart-ready change series and frequency-aligned category correlations.

    Rate-like series are differenced; positive level series use percent change.
    Correlations are calculated from monthly-last levels transformed after
    alignment, so daily, weekly, monthly, and quarterly series are compared on
    one defensible time grid without forward-filling missing releases.
    """
    obs = observations.copy()
    summary_frame = summary.copy()
    empty_correlations = pd.DataFrame(
        columns=["category", "left_series_id", "right_series_id", "correlation", "observations"]
    )
    if obs.empty or not {"series_id", "date", "value"}.issubset(obs.columns):
        for field in ("rate_of_change", "smoothed_rate_of_change"):
            if field not in obs.columns:
                obs[field] = pd.Series(dtype=float)
        return obs, summary_frame, empty_correlations

    obs["series_id"] = obs["series_id"].astype(str)
    obs["date"] = pd.to_datetime(obs["date"], errors="coerce")
    obs["value"] = pd.to_numeric(obs["value"], errors="coerce")
    obs = (
        obs.dropna(subset=["series_id", "date", "value"])
        .sort_values(["series_id", "date"])
        .drop_duplicates(subset=["series_id", "date"], keep="last")
        .reset_index(drop=True)
    )
    obs["rate_of_change"] = pd.NA
    obs["smoothed_rate_of_change"] = pd.NA

    metadata_by_id: dict[str, dict[str, Any]] = {}
    if not summary_frame.empty and "series_id" in summary_frame.columns:
        summary_frame["series_id"] = summary_frame["series_id"].astype(str)
        metadata_by_id = {
            str(row.get("series_id") or ""): row.dropna().to_dict()
            for _, row in summary_frame.iterrows()
            if str(row.get("series_id") or "").strip()
        }

    derived_metadata: dict[str, dict[str, Any]] = {}
    monthly_changes: dict[str, pd.Series] = {}
    for series_id, indexes in obs.groupby("series_id", sort=False).groups.items():
        series_rows = obs.loc[indexes].sort_values("date")
        metadata = metadata_by_id.get(str(series_id), {})
        rate, kind, label = _series_rate_of_change(series_rows["value"], metadata)
        obs.loc[series_rows.index, "rate_of_change"] = rate.to_numpy()
        frequency = normalize_fred_frequency_short(metadata.get("frequency_short") or metadata.get("frequency"))
        span = 21 if frequency in {"D", "B"} else 8 if frequency.startswith("W") or frequency == "BW" else 4 if frequency == "Q" else 3 if frequency in {"A", "SA"} else 6
        smoothed = rate.ewm(span=span, adjust=False, min_periods=max(2, span // 3)).mean()
        obs.loc[series_rows.index, "smoothed_rate_of_change"] = smoothed.to_numpy()
        derived_metadata[str(series_id)] = {
            "rate_of_change_kind": kind,
            "rate_of_change_label": label,
            "latest_rate_of_change": float(rate.dropna().iloc[-1]) if not rate.dropna().empty else None,
            "smoothing_span": span,
            "smoothing_label": f"{span}-observation EMA",
            "latest_smoothed_rate_of_change": float(smoothed.dropna().iloc[-1]) if not smoothed.dropna().empty else None,
        }

        monthly = (
            series_rows.assign(month=series_rows["date"].dt.to_period("M").dt.to_timestamp())
            .groupby("month", sort=True)["value"]
            .last()
        )
        monthly_rate, _, _ = _series_rate_of_change(monthly, metadata)
        monthly_changes[str(series_id)] = monthly_rate.dropna()

    obs["rate_of_change"] = pd.to_numeric(obs["rate_of_change"], errors="coerce")
    obs["smoothed_rate_of_change"] = pd.to_numeric(obs["smoothed_rate_of_change"], errors="coerce")
    if not summary_frame.empty and "series_id" in summary_frame.columns:
        for field in ("rate_of_change_kind", "rate_of_change_label", "latest_rate_of_change", "smoothing_span", "smoothing_label", "latest_smoothed_rate_of_change"):
            summary_frame[field] = summary_frame["series_id"].map(
                {series_id: metadata.get(field) for series_id, metadata in derived_metadata.items()}
            )

    correlation_rows: list[dict[str, Any]] = []
    if not summary_frame.empty and {"series_id", "category"}.issubset(summary_frame.columns):
        minimum = max(int(minimum_correlation_observations), 3)
        for category, category_summary in summary_frame.groupby("category", sort=False):
            series_ids = [
                str(value)
                for value in category_summary["series_id"].dropna().astype(str).tolist()
                if str(value) in monthly_changes
            ]
            if not series_ids:
                continue
            aligned = pd.concat(
                [monthly_changes[series_id].rename(series_id) for series_id in series_ids],
                axis=1,
            )
            correlations = aligned.corr(min_periods=minimum)
            for left_id in series_ids:
                for right_id in series_ids:
                    paired = aligned[[left_id, right_id]].dropna() if left_id != right_id else aligned[[left_id]].dropna()
                    count = int(len(paired))
                    value = correlations.at[left_id, right_id] if left_id in correlations.index and right_id in correlations.columns else None
                    correlation_rows.append(
                        {
                            "category": str(category),
                            "left_series_id": left_id,
                            "right_series_id": right_id,
                            "correlation": float(value) if value is not None and pd.notna(value) and count >= minimum else None,
                            "observations": count,
                        }
                    )

    correlations_frame = pd.DataFrame(correlation_rows, columns=empty_correlations.columns)
    return obs, summary_frame, correlations_frame


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
    """Use the calendar anniversary, retaining the actual comparison observation.

    Row offsets are not elapsed years when releases are missing or calendars
    differ. Periodic series need their matching year-ago period; frequent
    series use the last observation available at the anniversary.
    """
    if not frame.empty:
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None)
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = (frame.dropna(subset=["date", "value"]).sort_values("date", kind="stable")
                 .drop_duplicates("date", keep="last").reset_index(drop=True))
    frequency_short = normalize_fred_frequency_short(metadata.get("frequency_short") or metadata.get("frequency"))
    latest_row = _latest_valid(frame)
    if latest_row is None:
        return {
            "category": spec.category,
            "series_id": spec.series_id,
            "indicator": spec.label,
            "units_short": metadata.get("units_short") or metadata.get("units"),
            "frequency_short": frequency_short,
            "latest_date": pd.NaT,
            "latest_value": None,
            "prev_delta": None,
            "yoy_delta": None,
            "yoy_pct": None,
            "yoy_reference_date": pd.NaT,
            "yoy_reference_value": None,
        }

    latest_value = float(latest_row["value"])
    previous_value = float(frame.iloc[-2]["value"]) if len(frame) >= 2 else None
    anniversary = latest_row["date"] - pd.DateOffset(years=1)
    period = {"M": "M", "Q": "Q", "SA": "M", "A": "Y"}.get(frequency_short)
    if period:
        prior = frame[frame["date"].dt.to_period(period) == anniversary.to_period(period)]
    else:
        prior = frame[frame["date"] <= anniversary]
    reference = prior.iloc[-1] if not prior.empty else None
    yoy_value = float(reference["value"]) if reference is not None else None
    return {
        "category": spec.category,
        "series_id": spec.series_id,
        "indicator": spec.label,
        "units_short": metadata.get("units_short") or metadata.get("units"),
        "frequency_short": frequency_short,
        "latest_date": pd.to_datetime(latest_row["date"], errors="coerce"),
        "latest_value": latest_value,
        "prev_delta": _value_delta(latest_value, previous_value),
        "yoy_delta": _value_delta(latest_value, yoy_value),
        "yoy_pct": _pct_delta(latest_value, yoy_value),
        "yoy_reference_date": reference["date"] if reference is not None else pd.NaT,
        "yoy_reference_value": yoy_value,
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
    if "frequency_short" in summary_frame.columns:
        summary_frame["frequency_short"] = summary_frame["frequency_short"].map(normalize_fred_frequency_short)
    if "series_id" in series_index_frame.columns:
        series_index_frame["series_id"] = series_index_frame["series_id"].astype(str)
    if "frequency_short" in series_index_frame.columns:
        series_index_frame["frequency_short"] = series_index_frame["frequency_short"].map(normalize_fred_frequency_short)

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
    summary_rows: list[dict[str, object]] = []
    if not obs_frame.empty and "series_id" in obs_frame.columns:
        for series_id, frame in obs_frame.groupby("series_id", sort=False):
            series_data[str(series_id)] = frame[[col for col in ["date", "value"] if col in frame.columns]].reset_index(drop=True)

    for spec in FRED_SERIES_SPECS:
        metadata = dict(metadata_by_id.get(spec.series_id, {}))
        if not summary_lookup.empty and spec.series_id in summary_lookup.index:
            lookup_row = summary_lookup.loc[spec.series_id]
            if isinstance(lookup_row, pd.DataFrame):
                lookup_row = lookup_row.iloc[0]
            for key, value in lookup_row.items():
                if key not in metadata and pd.notna(value):
                    metadata[key] = value
        metadata["frequency_short"] = normalize_fred_frequency_short(
            metadata.get("frequency_short") or metadata.get("frequency")
        )
        frame = series_data.get(spec.series_id, pd.DataFrame(columns=["date", "value"]))
        summary_rows.append(build_fred_series_summary(spec, metadata, frame.reset_index(drop=True)))

    summary_output = pd.DataFrame(summary_rows).reset_index(drop=True)
    obs_frame, summary_output, correlations = build_fred_derived_views(obs_frame, summary_output)

    return {
        "summary": summary_output,
        "series_data": series_data,
        "metadata": metadata_by_id,
        "specs_by_category": fred_specs_by_category(),
        "category_blurbs": FRED_CATEGORY_BLURBS,
        "series_index": series_index_frame.reset_index(drop=True),
        "observations": obs_frame.reset_index(drop=True),
        "correlations": correlations.reset_index(drop=True),
        "release_index": release_index_frame.reset_index(drop=True),
    }


def build_fred_signal_dicts(
    observations: pd.DataFrame,
    *,
    metadata_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract dense signal dicts for all FRED series from observation data.

    Wraps ``compute.signal_extraction.extract_fred_signals`` with the shared
    ``FRED_SERIES_SPECS`` so callers don't need to assemble specs themselves.
    """
    from compute.signal_extraction import extract_fred_signals
    return extract_fred_signals(observations, list(FRED_SERIES_SPECS), metadata_by_id=metadata_by_id)


__all__ = [
    "FRED_CATEGORY_BLURBS",
    "FRED_SERIES_SPECS",
    "FredSeriesSpec",
    "build_fred_derived_views",
    "build_fred_dashboard_from_pipeline",
    "build_fred_signal_dicts",
    "build_fred_series_summary",
    "format_fred_delta",
    "format_fred_value",
    "fred_categories",
    "fred_specs_by_category",
    "normalize_fred_frequency_short",
    "utc_today_naive",
]
