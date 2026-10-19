"""Source document chunking and claim-frame serialization."""
from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from ..llm import EmbeddingClient
from .constants import LLMClient
from .evidence_index import annotate_source_documents, build_evidence_metadata
from ._shared import (
    _display_excerpt,
    _is_low_signal,
    _is_low_signal_claim_text,
    _is_provider_error_text,
    _merge_text_values,
    _trim,
)
from services.common.market_activity import (
    _coerce_text,
    _json_dumps,
    _normalize_symbol,
    _safe_list,
)

DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 18
DEFAULT_MAX_CHUNK_CHARS = 1000
def _normalize_chunk_pieces(raw_text: str) -> list[str]:
    paragraphs = [piece.strip() for piece in re.split(r"\n\s*\n+", raw_text) if piece.strip()]
    if not paragraphs:
        paragraphs = [raw_text.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        sentences = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", paragraph) if piece.strip()]
        if not sentences:
            sentences = [paragraph]
        for sentence in sentences:
            if len(sentence) <= DEFAULT_MAX_CHUNK_CHARS:
                pieces.append(sentence)
                continue
            clause_parts = [piece.strip() for piece in re.split(r"(?<=[,;:])\s+", sentence) if piece.strip()]
            if clause_parts:
                pieces.extend(clause_parts)
            else:
                pieces.append(sentence)
    return [piece for piece in pieces if piece]


def _document_chunk_texts(raw_text: str, *, max_chunks: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT) -> list[str]:
    pieces = _normalize_chunk_pieces(raw_text)
    if not pieces:
        return []
    filtered_pieces = [piece for piece in pieces if not _is_low_signal_claim_text(piece)]
    if filtered_pieces:
        pieces = filtered_pieces
    chunk_texts: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}".strip() if current else piece
        if current and len(candidate) > DEFAULT_MAX_CHUNK_CHARS:
            chunk_texts.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunk_texts.append(current)
    return [_trim(chunk, DEFAULT_MAX_CHUNK_CHARS) for chunk in chunk_texts[: max(int(max_chunks), 1)] if _coerce_text(chunk)]


def _documents_from_search_results(
    candidate: dict[str, Any],
    result_rows: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    from ._shared import _evidence_text
    out: list[dict[str, Any]] = []
    for row in result_rows:
        title = _coerce_text(row.get("title"))
        snippet = _coerce_text(row.get("snippet"))
        page_text = _coerce_text(row.get("page_text"))
        provider_text = _coerce_text(row.get("provider_text"))
        evidence_preview = provider_text or snippet
        provider_payload_json = _coerce_text(row.get("provider_payload_json"))
        raw_text = page_text or provider_text or _evidence_text(snippet, title)
        raw_text_origin = "page_text" if page_text else ("provider_text" if provider_text else "snippet")
        if not title and not snippet and not page_text and not provider_text:
            continue
        from ._shared import _is_irrelevant_news_text
        if _is_irrelevant_news_text(title, evidence_preview):
            continue
        if _is_provider_error_text(title) or _is_provider_error_text(evidence_preview):
            continue
        if _is_low_signal(title, evidence_preview) and not _normalize_symbol(candidate.get("symbol")) in f"{title} {evidence_preview}".upper():
            continue
        doc_id = f"doc::{_coerce_text(row.get('result_id'))}"
        out.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "bundle_subject": _normalize_symbol(candidate.get("symbol")),
                "document_id": doc_id,
                "source_kind": "search",
                "source_provider": _coerce_text(row.get("source") or row.get("provider")),
                "source_authority_bucket": _coerce_text(row.get("authority_bucket")) or "web",
                "authority_rank": int(row.get("authority_rank") or 3),
                "title": title,
                "url": _coerce_text(row.get("url")),
                "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                "raw_text": raw_text,
                "raw_text_origin": raw_text_origin,
                "raw_text_chars": len(raw_text),
                "display_excerpt": _display_excerpt(raw_text, title),
                "page_text": page_text,
                "browse_mode": _coerce_text(row.get("browse_mode")),
                "browse_warning": _coerce_text(row.get("browse_warning")),
                "search_provider": _coerce_text(row.get("provider")),
                "query_text": _coerce_text(row.get("query_text")),
                "provider_payload_json": provider_payload_json,
                "provider_text": provider_text,
                "source_trace": _json_dumps(
                    {
                        "source": "search",
                        "query_id": _coerce_text(row.get("query_id")),
                        "query_text": _coerce_text(row.get("query_text")),
                        "raw_text_origin": raw_text_origin,
                    }
                ),
            }
        )
    return annotate_source_documents(out, asof_time_utc=asof_time_utc)


