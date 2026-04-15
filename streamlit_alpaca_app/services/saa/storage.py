from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from services.secrets import build_azure_credential, resolve_secret_value


try:
    import psycopg
except Exception:
    psycopg = None

try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None


UploadBytesFn = Callable[[str, bytes, str], None]
_TRACKING_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src")


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).strip()
    return "" if text.upper() in {"NAN", "<NA>", "NONE", "NULL"} else text


def _slug(value: object, *, default: str = "unknown") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", _coerce_text(value).lower()).strip("-")
    return text or default


def _sha256_text(value: object) -> str:
    return hashlib.sha256(_coerce_text(value).encode("utf-8")).hexdigest()


def _canonical_url(value: object) -> str:
    raw = _coerce_text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query_pairs = []
    for key, item in parse_qsl(parts.query, keep_blank_values=False):
        normalized_key = key.strip().lower()
        if not normalized_key or normalized_key.startswith(_TRACKING_QUERY_PREFIXES):
            continue
        query_pairs.append((key, item))
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def _url_host(value: object) -> str:
    canonical = _canonical_url(value)
    if not canonical:
        return ""
    try:
        return (urlsplit(canonical).netloc or "").lower().strip()
    except Exception:
        return ""


def _normalized_title(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(value).lower()).strip()


def _published_slug(published_date: object, *, asof_time_utc: object) -> str:
    text = _coerce_text(published_date)
    if text:
        return text
    ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.isna(ts):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d")


def _parse_json_text(value: object) -> str:
    text = _coerce_text(value)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def build_canonical_document_fields(row: dict[str, Any]) -> dict[str, str]:
    canonical_url = _canonical_url(row.get("url"))
    raw_text = _coerce_text(row.get("raw_text"))
    title_key = _normalized_title(row.get("title"))
    published_date = _coerce_text(row.get("published_date") or row.get("primary_date"))
    source_provider = _coerce_text(row.get("source_provider") or row.get("search_provider") or row.get("source_kind"))
    content_sha256 = _sha256_text(raw_text)
    provider_payload_json = _parse_json_text(row.get("provider_payload_json"))
    provider_payload_sha256 = _sha256_text(provider_payload_json) if provider_payload_json else ""
    identity_key = (
        canonical_url
        or "|".join(
            part
            for part in (
                _coerce_text(row.get("source_kind")),
                source_provider,
                _coerce_text(row.get("bundle_subject")),
                published_date,
                title_key,
                content_sha256,
            )
            if part
        )
    )
    identity_sha256 = _sha256_text(identity_key)
    return {
        "canonical_document_id": f"saa_doc::{identity_sha256[:24]}",
        "canonical_url": canonical_url,
        "url_host": _url_host(canonical_url or row.get("url")),
        "document_identity_sha256": identity_sha256,
        "document_content_sha256": content_sha256,
        "provider_payload_sha256": provider_payload_sha256,
    }


