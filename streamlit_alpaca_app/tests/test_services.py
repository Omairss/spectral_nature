from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

import pandas as pd
import numpy as np

from compute import fundamentals as fundamentals_compute
from compute.treasury_yields import build_treasury_yield_facts_1d, build_treasury_yield_summary
from data_access.layer import DataAccessLayer
from services.alpaca_api import AlpacaAPI
from services import company as company_module
from services import attention_agentic as attention_agentic_module
from services.attention_agentic import build_bottom_up_attention_artifacts
from services.attention_feed_brief import build_attention_feed_brief
from services.attention_home_1d import (
    build_attention_entity_master,
    build_attention_event_candidates_1d,
    build_attention_home_1d,
    build_attention_research_bundle,
)
from services.attention_live_research import build_live_attention_research_bundle, merge_news_payloads
from services.attention_market_events import build_attention_market_events
from services.attention_surface import attention_home_bundle_preview, attention_home_surface_summary
from services.company import build_attention_news_narrative, build_company_description, load_recent_news, summarize_recent_news
from services.config import AppConfig
from services import data_cache
from services import fred as fred_module
from services.attention_context_llm import build_attention_context_narratives, build_edgar_evidence, merge_attention_context_with_llm
from services.attention_ticker_snapshots import (
    build_attention_ticker_background_snapshot_frame,
    build_attention_ticker_snapshot_frame,
)
from services.data_cache import CacheTarget, cached_frame
from services.edgar import EdgarClient, build_attention_context_bundle
from services.fred import FREDClient, FredSeriesSpec, build_fred_figure, build_fred_series_summary, format_fred_value, fred_categories, load_fred_dashboard
from services.fundamentals import load_quarterly_fundamentals
from services.web_research import WebSearchResult
from services.simfin_refresh import build_quarterly_fundamentals_frame, load_simfin_api_key
from services import treasury_yields as treasury_module
from services.homepage_v2 import (
    HOMEPAGE_V2_COMPANY_PANEL,
    HOMEPAGE_V2_RESEARCH_PANEL,
    build_homepage_v2_digest,
    build_homepage_v2_market_digest,
    homepage_v2_editorial_links,
    homepage_v2_bundle_symbol_lookup,
    normalize_homepage_v2_detail_state,
)
from services.llm import AzureOpenAIChatJSONClient, LLMConfig, load_llm_config
from services.market import (
    business_focus_for_symbol,
    business_focus_description,
    business_focus_options,
    business_focus_universe,
    default_commodity_proxy_symbols,
    commodity_dependency_graph,
    commodity_focus_description,
    commodity_focus_options,
    commodity_focus_universe,
    commodity_proxy_profile,
    extend_symbol_universe,
    scan_commodity_regimes,
    scan_correlation_phase_shifts,
    scan_momentum_profiles,
)
from services.options import analyze_option_candidates, load_option_chain, load_option_surface
from services.signals import build_signal_frame, forecast_next_week, summarize_signal_frame
from services.universe import build_liquidity_ranked_equity_universe, load_us_equity_listings
from services.web_research import WebSearchResult


class RecordingAlpacaAPI(AlpacaAPI):
    def __init__(self):
        super().__init__(
            AppConfig(
                alpaca_api_key="key",
                alpaca_secret_key="secret",
                alpaca_trading_base_url="https://paper-api.alpaca.markets",
                alpaca_data_base_url="https://data.alpaca.markets",
            )
        )
        self.last_request: dict | None = None

    def _request(self, method, base_url, path, params=None, timeout=25):
        self.last_request = {
            "method": method,
            "base_url": base_url,
            "path": path,
            "params": params or {},
        }
        return {"timestamp": [], "equity": [], "profit_loss": [], "profit_loss_pct": []}


class RecordingBarsAPI(AlpacaAPI):
    def __init__(self):
        super().__init__(
            AppConfig(
                alpaca_api_key="key",
                alpaca_secret_key="secret",
                alpaca_trading_base_url="https://paper-api.alpaca.markets",
                alpaca_data_base_url="https://data.alpaca.markets",
            )
        )
        self.requests: list[dict[str, object]] = []

    def _request(self, method, base_url, path, params=None, timeout=25):
        self.requests.append({"method": method, "base_url": base_url, "path": path, "params": params or {}})
        symbols = str((params or {}).get("symbols") or "").split(",")
        bars = {
            symbol: [
                {
                    "S": symbol,
                    "t": "2025-01-02T00:00:00Z",
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 1000,
                }
            ]
            for symbol in symbols
            if symbol
        }
        return {"bars": bars}


class RecordingSnapshotAPI(AlpacaAPI):
    def __init__(self):
        super().__init__(
            AppConfig(
                alpaca_api_key="key",
                alpaca_secret_key="secret",
                alpaca_trading_base_url="https://paper-api.alpaca.markets",
                alpaca_data_base_url="https://data.alpaca.markets",
            )
        )
        self.requests: list[dict[str, object]] = []

    def _request(self, method, base_url, path, params=None, timeout=25):
        self.requests.append({"method": method, "base_url": base_url, "path": path, "params": params or {}})
        symbols = str((params or {}).get("symbols") or "").split(",")
        return {
            "snapshots": {
                symbol: {
                    "dailyBar": {"c": 10.0, "v": 1000},
                    "prevDailyBar": {"c": 9.5},
                }
                for symbol in symbols
                if symbol
            }
        }


class FakeOptionAPI:
    def get_option_contracts(self, ticker: str) -> pd.DataFrame:
        assert ticker == "AAPL"
        return pd.DataFrame(
            [
                {
                    "contractSymbol": "AAPL260620C00100000",
                    "expiration": "2026-06-20",
                    "strike": 100,
                    "type": "call",
                    "lastPrice": 4.5,
                    "openInterest": 123,
                },
                {
                    "contractSymbol": "AAPL260620C00110000",
                    "expiration": "2026-06-20",
                    "strike": 110,
                    "type": "call",
                    "lastPrice": 1.6,
                    "openInterest": 210,
                },
                {
                    "contractSymbol": "AAPL260620P00100000",
                    "expiration": "2026-06-20",
                    "strike": 100,
                    "type": "put",
                    "lastPrice": 3.8,
                    "openInterest": 95,
                },
            ]
        )

    def get_option_snapshots(self, option_symbols: list[str], feed: str = "indicative") -> dict[str, dict]:
        assert sorted(option_symbols) == ["AAPL260620C00100000", "AAPL260620C00110000", "AAPL260620P00100000"]
        assert feed == "indicative"
        return {
            "AAPL260620C00100000": {
                "latestQuote": {"bp": 4.2, "ap": 4.6},
                "latestTrade": {"p": 4.5},
                "impliedVolatility": 0.27,
                "dailyBar": {"v": 41, "c": 4.4},
                "greeks": {"delta": 0.65, "gamma": 0.05, "theta": -0.09, "vega": 0.14, "rho": 0.02},
            },
            "AAPL260620C00110000": {
                "latestQuote": {"bp": 1.5, "ap": 1.7},
                "latestTrade": {"p": 1.6},
                "impliedVolatility": 0.31,
                "dailyBar": {"v": 64, "c": 1.55},
                "greeks": {"delta": 0.35, "gamma": 0.08, "theta": -0.05, "vega": 0.11, "rho": 0.01},
            },
            "AAPL260620P00100000": {
                "latestQuote": {"bp": 3.5, "ap": 3.9},
                "latestTrade": {"p": 3.8},
                "impliedVolatility": 0.31,
                "dailyBar": {"v": 28, "c": 3.7},
                "greeks": {"delta": -0.40, "gamma": 0.06, "theta": -0.06, "vega": 0.12, "rho": -0.02},
            },
        }


class FakeMarketAPI:
    def get_stock_bars(self, symbols: list[str], start=None, end=None, timeframe="1Day", feed="iex"):
        frames: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            if symbol == "FAST":
                closes = [100 + idx * 2 for idx in range(80)]
            elif symbol == "ACCEL":
                closes = [100 + idx * 0.2 for idx in range(40)] + [108 + idx * 3 for idx in range(40)]
            else:
                closes = [100 + idx * 0.1 for idx in range(80)]

            frames[symbol] = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2025-01-01", periods=len(closes), freq="B"),
                    "close": closes,
                }
            )
        return frames


def test_try_pipeline_frame_returns_none_when_materialized_lookup_is_empty(monkeypatch):
    layer = DataAccessLayer()

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(DataAccessLayer, "_pipeline_frame", lambda self, dataset_name: (pd.DataFrame(), {}))

    assert layer._try_pipeline_frame("yield_curve_summary", force_refresh=False) is None


def test_resolve_yield_curve_summary_falls_back_when_materialized_lookup_is_empty(monkeypatch):
    layer = DataAccessLayer()
    wide = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-25", "2026-03-26"]),
            "updated_at_utc": pd.to_datetime(["2026-03-26T15:57:18Z", "2026-03-26T15:57:18Z"]),
            "BC_3MONTH": [3.73, 3.73],
            "BC_6MONTH": [3.76, 3.77],
            "BC_1YEAR": [3.77, 3.83],
            "BC_2YEAR": [3.84, 3.96],
            "BC_5YEAR": [3.96, 4.08],
            "BC_10YEAR": [4.33, 4.42],
            "BC_30YEAR": [4.89, 4.93],
        }
    )

    monkeypatch.setattr(DataAccessLayer, "_should_try_pipeline", lambda self, force_refresh: True)
    monkeypatch.setattr(DataAccessLayer, "_pipeline_frame", lambda self, dataset_name: (pd.DataFrame(), {}))
    monkeypatch.setattr("data_access.layer.load_treasury_yield_curve", lambda years=3: wide)

    resolved = layer.resolve_yield_curve_summary(force_refresh=False)

    assert resolved.provenance.mode == "on_demand"
    assert not resolved.payload.empty
    assert "UST_10Y" in set(resolved.payload["series_id"])


class FakePhaseShiftAPI:
    def get_stock_bars(self, symbols: list[str], start=None, end=None, timeframe="1Day", feed="iex"):
        length = 140
        timestamps = pd.date_range("2025-01-01", periods=length, freq="B")
        x = np.arange(length, dtype=float)
        benchmark_returns = 0.0015 + 0.008 * np.sin(x / 5.5)

        def build_close(returns: np.ndarray, start_price: float = 100.0) -> pd.DataFrame:
            closes = [start_price]
            for ret in returns:
                closes.append(closes[-1] * (1.0 + float(ret)))
            close = pd.Series(closes[1:], dtype=float)
            return pd.DataFrame({"timestamp": timestamps, "close": close})

        lead_returns = benchmark_returns.copy()
        lead_returns[85:] = 0.005 + 0.010 * np.cos(x[85:] / 4.0)

        beta_returns = (benchmark_returns * 1.15) + 0.0005
        unwind_returns = benchmark_returns.copy()
        unwind_returns[85:] = (-0.004 + benchmark_returns[85:] * 0.85)

        series_map = {
            "SPY": build_close(benchmark_returns, 100.0),
            "LEAD": build_close(lead_returns, 55.0),
            "BETA": build_close(beta_returns, 70.0),
            "UNWIND": build_close(unwind_returns, 80.0),
        }
        return {symbol: series_map[symbol] for symbol in symbols if symbol in series_map}


class FakeCommodityAPI:
    def get_stock_bars(self, symbols: list[str], start=None, end=None, timeframe="1Day", feed="iex"):
        length = 160
        timestamps = pd.date_range("2025-01-01", periods=length, freq="B")
        x = np.arange(length, dtype=float)
        energy_returns = 0.0015 + 0.010 * np.sin(x / 6.0)
        metals_returns = 0.0010 + 0.007 * np.cos(x / 9.0)
        basket_returns = (energy_returns + metals_returns) / 2.0

        def build_close(returns: np.ndarray, start_price: float = 100.0) -> pd.DataFrame:
            closes = [start_price]
            for ret in returns:
                closes.append(closes[-1] * (1.0 + float(ret)))
            close = pd.Series(closes[1:], dtype=float)
            return pd.DataFrame({"timestamp": timestamps, "close": close})

        benefit_returns = basket_returns * 1.45 + 0.0015
        benefit_returns[95:] = basket_returns[95:] * 1.65 + 0.0035

        squeeze_returns = basket_returns * 1.20 - 0.0020
        squeeze_returns[95:] = basket_returns[95:] * 1.35 - 0.0040

        decouple_returns = basket_returns * 1.05
        decouple_returns[95:] = 0.0045 + 0.006 * np.cos(x[95:] / 5.0)

        series_map = {
            "USO": build_close(energy_returns, 100.0),
            "GLD": build_close(metals_returns, 90.0),
            "BENEFIT": build_close(benefit_returns, 55.0),
            "SQUEEZE": build_close(squeeze_returns, 70.0),
            "DECOUPLE": build_close(decouple_returns, 65.0),
        }
        return {symbol: series_map[symbol] for symbol in symbols if symbol in series_map}


class FakeCompanyAPI:
    def get_news(self, symbols: list[str], start=None, end=None, limit: int = 20):
        assert symbols == ["AAPL"]
        return pd.DataFrame(
            [
                {
                    "headline": "Apple expands AI features across devices",
                    "summary": "Apple highlighted a broader on-device AI rollout and stronger services attach rates.",
                    "published_at": pd.Timestamp("2026-02-27", tz="UTC"),
                    "source": "ExampleWire",
                    "url": "https://example.com/apple-ai",
                    "sentiment": "positive",
                    "symbols": ["AAPL"],
                },
                {
                    "headline": "Analysts focus on iPhone replacement cycle",
                    "summary": "Recent channel checks point to a steadier replacement cycle and margin discipline.",
                    "published_at": pd.Timestamp("2026-02-26", tz="UTC"),
                    "source": "ExampleStreet",
                    "url": "https://example.com/apple-cycle",
                    "sentiment": "positive",
                    "symbols": ["AAPL"],
                },
            ]
        )


class FakeFREDClient(FREDClient):
    def __init__(self):
        super().__init__("fake-key")

    def _request_v1(self, path: str, params):
        if path == "series":
            return {
                "seriess": [
                    {
                        "id": "CPIAUCSL",
                        "title": "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average",
                        "units": "Index 1982-1984=100",
                        "units_short": "Index 1982-1984=100",
                        "frequency": "Monthly",
                        "frequency_short": "M",
                        "last_updated": "2026-02-12 07:31:00-06",
                    }
                ]
            }
        if path == "series/release":
            return {
                "releases": [
                    {
                        "id": 10,
                        "name": "Consumer Price Index",
                        "press_release": True,
                        "link": "https://fred.stlouisfed.org/release?rid=10",
                    }
                ]
            }
        if path == "series/observations":
            return {
                "observations": [
                    {"date": "2025-01-01", "value": "309.000"},
                    {"date": "2025-02-01", "value": "309.400"},
                    {"date": "2025-03-01", "value": "309.800"},
                    {"date": "2025-04-01", "value": "310.100"},
                    {"date": "2025-05-01", "value": "310.500"},
                    {"date": "2025-06-01", "value": "311.000"},
                    {"date": "2025-07-01", "value": "311.700"},
                    {"date": "2025-08-01", "value": "312.600"},
                    {"date": "2025-09-01", "value": "313.700"},
                    {"date": "2025-10-01", "value": "315.000"},
                    {"date": "2025-11-01", "value": "316.900"},
                    {"date": "2025-12-01", "value": "318.500"},
                    {"date": "2026-01-01", "value": "319.300"},
                ]
            }
        raise AssertionError(path)

    def _request_v2(self, path: str, params):
        if path == "v2/release/observations":
            return {
                "has_more": False,
                "next_cursor": None,
                "series": [
                    {
                        "series_id": "CPIAUCSL",
                        "title": "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average",
                        "frequency": "Monthly",
                        "units": "Index 1982-1984=100",
                        "seasonal_adjustment": "Seasonally Adjusted",
                        "last_updated": "2026-02-12 07:31:00-06",
                        "notes": "CPI notes",
                        "observations": [
                            {"date": "2025-01-01", "value": "309.000"},
                            {"date": "2025-02-01", "value": "309.400"},
                            {"date": "2025-03-01", "value": "309.800"},
                            {"date": "2025-04-01", "value": "310.100"},
                            {"date": "2025-05-01", "value": "310.500"},
                            {"date": "2025-06-01", "value": "311.000"},
                            {"date": "2025-07-01", "value": "311.700"},
                            {"date": "2025-08-01", "value": "312.600"},
                            {"date": "2025-09-01", "value": "313.700"},
                            {"date": "2025-10-01", "value": "315.000"},
                            {"date": "2025-11-01", "value": "316.900"},
                            {"date": "2025-12-01", "value": "318.500"},
                            {"date": "2026-01-01", "value": "319.300"},
                        ],
                    }
                ],
            }
        raise AssertionError(path)


def _synthetic_price_history(days: int = 320) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=days, freq="B")
    x = np.arange(days, dtype=float)
    close = 100 + (x * 0.18) + (5.0 * np.sin(x / 7.0)) + (2.0 * np.sin(x / 17.0))
    open_ = close * (1.0 + (0.002 * np.sin(x / 5.0)))
    high = close * 1.012
    low = close * 0.988
    volume = 1_000_000 + (x * 2500)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_get_portfolio_history_maps_year_periods_to_alpaca_annual_units():
    api = RecordingAlpacaAPI()
    api.get_portfolio_history(period="1Y", timeframe="1D")
    assert api.last_request is not None
    assert api.last_request["params"]["period"] == "1A"


def test_alpaca_symbol_normalization_maps_class_share_hyphens_to_dots():
    assert AlpacaAPI._normalize_symbol("BRK-B") == "BRK.B"
    assert AlpacaAPI._normalize_symbol("bf-b") == "BF.B"
    assert AlpacaAPI._normalize_symbol("SPY") == "SPY"


def test_get_stock_bars_batches_large_symbol_lists():
    api = RecordingBarsAPI()
    symbols = [f"S{idx:02d}" for idx in range(60)]

    bars = api.get_stock_bars(symbols)

    assert len(api.requests) == 3
    assert len(bars) == 60
    assert api.requests[0]["path"] == "/v2/stocks/bars"


