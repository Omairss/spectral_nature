from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

from .secrets import resolve_secret_value
from .seeking_alpha_access import browse_seeking_alpha_page, is_seeking_alpha_url


DEFAULT_PAGE_TIMEOUT_SECONDS = 12
DEFAULT_MAX_TEXT_CHARS = 5000
DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_PLAYWRIGHT_IMPORT_ERROR: Exception | None = None

try:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception as exc:  # pragma: no cover - optional runtime dependency
    PlaywrightError = RuntimeError  # type: ignore[assignment]
    PlaywrightTimeoutError = RuntimeError  # type: ignore[assignment]
    async_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc

_SCRAPLING_IMPORT_ERROR: Exception | None = None

try:
    from scrapling.fetchers import StealthySession
except Exception as exc:  # pragma: no cover - optional runtime dependency
    StealthySession = None
    _SCRAPLING_IMPORT_ERROR = exc


class PageBrowsingError(RuntimeError):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim(text: object, *, limit: int) -> str:
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _env_flag(name: str, *, default: bool) -> bool:
    raw = _clean(os.getenv(name)).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.getenv(name) or default)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _normalized_url(url: str) -> str:
    clean = _clean(url)
    if not clean:
        raise PageBrowsingError("Page URL cannot be empty.")
    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return clean
    raise PageBrowsingError("Page URL must be an absolute http or https URL.")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignore_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self.description = ""
        self.og_title = ""
        self.og_description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower().strip()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if normalized == "title":
            self._in_title = True
            return
        if normalized == "meta":
            attrs_map = {str(key).lower(): str(value or "") for key, value in attrs}
            name = attrs_map.get("name", "").lower()
            prop = attrs_map.get("property", "").lower()
            content = _clean(attrs_map.get("content"))
            if name == "description" and content and not self.description:
                self.description = content
            elif prop == "og:title" and content and not self.og_title:
                self.og_title = content
            elif prop == "og:description" and content and not self.og_description:
                self.og_description = content

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower().strip()
        if normalized in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
            return
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        clean = _clean(data)
        if not clean or self._ignore_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(clean)
        else:
            self._text_parts.append(clean)

    @property
    def title(self) -> str:
        return _clean(" ".join(self._title_parts)) or self.og_title

    @property
    def text(self) -> str:
        return _clean(" ".join(self._text_parts))


