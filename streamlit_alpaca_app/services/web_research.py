from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests

from .secrets import resolve_secret_value


class WebResearchError(RuntimeError):
    pass


def _clean(value: object) -> str:
    return str(value or "").strip()


def _source_label(value: object) -> str:
    if isinstance(value, dict):
        return _clean(value.get("name") or value.get("source") or value.get("domain"))
    return _clean(value)


def _timeout_seconds() -> int:
    raw = _clean(os.getenv("WEB_RESEARCH_TIMEOUT_SECONDS")) or "20"
    try:
        return max(int(raw), 5)
    except Exception:
        return 20


@dataclass(frozen=True)
class WebSearchResult:
    provider: str
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_at: str = ""
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class SerpAPIConfig:
    api_key: str
    timeout_seconds: int = 20
    engine: str = "google"
    google_domain: str = "google.com"
    hl: str = "en"
    gl: str = "us"


@dataclass(frozen=True)
class TavilyConfig:
    api_key: str
    timeout_seconds: int = 20
    topic: str = "news"
    search_depth: str = "advanced"
    include_answer: bool = False
    include_raw_content: bool = False


def _resolve_serpapi_api_key() -> str:
    return resolve_secret_value(
        ["SERPAPI_API_KEY", "SERP_API_KEY"],
        secret_name_env="SERPAPI_API_KEY_SECRET_NAME",
        default_secret_name="serpapi-api-key",
    )


def _resolve_tavily_api_key() -> str:
    return resolve_secret_value(
        ["TAVILY_API_KEY", "TAVILY_KEY"],
        secret_name_env="TAVILY_API_KEY_SECRET_NAME",
        default_secret_name="tavily-api-key",
    )


def load_serpapi_config() -> SerpAPIConfig | None:
    api_key = _resolve_serpapi_api_key()
    if not api_key:
        return None
    return SerpAPIConfig(
        api_key=api_key,
        timeout_seconds=_timeout_seconds(),
        engine=_clean(os.getenv("SERPAPI_ENGINE")) or "google",
        google_domain=_clean(os.getenv("SERPAPI_GOOGLE_DOMAIN")) or "google.com",
        hl=_clean(os.getenv("SERPAPI_HL")) or "en",
        gl=_clean(os.getenv("SERPAPI_GL")) or "us",
    )


def load_tavily_config() -> TavilyConfig | None:
    api_key = _resolve_tavily_api_key()
    if not api_key:
        return None
    return TavilyConfig(
        api_key=api_key,
        timeout_seconds=_timeout_seconds(),
        topic=_clean(os.getenv("TAVILY_TOPIC")) or "news",
        search_depth=_clean(os.getenv("TAVILY_SEARCH_DEPTH")) or "advanced",
        include_answer=(_clean(os.getenv("TAVILY_INCLUDE_ANSWER")) or "").lower() in {"1", "true", "yes", "on"},
        include_raw_content=(_clean(os.getenv("TAVILY_INCLUDE_RAW_CONTENT")) or "").lower() in {"1", "true", "yes", "on"},
    )


class SerpAPISearchClient:
    def __init__(self, config: SerpAPIConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise WebResearchError("Missing SerpApi key.")
        self.config = config
        self.session = session or requests.Session()

    def search(self, query: str, *, news: bool = False, num: int = 10) -> list[WebSearchResult]:
        engine = "google_news" if news else self.config.engine
        response = self.session.get(
            "https://serpapi.com/search.json",
            params={
                "api_key": self.config.api_key,
                "engine": engine,
                "q": query,
                "num": max(int(num), 1),
                "google_domain": self.config.google_domain,
                "hl": self.config.hl,
                "gl": self.config.gl,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code != 200:
            raise WebResearchError(f"SerpApi request failed status={response.status_code}: {response.text[:400]}")
        payload = response.json()
        rows: list[WebSearchResult] = []
        organic_items = payload.get("news_results") if news else payload.get("organic_results")
        if not isinstance(organic_items, list):
            organic_items = []
        for item in organic_items:
            if not isinstance(item, dict):
                continue
            rows.append(
                WebSearchResult(
                    provider="serpapi",
                    title=_clean(item.get("title")),
                    url=_clean(item.get("link")),
                    snippet=_clean(item.get("snippet") or item.get("summary")),
                    source=_source_label(item.get("source")),
                    published_at=_clean(item.get("date")),
                    raw=item,
                )
            )
        return rows

    def search_ai_overview(self, query: str) -> WebSearchResult | None:
        response = self.session.get(
            "https://serpapi.com/search.json",
            params={
                "api_key": self.config.api_key,
                "engine": self.config.engine,
                "q": query,
                "num": 5,
                "google_domain": self.config.google_domain,
                "hl": self.config.hl,
                "gl": self.config.gl,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code != 200:
            raise WebResearchError(f"SerpApi request failed status={response.status_code}: {response.text[:400]}")
        payload = response.json()
        overview = payload.get("ai_overview")
        if not isinstance(overview, dict):
            overview = payload.get("ai_overview_result")
        if not isinstance(overview, dict):
            return None
        snippet = _clean(
            overview.get("snippet")
            or overview.get("summary")
            or overview.get("answer")
            or overview.get("content")
        )
        title = _clean(overview.get("title")) or "Google AI Overview"
        source_url = ""
        sources = overview.get("sources")
        if isinstance(sources, list) and sources:
            first = sources[0]
            if isinstance(first, dict):
                source_url = _clean(first.get("link") or first.get("url"))
        if not source_url:
            organic_items = payload.get("organic_results")
            if isinstance(organic_items, list) and organic_items:
                first = organic_items[0]
                if isinstance(first, dict):
                    source_url = _clean(first.get("link"))
        if not snippet and not source_url:
            return None
        return WebSearchResult(
            provider="serpapi_ai_overview",
            title=title,
            url=source_url,
            snippet=snippet,
            source="Google AI Overview",
            published_at="",
            raw=overview,
        )


class TavilySearchClient:
    def __init__(self, config: TavilyConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise WebResearchError("Missing Tavily key.")
        self.config = config
        self.session = session or requests.Session()

    def search(self, query: str, *, max_results: int = 10, topic: str | None = None) -> list[WebSearchResult]:
        response = self.session.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.config.api_key,
                "query": query,
                "topic": topic or self.config.topic,
                "search_depth": self.config.search_depth,
                "max_results": max(int(max_results), 1),
                "include_answer": self.config.include_answer,
                "include_raw_content": self.config.include_raw_content,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code != 200:
            raise WebResearchError(f"Tavily request failed status={response.status_code}: {response.text[:400]}")
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            results = []
        rows: list[WebSearchResult] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            rows.append(
                WebSearchResult(
                    provider="tavily",
                    title=_clean(item.get("title")),
                    url=_clean(item.get("url")),
                    snippet=_clean(item.get("content")),
                    source=_source_label(item.get("source") or item.get("domain")),
                    published_at=_clean(item.get("published_date")),
                    raw=item,
                )
            )
        return rows


__all__ = [
    "SerpAPIConfig",
    "SerpAPISearchClient",
    "TavilyConfig",
    "TavilySearchClient",
    "WebResearchError",
    "WebSearchResult",
    "load_serpapi_config",
    "load_tavily_config",
]
