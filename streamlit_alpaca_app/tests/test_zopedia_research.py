from __future__ import annotations

import pandas as pd

from services import zopedia_research



def test_market_impact_map_highlights_oil_and_spillover_symbols(monkeypatch):
    monkeypatch.setattr(
        zopedia_research,
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

    payload = zopedia_research.market_impact_map(
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
        zopedia_research,
        "browse_page",
        lambda url, **kwargs: {
            "url": url,
            "final_url": url,
            "title": "Example page",
            "excerpt": "Example excerpt.",
            "text": "Example visible text.",
            "mode": "http",
            "warning": "",
        },
    )

    payload = zopedia_research.open_page(url="https://example.com/story", max_chars=1200)

    assert payload["summary"][0]["title"] == "Example page"
    assert "Example visible text." in payload["llm_context_text"]


def test_live_evidence_preserves_the_planners_query_without_hidden_classification(monkeypatch):
    question = "Which developments explain the observed group movement?"
    captured = []
    def acquire(event, **kwargs):
        captured.append((event, kwargs))
        return {"articles": pd.DataFrame([{"headline":"Original source", "summary":"Original detail",
            "url":"https://example.com/original", "published_at":"2026-11-03T17:00:00Z"}])}
    def classify(*args, **kwargs):
        raise AssertionError("Acquisition must not run a second market planner")
    class Layer:
        def resolve_attention_home_1d(self, **kwargs):
            return type("Resolved", (), {"payload": {}})()
    monkeypatch.setattr(zopedia_research, "search_market_event_news_payload", acquire)
    monkeypatch.setattr(zopedia_research, "market_impact_map", classify)
    result = zopedia_research.live_event_evidence(query=question, layer=Layer())
    assert captured[0][0]["event_title"] == question
    assert captured[0][1]["search_query"] == question
    assert captured[0][1]["allow_llm_query"] is False
    assert result["summary"][0]["url"] == "https://example.com/original"
    assert result["summary"][0]["published_at"] == "2026-11-03T17:00:00+00:00"


def test_zopedia_research_does_not_load_search_providers_directly():
    source = zopedia_research.__loader__.get_source(zopedia_research.__name__)  # type: ignore[union-attr]

    assert "load_serper_config" not in source
    assert "SerperSearchClient" not in source
    assert "_fast_search_clients" not in source
