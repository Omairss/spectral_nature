from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Playwright

_PLAYWRIGHT_IMPORT_ERROR: Exception | None = None

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError as exc:  # pragma: no cover - handled at runtime in notebook setup
    PlaywrightTimeoutError = RuntimeError  # type: ignore[assignment]
    async_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc


SEEKING_ALPHA_BASE_URL = "https://seekingalpha.com"
_FACT_META_KEYS = (
    "description",
    "og:description",
    "og:title",
    "twitter:title",
    "twitter:description",
    "author",
)


def _require_playwright() -> None:
    if async_playwright is None:
        raise ImportError(
            "Playwright is not installed. Run `pip install playwright` and "
            "`python -m playwright install chromium` before using this helper."
        ) from _PLAYWRIGHT_IMPORT_ERROR


def normalize_target(target: str) -> str:
    value = target.strip()
    if not value:
        raise ValueError("Target cannot be empty.")

    if value.startswith(("http://", "https://")):
        return value

    if value.startswith("/"):
        return f"{SEEKING_ALPHA_BASE_URL}{value}"

    return f"{SEEKING_ALPHA_BASE_URL}/symbol/{value.upper()}"


def safe_slug(value: str, max_length: int = 80) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        slug = "snapshot"
    return slug[:max_length]


def _dedupe_text_blocks(blocks: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique_blocks: list[dict[str, str]] = []

    for block in blocks:
        text = re.sub(r"\s+", " ", block.get("text", "")).strip()
        tag = block.get("tag", "").strip().lower() or "text"
        if not text:
            continue
        key = f"{tag}:{text}"
        if key in seen:
            continue
        seen.add(key)
        unique_blocks.append({"tag": tag, "text": text})

    return unique_blocks


def _flatten_json_ld(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    if isinstance(value, list):
        for entry in value:
            items.extend(_flatten_json_ld(entry))
        return items

    if not isinstance(value, dict):
        return items

    graph = value.get("@graph")
    if isinstance(graph, list):
        for entry in graph:
            items.extend(_flatten_json_ld(entry))

    item = {key: nested for key, nested in value.items() if key != "@graph"}
    if item:
        items.append(item)
    return items


def _extract_author_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return str(name).strip() if name else None

    if isinstance(value, list):
        names = [name for name in (_extract_author_name(item) for item in value) if name]
        return ", ".join(names) if names else None

    if isinstance(value, str):
        text = value.strip()
        return text or None

    return None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _pick_primary_json_ld(json_ld: list[dict[str, Any]]) -> dict[str, Any]:
    preferred_types = {"article", "newsarticle", "analysisnewsarticle", "webpage"}

    for item in json_ld:
        raw_type = item.get("@type")
        type_names: list[str]
        if isinstance(raw_type, list):
            type_names = [str(name).strip().lower() for name in raw_type]
        else:
            type_names = [str(raw_type).strip().lower()] if raw_type else []

        if any(name in preferred_types for name in type_names):
            return item

    return json_ld[0] if json_ld else {}


def build_page_facts(
    page_title: str,
    meta: dict[str, str],
    json_ld: list[dict[str, Any]],
) -> dict[str, str | None]:
    primary_json_ld = _pick_primary_json_ld(json_ld)

    return {
        "title": _first_non_empty(
            primary_json_ld.get("headline"),
            primary_json_ld.get("name"),
            meta.get("og:title"),
            meta.get("twitter:title"),
            page_title,
        ),
        "description": _first_non_empty(
            primary_json_ld.get("description"),
            meta.get("og:description"),
            meta.get("twitter:description"),
            meta.get("description"),
        ),
        "author": _first_non_empty(
            _extract_author_name(primary_json_ld.get("author")),
            meta.get("author"),
        ),
        "published_at": _first_non_empty(
            primary_json_ld.get("datePublished"),
        ),
        "modified_at": _first_non_empty(
            primary_json_ld.get("dateModified"),
        ),
    }


def _default_snapshot_name(snapshot: dict[str, Any]) -> str:
    page_title = snapshot.get("page_facts", {}).get("title") or snapshot.get("page_title") or "snapshot"
    url_path = urlparse(snapshot.get("url", "")).path.strip("/")
    tail = url_path.split("/")[-1] if url_path else str(page_title)
    captured_at = str(snapshot.get("captured_at", ""))
    compact_stamp = re.sub(r"[^0-9]", "", captured_at)[:14] or "capture"
    return f"{safe_slug(tail)}_{compact_stamp}.json"


def save_snapshot(snapshot: dict[str, Any], output_dir: str | Path, file_name: str | None = None) -> Path:
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / (file_name or _default_snapshot_name(snapshot))
    destination.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return destination


def snapshot_to_text(snapshot: dict[str, Any], max_text_blocks: int = 12) -> str:
    facts = snapshot.get("page_facts", {})
    lines = [
        f"Title: {facts.get('title') or snapshot.get('page_title') or 'Unknown'}",
        f"URL: {snapshot.get('url', '')}",
    ]

    if facts.get("author"):
        lines.append(f"Author: {facts['author']}")
    if facts.get("published_at"):
        lines.append(f"Published: {facts['published_at']}")
    if facts.get("description"):
        lines.append(f"Description: {facts['description']}")

    headings = snapshot.get("headings", [])
    if headings:
        lines.append("Headings:")
        lines.extend(f"- {heading}" for heading in headings[:8])

    text_blocks = snapshot.get("text_blocks", [])
    if text_blocks:
        lines.append("Visible text:")
        lines.extend(f"- {block['text']}" for block in text_blocks[:max_text_blocks])

    return "\n".join(lines)


class SeekingAlphaBrowser:
    def __init__(
        self,
        user_data_dir: str | Path,
        *,
        headless: bool = False,
        slow_mo_ms: int = 0,
        timeout_ms: int = 15_000,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> "SeekingAlphaBrowser":
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def start(self) -> "SeekingAlphaBrowser":
        _require_playwright()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        if self.context is not None:
            return self

        self._playwright = await async_playwright().start()
        context_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.user_data_dir),
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
        }

        if self.headless:
            context_kwargs["viewport"] = {"width": 1600, "height": 1200}
        else:
            context_kwargs["args"] = ["--start-maximized"]
            context_kwargs["no_viewport"] = True

        self.context = await self._playwright.chromium.launch_persistent_context(**context_kwargs)
        self.context.set_default_timeout(self.timeout_ms)
        return self

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def page(self) -> "Page":
        if self.context is None:
            raise RuntimeError("Browser is not started. Call `start()` first.")

        return self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def wait_for_user(self, message: str | None = None, *, enabled: bool = True) -> bool:
        if not enabled:
            return False

        prompt = message or (
            "Finish any login, 2FA, or cookie banner steps in the browser window, "
            "then press Enter here to continue."
        )
        try:
            input(prompt + "\n")
            return True
        except EOFError:
            return False
        except Exception as exc:
            if type(exc).__name__ == "StdinNotImplementedError":
                return False
            raise

    async def open(
        self,
        target: str,
        *,
        scroll: bool = True,
        scroll_steps: int = 8,
        settle_ms: int = 1_500,
    ) -> "Page":
        page = await self.page()
        await page.goto(normalize_target(target), wait_until="domcontentloaded")
        await self._wait_for_content_root(page)

        if scroll:
            await self.auto_scroll(page, steps=scroll_steps)

        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)

        return page

    async def auto_scroll(self, page: "Page", *, steps: int = 8, pause_ms: int = 500) -> None:
        for _ in range(max(steps, 0)):
            await page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.85));")
            await page.wait_for_timeout(pause_ms)

        await page.evaluate("window.scrollTo(0, 0);")
        await page.wait_for_timeout(pause_ms)

    async def extract_snapshot(
        self,
        page: "Page",
        *,
        max_text_blocks: int = 60,
        max_tables: int = 5,
        max_table_rows: int = 20,
        max_links: int = 25,
    ) -> dict[str, Any]:
        page_title = await page.title()
        meta = await self._extract_meta(page)
        json_ld = await self._extract_json_ld(page)
        text_blocks = _dedupe_text_blocks(
            await self._extract_text_blocks(page, max_blocks=max_text_blocks)
        )
        tables = await self._extract_tables(
            page,
            max_tables=max_tables,
            max_rows=max_table_rows,
        )
        links = await self._extract_links(page, max_links=max_links)
        headings = [block["text"] for block in text_blocks if block["tag"] in {"h1", "h2", "h3"}]

        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "url": page.url,
            "page_title": page_title,
            "page_facts": build_page_facts(page_title=page_title, meta=meta, json_ld=json_ld),
            "headings": headings,
            "text_blocks": text_blocks,
            "tables": tables,
            "links": links,
            "meta": {key: value for key, value in meta.items() if key in _FACT_META_KEYS},
            "json_ld": json_ld,
        }

    async def _wait_for_content_root(self, page: "Page") -> None:
        for selector in ("article", "main", "body"):
            try:
                await page.locator(selector).first.wait_for(state="visible", timeout=5_000)
                return
            except PlaywrightTimeoutError:
                continue

    async def _extract_meta(self, page: "Page") -> dict[str, str]:
        return await page.evaluate(
            """
            () => {
              const keys = ['description', 'og:description', 'og:title', 'twitter:title', 'twitter:description', 'author'];
              const output = {};

              for (const key of keys) {
                const metaByName = document.querySelector(`meta[name="${key}"]`);
                const metaByProperty = document.querySelector(`meta[property="${key}"]`);
                const node = metaByName || metaByProperty;
                const content = node ? (node.getAttribute('content') || '').trim() : '';
                if (content) {
                  output[key] = content;
                }
              }

              return output;
            }
            """
        )

    async def _extract_json_ld(self, page: "Page") -> list[dict[str, Any]]:
        raw_blocks = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
              .map((node) => node.textContent || '')
            """
        )

        parsed_items: list[dict[str, Any]] = []
        for raw_block in raw_blocks:
            text = str(raw_block).strip()
            if not text:
                continue
            try:
                parsed_items.extend(_flatten_json_ld(json.loads(text)))
            except json.JSONDecodeError:
                continue

        return parsed_items

    async def _extract_text_blocks(self, page: "Page", *, max_blocks: int) -> list[dict[str, str]]:
        return await page.evaluate(
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

              const blocks = [];
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

                blocks.push({ tag, text });
                if (blocks.length >= maxBlocks) {
                  break;
                }
              }

              return blocks;
            }
            """,
            {"maxBlocks": max_blocks},
        )

    async def _extract_tables(
        self,
        page: "Page",
        *,
        max_tables: int,
        max_rows: int,
    ) -> list[dict[str, Any]]:
        return await page.evaluate(
            """
            ({ maxTables, maxRows }) => {
              const root = document.querySelector('article') || document.querySelector('main') || document.body;
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

              const tables = [];
              for (const table of root.querySelectorAll('table')) {
                if (!isVisible(table)) {
                  continue;
                }

                const rows = Array.from(table.querySelectorAll('tr'))
                  .slice(0, maxRows)
                  .map((row) =>
                    Array.from(row.querySelectorAll('th, td'))
                      .map((cell) => (cell.innerText || cell.textContent || '').replace(/\\s+/g, ' ').trim())
                      .filter(Boolean)
                  )
                  .filter((cells) => cells.length > 0);

                if (rows.length === 0) {
                  continue;
                }

                tables.push({ rows });
                if (tables.length >= maxTables) {
                  break;
                }
              }

              return tables;
            }
            """,
            {"maxTables": max_tables, "maxRows": max_rows},
        )

    async def _extract_links(self, page: "Page", *, max_links: int) -> list[dict[str, str]]:
        links = await page.evaluate(
            """
            ({ maxLinks }) => {
              const root = document.querySelector('article') || document.querySelector('main') || document.body;
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

              const items = [];
              for (const link of root.querySelectorAll('a[href]')) {
                if (!isVisible(link)) {
                  continue;
                }

                const text = (link.innerText || link.textContent || '').replace(/\\s+/g, ' ').trim();
                const href = (link.href || '').trim();

                if (!text || !href) {
                  continue;
                }

                items.push({ text, href });
                if (items.length >= maxLinks) {
                  break;
                }
              }

              return items;
            }
            """,
            {"maxLinks": max_links},
        )

        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in links:
            key = f"{item['text']}::{item['href']}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped
