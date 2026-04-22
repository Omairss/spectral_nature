from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import os
import random
import time
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots
from services.secrets import build_azure_credential

from compute.fred import (
    FRED_CATEGORY_BLURBS,
    FRED_SERIES_SPECS,
    FredSeriesSpec,
    build_fred_series_summary,
    format_fred_delta,
    format_fred_value,
    fred_categories,
    fred_specs_by_category,
    normalize_fred_frequency_short,
    utc_today_naive,
)


class FredAPIError(RuntimeError):
    pass


_FRED_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _load_fred_api_key_from_env() -> str:
    for env_var in ("FRED_API_KEY", "FRED_KEY"):
        value = (os.getenv(env_var) or "").strip()
        if value and value.lower() not in {"your_key_here", "demo"}:
            return value
    return ""


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except Exception:
        return default
    return max(parsed, minimum)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except Exception:
        return default
    return max(parsed, minimum)


def _fred_http_max_attempts() -> int:
    return _int_env("FRED_HTTP_MAX_ATTEMPTS", 5, minimum=1)


def _fred_http_initial_backoff_seconds() -> float:
    return _float_env("FRED_HTTP_INITIAL_BACKOFF_SECONDS", 1.0, minimum=0.0)


def _fred_http_backoff_multiplier() -> float:
    return _float_env("FRED_HTTP_BACKOFF_MULTIPLIER", 2.0, minimum=1.0)


def _fred_http_backoff_jitter_seconds() -> float:
    return _float_env("FRED_HTTP_BACKOFF_JITTER_SECONDS", 0.25, minimum=0.0)


def _fred_bulk_mode() -> str:
    raw = (os.getenv("FRED_BULK_MODE") or "v1_only").strip().lower()
    if raw in {"disable", "disabled", "off", "false", "0", "v1_only"}:
        return "v1_only"
    if raw in {"require", "required", "bulk_only"}:
        return "require"
    return "prefer"


def _fred_retry_delay_seconds(attempt: int, headers: dict[str, Any] | None = None) -> float:
    retry_after = str((headers or {}).get("Retry-After") or "").strip()
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except Exception:
            pass
    delay = _fred_http_initial_backoff_seconds() * (_fred_http_backoff_multiplier() ** max(int(attempt) - 1, 0))
    jitter = _fred_http_backoff_jitter_seconds()
    if jitter > 0:
        delay += random.uniform(0.0, jitter)
    return delay


@lru_cache(maxsize=4)
def _load_secret_from_keyvault(
    secret_name: str,
    vault_url: str | None = None,
    vault_name: str | None = None,
) -> str:
    try:
        from azure.keyvault.secrets import SecretClient
    except Exception as exc:
        raise FredAPIError("Azure Key Vault dependencies are not available.") from exc

    resolved_vault_url = (vault_url or "").strip()
    if not resolved_vault_url:
        resolved_vault_name = (vault_name or "").strip() or "spectral-nature-kvault"
        resolved_vault_url = f"https://{resolved_vault_name}.vault.azure.net"

    try:
        credential = build_azure_credential()
        if credential is None:
            raise FredAPIError("Azure credentials are not available.")
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


