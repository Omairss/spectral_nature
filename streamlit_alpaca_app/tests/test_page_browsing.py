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
