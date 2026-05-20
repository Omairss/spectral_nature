from __future__ import annotations

from io import BytesIO
from datetime import datetime, timezone
import html
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
import xml.etree.ElementTree as ET

import pandas as pd

from .storage import (
    _coerce_text,
    _db_connection,
    _sha256_text,
    _slug,
    load_retained_document,
    load_retained_document_metadata,
    load_retained_evidence_chunk,
)

try:
    import requests
except Exception:
    requests = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None


ZOPEDIA_PAGE_COLUMNS: tuple[str, ...] = (
    "page_id",
    "page_type",
    "title",
    "slug",
    "summary",
    "body_markdown",
    "source_document_ids_json",
    "source_urls_json",
    "entity_refs_json",
    "outgoing_links_json",
    "status",
    "version",
    "created_at_utc",
    "updated_at_utc",
    "metadata_json",
    "ts_rank_score",
)

ZOPEDIA_MUTATION_AUDIT_COLUMNS: tuple[str, ...] = (
    "mutation_id",
    "mutation_type",
    "risk_level",
    "status",
    "actor",
    "source",
    "page_ids_json",
    "evidence_refs_json",
    "before_state_json",
    "after_state_json",
    "rollback_hint_json",
    "created_at_utc",
)

ZOPEDIA_BACKLINK_COLUMNS: tuple[str, ...] = (
    "source_page_id",
    "target_page_id",
    "relation",
    "weight",
    "confidence",
    "created_at_utc",
    "updated_at_utc",
    "metadata_json",
)

ZOPEDIA_COMMUNITY_INDEX_COLUMNS: tuple[str, ...] = (
    "community_id",
    "label",
    "page_ids_json",
    "central_page_ids_json",
    "page_count",
    "edge_count",
    "source_count",
    "score",
    "created_at_utc",
    "updated_at_utc",
    "metadata_json",
)

ZOPEDIA_MAINTENANCE_REPORT_COLUMNS: tuple[str, ...] = (
    "run_id",
    "status",
    "page_count",
    "edge_count",
    "issue_count",
    "mutation_count",
    "proposal_count",
    "summary_json",
    "issue_rows_json",
    "mutation_ids_json",
    "proposal_ids_json",
    "created_at_utc",
)

_VALID_PAGE_TYPES = {
    "source",
    "concept",
    "entity",
    "theme",
    "market_event",
    "ticker",
    "macro",
    "question",
    "index",
}
_DEFAULT_SEARCH_LIMIT = 12
_SOURCE_BODY_LIMIT = 80_000
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch"
_TEXT_CONTROL_CHAR_LIMIT = 0.08
_DEFAULT_MAINTENANCE_PAGE_LIMIT = 5000
_DEFAULT_STALE_AFTER_DAYS = 120
_DEFAULT_BLOAT_CHAR_LIMIT = 32_000
_SAFE_MUTATION_TYPES = {"upsert_pages", "link_pages", "metadata_patch"}
_RISKY_MUTATION_TYPES = {"archive_pages", "delete_pages", "merge_pages", "rewrite_pages", "remove_links", "unlink_pages"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [text]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple, set)):
        parsed = [parsed]
    out: list[str] = []
    for item in parsed:
        clean = _coerce_text(item)
        if clean and clean not in out:
            out.append(clean)
    return out


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = _coerce_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_dict_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
    else:
        parsed = value
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _json_text(value: object, *, default: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                json.loads(text)
                return text
            except Exception:
                pass
    return json.dumps(default, ensure_ascii=False, sort_keys=True, default=str)


def _trim(value: object, limit: int) -> str:
    text = _coerce_text(value)
    if len(text) <= max(int(limit), 1):
        return text
    return text[: max(int(limit), 1) - 3].rstrip() + "..."


def _looks_like_text(value: str) -> bool:
    if not value:
        return False
    sample = value[: min(len(value), 4000)]
    if not sample:
        return False
    control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    return (control_count / max(len(sample), 1)) <= _TEXT_CONTROL_CHAR_LIMIT


def _decode_uploaded_text(content: bytes) -> str:
    if not content:
        return ""
    if b"\x00" in content and not (
        content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff") or content.startswith(b"\xef\xbb\xbf")
    ):
        return ""
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            decoded = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_like_text(decoded):
            return decoded
    try:
        decoded = content.decode("latin-1")
    except Exception:
        return ""
    return decoded if _looks_like_text(decoded) else ""


def _extract_pdf_text(content: bytes) -> str:
    if not content:
        return ""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return ""
    parts: list[str] = []
    for page in list(reader.pages or []):
        try:
            page_text = _coerce_text(page.extract_text())
        except Exception:
            page_text = ""
        if page_text:
            parts.append(page_text)
    return "\n\n".join(parts)


def _title_from_filename(filename: object) -> str:
    text = _coerce_text(filename)
    if not text:
        return "Uploaded Source"
    stem = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{1,12}$", "", stem).strip()
    return stem or text or "Uploaded Source"


def prepare_zopedia_uploaded_source(
    *,
    filename: str,
    content: bytes,
    content_type: str = "",
    source_text_limit: int = _SOURCE_BODY_LIMIT,
) -> dict[str, Any]:
    """Decode an uploaded source file into source text plus provenance metadata."""

    clean_filename = _coerce_text(filename) or "uploaded-source"
    clean_content_type = _coerce_text(content_type)
    payload = bytes(content or b"")
    byte_count = len(payload)
    lower_name = clean_filename.lower()
    lower_type = clean_content_type.lower()
    is_pdf = lower_name.endswith(".pdf") or lower_type == "application/pdf"

    if is_pdf:
        source_text = _extract_pdf_text(payload)
        source_type = "uploaded_pdf"
        if not source_text:
            return {
                "status": "unsupported",
                "message": "This PDF did not expose extractable text. Try a text, markdown, CSV, JSON file, or paste the source text.",
                "title": _title_from_filename(clean_filename),
                "source_text": "",
                "source_type": source_type,
                "metadata": {
                    "input_source": "upload",
                    "filename": clean_filename,
                    "content_type": clean_content_type,
                    "byte_count": byte_count,
                },
            }
    else:
        source_text = _decode_uploaded_text(payload)
        source_type = "uploaded_file"
        if not source_text:
            return {
                "status": "unsupported",
                "message": "This upload does not look like a readable text source. Try text, markdown, CSV, JSON, PDF with selectable text, or paste the source text.",
                "title": _title_from_filename(clean_filename),
                "source_text": "",
                "source_type": source_type,
                "metadata": {
                    "input_source": "upload",
                    "filename": clean_filename,
                    "content_type": clean_content_type,
                    "byte_count": byte_count,
                },
            }

    clean_text = _coerce_text(source_text)
    if not clean_text:
        return {
            "status": "empty",
            "message": "This upload did not contain readable source text.",
            "title": _title_from_filename(clean_filename),
            "source_text": "",
            "source_type": source_type,
            "metadata": {
                "input_source": "upload",
                "filename": clean_filename,
                "content_type": clean_content_type,
                "byte_count": byte_count,
            },
        }
    safe_limit = max(int(source_text_limit or _SOURCE_BODY_LIMIT), 1000)
    trimmed_text = _trim(clean_text, safe_limit)
    metadata = {
        "input_source": "upload",
        "filename": clean_filename,
        "content_type": clean_content_type,
        "byte_count": byte_count,
        "text_char_count": len(clean_text),
        "truncated": len(trimmed_text) < len(clean_text),
    }
    return {
        "status": "ok",
        "message": "",
        "title": _title_from_filename(clean_filename),
        "source_text": trimmed_text,
        "source_type": source_type,
        "metadata": metadata,
    }


def _normalize_page_type(value: object) -> str:
    page_type = _slug(value, default="source").replace("-", "_")
    return page_type if page_type in _VALID_PAGE_TYPES else "source"


def build_zopedia_page_id(*, page_type: object, title: object, slug: object = "") -> str:
    normalized_type = _normalize_page_type(page_type)
    normalized_slug = _slug(slug or title, default="untitled")
    digest = _sha256_text(f"{normalized_type}|{normalized_slug}")[:12]
    return f"zopedia::{normalized_type}::{normalized_slug[:64]}::{digest}"


def normalize_zopedia_page(
    page: dict[str, Any],
    *,
    now: datetime | None = None,
    source_title: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    title = _coerce_text(page.get("title")) or source_title or "Untitled Zopedia Page"
    page_type = _normalize_page_type(page.get("page_type") or page.get("type"))
    slug = _slug(page.get("slug") or title, default="untitled")
    body = _coerce_text(page.get("body_markdown") or page.get("body") or page.get("text"))
    summary = _coerce_text(page.get("summary") or page.get("summary_text")) or _trim(body, 360)
    source_urls = _json_list(page.get("source_urls") or page.get("source_urls_json"))
    if source_url and source_url not in source_urls:
        source_urls.append(source_url)
    source_document_ids = _json_list(page.get("source_document_ids") or page.get("source_document_ids_json"))
    entity_refs = _json_list(page.get("entity_refs") or page.get("entity_refs_json"))
    outgoing_links = _json_list(page.get("outgoing_links") or page.get("outgoing_links_json"))
    metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
    if source_title and "source_title" not in metadata:
        metadata["source_title"] = source_title
    if source_url and "source_url" not in metadata:
        metadata["source_url"] = source_url
    return {
        "page_id": _coerce_text(page.get("page_id")) or build_zopedia_page_id(
            page_type=page_type,
            title=title,
            slug=slug,
        ),
        "page_type": page_type,
        "title": title,
        "slug": slug,
        "summary": summary,
        "body_markdown": body or summary,
        "source_document_ids_json": _json_text(source_document_ids, default=source_document_ids),
        "source_urls_json": _json_text(source_urls, default=source_urls),
        "entity_refs_json": _json_text(entity_refs, default=entity_refs),
        "outgoing_links_json": _json_text(outgoing_links, default=outgoing_links),
        "status": _coerce_text(page.get("status")) or "active",
        "version": int(page.get("version") or 1),
        "created_at_utc": pd.to_datetime(page.get("created_at_utc") or timestamp, utc=True, errors="coerce"),
        "updated_at_utc": pd.to_datetime(page.get("updated_at_utc") or timestamp, utc=True, errors="coerce"),
        "metadata_json": _json_text(metadata, default=metadata),
        "ts_rank_score": float(page.get("ts_rank_score") or 0.0),
    }


def prepare_zopedia_pages(
    pages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    now: datetime | None = None,
    source_title: str = "",
    source_url: str = "",
) -> tuple[pd.DataFrame, list[tuple[Any, ...]]]:
    timestamp = now or _utc_now()
    normalized = [
        normalize_zopedia_page(
            dict(page or {}),
            now=timestamp,
            source_title=source_title,
            source_url=source_url,
        )
        for page in list(pages or [])
    ]
    frame = pd.DataFrame(normalized, columns=list(ZOPEDIA_PAGE_COLUMNS))
    records = [tuple(row[column] for column in ZOPEDIA_PAGE_COLUMNS[:-1]) for row in normalized]
    return frame, records


def bootstrap_zopedia_storage(conn: Any, *, commit: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_pages (
                page_id TEXT PRIMARY KEY,
                page_type TEXT NOT NULL,
                title TEXT NOT NULL,
                slug TEXT NOT NULL,
                summary TEXT,
                body_markdown TEXT,
                source_document_ids_json JSONB,
                source_urls_json JSONB,
                entity_refs_json JSONB,
                outgoing_links_json JSONB,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB
            )
            """
        )
        cur.execute("ALTER TABLE saa_zopedia_pages ADD COLUMN IF NOT EXISTS source_urls_json JSONB")
        cur.execute("ALTER TABLE saa_zopedia_pages ADD COLUMN IF NOT EXISTS search_tsvector tsvector")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_pages_updated
            ON saa_zopedia_pages (updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_pages_type_status
            ON saa_zopedia_pages (page_type, status, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_pages_search_tsvector
            ON saa_zopedia_pages USING GIN (search_tsvector)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_change_proposals (
                proposal_id TEXT PRIMARY KEY,
                proposal_type TEXT NOT NULL,
                page_id TEXT,
                title TEXT NOT NULL,
                rationale TEXT,
                proposal_payload_json JSONB,
                status TEXT NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_proposals_status
            ON saa_zopedia_change_proposals (status, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_mutation_audit (
                mutation_id TEXT PRIMARY KEY,
                mutation_type TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                actor TEXT,
                source TEXT,
                page_ids_json JSONB,
                evidence_refs_json JSONB,
                before_state_json JSONB,
                after_state_json JSONB,
                rollback_hint_json JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_mutation_audit_status
            ON saa_zopedia_mutation_audit (status, created_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_mutation_audit_type
            ON saa_zopedia_mutation_audit (mutation_type, created_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_backlinks (
                source_page_id TEXT NOT NULL,
                target_page_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB,
                PRIMARY KEY (source_page_id, target_page_id, relation)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_backlinks_target
            ON saa_zopedia_backlinks (target_page_id, relation)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_community_index (
                community_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                page_ids_json JSONB,
                central_page_ids_json JSONB,
                page_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                source_count INTEGER NOT NULL,
                score DOUBLE PRECISION NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL,
                updated_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_community_score
            ON saa_zopedia_community_index (score DESC, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_maintenance_reports (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                issue_count INTEGER NOT NULL,
                mutation_count INTEGER NOT NULL,
                proposal_count INTEGER NOT NULL,
                summary_json JSONB,
                issue_rows_json JSONB,
                mutation_ids_json JSONB,
                proposal_ids_json JSONB,
                created_at_utc TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_maintenance_reports_created
            ON saa_zopedia_maintenance_reports (created_at_utc DESC)
            """
        )
    if commit:
        conn.commit()


def _refresh_search_vectors(conn: Any, page_ids: list[str]) -> None:
    if not page_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE saa_zopedia_pages
            SET search_tsvector =
                setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(summary, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(body_markdown, '')), 'C')
            WHERE page_id = ANY(%s)
            """,
            (page_ids,),
        )


def _upsert_zopedia_page_records(conn: Any, records: list[tuple[Any, ...]], *, commit: bool = True) -> None:
    if not records:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO saa_zopedia_pages (
                page_id, page_type, title, slug, summary, body_markdown,
                source_document_ids_json, source_urls_json, entity_refs_json, outgoing_links_json,
                status, version, created_at_utc, updated_at_utc, metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (page_id) DO UPDATE SET
                page_type = EXCLUDED.page_type,
                title = EXCLUDED.title,
                slug = EXCLUDED.slug,
                summary = EXCLUDED.summary,
                body_markdown = EXCLUDED.body_markdown,
                source_document_ids_json = EXCLUDED.source_document_ids_json,
                source_urls_json = EXCLUDED.source_urls_json,
                entity_refs_json = EXCLUDED.entity_refs_json,
                outgoing_links_json = EXCLUDED.outgoing_links_json,
                status = EXCLUDED.status,
                version = GREATEST(saa_zopedia_pages.version + 1, EXCLUDED.version),
                updated_at_utc = EXCLUDED.updated_at_utc,
                metadata_json = EXCLUDED.metadata_json
            """,
            records,
        )
    _refresh_search_vectors(conn, [str(record[0]) for record in records if record])
    if commit:
        conn.commit()


