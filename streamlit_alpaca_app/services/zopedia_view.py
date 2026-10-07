from __future__ import annotations

from datetime import date, datetime
import json
import math
from typing import Any

import pandas as pd

from services.saa.storage import search_retained_documents
from services.saa.zopedia import (
    browse_zopedia_pages,
    ingest_zopedia_source,
    list_zopedia_change_proposals,
    list_zopedia_maintenance_reports,
    list_zopedia_folders,
    list_zopedia_page_links,
    load_zopedia_page,
    prepare_zopedia_uploaded_source,
)


def _text(value: object, *, limit: int = 4_000) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text[:limit]


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = []
    else:
        parsed = []
    return [_text(item, limit=240) for item in parsed if _text(item, limit=240)][:30]


def _safe(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            return _safe(value.item())
        except Exception:
            return _text(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return _text(value)


def _records(frame: pd.DataFrame, fields: tuple[str, ...], *, limit: int) -> list[dict[str, object]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    return [
        {field: _safe(row.get(field)) for field in fields}
        for row in frame.head(limit).to_dict("records")
    ]


def _page_view(row: dict[str, Any], *, body: bool = False) -> dict[str, Any]:
    page = {
        "page_id": _text(row.get("page_id"), limit=240), "page_type": _text(row.get("page_type"), limit=80),
        "title": _text(row.get("title"), limit=300), "slug": _text(row.get("slug"), limit=300),
        "summary": _text(row.get("summary"), limit=1_200), "status": _text(row.get("status"), limit=40),
        "version": int(row.get("version") or 0), "updated_at": _safe(row.get("updated_at_utc")),
        "source_urls": _json_list(row.get("source_urls_json")),
    }
    if body:
        page["body_markdown"] = _text(row.get("body_markdown"), limit=80_000)
        page["outgoing_links"] = _json_list(row.get("outgoing_links_json"))
    return page


def build_zopedia_page_view(page_id: str) -> dict[str, Any] | None:
    page = load_zopedia_page(page_id=page_id)
    if not page:
        return None
    links = list_zopedia_page_links(page_id=page_id)
    return {**_page_view(page, body=True), "links": [
        {**_page_view(row), "direction": _text(row.get("direction"), limit=20)}
        for row in links.to_dict("records")
    ]}


def build_zopedia_workspace(*, query: str = "", is_admin: bool = False, limit: int = 60, folder: str = "", offset: int = 0) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 60), 100))
    normalized_query = " ".join(str(query or "").split()).strip()[:300]
    pages_frame = browse_zopedia_pages(query=normalized_query, folder_path=folder, limit=safe_limit, offset=offset)
    source_pages_frame = browse_zopedia_pages(query=normalized_query, page_types=["source"], limit=safe_limit)
    folders_frame = list_zopedia_folders()
    current_folder = folder.strip("/") or "wiki"
    folder_name = "Knowledge"
    if not folders_frame.empty:
        current = folders_frame[folders_frame["path"] == current_folder]
        if not current.empty:
            folder_name = str(current.iloc[0]["name"])
        folders_frame = folders_frame[folders_frame["path"].astype(str).str.rsplit("/", n=1).str[0] == current_folder].sort_values(["name", "path"])
    documents_frame = search_retained_documents(query=normalized_query, limit=safe_limit)

    pages: list[dict[str, object]] = []
    if isinstance(pages_frame, pd.DataFrame) and not pages_frame.empty:
        for row in pages_frame.head(safe_limit).to_dict("records"):
            pages.append(_page_view(row))

    documents: list[dict[str, object]] = []
    if isinstance(documents_frame, pd.DataFrame) and not documents_frame.empty:
        for row in documents_frame.head(safe_limit).to_dict("records"):
            documents.append(
                {
                    "document_id": _text(row.get("canonical_document_id"), limit=240),
                    "title": _text(row.get("title"), limit=400),
                    "excerpt": _text(row.get("display_excerpt"), limit=1_200),
                    "url": _text(row.get("canonical_url"), limit=2_000),
                    "source_kind": _text(row.get("source_kind"), limit=80),
                    "provider": _text(row.get("source_provider") or row.get("search_provider"), limit=120),
                    "authority": _text(row.get("source_authority_bucket"), limit=80),
                    "published_at": _safe(row.get("published_at")),
                    "as_of": _safe(row.get("last_asof_time_utc")),
                    "tickers": _json_list(row.get("mentioned_tickers")),
                    "event_tags": _json_list(row.get("event_tags")),
                    "search_score": _safe(row.get("search_score")),
                }
            )

    proposals: list[dict[str, object]] = []
    reports: list[dict[str, object]] = []
    if is_admin:
        proposals = _records(
            list_zopedia_change_proposals(status="open", limit=30),
            ("proposal_id", "proposal_type", "page_id", "title", "rationale", "status", "updated_at_utc"),
            limit=30,
        )
        reports = _records(
            list_zopedia_maintenance_reports(limit=12),
            ("run_id", "status", "page_count", "edge_count", "issue_count", "mutation_count", "proposal_count", "created_at_utc"),
            limit=12,
        )

    return {
        "query": normalized_query,
        "pages": pages,
        "source_pages": [_page_view(row) for row in source_pages_frame.to_dict("records")],
        "folders": _records(folders_frame, ("folder_id", "parent_folder_id", "name", "path", "folder_type"), limit=500),
        "folder": _text(folder, limit=300),
        "folder_name": folder_name,
        "offset": max(0, int(offset)),
        "has_more": bool(not pages_frame.empty and int(pages_frame.iloc[0].get("total_count") or 0) > offset + len(pages)),
        "documents": documents,
        "proposals": proposals,
        "maintenance_reports": reports,
        "counts": {
            "pages": int(pages_frame.iloc[0].get("total_count") or len(pages)) if not pages_frame.empty else 0,
            "documents": len(documents),
            "open_proposals": len(proposals),
        },
    }


def store_uploaded_zopedia_source(
    *,
    filename: str,
    content: bytes,
    content_type: str,
    owner_id: str,
    owner_email: str,
) -> dict[str, Any]:
    prepared = prepare_zopedia_uploaded_source(
        filename=filename,
        content=content,
        content_type=content_type,
    )
    if prepared.get("status") != "ok":
        raise ValueError(_text(prepared.get("message"), limit=500) or "The source could not be read.")
    result = ingest_zopedia_source(
        title=_text(prepared.get("title"), limit=400),
        source_text=_text(prepared.get("source_text"), limit=80_000),
        source_type=_text(prepared.get("source_type"), limit=80) or "uploaded_file",
        source_metadata={
            **dict(prepared.get("metadata") or {}),
            "uploaded_by_user_id": _text(owner_id, limit=160),
            "uploaded_by_email": _text(owner_email, limit=320).lower(),
        },
        llm_client=None,
    )
    return {
        "status": result.get("status"),
        "source_title": result.get("source_title"),
        "page_count": int(result.get("page_count") or 0),
        "enrichment_status": result.get("enrichment_status"),
    }


__all__ = ["build_zopedia_workspace", "store_uploaded_zopedia_source"]
