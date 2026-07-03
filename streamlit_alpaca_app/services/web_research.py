from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import time
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


def search_client_provider(client: Any | None) -> str:
    if client is None:
        return ""
    name = type(client).__name__.lower()
    if "serper" in name:
        return "serper"
    if "serpapi" in name or "serp" in name:
        return "serpapi"
    if "tavily" in name:
        return "tavily"
    config_provider = _clean(getattr(getattr(client, "config", None), "provider", ""))
    return config_provider.lower() or name or "search"


def _timeout_seconds() -> int:
    raw = _clean(os.getenv("WEB_RESEARCH_TIMEOUT_SECONDS")) or "20"
    try:
        return max(int(raw), 5)
    except Exception:
        return 20


def _connector_telemetry_requested() -> bool:
    raw = _clean(os.getenv("CONNECTOR_TELEMETRY_ENABLED"))
    if raw:
        return raw.lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(_clean(os.getenv("PIPELINE_JOB_NAME")) or _clean(os.getenv("APP_TRACK")))


def _query_digest(query: object) -> str:
    text = _clean(query)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _record_connector_call(
    *,
    provider: str,
    operation: str,
    started_at_utc: datetime,
    started_monotonic: float,
    status: str,
    http_status: int | None = None,
    result_count: int | None = None,
    error: BaseException | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not _connector_telemetry_requested():
        return
    error_summary = _clean(error)
    try:
        from .pipeline_store import record_connector_call

        record_connector_call(
            provider=provider,
            operation=operation,
            status=status,
            started_at_utc=started_at_utc,
            duration_ms=round((time.monotonic() - started_monotonic) * 1000.0, 1),
            http_status=http_status,
            result_count=result_count,
            error_type=type(error).__name__ if isinstance(error, BaseException) else "",
            error_summary=error_summary,
            metadata=metadata or {},
        )
    except Exception:
        return


@dataclass(frozen=True)
class WebSearchResult:
    provider: str
    title: str
    url: str
    snippet: str = ""
    raw_text: str = ""
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


@dataclass(frozen=True)
class SerperConfig:
    api_key: str
    timeout_seconds: int = 20
    gl: str = "us"
    hl: str = "en"


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


def _resolve_serper_api_key() -> str:
    return resolve_secret_value(
        ["SERPER_API_KEY", "SERPER_KEY"],
        secret_name_env="SERPER_API_KEY_SECRET_NAME",
        default_secret_name="serper-api-key",
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
    include_raw_content_raw = _clean(os.getenv("TAVILY_INCLUDE_RAW_CONTENT"))
    return TavilyConfig(
        api_key=api_key,
        timeout_seconds=_timeout_seconds(),
        topic=_clean(os.getenv("TAVILY_TOPIC")) or "news",
        search_depth=_clean(os.getenv("TAVILY_SEARCH_DEPTH")) or "advanced",
        include_answer=(_clean(os.getenv("TAVILY_INCLUDE_ANSWER")) or "").lower() in {"1", "true", "yes", "on"},
        include_raw_content=True if not include_raw_content_raw else include_raw_content_raw.lower() in {"1", "true", "yes", "on"},
    )


def load_serper_config() -> SerperConfig | None:
    api_key = _resolve_serper_api_key()
    if not api_key:
        return None
    return SerperConfig(
        api_key=api_key,
        timeout_seconds=_timeout_seconds(),
        gl=_clean(os.getenv("SERPER_GL")) or "us",
        hl=_clean(os.getenv("SERPER_HL")) or "en",
    )


class SerpAPISearchClient:
    def __init__(self, config: SerpAPIConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise WebResearchError("Missing SerpApi key.")
        self.config = config
        self.session = session or requests.Session()

    def search(self, query: str, *, news: bool = False, num: int = 10) -> list[WebSearchResult]:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        operation = "search_news" if news else "search"
        metadata = {
            "query_sha256": _query_digest(query),
            "query_chars": len(_clean(query)),
            "engine": "google_news" if news else self.config.engine,
            "num": max(int(num), 1),
        }
        recorded = False
        engine = "google_news" if news else self.config.engine
        try:
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
                message = f"SerpApi request failed status={response.status_code}: {response.text[:400]}"
                _record_connector_call(
                    provider="serpapi",
                    operation=operation,
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    http_status=response.status_code,
                    error=message,
                    metadata=metadata,
                )
                recorded = True
                raise WebResearchError(message)
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
                        raw_text=_clean(item.get("snippet") or item.get("summary")),
                        source=_source_label(item.get("source")),
                        published_at=_clean(item.get("date")),
                        raw=item,
                    )
                )
            _record_connector_call(
                provider="serpapi",
                operation=operation,
                started_at_utc=started_at,
                started_monotonic=started_monotonic,
                status="success",
                http_status=response.status_code,
                result_count=len(rows),
                metadata=metadata,
            )
            return rows
        except Exception as exc:
            if not recorded:
                _record_connector_call(
                    provider="serpapi",
                    operation=operation,
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    error=exc,
                    metadata=metadata,
                )
            raise

    def search_ai_overview(self, query: str) -> WebSearchResult | None:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        metadata = {
            "query_sha256": _query_digest(query),
            "query_chars": len(_clean(query)),
            "engine": self.config.engine,
            "num": 5,
        }
        recorded = False
        try:
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
                message = f"SerpApi request failed status={response.status_code}: {response.text[:400]}"
                _record_connector_call(
                    provider="serpapi",
                    operation="ai_overview",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    http_status=response.status_code,
                    error=message,
                    metadata=metadata,
                )
                recorded = True
                raise WebResearchError(message)
            payload = response.json()
            overview = payload.get("ai_overview")
            if not isinstance(overview, dict):
                overview = payload.get("ai_overview_result")
            if not isinstance(overview, dict):
                _record_connector_call(
                    provider="serpapi",
                    operation="ai_overview",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="success",
                    http_status=response.status_code,
                    result_count=0,
                    metadata=metadata,
                )
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
                _record_connector_call(
                    provider="serpapi",
                    operation="ai_overview",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="success",
                    http_status=response.status_code,
                    result_count=0,
                    metadata=metadata,
                )
                return None
            _record_connector_call(
                provider="serpapi",
                operation="ai_overview",
                started_at_utc=started_at,
                started_monotonic=started_monotonic,
                status="success",
                http_status=response.status_code,
                result_count=1,
                metadata=metadata,
            )
            return WebSearchResult(
                provider="serpapi_ai_overview",
                title=title,
                url=source_url,
                snippet=snippet,
                raw_text=snippet,
                source="Google AI Overview",
                published_at="",
                raw=overview,
            )
        except Exception as exc:
            if not recorded:
                _record_connector_call(
                    provider="serpapi",
                    operation="ai_overview",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    error=exc,
                    metadata=metadata,
                )
            raise


class SerperSearchClient:
    def __init__(self, config: SerperConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise WebResearchError("Missing Serper key.")
        self.config = config
        self.session = session or requests.Session()

    def search(self, query: str, *, news: bool = False, num: int = 10) -> list[WebSearchResult]:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        operation = "search_news" if news else "search"
        metadata = {
            "query_sha256": _query_digest(query),
            "query_chars": len(_clean(query)),
            "engine": "news" if news else "search",
            "num": max(int(num), 1),
        }
        recorded = False
        endpoint = "news" if news else "search"
        try:
            response = self.session.post(
                f"https://google.serper.dev/{endpoint}",
                headers={
                    "X-API-KEY": self.config.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "q": query,
                    "num": max(int(num), 1),
                    "gl": self.config.gl,
                    "hl": self.config.hl,
                },
                timeout=self.config.timeout_seconds,
            )
            if response.status_code != 200:
                message = f"Serper request failed status={response.status_code}: {response.text[:400]}"
                _record_connector_call(
                    provider="serper",
                    operation=operation,
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    http_status=response.status_code,
                    error=message,
                    metadata=metadata,
                )
                recorded = True
                raise WebResearchError(message)
            payload = response.json()
            raw_items = payload.get("news") if news else payload.get("organic")
            if not isinstance(raw_items, list):
                raw_items = []
            rows: list[WebSearchResult] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    WebSearchResult(
                        provider="serper",
                        title=_clean(item.get("title")),
                        url=_clean(item.get("link")),
                        snippet=_clean(item.get("snippet")),
                        raw_text=_clean(item.get("snippet")),
                        source=_source_label(item.get("source")),
                        published_at=_clean(item.get("date")),
                        raw=item,
                    )
                )
            _record_connector_call(
                provider="serper",
                operation=operation,
                started_at_utc=started_at,
                started_monotonic=started_monotonic,
                status="success",
                http_status=response.status_code,
                result_count=len(rows),
                metadata=metadata,
            )
            return rows
        except Exception as exc:
            if not recorded:
                _record_connector_call(
                    provider="serper",
                    operation=operation,
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    error=exc,
                    metadata=metadata,
                )
            raise


class TavilySearchClient:
    def __init__(self, config: TavilyConfig, *, session: requests.Session | None = None) -> None:
        if not config.api_key:
            raise WebResearchError("Missing Tavily key.")
        self.config = config
        self.session = session or requests.Session()

    def search(self, query: str, *, max_results: int = 10, topic: str | None = None) -> list[WebSearchResult]:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        metadata = {
            "query_sha256": _query_digest(query),
            "query_chars": len(_clean(query)),
            "topic": topic or self.config.topic,
            "max_results": max(int(max_results), 1),
            "search_depth": self.config.search_depth,
        }
        recorded = False
        try:
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
                message = f"Tavily request failed status={response.status_code}: {response.text[:400]}"
                _record_connector_call(
                    provider="tavily",
                    operation="search",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    http_status=response.status_code,
                    error=message,
                    metadata=metadata,
                )
                recorded = True
                raise WebResearchError(message)
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
                        raw_text=_clean(item.get("raw_content") or item.get("content")),
                        source=_source_label(item.get("source") or item.get("domain")),
                        published_at=_clean(item.get("published_date")),
                        raw=item,
                    )
                )
            _record_connector_call(
                provider="tavily",
                operation="search",
                started_at_utc=started_at,
                started_monotonic=started_monotonic,
                status="success",
                http_status=response.status_code,
                result_count=len(rows),
                metadata=metadata,
            )
            return rows
        except Exception as exc:
            if not recorded:
                _record_connector_call(
                    provider="tavily",
                    operation="search",
                    started_at_utc=started_at,
                    started_monotonic=started_monotonic,
                    status="failure",
                    error=exc,
                    metadata=metadata,
                )
            raise


__all__ = [
    "SerperConfig",
    "SerperSearchClient",
    "SerpAPIConfig",
    "SerpAPISearchClient",
    "TavilyConfig",
    "TavilySearchClient",
    "WebResearchError",
    "WebSearchResult",
    "load_serper_config",
    "load_serpapi_config",
    "load_tavily_config",
    "search_client_provider",
]
