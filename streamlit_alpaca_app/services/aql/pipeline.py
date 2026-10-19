"""
AQL pipeline — top-level orchestration for bottom-up attention artifact generation.
"""
from __future__ import annotations

from services.work_scope import WorkScope

import hashlib
import json
import os
import queue
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from compute.signal_extraction import _history_correlation_map
from ..attention_signal_graph import _graph_edges
from ..attention_macro_signals import _build_macro_relationship_checks
from ..runtime_policy import attention_graph_policy
from ..json_utils import to_jsonable
from ..llm import EmbeddingClient, llm_request_deadline
from .constants import (
    AgenticAttentionArtifacts,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_WRITER_MODEL,
    LLMClient,
)
from ._shared import (
    _augment_candidate_frame,
    _latest_yield_facts,
    _yield_context_relevant,
)
from services.common.market_activity import (
    _coerce_float,
    _coerce_text,
    _json_dumps,
    _normalize_symbol,
)
from .config import _load_attention_macro_signal_profile
from . import config as _aql_config
from .collector import _candidate_context_documents, _peer_candidates, research_attention_candidate
from .extractor import _chunk_source_documents, _serialize_claims_frame
from .evidence_index import annotate_source_documents
from .evidence_pack import materialize_research_artifacts
from .events import research_source_events
from .assembler import _build_candidate_bundle, _build_event_bundle, build_observed_cohort_event_bundles
from .clusterer import _cluster_candidates
from .macro import (
    _apply_macro_diagnostics_to_release_bundles,
    _build_attention_hypotheses_from_relationship_checks,
    _build_attention_macro_context_frame,
    _build_macro_release_events,
    _empty_attention_hypotheses_frame,
    _empty_attention_macro_context_frame,
    _empty_macro_causal_graph_edges_frame,
    _empty_macro_relationship_checks_frame,
    _empty_macro_release_events_frame,
    _materialize_macro_causal_graph_edges,
    _verify_macro_hypotheses_with_web_evidence,
)


class AQLStepTimeout(TimeoutError):
    pass


_TIMEOUT_SETTINGS: ContextVar[dict[str, int]] = ContextVar("aql_timeout_settings", default={})


@contextmanager
def aql_timeout_scope(settings: dict[str, int]):
    token = _TIMEOUT_SETTINGS.set({**_TIMEOUT_SETTINGS.get(), **settings})
    try:
        yield
    finally:
        _TIMEOUT_SETTINGS.reset(token)


def _env_positive_int(name: str, default: int) -> int:
    raw = str(_TIMEOUT_SETTINGS.get().get(name, os.getenv(name) or default)).strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = default
    return max(parsed, 1)


def _candidate_research_timeout_seconds() -> int:
    return _env_positive_int("AQL_CANDIDATE_RESEARCH_TIMEOUT_SECONDS", 90)


def _candidate_research_max_workers() -> int:
    return _env_positive_int("AQL_CANDIDATE_RESEARCH_MAX_WORKERS", 4)


def _event_bundle_timeout_seconds() -> int:
    return _env_positive_int("AQL_EVENT_BUNDLE_TIMEOUT_SECONDS", 60)


def _macro_verification_timeout_seconds() -> int:
    return _env_positive_int("AQL_MACRO_VERIFICATION_TIMEOUT_SECONDS", 120)


def _call_with_timeout(
    label: str, timeout_seconds: int, fn: Callable[[], Any], *,
    on_timeout: Callable[[], Any] | None = None,
) -> Any:
    if timeout_seconds <= 0:
        raise AQLStepTimeout(f"{label} has no remaining execution budget")

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    work = WorkScope(time.monotonic() + float(timeout_seconds))
    deadline = time.monotonic() + float(timeout_seconds)

    def _runner() -> None:
        try:
            with work.activate(), llm_request_deadline(deadline):
                value = fn()
            result_queue.put(("ok", value))
        except BaseException as exc:
            result_queue.put(("error", exc))

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(_runner), name=f"aql-timeout-{label[:32]}", daemon=True)
    thread.start()
    thread.join(float(timeout_seconds))
    if thread.is_alive():
        retained = on_timeout() if on_timeout is not None else None
        work.cancel()
        if retained is not None:
            print(f"[warn] {label} exceeded {timeout_seconds}s; using retained research checkpoint", flush=True)
            return retained
        raise AQLStepTimeout(f"{label} exceeded {timeout_seconds}s")

    status, payload = result_queue.get_nowait()
    if status == "error":
        if isinstance(payload, TimeoutError) and on_timeout is not None:
            retained = on_timeout()
            if retained is not None:
                print(f"[warn] {label} timed out; using retained research checkpoint", flush=True)
                return retained
        raise payload
    return payload