def _parse_html_document(html: str) -> dict[str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return {
        "title": parser.title,
        "description": parser.description or parser.og_description,
        "text": parser.text,
    }


def _build_excerpt(title: str, description: str, text: str) -> str:
    for candidate in [description, text]:
        clean = _clean(candidate)
        if clean and clean.lower() != title.lower():
            return _trim(clean, limit=320)
    return _trim(text, limit=320)


_BLOCKED_PAGE_MARKERS: tuple[str, ...] = (
    "access to this page has been denied",
    "are you a human",
    "captcha",
    "cf-challenge",
    "checking if the site connection is secure",
    "cloudflare ray id",
    "enable javascript",
    "just a moment",
    "press & hold",
    "px-captcha",
    "request originates from an undeclared automated tool",
    "security check",
    "unusual traffic",
    "verify you are human",
    "why am i seeing this page",
)

_NAVIGATION_DOMINATED_MARKERS: tuple[str, ...] = (
    "accept all cookies",
    "contact careers",
    "home about",
    "ir overview",
    "manage cookie preferences",
    "privacy policy",
    "skip to footer",
    "skip to main content",
    "skip to section navigation",
    "terms of use",
)

_SEEKING_ALPHA_PREVIEW_GATE_MARKERS: tuple[str, ...] = (
    "invest smarter in volatile markets",
    "make informed decisions with the data, context, and analysis",
    "unlimited access to breaking stock news",
    "continue with google",
    "continue with email",
)


def page_quality_issue(payload: dict[str, Any] | None, *, min_text_chars: int = 500) -> str:
    if not isinstance(payload, dict):
        return "empty_payload"
    text = _clean(payload.get("text"))
    title = _clean(payload.get("title"))
    excerpt = _clean(payload.get("excerpt"))
    url = _clean(payload.get("url"))
    haystack = f"{title} {excerpt} {text[:1600]}".lower()
    for marker in _BLOCKED_PAGE_MARKERS:
        if marker in haystack:
            return f"blocked:{marker}"
    if is_seeking_alpha_url(url) and len(text) < 3000:
        preview_hits = sum(1 for marker in _SEEKING_ALPHA_PREVIEW_GATE_MARKERS if marker in haystack)
        if preview_hits >= 2:
            return "blocked:seeking_alpha_preview_gate"
    opening_text = text[:1800].lower()
    navigation_hits = sum(1 for marker in _NAVIGATION_DOMINATED_MARKERS if marker in opening_text)
    if navigation_hits >= 3 and len(text) < max(int(min_text_chars), 1) * 6:
        return "navigation_dominated"
    if len(text) < max(int(min_text_chars), 1):
        return f"shallow:{len(text)}<{max(int(min_text_chars), 1)}"
    return ""


async def _extract_readable_text_blocks(page: Any, *, max_blocks: int = 90) -> list[str]:
    try:
        blocks = await page.evaluate(
            """
            ({ maxBlocks }) => {
              const roots = [
                document.querySelector('article'),
                document.querySelector('main'),
                document.querySelector('[role="main"]'),
                document.body
              ].filter(Boolean);
              const root = roots[0];
              const selectors = ['h1', 'h2', 'h3', 'p', 'li', 'blockquote', 'td', 'th'];
              const reject = /cookie|privacy|terms of use|skip to|navigation|subscribe|sign in|accept all|browser vulnerability/i;
              const isVisible = (node) => {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              const output = [];
              for (const node of root.querySelectorAll(selectors.join(','))) {
                if (!isVisible(node)) continue;
                const tag = node.tagName.toLowerCase();
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                const minLength = tag.startsWith('h') ? 8 : 35;
                if (!text || text.length < minLength || reject.test(text)) continue;
                output.push(text);
                if (output.length >= maxBlocks) break;
              }
              return output;
            }
            """,
            {"maxBlocks": max_blocks},
        )
    except Exception:
        blocks = []
    return [_clean(item) for item in list(blocks or []) if _clean(item)]


async def _browse_with_playwright(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
) -> dict[str, Any]:
    if async_playwright is None:
        raise PageBrowsingError(
            "Playwright is not installed in this runtime."
        ) from _PLAYWRIGHT_IMPORT_ERROR

    timeout_ms = max(int(timeout_seconds), 1) * 1000
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=DEFAULT_BROWSER_USER_AGENT)
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 2500))
                except Exception:
                    pass
                title = _clean(await page.title())
                final_url = _clean(page.url)
                description = ""
                try:
                    description = _clean(await page.locator("meta[name='description']").first.get_attribute("content"))
                except Exception:
                    description = ""
                blocks = await _extract_readable_text_blocks(page)
                text = _clean("\n".join(blocks))
                if not text:
                    text = _clean(await page.locator("body").inner_text(timeout=min(timeout_ms, 2000)))
            finally:
                await browser.close()
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        raise PageBrowsingError(f"Playwright page browse failed: {type(exc).__name__}: {exc}") from exc

    text = _trim(text, limit=max_text_chars)
    return {
        "url": url,
        "final_url": final_url or url,
        "title": title or final_url or url,
        "description": description,
        "excerpt": _build_excerpt(title, description, text),
        "text": text,
        "mode": "playwright",
        "warning": "",
    }


def _first_selector(response: Any, selector: str) -> Any | None:
    try:
        matches = response.css(selector)
    except Exception:
        matches = []
    return matches[0] if matches else None


def _selector_text(response: Any, selector: str) -> str:
    node = _first_selector(response, selector)
    if node is None:
        return ""
    return _clean(getattr(node, "text", ""))


def _selector_attr(response: Any, selector: str, name: str) -> str:
    node = _first_selector(response, selector)
    if node is None:
        return ""
    try:
        return _clean(node.attrib.get(name))
    except Exception:
        return ""


def _scrapling_response_text(response: Any) -> str:
    for selector, minimum_length in (("article", 200), ("main", 200), ("body", 1)):
        node = _first_selector(response, selector)
        if node is None:
            continue
        try:
            text = _clean(node.get_all_text(strip=True))
        except Exception:
            text = ""
        if len(text) >= minimum_length:
            return text
    try:
        return _clean(response.get_all_text(strip=True))
    except Exception:
        return ""


