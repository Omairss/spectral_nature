from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TreasuryYieldFieldSpec:
    xml_field: str
    series_id: str
    label: str
    tenor_years: float


TREASURY_YIELD_FIELD_SPECS: tuple[TreasuryYieldFieldSpec, ...] = (
    TreasuryYieldFieldSpec("BC_3MONTH", "UST_3M", "3M Treasury", 0.25),
    TreasuryYieldFieldSpec("BC_6MONTH", "UST_6M", "6M Treasury", 0.50),
    TreasuryYieldFieldSpec("BC_1YEAR", "UST_1Y", "1Y Treasury", 1.0),
    TreasuryYieldFieldSpec("BC_2YEAR", "UST_2Y", "2Y Treasury", 2.0),
    TreasuryYieldFieldSpec("BC_5YEAR", "UST_5Y", "5Y Treasury", 5.0),
    TreasuryYieldFieldSpec("BC_10YEAR", "UST_10Y", "10Y Treasury", 10.0),
    TreasuryYieldFieldSpec("BC_30YEAR", "UST_30Y", "30Y Treasury", 30.0),
)

TREASURY_SPREAD_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("CURVE_2S10S", "2s10s Curve", "BC_10YEAR", "BC_2YEAR"),
    ("CURVE_3M10Y", "3m10y Curve", "BC_10YEAR", "BC_3MONTH"),
    ("CURVE_5S30S", "5s30s Curve", "BC_30YEAR", "BC_5YEAR"),
)

FIELD_SPEC_BY_XML: dict[str, TreasuryYieldFieldSpec] = {spec.xml_field: spec for spec in TREASURY_YIELD_FIELD_SPECS}
FIELD_SPEC_BY_SERIES: dict[str, TreasuryYieldFieldSpec] = {spec.series_id: spec for spec in TREASURY_YIELD_FIELD_SPECS}


def _coerce_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def _delta_bps(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return None
    return round(float((float(current) - float(previous)) * 100.0), 2)


def _latest_valid(series: pd.Series) -> tuple[float | None, float | None, float | None]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None, None, None
    latest = round(float(clean.iloc[-1]), 6)
    previous = round(float(clean.iloc[-2]), 6) if len(clean) >= 2 else None
    five_back = round(float(clean.iloc[-6]), 6) if len(clean) >= 6 else None
    return latest, previous, five_back


def build_treasury_yield_observations(wide_frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(wide_frame, pd.DataFrame) or wide_frame.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "updated_at_utc",
                "series_id",
                "label",
                "tenor_years",
                "yield_pct",
                "source_dataset",
            ]
        )

    frame = wide_frame.copy()
    frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame["updated_at_utc"] = pd.to_datetime(frame.get("updated_at_utc"), utc=True, errors="coerce")
    value_columns = [spec.xml_field for spec in TREASURY_YIELD_FIELD_SPECS if spec.xml_field in frame.columns]
    frame = _coerce_numeric(frame, value_columns)

    parts: list[pd.DataFrame] = []
    for spec in TREASURY_YIELD_FIELD_SPECS:
        if spec.xml_field not in frame.columns:
            continue
        subset = frame[["date", "updated_at_utc", spec.xml_field]].copy()
        subset = subset.rename(columns={spec.xml_field: "yield_pct"})
        subset["series_id"] = spec.series_id
        subset["label"] = spec.label
        subset["tenor_years"] = spec.tenor_years
        subset["source_dataset"] = "treasury_daily_yield_curve"
        parts.append(subset)

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True, sort=False)
    out = out.dropna(subset=["date", "yield_pct"]).sort_values(["series_id", "date"]).reset_index(drop=True)
    return out


