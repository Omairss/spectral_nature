import os
from types import SimpleNamespace

import pandas as pd
import psycopg
import pytest

from services.saa import semantic_index as index


@pytest.fixture
def connection(monkeypatch):
    dsn = os.getenv("SN_DB_TEST_DSN")
    if not dsn:
        pytest.skip("Set SN_DB_TEST_DSN for temporary-relation PostgreSQL checks")
    monkeypatch.setenv("PIPELINE_DATASET_NAMESPACE", "development")
    with psycopg.connect(dsn, connect_timeout=10, options="-c statement_timeout=10000",
                         application_name="sn_projection_fixture") as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TEMP TABLE saa_zopedia_pages (
                page_id text PRIMARY KEY,title text,summary text,body_markdown text,
                source_urls_json jsonb DEFAULT '[]',source_document_ids_json jsonb DEFAULT '[]',
                version int DEFAULT 1,updated_at_utc timestamptz DEFAULT now(),status text DEFAULT 'active');
                CREATE TEMP TABLE saa_evidence_chunks (LIKE public.saa_evidence_chunks INCLUDING ALL);
                CREATE TEMP TABLE aql_evidence_projection_state (
                projection text,source_key text,source_version text,updated_at timestamptz DEFAULT now(),
                PRIMARY KEY(projection,source_key));""")
        conn.commit()
        yield conn


def test_changed_unchanged_and_archived_pages_preserve_current_projection(connection):
    conn = connection
    with conn.cursor() as cur:
        cur.execute("INSERT INTO saa_zopedia_pages(page_id,title,body_markdown) VALUES('qa_page','QA',%s)", ("original " * 1100,))
    conn.commit()
    assert index.sync_zopedia_evidence(conn) == 3
    assert index.sync_zopedia_evidence(conn) == 0
    with conn.cursor() as cur:
        cur.execute("UPDATE saa_zopedia_pages SET body_markdown='revised original',version=2")
    conn.commit()
    assert index.sync_zopedia_evidence(conn) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_text FROM saa_evidence_chunks")
        assert cur.fetchall() == [("revised original",)]
        cur.execute("UPDATE saa_zopedia_pages SET status='archived'")
    conn.commit()
    assert index.sync_zopedia_evidence(conn) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM saa_evidence_chunks")
        assert cur.fetchone()[0] == 0


def test_interrupted_projection_retries_without_skipping_content(connection, monkeypatch):
    conn = connection
    with conn.cursor() as cur:
        cur.execute("INSERT INTO saa_zopedia_pages(page_id,title,body_markdown) VALUES('qa_page','QA','original text')")
    conn.commit()
    original = index._projected_batch
    monkeypatch.setattr(index, "_projected_batch", lambda *a: (_ for _ in ()).throw(ConnectionError("Interrupted checkpoint")))
    with pytest.raises(ConnectionError):
        index.sync_zopedia_evidence(conn)
    conn.rollback()
    monkeypatch.setattr(index, "_projected_batch", original)
    assert index.sync_zopedia_evidence(conn) == 1
    assert index.sync_zopedia_evidence(conn) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_text FROM saa_evidence_chunks")
        assert cur.fetchall() == [("original text",)]


def test_news_versions_are_loaded_only_until_successful_retention(connection, monkeypatch):
    conn = connection
    loaded = []

    def frames(name, *, limit, exclude_versions):
        loaded.extend(v for v in ["qa_v1", "qa_v2"] if v not in exclude_versions)
        return [(pd.DataFrame([{"text": v}]), SimpleNamespace(dataset_version_id=v, asof_time_utc="2026-10-16"))
                for v in ["qa_v1", "qa_v2"] if v not in exclude_versions]

    monkeypatch.setattr("services.pipeline_store.load_recent_dataset_frames", frames)
    monkeypatch.setattr(index, "retain_news_evidence", lambda *a, **kw: 1)
    assert index.sync_recent_news_evidence(conn) == 2
    assert index.sync_recent_news_evidence(conn) == 0
    assert loaded == ["qa_v1", "qa_v2"]


def test_failed_blob_read_is_not_checkpointed_as_an_empty_publication(connection, monkeypatch):
    conn = connection
    available = []
    metadata = SimpleNamespace(dataset_version_id="qa_unavailable", asof_time_utc="2026-10-16", row_count=1)

    def frames(name, *, limit, exclude_versions):
        if metadata.dataset_version_id in exclude_versions:
            return []
        return [(pd.DataFrame(available), metadata)]

    monkeypatch.setattr("services.pipeline_store.load_recent_dataset_frames", frames)
    monkeypatch.setattr(index, "retain_news_evidence", lambda *a, **kw: 1)
    with pytest.raises(RuntimeError, match="remain pending"):
        index.sync_recent_news_evidence(conn)
    available.append({"text": "Original recovered text"})
    assert index.sync_recent_news_evidence(conn) == 1
    assert index.sync_recent_news_evidence(conn) == 0
