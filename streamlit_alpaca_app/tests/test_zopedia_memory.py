from __future__ import annotations

import json

import pandas as pd

from services.aql.evidence_pack import build_aql_evidence_pack
from services.saa.zopedia import (
    apply_zopedia_typed_mutation,
    build_zopedia_maintenance_snapshot,
    build_zopedia_page_id,
    extract_youtube_video_id,
    fetch_youtube_transcript,
    ingest_zopedia_source,
    prepare_zopedia_mutation_rollback_pages,
    prepare_zopedia_pages,
    prepare_zopedia_uploaded_source,
    search_prepared_zopedia_pages,
    zopedia_page_neighborhood,
    zopedia_read_source,
    zopedia_sources_for_page,
    zopedia_trace_to_evidence,
)


class _FakeLLM:
    def generate_json(self, *, system_prompt, user_prompt, schema_name, schema):
        del system_prompt, user_prompt, schema_name, schema
        return {
            "pages": [
                {
                    "page_type": "theme",
                    "title": "AI Data Center Power Demand",
                    "summary": "AI data-center load raises power demand and grid-capacity questions.",
                    "body_markdown": "AI data-center buildouts can raise power demand and stress grid capacity.",
                    "entity_refs": ["AI", "Data Centers", "Utilities"],
                    "outgoing_links": ["Power Grid Capacity"],
                },
                {
                    "page_type": "concept",
                    "title": "Power Grid Capacity",
                    "summary": "Grid capacity constrains how fast new large-load customers can connect.",
                    "body_markdown": "Grid capacity is a bottleneck for large-load interconnections.",
                    "entity_refs": ["Utilities"],
                    "outgoing_links": ["AI Data Center Power Demand"],
                },
            ]
        }


def test_zopedia_pages_are_searchable_without_database():
    frame, records = prepare_zopedia_pages(
        [
            {
                "page_type": "theme",
                "title": "AI Data Center Power Demand",
                "summary": "AI data-center load raises utility demand.",
                "body_markdown": "Data centers need more power and may require grid upgrades.",
                "entity_refs": ["AI", "Utilities"],
            },
            {
                "page_type": "ticker",
                "title": "Airlines and Jet Fuel",
                "summary": "Airlines are sensitive to jet fuel costs.",
                "body_markdown": "Lower oil prices can help airline margins.",
            },
        ]
    )

    assert len(records) == 2
    assert str(frame.loc[0, "page_id"]).startswith("zopedia::theme::")

    results = search_prepared_zopedia_pages(frame, query="data center grid power", limit=3)

    assert len(results) == 1
    assert results.loc[0, "title"] == "AI Data Center Power Demand"
    assert "Utilities" in results.loc[0, "entity_refs"]


def test_zopedia_ingest_uses_llm_pages_but_keeps_source_page():
    result = ingest_zopedia_source(
        title="AI power memo",
        source_text="AI data centers are increasing power demand and grid interconnection pressure.",
        url="https://example.com/ai-power",
        source_type="memo",
        llm_client=_FakeLLM(),
        conn=None,
    )

    assert result["status"] == "stored"
    assert result["enrichment_status"] == "llm_enriched"
    titles = {row["title"] for row in result["pages"]}
    assert "AI power memo" in titles
    assert "AI Data Center Power Demand" in titles
    assert "Power Grid Capacity" in titles


def test_zopedia_ingest_attaches_generated_pages_to_source_page():
    result = ingest_zopedia_source(
        title="AI power memo",
        source_text="AI data centers are increasing power demand and grid interconnection pressure.",
        url="https://example.com/ai-power",
        source_type="memo",
        llm_client=_FakeLLM(),
        conn=None,
    )

    rows = list(result["pages"])
    source_row = next(row for row in rows if row["page_type"] == "source")
    generated_row = next(row for row in rows if row["title"] == "AI Data Center Power Demand")
    metadata = json.loads(generated_row["metadata_json"])

    assert source_row["page_id"] == build_zopedia_page_id(page_type="source", title="AI power memo")
    assert metadata["source_page_id"] == source_row["page_id"]
    assert metadata["source_page_title"] == "AI power memo"
    assert metadata["source_url"] == "https://example.com/ai-power"


