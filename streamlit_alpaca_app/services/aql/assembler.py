"""
AQL assembler — payload assembly (candidate/event bundles, home payload).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ._shared import (
    _augment_candidate_frame,
    _coerce_float,
    _coerce_text,
    _compose_surface_summary,
    _document_importance_map,
    _freshness_score,
    _importance_label,
    _json_dumps,
    _judge_cause_status,
    _move_direction,
    _normalize_symbol,
    _quality_label,
    _safe_list,
    _top_sources,
    _yield_context_relevant,
)
from .writer import _write_symbol_bundle, _write_event_bundle, _cluster_uses_yield_context, _infer_event_type
from .clusterer import _taxonomy_horizon_trends, _cluster_candidates
from ._shared import (
    _dominant_cluster_label,
    _event_title_from_cluster,
    _tag_tokens,
)
from .extractor import _claim_entities, _serialize_claims_frame
from .collector import _latest_yield_facts
from .constants import LLMClient


def _candidate_bundle_item(bundle: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_id": _coerce_text(bundle.get("bundle_id")),
        "symbol": _normalize_symbol(candidate.get("symbol")),
        "headline": _coerce_text(bundle.get("headline")),
        "what_changed_text": _coerce_text(bundle.get("what_changed_text")),
        "why_now_text": _coerce_text(bundle.get("why_now_text")),
        "what_else_moved_text": _coerce_text(bundle.get("what_else_moved_text")),
        "cause_status": _coerce_text(bundle.get("cause_status")) or "unresolved",
        "confidence_label": _coerce_text(bundle.get("confidence_label")) or "Developing",
        "candidate_score": _coerce_float(candidate.get("candidate_score")),
        "change_pct": _coerce_float(candidate.get("change_pct")),
        "expected_move_pct": _coerce_float(candidate.get("expected_move_pct")),
        "surprise_z": _coerce_float(candidate.get("surprise_z")),
        "sector": _coerce_text(candidate.get("sector")),
        "industry": _coerce_text(candidate.get("industry")),
        "source_label": _coerce_text(candidate.get("source_label")),
        "top_source": _coerce_text(bundle.get("source_summary") or candidate.get("top_source")),
        "best_authority_rank": int(bundle.get("best_authority_rank") or candidate.get("best_authority_rank") or 9),
        "source_count": int(bundle.get("source_count") or candidate.get("source_count") or 0),
        "evidence_count": int(bundle.get("evidence_count") or candidate.get("evidence_count") or 0),
        "same_day_evidence_count": int(bundle.get("same_day_evidence_count") or candidate.get("same_day_evidence_count") or 0),
        "surface_summary_text": _coerce_text(bundle.get("surface_summary_text")),
        "surface_what_changed_text": _coerce_text(bundle.get("what_changed_text")),
        "surface_why_text": _coerce_text(bundle.get("why_now_text")),
        "surface_what_else_moved_text": _coerce_text(bundle.get("what_else_moved_text")),
        "surface_cause_status": _coerce_text(bundle.get("cause_status")),
        "surface_evidence_quality": _coerce_text(bundle.get("evidence_quality")),
        "surface_freshness_quality": _coerce_text(bundle.get("freshness_quality")),
        "surface_source_summary": _coerce_text(bundle.get("source_summary")),
        "surface_confidence_label": _coerce_text(bundle.get("confidence_label")),
    }


def _event_item(bundle: dict[str, Any], cluster_rows: pd.DataFrame, event_type: str, *, event_score: float) -> dict[str, Any]:
    has_cluster_rows = isinstance(cluster_rows, pd.DataFrame) and not cluster_rows.empty
    if has_cluster_rows:
        drivers = [
            _normalize_symbol(row.get("symbol"))
            for _, row in cluster_rows.sort_values("candidate_score", ascending=False).head(3).iterrows()
            if _normalize_symbol(row.get("symbol"))
        ]
        up = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) >= 0 and _normalize_symbol(row.get("symbol"))]
        down = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) < 0 and _normalize_symbol(row.get("symbol"))]
        anchor_row = cluster_rows.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).iloc[0]
        anchor_symbol = _normalize_symbol(anchor_row.get("symbol"))
        anchor_direction = _move_direction(anchor_row.get("change_pct"))
        supporting_symbols = list(
            dict.fromkeys(
                [
                    _normalize_symbol(row.get("symbol"))
                    for _, row in cluster_rows.iterrows()
                    if _normalize_symbol(row.get("symbol"))
                ]
            )
        )
    else:
        drivers = [_normalize_symbol(value) for value in _safe_list(bundle.get("driver_symbols")) if _normalize_symbol(value)]
        up = [_normalize_symbol(value) for value in _safe_list(bundle.get("beneficiary_symbols")) if _normalize_symbol(value)]
        down = [_normalize_symbol(value) for value in _safe_list(bundle.get("loser_symbols")) if _normalize_symbol(value)]
        supporting_symbols = [_normalize_symbol(value) for value in _safe_list(bundle.get("supporting_symbols")) if _normalize_symbol(value)]
        anchor_symbol = _normalize_symbol(bundle.get("anchor_symbol")) or (supporting_symbols[0] if supporting_symbols else "")
        anchor_direction = _coerce_text(bundle.get("anchor_direction")).lower()
        if anchor_direction not in {"up", "down"}:
            anchor_direction = "up" if up else ("down" if down else "")
    surprise_score = _coerce_float(bundle.get("surprise_score"))
    surprise_z = _coerce_float(bundle.get("surprise_z"))
    reaction_pct = _coerce_float(bundle.get("cross_asset_reaction_pct"))
    support_score = _coerce_float(bundle.get("support_score"))
    contradiction_score = _coerce_float(bundle.get("contradiction_score"))
    return {
        "bundle_id": _coerce_text(bundle.get("bundle_id")),
        "market_event_id": _coerce_text(bundle.get("event_id")),
        "event_type": event_type,
        "event_title": _coerce_text(bundle.get("event_title")),
        "event_score": round(event_score, 1),
        "anchor_symbol": anchor_symbol,
        "anchor_direction": anchor_direction,
        "what_happened_text": _coerce_text(bundle.get("what_happened_text")),
        "why_happened_text": _coerce_text(bundle.get("why_happened_text")),
        "affected_assets_summary_text": _coerce_text(bundle.get("affected_assets_summary_text")),
        "driver_symbols": drivers,
        "beneficiary_symbols": up[:6],
        "loser_symbols": down[:6],
        "supporting_symbols": supporting_symbols[:8],
        "cause_status": _coerce_text(bundle.get("cause_status")) or "unresolved",
        "confidence_label": _coerce_text(bundle.get("confidence_label")) or "Developing",
        "source_count": int(bundle.get("source_count") or 0),
        "evidence_count": int(bundle.get("evidence_count") or 0),
        "surface_summary_text": _coerce_text(bundle.get("surface_summary_text")),
        "surface_what_changed_text": _coerce_text(bundle.get("what_happened_text")),
        "surface_why_text": _coerce_text(bundle.get("why_happened_text")),
        "surface_what_else_moved_text": _coerce_text(bundle.get("affected_assets_summary_text")),
        "surface_cause_status": _coerce_text(bundle.get("cause_status")),
        "surface_evidence_quality": _coerce_text(bundle.get("evidence_quality")),
        "surface_freshness_quality": _coerce_text(bundle.get("freshness_quality")),
        "surface_source_summary": _coerce_text(bundle.get("source_summary")),
        "surface_confidence_label": _coerce_text(bundle.get("confidence_label")),
        "importance_tier": _coerce_text(bundle.get("importance_tier")),
        "surprise_score": round(surprise_score, 2) if np.isfinite(surprise_score) else None,
        "surprise_source": _coerce_text(bundle.get("surprise_source")),
        "surprise_z": round(surprise_z, 4) if np.isfinite(surprise_z) else None,
        "cross_asset_reaction_pct": round(reaction_pct, 4) if np.isfinite(reaction_pct) else None,
        "release_type": _coerce_text(bundle.get("release_type")),
        "release_time_utc": _coerce_text(bundle.get("release_time_utc")),
        "promotion_reason": _coerce_text(bundle.get("promotion_reason")),
        "is_forced_macro_release": bool(bundle.get("is_forced_macro_release")),
        "status": _coerce_text(bundle.get("status")),
        "hypothesis_status": _coerce_text(bundle.get("hypothesis_status")),
        "support_score": round(support_score, 4) if np.isfinite(support_score) else None,
        "contradiction_score": round(contradiction_score, 4) if np.isfinite(contradiction_score) else None,
        "relationship_holding_count": int(bundle.get("relationship_holding_count") or 0),
        "relationship_mixed_count": int(bundle.get("relationship_mixed_count") or 0),
        "relationship_broken_count": int(bundle.get("relationship_broken_count") or 0),
        "relationship_unresolved_count": int(bundle.get("relationship_unresolved_count") or 0),
        "primary_nodes": list(_safe_list(bundle.get("primary_nodes"))),
        "source_dataset": _coerce_text(bundle.get("source_dataset")),
    }


def _build_candidate_bundle(
    candidate: dict[str, Any],
    claims: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    prompt_version: str,
    model_name: str,
    run_id: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cause_status, why_today_mode = _judge_cause_status(claims)
    evidence_quality, freshness_quality = _quality_label(claims, cause_status)
    written = _write_symbol_bundle(candidate, claims, peer_moves, llm_client=llm_client, cause_status=cause_status, yield_facts=yield_facts)
    document_importance = _document_importance_map(claims)
    evidence = []
    background = []
    for doc in documents:
        document_id = _coerce_text(doc.get("document_id"))
        source_kind = _coerce_text(doc.get("source_kind"))
        evidence_role = (
            "same_day"
            if _freshness_score(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce"), pd.to_datetime(candidate.get("asof_time_utc"), utc=True, errors="coerce")) >= 0.95
            else "background"
        )
        importance_score = float(document_importance.get(document_id, 0.0))
        importance_label = _importance_label(importance_score)
        is_important = importance_score >= 0.5
        if source_kind.lower() in {"news", "search"} and evidence_role == "same_day" and cause_status == "supported":
            is_important = is_important or importance_score >= 0.45
        if source_kind.lower() in {"news", "search"} and int(doc.get("authority_rank") or 3) <= 1:
            is_important = is_important or importance_score >= 0.42
        item = {
            "document_id": document_id,
            "source": _coerce_text(doc.get("source_provider")),
            "source_kind": source_kind,
            "search_provider": _coerce_text(doc.get("search_provider")),
            "authority_bucket": _coerce_text(doc.get("source_authority_bucket")),
            "headline": _coerce_text(doc.get("title")),
            "summary": _coerce_text(doc.get("display_excerpt") or doc.get("raw_text")),
            "display_excerpt": _coerce_text(doc.get("display_excerpt")),
            "url": _coerce_text(doc.get("url")),
            "published_at": _coerce_text(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce")) else ""),
            "evidence_role": evidence_role,
            "importance_score": round(importance_score, 3),
            "importance_label": importance_label,
            "is_important": bool(is_important),
        }
        if item["evidence_role"] == "same_day":
            evidence.append(item)
        else:
            background.append(item)
    top_claim_ids = [item["claim_id"] for item in claims[:4]]
    important_news_count = sum(
        1
        for item in evidence + background
        if bool(item.get("is_important")) and _coerce_text(item.get("source_kind")).lower() in {"news", "search"}
    )
    return {
        "bundle_id": _coerce_text(candidate.get("bundle_id")) or f"symbol::{_normalize_symbol(candidate.get('symbol'))}",
        "bundle_type": "symbol",
        "run_id": run_id,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "symbol": _normalize_symbol(candidate.get("symbol")),
        "headline": written["title"],
        "surface_summary_text": written["surface_summary"],
        "what_changed_text": written["what_changed_text"],
        "why_now_text": written["why_today_text"],
        "what_else_moved_text": written["what_else_moved_text"],
        "background_context_text": written["background_context_text"],
        "cause_status": cause_status,
        "why_today_mode": why_today_mode,
        "evidence_quality": evidence_quality,
        "freshness_quality": freshness_quality,
        "confidence_label": "High" if cause_status == "supported" and evidence_quality == "High" else ("Medium" if cause_status == "supported" else "Developing"),
        "sector": _coerce_text(candidate.get("sector")),
        "industry": _coerce_text(candidate.get("industry")),
        "source_summary": _top_sources(claims),
        "source_count": len({item.get("source") for item in claims if _coerce_text(item.get("source"))}),
        "evidence_count": len(documents),
        "same_day_evidence_count": len([item for item in claims if bool(item.get("is_same_day"))]),
        "important_news_count": int(important_news_count),
        "evidence": evidence[:6],
        "background_context": background[:4],
        "related_symbols": [],
        "peer_moves": peer_moves[:6],
        "claims": claims[:8],
        "supporting_claim_ids": top_claim_ids,
        "yield_facts": dict(yield_facts or {}),
        "source_trace": {
            "sources": list(dict.fromkeys(doc.get("source_kind") for doc in documents if _coerce_text(doc.get("source_kind")))),
            "macro_facts_dataset": "yield_curve_facts_1d" if yield_facts else "",
        },
    }


def _build_event_bundle(
    event_id: str,
    cluster_rows: pd.DataFrame,
    cluster_claims: list[dict[str, Any]],
    *,
    llm_client: LLMClient | None,
    prompt_version: str,
    model_name: str,
    run_id: str,
    yield_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cluster_tokens = set()
    for _, row in cluster_rows.iterrows():
        cluster_tokens |= _tag_tokens(_safe_list(row.get("macro_exposure_tags")) + _safe_list(row.get("business_tags")))
    cluster_tokens |= _claim_entities(cluster_claims)
    event_type = _infer_event_type(cluster_tokens, cluster_rows)
    anchor_row = cluster_rows.sort_values(["candidate_score", "abs_change_pct"], ascending=[False, False]).iloc[0]
    event_title_seed = _event_title_from_cluster(cluster_rows)
    cause_status, why_today_mode = _judge_cause_status(cluster_claims)
    evidence_quality, freshness_quality = _quality_label(cluster_claims, cause_status)
    written = _write_event_bundle(
        event_title_seed,
        cluster_rows,
        cluster_claims,
        llm_client=llm_client,
        yield_facts=yield_facts,
        event_type=event_type,
        cause_status=cause_status,
        evidence_quality=evidence_quality,
        freshness_quality=freshness_quality,
    )
    event_score = float(cluster_rows["candidate_score"].fillna(0).sum()) + len(cluster_rows["asset_class"].astype(str).dropna().unique()) * 8.0
    up_symbols = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) >= 0]
    down_symbols = [_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _coerce_float(row.get("change_pct"), 0.0) < 0]
    bundle = {
        "bundle_id": f"event::{event_id}",
        "bundle_type": "event",
        "run_id": run_id,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "event_id": event_id,
        "event_title": written["title"],
        "surface_summary_text": written["surface_summary"],
        "what_happened_text": written["what_happened_text"],
        "why_happened_text": written["why_happened_text"],
        "affected_assets_summary_text": written["affected_assets_summary_text"],
        "background_context_text": written["background_context_text"],
        "cause_status": cause_status,
        "why_today_mode": why_today_mode,
        "evidence_quality": evidence_quality,
        "freshness_quality": freshness_quality,
        "confidence_label": "High" if cause_status == "supported" and evidence_quality == "High" else ("Medium" if cause_status == "supported" else "Developing"),
        "source_summary": _top_sources(cluster_claims),
        "source_count": len({item.get("source") for item in cluster_claims if _coerce_text(item.get("source"))}),
        "evidence_count": len(cluster_claims),
        "same_day_evidence_count": len([item for item in cluster_claims if bool(item.get("is_same_day"))]),
        "supporting_symbols": list(dict.fromkeys([_normalize_symbol(row.get("symbol")) for _, row in cluster_rows.iterrows() if _normalize_symbol(row.get("symbol"))])),
        "driver_symbols": list(dict.fromkeys(down_symbols[:6] if _move_direction(anchor_row.get("change_pct")) == "down" else up_symbols[:6])),
        "beneficiary_symbols": list(dict.fromkeys(up_symbols[:6])),
        "loser_symbols": list(dict.fromkeys(down_symbols[:6])),
        "claims": cluster_claims[:10],
        "supporting_claim_ids": [item["claim_id"] for item in cluster_claims[:6]],
        "yield_facts": dict(yield_facts or {}) if _cluster_uses_yield_context(cluster_rows) else {},
        "peer_moves": [],
        "related_symbols": [
            {
                "symbol": _normalize_symbol(row.get("symbol")),
                "headline": _coerce_text(row.get("headline")),
                "change_pct": _coerce_float(row.get("change_pct")),
                "sector": _coerce_text(row.get("sector")),
                "industry": _coerce_text(row.get("industry")),
            }
            for _, row in cluster_rows.iterrows()
        ],
        "event_type": event_type,
        "event_score": round(event_score, 1),
    }
    return bundle


def _build_home_payload(
    candidates: pd.DataFrame,
    bundle_map: dict[str, dict[str, Any]],
    event_bundles: list[dict[str, Any]],
    *,
    attention_rows: pd.DataFrame | None,
    generated_at_utc: pd.Timestamp,
    run_id: str,
    entity_master: pd.DataFrame,
    top_events_limit: int,
    must_read_limit: int,
    unresolved_limit: int,
    macro_release_bundles: list[dict[str, Any]] | None = None,
    macro_release_force_limit: int = 0,
    relationship_checks_frame: pd.DataFrame | None = None,
    hypotheses_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    top_event_items = [
        _event_item(
            bundle,
            candidates[candidates["symbol"].astype(str).str.upper().isin(set(bundle.get("supporting_symbols") or []))].copy(),
            _coerce_text(bundle.get("event_type")) or "cluster",
            event_score=_coerce_float(bundle.get("event_score"), 0.0),
        )
        for bundle in sorted(event_bundles, key=lambda item: -_coerce_float(item.get("event_score"), 0.0))[: max(int(top_events_limit), 1)]
    ]
    macro_release_items = list(macro_release_bundles or [])
    all_qualifying_macro_release_bundles = sorted(
        [item for item in macro_release_items if bool(item.get("is_forced_macro_release"))],
        key=lambda item: (
            -_coerce_float(item.get("surprise_score"), 0.0),
            -_coerce_float(item.get("event_score"), 0.0),
            _coerce_text(item.get("release_type")),
        ),
    )
    qualifying_macro_release_bundles = list(all_qualifying_macro_release_bundles)
    if macro_release_force_limit == 0:
        qualifying_macro_release_bundles = []
    elif macro_release_force_limit > 0:
        qualifying_macro_release_bundles = qualifying_macro_release_bundles[:macro_release_force_limit]
    existing_bundle_ids = {_coerce_text(item.get("bundle_id")) for item in top_event_items if _coerce_text(item.get("bundle_id"))}
    forced_macro_added = 0
    for bundle in qualifying_macro_release_bundles:
        bundle_id = _coerce_text(bundle.get("bundle_id"))
        if bundle_id and bundle_id in existing_bundle_ids:
            continue
        scoped_rows = candidates[candidates["symbol"].astype(str).str.upper().isin(set(bundle.get("supporting_symbols") or []))].copy()
        top_event_items.append(
            _event_item(
                bundle,
                scoped_rows,
                _coerce_text(bundle.get("event_type")) or "macro_release",
                event_score=_coerce_float(bundle.get("event_score"), 0.0),
            )
        )
        if bundle_id:
            existing_bundle_ids.add(bundle_id)
        forced_macro_added += 1
    candidate_rows = candidates.to_dict(orient="records")
    must_read: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        symbol = _normalize_symbol(candidate.get("symbol"))
        bundle = bundle_map.get(f"symbol::{symbol}", {})
        if not bundle:
            continue
        item = _candidate_bundle_item(bundle, candidate)
        same_day = int(bundle.get("same_day_evidence_count") or 0)
        source_count = int(bundle.get("source_count") or 0)
        evidence_count = len(_safe_list(bundle.get("claims"))) + len(_safe_list(bundle.get("evidence")))
        cause_status = _coerce_text(bundle.get("cause_status"))
        has_source_context = same_day > 0 or source_count > 0 or evidence_count > 0
        if cause_status in {"supported", "continuation", "partial"} and has_source_context:
            must_read.append(item)
        elif abs(_coerce_float(candidate.get("change_pct"), 0.0)) >= 4.0:
            unresolved.append(item)
    must_read = sorted(
        must_read,
        key=lambda item: (-int(item.get("same_day_evidence_count") or 0), -_coerce_float(item.get("candidate_score"), 0.0), -abs(_coerce_float(item.get("change_pct"), 0.0))),
    )[: max(int(must_read_limit), 1)]
    unresolved = sorted(
        unresolved,
        key=lambda item: (-_coerce_float(item.get("candidate_score"), 0.0), -abs(_coerce_float(item.get("change_pct"), 0.0))),
    )[: max(int(unresolved_limit), 1)]
    taxonomy_horizon_trends = _taxonomy_horizon_trends(attention_rows, candidates)
    event_impacts = []
    for event in top_event_items:
        for symbol in _safe_list(event.get("supporting_symbols")):
            symbol_key = _normalize_symbol(symbol)
            row = next((item for item in candidate_rows if _normalize_symbol(item.get("symbol")) == symbol_key), {})
            if not row:
                continue
            if symbol_key in set(_safe_list(event.get("driver_symbols"))):
                impact_role = "driver"
            elif _coerce_float(row.get("change_pct"), 0.0) >= 0:
                impact_role = "beneficiary"
            else:
                impact_role = "affected"
            event_impacts.append(
                {
                    "market_event_id": _coerce_text(event.get("market_event_id")),
                    "symbol": symbol_key,
                    "impact_role": impact_role,
                    "direction": _move_direction(row.get("change_pct")),
                    "change_pct": _coerce_float(row.get("change_pct")),
                    "sector": _coerce_text(row.get("sector")),
                    "industry": _coerce_text(row.get("industry")),
                    "bundle_id": f"symbol::{symbol_key}",
                }
            )
    coverage_summary = {
        "candidate_count": int(len(candidates)),
        "event_count": int(len(top_event_items)),
        "must_read_count": int(len(must_read)),
        "unresolved_count": int(len(unresolved)),
        "macro_anchor_count": int(candidates["source_label"].astype(str).str.lower().eq("macro anchor").sum()) if "source_label" in candidates.columns else 0,
        "news_backed_count": int(sum(1 for bundle in bundle_map.values() if int(bundle.get("same_day_evidence_count") or 0) > 0)),
        "portfolio_overlap_count": int(candidates.get("in_portfolio", pd.Series(dtype=bool)).fillna(False).sum()) if "in_portfolio" in candidates.columns else 0,
        "today_only": len(taxonomy_horizon_trends) == 0,
        "supports_multi_horizon": len(taxonomy_horizon_trends) > 0,
        "taxonomy_trend_horizon_count": int(len(taxonomy_horizon_trends)),
        "taxonomy_trend_cohort_count": int(sum(len(item.get("cohorts") or []) for item in taxonomy_horizon_trends)),
        "macro_release_detected_count": int(len(macro_release_items)),
        "macro_release_qualifying_count": int(len(all_qualifying_macro_release_bundles)),
        "macro_release_promoted_count": int(forced_macro_added),
        "macro_release_suppressed_count": int(max(len(all_qualifying_macro_release_bundles) - forced_macro_added, 0)),
        "macro_relationship_check_count": int(len(relationship_checks_frame)) if isinstance(relationship_checks_frame, pd.DataFrame) else 0,
        "macro_relationship_holding_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("holding").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_mixed_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("mixed").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_broken_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("broken").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_relationship_unresolved_count": int(relationship_checks_frame["consistency_status"].astype(str).str.lower().eq("unresolved").sum()) if isinstance(relationship_checks_frame, pd.DataFrame) and "consistency_status" in relationship_checks_frame.columns else 0,
        "macro_hypothesis_count": int(len(hypotheses_frame)) if isinstance(hypotheses_frame, pd.DataFrame) else 0,
        "macro_hypothesis_supported_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("supported").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_continuation_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("continuation").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_conflicting_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("conflicting").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "macro_hypothesis_unresolved_count": int(hypotheses_frame["support_status"].astype(str).str.lower().eq("unresolved").sum()) if isinstance(hypotheses_frame, pd.DataFrame) and "support_status" in hypotheses_frame.columns else 0,
        "run_id": run_id,
    }
    return {
        "top_events": top_event_items,
        "must_read_movers": must_read,
        "unresolved_large_moves": unresolved,
        "generated_at_utc": generated_at_utc.isoformat(),
        "coverage_summary": coverage_summary,
        "taxonomy_horizon_trends": taxonomy_horizon_trends,
        "event_candidates_1d": candidates.to_dict(orient="records"),
        "event_impacts_1d": event_impacts,
        "entity_master": entity_master.to_dict(orient="records") if isinstance(entity_master, pd.DataFrame) else [],
        "run_id": run_id,
    }
