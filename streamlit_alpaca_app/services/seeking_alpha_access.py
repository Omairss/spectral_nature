from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .secrets import get_secret_value_from_vault

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Playwright


DEFAULT_SEEKING_ALPHA_USERNAME_SECRET_NAME = "seeking-alpha-username"
DEFAULT_SEEKING_ALPHA_PASSWORD_SECRET_NAME = "seeking-alpha-password"
DEFAULT_SEEKING_ALPHA_LOGIN_URL = "https://seekingalpha.com/account/login"
DEFAULT_SEEKING_ALPHA_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_SEEKING_ALPHA_WAIT_MS = 2_000

_ACCESS_DENIED_MARKERS = (
    "access to this page has been denied",
    "press & hold to confirm you are a human",
    "why am i seeing this page",
)
_PREVIEW_GATED_MARKERS = (
    "create a free account to read the full article",
    "create a free account to read full article",
    "already a subscriber? sign in",
    "already a subscriber? log in",
    "this article is for subscribers only",
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


class SeekingAlphaAccessError(RuntimeError):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim(text: object, *, limit: int) -> str:
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _env_flag(name: str, *, default: bool) -> bool:
    value = _clean(os.getenv(name)).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.getenv(name) or default)
    except Exception:
        parsed = int(default)
    return min(max(parsed, minimum), maximum)


def is_seeking_alpha_url(url: str) -> bool:
    parsed = urlparse(_clean(url))
    host = parsed.netloc.lower().strip(".")
    return host == "seekingalpha.com" or host.endswith(".seekingalpha.com")


def _profile_dir() -> Path:
    explicit = _clean(os.getenv("SEEKING_ALPHA_BROWSER_PROFILE_DIR"))
    if explicit:
        return Path(explicit).expanduser()
    return Path(tempfile.gettempdir()) / "spectral-seeking-alpha-browser"


def _secret_name(env_name: str, default: str) -> str:
    return _clean(os.getenv(env_name)) or default


def load_seeking_alpha_credentials(
    *,
    vault_name: str = "",
    vault_url: str = "",
) -> dict[str, str]:
    username = _clean(os.getenv("SEEKING_ALPHA_USERNAME"))
    password = _clean(os.getenv("SEEKING_ALPHA_PASSWORD"))
    username_secret_name = _secret_name(
        "SEEKING_ALPHA_USERNAME_SECRET_NAME",
        DEFAULT_SEEKING_ALPHA_USERNAME_SECRET_NAME,
    )
    password_secret_name = _secret_name(
        "SEEKING_ALPHA_PASSWORD_SECRET_NAME",
        DEFAULT_SEEKING_ALPHA_PASSWORD_SECRET_NAME,
    )

    if not username:
        username = get_secret_value_from_vault(
            username_secret_name,
            vault_name=vault_name,
            vault_url=vault_url,
        )
    if not password:
        password = get_secret_value_from_vault(
            password_secret_name,
            vault_name=vault_name,
            vault_url=vault_url,
        )

    return {
        "username": username,
        "password": password,
        "username_secret_name": username_secret_name,
        "password_secret_name": password_secret_name,
    }


def _require_scrapling() -> None:
    if StealthySession is None:
        raise SeekingAlphaAccessError(
            "Scrapling fetchers are not installed in this runtime."
        ) from _SCRAPLING_IMPORT_ERROR


def _require_playwright() -> None:
    if async_playwright is None:
        raise SeekingAlphaAccessError(
            "Playwright is not installed in this runtime."
        ) from _PLAYWRIGHT_IMPORT_ERROR


