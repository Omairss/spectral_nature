"""Alpha Vantage transport and its structured expected-earnings calendar."""
from __future__ import annotations

import csv
from datetime import date
from io import StringIO

import requests

from services.secrets import resolve_secret_value

API_URL = "https://www.alphavantage.co/query"


def load_alpha_vantage_key() -> str:
    return resolve_secret_value(
        ("ALPHA_VANTAGE_API_KEY", "ALPHAVANTAGE_API_KEY"),
        secret_name_env="ALPHA_VANTAGE_SECRET_NAME", default_secret_name="AlphaVantage",
        placeholders={"demo", "your_key_here"},
    )


class AlphaVantageError(RuntimeError):
    pass


def earnings_calendar(api_key: str, *, horizon: str = "3month") -> list[dict[str, str]]:
    if not api_key or api_key == "demo":
        raise AlphaVantageError("An owner API key is required")
    if horizon not in {"3month", "6month", "12month"}:
        raise ValueError("Unsupported earnings-calendar horizon")
    try:
        response = requests.get(API_URL, params={
            "function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": api_key,
        }, timeout=(10, 30))
        if response.status_code != 200:
            raise AlphaVantageError(f"HTTP {response.status_code}")
        # Do not propagate request URLs or provider messages that may contain credentials.
        return parse_earnings_csv(response.text)
    except requests.RequestException as exc:
        raise AlphaVantageError(type(exc).__name__) from None


def parse_earnings_csv(text: str) -> list[dict[str, str]]:
    if len(text) > 8_000_000:
        raise AlphaVantageError("Calendar response exceeds size limit")
    reader = csv.DictReader(StringIO(text.lstrip("\ufeff")))
    required = {"symbol", "name", "reportDate", "fiscalDateEnding"}
    if not required.issubset(reader.fieldnames or []):
        raise AlphaVantageError("Expected earnings CSV was not returned")
    rows = list(reader)
    for row in rows:
        if not row["symbol"]:
            raise AlphaVantageError("Incomplete earnings identity")
        try:
            date.fromisoformat(row["reportDate"])
            date.fromisoformat(row["fiscalDateEnding"])
        except (ValueError, TypeError):
            raise AlphaVantageError("Invalid earnings date or fiscal period") from None
    return rows
