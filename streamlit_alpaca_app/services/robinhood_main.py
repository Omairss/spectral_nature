from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests

from . import auth_store, brokerage_mcp_store, robinhood_main_store
from .brokerage_credentials import BrokerageCredentialError, decrypt_secret, encrypt_secret


PROVIDER = "robinhood_main"
RESOURCE_URL = "https://api.robinhood.com"
BONFIRE_URL = "https://bonfire.robinhood.com"
LOGIN_URL = f"{RESOURCE_URL}/oauth2/token/"
CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"
UPSTREAM_SOURCE_COMMIT = "cd2f4f368fba60ae45d139095238a5aadb7a1daa"
REVIEW_TOOL = "review_batch_stop_losses"
EXECUTION_TOOL = "place_batch_stop_losses"
BASKET_REVIEW_TOOL = "review_batch_equity_orders"
BASKET_EXECUTION_TOOL = "place_batch_equity_orders"
BENCHMARKS = ("SPY", "DIA", "QQQ", "VOO", "BRK.B", "ARKK")
TRUSTED_ROBINHOOD_HOSTS = frozenset({"api.robinhood.com", "bonfire.robinhood.com"})
PERFORMANCE_SPANS = ("day", "week", "month", "3month", "ytd", "year", "all")
PORTFOLIO_SERIES_KIND = "portfolio_performance"
PORTFOLIO_SERIES_KEY = "combined"
EQUITY_HISTORY_SERIES_KIND = "equity_history"
OPTION_HISTORY_SERIES_KIND = "option_history"
OPPORTUNITY_CATALOG_RESOURCE = "opportunity_catalog"
OPPORTUNITY_GROUP_RESOURCE = "opportunity_group"
SYNC_MARKER_RESOURCE = "sync_marker"


class RobinhoodMainError(RuntimeError):
    pass


class RobinhoodMainAuthorizationError(RobinhoodMainError):
    pass


class RobinhoodMainPolicyError(RobinhoodMainError):
    pass


class RobinhoodMainRequestError(RobinhoodMainError):
    def __init__(self, message: str, *, status_code: int = 0, response: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.response = dict(response or {})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _ensure_store() -> bool:
    return brokerage_mcp_store.ensure_brokerage_mcp_schema()


@lru_cache(maxsize=1)
def _ensure_data_store() -> bool:
    return robinhood_main_store.schema_ready()


def environment_name() -> str:
    value = (os.getenv("APP_TRACK") or "local").strip().lower() or "local"
    if not all(character.isalnum() or character in {"-", "_"} for character in value):
        raise RobinhoodMainError("APP_TRACK contains an invalid environment name.")
    return value


def execution_enabled() -> bool:
    return str(os.getenv("ROBINHOOD_MAIN_EXECUTION_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ticket_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("ROBINHOOD_ORDER_CONFIRMATION_TTL_SECONDS") or "120")
    except ValueError:
        configured = 120
    return min(max(configured, 30), 600)


def _safe_provider_message(payload: object) -> str:
    if not isinstance(payload, dict):
        return "Robinhood rejected the request."
    for key in ("detail", "message", "error_description", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:300]
    for key in ("non_field_errors", "errors", "validation_errors"):
        errors = payload.get(key)
        if not isinstance(errors, list) or not errors:
            continue
        first = errors[0]
        if isinstance(first, str):
            return " ".join(first.split())[:300]
        if isinstance(first, dict):
            return _safe_provider_message(first)
    safe_keys = sorted(
        str(key)
        for key in payload
        if str(key) not in {"access_token", "refresh_token", "device_token", "password", "username"}
        and not str(key).startswith("_")
    )
    if safe_keys:
        return f"Robinhood rejected fields: {', '.join(safe_keys[:8])}."
    return "Robinhood rejected the request."


def _authentication_error(stage: str, payload: dict[str, Any]) -> RobinhoodMainAuthorizationError:
    status = int(payload.get("_http_status") or 0)
    status_text = f" (HTTP {status})" if status else ""
    return RobinhoodMainAuthorizationError(
        f"Robinhood {stage} failed{status_text}: {_safe_provider_message(payload)}"
    )


def _decimal(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise RobinhoodMainPolicyError(f"{field} must be numeric.") from exc
    if not parsed.is_finite() or (parsed < 0 if allow_zero else parsed <= 0):
        raise RobinhoodMainPolicyError(f"{field} must be {'non-negative' if allow_zero else 'positive'}.")
    return parsed


def _signed_decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value if value is not None else "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise RobinhoodMainPolicyError(f"{field} must be numeric.") from exc
    if not parsed.is_finite():
        raise RobinhoodMainPolicyError(f"{field} must be numeric.")
    return parsed


def _decimal_text(value: Decimal, *, places: int | None = None) -> str:
    normalized = value
    if places is not None:
        normalized = value.quantize(Decimal(1).scaleb(-places))
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _round_price(value: object) -> str:
    price = _decimal(value, "Stop price")
    if price <= Decimal("0.01"):
        places = 6
    elif price < Decimal("1"):
        places = 4
    else:
        places = 2
    return _decimal_text(price, places=places)


def _mask_account(account_number: object) -> str:
    text = str(account_number or "").strip()
    return f"••••{text[-4:]}" if text else "account"


def _position_key(connection_id: str, account_number: object, symbol: object) -> str:
    material = f"{connection_id}|{str(account_number or '').strip()}|{str(symbol or '').upper().strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _arguments_hash(arguments: list[dict[str, str]]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [_json_safe(dict(row)) for row in frame.to_dict(orient="records")]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat() if pd.notna(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def generate_device_token() -> str:
    raw = secrets.token_hex(16)
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


class RobinhoodAppClient:
    _request_lock = threading.RLock()
    _last_request_at = 0.0
    _cooldown_until = 0.0

    def __init__(self, *, access_token: str = "", token_type: str = "Bearer") -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip,deflate,br",
                "Accept-Language": "en-US,en;q=1",
                "X-Robinhood-API-Version": "1.431.4",
                "Connection": "keep-alive",
                "User-Agent": str(
                    os.getenv("ROBINHOOD_MAIN_USER_AGENT")
                    or "spectral-nature/2026.10 robinhood-main-account"
                ).strip(),
            }
        )
        if access_token:
            self.session.headers["Authorization"] = f"{token_type or 'Bearer'} {access_token}"

    @staticmethod
    def _trusted_url(url: str) -> str:
        parsed = urlparse(str(url or ""))
        if parsed.scheme != "https" or parsed.netloc not in TRUSTED_ROBINHOOD_HOSTS:
            raise RobinhoodMainRequestError("Robinhood returned an untrusted API URL.")
        return url

    @classmethod
    def _pace_request(cls, method: str) -> None:
        now = time.monotonic()
        if cls._cooldown_until > now:
            remaining = max(1, int(cls._cooldown_until - now + 0.999))
            raise RobinhoodMainRequestError(
                f"Robinhood is rate limiting requests. Try again in about {remaining} seconds.",
                status_code=429,
            )
        try:
            configured = float(os.getenv("ROBINHOOD_MAIN_READ_DELAY_SECONDS") or "1.0")
        except ValueError:
            configured = 1.0
        minimum_delay = min(max(configured, 0.75), 3.0)
        if method.upper() != "GET":
            try:
                write_delay = float(os.getenv("ROBINHOOD_MAIN_WRITE_DELAY_SECONDS") or "2.5")
            except ValueError:
                write_delay = 2.5
            minimum_delay = min(max(write_delay, minimum_delay), 10.0)
        wait_for = minimum_delay - (now - cls._last_request_at)
        if wait_for > 0:
            time.sleep(wait_for)

    @classmethod
    def _record_rate_limit(cls, response: requests.Response, payload: object) -> int:
        retry_after = 0
        try:
            retry_after = int(float(response.headers.get("Retry-After") or 0))
        except (TypeError, ValueError):
            retry_after = 0
        if retry_after <= 0:
            message = _safe_provider_message(payload)
            match = re.search(r"(?:in|after)\s+(\d+)\s*seconds?", message, flags=re.IGNORECASE)
            retry_after = int(match.group(1)) if match else 300
        retry_after = min(max(retry_after, 5), 900)
        cls._cooldown_until = max(cls._cooldown_until, time.monotonic() + retry_after)
        return retry_after

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        timeout: int = 25,
        allow_error_payload: bool = False,
        extra_headers: dict[str, str] | None = None,
        operation: str = "request",
    ) -> dict[str, Any]:
        if data is not None and json_payload is not None:
            raise RobinhoodMainRequestError("A Robinhood request cannot contain both form and JSON data.")
        url = path_or_url if str(path_or_url).startswith("https://") else f"{RESOURCE_URL}/{str(path_or_url).lstrip('/')}"
        self._trusted_url(url)
        request_headers: dict[str, str] = dict(extra_headers or {})
        if json_payload is not None:
            request_headers["Content-Type"] = "application/json"
        elif data is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        attempts = 2 if method.upper() == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                with self._request_lock:
                    self._pace_request(method)
                    response = self.session.request(
                        method.upper(),
                        url,
                        params=params,
                        data=data,
                        json=json_payload,
                        headers=request_headers or None,
                        timeout=timeout,
                    )
                    type(self)._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                if attempt < attempts:
                    time.sleep(float(attempt))
                    continue
                raise RobinhoodMainRequestError(
                    f"Robinhood {operation} failed: {type(exc).__name__}."
                ) from exc
            try:
                payload = response.json() if response.content else {}
            except Exception:
                payload = {}
            if response.status_code < 400:
                return payload if isinstance(payload, dict) else {}
            if allow_error_payload:
                error_payload = dict(payload) if isinstance(payload, dict) else {}
                error_payload["_http_status"] = int(response.status_code)
                return error_payload
            if response.status_code in {401, 403}:
                raise RobinhoodMainAuthorizationError("Robinhood authorization expired. Reconnect the main account.")
            if response.status_code == 429:
                retry_after = self._record_rate_limit(response, payload)
                raise RobinhoodMainRequestError(
                    f"Robinhood rate limited {operation}. Try again in about {retry_after} seconds.",
                    status_code=429,
                    response=payload if isinstance(payload, dict) else {},
                )
            if response.status_code in {500, 502, 503, 504} and attempt < attempts:
                time.sleep(float(attempt))
                continue
            raise RobinhoodMainRequestError(
                f"Robinhood {operation} failed (HTTP {response.status_code}): {_safe_provider_message(payload)}",
                status_code=response.status_code,
                response=payload if isinstance(payload, dict) else {},
            )
        raise RobinhoodMainRequestError("Robinhood request failed.")

    def paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        operation: str = "pagination",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        payload = self.request(
            "GET",
            path,
            params=params,
            extra_headers=extra_headers,
            operation=operation,
        )
        visited: set[str] = set()
        while True:
            rows.extend(item for item in list(payload.get("results") or []) if isinstance(item, dict))
            next_url = str(payload.get("next") or "").strip()
            if not next_url:
                break
            if next_url in visited:
                raise RobinhoodMainRequestError("Robinhood returned a repeated pagination cursor.")
            visited.add(next_url)
            payload = self.request(
                "GET",
                next_url,
                extra_headers=extra_headers,
                operation=operation,
            )
        return rows

    def accounts(self) -> list[dict[str, Any]]:
        rows = self.paginated("/accounts/", params={"default_to_all_accounts": "true"})
        return [
            row
            for row in rows
            if not bool(row.get("deactivated"))
            and not bool(row.get("permanently_deactivated"))
            and str(row.get("account_number") or "").strip()
        ]

    def portfolio(self, account_number: str) -> dict[str, Any]:
        return self.request("GET", f"/portfolios/{account_number}/", operation="portfolio profile")

    def positions(self, account_number: str) -> list[dict[str, Any]]:
        return self.paginated(
            "/positions/",
            params={"account_number": account_number, "nonzero": "true"},
        )

    def instrument(self, url: str) -> dict[str, Any]:
        return self.request("GET", url)

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        normalized = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        if not normalized:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for index in range(0, len(normalized), 50):
            payload = self.request(
                "GET",
                "/quotes/",
                params={"symbols": ",".join(normalized[index : index + 50])},
                operation="equity quotes",
            )
            for row in list(payload.get("results") or []):
                if isinstance(row, dict) and row.get("symbol"):
                    out[str(row.get("symbol") or "").upper()] = row
        return out

    def instrument_by_symbol(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol or "").upper().strip()
        if not normalized:
            raise RobinhoodMainPolicyError("Symbol is required.")
        rows = self.paginated(
            "/instruments/",
            params={"symbol": normalized, "active_instruments_only": "true"},
            operation="instrument lookup",
        )
        exact = [row for row in rows if str(row.get("symbol") or "").upper().strip() == normalized]
        tradable = [row for row in exact if bool(row.get("tradeable", row.get("tradability") != "untradable"))]
        candidates = tradable or exact
        if len(candidates) != 1:
            raise RobinhoodMainPolicyError(f"Robinhood did not return one exact tradable instrument for {normalized}.")
        return candidates[0]

    def portfolio_live(self, account_number: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{BONFIRE_URL}/portfolio/account/{account_number}/live/",
            operation="live portfolio summary",
        )

    def portfolio_performance(self, account_number: str, *, span: str = "all") -> dict[str, Any]:
        normalized_span = span if span in PERFORMANCE_SPANS else "all"
        return self.request(
            "GET",
            f"{BONFIRE_URL}/portfolio/performance/{account_number}/",
            params={
                "display_span": normalized_span,
                "include_all_hours": "true",
                "chart_type": "historical_portfolio",
            },
            operation="portfolio performance",
        )

    def benchmark_history(self) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/quotes/historicals/",
            params={
                "symbols": ",".join(BENCHMARKS),
                "interval": "day",
                "span": "5year",
                "bounds": "regular",
            },
        )
        rows: list[dict[str, Any]] = []
        for group in list(payload.get("results") or []):
            if not isinstance(group, dict):
                continue
            symbol = str(group.get("symbol") or "").upper()
            for item in list(group.get("historicals") or []):
                if isinstance(item, dict):
                    rows.append({**item, "symbol": symbol})
        return rows

    def open_orders(self, account_number: str) -> list[dict[str, Any]]:
        rows = self.paginated("/orders/", params={"account_numbers": account_number})
        return [row for row in rows if row.get("cancel") or row.get("cancel_url")]

    def option_positions(self, account_number: str) -> list[dict[str, Any]]:
        return self.paginated(
            "/options/aggregate_positions/",
            params={"account_numbers": account_number, "nonzero": "true"},
        )

    def option_market_data(self, option_ids: list[str]) -> dict[str, dict[str, Any]]:
        identifiers = sorted({str(value or "").strip() for value in option_ids if str(value or "").strip()})
        out: dict[str, dict[str, Any]] = {}
        for index in range(0, len(identifiers), 17):
            batch = identifiers[index : index + 17]
            payload = self.request(
                "GET",
                "/marketdata/options/",
                params={"ids": ",".join(batch)},
                operation="option market data",
            )
            for item in list(payload.get("results") or []):
                if not isinstance(item, dict):
                    continue
                option_id = str(item.get("instrument_id") or item.get("id") or "").strip()
                if option_id:
                    out[option_id] = item
        return out

    def option_history(self, option_id: str, *, span: str = "week") -> list[dict[str, Any]]:
        normalized_span = span if span in {"day", "week", "year", "5year"} else "week"
        interval = {
            "day": "5minute",
            "week": "hour",
            "year": "day",
            "5year": "week",
        }[normalized_span]
        payload = self.request(
            "GET",
            f"/marketdata/options/historicals/{option_id}/",
            params={"interval": interval, "span": normalized_span},
            operation="option history",
        )
        return [item for item in list(payload.get("data_points") or []) if isinstance(item, dict)]

    def futures_accounts(self) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/ceres/v1/accounts/",
            extra_headers={"Rh-Contract-Protected": "true"},
            operation="futures accounts",
        )
        return [
            item
            for item in list(payload.get("results") or [])
            if isinstance(item, dict)
            and str(item.get("accountType") or item.get("account_type") or "").upper() == "FUTURES"
        ]

    def futures_positions(self, account_id: str) -> list[dict[str, Any]]:
        return self.paginated(
            f"/ceres/v1/accounts/{account_id}/aggregated_positions",
            extra_headers={"Rh-Contract-Protected": "true"},
            operation="futures positions",
        )

    def futures_cost_basis(self, account_id: str) -> dict[str, dict[str, Any]]:
        payload = self.request(
            "GET",
            f"/ceres/v1/accounts/{account_id}/pnl_cost_basis",
            extra_headers={"Rh-Contract-Protected": "true"},
            operation="futures cost basis",
        )
        values = payload.get("contractToInfo")
        return dict(values) if isinstance(values, dict) else {}

    def futures_market_data(self, contract_ids: list[str]) -> dict[str, dict[str, Any]]:
        identifiers = sorted({str(value or "").strip() for value in contract_ids if str(value or "").strip()})
        out: dict[str, dict[str, Any]] = {}
        for index in range(0, len(identifiers), 40):
            payload = self.request(
                "GET",
                "/marketdata/futures/quotes/v1/",
                params={"ids": ",".join(identifiers[index : index + 40])},
                extra_headers={"Rh-Contract-Protected": "true"},
                operation="futures market data",
            )
            for wrapper in list(payload.get("data") or payload.get("results") or []):
                if not isinstance(wrapper, dict):
                    continue
                quote = wrapper.get("data") if isinstance(wrapper.get("data"), dict) else wrapper
                identity = str(
                    quote.get("contract_id")
                    or quote.get("instrument_id")
                    or quote.get("id")
                    or wrapper.get("id")
                    or ""
                ).strip()
                if identity:
                    out[identity] = quote
        return out

    def futures_contract(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol or "").upper().lstrip("/").split(":", 1)[0].strip()
        if not normalized or not re.fullmatch(r"[A-Z0-9]+", normalized):
            raise RobinhoodMainRequestError("Robinhood returned an invalid futures symbol.")
        payload = self.request(
            "GET",
            f"/arsenal/v1/futures/contracts/symbol/{normalized}/",
            extra_headers={"Rh-Contract-Protected": "true"},
            operation="futures contract",
        )
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else dict(payload)

    def watchlists(self) -> list[dict[str, Any]]:
        return self.paginated("/discovery/lists/default/")

    def popular_watchlists(self) -> list[dict[str, Any]]:
        return self.paginated("/discovery/lists/popular/", operation="popular lists")

    def watchlist_items(self, list_id: str) -> list[dict[str, Any]]:
        return self.paginated(
            "/discovery/lists/items/",
            params={"list_id": list_id, "load_all_attributes": "true"},
        )

    def saved_screeners(self) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            "/beacon/scans/",
            operation="saved screeners",
        )
        for key in ("scans", "results", "screeners", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def stock_history(self, symbols: list[str], *, span: str = "year") -> list[dict[str, Any]]:
        normalized = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
        rows: list[dict[str, Any]] = []
        for index in range(0, len(normalized), 50):
            payload = self.request(
                "GET",
                "/marketdata/historicals/",
                params={
                    "symbols": ",".join(normalized[index : index + 50]),
                    "interval": "day",
                    "span": span,
                    "bounds": "regular",
                },
                operation="stock history",
            )
            rows.extend(item for item in list(payload.get("results") or []) if isinstance(item, dict))
        return rows

    def place_stop_loss(self, *, account: dict[str, Any], position: dict[str, Any], quote: dict[str, Any], order: dict[str, str], ref_id: str) -> dict[str, Any]:
        account_number = str(account.get("account_number") or "").strip()
        account_url = str(account.get("url") or f"{RESOURCE_URL}/accounts/{account_number}")
        instrument_url = str(position.get("instrument") or "").strip()
        if not account_number or not instrument_url:
            raise RobinhoodMainPolicyError("Robinhood account or instrument identity is unavailable.")
        payload = {
            "account": account_url,
            "instrument": instrument_url,
            "symbol": order["symbol"],
            "ask_price": _round_price(quote.get("ask_price") or quote.get("last_trade_price") or order["stop_price"]),
            "bid_ask_timestamp": str(
                quote.get("updated_at")
                or quote.get("venue_timestamp")
                or _now_utc().isoformat()
            ),
            "bid_price": _round_price(quote.get("bid_price") or quote.get("last_trade_price") or order["stop_price"]),
            "quantity": order["quantity"],
            "ref_id": ref_id,
            "type": "market",
            "stop_price": order["stop_price"],
            "time_in_force": "gtc",
            "trigger": "stop",
            "side": "sell",
            "market_hours": "regular_hours",
            "extended_hours": False,
            "position_effect": "close",
            "order_form_version": 7,
        }
        return self.request(
            "POST",
            "/orders/",
            params={"account_numbers": account_number},
            json_payload=payload,
            operation="stop order",
        )

    def place_equity_order(
        self,
        *,
        account: dict[str, Any],
        instrument: dict[str, Any],
        quote: dict[str, Any],
        side: str,
        quantity: str,
        ref_id: str,
    ) -> dict[str, Any]:
        account_number = str(account.get("account_number") or "").strip()
        symbol = str(instrument.get("symbol") or "").upper().strip()
        instrument_url = str(instrument.get("url") or "").strip()
        if side not in {"buy", "sell_short"}:
            raise RobinhoodMainPolicyError("Basket orders support buy or short.")
        if not account_number or not symbol or not instrument_url:
            raise RobinhoodMainPolicyError("Robinhood account or instrument identity is unavailable.")
        payload: dict[str, Any] = {
            "account": str(account.get("url") or f"{RESOURCE_URL}/accounts/{account_number}/"),
            "instrument": instrument_url,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": "market",
            "trigger": "immediate",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
            "extended_hours": False,
            "position_effect": "open",
            "order_form_version": 7,
            "ref_id": ref_id,
        }
        bid = quote.get("bid_price") or quote.get("last_trade_price")
        ask = quote.get("ask_price") or quote.get("last_trade_price")
        if bid:
            payload["bid_price"] = _round_price(bid)
        if ask:
            payload["ask_price"] = _round_price(ask)
        payload["bid_ask_timestamp"] = str(quote.get("updated_at") or _now_utc().isoformat())
        return self.request(
            "POST",
            "/orders/",
            params={"account_numbers": account_number},
            json_payload=payload,
            operation="equity order",
        )


def _login_payload(*, username: str, password: str, device_token: str) -> dict[str, Any]:
    return {
        "client_id": CLIENT_ID,
        "expires_in": 86400,
        "grant_type": "password",
        "password": password,
        "scope": "internal",
        "username": username,
        "device_token": device_token,
        "try_passkeys": False,
        "token_request_path": "/login",
        "create_read_only_secondary_token": True,
    }


def _token_result(payload: dict[str, Any], device_token: str) -> dict[str, Any] | None:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        return None
    return {
        "status": "authenticated",
        "device_token": device_token,
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or ""),
        "token_type": str(payload.get("token_type") or "Bearer"),
        "scope": str(payload.get("scope") or "internal"),
        "expires_in": int(payload.get("expires_in") or 86400),
    }