def persist_zopedia_pages(
    pages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    conn: Any | None = None,
    now: datetime | None = None,
    source_title: str = "",
    source_url: str = "",
) -> pd.DataFrame:
    frame, records = prepare_zopedia_pages(
        list(pages or []),
        now=now,
        source_title=source_title,
        source_url=source_url,
    )
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    try:
        if conn is not None and records:
            bootstrap_zopedia_storage(conn)
            _upsert_zopedia_page_records(conn, records)
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
    return frame


def _frame_from_zopedia_page_rows(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    normalized_rows = [
        tuple(list(row) + [None] * max(len(ZOPEDIA_PAGE_COLUMNS) - len(row), 0))[: len(ZOPEDIA_PAGE_COLUMNS)]
        for row in rows
    ]
    frame = pd.DataFrame(normalized_rows, columns=list(ZOPEDIA_PAGE_COLUMNS))
    if frame.empty:
        return frame
    for column in ("created_at_utc", "updated_at_utc"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for json_column, list_column in (
        ("source_document_ids_json", "source_document_ids"),
        ("source_urls_json", "source_urls"),
        ("entity_refs_json", "entity_refs"),
        ("outgoing_links_json", "outgoing_links"),
    ):
        frame[list_column] = frame[json_column].map(_json_list)
    frame["metadata"] = frame["metadata_json"].map(_json_dict)
    frame["ts_rank_score"] = pd.to_numeric(frame["ts_rank_score"], errors="coerce").fillna(0.0)
    return frame


def _ensure_page_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if out.empty:
        return out
    for column in ZOPEDIA_PAGE_COLUMNS:
        if column not in out.columns:
            out[column] = 0.0 if column == "ts_rank_score" else ""
    for column in ("created_at_utc", "updated_at_utc"):
        out[column] = pd.to_datetime(out[column], utc=True, errors="coerce")
    for json_column, list_column in (
        ("source_document_ids_json", "source_document_ids"),
        ("source_urls_json", "source_urls"),
        ("entity_refs_json", "entity_refs"),
        ("outgoing_links_json", "outgoing_links"),
    ):
        out[list_column] = out[json_column].map(_json_list)
    out["metadata"] = out["metadata_json"].map(_json_dict)
    out["ts_rank_score"] = pd.to_numeric(out["ts_rank_score"], errors="coerce").fillna(0.0)
    return out


def search_prepared_zopedia_pages(
    frame: pd.DataFrame,
    *,
    query: str = "",
    page_types: list[str] | None = None,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> pd.DataFrame:
    out = _ensure_page_frame(frame)
    if out.empty:
        return out
    out = out[out["status"].astype(str).str.lower().fillna("active") != "deleted"].copy()
    normalized_types = {_normalize_page_type(item) for item in list(page_types or []) if _coerce_text(item)}
    if normalized_types:
        out = out[out["page_type"].astype(str).str.lower().isin(normalized_types)].copy()
    if out.empty:
        return out

    query_text = _coerce_text(query).lower()
    query_tokens = [token for token in re.split(r"[^a-z0-9]+", query_text) if len(token) >= 2]

    def score(row: pd.Series) -> float:
        title = _coerce_text(row.get("title")).lower()
        summary = _coerce_text(row.get("summary")).lower()
        body = _coerce_text(row.get("body_markdown")).lower()
        linked = " ".join(list(row.get("entity_refs") or []) + list(row.get("outgoing_links") or [])).lower()
        value = float(row.get("ts_rank_score") or 0.0) * 12.0
        if query_text:
            if query_text in title:
                value += 20.0
            if query_text in summary:
                value += 12.0
            if query_text in body:
                value += 8.0
            token_hits = sum(token in title for token in query_tokens) * 4
            token_hits += sum(token in summary for token in query_tokens) * 3
            token_hits += sum(token in body for token in query_tokens)
            token_hits += sum(token in linked for token in query_tokens) * 2
            value += float(token_hits)
        else:
            value += 1.0
        return value

    out["search_score"] = out.apply(score, axis=1)
    if query_text:
        out = out[out["search_score"] > 0].copy()
    if out.empty:
        return out
    return out.sort_values(
        ["search_score", "updated_at_utc", "title"],
        ascending=[False, False, True],
        na_position="last",
    ).head(max(int(limit), 1)).reset_index(drop=True)


def _select_page_rows_sql(*, where_sql: str, order_sql: str, limit: int | None = None) -> str:
    limit_sql = "LIMIT %s" if limit is not None else ""
    return f"""
        SELECT
            page_id, page_type, title, slug, summary, body_markdown,
            source_document_ids_json::text, source_urls_json::text,
            entity_refs_json::text, outgoing_links_json::text,
            status, version, created_at_utc, updated_at_utc, metadata_json::text,
            0.0 AS ts_rank_score
        FROM saa_zopedia_pages
        WHERE {where_sql}
        ORDER BY {order_sql}
        {limit_sql}
    """


def search_zopedia_pages(
    *,
    query: str = "",
    page_types: list[str] | None = None,
    limit: int = _DEFAULT_SEARCH_LIMIT,
    conn: Any | None = None,
    include_debug_sources: bool = False,
) -> pd.DataFrame:
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    if conn is None:
        return pd.DataFrame(columns=list(ZOPEDIA_PAGE_COLUMNS))
    try:
        bootstrap_zopedia_storage(conn)
        normalized_query = _coerce_text(query)
        normalized_types = [_normalize_page_type(item) for item in list(page_types or []) if _coerce_text(item)]
        rows: list[tuple[Any, ...]]
        visibility_clause = "" if include_debug_sources else "AND COALESCE(metadata_json->>'source_type', '') <> 'product_eval'"
        with conn.cursor() as cur:
            if normalized_query:
                type_clause = ""
                if normalized_types:
                    type_clause = "AND page_type = ANY(%s)"
                cur.execute(
                    f"""
                    SELECT
                        page_id, page_type, title, slug, summary, body_markdown,
                        source_document_ids_json::text, source_urls_json::text,
                        entity_refs_json::text, outgoing_links_json::text,
                        status, version, created_at_utc, updated_at_utc, metadata_json::text,
                        ts_rank_cd(search_tsvector, plainto_tsquery('english', %s)) AS ts_rank_score
                    FROM saa_zopedia_pages
                    WHERE status != 'deleted'
                      {visibility_clause}
                      AND (
                        search_tsvector @@ plainto_tsquery('english', %s)
                        OR title ILIKE %s
                        OR summary ILIKE %s
                        OR body_markdown ILIKE %s
                      )
                      {type_clause}
                    ORDER BY ts_rank_score DESC, updated_at_utc DESC
                    LIMIT %s
                    """,
                    tuple([normalized_query, normalized_query, f"%{normalized_query}%", f"%{normalized_query}%", f"%{normalized_query}%"] + (normalized_types and [normalized_types] or []) + [max(int(limit) * 6, int(limit), 30)]),
                )
                rows = cur.fetchall()
                if not rows:
                    params: list[Any] = []
                    where = "status != 'deleted'"
                    if not include_debug_sources:
                        where += " AND COALESCE(metadata_json->>'source_type', '') <> 'product_eval'"
                    if normalized_types:
                        where += " AND page_type = ANY(%s)"
                        params.append(normalized_types)
                    params.append(max(int(limit) * 40, int(limit), 250))
                    cur.execute(
                        _select_page_rows_sql(
                            where_sql=where,
                            order_sql="updated_at_utc DESC, title ASC",
                            limit=max(int(limit) * 40, int(limit), 250),
                        ),
                        tuple(params),
                    )
                    rows = cur.fetchall()
            else:
                params = []
                where = "status != 'deleted'"
                if not include_debug_sources:
                    where += " AND COALESCE(metadata_json->>'source_type', '') <> 'product_eval'"
                if normalized_types:
                    where += " AND page_type = ANY(%s)"
                    params.append(normalized_types)
                params.append(max(int(limit) * 6, int(limit), 30))
                cur.execute(
                    _select_page_rows_sql(
                        where_sql=where,
                        order_sql="updated_at_utc DESC, title ASC",
                        limit=max(int(limit) * 6, int(limit), 30),
                    ),
                    tuple(params),
                )
                rows = cur.fetchall()
        return search_prepared_zopedia_pages(
            _frame_from_zopedia_page_rows(rows),
            query=normalized_query,
            page_types=normalized_types,
            limit=limit,
        )
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def load_zopedia_page(*, page_id: str, conn: Any | None = None) -> dict[str, Any]:
    normalized_page_id = _coerce_text(page_id)
    if not normalized_page_id:
        return {}
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    if conn is None:
        return {}
    try:
        bootstrap_zopedia_storage(conn, commit=own_conn)
        with conn.cursor() as cur:
            cur.execute(
                _select_page_rows_sql(
                    where_sql="page_id = %s AND status != 'deleted'",
                    order_sql="updated_at_utc DESC",
                    limit=None,
                ),
                (normalized_page_id,),
            )
            row = cur.fetchone()
        if not row:
            return {}
        frame = _frame_from_zopedia_page_rows([row])
        return dict(frame.iloc[0].to_dict()) if not frame.empty else {}
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def list_zopedia_pages(*, limit: int = 30, conn: Any | None = None) -> pd.DataFrame:
    return search_zopedia_pages(query="", limit=limit, conn=conn)


def zopedia_page_neighborhood(*, page_id: str, depth: int = 1, conn: Any | None = None) -> dict[str, Any]:
    seed = load_zopedia_page(page_id=page_id, conn=conn)
    if not seed:
        return {"page_id": _coerce_text(page_id), "nodes": [], "edges": []}
    pages = _ensure_page_frame(list_zopedia_pages(limit=250, conn=conn))
    if pages.empty:
        return {"page_id": seed["page_id"], "nodes": [seed], "edges": []}

    by_id = {str(row.get("page_id")): dict(row) for _, row in pages.iterrows()}
    by_slug = {str(row.get("slug")): str(row.get("page_id")) for _, row in pages.iterrows()}
    by_title = {str(row.get("title")).lower(): str(row.get("page_id")) for _, row in pages.iterrows()}
    node_ids: set[str] = {str(seed["page_id"])}
    edges: list[dict[str, str]] = []
    frontier = {str(seed["page_id"])}
    max_depth = max(int(depth), 1)
    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for current_id in list(frontier):
            current = by_id.get(current_id) or seed
            for raw_target in list(current.get("outgoing_links") or []):
                target = by_id.get(raw_target) and raw_target
                target = target or by_slug.get(raw_target)
                target = target or by_title.get(str(raw_target).lower())
                if not target:
                    continue
                edges.append({"source": current_id, "target": target, "relation": "links_to"})
                if target not in node_ids:
                    node_ids.add(target)
                    next_frontier.add(target)
            for candidate_id, candidate in by_id.items():
                if candidate_id == current_id:
                    continue
                outgoing = set(str(item) for item in list(candidate.get("outgoing_links") or []))
                if current_id in outgoing or str(current.get("slug")) in outgoing or str(current.get("title")) in outgoing:
                    edges.append({"source": candidate_id, "target": current_id, "relation": "backlink"})
                    if candidate_id not in node_ids:
                        node_ids.add(candidate_id)
                        next_frontier.add(candidate_id)
        frontier = next_frontier
        if not frontier:
            break
    nodes = [by_id.get(node_id, seed) for node_id in sorted(node_ids)]
    seen_edges: set[tuple[str, str, str]] = set()
    deduped_edges: list[dict[str, str]] = []
    for edge in edges:
        key = (edge["source"], edge["target"], edge["relation"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        deduped_edges.append(edge)
    return {"page_id": seed["page_id"], "nodes": nodes, "edges": deduped_edges}


def _list_metadata_values(metadata: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = metadata.get(key)
        if raw is None:
            continue
        for item in _json_list(raw):
            if item and item not in values:
                values.append(item)
    return values


def _source_ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    kind = _coerce_text(ref.get("kind"))
    value = (
        _coerce_text(ref.get("page_id"))
        or _coerce_text(ref.get("chunk_record_id"))
        or _coerce_text(ref.get("canonical_document_id"))
        or _coerce_text(ref.get("url"))
        or _coerce_text(ref.get("ref"))
    )
    return kind, value


def _dedupe_source_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = _source_ref_key(ref)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _source_node_ref(ref: dict[str, Any]) -> str:
    kind = _coerce_text(ref.get("kind")) or "source"
    stable_id = (
        _coerce_text(ref.get("page_id"))
        or _coerce_text(ref.get("chunk_record_id"))
        or _coerce_text(ref.get("canonical_document_id"))
        or _coerce_text(ref.get("url"))
        or _coerce_text(ref.get("ref"))
    )
    if not stable_id:
        stable_id = _sha256_text(json.dumps(ref, ensure_ascii=False, sort_keys=True, default=str))[:16]
    if stable_id.startswith("zopedia::"):
        return stable_id
    return f"{kind}::{_sha256_text(stable_id)[:16]}"


def _source_page_ref(
    source_page: dict[str, Any],
    *,
    support_type: str = "source_page",
    inferred: bool = False,
    source_text_limit: int = 1800,
) -> dict[str, Any]:
    metadata = _json_dict(source_page.get("metadata") or source_page.get("metadata_json"))
    source_urls = list(source_page.get("source_urls") or _json_list(source_page.get("source_urls_json")))
    source_url = _coerce_text(metadata.get("source_url")) or (source_urls[0] if source_urls else "")
    body = _coerce_text(source_page.get("body_markdown"))
    return {
        "kind": "zopedia_source_page",
        "ref": _coerce_text(source_page.get("page_id")),
        "page_id": _coerce_text(source_page.get("page_id")),
        "title": _coerce_text(source_page.get("title")),
        "source_title": _coerce_text(metadata.get("source_title")) or _coerce_text(source_page.get("title")),
        "source_type": _coerce_text(metadata.get("source_type")),
        "source_url": source_url,
        "url": source_url,
        "summary_text": _trim(source_page.get("summary"), 420),
        "body_excerpt": _trim(body, source_text_limit) if body else "",
        "support_type": support_type,
        "inferred": inferred,
    }


def _source_refs_for_page(
    page: dict[str, Any],
    *,
    pages: pd.DataFrame | None = None,
    source_text_limit: int = 1800,
) -> list[dict[str, Any]]:
    if not isinstance(page, dict) or not _coerce_text(page.get("page_id")):
        return []

    metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
    page_source_urls = list(page.get("source_urls") or _json_list(page.get("source_urls_json")))
    source_page_ids = _list_metadata_values(metadata, "source_page_id", "source_page_ids")
    source_document_ids = list(page.get("source_document_ids") or _json_list(page.get("source_document_ids_json")))
    chunk_record_ids = _list_metadata_values(metadata, "chunk_record_id", "chunk_record_ids")
    canonical_document_ids = _list_metadata_values(metadata, "canonical_document_id", "canonical_document_ids")
    source_title = _coerce_text(metadata.get("source_title"))
    source_url = _coerce_text(metadata.get("source_url"))
    if source_url and source_url not in page_source_urls:
        page_source_urls.append(source_url)

    all_pages = _ensure_page_frame(pages) if isinstance(pages, pd.DataFrame) else pd.DataFrame()
    source_pages_by_id: dict[str, dict[str, Any]] = {}
    source_pages: list[dict[str, Any]] = []
    if not all_pages.empty:
        for _, row in all_pages.iterrows():
            candidate = dict(row)
            if _coerce_text(candidate.get("page_type")) != "source":
                continue
            source_pages_by_id[_coerce_text(candidate.get("page_id"))] = candidate
            source_pages.append(candidate)

    refs: list[dict[str, Any]] = []
    if _coerce_text(page.get("page_type")) == "source":
        refs.append(_source_page_ref(page, support_type="self", source_text_limit=source_text_limit))

    for source_page_id in source_page_ids:
        source_page = source_pages_by_id.get(source_page_id)
        if source_page:
            refs.append(_source_page_ref(source_page, source_text_limit=source_text_limit))
        else:
            refs.append(
                {
                    "kind": "zopedia_source_page",
                    "ref": source_page_id,
                    "page_id": source_page_id,
                    "title": source_title,
                    "source_title": source_title,
                    "source_url": source_url,
                    "url": source_url,
                    "support_type": "source_page",
                    "missing": True,
                }
            )

    if not source_page_ids and source_pages:
        matched: dict[str, dict[str, Any]] = {}
        for source_page in source_pages:
            candidate_metadata = _json_dict(source_page.get("metadata") or source_page.get("metadata_json"))
            candidate_urls = list(source_page.get("source_urls") or _json_list(source_page.get("source_urls_json")))
            candidate_url = _coerce_text(candidate_metadata.get("source_url")) or (candidate_urls[0] if candidate_urls else "")
            candidate_title = _coerce_text(candidate_metadata.get("source_title")) or _coerce_text(source_page.get("title"))
            if source_title and candidate_title and source_title.lower() == candidate_title.lower():
                matched[_coerce_text(source_page.get("page_id"))] = source_page
            if page_source_urls and candidate_url and candidate_url in page_source_urls:
                matched[_coerce_text(source_page.get("page_id"))] = source_page
        for source_page in matched.values():
            refs.append(_source_page_ref(source_page, inferred=True, source_text_limit=source_text_limit))

    for chunk_record_id in chunk_record_ids:
        refs.append(
            {
                "kind": "retained_evidence_chunk",
                "ref": chunk_record_id,
                "chunk_record_id": chunk_record_id,
                "canonical_document_id": canonical_document_ids[0] if canonical_document_ids else "",
                "source_title": source_title,
                "source_url": source_url,
                "url": source_url,
                "support_type": "chunk",
            }
        )
    for canonical_document_id in list(dict.fromkeys(source_document_ids + canonical_document_ids)):
        refs.append(
            {
                "kind": "retained_source_document",
                "ref": canonical_document_id,
                "canonical_document_id": canonical_document_id,
                "source_title": source_title,
                "source_url": source_url,
                "url": source_url,
                "support_type": "document",
            }
        )
    for url in page_source_urls:
        refs.append(
            {
                "kind": "source_url",
                "ref": url,
                "url": url,
                "source_title": source_title,
                "source_url": url,
                "support_type": "url",
            }
        )
    return _dedupe_source_refs(refs)


def zopedia_sources_for_page(
    *,
    page_id: str,
    conn: Any | None = None,
    source_text_limit: int = 1800,
) -> dict[str, Any]:
    normalized_page_id = _coerce_text(page_id)
    page = load_zopedia_page(page_id=normalized_page_id, conn=conn)
    if not page:
        return {"page_id": normalized_page_id, "status": "not_found", "sources": [], "source_count": 0}
    pages = list_zopedia_pages(limit=500, conn=conn)
    refs = _source_refs_for_page(page, pages=pages, source_text_limit=source_text_limit)
    return {
        "page_id": _coerce_text(page.get("page_id")),
        "status": "found",
        "page": page,
        "sources": refs,
        "source_count": len(refs),
    }


def zopedia_trace_to_evidence(
    *,
    page_id: str,
    depth: int = 1,
    conn: Any | None = None,
    source_text_limit: int = 1200,
) -> dict[str, Any]:
    normalized_page_id = _coerce_text(page_id)
    graph = zopedia_page_neighborhood(page_id=normalized_page_id, depth=depth, conn=conn)
    nodes = [dict(node) for node in list(graph.get("nodes") or []) if isinstance(node, dict)]
    if not nodes:
        return {
            "page_id": normalized_page_id,
            "status": "not_found",
            "nodes": [],
            "source_nodes": [],
            "edges": [],
            "sources_by_page": {},
        }

    pages = list_zopedia_pages(limit=500, conn=conn)
    source_nodes_by_ref: dict[str, dict[str, Any]] = {}
    sources_by_page: dict[str, list[dict[str, Any]]] = {}
    evidence_edges: list[dict[str, str]] = []
    for node in nodes:
        node_id = _coerce_text(node.get("page_id"))
        refs = _source_refs_for_page(node, pages=pages, source_text_limit=source_text_limit)
        sources_by_page[node_id] = refs
        for ref in refs:
            source_ref = _source_node_ref(ref)
            source_node = dict(ref)
            source_node["ref"] = source_ref
            source_nodes_by_ref[source_ref] = source_node
            evidence_edges.append({"source": node_id, "target": source_ref, "relation": "supported_by"})

    seen_edges: set[tuple[str, str, str]] = set()
    edges: list[dict[str, str]] = []
    for edge in list(graph.get("edges") or []) + evidence_edges:
        if not isinstance(edge, dict):
            continue
        normalized_edge = {
            "source": _coerce_text(edge.get("source")),
            "target": _coerce_text(edge.get("target")),
            "relation": _coerce_text(edge.get("relation")) or "links_to",
        }
        key = (normalized_edge["source"], normalized_edge["target"], normalized_edge["relation"])
        if not key[0] or not key[1] or key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append(normalized_edge)

    return {
        "page_id": _coerce_text(graph.get("page_id")) or normalized_page_id,
        "status": "found",
        "nodes": nodes,
        "source_nodes": list(source_nodes_by_ref.values()),
        "edges": edges,
        "sources_by_page": sources_by_page,
    }


def _link_lookup_maps(pages: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    frame = _ensure_page_frame(pages)
    by_id: dict[str, dict[str, Any]] = {}
    by_slug: dict[str, str] = {}
    by_title: dict[str, str] = {}
    if frame.empty:
        return by_id, by_slug, by_title
    for _, row in frame.iterrows():
        item = dict(row)
        page_id = _coerce_text(item.get("page_id"))
        if not page_id:
            continue
        by_id[page_id] = item
        slug = _coerce_text(item.get("slug"))
        title = _coerce_text(item.get("title")).lower()
        if slug and slug not in by_slug:
            by_slug[slug] = page_id
        if title and title not in by_title:
            by_title[title] = page_id
    return by_id, by_slug, by_title


def _resolve_zopedia_link_target(raw_target: object, *, by_id: dict[str, dict[str, Any]], by_slug: dict[str, str], by_title: dict[str, str]) -> str:
    clean = _coerce_text(raw_target)
    if not clean:
        return ""
    return (
        (clean if clean in by_id else "")
        or by_slug.get(clean, "")
        or by_slug.get(_slug(clean, default=""), "")
        or by_title.get(clean.lower(), "")
    )


def _issue_row(
    *,
    issue_type: str,
    page_id: str = "",
    title: str = "",
    severity: str = "medium",
    summary: str = "",
    evidence: dict[str, Any] | None = None,
    suggested_action: str = "review",
) -> dict[str, Any]:
    return {
        "issue_id": f"zopedia_issue::{_slug(issue_type, default='issue')}::{_sha256_text(json.dumps({'page_id': page_id, 'title': title, 'summary': summary, 'evidence': evidence or {}}, sort_keys=True, default=str))[:16]}",
        "issue_type": _slug(issue_type, default="issue").replace("-", "_"),
        "page_id": _coerce_text(page_id),
        "title": _coerce_text(title),
        "severity": _slug(severity, default="medium"),
        "summary": _coerce_text(summary),
        "evidence_json": json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True, default=str),
        "suggested_action": _slug(suggested_action, default="review").replace("-", "_"),
    }


def _page_source_counts(pages: pd.DataFrame) -> dict[str, int]:
    frame = _ensure_page_frame(pages)
    counts: dict[str, int] = {}
    if frame.empty:
        return counts
    records = frame.to_dict("records")
    for page in records:
        page_id = _coerce_text(page.get("page_id"))
        if not page_id:
            continue
        refs = _source_refs_for_page(page, pages=frame, source_text_limit=240)
        counts[page_id] = len(refs)
    return counts


def _connected_components(page_ids: set[str], edges: list[dict[str, Any]]) -> list[set[str]]:
    adjacency = {page_id: set() for page_id in page_ids}
    for edge in edges:
        source = _coerce_text(edge.get("source_page_id") or edge.get("source"))
        target = _coerce_text(edge.get("target_page_id") or edge.get("target"))
        if source in page_ids and target in page_ids:
            adjacency[source].add(target)
            adjacency[target].add(source)
    seen: set[str] = set()
    components: list[set[str]] = []
    for page_id in sorted(page_ids):
        if page_id in seen:
            continue
        stack = [page_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(sorted(adjacency.get(current, set()) - seen))
        components.append(component)
    return components


def _maintenance_report_frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(columns)) if rows else pd.DataFrame(columns=list(columns))


def build_zopedia_maintenance_snapshot(
    pages: pd.DataFrame,
    *,
    run_id: str = "",
    now: datetime | None = None,
    stale_after_days: int = _DEFAULT_STALE_AFTER_DAYS,
    bloat_char_limit: int = _DEFAULT_BLOAT_CHAR_LIMIT,
) -> dict[str, Any]:
    """Build the derived Zopedia maintenance graph and issue report.

    This function is intentionally structural: it does not make market-domain
    claims. It derives link health, source coverage, communities, and review
    candidates from stored page contracts.
    """

    timestamp = now or _utc_now()
    normalized_run_id = _coerce_text(run_id) or f"zopedia-maintenance-{uuid_like_timestamp(timestamp)}"
    frame = _ensure_page_frame(pages)
    if frame.empty:
        report = {
            "run_id": normalized_run_id,
            "status": "empty",
            "page_count": 0,
            "edge_count": 0,
            "issue_count": 0,
            "mutation_count": 0,
            "proposal_count": 0,
            "summary_json": json.dumps({"status": "empty"}, sort_keys=True),
            "issue_rows_json": "[]",
            "mutation_ids_json": "[]",
            "proposal_ids_json": "[]",
            "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
        }
        return {
            "status": "empty",
            "run_id": normalized_run_id,
            "pages": frame,
            "backlinks": _maintenance_report_frame([], ZOPEDIA_BACKLINK_COLUMNS),
            "communities": _maintenance_report_frame([], ZOPEDIA_COMMUNITY_INDEX_COLUMNS),
            "issues": [],
            "report": report,
            "summary": {"status": "empty"},
        }

    active = frame[frame["status"].astype(str).str.lower().fillna("active") != "deleted"].copy()
    by_id, by_slug, by_title = _link_lookup_maps(active)
    source_counts = _page_source_counts(active)
    page_ids = set(by_id.keys())
    page_type_by_id = {page_id: _coerce_text(page.get("page_type")) for page_id, page in by_id.items()}

    link_edges: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for page_id, page in by_id.items():
        title = _coerce_text(page.get("title"))
        for raw_target in list(page.get("outgoing_links") or []):
            target_id = _resolve_zopedia_link_target(raw_target, by_id=by_id, by_slug=by_slug, by_title=by_title)
            if not target_id:
                issue_rows.append(
                    _issue_row(
                        issue_type="broken_link",
                        page_id=page_id,
                        title=title,
                        severity="medium",
                        summary="Stored outgoing link does not resolve to an active Zopedia page.",
                        evidence={"link": _coerce_text(raw_target)},
                        suggested_action="review_link",
                    )
                )
                continue
            if target_id == page_id:
                continue
            key = (page_id, target_id, "links_to")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            link_edges.append(
                {
                    "source_page_id": page_id,
                    "target_page_id": target_id,
                    "relation": "links_to",
                    "weight": 1.0,
                    "confidence": 1.0,
                    "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                    "updated_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                    "metadata_json": json.dumps({"derived_from": "outgoing_links"}, sort_keys=True),
                }
            )

    backlink_edges: list[dict[str, Any]] = []
    for edge in link_edges:
        inverse_key = (_coerce_text(edge["target_page_id"]), _coerce_text(edge["source_page_id"]), "backlink")
        if inverse_key in seen_edges:
            continue
        seen_edges.add(inverse_key)
        backlink_edges.append(
            {
                "source_page_id": inverse_key[0],
                "target_page_id": inverse_key[1],
                "relation": "backlink",
                "weight": float(edge.get("weight") or 1.0),
                "confidence": float(edge.get("confidence") or 1.0),
                "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                "updated_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                "metadata_json": json.dumps({"derived_from": "links_to", "source_relation": "links_to"}, sort_keys=True),
            }
        )
    all_edges = link_edges + backlink_edges

    degree: dict[str, int] = {page_id: 0 for page_id in page_ids}
    for edge in link_edges:
        degree[_coerce_text(edge.get("source_page_id"))] = degree.get(_coerce_text(edge.get("source_page_id")), 0) + 1
        degree[_coerce_text(edge.get("target_page_id"))] = degree.get(_coerce_text(edge.get("target_page_id")), 0) + 1

    safe_stale_days = max(int(stale_after_days or _DEFAULT_STALE_AFTER_DAYS), 1)
    safe_bloat_limit = max(int(bloat_char_limit or _DEFAULT_BLOAT_CHAR_LIMIT), 2000)
    stale_cutoff = pd.Timestamp(timestamp) - pd.Timedelta(days=safe_stale_days)
    for page_id, page in by_id.items():
        page_type = _coerce_text(page.get("page_type"))
        title = _coerce_text(page.get("title"))
        if page_type != "source" and source_counts.get(page_id, 0) <= 0:
            issue_rows.append(
                _issue_row(
                    issue_type="weak_source",
                    page_id=page_id,
                    title=title,
                    severity="high",
                    summary="Page has no retained source references.",
                    evidence={"source_count": 0},
                    suggested_action="attach_source_or_review",
                )
            )
        if page_type != "source" and degree.get(page_id, 0) <= 0:
            issue_rows.append(
                _issue_row(
                    issue_type="orphan_page",
                    page_id=page_id,
                    title=title,
                    severity="medium",
                    summary="Page has no resolved Zopedia links or backlinks.",
                    evidence={"degree": 0},
                    suggested_action="review_links",
                )
            )
        updated_at = pd.to_datetime(page.get("updated_at_utc"), utc=True, errors="coerce")
        if pd.notna(updated_at) and updated_at < stale_cutoff:
            issue_rows.append(
                _issue_row(
                    issue_type="stale_page",
                    page_id=page_id,
                    title=title,
                    severity="low",
                    summary="Page has not been updated within the configured freshness window.",
                    evidence={"updated_at_utc": updated_at.isoformat(), "stale_after_days": safe_stale_days},
                    suggested_action="refresh_sources",
                )
            )
        body_len = len(_coerce_text(page.get("body_markdown")))
        if body_len > safe_bloat_limit:
            issue_rows.append(
                _issue_row(
                    issue_type="bloated_page",
                    page_id=page_id,
                    title=title,
                    severity="medium",
                    summary="Page body is larger than the configured compaction window.",
                    evidence={"body_char_count": body_len, "bloat_char_limit": safe_bloat_limit},
                    suggested_action="compact_with_sources",
                )
            )
        metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
        contradiction_keys = [key for key in ("contradiction", "contradicts_page_id", "contradiction_refs") if metadata.get(key)]
        if contradiction_keys:
            issue_rows.append(
                _issue_row(
                    issue_type="contradiction_candidate",
                    page_id=page_id,
                    title=title,
                    severity="high",
                    summary="Page metadata marks a possible contradiction that needs review.",
                    evidence={"metadata_keys": contradiction_keys},
                    suggested_action="judge_contradiction",
                )
            )

    duplicate_groups: dict[str, list[str]] = {}
    for page_id, page in by_id.items():
        page_type = _coerce_text(page.get("page_type"))
        if page_type == "source":
            continue
        title_key = _slug(page.get("title"), default="")
        if not title_key:
            continue
        duplicate_groups.setdefault(f"{page_type}:{title_key}", []).append(page_id)
    for key, ids in sorted(duplicate_groups.items()):
        if len(ids) <= 1:
            continue
        issue_rows.append(
            _issue_row(
                issue_type="duplicate_candidate",
                page_id=ids[0],
                title=_coerce_text(by_id.get(ids[0], {}).get("title")),
                severity="medium",
                summary="Multiple active pages share the same normalized type and title.",
                evidence={"group_key": key, "page_ids": ids},
                suggested_action="merge_review",
            )
        )

    graph_page_ids = {page_id for page_id in page_ids if page_type_by_id.get(page_id) != "source"}
    components = _connected_components(graph_page_ids, link_edges)
    communities: list[dict[str, Any]] = []
    for component in components:
        if not component:
            continue
        component_edges = [
            edge for edge in link_edges
            if _coerce_text(edge.get("source_page_id")) in component and _coerce_text(edge.get("target_page_id")) in component
        ]
        ranked = sorted(
            component,
            key=lambda pid: (degree.get(pid, 0), source_counts.get(pid, 0), _coerce_text(by_id.get(pid, {}).get("title")).lower()),
            reverse=True,
        )
        central_ids = ranked[: min(3, len(ranked))]
        label = _coerce_text(by_id.get(central_ids[0], {}).get("title")) if central_ids else "Zopedia Community"
        score = float(sum(degree.get(pid, 0) for pid in component) + (sum(source_counts.get(pid, 0) for pid in component) * 0.25))
        community_id = f"zopedia_community::{_sha256_text('|'.join(sorted(component)))[:16]}"
        communities.append(
            {
                "community_id": community_id,
                "label": label,
                "page_ids_json": json.dumps(sorted(component), ensure_ascii=False, sort_keys=True),
                "central_page_ids_json": json.dumps(central_ids, ensure_ascii=False, sort_keys=True),
                "page_count": len(component),
                "edge_count": len(component_edges),
                "source_count": int(sum(source_counts.get(pid, 0) for pid in component)),
                "score": score,
                "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                "updated_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
                "metadata_json": json.dumps(
                    {
                        "godnode_page_id": central_ids[0] if central_ids else "",
                        "godnode_title": label,
                        "centrality": {pid: degree.get(pid, 0) for pid in central_ids},
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
    communities.sort(key=lambda item: (float(item.get("score") or 0.0), int(item.get("page_count") or 0)), reverse=True)

    summary = {
        "status": "ready",
        "page_count": len(active),
        "non_source_page_count": len(graph_page_ids),
        "edge_count": len(all_edges),
        "link_edge_count": len(link_edges),
        "backlink_count": len(backlink_edges),
        "community_count": len(communities),
        "issue_count": len(issue_rows),
        "issue_counts": {},
        "top_communities": [
            {
                "community_id": item["community_id"],
                "label": item["label"],
                "page_count": item["page_count"],
                "score": item["score"],
                "central_page_ids": _json_list(item["central_page_ids_json"]),
            }
            for item in communities[:8]
        ],
    }
    issue_counts: dict[str, int] = {}
    for issue in issue_rows:
        issue_type = _coerce_text(issue.get("issue_type"))
        issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1
    summary["issue_counts"] = issue_counts

    report = {
        "run_id": normalized_run_id,
        "status": "ready",
        "page_count": len(active),
        "edge_count": len(all_edges),
        "issue_count": len(issue_rows),
        "mutation_count": 1,
        "proposal_count": 0,
        "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str),
        "issue_rows_json": json.dumps(issue_rows, ensure_ascii=False, sort_keys=True, default=str),
        "mutation_ids_json": "[]",
        "proposal_ids_json": "[]",
        "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
    }
    return {
        "status": "ready",
        "run_id": normalized_run_id,
        "pages": active,
        "backlinks": _maintenance_report_frame(all_edges, ZOPEDIA_BACKLINK_COLUMNS),
        "communities": _maintenance_report_frame(communities, ZOPEDIA_COMMUNITY_INDEX_COLUMNS),
        "issues": issue_rows,
        "report": report,
        "summary": summary,
    }


def uuid_like_timestamp(timestamp: datetime) -> str:
    return pd.to_datetime(timestamp, utc=True, errors="coerce").strftime("%Y%m%dT%H%M%SZ")


def _replace_zopedia_backlinks(conn: Any, frame: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM saa_zopedia_backlinks")
        if frame.empty:
            return
        records = [
            (
                _coerce_text(row.get("source_page_id")),
                _coerce_text(row.get("target_page_id")),
                _coerce_text(row.get("relation")) or "links_to",
                float(row.get("weight") or 1.0),
                float(row.get("confidence") or 1.0),
                pd.to_datetime(row.get("created_at_utc") or _utc_now(), utc=True, errors="coerce"),
                pd.to_datetime(row.get("updated_at_utc") or _utc_now(), utc=True, errors="coerce"),
                _json_text(row.get("metadata_json"), default=_json_dict(row.get("metadata_json"))),
            )
            for _, row in frame.iterrows()
            if _coerce_text(row.get("source_page_id")) and _coerce_text(row.get("target_page_id"))
        ]
        cur.executemany(
            """
            INSERT INTO saa_zopedia_backlinks (
                source_page_id, target_page_id, relation, weight, confidence,
                created_at_utc, updated_at_utc, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_page_id, target_page_id, relation) DO UPDATE SET
                weight = EXCLUDED.weight,
                confidence = EXCLUDED.confidence,
                updated_at_utc = EXCLUDED.updated_at_utc,
                metadata_json = EXCLUDED.metadata_json
            """,
            records,
        )


def _replace_zopedia_community_index(conn: Any, frame: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM saa_zopedia_community_index")
        if frame.empty:
            return
        records = [
            (
                _coerce_text(row.get("community_id")),
                _coerce_text(row.get("label")) or "Zopedia Community",
                _json_text(row.get("page_ids_json"), default=_json_list(row.get("page_ids_json"))),
                _json_text(row.get("central_page_ids_json"), default=_json_list(row.get("central_page_ids_json"))),
                int(row.get("page_count") or 0),
                int(row.get("edge_count") or 0),
                int(row.get("source_count") or 0),
                float(row.get("score") or 0.0),
                pd.to_datetime(row.get("created_at_utc") or _utc_now(), utc=True, errors="coerce"),
                pd.to_datetime(row.get("updated_at_utc") or _utc_now(), utc=True, errors="coerce"),
                _json_text(row.get("metadata_json"), default=_json_dict(row.get("metadata_json"))),
            )
            for _, row in frame.iterrows()
            if _coerce_text(row.get("community_id"))
        ]
        cur.executemany(
            """
            INSERT INTO saa_zopedia_community_index (
                community_id, label, page_ids_json, central_page_ids_json,
                page_count, edge_count, source_count, score,
                created_at_utc, updated_at_utc, metadata_json
            ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (community_id) DO UPDATE SET
                label = EXCLUDED.label,
                page_ids_json = EXCLUDED.page_ids_json,
                central_page_ids_json = EXCLUDED.central_page_ids_json,
                page_count = EXCLUDED.page_count,
                edge_count = EXCLUDED.edge_count,
                source_count = EXCLUDED.source_count,
                score = EXCLUDED.score,
                updated_at_utc = EXCLUDED.updated_at_utc,
                metadata_json = EXCLUDED.metadata_json
            """,
            records,
        )


def _upsert_zopedia_maintenance_report(conn: Any, report: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO saa_zopedia_maintenance_reports (
                run_id, status, page_count, edge_count, issue_count, mutation_count, proposal_count,
                summary_json, issue_rows_json, mutation_ids_json, proposal_ids_json, created_at_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                page_count = EXCLUDED.page_count,
                edge_count = EXCLUDED.edge_count,
                issue_count = EXCLUDED.issue_count,
                mutation_count = EXCLUDED.mutation_count,
                proposal_count = EXCLUDED.proposal_count,
                summary_json = EXCLUDED.summary_json,
                issue_rows_json = EXCLUDED.issue_rows_json,
                mutation_ids_json = EXCLUDED.mutation_ids_json,
                proposal_ids_json = EXCLUDED.proposal_ids_json
            """,
            (
                _coerce_text(report.get("run_id")),
                _coerce_text(report.get("status")) or "ready",
                int(report.get("page_count") or 0),
                int(report.get("edge_count") or 0),
                int(report.get("issue_count") or 0),
                int(report.get("mutation_count") or 0),
                int(report.get("proposal_count") or 0),
                _json_text(report.get("summary_json"), default=_json_dict(report.get("summary_json"))),
                _json_text(report.get("issue_rows_json"), default=[]),
                _json_text(report.get("mutation_ids_json"), default=[]),
                _json_text(report.get("proposal_ids_json"), default=[]),
                pd.to_datetime(report.get("created_at_utc") or _utc_now(), utc=True, errors="coerce"),
            ),
        )


def persist_zopedia_maintenance_snapshot(
    snapshot: dict[str, Any],
    *,
    conn: Any | None = None,
) -> dict[str, Any]:
    own_conn = False
    db_conn = conn
    if db_conn is None:
        db_conn = _db_connection()
        own_conn = db_conn is not None
    if db_conn is None:
        out = dict(snapshot)
        out["persist_status"] = "no_database"
        return out
    try:
        bootstrap_zopedia_storage(db_conn, commit=False)
        backlinks = snapshot.get("backlinks") if isinstance(snapshot.get("backlinks"), pd.DataFrame) else pd.DataFrame()
        communities = snapshot.get("communities") if isinstance(snapshot.get("communities"), pd.DataFrame) else pd.DataFrame()
        report = dict(snapshot.get("report") or {})
        _replace_zopedia_backlinks(db_conn, backlinks)
        _replace_zopedia_community_index(db_conn, communities)
        audit = build_zopedia_mutation_audit(
            mutation_type="maintenance_index",
            risk_level="safe",
            status="committed",
            actor="zopedia-maintenance",
            source="zopedia.maintenance",
            page_ids=[_coerce_text(page_id) for page_id in list(snapshot.get("pages", pd.DataFrame()).get("page_id", [])) if _coerce_text(page_id)]
            if isinstance(snapshot.get("pages"), pd.DataFrame)
            else [],
            evidence_refs=[{"kind": "zopedia_maintenance_report", "ref": _coerce_text(report.get("run_id"))}],
            before_state=[],
            after_state=[],
            rollback_hint={"strategy": "rebuild_derived_index", "note": "Backlinks and communities are derived from pages."},
        )
        report["mutation_ids_json"] = json.dumps([audit["mutation_id"]], ensure_ascii=False, sort_keys=True)
        report["mutation_count"] = 1
        _upsert_zopedia_maintenance_report(db_conn, report)
        _upsert_zopedia_mutation_audit_records(db_conn, _mutation_audit_records([audit]))
        db_conn.commit()
        out = dict(snapshot)
        out["report"] = report
        out["mutation_audit"] = audit
        out["persist_status"] = "committed"
        return out
    finally:
        if own_conn and db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


def run_zopedia_maintenance(
    *,
    run_id: str = "",
    page_limit: int = _DEFAULT_MAINTENANCE_PAGE_LIMIT,
    stale_after_days: int = _DEFAULT_STALE_AFTER_DAYS,
    bloat_char_limit: int = _DEFAULT_BLOAT_CHAR_LIMIT,
    conn: Any | None = None,
) -> dict[str, Any]:
    own_conn = False
    db_conn = conn
    if db_conn is None:
        db_conn = _db_connection()
        own_conn = db_conn is not None
    if db_conn is None:
        return {"status": "no_database", "run_id": _coerce_text(run_id), "summary": {}, "issues": []}
    try:
        bootstrap_zopedia_storage(db_conn, commit=False)
        pages = list_zopedia_pages(limit=max(int(page_limit or _DEFAULT_MAINTENANCE_PAGE_LIMIT), 1), conn=db_conn)
        snapshot = build_zopedia_maintenance_snapshot(
            pages,
            run_id=run_id,
            stale_after_days=stale_after_days,
            bloat_char_limit=bloat_char_limit,
        )
        return persist_zopedia_maintenance_snapshot(snapshot, conn=db_conn)
    finally:
        if own_conn and db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


def list_zopedia_maintenance_reports(
    *,
    status: str = "",
    limit: int = 10,
    conn: Any | None = None,
) -> pd.DataFrame:
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    if conn is None:
        return pd.DataFrame(columns=list(ZOPEDIA_MAINTENANCE_REPORT_COLUMNS))
    try:
        bootstrap_zopedia_storage(conn)
        normalized_status = _coerce_text(status)
        params: list[Any] = []
        where_sql = ""
        if normalized_status:
            where_sql = "WHERE status = %s"
            params.append(normalized_status)
        params.append(max(int(limit), 1))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT run_id, status, page_count, edge_count, issue_count, mutation_count, proposal_count,
                       summary_json::text, issue_rows_json::text, mutation_ids_json::text,
                       proposal_ids_json::text, created_at_utc
                FROM saa_zopedia_maintenance_reports
                {where_sql}
                ORDER BY created_at_utc DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=list(ZOPEDIA_MAINTENANCE_REPORT_COLUMNS))
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def _retained_document_text(document: dict[str, Any] | None, *, limit: int) -> str:
    if not isinstance(document, dict):
        return ""
    for key in ("raw_text", "provider_text", "display_excerpt", "title"):
        text = _coerce_text(document.get(key))
        if text:
            return _trim(text, limit)
    return ""


def zopedia_read_source(
    *,
    page_id: str = "",
    ref: str = "",
    kind: str = "",
    chunk_record_id: str = "",
    canonical_document_id: str = "",
    url: str = "",
    conn: Any | None = None,
    source_text_limit: int = 6000,
) -> dict[str, Any]:
    """Open the concrete source behind a Zopedia source reference."""

    safe_limit = max(min(int(source_text_limit or 6000), 20_000), 500)
    normalized_page_id = _coerce_text(page_id)
    normalized_ref = _coerce_text(ref)
    normalized_kind = _coerce_text(kind).lower()
    normalized_chunk_id = _coerce_text(chunk_record_id)
    normalized_document_id = _coerce_text(canonical_document_id)
    normalized_url = _coerce_text(url)

    if not normalized_page_id and normalized_ref.startswith("zopedia::"):
        normalized_page_id = normalized_ref
    if not normalized_chunk_id and (normalized_kind == "retained_evidence_chunk" or normalized_ref.startswith("saa_chunk::")):
        normalized_chunk_id = normalized_ref
    if not normalized_document_id and (
        normalized_kind == "retained_source_document" or normalized_ref.startswith("saa_doc::")
    ):
        normalized_document_id = normalized_ref
    if not normalized_url and normalized_kind == "source_url":
        normalized_url = normalized_ref

    if normalized_page_id:
        page = load_zopedia_page(page_id=normalized_page_id, conn=conn)
        if not page:
            return {"status": "not_found", "source_kind": "zopedia_page", "page_id": normalized_page_id}
        metadata = _json_dict(page.get("metadata") or page.get("metadata_json"))
        source_urls = list(page.get("source_urls") or _json_list(page.get("source_urls_json")))
        source_url = _coerce_text(metadata.get("source_url")) or (source_urls[0] if source_urls else "")
        body = _coerce_text(page.get("body_markdown"))
        return {
            "status": "found",
            "source_kind": "zopedia_page",
            "page_id": _coerce_text(page.get("page_id")),
            "page_type": _coerce_text(page.get("page_type")),
            "title": _coerce_text(page.get("title")),
            "source_title": _coerce_text(metadata.get("source_title")) or _coerce_text(page.get("title")),
            "source_url": source_url,
            "url": source_url,
            "text": _trim(body, safe_limit),
            "metadata": metadata,
            "page": page,
        }

    if normalized_chunk_id:
        chunk = load_retained_evidence_chunk(normalized_chunk_id, conn=conn)
        if not chunk:
            return {"status": "not_found", "source_kind": "retained_evidence_chunk", "chunk_record_id": normalized_chunk_id}
        doc_id = _coerce_text(chunk.get("canonical_document_id"))
        document_metadata = load_retained_document_metadata(doc_id, conn=conn) if doc_id else None
        metadata = _json_dict(chunk.get("metadata") or chunk.get("metadata_json"))
        source_url = _coerce_text((document_metadata or {}).get("canonical_url"))
        return {
            "status": "found",
            "source_kind": "retained_evidence_chunk",
            "chunk_record_id": normalized_chunk_id,
            "canonical_document_id": doc_id,
            "title": _coerce_text(chunk.get("title")),
            "source_provider": _coerce_text(chunk.get("source_provider") or chunk.get("search_provider")),
            "source_url": source_url,
            "url": source_url,
            "published_at": _coerce_text(chunk.get("published_at")),
            "published_date": _coerce_text(chunk.get("published_date")),
            "text": _trim(chunk.get("chunk_text"), safe_limit),
            "display_excerpt": _coerce_text(chunk.get("display_excerpt")),
            "metadata": metadata,
            "chunk": chunk,
            "document_metadata": document_metadata or {},
        }

    if normalized_document_id:
        document_metadata = load_retained_document_metadata(normalized_document_id, conn=conn)
        document = load_retained_document(normalized_document_id, conn=conn)
        if not document_metadata and not document:
            return {
                "status": "not_found",
                "source_kind": "retained_source_document",
                "canonical_document_id": normalized_document_id,
            }
        return {
            "status": "found",
            "source_kind": "retained_source_document",
            "canonical_document_id": normalized_document_id,
            "title": _coerce_text((document_metadata or {}).get("title") or (document or {}).get("title")),
            "source_provider": _coerce_text((document_metadata or {}).get("source_provider")),
            "source_url": _coerce_text((document_metadata or {}).get("canonical_url") or (document or {}).get("canonical_url")),
            "url": _coerce_text((document_metadata or {}).get("canonical_url") or (document or {}).get("canonical_url")),
            "published_at": _coerce_text((document_metadata or {}).get("published_at")),
            "published_date": _coerce_text((document_metadata or {}).get("published_date")),
            "text": _retained_document_text(document, limit=safe_limit),
            "document": document or {},
            "document_metadata": document_metadata or {},
        }

    if normalized_url:
        return {
            "status": "external_url",
            "source_kind": "source_url",
            "url": normalized_url,
            "source_url": normalized_url,
            "text": "",
            "note": "Use research.open_page to fetch this URL when fresh page text is needed.",
        }

    return {"status": "missing_ref", "source_kind": normalized_kind or "source"}


def build_zopedia_change_proposal(
    *,
    proposal_type: str,
    page_id: str = "",
    title: str = "",
    rationale: str = "",
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    normalized_type = _slug(proposal_type, default="update").replace("-", "_")
    normalized_title = _coerce_text(title) or f"{normalized_type.title()} proposal"
    normalized_payload = dict(payload or {})
    digest = _sha256_text(
        json.dumps(
            {
                "proposal_type": normalized_type,
                "page_id": _coerce_text(page_id),
                "title": normalized_title,
                "rationale": _coerce_text(rationale),
                "payload": normalized_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )[:16]
    return {
        "proposal_id": f"zopedia_proposal::{normalized_type}::{digest}",
        "proposal_type": normalized_type,
        "page_id": _coerce_text(page_id),
        "title": normalized_title,
        "rationale": _coerce_text(rationale),
        "proposal_payload_json": json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, default=str),
        "status": "open",
        "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
        "updated_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
    }


def persist_zopedia_change_proposals(
    proposals: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    conn: Any | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(list(proposals or []))
    if frame.empty:
        return frame
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    try:
        if conn is not None:
            bootstrap_zopedia_storage(conn)
            records = [
                (
                    _coerce_text(row.get("proposal_id")),
                    _coerce_text(row.get("proposal_type")) or "update",
                    _coerce_text(row.get("page_id")) or None,
                    _coerce_text(row.get("title")) or "Zopedia proposal",
                    _coerce_text(row.get("rationale")) or None,
                    _json_text(row.get("proposal_payload_json"), default=_json_dict(row.get("proposal_payload_json"))),
                    _coerce_text(row.get("status")) or "open",
                    pd.to_datetime(row.get("created_at_utc") or _utc_now(), utc=True, errors="coerce"),
                    pd.to_datetime(row.get("updated_at_utc") or _utc_now(), utc=True, errors="coerce"),
                )
                for _, row in frame.iterrows()
            ]
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO saa_zopedia_change_proposals (
                        proposal_id, proposal_type, page_id, title, rationale,
                        proposal_payload_json, status, created_at_utc, updated_at_utc
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (proposal_id) DO UPDATE SET
                        proposal_type = EXCLUDED.proposal_type,
                        page_id = EXCLUDED.page_id,
                        title = EXCLUDED.title,
                        rationale = EXCLUDED.rationale,
                        proposal_payload_json = EXCLUDED.proposal_payload_json,
                        status = EXCLUDED.status,
                        updated_at_utc = EXCLUDED.updated_at_utc
                    """,
                    records,
                )
            conn.commit()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
    return frame


def list_zopedia_change_proposals(
    *,
    status: str = "open",
    limit: int = 30,
    conn: Any | None = None,
) -> pd.DataFrame:
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    columns = [
        "proposal_id",
        "proposal_type",
        "page_id",
        "title",
        "rationale",
        "proposal_payload_json",
        "status",
        "created_at_utc",
        "updated_at_utc",
    ]
    if conn is None:
        return pd.DataFrame(columns=columns)
    try:
        bootstrap_zopedia_storage(conn)
        normalized_status = _coerce_text(status)
        with conn.cursor() as cur:
            if normalized_status:
                cur.execute(
                    """
                    SELECT proposal_id, proposal_type, page_id, title, rationale,
                           proposal_payload_json::text, status, created_at_utc, updated_at_utc
                    FROM saa_zopedia_change_proposals
                    WHERE status = %s
                    ORDER BY updated_at_utc DESC
                    LIMIT %s
                    """,
                    (normalized_status, max(int(limit), 1)),
                )
            else:
                cur.execute(
                    """
                    SELECT proposal_id, proposal_type, page_id, title, rationale,
                           proposal_payload_json::text, status, created_at_utc, updated_at_utc
                    FROM saa_zopedia_change_proposals
                    ORDER BY updated_at_utc DESC
                    LIMIT %s
                    """,
                    (max(int(limit), 1),),
                )
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=columns)
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def _snapshot_pages(rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            "page_id",
            "page_type",
            "title",
            "slug",
            "summary",
            "body_markdown",
            "source_document_ids_json",
            "source_urls_json",
            "entity_refs_json",
            "outgoing_links_json",
            "status",
            "version",
            "created_at_utc",
            "updated_at_utc",
            "metadata_json",
        ):
            value = row.get(key)
            if isinstance(value, pd.Timestamp):
                value = value.isoformat()
            elif isinstance(value, datetime):
                value = value.isoformat()
            item[key] = value
        snapshots.append(item)
    return snapshots


def _load_zopedia_pages_by_ids(*, page_ids: list[str], conn: Any) -> list[dict[str, Any]]:
    normalized_ids = [_coerce_text(item) for item in list(page_ids or []) if _coerce_text(item)]
    if not normalized_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            _select_page_rows_sql(
                where_sql="page_id = ANY(%s)",
                order_sql="updated_at_utc DESC, title ASC",
                limit=None,
            ),
            (normalized_ids,),
        )
        rows = cur.fetchall()
    frame = _frame_from_zopedia_page_rows(rows)
    return frame.to_dict("records") if not frame.empty else []


def build_zopedia_mutation_audit(
    *,
    mutation_type: str,
    page_ids: list[str] | tuple[str, ...] | None = None,
    evidence_refs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    before_state: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    after_state: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    risk_level: str = "safe",
    status: str = "committed",
    actor: str = "zopedia",
    source: str = "",
    rollback_hint: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    before_rows = _snapshot_pages(list(before_state or []))
    after_rows = _snapshot_pages(list(after_state or []))
    normalized_page_ids: list[str] = []
    for item in list(page_ids or []):
        clean = _coerce_text(item)
        if clean and clean not in normalized_page_ids:
            normalized_page_ids.append(clean)
    for row in before_rows + after_rows:
        clean = _coerce_text(row.get("page_id"))
        if clean and clean not in normalized_page_ids:
            normalized_page_ids.append(clean)

    before_ids = {_coerce_text(row.get("page_id")) for row in before_rows if _coerce_text(row.get("page_id"))}
    default_rollback_hint = {
        "strategy": "restore_before_state_or_archive_new_pages",
        "page_ids": normalized_page_ids,
        "new_page_ids": [item for item in normalized_page_ids if item not in before_ids],
        "restore_page_ids": [item for item in normalized_page_ids if item in before_ids],
    }
    normalized_type = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(mutation_type).lower()).strip("_") or "update"
    normalized_status = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(status).lower()).strip("_") or "committed"
    normalized_risk = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(risk_level).lower()).strip("_") or "safe"
    normalized_evidence_refs = [dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)]
    payload_for_id = {
        "mutation_type": normalized_type,
        "page_ids": normalized_page_ids,
        "evidence_refs": normalized_evidence_refs,
        "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce").isoformat(),
    }
    digest = _sha256_text(json.dumps(payload_for_id, ensure_ascii=False, sort_keys=True, default=str))[:16]
    return {
        "mutation_id": f"zopedia_mutation::{normalized_type}::{digest}",
        "mutation_type": normalized_type,
        "risk_level": normalized_risk,
        "status": normalized_status,
        "actor": _coerce_text(actor) or "zopedia",
        "source": _coerce_text(source),
        "page_ids_json": json.dumps(normalized_page_ids, ensure_ascii=False, sort_keys=True, default=str),
        "evidence_refs_json": json.dumps(normalized_evidence_refs, ensure_ascii=False, sort_keys=True, default=str),
        "before_state_json": json.dumps(before_rows, ensure_ascii=False, sort_keys=True, default=str),
        "after_state_json": json.dumps(after_rows, ensure_ascii=False, sort_keys=True, default=str),
        "rollback_hint_json": json.dumps(rollback_hint or default_rollback_hint, ensure_ascii=False, sort_keys=True, default=str),
        "created_at_utc": pd.to_datetime(timestamp, utc=True, errors="coerce"),
    }


def _mutation_audit_records(
    audits: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[tuple[Any, ...]]:
    records: list[tuple[Any, ...]] = []
    for audit in list(audits or []):
        if not isinstance(audit, dict):
            continue
        records.append(
            (
                _coerce_text(audit.get("mutation_id")),
                _coerce_text(audit.get("mutation_type")) or "update",
                _coerce_text(audit.get("risk_level")) or "safe",
                _coerce_text(audit.get("status")) or "committed",
                _coerce_text(audit.get("actor")) or None,
                _coerce_text(audit.get("source")) or None,
                _json_text(audit.get("page_ids_json"), default=_json_list(audit.get("page_ids_json"))),
                _json_text(audit.get("evidence_refs_json"), default=list(audit.get("evidence_refs") or [])),
                _json_text(audit.get("before_state_json"), default=list(audit.get("before_state") or [])),
                _json_text(audit.get("after_state_json"), default=list(audit.get("after_state") or [])),
                _json_text(audit.get("rollback_hint_json"), default=_json_dict(audit.get("rollback_hint_json"))),
                pd.to_datetime(audit.get("created_at_utc") or _utc_now(), utc=True, errors="coerce"),
            )
        )
    return [record for record in records if record[0]]


def _upsert_zopedia_mutation_audit_records(conn: Any, records: list[tuple[Any, ...]]) -> None:
    if not records:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO saa_zopedia_mutation_audit (
                mutation_id, mutation_type, risk_level, status, actor, source,
                page_ids_json, evidence_refs_json, before_state_json, after_state_json,
                rollback_hint_json, created_at_utc
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb, %s
            )
            ON CONFLICT (mutation_id) DO UPDATE SET
                mutation_type = EXCLUDED.mutation_type,
                risk_level = EXCLUDED.risk_level,
                status = EXCLUDED.status,
                actor = EXCLUDED.actor,
                source = EXCLUDED.source,
                page_ids_json = EXCLUDED.page_ids_json,
                evidence_refs_json = EXCLUDED.evidence_refs_json,
                before_state_json = EXCLUDED.before_state_json,
                after_state_json = EXCLUDED.after_state_json,
                rollback_hint_json = EXCLUDED.rollback_hint_json
            """,
            records,
        )


def persist_zopedia_mutation_audits(
    audits: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    conn: Any | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(list(audits or []), columns=list(ZOPEDIA_MUTATION_AUDIT_COLUMNS))
    if frame.empty:
        return frame
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    try:
        if conn is not None:
            bootstrap_zopedia_storage(conn)
            _upsert_zopedia_mutation_audit_records(conn, _mutation_audit_records(list(audits or [])))
            conn.commit()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass
    return frame


def list_zopedia_mutation_audits(
    *,
    status: str = "",
    mutation_type: str = "",
    limit: int = 30,
    conn: Any | None = None,
) -> pd.DataFrame:
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    if conn is None:
        return pd.DataFrame(columns=list(ZOPEDIA_MUTATION_AUDIT_COLUMNS))
    try:
        bootstrap_zopedia_storage(conn)
        clauses: list[str] = []
        params: list[Any] = []
        normalized_status = _coerce_text(status)
        normalized_type = _coerce_text(mutation_type)
        if normalized_status:
            clauses.append("status = %s")
            params.append(normalized_status)
        if normalized_type:
            clauses.append("mutation_type = %s")
            params.append(normalized_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(int(limit), 1))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT mutation_id, mutation_type, risk_level, status, actor, source,
                       page_ids_json::text, evidence_refs_json::text,
                       before_state_json::text, after_state_json::text,
                       rollback_hint_json::text, created_at_utc
                FROM saa_zopedia_mutation_audit
                {where_sql}
                ORDER BY created_at_utc DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=list(ZOPEDIA_MUTATION_AUDIT_COLUMNS))
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def load_zopedia_mutation_audit(*, mutation_id: str, conn: Any | None = None) -> dict[str, Any]:
    normalized_id = _coerce_text(mutation_id)
    if not normalized_id:
        return {}
    own_conn = False
    if conn is None:
        conn = _db_connection()
        own_conn = conn is not None
    if conn is None:
        return {}
    try:
        bootstrap_zopedia_storage(conn, commit=own_conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mutation_id, mutation_type, risk_level, status, actor, source,
                       page_ids_json::text, evidence_refs_json::text,
                       before_state_json::text, after_state_json::text,
                       rollback_hint_json::text, created_at_utc
                FROM saa_zopedia_mutation_audit
                WHERE mutation_id = %s
                LIMIT 1
                """,
                (normalized_id,),
            )
            row = cur.fetchone()
        if not row:
            return {}
        frame = pd.DataFrame([row], columns=list(ZOPEDIA_MUTATION_AUDIT_COLUMNS))
        return dict(frame.iloc[0].to_dict()) if not frame.empty else {}
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def prepare_zopedia_mutation_rollback_pages(
    *,
    mutation_id: str,
    before_state: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    after_state: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamp = now or _utc_now()
    normalized_mutation_id = _coerce_text(mutation_id)
    before_by_id = {
        _coerce_text(row.get("page_id")): dict(row)
        for row in list(before_state or [])
        if isinstance(row, dict) and _coerce_text(row.get("page_id"))
    }
    after_by_id = {
        _coerce_text(row.get("page_id")): dict(row)
        for row in list(after_state or [])
        if isinstance(row, dict) and _coerce_text(row.get("page_id"))
    }
    rollback_pages: list[dict[str, Any]] = []
    for page_id, before_row in before_by_id.items():
        restored = dict(before_row)
        metadata = _json_dict(restored.get("metadata") or restored.get("metadata_json"))
        metadata["rollback_of_mutation_id"] = normalized_mutation_id
        restored["metadata"] = metadata
        restored["updated_at_utc"] = timestamp
        rollback_pages.append(restored)
    for page_id, after_row in after_by_id.items():
        if page_id in before_by_id:
            continue
        archived = dict(after_row)
        metadata = _json_dict(archived.get("metadata") or archived.get("metadata_json"))
        metadata["rollback_of_mutation_id"] = normalized_mutation_id
        metadata["rollback_previous_status"] = _coerce_text(archived.get("status")) or "active"
        archived["metadata"] = metadata
        archived["status"] = "deleted"
        archived["updated_at_utc"] = timestamp
        rollback_pages.append(archived)
    return rollback_pages


def _mark_zopedia_mutation_status(conn: Any, *, mutation_id: str, status: str) -> None:
    normalized_id = _coerce_text(mutation_id)
    normalized_status = _coerce_text(status)
    if not normalized_id or not normalized_status:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE saa_zopedia_mutation_audit
            SET status = %s
            WHERE mutation_id = %s
            """,
            (normalized_status, normalized_id),
        )


def rollback_zopedia_mutation(
    *,
    mutation_id: str,
    actor: str = "zopedia",
    source: str = "zopedia.rollback_mutation",
    conn: Any | None = None,
) -> dict[str, Any]:
    normalized_id = _coerce_text(mutation_id)
    if not normalized_id:
        return {"status": "missing_mutation_id", "pages": [], "page_count": 0}
    own_conn = False
    db_conn = conn
    if db_conn is None:
        db_conn = _db_connection()
        own_conn = db_conn is not None
    if db_conn is None:
        return {"status": "no_database", "mutation_id": normalized_id, "pages": [], "page_count": 0}
    timestamp = _utc_now()
    try:
        bootstrap_zopedia_storage(db_conn, commit=False)
        audit = load_zopedia_mutation_audit(mutation_id=normalized_id, conn=db_conn)
        if not audit:
            return {"status": "not_found", "mutation_id": normalized_id, "pages": [], "page_count": 0}
        if _coerce_text(audit.get("status")) == "rolled_back":
            return {"status": "already_rolled_back", "mutation_id": normalized_id, "pages": [], "page_count": 0}
        before_rows = _json_dict_list(audit.get("before_state_json"))
        after_rows = _json_dict_list(audit.get("after_state_json"))
        rollback_pages = prepare_zopedia_mutation_rollback_pages(
            mutation_id=normalized_id,
            before_state=before_rows,
            after_state=after_rows,
            now=timestamp,
        )
        frame, records = prepare_zopedia_pages(rollback_pages, now=timestamp)
        rows = frame.to_dict("records") if not frame.empty else []
        if not records:
            return {"status": "no_changes", "mutation_id": normalized_id, "pages": [], "page_count": 0}
        page_ids = [_coerce_text(row.get("page_id")) for row in rows if _coerce_text(row.get("page_id"))]
        current_rows = _load_zopedia_pages_by_ids(page_ids=page_ids, conn=db_conn)
        rollback_audit = build_zopedia_mutation_audit(
            mutation_type="rollback",
            risk_level="safe",
            status="committed",
            actor=actor,
            source=source,
            page_ids=page_ids,
            evidence_refs=[{"kind": "zopedia_mutation", "mutation_id": normalized_id, "ref": normalized_id}],
            before_state=current_rows,
            after_state=rows,
            rollback_hint={"strategy": "rollback_of_rollback_requires_manual_review", "rolled_back_mutation_id": normalized_id},
            now=timestamp,
        )
        _upsert_zopedia_page_records(db_conn, records, commit=False)
        _mark_zopedia_mutation_status(db_conn, mutation_id=normalized_id, status="rolled_back")
        _upsert_zopedia_mutation_audit_records(db_conn, _mutation_audit_records([rollback_audit]))
        db_conn.commit()
        return {
            "status": "rolled_back",
            "mutation_id": normalized_id,
            "pages": rows,
            "page_count": len(rows),
            "mutation_audit": rollback_audit,
            "rolled_back_mutation": audit,
        }
    finally:
        if own_conn and db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


def _zopedia_mutation_review_proposal(
    *,
    mutation_type: str,
    page_id: str = "",
    title: str = "",
    rationale: str = "",
    payload: dict[str, Any] | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    normalized_type = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(mutation_type).lower()).strip("_") or "update"
    proposal = build_zopedia_change_proposal(
        proposal_type=normalized_type,
        page_id=page_id,
        title=title or f"{normalized_type.replace('_', ' ').title()} review",
        rationale=rationale or "Zopedia needs review before applying this memory change.",
        payload=payload or {},
    )
    frame = persist_zopedia_change_proposals([proposal], conn=conn)
    return {
        "status": "proposed",
        "mutation_type": normalized_type,
        "proposal": proposal,
        "proposal_count": 1,
        "proposal_frame": frame,
        "pages": [],
        "page_count": 0,
        "mutation_audit": {},
    }


def apply_zopedia_typed_mutation(
    *,
    mutation_type: str,
    page_id: str = "",
    target_page_id: str = "",
    pages: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    metadata_patch: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    rationale: str = "",
    payload: dict[str, Any] | None = None,
    actor: str = "zopedia",
    source: str = "zopedia.apply_mutation",
    allow_risky: bool = False,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Apply a typed, audited Zopedia mutation when it is safe to commit.

    Destructive or ambiguous edits are converted to review proposals. This gives
    agents a single mutation contract without allowing silent deletes or rewrites.
    """

    normalized_type = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(mutation_type).lower()).strip("_") or "update"
    normalized_page_id = _coerce_text(page_id)
    normalized_target_id = _coerce_text(target_page_id)
    normalized_payload = dict(payload or {})
    normalized_evidence_refs = [dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)]

    if normalized_type in _RISKY_MUTATION_TYPES and not allow_risky:
        return _zopedia_mutation_review_proposal(
            mutation_type=normalized_type,
            page_id=normalized_page_id,
            title=normalized_payload.get("title") or f"Review {normalized_type.replace('_', ' ')}",
            rationale=rationale or "This mutation can remove, merge, or rewrite Zopedia memory and needs review.",
            payload={
                "mutation_type": normalized_type,
                "page_id": normalized_page_id,
                "target_page_id": normalized_target_id,
                "pages": list(pages or []),
                "metadata_patch": dict(metadata_patch or {}),
                "evidence_refs": normalized_evidence_refs,
                "payload": normalized_payload,
            },
            conn=conn,
        )

    if normalized_type not in _SAFE_MUTATION_TYPES:
        return _zopedia_mutation_review_proposal(
            mutation_type=normalized_type,
            page_id=normalized_page_id,
            title=normalized_payload.get("title") or f"Review {normalized_type.replace('_', ' ')}",
            rationale=rationale or "Zopedia does not have a safe automatic executor for this mutation type yet.",
            payload={
                "mutation_type": normalized_type,
                "page_id": normalized_page_id,
                "target_page_id": normalized_target_id,
                "pages": list(pages or []),
                "metadata_patch": dict(metadata_patch or {}),
                "evidence_refs": normalized_evidence_refs,
                "payload": normalized_payload,
            },
            conn=conn,
        )

    own_conn = False
    db_conn = conn
    if db_conn is None:
        db_conn = _db_connection()
        own_conn = db_conn is not None
    if db_conn is None:
        return {
            "status": "no_database",
            "mutation_type": normalized_type,
            "pages": [],
            "page_count": 0,
            "mutation_audit": {},
        }

    timestamp = _utc_now()
    try:
        bootstrap_zopedia_storage(db_conn, commit=False)
        after_pages: list[dict[str, Any]] = []
        before_page_ids: list[str] = []

        if normalized_type == "upsert_pages":
            raw_pages = [dict(item) for item in list(pages or []) if isinstance(item, dict)]
            if not raw_pages:
                return _zopedia_mutation_review_proposal(
                    mutation_type=normalized_type,
                    title="Review empty page upsert",
                    rationale=rationale or "No pages were supplied for the upsert mutation.",
                    payload={"mutation_type": normalized_type, "payload": normalized_payload},
                    conn=db_conn,
                )
            prepared_frame, _ = prepare_zopedia_pages(raw_pages, now=timestamp)
            before_page_ids = [
                _coerce_text(row.get("page_id"))
                for _, row in prepared_frame.iterrows()
                if _coerce_text(row.get("page_id"))
            ]
            after_pages = raw_pages

        elif normalized_type == "link_pages":
            if not normalized_page_id or not normalized_target_id:
                return _zopedia_mutation_review_proposal(
                    mutation_type=normalized_type,
                    page_id=normalized_page_id,
                    title="Review incomplete link mutation",
                    rationale=rationale or "A link mutation needs both page_id and target_page_id.",
                    payload={
                        "mutation_type": normalized_type,
                        "page_id": normalized_page_id,
                        "target_page_id": normalized_target_id,
                        "payload": normalized_payload,
                    },
                    conn=db_conn,
                )
            existing = _load_zopedia_pages_by_ids(page_ids=[normalized_page_id, normalized_target_id], conn=db_conn)
            by_id = {_coerce_text(row.get("page_id")): dict(row) for row in existing}
            source_page = by_id.get(normalized_page_id)
            target_page = by_id.get(normalized_target_id)
            if not source_page or not target_page:
                return _zopedia_mutation_review_proposal(
                    mutation_type=normalized_type,
                    page_id=normalized_page_id,
                    title="Review unresolved Zopedia link",
                    rationale=rationale or "The source or target page was not found.",
                    payload={
                        "mutation_type": normalized_type,
                        "page_id": normalized_page_id,
                        "target_page_id": normalized_target_id,
                        "missing_source": not bool(source_page),
                        "missing_target": not bool(target_page),
                    },
                    conn=db_conn,
                )
            outgoing_links = list(source_page.get("outgoing_links") or _json_list(source_page.get("outgoing_links_json")))
            if normalized_target_id not in outgoing_links:
                outgoing_links.append(normalized_target_id)
            metadata = _json_dict(source_page.get("metadata") or source_page.get("metadata_json"))
            metadata["last_typed_mutation"] = {
                "mutation_type": normalized_type,
                "target_page_id": normalized_target_id,
                "rationale": _coerce_text(rationale),
                "updated_at_utc": timestamp.isoformat(),
            }
            updated_page = dict(source_page)
            updated_page["outgoing_links"] = outgoing_links
            updated_page["metadata"] = metadata
            updated_page["updated_at_utc"] = timestamp
            after_pages = [updated_page]
            before_page_ids = [normalized_page_id]

        elif normalized_type == "metadata_patch":
            if not normalized_page_id:
                return _zopedia_mutation_review_proposal(
                    mutation_type=normalized_type,
                    title="Review metadata mutation without page",
                    rationale=rationale or "A metadata patch needs a page_id.",
                    payload={"mutation_type": normalized_type, "metadata_patch": dict(metadata_patch or {})},
                    conn=db_conn,
                )
            existing = _load_zopedia_pages_by_ids(page_ids=[normalized_page_id], conn=db_conn)
            if not existing:
                return _zopedia_mutation_review_proposal(
                    mutation_type=normalized_type,
                    page_id=normalized_page_id,
                    title="Review metadata patch for missing page",
                    rationale=rationale or "The target page was not found.",
                    payload={"mutation_type": normalized_type, "metadata_patch": dict(metadata_patch or {})},
                    conn=db_conn,
                )
            existing_page = dict(existing[0])
            metadata = _json_dict(existing_page.get("metadata") or existing_page.get("metadata_json"))
            for key, value in dict(metadata_patch or {}).items():
                clean_key = _coerce_text(key)
                if clean_key:
                    metadata[clean_key] = value
            metadata["last_typed_mutation"] = {
                "mutation_type": normalized_type,
                "rationale": _coerce_text(rationale),
                "updated_at_utc": timestamp.isoformat(),
            }
            updated_page = dict(existing_page)
            updated_page["metadata"] = metadata
            updated_page["updated_at_utc"] = timestamp
            after_pages = [updated_page]
            before_page_ids = [normalized_page_id]

        before_rows = _load_zopedia_pages_by_ids(page_ids=before_page_ids, conn=db_conn)
        frame, records = prepare_zopedia_pages(after_pages, now=timestamp)
        rows = frame.to_dict("records") if not frame.empty else []
        if not records:
            return {
                "status": "empty",
                "mutation_type": normalized_type,
                "pages": [],
                "page_count": 0,
                "mutation_audit": {},
            }
        audit = build_zopedia_mutation_audit(
            mutation_type=normalized_type,
            risk_level="safe",
            status="committed",
            actor=actor,
            source=source,
            page_ids=[_coerce_text(row.get("page_id")) for row in rows if _coerce_text(row.get("page_id"))],
            evidence_refs=normalized_evidence_refs,
            before_state=before_rows,
            after_state=rows,
            now=timestamp,
        )
        _upsert_zopedia_page_records(db_conn, records, commit=False)
        _upsert_zopedia_mutation_audit_records(db_conn, _mutation_audit_records([audit]))
        db_conn.commit()
        return {
            "status": "committed",
            "mutation_type": normalized_type,
            "pages": rows,
            "page_count": len(rows),
            "mutation_audit": audit,
        }
    finally:
        if own_conn and db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


_ZOPEDIA_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page_type": {
                        "type": "string",
                        "enum": sorted(_VALID_PAGE_TYPES),
                    },
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "body_markdown": {"type": "string"},
                    "entity_refs": {"type": "array", "items": {"type": "string"}},
                    "outgoing_links": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["page_type", "title", "summary", "body_markdown", "entity_refs", "outgoing_links"],
            },
        }
    },
    "required": ["pages"],
}


def extract_zopedia_pages_from_source(
    *,
    title: str,
    source_text: str,
    url: str = "",
    source_type: str = "source",
    llm_client: Any | None = None,
) -> list[dict[str, Any]]:
    clean_text = _trim(source_text, _SOURCE_BODY_LIMIT)
    if not clean_text or llm_client is None or not hasattr(llm_client, "generate_json"):
        return []
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "You convert source material into concise Zopedia wiki pages for a financial research product. "
                "Extract durable concepts, entities, tickers, macro themes, market events, and source notes. "
                "Use only facts in the supplied source. Do not invent market conclusions. "
                "Each page must be useful on its own and include links by page title when another extracted page is relevant."
            ),
            user_prompt=json.dumps(
                {
                    "title": _coerce_text(title),
                    "url": _coerce_text(url),
                    "source_type": _coerce_text(source_type) or "source",
                    "source_text": clean_text,
                },
                ensure_ascii=False,
            ),
            schema_name="zopedia_source_pages",
            schema=_ZOPEDIA_SOURCE_SCHEMA,
        )
    except Exception:
        return []
    pages = list(data.get("pages") or []) if isinstance(data, dict) else []
    return [page for page in pages if isinstance(page, dict) and _coerce_text(page.get("title"))]


