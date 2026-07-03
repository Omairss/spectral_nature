from __future__ import annotations

from pathlib import Path

import pandas as pd

from services.aql import collector
from services.aql import config as aql_config
from services.web_research import (
    SerperConfig,
    SerperSearchClient,
    WebSearchResult,
    search_client_provider,
)


APP_ROOT = Path(__file__).resolve().parents[1]


def test_aql_search_clients_use_serper_only_when_configured(monkeypatch):
    monkeypatch.setattr(aql_config, "load_serper_config", lambda: SerperConfig(api_key="serper-test-key"))

    primary_client, secondary_client = aql_config._load_search_clients()

    assert isinstance(primary_client, SerperSearchClient)
    assert search_client_provider(primary_client) == "serper"
    assert secondary_client is None


def test_aql_search_clients_do_not_fallback_without_serper(monkeypatch):
    monkeypatch.setattr(aql_config, "load_serper_config", lambda: None)

    primary_client, secondary_client = aql_config._load_search_clients()

    assert primary_client is None
    assert secondary_client is None


def test_symbol_news_payload_loads_serper_as_primary_search(monkeypatch):
    instances: list[object] = []

    class _FakeSerperClient:
        def __init__(self, config):
            self.config = config
            self.calls: list[dict[str, object]] = []
            instances.append(self)

        def search(self, query: str, *, news: bool = False, num: int = 10):
            self.calls.append({"query": query, "news": news, "num": num})
            return [
                WebSearchResult(
                    provider="serper",
                    title="NVIDIA NVDA shares fall after AI chip export report",
                    url="https://example.com/nvidia-ai-chip-report",
                    snippet="NVIDIA stock moved after a report about AI chip demand and export controls.",
                    source="Example News",
                    published_at="2 hours ago",
                )
            ]

    monkeypatch.setattr(collector, "load_serper_config", lambda: SerperConfig(api_key="serper-test-key"))
    monkeypatch.setattr(collector, "SerperSearchClient", _FakeSerperClient)

    payload = collector.search_symbol_news_payload(
        "NVDA",
        company_name="NVIDIA",
        max_results=4,
        asof_time_utc=pd.Timestamp("2026-07-02T15:00:00Z"),
    )

    assert instances
    assert instances[0].calls[0]["news"] is True
    assert payload["source"] == "serper"
    articles = payload["articles"]
    assert not articles.empty
    assert articles.iloc[0]["provider"] == "serper"
    assert "AI chip" in articles.iloc[0]["headline"]


def test_aql_collector_model_calls_use_zopedia_gateway():
    text = (APP_ROOT / "services/aql/collector.py").read_text(encoding="utf-8")

    assert ".generate_json(" not in text
    assert "generate_json_via_aql_zopedia_gateway" in text