def test_get_stock_bars_paginates_long_lookbacks(monkeypatch):
    monkeypatch.setenv("ALPACA_BATCH_PAUSE_MS", "0")

    class PagingBarsAPI(AlpacaAPI):
        def __init__(self):
            super().__init__(
                AppConfig(
                    alpaca_api_key="key",
                    alpaca_secret_key="secret",
                    alpaca_trading_base_url="https://paper-api.alpaca.markets",
                    alpaca_data_base_url="https://data.alpaca.markets",
                )
            )
            self.requests: list[dict[str, object]] = []

        def _request(self, method, base_url, path, params=None, timeout=25):
            payload = dict(params or {})
            self.requests.append(payload)
            if payload.get("page_token") == "page-2":
                return {
                    "bars": {
                        "AAPL": [
                            {"S": "AAPL", "t": "2025-01-03T00:00:00Z", "o": 102.0, "h": 103.0, "l": 101.0, "c": 102.5, "v": 1002}
                        ]
                    }
                }
            return {
                "bars": {
                    "AAPL": [
                        {"S": "AAPL", "t": "2025-01-01T00:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1000},
                        {"S": "AAPL", "t": "2025-01-02T00:00:00Z", "o": 101.0, "h": 102.0, "l": 100.0, "c": 101.5, "v": 1001},
                    ]
                },
                "next_page_token": "page-2",
            }

    api = PagingBarsAPI()

    bars = api.get_stock_bars(["AAPL"])

    assert len(api.requests) == 2
    assert api.requests[1]["page_token"] == "page-2"
    assert len(bars["AAPL"]) == 3
    assert bars["AAPL"]["timestamp"].max() == pd.Timestamp("2025-01-03T00:00:00Z")


def test_get_snapshots_batches_large_symbol_lists(monkeypatch):
    monkeypatch.setenv("ALPACA_SNAPSHOT_BATCH_SIZE", "200")
    monkeypatch.setenv("ALPACA_BATCH_PAUSE_MS", "0")
    api = RecordingSnapshotAPI()
    symbols = [f"S{idx:03d}" for idx in range(450)]

    snapshots = api.get_snapshots(symbols)

    assert len(api.requests) == 3
    assert len(snapshots) == 450
    assert api.requests[0]["path"] == "/v2/stocks/snapshots"


def test_alpaca_request_retries_rate_limited_responses(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object], text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.headers = headers or {}

        def json(self):
            return self._payload

    responses = [
        FakeResponse(429, {}, text="Too many requests", headers={"Retry-After": "0.1"}),
        FakeResponse(200, {"ok": True}),
    ]
    sleeps: list[float] = []

    def fake_request(method, url, headers=None, params=None, timeout=25):
        return responses.pop(0)

    monkeypatch.setattr("services.alpaca_api.requests.request", fake_request)
    monkeypatch.setattr("services.alpaca_api.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("ALPACA_REQUEST_MAX_ATTEMPTS", "2")

    api = AlpacaAPI(
        AppConfig(
            alpaca_api_key="key",
            alpaca_secret_key="secret",
            alpaca_trading_base_url="https://paper-api.alpaca.markets",
            alpaca_data_base_url="https://data.alpaca.markets",
        )
    )

    payload = api._request("GET", api.config.alpaca_data_base_url, "/v2/stocks/snapshots")

    assert payload == {"ok": True}
    assert sleeps == [0.1]


def test_load_quarterly_fundamentals_uses_local_simfin_dataset():
    data = load_quarterly_fundamentals("A")

    assert not data["income"].empty
    assert not data["balance"].empty
    assert not data["cashflow"].empty
    assert {"Total Revenue", "Operating Income", "Net Income"} <= set(data["income"]["metric"])
    assert {"Total Assets", "Total Liabilities", "Stockholders Equity"} <= set(data["balance"]["metric"])
    assert {"Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"} <= set(data["cashflow"]["metric"])


def test_load_quarterly_fundamentals_accepts_explicit_data_dir():
    data_dir = "/home/azureuser/cloudfiles/code/Users/omai.r/spectral_nature/streamlit_alpaca_app/data/stock_fundamental"
    data = load_quarterly_fundamentals("A", data_dir=data_dir)

    assert not data["income"].empty
    assert not data["balance"].empty
    assert not data["cashflow"].empty


def test_build_quarterly_fundamentals_frame_uses_refreshed_data_dir(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def _fake_refresh_simfin_quarterly_cache(*, refresh_days: int = 1, data_dir: str | None = None):
        return {"provider": "simfin", "data_dir": "/tmp/simfin_refresh", "refresh_days": refresh_days}

    def _fake_load_quarterly_fundamentals(ticker: str, *, data_dir: str | None = None):
        calls.append((ticker, data_dir))
        frame = pd.DataFrame(
            {
                "report_date": [pd.Timestamp("2024-12-31")],
                "year_quarter": ["2024Q4"],
                "metric": ["Total Revenue"],
                "value": [123.0],
                "ticker": [ticker],
                "statement": ["income"],
            }
        )
        return {"income": frame, "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}

    monkeypatch.setattr("services.simfin_refresh.simfin_refresh_configured", lambda: True)
    monkeypatch.setattr("services.simfin_refresh.refresh_simfin_quarterly_cache", _fake_refresh_simfin_quarterly_cache)
    monkeypatch.setattr("services.simfin_refresh.load_quarterly_fundamentals", _fake_load_quarterly_fundamentals)

    frame, details = build_quarterly_fundamentals_frame(["RDDT"], prefer_upstream=True, refresh_days=2)

    assert len(frame) == 1
    assert details["provider"] == "simfin"
    assert calls == [("RDDT", "/tmp/simfin_refresh")]


def test_load_simfin_api_key_falls_back_to_alternate_vault(monkeypatch):
    monkeypatch.setattr("services.simfin_refresh.resolve_secret_value", lambda *args, **kwargs: "")
    calls: list[tuple[str, str, str]] = []

    def _fake_get_secret_value_from_vault(secret_name: str, *, vault_name: str = "", vault_url: str = "") -> str:
        calls.append((secret_name, vault_name, vault_url))
        return "secret-token" if secret_name == "SimFinAPI" and vault_name == "spectral-nature-kvault" else ""

    monkeypatch.setattr("services.simfin_refresh.get_secret_value_from_vault", _fake_get_secret_value_from_vault)
    monkeypatch.delenv("SIMFIN_KEY_VAULT_NAME", raising=False)
    monkeypatch.delenv("SIMFIN_VAULT_NAME", raising=False)
    monkeypatch.delenv("SIMFIN_KEY_VAULT_URL", raising=False)
    monkeypatch.delenv("SIMFIN_VAULT_URL", raising=False)
    monkeypatch.delenv("SIMFIN_API_KEY_SECRET", raising=False)
    monkeypatch.delenv("SIMFIN_SECRET_NAME", raising=False)

    key = load_simfin_api_key()

    assert key == "secret-token"
    assert calls[0] == ("SimFinAPI", "spectral-nature-kvault", "")


def test_load_option_chain_shapes_alpaca_contracts_and_snapshots():
    expirations, calls, puts = load_option_chain(FakeOptionAPI(), "AAPL", expiration="2026-06-20")

    assert expirations == ["2026-06-20"]
    assert calls.iloc[0]["contractSymbol"] == "AAPL260620C00100000"
    assert calls.iloc[0]["bid"] == 4.2
    assert calls.iloc[0]["ask"] == 4.6
    assert calls.iloc[0]["impliedVolatility"] == 0.27
    assert calls.iloc[0]["volume"] == 41
    assert calls.iloc[0]["delta"] == 0.65
    assert puts.iloc[0]["contractSymbol"] == "AAPL260620P00100000"


def test_analyze_option_candidates_scores_cross_expiration_greek_surface():
    surface = load_option_surface(
        FakeOptionAPI(),
        "AAPL",
        underlying_price=100.0,
        expected_price=112.0,
        horizon_days=30,
        max_contracts=25,
    )
    candidates, summary = analyze_option_candidates(
        surface,
        underlying_price=100.0,
        expected_price=112.0,
        horizon_days=30,
    )

    assert not surface.empty
    assert {"delta", "gamma", "theta", "vega", "premium", "dte"} <= set(surface.columns)
    assert not candidates.empty
    assert summary["preferred_side"] == "call"
    assert candidates.iloc[0]["contractSymbol"] == "AAPL260620C00110000"
    assert candidates.iloc[0]["selection_score"] >= candidates.iloc[-1]["selection_score"]


def test_scan_momentum_profiles_returns_momentum_and_acceleration_scores():
    out = scan_momentum_profiles(FakeMarketAPI(), symbols=["FAST", "ACCEL", "SLOW"], days=120)

    assert not out.empty
    assert {
        "momentum_score",
        "momentum_roc_score",
        "roc_1w_to_1m",
        "roc_1m_to_3m",
        "return_1d_pct",
        "return_7d_pct",
        "daily_change_pct",
        "return_1w_pct",
        "return_1y_pct",
        "return_5y_pct",
        "trend_r2_3m",
        "trend_fit_gap",
        "sparkline_3m",
    } <= set(out.columns)
    assert out.iloc[0]["symbol"] == "ACCEL"
    assert out.nlargest(1, "momentum_roc_score").iloc[0]["symbol"] == "FAST"
    assert out["trend_r2_3m"].between(0, 1).all()
    assert isinstance(out.iloc[0]["sparkline_3m"], list)
    assert len(out.iloc[0]["sparkline_3m"]) >= 20


def test_business_focus_universe_uses_custom_business_lenses(monkeypatch):
    focus_map = {
        "All Market": ["HD", "GOOGL", "META", "BX", "KKR", "OWL", "XOM", "FCX", "MOS", "TTD", "APP", "DHI", "LEN"],
        "Alternative Asset Managers": ["BX", "KKR", "OWL"],
        "Housing": ["HD", "DHI", "LEN"],
        "Advertising": ["GOOGL", "TTD", "APP"],
        "Commodity": ["XOM", "FCX", "MOS"],
        "Retail": ["HD", "WMT"],
    }
    monkeypatch.setattr("services.market._taxonomy_focus_map", lambda symbols=None: focus_map)

    options = business_focus_options()
    alternatives = set(business_focus_universe("Alternative Asset Managers"))
    housing = set(business_focus_universe("Housing"))
    advertising = set(business_focus_universe("Advertising"))
    commodity = set(business_focus_universe("Commodity"))
    all_market = set(business_focus_universe("All Market"))

    assert "Alternative Asset Managers" in options
    assert "Housing" in options
    assert "Advertising" in options
    assert "Commodity" in options
    assert {"BX", "KKR", "OWL"} <= alternatives
    assert {"HD", "DHI", "LEN"} <= housing
    assert {"GOOGL", "TTD", "APP"} <= advertising
    assert {"XOM", "FCX", "MOS"} <= commodity
    assert {"HD", "GOOGL", "META", "BX", "KKR", "OWL"} <= all_market
    assert "Taxonomy-derived peer cohort" in business_focus_description("Retail")


def test_business_focus_for_symbol_maps_taxonomy_names_and_defaults_to_all_market(monkeypatch):
    focus_map = {
        "All Market": ["BX", "XOM", "UNMAPPED"],
        "Alternative Asset Managers": ["BX"],
        "Commodity": ["XOM"],
    }
    monkeypatch.setattr("services.market._taxonomy_focus_map", lambda symbols=None: focus_map)

    assert business_focus_for_symbol("BX") == "Alternative Asset Managers"
    assert business_focus_for_symbol("XOM") == "Commodity"
    assert business_focus_for_symbol("UNMAPPED") == "All Market"


def test_extend_symbol_universe_pins_extra_symbols_without_duplicates():
    assert extend_symbol_universe(["AAPL", "MSFT"], ["MSFT", "BX", ""]) == ["AAPL", "MSFT", "BX"]


def test_load_us_equity_listings_filters_test_issues_etfs_and_non_common(monkeypatch):
    nasdaq_text = "\n".join(
        [
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAAA|Alpha Holdings Common Stock|Q|N|N|100|N|N",
            "AHL$D|Atlas Holdings Common Stock|Q|N|N|100|N|N",
            "BBBB|Bravo Holdings Warrants|Q|N|N|100|N|N",
            "CCCC|Core Index ETF|Q|N|N|100|Y|N",
            "TEST|Test Issue Co Common Stock|Q|Y|N|100|N|N",
            "File Creation Time|20260320",
        ]
    )
    other_text = "\n".join(
        [
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
            "DDDD|Delta Industries Common Stock|N|DDDD|N|100|N|DDDD",
            "EEEE|Echo Capital Preferred Stock|N|EEEE|N|100|N|EEEE",
            "File Creation Time|20260320",
        ]
    )

    class FakeHTTPResponse:
        def __init__(self, text: str):
            self.text = text

    def fake_get(url: str, timeout: int = 30):
        if "nasdaqlisted" in url:
            return FakeHTTPResponse(nasdaq_text)
        return FakeHTTPResponse(other_text)

    monkeypatch.setattr("services.universe.requests.get", fake_get)

    listings = load_us_equity_listings()

    assert listings["symbol"].tolist() == ["AAAA", "DDDD"]
    assert listings["exchange"].tolist() == ["NASDAQ", "NYSE"]


def test_build_liquidity_ranked_equity_universe_keeps_explicit_pinned_symbols(monkeypatch):
    nasdaq_text = "\n".join(
        [
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAAA|Alpha Holdings Common Stock|Q|N|N|100|N|N",
            "FFFF|Foxtrot Logistics Common Stock|Q|N|N|100|N|N",
            "File Creation Time|20260320",
        ]
    )
    other_text = "\n".join(
        [
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
            "DDDD|Delta Industries Common Stock|N|DDDD|N|100|N|DDDD",
            "File Creation Time|20260320",
        ]
    )

    class FakeHTTPResponse:
        def __init__(self, text: str):
            self.text = text

    class FakeSnapshotUniverseAPI:
        def get_snapshots(self, symbols: list[str], feed: str = "iex"):
            assert feed == "iex"
            payload = {
                "AAAA": {"dailyBar": {"c": 50.0, "v": 600_000}, "prevDailyBar": {"c": 48.0}},
                "FFFF": {"dailyBar": {"c": 20.0, "v": 400_000}, "prevDailyBar": {"c": 19.0}},
                "DDDD": {"dailyBar": {"c": 10.0, "v": 50_000}, "prevDailyBar": {"c": 10.5}},
            }
            return {symbol: payload.get(symbol, {}) for symbol in symbols}

    def fake_get(url: str, timeout: int = 30):
        if "nasdaqlisted" in url:
            return FakeHTTPResponse(nasdaq_text)
        return FakeHTTPResponse(other_text)

    monkeypatch.setattr("services.universe.requests.get", fake_get)

    ranked = build_liquidity_ranked_equity_universe(
        FakeSnapshotUniverseAPI(),
        target_size=2,
        pinned_symbols=["DDDD"],
        min_price=1.0,
        min_volume=0.0,
        min_dollar_volume=5_000_000.0,
    )

    assert ranked["symbol"].tolist() == ["DDDD", "AAAA"]
    assert ranked.iloc[0]["selection_reason"] == "pinned"
    assert ranked.iloc[1]["selection_reason"] == "liquidity"


def test_commodity_focus_universe_uses_custom_commodity_baskets():
    options = commodity_focus_options()
    broad = commodity_focus_universe("Broad Commodity Market")
    energy = set(commodity_focus_universe("Energy & Oil"))
    agriculture = set(commodity_focus_universe("Softs & Agriculture"))

    assert "Energy & Oil" in options
    assert "Softs & Agriculture" in options
    assert len(broad) >= 20
    assert {"USO", "UNG"} <= energy
    assert {"DBA", "CORN", "JO", "NIB"} <= agriculture
    assert commodity_focus_description("Broad Commodity Market")


def test_commodity_proxy_profile_returns_readable_name_and_description():
    profile = commodity_proxy_profile("CPER")

    assert profile["name"]
    assert "Copper" in profile["commodity"]
    assert "copper" in profile["description"].lower()


def test_commodity_dependency_graph_returns_curated_links():
    graph = commodity_dependency_graph(["USO", "UNG", "CORN"])

    assert not graph.empty
    assert {"source", "target", "relation", "weight", "description"} <= set(graph.columns)
    assert {"USO", "UNG", "CORN"} >= set(graph["source"]).union(set(graph["target"]))


def test_build_attention_market_events_promotes_oil_shock_into_market_event():
    asof = pd.Timestamp("2026-03-23T18:00:00Z")
    feed = pd.DataFrame(
        {
            "event_id": ["bno", "uso", "ual", "dal", "iwm", "tlt", "aapl"],
            "asof_time_utc": [asof] * 7,
            "entity_id": ["BNO", "USO", "UAL", "DAL", "IWM", "TLT", "AAPL"],
            "direction": ["down", "down", "up", "up", "up", "up", "down"],
            "observed_value": [-10.0, -8.8, 5.1, 4.7, 2.9, 1.6, -0.8],
            "attention_score": [86.0, 81.0, 77.0, 74.0, 72.0, 69.0, 58.0],
            "severity_score": [90.0, 84.0, 70.0, 68.0, 66.0, 60.0, 40.0],
            "title": [
                "BNO is underperforming expectation",
                "USO is underperforming expectation",
                "UAL is outperforming expectation",
                "DAL is outperforming expectation",
                "IWM is outperforming expectation",
                "TLT is outperforming expectation",
                "AAPL is underperforming expectation",
            ],
            "subtitle": ["News confirmed move over 1d"] * 7,
            "horizon": ["1d"] * 7,
            "peer_group_name": [
                "Energy & Oil",
                "Energy & Oil",
                "Travel & Mobility",
                "Travel & Mobility",
                "All Market",
                "All Market",
                "All Market",
            ],
            "source_label": ["Commodities", "Commodities", "Equities", "Equities", "Equities", "Equities", "Equities"],
            "story_text": [
                "Oil-linked instruments are breaking lower.",
                "Oil-linked instruments are breaking lower.",
                "Airlines are firming as fuel pressure eases.",
                "Airlines are firming as fuel pressure eases.",
                "Small caps are rallying with the broader risk-on move.",
                "Treasuries are firming as inflation pressure cools.",
                "AAPL is lagging the broader tape.",
            ],
            "why_now_text": [
                "Oil is dropping sharply.",
                "Oil is dropping sharply.",
                "Travel is benefiting from lower fuel costs.",
                "Travel is benefiting from lower fuel costs.",
                "Risk appetite is improving.",
                "Bond yields are easing.",
                "Single-name pressure remains idiosyncratic.",
            ],
        }
    )
    news_payloads = {
        "BNO": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Oil prices ease and stock markets jump as hopes rise for an end to the Iran war",
                        "summary": "Investors read the move as lower odds of a prolonged supply disruption.",
                        "source": "AP",
                    }
                ]
            )
        }
    }

    events = build_attention_market_events(feed, news_payloads=news_payloads, context_payloads={}, max_events=3)

    assert not events.empty
    top = events.iloc[0]
    assert top["event_type"] == "oil"
    assert "Oil" in top["event_title"]
    assert "BNO" in top["what_happened_text"]
    assert "%" not in top["what_happened_text"]
    assert "TLT" not in top["what_happened_text"]
    assert "UAL" in top["affected_assets_summary_text"]
    assert "TLT" in top["affected_assets_summary_text"]
    assert "supply-risk" in top["why_happened_text"].lower()
    assert set(top["supporting_event_ids"]) >= {"bno", "uso", "ual", "dal", "iwm", "tlt"}


def test_build_attention_market_events_uses_observed_move_direction_for_event_copy():
    asof = pd.Timestamp("2026-03-23T18:00:00Z")
    feed = pd.DataFrame(
        {
            "event_id": ["bno", "uso", "ual", "tlt"],
            "asof_time_utc": [asof] * 4,
            "entity_id": ["BNO", "USO", "UAL", "TLT"],
            "direction": ["up", "up", "up", "up"],
            "observed_value": [-8.9, -7.4, 4.6, 1.5],
            "attention_score": [92.0, 86.0, 79.0, 74.0],
            "severity_score": [94.0, 88.0, 70.0, 62.0],
            "title": [
                "BNO is outperforming expectation",
                "USO is outperforming expectation",
                "UAL is outperforming expectation",
                "TLT is outperforming expectation",
            ],
            "subtitle": ["News confirmed move over 1d"] * 4,
            "horizon": ["1d"] * 4,
            "peer_group_name": ["Energy & Oil", "Energy & Oil", "Travel & Mobility", "All Market"],
            "source_label": ["Commodities", "Commodities", "Equities", "Equities"],
            "story_text": [
                "Oil-linked instruments are breaking lower.",
                "Oil-linked instruments are breaking lower.",
                "Airlines are firming as fuel pressure eases.",
                "Treasuries are firming as inflation pressure cools.",
            ],
            "why_now_text": [
                "Oil is dropping sharply.",
                "Oil is dropping sharply.",
                "Travel is benefiting from lower fuel costs.",
                "Bond yields are easing.",
            ],
        }
    )

    events = build_attention_market_events(feed, news_payloads={}, context_payloads={}, max_events=2)

    assert not events.empty
    top = events.iloc[0]
    assert top["event_type"] == "oil"
    assert top["anchor_direction"] == "down"
    assert top["event_title"] == "Energy & Oil move lower together today"
    assert "fell sharply" in top["what_happened_text"]
    assert "BNO" in top["what_happened_text"]
    assert "%" not in top["what_happened_text"]


def test_build_attention_market_events_does_not_pull_generic_energy_names_into_oil_event():
    asof = pd.Timestamp("2026-03-23T18:00:00Z")
    feed = pd.DataFrame(
        {
            "event_id": ["bno", "uso", "ual", "tlt", "apg"],
            "asof_time_utc": [asof] * 5,
            "entity_id": ["BNO", "USO", "UAL", "TLT", "APG"],
            "direction": ["down", "down", "up", "up", "up"],
            "observed_value": [-10.0, -8.8, 5.1, 1.6, 7.4],
            "attention_score": [86.0, 81.0, 77.0, 69.0, 93.0],
            "severity_score": [90.0, 84.0, 70.0, 60.0, 78.0],
            "title": [
                "BNO is underperforming expectation",
                "USO is underperforming expectation",
                "UAL is outperforming expectation",
                "TLT is outperforming expectation",
                "APG is outperforming expectation",
            ],
            "subtitle": ["News confirmed move over 1d"] * 5,
            "horizon": ["1d"] * 5,
            "peer_group_name": [
                "Energy & Oil",
                "Energy & Oil",
                "Travel & Mobility",
                "All Market",
                "Industrials",
            ],
            "source_label": ["Commodities", "Commodities", "Equities", "Equities", "Equities"],
            "story_text": [
                "Oil-linked instruments are breaking lower.",
                "Oil-linked instruments are breaking lower.",
                "Airlines are firming as fuel pressure eases.",
                "Treasuries are firming as inflation pressure cools.",
                "APG is rallying on better building energy-efficiency demand.",
            ],
            "why_now_text": [
                "Oil is dropping sharply.",
                "Oil is dropping sharply.",
                "Travel is benefiting from lower fuel costs.",
                "Bond yields are easing.",
                "A company-specific backlog update is driving the move.",
            ],
        }
    )
    context_payloads = {
        "APG": {
            "llm_why_now": "The stock's recent divergence coincides with regulatory approval to produce from the Gradizza Field and year-end proved reserve data.",
        }
    }

    events = build_attention_market_events(feed, news_payloads={}, context_payloads=context_payloads, max_events=2)

    assert not events.empty
    top = events.iloc[0]
    assert top["event_type"] == "oil"
    assert top["anchor_symbol"] == "BNO"
    assert "APG" not in set(top["supporting_symbols"])
    assert "Gradizza Field" not in top["why_happened_text"]


def test_build_attention_feed_brief_fallback_prefers_story_over_generic_cluster_text():
    brief = build_attention_feed_brief(
        {
            "symbol": "BNO",
            "title": "BNO is underperforming expectation",
            "story_text": "Oil-linked instruments are breaking lower as the market prices less supply risk.",
            "news_narrative": "Coverage from benzinga is clustering around commodity prices, which helps explain why the move looks idiosyncratic.",
            "headline_items": [],
            "company_description": "BNO tracks Brent crude oil.",
            "context_summary": "",
            "watchpoint_text": "Watch whether airlines and broad equities keep confirming the move.",
        },
        None,
    )

    assert brief["lead_text"].startswith("Oil-linked instruments are breaking lower")
    assert "coverage from benzinga" in brief["cluster_text"].lower()


def test_build_attention_entity_master_leaves_unknown_equities_unclassified_for_macro_roles(monkeypatch):
    monkeypatch.setattr("services.entity_taxonomy.taxonomy_lookup_by_symbol", lambda symbols: {})

    master = build_attention_entity_master(["APGE", "APG", "BNO", "TLT", "UAL"])
    lookup = {str(row["symbol"]).upper(): row for _, row in master.iterrows()}

    assert lookup["APGE"]["sector"] == "Unknown"
    assert lookup["APGE"]["commodity_role"] == ""
    assert lookup["APGE"]["rates_role"] == ""
    assert lookup["APGE"]["macro_role_tags"] == []
    assert lookup["APG"]["macro_role_tags"] == []
    assert lookup["BNO"]["commodity_role"] == ""
    assert lookup["TLT"]["macro_role_tags"] == []
    assert lookup["UAL"]["macro_role_tags"] == []


