"""Scheduled knowledge editing through the shared AQL/Zopedia research spine.

This adapter supplies an editorial task and materializes its reviewed result.
It does not own a planner, research loop, model client, judge or retry system.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any, Callable

from .aql_zopedia_engine import run_aql_zopedia_structured_agent, research_result_allows_knowledge_write
from .json_utils import to_jsonable
from .aql.evidence_pack import materialize_research_artifacts
from .llm import llm_settings_scope
from .llm_execution import retain_research_artifact
from .saa.storage import bootstrap_saa_storage, persist_retained_evidence_chunks
from .saa.zopedia import (
    ZopediaVersionConflict, apply_zopedia_typed_mutation, load_zopedia_maintenance_batch,
    record_zopedia_semantic_maintenance,
    render_zopedia_page_edit,
    load_zopedia_page,
)
from .work_scope import check_work, WorkScope


_STRINGS = {"type": "array", "items": {"type": "string"}}
_PAGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "page_id": {"type": "string"}, "summary": {"type": "string"},
        "body_markdown": {"type": "string"}, "source_urls": _STRINGS,
        "body_edits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["old_text", "new_text"],
        }},
        "source_document_ids": _STRINGS, "entity_refs": _STRINGS, "outgoing_links": _STRINGS,
        "metadata_patch": {"type": "object"},
    },
    "required": ["page_id", "summary", "source_urls", "source_document_ids", "entity_refs", "outgoing_links"],
    "oneOf": [{"required": ["body_markdown"]}, {"required": ["body_edits"]}],
}
MAINTENANCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "assessments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"page_id": {"type": "string"},
                           "decision": {"type": "string", "enum": ["keep", "edit", "defer"]},
                           "rationale": {"type": "string"},
                           "checks": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False,
                               "properties": {
                                   "subject": {"type": "string"}, "finding": {"type": "string"},
                                   "status": {"type": "string", "enum": ["checked", "unresolved"]},
                                   "assumptions": _STRINGS, "calculation_run_ids": _STRINGS,
                                   "evidence_refs": _STRINGS,
                               },
                               "required": ["subject", "finding", "status", "assumptions", "calculation_run_ids", "evidence_refs"],
                           }}},
            "required": ["page_id", "decision", "rationale", "checks"],
        }},
        "actions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "mutation_type": {"type": "string", "enum": ["edit_pages", "rewrite_pages", "merge_pages"]},
                "page_id": {"type": "string"}, "target_page_id": {"type": "string"},
                "pages": {"type": "array", "items": _PAGE_SCHEMA},
                "expected_versions": {"type": "object", "additionalProperties": {"type": "integer"}},
                "evidence_refs": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"kind": {"type": "string"}, "ref": {"type": "string"}},
                    "required": ["kind", "ref"],
                }},
                "rationale": {"type": "string"},
                "content_accounting": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"source_page_id": {"type": "string"},
                                   "observations_retained": _STRINGS,
                                   "information_removed": {"type": "array", "items": {
                                       "type": "object", "additionalProperties": False,
                                       "properties": {"observation": {"type": "string"}, "reason": {"type": "string"}},
                                       "required": ["observation", "reason"],
                                   }}},
                    "required": ["source_page_id", "observations_retained", "information_removed"],
                }},
            },
            "required": ["mutation_type", "page_id", "target_page_id", "pages", "expected_versions", "evidence_refs", "rationale", "content_accounting"],
        }},
        "unresolved": _STRINGS,
    },
    "required": ["assessments", "actions", "unresolved"],
}


_INSTRUCTIONS = """Maintain the supplied public Zopedia articles as a living, source-backed wiki.
You own the editorial judgment. Leave useful pages alone. Length and age are observations, not reasons by themselves to edit.
This visit is an opportunity to reconsider both evidence and reasoning. Inspect original sources and acquire current primary evidence where it would improve the article; do not treat previous model prose or an inherited URL as verification. Revisit material assumptions, definitions and calculations as well as reported facts. Discover analysis.run_python and execute calculations when they materially affect a conclusion; inspect its actual results and reconsider the economic assumptions. An official published result alone does not explain a discrepancy in the underlying data. An unavailable source or computation leaves that question open while independent improvements can proceed.
For each assessment, checks describes what you actually investigated, the finding, assumptions, evidence references, completed calculation run ids and unresolved work. A check is scoped to its subject; it does not certify the whole article. Do not claim a calculation ran without its successful tool result. previous_refresh is a dated earlier assessment of a specified version, useful for continuity but not an authority. Reconsider its open questions against source evidence. A successful investigation may return keep with no actions. Do not rewrite for the sake of producing a change.
Find and research stale or contradictory assertions when an update would materially improve the page. Read original retained sources; open fresh primary sources when current facts are needed. Keep old observations dated and preserve unresolved uncertainty. A forecast becoming older does not make it false. Distinguish units, denominators, periods, currencies, percentage changes and percentage points. Do not invent updated numbers or silently recalculate them from incompatible inputs.
Editorial revision does not establish which conflicting assertion is true. Neither prose nor metadata takes precedence merely because of its format. Resolve a substantive contradiction against its original evidence; when that evidence is unavailable, preserve the competing dated observations and the unresolved state instead of choosing a winner to make fields agree. Do not overwrite a contested current-value field with an unverified alternative. You can still improve the rest of the article. Retained findings remain useful evidence, but an inherited citation or an unsuccessful page read is not fresh verification.
Compact repetitive material into a complete, coherent article while retaining distinct useful facts, counterevidence, reasoning, dates and citations. Compaction is editorial rewriting, not an extract or an arbitrary length target. Preserve the article's useful depth. Source documents themselves are never edited.
Each supplied article contains its complete body_markdown, not an excerpt. Its source_id identifies the original in research.read_supplied_evidence; use pointer /body_markdown and continue with next_offset if you choose to reopen the body. Inspect the actual supplied content and completed reads before claiming an original is missing. The metadata is a separate stored representation that may contain conflicting assertions; do not let it replace the article text.
For every edited source page, provide content_accounting: identify its distinct observations retained in the replacement, and identify information deliberately removed with the editorial reason. Account for distinct quantities, their periods and definitions, counterparties, conditions, counterevidence and dated updates, including unique information buried inside repeated drafts. This account is for review and audit, not article prose. Review must compare it AND the full replacement against the complete originals; the account is not a substitute for source access. If an older uncertainty statement is resolved elsewhere in the supplied article, keep the resolved dated observation rather than restoring the older claim that it is unknown. Length reduction by itself is not a reason to remove useful knowledge.
Search retained pages when a duplicate is plausible. Merge only pages that truly cover the same subject and scope; related companies, differently dated events, a parent and subsidiary, or competing hypotheses may deserve separate pages. Read both complete pages before merging, using zopedia.read_page with max_chars=100000 for a discovered page; continue reading if next_offset is present. Preserve distinct supported information from both. Choose the existing canonical page; retain its identity and title. The other id will become a recoverable redirect, with its provenance and folder access preserved.
Return an assessment for every supplied article and actual evidence references. Prefer edit_pages for corrections: each page supplies body_edits, a list of old_text/new_text pairs against the ORIGINAL article. Copy each old_text exactly and include enough surrounding text to identify one unique passage. Edits cannot overlap. To insert material, replace an existing passage with that passage plus the addition. Every untouched character is preserved automatically. Use any number of edits for connected sections and update summary/metadata together. An empty body_edits list permits a metadata-only correction. Do not also supply body_markdown. The application assembles the complete candidate for review; inspect its whole meaning and consistency, including sections you did not change. content_accounting describes retained or removed information without reproducing the full article.
For a deliberate full reorganization, rewrite_pages contains complete body_markdown instead. For merge_pages, target_page_id is the canonical article, pages contains exactly that canonical replacement, and expected_versions contains the canonical version and the versions of ALL duplicates being merged into it. page_id names one of those duplicates. The whole group is one atomic action, including more than two pages when appropriate. Versions must come from supplied or fully read pages. Plan each page in at most one action; combine refresh and compaction with merging when useful. Do not write placeholders or call knowledge mutation tools: edits are committed only after shared editorial review and a database revision check.
Use Markdown citations to the supporting sources in the article, retaining appropriate dates and caveats where they belong. Copy source URLs exactly from supplied or acquired evidence. Do not invent a plausible link, reconstruct a path or substitute a product homepage for an unread source. This applies to the replacement body, source_urls and reviewer revisions. Evidence refs name supplied or acquired page/document ids or URLs; original provenance will also be retained independently. If new research fails, a grounded compaction or clearly supported merge can still be useful; defer only the unresolved edit. Keep all pending substantive questions in unresolved. Return the requested JSON artifact, without process commentary inside page prose.
If facts or dates also exist in page metadata, keep that structured knowledge consistent using the replacement's metadata_patch. Set a key to null to remove a superseded assertion rather than leaving contradictory structured data behind. Preserve useful unique metadata from merged pages where appropriate. Page identity, provenance, visibility and ownership are managed by the application, not by your patch.
"""


def preview_zopedia_maintenance(pages: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """Give shared review the same complete bodies the storage owner assembles."""
    originals = {page["page_id"]: page for page in pages}
    candidates, errors = [], []
    for index, action in enumerate(payload.get("actions") or []):
        for replacement in action.get("pages") or []:
            page_id = replacement.get("page_id")
            try:
                if "body_edits" in replacement and page_id not in originals:
                    raise ValueError("Section edits require the original article in the supplied batch.")
                candidate = render_zopedia_page_edit(originals.get(page_id, {}), replacement)
                candidates.append({"page_id": page_id, "summary": candidate.get("summary"),
                                   "body_markdown": candidate.get("body_markdown"),
                                   "original_metadata": (originals.get(page_id) or {}).get("metadata") or {},
                                   "metadata_patch": replacement.get("metadata_patch") or {}})
            except ValueError as exc:
                errors.append({"action": index, "page_id": page_id, "error": str(exc)})
    return {"complete_candidate_articles": candidates, "execution_errors": errors}


def research_zopedia_maintenance(
    *, pages: list[dict[str, Any]], timeout_seconds: float = 1200, max_tool_calls: int = 24,
    llm_step_timeout_seconds: int = 300,
    llm_client: Any = None, service: Any = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    request_context: dict[str, Any] | None = None,
    extra_evidence: list[dict[str, Any]] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Research-only entry point also used for personal qualitative evaluation."""
    # Database rows expose decoded values and duplicate JSON transport columns.
    # Supply one complete article representation; all unique metadata survives.
    evidence = [{"kind": "zopedia_page", "source_id": page["page_id"],
                 **to_jsonable({key: value for key, value in page.items()
                                if (not key.endswith("_json") or key[:-5] not in page)
                                and key != "ts_rank_score"})} for page in pages]
    return run_aql_zopedia_structured_agent(
        query="Refresh the supplied Zopedia articles against relevant sources, calculations and assumptions; return scoped checks and supported edits.",
        instructions=_INSTRUCTIONS, schema_name="zopedia_maintenance", schema=MAINTENANCE_SCHEMA,
        task="knowledge_maintenance", surface="zopedia.maintenance", persist_findings=False, write_policy="none",
        max_tool_calls=max_tool_calls, run_timeout_seconds=timeout_seconds, llm_client=llm_client,
        llm_step_timeout_seconds=min(llm_step_timeout_seconds, max(1, int(timeout_seconds))),
        service=service, initial_evidence=evidence + list(extra_evidence or []), request_context=request_context,
        progress_callback=progress_callback, review_answer=True,
        artifact_preview=lambda payload: preview_zopedia_maintenance(pages, payload),
        result_callback=result_callback,
    )


