from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
import os
import random
import re
import time
from typing import Any, Iterator

import pandas as pd
import requests

from .config import AppConfig


class AlpacaAPIError(RuntimeError):
    pass


@dataclass
class AlpacaAPI:
    config: AppConfig

    @staticmethod
    def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
        for idx in range(0, len(values), size):
            yield values[idx : idx + size]

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        cleaned = str(symbol or "").upper().strip()
        if re.fullmatch(r"[A-Z]+-[A-Z]+", cleaned):
            return cleaned.replace("-", ".")
        return cleaned

    @classmethod
    def _normalize_symbols(cls, symbols: list[str]) -> list[str]:
        return [normalized for normalized in (cls._normalize_symbol(symbol) for symbol in symbols) if normalized]

    @staticmethod
    def _normalize_portfolio_period(period: str) -> str:
        cleaned = str(period or "").strip().upper()
        match = re.fullmatch(r"(\d+)([DWMYA])", cleaned)
        if not match:
            raise AlpacaAPIError(
                f"Unsupported portfolio history period '{period}'. Expected values like 1M, 6M, 1Y, 2Y, 5Y."
            )
        value, unit = match.groups()
        if unit == "Y":
            unit = "A"
        return f"{int(value)}{unit}"

    @cached_property
    def _trading_client(self):
        try:
            from alpaca.trading.client import TradingClient
        except Exception as exc:
            raise AlpacaAPIError(
                "alpaca-py is required for trading operations. Install dependency: alpaca-py"
            ) from exc

        base = self.config.alpaca_trading_base_url.lower()
        paper = "paper-api" in base
        return TradingClient(
            api_key=self.config.alpaca_api_key,
            secret_key=self.config.alpaca_secret_key,
            paper=paper,
        )

    @staticmethod
    def _model_to_dict(model: Any) -> dict[str, Any]:
        if model is None:
            return {}
        if isinstance(model, dict):
            return model
        dump = getattr(model, "model_dump", None)
        if callable(dump):
            return dump()
        dump = getattr(model, "dict", None)
        if callable(dump):
            return dump()
        return {k: v for k, v in vars(model).items() if not k.startswith("_")}

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.config.alpaca_secret_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except Exception:
            value = int(default)
        value = max(value, minimum)
        if maximum is not None:
            value = min(value, maximum)
        return value

    @staticmethod
    def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except Exception:
            value = float(default)
        return max(value, minimum)

    @classmethod
    def _stock_bars_batch_size(cls) -> int:
        return cls._env_int("ALPACA_BARS_BATCH_SIZE", 25, minimum=1, maximum=25)

    @classmethod
    def _snapshot_batch_size(cls) -> int:
        return cls._env_int("ALPACA_SNAPSHOT_BATCH_SIZE", 200, minimum=1, maximum=500)

    @classmethod
    def _batch_pause_seconds(cls) -> float:
        return cls._env_float("ALPACA_BATCH_PAUSE_MS", 250.0, minimum=0.0) / 1000.0

    @staticmethod
    def _sleep(seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    @staticmethod
    def _retry_after_seconds(headers: Any) -> float | None:
        if not headers:
            return None
        raw = headers.get("Retry-After")
        if raw in {None, ""}:
            return None
        try:
            return max(float(raw), 0.0)
        except Exception:
            return None

    @staticmethod
    def _looks_rate_limited(status_code: int, body: str) -> bool:
        text = str(body or "").lower()
        if status_code == 429:
            return True
        if status_code != 403:
            return False
        tokens = ("rate limit", "too many requests", "exceeded", "throttle", "throttled")
        return any(token in text for token in tokens)

    @classmethod
    def _retry_delay_seconds(cls, attempt: int, headers: Any = None) -> float:
        retry_after = cls._retry_after_seconds(headers)
        if retry_after is not None:
            return retry_after
        base = cls._env_float("ALPACA_RETRY_BACKOFF_SECONDS", 1.25, minimum=0.1)
        jitter = cls._env_float("ALPACA_RETRY_JITTER_SECONDS", 0.35, minimum=0.0)
        return (base * (2 ** max(attempt - 1, 0))) + random.uniform(0.0, jitter)

    def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: int = 25,
    ) -> Any:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        max_attempts = self._env_int("ALPACA_REQUEST_MAX_ATTEMPTS", 4, minimum=1, maximum=8)
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    params=params,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt >= max_attempts:
                    raise AlpacaAPIError(f"Alpaca request failed: {exc}") from exc
                self._sleep(self._retry_delay_seconds(attempt))
                continue

            if resp.status_code < 400:
                return resp.json()

            error_text = (resp.text or "").strip()
            last_error = f"Alpaca API {resp.status_code}: {error_text}"
            should_retry = resp.status_code in {429, 500, 502, 503, 504} or self._looks_rate_limited(resp.status_code, error_text)
            if should_retry and attempt < max_attempts:
                self._sleep(self._retry_delay_seconds(attempt, resp.headers))
                continue
            raise AlpacaAPIError(last_error)

        raise AlpacaAPIError(last_error or "Alpaca request failed")

    def get_account(self) -> dict[str, Any]:
        try:
            account = self._trading_client.get_account()
        except Exception as exc:
            raise AlpacaAPIError(f"Alpaca trading client error: {exc}") from exc
        return self._model_to_dict(account)

    def get_asset(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            return {}

        payload = self._request(
            "GET",
            self.config.alpaca_trading_base_url,
            f"/v2/assets/{normalized_symbol}",
        )
        return payload or {}

    def get_positions(self) -> pd.DataFrame:
        try:
            positions = self._trading_client.get_all_positions()
        except Exception as exc:
            raise AlpacaAPIError(f"Alpaca trading client error: {exc}") from exc

        payload = [self._model_to_dict(item) for item in positions]
        df = pd.DataFrame(payload)
        if df.empty:
            return df

        numeric_cols = [
            "qty",
            "market_value",
            "avg_entry_price",
            "current_price",
            "unrealized_pl",
            "unrealized_plpc",
            "cost_basis",
            "change_today",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("market_value", ascending=False, na_position="last")
        return df

    def get_portfolio_history(
        self,
        period: str = "1Y",
        timeframe: str = "1D",
        extended_hours: bool = False,
    ) -> pd.DataFrame:
        normalized_period = self._normalize_portfolio_period(period)
        params = {
            "period": normalized_period,
            "timeframe": timeframe,
            "extended_hours": str(extended_hours).lower(),
            "pnl_reset": "per_day",
            "intraday_reporting": "market_hours",
        }
        payload = self._request(
            "GET",
            self.config.alpaca_trading_base_url,
            "/v2/account/portfolio/history",
            params=params,
        )

        timestamps = payload.get("timestamp", []) or []
        equity = payload.get("equity", []) or []
        profit_loss = payload.get("profit_loss", []) or []
        profit_loss_pct = payload.get("profit_loss_pct", []) or []

        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True, errors="coerce"),
                "equity": pd.to_numeric(equity, errors="coerce"),
                "profit_loss": pd.to_numeric(profit_loss, errors="coerce"),
                "profit_loss_pct": pd.to_numeric(profit_loss_pct, errors="coerce"),
            }
        ).dropna(subset=["timestamp"])

        if df.empty:
            return df

        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        return df

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration: str | None = None,
        status: str = "active",
    ) -> pd.DataFrame:
        symbol = self._normalize_symbol(underlying_symbol)
        if not symbol:
            return pd.DataFrame()

        base_params: dict[str, Any] = {
            "underlying_symbols": symbol,
            "status": status,
            "limit": 1000,
        }
        if expiration:
            base_params["expiration_date"] = expiration
        else:
            base_params["expiration_date_gte"] = datetime.now(timezone.utc).date().isoformat()

        rows: list[dict[str, Any]] = []
        next_page_token: str | None = None

        while True:
            params = dict(base_params)
            if next_page_token:
                params["page_token"] = next_page_token

            payload = self._request(
                "GET",
                self.config.alpaca_trading_base_url,
                "/v2/options/contracts",
                params=params,
            )
            rows.extend(payload.get("option_contracts", []) or [])
            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame

        rename_map = {
            "symbol": "contractSymbol",
            "expiration_date": "expiration",
            "strike_price": "strike",
            "close_price": "lastPrice",
            "open_interest": "openInterest",
            "size": "contractSize",
        }
        frame = frame.rename(columns=rename_map)

        for col in ["strike", "lastPrice", "openInterest", "contractSize"]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

        if "type" in frame.columns:
            frame["type"] = frame["type"].astype(str).str.lower()
        if "expiration" in frame.columns:
            frame["expiration"] = frame["expiration"].astype(str)

        sort_cols = [col for col in ["expiration", "strike", "contractSymbol"] if col in frame.columns]
        if sort_cols:
            frame = frame.sort_values(sort_cols).reset_index(drop=True)
        return frame

    def get_option_snapshots(self, option_symbols: list[str], feed: str = "indicative") -> dict[str, Any]:
        symbols = [self._normalize_symbol(symbol) for symbol in dict.fromkeys(option_symbols or []) if str(symbol).strip()]
        if not symbols:
            return {}

        snapshots: dict[str, Any] = {}
        pause_seconds = self._batch_pause_seconds()
        for index, batch in enumerate(self._chunked(symbols, 100), start=1):
            payload = self._request(
                "GET",
                self.config.alpaca_data_base_url,
                "/v1beta1/options/snapshots",
                params={"symbols": ",".join(batch), "feed": feed},
            )
            snapshots.update(payload.get("snapshots", {}) or {})
            if pause_seconds > 0 and index * 100 < len(symbols):
                self._sleep(pause_seconds)

        return snapshots

    def get_stock_bars(
        self,
        symbols: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        timeframe: str = "1Day",
        feed: str = "iex",
    ) -> dict[str, pd.DataFrame]:
        normalized_symbols = self._normalize_symbols(symbols)
        if not normalized_symbols:
            return {}

        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=365))
        by_symbol: dict[str, list[dict[str, Any]]] = {}

        # Alpaca's bars endpoint applies the limit to the whole request, not per symbol.
        # Large universes therefore need batching or the later symbols can be silently dropped.
        deduped_symbols = sorted(set(normalized_symbols))
        batch_size = self._stock_bars_batch_size()
        pause_seconds = self._batch_pause_seconds()
        for index, batch in enumerate(self._chunked(deduped_symbols, batch_size), start=1):
            page_token: str | None = None
            while True:
                params = {
                    "symbols": ",".join(batch),
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "adjustment": "all",
                    "feed": feed,
                    "sort": "asc",
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token

                payload = self._request(
                    "GET",
                    self.config.alpaca_data_base_url,
                    "/v2/stocks/bars",
                    params=params,
                )

                bars = payload.get("bars", {})
                if isinstance(bars, dict):
                    for symbol, rows in bars.items():
                        by_symbol.setdefault(str(symbol).upper(), []).extend(rows or [])
                elif isinstance(bars, list):
                    for row in bars:
                        sym = str(row.get("S", "")).upper()
                        by_symbol.setdefault(sym, []).append(row)

                page_token = str(payload.get("next_page_token") or payload.get("nextPageToken") or "").strip() or None
                has_more_batches = index * batch_size < len(deduped_symbols)
                if pause_seconds > 0 and (page_token is not None or has_more_batches):
                    self._sleep(pause_seconds)
                if page_token is None:
                    break

        parsed: dict[str, pd.DataFrame] = {}
        for symbol, rows in by_symbol.items():
            frame = pd.DataFrame(rows)
            if frame.empty:
                parsed[symbol] = frame
                continue

            rename_map = {
                "t": "timestamp",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
                "n": "trade_count",
                "vw": "vwap",
                "S": "symbol",
            }
            frame = frame.rename(columns=rename_map)
            if "timestamp" in frame.columns:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
            for col in ["open", "high", "low", "close", "volume", "trade_count", "vwap"]:
                if col in frame.columns:
                    frame[col] = pd.to_numeric(frame[col], errors="coerce")

            frame = frame.sort_values("timestamp")
            parsed[symbol.upper()] = frame

        return parsed

    def get_snapshots(self, symbols: list[str], feed: str = "iex") -> dict[str, Any]:
        normalized_symbols = self._normalize_symbols(symbols)
        if not normalized_symbols:
            return {}

        deduped_symbols = sorted(set(normalized_symbols))
        batch_size = self._snapshot_batch_size()
        pause_seconds = self._batch_pause_seconds()
        snapshots: dict[str, Any] = {}
        for index, batch in enumerate(self._chunked(deduped_symbols, batch_size), start=1):
            params = {
                "symbols": ",".join(batch),
                "feed": feed,
            }
            payload = self._request(
                "GET",
                self.config.alpaca_data_base_url,
                "/v2/stocks/snapshots",
                params=params,
            )
            if isinstance(payload, dict):
                snapshots.update(payload.get("snapshots", payload) or {})
            if pause_seconds > 0 and index * batch_size < len(deduped_symbols):
                self._sleep(pause_seconds)
        return snapshots

    def get_news(
        self,
        symbols: list[str],
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 20,
    ) -> pd.DataFrame:
        normalized_symbols = self._normalize_symbols(symbols)
        if not normalized_symbols:
            return pd.DataFrame()

        params: dict[str, Any] = {
            "symbols": ",".join(sorted(set(normalized_symbols))),
            "sort": "desc",
            "limit": min(max(int(limit), 1), 50),
        }
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        rows: list[dict[str, Any]] = []
        next_page_token: str | None = None

        while True:
            page_params = dict(params)
            if next_page_token:
                page_params["page_token"] = next_page_token

            payload = self._request(
                "GET",
                self.config.alpaca_data_base_url,
                "/v1beta1/news",
                params=page_params,
            )
            articles = payload.get("news", []) or []
            rows.extend(articles)
            next_page_token = payload.get("next_page_token")
            if not next_page_token or len(rows) >= params["limit"]:
                break

        frame = pd.DataFrame(rows[: params["limit"]])
        if frame.empty:
            return frame

        rename_map = {
            "headline": "headline",
            "summary": "summary",
            "created_at": "published_at",
            "updated_at": "updated_at",
            "author": "author",
            "url": "url",
            "source": "source",
            "symbols": "symbols",
        }
        frame = frame.rename(columns=rename_map)

        for col in ["published_at", "updated_at"]:
            if col in frame.columns:
                frame[col] = pd.to_datetime(frame[col], utc=True, errors="coerce")

        if "symbols" in frame.columns:
            frame["symbols"] = frame["symbols"].apply(
                lambda value: value if isinstance(value, list) else [item.strip() for item in str(value).split(",") if item.strip()]
            )

        sort_col = "published_at" if "published_at" in frame.columns else "updated_at"
        if sort_col in frame.columns:
            frame = frame.sort_values(sort_col, ascending=False, na_position="last")
        return frame.reset_index(drop=True)
