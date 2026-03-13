from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import os
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots


class FredAPIError(RuntimeError):
    pass


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


def _load_fred_api_key_from_env() -> str:
    for env_var in ("FRED_API_KEY", "FRED_KEY"):
        value = (os.getenv(env_var) or "").strip()
        if value and value.lower() not in {"your_key_here", "demo"}:
            return value
    return ""


@lru_cache(maxsize=4)
def _load_secret_from_keyvault(
    secret_name: str,
    vault_url: str | None = None,
    vault_name: str | None = None,
) -> str:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except Exception as exc:
        raise FredAPIError("Azure Key Vault dependencies are not available.") from exc

    resolved_vault_url = (vault_url or "").strip()
    if not resolved_vault_url:
        resolved_vault_name = (vault_name or "").strip() or "spectral-nature-kvault"
        resolved_vault_url = f"https://{resolved_vault_name}.vault.azure.net"

    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=resolved_vault_url, credential=credential)
        return str(client.get_secret(secret_name).value or "").strip()
    except Exception as exc:
        raise FredAPIError(f"Could not load '{secret_name}' from Azure Key Vault: {exc}") from exc


def load_fred_api_key() -> str:
    key_from_vault = ""
    vault_secret_name = (os.getenv("FRED_KEY_VAULT_SECRET") or "Fred").strip() or "Fred"
    vault_url = (os.getenv("AZURE_KEY_VAULT_URL") or "").strip()
    vault_name = (os.getenv("AZURE_KEY_VAULT_NAME") or os.getenv("KEY_VAULT_NAME") or "").strip()

    try:
        key_from_vault = _load_secret_from_keyvault(
            vault_secret_name,
            vault_url=vault_url or None,
            vault_name=vault_name or None,
        )
    except FredAPIError:
        key_from_vault = ""

    if key_from_vault:
        return key_from_vault
    return _load_fred_api_key_from_env()


def fred_categories() -> list[str]:
    return list(FRED_CATEGORY_BLURBS.keys())


def fred_specs_by_category() -> dict[str, list[FredSeriesSpec]]:
    grouped = {category: [] for category in fred_categories()}
    for spec in FRED_SERIES_SPECS:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped


def _utc_today_naive() -> pd.Timestamp:
    # FRED observation dates are parsed as naive timestamps, so the lookback cutoff
    # must also be naive or pandas will reject the comparison.
    return pd.Timestamp.utcnow().tz_localize(None).normalize()