def _browse_with_scrapling(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
    warning: str = "",
) -> dict[str, Any]:
    if StealthySession is None:
        raise PageBrowsingError("Scrapling fetchers are not installed in this runtime.") from _SCRAPLING_IMPORT_ERROR

    timeout_ms = max(int(timeout_seconds), 1) * 1000
    wait_ms = _env_int("PAGE_BROWSING_SCRAPLING_WAIT_MS", 1500, minimum=0, maximum=15000)
    with StealthySession(
        headless=_env_flag("PAGE_BROWSING_SCRAPLING_HEADLESS", default=True),
        timeout=timeout_ms,
        wait=wait_ms,
        solve_cloudflare=True,
    ) as session:
        response = session.fetch(url)

    status = int(getattr(response, "status", 0) or 0)
    if status >= 400:
        raise PageBrowsingError(f"Scrapling page browse failed status={status}.")

    final_url = _clean(getattr(response, "url", "")) or url
    title = _selector_text(response, "title")
    description = _selector_attr(response, "meta[name='description']", "content") or _selector_attr(
        response,
        "meta[property='og:description']",
        "content",
    )
    text = _trim(_scrapling_response_text(response), limit=max(max_text_chars, 500))
    if not text:
        raise PageBrowsingError("Scrapling captured no visible page text.")
    return {
        "url": url,
        "final_url": final_url,
        "title": title or final_url or url,
        "description": description,
        "excerpt": _build_excerpt(title, description, text),
        "text": text,
        "mode": "scrapling",
        "warning": _clean(warning),
    }


def _firecrawl_api_key() -> str:
    return resolve_secret_value(
        ["FIRECRAWL_API_KEY", "FIRECRAWL_KEY"],
        secret_name_env="FIRECRAWL_API_KEY_SECRET_NAME",
        default_secret_name="firecrawl-api-key",
    )


