"""
AQL extractor — chunking and claim extraction.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pandas as pd

from .constants import CLAIM_SCHEMA, EmbeddingClient, LLMClient
from .evidence_index import annotate_source_documents, build_evidence_metadata
from ._shared import (
    _candidate_claim_entities,
    _coerce_float,
    _coerce_text,
    _display_excerpt,
    _freshness_score,
    _is_low_signal,
    _is_low_signal_claim_text,
    _is_provider_error_text,
    _json_dumps,
    _merge_text_values,
    _normalize_symbol,
    _safe_list,
    _trim,
)
from .collector import _candidate_company_name, _candidate_subject

DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 18
DEFAULT_MAX_CHUNK_CHARS = 1000
DEFAULT_MAX_CLAIM_CHUNKS = 12


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


def _parse_json_list(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = text
    else:
        parsed = value
    return [item for item in _safe_list(parsed) if _coerce_text(item)]


def _token_overlap_score(text: object, query: object) -> float:
    text_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", _coerce_text(text).lower())
        if len(token) >= 3
    }
    query_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", _coerce_text(query).lower())
        if len(token) >= 3
    }
    if not text_tokens or not query_tokens:
        return 0.0
    overlap = text_tokens & query_tokens
    if not overlap:
        return 0.0
    return min(len(overlap) / max(len(query_tokens), 1), 1.0)


def _rank_chunk_score(
    row: pd.Series,
    *,
    candidate: dict[str, Any],
    asof_time_utc: pd.Timestamp,
) -> float:
    title = _coerce_text(row.get("title"))
    text = _coerce_text(row.get("chunk_text"))
    query_text = _coerce_text(row.get("query_text"))
    authority_rank = int(row.get("authority_rank") or 3)
    freshness = _freshness_score(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"), asof_time_utc)
    symbol = _normalize_symbol(candidate.get("symbol"))
    company_name = _candidate_company_name(candidate)
    title_blob = f"{title} {text}"

    score = 0.0
    score += freshness * 0.38
    score += max(0.0, 0.18 - authority_rank * 0.04)
    score += _token_overlap_score(title_blob, query_text) * 0.2
    if symbol and symbol in title_blob.upper():
        score += 0.14
    if company_name and company_name.lower() in title_blob.lower():
        score += 0.12
    if _parse_json_list(row.get("mentioned_tickers_json")):
        score += 0.05
    if _parse_json_list(row.get("event_tags_json")):
        score += 0.04
    if _coerce_text(row.get("raw_text_origin")) in {"page_text", "provider_text"}:
        score += 0.08
    if _coerce_text(row.get("source_authority_bucket")) == "official":
        score += 0.06
    if _is_low_signal(row.get("title"), text):
        score -= 0.2
    return round(score, 4)


def _rank_evidence_chunks(
    chunks: pd.DataFrame,
    *,
    candidate: dict[str, Any],
    asof_time_utc: pd.Timestamp,
) -> pd.DataFrame:
    if not isinstance(chunks, pd.DataFrame) or chunks.empty:
        return chunks if isinstance(chunks, pd.DataFrame) else pd.DataFrame()
    ranked = chunks.copy()
    ranked["retrieval_score"] = ranked.apply(
        lambda row: _rank_chunk_score(row, candidate=candidate, asof_time_utc=asof_time_utc),
        axis=1,
    )
    ranked = ranked.sort_values(
        ["retrieval_score", "published_at", "authority_rank"],
        ascending=[False, False, True],
        na_position="last",
    ).reset_index(drop=True)
    ranked["retrieval_rank"] = range(1, len(ranked) + 1)
    return ranked


def _documents_from_search_results(
    candidate: dict[str, Any],
    result_rows: list[dict[str, Any]],
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    from ._shared import _evidence_text, _source_authority_bucket
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


def _fallback_claims_from_chunks(
    candidate: dict[str, Any],
    chunks: pd.DataFrame,
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    hypotheses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    company_name = _candidate_company_name(candidate).upper()
    out: list[dict[str, Any]] = []
    hypothesis_names = [_coerce_text(item.get("kind")) for item in hypotheses if _coerce_text(item.get("kind"))]
    ranked_chunks = _rank_evidence_chunks(
        chunks,
        candidate=candidate,
        asof_time_utc=asof_time_utc,
    )
    for _, row in ranked_chunks.head(DEFAULT_MAX_CLAIM_CHUNKS).iterrows():
        text = _coerce_text(row.get("display_excerpt") or row.get("chunk_text"))
        if not text:
            continue
        title = _coerce_text(row.get("title"))
        if (
            _is_provider_error_text(text)
            or _is_provider_error_text(title)
            or _is_low_signal_claim_text(text)
            or _is_low_signal_claim_text(title)
        ):
            continue
        title_blob = f"{title} {text}".upper()
        published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
        freshness = _freshness_score(published_at, asof_time_utc)
        authority_rank = int(row.get("authority_rank") or 3)
        relevance = 0.4
        if symbol and symbol in title_blob:
            relevance += 0.2
        if company_name and company_name in title_blob:
            relevance += 0.2
        if authority_rank <= 1:
            relevance += 0.12
        elif authority_rank == 2:
            relevance += 0.08
        from ._shared import _normalized_text
        if any(token in _normalized_text(title_blob) for token in ("earnings", "guidance", "deal", "approval", "trial", "margin", "checkout", "commentary", "de escalation", "de-escalation", "supply", "yield", "treasury")):
            relevance += 0.1
        if _is_low_signal(row.get("title"), text):
            relevance -= 0.15
        causal = min(0.35 + freshness * 0.35 + max(0.0, 0.2 - authority_rank * 0.05), 0.92)
        claim_type = "cause" if freshness >= 0.75 else "background"
        chunk_id = _coerce_text(row.get("chunk_id"))
        claim_hash = hashlib.sha1(f"{chunk_id}|{text}".encode("utf-8")).hexdigest()[:16]
        out.append(
            {
                "claim_id": f"claim::{claim_hash}",
                "run_id": run_id,
                "bundle_subject": symbol,
                "claim_text": text,
                "claim_type": claim_type,
                "claim_entities": _candidate_claim_entities(candidate),
                "supports_hypothesis": hypothesis_names[0] if hypothesis_names else "unresolved",
                "freshness_class": "same_day" if freshness >= 0.95 else "background",
                "relevance_score": round(min(max(relevance, 0.0), 1.0), 3),
                "causal_score": round(min(max(causal, 0.0), 1.0), 3),
                "confidence_score": round(min(max((relevance + causal) / 2.0, 0.0), 1.0), 3),
                "evidence_chunk_ids": [_coerce_text(row.get("chunk_id"))],
                "is_same_day": bool(freshness >= 0.95),
                "source_authority_bucket": _coerce_text(row.get("source_authority_bucket")) or "web",
                "source": _coerce_text(row.get("source_provider")),
            }
        )
    return out


def _extract_claims(
    candidate: dict[str, Any],
    chunks: pd.DataFrame,
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    hypotheses: list[dict[str, Any]],
    llm_client: LLMClient | None,
) -> list[dict[str, Any]]:
    if chunks.empty:
        return []
    fallback = _fallback_claims_from_chunks(
        candidate,
        chunks,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        hypotheses=hypotheses,
    )
    if llm_client is None:
        return fallback
    system_prompt = (
        "You extract structured market claims from evidence chunks. "
        "Only retain high-signal claims. Prefer same-day explanations over stale context. "
        "Do not emit generic filing labels as claims."
    )
    ranked_chunks = _rank_evidence_chunks(
        chunks,
        candidate=candidate,
        asof_time_utc=asof_time_utc,
    )
    user_prompt = json.dumps(
        {
            "subject": _candidate_subject(candidate),
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "hypotheses": hypotheses[:4],
            "chunks": [
                {
                    "chunk_id": _coerce_text(row.get("chunk_id")),
                    "title": _coerce_text(row.get("title")),
                    "text": _coerce_text(row.get("chunk_text")),
                    "query_text": _coerce_text(row.get("query_text")),
                    "published_at": _coerce_text(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")) else ""),
                    "authority_bucket": _coerce_text(row.get("source_authority_bucket")),
                }
                for _, row in ranked_chunks.head(DEFAULT_MAX_CLAIM_CHUNKS).iterrows()
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_claims",
            schema=CLAIM_SCHEMA,
        )
    except Exception:
        return fallback
    claims: list[dict[str, Any]] = []
    chunk_lookup = {
        _coerce_text(row.get("chunk_id")): row
        for _, row in ranked_chunks.iterrows()
        if _coerce_text(row.get("chunk_id"))
    }
    for item in list(data.get("claims") or [])[:8]:
        if not isinstance(item, dict):
            continue
        claim_text = _trim(item.get("claim_text"), 260)
        if (
            not claim_text
            or _is_provider_error_text(claim_text)
            or _is_low_signal_claim_text(claim_text)
            or re.match(r"^(?:form\s+)?(?:8-k|10-k|10-q|20-f|6-k)\b", claim_text, flags=re.IGNORECASE)
        ):
            continue
        linked_chunk_id = next(iter(chunk_lookup.keys()), "")
        linked_chunk = chunk_lookup.get(linked_chunk_id, {})
        claims.append(
            {
                "claim_id": f"claim::{hashlib.sha1(f'{linked_chunk_id}|{claim_text}'.encode('utf-8')).hexdigest()[:16]}",
                "run_id": run_id,
                "bundle_subject": _normalize_symbol(candidate.get("symbol")),
                "claim_text": claim_text,
                "claim_type": _coerce_text(item.get("claim_type")) or "cause",
                "claim_entities": _merge_text_values(item.get("claim_entities"), _candidate_claim_entities(candidate)),
                "supports_hypothesis": _coerce_text(item.get("supports_hypothesis")) or "unresolved",
                "freshness_class": _coerce_text(item.get("freshness_class")) or ("same_day" if item.get("is_same_day") else "background"),
                "relevance_score": round(min(max(float(item.get("relevance_score") or 0.0), 0.0), 1.0), 3),
                "causal_score": round(min(max(float(item.get("causal_score") or 0.0), 0.0), 1.0), 3),
                "confidence_score": round(min(max(float(item.get("confidence_score") or 0.0), 0.0), 1.0), 3),
                "evidence_chunk_ids": [linked_chunk_id] if linked_chunk_id else [],
                "is_same_day": bool(item.get("is_same_day")),
                "source_authority_bucket": _coerce_text(linked_chunk.get("source_authority_bucket")) or "web",
                "source": _coerce_text(linked_chunk.get("source_provider")),
            }
        )
    return claims or fallback


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