class FREDClient:
    def __init__(self, api_key: str, base_url: str = "https://api.stlouisfed.org/fred"):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise FredAPIError("FRED_API_KEY is required to load macro data.")

    def _request_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        timeout: int,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        max_attempts = _fred_http_max_attempts()
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= max_attempts:
                    raise FredAPIError(
                        f"FRED request failed after {attempt} attempts: {type(exc).__name__}: {exc}"
                    ) from exc
                print(
                    f"[warn] FRED request retrying path={path} attempt={attempt}/{max_attempts} "
                    f"error={type(exc).__name__}"
                )
                time.sleep(_fred_retry_delay_seconds(attempt))
                continue

            if resp.status_code < 400:
                return resp.json()

            if resp.status_code in _FRED_RETRYABLE_STATUS_CODES and attempt < max_attempts:
                print(
                    f"[warn] FRED request retrying path={path} status={resp.status_code} "
                    f"attempt={attempt}/{max_attempts}"
                )
                time.sleep(_fred_retry_delay_seconds(attempt, getattr(resp, "headers", None)))
                continue

            raise FredAPIError(f"FRED API {resp.status_code}: {resp.text}")

        raise FredAPIError(f"FRED request failed after {max_attempts} attempts.")

    def _request_v1(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        payload["api_key"] = self.api_key
        payload["file_type"] = "json"
        return self._request_json(path, payload, timeout=25)

    def _request_v2(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = dict(params)
        payload.setdefault("format", "json")
        return self._request_json(path, payload, headers=headers, timeout=60)

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
                        "frequency_short": normalize_fred_frequency_short(
                            series.get("frequency_short") or series.get("frequency")
                        ),
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


def _observation_start_label(observation_start: pd.Timestamp) -> str:
    ts = pd.to_datetime(observation_start, errors="coerce")
    if pd.isna(ts):
        return str(observation_start or "")
    return ts.strftime("%Y-%m-%d")


def _release_index_row(release: dict[str, Any]) -> dict[str, Any]:
    release_id = pd.to_numeric(release.get("id"), errors="coerce")
    if pd.isna(release_id):
        raise FredAPIError(f"FRED release id is missing for release payload: {release}")
    return {
        "release_id": int(release_id),
        "release_name": release.get("name"),
        "press_release": bool(release.get("press_release")),
        "link": release.get("link"),
    }


def _series_index_row(spec: FredSeriesSpec, metadata: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    release_id = pd.to_numeric(release.get("id"), errors="coerce")
    return {
        "series_id": spec.series_id,
        "title": metadata.get("title") or spec.label,
        "frequency": metadata.get("frequency"),
        "frequency_short": normalize_fred_frequency_short(metadata.get("frequency_short") or metadata.get("frequency")),
        "units": metadata.get("units"),
        "units_short": metadata.get("units_short") or metadata.get("units"),
        "seasonal_adjustment": metadata.get("seasonal_adjustment_short") or metadata.get("seasonal_adjustment"),
        "last_updated": pd.to_datetime(metadata.get("last_updated"), utc=True, errors="coerce"),
        "notes": metadata.get("notes"),
        "release_id": int(release_id) if pd.notna(release_id) else None,
        "release_name": release.get("name"),
        "press_release": bool(release.get("press_release")),
        "link": release.get("link"),
    }


def _build_release_catalog(client: FREDClient) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]], pd.DataFrame]:
    release_by_series: dict[str, dict[str, Any]] = {}
    release_catalog: dict[int, dict[str, Any]] = {}
    release_index_rows: list[dict[str, Any]] = []
    for spec in FRED_SERIES_SPECS:
        release = client.get_series_release(spec.series_id)
        release_by_series[spec.series_id] = release
        release_row = _release_index_row(release)
        release_id = int(release_row["release_id"])
        if release_id in release_catalog:
            continue
        release_catalog[release_id] = release_row
        release_index_rows.append(release_row)
    release_index = pd.DataFrame(release_index_rows)
    if not release_index.empty:
        release_index = release_index.sort_values(["release_name", "release_id"], na_position="last").reset_index(drop=True)
    return release_by_series, release_catalog, release_index


def _finalize_fred_dashboard_payload(
    *,
    series_index: pd.DataFrame,
    observations: pd.DataFrame,
    release_index: pd.DataFrame,
) -> dict[str, object]:
    if not series_index.empty:
        series_index = (
            series_index.sort_values(["release_name", "title", "series_id"], na_position="last")
            .drop_duplicates(subset=["series_id"], keep="first")
            .reset_index(drop=True)
        )
    else:
        series_index = pd.DataFrame()

    if not observations.empty:
        observations = (
            observations.sort_values(["series_id", "date"])
            .drop_duplicates(subset=["series_id", "date"], keep="last")
            .reset_index(drop=True)
        )
    else:
        observations = pd.DataFrame(columns=["series_id", "date", "value", "release_id"])

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

    return {
        "summary": pd.DataFrame(summary_rows),
        "series_data": series_data,
        "metadata": metadata_by_id,
        "specs_by_category": fred_specs_by_category(),
        "category_blurbs": FRED_CATEGORY_BLURBS,
        "series_index": series_index,
        "observations": observations,
        "release_index": release_index,
    }


