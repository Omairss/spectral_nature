from __future__ import annotations

import pandas as pd

from services import omnibar_research



def test_market_impact_map_highlights_oil_and_spillover_symbols(monkeypatch):
    monkeypatch.setattr(
        omnibar_research,
        "_llm_query_intent",
        lambda query, llm_client, max_symbols: {
            "event_type": "oil",
            "direction": "up",
            "theme_label": "oil supply risk",
            "evidence_needed": True,
            "rows": [
                {"symbol": "USO", "role": "oil ETF", "expected_bias": "up", "why": ""},
                {"symbol": "CVX", "role": "energy equity", "expected_bias": "up", "why": ""},
                {"symbol": "BDRY", "role": "shipping proxy", "expected_bias": "up", "why": ""},
                {"symbol": "JETS", "role": "airlines", "expected_bias": "down", "why": ""},
            ],
        },
    )

    payload = omnibar_research.market_impact_map(
        query="How are things going to pan out now that there's no agreement in Iran US talks",
        max_symbols=8,
    )

    symbols = [row["symbol"] for row in payload["summary"]]
    assert payload["theme"] == "oil"
    assert payload["expected_direction"] == "up"
    assert "USO" in symbols
    assert "CVX" in symbols
    assert "BDRY" in symbols


def test_open_page_uses_browser_helper(monkeypatch):
    monkeypatch.setattr(
        omnibar_research,
        "browse_page",
        lambda url, max_text_chars: {
            "url": url,
            "final_url": url,
            "title": "Example page",
            "excerpt": "Example excerpt.",
            "text": "Example visible text.",
            "mode": "http",
            "warning": "",
        },
    )

    payload = omnibar_research.open_page(url="https://example.com/story", max_chars=1200)

    assert payload["summary"][0]["title"] == "Example page"
    assert "Example visible text." in payload["llm_context_text"]


def test_live_event_evidence_uses_impact_symbols_when_no_focus_symbols(monkeypatch):
    def _fake_search_market_event_news_payload(event, *, max_results, serp_client=None, tavily_client=None, **kwargs):
        del serp_client, tavily_client, kwargs
        assert event["event_type"] == "oil"
        assert "USO" in event["supporting_symbols"]
        return {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Oil rises on renewed supply risk",
                        "summary": "Supply risk rose after talks stalled.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-04-12T12:00:00Z"),
                        "url": "https://example.com/oil-story",
                    }
                ]
            )
        }

    def _fake_search_symbol_news_payload(symbol, *, company_name="", max_results=8, serp_client=None, tavily_client=None, llm_client=None):
        del company_name, max_results, serp_client, tavily_client, llm_client
        return {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": f"{symbol} reacts to energy shock",
                        "summary": "Symbol-specific follow-through.",
                        "source": "Bloomberg",
                        "published_at": pd.Timestamp("2026-04-12T13:00:00Z"),
                        "url": f"https://example.com/{symbol.lower()}-story",
                    }
                ]
            )
        }

    class _FakeLayer:
        def resolve_asset_metadata(self, ticker: str, *, force_refresh: bool = False):
            del force_refresh
            return type("Resolved", (), {"payload": {"name": ticker}})()

        def resolve_attention_home_1d(self, *, force_refresh: bool = False):
            del force_refresh
            return type("Resolved", (), {"payload": {}})()

    monkeypatch.setattr(omnibar_research, "search_market_event_news_payload", _fake_search_market_event_news_payload)
    monkeypatch.setattr(omnibar_research, "search_symbol_news_payload", _fake_search_symbol_news_payload)
    monkeypatch.setattr(omnibar_research, "load_aql_zopedia_llm_client", lambda **kwargs: None)
    monkeypatch.setattr(
        omnibar_research,
        "_llm_query_intent",
        lambda query, llm_client, max_symbols: {
            "event_type": "oil",
            "direction": "up",
            "theme_label": "oil supply risk",
            "evidence_needed": True,
            "rows": [
                {"symbol": "USO", "role": "oil ETF", "expected_bias": "up", "why": ""},
                {"symbol": "CVX", "role": "energy equity", "expected_bias": "up", "why": ""},
            ],
        },
    )
    monkeypatch.setattr(
        omnibar_research,
        "resolve_omnibar",
        lambda **kwargs: {"search_results": [], "intent": "agent"},
    )

    payload = omnibar_research.live_event_evidence(
        query="Iran talks stalled again",
        layer=_FakeLayer(),
    )

    assert payload["theme"] == "oil"
    assert "USO" in payload["focus_symbols"]
    assert payload["summary"][0]["headline"]


def test_live_event_evidence_returns_partial_when_internal_impact_map_times_out(monkeypatch):
    def _slow_market_impact_map(*args, **kwargs):
        import time

        del args, kwargs
        time.sleep(2.0)
        return {"theme": "oil", "expected_direction": "down", "focus_symbols": ["USO"], "summary": []}

    def _fake_search_market_event_news_payload(event, *, max_results, serp_client=None, tavily_client=None, **kwargs):
        del event, max_results, serp_client, tavily_client, kwargs
        return {
            "articles": pd.DataFrame(
                [
                    {
                        "headline": "Oil falls as geopolitical risk premium fades",
                        "summary": "Talks reduced supply-risk expectations.",
                        "source": "Reuters",
                        "published_at": pd.Timestamp("2026-05-20T12:00:00Z"),
                        "url": "https://example.com/oil-geopolitics",
                    }
                ]
            ),
            "messages": [],
        }

    class _FakeLayer:
        def resolve_attention_home_1d(self, *, force_refresh: bool = False):
            del force_refresh
            return type("Resolved", (), {"payload": {}})()

    monkeypatch.setattr(omnibar_research, "market_impact_map", _slow_market_impact_map)
    monkeypatch.setattr(omnibar_research, "search_market_event_news_payload", _fake_search_market_event_news_payload)
    monkeypatch.setattr(omnibar_research, "_fast_search_clients", lambda timeout_seconds: (None, None))
    monkeypatch.setattr(omnibar_research, "get_config_param", lambda key: 1 if "timeout" in str(key).lower() else 6)

    payload = omnibar_research.live_event_evidence(
        query="What about geopolitics?",
        focus_symbols=["USO"],
        layer=_FakeLayer(),
    )

    assert payload["summary"][0]["headline"] == "Oil falls as geopolitical risk premium fades"
    assert any("live_event_impact_map timed out" in item for item in payload["messages"])