def test_build_attention_home_1d_promotes_oil_event_and_keeps_big_single_name_mover_in_must_read():
    def make_bars(today_move_pct: float) -> pd.DataFrame:
        closes = [100.0] * 24
        closes.append(100.0 * (1.0 + float(today_move_pct) / 100.0))
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=len(closes), freq="B"),
                "close": closes,
            }
        )

    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -10.0, "close": 18.0, "prev_close": 20.0, "volume": 2_500_000, "dollar_volume": 45_000_000},
            {"symbol": "USO", "change_pct": -8.8, "close": 68.4, "prev_close": 75.0, "volume": 3_100_000, "dollar_volume": 212_040_000},
            {"symbol": "UAL", "change_pct": 5.1, "close": 56.8, "prev_close": 54.0, "volume": 5_600_000, "dollar_volume": 318_080_000},
            {"symbol": "DAL", "change_pct": 4.7, "close": 48.1, "prev_close": 45.9, "volume": 4_800_000, "dollar_volume": 230_880_000},
            {"symbol": "IWM", "change_pct": 2.9, "close": 224.0, "prev_close": 217.7, "volume": 6_000_000, "dollar_volume": 1_344_000_000},
            {"symbol": "TLT", "change_pct": 1.6, "close": 94.0, "prev_close": 92.5, "volume": 8_000_000, "dollar_volume": 752_000_000},
            {"symbol": "PYPL", "change_pct": 8.4, "close": 78.0, "prev_close": 72.0, "volume": 12_000_000, "dollar_volume": 936_000_000},
        ]
    )
    bars_by_symbol = {symbol: make_bars(move) for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)}
    news_payloads = {
        "BNO": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Oil prices ease as hopes rise for de-escalation with Iran",
                        "summary": "Traders are pricing lower odds of a prolonged supply disruption into crude.",
                        "source": "AP",
                        "published_at": pd.Timestamp("2026-03-23T15:00:00Z"),
                        "url": "https://example.com/oil",
                    }
                ]
            )
        },
        "PYPL": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "PayPal jumps after upbeat checkout and margin commentary",
                        "summary": "Investors focused on better branded-checkout trends and operating leverage.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-03-23T15:30:00Z"),
                        "url": "https://example.com/pypl",
                    }
                ]
            )
        },
    }
    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "IWM": {"macro_role_tags": ["broad_market"]},
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
        "DAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    assert home["top_events"]
    top_event = home["top_events"][0]
    assert top_event["event_type"] == "oil"
    assert "Oil" in top_event["event_title"]
    assert set(top_event["supporting_symbols"]) >= {"BNO", "USO"}

    must_read_symbols = {str(item["symbol"]).upper() for item in home["must_read_movers"]}
    assert "PYPL" in must_read_symbols
    assert "BNO" not in must_read_symbols
    assert "UAL" not in must_read_symbols


def test_build_attention_event_candidates_1d_uses_macro_roles_for_generic_peer_groups():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -9.1, "close": 18.4, "prev_close": 20.2, "volume": 2_100_000, "dollar_volume": 38_640_000},
            {"symbol": "USO", "change_pct": -8.6, "close": 68.7, "prev_close": 75.2, "volume": 3_600_000, "dollar_volume": 247_320_000},
            {"symbol": "TLT", "change_pct": 1.3, "close": 96.8, "prev_close": 95.6, "volume": 6_800_000, "dollar_volume": 658_240_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    candidates = build_attention_event_candidates_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    lookup = candidates.set_index("symbol")
    assert lookup.loc["BNO", "peer_group_name"] == "Oil"
    assert lookup.loc["USO", "peer_group_name"] == "Oil"
    assert lookup.loc["TLT", "peer_group_name"] == "Rates"


def test_build_attention_event_candidates_1d_preserves_security_name_for_graph_labels():
    daily_movers = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "Acme Industrial Holdings Inc.",
                "change_pct": 4.2,
                "close": 120.0,
                "prev_close": 115.2,
                "volume": 1_400_000,
                "dollar_volume": 168_000_000,
            }
        ]
    )
    entity_master = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "Acme Industrial Holdings Inc.",
                "asset_class": "equity",
                "security_type": "common_stock",
                "sector": "Industrials",
                "industry": "Capital Goods",
                "country": "US",
                "commodity_role": "",
                "rates_role": "",
                "defensive_role": "",
                "macro_role_tags": [],
                "business_role_tags": [],
                "source_of_truth": "listing_metadata",
                "override_reason": "unit-test",
            }
        ]
    )

    candidates = build_attention_event_candidates_1d(
        daily_movers,
        bars_by_symbol={},
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert not candidates.empty
    assert candidates.loc[0, "security_name"] == "Acme Industrial Holdings Inc."


def test_build_attention_home_1d_includes_taxonomy_multi_horizon_trend_surface():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -9.1, "close": 18.4, "prev_close": 20.2, "volume": 2_100_000, "dollar_volume": 38_640_000},
            {"symbol": "USO", "change_pct": -8.6, "close": 68.7, "prev_close": 75.2, "volume": 3_600_000, "dollar_volume": 247_320_000},
            {"symbol": "UAL", "change_pct": 4.8, "close": 56.9, "prev_close": 54.3, "volume": 4_200_000, "dollar_volume": 238_980_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    attention_rows = pd.DataFrame(
        [
            {"entity_id": "BNO", "horizon": "1w", "peer_group_name": "Energy & Oil", "attention_score": 79.0, "residual_zscore": 2.4, "direction": "down", "observed_value": -9.1},
            {"entity_id": "USO", "horizon": "1w", "peer_group_name": "Energy & Oil", "attention_score": 76.0, "residual_zscore": 2.1, "direction": "down", "observed_value": -8.6},
            {"entity_id": "UAL", "horizon": "1w", "peer_group_name": "Travel & Mobility", "attention_score": 68.0, "residual_zscore": 1.8, "direction": "up", "observed_value": 4.8},
            {"entity_id": "BNO", "horizon": "1mo", "peer_group_name": "Energy & Oil", "attention_score": 73.0, "residual_zscore": 1.9, "direction": "down", "observed_value": -6.2},
            {"entity_id": "UAL", "horizon": "1mo", "peer_group_name": "Travel & Mobility", "attention_score": 64.0, "residual_zscore": 1.6, "direction": "up", "observed_value": 3.5},
        ]
    )

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    home = build_attention_home_1d(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    trends = list(home.get("taxonomy_horizon_trends") or [])
    assert trends
    horizons = {str(item.get("horizon") or "").strip() for item in trends}
    assert {"1w", "1mo"} <= horizons
    first_horizon = next(item for item in trends if str(item.get("horizon") or "").strip() == "1w")
    assert first_horizon["cohorts"]
    assert "leader" in str(first_horizon["cohorts"][0].get("summary_text") or "").lower()
    assert bool(home.get("coverage_summary", {}).get("supports_multi_horizon"))


def test_build_attention_home_1d_keeps_large_liquid_single_name_movers_in_must_read():
    def make_bars(today_move_pct: float) -> pd.DataFrame:
        closes = [100.0] * 24
        closes.append(100.0 * (1.0 + float(today_move_pct) / 100.0))
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=len(closes), freq="B"),
                "close": closes,
            }
        )

    daily_movers = pd.DataFrame(
        [
            {"symbol": "PYPL", "change_pct": 8.1, "close": 78.0, "prev_close": 72.2, "volume": 10_500_000, "dollar_volume": 819_000_000},
            {"symbol": "QXO", "change_pct": 9.4, "close": 24.0, "prev_close": 21.9, "volume": 7_800_000, "dollar_volume": 187_200_000},
            {"symbol": "MT", "change_pct": 6.7, "close": 31.9, "prev_close": 29.9, "volume": 6_200_000, "dollar_volume": 197_780_000},
        ]
    )
    bars_by_symbol = {symbol: make_bars(move) for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)}
    news_payloads = {
        symbol: {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": f"{symbol} rallies after company-specific update",
                        "summary": f"Fresh company-specific news is behind the move in {symbol}.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-03-23T16:00:00Z"),
                        "url": f"https://example.com/{symbol.lower()}",
                    }
                ]
            )
        }
        for symbol in ["PYPL", "QXO", "MT"]
    }

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads={},
        entity_master=build_attention_entity_master(daily_movers["symbol"].tolist()),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    assert not home["top_events"]
    must_read_symbols = {str(item["symbol"]).upper() for item in home["must_read_movers"]}
    assert {"PYPL", "QXO", "MT"} <= must_read_symbols


def test_build_attention_home_1d_marks_large_low_evidence_moves_as_unresolved():
    def make_bars(today_move_pct: float) -> pd.DataFrame:
        closes = [100.0] * 24
        closes.append(100.0 * (1.0 + float(today_move_pct) / 100.0))
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=len(closes), freq="B"),
                "close": closes,
            }
        )

    daily_movers = pd.DataFrame(
        [
            {"symbol": "APGE", "change_pct": 14.2, "close": 52.5, "prev_close": 46.0, "volume": 1_400_000, "dollar_volume": 73_500_000},
            {"symbol": "XYZ", "change_pct": -5.4, "close": 38.0, "prev_close": 40.2, "volume": 1_800_000, "dollar_volume": 68_400_000},
        ]
    )
    bars_by_symbol = {symbol: make_bars(move) for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)}

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(daily_movers["symbol"].tolist()),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    unresolved_symbols = {str(item["symbol"]).upper() for item in home["unresolved_large_moves"]}
    assert "APGE" in unresolved_symbols
    assert "APGE" not in {str(item["symbol"]).upper() for item in home["must_read_movers"]}
    assert not home["top_events"]


def test_build_attention_home_1d_avoids_generic_tape_titles_and_canned_why_text():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "APGE", "change_pct": 14.2, "close": 52.5, "prev_close": 46.0, "volume": 1_400_000, "dollar_volume": 73_500_000},
        ]
    )
    bars_by_symbol = {
        "APGE": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [114.2],
            }
        )
    }

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["APGE"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    unresolved = home["unresolved_large_moves"][0]
    assert unresolved["headline"] == "APGE"
    assert "today's tape" not in unresolved["headline"].lower()
    assert "moves sharply today" not in unresolved["headline"].lower()
    assert unresolved["why_now_text"] == ""

    bundle = build_attention_research_bundle("symbol::APGE", home, news_payloads={}, context_payloads={})
    assert bundle["headline"] == "APGE"
    assert bundle["why_now_text"] == ""


def test_build_attention_research_bundle_sorts_source_authority_and_aggregates_symbols():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -9.8, "close": 18.2, "prev_close": 20.2, "volume": 2_000_000, "dollar_volume": 36_400_000},
            {"symbol": "USO", "change_pct": -8.1, "close": 68.0, "prev_close": 74.0, "volume": 3_000_000, "dollar_volume": 204_000_000},
            {"symbol": "UAL", "change_pct": 4.9, "close": 56.5, "prev_close": 53.8, "volume": 4_000_000, "dollar_volume": 226_000_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    news_payloads = {
        "BNO": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Oil prices fall on de-escalation hopes",
                        "summary": "The market is pricing lower odds of a supply shock.",
                        "source": "AP",
                        "published_at": pd.Timestamp("2026-03-23T15:00:00Z"),
                        "url": "https://example.com/ap-oil",
                    },
                    {
                        "headline": "BNO slides with crude",
                        "summary": "Broader web coverage echoed the move.",
                        "source": "Benzinga",
                        "published_at": pd.Timestamp("2026-03-23T15:30:00Z"),
                        "url": "https://example.com/benzinga-oil",
                    },
                ]
            )
        }
    }
    context_payloads = {
        "BNO": {
            "llm_headline": "Primary-source read on oil market relief",
            "llm_why_now": "Official market commentary points to easing supply-risk expectations.",
            "llm_source_line": "SEC filing",
            "top_filing_links": [{"label": "8-K filing", "url": "https://example.com/8k"}],
        }
    }

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    assert home["top_events"]
    bundle = build_attention_research_bundle(
        str(home["top_events"][0]["bundle_id"]),
        home,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
    )

    assert bundle["bundle_type"] == "event"
    assert bundle["related_symbols"]
    assert bundle["evidence"]
    assert bundle["evidence"][0]["authority_bucket"] == "official"
    assert bundle["related_symbols"][0]["symbol"] in {"BNO", "USO", "UAL"}


