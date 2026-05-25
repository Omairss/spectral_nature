from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from services.llm import load_embedding_client
from services.secrets import build_azure_credential, postgres_connect_timeout_seconds, resolve_secret_value


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
_DISPLAY_EXCERPT_MAX_CHARS = 280
_SEARCH_TEXT_MAX_CHARS = 6000
_DEFAULT_SEARCH_SCAN_LIMIT = 500
_DEFAULT_SEMANTIC_SCAN_LIMIT = 160
_DEFAULT_SEMANTIC_THRESHOLD = 0.72


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


def _trim(value: object, limit: int) -> str:
    text = _coerce_text(value)
    if not text or len(text) <= max(int(limit), 1):
        return text
    return text[: max(int(limit), 1) - 1].rstrip() + "…"


def _display_excerpt(row: dict[str, Any]) -> str:
    excerpt = _coerce_text(row.get("display_excerpt"))
    if excerpt:
        return _trim(excerpt, _DISPLAY_EXCERPT_MAX_CHARS)
    raw_text = _coerce_text(row.get("raw_text"))
    title = _coerce_text(row.get("title"))
    if raw_text:
        return _trim(raw_text, _DISPLAY_EXCERPT_MAX_CHARS)
    return _trim(title, _DISPLAY_EXCERPT_MAX_CHARS)


def _search_text(row: dict[str, Any]) -> str:
    merged = "\n\n".join(part for part in (_coerce_text(row.get("title")), _coerce_text(row.get("raw_text"))) if part)
    return _trim(merged, _SEARCH_TEXT_MAX_CHARS)


def _json_key_from_text(value: object) -> str:
    text = _coerce_text(value)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return ""
    if not isinstance(parsed, list):
        return ""
    clean = [str(item).strip() for item in parsed if str(item).strip()]
    if not clean:
        return ""
    return "|" + "|".join(clean) + "|"


def _safe_json_list(value: object) -> list[str]:
    text = _coerce_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _safe_json_vector(value: object) -> list[float]:
    text = _coerce_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    vector: list[float] = []
    for item in parsed:
        try:
            vector.append(float(item))
        except Exception:
            return []
    return vector


def _safe_datetime_series(values: pd.Series) -> pd.Series:
    def normalize(value: object) -> str:
        text = _coerce_text(value)
        if not text:
            return ""
        match = re.match(r"^([+-]?\d{1,})-", text)
        if match:
            try:
                year = int(match.group(1))
            except Exception:
                return ""
            if year < 1 or year > 9999:
                return ""
        return text

    return pd.to_datetime(values.map(normalize), utc=True, errors="coerce", format="mixed")


def _normalized_tokens(value: object) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _coerce_text(value).lower()) if len(token) >= 2]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(l * r for l, r in zip(left, right))
    left_norm = math.sqrt(sum(l * l for l in left))
    right_norm = math.sqrt(sum(r * r for r in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


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
        return psycopg.connect(conn_str, connect_timeout=postgres_connect_timeout_seconds())
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
                display_excerpt TEXT,
                search_text TEXT,
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
                mentioned_tickers_key TEXT,
                mentioned_commodities_json JSONB,
                mentioned_commodities_key TEXT,
                event_tags_json JSONB,
                event_tags_key TEXT,
                mentioned_dates_json JSONB,
                mentioned_dates_key TEXT,
                metadata_json JSONB
            )
            """
        )
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS display_excerpt TEXT")
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS search_text TEXT")
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS mentioned_tickers_key TEXT")
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS mentioned_commodities_key TEXT")
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS event_tags_key TEXT")
        cur.execute("ALTER TABLE saa_documents ADD COLUMN IF NOT EXISTS mentioned_dates_key TEXT")
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
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_ticker_key
            ON saa_documents (mentioned_tickers_key)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_documents_event_key
            ON saa_documents (event_tags_key)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_evidence_chunks (
                chunk_record_id TEXT PRIMARY KEY,
                chunk_identity_sha256 TEXT NOT NULL,
                canonical_document_id TEXT,
                document_id TEXT,
                chunk_id TEXT,
                chunk_index INTEGER,
                document_chunk_count INTEGER,
                title TEXT,
                display_excerpt TEXT,
                chunk_text TEXT,
                search_text TEXT,
                bundle_subject TEXT,
                source_kind TEXT,
                source_provider TEXT,
                search_provider TEXT,
                research_scope TEXT,
                source_authority_bucket TEXT,
                authority_rank INTEGER,
                published_at TIMESTAMPTZ,
                published_date TEXT,
                primary_date TEXT,
                raw_text_origin TEXT,
                raw_text_chars BIGINT,
                dataset_name TEXT NOT NULL,
                dataset_version_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                asof_time_utc TIMESTAMPTZ NOT NULL,
                embedding_model TEXT,
                embedding_vector_json TEXT,
                mentioned_tickers_json JSONB,
                mentioned_tickers_key TEXT,
                mentioned_commodities_json JSONB,
                mentioned_commodities_key TEXT,
                event_tags_json JSONB,
                event_tags_key TEXT,
                mentioned_dates_json JSONB,
                mentioned_dates_key TEXT,
                metadata_json JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_published_at
            ON saa_evidence_chunks (published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_bundle_subject
            ON saa_evidence_chunks (bundle_subject, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_run_id
            ON saa_evidence_chunks (run_id, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_document
            ON saa_evidence_chunks (canonical_document_id, chunk_index)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_provider
            ON saa_evidence_chunks (source_provider, search_provider, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_research_scope
            ON saa_evidence_chunks (research_scope, published_at DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_ticker_key
            ON saa_evidence_chunks (mentioned_tickers_key)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_event_key
            ON saa_evidence_chunks (event_tags_key)
            """
        )
        cur.execute("ALTER TABLE saa_evidence_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT")
        cur.execute("ALTER TABLE saa_evidence_chunks ADD COLUMN IF NOT EXISTS embedding_vector_json TEXT")
        # tsvector full-text search column — generated from search_text, title, chunk_text
        cur.execute(
            """
            ALTER TABLE saa_evidence_chunks
            ADD COLUMN IF NOT EXISTS search_tsvector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(display_excerpt, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(chunk_text, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(search_text, '')), 'C')
            ) STORED
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_chunks_search_tsvector
            ON saa_evidence_chunks USING GIN (search_tsvector)
            """
        )
        try:
            from .zopedia import bootstrap_zopedia_storage

            bootstrap_zopedia_storage(conn, commit=False)
        except Exception:
            pass
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
        "display_excerpt": _display_excerpt(row),
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
        item["display_excerpt"] = _display_excerpt(item)
        item["search_text"] = _search_text(item)
        item["mentioned_tickers_key"] = _coerce_text(item.get("mentioned_tickers_key")) or _json_key_from_text(item.get("mentioned_tickers_json"))
        item["mentioned_commodities_key"] = _coerce_text(item.get("mentioned_commodities_key")) or _json_key_from_text(item.get("mentioned_commodities_json"))
        item["event_tags_key"] = _coerce_text(item.get("event_tags_key")) or _json_key_from_text(item.get("event_tags_json"))
        item["mentioned_dates_key"] = _coerce_text(item.get("mentioned_dates_key")) or _json_key_from_text(item.get("mentioned_dates_json"))
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
        base.at[idx, "display_excerpt"] = item["display_excerpt"]
        base.at[idx, "search_text"] = item["search_text"]
        base.at[idx, "document_identity_sha256"] = item["document_identity_sha256"]
        base.at[idx, "document_content_sha256"] = item["document_content_sha256"]
        base.at[idx, "provider_payload_sha256"] = item["provider_payload_sha256"]
        base.at[idx, "mentioned_tickers_key"] = item["mentioned_tickers_key"]
        base.at[idx, "mentioned_commodities_key"] = item["mentioned_commodities_key"]
        base.at[idx, "event_tags_key"] = item["event_tags_key"]
        base.at[idx, "mentioned_dates_key"] = item["mentioned_dates_key"]
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
                _coerce_text(item.get("display_excerpt")) or None,
                _coerce_text(item.get("search_text")) or None,
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
                _coerce_text(item.get("mentioned_tickers_key")) or None,
                _parse_json_text(item.get("mentioned_commodities_json")) or "[]",
                _coerce_text(item.get("mentioned_commodities_key")) or None,
                _parse_json_text(item.get("event_tags_json")) or "[]",
                _coerce_text(item.get("event_tags_key")) or None,
                _parse_json_text(item.get("mentioned_dates_json")) or "[]",
                _coerce_text(item.get("mentioned_dates_key")) or None,
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
                canonical_document_id, canonical_url, url_host, title, display_excerpt,
                search_text, source_kind, source_provider, search_provider, bundle_subject, source_authority_bucket,
                authority_rank, published_at, published_date, primary_date,
                document_identity_sha256, document_content_sha256, provider_payload_sha256,
                raw_text_blob_path, raw_text_chars, raw_text_origin, last_document_id,
                last_dataset_name, last_dataset_version_id, last_run_id, last_asof_time_utc,
                first_seen_at_utc, last_seen_at_utc, mentioned_tickers_json, mentioned_tickers_key,
                mentioned_commodities_json, mentioned_commodities_key, event_tags_json, event_tags_key, mentioned_dates_json, mentioned_dates_key,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s,
                %s::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s,
                %s::jsonb
            )
            ON CONFLICT (canonical_document_id) DO UPDATE SET
                canonical_url = COALESCE(EXCLUDED.canonical_url, saa_documents.canonical_url),
                url_host = COALESCE(EXCLUDED.url_host, saa_documents.url_host),
                title = COALESCE(EXCLUDED.title, saa_documents.title),
                display_excerpt = COALESCE(EXCLUDED.display_excerpt, saa_documents.display_excerpt),
                search_text = COALESCE(EXCLUDED.search_text, saa_documents.search_text),
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
                mentioned_tickers_key = COALESCE(EXCLUDED.mentioned_tickers_key, saa_documents.mentioned_tickers_key),
                mentioned_commodities_json = EXCLUDED.mentioned_commodities_json,
                mentioned_commodities_key = COALESCE(EXCLUDED.mentioned_commodities_key, saa_documents.mentioned_commodities_key),
                event_tags_json = EXCLUDED.event_tags_json,
                event_tags_key = COALESCE(EXCLUDED.event_tags_key, saa_documents.event_tags_key),
                mentioned_dates_json = EXCLUDED.mentioned_dates_json,
                mentioned_dates_key = COALESCE(EXCLUDED.mentioned_dates_key, saa_documents.mentioned_dates_key),
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