def test_zopedia_ingest_returns_reversible_mutation_audit():
    result = ingest_zopedia_source(
        title="AI power memo",
        source_text="AI data centers are increasing power demand and grid interconnection pressure.",
        url="https://example.com/ai-power",
        source_type="memo",
        llm_client=_FakeLLM(),
        conn=None,
    )

    audit = result["mutation_audit"]
    page_ids = json.loads(audit["page_ids_json"])
    evidence_refs = json.loads(audit["evidence_refs_json"])
    rollback_hint = json.loads(audit["rollback_hint_json"])

    assert audit["mutation_type"] == "ingest_source"
    assert audit["risk_level"] == "safe"
    assert audit["status"] == "prepared"
    assert result["pages"][0]["page_id"] in page_ids
    assert any(ref["kind"] == "zopedia_source_page" for ref in evidence_refs)
    assert any(ref["kind"] == "source_url" and ref["url"] == "https://example.com/ai-power" for ref in evidence_refs)
    assert rollback_hint["strategy"] == "restore_before_state_or_archive_new_pages"
    assert set(rollback_hint["new_page_ids"]) == set(page_ids)


def test_prepare_zopedia_mutation_rollback_pages_restores_and_archives():
    before_state = [
        {
            "page_id": "zopedia::theme::ai-power::old",
            "page_type": "theme",
            "title": "AI Power",
            "summary": "Original summary.",
            "body_markdown": "Original body.",
            "status": "active",
            "metadata_json": json.dumps({"source": "before"}),
        }
    ]
    after_state = [
        {
            "page_id": "zopedia::theme::ai-power::old",
            "page_type": "theme",
            "title": "AI Power",
            "summary": "Changed summary.",
            "body_markdown": "Changed body.",
            "status": "active",
            "metadata_json": json.dumps({"source": "after"}),
        },
        {
            "page_id": "zopedia::concept::new-grid::new",
            "page_type": "concept",
            "title": "New Grid",
            "summary": "New page.",
            "body_markdown": "New page body.",
            "status": "active",
            "metadata_json": "{}",
        },
    ]

    pages = prepare_zopedia_mutation_rollback_pages(
        mutation_id="zopedia_mutation::ingest_source::abc",
        before_state=before_state,
        after_state=after_state,
    )

    restored = next(page for page in pages if page["page_id"] == "zopedia::theme::ai-power::old")
    archived = next(page for page in pages if page["page_id"] == "zopedia::concept::new-grid::new")

    assert restored["summary"] == "Original summary."
    assert restored["status"] == "active"
    assert restored["metadata"]["rollback_of_mutation_id"] == "zopedia_mutation::ingest_source::abc"
    assert archived["status"] == "deleted"
    assert archived["metadata"]["rollback_previous_status"] == "active"


def test_zopedia_uploaded_source_decodes_text_and_metadata():
    result = prepare_zopedia_uploaded_source(
        filename="ai-power-memo.md",
        content=b"# AI Power Memo\n\nAI data centers are increasing utility load.",
        content_type="text/markdown",
    )

    assert result["status"] == "ok"
    assert result["title"] == "ai-power-memo"
    assert result["source_type"] == "uploaded_file"
    assert "AI data centers" in result["source_text"]
    assert result["metadata"]["filename"] == "ai-power-memo.md"
    assert result["metadata"]["input_source"] == "upload"


def test_zopedia_uploaded_source_rejects_binary_without_text():
    result = prepare_zopedia_uploaded_source(
        filename="image.bin",
        content=b"\x00\x01\x02\x03\x04\x05\x06\x07" * 20,
        content_type="application/octet-stream",
    )

    assert result["status"] == "unsupported"
    assert result["source_text"] == ""
    assert "readable text" in result["message"]