def _visible_locator_sync(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() <= 0:
                continue
            if locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def _fill_first_sync(page: Any, selectors: list[str], value: str) -> bool:
    locator = _visible_locator_sync(page, selectors)
    if locator is None:
        return False
    locator.fill(value)
    return True


def _click_first_sync(page: Any, selectors: list[str]) -> bool:
    locator = _visible_locator_sync(page, selectors)
    if locator is None:
        return False
    locator.click()
    return True


def _page_text_sync(page: Any, *, limit: int = 2_000) -> str:
    try:
        text = _clean(page.locator("body").inner_text())
    except Exception:
        text = ""
    return _trim(text, limit=limit)


def _looks_logged_in_sync(page: Any) -> bool:
    current_url = _clean(getattr(page, "url", "")).lower()
    if "/account/login" in current_url:
        return False
    for selectors in (
        [
            "input[type='email']",
            "input[name='email']",
            "input[name*='email']",
            "input[autocomplete='username']",
        ],
        [
            "input[type='password']",
            "input[autocomplete='current-password']",
        ],
    ):
        if _visible_locator_sync(page, selectors) is not None:
            return False
    return True


def _dismiss_cookie_banner_sync(page: Any) -> None:
    for selector in [
        "button:has-text('Accept')",
        "button:has-text('I Agree')",
        "button:has-text('Agree')",
        "button:has-text('Got it')",
    ]:
        try:
            locator = page.locator(selector).first
            if locator.count() <= 0 or not locator.is_visible():
                continue
            locator.click()
            page.wait_for_timeout(300)
            return
        except Exception:
            continue


def _login_with_scrapling_session(
    session: Any,
    *,
    username: str,
    password: str,
    timeout_ms: int,
) -> None:
    if not username or not password:
        raise SeekingAlphaAccessError("Seeking Alpha credentials are required for authenticated page access.")

    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[name*='email']",
        "input[autocomplete='username']",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[name*='password']",
        "input[autocomplete='current-password']",
    ]
    continue_selectors = [
        "button:has-text('Continue')",
        "button:has-text('Next')",
    ]
    submit_selectors = [
        "button[type='submit']",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "input[type='submit']",
    ]

    def _login_action(page: Any) -> None:
        page.wait_for_timeout(300)
        _dismiss_cookie_banner_sync(page)

        if _looks_logged_in_sync(page):
            return

        current_url = _clean(getattr(page, "url", "")).lower()
        page_text = _page_text_sync(page)
        if "/account/login" in current_url and _contains_marker(current_url, page_text, markers=_ACCESS_DENIED_MARKERS):
            raise SeekingAlphaAccessError("Seeking Alpha login page is blocked by anti-bot protections.")

        if not _fill_first_sync(page, email_selectors, username):
            raise SeekingAlphaAccessError("Could not find the Seeking Alpha email field.")

        page.wait_for_timeout(150)
        if _visible_locator_sync(page, password_selectors) is None:
            _click_first_sync(page, continue_selectors)
            page.wait_for_timeout(500)

        if not _fill_first_sync(page, password_selectors, password):
            raise SeekingAlphaAccessError("Could not find the Seeking Alpha password field.")

        page.wait_for_timeout(150)
        if not _click_first_sync(page, submit_selectors):
            raise SeekingAlphaAccessError("Could not find the Seeking Alpha sign-in button.")

        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 4_000))
        except Exception:
            pass
        page.wait_for_timeout(800)

    session.fetch(DEFAULT_SEEKING_ALPHA_LOGIN_URL, page_action=_login_action)


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


def _response_text(response: Any) -> str:
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


def _contains_marker(*parts: object, markers: tuple[str, ...]) -> bool:
    haystack = _clean(" ".join(str(part or "") for part in parts)).lower()
    return any(marker in haystack for marker in markers)


def _build_scrapling_payload(
    response: Any,
    *,
    url: str,
    max_text_chars: int,
    warning: str = "",
) -> dict[str, Any]:
    status = int(getattr(response, "status", 0) or 0)
    if status >= 400:
        raise SeekingAlphaAccessError(f"Seeking Alpha fetch returned status={status}.")

    final_url = _clean(getattr(response, "url", "")) or url
    title = _selector_text(response, "title")
    description = _selector_attr(response, "meta[name='description']", "content") or _selector_attr(
        response,
        "meta[property='og:description']",
        "content",
    )
    text = _trim(_response_text(response), limit=max(max_text_chars, 500))
    excerpt = _trim(description or text, limit=320)

    if not text:
        raise SeekingAlphaAccessError("No visible page text was captured from Seeking Alpha.")

    return {
        "url": url,
        "final_url": final_url,
        "title": title or final_url or url,
        "description": description,
        "excerpt": excerpt,
        "text": text,
        "mode": "seeking_alpha_authenticated",
        "warning": _clean(warning),
        "backend": "scrapling",
    }