def test_build_live_attention_research_bundle_prefers_same_day_news_and_separates_background_context():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "FSLY", "change_pct": 13.7, "close": 14.2, "prev_close": 12.5, "volume": 8_000_000, "dollar_volume": 113_600_000},
        ]
    )
    bars_by_symbol = {
        "FSLY": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [113.7],
            }
        )
    }
    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["FSLY"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )
    merged_news = merge_news_payloads(
        {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Why is Fastly (FSLY) stock rocketing higher today",
                        "summary": "Investors are extending the post-earnings rerating in Fastly while momentum traders add to the move.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-03-24T15:30:00Z"),
                        "url": "https://example.com/fsly-reuters",
                    }
                ]
            ),
            "fallback_summary": None,
            "source": "alpaca",
        }
    )
    filings_frame = pd.DataFrame(
        [
            {
                "symbol": "FSLY",
                "filing_date": pd.Timestamp("2026-03-05T00:00:00Z"),
                "form": "8-K",
                "items": "4.01",
                "primary_doc_description": "Auditor change",
                "filing_url": "https://example.com/fsly-8k",
                "filing_excerpt": "Fastly disclosed an auditor transition and related governance updates.",
                "document_text": "Fastly disclosed an auditor transition and related governance updates.",
            }
        ]
    )

    bundle = build_live_attention_research_bundle(
        "symbol::FSLY",
        home,
        news_payloads={"FSLY": merged_news},
        context_payloads={"FSLY": {"llm_why_now": "Earlier SEC context pointed to an auditor change."}},
        filings_frame=filings_frame,
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert bundle["bundle_type"] == "symbol"
    assert bundle["cause_status"] == "supported"
    assert bundle["why_today_mode"] in {"fresh_catalyst", "same_day_confirmation"}
    assert bundle["freshness_quality"] == "High"
    assert bundle["evidence_quality"] == "Medium"
    assert bundle["evidence"]
    assert bundle["evidence"][0]["source"] == "Reuters"
    assert bundle["background_context"]
    assert any(item["source"] == "SEC EDGAR" for item in bundle["background_context"])
    assert "No clear same-day peer or cross-asset spillover was confirmed." in bundle["what_else_moved_text"]


def test_build_live_attention_research_bundle_event_prefers_macro_evidence_over_symbol_context():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -9.5, "close": 18.3, "prev_close": 20.2, "volume": 2_200_000, "dollar_volume": 40_260_000},
            {"symbol": "USO", "change_pct": -9.0, "close": 68.2, "prev_close": 75.0, "volume": 4_400_000, "dollar_volume": 300_080_000},
            {"symbol": "UAL", "change_pct": 4.7, "close": 57.4, "prev_close": 54.8, "volume": 6_100_000, "dollar_volume": 350_140_000},
            {"symbol": "DAL", "change_pct": 4.3, "close": 49.8, "prev_close": 47.7, "volume": 5_600_000, "dollar_volume": 278_880_000},
            {"symbol": "IWM", "change_pct": 2.0, "close": 217.0, "prev_close": 212.7, "volume": 8_000_000, "dollar_volume": 1_736_000_000},
            {"symbol": "TLT", "change_pct": 1.4, "close": 96.3, "prev_close": 95.0, "volume": 7_400_000, "dollar_volume": 712_620_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    news_payloads = {
        "BNO": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "BNO slides with crude benchmark",
                        "summary": "The ETF tracked lower oil prices.",
                        "source": "Benzinga",
                        "published_at": pd.Timestamp("2026-03-24T15:20:00Z"),
                        "url": "https://example.com/bno",
                    }
                ]
            )
        }
    }
    context_payloads = {
        "BNO": {
            "llm_why_now": "Oil participants fear fuel shortages, shifting from an oversupply outlook.",
            "llm_source_line": "Primary source",
        }
    }

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "IWM": {"macro_role_tags": ["broad_market"]},
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
        "DAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )
    top_event = next(item for item in home["top_events"] if item["event_type"] == "oil")
    bundle_id = str(top_event["bundle_id"])
    event_search_payload = {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "Oil prices fall on Iran de-escalation hopes",
                    "summary": "Traders are pricing lower odds of a supply disruption into crude, while airlines and bonds gain.",
                    "source": "AP",
                    "published_at": pd.Timestamp("2026-03-24T15:00:00Z"),
                    "url": "https://example.com/ap-oil",
                },
                {
                    "headline": "Crude drops as market relief spreads across airlines and Treasuries",
                    "summary": "Lower oil and firmer duration suggest easing inflation pressure.",
                    "source": "Reuters",
                    "published_at": pd.Timestamp("2026-03-24T15:10:00Z"),
                    "url": "https://example.com/reuters-oil",
                },
            ]
        ),
        "fallback_summary": None,
        "source": "event-search",
    }

    bundle = build_live_attention_research_bundle(
        bundle_id,
        home,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        search_payloads={bundle_id: event_search_payload},
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert bundle["bundle_type"] == "event"
    assert bundle["cause_status"] == "supported"
    assert bundle["freshness_quality"] == "High"
    assert "AP" in bundle["source_summary"]
    assert "fuel shortages" not in bundle["why_happened_text"].lower()
    assert "the tape reads this as" not in bundle["why_happened_text"].lower()
    assert any(token in bundle["why_happened_text"].lower() for token in ["de-escalation", "supply disruption", "supply-risk", "inflation pressure"])


def test_build_live_attention_research_bundle_rates_event_adds_numeric_context():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "TLT", "change_pct": 1.6, "close": 97.4, "prev_close": 95.9, "volume": 7_200_000, "dollar_volume": 701_280_000},
            {"symbol": "IEF", "change_pct": 0.8, "close": 95.0, "prev_close": 94.2, "volume": 5_600_000, "dollar_volume": 532_000_000},
            {"symbol": "HYG", "change_pct": 1.0, "close": 78.8, "prev_close": 78.0, "volume": 6_000_000, "dollar_volume": 472_800_000},
            {"symbol": "IWM", "change_pct": 2.1, "close": 219.0, "prev_close": 214.5, "volume": 7_400_000, "dollar_volume": 1_620_600_000},
            {"symbol": "GLD", "change_pct": -0.9, "close": 231.5, "prev_close": 233.6, "volume": 4_100_000, "dollar_volume": 949_150_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "IEF": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "HYG": {"macro_role_tags": ["credit"]},
        "IWM": {"macro_role_tags": ["broad_market"]},
        "GLD": {"commodity_role": "gold", "macro_role_tags": ["gold"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )
    rates_event = next(item for item in home["top_events"] if item["event_type"] == "rates")
    bundle = build_live_attention_research_bundle(
        str(rates_event["bundle_id"]),
        home,
        search_payloads={
            str(rates_event["bundle_id"]): {
                "articles": pd.DataFrame(
                    [
                        {
                            "headline": "Treasuries rally as softer yields lift rate-sensitive stocks",
                            "summary": "Lower yields helped credit and small caps firm with bonds.",
                            "source": "Reuters",
                            "published_at": pd.Timestamp("2026-03-24T15:15:00Z"),
                            "url": "https://example.com/reuters-rates",
                        }
                    ]
                ),
                "fallback_summary": None,
                "source": "event-search",
            }
        },
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    why_text = bundle["why_happened_text"].lower()
    assert "tlt +1.6%" not in why_text
    assert "ief +0.8%" not in why_text
    assert "bps" not in why_text
    assert "yields" in why_text or "curve" in why_text
    assert "the tape reads this as" not in why_text


def test_fallback_event_writer_non_rates_prefers_mechanism_claim_over_yield_stat_dump():
    cluster_rows = pd.DataFrame(
        [
            {
                "symbol": "USO",
                "change_pct": 5.95,
                "candidate_score": 92.0,
                "abs_change_pct": 5.95,
                "sector": "Energy",
                "industry": "Oil & Gas",
                "macro_exposure_tags": ["oil"],
                "business_tags": [],
            },
            {
                "symbol": "UAL",
                "change_pct": -4.61,
                "candidate_score": 88.0,
                "abs_change_pct": 4.61,
                "sector": "Industrials",
                "industry": "Airlines",
                "macro_exposure_tags": ["travel"],
                "business_tags": ["travel_mobility"],
            },
        ]
    )
    retained_claims = [
        {
            "claim_text": (
                "US Treasury yields showed mixed moves with front-end rates falling and long-end yields rising: "
                "the 2Y dropped 8 bps to 3.88% while the 10Y rose 2 bps to 4.44% and the 30Y rose 5 bps to 4.98%."
            ),
            "is_same_day": True,
            "causal_score": 0.20,
            "confidence_score": 0.80,
            "relevance_score": 0.80,
        },
        {
            "claim_text": (
                "Crude rose on supply-risk headlines, which raised expected jet fuel costs and pressured airline "
                "margin assumptions while supporting energy cash-flow expectations."
            ),
            "is_same_day": True,
            "causal_score": 0.92,
            "confidence_score": 0.87,
            "relevance_score": 0.91,
        },
    ]
    yield_facts = {
        "ust_2y": 3.88,
        "ust_10y": 4.44,
        "ust_30y": 4.98,
        "ust_2y_1d_bps": -8.0,
        "ust_10y_1d_bps": 2.0,
        "ust_30y_1d_bps": 5.0,
        "curve_2s10s": 0.56,
        "curve_2s10s_1d_bps": 10.0,
    }

    bundle = attention_agentic_module._fallback_event_writer(
        "Airlines lower, Energy higher",
        cluster_rows,
        retained_claims,
        yield_facts=yield_facts,
        event_type="oil",
    )

    why_text = bundle["why_happened_text"].lower()
    assert "fuel" in why_text or "margin" in why_text
    assert "2y" not in why_text
    assert "2s10s" not in why_text


def test_fallback_event_writer_rates_event_can_include_yield_context():
    cluster_rows = pd.DataFrame(
        [
            {
                "symbol": "TLT",
                "change_pct": 1.6,
                "candidate_score": 91.0,
                "abs_change_pct": 1.6,
                "sector": "Fixed Income",
                "industry": "Treasury ETF",
                "macro_exposure_tags": ["rates"],
                "business_tags": [],
            },
            {
                "symbol": "IEF",
                "change_pct": 0.8,
                "candidate_score": 84.0,
                "abs_change_pct": 0.8,
                "sector": "Fixed Income",
                "industry": "Treasury ETF",
                "macro_exposure_tags": ["rates"],
                "business_tags": [],
            },
        ]
    )
    yield_facts = {
        "ust_2y": 3.88,
        "ust_10y": 4.44,
        "ust_2y_1d_bps": -8.0,
        "ust_10y_1d_bps": 2.0,
        "curve_2s10s": 0.56,
        "curve_2s10s_1d_bps": 10.0,
    }

    bundle = attention_agentic_module._fallback_event_writer(
        "Rates higher",
        cluster_rows,
        [],
        yield_facts=yield_facts,
        event_type="rates",
    )
    why_text = bundle["why_happened_text"].lower()
    assert "bps" not in why_text
    assert "yields" in why_text


def test_write_event_bundle_replaces_tape_recap_what_happened_with_directional_summary():
    cluster_rows = pd.DataFrame(
        [
            {
                "symbol": "USO",
                "change_pct": 5.95,
                "candidate_score": 92.0,
                "abs_change_pct": 5.95,
                "sector": "Energy",
                "industry": "Oil & Gas",
                "macro_exposure_tags": ["oil"],
                "business_tags": [],
            },
            {
                "symbol": "UAL",
                "change_pct": -4.61,
                "candidate_score": 88.0,
                "abs_change_pct": 4.61,
                "sector": "Industrials",
                "industry": "Airlines",
                "macro_exposure_tags": ["travel"],
                "business_tags": ["travel_mobility"],
            },
        ]
    )
    retained_claims = [
        {
            "claim_text": (
                "Crude rose on supply-risk headlines, which raised expected jet fuel costs and pressured airline "
                "margin assumptions while supporting energy cash-flow expectations."
            ),
            "is_same_day": True,
            "causal_score": 0.92,
            "confidence_score": 0.87,
            "relevance_score": 0.91,
        }
    ]

    class _StatRecapLLM:
        def generate_json(self, **kwargs):
            return {
                "title": "Airlines lower, Energy higher",
                "surface_summary": "",
                "what_happened_text": (
                    "Energy and crude-linked products advanced with USO +5.95%, BNO +4.35%, and XOM +3.36%, "
                    "while travel equities fell including UAL -4.61% and LUV -5.45%."
                ),
                "why_happened_text": "Higher crude fed into fuel-cost expectations and pressured airline margin assumptions.",
                "affected_assets_summary_text": "Cross-asset spillover remained split between energy winners and travel laggards.",
                "background_context_text": "",
            }

    bundle = attention_agentic_module._write_event_bundle(
        "Airlines lower, Energy higher",
        cluster_rows,
        retained_claims,
        llm_client=_StatRecapLLM(),
        yield_facts={},
        event_type="oil",
    )

    what_text = bundle["what_happened_text"].lower()
    assert "%" not in what_text
    assert "moved" in what_text


def test_write_event_bundle_uses_mechanism_first_prompt_and_rich_payload():
    cluster_rows = pd.DataFrame(
        [
            {
                "symbol": "USO",
                "change_pct": -5.95,
                "candidate_score": 92.0,
                "abs_change_pct": 5.95,
                "sector": "Energy",
                "industry": "Oil & Gas",
                "asset_class": "commodity_proxy",
                "macro_exposure_tags": ["oil"],
                "business_tags": [],
                "commodity_role": "oil",
            },
            {
                "symbol": "UAL",
                "change_pct": 4.61,
                "candidate_score": 88.0,
                "abs_change_pct": 4.61,
                "sector": "Industrials",
                "industry": "Airlines",
                "asset_class": "equity",
                "macro_exposure_tags": ["travel"],
                "business_tags": ["travel_mobility"],
            },
            {
                "symbol": "TLT",
                "change_pct": 1.42,
                "candidate_score": 79.0,
                "abs_change_pct": 1.42,
                "sector": "Fixed Income",
                "industry": "Treasury ETF",
                "asset_class": "fixed_income",
                "macro_exposure_tags": ["rates"],
                "business_tags": ["duration"],
                "rates_role": "rates",
            },
        ]
    )
    retained_claims = [
        {
            "claim_text": (
                "De-escalation headlines reduced perceived supply-disruption risk, which lowered crude and eased expected "
                "jet fuel cost pressure for airlines."
            ),
            "claim_type": "mechanism",
            "source": "Reuters",
            "source_authority_bucket": "high",
            "is_same_day": True,
            "freshness_class": "same_day",
            "supports_hypothesis": "lower fuel-cost pressure helps airlines",
            "claim_entities": ["crude", "airlines"],
            "relevance_score": 0.93,
            "causal_score": 0.92,
            "confidence_score": 0.88,
        },
        {
            "claim_text": "Treasuries firmed as lower oil reduced near-term inflation pressure and improved duration sentiment.",
            "claim_type": "cross_asset",
            "source": "AP",
            "source_authority_bucket": "high",
            "is_same_day": True,
            "freshness_class": "same_day",
            "supports_hypothesis": "lower inflation pressure helps duration",
            "claim_entities": ["treasuries", "inflation"],
            "relevance_score": 0.82,
            "causal_score": 0.71,
            "confidence_score": 0.77,
        },
    ]

    captured: dict[str, str] = {}

    class _CapturingLLM:
        def generate_json(self, **kwargs):
            captured["system_prompt"] = kwargs["system_prompt"]
            captured["user_prompt"] = kwargs["user_prompt"]
            return {
                "title": "Crude lower, airlines catch relief bid",
                "surface_summary": (
                    "Oil-linked assets fell while travel-sensitive names and Treasuries benefited. "
                    "Lower perceived supply risk eased fuel-cost and inflation pressure."
                ),
                "what_happened_text": (
                    "Oil-linked assets moved lower while airlines and Treasuries held up better as the cluster rotated away "
                    "from crude exposure."
                ),
                "why_happened_text": (
                    "De-escalation headlines reduced perceived supply-disruption risk, which lowered expected fuel-cost pressure "
                    "and improved the market's view of airline margins and inflation-sensitive duration trades."
                ),
                "affected_assets_summary_text": (
                    "The spillover reached travel and duration-sensitive assets while crude proxies led the downside."
                ),
                "background_context_text": "",
            }

    bundle = attention_agentic_module._write_event_bundle(
        "Oil lower while airlines rebound",
        cluster_rows,
        retained_claims,
        llm_client=_CapturingLLM(),
        yield_facts={"ust_10y_1d_bps": -6.0},
        event_type="oil",
        cause_status="supported",
        evidence_quality="High",
        freshness_quality="High",
    )

    payload = json.loads(captured["user_prompt"])

    assert "senior cross-asset strategist" in captured["system_prompt"].lower()
    assert "catalyst -> transmission channel -> market pricing reaction" in captured["system_prompt"]
    assert payload["cause_status"] == "supported"
    assert payload["evidence_quality"] == "High"
    assert payload["freshness_quality"] == "High"
    assert payload["event_type"] == "oil"
    assert payload["cluster_context"]["anchor_symbol"] == "USO"
    assert "oil" in payload["cluster_context"]["dominant_tags"]
    assert "travel" in payload["cluster_context"]["dominant_tags"]
    assert payload["claims"][0]["causal_score"] == 0.92
    assert payload["claims"][0]["claim_entities"] == ["crude", "airlines"]
    assert bundle["title"] == "Crude lower, airlines catch relief bid"
    assert "supply-disruption risk" in bundle["why_happened_text"]


def test_documents_from_search_results_skip_provider_error_rows():
    candidate = {"candidate_id": "candidate::HOOD", "symbol": "HOOD"}
    docs = attention_agentic_module._documents_from_search_results(
        candidate,
        [
            {
                "result_id": "query::1::tavily::error",
                "title": "",
                "snippet": "Tavily request failed status=432: {\"detail\":{\"error\":\"This request exceeds your plan's set usage limit.\"}}",
                "source": "tavily",
                "provider": "tavily",
                "published_at": "",
                "authority_bucket": "web",
                "authority_rank": 3,
                "query_id": "query::1",
            },
            {
                "result_id": "query::1::serpapi::ok",
                "title": "Retail flows stabilize after stronger account growth",
                "snippet": "Robinhood shares rose as investors priced better engagement and deposit momentum.",
                "source": "Reuters",
                "provider": "serpapi",
                "published_at": "2026-03-31T01:00:00Z",
                "authority_bucket": "wire",
                "authority_rank": 1,
                "query_id": "query::1",
            },
        ],
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-03-31T02:00:00Z"),
    )

    assert len(docs) == 1
    assert docs[0]["title"] == "Retail flows stabilize after stronger account growth"
    assert "usage limit" not in docs[0]["raw_text"].lower()


def test_documents_from_search_results_uses_headline_when_snippet_missing():
    candidate = {"candidate_id": "candidate::BMY", "symbol": "BMY"}
    title = "Bristol-Myers Squibb reports positive late-stage trial data"
    docs = attention_agentic_module._documents_from_search_results(
        candidate,
        [
            {
                "result_id": "query::1::serpapi::ok",
                "title": title,
                "snippet": "",
                "source": "Yahoo Finance",
                "provider": "serpapi",
                "published_at": "2026-04-03T01:00:00Z",
                "authority_bucket": "press",
                "authority_rank": 2,
                "query_id": "query::1",
            }
        ],
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-04-03T02:00:00Z"),
    )

    assert len(docs) == 1
    assert docs[0]["raw_text"] == title


def test_search_query_results_uses_tavily_rag_fallback_when_serp_is_irrelevant():
    class _SerpClient:
        def search(self, query, *, news=False, num=10):
            return [
                WebSearchResult(
                    provider="serpapi",
                    title="Iridium director gets 58.5 share dividend equivalents",
                    url="https://example.com/irdm-dividend-equivalent",
                    snippet="Form 4 filing about director dividend-equivalent rights.",
                    source="Stock Titan",
                    published_at="2026-04-03T08:00:00Z",
                )
            ]

    class _TavilyClient:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def search(self, query, *, max_results=10, topic=None):
            self.calls.append({"query": query, "max_results": max_results, "topic": topic})
            return [
                WebSearchResult(
                    provider="tavily",
                    title="Iridium secures new U.S. government communications award",
                    url="https://example.com/irdm-contract",
                    snippet="The award expands protected satcom capacity for defense use.",
                    source="Reuters",
                    published_at="2026-04-03T10:00:00Z",
                )
            ]

    class _RouterLLM:
        def __init__(self):
            self.relevance_calls = 0

        def generate_json(self, **kwargs):
            schema_name = kwargs.get("schema_name")
            if schema_name == "attention_search_relevance":
                self.relevance_calls += 1
                if self.relevance_calls == 1:
                    return {"relevant_indices": [], "reason": "Noisy insider/form-4 coverage."}
                return {"relevant_indices": [0], "reason": "Material contract update."}
            return {
                "use_tavily": True,
                "tavily_topic": "general",
                "tavily_query": "Iridium Communications latest government contract and guidance developments",
                "reason": "Serp results are insider/noise.",
            }

    tavily = _TavilyClient()
    router_llm = _RouterLLM()
    request_rows, result_rows = attention_agentic_module._search_query_results(
        "IRDM stock move today",
        candidate_id="candidate::IRDM",
        symbol="IRDM",
        company_name="Iridium Communications",
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-04-03T12:00:00Z"),
        serp_client=_SerpClient(),
        tavily_client=tavily,
        llm_client=router_llm,
        budget=4,
    )

    assert any(row.get("provider") == "tavily" for row in request_rows)
    tavily_request = next(row for row in request_rows if row.get("provider") == "tavily")
    assert tavily_request["route_mode"] == "rag_fallback"
    assert tavily_request["topic"] == "general"
    assert tavily.calls and tavily.calls[0]["topic"] == "general"
    assert any(row.get("provider") == "tavily" and "government communications award" in str(row.get("title")).lower() for row in result_rows)


def test_search_query_results_skips_tavily_when_serp_is_relevant():
    class _SerpClient:
        def search(self, query, *, news=False, num=10):
            return [
                WebSearchResult(
                    provider="serpapi",
                    title="Iridium Communications (IRDM) raises full-year outlook after stronger subscriber growth",
                    url="https://example.com/irdm-outlook",
                    snippet="Management raised guidance after stronger commercial demand trends.",
                    source="Reuters",
                    published_at="2026-04-03T09:00:00Z",
                )
            ]

    class _TavilyClient:
        def __init__(self):
            self.calls = 0

        def search(self, query, *, max_results=10, topic=None):
            self.calls += 1
            return []

    class _RelevantLLM:
        def generate_json(self, **kwargs):
            schema_name = kwargs.get("schema_name")
            if schema_name == "attention_search_relevance":
                return {"relevant_indices": [0], "reason": "Company-specific catalyst coverage."}
            return {
                "use_tavily": False,
                "tavily_topic": "news",
                "tavily_query": "IRDM stock move today",
                "reason": "Serp already contains relevant coverage.",
            }

    tavily = _TavilyClient()
    request_rows, result_rows = attention_agentic_module._search_query_results(
        "IRDM stock move today",
        candidate_id="candidate::IRDM",
        symbol="IRDM",
        company_name="Iridium Communications",
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-04-03T12:00:00Z"),
        serp_client=_SerpClient(),
        tavily_client=tavily,
        llm_client=_RelevantLLM(),
        budget=4,
    )

    assert tavily.calls == 0
    assert all(row.get("provider") != "tavily" for row in request_rows)
    assert any(row.get("provider") == "serpapi" for row in result_rows)


def test_fallback_claims_from_chunks_skip_provider_error_chunks():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "chunk::1",
                "title": "tavily error",
                "display_excerpt": "Tavily request failed status=432: {\"detail\":{\"error\":\"This request exceeds your plan's set usage limit.\"}}",
                "chunk_text": "Tavily request failed status=432: {\"detail\":{\"error\":\"This request exceeds your plan's set usage limit.\"}}",
                "published_at": pd.Timestamp("2026-03-31T00:00:00Z"),
                "authority_rank": 3,
                "source_authority_bucket": "web",
                "source_provider": "tavily",
            },
            {
                "chunk_id": "chunk::2",
                "title": "Customer balances grew",
                "display_excerpt": "Customer balances grew faster than expected, which improved revenue expectations for the brokerage platform.",
                "chunk_text": "Customer balances grew faster than expected, which improved revenue expectations for the brokerage platform.",
                "published_at": pd.Timestamp("2026-03-31T00:00:00Z"),
                "authority_rank": 1,
                "source_authority_bucket": "wire",
                "source_provider": "Reuters",
            },
        ]
    )

    claims = attention_agentic_module._fallback_claims_from_chunks(
        {"symbol": "HOOD", "macro_exposure_tags": []},
        chunks,
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-03-31T02:00:00Z"),
        hypotheses=[{"kind": "company_specific"}],
    )

    assert len(claims) == 1
    assert claims[0]["source"] == "Reuters"
    assert "usage limit" not in claims[0]["claim_text"].lower()


def test_chunk_source_documents_skip_low_signal_analyst_rating_snippets():
    chunks = attention_agentic_module._chunk_source_documents(
        [
            {
                "candidate_id": "candidate::BSX",
                "bundle_subject": "BSX",
                "document_id": "doc::BSX::news::1",
                "source_provider": "benzinga",
                "source_authority_bucket": "press",
                "authority_rank": 2,
                "title": "Analyst roundup",
                "url": "https://example.com/analyst-roundup",
                "published_at": pd.Timestamp("2026-03-31T00:00:00Z"),
                "raw_text": (
                    "Top Wall Street analysts changed outlook on top names. "
                    "For all changes, including upgrades/downgrades, see analyst ratings page. "
                    "Orders recovered and margin expectations improved for the medtech supplier."
                ),
            }
        ],
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-03-31T02:00:00Z"),
    )

    assert len(chunks) == 1
    assert "orders recovered" in chunks.iloc[0]["chunk_text"].lower()
    assert "analyst ratings page" not in chunks.iloc[0]["chunk_text"].lower()


def test_build_live_attention_research_bundle_filters_irrelevant_roundups_and_marks_unresolved():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "FSLY", "change_pct": 11.2, "close": 13.9, "prev_close": 12.5, "volume": 6_400_000, "dollar_volume": 88_960_000},
        ]
    )
    bars_by_symbol = {
        "FSLY": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [111.2],
            }
        )
    }
    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["FSLY"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    irrelevant_roundup = {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "Apogee Therapeutics, Tower Semiconductor, Norwegian Cruise Line And Other Big Stocks Moving Higher On Thursday",
                    "summary": "APGE and several other names were among notable movers.",
                    "source": "Benzinga",
                    "published_at": pd.Timestamp("2026-03-24T14:45:00Z"),
                    "url": "https://example.com/roundup",
                }
            ]
        ),
        "fallback_summary": None,
        "source": "alpaca",
    }

    bundle = build_live_attention_research_bundle(
        "symbol::FSLY",
        home,
        news_payloads={"FSLY": irrelevant_roundup},
        context_payloads={},
        filings_frame=pd.DataFrame(),
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert bundle["cause_status"] == "unresolved"
    assert bundle["evidence"] == []
    assert bundle["background_context"] == []
    assert "No clear new company-specific catalyst" in bundle["why_now_text"]


def test_bottom_up_attention_artifacts_use_dynamic_event_titles_instead_of_generic_cluster_copy():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -8.9, "close": 25.0, "prev_close": 27.44, "volume": 2_000_000, "dollar_volume": 50_000_000},
            {"symbol": "USO", "change_pct": -7.4, "close": 70.0, "prev_close": 75.59, "volume": 4_000_000, "dollar_volume": 280_000_000},
            {"symbol": "UAL", "change_pct": 4.6, "close": 45.0, "prev_close": 43.02, "volume": 5_000_000, "dollar_volume": 225_000_000},
            {"symbol": "DAL", "change_pct": 4.1, "close": 53.0, "prev_close": 50.91, "volume": 4_500_000, "dollar_volume": 238_500_000},
            {"symbol": "TLT", "change_pct": 1.5, "close": 97.0, "prev_close": 95.57, "volume": 6_000_000, "dollar_volume": 582_000_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }

    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
        "DAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]

    artifacts = build_bottom_up_attention_artifacts(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
        llm_client=None,
    )

    top_event = artifacts.home_payload["top_events"][0]
    assert "related assets move together today" not in top_event["event_title"].lower()
    assert top_event["event_type"] == "oil"
    assert top_event["event_title"].startswith("Oil lower")
    assert "oil moved lower today" in top_event["surface_summary_text"].lower()