def _challenge_state(client: RobinhoodAppClient, machine_id: str) -> dict[str, str]:
    payload = client.request("GET", f"/pathfinder/inquiries/{machine_id}/user_view/")
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    if not context:
        type_context = payload.get("type_context") if isinstance(payload.get("type_context"), dict) else {}
        context = type_context.get("context") if isinstance(type_context.get("context"), dict) else {}
    challenge = context.get("sheriff_challenge") if isinstance(context.get("sheriff_challenge"), dict) else {}
    return {
        "challenge_id": str(challenge.get("id") or ""),
        "challenge_type": str(challenge.get("type") or "prompt"),
        "challenge_status": str(challenge.get("status") or "pending"),
    }


def _workflow_result(payload: dict[str, Any]) -> str:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    type_context = payload.get("type_context") if isinstance(payload.get("type_context"), dict) else {}
    workflow = payload.get("verification_workflow") if isinstance(payload.get("verification_workflow"), dict) else {}
    return str(
        context.get("result")
        or type_context.get("result")
        or workflow.get("workflow_status")
        or ""
    ).strip()


def _continue_verification_workflow(
    client: RobinhoodAppClient,
    machine_id: str,
) -> tuple[bool, str]:
    inquiry = client.request(
        "POST",
        f"/pathfinder/inquiries/{machine_id}/user_view/",
        json_payload={"sequence": 0, "user_input": {"status": "continue"}},
        allow_error_payload=True,
    )
    if int(inquiry.get("_http_status") or 0) >= 400:
        raise _authentication_error("device approval", inquiry)
    result = _workflow_result(inquiry)
    return result == "workflow_status_approved", result


def start_authentication(*, username: str, password: str, device_token: str = "") -> dict[str, Any]:
    clean_username = str(username or "").strip()
    clean_password = str(password or "")
    if not clean_username or not clean_password:
        raise RobinhoodMainAuthorizationError("Robinhood username and password are required.")
    token = str(device_token or "").strip() or generate_device_token()
    client = RobinhoodAppClient()
    payload = client.request(
        "POST",
        LOGIN_URL,
        data=_login_payload(username=clean_username, password=clean_password, device_token=token),
        allow_error_payload=True,
    )
    authenticated = _token_result(payload, token)
    if authenticated:
        return authenticated
    workflow = payload.get("verification_workflow") if isinstance(payload.get("verification_workflow"), dict) else {}
    workflow_id = str(workflow.get("id") or "").strip()
    if not workflow_id:
        raise _authentication_error("login", payload)
    try:
        machine = client.request(
            "POST",
            "/pathfinder/user_machine/",
            json_payload={"device_id": token, "flow": "suv", "input": {"workflow_id": workflow_id}},
        )
    except RobinhoodMainRequestError as exc:
        status_text = f" (HTTP {exc.status_code})" if exc.status_code else ""
        raise RobinhoodMainAuthorizationError(
            f"Robinhood device verification failed{status_text}: {exc}"
        ) from exc
    machine_id = str(machine.get("id") or "").strip()
    if not machine_id:
        raise RobinhoodMainAuthorizationError("Robinhood did not start device verification.")
    challenge: dict[str, str] = {}
    for _ in range(4):
        challenge = _challenge_state(client, machine_id)
        if challenge.get("challenge_id") or challenge.get("challenge_status") == "validated":
            break
        time.sleep(1)
    return {
        "status": "verification_required",
        "device_token": token,
        "workflow_id": workflow_id,
        "machine_id": machine_id,
        **challenge,
    }


def complete_authentication(
    *,
    username: str,
    password: str,
    pending: dict[str, Any],
    verification_code: str = "",
) -> dict[str, Any]:
    client = RobinhoodAppClient()
    device_token = str(pending.get("device_token") or "").strip()
    machine_id = str(pending.get("machine_id") or "").strip()
    challenge_id = str(pending.get("challenge_id") or "").strip()
    challenge_type = str(pending.get("challenge_type") or "prompt").strip().lower()
    if not device_token or not machine_id:
        raise RobinhoodMainAuthorizationError("Robinhood verification state is incomplete. Start again.")
    if challenge_type in {"sms", "email"}:
        code = str(verification_code or "").strip()
        if not code:
            return {**pending, "status": "verification_code_required"}
        response = client.request(
            "POST",
            f"/challenge/{challenge_id}/respond/",
            json_payload={"response": code},
            allow_error_payload=True,
        )
        if str(response.get("status") or "").lower() != "validated":
            raise _authentication_error("verification code", response)
    approved = False
    if challenge_type == "prompt" and challenge_id:
        prompt_url = f"/push/{challenge_id}/get_prompts_status/"
        for _ in range(12):
            prompt = client.request("GET", prompt_url)
            if str(prompt.get("challenge_status") or "").lower() == "validated":
                approved, result = _continue_verification_workflow(client, machine_id)
                if approved:
                    break
                if result and result != "workflow_status_internal_pending":
                    raise RobinhoodMainAuthorizationError(
                        f"Robinhood device approval failed: {result}."
                    )
            time.sleep(5)
    else:
        for _ in range(12):
            approved, result = _continue_verification_workflow(client, machine_id)
            if approved:
                break
            if result and result != "workflow_status_internal_pending":
                raise RobinhoodMainAuthorizationError(
                    f"Robinhood device approval failed: {result}."
                )
            time.sleep(5)
    if not approved:
        return {**pending, "status": "verification_required"}

    login = client.request(
        "POST",
        LOGIN_URL,
        data=_login_payload(username=str(username or "").strip(), password=str(password or ""), device_token=device_token),
        allow_error_payload=True,
    )
    authenticated = _token_result(login, device_token)
    if not authenticated:
        raise _authentication_error("login completion", login)
    return authenticated


def _connection_for_user(user_id: str) -> dict[str, Any] | None:
    if not _ensure_store():
        return None
    return brokerage_mcp_store.get_connection(
        user_id=str(user_id or "").strip(),
        provider=PROVIDER,
        resource_url=RESOURCE_URL,
        environment=environment_name(),
    )


def _client_from_connection(connection: dict[str, Any]) -> RobinhoodAppClient:
    if str(connection.get("status") or "") != "connected":
        raise RobinhoodMainAuthorizationError("Robinhood main accounts are not connected.")
    if _connection_token_expired(connection):
        connection = _refresh_connection_tokens(connection)
    try:
        access_token = decrypt_secret(connection.get("access_token_ciphertext"))
    except BrokerageCredentialError as exc:
        raise RobinhoodMainAuthorizationError(str(exc)) from exc
    if not access_token:
        raise RobinhoodMainAuthorizationError("Robinhood authorization is unavailable. Reconnect the main account.")
    return RobinhoodAppClient(
        access_token=access_token,
        token_type=str(connection.get("token_type") or "Bearer"),
    )


def _refresh_connection_tokens(connection: dict[str, Any]) -> dict[str, Any]:
    try:
        refresh_token = decrypt_secret(connection.get("refresh_token_ciphertext"))
        device_token = decrypt_secret(connection.get("device_token_ciphertext"))
    except BrokerageCredentialError as exc:
        raise RobinhoodMainAuthorizationError(str(exc)) from exc
    if not refresh_token or not device_token:
        raise RobinhoodMainAuthorizationError("Robinhood authorization expired. Reconnect the main account.")
    payload = RobinhoodAppClient().request(
        "POST",
        LOGIN_URL,
        data={
            "client_id": CLIENT_ID,
            "expires_in": 86400,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "internal",
            "device_token": device_token,
        },
        allow_error_payload=True,
        operation="token refresh",
    )
    refreshed = _token_result(payload, device_token)
    if not refreshed:
        raise _authentication_error("token refresh", payload)
    expires_in = min(max(int(refreshed.get("expires_in") or 86400), 60), 86400)
    new_refresh_token = str(refreshed.get("refresh_token") or refresh_token)
    encrypted_access_token = encrypt_secret(refreshed.get("access_token"))
    encrypted_refresh_token = encrypt_secret(new_refresh_token)
    expires_at = _now_utc() + timedelta(seconds=expires_in)
    brokerage_mcp_store.save_connection_tokens(
        connection_id=str(connection.get("id") or ""),
        access_token_ciphertext=encrypted_access_token,
        refresh_token_ciphertext=encrypted_refresh_token,
        token_type=str(refreshed.get("token_type") or "Bearer"),
        scope=str(refreshed.get("scope") or "internal"),
        token_expires_at=expires_at,
    )
    return {
        **connection,
        "access_token_ciphertext": encrypted_access_token,
        "refresh_token_ciphertext": encrypted_refresh_token,
        "token_type": str(refreshed.get("token_type") or "Bearer"),
        "scope": str(refreshed.get("scope") or "internal"),
        "token_expires_at": expires_at,
        "status": "connected",
    }


