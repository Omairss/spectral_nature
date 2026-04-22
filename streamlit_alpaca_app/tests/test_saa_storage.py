from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd
from services.aql.evidence_index import annotate_source_documents
from services.saa.storage import (
    load_retained_document_metadata,
    prepare_retained_evidence_chunks,
    prepare_retained_source_documents,
    search_prepared_evidence_chunks,
    search_retained_evidence_chunks,
    search_retained_documents,
)


def _retained_row(
    *,
    canonical_document_id: str,
    title: str,
    display_excerpt: str,
    search_text: str,
    bundle_subject: str,
    source_provider: str,
    search_provider: str,
    published_at: str,
    published_date: str,
    primary_date: str,
    mentioned_tickers: list[str],
    mentioned_commodities: list[str],
    event_tags: list[str],
    mentioned_dates: list[str],
) -> tuple[object, ...]:
    return (
        canonical_document_id,
        f"https://example.com/{canonical_document_id}",
        "example.com",
        title,
        display_excerpt,
        search_text,
        "search",
        source_provider,
        search_provider,
        bundle_subject,
        "web",
        1,
        pd.Timestamp(published_at),
        published_date,
        primary_date,
        f"saa/raw_documents/{canonical_document_id}.json",
        len(search_text),
        "provider_text",
        f"doc::{canonical_document_id}",
        "attention_source_documents",
        "attention_source_documents__20260414T180000Z__retentio",
        "run-1",
        pd.Timestamp("2026-04-14T18:00:00Z"),
        json.dumps(mentioned_tickers),
        "|" + "|".join(mentioned_tickers) + "|" if mentioned_tickers else "",
        json.dumps(mentioned_commodities),
        "|" + "|".join(mentioned_commodities) + "|" if mentioned_commodities else "",
        json.dumps(event_tags),
        "|" + "|".join(event_tags) + "|" if event_tags else "",
        json.dumps(mentioned_dates),
        "|" + "|".join(mentioned_dates) + "|" if mentioned_dates else "",
        json.dumps({"query_text": "ceasefire oil"}, ensure_ascii=False),
    )


def _chunk_row(
    *,
    chunk_record_id: str,
    canonical_document_id: str,
    title: str,
    display_excerpt: str,
    chunk_text: str,
    search_text: str,
    bundle_subject: str,
    source_provider: str,
    search_provider: str,
    research_scope: str,
    published_at: str,
    published_date: str,
    primary_date: str,
    mentioned_tickers: list[str],
    mentioned_commodities: list[str],
    event_tags: list[str],
    mentioned_dates: list[str],
    embedding_model: str = "",
    embedding_vector_json: str = "",
) -> tuple[object, ...]:
    return (
        chunk_record_id,
        f"{chunk_record_id}-identity",
        canonical_document_id,
        f"doc::{canonical_document_id}",
        f"chunk::{canonical_document_id}",
        1,
        3,
        title,
        display_excerpt,
        chunk_text,
        search_text,
        bundle_subject,
        "search",
        source_provider,
        search_provider,
        research_scope,
        "web",
        1,
        pd.Timestamp(published_at),
        published_date,
        primary_date,
        "provider_text",
        len(chunk_text),
        "attention_evidence_chunks",
        "attention_evidence_chunks__20260414T180000Z__retentio",
        "run-1",
        pd.Timestamp("2026-04-14T18:00:00Z"),
        embedding_model,
        embedding_vector_json,
        json.dumps(mentioned_tickers),
        "|" + "|".join(mentioned_tickers) + "|" if mentioned_tickers else "",
        json.dumps(mentioned_commodities),
        "|" + "|".join(mentioned_commodities) + "|" if mentioned_commodities else "",
        json.dumps(event_tags),
        "|" + "|".join(event_tags) + "|" if event_tags else "",
        json.dumps(mentioned_dates),
        "|" + "|".join(mentioned_dates) + "|" if mentioned_dates else "",
        json.dumps({"query_text": "ceasefire oil"}, ensure_ascii=False),
    )


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]):
        self.rows = rows
        self.last_query = ""
        self.last_params: tuple[object, ...] = ()

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> None:
        self.last_query = query
        self.last_params = tuple(params or ())

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, rows: list[tuple[object, ...]]):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)


