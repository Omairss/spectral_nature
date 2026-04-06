from __future__ import annotations

from services.web_research import SerpAPIConfig, SerpAPISearchClient, load_serpapi_config, load_tavily_config


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)

    def get(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("No fake responses left for GET request.")
        return self._responses.pop(0)


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


def test_search_ai_overview_parses_serp_payload():
    client = SerpAPISearchClient(
        SerpAPIConfig(api_key="serp-test-key"),
        session=_FakeSession(
            [
                _FakeResponse(
                    {
                        "ai_overview": {
                            "title": "Google AI Overview",
                            "snippet": "BMY pipeline update includes positive Phase 3 data.",
                            "sources": [{"link": "https://example.com/bmy-pipeline"}],
                        }
                    }
                )
            ]
        ),
    )
    result = client.search_ai_overview("BMY stock today")
    assert result is not None
    assert result.provider == "serpapi_ai_overview"
    assert result.source == "Google AI Overview"
    assert "pipeline" in result.snippet.lower()
    assert result.url == "https://example.com/bmy-pipeline"


def test_search_ai_overview_returns_none_when_not_present():
    client = SerpAPISearchClient(
        SerpAPIConfig(api_key="serp-test-key"),
        session=_FakeSession([_FakeResponse({"organic_results": []})]),
    )
    assert client.search_ai_overview("BMY stock today") is None