def test_bottom_up_attention_artifacts_promotes_high_importance_macro_release_into_top_events():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "TLT", "change_pct": 1.8, "close": 97.6, "prev_close": 95.9, "volume": 6_400_000, "dollar_volume": 624_640_000},
        ]
    )
    bars_by_symbol = {
        "TLT": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [101.8],
            }
        )
    }
    fred_summary_frame = pd.DataFrame(
        [
            {
                "series_id": "UNRATE",
                "indicator": "Unemployment Rate",
                "units_short": "Percent",
                "latest_value": 4.4,
                "prev_delta": 0.2,
                "last_updated": pd.Timestamp("2026-03-24T13:31:00Z"),
                "latest_date": pd.Timestamp("2026-03-01"),
            }
        ]
    )

    artifacts = build_bottom_up_attention_artifacts(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["TLT"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
        fred_summary_frame=fred_summary_frame,
        llm_client=None,
        top_events_limit=1,
    )

    top_events = list(artifacts.home_payload.get("top_events") or [])
    macro_event = next(item for item in top_events if str(item.get("event_type")) == "macro_release")
    assert macro_event["importance_tier"] == "high"
    assert macro_event["is_forced_macro_release"] is True
    assert float(macro_event["surprise_score"] or 0.0) >= 60.0
    assert not artifacts.frames["macro_release_events_1d"].empty
    assert int(artifacts.home_payload.get("coverage_summary", {}).get("macro_release_promoted_count") or 0) >= 1


def test_bottom_up_attention_artifacts_does_not_suppress_forced_macro_release_when_event_slots_are_full():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "BNO", "change_pct": -8.9, "close": 25.0, "prev_close": 27.44, "volume": 2_000_000, "dollar_volume": 50_000_000},
            {"symbol": "USO", "change_pct": -7.4, "close": 70.0, "prev_close": 75.59, "volume": 4_000_000, "dollar_volume": 280_000_000},
            {"symbol": "UAL", "change_pct": 4.6, "close": 45.0, "prev_close": 43.02, "volume": 5_000_000, "dollar_volume": 225_000_000},
            {"symbol": "DAL", "change_pct": 4.1, "close": 53.0, "prev_close": 50.91, "volume": 4_500_000, "dollar_volume": 238_500_000},
            {"symbol": "TLT", "change_pct": 1.2, "close": 97.0, "prev_close": 95.85, "volume": 6_000_000, "dollar_volume": 582_000_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    entity_master = build_attention_entity_master(daily_movers["symbol"].tolist()).copy()
    macro_overrides = {
        "BNO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "USO": {"commodity_role": "oil", "macro_role_tags": ["oil"]},
        "TLT": {"rates_role": "rates", "macro_role_tags": ["rates"]},
        "UAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
        "DAL": {"industry": "Airlines", "business_role_tags": ["travel_mobility"], "macro_role_tags": ["travel"]},
    }
    for symbol, payload in macro_overrides.items():
        mask = entity_master["symbol"] == symbol
        for column, value in payload.items():
            entity_master.loc[mask, column] = [value]
    fred_summary_frame = pd.DataFrame(
        [
            {
                "series_id": "CPIAUCSL",
                "indicator": "Headline CPI",
                "units_short": "Index 1982-1984=100",
                "latest_value": 326.6,
                "prev_delta": 0.55,
                "last_updated": pd.Timestamp("2026-03-24T13:31:00Z"),
                "latest_date": pd.Timestamp("2026-03-01"),
            }
        ]
    )

    artifacts = build_bottom_up_attention_artifacts(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=entity_master,
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
        fred_summary_frame=fred_summary_frame,
        llm_client=None,
        top_events_limit=1,
    )

    top_events = list(artifacts.home_payload.get("top_events") or [])
    event_types = {str(item.get("event_type")) for item in top_events}
    assert "oil" in event_types
    assert "macro_release" in event_types
    assert len(top_events) >= 2


def test_bottom_up_attention_artifacts_uses_runtime_macro_profile_for_release_mapping(monkeypatch):
    with TemporaryDirectory() as tmp_dir:
        profile_path = Path(tmp_dir) / "attention_macro_signal_profile.v1.yaml"
        profile_path.write_text(
            "\n".join(
                [
                    "release_display_names:",
                    "  money_supply: M2 money supply release",
                    "release_components:",
                    "  M2SL:",
                    "    release_type: money_supply",
                    "    component_label: M2 Supply",
                    "    importance_tier: high",
                    "    primary_nodes: [liquidity, rates, usd, equity_duration]",
                    "release_rules:",
                    "  surprise_threshold: 0",
                    "  reaction_cap_move: 1.0",
                    "  freshness_hours: 72",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ATTENTION_MACRO_SIGNAL_PROFILE_PATH", str(profile_path))

        daily_movers = pd.DataFrame(
            [
                {"symbol": "SPY", "change_pct": 1.1, "close": 505.0, "prev_close": 499.5, "volume": 50_000_000, "dollar_volume": 25_250_000_000},
            ]
        )
        bars_by_symbol = {
            "SPY": pd.DataFrame(
                {
                    "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                    "close": [100.0] * 24 + [101.1],
                }
            )
        }
        fred_summary_frame = pd.DataFrame(
            [
                {
                    "series_id": "M2SL",
                    "indicator": "M2",
                    "units_short": "Billions of Dollars",
                    "latest_value": 22_300.0,
                    "prev_delta": 120.0,
                    "last_updated": pd.Timestamp("2026-03-24T13:31:00Z"),
                    "latest_date": pd.Timestamp("2026-03-01"),
                }
            ]
        )

        artifacts = build_bottom_up_attention_artifacts(
            daily_movers,
            bars_by_symbol=bars_by_symbol,
            news_payloads={},
            context_payloads={},
            entity_master=build_attention_entity_master(["SPY"]),
            holdings=[],
            generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
            fred_summary_frame=fred_summary_frame,
            llm_client=None,
            top_events_limit=1,
        )

        release_frame = artifacts.frames["macro_release_events_1d"]
        assert not release_frame.empty
        assert release_frame["release_type"].astype(str).str.lower().eq("money_supply").any()
        top_events = list(artifacts.home_payload.get("top_events") or [])
        macro_event = next(item for item in top_events if str(item.get("event_type")) == "macro_release")
        assert str(macro_event.get("release_type")) == "money_supply"


def test_bottom_up_attention_artifacts_builds_macro_relationship_checks_and_hypotheses():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "TLT", "change_pct": 1.8, "close": 97.6, "prev_close": 95.9, "volume": 6_400_000, "dollar_volume": 624_640_000},
            {"symbol": "UUP", "change_pct": -1.1, "close": 27.5, "prev_close": 27.81, "volume": 8_200_000, "dollar_volume": 225_500_000},
            {"symbol": "QQQ", "change_pct": 1.3, "close": 498.0, "prev_close": 491.6, "volume": 39_000_000, "dollar_volume": 19_422_000_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    fred_summary_frame = pd.DataFrame(
        [
            {
                "series_id": "UNRATE",
                "indicator": "Unemployment Rate",
                "units_short": "Percent",
                "latest_value": 4.4,
                "prev_delta": 0.2,
                "last_updated": pd.Timestamp("2026-03-24T13:31:00Z"),
                "latest_date": pd.Timestamp("2026-03-01"),
            }
        ]
    )

    artifacts = build_bottom_up_attention_artifacts(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["TLT", "UUP", "QQQ"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
        fred_summary_frame=fred_summary_frame,
        llm_client=None,
        top_events_limit=1,
    )

    checks = artifacts.frames["macro_relationship_checks_1d"]
    edges = artifacts.frames["macro_causal_graph_edges_v1"]
    macro_context = artifacts.frames["attention_macro_context_1d"]
    hypotheses = artifacts.frames["attention_hypotheses_1d"]
    assert not edges.empty
    assert not checks.empty
    assert not hypotheses.empty
    assert not macro_context.empty
    assert "consistency_status" in checks.columns
    assert checks["consistency_status"].astype(str).str.lower().isin({"holding", "mixed", "broken", "unresolved"}).all()
    assert checks["consistency_status"].astype(str).str.lower().eq("holding").any()
    assert hypotheses["support_status"].astype(str).str.lower().isin({"supported", "continuation", "conflicting", "unresolved"}).all()
    macro_event = next(item for item in (artifacts.home_payload.get("top_events") or []) if str(item.get("event_type")) == "macro_release")
    assert int(macro_event.get("relationship_holding_count") or 0) >= 0
    assert str(macro_event.get("hypothesis_status") or "").lower() in {"supported", "continuation", "conflicting", "unresolved"}


def test_bottom_up_attention_artifacts_verifies_macro_hypotheses_with_search(monkeypatch):
    class _SerpClient:
        def search(self, query: str, *, news: bool = False, num: int = 10):
            del query, news, num
            return [
                WebSearchResult(
                    provider="serpapi",
                    title="Jobs cooling lifts rate-cut expectations and supports growth stocks",
                    url="https://example.com/macro-release",
                    snippet="Treasury proxies rallied, the dollar softened, and duration-sensitive equities gained after labor cooled.",
                    source="Reuters",
                    published_at="2026-03-24T14:00:00Z",
                )
            ]

    monkeypatch.setattr("services.attention_agentic._load_search_clients", lambda: (_SerpClient(), None))

    daily_movers = pd.DataFrame(
        [
            {"symbol": "TLT", "change_pct": 1.8, "close": 97.6, "prev_close": 95.9, "volume": 6_400_000, "dollar_volume": 624_640_000},
            {"symbol": "UUP", "change_pct": -1.1, "close": 27.5, "prev_close": 27.81, "volume": 8_200_000, "dollar_volume": 225_500_000},
            {"symbol": "QQQ", "change_pct": 1.3, "close": 498.0, "prev_close": 491.6, "volume": 39_000_000, "dollar_volume": 19_422_000_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    fred_summary_frame = pd.DataFrame(
        [
            {
                "series_id": "UNRATE",
                "indicator": "Unemployment Rate",
                "units_short": "Percent",
                "latest_value": 4.4,
                "prev_delta": 0.2,
                "last_updated": pd.Timestamp("2026-03-24T13:31:00Z"),
                "latest_date": pd.Timestamp("2026-03-01"),
            }
        ]
    )

    artifacts = build_bottom_up_attention_artifacts(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["TLT", "UUP", "QQQ"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
        fred_summary_frame=fred_summary_frame,
        llm_client=None,
        top_events_limit=1,
    )

    hypotheses = artifacts.frames["attention_hypotheses_1d"]
    assert not hypotheses.empty
    assert int(hypotheses["evidence_count"].max()) >= 1
    assert artifacts.frames["attention_search_requests"]["candidate_id"].astype(str).str.startswith("macro_release::").any()


def test_build_live_attention_research_bundle_generic_filing_sections_do_not_drive_continuation_copy():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "FSLY", "change_pct": 13.7, "close": 14.2, "prev_close": 12.5, "volume": 8_000_000, "dollar_volume": 113_600_000},
        ]
    )
    bars_by_symbol = {
        "FSLY": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [113.7],
            }
        )
    }
    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["FSLY"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    filings_frame = pd.DataFrame(
        [
            {
                "symbol": "FSLY",
                "filing_date": pd.Timestamp("2026-03-05T00:00:00Z"),
                "form": "10-K",
                "items": "7",
                "primary_doc_description": "10-K Item 7",
                "filing_url": "https://example.com/fsly-10k",
                "filing_excerpt": "",
                "document_text": "",
            }
        ]
    )

    bundle = build_live_attention_research_bundle(
        "symbol::FSLY",
        home,
        news_payloads={},
        context_payloads={"FSLY": {"llm_why_now": "10-K Item 7", "source_line": "Synthesized from SEC EDGAR filings and anomaly context"}},
        filings_frame=filings_frame,
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert bundle["cause_status"] == "unresolved"
    assert "10-K Item 7" not in bundle["why_now_text"]
    assert "Item 7" not in bundle["why_now_text"]
    assert bundle["background_context"] == []
    assert bundle["background_context_text"] == ""


def test_build_attention_home_1d_demotes_stale_background_names_out_of_must_read():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "PYPL", "change_pct": 8.1, "close": 78.0, "prev_close": 72.2, "volume": 10_500_000, "dollar_volume": 819_000_000},
            {"symbol": "MU", "change_pct": -4.4, "close": 91.0, "prev_close": 95.2, "volume": 7_200_000, "dollar_volume": 655_200_000},
        ]
    )
    bars_by_symbol = {
        symbol: pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [100.0 * (1.0 + float(move) / 100.0)],
            }
        )
        for symbol, move in daily_movers[["symbol", "change_pct"]].itertuples(index=False, name=None)
    }
    news_payloads = {
        "PYPL": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "PayPal jumps after upbeat checkout and margin commentary",
                        "summary": "Investors focused on better branded-checkout trends and operating leverage.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-03-24T15:30:00Z"),
                        "url": "https://example.com/pypl",
                    }
                ]
            )
        }
    }
    context_payloads = {
        "MU": {
            "llm_why_now": "Investors are still reacting to Micron's earlier earnings and guidance update.",
            "llm_source_line": "Primary source",
        }
    }

    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=build_attention_entity_master(daily_movers["symbol"].tolist()),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    must_read_symbols = {str(item["symbol"]).upper() for item in home["must_read_movers"]}
    unresolved_symbols = {str(item["symbol"]).upper() for item in home["unresolved_large_moves"]}

    assert "PYPL" in must_read_symbols
    assert "MU" not in must_read_symbols
    assert "MU" in unresolved_symbols


def test_attention_home_surface_summary_simplifies_move_vs_expectation_copy():
    preview = attention_home_bundle_preview(
        {
            "what_changed_text": "FSLY rose 13.7% today versus a +1.8% 20-day baseline (2.3z away from expectation).",
            "why_now_text": "No clear new company-specific catalyst was confirmed today. The move appears to be extending an earlier narrative.",
            "cause_status": "continuation",
        }
    )

    summary = attention_home_surface_summary(preview, is_event=False)

    assert "20-day baseline" not in summary
    assert "2.3z" not in summary
    assert "relative to its recent baseline" in summary


def test_build_live_attention_research_bundle_emits_tight_display_excerpts():
    daily_movers = pd.DataFrame(
        [
            {"symbol": "FSLY", "change_pct": 13.7, "close": 14.2, "prev_close": 12.5, "volume": 8_000_000, "dollar_volume": 113_600_000},
        ]
    )
    bars_by_symbol = {
        "FSLY": pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-20", periods=25, freq="B"),
                "close": [100.0] * 24 + [113.7],
            }
        )
    }
    home = build_attention_home_1d(
        daily_movers,
        bars_by_symbol=bars_by_symbol,
        news_payloads={},
        context_payloads={},
        entity_master=build_attention_entity_master(["FSLY"]),
        holdings=[],
        generated_at_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    bundle = build_live_attention_research_bundle(
        "symbol::FSLY",
        home,
        news_payloads={
            "FSLY": {
                "articles": pd.DataFrame(
                    [
                        {
                            "headline": "Why is Fastly (FSLY) stock rocketing higher today",
                            "summary": (
                                "Investors are extending the post-earnings rerating in Fastly while momentum traders add to the move. "
                                "A longer second sentence should stay out of the evidence panel."
                            ),
                            "source": "Reuters",
                            "published_at": pd.Timestamp("2026-03-24T15:30:00Z"),
                            "url": "https://example.com/fsly-reuters",
                        }
                    ]
                ),
                "fallback_summary": None,
                "source": "alpaca",
            }
        },
        context_payloads={},
        filings_frame=pd.DataFrame(),
        llm_client=None,
        asof_time_utc=pd.Timestamp("2026-03-24T18:00:00Z"),
    )

    assert bundle["evidence"]
    display_excerpt = bundle["evidence"][0]["display_excerpt"]
    assert display_excerpt == "Investors are extending the post-earnings rerating in Fastly while momentum traders add to the move."
    assert "longer second sentence" not in display_excerpt.lower()


def test_attention_home_surface_summary_keeps_useful_numeric_event_context():
    summary = attention_home_surface_summary(
        {
            "what_changed_text": "Treasury proxies rose today, led by TLT +1.6% and IEF +0.8%.",
            "why_text": "Treasury proxies rallied: TLT +1.6% and IEF +0.8%, implying yields fell about 10 bps.",
            "what_else_moved_text": "Relief showed up in IWM +2.1% and HYG +1.0%.",
        },
        is_event=True,
    )

    assert "1.6%" in summary
    assert "10 bps" in summary
    assert "tape reads this as" not in summary.lower()


def test_scan_correlation_phase_shifts_finds_decoupling_leaders():
    out = scan_correlation_phase_shifts(
        FakePhaseShiftAPI(),
        symbols=["LEAD", "BETA", "UNWIND"],
        benchmark="SPY",
        days=160,
        corr_window=20,
        roc_window=10,
        momentum_window=42,
    )

    summary = out["summary"]
    history = out["history"]

    assert not summary.empty
    assert not history.empty
    assert {"correlation_now", "correlation_roc", "compounding_momentum_pct", "decoupling_score", "phase_regime"} <= set(summary.columns)
    assert summary.nlargest(1, "decoupling_score").iloc[0]["symbol"] == "LEAD"
    assert "Decoupling leader" in set(summary["phase_regime"])


def test_scan_commodity_regimes_finds_beneficiaries_and_decouplers():
    out = scan_commodity_regimes(
        FakeCommodityAPI(),
        symbols=["BENEFIT", "SQUEEZE", "DECOUPLE"],
        commodity_symbols=["USO", "GLD"],
        days=180,
        corr_window=20,
        roc_window=10,
        momentum_window=42,
    )

    summary = out["summary"]
    history = out["history"]

    assert not summary.empty
    assert not history.empty
    assert {"beta_now", "beta_roc", "transmission_gap_pct", "beneficiary_score", "commodity_regime"} <= set(summary.columns)
    assert summary.nlargest(1, "beneficiary_score").iloc[0]["symbol"] == "BENEFIT"
    assert summary.nlargest(1, "decoupler_score").iloc[0]["symbol"] == "DECOUPLE"
    assert summary.nlargest(1, "squeeze_score").iloc[0]["symbol"] == "SQUEEZE"


def test_default_commodity_proxy_symbols_uses_broad_market_focus():
    assert default_commodity_proxy_symbols() == commodity_focus_universe("Broad Commodity Market")


def test_build_signal_frame_computes_pullback_and_channel_features():
    frame = build_signal_frame(_synthetic_price_history())

    assert not frame.empty
    assert {"pullback_from_ath_pct", "channel_support", "channel_resistance", "channel_position"} <= set(frame.columns)
    latest = summarize_signal_frame(frame)
    assert latest["pullback_from_ath_pct"] <= 0
    assert 0 <= latest["channel_position"] <= 1
    assert latest["channel_support"] <= latest["close"] <= latest["channel_resistance"]


def test_forecast_next_week_returns_probability_bands_and_probabilities():
    signal_frame = build_signal_frame(_synthetic_price_history(days=360))
    forecast = forecast_next_week(signal_frame, horizon=5, simulations=600)

    assert forecast
    bands = forecast["percentiles"]
    assert len(bands) == 5
    assert {"p10", "p25", "p50", "p75", "p90"} <= set(bands.columns)
    assert forecast["simulated_prices"].shape == (600, 5)
    assert 0 <= forecast["up_probability"] <= 1
    assert 0 <= forecast["breakout_probability"] <= 1
    assert 0 <= forecast["support_break_probability"] <= 1


def test_build_company_description_uses_company_role_and_narrative_themes():
    payload = load_recent_news(FakeCompanyAPI(), "AAPL", days=14, limit=4)
    description = build_company_description(
        "AAPL",
        {"name": "Apple", "exchange": "NASDAQ", "status": "active", "class": "us_equity"},
        {},
        {"regime": "Trend continuation", "pullback_from_ath_pct": -6.2, "dist_to_resistance_pct": 3.4},
        news_payload=payload,
    )

    assert "Apple (AAPL)" in description
    assert "consumer device" in description.lower()
    assert "ai rollout" in description.lower()
    assert "trend-continuation" in description.lower()
    assert "latest quarterly results" not in description.lower()


def test_build_company_description_uses_canonical_symbol_narrative_lens(monkeypatch):
    monkeypatch.setattr("services.company.narrative_business_lens_for_symbol", lambda symbol: "Commodity" if symbol == "SOC" else "")

    description = build_company_description(
        "SOC",
        {"name": "Sable Offshore Corp. Common Stock"},
        {},
        {},
        news_payload={"articles": pd.DataFrame()},
    )

    assert "commodity narrative" in description.lower()
    assert "commodity prices" in description.lower()


def test_build_company_description_omits_empty_watch_clause_for_unmapped_business_lens():
    description = build_company_description(
        "SOC",
        {"name": "Sable Offshore Corp. Common Stock"},
        {},
        {},
        news_payload={"articles": pd.DataFrame()},
        active_lens="Offshore Operator",
    )

    assert "watch ." not in description
    assert "offshore operator narrative" in description.lower()
    assert "commodity prices" not in description.lower()


def test_build_company_description_uses_clean_name_and_avoids_dashboard_template(monkeypatch):
    monkeypatch.setattr(company_module, "_wikipedia_company_background", lambda company_name: "")
    monkeypatch.setattr("services.company.narrative_business_lens_for_symbol", lambda symbol: "")

    description = build_company_description(
        "SBAC",
        {"name": "SBA Communications Corporation - Class A Common Stock"},
        {},
        {},
        news_payload={"articles": pd.DataFrame()},
    )

    assert "class a common stock" not in description.lower()
    assert "tracked here as an individual company narrative" not in description.lower()
    assert "SBA Communications Corporation (SBAC)" in description


def test_load_recent_news_and_summary_use_recent_articles():
    payload = load_recent_news(FakeCompanyAPI(), "AAPL", days=14, limit=4)
    summary = summarize_recent_news("AAPL", payload)

    assert payload["source"] == "alpaca"
    assert not payload["articles"].empty
    assert summary["summary_lines"]
    assert "tone is positive" in summary["summary_lines"][0].lower()


def test_summarize_recent_news_uses_mixed_when_sentiment_is_blank():
    payload = {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "Restart update",
                    "summary": "Production restart remains the main watchpoint.",
                    "published_at": pd.Timestamp("2026-03-30", tz="UTC"),
                    "sentiment": "",
                },
                {
                    "headline": "Offering update",
                    "summary": "The market is digesting a new equity filing.",
                    "published_at": pd.Timestamp("2026-03-31", tz="UTC"),
                    "sentiment": "   ",
                },
            ]
        ),
        "fallback_summary": None,
        "source": "alpaca",
    }

    summary = summarize_recent_news("SOC", payload)

    assert summary["summary_lines"]
    assert "tone is mixed" in summary["summary_lines"][0].lower()
    assert "tone is ." not in summary["summary_lines"][0].lower()


def test_build_attention_news_narrative_surfaces_copper_ai_supply_story():
    payload = {
        "articles": pd.DataFrame(
            [
                {
                    "headline": "Financial Times: AI buildout deepens copper shortage worries",
                    "summary": "Data center capex and AI spending are adding to copper demand while inventories stay tight.",
                    "published_at": pd.Timestamp("2026-03-20", tz="UTC"),
                    "source": "Financial Times",
                    "url": "https://example.com/ft-copper",
                },
                {
                    "headline": "WSJ says miners struggle to keep up with data center copper demand",
                    "summary": "The market is focused on supply tightness and slower mine additions.",
                    "published_at": pd.Timestamp("2026-03-19", tz="UTC"),
                    "source": "WSJ",
                    "url": "https://example.com/wsj-copper",
                },
                {
                    "headline": "Seeking Alpha: copper chain benefits from AI spending",
                    "summary": "Analysts keep pointing to data center buildout and tighter physical supply.",
                    "published_at": pd.Timestamp("2026-03-18", tz="UTC"),
                    "source": "Seeking Alpha",
                    "url": "https://example.com/sa-copper",
                },
            ]
        ),
        "fallback_summary": None,
        "source": "pipeline",
    }

    narrative = build_attention_news_narrative("CPER", payload, peer_group_name="Industrial Metals")

    assert "AI and data-center spending" in narrative["narrative_text"]
    assert "copper demand" in narrative["narrative_text"].lower()
    assert "tighter supply" in narrative["narrative_text"].lower()
    assert narrative["source_labels"] == ["Financial Times", "WSJ", "Seeking Alpha"]
    assert len(narrative["headline_links"]) == 2


def test_build_attention_feed_brief_explains_company_and_terms_without_numeric_prose():
    brief = build_attention_feed_brief(
        {
            "symbol": "APGE",
            "company_name": "Apogee Therapeutics",
            "title": "APGE is outperforming expectation",
            "story_text": "APGE is trading stronger than its peers implied. Price action still looks like a trend breakout setup.",
            "news_narrative": "Coverage from Benzinga is clustering around positive Phase 2 maintenance data in atopic dermatitis.",
            "headline_items": [
                {
                    "headline": "Apogee Therapeutics shares rise after positive Phase 2 maintenance data for IL-13 antibody candidate in atopic dermatitis",
                    "summary": "The update strengthened confidence that the eczema program can hold up over time.",
                    "source": "Benzinga",
                }
            ],
            "company_description": "Apogee Therapeutics (APGE) develops antibody-based medicines for inflammatory and immunology diseases.",
            "context_summary": "",
            "context_narrative": "",
            "watchpoint_text": "Watch whether the market keeps treating the update as a company-specific catalyst.",
        },
        None,
    )

    assert "Benzinga" in brief["headline_text"]
    assert "Apogee Therapeutics" in brief["company_text"]
    assert "mid-stage clinical trial" in brief["explainer_text"]
    assert "IL-13" in brief["explainer_text"]
    assert "atopic dermatitis" in brief["explainer_text"].lower()
    assert "residual" not in brief["lead_text"].lower()
    assert "observed" not in brief["lead_text"].lower()


def test_cached_news_context_handles_array_like_symbol_payloads(monkeypatch):
    blob = {
        "news_bundle": {
            "news": {
                "OKLO": {
                    "data": {
                        "target": [
                            {
                                "headline": "Oklo secures new partner",
                                "published_at": "2026-03-20T12:00:00Z",
                                "symbols": np.array(["OKLO", "SPY"]),
                                "source": "ExampleWire",
                            }
                        ]
                    }
                }
            }
        }
    }

    monkeypatch.setattr(company_module, "_news_files", lambda: [Path("/tmp/oklo.pkl")])
    monkeypatch.setattr(company_module, "_load_news_blob", lambda _path: blob)

    payload = company_module._load_cached_news_context("OKLO", limit=4)

    assert payload["source"] == "cache"
    assert payload["articles"]["headline"].tolist() == ["Oklo secures new partner"]