def test_zopedia_ingest_preserves_uploaded_source_metadata():
    result = ingest_zopedia_source(
        title="AI upload",
        source_text="Uploaded memo says AI data centers are increasing utility load.",
        source_type="uploaded_file",
        source_metadata={"filename": "ai-upload.txt", "content_type": "text/plain", "byte_count": 128},
        llm_client=_FakeLLM(),
        conn=None,
    )

    source_row = next(row for row in result["pages"] if row["page_type"] == "source")
    generated_row = next(row for row in result["pages"] if row["title"] == "AI Data Center Power Demand")
    source_metadata = json.loads(source_row["metadata_json"])
    generated_metadata = json.loads(generated_row["metadata_json"])

    assert source_metadata["filename"] == "ai-upload.txt"
    assert source_metadata["content_type"] == "text/plain"
    assert generated_metadata["filename"] == "ai-upload.txt"


def test_zopedia_neighborhood_links_pages_with_fake_connection(monkeypatch):
    frame, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "theme",
                "title": "AI Data Center Power Demand",
                "outgoing_links": ["Power Grid Capacity"],
            },
            {
                "page_type": "concept",
                "title": "Power Grid Capacity",
                "outgoing_links": ["AI Data Center Power Demand"],
            },
        ]
    )
    pages = frame.to_dict("records")
    seed = pages[0]

    monkeypatch.setattr(
        "services.saa.zopedia.load_zopedia_page",
        lambda page_id, conn=None: seed if page_id == seed["page_id"] else {},
    )
    monkeypatch.setattr(
        "services.saa.zopedia.list_zopedia_pages",
        lambda limit=30, conn=None: frame,
    )

    graph = zopedia_page_neighborhood(page_id=seed["page_id"], depth=1)

    assert graph["nodes"]
    assert any(edge["source"] == seed["page_id"] for edge in graph["edges"])


def test_zopedia_sources_for_page_returns_source_page_and_url(monkeypatch):
    source_page_id = build_zopedia_page_id(page_type="source", title="AI power memo")
    frame, _ = prepare_zopedia_pages(
        [
            {
                "page_id": source_page_id,
                "page_type": "source",
                "title": "AI power memo",
                "summary": "AI data centers are increasing power demand.",
                "body_markdown": "Original memo text about AI data centers and grid interconnection.",
                "source_urls": ["https://example.com/ai-power"],
                "metadata": {
                    "source_title": "AI power memo",
                    "source_url": "https://example.com/ai-power",
                    "source_type": "memo",
                },
            },
            {
                "page_type": "theme",
                "title": "AI Data Center Power Demand",
                "summary": "AI data-center load raises utility demand.",
                "body_markdown": "Data centers need more power and may require grid upgrades.",
                "metadata": {
                    "source_page_id": source_page_id,
                    "source_title": "AI power memo",
                    "source_url": "https://example.com/ai-power",
                    "source_type": "memo",
                },
            },
        ]
    )
    pages = frame.to_dict("records")
    generated = pages[1]

    monkeypatch.setattr(
        "services.saa.zopedia.load_zopedia_page",
        lambda page_id, conn=None: generated if page_id == generated["page_id"] else {},
    )
    monkeypatch.setattr("services.saa.zopedia.list_zopedia_pages", lambda limit=30, conn=None: frame)

    result = zopedia_sources_for_page(page_id=generated["page_id"])

    assert result["status"] == "found"
    assert result["source_count"] >= 2
    assert any(ref["kind"] == "zopedia_source_page" and ref["page_id"] == source_page_id for ref in result["sources"])
    assert any(ref["kind"] == "source_url" and ref["url"] == "https://example.com/ai-power" for ref in result["sources"])


