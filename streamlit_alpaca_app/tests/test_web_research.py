from __future__ import annotations

from services.web_research import load_serpapi_config, load_tavily_config


def test_load_serpapi_config_from_env(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "serp-test-key")
    monkeypatch.delenv("SERPAPI_API_KEY_SECRET_NAME", raising=False)
    cfg = load_serpapi_config()
    assert cfg is not None
    assert cfg.api_key == "serp-test-key"
    assert cfg.engine == "google"
    assert cfg.gl == "us"


def test_load_tavily_config_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-key")
    monkeypatch.delenv("TAVILY_API_KEY_SECRET_NAME", raising=False)
    monkeypatch.setenv("TAVILY_INCLUDE_ANSWER", "true")
    cfg = load_tavily_config()
    assert cfg is not None
    assert cfg.api_key == "tavily-test-key"
    assert cfg.topic == "news"
    assert cfg.include_answer is True