def _observed_references(pages: list[dict[str, Any]], result: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    versions = {page["page_id"]: int(page["version"]) for page in pages}
    refs = set(versions)
    for page in pages:
        refs.update(page.get("source_urls") or [])
        refs.update(page.get("source_document_ids") or [])
    for call in (result.get("agent_result") or {}).get("tool_calls") or []:
        if call.get("status") != "completed":
            continue
        summary = call.get("result_summary") or {}
        versions.update(summary.get("read_versions") or {})
        analysis = (summary.get("render_payload") or {}).get("analysis") or {}
        if analysis.get("status") in {"succeeded", "success"} and analysis.get("analysis_run_id"):
            refs.add(str(analysis["analysis_run_id"]))
        for ref in summary.get("evidence_refs") or []:
            for key in ("ref", "page_id", "canonical_document_id", "document_id", "source_id", "url", "chunk_record_id"):
                if ref.get(key):
                    refs.add(str(ref[key]))
    return versions, refs


def apply_zopedia_maintenance_result(*, pages: list[dict[str, Any]], result: dict[str, Any], conn: Any) -> dict[str, Any]:
    """Apply independent reviewed actions; conflicts never discard other work."""
    payload = result.get("payload") or {}
    summary: dict[str, Any] = {"status": "partial", "assessments": [dict(row) for row in payload.get("assessments") or []],
                               "unresolved": payload.get("unresolved") or [], "outcomes": [], "mutation_ids": [],
                               "research_run_id": (result.get("agent_result") or {}).get("run_id"),
                               "review": (result.get("agent_result") or {}).get("quality_review") or {}}
    summary["observed_versions"] = {page["page_id"]: int(page["version"]) for page in pages}
    if not research_result_allows_knowledge_write(result):
        if result.get("status") in {"failed", "unavailable"} and not payload:
            summary["status"] = "failed"
        summary["error"] = (result.get("error") or (result.get("agent_result") or {}).get("error")
                            or "Semantic research has no completed editorial review; edits were retained for review.")
        return summary
    agent = result.get("agent_result") or {}
    summary["reviewed_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["evidence_activity"] = [{"tool_call_id": call.get("tool_call_id"),
                                      "tool_name": call.get("tool_name"), "status": call.get("status"),
                                      "evidence_refs": (call.get("result_summary") or {}).get("evidence_refs") or [],
                                      "analysis": ((call.get("result_summary") or {}).get("render_payload") or {}).get("analysis")}
                                     for call in agent.get("tool_calls") or []]
    acquisition = materialize_research_artifacts(
        agent, candidate_id="knowledge_maintenance", subject="Zopedia maintenance",
        run_id=str(agent.get("run_id") or "maintenance"), asof_time_utc=datetime.now(timezone.utc),
    )
    acquired = acquisition["chunks"]
    retained = []
    if not acquired.empty:
        bootstrap_saa_storage(conn)
        retained = persist_retained_evidence_chunks(
            "zopedia_maintenance", acquired, dataset_version_id=str(agent.get("run_id")),
            run_id=str(agent.get("run_id")), asof_time_utc=datetime.now(timezone.utc), conn=conn,
        ).to_dict("records")
        conn.commit()
    versions, refs = _observed_references(pages, result)
    calculations = {row["analysis"]["analysis_run_id"] for row in summary["evidence_activity"]
                    if row.get("status") == "completed" and row.get("analysis")
                    and row["analysis"].get("status") in {"succeeded", "success"}}
    for assessment in summary["assessments"]:
        assessment["observed_version"] = versions.get(assessment.get("page_id"))
        assessment["reviewed_at_utc"] = summary["reviewed_at_utc"]
        assessment["execution_issues"] = [
            {"check": index, "error": "A claimed calculation did not complete in this investigation.",
             "unobserved_run_ids": sorted(set(check.get("calculation_run_ids") or []) - calculations)}
            for index, check in enumerate(assessment.get("checks") or [])
            if set(check.get("calculation_run_ids") or []) - calculations
        ]
    inherited_documents = {doc for page in pages for doc in page.get("source_document_ids") or []}
    proposed_documents = {doc for action in payload.get("actions") or []
                          for page in action.get("pages") or [] for doc in page.get("source_document_ids") or []}
    durable_documents = set(inherited_documents)
    if proposed_documents - inherited_documents:
        with conn.cursor() as cur:
            cur.execute("SELECT canonical_document_id FROM saa_documents WHERE canonical_document_id=ANY(%s)",
                        (sorted(proposed_documents - inherited_documents),))
            durable_documents.update(row[0] for row in cur.fetchall())
        conn.commit()
    alias_chunks = {alias: [row for row in retained if row.get("chunk_id") in chunk_ids and row.get("chunk_record_id")]
                    for alias, chunk_ids in acquisition["source_aliases"].items()}
    seen: set[str] = set()
    for index, action in enumerate(payload.get("actions") or []):
        try:
            check_work()
            expected = action.get("expected_versions") or {}
            if not expected or any(versions.get(key) != value for key, value in expected.items()):
                raise ValueError("An edited page was not supplied or completely read at the proposed version.")
            if seen.intersection(expected):
                raise ValueError("Combine edits to the same page into one action.")
            if any(str(ref.get("ref") or "") not in refs for ref in action.get("evidence_refs") or []):
                raise ValueError("An evidence reference was not supplied or acquired in this investigation.")
            cited = {ref["ref"] for ref in action["evidence_refs"]}
            updated_pages = []
            for page in action.get("pages") or []:
                if any(url not in refs for url in page.get("source_urls") or []):
                    raise ValueError("A source URL was not supplied or acquired in this investigation.")
                for doc in page.get("source_document_ids") or []:
                    if doc not in durable_documents and not alias_chunks.get(doc):
                        raise ValueError("A source document id does not resolve to retained evidence.")
                    cited.add(doc)
                updated_pages.append({**page, "source_document_ids": [doc for doc in page.get("source_document_ids") or []
                                                                       if doc in durable_documents]})
            cited_chunk_ids = {row["chunk_record_id"] for ref in cited for row in alias_chunks.get(ref, [])}
            action = {**action, "evidence_refs": list(action["evidence_refs"]) + [
                {"kind": "retained_evidence_chunk", "ref": row["chunk_record_id"]}
                for row in retained if (row.get("url") in cited or row.get("chunk_record_id") in cited_chunk_ids)
                and row.get("chunk_record_id")
            ], "pages": updated_pages}
            applied = apply_zopedia_typed_mutation(
                **{key: value for key, value in action.items() if key != "content_accounting"},
                payload={"content_accounting": action.get("content_accounting") or []},
                allow_risky=True, actor="zopedia-maintenance", source="zopedia.maintenance", conn=conn,
            )
            mutation_id = (applied.get("mutation_audit") or {}).get("mutation_id")
            if mutation_id:
                summary["mutation_ids"].append(mutation_id)
                seen.update(expected)
            summary["outcomes"].append({"action": index, "status": applied["status"], "mutation_id": mutation_id})
            summary["outcomes"][-1]["page_ids"] = list(expected)
            summary.setdefault("published_versions", {}).update({page["page_id"]: page["version"] for page in applied.get("pages") or []})
        except (ZopediaVersionConflict, ValueError) as exc:
            conn.rollback()
            summary["outcomes"].append({"action": index, "page_ids": list(action.get("expected_versions") or {}), "status": "conflict" if isinstance(exc, ZopediaVersionConflict) else "invalid", "error": str(exc)})
        except Exception as exc:
            conn.rollback()
            summary["outcomes"].append({"action": index, "page_ids": list(action.get("expected_versions") or {}), "status": "failed", "error": str(exc)})
    for assessment in summary["assessments"]:
        assessment["publication_outcomes"] = [row for row in summary["outcomes"]
                                               if assessment.get("page_id") in row.get("page_ids", [])]
    coverage = {row.get("page_id") for row in summary["assessments"]}
    summary["batch_reviewed"] = coverage == {page["page_id"] for page in pages}
    if (summary["batch_reviewed"]
            and all(row["status"] == "committed" for row in summary["outcomes"])
            and not any(row.get("execution_issues") for row in summary["assessments"])):
        summary["status"] = "completed"
    return summary