def test_annotate_source_documents_adds_canonical_fields():
    documents = annotate_source_documents(
        [
            {
                "document_id": "doc::example",
                "title": "Copper outlook improves on AI demand",
                "url": "https://www.seekingalpha.com/article/12345-copper-outlook?utm_source=newsletter",
                "raw_text": "Copper demand is rising because AI data-center buildouts are expanding.",
                "provider_payload_json": json.dumps({"raw_content": "Full provider payload"}),
                "source_kind": "search",
                "source_provider": "seeking_alpha",
                "search_provider": "tavily",
                "bundle_subject": "FCX",
                "published_at": pd.Timestamp("2026-04-14T12:30:00Z"),
            }
        ],
        asof_time_utc=pd.Timestamp("2026-04-14T18:00:00Z"),
    )

    row = documents[0]

    assert row["canonical_document_id"].startswith("saa_doc::")
    assert row["canonical_url"] == "https://seekingalpha.com/article/12345-copper-outlook"
    assert row["url_host"] == "seekingalpha.com"
    assert len(row["document_identity_sha256"]) == 64
    assert len(row["document_content_sha256"]) == 64
    assert len(row["provider_payload_sha256"]) == 64


def test_prepare_retained_source_documents_builds_reopenable_blob_payload():
    annotated = annotate_source_documents(
        [
            {
                "document_id": "doc::example",
                "run_id": "retention-test-run",
                "asof_time_utc": pd.Timestamp("2026-04-14T18:00:00Z"),
                "title": "Copper outlook improves on AI demand",
                "url": "https://www.seekingalpha.com/article/12345-copper-outlook?utm_source=newsletter",
                "raw_text": "Copper demand is rising because AI data-center buildouts are expanding.",
                "provider_text": "Full retained provider text",
                "provider_payload_json": json.dumps({"raw_content": "Full provider payload"}),
                "source_kind": "search",
                "source_provider": "seeking_alpha",
                "search_provider": "tavily",
                "bundle_subject": "FCX",
                "published_at": pd.Timestamp("2026-04-14T12:30:00Z"),
            }
        ],
        asof_time_utc=pd.Timestamp("2026-04-14T18:00:00Z"),
    )
    frame = pd.DataFrame(annotated)

    prepared, uploads, records = prepare_retained_source_documents(
        frame,
        dataset_name="attention_source_documents",
        dataset_version_id="attention_source_documents__20260414T180000Z__retentio",
        run_id="retention-test-run",
        asof_time_utc=datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc),
        universe_version="20260414",
    )

    assert len(uploads) == 1
    assert len(records) == 1
    assert prepared.loc[0, "raw_text_blob_path"].startswith("saa/raw_documents/provider=seeking-alpha/")
    assert prepared.loc[0, "canonical_document_id"].startswith("saa_doc::")

    body = json.loads(uploads[0][1].decode("utf-8"))
    assert body["canonical_document_id"] == prepared.loc[0, "canonical_document_id"]
    assert body["raw_text"] == "Copper demand is rising because AI data-center buildouts are expanding."
    assert body["provider_payload_json"] == json.dumps({"raw_content": "Full provider payload"})
    assert body["dataset_name"] == "attention_source_documents"


def test_search_retained_documents_filters_historical_rows():
    rows = [
        _retained_row(
            canonical_document_id="saa_doc::uso",
            title="USO falls as oil pulls back on ceasefire hopes",
            display_excerpt="USO and airlines moved as supply-risk eased.",
            search_text="USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
            bundle_subject="USO",
            source_provider="Reuters",
            search_provider="tavily",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["USO", "BNO"],
            mentioned_commodities=["oil"],
            event_tags=["geopolitics", "supply_chain"],
            mentioned_dates=["2026-03-24"],
        ),
        _retained_row(
            canonical_document_id="saa_doc::aapl",
            title="AAPL gains after product event",
            display_excerpt="AAPL rose on launch commentary.",
            search_text="AAPL rose after a product launch update.",
            bundle_subject="AAPL",
            source_provider="Reuters",
            search_provider="serpapi",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["AAPL"],
            mentioned_commodities=[],
            event_tags=["product_launch"],
            mentioned_dates=["2026-03-24"],
        ),
    ]

    resolved = search_retained_documents(
        query="ceasefire oil",
        tickers=["USO"],
        commodities=["oil"],
        event_tags=["geopolitics"],
        dates=["2026-03-24"],
        providers=["reuters"],
        limit=5,
        conn=_Conn(rows),
    )

    assert resolved["canonical_document_id"].tolist() == ["saa_doc::uso"]
    assert resolved.iloc[0]["mentioned_tickers"] == ["USO", "BNO"]
    assert resolved.iloc[0]["event_tags"] == ["geopolitics", "supply_chain"]
    assert resolved.iloc[0]["search_score"] > 0


