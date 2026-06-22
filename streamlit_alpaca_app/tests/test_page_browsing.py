from __future__ import annotations

import asyncio
import pytest

from services import page_browsing
from services import seeking_alpha_access


def test_browse_page_prefers_authenticated_seeking_alpha_helper(monkeypatch):
    async def _fake_browse(url: str, *, timeout_seconds: int, max_text_chars: int):
        assert url == "https://seekingalpha.com/news/123456"
        assert timeout_seconds == 12
        assert max_text_chars == 1800
        return {
            "url": url,
            "final_url": url,
            "title": "Seeking Alpha Story",
            "description": "Story description",
            "excerpt": "Story excerpt",
            "text": "Visible article text",
            "mode": "seeking_alpha_authenticated",
            "warning": "",
        }

    monkeypatch.setattr(page_browsing, "browse_seeking_alpha_page", _fake_browse)

    payload = page_browsing.browse_page(
        "https://seekingalpha.com/news/123456",
        max_text_chars=1800,
    )

    assert payload["mode"] == "seeking_alpha_authenticated"
    assert payload["text"] == "Visible article text"


def test_page_quality_issue_detects_blocked_and_shallow_pages():
    assert page_browsing.page_quality_issue({"title": "Access denied", "text": "Verify you are human"})
    assert page_browsing.page_quality_issue({"title": "Readable", "text": "short"}, min_text_chars=20) == "shallow:5<20"
    assert (
        page_browsing.page_quality_issue(
            {
                "title": "Cloudflare investor overview",
                "text": "Cloudflare sells network security and developer services to enterprise customers. " * 12,
            },
            min_text_chars=120,
        )
        == ""
    )
    assert (
        page_browsing.page_quality_issue(
            {
                "title": "Investor relations",
                "text": (
                    "Skip to main content Skip to section navigation Skip to footer "
                    "Home About News Contact Careers IR Overview Manage Cookie Preferences "
                    "A short visible page shell."
                ),
            },
            min_text_chars=80,
        )
        == "navigation_dominated"
    )
    assert page_browsing.page_quality_issue({"title": "Readable", "text": "useful business evidence " * 24}) == ""


def test_browse_page_escalates_from_shallow_browser_result_to_scrapling(monkeypatch):
    calls: list[str] = []

    async def _fake_playwright(url: str, *, timeout_seconds: int, max_text_chars: int):
        del timeout_seconds, max_text_chars
        calls.append("playwright")
        return {
            "url": url,
            "final_url": url,
            "title": "Security Check",
            "description": "",
            "excerpt": "Verify you are human",
            "text": "Verify you are human",
            "mode": "playwright",
            "warning": "",
        }

    def _fake_http(url: str, *, timeout_seconds: int, max_text_chars: int, warning: str = ""):
        del timeout_seconds, max_text_chars
        calls.append("http")
        return {
            "url": url,
            "final_url": url,
            "title": "Overview",
            "description": "",
            "excerpt": "too short",
            "text": "too short",
            "mode": "http",
            "warning": warning,
        }

    def _fake_scrapling(url: str, *, timeout_seconds: int, max_text_chars: int, warning: str = ""):
        del timeout_seconds, max_text_chars
        calls.append("scrapling")
        return {
            "url": url,
            "final_url": url,
            "title": "Company overview",
            "description": "",
            "excerpt": "Company sells critical infrastructure",
            "text": "Company sells critical infrastructure to enterprise customers. " * 12,
            "mode": "scrapling",
            "warning": warning,
        }

    def _fake_firecrawl(*args, **kwargs):
        raise AssertionError("Firecrawl should not run after Scrapling succeeds.")

    monkeypatch.setattr(page_browsing, "_browse_with_playwright", _fake_playwright)
    monkeypatch.setattr(page_browsing, "_browse_with_http", _fake_http)
    monkeypatch.setattr(page_browsing, "_browse_with_scrapling", _fake_scrapling)
    monkeypatch.setattr(page_browsing, "_browse_with_firecrawl", _fake_firecrawl)

    payload = page_browsing.browse_page(
        "https://example.com/company",
        require_main_content=True,
        min_text_chars=80,
    )

    assert calls == ["playwright", "http", "scrapling"]
    assert payload["mode"] == "scrapling"
    assert "Playwright quality issue" in payload["warning"]
    assert "HTTP quality issue" in payload["warning"]
    assert "enterprise customers" in payload["text"]