def _browse_with_firecrawl(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
    warning: str = "",
) -> dict[str, Any]:
    api_key = _firecrawl_api_key()
    if not api_key:
        raise PageBrowsingError("Firecrawl API key is not configured.")

    endpoint = _clean(os.getenv("FIRECRAWL_API_URL")) or "https://api.firecrawl.dev/v2/scrape"
    timeout_ms = min(max(int(timeout_seconds), 1) * 1000, 300000)
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "onlyCleanContent": _env_flag("FIRECRAWL_ONLY_CLEAN_CONTENT", default=True),
        "timeout": timeout_ms,
        "removeBase64Images": True,
        "blockAds": True,
        "proxy": _clean(os.getenv("FIRECRAWL_PROXY")) or "auto",
        "location": {"country": _clean(os.getenv("FIRECRAWL_LOCATION_COUNTRY")) or "US", "languages": ["en-US"]},
    }
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=max(int(timeout_seconds), 1),
        )
    except requests.RequestException as exc:
        raise PageBrowsingError(f"Firecrawl page browse failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise PageBrowsingError(f"Firecrawl page browse failed status={response.status_code}: {response.text[:240]}")

    try:
        body = response.json()
    except Exception as exc:
        raise PageBrowsingError("Firecrawl returned non-JSON content.") from exc
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if not isinstance(data, dict):
        raise PageBrowsingError("Firecrawl returned no data object.")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    text = _clean(data.get("markdown") or data.get("summary") or data.get("html") or data.get("rawHtml"))
    if not text:
        raise PageBrowsingError("Firecrawl returned no markdown or text content.")
    final_url = _clean(metadata.get("sourceURL") or metadata.get("url") or url)
    title = _clean(metadata.get("title")) or final_url or url
    description = _clean(metadata.get("description"))
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "description": description,
        "excerpt": _build_excerpt(title, description, text),
        "text": _trim(text, limit=max(max_text_chars, 500)),
        "mode": "firecrawl",
        "warning": _clean(warning or data.get("warning")),
    }


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _browse_with_http(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
    warning: str = "",
) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            timeout=max(int(timeout_seconds), 1),
            headers={"User-Agent": DEFAULT_BROWSER_USER_AGENT},
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise PageBrowsingError(f"HTTP page browse failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        raise PageBrowsingError(f"HTTP page browse failed status={response.status_code}.")

    content_type = _clean(response.headers.get("Content-Type"))
    raw_text = response.text
    title = ""
    description = ""
    text = _clean(raw_text)
    if "html" in content_type.lower() or "<html" in raw_text.lower():
        parsed = _parse_html_document(raw_text)
        title = parsed["title"]
        description = parsed["description"]
        text = parsed["text"] or text

    text = _trim(text, limit=max_text_chars)
    final_url = _clean(response.url) or url
    return {
        "url": url,
        "final_url": final_url,
        "title": title or final_url or url,
        "description": description,
        "excerpt": _build_excerpt(title, description, text),
        "text": text,
        "mode": "http",
        "warning": _clean(warning),
    }


def browse_page(
    url: str,
    *,
    prefer_browser: bool = True,
    timeout_seconds: int = DEFAULT_PAGE_TIMEOUT_SECONDS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    require_main_content: bool = False,
    min_text_chars: int = 500,
) -> dict[str, Any]:
    normalized_url = _normalized_url(url)
    safe_timeout = max(int(timeout_seconds), 1)
    safe_max_chars = max(int(max_text_chars), 500)
    safe_min_text_chars = max(int(min_text_chars), 1)
    fallback_warning = ""
    best_payload: dict[str, Any] | None = None
    best_issue = "empty_payload"

    def _remember(payload: dict[str, Any]) -> str:
        nonlocal best_payload, best_issue
        issue = page_quality_issue(payload, min_text_chars=safe_min_text_chars)
        if best_payload is None or len(_clean(payload.get("text"))) > len(_clean(best_payload.get("text"))):
            best_payload = payload
            best_issue = issue
        return issue

    def _warning_with(existing: str, extra: str) -> str:
        parts = [part for part in [_clean(existing), _clean(extra)] if part]
        return " ".join(parts)

    if prefer_browser and is_seeking_alpha_url(normalized_url):
        try:
            payload = _run_async(
                browse_seeking_alpha_page(
                    normalized_url,
                    timeout_seconds=safe_timeout,
                    max_text_chars=safe_max_chars,
                )
            )
            if not require_main_content or not _remember(payload):
                return payload
            fallback_warning = _warning_with(fallback_warning, f"Seeking Alpha browse quality issue: {best_issue}.")
        except Exception as exc:
            fallback_warning = _trim(str(exc), limit=220)

    if prefer_browser:
        try:
            payload = _run_async(
                _browse_with_playwright(
                    normalized_url,
                    timeout_seconds=safe_timeout,
                    max_text_chars=safe_max_chars,
                )
            )
            if not require_main_content or not _remember(payload):
                return payload
            fallback_warning = _warning_with(fallback_warning, f"Playwright quality issue: {best_issue}.")
        except Exception as exc:
            fallback_warning = _warning_with(fallback_warning, _trim(str(exc), limit=220))

    try:
        payload = _browse_with_http(
            normalized_url,
            timeout_seconds=safe_timeout,
            max_text_chars=safe_max_chars,
            warning=fallback_warning,
        )
        if not require_main_content or not _remember(payload):
            return payload
        fallback_warning = _warning_with(fallback_warning, f"HTTP quality issue: {best_issue}.")
    except Exception as exc:
        fallback_warning = _warning_with(fallback_warning, _trim(str(exc), limit=220))

    if require_main_content:
        try:
            payload = _browse_with_scrapling(
                normalized_url,
                timeout_seconds=safe_timeout,
                max_text_chars=safe_max_chars,
                warning=fallback_warning,
            )
            if not _remember(payload):
                return payload
            fallback_warning = _warning_with(fallback_warning, f"Scrapling quality issue: {best_issue}.")
        except Exception as exc:
            fallback_warning = _warning_with(fallback_warning, _trim(str(exc), limit=220))

        try:
            payload = _browse_with_firecrawl(
                normalized_url,
                timeout_seconds=safe_timeout,
                max_text_chars=safe_max_chars,
                warning=fallback_warning,
            )
            if not _remember(payload):
                return payload
            fallback_warning = _warning_with(fallback_warning, f"Firecrawl quality issue: {best_issue}.")
        except Exception as exc:
            fallback_warning = _warning_with(fallback_warning, _trim(str(exc), limit=220))

    if best_payload is not None:
        best_payload = dict(best_payload)
        best_payload["warning"] = _warning_with(best_payload.get("warning"), fallback_warning)
        best_payload["quality_issue"] = best_issue
        return best_payload

    raise PageBrowsingError(f"Page browse failed for all methods. {fallback_warning}".strip())


__all__ = [
    "DEFAULT_MAX_TEXT_CHARS",
    "DEFAULT_PAGE_TIMEOUT_SECONDS",
    "PageBrowsingError",
    "browse_page",
    "page_quality_issue",
]