def test_load_retained_document_metadata_returns_single_row():
    rows = [
        _retained_row(
            canonical_document_id="saa_doc::uso",
            title="USO falls as oil pulls back on ceasefire hopes",
            display_excerpt="USO and airlines moved as supply-risk eased.",
            search_text="USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
            bundle_subject="USO",
            source_provider="Reuters",
            search_provider="tavily",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["USO", "BNO"],
            mentioned_commodities=["oil"],
            event_tags=["geopolitics", "supply_chain"],
            mentioned_dates=["2026-03-24"],
        )
    ]

    metadata = load_retained_document_metadata("saa_doc::uso", conn=_Conn(rows))

    assert metadata is not None
    assert metadata["canonical_document_id"] == "saa_doc::uso"
    assert metadata["display_excerpt"] == "USO and airlines moved as supply-risk eased."
    assert metadata["mentioned_commodities"] == ["oil"]


def test_prepare_retained_evidence_chunks_builds_searchable_chunk_rows():
    frame = pd.DataFrame(
        [
            {
                "canonical_document_id": "saa_doc::uso",
                "document_id": "doc::uso",
                "chunk_id": "chunk::uso",
                "chunk_index": 1,
                "document_chunk_count": 2,
                "title": "USO falls as oil pulls back on ceasefire hopes",
                "display_excerpt": "USO and airlines moved as supply-risk eased.",
                "chunk_text": "USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
                "bundle_subject": "USO",
                "source_kind": "search",
                "source_provider": "Reuters",
                "search_provider": "tavily",
                "research_scope": "home_summary",
                "published_at": pd.Timestamp("2026-03-24T17:30:00Z"),
                "published_date": "2026-03-24",
                "primary_date": "2026-03-24",
                "mentioned_tickers_json": json.dumps(["USO", "BNO"]),
                "mentioned_commodities_json": json.dumps(["oil"]),
                "event_tags_json": json.dumps(["geopolitics"]),
                "mentioned_dates_json": json.dumps(["2026-03-24"]),
            }
        ]
    )

    prepared, records = prepare_retained_evidence_chunks(
        frame,
        dataset_name="attention_evidence_chunks",
        dataset_version_id="attention_evidence_chunks__20260414T180000Z__retentio",
        run_id="run-1",
        asof_time_utc=datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc),
    )

    assert len(records) == 1
    assert prepared.loc[0, "chunk_record_id"].startswith("saa_chunk::")
    assert len(prepared.loc[0, "chunk_identity_sha256"]) == 64
    assert prepared.loc[0, "search_text"].startswith("USO falls as oil pulls back on ceasefire hopes")
    assert prepared.loc[0, "mentioned_tickers_key"] == "|USO|BNO|"


def test_search_retained_evidence_chunks_filters_historical_rows():
    rows = [
        _chunk_row(
            chunk_record_id="saa_chunk::uso",
            canonical_document_id="saa_doc::uso",
            title="USO falls as oil pulls back on ceasefire hopes",
            display_excerpt="USO and airlines moved as supply-risk eased.",
            chunk_text="USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
            search_text="USO and BNO fell on March 24, 2026 as oil eased after ceasefire headlines.",
            bundle_subject="USO",
            source_provider="Reuters",
            search_provider="tavily",
            research_scope="home_summary",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["USO", "BNO"],
            mentioned_commodities=["oil"],
            event_tags=["geopolitics", "supply_chain"],
            mentioned_dates=["2026-03-24"],
        ),
        _chunk_row(
            chunk_record_id="saa_chunk::aapl",
            canonical_document_id="saa_doc::aapl",
            title="AAPL gains after product event",
            display_excerpt="AAPL rose on launch commentary.",
            chunk_text="AAPL rose after a product launch update.",
            search_text="AAPL rose after a product launch update.",
            bundle_subject="AAPL",
            source_provider="Reuters",
            search_provider="serpapi",
            research_scope="symbol",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["AAPL"],
            mentioned_commodities=[],
            event_tags=["product_launch"],
            mentioned_dates=["2026-03-24"],
        ),
    ]

    resolved = search_retained_evidence_chunks(
        query="ceasefire oil",
        tickers=["USO"],
        commodities=["oil"],
        event_tags=["geopolitics"],
        research_scopes=["home_summary"],
        dates=["2026-03-24"],
        providers=["reuters"],
        limit=5,
        conn=_Conn(rows),
    )

    assert resolved["chunk_record_id"].tolist() == ["saa_chunk::uso"]
    assert resolved.iloc[0]["canonical_document_id"] == "saa_doc::uso"
    assert resolved.iloc[0]["mentioned_tickers"] == ["USO", "BNO"]
    assert resolved.iloc[0]["search_score"] > 0


