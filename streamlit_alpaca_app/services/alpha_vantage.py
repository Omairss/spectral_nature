"""Alpha Vantage transport and its structured expected-earnings calendar."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
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


class AlphaVantageQuotaError(AlphaVantageError):
    pass


class AlphaVantageClient:
    """One provider cache and application allowance shared by both deployments."""
    namespace = "market_calendar:alpha_vantage"

    def __init__(self, api_key: str, *, now: datetime | None = None):
        if not api_key or api_key == "demo":
            raise AlphaVantageError("An owner API key is required")
        self.api_key = api_key
        self.now = now or datetime.now(timezone.utc)
        self.account = hashlib.sha256(api_key.encode()).hexdigest()

    def _key(self, value: str) -> str:
        return self.account + ":" + value

    def history_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        from services.pipeline_store import read_execution_states
        values = read_execution_states(self.namespace, [self._key("history:" + s) for s in symbols])
        return {s: values.get(self._key("history:" + s), {}) for s in symbols}

    def _reserve(self, kind: str) -> None:
        from services.pipeline_store import mutate_execution_state
        now = self.now.timestamp()

        def reserve(value):
            calls = [row for row in value.get("calls", []) if row["at"] > now - 86400]
            # Leave eight of twenty slots for calendar requests and five provider
            # slots outside this application. Failed HTTP attempts still count.
            if len(calls) >= 20 or (kind == "history" and sum(r["kind"] == kind for r in calls) >= 12):
                raise AlphaVantageQuotaError("Application rolling request allowance reached")
            return {"calls": [*calls, {"at": now, "kind": kind}]}

        mutate_execution_state(self.namespace, self._key("requests"), reserve)

    def _fetch(self, kind: str, params: dict[str, str]) -> str:
        self._reserve(kind)
        try:
            response = requests.get(API_URL, params={**params, "apikey": self.api_key}, timeout=(8, 20))
            if response.status_code != 200:
                raise AlphaVantageError(f"HTTP {response.status_code}")
            if len(response.content) > 8_000_000:
                raise AlphaVantageError("Provider response exceeds size limit")
            return response.text
        except requests.RequestException as exc:
            raise AlphaVantageError(type(exc).__name__) from None

    def calendar(self, horizon: str) -> list[dict[str, str]]:
        from services.pipeline_store import read_execution_state, write_execution_state
        key = self._key("calendar:" + horizon)
        cached = read_execution_state(self.namespace, key, strict=True) or {}
        age = self.now.timestamp() - float(cached.get("fetched_at", 0))
        if not 0 <= age < 5 * 3600:
            rows = parse_earnings_csv(self._fetch("calendar", {"function": "EARNINGS_CALENDAR", "horizon": horizon}))
            cached = {"fetched_at": self.now.timestamp(), "rows": rows}
            if not write_execution_state(self.namespace, key, cached):
                raise AlphaVantageError("Calendar provider cache could not be retained")
        checked = datetime.fromtimestamp(cached["fetched_at"], timezone.utc).isoformat()
        return [{**row, "_fetched_at": checked} for row in cached["rows"]]

    def history(self, symbol: str) -> dict:
        from services.pipeline_store import mutate_execution_state, write_execution_state
        try:
            text = self._fetch("history", {"function": "EARNINGS", "symbol": symbol})
            try:
                payload = json.loads(text)
            except ValueError:
                raise AlphaVantageError("Expected earnings history JSON was not returned") from None
            rows = payload.get("quarterlyEarnings")
            if payload.get("symbol") != symbol or not isinstance(rows, list):
                raise AlphaVantageError("Expected company earnings history was not returned")
            retained = []
            for row in rows:
                try:
                    reported = date.fromisoformat(row["reportedDate"])
                    date.fromisoformat(row["fiscalDateEnding"])
                except (KeyError, ValueError, TypeError):
                    raise AlphaVantageError("Invalid reported earnings date or fiscal period") from None
                if reported >= self.now.date() - timedelta(days=370):
                    retained.append(row)
        except AlphaVantageQuotaError:
            raise
        except Exception as exc:
            # Retain good history and cool down unavailable companies so one bad
            # symbol cannot consume every subsequent backfill batch.
            mutate_execution_state(self.namespace, self._key("history:" + symbol),
                lambda old: {**old, "last_attempt_at": self.now.timestamp(), "error": type(exc).__name__})
            raise
        result = {"symbol": symbol, "fetched_at": self.now.timestamp(),
                  "last_attempt_at": self.now.timestamp(), "rows": retained}
        if not write_execution_state(self.namespace, self._key("history:" + symbol), result):
            raise AlphaVantageError("Company earnings history could not be retained")
        return result


def earnings_calendar(api_key: str, *, horizon: str = "3month") -> list[dict[str, str]]:
    if not api_key or api_key == "demo":
        raise AlphaVantageError("An owner API key is required")
    if horizon not in {"3month", "6month", "12month"}:
        raise ValueError("Unsupported earnings-calendar horizon")
    return AlphaVantageClient(api_key).calendar(horizon)


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
