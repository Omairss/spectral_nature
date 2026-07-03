from __future__ import annotations

import hashlib
import json
from typing import Any


EVIDENCE_PACK_SCHEMA_VERSION = "aql_evidence_pack_v1"


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _dedupe_dicts(rows: list[dict[str, Any]], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(_clean(row.get(item)) for item in keys)
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
            _clean(item.get("chunk_record_id"))
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