def _connection_token_expired(connection: dict[str, Any]) -> bool:
    expires_at = pd.to_datetime(connection.get("token_expires_at"), utc=True, errors="coerce")
    return bool(pd.notna(expires_at) and expires_at.to_pydatetime() <= _now_utc())


def stored_device_token(*, user_id: str) -> str:
    connection = _connection_for_user(user_id)
    if not connection:
        return ""
    try:
        return decrypt_secret(connection.get("device_token_ciphertext"))
    except BrokerageCredentialError:
        return ""


def connection_status(*, user_id: str, portfolio_id: str = "") -> dict[str, Any]:
    connection = _connection_for_user(user_id)
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id) if portfolio_id else None
    linked = bool(
        connection
        and portfolio
        and str(portfolio.get("brokerage_source") or "") == PROVIDER
        and str(portfolio.get("brokerage_account_ref") or "") == str(connection.get("id") or "")
    )
    if not connection:
        return {"status": "disconnected", "linked": False, "execution_enabled": execution_enabled()}
    info = dict(connection.get("server_info") or {})
    status = "expired" if _connection_token_expired(connection) else str(connection.get("status") or "disconnected")
    return {
        "status": status,
        "linked": linked,
        "account_count": int(info.get("account_count") or 0),
        "account_labels": list(info.get("account_labels") or []),
        "token_expires_at": connection.get("token_expires_at"),
        "last_verified_at": connection.get("last_verified_at"),
        "last_error": str(connection.get("last_error") or ""),
        "execution_enabled": execution_enabled(),
    }


def save_authentication(*, user_id: str, authenticated: dict[str, Any]) -> dict[str, Any]:
    if str(authenticated.get("status") or "") != "authenticated":
        raise RobinhoodMainAuthorizationError("Robinhood authentication is incomplete.")
    client = RobinhoodAppClient(
        access_token=str(authenticated.get("access_token") or ""),
        token_type=str(authenticated.get("token_type") or "Bearer"),
    )
    accounts = client.accounts()
    if not accounts:
        raise RobinhoodMainAuthorizationError("Robinhood returned no active investing accounts.")
    expires_in = min(max(int(authenticated.get("expires_in") or 86400), 60), 86400)
    server_info = {
        "account_count": len(accounts),
        "account_labels": [
            f"{str(account.get('type') or 'investing').replace('_', ' ')} {_mask_account(account.get('account_number'))}"
            for account in accounts
        ],
        "upstream_source_commit": UPSTREAM_SOURCE_COMMIT,
    }
    return brokerage_mcp_store.save_direct_connection(
        user_id=user_id,
        provider=PROVIDER,
        resource_url=RESOURCE_URL,
        environment=environment_name(),
        access_token_ciphertext=encrypt_secret(authenticated.get("access_token")),
        refresh_token_ciphertext=encrypt_secret(authenticated.get("refresh_token")),
        device_token_ciphertext=encrypt_secret(authenticated.get("device_token")),
        token_type=str(authenticated.get("token_type") or "Bearer"),
        scope=str(authenticated.get("scope") or "internal"),
        token_expires_at=_now_utc() + timedelta(seconds=expires_in),
        server_info=server_info,
    )


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if pd.notna(parsed) and parsed not in {float("inf"), float("-inf")} else default


def _money(value: object) -> float:
    if isinstance(value, (int, float, Decimal)):
        return _number(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    negative = text.startswith("-") or text.startswith("(")
    magnitude = _number(re.sub(r"[^0-9.]", "", text))
    return -magnitude if negative else magnitude


def _amount(value: object, default: float = 0.0) -> float:
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "units"):
            if value.get(key) is not None:
                return _number(value.get(key), default)
        return default
    return _number(value, default)


def _account_label(account: dict[str, Any]) -> str:
    account_type = str(account.get("type") or account.get("account_type") or "investing").replace("_", " ")
    return f"{account_type} {_mask_account(account.get('account_number'))}"