def build_treasury_yield_summary(wide_frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(wide_frame, pd.DataFrame) or wide_frame.empty:
        return pd.DataFrame()

    frame = wide_frame.copy()
    frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame["updated_at_utc"] = pd.to_datetime(frame.get("updated_at_utc"), utc=True, errors="coerce")
    value_columns = [spec.xml_field for spec in TREASURY_YIELD_FIELD_SPECS if spec.xml_field in frame.columns]
    frame = _coerce_numeric(frame, value_columns)
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for spec in TREASURY_YIELD_FIELD_SPECS:
        if spec.xml_field not in frame.columns:
            continue
        latest, previous, five_back = _latest_valid(frame[spec.xml_field])
        if latest is None:
            continue
        latest_row = frame.dropna(subset=[spec.xml_field]).iloc[-1]
        rows.append(
            {
                "series_id": spec.series_id,
                "label": spec.label,
                "metric_type": "yield",
                "latest_date": pd.to_datetime(latest_row.get("date"), errors="coerce"),
                "updated_at_utc": pd.to_datetime(latest_row.get("updated_at_utc"), utc=True, errors="coerce"),
                "latest_value": latest,
                "prev_delta_bps": _delta_bps(latest, previous),
                "five_day_delta_bps": _delta_bps(latest, five_back),
                "units": "Percent",
                "source_dataset": "treasury_daily_yield_curve",
            }
        )

    for series_id, label, long_field, short_field in TREASURY_SPREAD_SPECS:
        if long_field not in frame.columns or short_field not in frame.columns:
            continue
        spread = pd.to_numeric(frame[long_field], errors="coerce") - pd.to_numeric(frame[short_field], errors="coerce")
        latest, previous, five_back = _latest_valid(spread)
        if latest is None:
            continue
        valid = frame.loc[spread.dropna().index]
        latest_row = valid.iloc[-1]
        rows.append(
            {
                "series_id": series_id,
                "label": label,
                "metric_type": "spread",
                "latest_date": pd.to_datetime(latest_row.get("date"), errors="coerce"),
                "updated_at_utc": pd.to_datetime(latest_row.get("updated_at_utc"), utc=True, errors="coerce"),
                "latest_value": latest,
                "prev_delta_bps": _delta_bps(latest, previous),
                "five_day_delta_bps": _delta_bps(latest, five_back),
                "units": "Percent",
                "source_dataset": "treasury_daily_yield_curve",
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["metric_type", "series_id"]).reset_index(drop=True)
    return out


def build_treasury_yield_facts_1d(wide_frame: pd.DataFrame, *, asof_time_utc: Any | None = None) -> pd.DataFrame:
    if not isinstance(wide_frame, pd.DataFrame) or wide_frame.empty:
        return pd.DataFrame()

    frame = wide_frame.copy()
    frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame["updated_at_utc"] = pd.to_datetime(frame.get("updated_at_utc"), utc=True, errors="coerce")
    value_columns = [spec.xml_field for spec in TREASURY_YIELD_FIELD_SPECS if spec.xml_field in frame.columns]
    frame = _coerce_numeric(frame, value_columns)
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame()

    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) >= 2 else None
    row: dict[str, Any] = {
        "asof_time_utc": pd.to_datetime(asof_time_utc, utc=True, errors="coerce"),
        "latest_date": pd.to_datetime(latest.get("date"), errors="coerce"),
        "updated_at_utc": pd.to_datetime(latest.get("updated_at_utc"), utc=True, errors="coerce"),
        "source_dataset": "treasury_daily_yield_curve",
    }

    for spec in TREASURY_YIELD_FIELD_SPECS:
        value = pd.to_numeric(latest.get(spec.xml_field), errors="coerce")
        prev_value = pd.to_numeric(previous.get(spec.xml_field), errors="coerce") if previous is not None else pd.NA
        prefix = spec.series_id.lower()
        row[prefix] = float(value) if pd.notna(value) else None
        row[f"{prefix}_1d_bps"] = _delta_bps(float(value), float(prev_value)) if pd.notna(value) and pd.notna(prev_value) else None

    def _spread(long_field: str, short_field: str) -> tuple[float | None, float | None]:
        long_value = pd.to_numeric(latest.get(long_field), errors="coerce")
        short_value = pd.to_numeric(latest.get(short_field), errors="coerce")
        if pd.isna(long_value) or pd.isna(short_value):
            return None, None
        current = round(float(long_value - short_value), 6)
        if previous is None:
            return current, None
        prev_long = pd.to_numeric(previous.get(long_field), errors="coerce")
        prev_short = pd.to_numeric(previous.get(short_field), errors="coerce")
        if pd.isna(prev_long) or pd.isna(prev_short):
            return current, None
        return current, _delta_bps(current, round(float(prev_long - prev_short), 6))

    curve_2s10s, curve_2s10s_delta = _spread("BC_10YEAR", "BC_2YEAR")
    curve_3m10y, curve_3m10y_delta = _spread("BC_10YEAR", "BC_3MONTH")
    curve_5s30s, curve_5s30s_delta = _spread("BC_30YEAR", "BC_5YEAR")
    row["curve_2s10s"] = curve_2s10s
    row["curve_2s10s_1d_bps"] = curve_2s10s_delta
    row["curve_3m10y"] = curve_3m10y
    row["curve_3m10y_1d_bps"] = curve_3m10y_delta
    row["curve_5s30s"] = curve_5s30s
    row["curve_5s30s_1d_bps"] = curve_5s30s_delta
    return pd.DataFrame([row])


__all__ = [
    "FIELD_SPEC_BY_SERIES",
    "FIELD_SPEC_BY_XML",
    "TREASURY_SPREAD_SPECS",
    "TREASURY_YIELD_FIELD_SPECS",
    "TreasuryYieldFieldSpec",
    "build_treasury_yield_facts_1d",
    "build_treasury_yield_observations",
    "build_treasury_yield_summary",
]