def _source_page(
    *,
    title: str,
    source_text: str,
    url: str = "",
    source_type: str = "source",
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = _coerce_text(title) or _coerce_text(url) or "Untitled Source"
    clean_url = _coerce_text(url)
    clean_text = _trim(source_text, _SOURCE_BODY_LIMIT)
    metadata = {
        "source_type": _coerce_text(source_type) or "source",
        "source_title": clean_title,
        "source_url": clean_url,
    }
    for key, value in _json_dict(source_metadata).items():
        clean_key = _coerce_text(key)
        if clean_key and clean_key not in metadata and value not in (None, ""):
            metadata[clean_key] = value
    return {
        "page_type": "source",
        "title": clean_title,
        "summary": _trim(clean_text, 420),
        "body_markdown": clean_text,
        "source_urls": [clean_url] if clean_url else [],
        "entity_refs": [],
        "outgoing_links": [],
        "metadata": metadata,
    }


def ingest_zopedia_source(
    *,
    title: str,
    source_text: str,
    url: str = "",
    source_type: str = "source",
    source_metadata: dict[str, Any] | None = None,
    llm_client: Any | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    clean_text = _coerce_text(source_text)
    if not clean_text:
        return {
            "status": "no_source_text",
            "message": "Zopedia needs source text before it can store a page.",
            "pages": [],
            "page_count": 0,
            "enrichment_status": "not_started",
        }
    source_title = _coerce_text(title) or _coerce_text(url) or "Untitled Source"
    source_page_id = build_zopedia_page_id(page_type="source", title=source_title)
    extracted_pages = extract_zopedia_pages_from_source(
        title=source_title,
        source_text=clean_text,
        url=url,
        source_type=source_type,
        llm_client=llm_client,
    )
    source_page = _source_page(
        title=source_title,
        source_text=clean_text,
        url=url,
        source_type=source_type,
        source_metadata=source_metadata,
    )
    source_page["page_id"] = source_page_id
    clean_source_metadata = _json_dict(source_metadata)
    enriched_pages: list[dict[str, Any]] = []
    for page in extracted_pages[:12]:
        enriched = dict(page)
        metadata = _json_dict(enriched.get("metadata") or enriched.get("metadata_json"))
        metadata.setdefault("source_page_id", source_page_id)
        metadata.setdefault("source_page_title", source_title)
        metadata.setdefault("source_title", source_title)
        metadata.setdefault("source_type", _coerce_text(source_type) or "source")
        for key, value in clean_source_metadata.items():
            clean_key = _coerce_text(key)
            if clean_key and clean_key not in metadata and value not in (None, ""):
                metadata[clean_key] = value
        if _coerce_text(url):
            metadata.setdefault("source_url", _coerce_text(url))
            source_urls = _json_list(enriched.get("source_urls") or enriched.get("source_urls_json"))
            if _coerce_text(url) not in source_urls:
                source_urls.append(_coerce_text(url))
            enriched["source_urls"] = source_urls
        enriched["metadata"] = metadata
        enriched_pages.append(enriched)
    pages = [source_page]
    pages.extend(enriched_pages)
    timestamp = _utc_now()
    frame, records = prepare_zopedia_pages(
        pages,
        now=timestamp,
        source_title=source_title,
        source_url=_coerce_text(url),
    )
    rows = frame.to_dict("records") if not frame.empty else []
    page_ids = [_coerce_text(row.get("page_id")) for row in rows if _coerce_text(row.get("page_id"))]
    evidence_refs = [
        {
            "kind": "zopedia_source_page",
            "page_id": source_page_id,
            "title": source_title,
            "source_type": _coerce_text(source_type) or "source",
        }
    ]
    if _coerce_text(url):
        evidence_refs.append({"kind": "source_url", "url": _coerce_text(url), "title": source_title})
    own_conn = False
    db_conn = conn
    if db_conn is None:
        db_conn = _db_connection()
        own_conn = db_conn is not None
    before_rows: list[dict[str, Any]] = []
    mutation_audit = build_zopedia_mutation_audit(
        mutation_type="ingest_source",
        risk_level="safe",
        status="prepared" if rows else "empty",
        actor="zopedia",
        source="zopedia.ingest_source",
        page_ids=page_ids,
        evidence_refs=evidence_refs,
        before_state=[],
        after_state=rows,
        now=timestamp,
    )
    try:
        if db_conn is not None and records:
            bootstrap_zopedia_storage(db_conn, commit=False)
            before_rows = _load_zopedia_pages_by_ids(page_ids=page_ids, conn=db_conn)
            mutation_audit = build_zopedia_mutation_audit(
                mutation_type="ingest_source",
                risk_level="safe",
                status="committed",
                actor="zopedia",
                source="zopedia.ingest_source",
                page_ids=page_ids,
                evidence_refs=evidence_refs,
                before_state=before_rows,
                after_state=rows,
                now=timestamp,
            )
            _upsert_zopedia_page_records(db_conn, records, commit=False)
            _upsert_zopedia_mutation_audit_records(db_conn, _mutation_audit_records([mutation_audit]))
            db_conn.commit()
    finally:
        if own_conn and db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass
    return {
        "status": "stored" if rows else "empty",
        "pages": rows,
        "page_count": len(rows),
        "enrichment_status": "llm_enriched" if extracted_pages else "source_only",
        "source_title": source_title,
        "url": _coerce_text(url),
        "mutation_audit": mutation_audit,
    }


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(_coerce_text(url))
    host = parsed.netloc.lower().replace("www.", "")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0]
    if host.endswith("youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            return parts[1]
    return ""


def _caption_tracks_from_watch_html(text: str) -> list[dict[str, Any]]:
    marker = "ytInitialPlayerResponse"
    marker_index = text.find(marker)
    if marker_index >= 0:
        json_start = text.find("{", marker_index)
        if json_start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(text[json_start:])
                tracks = (
                    data.get("captions", {})
                    .get("playerCaptionsTracklistRenderer", {})
                    .get("captionTracks", [])
                )
                if isinstance(tracks, list):
                    return [track for track in tracks if isinstance(track, dict)]
            except Exception:
                pass
    match = re.search(r'"captionTracks"\s*:\s*(\[.*?\])\s*,\s*"audioTracks"', text)
    if not match:
        return []
    try:
        tracks = json.loads(match.group(1))
    except Exception:
        return []
    return [track for track in tracks if isinstance(track, dict)] if isinstance(tracks, list) else []


def _parse_caption_payload(text: str) -> str:
    clean = _coerce_text(text)
    if not clean:
        return ""
    try:
        data = json.loads(clean)
    except Exception:
        data = None
    if isinstance(data, dict):
        segments: list[str] = []
        for event in list(data.get("events") or []):
            if not isinstance(event, dict):
                continue
            piece = "".join(
                str(seg.get("utf8") or "")
                for seg in list(event.get("segs") or [])
                if isinstance(seg, dict)
            )
            if piece:
                segments.append(piece)
        return " ".join(" ".join(segments).split())
    try:
        root = ET.fromstring(clean)
    except Exception:
        return ""
    segments = [
        html.unescape("".join(node.itertext())).strip()
        for node in root.iter()
        if node.tag.endswith("text")
    ]
    return " ".join(" ".join(segment for segment in segments if segment).split())


def fetch_youtube_transcript(
    url: str,
    *,
    languages: tuple[str, ...] = ("en", "en-US", "en-GB"),
    timeout: int = 12,
    requests_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return {"status": "invalid_url", "video_id": "", "transcript": ""}
    provider_errors: list[str] = []
    if requests_get is None and YouTubeTranscriptApi is not None:
        try:
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=list(languages))
            segments: list[str] = []
            for item in fetched:
                text = ""
                if isinstance(item, dict):
                    text = _coerce_text(item.get("text"))
                else:
                    text = _coerce_text(getattr(item, "text", ""))
                if text:
                    segments.append(text)
            transcript = " ".join(" ".join(segments).split())
            if transcript:
                return {
                    "status": "ok",
                    "video_id": video_id,
                    "transcript": transcript,
                    "caption_language": _coerce_text(getattr(fetched, "language_code", "")),
                    "caption_name": _coerce_text(getattr(fetched, "language", "")),
                    "provider": "youtube_transcript_api",
                }
        except Exception as exc:
            provider_errors.append(f"youtube_transcript_api:{type(exc).__name__}")
    get = requests_get or (requests.get if requests is not None else None)
    if get is None:
        return {"status": "unavailable", "video_id": video_id, "transcript": ""}
    watch_url = f"{_YOUTUBE_WATCH_URL}?{urlencode({'v': video_id})}"
    try:
        watch_response = get(watch_url, timeout=timeout)
        watch_text = getattr(watch_response, "text", "") or ""
    except Exception as exc:
        return {"status": "watch_fetch_failed", "video_id": video_id, "transcript": "", "error": str(exc), "provider_errors": provider_errors}
    tracks = _caption_tracks_from_watch_html(watch_text)
    if not tracks:
        return {"status": "no_caption_tracks", "video_id": video_id, "transcript": "", "provider_errors": provider_errors}
    preferred = None
    for language in languages:
        preferred = next((track for track in tracks if _coerce_text(track.get("languageCode")) == language), None)
        if preferred:
            break
    preferred = preferred or tracks[0]
    caption_url = _coerce_text(preferred.get("baseUrl"))
    if not caption_url:
        return {"status": "no_caption_url", "video_id": video_id, "transcript": "", "provider_errors": provider_errors}
    if "fmt=" not in caption_url:
        separator = "&" if "?" in caption_url else "?"
        caption_url = f"{caption_url}{separator}fmt=json3"
    try:
        caption_response = get(caption_url, timeout=timeout)
        caption_text = getattr(caption_response, "text", "") or ""
    except Exception as exc:
        return {"status": "caption_fetch_failed", "video_id": video_id, "transcript": "", "error": str(exc), "provider_errors": provider_errors}
    transcript = _parse_caption_payload(caption_text)
    status = "ok" if transcript else "empty_caption_payload"
    if not transcript and provider_errors:
        status = provider_errors[0].split(":", 1)[1].lower()
    return {
        "status": status,
        "video_id": video_id,
        "transcript": transcript,
        "caption_language": _coerce_text(preferred.get("languageCode")),
        "caption_name": _coerce_text(preferred.get("name")),
        "provider": "watch_caption_tracks",
        "provider_errors": provider_errors,
    }


__all__ = [
    "ZOPEDIA_BACKLINK_COLUMNS",
    "ZOPEDIA_COMMUNITY_INDEX_COLUMNS",
    "ZOPEDIA_MAINTENANCE_REPORT_COLUMNS",
    "ZOPEDIA_MUTATION_AUDIT_COLUMNS",
    "ZOPEDIA_PAGE_COLUMNS",
    "apply_zopedia_typed_mutation",
    "bootstrap_zopedia_storage",
    "build_zopedia_change_proposal",
    "build_zopedia_maintenance_snapshot",
    "build_zopedia_mutation_audit",
    "build_zopedia_page_id",
    "extract_youtube_video_id",
    "extract_zopedia_pages_from_source",
    "fetch_youtube_transcript",
    "ingest_zopedia_source",
    "list_zopedia_change_proposals",
    "list_zopedia_maintenance_reports",
    "list_zopedia_mutation_audits",
    "list_zopedia_pages",
    "load_zopedia_mutation_audit",
    "load_zopedia_page",
    "normalize_zopedia_page",
    "persist_zopedia_maintenance_snapshot",
    "persist_zopedia_change_proposals",
    "persist_zopedia_mutation_audits",
    "persist_zopedia_pages",
    "prepare_zopedia_mutation_rollback_pages",
    "prepare_zopedia_pages",
    "prepare_zopedia_uploaded_source",
    "rollback_zopedia_mutation",
    "run_zopedia_maintenance",
    "search_prepared_zopedia_pages",
    "search_zopedia_pages",
    "zopedia_page_neighborhood",
    "zopedia_read_source",
    "zopedia_sources_for_page",
    "zopedia_trace_to_evidence",
]