def _browse_seeking_alpha_page_with_scrapling(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
) -> dict[str, Any]:
    if not is_seeking_alpha_url(url):
        raise SeekingAlphaAccessError("URL is not a Seeking Alpha page.")

    _require_scrapling()

    timeout_ms = max(int(timeout_seconds), 1) * 1000
    wait_ms = _env_int(
        "SEEKING_ALPHA_BROWSER_WAIT_MS",
        DEFAULT_SEEKING_ALPHA_WAIT_MS,
        minimum=0,
        maximum=15_000,
    )
    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    credentials = load_seeking_alpha_credentials()
    warning = ""

    with StealthySession(
        headless=_env_flag("SEEKING_ALPHA_BROWSER_HEADLESS", default=True),
        timeout=timeout_ms,
        wait=wait_ms,
        solve_cloudflare=True,
        user_data_dir=str(profile_dir),
    ) as session:
        if not (credentials["username"] and credentials["password"]):
            warning = "Seeking Alpha credentials were not found. Attempted public page access only."

        response = session.fetch(url)
        response_url = _clean(getattr(response, "url", "")).lower()
        response_status = int(getattr(response, "status", 0) or 0)
        response_text = _response_text(response)

        if "/account/login" in response_url:
            if not (credentials["username"] and credentials["password"]):
                raise SeekingAlphaAccessError("Seeking Alpha redirected to login and no credentials were available.")
            if response_status >= 400 or _contains_marker(response_url, response_text, markers=_ACCESS_DENIED_MARKERS):
                raise SeekingAlphaAccessError("Seeking Alpha redirected to a blocked login page.")
            _login_with_scrapling_session(
                session,
                username=credentials["username"],
                password=credentials["password"],
                timeout_ms=timeout_ms,
            )
            response = session.fetch(url)

        payload = _build_scrapling_payload(
            response,
            url=url,
            max_text_chars=max_text_chars,
            warning=warning,
        )

        if _contains_marker(payload["title"], payload["text"], markers=_ACCESS_DENIED_MARKERS):
            raise SeekingAlphaAccessError("Seeking Alpha blocked the page with an anti-bot challenge.")

        if _contains_marker(payload["title"], payload["text"], markers=_PREVIEW_GATED_MARKERS):
            if credentials["username"] and credentials["password"]:
                _login_with_scrapling_session(
                    session,
                    username=credentials["username"],
                    password=credentials["password"],
                    timeout_ms=timeout_ms,
                )
                response = session.fetch(url)
                payload = _build_scrapling_payload(
                    response,
                    url=url,
                    max_text_chars=max_text_chars,
                    warning=warning,
                )
            if _contains_marker(payload["title"], payload["text"], markers=_PREVIEW_GATED_MARKERS):
                if credentials["username"] and credentials["password"]:
                    raise SeekingAlphaAccessError("Seeking Alpha page is still preview-gated after login.")
                raise SeekingAlphaAccessError("Seeking Alpha preview content was returned and no credentials were available.")

        return payload


def _append_warning(payload: dict[str, Any], extra: str) -> dict[str, Any]:
    item = dict(payload or {})
    existing = _clean(item.get("warning"))
    extra = _clean(extra)
    if existing and extra:
        item["warning"] = _trim(f"{existing} {extra}", limit=320)
    else:
        item["warning"] = existing or extra
    return item


async def _page_for_context(context: "BrowserContext") -> "Page":
    return context.pages[0] if context.pages else await context.new_page()


