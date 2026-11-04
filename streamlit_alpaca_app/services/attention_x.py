"""Publish existing, reviewed Attention headlines through the owned X browser."""
from __future__ import annotations

import asyncio
from typing import Any

from services.x_publishing import SECTIONS, XAccountStore, XBrowser, XPublishingError
from services.secrets import runtime_env_value


def attention_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = ("bundle_id", "event_id", "event_title")
    result = []
    for section in SECTIONS:
        for index, item in enumerate(payload.get(section) or []):
            if isinstance(item, dict) and item.get("publish", True):
                result.append({"source_ref": f"{section}:{index}", **{key:item[key] for key in fields if key in item}})
    return result


def prepare_attention_posts(
    payload: dict[str, Any], *, source_version: str, account: str,
) -> dict[str, Any]:
    """Queue one existing feed headline per event, with no extra model call."""
    if not source_version or not payload.get("run_id"):
        raise XPublishingError("A completed, versioned Attention publication is required.")
    events = attention_events(payload)
    with XAccountStore(account) as store:
        prepared = []
        for event in events:
            event_id = str(event.get("event_id") or event.get("bundle_id") or "")
            text = str(event.get("event_title") or "").strip()
            if not event_id or not text or len(text) > 280:
                raise XPublishingError("The feed event needs an identity and a headline that fits X.")
            existing = store.read_post(event_id)
            if existing:
                prepared.append(existing)
                continue
            record = {
                "event_id": event_id, "text": text, "account": account,
                "source_title": text, "source_ref": event["source_ref"],
                "source_version": source_version, "source_as_of": payload.get("generated_at_utc"),
                "source_bundle_id": event.get("bundle_id"), "source_run_id": payload["run_id"],
                "status": "prepared", "composition": "attention_feed_headline",
            }
            store.save_post(record)
            prepared.append(record)
        result = {
            "status": "prepared" if events else "empty", "source_version": source_version,
            "composition": "attention_feed_headline", "posts": prepared,
        }
        store.save_batch(source_version, result)
        return result


def deliver_attention_posts(account: str, *, headless: bool = True, maximum: int = 1) -> list[dict[str, Any]]:
    """Deliver prepared events; unconfirmed event IDs are never blindly retried."""
    if maximum < 1:
        raise XPublishingError("A positive delivery bound is required.")
    with XAccountStore(account) as store:
        records = store.records()
        pending = sorted((record for record in records if record.get("status") == "prepared"),
                         key=lambda record:(record.get("source_as_of") or "",record["event_id"]))[:maximum]
        if not pending:
            return []
        async def deliver():
            async with XBrowser(store, headless=headless) as browser:
                return [await browser.publish(record) for record in pending]
        return asyncio.run(deliver())


def publish_completed_attention(*, run_id: str, source_version: str) -> dict[str, Any]:
    """Called after the run completion receipt, outside the research clock."""
    if (runtime_env_value("ATTENTION_X_PUBLISH_ENABLED") or "false").lower() not in {"true", "1", "yes"}:
        return {"status": "disabled"}
    from services.attention_materialized import deserialize_attention_home_payload
    from services.pipeline_store import (dataset_metadata_for_version, load_dataset_frame, write_execution_state,
                                        dataset_namespace, job_execution_receipt)
    from services.x_publishing import configured_x_account

    state = {"run_id": run_id, "source_version": source_version, "status": "preparing"}
    try:
        completion = job_execution_receipt("attention-home-build", run_id)
        if (not completion or completion.get("status") not in {"Succeeded", "Warning"}
                or (completion.get("datasets") or {}).get("attention_home_snapshots_1d") != source_version):
            raise XPublishingError("The exact Attention version needs its finished run receipt.")
        metadata = dataset_metadata_for_version("attention_home_snapshots_1d", source_version)
        if metadata is None:
            raise XPublishingError("The completed run's Attention version is not readable.")
        frame = load_dataset_frame(metadata)
        if frame.empty:
            raise XPublishingError("The completed Attention publication is empty.")
        payload = deserialize_attention_home_payload(frame)
        if str(payload.get("run_id")) != run_id:
            raise XPublishingError("The Attention version belongs to another run.")
        account = configured_x_account()
        prepared = prepare_attention_posts(payload, source_version=source_version, account=account)
        state.update(status="queued", event_ids=[post["event_id"] for post in prepared["posts"]])
        write_execution_state("attention_x_completion:" + dataset_namespace(), run_id, state)
        queued_count = sum(post.get("status") == "prepared" for post in XAccountStore(account).records())
        delivered = deliver_attention_posts(account, maximum=queued_count) if queued_count else []
        current = [post for post in XAccountStore(account).records() if post["event_id"] in state["event_ids"]]
        state.update(status="delivered", new_post_count=len(delivered),
                     receipts=[post["x_post_url"] for post in current if post.get("status") == "published"])
        unresolved = [post["event_id"] for post in current if post.get("status") != "published"]
        if unresolved:
            state["status"] = "partial"
            state["unconfirmed_event_ids"] = unresolved
    except Exception as exc:
        state.update(status="pending", error_type=type(exc).__name__)
    write_execution_state("attention_x_completion:" + dataset_namespace(), run_id, state)
    return state