def test_edgar_client_loads_recent_filings_and_builds_context_bundle():
    class FakeResponse:
        def __init__(self, payload: object, status_code: int = 200, text: str = ""):
            self._payload = payload
            self.status_code = status_code
            self.text = text

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url: str, headers=None, timeout=None):
            self.calls.append(url)
            if url.endswith("/files/company_tickers.json"):
                return FakeResponse(
                    {
                        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                    }
                )
            if url.endswith("/submissions/CIK0000320193.json"):
                return FakeResponse(
                    {
                        "filings": {
                            "recent": {
                                "accessionNumber": ["0000320193-26-000010", "0000320193-25-000200"],
                                "filingDate": ["2026-03-20", "2025-12-15"],
                                "form": ["8-K", "10-K"],
                                "primaryDocument": ["a8k.htm", "a10k.htm"],
                                "primaryDocDescription": ["Current report", "Annual report"],
                                "items": ["2.02, 9.01", ""],
                                "isXBRL": [1, 1],
                                "isInlineXBRL": [1, 1],
                            }
                        }
                    }
                )
            if url.endswith("/320193/000032019326000010/a8k.htm"):
                return FakeResponse(
                    {},
                    text="""
                    <html><body>
                    <p>Item 2.02 Results of Operations and Financial Condition.</p>
                    <p>Apple reported strong services growth and accelerated share repurchases in the quarter.</p>
                    <p>The company also highlighted installed-base monetization and cash flow momentum.</p>
                    </body></html>
                    """,
                )
            if url.endswith("/320193/000032019325000200/a10k.htm"):
                return FakeResponse(
                    {},
                    text="""
                    <html><body>
                    <p>Management's Discussion and Analysis of Financial Condition and Results of Operations.</p>
                    <p>Revenue increased due to services growth and product mix.</p>
                    </body></html>
                    """,
                )
            raise AssertionError(f"unexpected url {url}")

    client = EdgarClient(session=FakeSession(), user_agent="spectral-nature tests", pause_seconds=0.0)
    filings = client.load_recent_filings(["AAPL"], days=120, max_filings_per_symbol=3)

    assert filings["symbol"].tolist() == ["AAPL", "AAPL"]
    assert filings.iloc[0]["filing_url"].endswith("/320193/000032019326000010/a8k.htm")
    assert filings.iloc[0]["items"] == "2.02, 9.01"
    assert "services growth" in filings.iloc[0]["filing_excerpt"].lower()

    attention = pd.DataFrame({"entity_id": ["AAPL"], "attention_score": [82.5]})
    bundle = build_attention_context_bundle(attention, filings, asof_time_utc=pd.Timestamp("2026-03-21T12:00:00Z"))

    assert bundle.iloc[0]["symbol"] == "AAPL"
    assert "8-K" in bundle.iloc[0]["context_story_text"]
    assert "2.02" in bundle.iloc[0]["context_story_text"]
    assert "SEC EDGAR" in bundle.iloc[0]["source_line"]
    assert "services growth" in bundle.iloc[0]["primary_source_excerpt"].lower()


def test_edgar_client_reuses_existing_document_text_without_refetching():
    class FakeResponse:
        def __init__(self, payload: object, status_code: int = 200, text: str = ""):
            self._payload = payload
            self.status_code = status_code
            self.text = text

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url: str, headers=None, timeout=None):
            self.calls.append(url)
            if url.endswith("/files/company_tickers.json"):
                return FakeResponse({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
            if url.endswith("/submissions/CIK0000320193.json"):
                return FakeResponse(
                    {
                        "filings": {
                            "recent": {
                                "accessionNumber": ["0000320193-26-000010"],
                                "filingDate": ["2026-03-20"],
                                "form": ["8-K"],
                                "primaryDocument": ["a8k.htm"],
                                "primaryDocDescription": ["Current report"],
                                "items": ["2.02, 9.01"],
                                "isXBRL": [1],
                                "isInlineXBRL": [1],
                            }
                        }
                    }
                )
            raise AssertionError(f"unexpected url {url}")

    existing = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "accession_number": ["0000320193-26-000010"],
            "filing_url": ["https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm"],
            "filing_excerpt": ["Apple highlighted services growth."],
            "document_text": ["Apple discussed services growth and capital returns."],
            "document_text_hash": ["abc123"],
            "document_text_chars": [55],
        }
    )

    session = FakeSession()
    client = EdgarClient(session=session, user_agent="spectral-nature tests", pause_seconds=0.0)
    filings = client.load_recent_filings(
        ["AAPL"],
        days=120,
        max_filings_per_symbol=3,
        existing_frame=existing,
    )

    assert filings.iloc[0]["document_text"] == "Apple discussed services growth and capital returns."
    assert filings.iloc[0]["document_text_hash"] == "abc123"
    assert all("/320193/000032019326000010/a8k.htm" not in url for url in session.calls)


def test_attention_context_llm_builders_reuse_hashes_and_merge():
    class FakeLLMClient:
        def __init__(self):
            self.calls: list[str] = []
            self.config = type("Config", (), {"model": "gpt-4.1-mini"})()

        def generate_json(self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict):
            self.calls.append(schema_name)
            if schema_name == "edgar_evidence":
                return {
                    "filing_angle": "Management is emphasizing services growth and shareholder returns.",
                    "management_focus": "Services mix and capital return remain the main emphasis.",
                    "key_points": ["Services growth stayed strong", "Buybacks accelerated"],
                    "catalysts": ["Installed-base monetization"],
                    "risk_flags": ["Hardware demand was not the focal point"],
                    "tone": "constructive",
                    "confidence_note": "This is directly grounded in the filing excerpt.",
                }
            if schema_name == "attention_context_narrative":
                return {
                    "headline": "EDGAR points to a services-led support story",
                    "summary_text": "The filing suggests management is leaning on services momentum and capital returns as the cleanest explanation for the move.",
                    "narrative_text": "Against the anomaly backdrop, the filing makes the story look less like a random squeeze and more like investors reacting to durable services monetization.",
                    "why_now": "The attention move lines up with a fresh current report and explicit management emphasis.",
                    "management_signal": "Management is framing the quarter around services growth and cash deployment.",
                    "supporting_points": ["Fresh 8-K", "Services growth language", "Capital returns"],
                    "confidence": "medium-high",
                }
            raise AssertionError(f"unexpected schema {schema_name}")

    filings = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "company_name": ["Apple Inc."],
            "form": ["8-K"],
            "filing_date": [pd.Timestamp("2026-03-20T00:00:00Z")],
            "accession_number": ["0000320193-26-000010"],
            "filing_url": ["https://example.com/aapl-8k"],
            "items": ["2.02, 9.01"],
            "filing_excerpt": ["Apple highlighted services growth and capital returns."],
            "document_text": ["Apple reported strong services growth and accelerated share repurchases."],
            "document_text_hash": ["hash-aapl-8k"],
        }
    )
    attention = pd.DataFrame(
        {
            "entity_id": ["AAPL"],
            "entity_type": ["symbol"],
            "attention_score": [82.5],
            "horizon": ["1d"],
            "title": ["AAPL is outperforming expectation"],
            "expected_return_pct": [0.8],
            "observed_return_pct": [2.4],
            "residual_zscore": [2.3],
        }
    )
    base_context = build_attention_context_bundle(attention, filings, asof_time_utc=pd.Timestamp("2026-03-21T12:00:00Z"))

    llm = FakeLLMClient()
    evidence = build_edgar_evidence(filings, llm, asof_time_utc=pd.Timestamp("2026-03-21T12:00:00Z"))
    narratives = build_attention_context_narratives(attention, filings, evidence, llm, asof_time_utc=pd.Timestamp("2026-03-21T12:00:00Z"))
    merged = merge_attention_context_with_llm(base_context, narratives)

    assert llm.calls == ["edgar_evidence", "attention_context_narrative"]
    assert evidence.iloc[0]["management_focus"].lower().startswith("services mix")
    assert narratives.iloc[0]["llm_headline"] == "EDGAR points to a services-led support story"
    assert "services momentum" in narratives.iloc[0]["llm_summary_text"].lower()
    assert "Fresh 8-K" in json.loads(narratives.iloc[0]["llm_supporting_points_json"])
    assert merged.iloc[0]["llm_headline"] == "EDGAR points to a services-led support story"

    llm_reuse = FakeLLMClient()
    reused_evidence = build_edgar_evidence(
        filings,
        llm_reuse,
        existing_frame=evidence,
        asof_time_utc=pd.Timestamp("2026-03-21T12:05:00Z"),
    )
    reused_narratives = build_attention_context_narratives(
        attention,
        filings,
        evidence,
        llm_reuse,
        existing_frame=narratives,
        asof_time_utc=pd.Timestamp("2026-03-21T12:05:00Z"),
    )

    assert llm_reuse.calls == []
    assert reused_evidence.iloc[0]["document_text_hash"] == "hash-aapl-8k"
    assert reused_narratives.iloc[0]["llm_headline"] == "EDGAR points to a services-led support story"


def test_azure_openai_chat_json_client_uses_azure_headers_and_endpoint():
    class FakeResponse:
        def __init__(self, status_code: int = 200):
            self.status_code = status_code
            self.text = '{"choices":[{"message":{"content":"{\\"answer\\":\\"ok\\"}"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": '{"answer":"ok"}'}}]}

    class FakeSession:
        def __init__(self):
            self.calls: list[dict[str, object]] = []

        def post(self, url: str, headers=None, params=None, json=None, timeout=None):
            self.calls.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "params": params or {},
                    "json": json or {},
                    "timeout": timeout,
                }
            )
            return FakeResponse()

    session = FakeSession()
    client = AzureOpenAIChatJSONClient(
        LLMConfig(
            provider="azure_openai",
            api_key="azure-key",
            model="gpt-5.3-chat",
            deployment="gpt-5.3-chat",
            base_url="https://example.cognitiveservices.azure.com/openai/v1",
            api_version="2024-10-21",
        ),
        session=session,
    )

    result = client.generate_json(
        system_prompt="System",
        user_prompt="User",
        schema_name="test_schema",
        schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False},
    )

    assert result == {"answer": "ok"}
    assert session.calls[0]["url"] == "https://example.cognitiveservices.azure.com/openai/v1/chat/completions"
    assert session.calls[0]["headers"]["api-key"] == "azure-key"
    assert session.calls[0]["params"]["api-version"] == "2024-10-21"
    assert session.calls[0]["json"]["model"] == "gpt-5.3-chat"


def test_azure_openai_v1_config_does_not_default_api_version(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.cognitiveservices.azure.com/")
    monkeypatch.setenv("LLM_API_KEY", "azure-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.3-chat")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)

    config = load_llm_config()

    assert config is not None
    assert config.provider == "azure_openai"
    assert config.base_url == "https://example.cognitiveservices.azure.com/openai/v1"
    assert config.api_version == ""
    assert config.temperature == 1.0


def test_build_homepage_v2_digest_uses_llm_mapping():
    class FakeLLMClient:
        def __init__(self):
            self.calls: list[dict[str, object]] = []
            self.config = SimpleNamespace(model="gpt-5.3-chat")

        def generate_json(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "headline": "Markets turned into a concentrated anomaly tape.",
                "dek": "A handful of names accounted for most of the past day's surprise moves.",
                "beats": [
                    {
                        "beat_id": "beat-1",
                        "sentence": "AAOI jumped to the front of the tape after a fresh filing sharpened the bull case.",
                        "summary": "The stock moved above expectation and the filing context added a concrete catalyst. The tape treated it as an idiosyncratic story rather than a broad market move.",
                        "event_ids": ["evt-aaoi"],
                        "symbols": ["AAOI"],
                    }
                ],
            }

    digest = build_homepage_v2_digest(
        [
            {
                "event_id": "evt-aaoi",
                "symbol": "AAOI",
                "title": "AAOI is outperforming expectation",
                "story_text": "AAOI broke above expectation after a fresh catalyst.",
                "news_summary_text": "Coverage focused on a material agreement and demand durability.",
                "context_summary_text": "An 8-K pointed to a new material agreement.",
            }
        ],
        FakeLLMClient(),
        asof_time_utc=pd.Timestamp("2026-03-22T08:00:00Z"),
        max_sentences=12,
    )

    assert digest["mode"] == "llm"
    assert digest["headline"] == "Markets turned into a concentrated anomaly tape."
    assert digest["beats"][0]["event_ids"] == ["evt-aaoi"]
    assert digest["beats"][0]["symbols"] == ["AAOI"]


def test_build_homepage_v2_digest_falls_back_without_llm():
    digest = build_homepage_v2_digest(
        [
            {
                "event_id": "evt-bx",
                "symbol": "BX",
                "title": "BX is outperforming expectation",
                "story_text": "BX pushed above expectation on a sharp idiosyncratic move.",
                "expected_vs_observed_text": "Observed return ran ahead of the model baseline.",
                "news_summary_text": "News tied the move to improving private credit sentiment.",
            }
        ],
        None,
        asof_time_utc=pd.Timestamp("2026-03-22T08:00:00Z"),
        max_sentences=12,
    )

    assert digest["mode"] == "fallback"
    assert digest["beats"][0]["event_ids"] == ["evt-bx"]
    assert "BX" in digest["beats"][0]["sentence"]


def test_build_homepage_v2_market_digest_uses_market_event_titles_and_underlying_anomalies():
    digest = build_homepage_v2_market_digest(
        [
            {
                "event_title": "Energy & Oil move lower together today",
                "what_happened_text": "Oil-linked instruments fell sharply, led by BNO and USO.",
                "why_happened_text": "The tape reads this as easing supply-risk and lower inflation pressure.",
                "affected_assets_summary_text": "Down: BNO, USO | Up: UAL, DAL, IWM, TLT",
                "headline_text": "Oil prices eased as hopes rose for a de-escalation path.",
                "anchor_symbol": "BNO",
                "supporting_event_ids": ["bno", "uso", "ual", "tlt"],
                "supporting_symbols": ["BNO", "USO", "UAL", "TLT"],
            }
        ],
        asof_time_utc=pd.Timestamp("2026-03-23T18:00:00Z"),
    )

    assert digest["mode"] == "market_events"
    assert digest["headline"] == "Energy & Oil move lower together today"
    assert digest["beats"][0]["sentence"] == "Energy & Oil move lower together today"
    assert digest["beats"][0]["event_ids"] == ["bno", "uso", "ual", "tlt"]
    assert "easing supply-risk" in digest["beats"][0]["summary"].lower()


def test_homepage_v2_bundle_symbol_lookup_deduplicates_symbols_per_bundle():
    lookup = homepage_v2_bundle_symbol_lookup(
        [
            {"bundle_id": "bundle-1", "symbols": ["msft", "AAPL", "MSFT"]},
            {"bundle_id": "bundle-1", "symbols": ["NVDA", "aapl"]},
            {"bundle_id": "bundle-2", "symbols": ["TLT"]},
        ]
    )

    assert lookup == {
        "bundle-1": ["MSFT", "AAPL", "NVDA"],
        "bundle-2": ["TLT"],
    }


def test_homepage_v2_editorial_links_exposes_top_level_torres_capital_substack_cta():
    links = homepage_v2_editorial_links(placement="sidebar_brand")

    assert links == [
        {
            "link_id": "torres-capital-substack",
            "placement": "sidebar_brand",
            "label": "Torres Capital Substack",
            "button_label": "Read on Substack",
            "icon_name": "substack",
            "url": "https://substack.com/@torrescap",
        }
    ]


def test_normalize_homepage_v2_detail_state_defaults_to_first_bundle_and_symbol():
    state = normalize_homepage_v2_detail_state(
        [
            {"bundle_id": "bundle-1", "symbols": ["msft", "aapl"]},
            {"bundle_id": "bundle-2", "symbols": ["nvda"]},
        ],
        selected_bundle_id="missing",
        selected_ticker="",
        active_panel="",
    )

    assert state == {
        "selected_bundle_id": "bundle-1",
        "selected_ticker": "MSFT",
        "active_panel": HOMEPAGE_V2_RESEARCH_PANEL,
    }


def test_normalize_homepage_v2_detail_state_preserves_explicit_company_selection():
    state = normalize_homepage_v2_detail_state(
        [{"bundle_id": "bundle-1", "symbols": ["msft", "aapl"]}],
        selected_bundle_id="bundle-1",
        selected_ticker="tsla",
        active_panel=HOMEPAGE_V2_COMPANY_PANEL,
    )

    assert state == {
        "selected_bundle_id": "bundle-1",
        "selected_ticker": "TSLA",
        "active_panel": HOMEPAGE_V2_COMPANY_PANEL,
    }


def test_normalize_homepage_v2_detail_state_falls_back_to_research_without_ticker():
    state = normalize_homepage_v2_detail_state(
        [{"bundle_id": "bundle-1", "symbols": []}],
        selected_bundle_id="bundle-1",
        selected_ticker="",
        active_panel=HOMEPAGE_V2_COMPANY_PANEL,
    )

    assert state == {
        "selected_bundle_id": "bundle-1",
        "selected_ticker": "",
        "active_panel": HOMEPAGE_V2_RESEARCH_PANEL,
    }


def test_fred_client_parses_observations_and_builds_summary():
    client = FakeFREDClient()
    metadata = client.get_series_metadata("CPIAUCSL")
    frame = client.get_series_observations("CPIAUCSL", observation_start="2024-01-01")
    spec = FredSeriesSpec("Inflation", "CPIAUCSL", "Headline CPI", "Consumer Price Index, all items.")
    summary = build_fred_series_summary(spec, metadata, frame)

    assert len(frame) == 13
    assert summary["indicator"] == "Headline CPI"
    assert round(summary["prev_delta"], 1) == 0.8
    assert round(summary["yoy_delta"], 1) == 10.3


def test_fred_client_bulk_release_loader_returns_series_index_and_observations():
    client = FakeFREDClient()
    series_index, observations = client.get_release_observations_bulk(10)

    assert not series_index.empty
    assert not observations.empty
    assert series_index.iloc[0]["series_id"] == "CPIAUCSL"
    assert observations.iloc[-1]["value"] == 319.3


def test_build_fred_figure_can_overlay_stationary_percent_change_for_level_series():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
            "value": [100.0, 110.0, 121.0],
        }
    )
    fig = build_fred_figure(
        FredSeriesSpec("Inflation", "TEST", "Test Series", ""),
        {"units_short": "Index", "units": "Index"},
        frame,
        show_stationary_overlay=True,
    )

    assert len(fig.data) == 2
    assert fig.data[1].name == "Obs-to-obs % change"
    assert np.allclose(list(fig.data[1].y), [10.0, 10.0])


def test_build_fred_figure_can_overlay_stationary_delta_for_rate_series():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
            "value": [4.0, 4.2, 4.1],
        }
    )
    fig = build_fred_figure(
        FredSeriesSpec("Labor (BLS)", "UNRATE", "Unemployment Rate", ""),
        {"units_short": "Percent", "units": "Percent"},
        frame,
        show_stationary_overlay=True,
    )

    assert len(fig.data) == 2
    assert fig.data[1].name == "Obs-to-obs delta"
    assert np.allclose(list(fig.data[1].y), [0.2, -0.1])


def test_load_fred_dashboard_handles_bulk_observation_dates_without_timezone_conflicts():
    original_client = fred_module.FREDClient
    fred_module.FREDClient = lambda api_key: FakeFREDClient()

    try:
        dashboard = load_fred_dashboard("fake-key", years=2)
    finally:
        fred_module.FREDClient = original_client

    assert not dashboard["summary"].empty
    assert "CPIAUCSL" in dashboard["series_data"]
    assert not dashboard["series_data"]["CPIAUCSL"].empty


def test_build_treasury_yield_summary_and_facts_capture_bp_moves():
    wide = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-24", "2026-03-25", "2026-03-26"]),
            "updated_at_utc": pd.to_datetime(["2026-03-26T15:57:18Z"] * 3, utc=True),
            "BC_3MONTH": [4.30, 4.25, 4.20],
            "BC_2YEAR": [4.05, 3.98, 3.91],
            "BC_5YEAR": [4.12, 4.04, 3.97],
            "BC_10YEAR": [4.33, 4.24, 4.13],
            "BC_30YEAR": [4.61, 4.52, 4.43],
        }
    )

    summary = build_treasury_yield_summary(wide)
    facts = build_treasury_yield_facts_1d(wide, asof_time_utc=pd.Timestamp("2026-03-26T16:00:00Z"))

    ust10 = summary[summary["series_id"] == "UST_10Y"].iloc[0]
    curve = summary[summary["series_id"] == "CURVE_2S10S"].iloc[0]
    assert ust10["latest_value"] == 4.13
    assert ust10["prev_delta_bps"] == -11.0
    assert curve["latest_value"] == 0.22
    assert curve["prev_delta_bps"] == -4.0
    assert facts.iloc[0]["ust_10y"] == 4.13
    assert facts.iloc[0]["ust_10y_1d_bps"] == -11.0
    assert facts.iloc[0]["curve_2s10s"] == 0.22


def test_load_treasury_yield_datasets_parses_official_xml(monkeypatch):
    xml = """<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<feed xml:base="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <updated>2026-03-26T15:57:18Z</updated>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE m:type="Edm.DateTime">2026-03-25T00:00:00</d:NEW_DATE>
        <d:BC_3MONTH m:type="Edm.Double">4.25</d:BC_3MONTH>
        <d:BC_6MONTH m:type="Edm.Double">4.10</d:BC_6MONTH>
        <d:BC_1YEAR m:type="Edm.Double">4.00</d:BC_1YEAR>
        <d:BC_2YEAR m:type="Edm.Double">3.98</d:BC_2YEAR>
        <d:BC_5YEAR m:type="Edm.Double">4.04</d:BC_5YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.24</d:BC_10YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.52</d:BC_30YEAR>
      </m:properties>
    </content>
  </entry>
  <entry>
    <updated>2026-03-26T15:57:18Z</updated>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE m:type="Edm.DateTime">2026-03-26T00:00:00</d:NEW_DATE>
        <d:BC_3MONTH m:type="Edm.Double">4.20</d:BC_3MONTH>
        <d:BC_6MONTH m:type="Edm.Double">4.06</d:BC_6MONTH>
        <d:BC_1YEAR m:type="Edm.Double">3.95</d:BC_1YEAR>
        <d:BC_2YEAR m:type="Edm.Double">3.91</d:BC_2YEAR>
        <d:BC_5YEAR m:type="Edm.Double">3.97</d:BC_5YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.13</d:BC_10YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.43</d:BC_30YEAR>
      </m:properties>
    </content>
  </entry>
</feed>"""

    monkeypatch.setattr(treasury_module, "_treasury_request", lambda params: xml)
    payload = treasury_module.load_treasury_yield_datasets(years=1, end_date=pd.Timestamp("2026-03-26T16:00:00Z"))

    assert len(payload["yield_curve_observations"]) >= 10
    assert not payload["yield_curve_summary"].empty
    assert payload["yield_curve_facts_1d"].iloc[0]["ust_2y_1d_bps"] == -7.0