def _blob_service_client() -> Any | None:
    if BlobServiceClient is None:
        return None
    account_url = (os.getenv("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url:
        return None
    credential = build_azure_credential()
    if credential is None:
        return None
    try:
        return BlobServiceClient(account_url=account_url, credential=credential)
    except Exception:
        return None


def _storage_container() -> str:
    return (os.getenv("AZURE_STORAGE_CONTAINER") or "datasets").strip() or "datasets"


def _read_blob_json(blob_path: str) -> dict[str, Any] | None:
    client = _blob_service_client()
    if client is None or not blob_path:
        return None
    try:
        blob = client.get_blob_client(container=_storage_container(), blob=blob_path)
        payload = blob.download_blob().readall()
        parsed = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _db_connection() -> Any | None:
    conn_str = resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
    except Exception:
        return None


def bootstrap_saa_storage(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_documents (
                canonical_document_id TEXT PRIMARY KEY,
                canonical_url TEXT,
                url_host TEXT,
                title TEXT,
                source_kind TEXT,
                source_provider TEXT,
                search_provider TEXT,
                bundle_subject TEXT,
                source_authority_bucket TEXT,
                authority_rank INTEGER,
                published_at TIMESTAMPTZ,
                published_date TEXT,
                primary_date TEXT,
                document_identity_sha256 TEXT NOT NULL,
                document_content_sha256 TEXT NOT NULL,
                provider_payload_sha256 TEXT,
                raw_text_blob_path TEXT NOT NULL,
                raw_text_chars BIGINT NOT NULL,
                raw_text_origin TEXT,
                last_document_id TEXT,
                last_dataset_name TEXT,
                last_dataset_version_id TEXT,
                last_run_id TEXT,
                last_asof_time_utc TIMESTAMPTZ,
                first_seen_at_utc TIMESTAMPTZ NOT NULL,
                last_seen_at_utc TIMESTAMPTZ NOT NULL,
                mentioned_tickers_json JSONB,
                mentioned_commodities_json JSONB,
                event_tags_json JSONB,
                mentioned_dates_json JSONB,
                metadata_json JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_published_at
            ON saa_documents (published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_bundle_subject
            ON saa_documents (bundle_subject, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_source_provider
            ON saa_documents (source_provider, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_url_host
            ON saa_documents (url_host)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_primary_date
            ON saa_documents (primary_date, published_date)
            """
        )
    conn.commit()


def _retained_blob_path(row: dict[str, Any], *, asof_time_utc: object) -> str:
    published_slug = _published_slug(row.get("published_date"), asof_time_utc=asof_time_utc)
    provider_slug = _slug(row.get("source_provider") or row.get("search_provider") or row.get("source_kind"))
    canonical_document_id = _coerce_text(row.get("canonical_document_id"))
    content_sha256 = _coerce_text(row.get("document_content_sha256"))
    return (
        f"saa/raw_documents/provider={provider_slug}/dt={published_slug}/"
        f"document={canonical_document_id}/content={content_sha256[:24]}.json"
    )


def _retained_document_body(
    row: dict[str, Any],
    *,
    dataset_name: str,
    dataset_version_id: str,
    universe_version: str,
    asof_time_utc: object,
    blob_path: str,
) -> dict[str, Any]:
    return {
        "canonical_document_id": _coerce_text(row.get("canonical_document_id")),
        "document_id": _coerce_text(row.get("document_id")),
        "dataset_name": dataset_name,
        "dataset_version_id": dataset_version_id,
        "run_id": _coerce_text(row.get("run_id")),
        "asof_time_utc": pd.to_datetime(asof_time_utc, utc=True, errors="coerce").isoformat(),
        "universe_version": _coerce_text(universe_version),
        "blob_path": blob_path,
        "canonical_url": _coerce_text(row.get("canonical_url")),
        "url": _coerce_text(row.get("url")),
        "url_host": _coerce_text(row.get("url_host")),
        "title": _coerce_text(row.get("title")),
        "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")) else "",
        "published_date": _coerce_text(row.get("published_date")),
        "primary_date": _coerce_text(row.get("primary_date")),
        "bundle_subject": _coerce_text(row.get("bundle_subject")),
        "source_kind": _coerce_text(row.get("source_kind")),
        "source_provider": _coerce_text(row.get("source_provider")),
        "search_provider": _coerce_text(row.get("search_provider")),
        "source_authority_bucket": _coerce_text(row.get("source_authority_bucket")),
        "authority_rank": int(row.get("authority_rank") or 0),
        "raw_text_origin": _coerce_text(row.get("raw_text_origin")),
        "raw_text_chars": int(row.get("raw_text_chars") or len(_coerce_text(row.get("raw_text")))),
        "document_identity_sha256": _coerce_text(row.get("document_identity_sha256")),
        "document_content_sha256": _coerce_text(row.get("document_content_sha256")),
        "provider_payload_sha256": _coerce_text(row.get("provider_payload_sha256")),
        "mentioned_tickers_json": _coerce_text(row.get("mentioned_tickers_json")),
        "mentioned_commodities_json": _coerce_text(row.get("mentioned_commodities_json")),
        "event_tags_json": _coerce_text(row.get("event_tags_json")),
        "mentioned_dates_json": _coerce_text(row.get("mentioned_dates_json")),
        "query_text": _coerce_text(row.get("query_text")),
        "source_trace": _coerce_text(row.get("source_trace")),
        "raw_text": _coerce_text(row.get("raw_text")),
        "provider_text": _coerce_text(row.get("provider_text")),
        "provider_payload_json": _coerce_text(row.get("provider_payload_json")),
    }


def prepare_retained_source_documents(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    dataset_version_id: str,
    run_id: str,
    asof_time_utc: object,
    universe_version: str,
) -> tuple[pd.DataFrame, list[tuple[str, bytes, str]], list[tuple[Any, ...]]]:
    base = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if base.empty or "raw_text" not in base.columns:
        return base, [], []
    uploads: list[tuple[str, bytes, str]] = []
    records: list[tuple[Any, ...]] = []
    seen_paths: set[str] = set()
    retained_at = datetime.now(timezone.utc)
    retained_at_text = retained_at.isoformat()

    if "run_id" not in base.columns:
        base["run_id"] = run_id
    if "asof_time_utc" not in base.columns:
        base["asof_time_utc"] = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")

    for idx, row in base.iterrows():
        item = dict(row.dropna().to_dict())
        item.update(build_canonical_document_fields(item))
        blob_path = _retained_blob_path(item, asof_time_utc=asof_time_utc)
        body = _retained_document_body(
            item,
            dataset_name=dataset_name,
            dataset_version_id=dataset_version_id,
            universe_version=universe_version,
            asof_time_utc=asof_time_utc,
            blob_path=blob_path,
        )
        base.at[idx, "canonical_document_id"] = item["canonical_document_id"]
        base.at[idx, "canonical_url"] = item["canonical_url"]
        base.at[idx, "url_host"] = item["url_host"]
        base.at[idx, "document_identity_sha256"] = item["document_identity_sha256"]
        base.at[idx, "document_content_sha256"] = item["document_content_sha256"]
        base.at[idx, "provider_payload_sha256"] = item["provider_payload_sha256"]
        base.at[idx, "raw_text_blob_path"] = blob_path
        base.at[idx, "retained_at_utc"] = retained_at_text
        if blob_path not in seen_paths:
            uploads.append((blob_path, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json"))
            seen_paths.add(blob_path)
        records.append(
            (
                item["canonical_document_id"],
                item["canonical_url"] or None,
                item["url_host"] or None,
                _coerce_text(item.get("title")) or None,
                _coerce_text(item.get("source_kind")) or None,
                _coerce_text(item.get("source_provider")) or None,
                _coerce_text(item.get("search_provider")) or None,
                _coerce_text(item.get("bundle_subject")) or None,
                _coerce_text(item.get("source_authority_bucket")) or None,
                int(item.get("authority_rank") or 0),
                pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"),
                _coerce_text(item.get("published_date")) or None,
                _coerce_text(item.get("primary_date")) or None,
                item["document_identity_sha256"],
                item["document_content_sha256"],
                item["provider_payload_sha256"] or None,
                blob_path,
                int(item.get("raw_text_chars") or len(_coerce_text(item.get("raw_text")))),
                _coerce_text(item.get("raw_text_origin")) or None,
                _coerce_text(item.get("document_id")) or None,
                dataset_name,
                dataset_version_id,
                run_id,
                pd.to_datetime(asof_time_utc, utc=True, errors="coerce"),
                retained_at,
                retained_at,
                _parse_json_text(item.get("mentioned_tickers_json")) or "[]",
                _parse_json_text(item.get("mentioned_commodities_json")) or "[]",
                _parse_json_text(item.get("event_tags_json")) or "[]",
                _parse_json_text(item.get("mentioned_dates_json")) or "[]",
                json.dumps(
                    {
                        "query_text": _coerce_text(item.get("query_text")),
                        "source_trace": _coerce_text(item.get("source_trace")),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return base, uploads, records


def _upsert_retained_document_records(conn: Any, records: list[tuple[Any, ...]]) -> None:
    if not records:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO saa_documents (
                canonical_document_id, canonical_url, url_host, title, source_kind,
                source_provider, search_provider, bundle_subject, source_authority_bucket,
                authority_rank, published_at, published_date, primary_date,
                document_identity_sha256, document_content_sha256, provider_payload_sha256,
                raw_text_blob_path, raw_text_chars, raw_text_origin, last_document_id,
                last_dataset_name, last_dataset_version_id, last_run_id, last_asof_time_utc,
                first_seen_at_utc, last_seen_at_utc, mentioned_tickers_json,
                mentioned_commodities_json, event_tags_json, mentioned_dates_json,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s::jsonb,
                %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb
            )
            ON CONFLICT (canonical_document_id) DO UPDATE SET
                canonical_url = COALESCE(EXCLUDED.canonical_url, saa_documents.canonical_url),
                url_host = COALESCE(EXCLUDED.url_host, saa_documents.url_host),
                title = COALESCE(EXCLUDED.title, saa_documents.title),
                source_kind = COALESCE(EXCLUDED.source_kind, saa_documents.source_kind),
                source_provider = COALESCE(EXCLUDED.source_provider, saa_documents.source_provider),
                search_provider = COALESCE(EXCLUDED.search_provider, saa_documents.search_provider),
                bundle_subject = COALESCE(EXCLUDED.bundle_subject, saa_documents.bundle_subject),
                source_authority_bucket = COALESCE(EXCLUDED.source_authority_bucket, saa_documents.source_authority_bucket),
                authority_rank = CASE
                    WHEN EXCLUDED.authority_rank > 0 THEN EXCLUDED.authority_rank
                    ELSE saa_documents.authority_rank
                END,
                published_at = COALESCE(EXCLUDED.published_at, saa_documents.published_at),
                published_date = COALESCE(EXCLUDED.published_date, saa_documents.published_date),
                primary_date = COALESCE(EXCLUDED.primary_date, saa_documents.primary_date),
                document_identity_sha256 = EXCLUDED.document_identity_sha256,
                document_content_sha256 = EXCLUDED.document_content_sha256,
                provider_payload_sha256 = COALESCE(EXCLUDED.provider_payload_sha256, saa_documents.provider_payload_sha256),
                raw_text_blob_path = EXCLUDED.raw_text_blob_path,
                raw_text_chars = EXCLUDED.raw_text_chars,
                raw_text_origin = COALESCE(EXCLUDED.raw_text_origin, saa_documents.raw_text_origin),
                last_document_id = COALESCE(EXCLUDED.last_document_id, saa_documents.last_document_id),
                last_dataset_name = EXCLUDED.last_dataset_name,
                last_dataset_version_id = EXCLUDED.last_dataset_version_id,
                last_run_id = EXCLUDED.last_run_id,
                last_asof_time_utc = EXCLUDED.last_asof_time_utc,
                last_seen_at_utc = EXCLUDED.last_seen_at_utc,
                mentioned_tickers_json = EXCLUDED.mentioned_tickers_json,
                mentioned_commodities_json = EXCLUDED.mentioned_commodities_json,
                event_tags_json = EXCLUDED.event_tags_json,
                mentioned_dates_json = EXCLUDED.mentioned_dates_json,
                metadata_json = EXCLUDED.metadata_json
            """,
            records,
        )
    conn.commit()


def persist_retained_source_documents(
    dataset_name: str,
    frame: pd.DataFrame,
    *,
    dataset_version_id: str,
    run_id: str,
    asof_time_utc: object,
    universe_version: str,
    conn: Any | None,
    upload_bytes_fn: UploadBytesFn,
) -> pd.DataFrame:
    prepared_frame, uploads, records = prepare_retained_source_documents(
        frame,
        dataset_name=dataset_name,
        dataset_version_id=dataset_version_id,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        universe_version=universe_version,
    )
    for path, payload, content_type in uploads:
        upload_bytes_fn(path, payload, content_type)
    if conn is not None and records:
        _upsert_retained_document_records(conn, records)
    return prepared_frame


def load_retained_document(
    canonical_document_id: str,
    *,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    normalized_id = _coerce_text(canonical_document_id)
    if not normalized_id:
        return None
    active_conn = conn
    should_close = False
    if active_conn is None:
        active_conn = _db_connection()
        should_close = active_conn is not None
    if active_conn is None:
        return None
    try:
        with active_conn.cursor() as cur:
            cur.execute(
                """
                SELECT raw_text_blob_path
                FROM saa_documents
                WHERE canonical_document_id = %s
                """,
                (normalized_id,),
            )
            row = cur.fetchone()
    finally:
        if should_close:
            active_conn.close()
    blob_path = _coerce_text(row[0]) if row else ""
    if not blob_path:
        return None
    return _read_blob_json(blob_path)


__all__ = [
    "bootstrap_saa_storage",
    "build_canonical_document_fields",
    "load_retained_document",
    "persist_retained_source_documents",
    "prepare_retained_source_documents",
]
