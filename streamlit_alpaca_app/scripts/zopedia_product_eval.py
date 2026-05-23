#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
from typing import Any
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.agent_tools import invoke_tool
from services.aql_zopedia_engine import run_aql_zopedia_agent
from services.llm import check_llm_readiness
from services.saa.storage import _db_connection
from services.saa.zopedia import (
    build_zopedia_change_proposal,
    fetch_youtube_transcript,
    ingest_zopedia_source,
    list_zopedia_change_proposals,
    load_zopedia_page,
    persist_zopedia_change_proposals,
    persist_zopedia_pages,
    search_zopedia_pages,
    zopedia_page_neighborhood,
)
from services.zopedia_runtime import load_zopedia_llm_client


YOUTUBE_FIXTURES = [
    "https://www.youtube.com/watch?v=BOT2rrm10RM",
    "https://www.youtube.com/watch?v=t6y_VmxuO28",
    "https://www.youtube.com/watch?v=n889nI8sR84",
]

SOURCE_FIXTURES = [
    {
        "slug": "diesel-hormuz",
        "title": "Diesel Pinch Point And Hormuz Risk",
        "query": "diesel pinch point Hormuz equity la la land",
        "expected_terms": ["diesel", "Hormuz", "equity"],
        "text": (
            "Jeff Currie argued that the important commodity stress point is not only headline crude oil. "
            "The diesel pinch point matters because middle distillates carry freight, industry, and parts of the real economy. "
            "If Hormuz risk tightens physical barrels, refiners and consumers can feel pressure before broad equity indexes price it. "
            "The note also says equity markets can look like la la land when investors ignore energy bottlenecks."
        ),
    },
    {
        "slug": "inflation-bonds",
        "title": "Inflation Surge And Bond Market Stress",
        "query": "inflation surge bond market disaster recession headed higher",
        "expected_terms": ["inflation", "bond", "recession"],
        "text": (
            "The inflation fixture says sticky service inflation and supply pressure can keep nominal yields elevated. "
            "That creates a bond market stress path where duration loses appeal, refinancing costs rise, and recession risk increases. "
            "The main claim is that inflation can damage bonds before the equity market fully accepts the growth slowdown."
        ),
    },
    {
        "slug": "nasdaq-euphoria",
        "title": "Nasdaq Euphoria Hitting Its Limit",
        "query": "Nasdaq euphoria hitting its limit mega cap concentration",
        "expected_terms": ["Nasdaq", "euphoria", "concentration"],
        "text": (
            "The Nasdaq fixture says market euphoria can persist while mega-cap concentration masks weaker breadth. "
            "The important point is that index strength can become fragile when performance depends on a narrow leadership group. "
            "Risk rises when positioning, valuation, and expectations all assume the same optimistic technology outcome."
        ),
    },
]


class EvalStepTimeout(TimeoutError):
    pass


@contextmanager
def _step_timeout(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):
        del signum, frame
        raise EvalStepTimeout(f"Eval step timed out after {seconds}s.")

    previous = signal.getsignal(signal.SIGALRM)
    previous_alarm = signal.alarm(0)
    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if previous_alarm:
            signal.alarm(previous_alarm)


@dataclass
class EvalCheck:
    name: str
    status: str
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


@dataclass
class EvalRun:
    run_id: str
    started_at_utc: str
    checks: list[EvalCheck] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, status: str, detail: str, *, metrics: dict[str, Any] | None = None, elapsed: float = 0.0) -> None:
        self.checks.append(
            EvalCheck(
                name=name,
                status=status,
                detail=detail,
                metrics=metrics or {},
                elapsed_seconds=round(float(elapsed), 3),
            )
        )

    def counts(self) -> dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for check in self.checks:
            out[check.status] = out.get(check.status, 0) + 1
        return out

    def decision(self) -> str:
        names = {check.name: check for check in self.checks}
        if any(check.status == "fail" for check in self.checks):
            return "hold"
        if names.get("youtube_transcripts", EvalCheck("", "pass", "")).status != "pass":
            return "dev-only"
        if any(check.status == "warn" for check in self.checks):
            return "dev-only"
        return "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at_utc": self.started_at_utc,
            "decision": self.decision(),
            "counts": self.counts(),
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "detail": check.detail,
                    "metrics": check.metrics,
                    "elapsed_seconds": check.elapsed_seconds,
                }
                for check in self.checks
            ],
            "cleanup": self.cleanup,
        }


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _safe_slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean(value).lower()).strip("-")


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _status_for(condition: bool, *, warn: bool = False) -> str:
    if condition:
        return "pass"
    return "warn" if warn else "fail"


