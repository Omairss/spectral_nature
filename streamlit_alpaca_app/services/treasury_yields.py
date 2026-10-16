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


def parse_treasury_rate_feed(xml_text: str, *, date_field: str = "NEW_DATE") -> pd.DataFrame:
    """Retain all Treasury fields, using the feed's declared EDM data types.

    Curves use NEW_DATE; the daily bill feed uses INDEX_DATE. QUOTE_DATE,
    maturity dates and CUSIPs remain distinct source fields.
    """
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
        for prop in props:
            name = prop.tag.rsplit("}", 1)[-1]
            data_type = prop.get(f"{{{TREASURY_XML_NAMESPACES['m']}}}type", "Edm.String")
            raw = prop.text
            if data_type in {"Edm.Double", "Edm.Single", "Edm.Decimal", "Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte"}:
                row[name] = pd.to_numeric(raw, errors="coerce")
            elif data_type in {"Edm.DateTime", "Edm.DateTimeOffset"}:
                row[name] = pd.to_datetime(raw, errors="coerce")
            else:
                row[name] = raw
        if date_field not in row:
            raise TreasuryYieldError(f"Treasury feed missing observation date field {date_field}")
        row["date"] = pd.to_datetime(row[date_field], errors="coerce")
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if frame["date"].isna().any():
        raise TreasuryYieldError(f"Treasury feed contains invalid {date_field} values")
    frame = frame.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return frame


def _parse_treasury_yield_feed(xml_text: str) -> pd.DataFrame:
    frame = parse_treasury_rate_feed(xml_text)
    for spec in TREASURY_YIELD_FIELD_SPECS:
        if not frame.empty and spec.xml_field not in frame:
            frame[spec.xml_field] = float("nan")
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
    "parse_treasury_rate_feed",
    "load_treasury_yield_curve",
    "load_treasury_yield_datasets",
]
