"""Corpus-wide embedding index for the shared retained-evidence store.

Producers persist text first. This worker embeds pending content in batches,
outside dataset materialization. Search never embeds the corpus or scans only
recent documents. Equal content shares one vector across repeated captures.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from typing import Any

from services.llm import load_embedding_client, embedding_space

log = logging.getLogger(__name__)


def _projection_store(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('aql_evidence_projection_state')")
        exists = cur.fetchone()[0] is not None
    conn.commit()
    if not exists:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS aql_evidence_projection_state (
                projection TEXT NOT NULL, source_key TEXT NOT NULL, source_version TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(projection,source_key))""")
        conn.commit()


def _projected_batch(conn: Any, projection: str, versions: list[tuple[str, str]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO aql_evidence_projection_state(projection,source_key,source_version)
            VALUES(%s,%s,%s) ON CONFLICT(projection,source_key) DO UPDATE
            SET source_version=excluded.source_version,updated_at=now()""",
            [(projection, key, version) for key, version in versions])
    conn.commit()


def _projected(conn: Any, projection: str, key: str, version: str) -> None:
    _projected_batch(conn, projection, [(key, version)])


def bootstrap_embedding_index(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""CREATE TABLE IF NOT EXISTS aql_evidence_embeddings (
            content_sha256 TEXT NOT NULL, embedding_space TEXT NOT NULL,
            embedding vector NOT NULL, indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (content_sha256, embedding_space))""")
    conn.commit()


def index_pending_evidence(
    *, conn: Any, batch_size: int = 64, max_batches: int = 100, client: Any | None = None
) -> dict[str, Any]:
    client = client or load_embedding_client()
    if client is None:
        raise RuntimeError("Embedding provider is not configured independently of the chat model.")
    bootstrap_embedding_index(conn)
    space = embedding_space(client)
    indexed = 0
    for _ in range(max_batches):
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (c.chunk_identity_sha256)
                           c.chunk_identity_sha256, left(concat_ws(E'\n',c.title,c.chunk_text),12000)
                           FROM saa_evidence_chunks c
                           LEFT JOIN aql_evidence_embeddings e ON e.content_sha256=c.chunk_identity_sha256
                                AND e.embedding_space=%s
                           WHERE e.content_sha256 IS NULL
                           ORDER BY c.chunk_identity_sha256, c.asof_time_utc DESC LIMIT %s""",
                (space, batch_size),
            )
            rows = cur.fetchall()
        conn.commit()
        if not rows:
            break
        vectors = client.generate_embeddings([row[1] for row in rows])
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO aql_evidence_embeddings(content_sha256,embedding_space,embedding)
                               VALUES (%s,%s,%s::vector) ON CONFLICT DO NOTHING""",
                [(row[0], space, json.dumps(vector)) for row, vector in zip(rows, vectors, strict=True)],
            )
        conn.commit()
        indexed += len(rows)
        log.info("Indexed %s evidence texts", indexed)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*),count(e.content_sha256) FROM saa_evidence_chunks c
                       LEFT JOIN aql_evidence_embeddings e ON e.content_sha256=c.chunk_identity_sha256
                            AND e.embedding_space=%s""",
            (space,),
        )
        total, covered = cur.fetchone()
    conn.commit()
    return {
        "indexed_texts": indexed,
        "total_chunks": total,
        "covered_chunks": covered,
        "pending_chunks": total - covered,
        "embedding_space": space,
    }


def refresh_evidence_index(*, conn: Any, batch_size: int = 64, max_batches: int = 100) -> dict[str, Any]:
    """Own a bounded refresh without retaining a connection across inference."""
    from services.pipeline_store import claim_execution_state, release_execution_state
    from services.work_scope import WorkScope, WorkCancelled
    from services.llm import llm_request_deadline
    owner = uuid.uuid4().hex
    budget = max(1, int(os.getenv("EVIDENCE_INDEX_RUN_BUDGET_SECONDS", "480")))
    acquired = claim_execution_state("evidence_index", "refresh", owner=owner, seconds=budget + 300)
    if not acquired:
        return {"status": "already_running"}
    deadline = time.monotonic() + budget
    try:
        with WorkScope(deadline).activate(), llm_request_deadline(deadline):
            sync_zopedia_evidence(conn)
            sync_recent_news_evidence(conn)
            return index_pending_evidence(conn=conn, batch_size=batch_size, max_batches=max_batches)
    except WorkCancelled:
        return {"status": "paused", "reason": "execution_budget", "scan_complete": False}
    finally:
        conn.rollback()
        release_execution_state("evidence_index", "refresh", owner=owner)


def semantic_candidates(
    conn: Any,
    *,
    query: str,
    clauses: list[str],
    params: list[Any],
    limit: int,
    client: Any | None = None,
) -> list[str]:
    client = client or load_embedding_client()
    if client is None:
        return []
    vector = client.generate_embeddings([query])[0]
    # Exact nearest neighbours over the full corpus. At this corpus size this
    # preserves recall and avoids ANN filtering losses. Only IDs cross the wire.
    with conn.cursor() as cur:
        cur.execute(
            f"""WITH matches AS (
                          SELECT c.chunk_record_id, c.source_kind,
                            e.embedding <=> %s::vector AS distance,
                            row_number() OVER (
                              PARTITION BY coalesce(nullif(c.canonical_document_id,''),c.chunk_identity_sha256)
                              ORDER BY e.embedding <=> %s::vector, c.asof_time_utc DESC
                            ) AS document_rank
                          FROM saa_evidence_chunks c JOIN aql_evidence_embeddings e
                            ON e.content_sha256=c.chunk_identity_sha256 AND e.embedding_space=%s
                          WHERE {' AND '.join(clauses)}
                        ), diverse AS (
                          SELECT *, row_number() OVER (PARTITION BY CASE WHEN source_kind='zopedia_memory' THEN 'knowledge' WHEN source_kind IN ('agent_research','research','context') THEN 'derived' ELSE 'original' END ORDER BY distance) AS kind_rank
                          FROM matches WHERE document_rank=1
                        ) SELECT chunk_record_id FROM diverse ORDER BY kind_rank,distance LIMIT %s""",
            [json.dumps(vector), json.dumps(vector), embedding_space(client), *params, limit],
        )
        return [row[0] for row in cur.fetchall()]


def sync_zopedia_evidence(conn: Any) -> int:
    """Project changed pages in bounded batches without reloading unchanged bodies."""
    import pandas as pd
    from .storage import persist_retained_evidence_chunks
    _projection_store(conn)
    # Retire inactive pages even during a cold projection backlog. Unchanged
    # active pages keep their prior complete chunks throughout the refresh.
    with conn.cursor() as cur:
        cur.execute("""DELETE FROM saa_evidence_chunks c WHERE c.source_kind='zopedia_memory'
            AND NOT EXISTS(SELECT 1 FROM saa_zopedia_pages p
                           WHERE p.page_id=c.canonical_document_id AND p.status='active')""")
        cur.execute("""DELETE FROM aql_evidence_projection_state s WHERE s.projection='zopedia:v1'
            AND NOT EXISTS(SELECT 1 FROM saa_zopedia_pages p
                           WHERE p.page_id=s.source_key AND p.status='active')""")
    conn.commit()
    count = 0
    batch_size = max(1, int(os.getenv("EVIDENCE_PROJECTION_BATCH_PAGES", "64")))
    while True:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.page_id,p.title,p.summary,p.body_markdown,p.source_urls_json,
                                  p.source_document_ids_json,p.version,p.updated_at_utc,
                                  concat_ws(':',p.version::text,p.updated_at_utc::text,'v1') AS signature
                           FROM saa_zopedia_pages p LEFT JOIN aql_evidence_projection_state s
                             ON s.projection='zopedia:v1' AND s.source_key=p.page_id
                           WHERE p.status='active' AND s.source_version IS DISTINCT FROM
                             concat_ws(':',p.version::text,p.updated_at_utc::text,'v1')
                           ORDER BY p.page_id LIMIT %s""", (batch_size,))
            pages = cur.fetchall()
        conn.commit()
        if not pages:
            return count
        rows, versions = [], []
        for page_id, title, summary, body, urls, documents, version, updated, signature in pages:
            versions.append((page_id, signature))
            text = str(body or summary or "")
            refs = [{"url": url} for url in (urls or []) if isinstance(url, str)]
            refs += [{"canonical_document_id": doc} for doc in (documents or []) if isinstance(doc, str)]
            for start in range(0, len(text), 4000):
                rows.append({"chunk_text": text[start:start + 4500], "chunk_id": f"{page_id}:{start}",
                    "title": title, "source_kind": "zopedia_memory", "source_provider": "zopedia",
                    "canonical_document_id": page_id, "published_at": None, "page_id": page_id,
                    "source_version": version, "source_refs": refs, "evidence_type": "retained_knowledge",
                    "retrieved_at": str(updated), "asof_time_utc": updated})
        projected = persist_retained_evidence_chunks("zopedia_memory", pd.DataFrame(rows),
            dataset_version_id="current", run_id="zopedia_index", asof_time_utc=pd.Timestamp.now(tz="UTC"), conn=conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saa_evidence_chunks WHERE source_kind='zopedia_memory' "
                        "AND canonical_document_id=ANY(%s) AND NOT(chunk_record_id=ANY(%s))",
                        ([key for key, _ in versions], list(projected.get("chunk_record_id", []))))
        _projected_batch(conn, "zopedia:v1", versions)
        count += len(rows)