async def _visible_locator(page: "Page", selectors: list[str]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() <= 0:
                continue
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _fill_first(page: "Page", selectors: list[str], value: str) -> bool:
    locator = await _visible_locator(page, selectors)
    if locator is None:
        return False
    await locator.fill(value)
    return True


async def _click_first(page: "Page", selectors: list[str]) -> bool:
    locator = await _visible_locator(page, selectors)
    if locator is None:
        return False
    await locator.click()
    return True


async def _page_text(page: "Page", *, limit: int = 2_000) -> str:
    try:
        text = _clean(await page.locator("body").inner_text(timeout=2_000))
    except Exception:
        text = ""
    return _trim(text, limit=limit)


async def _looks_logged_in(page: "Page") -> bool:
    current_url = _clean(page.url).lower()
    if "/account/login" in current_url:
        return False
    for selectors in (
        [
            "input[type='email']",
            "input[name='email']",
            "input[name*='email']",
            "input[autocomplete='username']",
        ],
        [
            "input[type='password']",
            "input[autocomplete='current-password']",
        ],
    ):
        if await _visible_locator(page, selectors) is not None:
            return False
    return True


async def _dismiss_cookie_banner(page: "Page") -> None:
    for selector in [
        "button:has-text('Accept')",
        "button:has-text('I Agree')",
        "button:has-text('Agree')",
        "button:has-text('Got it')",
    ]:
        try:
            locator = page.locator(selector).first
            if await locator.count() <= 0 or not await locator.is_visible():
                continue
            await locator.click()
            await page.wait_for_timeout(300)
            return
        except Exception:
            continue


async def _login(page: "Page", *, username: str, password: str, timeout_ms: int) -> None:
    if not username or not password:
        raise SeekingAlphaAccessError("Seeking Alpha credentials are required for authenticated page access.")

    await page.goto(DEFAULT_SEEKING_ALPHA_LOGIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(300)
    await _dismiss_cookie_banner(page)

    if await _looks_logged_in(page):
        return

    page_text = await _page_text(page)
    if "/account/login" in _clean(page.url).lower() and _contains_marker(page.url, page_text, markers=_ACCESS_DENIED_MARKERS):
        raise SeekingAlphaAccessError("Seeking Alpha login page is blocked by anti-bot protections.")

    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[name*='email']",
        "input[autocomplete='username']",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[name*='password']",
        "input[autocomplete='current-password']",
    ]
    continue_selectors = [
        "button:has-text('Continue')",
        "button:has-text('Next')",
    ]
    submit_selectors = [
        "button[type='submit']",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "input[type='submit']",
    ]

    if not await _fill_first(page, email_selectors, username):
        raise SeekingAlphaAccessError("Could not find the Seeking Alpha email field.")

    if await _visible_locator(page, password_selectors) is None:
        await _click_first(page, continue_selectors)
        await page.wait_for_timeout(300)

    if not await _fill_first(page, password_selectors, password):
        raise SeekingAlphaAccessError("Could not find the Seeking Alpha password field.")

    if not await _click_first(page, submit_selectors):
        raise SeekingAlphaAccessError("Could not find the Seeking Alpha sign-in button.")

    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 4_000))
    except Exception:
        pass
    await page.wait_for_timeout(600)

    if await _looks_logged_in(page):
        return

    raise SeekingAlphaAccessError(
        "Seeking Alpha login did not complete. The site may require extra verification."
    )


async def _wait_for_content_root(page: "Page") -> None:
    for selector in ("article", "main", "body"):
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=5_000)
            return
        except PlaywrightTimeoutError:
            continue


async def _extract_text_blocks(page: "Page", *, max_blocks: int) -> list[str]:
    try:
        blocks = await page.evaluate(
            """
            ({ maxBlocks }) => {
              const root = document.querySelector('article') || document.querySelector('main') || document.body;
              const selectors = ['h1', 'h2', 'h3', 'p', 'li', 'blockquote'];

              const isVisible = (node) => {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return (
                  style.display !== 'none' &&
                  style.visibility !== 'hidden' &&
                  rect.width > 0 &&
                  rect.height > 0
                );
              };

              const output = [];
              for (const node of root.querySelectorAll(selectors.join(','))) {
                if (!isVisible(node)) {
                  continue;
                }
                const tag = node.tagName.toLowerCase();
                const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                const minLength = tag.startsWith('h') ? 1 : 35;
                if (!text || text.length < minLength) {
                  continue;
                }
                output.push(text);
                if (output.length >= maxBlocks) {
                  break;
                }
              }
              return output;
            }
            """,
            {"maxBlocks": max_blocks},
        )
    except Exception:
        blocks = []
    return [_clean(item) for item in list(blocks or []) if _clean(item)]


async def _meta_description(page: "Page") -> str:
    for selector in ["meta[name='description']", "meta[property='og:description']"]:
        try:
            content = await page.locator(selector).first.get_attribute("content")
        except Exception:
            content = ""
        clean = _clean(content)
        if clean:
            return clean
    return ""