def _http_status(url: str, *, method: str = "GET", timeout: int = 30) -> int | None:
    try:
        request = Request(url, method=method)
        with urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except Exception:
        return None


def _run_timed_step(
    run: EvalRun,
    *,
    name: str,
    timeout_seconds: int,
    func,
) -> Any:
    started = time.monotonic()
    print(f"[zopedia-eval] running {name}", flush=True)
    try:
        with _step_timeout(timeout_seconds):
            return func()
    except EvalStepTimeout as exc:
        run.add(
            name,
            "fail",
            str(exc),
            metrics={"timeout_seconds": timeout_seconds},
            elapsed=time.monotonic() - started,
        )
        return None
    except Exception as exc:
        run.add(
            name,
            "fail",
            f"{type(exc).__name__}: {_clean(exc)[:500]}",
            elapsed=time.monotonic() - started,
        )
        return None


def _cleanup_eval_rows(conn: Any, tag: str) -> dict[str, Any]:
    if conn is None:
        return {"status": "skipped", "reason": "no database connection"}
    pattern = f"%{tag}%"
    deleted_pages = 0
    deleted_proposals = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM saa_zopedia_change_proposals
            WHERE title ILIKE %s
               OR rationale ILIKE %s
               OR proposal_payload_json::text ILIKE %s
            """,
            (pattern, pattern, pattern),
        )
        deleted_proposals = int(cur.rowcount or 0)
        cur.execute(
            """
            DELETE FROM saa_zopedia_pages
            WHERE title ILIKE %s
               OR summary ILIKE %s
               OR body_markdown ILIKE %s
               OR metadata_json::text ILIKE %s
            """,
            (pattern, pattern, pattern, pattern),
        )
        deleted_pages = int(cur.rowcount or 0)
    conn.commit()
    return {"status": "completed", "deleted_pages": deleted_pages, "deleted_proposals": deleted_proposals}


def _run_environment_checks(run: EvalRun, *, check_urls: bool) -> Any:
    started = time.monotonic()
    readiness = check_llm_readiness()
    llm_status = _clean(readiness.get("llm"))
    run.add(
        "llm_runtime",
        _status_for(llm_status == "configured"),
        llm_status or "LLM runtime status unavailable.",
        metrics={
            "provider": readiness.get("llm_provider"),
            "model": readiness.get("llm_model"),
            "deployment": readiness.get("llm_deployment"),
            "embeddings": readiness.get("embeddings"),
        },
        elapsed=time.monotonic() - started,
    )

    started = time.monotonic()
    conn = _db_connection()
    db_ok = False
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = bool(cur.fetchone())
        except Exception:
            db_ok = False
    run.add(
        "dev_database",
        _status_for(db_ok),
        "Dev database connection is usable." if db_ok else "Dev database connection is unavailable.",
        elapsed=time.monotonic() - started,
    )

    if check_urls:
        started = time.monotonic()
        api_status = _http_status("https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io/health")
        ui_status = _http_status("https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io", method="HEAD")
        run.add(
            "dev_deployed_endpoints",
            _status_for(api_status == 200 and ui_status == 200, warn=True),
            f"API health={api_status}; UI root={ui_status}.",
            metrics={"api_health_status": api_status, "ui_root_status": ui_status},
            elapsed=time.monotonic() - started,
        )
    return conn


def _run_youtube_checks(run: EvalRun) -> None:
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    for url in YOUTUBE_FIXTURES:
        result = fetch_youtube_transcript(url, timeout=12)
        rows.append(
            {
                "url": url,
                "video_id": result.get("video_id"),
                "status": result.get("status"),
                "provider": result.get("provider"),
                "chars": len(_clean(result.get("transcript"))),
                "provider_errors": result.get("provider_errors") or [],
            }
        )
    ok_count = sum(1 for row in rows if row["status"] == "ok" and row["chars"] > 0)
    status = "pass" if ok_count == len(rows) else "fail"
    run.add(
        "youtube_transcripts",
        status,
        f"{ok_count}/{len(rows)} real YouTube transcript fixtures returned text.",
        metrics={"fixtures": rows},
        elapsed=time.monotonic() - started,
    )


def _run_source_memory_checks(run: EvalRun, *, conn: Any, tag: str) -> list[dict[str, Any]]:
    started = time.monotonic()
    llm_client = load_zopedia_llm_client(surface="zopedia.product_eval.ingest_source")
    pages: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    for fixture in SOURCE_FIXTURES:
        title = f"{tag} {fixture['title']}"
        text = f"{fixture['text']}\n\nEval tag: {tag}."
        result = ingest_zopedia_source(
            title=title,
            source_text=text,
            url=f"https://eval.local/zopedia/{tag}/{fixture['slug']}",
            source_type="product_eval",
            llm_client=llm_client,
            conn=conn,
        )
        rows = list(result.get("pages") or [])
        pages.extend(row for row in rows if isinstance(row, dict))
        source_results.append(
            {
                "title": title,
                "status": result.get("status"),
                "enrichment_status": result.get("enrichment_status"),
                "page_count": result.get("page_count"),
                "page_titles": [_clean(row.get("title")) for row in rows[:8] if isinstance(row, dict)],
            }
        )
    enriched_count = sum(1 for item in source_results if item.get("enrichment_status") == "llm_enriched")
    minimum_pages = len(SOURCE_FIXTURES) * 2
    run.add(
        "source_ingest_and_page_generation",
        _status_for(enriched_count == len(SOURCE_FIXTURES) and len(pages) >= minimum_pages),
        f"Ingested {len(SOURCE_FIXTURES)} text fixtures into {len(pages)} Zopedia pages; {enriched_count} were LLM-enriched.",
        metrics={"sources": source_results, "page_count": len(pages)},
        elapsed=time.monotonic() - started,
    )

    started = time.monotonic()
    search_rows: list[dict[str, Any]] = []
    hits = 0
    for fixture in SOURCE_FIXTURES:
        query = f"{tag} {fixture['query']}"
        frame = search_zopedia_pages(query=query, limit=5, conn=conn, include_debug_sources=True)
        titles = [str(row.get("title") or "") for _, row in frame.iterrows()] if not frame.empty else []
        summaries = [str(row.get("summary") or "") for _, row in frame.iterrows()] if not frame.empty else []
        haystack = " ".join(titles + summaries).lower()
        expected_hit = any(term.lower() in haystack for term in fixture["expected_terms"])
        tag_hit = tag.lower() in haystack
        if expected_hit and tag_hit:
            hits += 1
        search_rows.append(
            {
                "query": query,
                "result_count": int(len(frame)),
                "top_titles": titles[:5],
                "expected_hit": expected_hit,
                "tag_hit": tag_hit,
            }
        )
    run.add(
        "wiki_search_recall",
        _status_for(hits == len(SOURCE_FIXTURES)),
        f"{hits}/{len(SOURCE_FIXTURES)} fixture searches found tagged expected content in top 5.",
        metrics={"searches": search_rows},
        elapsed=time.monotonic() - started,
    )

    started = time.monotonic()
    source_page = next((page for page in pages if _clean(page.get("page_type")) == "source"), pages[0] if pages else {})
    page_id = _clean(source_page.get("page_id"))
    loaded = load_zopedia_page(page_id=page_id, conn=conn) if page_id else {}
    body = _clean(loaded.get("body_markdown"))
    run.add(
        "exact_page_read",
        _status_for(bool(loaded) and tag in body),
        "Exact page read returned the requested tagged source page." if loaded else "Exact page read returned no page.",
        metrics={"page_id": page_id, "title": loaded.get("title"), "body_chars": len(body)},
        elapsed=time.monotonic() - started,
    )
    return pages


def _run_graph_and_proposal_checks(run: EvalRun, *, conn: Any, tag: str) -> None:
    started = time.monotonic()
    manual_pages = [
        {
            "page_type": "theme",
            "title": f"{tag} Manual Diesel Pinch Point",
            "summary": f"{tag} diesel pinch point links distillate tightness to freight and real-economy pressure.",
            "body_markdown": f"{tag} source-backed manual graph fixture for diesel pinch point.",
            "entity_refs": ["Diesel", "Refining", tag],
            "outgoing_links": [f"{tag} Manual Hormuz Risk", f"{tag} Manual Equity Fragility"],
            "metadata": {"eval_tag": tag},
        },
        {
            "page_type": "market_event",
            "title": f"{tag} Manual Hormuz Risk",
            "summary": f"{tag} Hormuz risk can tighten physical oil flows.",
            "body_markdown": f"{tag} source-backed manual graph fixture for Hormuz risk.",
            "entity_refs": ["Hormuz", "Oil", tag],
            "outgoing_links": [f"{tag} Manual Diesel Pinch Point"],
            "metadata": {"eval_tag": tag},
        },
        {
            "page_type": "concept",
            "title": f"{tag} Manual Equity Fragility",
            "summary": f"{tag} equity fragility rises when macro bottlenecks are ignored.",
            "body_markdown": f"{tag} source-backed manual graph fixture for equity fragility.",
            "entity_refs": ["Equities", tag],
            "outgoing_links": [f"{tag} Manual Diesel Pinch Point"],
            "metadata": {"eval_tag": tag},
        },
    ]
    frame = persist_zopedia_pages(manual_pages, conn=conn, source_title=f"{tag} manual graph fixture")
    seed_id = _clean(frame.iloc[0]["page_id"]) if not frame.empty else ""
    graph_1 = zopedia_page_neighborhood(page_id=seed_id, depth=2, conn=conn)
    graph_2 = zopedia_page_neighborhood(page_id=seed_id, depth=2, conn=conn)
    edge_keys_1 = sorted((edge.get("source"), edge.get("target"), edge.get("relation")) for edge in graph_1.get("edges") or [])
    edge_keys_2 = sorted((edge.get("source"), edge.get("target"), edge.get("relation")) for edge in graph_2.get("edges") or [])
    graph_ok = len(graph_1.get("nodes") or []) >= 3 and len(graph_1.get("edges") or []) >= 3 and edge_keys_1 == edge_keys_2
    run.add(
        "graph_neighborhood_determinism",
        _status_for(graph_ok),
        f"Manual graph fixture returned {len(graph_1.get('nodes') or [])} nodes and {len(graph_1.get('edges') or [])} edges.",
        metrics={
            "seed_page_id": seed_id,
            "node_count": len(graph_1.get("nodes") or []),
            "edge_count": len(graph_1.get("edges") or []),
            "deterministic_edges": edge_keys_1 == edge_keys_2,
        },
        elapsed=time.monotonic() - started,
    )

    started = time.monotonic()
    proposals = [
        build_zopedia_change_proposal(
            proposal_type="add",
            page_id=seed_id,
            title=f"{tag} Add link proposal",
            rationale=f"{tag} Add a link when a retained source introduces a new durable relationship.",
            payload={"target": f"{tag} Manual Hormuz Risk", "operation": "add_edge", "eval_tag": tag},
        ),
        build_zopedia_change_proposal(
            proposal_type="delete",
            page_id=seed_id,
            title=f"{tag} Delete stale link proposal",
            rationale=f"{tag} Delete a link when current evidence contradicts the stored relationship.",
            payload={"target": "stale-link", "operation": "delete_edge", "eval_tag": tag},
        ),
        build_zopedia_change_proposal(
            proposal_type="update",
            page_id=seed_id,
            title=f"{tag} Update page proposal",
            rationale=f"{tag} Update page text when a source adds a sharper caveat.",
            payload={"field": "summary", "operation": "update_page", "eval_tag": tag},
        ),
    ]
    persist_zopedia_change_proposals(proposals, conn=conn)
    listed = list_zopedia_change_proposals(status="open", limit=50, conn=conn)
    titles = [str(row.get("title") or "") for _, row in listed.iterrows()] if not listed.empty else []
    proposal_hits = sum(1 for title in titles if tag in title)
    run.add(
        "reviewable_add_delete_update_proposals",
        _status_for(proposal_hits >= 3),
        f"Created and listed {proposal_hits} tagged reviewable proposals.",
        metrics={"proposal_hits": proposal_hits, "titles": [title for title in titles if tag in title][:10]},
        elapsed=time.monotonic() - started,
    )


def _run_tool_checks(run: EvalRun, *, tag: str) -> None:
    started = time.monotonic()
    try:
        search_result = invoke_tool(
            service=None,
            tool_name="zopedia.search_pages",
            arguments={"query": f"{tag} diesel pinch point", "max_results": 5},
        )
    except Exception as exc:
        run.add(
            "agent_tool_zopedia_search_read_neighborhood",
            "fail",
            f"Zopedia tool invocation raised {type(exc).__name__}: {_clean(exc)[:300]}",
            elapsed=time.monotonic() - started,
        )
        return
    payload = search_result.get("payload") or {}
    rows = list(payload.get("summary") or [])
    page_id = _clean(rows[0].get("page_id")) if rows and isinstance(rows[0], dict) else ""
    read_result = invoke_tool(
        service=None,
        tool_name="zopedia.read_page",
        arguments={"page_id": page_id},
    ) if page_id else {}
    neighborhood_result = invoke_tool(
        service=None,
        tool_name="zopedia.neighborhood",
        arguments={"page_id": page_id, "depth": 1},
    ) if page_id else {}
    read_payload = read_result.get("payload") or {}
    neighborhood_payload = neighborhood_result.get("payload") or {}
    tool_ok = bool(rows) and bool(read_payload.get("page")) and int(neighborhood_payload.get("node_count") or 0) >= 1
    run.add(
        "agent_tool_zopedia_search_read_neighborhood",
        _status_for(tool_ok),
        f"Zopedia tools returned {len(rows)} search rows and neighborhood nodes={neighborhood_payload.get('node_count')}.",
        metrics={
            "search_rows": len(rows),
            "page_id": page_id,
            "read_status": read_payload.get("status"),
            "neighborhood_nodes": neighborhood_payload.get("node_count"),
            "neighborhood_edges": neighborhood_payload.get("edge_count"),
        },
        elapsed=time.monotonic() - started,
    )


def _run_agent_smoke(run: EvalRun, *, tag: str) -> None:
    started = time.monotonic()
    progress_events: list[dict[str, Any]] = []

    def _progress(event: dict[str, Any]) -> None:
        progress_events.append({k: event.get(k) for k in ("stage", "message", "tool_name", "progress")})

    query = (
        f"What does Zopedia memory say about {tag} diesel pinch point and Hormuz risk? "
        "Use Zopedia memory first, then answer with citations or clear limitations."
    )
    try:
        result = run_aql_zopedia_agent(
            query=query,
            task="product_eval_memory_question",
            surface="zopedia.product_eval",
            max_tool_calls=5,
            force_refresh=False,
            progress_callback=_progress,
            persist_findings=False,
        )
    except Exception as exc:
        run.add(
            "zopedia_agent_memory_question",
            "fail",
            f"Agent raised {type(exc).__name__}: {_clean(exc)[:300]}",
            metrics={"progress_events": progress_events[-10:]},
            elapsed=time.monotonic() - started,
        )
        return

    tool_calls = list(result.get("tool_calls") or [])
    tool_names = [_clean(call.get("tool_name")) for call in tool_calls if isinstance(call, dict)]
    answer = _clean(result.get("answer_markdown"))
    evidence_pack = result.get("aql_evidence_pack") or {}
    zopedia_refs = evidence_pack.get("zopedia_pages") or []
    answer_lc = answer.lower()
    cites_zopedia_memory = (
        "zopedia::" in answer
        or tag.lower() in answer_lc
        or any(_clean(ref.get("title")).lower() in answer_lc for ref in zopedia_refs if _clean(ref.get("title")))
    )
    agent_ok = (
        result.get("status") == "completed"
        and "zopedia.search_pages" in tool_names
        and "zopedia.read_page" in tool_names
        and len(zopedia_refs) > 0
        and cites_zopedia_memory
        and "no zopedia memory found" not in answer_lc
        and ("diesel" in answer_lc or "hormuz" in answer_lc)
    )
    run.add(
        "zopedia_agent_memory_question",
        _status_for(agent_ok),
        f"Agent status={result.get('status')}; tools={tool_names}; answer_chars={len(answer)}.",
        metrics={
            "status": result.get("status"),
            "confidence": result.get("confidence"),
            "tool_names": tool_names,
            "zopedia_ref_count": len(zopedia_refs),
            "cites_zopedia_memory": cites_zopedia_memory,
            "answer_preview": answer[:800],
            "answer_markdown": answer[:6000],
            "limitations": result.get("limitations") or [],
            "progress_stages": [event.get("stage") for event in progress_events],
        },
        elapsed=time.monotonic() - started,
    )


def _tool_names(result: dict[str, Any]) -> list[str]:
    return [_clean(call.get("tool_name")) for call in list(result.get("tool_calls") or []) if isinstance(call, dict)]


def _tool_index(tool_names: list[str], name: str) -> int:
    try:
        return tool_names.index(name)
    except ValueError:
        return 10_000


def _run_company_macro_resilience_checks(run: EvalRun, *, conn: Any, tag: str) -> None:
    company_query = (
        "For NVDA, look at fundamentals, recent news, and past Spectral Nature updates. "
        "What matters for the company right now?"
    )
    started = time.monotonic()
    try:
        company_result = run_aql_zopedia_agent(
            query=company_query,
            task="product_eval_company_question",
            surface="zopedia.product_eval",
            max_tool_calls=8,
            force_refresh=False,
            persist_findings=False,
        )
    except Exception as exc:
        run.add(
            "company_question_uses_fundamentals_news_and_history",
            "fail",
            f"Agent raised {type(exc).__name__}: {_clean(exc)[:300]}",
            elapsed=time.monotonic() - started,
        )
    else:
        names = _tool_names(company_result)
        answer = _clean(company_result.get("answer_markdown"))
        required = {
            "investigator.company_context",
            "investigator.fundamentals",
            "investigator.recent_news",
            "research.search_evidence",
        }
        ok = (
            company_result.get("status") == "completed"
            and required.issubset(set(names))
            and "nvda" in answer.lower()
            and "fundamental" in " ".join(names + [answer]).lower()
        )
        run.add(
            "company_question_uses_fundamentals_news_and_history",
            _status_for(ok),
            f"Company question status={company_result.get('status')}; tools={names}.",
            metrics={
                "query": company_query,
                "tool_names": names,
                "answer_preview": answer[:1000],
                "limitations": company_result.get("limitations") or [],
            },
            elapsed=time.monotonic() - started,
        )

    macro_query = (
        "What do CPI, unemployment, rates, and money supply say about the macro setup right now? "
        "Use Spectral Nature data first, then fetch outside evidence only for gaps."
    )
    started = time.monotonic()
    try:
        macro_result = run_aql_zopedia_agent(
            query=macro_query,
            task="product_eval_macro_question",
            surface="zopedia.product_eval",
            max_tool_calls=8,
            force_refresh=False,
            persist_findings=False,
        )
    except Exception as exc:
        run.add(
            "macro_question_uses_local_datapoints_first",
            "fail",
            f"Agent raised {type(exc).__name__}: {_clean(exc)[:300]}",
            elapsed=time.monotonic() - started,
        )
    else:
        names = _tool_names(macro_result)
        external_idx = _tool_index(names, "research.live_event_evidence")
        local_before_external = all(
            _tool_index(names, local_name) < external_idx
            for local_name in ("dataset.attention_macro_context_1d", "dataset.fred_dashboard")
            if local_name in names
        )
        ok = (
            macro_result.get("status") == "completed"
            and "dataset.fred_dashboard" in names
            and "dataset.attention_macro_context_1d" in names
            and local_before_external
        )
        run.add(
            "macro_question_uses_local_datapoints_first",
            _status_for(ok),
            f"Macro question status={macro_result.get('status')}; tools={names}.",
            metrics={
                "query": macro_query,
                "tool_names": names,
                "local_before_external": local_before_external,
                "answer_preview": _clean(macro_result.get("answer_markdown"))[:1000],
                "limitations": macro_result.get("limitations") or [],
            },
            elapsed=time.monotonic() - started,
        )

    false_query = (
        "NVDA revenue collapsed 90% last quarter. Verify this claim using fundamentals "
        "and recent evidence before explaining what happened."
    )
    started = time.monotonic()
    try:
        false_result = run_aql_zopedia_agent(
            query=false_query,
            task="product_eval_false_premise",
            surface="zopedia.product_eval",
            max_tool_calls=8,
            force_refresh=False,
            persist_findings=False,
        )
    except Exception as exc:
        run.add(
            "false_premise_is_checked_not_repeated",
            "fail",
            f"Agent raised {type(exc).__name__}: {_clean(exc)[:300]}",
            elapsed=time.monotonic() - started,
        )
    else:
        names = _tool_names(false_result)
        answer = _clean(false_result.get("answer_markdown"))
        answer_lc = answer.lower()
        refutes = any(
            marker in answer_lc
            for marker in (
                "does not support",
                "do not support",
                "not support",
                "no evidence",
                "contradict",
                "premise",
                "not show",
                "cannot confirm",
                "not backed",
                "not borne out",
            )
        )
        ok = (
            false_result.get("status") == "completed"
            and "investigator.fundamentals" in names
            and refutes
        )
        run.add(
            "false_premise_is_checked_not_repeated",
            _status_for(ok),
            f"False-premise question status={false_result.get('status')}; refutes={refutes}; tools={names}.",
            metrics={
                "query": false_query,
                "tool_names": names,
                "refutes_false_premise": refutes,
                "answer_preview": answer[:1400],
                "limitations": false_result.get("limitations") or [],
            },
            elapsed=time.monotonic() - started,
        )

    stale_pages = persist_zopedia_pages(
        [
            {
                "page_type": "analysis",
                "title": f"{tag} Stale Page About Data Center Power",
                "summary": f"{tag} stale memory says data-center power constraints are irrelevant to AI infrastructure.",
                "body_markdown": (
                    f"{tag} stale memory claim: data-center power constraints are irrelevant to AI infrastructure. "
                    "This page intentionally contains a stale claim for eval."
                ),
                "entity_refs": ["AI Infrastructure", "Power Constraints", tag],
                "outgoing_links": [],
                "metadata": {"eval_tag": tag, "eval_case": "stale_memory"},
            }
        ],
        conn=conn,
        source_title=f"{tag} stale memory eval",
    )
    page_id = _clean(stale_pages.iloc[0].get("page_id")) if not stale_pages.empty else ""
    update_query = (
        f"Zopedia memory page {page_id} is stale. It says data-center power constraints are irrelevant to AI infrastructure. "
        "New source text says power availability and grid bottlenecks can be a binding constraint on AI data-center buildout. "
        "Read the page and create a reviewable Zopedia update proposal; do not silently edit the page."
    )
    started = time.monotonic()
    try:
        update_result = run_aql_zopedia_agent(
            query=update_query,
            task="product_eval_memory_update",
            surface="zopedia.product_eval",
            max_tool_calls=8,
            force_refresh=False,
            persist_findings=False,
        )
    except Exception as exc:
        run.add(
            "stale_memory_creates_reviewable_update_proposal",
            "fail",
            f"Agent raised {type(exc).__name__}: {_clean(exc)[:300]}",
            elapsed=time.monotonic() - started,
        )
    else:
        names = _tool_names(update_result)
        listed = list_zopedia_change_proposals(status="open", limit=50, conn=conn)
        proposal_rows = []
        if not listed.empty:
            for _, row in listed.iterrows():
                title = _clean(row.get("title"))
                rationale = _clean(row.get("rationale"))
                payload_text = json.dumps(row.get("proposal_payload_json") or {}, sort_keys=True, default=str)
                if tag in title or tag in rationale or tag in payload_text or page_id in payload_text:
                    proposal_rows.append({"title": title, "rationale": rationale[:500]})
        ok = (
            update_result.get("status") == "completed"
            and "zopedia.propose_change" in names
            and bool(proposal_rows)
        )
        run.add(
            "stale_memory_creates_reviewable_update_proposal",
            _status_for(ok),
            f"Wiki update question status={update_result.get('status')}; tools={names}; proposals={len(proposal_rows)}.",
            metrics={
                "query": update_query,
                "page_id": page_id,
                "tool_names": names,
                "proposal_rows": proposal_rows[:5],
                "answer_preview": _clean(update_result.get("answer_markdown"))[:1000],
                "limitations": update_result.get("limitations") or [],
            },
            elapsed=time.monotonic() - started,
        )


def _write_reports(run: EvalRun, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"zopedia_product_eval_{run.run_id}.json"
    md_path = output_dir / f"ZOPEDIA_PRODUCT_EVAL_REPORT_{run.run_id}.md"
    data = run.to_dict()
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Zopedia Product Eval Report",
        "",
        f"Run ID: `{run.run_id}`",
        f"Started UTC: `{run.started_at_utc}`",
        f"Decision: **{data['decision']}**",
        "",
        "## Summary",
        "",
        f"- Passed: {data['counts'].get('pass', 0)}",
        f"- Warnings: {data['counts'].get('warn', 0)}",
        f"- Failed: {data['counts'].get('fail', 0)}",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail | Seconds |",
        "| --- | --- | --- | ---: |",
    ]
    for check in run.checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(f"| `{check.name}` | **{check.status}** | {detail} | {check.elapsed_seconds:.3f} |")
    lines.extend(["", "## Detailed Metrics", ""])
    for check in run.checks:
        lines.extend(
            [
                f"### {check.name}",
                "",
                f"Status: **{check.status}**",
                "",
                "```json",
                json.dumps(check.metrics, indent=2, sort_keys=True, default=str),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Cleanup",
            "",
            "```json",
            json.dumps(run.cleanup, indent=2, sort_keys=True, default=str),
            "```",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run headless product-quality evals for native Zopedia.")
    parser.add_argument(
        "--env-file",
        default=str(APP_ROOT / "infra" / ".generated" / "deployment.local.env"),
        help="Optional env file with dev LLM/DB settings.",
    )
    parser.add_argument("--tag", default="", help="Optional eval tag. Defaults to timestamped zopedia-eval tag.")
    parser.add_argument("--skip-agent", action="store_true", help="Skip the live Zopedia agent smoke test.")
    parser.add_argument("--skip-youtube", action="store_true", help="Skip real YouTube transcript checks.")
    parser.add_argument("--check-dev-urls", action="store_true", help="Check deployed dev API/UI URLs.")
    parser.add_argument("--keep-rows", action="store_true", help="Keep eval pages/proposals in dev DB.")
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=240,
        help="Max wall-clock seconds for one eval step before recording a failure and continuing.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(APP_ROOT / "documents" / "architecture" / "new_features" / "zopedia" / "eval_runs"),
        help="Directory for JSON and Markdown reports.",
    )
    args = parser.parse_args()

    env_file = Path(args.env_file)
    _load_env_file(env_file)
    os.environ.setdefault("LLM_TIMEOUT_SECONDS", "90")

    now = datetime.now(timezone.utc)
    tag = _safe_slug(args.tag) or f"zopedia-eval-{now.strftime('%Y%m%d-%H%M%S')}"
    run = EvalRun(run_id=tag, started_at_utc=now.isoformat())

    conn = None
    try:
        conn = _run_timed_step(
            run,
            name="environment_checks_step",
            timeout_seconds=args.step_timeout_seconds,
            func=lambda: _run_environment_checks(run, check_urls=args.check_dev_urls),
        )
        if conn is not None:
            _cleanup_eval_rows(conn, tag)
        if not args.skip_youtube:
            _run_timed_step(
                run,
                name="youtube_transcripts_step",
                timeout_seconds=args.step_timeout_seconds,
                func=lambda: _run_youtube_checks(run),
            )
        if conn is not None:
            _run_timed_step(
                run,
                name="source_memory_step",
                timeout_seconds=args.step_timeout_seconds,
                func=lambda: _run_source_memory_checks(run, conn=conn, tag=tag),
            )
            _run_timed_step(
                run,
                name="graph_and_proposal_step",
                timeout_seconds=args.step_timeout_seconds,
                func=lambda: _run_graph_and_proposal_checks(run, conn=conn, tag=tag),
            )
            _run_timed_step(
                run,
                name="zopedia_tool_step",
                timeout_seconds=args.step_timeout_seconds,
                func=lambda: _run_tool_checks(run, tag=tag),
            )
            if not args.skip_agent:
                _run_timed_step(
                    run,
                    name="agent_memory_smoke_step",
                    timeout_seconds=args.step_timeout_seconds,
                    func=lambda: _run_agent_smoke(run, tag=tag),
                )
                _run_timed_step(
                    run,
                    name="company_macro_resilience_step",
                    timeout_seconds=args.step_timeout_seconds,
                    func=lambda: _run_company_macro_resilience_checks(run, conn=conn, tag=tag),
                )
        else:
            run.add("zopedia_storage_flows", "fail", "Skipped storage, graph, tool, and agent checks because DB was unavailable.")
    finally:
        if conn is not None:
            if args.keep_rows:
                run.cleanup = {"status": "skipped", "reason": "--keep-rows was set"}
            else:
                run.cleanup = _cleanup_eval_rows(conn, tag)
            try:
                conn.close()
            except Exception:
                pass

    json_path, md_path = _write_reports(run, Path(args.output_dir))
    print(json.dumps({"decision": run.decision(), "counts": run.counts(), "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 1 if run.decision() == "hold" else 0


if __name__ == "__main__":
    raise SystemExit(main())