def test_browse_page_escalates_to_firecrawl_when_scrapling_is_unusable(monkeypatch):
    async def _fake_playwright(url: str, *, timeout_seconds: int, max_text_chars: int):
        del timeout_seconds, max_text_chars
        return {
            "url": url,
            "final_url": url,
            "title": "Blocked",
            "description": "",
            "excerpt": "captcha",
            "text": "captcha",
            "mode": "playwright",
            "warning": "",
        }

    def _fake_http(*args, **kwargs):
        raise page_browsing.PageBrowsingError("HTTP blocked")

    def _fake_scrapling(*args, **kwargs):
        raise page_browsing.PageBrowsingError("Scrapling blocked")

    def _fake_firecrawl(url: str, *, timeout_seconds: int, max_text_chars: int, warning: str = ""):
        del timeout_seconds, max_text_chars
        return {
            "url": url,
            "final_url": url,
            "title": "Company investor relations",
            "description": "",
            "excerpt": "Firecrawl markdown",
            "text": "The company sells software subscriptions and cloud services to commercial customers. " * 12,
            "mode": "firecrawl",
            "warning": warning,
        }

    monkeypatch.setattr(page_browsing, "_browse_with_playwright", _fake_playwright)
    monkeypatch.setattr(page_browsing, "_browse_with_http", _fake_http)
    monkeypatch.setattr(page_browsing, "_browse_with_scrapling", _fake_scrapling)
    monkeypatch.setattr(page_browsing, "_browse_with_firecrawl", _fake_firecrawl)

    payload = page_browsing.browse_page(
        "https://example.com/investors",
        require_main_content=True,
        min_text_chars=120,
    )

    assert payload["mode"] == "firecrawl"
    assert "Playwright quality issue" in payload["warning"]
    assert "HTTP blocked" in payload["warning"]
    assert "Scrapling blocked" in payload["warning"]