def run_zopedia_semantic_maintenance(
    *, run_id: str, conn: Any, batch_size: int = 32, timeout_seconds: float = 1200,
    max_tool_calls: int = 24, llm_step_timeout_seconds: int = 300,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    article_timeout_seconds: float = 300,
    llm_client: Any = None, service: Any = None,
    request_context: dict[str, Any] | None = None,
    extra_evidence: list[dict[str, Any]] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Visit articles independently within the existing scheduled run budget."""
    deadline = time.monotonic() + timeout_seconds
    batch = load_zopedia_maintenance_batch(limit=batch_size, conn=conn)
    pages = batch["pages"]
    summary: dict[str, Any] = {"status": "partial", "articles": [], "assessments": [], "outcomes": [],
                               "mutation_ids": [], "unresolved": [], "observed_versions": {},
                               "published_versions": {}, "next_cursor": batch["cursor"]}
    if not pages:
        summary.update(status="empty", next_cursor="")
    used_tools = 0
    for page in pages:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or used_tools >= max_tool_calls:
            break
        article = None
        result = None
        try:
            current = load_zopedia_page(page_id=page["page_id"], conn=conn)
            conn.commit()
            if not current or current.get("page_id") != page["page_id"]:
                article = {"status": "completed", "skipped": "Page was removed or merged after batch selection.", "mutation_ids": []}
            else:
                current["previous_refresh"] = page.get("previous_refresh") or {}
                seconds = min(deadline - time.monotonic(), article_timeout_seconds)
                if seconds <= 0:
                    break
                with WorkScope(time.monotonic() + seconds).activate():
                    result = research_zopedia_maintenance(
                        pages=[current], timeout_seconds=seconds, max_tool_calls=max_tool_calls - used_tools,
                        progress_callback=progress_callback, llm_step_timeout_seconds=llm_step_timeout_seconds,
                        llm_client=llm_client, service=service, request_context=request_context, extra_evidence=extra_evidence,
                        result_callback=result_callback,
                    )
                    used_tools += int(((result.get("agent_result") or {}).get("engine") or {}).get("tool_calls_used", 0))
                    identity = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
                    retained = retain_research_artifact(
                        f"research_artifact:{llm_settings_scope()}:public_maintenance", identity,
                        subject=f"{run_id}:{page['page_id']}", task="knowledge_maintenance", result=to_jsonable(result),
                    )
                    article = apply_zopedia_maintenance_result(pages=[current], result=result, conn=conn)
                    article["research_artifact"] = retained
                    if not retained:
                        article["uncheckpointed_research_result"] = to_jsonable(result)
        except Exception as exc:
            conn.rollback()
            if article is None:
                article = {"status": "failed", "error": str(exc), "mutation_ids": []}
                if result is not None:
                    article["uncheckpointed_research_result"] = to_jsonable(result)
                else:
                    # The tool spend is unknown after an unhandled failure.
                    # Stop this run rather than resetting its remaining budget.
                    used_tools = max_tool_calls
            else:
                # Publication completed before the enclosing clock check.
                # Preserve returned commits instead of relabelling them lost.
                article["execution_limit_error"] = str(exc)
        article["page_id"] = page["page_id"]
        summary["articles"].append(article)
        for key in ("assessments", "outcomes", "mutation_ids", "unresolved"):
            summary[key].extend(article.get(key) or [])
        for key in ("observed_versions", "published_versions"):
            summary[key].update(article.get(key) or {})
        if article.get("reviewed_at_utc"):
            summary["reviewed_at_utc"] = article["reviewed_at_utc"]
        # Checkpoint after each attempt. A failed article returns on the next
        # sweep; unattempted articles remain ahead of this cursor next run.
        summary["next_cursor"] = page["page_id"]
        record_zopedia_semantic_maintenance(run_id=run_id, semantic=summary, conn=conn)
    if len(summary["articles"]) == len(pages) and pages:
        summary["next_cursor"] = batch["next_cursor"]
        if all(row["status"] == "completed" for row in summary["articles"]):
            summary["status"] = "completed"
    summary["batch_page_ids"] = [page["page_id"] for page in pages]
    summary["tool_calls_used"] = used_tools
    record_zopedia_semantic_maintenance(run_id=run_id, semantic=summary, conn=conn)
    return summary
