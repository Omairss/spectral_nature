from __future__ import annotations

from services import web_research
from services.web_research import (
    SerpAPIConfig,
    SerpAPISearchClient,
    TavilyConfig,
    TavilySearchClient,
    WebResearchError,
    load_serpapi_config,
    load_tavily_config,
)


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

    def post(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("No fake responses left for POST request.")
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


def test_serpapi_search_records_success_telemetry(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CONNECTOR_TELEMETRY_ENABLED", "true")
    monkeypatch.setattr(web_research, "_record_connector_call", lambda **kwargs: calls.append(kwargs))
    client = SerpAPISearchClient(
        SerpAPIConfig(api_key="serp-test-key"),
        session=_FakeSession(
            [
                _FakeResponse(
                    {
                        "news_results": [
                            {
                                "title": "BMY update",
                                "link": "https://example.com/bmy",
                                "summary": "BMY pipeline news",
                                "source": "Example",
                            }
                        ]
                    }
                )
            ]
        ),
    )

    rows = client.search("BMY stock today", news=True, num=3)

    assert len(rows) == 1
    assert calls[0]["provider"] == "serpapi"
    assert calls[0]["operation"] == "search_news"
    assert calls[0]["status"] == "success"
    assert calls[0]["http_status"] == 200
    assert calls[0]["result_count"] == 1
    assert calls[0]["metadata"]["query_sha256"]
    assert "BMY stock today" not in str(calls[0]["metadata"])


def test_tavily_search_records_failure_telemetry(monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CONNECTOR_TELEMETRY_ENABLED", "true")
    monkeypatch.setattr(web_research, "_record_connector_call", lambda **kwargs: calls.append(kwargs))
    client = TavilySearchClient(
        TavilyConfig(api_key="tavily-test-key"),
        session=_FakeSession([_FakeResponse({"error": "quota"}, status_code=432, text="quota exceeded")]),
    )

    try:
        client.search("BMY stock today", max_results=3)
    except WebResearchError:
        pass
    else:
        raise AssertionError("Expected Tavily failure")

    assert calls[0]["provider"] == "tavily"
    assert calls[0]["operation"] == "search"
    assert calls[0]["status"] == "failure"
    assert calls[0]["http_status"] == 432
    assert "quota exceeded" in str(calls[0]["error"])
