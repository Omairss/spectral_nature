from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd

from services.aql.evidence_index import annotate_source_documents
from services.saa.storage import prepare_retained_source_documents


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
