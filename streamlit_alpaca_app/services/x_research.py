"""Dated company reactions for the existing research model to interpret."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from services.pipeline_store import dataset_namespace, read_execution_state, write_execution_state
from services.x_publishing import configured_x_account, open_x_account


def company_sentiment(
    *, symbol: str, company_name: str = "", query: str = "", start_date: str = "",
    end_date: str = "", asof_time_utc: str = "", max_results: int = 12,
    post_url: str = "", force_refresh: bool = False, timeout_seconds: int = 180,
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    """Acquire reactions, not a coded score or a second model's judgment."""
    symbol = symbol.strip().upper().removeprefix("$")
    if not symbol:
        raise ValueError("A company symbol is required.")
    cutoff = datetime.fromisoformat(asof_time_utc.replace("Z", "+00:00")) if asof_time_utc else datetime.now(timezone.utc)
    cutoff = cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff.astimezone(timezone.utc)
    zone = ZoneInfo(timezone_name)
    local_date = cutoff.astimezone(zone).date()
    start = date.fromisoformat(start_date) if start_date else local_date - timedelta(days=2)
    end = date.fromisoformat(end_date) if end_date else local_date
    if end < start:
        raise ValueError("The reaction period ends before it starts.")
    limit = min(max(int(max_results), 1), 20)
    identity = "$" + symbol
    if company_name.strip():
        identity = "(" + identity + " OR " + json.dumps(company_name.strip(), ensure_ascii=False) + ")"
    search = identity + (" (" + query.strip() + ")" if query.strip() else "")
    start_utc = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    # X search has day-sized bounds. Overfetch the edge day, then apply the
    # caller's exact timezone and observation cutoff below.
    search += f" since:{start_utc.date().isoformat()} until:{(end_utc.date() + timedelta(days=1)).isoformat()}"
    namespace = "x_company_reactions:" + dataset_namespace()
    key = hashlib.sha256(json.dumps([search, post_url, limit, timezone_name, start.isoformat(), end.isoformat()]).encode()).hexdigest()
    cached = read_execution_state(namespace, key)
    age = ((datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at_utc"])).total_seconds()
           if cached else None)

    async def acquire() -> dict[str, Any]:
        posts: dict[str, dict[str, Any]] = {}
        async with open_x_account(configured_x_account()) as browser:
            if post_url:
                post = await browser.read_post(post_url)
                posts[post["url"]] = post
            else:
                for mode in ("top", "latest"):
                    for post in await browser.search(search, mode=mode, limit=limit):
                        previous = posts.get(post["url"], {})
                        posts[post["url"]] = {**post, "search_modes": sorted(set(
                            [*previous.get("search_modes", []), mode]))}
        return {"status": "available", "symbol": symbol, "company_name": company_name,
                "query": search, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                "posts": list(posts.values()), "coverage": "One bounded Top/Latest search sample; not a representative poll."}

    if cached and not force_refresh and age is not None and age <= 900:
        result = dict(cached)
        result["cache_status"] = "hit"
    else:
        try:
            result = asyncio.run(asyncio.wait_for(acquire(), timeout=max(30, min(int(timeout_seconds), 300)) - 10))
        except Exception as exc:
            if not cached:
                return {"status": "unavailable", "symbol": symbol, "query": search,
                        "posts": [], "error_type": type(exc).__name__,
                        "messages": ["X reaction collection was unavailable; no sentiment conclusion was produced."]}
            result = {**cached, "status": "partial", "cache_status": "stale",
                      "acquisition_error": type(exc).__name__}
        else:
            try:
                stored = write_execution_state(namespace, key, result)
                result["cache_write_status"] = "saved" if stored else "failed"
            except Exception:
                result["cache_write_status"] = "failed"
    # An acquisition cache is shared across consumers. Apply each caller's
    # observation cutoff without relabeling capture or original post dates.
    result = {**result, "asof_time_utc": cutoff.isoformat(), "posts": [
        post for post in result["posts"] if post.get("published_at") and
        datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")) <= cutoff and
        (bool(post_url) or start_utc <= datetime.fromisoformat(post["published_at"].replace("Z", "+00:00")) < end_utc)
    ]}
    result["source_links"] = [{"url": post["url"], "title": post.get("author", "X post")}
                              for post in result["posts"]]
    result["candidate_sources"] = [{
        **post, "document_id": "x-post::" + post["url"].split("/status/", 1)[-1].split("?", 1)[0],
        "title": post.get("author", "X post"), "source": "X", "source_kind": "social_post",
        "evidence_type": "attributed_opinion",
    } for post in result["posts"]]
    result["llm_context_text"] = (
        "Interpret these company-specific reactions in the current investigation. Separate supportive and "
        "critical views, their business reasons, disagreement and changes in expectations. Attribute claims "
        "and retain dates. These are sampled online opinions, not verified company facts or market-wide "
        "sentiment percentages. Use post_url to read selected truncated posts in full.\n"
        + json.dumps({"acquisition": {key: result.get(key) for key in (
            "status", "query", "asof_time_utc", "fetched_at_utc", "coverage", "cache_status", "acquisition_error"
        )}, "posts": result["candidate_sources"]}, ensure_ascii=False)
    )
    return result