async def _browse_seeking_alpha_page_with_playwright_auth(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
) -> dict[str, Any]:
    _require_playwright()

    timeout_ms = max(int(timeout_seconds), 1) * 1000
    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    credentials = load_seeking_alpha_credentials()
    warning = ""

    try:
        playwright: "Playwright" = await async_playwright().start()
        context: "BrowserContext" | None = None
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=_env_flag("SEEKING_ALPHA_BROWSER_HEADLESS", default=True),
                user_agent=DEFAULT_SEEKING_ALPHA_USER_AGENT,
                viewport={"width": 1600, "height": 1200},
            )
            context.set_default_timeout(timeout_ms)
            page = await _page_for_context(context)

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _dismiss_cookie_banner(page)

            if "/account/login" in _clean(page.url).lower():
                if credentials["username"] and credentials["password"]:
                    page_text = await _page_text(page)
                    if _contains_marker(page.url, page_text, markers=_ACCESS_DENIED_MARKERS):
                        raise SeekingAlphaAccessError("Seeking Alpha redirected to a blocked login page.")
                    await _login(
                        page,
                        username=credentials["username"],
                        password=credentials["password"],
                        timeout_ms=timeout_ms,
                    )
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    raise SeekingAlphaAccessError("Seeking Alpha redirected to login and no credentials were available.")
            elif not (credentials["username"] and credentials["password"]):
                warning = "Seeking Alpha credentials were not found. Attempted public page access only."

            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 4_000))
            except Exception:
                pass
            await _wait_for_content_root(page)
            await page.wait_for_timeout(800)

            title = _clean(await page.title())
            description = await _meta_description(page)
            text_blocks = await _extract_text_blocks(page, max_blocks=80)
            text = _trim("\n".join(text_blocks), limit=max(max_text_chars, 500))
            excerpt = _trim(description or (text_blocks[0] if text_blocks else title), limit=320)
            final_url = _clean(page.url) or url

            if not text:
                raise SeekingAlphaAccessError("No visible page text was captured from Seeking Alpha.")

            if _contains_marker(title, text, markers=_PREVIEW_GATED_MARKERS):
                if credentials["username"] and credentials["password"]:
                    await _login(
                        page,
                        username=credentials["username"],
                        password=credentials["password"],
                        timeout_ms=timeout_ms,
                    )
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 4_000))
                    except Exception:
                        pass
                    await _wait_for_content_root(page)
                    await page.wait_for_timeout(600)
                    title = _clean(await page.title())
                    description = await _meta_description(page)
                    text_blocks = await _extract_text_blocks(page, max_blocks=80)
                    text = _trim("\n".join(text_blocks), limit=max(max_text_chars, 500))
                    excerpt = _trim(description or (text_blocks[0] if text_blocks else title), limit=320)
                    final_url = _clean(page.url) or url
                if _contains_marker(title, text, markers=_PREVIEW_GATED_MARKERS):
                    raise SeekingAlphaAccessError("Seeking Alpha page is still preview-gated after login.")

            return {
                "url": url,
                "final_url": final_url,
                "title": title or final_url or url,
                "description": description,
                "excerpt": excerpt,
                "text": text,
                "mode": "seeking_alpha_authenticated",
                "warning": warning,
                "backend": "playwright",
            }
        finally:
            if context is not None:
                await context.close()
            await playwright.stop()
    except (PlaywrightTimeoutError, PlaywrightError, SeekingAlphaAccessError):
        raise
    except Exception as exc:
        raise SeekingAlphaAccessError(
            f"Authenticated Seeking Alpha browse failed: {type(exc).__name__}: {exc}"
        ) from exc


async def browse_seeking_alpha_page(
    url: str,
    *,
    timeout_seconds: int,
    max_text_chars: int,
) -> dict[str, Any]:
    if not is_seeking_alpha_url(url):
        raise SeekingAlphaAccessError("URL is not a Seeking Alpha page.")

    scrapling_error: Exception | None = None
    try:
        return await asyncio.to_thread(
            _browse_seeking_alpha_page_with_scrapling,
            url,
            timeout_seconds=timeout_seconds,
            max_text_chars=max_text_chars,
        )
    except Exception as exc:
        scrapling_error = exc

    try:
        payload = await _browse_seeking_alpha_page_with_playwright_auth(
            url,
            timeout_seconds=timeout_seconds,
            max_text_chars=max_text_chars,
        )
    except Exception as fallback_exc:
        if scrapling_error is None:
            raise fallback_exc
        raise SeekingAlphaAccessError(
            "Seeking Alpha access failed. "
            f"Scrapling: {type(scrapling_error).__name__}: {_trim(scrapling_error, limit=220)}. "
            f"Playwright fallback: {type(fallback_exc).__name__}: {_trim(fallback_exc, limit=220)}."
        ) from fallback_exc

    if scrapling_error is None:
        return payload

    return _append_warning(
        payload,
        f"Scrapling fallback: {type(scrapling_error).__name__}: {_trim(scrapling_error, limit=220)}.",
    )


__all__ = [
    "SeekingAlphaAccessError",
    "browse_seeking_alpha_page",
    "is_seeking_alpha_url",
    "load_seeking_alpha_credentials",
]