def test_zopedia_trace_to_evidence_adds_supported_by_edges(monkeypatch):
    source_page_id = build_zopedia_page_id(page_type="source", title="AI power memo")
    frame, _ = prepare_zopedia_pages(
        [
            {
                "page_id": source_page_id,
                "page_type": "source",
                "title": "AI power memo",
                "body_markdown": "Original memo text about AI power and grid capacity.",
                "source_urls": ["https://example.com/ai-power"],
                "metadata": {"source_type": "memo", "source_url": "https://example.com/ai-power"},
            },
            {
                "page_type": "theme",
                "title": "AI Data Center Power Demand",
                "outgoing_links": ["Power Grid Capacity"],
                "metadata": {"source_page_id": source_page_id, "source_title": "AI power memo"},
            },
            {
                "page_type": "concept",
                "title": "Power Grid Capacity",
                "metadata": {"source_page_id": source_page_id, "source_title": "AI power memo"},
            },
        ]
    )
    pages = frame.to_dict("records")
    seed = pages[1]

    monkeypatch.setattr(
        "services.saa.zopedia.load_zopedia_page",
        lambda page_id, conn=None: seed if page_id == seed["page_id"] else {},
    )
    monkeypatch.setattr("services.saa.zopedia.list_zopedia_pages", lambda limit=30, conn=None: frame)

    trace = zopedia_trace_to_evidence(page_id=seed["page_id"], depth=1)

    assert trace["status"] == "found"
    assert any(edge["relation"] == "links_to" for edge in trace["edges"])
    assert any(
        edge["relation"] == "supported_by" and edge["source"] == seed["page_id"] and edge["target"] == source_page_id
        for edge in trace["edges"]
    )
    assert any(node["kind"] == "zopedia_source_page" for node in trace["source_nodes"])


def test_zopedia_read_source_opens_retained_evidence_chunk(monkeypatch):
    chunk = {
        "chunk_record_id": "saa_chunk::ai-power",
        "canonical_document_id": "saa_doc::ai-power",
        "title": "AI power memo",
        "chunk_text": "Original retained evidence says AI data centers are increasing power demand.",
        "display_excerpt": "AI data centers are increasing power demand.",
        "source_provider": "memo",
        "published_date": "2026-05-01",
        "metadata_json": "{}",
    }
    document_metadata = {
        "canonical_document_id": "saa_doc::ai-power",
        "canonical_url": "https://example.com/ai-power",
        "title": "AI power memo",
    }

    monkeypatch.setattr("services.saa.zopedia.load_retained_evidence_chunk", lambda chunk_record_id, conn=None: chunk)
    monkeypatch.setattr("services.saa.zopedia.load_retained_document_metadata", lambda canonical_document_id, conn=None: document_metadata)

    result = zopedia_read_source(ref="saa_chunk::ai-power", kind="retained_evidence_chunk")

    assert result["status"] == "found"
    assert result["source_kind"] == "retained_evidence_chunk"
    assert result["canonical_document_id"] == "saa_doc::ai-power"
    assert result["url"] == "https://example.com/ai-power"
    assert "Original retained evidence" in result["text"]


