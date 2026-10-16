from __future__ import annotations

import hashlib
import json
from typing import Any


EVIDENCE_PACK_SCHEMA_VERSION = "aql_evidence_pack_v1"


def resolve_evidence_selection(tool_calls: list[dict[str, Any]], selected_source_ids: list[str] | None = None) -> dict[str, Any]:
    """Resolve tool, capture, document, page and chunk IDs against one catalog.

    None selects all sources; [] selects none. Numeric observations and execution
    outcomes remain available independently. Unresolved IDs retain the available
    evidence and are reported, rather than silently losing it.
    """
    identity_fields = ("tool_call_id", "source_id", "capture_id", "canonical_document_id", "document_id", "page_id", "chunk_record_id", "chunk_id", "ref", "url", "canonical_url")
    requested = None if selected_source_ids is None else {str(x) for x in selected_source_ids}
    resolved: set[str] = set()
    records, catalog = [], []
    for call in tool_calls:
        summary = _summary(call)
        call_ids = {str(call[k]) for k in identity_fields if call.get(k)}
        candidates = summary.get("candidate_sources") or summary.get("evidence_refs") or summary.get("source_links") or []
        selected = []
        for source in candidates:
            if not isinstance(source, dict):
                continue
            identities = call_ids | {str(source[k]) for k in identity_fields if source.get(k)}
            matched = set() if requested is None else requested & identities
            resolved.update(matched)
            catalog.append({k: source[k] for k in (*identity_fields, "title", "published_at", "published_date", "source_kind", "evidence_type") if source.get(k)} | {"tool_call_id": call.get("tool_call_id")})
            if requested is None or matched:
                selected.append(source)
        call_match = set() if requested is None else requested & call_ids
        resolved.update(call_match)
        if requested is None or selected or call_match or not candidates:
            records.append({
                "tool_call_id": call.get("tool_call_id"), "tool_name": call.get("tool_name"),
                "origin_run_id": call.get("origin_run_id"), "origin_turn_id": call.get("origin_turn_id"),
                "carried_forward": call.get("carried_forward", False), "status": call.get("status"),
                "arguments": call.get("arguments") or {},
                "evidence": selected if summary.get("candidate_sources") else summary,
                "original_documents": summary.get("source_documents") or [],
                "retrieval_context": summary.get("retrieval_context") or {},
                "error": call.get("error"),
            })
    unresolved = sorted((requested or set()) - resolved)
    if unresolved:
        records = resolve_evidence_selection(tool_calls)["records"]
    return {"selection": "all" if requested is None else "unresolved" if unresolved else "selected" if requested else "empty",
            "unresolved_source_ids": unresolved, "records": records, "source_catalog": catalog}


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _dedupe_dicts(rows: list[dict[str, Any]], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = ((_clean(row["source_reference_id"]),) if row.get("source_reference_id")
               else tuple(_clean(row.get(item)) for item in keys))
        if not any(key) or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _tool_call_id(call: dict[str, Any]) -> str:
    return _clean(call.get("tool_call_id"))


def _tool_name(call: dict[str, Any]) -> str:
    return _clean(call.get("tool_name"))


def _summary(call: dict[str, Any]) -> dict[str, Any]:
    value = call.get("result_summary")
    return value if isinstance(value, dict) else {}


def _source_links(call: dict[str, Any]) -> list[dict[str, str]]:
    links = _summary(call).get("source_links")
    rows: list[dict[str, str]] = []
    if not isinstance(links, list):
        return rows
    for item in links:
        if not isinstance(item, dict):
            continue
        url = _clean(item.get("url"))
        if not url:
            continue
        rows.append(
            {
                "url": url,
                "label": _clean(item.get("label")) or url,
                "tool_call_id": _tool_call_id(call),
                "tool_name": _tool_name(call),
            }
        )
    return rows


def _evidence_refs(call: dict[str, Any]) -> list[dict[str, Any]]:
    refs = _summary(call).get("evidence_refs")
    rows: list[dict[str, Any]] = []
    if not isinstance(refs, list):
        return rows
    for item in refs:
        if not isinstance(item, dict):
            continue
        stable_id = (
            _clean(item.get("source_reference_id"))
            or _clean(item.get("chunk_record_id"))
            or _clean(item.get("canonical_document_id"))
            or _clean(item.get("ref"))
            or _clean(item.get("url"))
        )
        if not stable_id:
            continue
        row = dict(item)
        row["tool_call_id"] = _tool_call_id(call)
        row["tool_name"] = _tool_name(call)
        rows.append(row)
    return rows


def _tool_trace(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for call in tool_calls:
        summary = _summary(call)
        trace.append(
            {
                "tool_call_id": _tool_call_id(call),
                "tool_name": _tool_name(call),
                "status": _clean(call.get("status")),
                "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                "preview_text": _clean(summary.get("user_preview") or summary.get("preview_text"))[:1000],
                "result_type": _clean(summary.get("result_type")),
                "error": _clean(call.get("error")),
            }
        )
    return trace


def _pack_id(*, run_id: str, query: str, tool_calls: list[dict[str, Any]]) -> str:
    tool_parts = [
        {
            "tool_call_id": _tool_call_id(call),
            "tool_name": _tool_name(call),
            "status": _clean(call.get("status")),
            "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
        }
        for call in tool_calls
    ]
    digest = hashlib.sha256(
        _json_dumps(
            {
                "run_id": run_id,
                "query": query,
                "tool_calls": tool_parts,
            }
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"aqlpack::{digest}"


def build_aql_evidence_pack(
    *,
    run_id: str,
    query: str,
    original_query: str = "",
    surface: str = "zopedia_agent",
    status: str = "",
    model: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    calls = [call for call in list(tool_calls or []) if isinstance(call, dict)]
    completed_calls = [call for call in calls if _clean(call.get("status")) == "completed"]
    evidence_refs: list[dict[str, Any]] = []
    source_links: list[dict[str, str]] = []
    for call in completed_calls:
        evidence_refs.extend(_evidence_refs(call))
        source_links.extend(_source_links(call))

    retained_tools = {"research.prefetched_context", "research.retained_context", "research.search_evidence"}
    live_tools = {"research.live_event_evidence", "research.open_page", "investigator.recent_news"}
    zopedia_tools = {
        "zopedia.search_pages",
        "zopedia.read_page",
        "zopedia.sources_for_page",
        "zopedia.trace_to_evidence",
        "zopedia.neighborhood",
        "zopedia.ingest_source",
        "zopedia.ingest_youtube",
        "zopedia.list_mutations",
        "zopedia.list_maintenance_reports",
        "zopedia.apply_mutation",
        "zopedia.rollback_mutation",
    }
    retained_refs = [ref for ref in evidence_refs if _clean(ref.get("tool_name")) in retained_tools]
    live_refs = [ref for ref in evidence_refs if _clean(ref.get("tool_name")) in live_tools]
    zopedia_refs = [
        ref
        for ref in evidence_refs
        if _clean(ref.get("tool_name")) in zopedia_tools or _clean(ref.get("kind")) == "zopedia_page"
    ]
    proposal_refs = [ref for ref in evidence_refs if _clean(ref.get("kind")) == "zopedia_proposal"]
    mutation_refs = [ref for ref in evidence_refs if _clean(ref.get("kind")) == "zopedia_mutation"]

    pack = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "evidence_pack_id": _pack_id(run_id=run_id, query=query, tool_calls=calls),
        "run_id": _clean(run_id),
        "surface": _clean(surface) or "zopedia_agent",
        "query": _clean(query),
        "original_query": _clean(original_query),
        "status": _clean(status),
        "model": _clean(model),
        "entities": [],
        "retained_chunks": _dedupe_dicts(retained_refs, keys=("chunk_record_id", "canonical_document_id", "ref", "url")),
        "source_documents": _dedupe_dicts(evidence_refs, keys=("canonical_document_id", "ref", "url")),
        "zopedia_pages": _dedupe_dicts(zopedia_refs, keys=("page_id", "ref", "url")),
        "kg_paths": [],
        "live_evidence": _dedupe_dicts(live_refs, keys=("url", "ref", "canonical_document_id")),
        "citations": _dedupe_dicts(source_links, keys=("url",)),
        "data_gaps": [_clean(item) for item in list(limitations or []) if _clean(item)],
        "critique_findings": [],
        "proposals": _dedupe_dicts(proposal_refs, keys=("ref", "page_id", "title")),
        "mutations": _dedupe_dicts(mutation_refs, keys=("ref", "mutation_id", "title")),
        "trace": _tool_trace(calls),
        "error": _clean(error),
    }
    return pack


__all__ = ["EVIDENCE_PACK_SCHEMA_VERSION", "build_aql_evidence_pack"]


def materialize_research_artifacts(
    agent_result: dict[str, Any],
    *,
    candidate_id: str,
    subject: str,
    run_id: str,
    asof_time_utc: Any,
) -> dict[str, Any]:
    """Project the shared engine's exact acquisition into AQL storage rows.

    This is a pure storage adapter. It never searches, calls a model, re-extracts
    claims or treats a rendered preview as the complete evidence.
    """
    import pandas as pd

    documents, chunks, requests, results = [], [], [], []
    aliases: dict[str, list[str]] = {}
    for call in agent_result.get("tool_calls") or []:
        call_id = _clean(call.get("tool_call_id"))
        arguments = call.get("arguments") or {}
        query = _clean(arguments.get("query") or arguments.get("search_text"))
        base = {
            "run_id": run_id,
            "asof_time_utc": asof_time_utc,
            "candidate_id": candidate_id,
            "symbol": subject,
            "request_id": f"{agent_result.get('run_id')}::{call_id}",
            "provider": _clean(call.get("tool_name")),
            "query_text": query,
        }
        if query:
            requests.append(
                {
                    **base,
                    "status": call.get("status"),
                    "error_text": call.get("error") or "",
                }
            )
        if call.get("status") != "completed":
            results.append(
                {
                    **base,
                    "result_id": base["request_id"],
                    "result_kind": "error",
                    "error_text": call.get("error") or "",
                }
            )
            continue
        summary = call.get("result_summary") or {}
        sources = (
            summary.get("source_documents") or summary.get("candidate_sources") or []
        )
        if not sources:
            text = (
                summary.get("evidence_context_text")
                or summary.get("llm_context_text")
                or ""
            )
            if not text:
                continue
            links = summary.get("source_links") or []
            refs = summary.get("evidence_refs") or []
            # An aggregate tool result stays aggregate; it is not attributed to
            # every original URL as though each page contained the entire text.
            sources = [
                {
                    "chunk_text": text,
                    "title": (
                        links[0].get("label")
                        if len(links) == 1
                        else call.get("tool_name")
                    ),
                    "url": links[0].get("url") if len(links) == 1 else "",
                    "source_refs": refs,
                    "source_kind": "research_tool_result",
                }
            ]
        for index, source in enumerate(sources):
            text = (
                source.get("chunk_text")
                or source.get("text")
                or source.get("content")
                or ""
            )
            if not text:
                continue
            identity = f"{agent_result.get('run_id')}::{call_id}::{index}"
            document_id = (
                "agent_source::" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            )
            canonical_id = (
                _clean(source.get("canonical_document_id") or source.get("document_id"))
                or document_id
            )
            chunk_id = (
                _clean(source.get("chunk_record_id") or source.get("chunk_id"))
                or document_id + "::1"
            )
            for alias in (
                call_id,
                source.get("source_id"),
                source.get("chunk_record_id"),
                source.get("chunk_id"),
                source.get("canonical_document_id"),
                document_id,
            ):
                if alias:
                    references = aliases.setdefault(str(alias), [])
                    if chunk_id not in references:
                        references.append(chunk_id)
            published = pd.to_datetime(
                source.get("published_date") or source.get("published_at"),
                utc=True,
                errors="coerce",
            )
            doc = {
                **base,
                "document_id": document_id,
                "canonical_document_id": canonical_id,
                "bundle_subject": subject,
                "title": _clean(
                    source.get("title")
                    or source.get("headline")
                    or call.get("tool_name")
                ),
                "url": _clean(source.get("url")),
                "canonical_url": _clean(source.get("url")),
                "raw_text": text,
                "display_excerpt": text[:240],
                "published_at": published,
                "source_provider": _clean(
                    source.get("source")
                    or source.get("source_provider")
                    or call.get("tool_name")
                ),
                "source_kind": _clean(
                    source.get("source_kind") or source.get("evidence_type")
                )
                or "retained_evidence",
                "source_authority_bucket": _clean(source.get("authority_bucket"))
                or "research",
                "authority_rank": 3,
                "source_refs_json": _json_dumps(
                    source.get("source_refs") or ([{
                        key: source[key] for key in ("chunk_record_id", "canonical_document_id", "source_reference_resource")
                        if source.get(key)
                    }] if source.get("source_references_loaded") is False else [])
                ),
            }
            documents.append(doc)
            chunks.append(
                {
                    **doc,
                    "chunk_id": chunk_id,
                    "chunk_text": text,
                    "chunk_index": 1,
                    "document_chunk_count": 1,
                    "embedding_model": "",
                    "embedding_vector_json": "",
                }
            )
            results.append(
                {
                    **base,
                    "result_id": identity,
                    "result_kind": "result",
                    "title": doc["title"],
                    "snippet": text,
                    "url": doc["url"],
                    "source": doc["source_provider"],
                    "published_at": published,
                    "authority_bucket": doc["source_authority_bucket"],
                }
            )
    return {
        "documents": documents,
        "chunks": pd.DataFrame(chunks),
        "request_rows": requests,
        "result_rows": results,
        "source_aliases": aliases,
    }
