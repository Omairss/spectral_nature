from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import numpy as np

from services.alpaca_api import AlpacaAPI
from services import company as company_module
from services.company import build_attention_news_narrative, build_company_description, load_recent_news, summarize_recent_news
from services.config import AppConfig
from services import data_cache
from services import fred as fred_module
from services.attention_context_llm import build_attention_context_narratives, build_edgar_evidence, merge_attention_context_with_llm
from services.data_cache import CacheTarget, cached_frame
from services.edgar import EdgarClient, build_attention_context_bundle
from services.fred import FREDClient, FredSeriesSpec, build_fred_figure, build_fred_series_summary, format_fred_value, fred_categories, load_fred_dashboard
from services.fundamentals import load_quarterly_fundamentals
from services.llm import AzureOpenAIChatJSONClient, LLMConfig, load_llm_config
from services.market import (
    business_focus_for_symbol,
    business_focus_description,
    business_focus_options,
    business_focus_universe,
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


def test_business_focus_universe_uses_custom_business_lenses():
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
    assert business_focus_description("Retail")


def test_business_focus_for_symbol_maps_curated_names_and_defaults_to_all_market():
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


def test_build_liquidity_ranked_equity_universe_keeps_pinned_symbols(monkeypatch):
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
    assert ranked.iloc[0]["selection_reason"] == "pinned_curated"
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


def test_load_recent_news_and_summary_use_recent_articles():
    payload = load_recent_news(FakeCompanyAPI(), "AAPL", days=14, limit=4)
    summary = summarize_recent_news("AAPL", payload)

    assert payload["source"] == "alpaca"
    assert not payload["articles"].empty
    assert summary["summary_lines"]
    assert "tone is positive" in summary["summary_lines"][0].lower()


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