def test_youtube_transcript_fetcher_parses_caption_tracks():
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=BOT2rrm10RM") == "BOT2rrm10RM"
    assert extract_youtube_video_id("https://youtu.be/t6y_VmxuO28") == "t6y_VmxuO28"
    assert extract_youtube_video_id("https://www.youtube.com/embed/n889nI8sR84") == "n889nI8sR84"

    class _Response:
        def __init__(self, text: str):
            self.text = text

    def fake_get(url, timeout):
        del timeout
        if "watch" in url:
            return _Response(
                'var ytInitialPlayerResponse = {"captions":{"playerCaptionsTracklistRenderer":'
                '{"captionTracks":[{"baseUrl":"https://captions.example/json3","languageCode":"en"}]}}};'
            )
        return _Response(json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world"}]}, {"segs": [{"utf8": "Next line"}]}]}))

    transcript = fetch_youtube_transcript("https://www.youtube.com/watch?v=BOT2rrm10RM", requests_get=fake_get)

    assert transcript["status"] == "ok"
    assert transcript["transcript"] == "Hello world Next line"


def test_evidence_pack_promotes_zopedia_pages_and_proposals():
    pack = build_aql_evidence_pack(
        run_id="run-1",
        query="ai power",
        tool_calls=[
            {
                "tool_call_id": "agtc_1",
                "tool_name": "zopedia.search_pages",
                "status": "completed",
                "arguments": {"query": "ai power"},
                "result_summary": {
                    "evidence_refs": [
                        {
                            "kind": "zopedia_page",
                            "page_id": "zopedia::theme::ai-power::abc",
                            "ref": "zopedia::theme::ai-power::abc",
                            "title": "AI Power",
                        }
                    ]
                },
            },
            {
                "tool_call_id": "agtc_2",
                "tool_name": "zopedia.propose_change",
                "status": "completed",
                "arguments": {"proposal_type": "update"},
                "result_summary": {
                    "evidence_refs": [
                        {
                            "kind": "zopedia_proposal",
                            "ref": "zopedia_proposal::update::123",
                            "title": "Update AI Power",
                        }
                    ]
                },
            },
            {
                "tool_call_id": "agtc_3",
                "tool_name": "zopedia.list_mutations",
                "status": "completed",
                "arguments": {"status": "committed"},
                "result_summary": {
                    "evidence_refs": [
                        {
                            "kind": "zopedia_mutation",
                            "ref": "zopedia_mutation::ingest_source::123",
                            "mutation_id": "zopedia_mutation::ingest_source::123",
                            "title": "Ingest AI Power",
                        }
                    ]
                },
            },
            {
                "tool_call_id": "agtc_4",
                "tool_name": "zopedia.list_maintenance_reports",
                "status": "completed",
                "arguments": {},
                "result_summary": {
                    "evidence_refs": [
                        {
                            "kind": "zopedia_maintenance_report",
                            "ref": "maintenance-run-1",
                            "title": "Zopedia maintenance",
                        }
                    ]
                },
            },
        ],
    )

    assert pack["zopedia_pages"][0]["page_id"] == "zopedia::theme::ai-power::abc"
    assert pack["proposals"][0]["ref"] == "zopedia_proposal::update::123"
    assert pack["mutations"][0]["mutation_id"] == "zopedia_mutation::ingest_source::123"
    assert any(ref["ref"] == "maintenance-run-1" for ref in pack["zopedia_pages"])


def test_zopedia_maintenance_snapshot_builds_backlinks_communities_and_issues():
    source_page_id = build_zopedia_page_id(page_type="source", title="AI power memo")
    frame, _ = prepare_zopedia_pages(
        [
            {
                "page_id": source_page_id,
                "page_type": "source",
                "title": "AI power memo",
                "body_markdown": "Source text about AI power demand.",
                "source_urls": ["https://example.com/ai-power"],
                "metadata": {"source_url": "https://example.com/ai-power", "source_type": "memo"},
            },
            {
                "page_type": "theme",
                "title": "AI Data Center Power Demand",
                "summary": "AI data centers raise utility demand.",
                "body_markdown": "AI data centers raise utility demand.",
                "outgoing_links": ["Power Grid Capacity", "Missing Link"],
                "metadata": {"source_page_id": source_page_id, "source_title": "AI power memo"},
            },
            {
                "page_type": "concept",
                "title": "Power Grid Capacity",
                "summary": "Grid capacity can constrain large loads.",
                "body_markdown": "Grid capacity can constrain large loads.",
                "outgoing_links": ["AI Data Center Power Demand"],
                "metadata": {"source_page_id": source_page_id, "source_title": "AI power memo"},
            },
            {
                "page_type": "concept",
                "title": "Unsupported Orphan",
                "summary": "A page without sources or links.",
                "body_markdown": "A page without sources or links.",
            },
        ]
    )

    snapshot = build_zopedia_maintenance_snapshot(frame, run_id="maintenance-test-run")

    assert snapshot["status"] == "ready"
    assert set(snapshot["backlinks"]["relation"]) >= {"links_to", "backlink"}
    assert not snapshot["communities"].empty
    assert "godnode_page_id" in json.loads(snapshot["communities"].iloc[0]["metadata_json"])
    issue_types = {issue["issue_type"] for issue in snapshot["issues"]}
    assert {"broken_link", "weak_source", "orphan_page"}.issubset(issue_types)
    assert snapshot["summary"]["community_count"] >= 1


def test_apply_zopedia_typed_mutation_escalates_risky_without_database():
    result = apply_zopedia_typed_mutation(
        mutation_type="delete_pages",
        page_id="zopedia::theme::ai-power::abc",
        rationale="A test deletion should require review.",
        conn=None,
    )

    assert result["status"] == "proposed"
    assert result["proposal"]["proposal_type"] == "delete_pages"
    payload = json.loads(result["proposal"]["proposal_payload_json"])
    assert payload["page_id"] == "zopedia::theme::ai-power::abc"