class FREDClient:
    def __init__(self, api_key: str, base_url: str = "https://api.stlouisfed.org/fred"):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise FredAPIError("FRED_API_KEY is required to load macro data.")

    def _request_v1(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        payload["api_key"] = self.api_key
        payload["file_type"] = "json"
        resp = requests.get(f"{self.base_url}/{path.lstrip('/')}", params=payload, timeout=25)
        if resp.status_code >= 400:
            raise FredAPIError(f"FRED API {resp.status_code}: {resp.text}")
        return resp.json()

    def _request_v2(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = dict(params)
        payload.setdefault("format", "json")
        resp = requests.get(f"{self.base_url}/{path.lstrip('/')}", params=payload, headers=headers, timeout=60)
        if resp.status_code >= 400:
            raise FredAPIError(f"FRED API {resp.status_code}: {resp.text}")
        return resp.json()

    def get_series_metadata(self, series_id: str) -> dict[str, Any]:
        payload = self._request_v1("series", {"series_id": series_id})
        series_rows = payload.get("seriess", []) or []
        if not series_rows:
            raise FredAPIError(f"No FRED metadata found for series '{series_id}'.")
        return series_rows[0]

    def get_series_observations(self, series_id: str, observation_start: str) -> pd.DataFrame:
        payload = self._request_v1(
            "series/observations",
            {
                "series_id": series_id,
                "observation_start": observation_start,
                "sort_order": "asc",
                "limit": 100000,
            },
        )
        rows = payload.get("observations", []) or []
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
        return frame

    def get_series_release(self, series_id: str) -> dict[str, Any]:
        payload = self._request_v1("series/release", {"series_id": series_id})
        releases = payload.get("releases", []) or []
        if not releases:
            raise FredAPIError(f"No FRED release found for series '{series_id}'.")
        return releases[0]

    def get_release_observations_bulk(self, release_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
        next_cursor: str | None = None
        series_meta: dict[str, dict[str, Any]] = {}
        observation_parts: list[pd.DataFrame] = []

        while True:
            params: dict[str, Any] = {
                "release_id": int(release_id),
                "format": "json",
                "limit": 500000,
            }
            if next_cursor:
                params["next_cursor"] = next_cursor

            payload = self._request_v2("v2/release/observations", params)
            for series in payload.get("series", []) or []:
                series_id = str(series.get("series_id") or "").strip()
                if not series_id:
                    continue

                series_meta.setdefault(
                    series_id,
                    {
                        "series_id": series_id,
                        "title": series.get("title"),
                        "frequency": series.get("frequency"),
                        "frequency_short": series.get("frequency_short") or series.get("frequency"),
                        "units": series.get("units"),
                        "units_short": series.get("units_short") or series.get("units"),
                        "seasonal_adjustment": series.get("seasonal_adjustment"),
                        "last_updated": pd.to_datetime(series.get("last_updated"), utc=True, errors="coerce"),
                        "notes": series.get("notes"),
                    },
                )

                observations = pd.DataFrame(series.get("observations", []) or [])
                if observations.empty:
                    continue
                observations["series_id"] = series_id
                observations["date"] = pd.to_datetime(observations["date"], errors="coerce")
                observations["value"] = pd.to_numeric(observations["value"], errors="coerce")
                observations = observations.dropna(subset=["date", "value"])
                if observations.empty:
                    continue
                observation_parts.append(observations[["series_id", "date", "value"]])

            has_more = str(payload.get("has_more", "false")).lower() == "true"
            next_cursor = payload.get("next_cursor")
            if not has_more or not next_cursor:
                break

        series_index = pd.DataFrame(series_meta.values())
        if not series_index.empty:
            series_index = series_index.sort_values(["title", "series_id"], na_position="last").reset_index(drop=True)

        observations = pd.concat(observation_parts, ignore_index=True) if observation_parts else pd.DataFrame(
            columns=["series_id", "date", "value"]
        )
        if not observations.empty:
            observations = (
                observations.sort_values(["series_id", "date"])
                .drop_duplicates(subset=["series_id", "date"], keep="last")
                .reset_index(drop=True)
            )
        return series_index, observations


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


def load_fred_dashboard(api_key: str, years: int = 10) -> dict[str, object]:
    client = FREDClient(api_key)
    observation_start = _utc_today_naive() - pd.DateOffset(years=max(int(years), 1))

    release_by_series: dict[str, dict[str, Any]] = {}
    release_index_rows: list[dict[str, Any]] = []
    release_catalog: dict[int, dict[str, Any]] = {}
    for spec in FRED_SERIES_SPECS:
        release = client.get_series_release(spec.series_id)
        release_id = int(release["id"])
        release_by_series[spec.series_id] = release
        if release_id not in release_catalog:
            release_row = {
                "release_id": release_id,
                "release_name": release.get("name"),
                "press_release": bool(release.get("press_release")),
                "link": release.get("link"),
            }
            release_catalog[release_id] = release_row
            release_index_rows.append(release_row)

    series_index_parts: list[pd.DataFrame] = []
    observation_parts: list[pd.DataFrame] = []
    for release_id, release_row in release_catalog.items():
        release_series_index, release_observations = client.get_release_observations_bulk(release_id)
        if not release_series_index.empty:
            release_series_index = release_series_index.copy()
            release_series_index["release_id"] = release_id
            release_series_index["release_name"] = release_row["release_name"]
            series_index_parts.append(release_series_index)
        if not release_observations.empty:
            release_observations = release_observations[release_observations["date"] >= observation_start].copy()
            if not release_observations.empty:
                release_observations["release_id"] = release_id
                observation_parts.append(release_observations)

    series_index = pd.concat(series_index_parts, ignore_index=True) if series_index_parts else pd.DataFrame()
    if not series_index.empty:
        series_index = (
            series_index.sort_values(["release_name", "title", "series_id"], na_position="last")
            .drop_duplicates(subset=["series_id"], keep="first")
            .reset_index(drop=True)
        )
    observations = pd.concat(observation_parts, ignore_index=True) if observation_parts else pd.DataFrame(
        columns=["series_id", "date", "value", "release_id"]
    )
    if not observations.empty:
        observations = (
            observations.sort_values(["series_id", "date"])
            .drop_duplicates(subset=["series_id", "date"], keep="last")
            .reset_index(drop=True)
        )

    metadata_by_id = {
        str(row["series_id"]): row.dropna().to_dict()
        for _, row in series_index.iterrows()
    }
    series_data: dict[str, pd.DataFrame] = {}
    summary_rows: list[dict[str, object]] = []
    for spec in FRED_SERIES_SPECS:
        frame = observations[observations["series_id"] == spec.series_id][["date", "value"]].copy()
        metadata = metadata_by_id.get(spec.series_id, {})
        series_data[spec.series_id] = frame.reset_index(drop=True)
        summary_rows.append(build_fred_series_summary(spec, metadata, frame.reset_index(drop=True)))

    summary = pd.DataFrame(summary_rows)
    return {
        "summary": summary,
        "series_data": series_data,
        "metadata": metadata_by_id,
        "specs_by_category": fred_specs_by_category(),
        "category_blurbs": FRED_CATEGORY_BLURBS,
        "series_index": series_index,
        "observations": observations,
        "release_index": pd.DataFrame(release_index_rows),
    }

def _is_rate_like_series(metadata: dict[str, Any]) -> bool:
    units_blob = " ".join(
        [
            str(metadata.get("units_short") or ""),
            str(metadata.get("units") or ""),
            str(metadata.get("frequency_short") or ""),
        ]
    ).lower()
    rate_tokens = ("percent", "%", "rate", "ratio", "basis points", "bps")
    return any(token in units_blob for token in rate_tokens)


def _stationarized_change(frame: pd.DataFrame, metadata: dict[str, Any]) -> tuple[pd.DataFrame, str, str, str]:
    if frame.empty or "value" not in frame.columns or "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "stationary_value"]), "", "", ""

    values = pd.to_numeric(frame["value"], errors="coerce")
    if values.dropna().shape[0] < 2:
        return pd.DataFrame(columns=["date", "stationary_value"]), "", "", ""

    if _is_rate_like_series(metadata) or (values.dropna() <= 0).any():
        stationary = values.diff()
        trace_name = "Obs-to-obs delta"
        axis_title = "Delta per observation"
        hover_label = "Delta"
    else:
        stationary = values.pct_change() * 100.0
        trace_name = "Obs-to-obs % change"
        axis_title = "Change per observation (%)"
        hover_label = "% Change"

    out = pd.DataFrame({"date": frame["date"], "stationary_value": stationary})
    out = out.dropna(subset=["date", "stationary_value"]).reset_index(drop=True)
    return out, trace_name, axis_title, hover_label


def build_fred_figure(
    spec: FredSeriesSpec,
    metadata: dict[str, Any],
    frame: pd.DataFrame,
    *,
    show_stationary_overlay: bool = False,
) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if frame.empty:
        fig.update_layout(template="plotly_dark", title=spec.label)
        return fig

    fig.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["value"],
            mode="lines",
            name=spec.label,
            line={"width": 2.5},
            hovertemplate="%{x|%Y-%m-%d}<br>Value=%{y}<extra></extra>",
        ),
        secondary_y=False,
    )

    if show_stationary_overlay:
        stationary, trace_name, axis_title, hover_label = _stationarized_change(frame, metadata)
        if not stationary.empty:
            fig.add_trace(
                go.Scatter(
                    x=stationary["date"],
                    y=stationary["stationary_value"],
                    mode="lines",
                    name=trace_name,
                    line={"width": 1.75, "dash": "dot"},
                    hovertemplate=f"%{{x|%Y-%m-%d}}<br>{hover_label}=%{{y:.2f}}<extra></extra>",
                ),
                secondary_y=True,
            )
            fig.update_yaxes(title_text=axis_title, secondary_y=True, zeroline=True, zerolinecolor="#666")

    fig.update_layout(
        template="plotly_dark",
        title=spec.label,
        xaxis_title="Date",
        yaxis_title=str(metadata.get("units_short") or metadata.get("units") or ""),
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
        height=320,
    )
    fig.update_yaxes(title_text=str(metadata.get("units_short") or metadata.get("units") or ""), secondary_y=False)
    if not show_stationary_overlay:
        fig.update_yaxes(showticklabels=False, secondary_y=True)
    return fig
