from __future__ import annotations

from datetime import datetime
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from compute.treasury_yields import (
    TREASURY_YIELD_FIELD_SPECS,
    build_treasury_yield_facts_1d,
    build_treasury_yield_observations,
    build_treasury_yield_summary,
)


class TreasuryYieldError(RuntimeError):
    pass


TREASURY_XML_BASE_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
TREASURY_YIELD_DATA_KEY = "daily_treasury_yield_curve"
TREASURY_XML_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
}


def _treasury_request(params: dict[str, str]) -> str:
    headers = {
        "User-Agent": "spectral-nature/treasury-yields (+https://home.treasury.gov/)",
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(TREASURY_XML_BASE_URL, params=params, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise TreasuryYieldError(f"Treasury yield feed {response.status_code}: {response.text[:200]}")
    return response.text


def _parse_treasury_yield_feed(xml_text: str) -> pd.DataFrame:
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        raise TreasuryYieldError(f"Could not parse Treasury yield XML: {exc}") from exc

    rows: list[dict[str, object]] = []
    for entry in root.findall("atom:entry", TREASURY_XML_NAMESPACES):
        props = entry.find("atom:content/m:properties", TREASURY_XML_NAMESPACES)
        if props is None:
            continue
        row: dict[str, object] = {
            "updated_at_utc": pd.to_datetime(
                entry.findtext("atom:updated", default="", namespaces=TREASURY_XML_NAMESPACES),
                utc=True,
                errors="coerce",
            )
        }
        date_text = props.findtext("d:NEW_DATE", default="", namespaces=TREASURY_XML_NAMESPACES)
        row["date"] = pd.to_datetime(date_text, errors="coerce")
        for spec in TREASURY_YIELD_FIELD_SPECS:
            raw = props.findtext(f"d:{spec.xml_field}", default="", namespaces=TREASURY_XML_NAMESPACES)
            row[spec.xml_field] = pd.to_numeric(raw, errors="coerce")
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return frame


def load_treasury_yield_curve(*, years: int = 3, end_date: datetime | pd.Timestamp | None = None) -> pd.DataFrame:
    latest = pd.to_datetime(end_date or pd.Timestamp.utcnow(), utc=True, errors="coerce")
    if pd.isna(latest):
        latest = pd.Timestamp.utcnow().tz_localize("UTC")
    latest = latest.tz_convert("UTC")
    years = max(int(years), 1)
    start_year = int(latest.year) - years + 1
    frames: list[pd.DataFrame] = []
    for year in range(start_year, int(latest.year) + 1):
        payload = _treasury_request(
            {
                "data": TREASURY_YIELD_DATA_KEY,
                "field_tdr_date_value": str(year),
            }
        )
        frame = _parse_treasury_yield_feed(payload)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["updated_at_utc"] = pd.to_datetime(out["updated_at_utc"], utc=True, errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return out


def load_treasury_yield_datasets(*, years: int = 3, end_date: datetime | pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    wide = load_treasury_yield_curve(years=years, end_date=end_date)
    return {
        "yield_curve_wide": wide,
        "yield_curve_observations": build_treasury_yield_observations(wide),
        "yield_curve_summary": build_treasury_yield_summary(wide),
        "yield_curve_facts_1d": build_treasury_yield_facts_1d(wide, asof_time_utc=end_date or pd.Timestamp.utcnow()),
    }


__all__ = [
    "TREASURY_XML_BASE_URL",
    "TREASURY_YIELD_DATA_KEY",
    "TREASURY_XML_NAMESPACES",
    "TreasuryYieldError",
    "load_treasury_yield_curve",
    "load_treasury_yield_datasets",
]
