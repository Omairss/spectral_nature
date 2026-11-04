"""Publish existing, reviewed Attention headlines through the owned X browser."""
from __future__ import annotations

from typing import Any

from services.x_publishing import SECTIONS, XAccountStore, XBrowser, XPublishingError


def pending_attention_publications(account: str, latest: Any) -> list[Any]:
    """Catch up every indexed publication since this account's activation.

    Persist the starting boundary once; a restart cannot silently jump to the
    newest version. Completed batches are the acknowledgement, not a cursor
    advanced before their content has been retained.
    """
    from services.pipeline_store import PipelineDataset, dataset_storage_name

    with XAccountStore(account) as store:
        with store._db() as cur:
            cur.execute("""UPDATE x_publishing_accounts
                SET attention_started_at=coalesce(attention_started_at,%s::timestamptz)
                WHERE account=%s AND owner=%s AND lease_until>clock_timestamp()
                RETURNING attention_started_at""",
                (latest.ingested_at_utc, store.account.lower(), store.owner))
            row = cur.fetchone()
            if row is None:
                raise XPublishingError("The publication lease was lost before activation.")
            cur.execute("""SELECT d.dataset_version_id,d.blob_path,d.asof_time_utc,
                    d.ingested_at_utc,d.row_count
                FROM dataset_versions d LEFT JOIN x_publishing_batches b
                  ON b.account=%s AND b.source_version=d.dataset_version_id
                WHERE d.dataset_name=%s AND d.status='ready' AND d.ingested_at_utc>=%s
                  AND coalesce(b.payload->>'status','') NOT IN ('prepared','empty')
                ORDER BY d.ingested_at_utc,d.dataset_version_id""",
                (store.account.lower(), dataset_storage_name("attention_home_snapshots_1d"), row[0]))
            return [PipelineDataset(
                dataset_name="attention_home_snapshots_1d", dataset_version_id=str(item[0]),
                blob_path=str(item[1]), asof_time_utc=str(item[2]),
                ingested_at_utc=str(item[3]), row_count=int(item[4] or 0),
            ) for item in cur.fetchall()]


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
    """Bounded explicit delivery; pending/uncertain submissions stop the account."""
    if maximum < 1:
        raise XPublishingError("A positive delivery bound is required.")
    with XAccountStore(account) as store:
        records = store.records()
        if any(record.get("status") in {"submitting","uncertain"} for record in records):
            raise XPublishingError("Reconcile the account's unresolved submission before delivery.")
        pending = sorted((record for record in records if record.get("status") == "prepared"),
                         key=lambda record:(record.get("source_as_of") or "",record["event_id"]))[:maximum]
        if not pending:
            return []
        with XBrowser(store,headless=headless) as browser:
            return [browser.publish(record) for record in pending]
