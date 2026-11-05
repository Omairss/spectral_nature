"""Capture the saved Attention page with the same React charts as Home."""
from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse

from services.secrets import runtime_env_value
from services.x_publishing import XPublishingError


def _origin(value: str) -> str:
    parsed = urlparse(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
        raise XPublishingError("Attention sharing requires a configured HTTPS application origin.")
    return value.rstrip("/")


def attention_path(source_version: str, bundle_id: str) -> str:
    return f"/attention/{quote(source_version, safe='')}/{quote(bundle_id, safe='')}"


def attention_url(source_version: str, bundle_id: str) -> str:
    return _origin(runtime_env_value("APP_PUBLIC_BASE_URL") or "") + attention_path(source_version, bundle_id)


@asynccontextmanager
async def attention_renderer() -> AsyncIterator[Any]:
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1240, "height": 1600}, device_scale_factor=1)
            yield page
        finally:
            await browser.close()


async def capture_attention_images(page: Any, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return PNG bytes only after checking the rendered immutable source identity."""
    base = _origin(runtime_env_value("ATTENTION_X_RENDER_BASE_URL") or runtime_env_value("APP_PUBLIC_BASE_URL") or "")
    path = attention_path(record["source_version"], record["source_bundle_id"])
    response = await page.goto(base + path + "/images", wait_until="networkidle", timeout=90_000)
    if not response or not response.ok:
        raise XPublishingError("The saved Attention image page is unavailable.")
    root = page.locator("[data-source-version]")
    if (await root.get_attribute("data-source-version") != record["source_version"]
            or await root.get_attribute("data-source-run") != record["source_run_id"]
            or await root.get_attribute("data-event-id") != record["source_bundle_id"]):
        raise XPublishingError("The image page does not match the prepared Attention event.")
    await page.evaluate("document.fonts.ready")
    panels = root.locator("[data-attention-image]")
    count = await panels.count()
    if not 1 <= count <= 4:
        raise XPublishingError("Attention must render between one and four complete image panels.")
    images = []
    for index in range(count):
        panel = panels.nth(index)
        bounds = await panel.bounding_box()
        if not bounds:
            raise XPublishingError("An Attention image panel is not visible.")
        # The image page scrolls inside its canvas. Fit the entire panel before
        # capture so a tall financial card cannot be clipped by that ancestor.
        await page.set_viewport_size({"width": 1240, "height": max(1600, math.ceil(bounds["height"]) + 20)})
        data = await panel.screenshot(type="png", animations="disabled")
        if len(data) > 5_000_000:
            raise XPublishingError("An Attention chart image exceeds X's photo limit.")
        images.append({"name": f"attention-{index + 1}.png", "mime_type": "image/png", "data": data,
                       "alt_text": (await panel.get_attribute("data-image-description") or "")[:1000]})
    return images


def media_archive(account: str):
    from services.pipeline_store import ImmutableBlobArchive
    # Account-wide receipts must remain readable when another environment owns delivery.
    return ImmutableBlobArchive(namespace="", root=f"attention-x/{account.lower()}")


async def retain_attention_images(page: Any, record: dict[str, Any], account: str) -> list[dict[str, Any]]:
    images = await capture_attention_images(page, record)
    archive = media_archive(account)
    retained = []
    for image in images:
        receipt = await asyncio.to_thread(archive.put, image["data"], suffix="png", content_type="image/png")
        retained.append({key: value for key, value in image.items() if key != "data"} | {"archive": receipt})
    return retained


def load_attention_images(record: dict[str, Any], account: str) -> list[dict[str, Any]]:
    media = record.get("media") or []
    if not 1 <= len(media) <= 4:
        raise XPublishingError("The Attention post needs its retained image panels.")
    archive = media_archive(account)
    return [{"name": item["name"], "mimeType": item["mime_type"], "buffer": archive.get(item["archive"])} for item in media]