def test_search_retained_evidence_chunks_supports_semantic_rerank(monkeypatch):
    import services.saa.storage as storage

    class _FakeEmbeddingClient:
        def __init__(self) -> None:
            self.config = type("Cfg", (), {"embedding_model": "text-embedding-3-small"})()

        def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["fertilizer demand"]
            return [[1.0, 0.0]]

    monkeypatch.setattr(storage, "load_embedding_client", lambda: _FakeEmbeddingClient())

    rows = [
        _chunk_row(
            chunk_record_id="saa_chunk::cf",
            canonical_document_id="saa_doc::cf",
            title="Crop nutrient pricing stays firm",
            display_excerpt="Potash and fertilizer markets remain tight.",
            chunk_text="Global crop nutrient supply stayed tight as potash shipments remained constrained.",
            search_text="Global crop nutrient supply stayed tight as potash shipments remained constrained.",
            bundle_subject="CF",
            source_provider="Seeking Alpha",
            search_provider="tavily",
            research_scope="symbol",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["CF"],
            mentioned_commodities=["fertilizer"],
            event_tags=["supply_chain"],
            mentioned_dates=["2026-03-24"],
            embedding_model="text-embedding-3-small",
            embedding_vector_json=json.dumps([1.0, 0.0]),
        ),
        _chunk_row(
            chunk_record_id="saa_chunk::aapl",
            canonical_document_id="saa_doc::aapl",
            title="AAPL gains after product event",
            display_excerpt="AAPL rose on launch commentary.",
            chunk_text="AAPL rose after a product launch update.",
            search_text="AAPL rose after a product launch update.",
            bundle_subject="AAPL",
            source_provider="Reuters",
            search_provider="serpapi",
            research_scope="symbol",
            published_at="2026-03-24T17:30:00Z",
            published_date="2026-03-24",
            primary_date="2026-03-24",
            mentioned_tickers=["AAPL"],
            mentioned_commodities=[],
            event_tags=["product_launch"],
            mentioned_dates=["2026-03-24"],
            embedding_model="text-embedding-3-small",
            embedding_vector_json=json.dumps([0.0, 1.0]),
        ),
    ]

    resolved = search_retained_evidence_chunks(
        query="fertilizer demand",
        use_semantic=True,
        limit=5,
        conn=_Conn(rows),
    )

    assert resolved["chunk_record_id"].tolist() == ["saa_chunk::cf"]
    assert resolved.iloc[0]["match_source"] == "semantic"
    assert resolved.iloc[0]["score_lexical"] == 0.0
    assert resolved.iloc[0]["score_embedding"] >= 0.99


def test_search_prepared_evidence_chunks_scores_in_memory_frame():
    frame = pd.DataFrame(
        [
            {
                "canonical_document_id": "saa_doc::uso",
                "document_id": "doc::uso",
                "chunk_id": "chunk::uso",
                "chunk_index": 1,
                "document_chunk_count": 2,
                "title": "USO falls as oil pulls back on ceasefire hopes",
                "display_excerpt": "USO and airlines moved as supply-risk eased.",
                "chunk_text": "USO and BNO fell as oil eased after ceasefire headlines while airlines rallied.",
                "bundle_subject": "USO",
                "source_kind": "search",
                "source_provider": "Reuters",
                "search_provider": "tavily",
                "research_scope": "home_summary",
                "published_at": pd.Timestamp("2026-03-24T17:30:00Z"),
                "published_date": "2026-03-24",
                "primary_date": "2026-03-24",
                "mentioned_tickers_json": json.dumps(["USO", "UAL"]),
                "mentioned_commodities_json": json.dumps(["oil"]),
                "event_tags_json": json.dumps(["geopolitics"]),
                "mentioned_dates_json": json.dumps(["2026-03-24"]),
            }
        ]
    )
    prepared, _ = prepare_retained_evidence_chunks(
        frame,
        dataset_name="attention_evidence_chunks",
        dataset_version_id="attention_evidence_chunks__20260414T180000Z__retentio",
        run_id="run-1",
        asof_time_utc=datetime(2026, 4, 14, 18, 0, tzinfo=timezone.utc),
    )

    resolved = search_prepared_evidence_chunks(
        prepared,
        query="oil eased after ceasefire headlines",
        research_scopes=["home_summary"],
        tickers=["USO"],
        limit=5,
        use_semantic=False,
    )

    assert resolved["chunk_record_id"].tolist() == [prepared.iloc[0]["chunk_record_id"]]
    assert resolved.iloc[0]["match_source"] == "lexical"
    assert resolved.iloc[0]["search_score"] > 0