def _chunk_search_text(row: dict[str, Any]) -> str:
    merged = "\n\n".join(
        part
        for part in (
            _coerce_text(row.get("title")),
            _coerce_text(row.get("display_excerpt")),
            _coerce_text(row.get("chunk_text")),
        )
        if part
    )
    return _trim(merged, _SEARCH_TEXT_MAX_CHARS)


def prepare_retained_evidence_chunks(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    dataset_version_id: str,
    run_id: str,
    asof_time_utc: object,
) -> tuple[pd.DataFrame, list[tuple[Any, ...]]]:
    base = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if base.empty or "chunk_text" not in base.columns:
        return base, []

    if "run_id" not in base.columns:
        base["run_id"] = run_id
    if "asof_time_utc" not in base.columns:
        base["asof_time_utc"] = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")

    records: list[tuple[Any, ...]] = []
    for idx, row in base.iterrows():
        item = dict(row.dropna().to_dict())
        chunk_text = _coerce_text(item.get("chunk_text"))
        if not chunk_text:
            continue
        title = _coerce_text(item.get("title"))
        display_excerpt = _trim(_coerce_text(item.get("display_excerpt")) or chunk_text, _DISPLAY_EXCERPT_MAX_CHARS)
        search_text = _chunk_search_text({**item, "display_excerpt": display_excerpt})
        chunk_identity_sha256 = _sha256_text(
            "|".join(
                part
                for part in (
                    _coerce_text(item.get("canonical_document_id")),
                    _coerce_text(item.get("chunk_index")),
                    chunk_text,
                )
                if part
            )
        )
        chunk_record_key = "|".join(
            part
            for part in (
                dataset_version_id,
                run_id,
                _coerce_text(item.get("chunk_id")),
                chunk_identity_sha256,
            )
            if part
        )
        chunk_record_id = f"saa_chunk::{_sha256_text(chunk_record_key)[:24]}"
        mentioned_tickers_key = _coerce_text(item.get("mentioned_tickers_key")) or _json_key_from_text(item.get("mentioned_tickers_json"))
        mentioned_commodities_key = _coerce_text(item.get("mentioned_commodities_key")) or _json_key_from_text(item.get("mentioned_commodities_json"))
        event_tags_key = _coerce_text(item.get("event_tags_key")) or _json_key_from_text(item.get("event_tags_json"))
        mentioned_dates_key = _coerce_text(item.get("mentioned_dates_key")) or _json_key_from_text(item.get("mentioned_dates_json"))

        base.at[idx, "chunk_record_id"] = chunk_record_id
        base.at[idx, "chunk_identity_sha256"] = chunk_identity_sha256
        base.at[idx, "display_excerpt"] = display_excerpt
        base.at[idx, "search_text"] = search_text
        base.at[idx, "mentioned_tickers_key"] = mentioned_tickers_key
        base.at[idx, "mentioned_commodities_key"] = mentioned_commodities_key
        base.at[idx, "event_tags_key"] = event_tags_key
        base.at[idx, "mentioned_dates_key"] = mentioned_dates_key

        records.append(
            (
                chunk_record_id,
                chunk_identity_sha256,
                _coerce_text(item.get("canonical_document_id")) or None,
                _coerce_text(item.get("document_id")) or None,
                _coerce_text(item.get("chunk_id")) or None,
                int(item.get("chunk_index") or 0),
                int(item.get("document_chunk_count") or 0),
                title or None,
                display_excerpt or None,
                chunk_text,
                search_text or None,
                _coerce_text(item.get("bundle_subject")) or None,
                _coerce_text(item.get("source_kind")) or None,
                _coerce_text(item.get("source_provider")) or None,
                _coerce_text(item.get("search_provider")) or None,
                _coerce_text(item.get("research_scope")) or None,
                _coerce_text(item.get("source_authority_bucket")) or None,
                int(item.get("authority_rank") or 0),
                pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"),
                _coerce_text(item.get("published_date")) or None,
                _coerce_text(item.get("primary_date")) or None,
                _coerce_text(item.get("raw_text_origin")) or None,
                int(item.get("raw_text_chars") or 0),
                dataset_name,
                dataset_version_id,
                run_id,
                pd.to_datetime(asof_time_utc, utc=True, errors="coerce"),
                _coerce_text(item.get("embedding_model")) or None,
                _coerce_text(item.get("embedding_vector_json")) or None,
                _parse_json_text(item.get("mentioned_tickers_json")) or "[]",
                mentioned_tickers_key or None,
                _parse_json_text(item.get("mentioned_commodities_json")) or "[]",
                mentioned_commodities_key or None,
                _parse_json_text(item.get("event_tags_json")) or "[]",
                event_tags_key or None,
                _parse_json_text(item.get("mentioned_dates_json")) or "[]",
                mentioned_dates_key or None,
                json.dumps(
                    {
                        "query_text": _coerce_text(item.get("query_text")),
                        "source_trace": _coerce_text(item.get("source_trace")),
                        "document_content_sha256": _coerce_text(item.get("document_content_sha256")),
                        "provider_payload_sha256": _coerce_text(item.get("provider_payload_sha256")),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return base, records


def _upsert_retained_evidence_chunk_records(conn: Any, records: list[tuple[Any, ...]]) -> None:
    if not records:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO saa_evidence_chunks (
                chunk_record_id, chunk_identity_sha256, canonical_document_id, document_id,
                chunk_id, chunk_index, document_chunk_count, title, display_excerpt,
                chunk_text, search_text, bundle_subject, source_kind, source_provider,
                search_provider, research_scope, source_authority_bucket, authority_rank,
                published_at, published_date, primary_date, raw_text_origin, raw_text_chars,
                dataset_name, dataset_version_id, run_id, asof_time_utc, embedding_model, embedding_vector_json,
                mentioned_tickers_json, mentioned_tickers_key, mentioned_commodities_json,
                mentioned_commodities_key, event_tags_json, event_tags_key,
                mentioned_dates_json, mentioned_dates_key, metadata_json
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s::jsonb,
                %s, %s::jsonb, %s,
                %s::jsonb, %s, %s::jsonb
            )
            ON CONFLICT (chunk_record_id) DO UPDATE SET
                chunk_identity_sha256 = EXCLUDED.chunk_identity_sha256,
                canonical_document_id = COALESCE(EXCLUDED.canonical_document_id, saa_evidence_chunks.canonical_document_id),
                document_id = COALESCE(EXCLUDED.document_id, saa_evidence_chunks.document_id),
                chunk_id = COALESCE(EXCLUDED.chunk_id, saa_evidence_chunks.chunk_id),
                chunk_index = CASE
                    WHEN EXCLUDED.chunk_index > 0 THEN EXCLUDED.chunk_index
                    ELSE saa_evidence_chunks.chunk_index
                END,
                document_chunk_count = CASE
                    WHEN EXCLUDED.document_chunk_count > 0 THEN EXCLUDED.document_chunk_count
                    ELSE saa_evidence_chunks.document_chunk_count
                END,
                title = COALESCE(EXCLUDED.title, saa_evidence_chunks.title),
                display_excerpt = COALESCE(EXCLUDED.display_excerpt, saa_evidence_chunks.display_excerpt),
                chunk_text = EXCLUDED.chunk_text,
                search_text = COALESCE(EXCLUDED.search_text, saa_evidence_chunks.search_text),
                bundle_subject = COALESCE(EXCLUDED.bundle_subject, saa_evidence_chunks.bundle_subject),
                source_kind = COALESCE(EXCLUDED.source_kind, saa_evidence_chunks.source_kind),
                source_provider = COALESCE(EXCLUDED.source_provider, saa_evidence_chunks.source_provider),
                search_provider = COALESCE(EXCLUDED.search_provider, saa_evidence_chunks.search_provider),
                research_scope = COALESCE(EXCLUDED.research_scope, saa_evidence_chunks.research_scope),
                source_authority_bucket = COALESCE(EXCLUDED.source_authority_bucket, saa_evidence_chunks.source_authority_bucket),
                authority_rank = CASE
                    WHEN EXCLUDED.authority_rank > 0 THEN EXCLUDED.authority_rank
                    ELSE saa_evidence_chunks.authority_rank
                END,
                published_at = COALESCE(EXCLUDED.published_at, saa_evidence_chunks.published_at),
                published_date = COALESCE(EXCLUDED.published_date, saa_evidence_chunks.published_date),
                primary_date = COALESCE(EXCLUDED.primary_date, saa_evidence_chunks.primary_date),
                raw_text_origin = COALESCE(EXCLUDED.raw_text_origin, saa_evidence_chunks.raw_text_origin),
                raw_text_chars = CASE
                    WHEN EXCLUDED.raw_text_chars > 0 THEN EXCLUDED.raw_text_chars
                    ELSE saa_evidence_chunks.raw_text_chars
                END,
                dataset_name = EXCLUDED.dataset_name,
                dataset_version_id = EXCLUDED.dataset_version_id,
                run_id = EXCLUDED.run_id,
                asof_time_utc = EXCLUDED.asof_time_utc,
                embedding_model = COALESCE(EXCLUDED.embedding_model, saa_evidence_chunks.embedding_model),
                embedding_vector_json = COALESCE(EXCLUDED.embedding_vector_json, saa_evidence_chunks.embedding_vector_json),
                mentioned_tickers_json = EXCLUDED.mentioned_tickers_json,
                mentioned_tickers_key = COALESCE(EXCLUDED.mentioned_tickers_key, saa_evidence_chunks.mentioned_tickers_key),
                mentioned_commodities_json = EXCLUDED.mentioned_commodities_json,
                mentioned_commodities_key = COALESCE(EXCLUDED.mentioned_commodities_key, saa_evidence_chunks.mentioned_commodities_key),
                event_tags_json = EXCLUDED.event_tags_json,
                event_tags_key = COALESCE(EXCLUDED.event_tags_key, saa_evidence_chunks.event_tags_key),
                mentioned_dates_json = EXCLUDED.mentioned_dates_json,
                mentioned_dates_key = COALESCE(EXCLUDED.mentioned_dates_key, saa_evidence_chunks.mentioned_dates_key),
                metadata_json = EXCLUDED.metadata_json
            """,
            records,
        )
    conn.commit()


def persist_retained_evidence_chunks(
    dataset_name: str,
    frame: pd.DataFrame,
    *,
    dataset_version_id: str,
    run_id: str,
    asof_time_utc: object,
    conn: Any | None,
) -> pd.DataFrame:
    prepared_frame, records = prepare_retained_evidence_chunks(
        frame,
        dataset_name=dataset_name,
        dataset_version_id=dataset_version_id,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    if conn is not None and records:
        _upsert_retained_evidence_chunk_records(conn, records)
    return prepared_frame


def persist_agent_research_evidence(
    *,
    run_id: str,
    query: str,
    answer: str,
    claims: list[str],
    symbols: list[str],
) -> pd.DataFrame:
    """Persist agent findings as retained evidence for future searches."""
    conn = _db_connection()
    if conn is None:
        return pd.DataFrame()

    now = datetime.now(timezone.utc)
    normalized_symbols = [str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()]
    tickers_json = json.dumps(normalized_symbols, ensure_ascii=False) if normalized_symbols else None
    tickers_key = "|".join(normalized_symbols) if normalized_symbols else None
    rows: list[dict[str, Any]] = []

    if _coerce_text(answer):
        rows.append(
            {
                "chunk_text": f"Agent research on: {query}\n\n{answer}",
                "title": f"Agent: {query[:120]}",
                "display_excerpt": answer[:280],
                "source_kind": "agent_research",
                "source_provider": "omnibar_agent",
                "research_scope": "agent_answer",
                "bundle_subject": query[:200],
                "published_at": now,
                "published_date": now.strftime("%Y-%m-%d"),
                "primary_date": now.strftime("%Y-%m-%d"),
                "mentioned_tickers_json": tickers_json,
                "mentioned_tickers_key": tickers_key,
            }
        )

    for index, claim in enumerate(claims):
        clean_claim = _coerce_text(claim)
        if not clean_claim:
            continue
        rows.append(
            {
                "chunk_text": clean_claim,
                "title": f"Agent evidence ({index + 1}/{len(claims)}): {query[:80]}",
                "display_excerpt": clean_claim[:280],
                "source_kind": "agent_research",
                "source_provider": "omnibar_agent",
                "research_scope": "agent_evidence",
                "bundle_subject": query[:200],
                "published_at": now,
                "published_date": now.strftime("%Y-%m-%d"),
                "primary_date": now.strftime("%Y-%m-%d"),
                "mentioned_tickers_json": tickers_json,
                "mentioned_tickers_key": tickers_key,
            }
        )

    if not rows:
        conn.close()
        return pd.DataFrame()

    try:
        return persist_retained_evidence_chunks(
            "agent_research",
            pd.DataFrame(rows),
            dataset_version_id=run_id,
            run_id=run_id,
            asof_time_utc=now,
            conn=conn,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _frame_from_retained_chunk_rows(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    columns = [
        "chunk_record_id",
        "chunk_identity_sha256",
        "canonical_document_id",
        "document_id",
        "chunk_id",
        "chunk_index",
        "document_chunk_count",
        "title",
        "display_excerpt",
        "chunk_text",
        "search_text",
        "bundle_subject",
        "source_kind",
        "source_provider",
        "search_provider",
        "research_scope",
        "source_authority_bucket",
        "authority_rank",
        "published_at",
        "published_date",
        "primary_date",
        "raw_text_origin",
        "raw_text_chars",
        "dataset_name",
        "dataset_version_id",
        "run_id",
        "asof_time_utc",
        "embedding_model",
        "embedding_vector_json",
        "mentioned_tickers_json",
        "mentioned_tickers_key",
        "mentioned_commodities_json",
        "mentioned_commodities_key",
        "event_tags_json",
        "event_tags_key",
        "mentioned_dates_json",
        "mentioned_dates_key",
        "metadata_json",
        "ts_rank_score",
    ]
    normalized_rows = [
        tuple(list(row) + [None] * max(len(columns) - len(row), 0))[: len(columns)]
        for row in rows
    ]
    frame = pd.DataFrame(normalized_rows, columns=columns)
    if frame.empty:
        return frame
    for column in ("published_at", "asof_time_utc"):
        frame[column] = _safe_datetime_series(frame[column])
    for json_column, list_column in (
        ("mentioned_tickers_json", "mentioned_tickers"),
        ("mentioned_commodities_json", "mentioned_commodities"),
        ("event_tags_json", "event_tags"),
        ("mentioned_dates_json", "mentioned_dates"),
    ):
        frame[list_column] = frame[json_column].map(_safe_json_list)
    return frame


def _ensure_searchable_chunk_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if out.empty:
        return out
    defaults: dict[str, Any] = {
        "chunk_record_id": "",
        "chunk_id": "",
        "chunk_index": 0,
        "document_chunk_count": 0,
        "title": "",
        "display_excerpt": "",
        "chunk_text": "",
        "search_text": "",
        "bundle_subject": "",
        "source_kind": "",
        "source_provider": "",
        "search_provider": "",
        "research_scope": "",
        "source_authority_bucket": "",
        "authority_rank": 0,
        "published_at": pd.NaT,
        "published_date": "",
        "primary_date": "",
        "raw_text_origin": "",
        "raw_text_chars": 0,
        "dataset_name": "",
        "dataset_version_id": "",
        "run_id": "",
        "asof_time_utc": pd.NaT,
        "embedding_model": "",
        "embedding_vector_json": "",
        "mentioned_tickers_json": "[]",
        "mentioned_tickers_key": "",
        "mentioned_commodities_json": "[]",
        "mentioned_commodities_key": "",
        "event_tags_json": "[]",
        "event_tags_key": "",
        "mentioned_dates_json": "[]",
        "mentioned_dates_key": "",
        "metadata_json": "{}",
    }
    for column, default in defaults.items():
        if column not in out.columns:
            out[column] = default
    if "canonical_document_id" not in out.columns:
        out["canonical_document_id"] = ""
    if "document_id" not in out.columns:
        out["document_id"] = ""
    if "chunk_record_id" in out.columns:
        out["chunk_record_id"] = [
            _coerce_text(record_id) or _coerce_text(chunk_id) or f"chunk::{idx + 1}"
            for idx, (record_id, chunk_id) in enumerate(zip(out["chunk_record_id"], out["chunk_id"]))
        ]
    if "search_text" in out.columns:
        out["search_text"] = [
            _coerce_text(value) or _chunk_search_text(dict(row.dropna().to_dict()))
            for value, (_, row) in zip(out["search_text"], out.iterrows())
        ]
    for json_column, key_column in (
        ("mentioned_tickers_json", "mentioned_tickers_key"),
        ("mentioned_commodities_json", "mentioned_commodities_key"),
        ("event_tags_json", "event_tags_key"),
        ("mentioned_dates_json", "mentioned_dates_key"),
    ):
        out[key_column] = [
            _coerce_text(value) or _json_key_from_text(json_value)
            for value, json_value in zip(out[key_column], out[json_column])
        ]
    for column in ("published_at", "asof_time_utc"):
        out[column] = pd.to_datetime(out[column], utc=True, errors="coerce")
    for json_column, list_column in (
        ("mentioned_tickers_json", "mentioned_tickers"),
        ("mentioned_commodities_json", "mentioned_commodities"),
        ("event_tags_json", "event_tags"),
        ("mentioned_dates_json", "mentioned_dates"),
    ):
        out[list_column] = out[json_column].map(_safe_json_list)
    return out


def _filter_and_score_retained_chunk_frame(
    frame: pd.DataFrame,
    *,
    query: str = "",
    tickers: list[str] | None = None,
    commodities: list[str] | None = None,
    event_tags: list[str] | None = None,
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_kinds: list[str] | None = None,
    providers: list[str] | None = None,
    research_scopes: list[str] | None = None,
    run_id: str | None = None,
    canonical_document_id: str | None = None,
    limit: int = 20,
    use_semantic: bool = True,
    embedding_client: Any | None = None,
) -> pd.DataFrame:
    out = _ensure_searchable_chunk_frame(frame)
    if out.empty:
        return out

    normalized_run_id = _coerce_text(run_id)
    normalized_document_id = _coerce_text(canonical_document_id)
    normalized_source_kinds = [item.lower() for item in list(source_kinds or []) if _coerce_text(item)]
    normalized_providers = [item.lower() for item in list(providers or []) if _coerce_text(item)]
    normalized_scopes = [item.lower() for item in list(research_scopes or []) if _coerce_text(item)]
    normalized_tickers = [item.upper() for item in list(tickers or []) if _coerce_text(item)]
    normalized_commodities = [item.lower() for item in list(commodities or []) if _coerce_text(item)]
    normalized_event_tags = [item.lower() for item in list(event_tags or []) if _coerce_text(item)]
    exact_dates = [_coerce_text(item) for item in list(dates or []) if _coerce_text(item)]
    query_text = _coerce_text(query)
    query_tokens = [token for token in _normalized_tokens(query_text) if len(token) >= 3]

    if normalized_run_id:
        out = out[out["run_id"].astype(str) == normalized_run_id].copy()
    if normalized_document_id:
        out = out[out["canonical_document_id"].astype(str) == normalized_document_id].copy()
    if normalized_source_kinds:
        out = out[out["source_kind"].astype(str).str.lower().isin(set(normalized_source_kinds))].copy()
    if normalized_providers:
        provider_set = set(normalized_providers)
        out = out[
            out["source_provider"].astype(str).str.lower().isin(provider_set)
            | out["search_provider"].astype(str).str.lower().isin(provider_set)
        ].copy()
    if normalized_scopes:
        out = out[out["research_scope"].astype(str).str.lower().isin(set(normalized_scopes))].copy()
    if normalized_tickers:
        ticker_set = set(normalized_tickers)
        out = out[
            out["bundle_subject"].astype(str).str.upper().isin(ticker_set)
            | out["mentioned_tickers_key"].map(lambda value: _contains_any_key(str(value).upper(), normalized_tickers))
        ].copy()
    if normalized_commodities:
        out = out[out["mentioned_commodities_key"].map(lambda value: _contains_any_key(str(value).lower(), normalized_commodities))].copy()
    if normalized_event_tags:
        out = out[out["event_tags_key"].map(lambda value: _contains_any_key(str(value).lower(), normalized_event_tags))].copy()

    parsed_start = pd.to_datetime(start_date, utc=True, errors="coerce") if _coerce_text(start_date) else pd.NaT
    parsed_end = pd.to_datetime(end_date, utc=True, errors="coerce") if _coerce_text(end_date) else pd.NaT
    start_ts = None if pd.isna(parsed_start) else parsed_start
    end_ts = None if pd.isna(parsed_end) else parsed_end
    if exact_dates or start_ts is not None or end_ts is not None:
        out = out[out.apply(lambda row: _row_matches_dates(row, exact_dates=exact_dates, start_date=start_ts, end_date=end_ts), axis=1)].copy()
    if out.empty:
        return out

    out["score_lexical"] = out.apply(
        lambda row: _search_score(
            row,
            query=query_text,
            query_tokens=query_tokens,
            tickers=normalized_tickers,
            providers=normalized_providers,
        ),
        axis=1,
    )
    # Incorporate ts_rank from tsvector full-text search (0-1 range, weight A/B/C)
    if "ts_rank_score" in out.columns:
        out["score_ts_rank"] = pd.to_numeric(out["ts_rank_score"], errors="coerce").fillna(0.0)
    else:
        out["score_ts_rank"] = 0.0
    if normalized_scopes:
        out["score_lexical"] = out["score_lexical"] + out["research_scope"].astype(str).str.lower().isin(set(normalized_scopes)).astype(float) * 1.5
    if normalized_commodities:
        out["score_lexical"] = out["score_lexical"] + out["mentioned_commodities_key"].map(lambda value: 3.0 if _contains_any_key(str(value).lower(), normalized_commodities) else 0.0)
    if normalized_event_tags:
        out["score_lexical"] = out["score_lexical"] + out["event_tags_key"].map(lambda value: 3.0 if _contains_any_key(str(value).lower(), normalized_event_tags) else 0.0)

    score_embedding_map: dict[str, float] = {}
    if use_semantic and query_text:
        score_embedding_map, query_embedding_model = _semantic_chunk_scores(
            query_text,
            out,
            embedding_client=embedding_client,
        )
        if query_embedding_model:
            out["query_embedding_model"] = query_embedding_model
    out["score_embedding"] = out["chunk_record_id"].astype(str).map(lambda value: float(score_embedding_map.get(value, 0.0)))
    out["score_rerank"] = out.apply(_ranking_bonus, axis=1)
    out["search_score"] = out["score_lexical"] + out["score_ts_rank"] * 12.0 + out["score_embedding"] * 8.0 + out["score_rerank"]

    if query_text:
        out = out[(out["score_lexical"] > 0) | (out["score_ts_rank"] > 0) | (out["score_embedding"] >= _DEFAULT_SEMANTIC_THRESHOLD)].copy()
    if out.empty:
        return out
    has_lexical = out["score_lexical"] > 0
    has_ts_rank = out["score_ts_rank"] > 0
    has_semantic = out["score_embedding"] >= _DEFAULT_SEMANTIC_THRESHOLD
    out["match_source"] = "structured"
    out.loc[has_lexical | has_ts_rank, "match_source"] = "lexical"
    out.loc[has_semantic, "match_source"] = "semantic"
    out.loc[(has_lexical | has_ts_rank) & has_semantic, "match_source"] = "hybrid"

    out = out.sort_values(
        ["search_score", "published_at", "authority_rank", "chunk_index"],
        ascending=[False, False, True, True],
        na_position="last",
    ).head(max(int(limit), 1)).reset_index(drop=True)
    return out


def search_prepared_evidence_chunks(
    frame: pd.DataFrame,
    *,
    query: str = "",
    tickers: list[str] | None = None,
    commodities: list[str] | None = None,
    event_tags: list[str] | None = None,
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_kinds: list[str] | None = None,
    providers: list[str] | None = None,
    research_scopes: list[str] | None = None,
    run_id: str | None = None,
    canonical_document_id: str | None = None,
    limit: int = 20,
    use_semantic: bool = True,
    embedding_client: Any | None = None,
) -> pd.DataFrame:
    return _filter_and_score_retained_chunk_frame(
        frame,
        query=query,
        tickers=tickers,
        commodities=commodities,
        event_tags=event_tags,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        source_kinds=source_kinds,
        providers=providers,
        research_scopes=research_scopes,
        run_id=run_id,
        canonical_document_id=canonical_document_id,
        limit=limit,
        use_semantic=use_semantic,
        embedding_client=embedding_client,
    )


def _frame_from_retained_rows(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    columns = [
        "canonical_document_id",
        "canonical_url",
        "url_host",
        "title",
        "display_excerpt",
        "search_text",
        "source_kind",
        "source_provider",
        "search_provider",
        "bundle_subject",
        "source_authority_bucket",
        "authority_rank",
        "published_at",
        "published_date",
        "primary_date",
        "raw_text_blob_path",
        "raw_text_chars",
        "raw_text_origin",
        "last_document_id",
        "last_dataset_name",
        "last_dataset_version_id",
        "last_run_id",
        "last_asof_time_utc",
        "mentioned_tickers_json",
        "mentioned_tickers_key",
        "mentioned_commodities_json",
        "mentioned_commodities_key",
        "event_tags_json",
        "event_tags_key",
        "mentioned_dates_json",
        "mentioned_dates_key",
        "metadata_json",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    for column in ("published_at", "last_asof_time_utc"):
        frame[column] = _safe_datetime_series(frame[column])
    for json_column, list_column in (
        ("mentioned_tickers_json", "mentioned_tickers"),
        ("mentioned_commodities_json", "mentioned_commodities"),
        ("event_tags_json", "event_tags"),
        ("mentioned_dates_json", "mentioned_dates"),
    ):
        frame[list_column] = frame[json_column].map(_safe_json_list)
    return frame


def _contains_any_key(value: object, targets: list[str]) -> bool:
    key = _coerce_text(value)
    if not key or not targets:
        return False
    return any(f"|{target}|" in key for target in targets)


def _row_matches_dates(
    row: pd.Series,
    *,
    exact_dates: list[str],
    start_date: pd.Timestamp | None,
    end_date: pd.Timestamp | None,
) -> bool:
    candidates = [item for item in list(row.get("mentioned_dates") or []) if item]
    published_date = _coerce_text(row.get("published_date"))
    primary_date = _coerce_text(row.get("primary_date"))
    if published_date:
        candidates.append(published_date)
    if primary_date:
        candidates.append(primary_date)
    seen: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.append(item)
    if exact_dates and not any(item in exact_dates for item in seen):
        return False
    if start_date is None and end_date is None:
        return True
    for item in seen:
        parsed = pd.to_datetime(item, utc=True, errors="coerce")
        if pd.isna(parsed):
            continue
        if start_date is not None and parsed < start_date:
            continue
        if end_date is not None and parsed > end_date:
            continue
        return True
    return False


def _search_score(row: pd.Series, *, query: str, query_tokens: list[str], tickers: list[str], providers: list[str]) -> float:
    score = 0.0
    title_blob = _coerce_text(row.get("title")).lower()
    excerpt_blob = _coerce_text(row.get("display_excerpt")).lower()
    chunk_blob = _coerce_text(row.get("chunk_text")).lower()
    search_blob = _coerce_text(row.get("search_text")).lower()
    provider_blob = " ".join(
        [
            _coerce_text(row.get("source_provider")).lower(),
            _coerce_text(row.get("search_provider")).lower(),
            _coerce_text(row.get("source_authority_bucket")).lower(),
        ]
    )
    if query:
        phrase = query.lower()
        if phrase in title_blob:
            score += 10.0
        if phrase in excerpt_blob:
            score += 8.0
        if phrase in chunk_blob:
            score += 9.0
        if phrase in search_blob:
            score += 6.0
    if query_tokens:
        token_hits = sum(token in search_blob for token in query_tokens)
        title_hits = sum(token in title_blob for token in query_tokens)
        chunk_hits = sum(token in chunk_blob for token in query_tokens)
        score += float(token_hits * 2 + title_hits * 3 + chunk_hits * 2)
    if tickers:
        score += float(_coerce_text(row.get("bundle_subject")).upper() in set(tickers)) * 6.0
        score += float(_contains_any_key(row.get("mentioned_tickers_key"), tickers)) * 4.0
    if providers:
        provider_set = set(providers)
        provider_match = _coerce_text(row.get("source_provider")).lower() in provider_set or _coerce_text(row.get("search_provider")).lower() in provider_set
        score += float(provider_match) * 2.0
    return round(score, 3)


def _ranking_bonus(row: pd.Series) -> float:
    score = 0.0
    authority_rank = int(row.get("authority_rank") or 0)
    if authority_rank > 0:
        score += max(0.0, 1.5 - authority_rank * 0.25)
    published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
    if pd.notna(published_at):
        age_hours = max((pd.Timestamp.now(tz="UTC") - published_at).total_seconds() / 3600.0, 0.0)
        score += max(0.0, 2.0 - min(age_hours / 24.0, 2.0))
    return round(score, 3)


def _append_or_like_clauses(
    clauses: list[str],
    params: list[Any],
    *,
    column_sql: str,
    values: list[str],
) -> None:
    clean_values = [_coerce_text(value) for value in values if _coerce_text(value)]
    if not clean_values:
        return
    parts = [f"{column_sql} LIKE %s" for _ in clean_values]
    clauses.append("(" + " OR ".join(parts) + ")")
    params.extend(clean_values)


def _fetch_retained_chunk_rows(
    conn: Any,
    *,
    clauses: list[str],
    params: list[Any],
    limit: int,
    ts_query_text: str = "",
) -> list[tuple[Any, ...]]:
    # When a tsvector query is provided, include ts_rank for SQL-side relevance scoring
    ts_rank_col = "0.0 AS ts_rank_score"
    ts_order = ""
    extra_params: list[Any] = []
    if ts_query_text:
        ts_rank_col = "ts_rank(search_tsvector, plainto_tsquery('english', %s), 32) AS ts_rank_score"
        ts_order = "ts_rank_score DESC, "
        extra_params.append(ts_query_text)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                chunk_record_id, chunk_identity_sha256, canonical_document_id, document_id,
                chunk_id, chunk_index, document_chunk_count, title, display_excerpt,
                chunk_text, search_text, bundle_subject, source_kind, source_provider,
                search_provider, research_scope, source_authority_bucket, authority_rank,
                published_at::text AS published_at, published_date, primary_date, raw_text_origin, raw_text_chars,
                dataset_name, dataset_version_id, run_id, asof_time_utc::text AS asof_time_utc, embedding_model, embedding_vector_json,
                mentioned_tickers_json::text, mentioned_tickers_key, mentioned_commodities_json::text,
                mentioned_commodities_key, event_tags_json::text, event_tags_key,
                mentioned_dates_json::text, mentioned_dates_key, metadata_json::text,
                {ts_rank_col}
            FROM saa_evidence_chunks
            WHERE {" AND ".join(clauses)}
            ORDER BY {ts_order}COALESCE(published_date, published_at::text, asof_time_utc::text) DESC NULLS LAST, authority_rank ASC NULLS LAST, chunk_index ASC NULLS LAST
            LIMIT %s
            """,
            (*extra_params, *params, limit),
        )
        return cur.fetchall()


def _semantic_chunk_scores(
    query_text: str,
    frame: pd.DataFrame,
    *,
    embedding_client: Any | None = None,
) -> tuple[dict[str, float], str]:
    normalized_query = _coerce_text(query_text)
    if not normalized_query or not isinstance(frame, pd.DataFrame) or frame.empty or "embedding_vector_json" not in frame.columns:
        return {}, ""
    vectors_by_id: dict[str, list[float]] = {}
    for _, row in frame.iterrows():
        chunk_record_id = _coerce_text(row.get("chunk_record_id"))
        if not chunk_record_id:
            continue
        vector = _safe_json_vector(row.get("embedding_vector_json"))
        if vector:
            vectors_by_id[chunk_record_id] = vector
    if not vectors_by_id:
        return {}, ""
    embedding_client = embedding_client or load_embedding_client()
    if embedding_client is None:
        return {}, ""
    try:
        query_vectors = embedding_client.generate_embeddings([normalized_query])
    except Exception:
        return {}, ""
    if not query_vectors:
        return {}, ""
    query_vector = query_vectors[0]
    expected_model = _coerce_text(getattr(getattr(embedding_client, "config", object()), "embedding_model", ""))
    scores: dict[str, float] = {}
    for _, row in frame.iterrows():
        chunk_record_id = _coerce_text(row.get("chunk_record_id"))
        if not chunk_record_id:
            continue
        if expected_model:
            row_model = _coerce_text(row.get("embedding_model"))
            if row_model and row_model != expected_model:
                continue
        vector = vectors_by_id.get(chunk_record_id)
        if not vector:
            continue
        score = (_cosine_similarity(query_vector, vector) + 1.0) / 2.0
        scores[chunk_record_id] = round(float(score), 4)
    return scores, expected_model


def load_retained_document_metadata(
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
                SELECT
                    canonical_document_id, canonical_url, url_host, title, display_excerpt, search_text,
                    source_kind, source_provider, search_provider, bundle_subject, source_authority_bucket,
                    authority_rank, published_at::text AS published_at, published_date, primary_date, raw_text_blob_path,
                    raw_text_chars, raw_text_origin, last_document_id, last_dataset_name,
                    last_dataset_version_id, last_run_id, last_asof_time_utc::text AS last_asof_time_utc, mentioned_tickers_json::text,
                    mentioned_tickers_key, mentioned_commodities_json::text, mentioned_commodities_key,
                    event_tags_json::text, event_tags_key, mentioned_dates_json::text, mentioned_dates_key,
                    metadata_json::text
                FROM saa_documents
                WHERE canonical_document_id = %s
                """,
                (normalized_id,),
            )
            rows = cur.fetchall()
    finally:
        if should_close:
            active_conn.close()
    frame = _frame_from_retained_rows(rows)
    if frame.empty:
        return None
    return frame.iloc[0].to_dict()


def search_retained_documents(
    *,
    query: str = "",
    tickers: list[str] | None = None,
    commodities: list[str] | None = None,
    event_tags: list[str] | None = None,
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_kinds: list[str] | None = None,
    providers: list[str] | None = None,
    run_id: str | None = None,
    limit: int = 20,
    conn: Any | None = None,
) -> pd.DataFrame:
    active_conn = conn
    should_close = False
    if active_conn is None:
        active_conn = _db_connection()
        should_close = active_conn is not None
    if active_conn is None:
        return pd.DataFrame()

    normalized_run_id = _coerce_text(run_id)
    normalized_source_kinds = [item.lower() for item in list(source_kinds or []) if _coerce_text(item)]
    normalized_providers = [item.lower() for item in list(providers or []) if _coerce_text(item)]
    query_text = _coerce_text(query)
    query_tokens = _normalized_tokens(query_text)
    scan_limit = max(min(max(int(limit), 1) * 8, _DEFAULT_SEARCH_SCAN_LIMIT), max(int(limit), 1))
    clauses = ["1=1"]
    params: list[Any] = []
    if normalized_run_id:
        clauses.append("last_run_id = %s")
        params.append(normalized_run_id)
    if normalized_source_kinds:
        clauses.append("lower(source_kind) = ANY(%s)")
        params.append(normalized_source_kinds)
    if normalized_providers:
        clauses.append("(lower(source_provider) = ANY(%s) OR lower(search_provider) = ANY(%s))")
        params.extend([normalized_providers, normalized_providers])
    parsed_start = pd.to_datetime(start_date, utc=True, errors="coerce") if _coerce_text(start_date) else pd.NaT
    parsed_end = pd.to_datetime(end_date, utc=True, errors="coerce") if _coerce_text(end_date) else pd.NaT
    if pd.notna(parsed_start):
        clauses.append("COALESCE(published_at, last_asof_time_utc) >= %s")
        params.append(parsed_start)
    if pd.notna(parsed_end):
        clauses.append("COALESCE(published_at, last_asof_time_utc) <= %s")
        params.append(parsed_end)
    try:
        with active_conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    canonical_document_id, canonical_url, url_host, title, display_excerpt, search_text,
                    source_kind, source_provider, search_provider, bundle_subject, source_authority_bucket,
                    authority_rank, published_at::text AS published_at, published_date, primary_date, raw_text_blob_path,
                    raw_text_chars, raw_text_origin, last_document_id, last_dataset_name,
                    last_dataset_version_id, last_run_id, last_asof_time_utc::text AS last_asof_time_utc, mentioned_tickers_json::text,
                    mentioned_tickers_key, mentioned_commodities_json::text, mentioned_commodities_key,
                    event_tags_json::text, event_tags_key, mentioned_dates_json::text, mentioned_dates_key,
                    metadata_json::text
                FROM saa_documents
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(published_at, last_asof_time_utc) DESC, authority_rank ASC NULLS LAST
                LIMIT %s
                """,
                (*params, scan_limit),
            )
            rows = cur.fetchall()
    finally:
        if should_close:
            active_conn.close()

    out = _frame_from_retained_rows(rows)
    if out.empty:
        return out

    normalized_tickers = [item.upper() for item in list(tickers or []) if _coerce_text(item)]
    if normalized_tickers:
        out = out[
            out["bundle_subject"].astype(str).str.upper().isin(set(normalized_tickers))
            | out["mentioned_tickers_key"].map(lambda value: _contains_any_key(str(value).upper(), normalized_tickers))
        ].copy()

    normalized_commodities = [item.lower() for item in list(commodities or []) if _coerce_text(item)]
    if normalized_commodities:
        out = out[out["mentioned_commodities_key"].map(lambda value: _contains_any_key(str(value).lower(), normalized_commodities))].copy()

    normalized_event_tags = [item.lower() for item in list(event_tags or []) if _coerce_text(item)]
    if normalized_event_tags:
        out = out[out["event_tags_key"].map(lambda value: _contains_any_key(str(value).lower(), normalized_event_tags))].copy()

    exact_dates = [_coerce_text(item) for item in list(dates or []) if _coerce_text(item)]
    start_ts = None if pd.isna(parsed_start) else parsed_start
    end_ts = None if pd.isna(parsed_end) else parsed_end
    if exact_dates or start_ts is not None or end_ts is not None:
        out = out[out.apply(lambda row: _row_matches_dates(row, exact_dates=exact_dates, start_date=start_ts, end_date=end_ts), axis=1)].copy()

    out["search_score"] = out.apply(
        lambda row: _search_score(
            row,
            query=query_text,
            query_tokens=query_tokens,
            tickers=normalized_tickers,
            providers=normalized_providers,
        ),
        axis=1,
    )
    out["search_score"] = out["search_score"] + out.apply(_ranking_bonus, axis=1)
    if query_text:
        out = out[out["search_score"] > 0].copy()
    out = out.sort_values(
        ["search_score", "published_at", "authority_rank"],
        ascending=[False, False, True],
        na_position="last",
    ).head(max(int(limit), 1)).reset_index(drop=True)
    return out


def search_retained_evidence_chunks(
    *,
    query: str = "",
    tickers: list[str] | None = None,
    commodities: list[str] | None = None,
    event_tags: list[str] | None = None,
    dates: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_kinds: list[str] | None = None,
    providers: list[str] | None = None,
    research_scopes: list[str] | None = None,
    run_id: str | None = None,
    canonical_document_id: str | None = None,
    limit: int = 20,
    use_semantic: bool = True,
    embedding_client: Any | None = None,
    conn: Any | None = None,
) -> pd.DataFrame:
    active_conn = conn
    should_close = False
    if active_conn is None:
        active_conn = _db_connection()
        should_close = active_conn is not None
    if active_conn is None:
        return pd.DataFrame()

    normalized_run_id = _coerce_text(run_id)
    normalized_document_id = _coerce_text(canonical_document_id)
    normalized_source_kinds = [item.lower() for item in list(source_kinds or []) if _coerce_text(item)]
    normalized_providers = [item.lower() for item in list(providers or []) if _coerce_text(item)]
    normalized_scopes = [item.lower() for item in list(research_scopes or []) if _coerce_text(item)]
    normalized_tickers = [item.upper() for item in list(tickers or []) if _coerce_text(item)]
    normalized_commodities = [item.lower() for item in list(commodities or []) if _coerce_text(item)]
    normalized_event_tags = [item.lower() for item in list(event_tags or []) if _coerce_text(item)]
    exact_dates = [_coerce_text(item) for item in list(dates or []) if _coerce_text(item)]
    query_text = _coerce_text(query)
    query_tokens = [token for token in _normalized_tokens(query_text) if len(token) >= 3]
    scan_limit = max(min(max(int(limit), 1) * 12, _DEFAULT_SEARCH_SCAN_LIMIT * 4), max(int(limit), 1))
    semantic_scan_limit = max(min(max(int(limit), 1) * 10, _DEFAULT_SEMANTIC_SCAN_LIMIT), max(int(limit), 1))

    base_clauses = ["1=1"]
    base_params: list[Any] = []
    if normalized_run_id:
        base_clauses.append("run_id = %s")
        base_params.append(normalized_run_id)
    if normalized_document_id:
        base_clauses.append("canonical_document_id = %s")
        base_params.append(normalized_document_id)
    if normalized_source_kinds:
        base_clauses.append("lower(source_kind) = ANY(%s)")
        base_params.append(normalized_source_kinds)
    if normalized_providers:
        base_clauses.append("(lower(source_provider) = ANY(%s) OR lower(search_provider) = ANY(%s))")
        base_params.extend([normalized_providers, normalized_providers])
    if normalized_scopes:
        base_clauses.append("lower(research_scope) = ANY(%s)")
        base_params.append(normalized_scopes)

    parsed_start = pd.to_datetime(start_date, utc=True, errors="coerce") if _coerce_text(start_date) else pd.NaT
    parsed_end = pd.to_datetime(end_date, utc=True, errors="coerce") if _coerce_text(end_date) else pd.NaT
    if pd.notna(parsed_start):
        base_clauses.append("COALESCE(published_at, asof_time_utc) >= %s")
        base_params.append(parsed_start)
    if pd.notna(parsed_end):
        base_clauses.append("COALESCE(published_at, asof_time_utc) <= %s")
        base_params.append(parsed_end)

    if normalized_tickers:
        ticker_parts = ["upper(bundle_subject) = ANY(%s)"]
        ticker_params: list[Any] = [normalized_tickers]
        ticker_key_patterns = [f"%|{ticker}|%" for ticker in normalized_tickers]
        if ticker_key_patterns:
            ticker_parts.extend(["mentioned_tickers_key LIKE %s" for _ in ticker_key_patterns])
            ticker_params.extend(ticker_key_patterns)
        base_clauses.append("(" + " OR ".join(ticker_parts) + ")")
        base_params.extend(ticker_params)
    _append_or_like_clauses(
        base_clauses,
        base_params,
        column_sql="lower(mentioned_commodities_key)",
        values=[f"%|{item}|%" for item in normalized_commodities],
    )
    _append_or_like_clauses(
        base_clauses,
        base_params,
        column_sql="lower(event_tags_key)",
        values=[f"%|{item}|%" for item in normalized_event_tags],
    )
    _append_or_like_clauses(
        base_clauses,
        base_params,
        column_sql="mentioned_dates_key",
        values=[f"%|{item}|%" for item in exact_dates],
    )

    # Full-text search: tsvector @@ with ILIKE fallback for exact substring matches
    tsvector_clauses = list(base_clauses)
    tsvector_params = list(base_params)
    ilike_clauses = list(base_clauses)
    ilike_params = list(base_params)
    if query_text:
        # Primary: tsvector full-text search (uses GIN index, stemming, ranking)
        tsvector_clauses.append("search_tsvector @@ plainto_tsquery('english', %s)")
        tsvector_params.append(query_text)
        # Fallback: ILIKE for exact substrings tsvector might miss (ticker symbols, acronyms)
        ilike_parts = ["lower(search_text) LIKE %s", "lower(title) LIKE %s", "lower(chunk_text) LIKE %s"]
        ilike_search_params: list[Any] = [f"%{query_text.lower()}%"] * 3
        ilike_clauses.append("(" + " OR ".join(ilike_parts) + ")")
        ilike_params.extend(ilike_search_params)

    try:
        if query_text:
            # tsvector path — fast, indexed, with ts_rank scoring
            tsvector_rows = _fetch_retained_chunk_rows(
                active_conn,
                clauses=tsvector_clauses,
                params=tsvector_params,
                limit=scan_limit,
                ts_query_text=query_text,
            )
            # ILIKE fallback — catches exact substrings that stemming misses
            ilike_rows = _fetch_retained_chunk_rows(
                active_conn,
                clauses=ilike_clauses,
                params=ilike_params,
                limit=scan_limit,
            )
            # Merge: tsvector results first, then any ILIKE-only matches
            row_map: dict[str, tuple[Any, ...]] = {}
            for row in tsvector_rows:
                key = _coerce_text(row[0]) if row else ""
                if key and key not in row_map:
                    row_map[key] = row
            for row in ilike_rows:
                key = _coerce_text(row[0]) if row else ""
                if key and key not in row_map:
                    row_map[key] = row
            lexical_rows = list(row_map.values())
            if use_semantic:
                semantic_rows = _fetch_retained_chunk_rows(
                    active_conn,
                    clauses=base_clauses,
                    params=base_params,
                    limit=max(scan_limit, semantic_scan_limit),
                )
                row_map: dict[str, tuple[Any, ...]] = {}
                for row in lexical_rows + semantic_rows:
                    key = _coerce_text(row[0]) if row else ""
                    if key and key not in row_map:
                        row_map[key] = row
                rows = list(row_map.values())
            else:
                rows = lexical_rows
        else:
            rows = _fetch_retained_chunk_rows(
                active_conn,
                clauses=base_clauses,
                params=base_params,
                limit=scan_limit,
            )
    finally:
        if should_close:
            active_conn.close()

    out = _frame_from_retained_chunk_rows(rows)
    return _filter_and_score_retained_chunk_frame(
        out,
        query=query_text,
        tickers=normalized_tickers,
        commodities=normalized_commodities,
        event_tags=normalized_event_tags,
        dates=exact_dates,
        start_date=_coerce_text(start_date),
        end_date=_coerce_text(end_date),
        source_kinds=normalized_source_kinds,
        providers=normalized_providers,
        research_scopes=normalized_scopes,
        run_id=normalized_run_id,
        canonical_document_id=normalized_document_id,
        limit=limit,
        use_semantic=use_semantic,
        embedding_client=embedding_client,
    )


def load_retained_evidence_chunk(
    chunk_record_id: str,
    *,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    normalized_id = _coerce_text(chunk_record_id)
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
        rows = _fetch_retained_chunk_rows(
            active_conn,
            clauses=["chunk_record_id = %s"],
            params=[normalized_id],
            limit=1,
        )
    finally:
        if should_close:
            active_conn.close()
    frame = _frame_from_retained_chunk_rows(rows)
    if frame.empty:
        return None
    return frame.iloc[0].to_dict()


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
    "load_retained_evidence_chunk",
    "load_retained_document_metadata",
    "persist_retained_evidence_chunks",
    "persist_agent_research_evidence",
    "persist_retained_source_documents",
    "prepare_retained_evidence_chunks",
    "prepare_retained_source_documents",
    "search_prepared_evidence_chunks",
    "search_retained_evidence_chunks",
    "search_retained_documents",
]