def _opaque_key(connection_id: str, account_number: object, kind: str, identity: object) -> str:
    material = "|".join(
        (connection_id, str(account_number or "").strip(), kind, str(identity or "").strip())
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _collector_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    message = re.sub(r"(?<!\d)(\d{4,})(?!\d)", lambda match: f"••••{match.group(1)[-4:]}", message)
    return message[:240] or type(exc).__name__


def _live_positions(client: RobinhoodAppClient, connection_id: str, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    instruments: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_number = str(account.get("account_number") or "").strip()
        label = _account_label(account)
        for position in client.positions(account_number):
            instrument_url = str(position.get("instrument") or "").strip()
            symbol = str(position.get("symbol") or "").upper().strip()
            if instrument_url and not symbol and instrument_url not in instruments:
                instruments[instrument_url] = client.instrument(instrument_url)
            instrument = instruments.get(instrument_url, {})
            symbol = symbol or str(instrument.get("symbol") or "").upper().strip()
            raw_quantity = _signed_decimal(position.get("quantity") or "0", "Position quantity")
            clearing_direction = str(position.get("clearing_direction") or "").lower().strip()
            quantity = -abs(raw_quantity) if clearing_direction in {"short", "credit"} else raw_quantity
            held_for_sells = _decimal(position.get("shares_held_for_sells") or "0", "Reserved shares", allow_zero=True)
            available_field = position.get("shares_available_for_sells")
            if quantity > 0 and available_field not in {None, ""}:
                available = min(
                    quantity,
                    _decimal(available_field, "Available shares", allow_zero=True),
                )
            else:
                available = max(quantity - held_for_sells, Decimal("0")) if quantity > 0 else Decimal("0")
            if not symbol or quantity == 0:
                continue
            average_cost = _decimal(
                position.get("clearing_average_cost")
                or position.get("average_buy_price")
                or "0",
                "Average price",
                allow_zero=True,
            )
            raw.append(
                {
                    "account": account,
                    "account_number": account_number,
                    "account_label": label,
                    "position_key": _position_key(connection_id, account_number, symbol),
                    "symbol": symbol,
                    "instrument": instrument_url,
                    "quantity": quantity,
                    "available_quantity": available,
                    "average_buy_price": average_cost,
                    "direction": "short" if quantity < 0 else "long",
                    "raw": position,
                }
            )
    quotes = client.quotes([row["symbol"] for row in raw])
    for row in raw:
        quote = quotes.get(row["symbol"], {})
        current = _decimal(
            quote.get("last_extended_hours_trade_price")
            or quote.get("last_trade_price")
            or quote.get("bid_price")
            or "0",
            "Current price",
            allow_zero=True,
        )
        previous = _decimal(quote.get("adjusted_previous_close") or quote.get("previous_close") or "0", "Previous close", allow_zero=True)
        row["quote"] = quote
        row["current_price"] = current
        row["previous_close"] = previous
    return raw


def _performance_points(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    lines = list(payload.get("lines") or [])
    returns_line = next(
        (line for line in lines if isinstance(line, dict) and line.get("identifier") == "returns"),
        lines[0] if lines and isinstance(lines[0], dict) else {},
    )
    points: list[dict[str, Any]] = []
    for segment in list(returns_line.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        for point in list(segment.get("points") or []):
            if not isinstance(point, dict):
                continue
            cursor = point.get("cursor_data") if isinstance(point.get("cursor_data"), dict) else {}
            label = cursor.get("label") if isinstance(cursor.get("label"), dict) else {}
            primary = cursor.get("primary_value") if isinstance(cursor.get("primary_value"), dict) else {}
            timestamp = pd.to_datetime(label.get("value"), utc=True, errors="coerce")
            if pd.isna(timestamp):
                continue
            points.append(
                {
                    "timestamp": timestamp,
                    "value": _money(primary.get("value")),
                    "return_pct": _number(point.get("y")) * 100.0,
                }
            )
    baseline = payload.get("performance_baseline")
    baseline_value = _money(baseline.get("amount")) if isinstance(baseline, dict) else 0.0
    return points, baseline_value


def _collect_performance(
    client: RobinhoodAppClient,
    accounts: list[dict[str, Any]],
    collector_errors: dict[str, str],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    histories: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for index, account in enumerate(accounts):
        account_number = str(account.get("account_number") or "").strip()
        label = _account_label(account)
        try:
            payload = client.portfolio_performance(account_number, span="all")
            points, baseline = _performance_points(payload)
            if not points:
                raise RobinhoodMainError("Robinhood returned no usable performance points.")
            frame = pd.DataFrame(points).sort_values("timestamp")
            column = f"account_{index}"
            frame = frame[["timestamp", "value"]].rename(columns={"value": column})
            histories.append(frame)
            last = points[-1]
            curve_start = float(points[0]["value"])
            current_value = float(last["value"])
            broker_return_pct = float(last["return_pct"])
            return_factor = 1.0 + broker_return_pct / 100.0
            effective_baseline = float(
                baseline
                or (current_value / return_factor if current_value and return_factor > 0 else 0.0)
                or curve_start
            )
            summaries.append(
                {
                    "account": label,
                    "baseline": effective_baseline,
                    "current_value": current_value,
                    "return_amount": current_value - effective_baseline,
                    "return_pct": broker_return_pct,
                    "point_count": len(points),
                }
            )
        except Exception as exc:
            collector_errors[f"performance:{label}"] = _collector_error(exc)
    if not histories:
        return pd.DataFrame(columns=["timestamp", "portfolio"]), summaries
    merged = histories[0]
    for history in histories[1:]:
        merged = merged.merge(history, on="timestamp", how="outer")
    merged = merged.sort_values("timestamp")
    account_columns = [column for column in merged.columns if column != "timestamp"]
    merged[account_columns] = merged[account_columns].apply(pd.to_numeric, errors="coerce").ffill()
    merged["portfolio"] = merged[account_columns].sum(axis=1, min_count=1)
    return merged[["timestamp", "portfolio"]].dropna(), summaries


def _collect_benchmarks(
    client: RobinhoodAppClient,
    timeseries: pd.DataFrame,
    collector_errors: dict[str, str],
) -> pd.DataFrame:
    if timeseries.empty:
        return timeseries
    try:
        benchmark_rows = client.benchmark_history()
        benchmarks = pd.DataFrame(benchmark_rows)
        if not {"begins_at", "close_price", "symbol"}.issubset(benchmarks.columns):
            return timeseries
        benchmarks["timestamp"] = pd.to_datetime(benchmarks["begins_at"], utc=True, errors="coerce")
        benchmarks["close_price"] = pd.to_numeric(benchmarks["close_price"], errors="coerce")
        pivot = benchmarks.pivot_table(
            index="timestamp", columns="symbol", values="close_price", aggfunc="last"
        ).reset_index()
        merged = timeseries.merge(pivot, on="timestamp", how="left").sort_values("timestamp")
        benchmark_columns = [column for column in merged.columns if column not in {"timestamp", "portfolio"}]
        merged[benchmark_columns] = merged[benchmark_columns].ffill()
        return merged
    except Exception as exc:
        collector_errors["benchmarks"] = _collector_error(exc)
        return timeseries


def _equity_rows(live_positions: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for position in live_positions:
        quantity = position["quantity"]
        current = position["current_price"]
        average = position["average_buy_price"]
        previous = position["previous_close"]
        market_value = quantity * current
        cost_basis = quantity * average
        unrealized = market_value - cost_basis
        daily_pl = quantity * (current - previous) if previous else Decimal("0")
        common = {
            "symbol": position["symbol"],
            "qty": float(quantity),
            "avg_entry_price": float(average),
            "current_price": float(current),
            "market_value": float(market_value),
            "unrealized_pl": float(unrealized),
            "unrealized_plpc": float(unrealized / abs(cost_basis)) if cost_basis else 0.0,
            "change_today": float((current / previous) - 1) if previous else 0.0,
        }
        rows.append(common)
        details.append(
            {
                **common,
                "asset_class": "equity",
                "account": position["account_label"],
                "position_key": position["position_key"],
                "direction": position["direction"],
                "available_quantity": float(position["available_quantity"]),
                "cost_basis": float(cost_basis),
                "daily_pl": float(daily_pl),
            }
        )
    positions = pd.DataFrame(rows)
    if positions.empty:
        return positions, details
    positions = positions.groupby("symbol", as_index=False).agg(
        qty=("qty", "sum"),
        market_value=("market_value", "sum"),
        unrealized_pl=("unrealized_pl", "sum"),
        current_price=("current_price", "last"),
        change_today=("change_today", "last"),
    )
    cost = positions["market_value"] - positions["unrealized_pl"]
    positions["avg_entry_price"] = (cost / positions["qty"]).where(positions["qty"] != 0, 0.0)
    positions["unrealized_plpc"] = (positions["unrealized_pl"] / cost.abs()).where(cost != 0, 0.0)
    return positions.sort_values("market_value", ascending=False).reset_index(drop=True), details


def _collect_options(
    client: RobinhoodAppClient,
    connection_id: str,
    accounts: list[dict[str, Any]],
    collector_errors: dict[str, str],
    collector_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    aggregates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    option_ids: list[str] = []
    for account in accounts:
        account_number = str(account.get("account_number") or "").strip()
        label = _account_label(account)
        try:
            for position in client.option_positions(account_number):
                aggregates.append((account, position))
                option_ids.extend(
                    str(leg.get("option_id") or "").strip()
                    for leg in list(position.get("legs") or [])
                    if isinstance(leg, dict) and leg.get("option_id")
                )
        except Exception as exc:
            collector_errors[f"options:{label}"] = _collector_error(exc)
    try:
        market = client.option_market_data(option_ids)
    except Exception as exc:
        collector_errors["option_market_data"] = _collector_error(exc)
        market = {}

    rows: list[dict[str, Any]] = []
    for account, position in aggregates:
        account_number = str(account.get("account_number") or "").strip()
        quantity = abs(_number(position.get("quantity")))
        if quantity == 0:
            continue
        strategy = str(position.get("strategy") or "option").replace("_", " ")
        symbol = str(position.get("symbol") or "").upper().strip()
        leg_rows: list[dict[str, Any]] = []
        market_value = 0.0
        daily_pl = 0.0
        greek_totals = {name: 0.0 for name in ("delta", "gamma", "theta", "vega", "rho")}
        implied_volatilities: list[float] = []
        open_interest = 0.0
        volume = 0.0
        has_short = False
        has_long = False
        for leg in list(position.get("legs") or []):
            if not isinstance(leg, dict):
                continue
            option_id = str(leg.get("option_id") or "").strip()
            leg_key = _opaque_key(connection_id, account_number, "option", option_id)
            if option_id and collector_state is not None:
                collector_state.setdefault("option_legs", {})[leg_key] = option_id
            quote = market.get(option_id, {})
            leg_side = str(leg.get("position_type") or "").lower().strip()
            if leg_side not in {"long", "short"}:
                leg_side = "short" if strategy.lower().startswith("short") else "long"
            has_short = has_short or leg_side == "short"
            has_long = has_long or leg_side == "long"
            sign = -1.0 if leg_side == "short" else 1.0
            ratio = abs(_number(leg.get("ratio_quantity"), 1.0)) or 1.0
            mark = _number(quote.get("adjusted_mark_price") or quote.get("mark_price"))
            previous_mark = _number(quote.get("previous_close_price"))
            leg_value = sign * mark * 100.0 * quantity * ratio
            market_value += leg_value
            if previous_mark:
                daily_pl += sign * (mark - previous_mark) * 100.0 * quantity * ratio
            implied_volatility = _number(quote.get("implied_volatility"))
            if implied_volatility:
                implied_volatilities.append(implied_volatility)
            open_interest += _number(quote.get("open_interest")) * ratio
            volume += _number(quote.get("volume")) * ratio
            exposures: dict[str, float] = {}
            for greek in greek_totals:
                exposure = sign * _number(quote.get(greek)) * 100.0 * quantity * ratio
                greek_totals[greek] += exposure
                exposures[greek] = exposure
            leg_rows.append(
                {
                    "leg_key": leg_key,
                    "side": leg_side,
                    "option_type": str(leg.get("option_type") or ""),
                    "strike": _number(leg.get("strike_price")),
                    "expiration": str(leg.get("expiration_date") or ""),
                    "ratio": ratio,
                    "mark": mark,
                    "bid": _number(quote.get("bid_price")),
                    "ask": _number(quote.get("ask_price")),
                    "implied_volatility": implied_volatility,
                    "open_interest": _number(quote.get("open_interest")),
                    "volume": _number(quote.get("volume")),
                    **exposures,
                }
            )
        average_open = abs(_number(position.get("average_open_price")))
        opening_value = average_open * quantity
        direction = "spread" if has_short and has_long else "short" if has_short else "long"
        credit = bool(leg_rows and leg_rows[0].get("side") == "short")
        signed_cost = -opening_value if credit else opening_value
        unrealized = market_value - signed_cost
        identifiers = ",".join(
            sorted(
                str(leg.get("option_id") or "")
                for leg in list(position.get("legs") or [])
                if isinstance(leg, dict)
            )
        )
        rows.append(
            {
                "asset_class": "option",
                "account": _account_label(account),
                "position_key": _opaque_key(connection_id, account_number, "option_strategy", identifiers),
                "symbol": symbol,
                "description": str(position.get("detail_display_name") or strategy),
                "strategy": strategy,
                "direction": direction,
                "quantity": quantity,
                "average_open_price": average_open,
                "cost_basis": signed_cost,
                "market_value": market_value,
                "daily_pl": daily_pl,
                "unrealized_pl": unrealized,
                "unrealized_plpc": unrealized / abs(signed_cost) if signed_cost else 0.0,
                "implied_volatility": sum(implied_volatilities) / len(implied_volatilities) if implied_volatilities else 0.0,
                "open_interest": open_interest,
                "volume": volume,
                "expirations": sorted({str(leg.get("expiration") or "") for leg in leg_rows if leg.get("expiration")}),
                **greek_totals,
                "legs": leg_rows,
            }
        )
    return rows


def _collect_futures(
    client: RobinhoodAppClient,
    connection_id: str,
    accounts: list[dict[str, Any]],
    collector_errors: dict[str, str],
) -> list[dict[str, Any]]:
    try:
        futures_accounts = client.futures_accounts()
    except Exception as exc:
        collector_errors["futures_accounts"] = _collector_error(exc)
        return []
    labels = {str(account.get("account_number") or ""): _account_label(account) for account in accounts}
    rows: list[dict[str, Any]] = []
    for futures_account in futures_accounts:
        account_id = str(futures_account.get("id") or "").strip()
        rhs_number = str(
            futures_account.get("rhsAccountNumber")
            or futures_account.get("rhs_account_number")
            or ""
        ).strip()
        if not account_id:
            continue
        try:
            positions = client.futures_positions(account_id)
        except Exception as exc:
            collector_errors[f"futures:{labels.get(rhs_number, _mask_account(rhs_number))}"] = _collector_error(exc)
            continue
        try:
            cost_basis = client.futures_cost_basis(account_id)
        except Exception as exc:
            collector_errors[f"futures_cost:{labels.get(rhs_number, _mask_account(rhs_number))}"] = _collector_error(exc)
            cost_basis = {}
        contract_ids = [
            str(
                position.get("contractId")
                or position.get("contract_id")
                or (
                    position.get("contract", {}).get("id")
                    if isinstance(position.get("contract"), dict)
                    else ""
                )
                or ""
            ).strip()
            for position in positions
            if isinstance(position, dict)
        ]
        try:
            market_data = client.futures_market_data(contract_ids)
        except Exception as exc:
            collector_errors[f"futures_quotes:{labels.get(rhs_number, _mask_account(rhs_number))}"] = _collector_error(exc)
            market_data = {}
        contract_metadata: dict[str, dict[str, Any]] = {}
        unresolved_contracts = 0
        for contract_id in contract_ids:
            quote = market_data.get(contract_id, {})
            raw_symbol = str(quote.get("symbol") or "").strip()
            normalized_symbol = raw_symbol.upper().lstrip("/").split(":", 1)[0]
            if not normalized_symbol:
                unresolved_contracts += 1
                continue
            try:
                contract_metadata[contract_id] = client.futures_contract(normalized_symbol)
            except Exception as exc:
                collector_errors[f"futures_contract:{normalized_symbol}"] = _collector_error(exc)
                contract_metadata[contract_id] = {}
        if unresolved_contracts:
            collector_errors[
                f"futures_unresolved:{labels.get(rhs_number, _mask_account(rhs_number))}"
            ] = f"{unresolved_contracts} futures position(s) had no resolvable market contract."
        for position in positions:
            contract_id = str(
                position.get("contractId")
                or position.get("contract_id")
                or ""
            ).strip()
            basis = cost_basis.get(contract_id) if isinstance(cost_basis.get(contract_id), dict) else {}
            quote = market_data.get(contract_id, {})
            contract = contract_metadata.get(contract_id, {})
            raw_symbol = str(
                contract.get("displaySymbol")
                or contract.get("symbol")
                or quote.get("symbol")
                or ""
            ).strip()
            symbol = raw_symbol.upper().lstrip("/").split(":", 1)[0]
            if not symbol:
                continue
            quantity = _amount(
                basis.get("signedQuantity")
                or position.get("quantity")
                or position.get("netQuantity")
                or position.get("net_quantity")
            )
            average = _amount(
                basis.get("avgTradePrice")
                or basis.get("averagePrice")
                or basis.get("average_price")
                or basis.get("averageCost")
                or position.get("avgTradePrice")
                or position.get("averagePrice")
                or position.get("average_price")
                or position.get("averageCost")
            )
            open_basis_value = basis.get("openPnlCostBasis")
            open_basis = _amount(open_basis_value)
            inferred_multiplier = (
                abs(open_basis / (average * quantity))
                if open_basis_value is not None and average and quantity
                else 0.0
            )
            multiplier = _amount(
                contract.get("multiplier")
                or position.get("multiplier")
                or basis.get("multiplier"),
                inferred_multiplier or 1.0,
            ) or inferred_multiplier or 1.0
            bid = _amount(quote.get("bid_price"))
            ask = _amount(quote.get("ask_price"))
            current = (
                (bid + ask) / 2.0
                if bid > 0 and ask > 0
                else _amount(
                    quote.get("mark_price")
                    or quote.get("last_trade_price")
                    or quote.get("mid_price")
                    or position.get("markPrice")
                    or position.get("mark_price")
                    or position.get("lastPrice")
                    or position.get("last_price")
                )
            )
            signed_notional = quantity * current * multiplier
            market_value = _amount(
                position.get("marketValue") or position.get("market_value"), signed_notional
            )
            unrealized_source = (
                basis.get("unrealizedPnl")
                or basis.get("unrealized_pnl")
                or basis.get("unrealizedProfitLoss")
                or position.get("unrealizedPnl")
                or position.get("unrealized_pnl")
                or position.get("unrealizedProfitLoss")
            )
            unrealized = (
                _amount(unrealized_source)
                if unrealized_source is not None
                else signed_notional - open_basis
                if open_basis_value is not None
                else (current - average) * quantity * multiplier
            )
            realized_source = (
                basis.get("realizedPnl")
                or basis.get("realized_pnl")
                or position.get("realizedPnl")
            )
            realized = _amount(realized_source) if realized_source is not None else None
            day_basis_value = basis.get("dayPnlCostBasis")
            day_basis = _amount(day_basis_value)
            previous = (
                day_basis / (quantity * multiplier)
                if day_basis_value is not None and quantity and multiplier
                else _amount(quote.get("previous_close_price") or quote.get("previous_close"))
            )
            daily_pl = (
                signed_notional - day_basis
                if day_basis_value is not None
                else (current - previous) * quantity * multiplier
                if current and previous
                else 0.0
            )
            rows.append(
                {
                    "asset_class": "future",
                    "account": labels.get(rhs_number, _mask_account(rhs_number)),
                    "position_key": _opaque_key(connection_id, rhs_number, "future", symbol),
                    "symbol": symbol,
                    "description": str(contract.get("description") or position.get("description") or symbol),
                    "direction": "short" if quantity < 0 else "long",
                    "quantity": quantity,
                    "average_open_price": average,
                    "current_price": current,
                    "bid_price": bid,
                    "ask_price": ask,
                    "multiplier": multiplier,
                    "cost_basis": open_basis,
                    "market_value": market_value,
                    "daily_pl": daily_pl,
                    "unrealized_pl": unrealized,
                    "realized_pl": realized,
                    "unrealized_plpc": unrealized / abs(open_basis) if open_basis else 0.0,
                    "change_today": current / previous - 1.0 if current and previous else 0.0,
                    "expiration": str(
                        contract.get("expiration")
                        or contract.get("expirationDate")
                        or contract.get("expiration_date")
                        or ""
                    ),
                }
            )
    return rows


def _collect_opportunity_sets(
    client: RobinhoodAppClient,
    connection_id: str,
    collector_errors: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, loader in (("watchlists", client.watchlists), ("popular_lists", client.popular_watchlists)):
        try:
            watchlists = loader()
        except Exception as exc:
            collector_errors[source] = _collector_error(exc)
            continue
        for watchlist in watchlists:
            identity = watchlist.get("id") or watchlist.get("list_id") or watchlist.get("url")
            kind = "curated list" if source == "popular_lists" else "watchlist"
            rows.append(
                {
                    "set_key": _opaque_key(connection_id, "", kind, identity),
                    "kind": kind,
                    "name": str(
                        watchlist.get("display_name")
                        or watchlist.get("name")
                        or watchlist.get("title")
                        or "Watchlist"
                    ),
                    "item_count": int(_number(watchlist.get("item_count") or watchlist.get("count"))),
                    "_provider_identity": str(identity or ""),
                }
            )
    try:
        for screener in client.saved_screeners():
            configuration = screener.get("activeScanConfiguration")
            columns = configuration.get("columns") if isinstance(configuration, dict) else []
            identity = screener.get("id") or screener.get("scanId")
            rows.append(
                {
                    "set_key": _opaque_key(connection_id, "", "screener", identity),
                    "kind": "screener",
                    "name": str(screener.get("title") or "Saved screener"),
                    "item_count": int(_number(screener.get("columnCount"), len(columns or []))),
                    "_provider_identity": str(identity or ""),
                    "_definition": _json_safe(configuration if isinstance(configuration, dict) else {}),
                }
            )
    except Exception as exc:
        collector_errors["screeners"] = _collector_error(exc)
    return rows


def _normalized_snapshot(
    client: RobinhoodAppClient,
    connection: dict[str, Any],
    *,
    include_performance: bool = True,
    include_opportunities: bool = True,
    previous_details: dict[str, Any] | None = None,
    collector_state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    accounts = client.accounts()
    if not accounts:
        raise RobinhoodMainError("Robinhood returned no active investing accounts.")
    connection_id = str(connection.get("id") or "")
    collector_errors: dict[str, str] = {}
    summaries: list[dict[str, Any]] = []
    for account in accounts:
        label = _account_label(account)
        try:
            summaries.append({"account": account, "portfolio": client.portfolio(str(account.get("account_number") or ""))})
        except Exception as exc:
            collector_errors[f"portfolio:{label}"] = _collector_error(exc)
            summaries.append({"account": account, "portfolio": {}})

    if include_performance:
        timeseries, performance_accounts = _collect_performance(client, accounts, collector_errors)
        timeseries = _collect_benchmarks(client, timeseries, collector_errors)
    else:
        timeseries = pd.DataFrame(columns=["timestamp", "portfolio"])
        performance_accounts = list((previous_details or {}).get("performance_accounts") or [])
    try:
        live_positions = _live_positions(client, connection_id, accounts)
    except Exception as exc:
        collector_errors["equities"] = _collector_error(exc)
        live_positions = []
    positions, equity_details = _equity_rows(live_positions)
    option_details = _collect_options(
        client,
        connection_id,
        accounts,
        collector_errors,
        collector_state=collector_state,
    )
    futures_details = _collect_futures(client, connection_id, accounts, collector_errors)
    if include_opportunities:
        opportunity_internal = _collect_opportunity_sets(client, connection_id, collector_errors)
        if collector_state is not None:
            collector_state["opportunity_sets"] = opportunity_internal
        opportunity_sets = [
            {key: value for key, value in item.items() if not str(key).startswith("_")}
            for item in opportunity_internal
        ]
    else:
        opportunity_sets = list((previous_details or {}).get("opportunity_sets") or [])

    def total(field: str, *, source: str) -> float:
        return float(sum(_number(item[source].get(field)) for item in summaries))

    equity = total("equity", source="portfolio")
    if not equity and not timeseries.empty:
        equity = float(timeseries.iloc[-1]["portfolio"])
    day_pl = sum(_number(item.get("daily_pl")) for item in equity_details)
    day_pl += sum(_number(item.get("daily_pl")) for item in option_details + futures_details)
    all_holdings = equity_details + option_details + futures_details
    total_pl = sum(_number(item.get("unrealized_pl")) for item in all_holdings)
    market_values = [_number(item.get("market_value")) for item in all_holdings]
    summary = {
        "status": "active",
        "equity": equity,
        "cash": total("portfolio_cash", source="account"),
        "portfolio_value": equity,
        "buying_power": total("buying_power", source="account"),
        "day_pl": day_pl,
        "total_unrealized_pl": total_pl,
        "gross_long_exposure": sum(value for value in market_values if value > 0),
        "gross_short_exposure": abs(sum(value for value in market_values if value < 0)),
        "net_exposure": sum(market_values),
        "daytrade_count": int(sum(int(item["account"].get("day_trade_count") or 0) for item in summaries)),
        "account_count": len(accounts),
        "equity_position_count": len(equity_details),
        "option_position_count": len(option_details),
        "futures_position_count": len(futures_details),
    }
    details = {
        "accounts": [
            {
                "label": _account_label(account),
                "type": str(account.get("type") or "investing"),
                "account_key": _opaque_key(
                    connection_id,
                    account.get("account_number"),
                    "account",
                    "trading",
                ),
                "buying_power": _number(account.get("buying_power")),
            }
            for account in accounts
        ],
        "holdings": equity_details + option_details + futures_details,
        "performance_accounts": performance_accounts,
        "opportunity_sets": opportunity_sets,
        "capabilities": {
            "equities": not any(key == "equities" for key in collector_errors),
            "options": not any(key.startswith("option") for key in collector_errors),
            "futures": not any(key.startswith("futures") for key in collector_errors),
            "performance": (
                bool((previous_details or {}).get("capabilities", {}).get("performance"))
                if not include_performance
                else not timeseries.empty
                and not any(key.startswith("performance:") for key in collector_errors)
            ),
            "screeners": (
                bool((previous_details or {}).get("capabilities", {}).get("screeners"))
                if not include_opportunities
                else not any(key == "screeners" for key in collector_errors)
            ),
        },
        "collector_errors": collector_errors,
    }
    return (
        summary,
        positions,
        timeseries.dropna(subset=["timestamp", "portfolio"]).reset_index(drop=True),
        details,
    )


def _collector_section_failed(errors: dict[str, Any], section: str) -> bool:
    keys = tuple(str(key) for key in errors)
    if section == "equities":
        return "equities" in keys
    if section == "options":
        return any(key == "option_market_data" or key.startswith("options:") for key in keys)
    if section == "futures":
        return any(key.startswith("futures") for key in keys)
    if section == "performance":
        return any(key.startswith("performance:") for key in keys)
    if section == "opportunities":
        return any(key in {"watchlists", "popular_lists", "screeners"} for key in keys)
    if section == "account_summary":
        return any(key.startswith("portfolio:") for key in keys)
    return False


def _preserve_failed_snapshot_sections(
    *,
    summary: dict[str, Any],
    positions: pd.DataFrame,
    timeseries: pd.DataFrame,
    details: dict[str, Any],
    previous_snapshot: dict[str, Any],
    include_performance: bool,
    include_opportunities: bool,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Keep each last-good local section when its current collector is incomplete."""
    previous_summary = dict(previous_snapshot.get("account_summary") or {})
    previous_details = dict(previous_snapshot.get("details") or {})
    previous_holdings = [
        dict(item)
        for item in list(previous_details.get("holdings") or [])
        if isinstance(item, dict)
    ]
    current_holdings = [
        dict(item)
        for item in list(details.get("holdings") or [])
        if isinstance(item, dict)
    ]
    errors = dict(details.get("collector_errors") or {})
    previous_capabilities = dict(previous_details.get("capabilities") or {})
    stale_sections = {
        str(item)
        for item in list(previous_details.get("stale_sections") or [])
        if str(item)
    }
    capabilities = dict(details.get("capabilities") or {})

    for section, asset_class in (
        ("equities", "equity"),
        ("options", "option"),
        ("futures", "future"),
    ):
        if _collector_section_failed(errors, section):
            current_holdings = [
                item for item in current_holdings if str(item.get("asset_class") or "") != asset_class
            ]
            current_holdings.extend(
                item
                for item in previous_holdings
                if str(item.get("asset_class") or "") == asset_class
            )
            stale_sections.add(section)
            capabilities[section] = bool(
                any(str(item.get("asset_class") or "") == asset_class for item in previous_holdings)
            )
            if section == "equities":
                positions = pd.DataFrame(list(previous_snapshot.get("positions") or []))
        else:
            stale_sections.discard(section)

    if include_performance:
        if _collector_section_failed(errors, "performance"):
            timeseries = pd.DataFrame(columns=["timestamp", "portfolio"])
            details["performance_accounts"] = list(previous_details.get("performance_accounts") or [])
            capabilities["performance"] = bool(
                previous_capabilities.get("performance")
            )
            stale_sections.add("performance")
        else:
            stale_sections.discard("performance")

    if include_opportunities:
        if _collector_section_failed(errors, "opportunities"):
            details["opportunity_sets"] = list(previous_details.get("opportunity_sets") or [])
            capabilities["screeners"] = bool(
                previous_capabilities.get("screeners")
            )
            stale_sections.add("opportunities")
        else:
            stale_sections.discard("opportunities")

    if _collector_section_failed(errors, "account_summary"):
        for field in ("equity", "portfolio_value"):
            if field in previous_summary:
                summary[field] = previous_summary[field]
        stale_sections.add("account_summary")
    else:
        stale_sections.discard("account_summary")

    market_values = [_number(item.get("market_value")) for item in current_holdings]
    summary.update(
        {
            "day_pl": sum(_number(item.get("daily_pl")) for item in current_holdings),
            "total_unrealized_pl": sum(
                _number(item.get("unrealized_pl")) for item in current_holdings
            ),
            "gross_long_exposure": sum(value for value in market_values if value > 0),
            "gross_short_exposure": abs(sum(value for value in market_values if value < 0)),
            "net_exposure": sum(market_values),
            "equity_position_count": sum(
                str(item.get("asset_class") or "") == "equity" for item in current_holdings
            ),
            "option_position_count": sum(
                str(item.get("asset_class") or "") == "option" for item in current_holdings
            ),
            "futures_position_count": sum(
                str(item.get("asset_class") or "") == "future" for item in current_holdings
            ),
        }
    )
    details["holdings"] = current_holdings
    details["capabilities"] = capabilities
    details["stale_sections"] = sorted(stale_sections)
    return summary, positions, timeseries, details


def _resource_is_fresh(resource: dict[str, Any] | None, now: datetime) -> bool:
    if not resource or str(resource.get("status") or "") != "ready":
        return False
    refresh_after = pd.to_datetime(resource.get("refresh_after"), utc=True, errors="coerce")
    return bool(pd.notna(refresh_after) and refresh_after.to_pydatetime() > now)


def _watchlist_symbols(
    client: RobinhoodAppClient,
    provider_identity: str,
    instrument_cache: dict[str, dict[str, Any]],
) -> list[str]:
    symbols: set[str] = set()
    unresolved_ids: set[str] = set()
    for item in client.watchlist_items(provider_identity):
        instrument = item.get("instrument") if isinstance(item.get("instrument"), dict) else {}
        symbol = str(item.get("symbol") or instrument.get("symbol") or "").upper().strip()
        if symbol:
            symbols.add(symbol)
            continue
        object_type = str(item.get("object_type") or "").lower()
        object_id = str(item.get("object_id") or item.get("instrument_id") or "").strip()
        if object_id and object_type in {"", "instrument", "equity", "stock"}:
            unresolved_ids.add(object_id)
    for instrument_id in sorted(unresolved_ids):
        try:
            if instrument_id not in instrument_cache:
                instrument_cache[instrument_id] = client.instrument(
                    f"{RESOURCE_URL}/instruments/{instrument_id}/"
                )
            symbol = str(instrument_cache[instrument_id].get("symbol") or "").upper().strip()
            if symbol:
                symbols.add(symbol)
        except RobinhoodMainError:
            continue
    return sorted(symbols)


def _persist_opportunity_data(
    *,
    client: RobinhoodAppClient,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    opportunity_sets: list[dict[str, Any]],
    source_updated_at: datetime,
    collector_errors: dict[str, str],
) -> dict[str, int]:
    instrument_cache: dict[str, dict[str, Any]] = {}
    memberships: dict[str, list[str]] = {}
    existing_payloads: dict[str, dict[str, Any]] = {}
    public_catalog: list[dict[str, Any]] = []
    for item in opportunity_sets:
        public = {key: value for key, value in item.items() if not str(key).startswith("_")}
        public_catalog.append(public)
        set_key = str(item.get("set_key") or "")
        existing = robinhood_main_store.get_resource(
            portfolio_id=portfolio_id,
            environment=environment,
            resource_kind=OPPORTUNITY_GROUP_RESOURCE,
            resource_key=set_key,
        )
        existing_payloads[set_key] = dict((existing or {}).get("payload") or {})
        if _resource_is_fresh(existing, source_updated_at):
            existing_payload = existing_payloads[set_key]
            if str(existing_payload.get("status") or "") == "ready":
                memberships[set_key] = [
                    str(member.get("symbol") or "").upper()
                    for member in list(existing_payload.get("members") or [])
                    if isinstance(member, dict) and member.get("symbol")
                ]
            continue
        if str(item.get("kind") or "") == "screener":
            robinhood_main_store.upsert_resource(
                portfolio_id=portfolio_id,
                connection_id=connection_id,
                environment=environment,
                resource_kind=OPPORTUNITY_GROUP_RESOURCE,
                resource_key=set_key,
                payload={
                    **public,
                    "members": [],
                    "definition": dict(item.get("_definition") or {}),
                    "status": "definition_only",
                },
                source_updated_at=source_updated_at,
                refresh_after=source_updated_at + timedelta(days=1),
            )
            continue
        provider_identity = str(item.get("_provider_identity") or "")
        try:
            memberships[set_key] = _watchlist_symbols(client, provider_identity, instrument_cache)
        except Exception as exc:
            message = _collector_error(exc)
            collector_errors[f"opportunity:{public.get('name', set_key)}"] = message
            robinhood_main_store.mark_resource_error(
                portfolio_id=portfolio_id,
                environment=environment,
                resource_kind=OPPORTUNITY_GROUP_RESOURCE,
                resource_key=set_key,
                error=message,
                retry_after=source_updated_at + timedelta(hours=2),
            )

    for item in opportunity_sets:
        set_key = str(item.get("set_key") or "")
        if set_key not in memberships:
            continue
        public = {key: value for key, value in item.items() if not str(key).startswith("_")}
        existing_members = {
            str(member.get("symbol") or "").upper(): dict(member)
            for member in list(existing_payloads.get(set_key, {}).get("members") or [])
            if isinstance(member, dict) and member.get("symbol")
        }
        members = [
            {
                "symbol": symbol,
                "current_price": existing_members.get(symbol, {}).get("current_price"),
                "change_today": existing_members.get(symbol, {}).get("change_today"),
            }
            for symbol in memberships[set_key]
        ]
        analytics_status = (
            "pending"
            if len(members) <= _opportunity_group_member_limit()
            else "metadata_only"
        )
        previous_status = str(existing_payloads.get(set_key, {}).get("analytics_status") or "")
        if previous_status == "ready" and analytics_status == "pending":
            analytics_status = "ready"
        robinhood_main_store.upsert_resource(
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            resource_kind=OPPORTUNITY_GROUP_RESOURCE,
            resource_key=set_key,
            payload={
                **public,
                "members": members,
                "status": "ready",
                "analytics_status": analytics_status,
            },
            source_updated_at=source_updated_at,
            refresh_after=source_updated_at + timedelta(
                days=1 if str(item.get("kind") or "") == "watchlist" else 7
            ),
        )

    robinhood_main_store.upsert_resource(
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        resource_kind=OPPORTUNITY_CATALOG_RESOURCE,
        resource_key="catalog",
        payload={"sets": public_catalog},
        source_updated_at=source_updated_at,
        refresh_after=source_updated_at + timedelta(days=1),
    )
    return {
        "group_count": len(public_catalog),
        "symbol_count": len(
            {
                symbol
                for symbols in memberships.values()
                if len(symbols) <= _opportunity_group_member_limit()
                for symbol in symbols
            }
        ),
        "opportunity_history_points": 0,
    }


def _opportunity_group_member_limit() -> int:
    try:
        configured = int(os.getenv("ROBINHOOD_MAIN_ANALYTICS_GROUP_MAX_MEMBERS") or "100")
    except ValueError:
        configured = 100
    return min(max(configured, 25), 250)


def _is_equity_history_symbol(value: object) -> bool:
    symbol = str(value or "").upper().strip()
    if re.fullmatch(r"[A-Z0-9]{2,10}-(USD|USDT|USDC)", symbol):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol))


def _compact_equity_history(
    *,
    symbol: str,
    points: list[dict[str, Any]],
    source_updated_at: datetime,
) -> list[dict[str, Any]]:
    cutoff = pd.Timestamp(source_updated_at) - pd.DateOffset(years=1)
    weekly: dict[tuple[int, int], dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for point in points:
        timestamp = pd.to_datetime(point.get("begins_at"), utc=True, errors="coerce")
        close = _number(point.get("close_price"))
        if pd.isna(timestamp) or close <= 0:
            continue
        normalized = {
            "series_key": symbol,
            "observed_at": timestamp.to_pydatetime(),
            "values": {
                "open": _number(point.get("open_price")),
                "high": _number(point.get("high_price")),
                "low": _number(point.get("low_price")),
                "close": close,
                "volume": _number(point.get("volume")),
            },
        }
        if timestamp < cutoff:
            calendar = timestamp.isocalendar()
            weekly[(int(calendar.year), int(calendar.week))] = normalized
        else:
            recent.append(normalized)
    return [*weekly.values(), *recent]


def _persist_equity_histories(
    *,
    client: RobinhoodAppClient,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    required_symbols: list[str],
    source_updated_at: datetime,
    collector_errors: dict[str, str],
) -> dict[str, int]:
    marker = robinhood_main_store.get_resource(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind=SYNC_MARKER_RESOURCE,
        resource_key="opportunity_history",
    )
    resources = robinhood_main_store.list_resources(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind=OPPORTUNITY_GROUP_RESOURCE,
    )
    limit = _opportunity_group_member_limit()
    eligible = []
    deferred = []
    for resource in resources:
        payload = dict(resource.get("payload") or {})
        if str(payload.get("status") or "") != "ready":
            continue
        members = [
            dict(item)
            for item in list(payload.get("members") or [])
            if isinstance(item, dict) and item.get("symbol")
        ]
        (eligible if len(members) <= limit else deferred).append((resource, payload, members))
    opportunity_symbols = {
        str(member.get("symbol") or "").upper()
        for _, _, members in eligible
        for member in members
        if _is_equity_history_symbol(member.get("symbol"))
    }
    normalized_required = {
        str(symbol or "").upper().strip()
        for symbol in required_symbols
        if _is_equity_history_symbol(symbol)
    }
    symbols = sorted(opportunity_symbols | normalized_required)
    if not symbols:
        return {"symbol_count": 0, "opportunity_history_points": 0}

    required_fingerprint = hashlib.sha256(
        "\n".join(sorted(normalized_required)).encode("utf-8")
    ).hexdigest()
    marker_payload = dict((marker or {}).get("payload") or {})
    marker_is_fresh = _resource_is_fresh(marker, source_updated_at)
    required_set_changed = marker_payload.get("required_symbols_hash") != required_fingerprint
    coverage = robinhood_main_store.get_series_coverage(
        portfolio_id=portfolio_id,
        environment=environment,
        series_kind=EQUITY_HISTORY_SERIES_KIND,
        series_keys=symbols,
    )
    if marker_is_fresh and not required_set_changed:
        return {
            "symbol_count": len(coverage),
            "opportunity_history_points": 0,
        }
    symbols_to_fetch = (
        sorted(normalized_required - set(coverage))
        if marker_is_fresh
        else symbols
    )

    persisted = 0
    available_symbols: set[str] = set(coverage)
    quotes_by_symbol: dict[str, dict[str, Any]] = {}
    try:
        for index in range(0, len(symbols_to_fetch), 50):
            batch = symbols_to_fetch[index : index + 50]
            batch_quotes = client.quotes(batch)
            quotes_by_symbol.update(batch_quotes)
            valid_batch = sorted(batch_quotes)
            if not valid_batch:
                continue
            normalized: list[dict[str, Any]] = []
            for group in client.stock_history(valid_batch, span="5year"):
                symbol = str(group.get("symbol") or "").upper().strip()
                if not symbol:
                    continue
                available_symbols.add(symbol)
                normalized.extend(
                    _compact_equity_history(
                        symbol=symbol,
                        points=[
                            point
                            for point in list(group.get("historicals") or [])
                            if isinstance(point, dict)
                        ],
                        source_updated_at=source_updated_at,
                    )
                )
            persisted += robinhood_main_store.upsert_series_batch(
                portfolio_id=portfolio_id,
                connection_id=connection_id,
                environment=environment,
                series_kind=EQUITY_HISTORY_SERIES_KIND,
                points=normalized,
                source_updated_at=source_updated_at,
            )
    except Exception as exc:
        message = _collector_error(exc)
        collector_errors["opportunity_history"] = message
        robinhood_main_store.mark_resource_error(
            portfolio_id=portfolio_id,
            environment=environment,
            resource_kind=SYNC_MARKER_RESOURCE,
            resource_key="opportunity_history",
            error=message,
            retry_after=source_updated_at + timedelta(hours=2),
        )
        return {"symbol_count": len(available_symbols), "opportunity_history_points": persisted}

    for resource, payload, members in [*eligible, *deferred]:
        refreshed_members: list[dict[str, Any]] = []
        covered_members = 0
        for member in members:
            symbol = str(member.get("symbol") or "").upper()
            quote = quotes_by_symbol.get(symbol, {})
            current = _number(
                quote.get("last_extended_hours_trade_price")
                or quote.get("last_trade_price")
                or quote.get("ask_price")
                or member.get("current_price")
            )
            previous = _number(
                quote.get("adjusted_previous_close") or quote.get("previous_close")
            )
            refreshed_members.append(
                {
                    **member,
                    "current_price": current or member.get("current_price"),
                    "change_today": (
                        current / previous - 1.0
                        if current and previous
                        else member.get("change_today")
                    ),
                }
            )
            covered_members += symbol in available_symbols
        payload["members"] = refreshed_members
        payload["analytics_status"] = (
            "ready"
            if len(members) <= limit and covered_members >= 2
            else "metadata_only"
            if len(members) > limit
            else "unavailable"
        )
        robinhood_main_store.upsert_resource(
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            resource_kind=OPPORTUNITY_GROUP_RESOURCE,
            resource_key=str(resource.get("resource_key") or ""),
            payload=payload,
            source_updated_at=resource.get("source_updated_at") or source_updated_at,
            refresh_after=resource.get("refresh_after") or source_updated_at,
        )
    robinhood_main_store.upsert_resource(
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        resource_kind=SYNC_MARKER_RESOURCE,
        resource_key="opportunity_history",
        payload={
            "symbol_count": len(symbols),
            "available_symbol_count": len(available_symbols),
            "point_count": persisted,
            "group_member_limit": limit,
            "required_symbol_count": len(normalized_required),
            "required_symbols_hash": required_fingerprint,
        },
        source_updated_at=source_updated_at,
        refresh_after=source_updated_at + timedelta(days=6),
    )
    return {"symbol_count": len(available_symbols), "opportunity_history_points": persisted}


def _persist_option_histories(
    *,
    client: RobinhoodAppClient,
    portfolio_id: str,
    connection_id: str,
    environment: str,
    option_legs: dict[str, str],
    source_updated_at: datetime,
    collector_errors: dict[str, str],
) -> int:
    persisted = 0
    for leg_key, option_id in sorted(option_legs.items()):
        marker = robinhood_main_store.get_resource(
            portfolio_id=portfolio_id,
            environment=environment,
            resource_kind=SYNC_MARKER_RESOURCE,
            resource_key=f"option_history:{leg_key}",
        )
        if _resource_is_fresh(marker, source_updated_at):
            continue
        try:
            points = client.option_history(option_id, span="5year")
            normalized: list[dict[str, Any]] = []
            for point in points:
                timestamp = pd.to_datetime(point.get("begins_at"), utc=True, errors="coerce")
                if pd.isna(timestamp):
                    continue
                normalized.append(
                    {
                        "observed_at": timestamp.to_pydatetime(),
                        "values": {
                            "open": _number(point.get("open_price")),
                            "high": _number(point.get("high_price")),
                            "low": _number(point.get("low_price")),
                            "close": _number(point.get("close_price")),
                            "volume": _number(point.get("volume")),
                        },
                    }
                )
            persisted += robinhood_main_store.upsert_series_points(
                portfolio_id=portfolio_id,
                connection_id=connection_id,
                environment=environment,
                series_kind=OPTION_HISTORY_SERIES_KIND,
                series_key=leg_key,
                points=normalized,
                source_updated_at=source_updated_at,
            )
            robinhood_main_store.upsert_resource(
                portfolio_id=portfolio_id,
                connection_id=connection_id,
                environment=environment,
                resource_kind=SYNC_MARKER_RESOURCE,
                resource_key=f"option_history:{leg_key}",
                payload={"point_count": len(normalized)},
                source_updated_at=source_updated_at,
                refresh_after=source_updated_at + timedelta(days=1),
            )
        except Exception as exc:
            message = _collector_error(exc)
            collector_errors[f"option_history:{leg_key[:8]}"] = message
            robinhood_main_store.mark_resource_error(
                portfolio_id=portfolio_id,
                environment=environment,
                resource_kind=SYNC_MARKER_RESOURCE,
                resource_key=f"option_history:{leg_key}",
                error=message,
                retry_after=source_updated_at + timedelta(hours=2),
            )
    return persisted


def sync_portfolio(*, user_id: str, portfolio_id: str) -> dict[str, Any]:
    connection = _connection_for_user(user_id)
    if not connection:
        raise RobinhoodMainAuthorizationError("Connect Robinhood main accounts first.")
    if not _ensure_data_store():
        raise RobinhoodMainError("Robinhood local data storage is unavailable.")
    environment = environment_name()
    connection_id = str(connection.get("id") or "")
    source_updated_at = _now_utc()
    previous_snapshot = robinhood_main_store.get_snapshot(
        portfolio_id=portfolio_id,
        environment=environment,
    ) or {}
    previous_details = dict(previous_snapshot.get("details") or {})
    performance_marker = robinhood_main_store.get_resource(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind=SYNC_MARKER_RESOURCE,
        resource_key="portfolio_performance",
    )
    catalog_resource = robinhood_main_store.get_resource(
        portfolio_id=portfolio_id,
        environment=environment,
        resource_kind=OPPORTUNITY_CATALOG_RESOURCE,
        resource_key="catalog",
    )
    include_performance = not _resource_is_fresh(performance_marker, source_updated_at)
    include_opportunities = not _resource_is_fresh(catalog_resource, source_updated_at)
    collector_state: dict[str, Any] = {}
    client = _client_from_connection(connection)
    try:
        summary, positions, timeseries, details = _normalized_snapshot(
            client,
            connection,
            include_performance=include_performance,
            include_opportunities=include_opportunities,
            previous_details=previous_details,
            collector_state=collector_state,
        )
    except RobinhoodMainAuthorizationError:
        refreshed_connection = _refresh_connection_tokens(connection)
        client = _client_from_connection(refreshed_connection)
        summary, positions, timeseries, details = _normalized_snapshot(
            client,
            refreshed_connection,
            include_performance=include_performance,
            include_opportunities=include_opportunities,
            previous_details=previous_details,
            collector_state=collector_state,
        )

    summary, positions, timeseries, details = _preserve_failed_snapshot_sections(
        summary=summary,
        positions=positions,
        timeseries=timeseries,
        details=details,
        previous_snapshot=previous_snapshot,
        include_performance=include_performance,
        include_opportunities=include_opportunities,
    )

    performance_points = 0
    if not timeseries.empty:
        performance_points += robinhood_main_store.upsert_series_points(
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            series_kind=PORTFOLIO_SERIES_KIND,
            series_key=PORTFOLIO_SERIES_KEY,
            points=[
                {
                    "observed_at": pd.to_datetime(row.get("timestamp"), utc=True).to_pydatetime(),
                    "values": {
                        key: value
                        for key, value in row.items()
                        if key != "timestamp" and value is not None and not pd.isna(value)
                    },
                }
                for row in _json_rows(timeseries)
            ],
            source_updated_at=source_updated_at,
        )
        robinhood_main_store.upsert_resource(
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            resource_kind=SYNC_MARKER_RESOURCE,
            resource_key="portfolio_performance",
            payload={"point_count": int(len(timeseries))},
            source_updated_at=source_updated_at,
            refresh_after=source_updated_at + timedelta(hours=20),
        )
    current_value = _number(summary.get("portfolio_value") or summary.get("equity"))
    if current_value > 0 and "account_summary" not in set(details.get("stale_sections") or []):
        performance_points += robinhood_main_store.upsert_series_points(
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            series_kind=PORTFOLIO_SERIES_KIND,
            series_key=PORTFOLIO_SERIES_KEY,
            points=[
                {
                    "observed_at": source_updated_at,
                    "values": {"portfolio": current_value, "sample": "local_sync"},
                }
            ],
            source_updated_at=source_updated_at,
        )

    collector_errors = details.get("collector_errors")
    if not isinstance(collector_errors, dict):
        collector_errors = {}
        details["collector_errors"] = collector_errors
    opportunity_result = {
        "group_count": 0,
        "symbol_count": 0,
        "opportunity_history_points": 0,
    }
    if include_opportunities and not _collector_section_failed(collector_errors, "opportunities"):
        opportunity_result = _persist_opportunity_data(
            client=client,
            portfolio_id=portfolio_id,
            connection_id=connection_id,
            environment=environment,
            opportunity_sets=list(collector_state.get("opportunity_sets") or []),
            source_updated_at=source_updated_at,
            collector_errors=collector_errors,
        )
    elif include_opportunities:
        robinhood_main_store.mark_resource_error(
            portfolio_id=portfolio_id,
            environment=environment,
            resource_kind=OPPORTUNITY_CATALOG_RESOURCE,
            resource_key="catalog",
            error="; ".join(
                str(value)
                for key, value in collector_errors.items()
                if key in {"watchlists", "popular_lists", "screeners"}
            ),
            retry_after=source_updated_at + timedelta(hours=2),
        )
    equity_history_symbols = sorted(
        {
            str(holding.get("symbol") or "").upper().strip()
            for holding in list(details.get("holdings") or [])
            if isinstance(holding, dict)
            and str(holding.get("asset_class") or "").lower() == "equity"
            and _is_equity_history_symbol(holding.get("symbol"))
        }
    )
    opportunity_history_result = _persist_equity_histories(
        client=client,
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        required_symbols=equity_history_symbols,
        source_updated_at=source_updated_at,
        collector_errors=collector_errors,
    )
    opportunity_result.update(opportunity_history_result)
    stale_sections = set(details.get("stale_sections") or [])
    if "opportunity_history" in collector_errors:
        stale_sections.add("opportunity_history")
    else:
        stale_sections.discard("opportunity_history")
    details["stale_sections"] = sorted(stale_sections)
    option_history_points = _persist_option_histories(
        client=client,
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        option_legs=dict(collector_state.get("option_legs") or {}),
        source_updated_at=source_updated_at,
        collector_errors=collector_errors,
    )
    snapshot = robinhood_main_store.save_snapshot(
        portfolio_id=portfolio_id,
        connection_id=connection_id,
        environment=environment,
        account_summary=_json_safe(summary),
        positions=_json_rows(positions),
        details=_json_safe(details),
        source_updated_at=source_updated_at,
    )
    brokerage_mcp_store.set_portfolio_brokerage(
        portfolio_id=portfolio_id,
        brokerage_source=PROVIDER,
        brokerage_account_ref=str(connection.get("id") or ""),
    )
    return {
        "status": "ready",
        "account_count": int(summary.get("account_count") or 0),
        "position_count": int(len(positions)),
        "option_position_count": int(summary.get("option_position_count") or 0),
        "futures_position_count": int(summary.get("futures_position_count") or 0),
        "history_points": int(performance_points),
        "option_history_points": int(option_history_points),
        **opportunity_result,
        "collector_errors": dict(details.get("collector_errors") or {}),
        "updated_at": snapshot.get("source_updated_at"),
    }


def portfolio_snapshot(*, user_id: str, portfolio_id: str) -> dict[str, Any] | None:
    if not _ensure_store():
        return None
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id)
    if not portfolio or str(portfolio.get("brokerage_source") or "") != PROVIDER:
        return None
    _portfolio_read_connection(user_id=user_id, portfolio_id=portfolio_id)
    environment = environment_name()
    snapshot = None
    if _ensure_data_store():
        snapshot = robinhood_main_store.get_snapshot(
            portfolio_id=portfolio_id,
            environment=environment,
        )
    if not snapshot:
        return None
    series_rows = (
        robinhood_main_store.get_series_points(
            portfolio_id=portfolio_id,
            environment=environment,
            series_kind=PORTFOLIO_SERIES_KIND,
            series_keys=[PORTFOLIO_SERIES_KEY],
        )
        if _ensure_data_store()
        else []
    )
    if series_rows:
        timeseries = pd.DataFrame(
            [
                {"timestamp": row.get("observed_at"), **dict(row.get("values") or {})}
                for row in series_rows
            ]
        )
        timeseries = timeseries.drop(columns=["sample"], errors="ignore")
        if not timeseries.empty:
            timeseries["timestamp"] = pd.to_datetime(timeseries["timestamp"], utc=True, errors="coerce")
            timeseries = timeseries.dropna(subset=["timestamp"]).sort_values("timestamp")
            value_columns = [column for column in timeseries.columns if column != "timestamp"]
            timeseries[value_columns] = timeseries[value_columns].apply(pd.to_numeric, errors="coerce")
            benchmark_columns = [column for column in value_columns if column != "portfolio"]
            if benchmark_columns:
                timeseries[benchmark_columns] = timeseries[benchmark_columns].ffill()
            timeseries = timeseries.dropna(subset=["portfolio"]).reset_index(drop=True)
    else:
        stored = snapshot.get("timeseries") if isinstance(snapshot, dict) else None
        timeseries = stored.copy() if isinstance(stored, pd.DataFrame) else pd.DataFrame(list(stored or []))
    details = dict(snapshot.get("details") or {})
    if _ensure_data_store():
        catalog = robinhood_main_store.get_resource(
            portfolio_id=portfolio_id,
            environment=environment,
            resource_kind=OPPORTUNITY_CATALOG_RESOURCE,
            resource_key="catalog",
        )
        if catalog and isinstance(catalog.get("payload"), dict):
            group_resources = robinhood_main_store.list_resources(
                portfolio_id=portfolio_id,
                environment=environment,
                resource_kind=OPPORTUNITY_GROUP_RESOURCE,
            )
            group_payloads = {
                str(resource.get("resource_key") or ""): dict(resource.get("payload") or {})
                for resource in group_resources
                if isinstance(resource.get("payload"), dict)
            }
            details["opportunity_sets"] = [
                {
                    **dict(item),
                    "analytics_status": str(
                        group_payloads.get(str(item.get("set_key") or ""), {}).get(
                            "analytics_status"
                        )
                        or "unavailable"
                    ),
                    "member_count": len(
                        list(
                            group_payloads.get(str(item.get("set_key") or ""), {}).get(
                                "members"
                            )
                            or []
                        )
                    ),
                }
                for item in list(catalog["payload"].get("sets") or [])
                if isinstance(item, dict)
            ]
        refresh = robinhood_main_store.latest_refresh(
            portfolio_id=portfolio_id,
            environment=environment,
        )
        if refresh:
            details["refresh"] = {
                key: refresh.get(key)
                for key in ("status", "requested_at", "started_at", "completed_at", "error")
            }
    return {
        **snapshot,
        "account_summary": dict(snapshot.get("account_summary") or {}),
        "positions": pd.DataFrame(list(snapshot.get("positions") or [])),
        "timeseries": timeseries,
        "details": details,
    }


def portfolio_data_status(*, user_id: str, portfolio_id: str) -> dict[str, Any]:
    if not _ensure_store():
        return {"state": "unavailable", "snapshot_ready": False}
    if not auth_store.user_has_active_portfolio_membership(
        user_id=user_id,
        portfolio_id=portfolio_id,
    ):
        raise RobinhoodMainAuthorizationError("The portfolio is not available to this user.")
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id)
    if not portfolio or str(portfolio.get("brokerage_source") or "") != PROVIDER:
        return {"state": "unlinked", "snapshot_ready": False}
    _portfolio_read_connection(user_id=user_id, portfolio_id=portfolio_id)
    if not _ensure_data_store():
        return {"state": "unavailable", "snapshot_ready": False}

    environment = environment_name()
    snapshot = robinhood_main_store.get_snapshot(
        portfolio_id=portfolio_id,
        environment=environment,
    )
    refresh = robinhood_main_store.latest_refresh(
        portfolio_id=portfolio_id,
        environment=environment,
    ) or {}
    refresh_status = str(refresh.get("status") or "").lower()
    if refresh_status in {"queued", "running"}:
        state = "in_progress"
    elif snapshot:
        state = "ready"
    elif refresh_status == "failed":
        state = "failed"
    else:
        state = "waiting"
    return {
        "state": state,
        "snapshot_ready": bool(snapshot),
        "refresh_status": "in_progress" if refresh_status in {"queued", "running"} else refresh_status,
        "requested_at": refresh.get("requested_at"),
        "started_at": refresh.get("started_at"),
        "completed_at": refresh.get("completed_at"),
    }


def request_portfolio_refresh(*, user_id: str, portfolio_id: str, reason: str = "manual") -> dict[str, Any]:
    connection = _owned_portfolio_connection(user_id=user_id, portfolio_id=portfolio_id)
    if not _ensure_data_store():
        raise RobinhoodMainError("Robinhood local data storage is unavailable.")
    return robinhood_main_store.enqueue_refresh(
        user_id=user_id,
        portfolio_id=portfolio_id,
        connection_id=str(connection.get("id") or ""),
        environment=environment_name(),
        reason=reason,
    )


def link_portfolio(*, user_id: str, portfolio_id: str) -> dict[str, Any]:
    _require_portfolio_admin(user_id=user_id, portfolio_id=portfolio_id)
    connection = _connection_for_user(user_id)
    if not connection:
        raise RobinhoodMainAuthorizationError("Connect Robinhood main accounts first.")
    brokerage_mcp_store.set_portfolio_brokerage(
        portfolio_id=portfolio_id,
        brokerage_source=PROVIDER,
        brokerage_account_ref=str(connection.get("id") or ""),
    )
    return request_portfolio_refresh(
        user_id=user_id,
        portfolio_id=portfolio_id,
        reason="initial",
    )


def trading_accounts(*, user_id: str, portfolio_id: str) -> list[dict[str, Any]]:
    _owned_portfolio_connection(user_id=user_id, portfolio_id=portfolio_id)
    snapshot = portfolio_snapshot(user_id=user_id, portfolio_id=portfolio_id)
    details = dict((snapshot or {}).get("details") or {})
    return [
        {
            "account_key": str(account.get("account_key") or ""),
            "label": str(account.get("label") or "Account"),
            "type": str(account.get("type") or "investing"),
            "buying_power": _number(account.get("buying_power")),
        }
        for account in list(details.get("accounts") or [])
        if isinstance(account, dict) and len(str(account.get("account_key") or "")) == 64
    ]


def option_leg_history(
    *,
    user_id: str,
    portfolio_id: str,
    leg_key: str,
    span: str = "week",
) -> list[dict[str, Any]]:
    _portfolio_read_connection(user_id=user_id, portfolio_id=portfolio_id)
    rows = robinhood_main_store.get_series_points(
        portfolio_id=portfolio_id,
        environment=environment_name(),
        series_kind=OPTION_HISTORY_SERIES_KIND,
        series_keys=[str(leg_key or "")],
        start_at=_lookback_start(span),
    )
    return [
        {"timestamp": row.get("observed_at"), **dict(row.get("values") or {})}
        for row in rows
    ]


def _lookback_start(value: str) -> datetime | None:
    normalized = str(value or "").strip().lower()
    now = pd.Timestamp.now(tz="UTC")
    if normalized in {"all", "max"}:
        return None
    if normalized in {"ytd"}:
        return datetime(now.year, 1, 1, tzinfo=timezone.utc)
    offsets = {
        "day": pd.DateOffset(days=1),
        "1d": pd.DateOffset(days=1),
        "week": pd.DateOffset(weeks=1),
        "1w": pd.DateOffset(weeks=1),
        "month": pd.DateOffset(months=1),
        "1m": pd.DateOffset(months=1),
        "3month": pd.DateOffset(months=3),
        "3m": pd.DateOffset(months=3),
        "year": pd.DateOffset(years=1),
        "1y": pd.DateOffset(years=1),
        "5year": pd.DateOffset(years=5),
        "5y": pd.DateOffset(years=5),
    }
    offset = offsets.get(normalized)
    return (now - offset).to_pydatetime() if offset is not None else None


def opportunity_set_snapshot(
    *,
    user_id: str,
    portfolio_id: str,
    set_key: str,
    span: str = "year",
) -> dict[str, Any]:
    _portfolio_read_connection(user_id=user_id, portfolio_id=portfolio_id)
    resource = robinhood_main_store.get_resource(
        portfolio_id=portfolio_id,
        environment=environment_name(),
        resource_kind=OPPORTUNITY_GROUP_RESOURCE,
        resource_key=str(set_key or ""),
    )
    if not resource or not isinstance(resource.get("payload"), dict):
        raise RobinhoodMainPolicyError("That list is no longer available.")
    selected = dict(resource["payload"])
    if selected.get("kind") == "screener":
        return selected
    members = [dict(item) for item in list(selected.get("members") or []) if isinstance(item, dict)]
    symbols = [str(item.get("symbol") or "").upper() for item in members if item.get("symbol")]
    start_at = _lookback_start(span)
    history_rows = robinhood_main_store.get_series_points(
        portfolio_id=portfolio_id,
        environment=environment_name(),
        series_kind=EQUITY_HISTORY_SERIES_KIND,
        series_keys=symbols,
        start_at=start_at,
    )
    frames: list[pd.DataFrame] = []
    returns: dict[str, float] = {}
    for symbol in symbols:
        rows = [row for row in history_rows if row.get("series_key") == symbol]
        frame = pd.DataFrame(
            [
                {
                    "timestamp": row.get("observed_at"),
                    symbol: _number(dict(row.get("values") or {}).get("close")),
                }
                for row in rows
            ]
        )
        if frame.empty:
            continue
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame[symbol] = pd.to_numeric(frame[symbol], errors="coerce")
        frame = frame.dropna(subset=["timestamp", symbol]).sort_values("timestamp")
        frame = frame[frame[symbol] > 0]
        if len(frame) < 2:
            continue
        returns[symbol] = float(frame[symbol].iloc[-1] / frame[symbol].iloc[0] - 1.0)
        frame[symbol] = frame[symbol] / float(frame[symbol].iloc[0]) * 100.0
        frames.append(frame)
    for member in members:
        member["period_return"] = returns.get(str(member.get("symbol") or "").upper())
    performance: list[dict[str, Any]] = []
    if frames:
        merged = frames[0]
        for frame in frames[1:]:
            merged = merged.merge(frame, on="timestamp", how="outer")
        merged = merged.sort_values("timestamp")
        value_columns = [column for column in merged.columns if column != "timestamp"]
        merged[value_columns] = merged[value_columns].ffill()
        merged["equal_weight"] = merged[value_columns].mean(axis=1, skipna=True)
        performance = _json_rows(merged[["timestamp", "equal_weight"]].dropna())
    return {
        **selected,
        "members": members,
        "performance": performance,
    }


def _canonical_equity_orders(orders: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(orders, list) or not orders:
        raise RobinhoodMainPolicyError("Select at least one symbol.")
    canonical: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in orders:
        account_key = str(raw.get("account_key") or "").strip()
        symbol = str(raw.get("symbol") or "").upper().strip()
        side = str(raw.get("side") or "buy").lower().strip()
        quantity = _decimal(raw.get("quantity"), f"{symbol or 'Order'} quantity")
        if len(account_key) != 64 or not symbol or side not in {"buy", "sell_short"}:
            raise RobinhoodMainPolicyError("A basket order is invalid.")
        if side == "sell_short" and quantity != quantity.to_integral_value():
            raise RobinhoodMainPolicyError(f"{symbol} short quantity must be a whole number.")
        identity = (account_key, symbol, side)
        if identity in seen:
            raise RobinhoodMainPolicyError(f"{symbol} appears more than once for the same account and side.")
        seen.add(identity)
        canonical.append(
            {
                "account_key": account_key,
                "symbol": symbol,
                "side": side,
                "quantity": _decimal_text(quantity),
            }
        )
    return sorted(canonical, key=lambda item: (item["account_key"], item["symbol"], item["side"]))


def _resolve_equity_batch(
    connection: dict[str, Any],
    orders: list[dict[str, str]],
) -> tuple[RobinhoodAppClient, list[dict[str, Any]]]:
    client = _client_from_connection(connection)
    connection_id = str(connection.get("id") or "")
    accounts = client.accounts()
    accounts_by_key = {
        _opaque_key(connection_id, account.get("account_number"), "account", "trading"): account
        for account in accounts
    }
    instruments: dict[str, dict[str, Any]] = {}
    quotes = client.quotes([order["symbol"] for order in orders])
    open_orders: dict[str, list[dict[str, Any]]] = {}
    resolved: list[dict[str, Any]] = []
    for order in orders:
        account = accounts_by_key.get(order["account_key"])
        if not account:
            raise RobinhoodMainPolicyError(f"The selected account for {order['symbol']} is unavailable.")
        if order["side"] == "sell_short" and "margin" not in str(account.get("type") or "").lower():
            raise RobinhoodMainPolicyError(f"{order['symbol']} can only be shorted in a margin account.")
        account_number = str(account.get("account_number") or "")
        if account_number not in open_orders:
            open_orders[account_number] = client.open_orders(account_number)
        duplicate = any(
            str(item.get("symbol") or "").upper() == order["symbol"]
            and str(item.get("side") or "").lower() == order["side"]
            for item in open_orders[account_number]
        )
        if duplicate:
            raise RobinhoodMainPolicyError(f"{order['symbol']} already has a matching open order.")
        if order["symbol"] not in instruments:
            instruments[order["symbol"]] = client.instrument_by_symbol(order["symbol"])
        instrument = instruments[order["symbol"]]
        if not bool(instrument.get("tradeable", instrument.get("tradability") == "tradable")):
            raise RobinhoodMainPolicyError(f"{order['symbol']} is not currently tradable.")
        if order["side"] == "sell_short" and str(instrument.get("short_selling_tradability") or "").lower() != "tradable":
            raise RobinhoodMainPolicyError(f"{order['symbol']} is not currently shortable.")
        quote = quotes.get(order["symbol"], {})
        price = _number(
            quote.get("last_extended_hours_trade_price")
            or quote.get("last_trade_price")
            or quote.get("ask_price")
        )
        if price <= 0:
            raise RobinhoodMainPolicyError(f"{order['symbol']} has no usable live quote.")
        resolved.append(
            {
                "order": order,
                "account": account,
                "account_label": _account_label(account),
                "instrument": instrument,
                "quote": quote,
                "price": price,
            }
        )
    return client, resolved


def review_batch_equity_orders(
    *,
    user_id: str,
    portfolio_id: str,
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMainPolicyError("Main-account execution is disabled in this environment.")
    connection = _owned_portfolio_connection(user_id=user_id, portfolio_id=portfolio_id)
    canonical = _canonical_equity_orders(orders)
    _, resolved = _resolve_equity_batch(connection, canonical)
    summary = [
        {
            "account": item["account_label"],
            "symbol": item["order"]["symbol"],
            "side": "Short" if item["order"]["side"] == "sell_short" else "Buy",
            "quantity": item["order"]["quantity"],
            "reference_price": item["price"],
            "estimated_value": item["price"] * _number(item["order"]["quantity"]),
        }
        for item in resolved
    ]
    ticket = brokerage_mcp_store.create_order_ticket(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        review_tool_name=BASKET_REVIEW_TOOL,
        execution_tool_name=BASKET_EXECUTION_TOOL,
        arguments_hash=_arguments_hash(canonical),
        ref_id=str(uuid.uuid4()),
        request_summary={
            "portfolio_id": portfolio_id,
            "orders": summary,
            "order_count": len(summary),
        },
        expires_at=_now_utc() + timedelta(seconds=_ticket_ttl_seconds()),
    )
    return {
        "ticket_id": str(ticket.get("id") or ""),
        "expires_at": ticket.get("expires_at"),
        "orders": summary,
        "canonical_orders": canonical,
    }


def confirm_batch_equity_orders(
    *,
    user_id: str,
    ticket_id: str,
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMainPolicyError("Main-account execution is disabled in this environment.")
    canonical = _canonical_equity_orders(orders)
    ticket = brokerage_mcp_store.consume_order_ticket(
        ticket_id=ticket_id,
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        arguments_hash=_arguments_hash(canonical),
    )
    if not ticket:
        raise RobinhoodMainPolicyError("This basket review expired, changed, or was already used.")
    reviewed_portfolio_id = str((ticket.get("request_summary") or {}).get("portfolio_id") or "")
    _require_portfolio_admin(user_id=user_id, portfolio_id=reviewed_portfolio_id)
    connection = brokerage_mcp_store.get_connection_by_id(
        connection_id=str(ticket.get("connection_id") or ""),
        environment=environment_name(),
    )
    if not connection:
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="failed", error="Connection unavailable.")
        raise RobinhoodMainAuthorizationError("Robinhood main-account connection is unavailable.")
    try:
        client, resolved = _resolve_equity_batch(connection, canonical)
    except Exception as exc:
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="failed", error=str(exc))
        raise
    audit_id = brokerage_mcp_store.start_audit_event(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        tool_name=BASKET_EXECUTION_TOOL,
        intent="execution",
        request_summary={"order_count": len(canonical), "symbols": [item["symbol"] for item in canonical]},
    )
    results: list[dict[str, Any]] = []
    batch_ref = uuid.UUID(str(ticket.get("ref_id")))
    try:
        for index, item in enumerate(resolved):
            order = item["order"]
            order_ref = str(uuid.uuid5(batch_ref, f"{order['account_key']}:{order['symbol']}:{order['side']}"))
            try:
                response = client.place_equity_order(
                    account=item["account"],
                    instrument=item["instrument"],
                    quote=item["quote"],
                    side=order["side"],
                    quantity=order["quantity"],
                    ref_id=order_ref,
                )
                order_id = str(response.get("id") or "")
                if not order_id:
                    raise RobinhoodMainRequestError(
                        _safe_provider_message(response), status_code=400, response=response
                    )
                results.append(
                    {
                        "symbol": order["symbol"],
                        "account": item["account_label"],
                        "side": order["side"],
                        "status": "submitted",
                        "state": str(response.get("state") or "submitted"),
                        "order_id": order_id,
                    }
                )
            except RobinhoodMainRequestError as exc:
                if exc.status_code == 0:
                    raise
                if exc.status_code == 429:
                    results.append(
                        {
                            "symbol": order["symbol"],
                            "account": item["account_label"],
                            "side": order["side"],
                            "status": "rate_limited",
                            "error": str(exc)[:300],
                        }
                    )
                    results.extend(
                        {
                            "symbol": remaining["order"]["symbol"],
                            "account": remaining["account_label"],
                            "side": remaining["order"]["side"],
                            "status": "skipped",
                            "error": "Batch stopped after Robinhood rate limiting.",
                        }
                        for remaining in resolved[index + 1 :]
                    )
                    break
                results.append(
                    {
                        "symbol": order["symbol"],
                        "account": item["account_label"],
                        "side": order["side"],
                        "status": "rejected",
                        "error": str(exc)[:300],
                    }
                )
    except RobinhoodMainAuthorizationError as exc:
        result_summary = {"results": results, "status": "failed"}
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_id, status="failed", result_summary=result_summary, error=str(exc)
        )
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=ticket_id, status="failed", result_summary=result_summary, error=str(exc)
        )
        raise
    except (RobinhoodMainRequestError, requests.RequestException, TimeoutError) as exc:
        result_summary = {"results": results, "status": "unknown"}
        brokerage_mcp_store.finish_audit_event(
            audit_id=audit_id, status="unknown", result_summary=result_summary, error=str(exc)
        )
        brokerage_mcp_store.finish_order_ticket(
            ticket_id=ticket_id, status="unknown", result_summary=result_summary, error=str(exc)
        )
        raise RobinhoodMainError("Basket status is unknown after a connection failure. Check Robinhood before retrying.") from exc
    submitted = sum(item.get("status") == "submitted" for item in results)
    rejected = len(results) - submitted
    terminal = "executed" if submitted == len(results) else "partial" if submitted else "failed"
    result_summary = {"submitted": submitted, "rejected": rejected, "results": results}
    brokerage_mcp_store.finish_audit_event(
        audit_id=audit_id,
        status="completed" if submitted else "failed",
        result_summary=result_summary,
        error="" if submitted else "Robinhood rejected every basket order.",
    )
    brokerage_mcp_store.finish_order_ticket(
        ticket_id=ticket_id,
        status=terminal,
        result_summary=result_summary,
        error="" if submitted else "Robinhood rejected every basket order.",
    )
    return {"status": terminal, **result_summary}


def disconnect(*, user_id: str, portfolio_id: str) -> bool:
    _require_portfolio_admin(user_id=user_id, portfolio_id=portfolio_id)
    connection = _connection_for_user(user_id)
    disconnected = brokerage_mcp_store.disconnect_connection(
        user_id=user_id,
        provider=PROVIDER,
        resource_url=RESOURCE_URL,
        environment=environment_name(),
    )
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id)
    if connection and portfolio and str(portfolio.get("brokerage_account_ref") or "") == str(connection.get("id") or ""):
        brokerage_mcp_store.set_portfolio_brokerage(
            portfolio_id=portfolio_id,
            brokerage_source="unlinked",
            brokerage_account_ref=None,
        )
    return disconnected


def _owned_portfolio_connection(*, user_id: str, portfolio_id: str) -> dict[str, Any]:
    if not _ensure_store():
        raise RobinhoodMainError("Brokerage storage is unavailable.")
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id)
    if not portfolio or str(portfolio.get("brokerage_source") or "") != PROVIDER:
        raise RobinhoodMainAuthorizationError("The portfolio is not linked to Robinhood main accounts.")
    connection = brokerage_mcp_store.get_connection_by_id(
        connection_id=str(portfolio.get("brokerage_account_ref") or ""),
        environment=environment_name(),
    )
    if not connection:
        raise RobinhoodMainAuthorizationError("Robinhood main-account connection is unavailable.")
    if str(connection.get("user_id") or "") != str(user_id or "").strip():
        raise RobinhoodMainAuthorizationError("Robinhood main accounts belong to another administrator.")
    _require_portfolio_admin(user_id=user_id, portfolio_id=portfolio_id)
    return connection


def _require_portfolio_admin(*, user_id: str, portfolio_id: str) -> None:
    if not auth_store.user_has_active_portfolio_admin_membership(
        user_id=user_id,
        portfolio_id=portfolio_id,
    ):
        raise RobinhoodMainAuthorizationError("Administrator access is required for Robinhood changes.")


def _portfolio_read_connection(*, user_id: str, portfolio_id: str) -> dict[str, Any]:
    if not _ensure_store():
        raise RobinhoodMainError("Brokerage storage is unavailable.")
    if not auth_store.user_has_active_portfolio_membership(
        user_id=user_id,
        portfolio_id=portfolio_id,
    ):
        raise RobinhoodMainAuthorizationError("The portfolio is not available to this user.")
    portfolio = brokerage_mcp_store.get_portfolio_brokerage(portfolio_id=portfolio_id)
    if not portfolio or str(portfolio.get("brokerage_source") or "") != PROVIDER:
        raise RobinhoodMainAuthorizationError("The portfolio is not linked to Robinhood main accounts.")
    connection = brokerage_mcp_store.get_connection_by_id(
        connection_id=str(portfolio.get("brokerage_account_ref") or ""),
        environment=environment_name(),
    )
    if not connection:
        raise RobinhoodMainAuthorizationError("Robinhood main-account connection is unavailable.")
    return connection


def stop_loss_positions(*, user_id: str, portfolio_id: str) -> list[dict[str, Any]]:
    _owned_portfolio_connection(user_id=user_id, portfolio_id=portfolio_id)
    snapshot = portfolio_snapshot(user_id=user_id, portfolio_id=portfolio_id)
    details = dict((snapshot or {}).get("details") or {})
    positions = [
        item
        for item in list(details.get("holdings") or [])
        if isinstance(item, dict) and item.get("asset_class") == "equity"
    ]
    return [
        {
            "position_key": str(row.get("position_key") or ""),
            "account": str(row.get("account") or ""),
            "symbol": str(row.get("symbol") or ""),
            "quantity": float(_number(row.get("available_quantity"))),
            "current_price": float(_number(row.get("current_price"))),
        }
        for row in positions
        if _number(row.get("available_quantity")) > 0 and _number(row.get("current_price")) > 0
    ]


def _canonical_stop_orders(orders: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(orders, list) or not orders:
        raise RobinhoodMainPolicyError("Select at least one position.")
    canonical: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in orders:
        position_key = str(raw.get("position_key") or "").strip()
        symbol = str(raw.get("symbol") or "").upper().strip()
        if len(position_key) != 64 or not symbol:
            raise RobinhoodMainPolicyError("A selected position is invalid.")
        if position_key in seen:
            raise RobinhoodMainPolicyError("A position can appear only once in a batch.")
        seen.add(position_key)
        canonical.append(
            {
                "position_key": position_key,
                "symbol": symbol,
                "quantity": _decimal_text(_decimal(raw.get("quantity"), f"{symbol} quantity")),
                "stop_price": _round_price(raw.get("stop_price")),
            }
        )
    return sorted(canonical, key=lambda item: (item["symbol"], item["position_key"]))


def _resolve_live_batch(connection: dict[str, Any], orders: list[dict[str, str]]) -> tuple[RobinhoodAppClient, list[dict[str, Any]]]:
    client = _client_from_connection(connection)
    positions = _live_positions(client, str(connection.get("id") or ""), client.accounts())
    by_key = {row["position_key"]: row for row in positions}
    resolved: list[dict[str, Any]] = []
    for order in orders:
        position = by_key.get(order["position_key"])
        if not position or position["symbol"] != order["symbol"]:
            raise RobinhoodMainPolicyError(f"{order['symbol']} is no longer an open position in that account.")
        quantity = _decimal(order["quantity"], f"{order['symbol']} quantity")
        stop_price = _decimal(order["stop_price"], f"{order['symbol']} stop price")
        if quantity > position["available_quantity"]:
            raise RobinhoodMainPolicyError(f"{order['symbol']} no longer has enough unreserved shares.")
        if stop_price >= position["current_price"]:
            raise RobinhoodMainPolicyError(f"{order['symbol']} stop price must remain below its current price.")
        resolved.append({"order": order, "position": position})
    return client, resolved


def review_batch_stop_losses(*, user_id: str, portfolio_id: str, orders: list[dict[str, Any]]) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMainPolicyError("Main-account execution is disabled in this environment.")
    connection = _owned_portfolio_connection(user_id=user_id, portfolio_id=portfolio_id)
    canonical = _canonical_stop_orders(orders)
    _, resolved = _resolve_live_batch(connection, canonical)
    summary = [
        {
            "account": item["position"]["account_label"],
            "symbol": item["order"]["symbol"],
            "quantity": item["order"]["quantity"],
            "stop_price": item["order"]["stop_price"],
            "current_price": _decimal_text(item["position"]["current_price"], places=2),
        }
        for item in resolved
    ]
    ticket = brokerage_mcp_store.create_order_ticket(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        review_tool_name=REVIEW_TOOL,
        execution_tool_name=EXECUTION_TOOL,
        arguments_hash=_arguments_hash(canonical),
        ref_id=str(uuid.uuid4()),
        request_summary={
            "portfolio_id": portfolio_id,
            "orders": summary,
            "order_count": len(summary),
        },
        expires_at=_now_utc() + timedelta(seconds=_ticket_ttl_seconds()),
    )
    return {
        "ticket_id": str(ticket.get("id") or ""),
        "expires_at": ticket.get("expires_at"),
        "orders": summary,
        "canonical_orders": canonical,
    }


def confirm_batch_stop_losses(*, user_id: str, ticket_id: str, orders: list[dict[str, Any]]) -> dict[str, Any]:
    if not execution_enabled():
        raise RobinhoodMainPolicyError("Main-account execution is disabled in this environment.")
    canonical = _canonical_stop_orders(orders)
    ticket = brokerage_mcp_store.consume_order_ticket(
        ticket_id=ticket_id,
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        arguments_hash=_arguments_hash(canonical),
    )
    if not ticket:
        raise RobinhoodMainPolicyError("This batch review expired, changed, or was already used.")
    reviewed_portfolio_id = str((ticket.get("request_summary") or {}).get("portfolio_id") or "")
    _require_portfolio_admin(user_id=user_id, portfolio_id=reviewed_portfolio_id)
    connection = brokerage_mcp_store.get_connection_by_id(
        connection_id=str(ticket.get("connection_id") or ""),
        environment=environment_name(),
    )
    if not connection:
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="failed", error="Connection unavailable.")
        raise RobinhoodMainAuthorizationError("Robinhood main-account connection is unavailable.")
    try:
        client, resolved = _resolve_live_batch(connection, canonical)
    except Exception as exc:
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="failed", error=str(exc))
        raise

    audit_id = brokerage_mcp_store.start_audit_event(
        connection_id=str(connection.get("id") or ""),
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
        tool_name=EXECUTION_TOOL,
        intent="execution",
        request_summary={"order_count": len(canonical), "symbols": [item["symbol"] for item in canonical]},
    )
    results: list[dict[str, Any]] = []
    batch_ref = uuid.UUID(str(ticket.get("ref_id")))
    try:
        for index, item in enumerate(resolved):
            order = item["order"]
            position = item["position"]
            order_ref = str(uuid.uuid5(batch_ref, order["position_key"]))
            try:
                response = client.place_stop_loss(
                    account=position["account"],
                    position=position,
                    quote=position["quote"],
                    order=order,
                    ref_id=order_ref,
                )
                provider_order_id = str(response.get("id") or "")
                state = str(response.get("state") or "").strip().lower()
                if not provider_order_id and any(response.get(key) for key in ("detail", "message", "error", "errors")):
                    raise RobinhoodMainRequestError(
                        _safe_provider_message(response),
                        status_code=400,
                        response=response,
                    )
                if not provider_order_id:
                    if state in {"failed", "rejected", "canceled", "cancelled"}:
                        raise RobinhoodMainRequestError(
                            f"Robinhood returned order state {state}.",
                            status_code=400,
                            response=response,
                        )
                    raise RobinhoodMainRequestError(
                        "Robinhood did not return an order identifier.",
                        response=response,
                    )
                results.append(
                    {
                        "symbol": order["symbol"],
                        "account": position["account_label"],
                        "status": "submitted",
                        "state": state or "submitted",
                        "order_id": provider_order_id,
                    }
                )
            except RobinhoodMainRequestError as exc:
                if exc.status_code == 0:
                    raise
                if exc.status_code == 429:
                    results.append(
                        {
                            "symbol": order["symbol"],
                            "account": position["account_label"],
                            "status": "rate_limited",
                            "error": str(exc)[:300],
                        }
                    )
                    results.extend(
                        {
                            "symbol": remaining["order"]["symbol"],
                            "account": remaining["position"]["account_label"],
                            "status": "skipped",
                            "error": "Batch stopped after Robinhood rate limiting.",
                        }
                        for remaining in resolved[index + 1 :]
                    )
                    break
                results.append(
                    {
                        "symbol": order["symbol"],
                        "account": position["account_label"],
                        "status": "rejected",
                        "error": str(exc)[:300],
                    }
                )
    except RobinhoodMainAuthorizationError as exc:
        summary = {"results": results, "status": "failed"}
        brokerage_mcp_store.finish_audit_event(audit_id=audit_id, status="failed", result_summary=summary, error=str(exc))
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="failed", result_summary=summary, error=str(exc))
        raise
    except (RobinhoodMainRequestError, requests.RequestException, TimeoutError) as exc:
        summary = {"results": results, "status": "unknown"}
        brokerage_mcp_store.finish_audit_event(audit_id=audit_id, status="unknown", result_summary=summary, error=str(exc))
        brokerage_mcp_store.finish_order_ticket(ticket_id=ticket_id, status="unknown", result_summary=summary, error=str(exc))
        raise RobinhoodMainError("Batch status is unknown after a connection failure. Check Robinhood before retrying.") from exc

    submitted = sum(item.get("status") == "submitted" for item in results)
    rejected = len(results) - submitted
    terminal = "executed" if submitted == len(results) else "partial" if submitted else "failed"
    summary = {"submitted": submitted, "rejected": rejected, "results": results}
    brokerage_mcp_store.finish_audit_event(
        audit_id=audit_id,
        status="completed" if submitted else "failed",
        result_summary=summary,
        error="" if submitted else "Robinhood rejected every stop order.",
    )
    brokerage_mcp_store.finish_order_ticket(
        ticket_id=ticket_id,
        status=terminal,
        result_summary=summary,
        error="" if submitted else "Robinhood rejected every stop order.",
    )
    return {"status": terminal, **summary}


def cancel_batch_review(*, user_id: str, ticket_id: str) -> bool:
    return brokerage_mcp_store.cancel_order_ticket(
        ticket_id=ticket_id,
        user_id=user_id,
        provider=PROVIDER,
        environment=environment_name(),
    )


__all__ = [
    "RobinhoodMainAuthorizationError",
    "RobinhoodMainError",
    "RobinhoodMainPolicyError",
    "cancel_batch_review",
    "complete_authentication",
    "confirm_batch_equity_orders",
    "confirm_batch_stop_losses",
    "connection_status",
    "disconnect",
    "environment_name",
    "execution_enabled",
    "link_portfolio",
    "opportunity_set_snapshot",
    "option_leg_history",
    "portfolio_data_status",
    "portfolio_snapshot",
    "request_portfolio_refresh",
    "review_batch_equity_orders",
    "review_batch_stop_losses",
    "save_authentication",
    "start_authentication",
    "stop_loss_positions",
    "stored_device_token",
    "sync_portfolio",
    "trading_accounts",
]