def sync_recent_news_evidence(conn: Any) -> int:
    from services.pipeline_store import load_recent_dataset_frames, dataset_storage_name

    _projection_store(conn)
    projection = "news:v1:" + dataset_storage_name("news_articles")
    with conn.cursor() as cur:
        cur.execute("SELECT source_key FROM aql_evidence_projection_state WHERE projection=%s", (projection,))
        completed = {row[0] for row in cur.fetchall()}
    conn.commit()
    total = 0
    unavailable = []
    for frame, meta in load_recent_dataset_frames("news_articles", limit=8, exclude_versions=completed):
        if frame.empty and getattr(meta, "row_count", 0) > 0:
            unavailable.append(meta.dataset_version_id)
            continue
        if not frame.empty:
            total += retain_news_evidence(
                frame,
                conn=conn,
                dataset_version_id=meta.dataset_version_id,
                run_id="evidence_index::" + meta.dataset_version_id,
                asof=meta.asof_time_utc,
            )
        _projected(conn, projection, meta.dataset_version_id, meta.dataset_version_id)
    if unavailable:
        raise RuntimeError(f"{len(unavailable)} news publications unavailable; their projections remain pending")
    return total


def retain_news_evidence(frame: Any, *, conn: Any, dataset_version_id: str, run_id: str, asof: Any) -> int:
    """Retain the original provider text independently of any generated story."""
    import pandas as pd
    from .storage import (
        persist_retained_evidence_chunks,
        build_canonical_document_fields,
        _coerce_text,
    )

    rows = []
    for _, article in frame.iterrows():
        item = article.dropna().to_dict()
        title = _coerce_text(item.get("headline")) or _coerce_text(item.get("title"))
        text = (
            _coerce_text(item.get("content"))
            or _coerce_text(item.get("summary"))
            or _coerce_text(item.get("description"))
            or title
        )
        url = _coerce_text(item.get("url"))
        published = pd.to_datetime(article.get("published_at"), utc=True, errors="coerce")
        source = _coerce_text(item.get("source"))
        canonical = build_canonical_document_fields(
            {"url": url, "title": title, "raw_text": text, "source_provider": source}
        )
        if title or text:
            rows.append(
                {
                    **canonical,
                    "title": title,
                    "chunk_text": text,
                    "chunk_id": _coerce_text(item.get("id")) or canonical["canonical_document_id"],
                    "canonical_url": url,
                    "source_kind": "news",
                    "source_provider": source,
                    "evidence_type": "original_reporting",
                    "published_at": published,
                    "published_date": (published.strftime("%Y-%m-%d") if pd.notna(published) else None),
                }
            )
    if rows:
        persist_retained_evidence_chunks(
            "news_articles",
            pd.DataFrame(rows),
            dataset_version_id=dataset_version_id,
            run_id=run_id,
            asof_time_utc=asof,
            conn=conn,
        )
    return len(rows)


if __name__ == "__main__":
    from .storage import _db_connection

    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-batches", type=int, default=1000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    connection = _db_connection()
    if connection is None:
        raise RuntimeError("Evidence database unavailable")
    try:
        print(
            json.dumps(
                refresh_evidence_index(
                    conn=connection, batch_size=args.batch_size, max_batches=args.max_batches
                )
            )
        )
    finally:
        connection.close()
