from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlparse

import requests

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


class PageBrowsingError(RuntimeError):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim(text: object, *, limit: int) -> str:
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


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
) -> dict[str, Any]:
    normalized_url = _normalized_url(url)
    safe_timeout = max(int(timeout_seconds), 1)
    safe_max_chars = max(int(max_text_chars), 500)
    fallback_warning = ""

    if prefer_browser and is_seeking_alpha_url(normalized_url):
        try:
            return _run_async(
                browse_seeking_alpha_page(
                    normalized_url,
                    timeout_seconds=safe_timeout,
                    max_text_chars=safe_max_chars,
                )
            )
        except Exception as exc:
            fallback_warning = _trim(str(exc), limit=220)

    if prefer_browser:
        try:
            return _run_async(
                _browse_with_playwright(
                    normalized_url,
                    timeout_seconds=safe_timeout,
                    max_text_chars=safe_max_chars,
                )
            )
        except Exception as exc:
            fallback_warning = _trim(str(exc), limit=220)

    return _browse_with_http(
        normalized_url,
        timeout_seconds=safe_timeout,
        max_text_chars=safe_max_chars,
        warning=fallback_warning,
    )


__all__ = [
    "DEFAULT_MAX_TEXT_CHARS",
    "DEFAULT_PAGE_TIMEOUT_SECONDS",
    "PageBrowsingError",
    "browse_page",
]