def test_candidate_context_documents_adds_treasury_yield_summary_for_rates_names():
    candidate = {
        "candidate_id": "candidate::TLT",
        "symbol": "TLT",
        "rates_role": "duration",
        "macro_exposure_tags": ["rates", "duration"],
    }
    yield_facts = pd.DataFrame(
        [
            {
                "latest_date": pd.Timestamp("2026-03-26"),
                "updated_at_utc": pd.Timestamp("2026-03-26T15:57:18Z"),
                "ust_3m": 4.20,
                "ust_3m_1d_bps": -5.0,
                "ust_2y": 3.91,
                "ust_2y_1d_bps": -7.0,
                "ust_10y": 4.13,
                "ust_10y_1d_bps": -11.0,
                "ust_30y": 4.43,
                "ust_30y_1d_bps": -9.0,
                "curve_2s10s": 0.22,
                "curve_2s10s_1d_bps": -4.0,
                "curve_3m10y": -0.07,
                "curve_3m10y_1d_bps": -6.0,
            }
        ]
    )

    docs = attention_agentic_module._candidate_context_documents(
        candidate,
        news_payloads={},
        context_payloads={},
        filings_frame=pd.DataFrame(),
        fred_summary_frame=pd.DataFrame(),
        yield_curve_facts_frame=yield_facts,
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-03-26T16:00:00Z"),
        official_routes=["treasury"],
        priority_entities=["rates", "treasury"],
    )

    treasury_docs = [doc for doc in docs if doc.get("source_kind") == "treasury"]
    assert len(treasury_docs) == 1
    raw_text = str(treasury_docs[0]["raw_text"]).lower()
    assert "yields" in raw_text
    assert "curve" in raw_text
    assert "%" not in raw_text
    assert "bps" not in raw_text


def test_candidate_context_documents_uses_headline_when_news_summary_missing():
    candidate = {
        "candidate_id": "candidate::BMY",
        "symbol": "BMY",
    }
    news_payloads = {
        "BMY": {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Bristol-Myers advances oncology program with new data",
                        "summary": "",
                        "description": "",
                        "source": "Yahoo Finance",
                        "published_at": "2026-04-03T05:00:00Z",
                        "url": "https://example.com/bmy-oncology",
                        "provider": "serpapi",
                    }
                ]
            )
        }
    }
    docs = attention_agentic_module._candidate_context_documents(
        candidate,
        news_payloads=news_payloads,
        context_payloads={},
        filings_frame=pd.DataFrame(),
        fred_summary_frame=pd.DataFrame(),
        yield_curve_facts_frame=pd.DataFrame(),
        run_id="run-1",
        asof_time_utc=pd.Timestamp("2026-04-03T06:00:00Z"),
        official_routes=[],
        priority_entities=[],
    )
    news_docs = [doc for doc in docs if doc.get("source_kind") == "news"]
    assert len(news_docs) == 1
    assert news_docs[0]["raw_text"] == "Bristol-Myers advances oncology program with new data"


def test_csv_cache_reuses_fresh_files_and_refreshes_when_stale_or_forced():
    original_root = data_cache.CACHE_ROOT
    original_data_root = data_cache.CACHE_DATA_ROOT
    original_policy_path = data_cache.CACHE_POLICY_PATH

    with TemporaryDirectory() as tmp_dir:
        temp_root = Path(tmp_dir)
        data_cache.CACHE_ROOT = temp_root
        data_cache.CACHE_DATA_ROOT = temp_root / "data"
        data_cache.CACHE_POLICY_PATH = temp_root / "cache_policy.json"
        data_cache.CACHE_POLICY_PATH.write_text(
            json.dumps({"default_stale_minutes": 60, "datasets": {"price_history": 60}}),
            encoding="utf-8",
        )

        calls = {"count": 0}

        def fetcher() -> pd.DataFrame:
            calls["count"] += 1
            return pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(["2025-01-01", "2025-01-02"]),
                    "close": [100.0, 100.0 + calls["count"]],
                }
            )

        try:
            first = cached_frame("price_history", "AAPL__365d", fetcher)
            second = cached_frame("price_history", "AAPL__365d", fetcher)
            assert calls["count"] == 1
            assert first.equals(second)

            target = CacheTarget("price_history", "AAPL__365d")
            meta = json.loads(target.meta_path.read_text(encoding="utf-8"))
            meta["cached_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            target.meta_path.write_text(json.dumps(meta), encoding="utf-8")

            stale_refresh = cached_frame("price_history", "AAPL__365d", fetcher)
            forced_refresh = cached_frame("price_history", "AAPL__365d", fetcher, force_refresh=True)

            assert calls["count"] == 3
            assert stale_refresh.iloc[-1]["close"] == 102.0
            assert forced_refresh.iloc[-1]["close"] == 103.0
            assert (target.bundle_dir / "data.csv").exists()
        finally:
            data_cache.CACHE_ROOT = original_root
            data_cache.CACHE_DATA_ROOT = original_data_root
            data_cache.CACHE_POLICY_PATH = original_policy_path


def test_format_fred_value_and_categories_cover_macro_groups():
    assert format_fred_value(3.2, "Percent") == "3.20%"
    assert "Inflation" in fred_categories()
    assert "Money Supply" in fred_categories()


def test_load_fred_api_key_prefers_keyvault_secret_over_env():
    original_loader = fred_module._load_secret_from_keyvault
    original_env_key = fred_module.os.environ.get("FRED_API_KEY")
    fred_module._load_secret_from_keyvault.cache_clear()
    fred_module.os.environ["FRED_API_KEY"] = "env-key"

    try:
        fred_module._load_secret_from_keyvault = lambda secret_name, vault_url=None, vault_name=None: "vault-key"
        assert fred_module.load_fred_api_key() == "vault-key"
    finally:
        fred_module._load_secret_from_keyvault = original_loader
        fred_module._load_secret_from_keyvault.cache_clear()
        if original_env_key is None:
            fred_module.os.environ.pop("FRED_API_KEY", None)
        else:
            fred_module.os.environ["FRED_API_KEY"] = original_env_key


def test_share_count_asof_respects_asof_cutoff(monkeypatch):
    quarterly = pd.DataFrame(
        {
            "Ticker": ["AAA", "AAA"],
            "Report Date": ["2025-12-31", "2026-03-31"],
            "Fiscal Period": ["Q4", "Q1"],
            "Fiscal Year": [2025, 2026],
            "Shares Diluted": [100.0, 120.0],
            "Shares Basic": [95.0, 115.0],
        }
    )

    monkeypatch.setattr(
        fundamentals_compute,
        "_load_statement",
        lambda statement, data_dir="": quarterly.copy() if statement == "income" else pd.DataFrame(columns=quarterly.columns),
    )

    value_early, date_early, metric_early = fundamentals_compute.share_count_asof(
        "AAA",
        asof_time_utc="2026-02-01T00:00:00Z",
    )
    value_late, date_late, metric_late = fundamentals_compute.share_count_asof(
        "AAA",
        asof_time_utc="2026-04-15T00:00:00Z",
    )

    assert value_early == 100.0
    assert str(pd.Timestamp(date_early).date()) == "2025-12-31"
    assert metric_early == "Shares Diluted"
    assert value_late == 120.0
    assert str(pd.Timestamp(date_late).date()) == "2026-03-31"
    assert metric_late == "Shares Diluted"


def test_build_attention_ticker_snapshot_frame_includes_backtest_metadata(monkeypatch):
    monkeypatch.setattr(
        "services.attention_ticker_snapshots.share_count_asof",
        lambda ticker, asof_time_utc=None: (1000.0, pd.Timestamp("2025-12-31"), "Shares Diluted"),
    )

    price_history = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "timestamp": pd.to_datetime(["2026-03-20", "2026-03-21", "2026-03-24"], utc=True),
            "close": [10.0, 11.0, 12.0],
        }
    )
    universe_snapshot = pd.DataFrame({"symbol": ["AAA"], "security_name": ["Acme Holdings"]})

    frame = build_attention_ticker_snapshot_frame(
        ["AAA"],
        price_history_frame=price_history,
        universe_snapshot_frame=universe_snapshot,
        asof_time_utc="2026-03-24T18:00:00Z",
        run_id="run-123",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["symbol"] == "AAA"
    assert row["company_name"] == "Acme Holdings"
    assert row["market_cap_label"] == "$12,000"
    assert row["run_id"] == "run-123"
    assert row["asof_time_utc"].startswith("2026-03-24T18:00:00")
    assert str(row["sparkline_data_uri"]).startswith("data:image/svg+xml;base64,")
    trace = json.loads(row["source_trace_json"])
    assert trace["datasets"] == ["price_history", "universe_snapshot"]


def test_build_attention_ticker_background_snapshot_frame_serializes_replay_fields(monkeypatch):
    monkeypatch.setattr(
        "services.attention_ticker_snapshots.share_count_asof",
        lambda ticker, asof_time_utc=None: (2000.0, pd.Timestamp("2025-12-31"), "Shares Diluted"),
    )
    monkeypatch.setattr(
        "services.attention_ticker_snapshots.load_quarterly_fundamentals",
        lambda ticker: {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()},
    )

    price_history = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "timestamp": pd.to_datetime(["2026-03-20", "2026-03-24"], utc=True),
            "close": [10.0, 12.0],
        }
    )
    universe_snapshot = pd.DataFrame({"symbol": ["AAA"], "security_name": ["Acme Holdings"]})
    news_frame = pd.DataFrame(
        {
            "symbols": [["AAA"]],
            "headline": ["Acme launches new product"],
            "summary": ["Acme introduced a new product line."],
            "source": ["ExampleWire"],
            "published_at": [pd.Timestamp("2026-03-24T12:00:00Z")],
            "url": ["https://example.com/acme"],
        }
    )
    context_frame = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "llm_source_line": ["Synthesized from official and news context"],
            "llm_headline": ["Management outlined a new product push"],
            "llm_summary_text": ["The company highlighted a broader product expansion."],
            "context_story_text": ["This follows earlier execution improvements."],
            "top_filing_links_json": ["[]"],
        }
    )

    frame = build_attention_ticker_background_snapshot_frame(
        ["AAA"],
        price_history_frame=price_history,
        universe_snapshot_frame=universe_snapshot,
        news_frame=news_frame,
        attention_context_frame=context_frame,
        asof_time_utc="2026-03-24T18:00:00Z",
        run_id="run-123",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["symbol"] == "AAA"
    assert row["company_name"] == "Acme Holdings"
    assert row["run_id"] == "run-123"
    assert "Acme" in row["description_text"]
    assert json.loads(row["news_summary_lines_json"])
    assert json.loads(row["recent_headlines_json"])[0]["headline"] == "Acme launches new product"
    assert len(json.loads(row["price_points_json"])) == 2
    trace = json.loads(row["source_trace_json"])
    assert "attention_context_bundle" in trace["datasets"]


def test_augment_candidate_frame_preserves_upstream_peer_group_and_merges_tags():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "industry": "Unknown",
                "sector": "Energy",
                "asset_class": "commodity",
                "peer_group_id": "commodity_role:oil_supply_shock",
                "macro_exposure_tags": np.array(["oil"], dtype=object),
                "macro_role_tags": np.array(["supply_tightness"], dtype=object),
                "business_tags": np.array(["refining"], dtype=object),
                "business_role_tags": np.array(["midstream"], dtype=object),
            }
        ]
    )

    out = attention_agentic_module._augment_candidate_frame(frame, asof_time_utc=asof, run_id="run-123")
    row = out.iloc[0]

    assert row["peer_group_id"] == "commodity_role:oil_supply_shock"
    assert row["macro_exposure_tags"] == ["oil", "supply_tightness"]
    assert row["business_tags"] == ["refining", "midstream"]


def test_graph_edges_connect_same_industry_without_tag_overlap():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-aaa",
                "symbol": "AAA",
                "industry": "Software",
                "sector": "Information Technology",
                "change_pct": 4.2,
            },
            {
                "candidate_id": "cand-bbb",
                "symbol": "BBB",
                "industry": "Software",
                "sector": "Information Technology",
                "change_pct": 3.8,
            },
        ]
    )

    candidates = attention_agentic_module._augment_candidate_frame(frame, asof_time_utc=asof, run_id="run-123")
    edges = attention_agentic_module._graph_edges(candidates, claim_map={}, run_id="run-123", asof_time_utc=asof)

    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"AAA", "BBB"}
    assert json.loads(row["edge_reasons_json"]) == ["taxonomy_peer"]
    assert float(row["edge_weight"]) >= 0.42


def test_graph_edges_use_explicit_upstream_peer_groups_without_tags():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-gld",
                "symbol": "GLD",
                "industry": "Unknown",
                "sector": "Unknown",
                "asset_class": "commodity",
                "peer_group_id": "macro_role:inflation_hedge",
                "peer_group_name": "Inflation Hedge",
                "change_pct": 1.9,
            },
            {
                "candidate_id": "cand-gdx",
                "symbol": "GDX",
                "industry": "Unknown",
                "sector": "Materials",
                "asset_class": "equity",
                "peer_group_id": "macro_role:inflation_hedge",
                "peer_group_name": "Inflation Hedge",
                "change_pct": 2.4,
            },
        ]
    )

    candidates = attention_agentic_module._augment_candidate_frame(frame, asof_time_utc=asof, run_id="run-123")
    edges = attention_agentic_module._graph_edges(candidates, claim_map={}, run_id="run-123", asof_time_utc=asof)

    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"GLD", "GDX"}
    assert json.loads(row["edge_reasons_json"]) == ["taxonomy_peer"]
    assert float(row["edge_weight"]) >= 0.42


def test_graph_edges_use_shared_macro_roles_when_taxonomy_is_broad():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-tlt",
                "symbol": "TLT",
                "industry": "Long Duration US Treasury ETF",
                "sector": "Rates",
                "peer_group_name": "Rates",
                "rates_role": "rates",
                "macro_role_tags": ["rates"],
                "change_pct": 1.6,
            },
            {
                "candidate_id": "cand-ief",
                "symbol": "IEF",
                "industry": "US Treasury Bond ETF",
                "sector": "Rates",
                "peer_group_name": "Rates",
                "rates_role": "rates",
                "macro_role_tags": ["rates"],
                "change_pct": 0.8,
            },
        ]
    )

    candidates = attention_agentic_module._augment_candidate_frame(frame, asof_time_utc=asof, run_id="run-123")
    edges = attention_agentic_module._graph_edges(candidates, claim_map={}, run_id="run-123", asof_time_utc=asof)

    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"TLT", "IEF"}
    reasons = json.loads(row["edge_reasons_json"])
    assert "taxonomy_sector" in reasons
    assert "role_match" in reasons
    assert "tags" in reasons
    assert float(row["edge_weight"]) >= 0.42


def test_recompute_attention_candidate_graph_prefers_effective_taxonomy_fields():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-aaa",
                "symbol": "AAA",
                "industry": "Unknown",
                "sector": "Unknown",
                "effective_industry": "Application Software",
                "effective_sector": "Information Technology",
                "change_pct": 3.1,
            },
            {
                "candidate_id": "cand-bbb",
                "symbol": "BBB",
                "industry": "Unknown",
                "sector": "Unknown",
                "effective_industry": "Application Software",
                "effective_sector": "Information Technology",
                "change_pct": 2.8,
            },
        ]
    )

    candidates, edges = attention_agentic_module.recompute_attention_candidate_graph(
        frame,
        claims_frame=pd.DataFrame(),
        run_id="run-123",
        asof_time_utc=asof,
    )

    assert len(candidates) == 2
    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"AAA", "BBB"}
    assert json.loads(row["edge_reasons_json"]) == ["taxonomy_peer"]


def _price_history_frame_for_returns(symbol_starts: dict[str, float], returns: list[float]) -> pd.DataFrame:
    timestamps = pd.date_range("2025-10-01", periods=len(returns) + 1, freq="D", tz="UTC")
    rows: list[dict[str, object]] = []
    for symbol, start_price in symbol_starts.items():
        close = float(start_price)
        closes = [close]
        for ret in returns:
            close *= 1.0 + float(ret)
            closes.append(close)
        for timestamp, close_value in zip(timestamps, closes):
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "open": close_value,
                    "high": close_value,
                    "low": close_value,
                    "close": close_value,
                    "volume": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def test_graph_edges_use_history_correlation_when_graph_signal_is_sparse():
    from services.attention_materialized import bars_by_symbol_from_price_history

    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-aaa",
                "symbol": "AAA",
                "industry": "Unknown",
                "sector": "Industrials",
                "change_pct": 3.4,
            },
            {
                "candidate_id": "cand-bbb",
                "symbol": "BBB",
                "industry": "Unknown",
                "sector": "Communication Services",
                "change_pct": 2.7,
            },
        ]
    )
    returns = [0.011, -0.004, 0.009, -0.006, 0.013, -0.003, 0.008, -0.005] * 12
    price_history = _price_history_frame_for_returns({"AAA": 100.0, "BBB": 57.0}, returns)
    bars_by_symbol = bars_by_symbol_from_price_history(
        price_history,
        ["AAA", "BBB"],
        asof_time_utc=asof,
        lookback_days=180,
    )

    candidates = attention_agentic_module._augment_candidate_frame(frame, asof_time_utc=asof, run_id="run-123")
    history_corr_map = attention_agentic_module._history_correlation_map(
        bars_by_symbol,
        ["AAA", "BBB"],
        min_observations=attention_agentic_module.attention_graph_policy().history_corr_min_observations,
    )
    edges = attention_agentic_module._graph_edges(
        candidates,
        claim_map={},
        history_correlation_map=history_corr_map,
        run_id="run-123",
        asof_time_utc=asof,
    )

    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"AAA", "BBB"}
    assert json.loads(row["edge_reasons_json"]) == ["history_corr"]
    assert float(row["history_correlation"]) >= 0.99
    assert int(row["history_correlation_observations"]) >= 60
    assert float(row["edge_weight"]) >= 0.42


def test_recompute_attention_candidate_graph_uses_price_history_for_sparse_pairs():
    asof = pd.Timestamp("2026-03-31T12:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "candidate_id": "cand-aaa",
                "symbol": "AAA",
                "industry": "Unknown",
                "sector": "Industrials",
                "change_pct": 3.4,
            },
            {
                "candidate_id": "cand-bbb",
                "symbol": "BBB",
                "industry": "Unknown",
                "sector": "Communication Services",
                "change_pct": 2.7,
            },
        ]
    )
    returns = [0.011, -0.004, 0.009, -0.006, 0.013, -0.003, 0.008, -0.005] * 12
    price_history = _price_history_frame_for_returns({"AAA": 100.0, "BBB": 57.0}, returns)

    candidates, edges = attention_agentic_module.recompute_attention_candidate_graph(
        frame,
        claims_frame=pd.DataFrame(),
        price_history_frame=price_history,
        run_id="run-123",
        asof_time_utc=asof,
    )

    assert len(candidates) == 2
    assert len(edges) == 1
    row = edges.iloc[0]
    assert {row["left_symbol"], row["right_symbol"]} == {"AAA", "BBB"}
    assert json.loads(row["edge_reasons_json"]) == ["history_corr"]
    assert float(row["history_correlation"]) >= 0.99
    assert int(row["history_correlation_observations"]) >= 60


def test_build_network_backbone_preserves_isolated_symbols():
    from services.attention_graph_network import build_attention_candidate_network, build_network_backbone

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Energy", "industry": "Oil & Gas", "peer_group_name": "Oil & Gas", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
            {"symbol": "DDD", "sector": "Utilities", "industry": "Water Utilities", "peer_group_name": "Water Utilities", "direction": "up", "change_pct": 1.8, "candidate_score": 55.0, "attention_score": 53.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "left_symbol": "AAA",
                "right_symbol": "BBB",
                "edge_weight": 0.92,
                "edge_reasons": ["taxonomy_peer"],
            }
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    backbone = build_network_backbone(graph, per_node_k=1, keep_quantile=0.9)

    assert set(backbone.nodes()) == {"AAA", "BBB", "CCC", "DDD"}
    assert backbone.number_of_edges() == 1
    assert backbone.degree("CCC") == 0
    assert backbone.degree("DDD") == 0


def test_expand_network_bridge_nodes_adds_real_intermediate_concepts():
    import networkx as nx

    from services.attention_graph_network import (
        build_attention_candidate_network,
        build_network_backbone,
        expand_network_bridge_nodes,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Information Technology", "industry": "Cybersecurity Software", "peer_group_name": "Cybersecurity Software", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
            {"symbol": "DDD", "sector": "Information Technology", "industry": "Cybersecurity Software", "peer_group_name": "Cybersecurity Software", "direction": "down", "change_pct": -1.8, "candidate_score": 55.0, "attention_score": 53.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "left_symbol": "AAA",
                "right_symbol": "BBB",
                "edge_weight": 0.92,
                "edge_reasons": ["taxonomy_peer"],
            },
            {
                "left_symbol": "CCC",
                "right_symbol": "DDD",
                "edge_weight": 0.87,
                "edge_reasons": ["taxonomy_peer"],
            },
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    backbone = build_network_backbone(graph, per_node_k=1, keep_quantile=0.9)
    expanded = expand_network_bridge_nodes(backbone, max_support_share=1.0)

    bridge_nodes = [
        node
        for node, attrs in expanded.nodes(data=True)
        if attrs.get("node_type") == "bridge"
    ]

    assert "bridge::software" in bridge_nodes
    assert expanded.has_edge("AAA", "bridge::software")
    assert expanded.has_edge("CCC", "bridge::software")
    assert not expanded.has_edge("AAA", "CCC")
    assert nx.number_connected_components(expanded.subgraph(["AAA", "BBB", "CCC", "DDD", "bridge::software"])) == 1


def test_build_attention_topology_graph_adds_real_tag_routes_from_universe_taxonomy():
    import networkx as nx

    from services.attention_graph_topology import build_attention_topology_graph

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Application Software", "peer_group_id": "software", "candidate_score": 91.0, "change_pct": 4.1},
            {"symbol": "CCC", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_id": "interactive_media", "candidate_score": 64.0, "change_pct": -2.6},
        ]
    )
    universe = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Application Software", "peer_group_id": "software", "business_role_tags": ["shared_platform"]},
            {"symbol": "CCC", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_id": "interactive_media", "business_role_tags": ["shared_platform"]},
        ]
    )

    graph = build_attention_topology_graph(candidates, universe_frame=universe)

    assert "tag::shared_platform" in graph
    assert nx.has_path(graph, "AAA", "CCC")
    assert nx.shortest_path(graph, "AAA", "CCC") == ["AAA", "tag::shared_platform", "CCC"]


