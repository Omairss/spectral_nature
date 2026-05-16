"""
Knowledge graph update proposals.

Attention jobs should not silently mutate the core knowledge graph. This module
turns attention evidence into reviewable add/update proposals that the UI can
load into the existing knowledge-graph review flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any

import pandas as pd

from . import knowledge_graph as kg
from .entity_extraction import extract_entities, graph_add_node_candidates


KG_PROPOSAL_COLUMNS = [
    "proposal_id",
    "run_id",
    "asof_time_utc",
    "proposal_type",
    "operation",
    "review_status",
    "node_id",
    "label",
    "node_type",
    "source_node_id",
    "target_node_id",
    "relationship",
    "mechanism",
    "polarity",
    "directness",
    "severity",
    "confidence",
    "conditions_json",
    "evidence_refs_json",
    "rationale",
    "source_dataset",
    "source_record_id",
    "existing_status",
    "proposal_payload_json",
]


def _clean(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _slug(value: object, *, fallback: str = "node") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _clean(value).lower()).strip("_")
    return slug or fallback


def _json_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return [text]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if not math.isfinite(out):
        return default
    return out


def _bounded(value: object, default: float = 0.55) -> float:
    return min(max(_float(value, default), 0.0), 1.0)


def _proposal_id(*parts: object) -> str:
    raw = "|".join(_clean(part) for part in parts)
    return f"kgp::{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:18]}"


def empty_knowledge_graph_proposals_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=KG_PROPOSAL_COLUMNS)


def _snapshot_or_default(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return snapshot
    try:
        return kg.load_knowledge_graph_snapshot()
    except Exception:
        return {"nodes_by_id": {}, "edges": []}


def _node_exists(snapshot: dict[str, Any], node_id: str) -> bool:
    return _clean(node_id) in dict(snapshot.get("nodes_by_id") or {})


def _edge_exists(snapshot: dict[str, Any], *, edge_id: str, source: str, target: str, relationship: str) -> bool:
    for edge in list(snapshot.get("edges") or []):
        if _clean(edge.get("edge_id")) == edge_id:
            return True
        if (
            _clean(edge.get("source_node_id")) == source
            and _clean(edge.get("target_node_id")) == target
            and _clean(edge.get("relationship")) == relationship
        ):
            return True
    return False


def _proposal_row(**values: object) -> dict[str, object]:
    row = {column: "" for column in KG_PROPOSAL_COLUMNS}
    row.update(values)
    return row


def _claim_entity_node_proposals(
    *,
    run_id: str,
    asof_time_utc: str,
    claims_frame: pd.DataFrame,
    snapshot: dict[str, Any],
) -> list[dict[str, object]]:
    if not isinstance(claims_frame, pd.DataFrame) or claims_frame.empty:
        return []
    proposals: list[dict[str, object]] = []
    seen: set[str] = set()
    for _, claim in claims_frame.iterrows():
        claim_text = _clean(claim.get("claim_text"))
        claim_id = _clean(claim.get("claim_id"))
        if not claim_text and not claim_id:
            continue
        claim_entities = _json_list(claim.get("claim_entities_json") or claim.get("claim_entities"))
        subject_symbol = _clean(claim.get("bundle_subject") or claim.get("symbol"))
        try:
            mentions = extract_entities(
                claim_text,
                subject_symbol=subject_symbol,
                claim_entities=claim_entities,
                snapshot=snapshot,
                include_taxonomy=False,
            )
        except Exception:
            mentions = []
        for candidate in graph_add_node_candidates(mentions, min_confidence=0.74):
            node_id = _clean(candidate.get("node_id"))
            if not node_id or node_id in seen or _node_exists(snapshot, node_id):
                continue
            seen.add(node_id)
            confidence = _bounded(candidate.get("confidence"), 0.74)
            proposals.append(
                _proposal_row(
                    proposal_id=_proposal_id(run_id, "node", node_id, claim_id),
                    run_id=run_id,
                    asof_time_utc=asof_time_utc,
                    proposal_type="node",
                    operation="add_node",
                    review_status="proposed",
                    node_id=node_id,
                    label=_clean(candidate.get("label") or node_id),
                    node_type=_clean(candidate.get("node_type") or "concept"),
                    confidence=confidence,
                    evidence_refs_json=_json_dumps([claim_id] if claim_id else []),
                    rationale=_clean(candidate.get("reason") or "Extracted from attention claim evidence."),
                    source_dataset="attention_claims",
                    source_record_id=claim_id,
                    existing_status="new",
                    proposal_payload_json=_json_dumps({"candidate": candidate, "claim_text": claim_text}),
                )
            )
    return proposals


def _macro_node_label(node_id: str) -> str:
    return _clean(node_id).replace("_", " ").title()


def _macro_graph_proposals(
    *,
    run_id: str,
    asof_time_utc: str,
    macro_edges_frame: pd.DataFrame,
    relationship_checks_frame: pd.DataFrame,
    snapshot: dict[str, Any],
) -> list[dict[str, object]]:
    if not isinstance(macro_edges_frame, pd.DataFrame) or macro_edges_frame.empty:
        return []
    check_lookup: dict[str, dict[str, Any]] = {}
    if isinstance(relationship_checks_frame, pd.DataFrame) and not relationship_checks_frame.empty:
        for _, row in relationship_checks_frame.iterrows():
            edge_id = _clean(row.get("edge_id"))
            if edge_id:
                check_lookup[edge_id] = dict(row)

    proposals: list[dict[str, object]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[str] = set()
    for _, edge in macro_edges_frame.iterrows():
        source = _slug(edge.get("from_node"), fallback="")
        target = _slug(edge.get("to_node"), fallback="")
        if not source or not target:
            continue
        source_record_id = _clean(edge.get("edge_id")) or f"{source}_to_{target}"
        check = check_lookup.get(source_record_id, {})
        for node_id in [source, target]:
            if node_id in seen_nodes or _node_exists(snapshot, node_id):
                continue
            seen_nodes.add(node_id)
            proposals.append(
                _proposal_row(
                    proposal_id=_proposal_id(run_id, "macro_node", node_id),
                    run_id=run_id,
                    asof_time_utc=asof_time_utc,
                    proposal_type="node",
                    operation="add_node",
                    review_status="proposed",
                    node_id=node_id,
                    label=_macro_node_label(node_id),
                    node_type="macro_concept",
                    confidence=_bounded(edge.get("confidence_prior"), 0.55),
                    evidence_refs_json=_json_dumps([source_record_id]),
                    rationale="Macro relationship profile referenced this node during the attention run.",
                    source_dataset="macro_causal_graph_edges_v1",
                    source_record_id=source_record_id,
                    existing_status="new",
                    proposal_payload_json=_json_dumps({"macro_edge": dict(edge)}),
                )
            )

        relationship = "influences"
        edge_id = _clean(edge.get("edge_id")) or f"{source}_{relationship}_{target}"
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        exists = _edge_exists(snapshot, edge_id=edge_id, source=source, target=target, relationship=relationship)
        confidence = _bounded(edge.get("confidence_prior"), 0.55)
        severity = min(max(_float(edge.get("strength_weight"), 1.0) / 2.0, 0.2), 1.0)
        expected_sign = int(_float(edge.get("expected_sign"), 0.0))
        polarity = "positive" if expected_sign > 0 else ("negative" if expected_sign < 0 else "mixed")
        consistency_status = _clean(check.get("consistency_status"))
        mechanism = (
            f"{_macro_node_label(source)} is expected to influence {_macro_node_label(target)} "
            f"over {edge.get('lag_window') or 'same_day'} in {edge.get('regime_filter') or 'all'} regimes."
        )
        proposals.append(
            _proposal_row(
                proposal_id=_proposal_id(run_id, "macro_edge", edge_id),
                run_id=run_id,
                asof_time_utc=asof_time_utc,
                proposal_type="edge",
                operation="update_edge" if exists else "add_edge",
                review_status="proposed",
                source_node_id=source,
                target_node_id=target,
                relationship=relationship,
                mechanism=mechanism,
                polarity=polarity,
                directness="indirect",
                severity=round(severity, 3),
                confidence=round(confidence, 3),
                conditions_json=_json_dumps(
                    [
                        item
                        for item in [
                            f"lag_window={_clean(edge.get('lag_window'))}" if _clean(edge.get("lag_window")) else "",
                            f"regime_filter={_clean(edge.get('regime_filter'))}" if _clean(edge.get("regime_filter")) else "",
                            f"consistency_status={consistency_status}" if consistency_status else "",
                        ]
                        if item
                    ]
                ),
                evidence_refs_json=_json_dumps([source_record_id]),
                rationale="Attention macro diagnostics materialized this causal relationship as a reviewable KG edge.",
                source_dataset="macro_causal_graph_edges_v1",
                source_record_id=source_record_id,
                existing_status="existing" if exists else "new",
                proposal_payload_json=_json_dumps({"macro_edge": dict(edge), "relationship_check": check}),
            )
        )
    return proposals


def build_attention_knowledge_graph_proposals(
    *,
    run_id: str,
    asof_time_utc: object,
    claims_frame: pd.DataFrame | None = None,
    macro_edges_frame: pd.DataFrame | None = None,
    relationship_checks_frame: pd.DataFrame | None = None,
    snapshot: dict[str, Any] | None = None,
) -> pd.DataFrame:
    resolved_snapshot = _snapshot_or_default(snapshot)
    run = _clean(run_id) or "attention-run"
    asof = pd.to_datetime(asof_time_utc or datetime.now(timezone.utc), utc=True, errors="coerce")
    asof_text = asof.isoformat() if pd.notna(asof) else datetime.now(timezone.utc).isoformat()
    rows = []
    rows.extend(
        _claim_entity_node_proposals(
            run_id=run,
            asof_time_utc=asof_text,
            claims_frame=claims_frame if isinstance(claims_frame, pd.DataFrame) else pd.DataFrame(),
            snapshot=resolved_snapshot,
        )
    )
    rows.extend(
        _macro_graph_proposals(
            run_id=run,
            asof_time_utc=asof_text,
            macro_edges_frame=macro_edges_frame if isinstance(macro_edges_frame, pd.DataFrame) else pd.DataFrame(),
            relationship_checks_frame=relationship_checks_frame if isinstance(relationship_checks_frame, pd.DataFrame) else pd.DataFrame(),
            snapshot=resolved_snapshot,
        )
    )
    if not rows:
        return empty_knowledge_graph_proposals_frame()
    frame = pd.DataFrame(rows)
    for column in KG_PROPOSAL_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    for column in ["severity", "confidence"]:
        frame[column] = pd.to_numeric(frame[column].replace("", pd.NA), errors="coerce")
    return frame[KG_PROPOSAL_COLUMNS].drop_duplicates(subset=["proposal_id"]).reset_index(drop=True)


def build_knowledge_graph_draft_from_proposals(
    proposals_frame: pd.DataFrame,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_snapshot = _snapshot_or_default(snapshot)
    nodes_by_id = dict(resolved_snapshot.get("nodes_by_id") or {})
    proposals = proposals_frame.copy() if isinstance(proposals_frame, pd.DataFrame) else pd.DataFrame()
    if proposals.empty:
        return {
            "query": "Latest Attention KG proposals",
            "title": "Attention Knowledge Graph Proposals",
            "seed_matches": [],
            "selected_node_ids": [],
            "nodes": [],
            "edges": [],
            "agentic_summary": "",
            "limitations": ["No knowledge-graph proposals are available."],
            "runtime_status": kg.knowledge_graph_runtime_status(),
        }

    node_rows_by_id: dict[str, dict[str, Any]] = {}

    def ensure_node(node_id: str, *, label: str = "", node_type: str = "concept", reason: str = "") -> None:
        clean_id = _clean(node_id)
        if not clean_id or clean_id in node_rows_by_id:
            return
        existing = dict(nodes_by_id.get(clean_id) or {})
        node_rows_by_id[clean_id] = {
            "keep": True,
            "node_id": clean_id,
            "label": _clean(label or existing.get("canonical_label") or clean_id),
            "node_type": _clean(node_type or existing.get("node_type") or "concept"),
            "description": _clean(existing.get("description")),
            "status": _clean(existing.get("status")),
            "aliases": ", ".join(_clean(alias) for alias in _json_list(existing.get("aliases")) if _clean(alias)),
            "source_status": "agent_suggested" if not existing else _clean(existing.get("source_status") or "seeded"),
            "confidence": "",
            "reason": reason,
        }

    edge_rows: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    for _, proposal in proposals.iterrows():
        if _clean(proposal.get("review_status") or "proposed") not in {"", "proposed"}:
            continue
        proposal_type = _clean(proposal.get("proposal_type"))
        operation = _clean(proposal.get("operation"))
        rationale = _clean(proposal.get("rationale"))
        if proposal_type == "node" and operation in {"add_node", "update_node"}:
            node_id = _clean(proposal.get("node_id"))
            ensure_node(
                node_id,
                label=_clean(proposal.get("label")),
                node_type=_clean(proposal.get("node_type") or "concept"),
                reason=rationale,
            )
            if node_id and node_id not in selected_ids:
                selected_ids.append(node_id)
        elif proposal_type == "edge" and operation in {"add_edge", "update_edge"}:
            source = _clean(proposal.get("source_node_id"))
            target = _clean(proposal.get("target_node_id"))
            ensure_node(source, node_type="concept", reason="Endpoint for proposed edge.")
            ensure_node(target, node_type="concept", reason="Endpoint for proposed edge.")
            if source and source not in selected_ids:
                selected_ids.append(source)
            if target and target not in selected_ids:
                selected_ids.append(target)
            edge_id = _clean(proposal.get("source_record_id")) or f"{source}_{_slug(proposal.get('relationship'), fallback='related_to')}_{target}"
            edge_rows.append(
                {
                    "keep": True,
                    "edge_id": edge_id,
                    "source": source,
                    "target": target,
                    "relationship": _clean(proposal.get("relationship") or "related_to"),
                    "mechanism": _clean(proposal.get("mechanism")),
                    "polarity": _clean(proposal.get("polarity")),
                    "directness": _clean(proposal.get("directness")),
                    "severity": proposal.get("severity"),
                    "confidence": proposal.get("confidence"),
                    "conditions": ", ".join(_json_list(proposal.get("conditions_json"))),
                    "source_status": "agent_suggested",
                    "reason": rationale,
                }
            )

    return {
        "query": "Latest Attention KG proposals",
        "title": "Attention Knowledge Graph Proposals",
        "seed_matches": [],
        "selected_node_ids": selected_ids[:8],
        "nodes": list(node_rows_by_id.values()),
        "edges": edge_rows,
        "agentic_summary": f"Loaded {len(proposals)} reviewable proposal row(s) from the latest Attention run.",
        "limitations": [],
        "runtime_status": kg.knowledge_graph_runtime_status(),
    }


__all__ = [
    "KG_PROPOSAL_COLUMNS",
    "build_attention_knowledge_graph_proposals",
    "build_knowledge_graph_draft_from_proposals",
    "empty_knowledge_graph_proposals_frame",
]