def build_bottom_up_attention_artifacts(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    news_frame: pd.DataFrame | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    topology_universe_frame: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    generated_at_utc: datetime | str | None = None,
    filings_frame: pd.DataFrame | None = None,
    fred_summary_frame: pd.DataFrame | None = None,
    yield_curve_facts_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    run_id: str | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    top_events_limit: int = 5,
    must_read_limit: int = 10,
    unresolved_limit: int = 5,
    research_limit: int = 40,
    load_search_clients: bool = False,
    search_clients: list[Any] | tuple[Any, ...] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> AgenticAttentionArtifacts:
    import uuid

    from ..attention_home_1d import build_attention_entity_master, build_attention_event_candidates_1d
    from ..attention_materialized import serialize_attention_home_payload, serialize_attention_research_bundles
    from ..attention_graph_network import build_homepage_attention_graph_payload
    from .assembler import _build_home_payload

    asof_time_utc = pd.to_datetime(generated_at_utc or datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(asof_time_utc):
        asof_time_utc = pd.Timestamp.now(tz="UTC")
    run_id = _coerce_text(run_id) or f"attention-run-{uuid.uuid4().hex[:12]}"
    macro_profile = _load_attention_macro_signal_profile()
    release_rules = macro_profile.get("release_rules") if isinstance(macro_profile.get("release_rules"), dict) else {}
    macro_release_force_limit = max(int(_coerce_float(release_rules.get("force_limit"), 2)), 0)
    entity_rows = entity_master if isinstance(entity_master, pd.DataFrame) else build_attention_entity_master(daily_movers.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist())
    source_news = news_frame
    if not isinstance(source_news, pd.DataFrame):
        article_frames = [item.get("articles") for item in (news_payloads or {}).values() if isinstance(item, dict) and isinstance(item.get("articles"), pd.DataFrame)]
        source_news = pd.concat(article_frames, ignore_index=True, sort=False) if article_frames else pd.DataFrame()
    source_events = research_source_events(
        source_news, market_frame=daily_movers, universe_frame=topology_universe_frame,
        asof_time_utc=asof_time_utc, run_id=run_id, llm_client=llm_client,
        progress_callback=(lambda event: progress_callback(0, 1, event)) if progress_callback else None,
    )
    base_candidates = build_attention_event_candidates_1d(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_rows,
        holdings=holdings,
        asof_time_utc=asof_time_utc.isoformat(),
    )
    candidates = _augment_candidate_frame(base_candidates, asof_time_utc=asof_time_utc, run_id=run_id)
    identity_records = {
        _normalize_symbol(row.get("symbol")): to_jsonable(row)
        for row in topology_universe_frame.to_dict("records")
    } if isinstance(topology_universe_frame, pd.DataFrame) else {}
    price_records = {
        _normalize_symbol(row.get("symbol")): to_jsonable(row)
        for row in daily_movers.to_dict("records")
    } if isinstance(daily_movers, pd.DataFrame) else {}
    candidates["instrument_context_json"] = candidates.get("symbol", pd.Series(dtype=str)).map(lambda symbol: json.dumps({
        "universe_identity": identity_records.get(_normalize_symbol(symbol)),
        "market_observation": price_records.get(_normalize_symbol(symbol)),
    }, default=str))
    if candidates.empty and not source_events["bundles"]:
        empty_payload = {
            "top_events": [],
            "must_read_movers": [],
            "unresolved_large_moves": [],
            "generated_at_utc": asof_time_utc.isoformat(),
            "coverage_summary": {"candidate_count": 0, "event_count": 0, "must_read_count": 0, "unresolved_count": 0, "run_id": run_id,
                                 "source_event_research_status": source_events["status"], "source_event_input_count": source_events.get("input_count", len(source_events["documents"])),
                                 "source_event_nominated_count": len(source_events["plans"]), "source_event_completed_count": 0},
            "taxonomy_horizon_trends": [],
            "event_candidates_1d": [],
            "event_impacts_1d": [],
            "entity_master": entity_rows.to_dict(orient="records") if isinstance(entity_rows, pd.DataFrame) else [],
            "homepage_graph": {},
            "run_id": run_id,
        }
        frames = {
            "attention_candidates_1d": pd.DataFrame(),
            "attention_research_plans": pd.DataFrame(),
            "attention_search_requests": pd.DataFrame(),
            "attention_search_results": pd.DataFrame(),
            "attention_source_documents": pd.DataFrame(source_events["documents"]),
            "attention_source_event_plans": pd.DataFrame(source_events["plans"]),
            "attention_source_event_research": pd.DataFrame(source_events["results"]),
            "attention_evidence_chunks": pd.DataFrame(),
            "attention_claims": pd.DataFrame(),
            "attention_candidate_graph": pd.DataFrame(),
            "attention_event_clusters_1d": pd.DataFrame(),
            "macro_release_events_1d": _empty_macro_release_events_frame(),
            "attention_macro_context_1d": _empty_attention_macro_context_frame(),
            "macro_causal_graph_edges_v1": _empty_macro_causal_graph_edges_frame(),
            "macro_relationship_checks_1d": _empty_macro_relationship_checks_frame(),
            "attention_hypotheses_1d": _empty_attention_hypotheses_frame(),
            "attention_home_snapshots_1d": serialize_attention_home_payload(empty_payload),
            "attention_bundle_snapshots": serialize_attention_research_bundles({}, generated_at_utc=asof_time_utc),
        }
        return AgenticAttentionArtifacts(home_payload=empty_payload, bundle_map={}, frames=frames)

    model_name = getattr(getattr(llm_client, "config", object()), "model", DEFAULT_WRITER_MODEL) if llm_client is not None else "llm_unavailable"
    research_limit_safe = max(int(research_limit), 0)
    if research_limit_safe > 0 and search_clients is not None:
        client_items = list(search_clients or [])
        serp_client = client_items[0] if len(client_items) > 0 else None
        tavily_client = client_items[1] if len(client_items) > 1 else None
    elif research_limit_safe > 0 and load_search_clients:
        serp_client, tavily_client = _aql_config._load_search_clients()
    else:
        serp_client, tavily_client = None, None
    if research_limit_safe > 0:
        research_candidates = candidates.head(research_limit_safe).to_dict(orient="records")
    else:
        research_candidates = []
    plan_rows: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    chunk_frames: list[pd.DataFrame] = []
    claim_frames: list[pd.DataFrame] = []
    bundle_map: dict[str, dict[str, Any]] = {}
    claim_map: dict[str, list[dict[str, Any]]] = {}

    def _skipped_candidate_payload(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
        symbol = _normalize_symbol(candidate.get("symbol"))
        plan_row = {
            "run_id": run_id,
            "asof_time_utc": asof_time_utc,
            "candidate_id": _coerce_text(candidate.get("candidate_id")),
            "symbol": symbol,
            "prompt_version": prompt_version,
            "model_name": "llm_unavailable",
            "research_subjects_json": _json_dumps([]),
            "hypotheses_json": _json_dumps([]),
            "queries_json": _json_dumps([]),
            "official_routes_json": _json_dumps([]),
            "priority_entities_json": _json_dumps([]),
            "evidence_budget": 0,
            "skip_reason": reason,
        }
        bundle = _build_candidate_bundle(
            candidate,
            [],
            [],
            [],
            llm_client=None,
            prompt_version=prompt_version,
            model_name="llm_unavailable",
            run_id=run_id,
            yield_facts=_latest_yield_facts(yield_curve_facts_frame) if _yield_context_relevant(candidate) else {},
        )
        return {
            "symbol": symbol,
            "plan_row": plan_row,
            "request_rows": [],
            "result_rows": [],
            "document_rows": [],
            "chunks": pd.DataFrame(),
            "claims": [],
            "claims_frame": pd.DataFrame(),
            "bundle": bundle,
        }

    def _materialize_candidate_result(
        candidate: dict[str, Any], result: dict[str, Any],
        supplied_documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        peer_symbols = _peer_candidates(candidate, candidates, limit=5)
        agent = result.get("agent_result") or {}
        if result.get("status") not in {"completed", "partial"}:
            raise RuntimeError(
                result.get("error")
                or agent.get("error")
                or "Shared candidate research did not complete"
            )
        payload = result.get("payload") or {"claims": []}
        artifacts = materialize_research_artifacts(
            agent,
            candidate_id=_coerce_text(candidate.get("candidate_id")),
            subject=_normalize_symbol(candidate.get("symbol")),
            run_id=run_id,
            asof_time_utc=asof_time_utc,
        )
        claims = []
        for index, item in enumerate(payload["claims"]):
            refs = [
                chunk
                for ref in item.get("evidence_chunk_ids") or []
                for chunk in artifacts["source_aliases"].get(ref, [ref])
            ]
            claims.append(
                {
                    **item,
                    "claim_id": f"claim::{agent['run_id']}::{index}",
                    "run_id": run_id,
                    "bundle_subject": _normalize_symbol(candidate.get("symbol")),
                    "evidence_chunk_ids": refs,
                    "source_authority_bucket": "research",
                    "source": "",
                }
            )
        documents = supplied_documents + artifacts["documents"]
        peer_moves = [
            {
                "symbol": peer,
                "change_pct": price_records.get(peer, {}).get("change_pct"),
            }
            for peer in peer_symbols
        ]
        bundle = _build_candidate_bundle(
            candidate,
            claims,
            peer_moves,
            documents,
            llm_client=None,
            prompt_version=prompt_version,
            model_name=agent.get("model") or model_name,
            run_id=run_id,
            written=result.get("payload"),
            yield_facts=(
                _latest_yield_facts(yield_curve_facts_frame)
                if _yield_context_relevant(candidate)
                else {}
            ),
        )
        state = agent.get("research_state") or {}
        return {
            "symbol": _normalize_symbol(candidate.get("symbol")),
            "plan_row": {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "candidate_id": _coerce_text(candidate.get("candidate_id")),
                "symbol": _normalize_symbol(candidate.get("symbol")),
                "prompt_version": prompt_version,
                "model_name": agent.get("model"),
                "research_subjects_json": _json_dumps([state.get("goal")]),
                "hypotheses_json": _json_dumps(state.get("alternatives") or []),
                "queries_json": _json_dumps(
                    [row["query_text"] for row in artifacts["request_rows"]]
                ),
                "official_routes_json": "[]",
                "priority_entities_json": "[]",
                "evidence_budget": agent.get("engine", {}).get("max_tool_calls"),
                "engine_revision": agent.get("engine", {}).get("revision"),
                "research_status": result.get("status"),
            },
            "request_rows": artifacts["request_rows"],
            "result_rows": artifacts["result_rows"],
            "document_rows": documents,
            "chunks": artifacts["chunks"],
            "claims": claims,
            "claims_frame": _serialize_claims_frame(
                claims, asof_time_utc=asof_time_utc
            ),
            "bundle": bundle,
        }

    research_total = len(research_candidates)
    candidate_timeout_seconds = _candidate_research_timeout_seconds()
    candidate_max_workers = min(research_total, _candidate_research_max_workers()) if research_total else 0
    if research_total:
        print(
            "[info] AQL candidate research starting "
            f"candidates={research_total} workers={candidate_max_workers} timeout={candidate_timeout_seconds}s"
        )

    def _run_candidate_research(index: int, candidate: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
        symbol = _normalize_symbol(candidate.get("symbol"))
        try:
            print(
                "[info] AQL candidate research started "
                f"index={index}/{research_total} symbol={symbol or '?'}",
                flush=True,
            )
            supplied_documents = _candidate_context_documents(
                candidate, news_payloads=news_payloads, context_payloads=context_payloads,
                filings_frame=filings_frame, fred_summary_frame=fred_summary_frame,
                yield_curve_facts_frame=yield_curve_facts_frame,
                run_id=run_id, asof_time_utc=asof_time_utc,
            )
            latest_result: list[dict[str, Any] | None] = [None]

            def retain_result(result: dict[str, Any]) -> None:
                if latest_result[0] is None or result["status"] == "completed" or latest_result[0]["status"] != "completed":
                    latest_result[0] = result

            result = _call_with_timeout(
                f"AQL candidate research {symbol or index}",
                candidate_timeout_seconds,
                lambda: research_attention_candidate(
                    candidate, peer_symbols=_peer_candidates(candidate, candidates, limit=5),
                    documents=supplied_documents, llm_client=llm_client,
                    timeout_seconds=candidate_timeout_seconds,
                    progress_callback=(lambda event: progress_callback(0, 1, event)) if progress_callback else None,
                    result_callback=retain_result,
                ),
                on_timeout=lambda: latest_result[0],
            )
            candidate_payload = _materialize_candidate_result(candidate, result, supplied_documents)
            claims_count = len(candidate_payload.get("claims") or []) if isinstance(candidate_payload, dict) else 0
            document_rows = candidate_payload.get("document_rows") if isinstance(candidate_payload, dict) else []
            document_count = len(document_rows) if hasattr(document_rows, "__len__") else 0
            print(
                "[info] AQL candidate research completed "
                f"index={index}/{research_total} symbol={symbol or '?'} claims={claims_count} documents={document_count}",
                flush=True,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            print(
                "[warn] AQL candidate research skipped "
                f"index={index}/{research_total} symbol={symbol or '?'} reason={reason}",
                flush=True,
            )
            candidate_payload = _skipped_candidate_payload(candidate, reason)
        return index, candidate, candidate_payload

    candidate_payloads_by_index: dict[int, dict[str, Any]] = {}
    if candidate_max_workers <= 1:
        for index, candidate in enumerate(research_candidates, start=1):
            result_index, _, candidate_payload = _run_candidate_research(index, candidate)
            candidate_payloads_by_index[result_index] = candidate_payload
            if progress_callback is not None:
                try:
                    progress_callback(index, research_total, candidate)
                except Exception:
                    pass
    else:
        with ThreadPoolExecutor(max_workers=candidate_max_workers, thread_name_prefix="aql-candidate") as pool:
            futures = {
                pool.submit(copy_context().run, _run_candidate_research, index, candidate): (index, candidate)
                for index, candidate in enumerate(research_candidates, start=1)
            }
            completed = 0
            for future in as_completed(futures):
                planned_index, planned_candidate = futures[future]
                try:
                    result_index, result_candidate, candidate_payload = future.result()
                except Exception as exc:
                    result_index = planned_index
                    result_candidate = planned_candidate
                    symbol = _normalize_symbol(planned_candidate.get("symbol"))
                    reason = f"{type(exc).__name__}: {exc}"
                    print(f"[warn] AQL candidate research skipped symbol={symbol or '?'} reason={reason}")
                    candidate_payload = _skipped_candidate_payload(planned_candidate, reason)
                completed += 1
                if progress_callback is not None:
                    try:
                        progress_callback(completed, research_total, result_candidate)
                    except Exception:
                        pass
                candidate_payloads_by_index[result_index] = candidate_payload

    for index in range(1, research_total + 1):
        candidate_payload = candidate_payloads_by_index.get(index)
        if not candidate_payload:
            candidate = research_candidates[index - 1]
            candidate_payload = _skipped_candidate_payload(candidate, "missing candidate research result")
        plan_rows.append(candidate_payload["plan_row"])
        request_rows.extend(candidate_payload["request_rows"])
        result_rows.extend(candidate_payload["result_rows"])
        document_rows.extend(candidate_payload["document_rows"])
        chunks = candidate_payload["chunks"]
        if isinstance(chunks, pd.DataFrame) and not chunks.empty:
            chunk_frames.append(chunks)
        claims_frame = candidate_payload["claims_frame"]
        if isinstance(claims_frame, pd.DataFrame) and not claims_frame.empty:
            claim_frames.append(claims_frame)
        claim_map[_normalize_symbol(candidate_payload["symbol"])] = list(candidate_payload["claims"] or [])
        bundle = candidate_payload["bundle"]
        bundle_map[bundle["bundle_id"]] = bundle

    print("[info] AQL candidate research complete; building event bundles")
    event_bundle_timeout_seconds = _event_bundle_timeout_seconds()
    for _, candidate_row in candidates.iterrows():
        symbol = _normalize_symbol(candidate_row.get("symbol"))
        bundle_id = f"symbol::{symbol}"
        if bundle_id in bundle_map:
            continue
        candidate = candidate_row.to_dict()
        bundle_map[bundle_id] = _build_candidate_bundle(
            candidate,
            [],
            [],
            [],
            llm_client=None,
            prompt_version=prompt_version,
            model_name="llm_unavailable",
            run_id=run_id,
            yield_facts=_latest_yield_facts(yield_curve_facts_frame) if _yield_context_relevant(candidate) else {},
        )
        claim_map[symbol] = []

    history_corr_map = _history_correlation_map(
        bars_by_symbol,
        candidates.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist(),
        min_observations=attention_graph_policy().history_corr_min_observations,
    )
    graph = _graph_edges(
        candidates,
        claim_map,
        history_correlation_map=history_corr_map,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    clusters = _cluster_candidates(candidates, graph)
    event_bundles: list[dict[str, Any]] = []
    event_cluster_rows: list[dict[str, Any]] = []
    for index, symbols in enumerate(clusters, start=1):
        cluster_rows = candidates[candidates["symbol"].astype(str).str.upper().isin(set(symbols))].copy()
        cluster_claims: list[dict[str, Any]] = []
        for symbol in symbols:
            cluster_claims.extend(claim_map.get(symbol, []))
        cluster_claims = sorted(
            cluster_claims,
            key=lambda item: (
                -float(item.get("confidence_score") or 0.0),
                -float(item.get("causal_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
            ),
        )
        cluster_id = f"cluster-{index:02d}-{hashlib.sha1('|'.join(symbols).encode('utf-8')).hexdigest()[:10]}"
        try:
            bundle = _call_with_timeout(
                f"AQL event bundle {cluster_id}",
                event_bundle_timeout_seconds,
                lambda cluster_id=cluster_id, cluster_rows=cluster_rows, cluster_claims=cluster_claims: _build_event_bundle(
                    cluster_id,
                    cluster_rows,
                    cluster_claims,
                    llm_client=llm_client,
                    prompt_version=prompt_version,
                    model_name=model_name,
                    run_id=run_id,
                    yield_facts=_latest_yield_facts(yield_curve_facts_frame),
                ),
            )
        except Exception as exc:
            print(f"[warn] AQL event bundle skipped cluster={cluster_id} reason={type(exc).__name__}: {exc}")
            bundle = _build_event_bundle(
                cluster_id,
                cluster_rows,
                cluster_claims,
                llm_client=None,
                prompt_version=prompt_version,
                model_name="llm_unavailable",
                run_id=run_id,
                yield_facts=_latest_yield_facts(yield_curve_facts_frame),
            )
        bundle_map[bundle["bundle_id"]] = bundle
        event_bundles.append(bundle)
        event_cluster_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "event_id": cluster_id,
                "member_candidate_ids_json": _json_dumps(cluster_rows["candidate_id"].tolist()),
                "anchor_candidate_ids_json": _json_dumps(cluster_rows.sort_values("candidate_score", ascending=False).head(2)["candidate_id"].tolist()),
                "driver_symbols_json": _json_dumps(bundle.get("driver_symbols") or []),
                "beneficiary_symbols_json": _json_dumps(bundle.get("beneficiary_symbols") or []),
                "loser_symbols_json": _json_dumps(bundle.get("loser_symbols") or []),
                "event_facts_json": _json_dumps(
                    {
                        "members": [
                            {
                                "symbol": _normalize_symbol(row.get("symbol")),
                                "change_pct": _coerce_float(row.get("change_pct")),
                                "sector": _coerce_text(row.get("sector")),
                                "industry": _coerce_text(row.get("industry")),
                            }
                            for _, row in cluster_rows.iterrows()
                        ],
                        "yield_facts": bundle.get("yield_facts") or {},
                    }
                ),
                "supporting_claim_ids_json": _json_dumps(bundle.get("supporting_claim_ids") or []),
                "event_score": _coerce_float(bundle.get("event_score"), 0.0),
                "cause_status": _coerce_text(bundle.get("cause_status")),
                "event_type": _coerce_text(bundle.get("event_type")),
            }
        )

    observed_cohort_bundles = build_observed_cohort_event_bundles(
        candidates,
        run_id=run_id,
        prompt_version=prompt_version,
        model_name=model_name,
    )
    if observed_cohort_bundles:
        print(f"[info] AQL observed cohort events promoted: {len(observed_cohort_bundles)}")
    for bundle in observed_cohort_bundles:
        bundle_id = _coerce_text(bundle.get("bundle_id"))
        if bundle_id:
            bundle_map[bundle_id] = bundle
        event_bundles.append(bundle)
        supporting_symbols = [
            _normalize_symbol(symbol)
            for symbol in list(bundle.get("supporting_symbols") or [])
            if _normalize_symbol(symbol)
        ]
        cluster_rows = candidates[candidates["symbol"].astype(str).str.upper().isin(set(supporting_symbols))].copy()
        event_cluster_rows.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof_time_utc,
                "event_id": _coerce_text(bundle.get("event_id")),
                "member_candidate_ids_json": _json_dumps(cluster_rows["candidate_id"].tolist() if "candidate_id" in cluster_rows.columns else []),
                "anchor_candidate_ids_json": _json_dumps(cluster_rows.sort_values("candidate_score", ascending=False).head(2)["candidate_id"].tolist() if "candidate_id" in cluster_rows.columns else []),
                "driver_symbols_json": _json_dumps(bundle.get("driver_symbols") or []),
                "beneficiary_symbols_json": _json_dumps(bundle.get("beneficiary_symbols") or []),
                "loser_symbols_json": _json_dumps(bundle.get("loser_symbols") or []),
                "event_facts_json": _json_dumps(
                    {
                        "members": [
                            {
                                "symbol": _normalize_symbol(row.get("symbol")),
                                "change_pct": _coerce_float(row.get("change_pct")),
                                "sector": _coerce_text(row.get("sector")),
                                "industry": _coerce_text(row.get("industry")),
                            }
                            for _, row in cluster_rows.iterrows()
                        ],
                        "cohort_label": _coerce_text(bundle.get("cohort_label")),
                        "cohort_kind": _coerce_text(bundle.get("cohort_kind")),
                        "observed_cohort": True,
                    }
                ),
                "supporting_claim_ids_json": _json_dumps([]),
                "event_score": _coerce_float(bundle.get("event_score"), 0.0),
                "cause_status": _coerce_text(bundle.get("cause_status")),
                "event_type": _coerce_text(bundle.get("event_type")),
            }
        )

    for bundle in source_events["bundles"]:
        bundle_map[bundle["bundle_id"]] = bundle
        event_bundles.append(bundle)
        member_rows = candidates[candidates["symbol"].isin(bundle.get("supporting_symbols") or [])]
        event_cluster_rows.append({
            "run_id": run_id, "asof_time_utc": asof_time_utc, "event_id": bundle["event_id"],
            "member_candidate_ids_json": _json_dumps(member_rows["candidate_id"].tolist()),
            "anchor_candidate_ids_json": _json_dumps([]),
            "driver_symbols_json": _json_dumps(bundle.get("supporting_symbols") or []),
            "beneficiary_symbols_json": _json_dumps([]), "loser_symbols_json": _json_dumps([]),
            "event_facts_json": _json_dumps({"event_title": bundle["event_title"], "source_event": True}),
            "supporting_claim_ids_json": _json_dumps(bundle.get("supporting_claim_ids") or []),
            "event_score": bundle.get("event_score", 0), "cause_status": bundle["cause_status"], "event_type": "source_event",
        })
    source_documents = annotate_source_documents(source_events["documents"], asof_time_utc=asof_time_utc)
    document_rows.extend(source_documents)
    source_chunks = _chunk_source_documents(source_documents, run_id=run_id, asof_time_utc=asof_time_utc, embedding_client=embedding_client)
    if not source_chunks.empty:
        chunk_frames.append(source_chunks)
    source_claims = [claim for bundle in source_events["bundles"] for claim in bundle.get("claims", [])]
    if not source_chunks.empty:
        chunks_by_document = source_chunks.groupby("document_id")["chunk_id"].agg(list).to_dict()
        for claim in source_claims:
            claim["evidence_chunk_ids"] = chunks_by_document.get(claim.get("document_id"), [])
    if source_claims:
        claim_frames.append(pd.DataFrame(source_claims))

    macro_release_events_frame, macro_release_bundles = _build_macro_release_events(
        fred_summary_frame=fred_summary_frame,
        candidates=candidates,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    macro_causal_graph_edges_frame = _materialize_macro_causal_graph_edges(
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    macro_relationship_checks_frame = _build_macro_relationship_checks(
        macro_release_bundles=macro_release_bundles,
        macro_causal_graph_edges_frame=macro_causal_graph_edges_frame,
        candidates=candidates,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
    )
    attention_hypotheses_frame, hypothesis_summary_by_release = _build_attention_hypotheses_from_relationship_checks(
        macro_release_bundles=macro_release_bundles,
        relationship_checks_frame=macro_relationship_checks_frame,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
        profile=macro_profile,
    )
    print("[info] AQL macro verification starting")
    try:
        (
            attention_hypotheses_frame,
            verification_summary_by_release,
            macro_verification_request_rows,
            macro_verification_result_rows,
            macro_claim_rows,
        ) = _call_with_timeout(
            "AQL macro verification",
            _macro_verification_timeout_seconds(),
            lambda: _verify_macro_hypotheses_with_web_evidence(
                hypotheses_frame=attention_hypotheses_frame,
                macro_release_bundles=macro_release_bundles,
                asof_time_utc=asof_time_utc,
                run_id=run_id,
                profile=macro_profile,
                llm_client=llm_client,
                serp_client=serp_client,
                tavily_client=tavily_client,
            ),
        )
        print("[info] AQL macro verification completed")
    except Exception as exc:
        print(f"[warn] AQL macro verification skipped reason={type(exc).__name__}: {exc}")
        verification_summary_by_release = {}
        macro_verification_request_rows = []
        macro_verification_result_rows = []
        macro_claim_rows = []
    hypothesis_summary_by_release = {
        **hypothesis_summary_by_release,
        **{
            release_id: {
                **dict(hypothesis_summary_by_release.get(release_id) or {}),
                **dict(summary or {}),
            }
            for release_id, summary in verification_summary_by_release.items()
        },
    }
    macro_release_bundles = _apply_macro_diagnostics_to_release_bundles(
        macro_release_bundles=macro_release_bundles,
        hypothesis_summary_by_release=hypothesis_summary_by_release,
    )
    for bundle in macro_release_bundles:
        bundle_id = _coerce_text(bundle.get("bundle_id"))
        if bundle_id:
            bundle_map[bundle_id] = bundle
    if macro_verification_request_rows:
        request_rows.extend(macro_verification_request_rows)
    if macro_verification_result_rows:
        result_rows.extend(macro_verification_result_rows)
    if macro_claim_rows:
        macro_claims_frame = _serialize_claims_frame(macro_claim_rows, asof_time_utc=asof_time_utc)
        if not macro_claims_frame.empty:
            claim_frames.append(macro_claims_frame)
    attention_macro_context_frame = _build_attention_macro_context_frame(
        candidates=candidates,
        attention_rows=attention_rows,
        macro_release_bundles=macro_release_bundles,
        asof_time_utc=asof_time_utc,
        run_id=run_id,
    )

    for bundle in bundle_map.values():
        symbols = list(bundle.get("supporting_symbols") or [])
        if bundle.get("symbol"):
            symbols.append(bundle["symbol"])
        bundle["instrument_context"] = [{
            "symbol": symbol, "universe_identity": identity_records.get(_normalize_symbol(symbol)),
            "market_observation": price_records.get(_normalize_symbol(symbol)),
        } for symbol in dict.fromkeys(symbols)]

    home_payload = _build_home_payload(
        candidates,
        bundle_map,
        event_bundles,
        attention_rows=attention_rows,
        generated_at_utc=asof_time_utc,
        run_id=run_id,
        entity_master=entity_rows,
        top_events_limit=top_events_limit,
        must_read_limit=must_read_limit,
        unresolved_limit=unresolved_limit,
        macro_release_bundles=macro_release_bundles,
        macro_release_force_limit=macro_release_force_limit,
        relationship_checks_frame=macro_relationship_checks_frame,
        hypotheses_frame=attention_hypotheses_frame,
    )
    topology_universe = (
        topology_universe_frame.copy()
        if isinstance(topology_universe_frame, pd.DataFrame) and not topology_universe_frame.empty
        else entity_rows.copy()
    )
    cluster_frame = pd.DataFrame(event_cluster_rows)
    home_payload["homepage_graph"] = build_homepage_attention_graph_payload(
        candidates,
        graph,
        cluster_frame,
        universe_frame=topology_universe,
    )
    home_payload["coverage_summary"].update({
        "source_event_research_status": source_events["status"],
        "source_event_nominated_count": len(source_events["plans"]),
        "source_event_completed_count": len(source_events["bundles"]),
        "source_event_input_count": source_events.get("input_count", len(source_events["documents"])),
    })
    frames = {
        "attention_source_event_plans": pd.DataFrame(source_events["plans"]),
        "attention_source_event_research": pd.DataFrame(source_events["results"]),
        "attention_candidates_1d": candidates.reset_index(drop=True),
        "attention_research_plans": pd.DataFrame(plan_rows),
        "attention_search_requests": pd.DataFrame(request_rows),
        "attention_search_results": pd.DataFrame(result_rows),
        "attention_source_documents": pd.DataFrame(document_rows),
        "attention_evidence_chunks": pd.concat(chunk_frames, ignore_index=True, sort=False) if chunk_frames else pd.DataFrame(),
        "attention_claims": pd.concat(claim_frames, ignore_index=True, sort=False) if claim_frames else pd.DataFrame(),
        "attention_candidate_graph": graph.reset_index(drop=True) if isinstance(graph, pd.DataFrame) else pd.DataFrame(),
        "attention_event_clusters_1d": cluster_frame,
        "macro_release_events_1d": macro_release_events_frame,
        "attention_macro_context_1d": attention_macro_context_frame,
        "macro_causal_graph_edges_v1": macro_causal_graph_edges_frame,
        "macro_relationship_checks_1d": macro_relationship_checks_frame,
        "attention_hypotheses_1d": attention_hypotheses_frame,
        "attention_home_snapshots_1d": serialize_attention_home_payload(home_payload),
        "attention_bundle_snapshots": serialize_attention_research_bundles(bundle_map, generated_at_utc=asof_time_utc),
    }
    return AgenticAttentionArtifacts(home_payload=home_payload, bundle_map=bundle_map, frames=frames)


def build_bottom_up_attention_home(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    generated_at_utc: datetime | str | None = None,
    filings_frame: pd.DataFrame | None = None,
    fred_summary_frame: pd.DataFrame | None = None,
    yield_curve_facts_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    run_id: str | None = None,
    top_events_limit: int = 5,
    must_read_limit: int = 10,
    unresolved_limit: int = 5,
    research_limit: int = 40,
    load_search_clients: bool = False,
    search_clients: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    return build_bottom_up_attention_artifacts(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_master,
        holdings=holdings,
        generated_at_utc=generated_at_utc,
        filings_frame=filings_frame,
        fred_summary_frame=fred_summary_frame,
        yield_curve_facts_frame=yield_curve_facts_frame,
        llm_client=llm_client,
        embedding_client=embedding_client,
        run_id=run_id,
        top_events_limit=top_events_limit,
        must_read_limit=must_read_limit,
        unresolved_limit=unresolved_limit,
        research_limit=research_limit,
        load_search_clients=load_search_clients,
        search_clients=search_clients,
    ).home_payload


def build_bottom_up_attention_bundle(
    bundle_id: str,
    home_payload: dict[str, Any],
    *,
    bundle_snapshots_frame: pd.DataFrame | None = None,
    bundle_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_bundle_id = _coerce_text(bundle_id)
    if not normalized_bundle_id:
        return {}
    if isinstance(bundle_snapshots_frame, pd.DataFrame) and not bundle_snapshots_frame.empty and "bundle_id" in bundle_snapshots_frame.columns:
        scoped = bundle_snapshots_frame[bundle_snapshots_frame["bundle_id"].astype(str) == normalized_bundle_id].head(1)
        if not scoped.empty:
            payload_text = _coerce_text(scoped.iloc[0].get("payload_json"))
            if payload_text:
                try:
                    payload = json.loads(payload_text)
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    pass
    if isinstance(bundle_map, dict) and normalized_bundle_id in bundle_map:
        return dict(bundle_map.get(normalized_bundle_id) or {})

    def _normalized_stub(item: dict[str, Any], *, section: str) -> dict[str, Any]:
        payload = dict(item or {})
        if section == "top_events":
            payload.setdefault("bundle_type", "event")
            payload.setdefault("event_title", _coerce_text(payload.get("event_title")) or _coerce_text(payload.get("headline")))
            payload.setdefault("what_happened_text", _coerce_text(payload.get("what_happened_text")) or _coerce_text(payload.get("surface_what_changed_text")))
            payload.setdefault("why_happened_text", _coerce_text(payload.get("why_happened_text")) or _coerce_text(payload.get("surface_why_text")))
            payload.setdefault("affected_assets_summary_text", _coerce_text(payload.get("affected_assets_summary_text")) or _coerce_text(payload.get("surface_what_else_moved_text")))
        else:
            payload.setdefault("bundle_type", "symbol")
            payload.setdefault("headline", _coerce_text(payload.get("headline")) or _coerce_text(payload.get("event_title")))
            payload.setdefault("what_changed_text", _coerce_text(payload.get("what_changed_text")) or _coerce_text(payload.get("surface_what_changed_text")))
            payload.setdefault("why_now_text", _coerce_text(payload.get("why_now_text")) or _coerce_text(payload.get("surface_why_text")))
            payload.setdefault("what_else_moved_text", _coerce_text(payload.get("what_else_moved_text")) or _coerce_text(payload.get("surface_what_else_moved_text")))
        payload.setdefault("surface_summary_text", _coerce_text(payload.get("surface_summary_text")))
        payload.setdefault("cause_status", _coerce_text(payload.get("cause_status")) or _coerce_text(payload.get("surface_cause_status")) or "unresolved")
        payload.setdefault("confidence_label", _coerce_text(payload.get("confidence_label")) or _coerce_text(payload.get("surface_confidence_label")) or "Developing")
        payload.setdefault("evidence_quality", _coerce_text(payload.get("evidence_quality")) or _coerce_text(payload.get("surface_evidence_quality")))
        payload.setdefault("freshness_quality", _coerce_text(payload.get("freshness_quality")) or _coerce_text(payload.get("surface_freshness_quality")))
        payload.setdefault("source_summary", _coerce_text(payload.get("source_summary")) or _coerce_text(payload.get("surface_source_summary")))
        payload.setdefault("evidence", [])
        payload.setdefault("background_context", [])
        payload.setdefault("claims", [])
        return payload

    for section in ["top_events", "must_read_movers", "unresolved_large_moves"]:
        for item in list(home_payload.get(section) or []):
            if _coerce_text((item or {}).get("bundle_id")) == normalized_bundle_id:
                return _normalized_stub(dict(item or {}), section=section)
    return {}


__all__ = [
    "build_bottom_up_attention_artifacts",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
]
