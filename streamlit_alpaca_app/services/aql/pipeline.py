"""
AQL pipeline — top-level orchestration for bottom-up attention artifact generation.
"""
from __future__ import annotations

from services.work_scope import WorkScope

import json
import os
import queue
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from ..attention_macro_signals import _build_macro_relationship_checks
from ..json_utils import to_jsonable
from ..llm import EmbeddingClient, llm_request_deadline, register_config_param, get_config_param
from .constants import AgenticAttentionArtifacts, DEFAULT_PROMPT_VERSION, LLMClient
from ._shared import _augment_candidate_frame
from services.common.market_activity import _coerce_text, _json_dumps, _normalize_symbol
from .config import _load_attention_macro_signal_profile
from .extractor import _chunk_source_documents
from .evidence_index import annotate_source_documents
from .events import research_source_events
from .macro import (
    _build_attention_hypotheses_from_relationship_checks,
    _build_attention_macro_context_frame,
    _build_macro_release_events,
    _materialize_macro_causal_graph_edges,
)

_MARKET_PARAMETERS = {name: register_config_param(label, group="AQL / Zopedia Engine", default=default,
    description=description) for name, label, default, description in (
    ("window", "Market relationship window sessions", 40, "Closed trading sessions per return-correlation window."),
    ("comparison_shift", "Market relationship comparison shift sessions", 20, "Sessions separating comparable windows; overlap reduces abrupt boundary effects."),
    ("min_observations", "Market relationship minimum observations", 30, "Minimum shared return observations per pair in both windows."),
    ("correlation_floor", "Market relationship group correlation floor", .6, "Minimum pair correlation within complete-linkage groups; numerical lead, not significance test."))}


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
    economic_context: dict[str, Any] | None = None,
    previous_publication: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> AgenticAttentionArtifacts:
    import uuid
    from compute.market_attention import build_market_attention_context
    from ..attention_home_1d import build_attention_entity_master, build_attention_event_candidates_1d
    from ..attention_materialized import serialize_attention_home_payload, serialize_attention_research_bundles
    from ..attention_graph_network import build_homepage_attention_graph_payload
    from .assembler import _build_home_payload

    asof = pd.to_datetime(generated_at_utc or datetime.now(timezone.utc), utc=True)
    run_id = _coerce_text(run_id) or f"attention-run-{uuid.uuid4().hex[:12]}"
    policy = {name: (float(get_config_param(key)) if name == "correlation_floor" else int(get_config_param(key)))
              for name, key in _MARKET_PARAMETERS.items()}
    entity_rows = entity_master if isinstance(entity_master, pd.DataFrame) else build_attention_entity_master(
        daily_movers.get("symbol", pd.Series(dtype=str)).dropna().astype(str).tolist())
    # Numerical signals retain complete coverage independently of which questions
    # receive research. Ranking and macro consumers must not inherit that cut.
    candidates = _augment_candidate_frame(build_attention_event_candidates_1d(
        daily_movers, selected_symbols=daily_movers.get("symbol", pd.Series(dtype=str)).tolist(),
        attention_rows=attention_rows, bars_by_symbol=bars_by_symbol, news_payloads=news_payloads,
        context_payloads=context_payloads, entity_master=entity_rows, holdings=holdings,
        asof_time_utc=asof), asof_time_utc=asof, run_id=run_id)
    feature_columns = [key for key in ("anomaly_score", "return_z", "volume_z", "range_z", "anomaly_history_count")
                       if key in candidates and key not in daily_movers]
    observed = daily_movers.merge(candidates[["symbol", *feature_columns]], on="symbol", how="left") if "symbol" in daily_movers else daily_movers
    numerical = to_jsonable(build_market_attention_context(observed, bars_by_symbol or {},
        asof_time_utc=asof, universe=topology_universe_frame,
        benchmark_symbol=os.getenv("PHASE_SHIFT_BENCHMARK", "SPY").strip().upper(), **policy))
    source_news = news_frame
    if not isinstance(source_news, pd.DataFrame):
        articles = [item["articles"] for item in (news_payloads or {}).values()
                    if isinstance(item, dict) and isinstance(item.get("articles"), pd.DataFrame)]
        source_news = pd.concat(articles, ignore_index=True, sort=False) if articles else pd.DataFrame()
    economic = {"fred_observations": to_jsonable(fred_summary_frame.to_dict("records"))
                    if isinstance(fred_summary_frame, pd.DataFrame) else [],
                "yield_curve_observations": to_jsonable(yield_curve_facts_frame.to_dict("records"))
                    if isinstance(yield_curve_facts_frame, pd.DataFrame) else [], **(economic_context or {})}
    selected = research_source_events(source_news, market_frame=observed,
        universe_frame=topology_universe_frame, asof_time_utc=asof, run_id=run_id,
        llm_client=llm_client, market_context=numerical, macro_context=economic,
        previous_publication=previous_publication,
        progress_callback=(lambda event: progress_callback(0, 1, event)) if progress_callback else None)
    # Selection narrows research and its display adapters, never numerical data.
    symbols = list(dict.fromkeys(
        [_normalize_symbol(symbol) for row in selected["plans"]
         for symbol in json.loads(row["plan_json"])["related_symbols"]]
        + [symbol for bundle in selected["bundles"] for symbol in bundle["supporting_symbols"]]))
    extra_symbols = sorted(set(symbols) - set(candidates.get("symbol", [])))
    if extra_symbols:
        extra = _augment_candidate_frame(build_attention_event_candidates_1d(
            daily_movers, selected_symbols=extra_symbols, attention_rows=attention_rows,
            bars_by_symbol=bars_by_symbol, news_payloads=news_payloads, context_payloads=context_payloads,
            entity_master=entity_rows, holdings=holdings, asof_time_utc=asof), asof_time_utc=asof, run_id=run_id)
        candidates = pd.concat([candidates, extra], ignore_index=True)
    selected_candidates = candidates[candidates["symbol"].isin(symbols)]
    identities = { _normalize_symbol(row.get("symbol")): row for row in to_jsonable(
        topology_universe_frame.to_dict("records"))} if isinstance(topology_universe_frame, pd.DataFrame) else {}
    observations = {_normalize_symbol(row.get("symbol")): row for row in to_jsonable(daily_movers.to_dict("records"))}
    bundles = selected["bundles"]
    bundle_map = {bundle["bundle_id"]: bundle for bundle in bundles}
    for bundle in bundles:
        bundle["instrument_context"] = [{"symbol": symbol, "universe_identity": identities.get(symbol),
            "market_observation": observations.get(symbol)} for symbol in bundle["supporting_symbols"]]
    documents = annotate_source_documents(selected["documents"], asof_time_utc=asof)
    chunks = _chunk_source_documents(documents, run_id=run_id, asof_time_utc=asof, embedding_client=embedding_client)
    by_document = chunks.groupby("document_id")["chunk_id"].agg(list).to_dict() if not chunks.empty else {}
    claims = [claim for bundle in bundles for claim in bundle.get("claims", [])]
    for claim in claims:
        claim["evidence_chunk_ids"] = by_document.get(claim.get("document_id"), [])
    # Keep existing numerical macro datasets and explanatory graph contracts.
    # Optional macro investigations now belong to the same selected agenda.
    profile = _load_attention_macro_signal_profile()
    releases, release_bundles = _build_macro_release_events(fred_summary_frame=fred_summary_frame,
        candidates=candidates, asof_time_utc=asof, run_id=run_id, profile=profile)
    causal_edges = _materialize_macro_causal_graph_edges(asof_time_utc=asof, run_id=run_id, profile=profile)
    checks = _build_macro_relationship_checks(macro_release_bundles=release_bundles,
        macro_causal_graph_edges_frame=causal_edges, candidates=candidates, asof_time_utc=asof, run_id=run_id)
    hypotheses, _ = _build_attention_hypotheses_from_relationship_checks(macro_release_bundles=release_bundles,
        relationship_checks_frame=checks, asof_time_utc=asof, run_id=run_id, profile=profile)
    macro_frame = _build_attention_macro_context_frame(candidates=candidates, attention_rows=attention_rows,
        macro_release_bundles=release_bundles, asof_time_utc=asof, run_id=run_id)
    # The graph is a projection of measured pre-research groups. Taxonomy and
    # research relationships remain context, not measured correlation edges.
    graph_rows = []
    for group in numerical["groups"]:
        members = [symbol for symbol in group["members"] if symbol in symbols]
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                graph_rows.append({"left_symbol": left, "right_symbol": right,
                    "edge_weight": 1., "edge_reasons_json": _json_dumps(["observed_return_group"]),
                    "run_id": run_id, "asof_time_utc": asof})
    graph = pd.DataFrame(graph_rows)
    clusters = pd.DataFrame([{"run_id": run_id, "asof_time_utc": asof,
        "event_id": bundle["event_id"], "member_candidate_ids_json": _json_dumps([
            "candidate::" + symbol for symbol in bundle["supporting_symbols"]]),
        "anchor_candidate_ids_json": "[]", "driver_symbols_json": _json_dumps(bundle["supporting_symbols"]),
        "beneficiary_symbols_json": "[]", "loser_symbols_json": "[]",
        "event_facts_json": _json_dumps({"event_title": bundle["event_title"], "common_agenda": True}),
        "supporting_claim_ids_json": _json_dumps(bundle["supporting_claim_ids"]),
        "event_score": 0., "cause_status": bundle["cause_status"], "event_type": "market_research"}
        for bundle in bundles])
    home = _build_home_payload(selected_candidates, bundle_map, bundles, attention_rows=attention_rows,
        generated_at_utc=asof, run_id=run_id, entity_master=entity_rows, top_events_limit=top_events_limit,
        must_read_limit=must_read_limit, unresolved_limit=unresolved_limit,
        macro_release_bundles=[], macro_release_force_limit=0, relationship_checks_frame=checks,
        hypotheses_frame=hypotheses)
    home["market_context"] = numerical
    home["homepage_graph"] = build_homepage_attention_graph_payload(selected_candidates, graph, clusters,
        universe_frame=topology_universe_frame if isinstance(topology_universe_frame, pd.DataFrame) else entity_rows)
    home["coverage_summary"].update({"source_event_research_status": selected["status"],
        "source_event_nominated_count": len(selected["plans"]), "source_event_completed_count": len(bundles),
        "source_event_input_count": selected["input_count"], "research_selection": "common_market_agenda",
        "numerical_coverage": numerical["coverage"]})
    frames = {
        "attention_market_research": pd.DataFrame(selected["market_research"]),
        "attention_source_event_plans": pd.DataFrame(selected["plans"]),
        "attention_source_event_research": pd.DataFrame(selected["results"]),
        "attention_candidates_1d": candidates, "attention_research_plans": pd.DataFrame(selected["plans"]),
        "attention_search_requests": pd.DataFrame(), "attention_search_results": pd.DataFrame(),
        "attention_source_documents": pd.DataFrame(documents), "attention_evidence_chunks": chunks,
        "attention_claims": pd.DataFrame(claims), "attention_candidate_graph": graph,
        "attention_event_clusters_1d": clusters, "macro_release_events_1d": releases,
        "attention_macro_context_1d": macro_frame, "macro_causal_graph_edges_v1": causal_edges,
        "macro_relationship_checks_1d": checks, "attention_hypotheses_1d": hypotheses,
        "attention_home_snapshots_1d": serialize_attention_home_payload(home),
        "attention_bundle_snapshots": serialize_attention_research_bundles(bundle_map, generated_at_utc=asof)}
    return AgenticAttentionArtifacts(home_payload=home, bundle_map=bundle_map, frames=frames)


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