def _chunk_source_documents(
    documents: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    embedding_client: EmbeddingClient | None = None,
    llm_client: LLMClient | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for doc in documents:
        raw_text = _coerce_text(doc.get("raw_text"))
        if not raw_text:
            continue
        chunk_texts_for_doc = _document_chunk_texts(raw_text)
        if not chunk_texts_for_doc:
            continue
        chunk_texts = []
        chunk_rows = []
        for idx, piece in enumerate(chunk_texts_for_doc):
            chunk_id = f"{_coerce_text(doc.get('document_id'))}::chunk::{idx + 1}"
            display_excerpt = _display_excerpt(piece, doc.get("title"))
            chunk_text = _trim(piece, DEFAULT_MAX_CHUNK_CHARS)
            if not chunk_text or _is_low_signal_claim_text(chunk_text):
                continue
            metadata = build_evidence_metadata(
                title=doc.get("title"),
                text=chunk_text,
                published_at=doc.get("published_at"),
                bundle_subject=doc.get("bundle_subject"),
                source_kind=doc.get("source_kind"),
                asof_time_utc=asof_time_utc,
                llm_client=llm_client,
            )
            chunk_rows.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof_time_utc,
                    "candidate_id": _coerce_text(doc.get("candidate_id")),
                    "bundle_subject": _coerce_text(doc.get("bundle_subject")),
                    "document_id": _coerce_text(doc.get("document_id")),
                    "canonical_document_id": _coerce_text(doc.get("canonical_document_id")),
                    "canonical_url": _coerce_text(doc.get("canonical_url")),
                    "url_host": _coerce_text(doc.get("url_host")),
                    "chunk_id": chunk_id,
                    "chunk_text": chunk_text,
                    "display_excerpt": display_excerpt or _trim(chunk_text, 180),
                    "chunk_index": idx + 1,
                    "document_chunk_count": len(chunk_texts_for_doc),
                    "source_kind": _coerce_text(doc.get("source_kind")),
                    "source_provider": _coerce_text(doc.get("source_provider")),
                    "source_authority_bucket": _coerce_text(doc.get("source_authority_bucket")),
                    "authority_rank": int(doc.get("authority_rank") or 3),
                    "title": _coerce_text(doc.get("title")),
                    "url": _coerce_text(doc.get("url")),
                    "published_at": pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce"),
                    "query_text": _coerce_text(doc.get("query_text")),
                    "provider_payload_json": _coerce_text(doc.get("provider_payload_json")),
                    "provider_text": _coerce_text(doc.get("provider_text")),
                    "raw_text_origin": _coerce_text(doc.get("raw_text_origin")),
                    "raw_text_chars": int(doc.get("raw_text_chars") or len(raw_text)),
                    "document_identity_sha256": _coerce_text(doc.get("document_identity_sha256")),
                    "document_content_sha256": _coerce_text(doc.get("document_content_sha256")),
                    "provider_payload_sha256": _coerce_text(doc.get("provider_payload_sha256")),
                    "published_date": _coerce_text(metadata.get("published_date") or doc.get("published_date")),
                    "primary_date": _coerce_text(metadata.get("primary_date") or doc.get("primary_date")),
                    "mentioned_tickers_json": _coerce_text(metadata.get("mentioned_tickers_json")),
                    "mentioned_tickers_key": _coerce_text(metadata.get("mentioned_tickers_key")),
                    "mentioned_commodities_json": _coerce_text(metadata.get("mentioned_commodities_json")),
                    "mentioned_commodities_key": _coerce_text(metadata.get("mentioned_commodities_key")),
                    "event_tags_json": _coerce_text(metadata.get("event_tags_json")),
                    "event_tags_key": _coerce_text(metadata.get("event_tags_key")),
                    "mentioned_dates_json": _coerce_text(metadata.get("mentioned_dates_json")),
                    "mentioned_dates_key": _coerce_text(metadata.get("mentioned_dates_key")),
                    "search_provider": _coerce_text(doc.get("search_provider")),
                    "source_trace": _coerce_text(doc.get("source_trace")),
                    "embedding_model": "",
                    "embedding_vector_json": "",
                }
            )
            chunk_texts.append(chunk_text)
        if embedding_client is not None and chunk_rows:
            try:
                vectors = embedding_client.generate_embeddings(chunk_texts)
            except Exception:
                vectors = []
            for row, vector in zip(chunk_rows, vectors):
                row["embedding_model"] = getattr(getattr(embedding_client, "config", object()), "embedding_model", "") or ""
                row["embedding_vector_json"] = _json_dumps(vector)
        rows.extend(chunk_rows)
    return pd.DataFrame(rows)


def _serialize_claims_frame(claims: list[dict[str, Any]], *, asof_time_utc: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for item in claims:
        row = dict(item)
        row["asof_time_utc"] = asof_time_utc
        row["claim_entities_json"] = _json_dumps(item.get("claim_entities") or [])
        row["evidence_chunk_ids_json"] = _json_dumps(item.get("evidence_chunk_ids") or [])
        rows.append(row)
    return pd.DataFrame(rows)


def _claim_entities(claims: list[dict[str, Any]]) -> set[str]:
    entities: set[str] = set()
    for item in claims:
        for entity in _safe_list(item.get("claim_entities")):
            clean = _coerce_text(entity).lower()
            if clean:
                entities.add(clean)
    return entities


def _claim_entities_from_value(value: object) -> list[str]:
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            maybe = json.loads(text)
        except Exception:
            maybe = text
        parsed = maybe
    return [entity for entity in _merge_text_values(parsed) if _coerce_text(entity)]


def _claim_map_from_frame(claims_frame: pd.DataFrame | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(claims_frame, pd.DataFrame) or claims_frame.empty:
        return {}
    claim_map: dict[str, list[dict[str, Any]]] = {}
    for _, row in claims_frame.iterrows():
        symbol = _normalize_symbol(row.get("bundle_subject") or row.get("symbol"))
        if not symbol:
            continue
        entities = _claim_entities_from_value(row.get("claim_entities"))
        if not entities:
            entities = _claim_entities_from_value(row.get("claim_entities_json"))
        claim_map.setdefault(symbol, []).append({"claim_entities": entities})
    return claim_map