def test_firecrawl_browse_requests_clean_markdown_main_content(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeFirecrawlResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "data": {
                    "markdown": "Company sells data-center infrastructure services. " * 12,
                    "metadata": {
                        "title": "Investor overview",
                        "sourceURL": "https://example.com/investors",
                        "description": "Investor relations overview",
                    },
                }
            }

    def _fake_post(endpoint, *, headers, json, timeout):
        captured["endpoint"] = endpoint
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeFirecrawlResponse()

    monkeypatch.setattr(page_browsing, "_firecrawl_api_key", lambda: "firecrawl-test-key")
    monkeypatch.setattr(page_browsing.requests, "post", _fake_post)

    payload = page_browsing._browse_with_firecrawl(
        "https://example.com/investors",
        timeout_seconds=9,
        max_text_chars=900,
    )

    assert payload["mode"] == "firecrawl"
    assert captured["endpoint"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["headers"]["Authorization"] == "Bearer firecrawl-test-key"
    assert captured["json"]["formats"] == ["markdown"]
    assert captured["json"]["onlyMainContent"] is True
    assert captured["json"]["onlyCleanContent"] is True
    assert captured["json"]["timeout"] == 9000
    assert "data-center infrastructure" in payload["text"]


def test_load_seeking_alpha_credentials_uses_default_key_vault_secret_names(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def _fake_get_secret_value_from_vault(secret_name: str, *, vault_name: str = "", vault_url: str = "") -> str:
        calls.append((secret_name, vault_name, vault_url))
        if secret_name == "seeking-alpha-username":
            return "user@example.com"
        if secret_name == "seeking-alpha-password":
            return "password-value"
        return ""

    monkeypatch.delenv("SEEKING_ALPHA_USERNAME", raising=False)
    monkeypatch.delenv("SEEKING_ALPHA_PASSWORD", raising=False)
    monkeypatch.delenv("SEEKING_ALPHA_USERNAME_SECRET_NAME", raising=False)
    monkeypatch.delenv("SEEKING_ALPHA_PASSWORD_SECRET_NAME", raising=False)
    monkeypatch.setattr(
        seeking_alpha_access,
        "get_secret_value_from_vault",
        _fake_get_secret_value_from_vault,
    )

    credentials = seeking_alpha_access.load_seeking_alpha_credentials(vault_name="spectral-nature-kvault")

    assert credentials["username"] == "user@example.com"
    assert credentials["password"] == "password-value"
    assert calls == [
        ("seeking-alpha-username", "spectral-nature-kvault", ""),
        ("seeking-alpha-password", "spectral-nature-kvault", ""),
    ]


def test_browse_seeking_alpha_page_prefers_scrapling(monkeypatch):
    calls: list[str] = []

    def _fake_scrapling(url: str, *, timeout_seconds: int, max_text_chars: int):
        calls.append("scrapling")
        assert url == "https://seekingalpha.com/article/123"
        assert timeout_seconds == 15
        assert max_text_chars == 2400
        return {
            "url": url,
            "final_url": url,
            "title": "Full Article",
            "description": "Desc",
            "excerpt": "Excerpt",
            "text": "Full text",
            "mode": "seeking_alpha_authenticated",
            "warning": "",
            "backend": "scrapling",
        }

    async def _fake_playwright(url: str, *, timeout_seconds: int, max_text_chars: int):
        del url, timeout_seconds, max_text_chars
        calls.append("playwright")
        raise AssertionError("playwright fallback should not run")

    monkeypatch.setattr(
        seeking_alpha_access,
        "_browse_seeking_alpha_page_with_scrapling",
        _fake_scrapling,
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "_browse_seeking_alpha_page_with_playwright_auth",
        _fake_playwright,
    )

    payload = asyncio.run(
        seeking_alpha_access.browse_seeking_alpha_page(
            "https://seekingalpha.com/article/123",
            timeout_seconds=15,
            max_text_chars=2400,
        )
    )

    assert calls == ["scrapling"]
    assert payload["backend"] == "scrapling"
    assert payload["text"] == "Full text"


def test_browse_seeking_alpha_page_falls_back_to_playwright(monkeypatch):
    def _fake_scrapling(url: str, *, timeout_seconds: int, max_text_chars: int):
        del url, timeout_seconds, max_text_chars
        raise seeking_alpha_access.SeekingAlphaAccessError("scrapling blocked")

    async def _fake_playwright(url: str, *, timeout_seconds: int, max_text_chars: int):
        assert url == "https://seekingalpha.com/news/123"
        assert timeout_seconds == 12
        assert max_text_chars == 1800
        return {
            "url": url,
            "final_url": url,
            "title": "Playwright Article",
            "description": "",
            "excerpt": "Excerpt",
            "text": "Fallback text",
            "mode": "seeking_alpha_authenticated",
            "warning": "",
            "backend": "playwright",
        }

    monkeypatch.setattr(
        seeking_alpha_access,
        "_browse_seeking_alpha_page_with_scrapling",
        _fake_scrapling,
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "_browse_seeking_alpha_page_with_playwright_auth",
        _fake_playwright,
    )

    payload = asyncio.run(
        seeking_alpha_access.browse_seeking_alpha_page(
            "https://seekingalpha.com/news/123",
            timeout_seconds=12,
            max_text_chars=1800,
        )
    )

    assert payload["backend"] == "playwright"
    assert "scrapling blocked" in payload["warning"]


class _FakeResponseNode:
    def __init__(self, text: str = "", attrib: dict[str, str] | None = None):
        self.text = text
        self.attrib = dict(attrib or {})

    def get_all_text(self, strip: bool = True) -> str:
        del strip
        return self.text


class _FakeScraplingResponse:
    def __init__(self, *, url: str, status: int = 200, title: str = "", description: str = "", text: str = ""):
        self.url = url
        self.status = status
        self._title = title
        self._description = description
        self._text = text

    def css(self, selector: str):
        if selector == "title" and self._title:
            return [_FakeResponseNode(self._title)]
        if selector in {"meta[name='description']", "meta[property='og:description']"} and self._description:
            return [_FakeResponseNode(attrib={"content": self._description})]
        if selector in {"article", "main", "body"} and self._text:
            return [_FakeResponseNode(self._text)]
        return []

    def get_all_text(self, strip: bool = True) -> str:
        del strip
        return self._text


class _FakeStealthySession:
    def __init__(self, responses: list[_FakeScraplingResponse], calls: list[str]):
        self._responses = list(responses)
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def fetch(self, url: str, page_action=None):
        del page_action
        self._calls.append(url)
        if not self._responses:
            raise AssertionError("No fake Scrapling responses left.")
        return self._responses.pop(0)


def test_scrapling_browse_skips_login_when_target_page_is_already_readable(monkeypatch):
    article_url = "https://seekingalpha.com/article/123"
    calls: list[str] = []
    login_calls: list[str] = []
    responses = [
        _FakeScraplingResponse(
            url=article_url,
            status=200,
            title="Readable article",
            description="Desc",
            text="Visible article text " * 40,
        )
    ]

    monkeypatch.setattr(
        seeking_alpha_access,
        "StealthySession",
        lambda **kwargs: _FakeStealthySession(responses, calls),
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "load_seeking_alpha_credentials",
        lambda: {"username": "user@example.com", "password": "pw"},
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "_login_with_scrapling_session",
        lambda *args, **kwargs: login_calls.append("login"),
    )

    payload = seeking_alpha_access._browse_seeking_alpha_page_with_scrapling(
        article_url,
        timeout_seconds=15,
        max_text_chars=2400,
    )

    assert calls == [article_url]
    assert login_calls == []
    assert payload["title"] == "Readable article"


def test_scrapling_browse_logs_in_only_after_target_redirects_to_login(monkeypatch):
    article_url = "https://seekingalpha.com/article/123"
    login_url = "https://seekingalpha.com/account/login"
    calls: list[str] = []
    login_calls: list[str] = []
    responses = [
        _FakeScraplingResponse(
            url=login_url,
            status=200,
            title="Login",
            text="Sign in to continue",
        ),
        _FakeScraplingResponse(
            url=article_url,
            status=200,
            title="Readable article",
            description="Desc",
            text="Visible article text " * 40,
        ),
    ]

    monkeypatch.setattr(
        seeking_alpha_access,
        "StealthySession",
        lambda **kwargs: _FakeStealthySession(responses, calls),
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "load_seeking_alpha_credentials",
        lambda: {"username": "user@example.com", "password": "pw"},
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "_login_with_scrapling_session",
        lambda *args, **kwargs: login_calls.append("login"),
    )

    payload = seeking_alpha_access._browse_seeking_alpha_page_with_scrapling(
        article_url,
        timeout_seconds=15,
        max_text_chars=2400,
    )

    assert calls == [article_url, article_url]
    assert login_calls == ["login"]
    assert payload["title"] == "Readable article"


def test_scrapling_browse_fails_fast_when_redirected_to_blocked_login_page(monkeypatch):
    article_url = "https://seekingalpha.com/article/123"
    login_url = "https://seekingalpha.com/account/login"
    calls: list[str] = []
    login_calls: list[str] = []
    responses = [
        _FakeScraplingResponse(
            url=login_url,
            status=403,
            title="Access denied",
            text="Access to this page has been denied.",
        ),
    ]

    monkeypatch.setattr(
        seeking_alpha_access,
        "StealthySession",
        lambda **kwargs: _FakeStealthySession(responses, calls),
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "load_seeking_alpha_credentials",
        lambda: {"username": "user@example.com", "password": "pw"},
    )
    monkeypatch.setattr(
        seeking_alpha_access,
        "_login_with_scrapling_session",
        lambda *args, **kwargs: login_calls.append("login"),
    )

    with pytest.raises(seeking_alpha_access.SeekingAlphaAccessError, match="blocked login page"):
        seeking_alpha_access._browse_seeking_alpha_page_with_scrapling(
            article_url,
            timeout_seconds=15,
            max_text_chars=2400,
        )

    assert calls == [article_url]
    assert login_calls == []