def test_expand_network_real_paths_connects_components_via_real_topology_nodes():
    import networkx as nx

    from services.attention_graph_network import (
        build_attention_candidate_network,
        build_network_backbone,
        expand_network_real_paths,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_name": "Interactive Media", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
            {"symbol": "DDD", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_name": "Interactive Media", "direction": "down", "change_pct": -1.8, "candidate_score": 55.0, "attention_score": 53.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "BBB", "edge_weight": 0.92, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "CCC", "right_symbol": "DDD", "edge_weight": 0.87, "edge_reasons": ["taxonomy_peer"]},
        ]
    )

    topology = nx.Graph()
    topology.add_node("AAA", node_type="symbol", label="AAA", sector="Information Technology", peer_group="Software", industry="Software")
    topology.add_node("CCC", node_type="symbol", label="CCC", sector="Communication Services", peer_group="Interactive Media", industry="Interactive Media")
    topology.add_node("tag::shared_platform", node_type="tag", label="Shared Platform", sector="Unclassified")
    topology.add_edge("AAA", "tag::shared_platform", edge_type="tag", weight=1.0)
    topology.add_edge("tag::shared_platform", "CCC", edge_type="tag", weight=1.0)

    graph = build_attention_candidate_network(candidates, edges)
    expanded = expand_network_real_paths(
        build_network_backbone(graph, per_node_k=1, keep_quantile=0.9),
        topology,
        max_path_length=4,
    )

    assert "tag::shared_platform" in expanded
    assert expanded.nodes["tag::shared_platform"]["is_intermediate_path"] is True
    assert expanded.has_edge("AAA", "tag::shared_platform")
    assert expanded.has_edge("tag::shared_platform", "CCC")
    assert not expanded.has_edge("AAA", "CCC")
    assert nx.has_path(expanded, "BBB", "DDD")


def test_pack_component_positions_prefers_landscape_layout_for_many_components():
    from services.attention_graph_network import build_attention_candidate_network, _pack_component_positions

    candidates = pd.DataFrame(
        [
            {
                "symbol": f"S{i:02d}",
                "sector": "Information Technology" if i % 2 == 0 else "Health Care",
                "industry": f"Group {i // 2}",
                "peer_group_name": f"Group {i // 2}",
                "direction": "up" if i % 2 == 0 else "down",
                "change_pct": 2.5 if i % 2 == 0 else -1.8,
                "candidate_score": 90.0 - i,
                "attention_score": 70.0 - i,
            }
            for i in range(20)
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "left_symbol": f"S{i:02d}",
                "right_symbol": f"S{i + 1:02d}",
                "edge_weight": 0.8,
                "edge_reasons": ["taxonomy_peer"],
            }
            for i in range(0, 20, 2)
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    positions = _pack_component_positions(graph, seed=3)
    xs = [x for x, _ in positions.values()]
    ys = [y for _, y in positions.values()]

    assert len(positions) == 20
    assert (max(xs) - min(xs)) > (max(ys) - min(ys))


def test_plot_attention_candidate_network_calls_out_isolated_band():
    from services.attention_graph_network import (
        build_attention_candidate_network,
        build_network_backbone,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0, "headline": "AAA extends rally"},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0, "headline": "BBB follows software peers"},
            {"symbol": "CCC", "sector": "Energy", "industry": "Oil & Gas", "peer_group_name": "Oil & Gas", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0, "headline": "CCC fades without cluster support"},
            {"symbol": "DDD", "sector": "Utilities", "industry": "Water Utilities", "peer_group_name": "Water Utilities", "direction": "up", "change_pct": 1.8, "candidate_score": 55.0, "attention_score": 53.0, "headline": "DDD rises on single-name news"},
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "left_symbol": "AAA",
                "right_symbol": "BBB",
                "edge_weight": 0.92,
                "edge_reasons": ["taxonomy_peer"],
            }
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    backbone = build_network_backbone(graph, per_node_k=1, keep_quantile=0.9)
    fig = plot_attention_candidate_network(backbone, title="Attention graph", height=480, seed=3)

    node_points = sum(
        len(list(trace.x if trace.x is not None else []))
        for trace in fig.data
        if "markers" in str(getattr(trace, "mode", ""))
    )
    annotation_texts = [str(item.text) for item in list(fig.layout.annotations or [])]

    assert node_points == 4
    assert any("2 isolates" in text for text in annotation_texts)
    assert any(text == "Isolated symbols (2)" for text in annotation_texts)
    assert any("Energy isolates (1)" in text for text in annotation_texts)
    assert fig.layout.height > 480


def test_plot_attention_candidate_network_can_hide_isolates_and_soften_node_overlap():
    from services.attention_graph_network import (
        build_attention_candidate_network,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Energy", "industry": "Oil & Gas", "peer_group_name": "Oil & Gas", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "left_symbol": "AAA",
                "right_symbol": "BBB",
                "edge_weight": 0.92,
                "edge_reasons": ["taxonomy_peer"],
            }
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    fig = plot_attention_candidate_network(
        graph,
        title="Attention graph",
        height=480,
        seed=3,
        show_isolates=False,
    )

    node_points = sum(
        len(list(trace.x if trace.x is not None else []))
        for trace in fig.data
        if "markers" in str(getattr(trace, "mode", ""))
    )
    annotation_texts = [str(item.text) for item in list(fig.layout.annotations or [])]
    primary_trace = next(trace for trace in fig.data if getattr(trace, "name", "") == "Information Technology")

    assert node_points == 2
    assert any("visible symbols" in text for text in annotation_texts)
    assert not any(text == "Isolated symbols (1)" for text in annotation_texts)
    assert float(primary_trace.marker.opacity) < 0.85


def test_plot_attention_candidate_network_adds_component_captions_and_company_labels():
    from services.attention_graph_network import (
        build_attention_candidate_network,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "security_name": "Alpha Software Holdings Inc.",
                "sector": "Information Technology",
                "industry": "Application Software",
                "peer_group_name": "Application Software",
                "direction": "up",
                "change_pct": 3.6,
                "candidate_score": 91.0,
                "attention_score": 84.0,
            },
            {
                "symbol": "AAB",
                "security_name": "Beta Software Group Inc.",
                "sector": "Information Technology",
                "industry": "Application Software",
                "peer_group_name": "Application Software",
                "direction": "up",
                "change_pct": 2.1,
                "candidate_score": 78.0,
                "attention_score": 73.0,
            },
            {
                "symbol": "BBC",
                "security_name": "Circuit Foundry Corporation",
                "sector": "Information Technology",
                "industry": "Semiconductors",
                "peer_group_name": "Semiconductors",
                "direction": "down",
                "change_pct": -1.4,
                "candidate_score": 83.0,
                "attention_score": 70.0,
            },
            {
                "symbol": "BBD",
                "security_name": "Delta Chip Company",
                "sector": "Information Technology",
                "industry": "Semiconductors",
                "peer_group_name": "Semiconductors",
                "direction": "up",
                "change_pct": 1.1,
                "candidate_score": 76.0,
                "attention_score": 68.0,
            },
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "AAB", "edge_weight": 0.88, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "BBC", "right_symbol": "BBD", "edge_weight": 0.84, "edge_reasons": ["taxonomy_peer"]},
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    fig = plot_attention_candidate_network(
        graph,
        title="Attention graph",
        height=460,
        seed=3,
        show_isolates=False,
        label_top_n=12,
    )

    annotation_texts = [str(item.text) for item in list(fig.layout.annotations or [])]
    tech_trace = next(trace for trace in fig.data if getattr(trace, "name", "") == "Information Technology")
    rendered_labels = [str(text) for text in list(getattr(tech_trace, "text", []) or []) if str(text).strip()]

    assert any(text.startswith("CC1:") for text in annotation_texts)
    assert any("Application Software" in text or "Semiconductors" in text for text in annotation_texts)
    assert any("<br>" in text for text in rendered_labels)


def test_plot_attention_candidate_network_adds_subcluster_labels_within_single_component():
    import services.attention_graph_network as attention_graph_network
    from services.attention_graph_network import (
        build_attention_candidate_network,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "security_name": "Alpha Software Holdings Inc.", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 3.6, "candidate_score": 91.0, "attention_score": 84.0},
            {"symbol": "AAB", "security_name": "Beta Software Group Inc.", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 2.1, "candidate_score": 78.0, "attention_score": 73.0},
            {"symbol": "BBC", "security_name": "Circuit Foundry Corporation", "sector": "Information Technology", "industry": "Semiconductors", "peer_group_name": "Semiconductors", "direction": "down", "change_pct": -1.4, "candidate_score": 83.0, "attention_score": 70.0},
            {"symbol": "BBD", "security_name": "Delta Chip Company", "sector": "Information Technology", "industry": "Semiconductors", "peer_group_name": "Semiconductors", "direction": "up", "change_pct": 1.1, "candidate_score": 76.0, "attention_score": 68.0},
            {"symbol": "CCC", "security_name": "Core Energy Group", "sector": "Information Technology", "industry": "Solar Power Electronics", "peer_group_name": "Solar Power Electronics", "direction": "up", "change_pct": 0.8, "candidate_score": 72.0, "attention_score": 64.0},
            {"symbol": "CCD", "security_name": "Helix Power Systems", "sector": "Information Technology", "industry": "Solar Power Electronics", "peer_group_name": "Solar Power Electronics", "direction": "down", "change_pct": -0.7, "candidate_score": 68.0, "attention_score": 61.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "AAB", "edge_weight": 0.88, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "BBC", "right_symbol": "BBD", "edge_weight": 0.84, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "CCC", "right_symbol": "CCD", "edge_weight": 0.82, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "AAA", "right_symbol": "BBC", "edge_weight": 0.58, "edge_reasons": ["theme"]},
            {"left_symbol": "BBC", "right_symbol": "CCC", "edge_weight": 0.55, "edge_reasons": ["theme"]},
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    fixed_positions = {
        "AAA": (0.0, 2.2),
        "AAB": (1.0, 2.2),
        "BBC": (3.6, 1.2),
        "BBD": (4.6, 1.2),
        "CCC": (7.2, 0.2),
        "CCD": (8.2, 0.2),
    }
    original_pack = attention_graph_network._pack_component_positions
    attention_graph_network._pack_component_positions = lambda _graph, seed=7: dict(fixed_positions)
    try:
        fig = plot_attention_candidate_network(
            graph,
            title="Attention graph",
            height=460,
            seed=3,
            show_isolates=False,
            label_top_n=12,
            show_component_labels=True,
        )
    finally:
        attention_graph_network._pack_component_positions = original_pack

    annotation_texts = [str(item.text) for item in list(fig.layout.annotations or [])]

    assert any(text.startswith("CC1:") for text in annotation_texts)
    assert any(text == "Application Software" for text in annotation_texts)
    assert any(text == "Semiconductors" for text in annotation_texts)


def test_serialize_attention_home_payload_preserves_homepage_graph():
    from services.attention_materialized import deserialize_attention_home_payload, serialize_attention_home_payload

    payload = {
        "run_id": "run-123",
        "generated_at_utc": "2026-04-01T18:00:00Z",
        "coverage_summary": {"candidate_count": 4},
        "homepage_graph": {
            "figure": {"data": [], "layout": {"height": 320, "showlegend": False}},
            "summary": {"connected_components": 1},
        },
    }

    frame = serialize_attention_home_payload(payload)
    restored = deserialize_attention_home_payload(frame)

    assert restored["run_id"] == "run-123"
    assert restored["homepage_graph"]["figure"]["layout"]["height"] == 320
    assert restored["homepage_graph"]["summary"]["connected_components"] == 1


def test_build_homepage_attention_graph_payload_returns_compact_banner_figure():
    import plotly.graph_objects as go

    from services.attention_graph_network import build_homepage_attention_graph_payload

    candidates = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "sector": "Information Technology",
                "industry": "Application Software",
                "peer_group_name": "Application Software",
                "direction": "up",
                "change_pct": 4.1,
                "candidate_score": 91.0,
                "attention_score": 88.0,
                "business_role_tags": ["creator economy"],
            },
            {
                "symbol": "BBB",
                "sector": "Information Technology",
                "industry": "Application Software",
                "peer_group_name": "Application Software",
                "direction": "up",
                "change_pct": 3.7,
                "candidate_score": 82.0,
                "attention_score": 79.0,
            },
            {
                "symbol": "CCC",
                "sector": "Communication Services",
                "industry": "Interactive Media",
                "peer_group_name": "Interactive Media",
                "direction": "down",
                "change_pct": -2.6,
                "candidate_score": 64.0,
                "attention_score": 61.0,
                "business_role_tags": ["creator economy"],
            },
            {
                "symbol": "DDD",
                "sector": "Communication Services",
                "industry": "Interactive Media",
                "peer_group_name": "Interactive Media",
                "direction": "down",
                "change_pct": -1.8,
                "candidate_score": 55.0,
                "attention_score": 53.0,
            },
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "BBB", "edge_weight": 0.92, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "CCC", "right_symbol": "DDD", "edge_weight": 0.87, "edge_reasons": ["taxonomy_peer"]},
        ]
    )

    payload = build_homepage_attention_graph_payload(
        candidates,
        edges,
        height=320,
        label_top_n=6,
    )
    figure = go.Figure(payload["figure"])
    path_trace = next(trace for trace in figure.data if getattr(trace, "name", "") == "Path Nodes")

    assert payload["summary"]["connected_components"] == 1
    assert figure.layout.showlegend is False
    assert figure.layout.height == 320
    annotation_texts = [str(item.text) for item in list(figure.layout.annotations or [])]
    assert any(text.startswith("CC1:") for text in annotation_texts)
    assert float(path_trace.marker.opacity) < 0.6


def test_plot_attention_candidate_network_auto_labels_reduce_dense_path_clutter():
    import networkx as nx

    import services.attention_graph_network as attention_graph_network

    graph = nx.Graph()
    for symbol, score, change, attention in [
        ("AAA", 96.0, 4.1, 88.0),
        ("BBB", 88.0, 3.4, 79.0),
        ("CCC", 84.0, -2.2, 73.0),
    ]:
        graph.add_node(
            symbol,
            symbol=symbol,
            node_type="security",
            sector="Information Technology",
            industry="Software",
            peer_group="Software",
            direction="up" if change > 0 else "down",
            change_pct=change,
            candidate_score=score,
            attention_score=attention,
        )
    for node, label, degree_hint in [
        ("peer::software", "Software", "peer"),
        ("tag::shared_platform", "Shared Platform", "tag"),
        ("sector::it", "Information Technology", "sector"),
    ]:
        graph.add_node(
            node,
            symbol=label,
            node_type="path",
            sector="Path Nodes",
            industry=label,
            peer_group=label,
            direction="path",
            change_pct=0.0,
            candidate_score=18.0,
            attention_score=0.0,
            source_label="intermediate path",
            topology_node_type=degree_hint,
            topology_sector="Information Technology",
            topology_peer_group="Software",
            topology_industry="Software",
            is_intermediate_path=True,
        )
    for index in range(11):
        symbol = f"F{index:02d}"
        graph.add_node(
            symbol,
            symbol=symbol,
            node_type="security",
            sector="Industrials",
            industry="Capital Goods",
            peer_group="Capital Goods",
            direction="up",
            change_pct=1.0,
            candidate_score=22.0 - index,
            attention_score=0.0,
        )

    for left, right, weight, reason in [
        ("AAA", "BBB", 0.86, "taxonomy_peer"),
        ("BBB", "CCC", 0.82, "taxonomy_peer"),
        ("peer::software", "AAA", 0.32, "topology_membership"),
        ("peer::software", "BBB", 0.32, "topology_membership"),
        ("tag::shared_platform", "BBB", 0.34, "topology_tag"),
        ("tag::shared_platform", "CCC", 0.34, "topology_tag"),
        ("sector::it", "AAA", 0.26, "topology_taxonomy"),
        ("sector::it", "CCC", 0.26, "topology_taxonomy"),
    ]:
        graph.add_edge(left, right, edge_weight=weight, edge_reasons=[reason], edge_reason_text=reason)
    graph.add_edge("CCC", "F00", edge_weight=0.58, edge_reasons=["theme"], edge_reason_text="theme")
    for index in range(10):
        left = f"F{index:02d}"
        right = f"F{index + 1:02d}"
        graph.add_edge(left, right, edge_weight=0.52, edge_reasons=["theme"], edge_reason_text="theme")

    fixed_positions = {
        "AAA": (0.00, 0.00),
        "BBB": (0.38, 0.08),
        "CCC": (2.95, 0.12),
        "peer::software": (0.18, 0.22),
        "tag::shared_platform": (0.56, 0.16),
        "sector::it": (3.22, 0.02),
    }
    for index in range(11):
        fixed_positions[f"F{index:02d}"] = (5.0 + (index * 0.9), 0.25 + ((index % 2) * 0.2))
    original_pack = attention_graph_network._pack_component_positions
    attention_graph_network._pack_component_positions = lambda _graph, seed=7: dict(fixed_positions)
    try:
        fig = attention_graph_network.plot_attention_candidate_network(
            graph,
            title="Crowded graph",
            height=480,
            seed=3,
            label_top_n=6,
            show_isolates=False,
        )
    finally:
        attention_graph_network._pack_component_positions = original_pack

    path_trace = next(trace for trace in fig.data if getattr(trace, "name", "") == "Path Nodes")
    path_labels = [str(text) for text in list(path_trace.text or []) if str(text).strip()]
    all_label_positions = []
    for trace in fig.data:
        if getattr(trace, "mode", "") != "markers+text":
            continue
        trace_positions = list(getattr(trace, "textposition", []) or [])
        for text, position in zip(list(getattr(trace, "text", []) or []), trace_positions):
            if str(text).strip():
                all_label_positions.append(str(position))

    assert 0 < len(path_labels) < 3
    assert len(set(all_label_positions)) >= 2


def test_plot_attention_candidate_network_fades_bridge_concepts():
    from services.attention_graph_network import (
        build_attention_candidate_network,
        build_network_backbone,
        expand_network_bridge_nodes,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Application Software", "peer_group_name": "Application Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Information Technology", "industry": "Cybersecurity Software", "peer_group_name": "Cybersecurity Software", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
            {"symbol": "DDD", "sector": "Information Technology", "industry": "Cybersecurity Software", "peer_group_name": "Cybersecurity Software", "direction": "down", "change_pct": -1.8, "candidate_score": 55.0, "attention_score": 53.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "BBB", "edge_weight": 0.92, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "CCC", "right_symbol": "DDD", "edge_weight": 0.87, "edge_reasons": ["taxonomy_peer"]},
        ]
    )

    graph = build_attention_candidate_network(candidates, edges)
    plot_graph = expand_network_bridge_nodes(
        build_network_backbone(graph, per_node_k=1, keep_quantile=0.9),
        max_support_share=1.0,
    )
    fig = plot_attention_candidate_network(plot_graph, title="Bridge graph", height=480, seed=3)

    bridge_traces = [trace for trace in fig.data if getattr(trace, "name", "") == "Bridge Concepts"]
    bridge_edge_traces = [
        trace
        for trace in fig.data
        if getattr(trace, "mode", "") == "lines"
        and "bridge concept" in "".join(getattr(trace, "text", []) or [])
    ]

    assert bridge_traces
    assert bridge_traces[0].marker.color == "#94a3b8"
    assert float(bridge_traces[0].marker.opacity) < 0.7
    assert bridge_edge_traces


def test_plot_attention_candidate_network_fades_real_path_nodes():
    import networkx as nx

    from services.attention_graph_network import (
        build_attention_candidate_network,
        build_network_backbone,
        expand_network_real_paths,
        plot_attention_candidate_network,
    )

    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 4.1, "candidate_score": 91.0, "attention_score": 88.0},
            {"symbol": "BBB", "sector": "Information Technology", "industry": "Software", "peer_group_name": "Software", "direction": "up", "change_pct": 3.7, "candidate_score": 82.0, "attention_score": 79.0},
            {"symbol": "CCC", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_name": "Interactive Media", "direction": "down", "change_pct": -2.6, "candidate_score": 64.0, "attention_score": 61.0},
            {"symbol": "DDD", "sector": "Communication Services", "industry": "Interactive Media", "peer_group_name": "Interactive Media", "direction": "down", "change_pct": -1.8, "candidate_score": 55.0, "attention_score": 53.0},
        ]
    )
    edges = pd.DataFrame(
        [
            {"left_symbol": "AAA", "right_symbol": "BBB", "edge_weight": 0.92, "edge_reasons": ["taxonomy_peer"]},
            {"left_symbol": "CCC", "right_symbol": "DDD", "edge_weight": 0.87, "edge_reasons": ["taxonomy_peer"]},
        ]
    )
    topology = nx.Graph()
    topology.add_node("AAA", node_type="symbol", label="AAA", sector="Information Technology", peer_group="Software", industry="Software")
    topology.add_node("CCC", node_type="symbol", label="CCC", sector="Communication Services", peer_group="Interactive Media", industry="Interactive Media")
    topology.add_node("tag::shared_platform", node_type="tag", label="Shared Platform", sector="Unclassified")
    topology.add_edge("AAA", "tag::shared_platform", edge_type="tag", weight=1.0)
    topology.add_edge("tag::shared_platform", "CCC", edge_type="tag", weight=1.0)

    graph = build_attention_candidate_network(candidates, edges)
    plot_graph = expand_network_real_paths(
        build_network_backbone(graph, per_node_k=1, keep_quantile=0.9),
        topology,
        max_path_length=4,
    )
    fig = plot_attention_candidate_network(plot_graph, title="Real path graph", height=480, seed=3)

    path_traces = [trace for trace in fig.data if getattr(trace, "name", "") == "Path Nodes"]
    path_edge_traces = [
        trace
        for trace in fig.data
        if getattr(trace, "mode", "") == "lines"
        and "topology path" in "".join(getattr(trace, "text", []) or [])
    ]

    assert path_traces
    assert path_traces[0].marker.color == "#94a3b8"
    assert float(path_traces[0].marker.opacity) < 0.6
    assert path_edge_traces