def _load_fred_dashboard_from_bulk(
    client: FREDClient,
    *,
    observation_start: pd.Timestamp,
    release_catalog: dict[int, dict[str, Any]],
    release_index: pd.DataFrame,
) -> dict[str, object]:
    series_index_parts: list[pd.DataFrame] = []
    observation_parts: list[pd.DataFrame] = []
    for release_id, release_row in release_catalog.items():
        release_series_index, release_observations = client.get_release_observations_bulk(release_id)
        if not release_series_index.empty:
            release_series_index = release_series_index.copy()
            release_series_index["release_id"] = release_id
            release_series_index["release_name"] = release_row["release_name"]
            release_series_index["press_release"] = release_row["press_release"]
            release_series_index["link"] = release_row["link"]
            series_index_parts.append(release_series_index)
        if not release_observations.empty:
            release_observations = release_observations[release_observations["date"] >= observation_start].copy()
            if not release_observations.empty:
                release_observations["release_id"] = release_id
                observation_parts.append(release_observations)

    series_index = pd.concat(series_index_parts, ignore_index=True) if series_index_parts else pd.DataFrame()
    observations = pd.concat(observation_parts, ignore_index=True) if observation_parts else pd.DataFrame(
        columns=["series_id", "date", "value", "release_id"]
    )
    return _finalize_fred_dashboard_payload(
        series_index=series_index,
        observations=observations,
        release_index=release_index.copy(),
    )


def _load_fred_dashboard_from_series(
    client: FREDClient,
    *,
    observation_start: pd.Timestamp,
    release_by_series: dict[str, dict[str, Any]],
    release_index: pd.DataFrame,
) -> dict[str, object]:
    observation_start_label = _observation_start_label(observation_start)
    series_index_rows: list[dict[str, Any]] = []
    observation_parts: list[pd.DataFrame] = []
    for spec in FRED_SERIES_SPECS:
        metadata = client.get_series_metadata(spec.series_id)
        release = release_by_series[spec.series_id]
        series_index_rows.append(_series_index_row(spec, metadata, release))

        frame = client.get_series_observations(spec.series_id, observation_start_label)
        if frame.empty:
            continue
        release_id = pd.to_numeric(release.get("id"), errors="coerce")
        frame = frame.copy()
        frame["series_id"] = spec.series_id
        if pd.notna(release_id):
            frame["release_id"] = int(release_id)
        observation_parts.append(frame[["series_id", "date", "value", "release_id"]])

    series_index = pd.DataFrame(series_index_rows)
    observations = pd.concat(observation_parts, ignore_index=True) if observation_parts else pd.DataFrame(
        columns=["series_id", "date", "value", "release_id"]
    )
    return _finalize_fred_dashboard_payload(
        series_index=series_index,
        observations=observations,
        release_index=release_index.copy(),
    )


def load_fred_dashboard(api_key: str, years: int = 10) -> dict[str, object]:
    client = FREDClient(api_key)
    observation_start = utc_today_naive() - pd.DateOffset(years=max(int(years), 1))
    release_by_series, release_catalog, release_index = _build_release_catalog(client)
    bulk_mode = _fred_bulk_mode()

    if bulk_mode != "v1_only":
        try:
            return _load_fred_dashboard_from_bulk(
                client,
                observation_start=observation_start,
                release_catalog=release_catalog,
                release_index=release_index,
            )
        except Exception as exc:
            if bulk_mode == "require":
                raise
            print(f"[warn] FRED bulk loader unavailable; falling back to per-series v1 requests: {type(exc).__name__}: {exc}")

    return _load_fred_dashboard_from_series(
        client,
        observation_start=observation_start,
        release_by_series=release_by_series,
        release_index=release_index,
    )

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
