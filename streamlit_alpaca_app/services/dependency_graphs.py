from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


APP_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_GRAPH_ROOT = APP_ROOT / "data" / "dependency_graphs"
DEPENDENCY_GRAPH_DATA_DIR = DEPENDENCY_GRAPH_ROOT / "graphs"
DEPENDENCY_GRAPH_SCHEMA_PATH = DEPENDENCY_GRAPH_ROOT / "schemas" / "dependency_graph.schema.json"


def _normalized_tokens(values: list[str] | None) -> set[str]:
    return {str(value).strip().lower() for value in (values or []) if str(value).strip()}


def dependency_graph_paths() -> list[Path]:
    if not DEPENDENCY_GRAPH_DATA_DIR.exists():
        return []
    return sorted(path for path in DEPENDENCY_GRAPH_DATA_DIR.glob("*.json") if path.is_file())


def validate_dependency_graph_payload(payload: dict[str, Any], *, source: str = "") -> dict[str, Any]:
    prefix = f"{source}: " if source else ""
    if not isinstance(payload, dict):
        raise ValueError(f"{prefix}dependency graph payload must be an object")

    schema_version = str(payload.get("schema_version") or "").strip()
    if not schema_version:
        raise ValueError(f"{prefix}missing required field `schema_version`")

    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise ValueError(f"{prefix}missing required object `graph`")

    graph_id = str(graph.get("id") or "").strip()
    title = str(graph.get("title") or "").strip()
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not graph_id:
        raise ValueError(f"{prefix}graph.id is required")
    if not title:
        raise ValueError(f"{prefix}graph.title is required")
    if not isinstance(nodes, list):
        raise ValueError(f"{prefix}graph.nodes must be an array")
    if not isinstance(edges, list):
        raise ValueError(f"{prefix}graph.edges must be an array")

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"{prefix}graph.nodes[{index}] must be an object")
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        label = str(node.get("label") or "").strip()
        if not node_id:
            raise ValueError(f"{prefix}graph.nodes[{index}].id is required")
        if node_id in node_ids:
            raise ValueError(f"{prefix}duplicate node id `{node_id}`")
        if not node_type:
            raise ValueError(f"{prefix}graph.nodes[{index}].type is required")
        if not label:
            raise ValueError(f"{prefix}graph.nodes[{index}].label is required")
        node_ids.add(node_id)

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"{prefix}graph.edges[{index}] must be an object")
        edge_id = str(edge.get("id") or "").strip()
        source_id = str(edge.get("source") or "").strip()
        target_id = str(edge.get("target") or "").strip()
        relationship = str(edge.get("relationship") or "").strip()
        if not edge_id:
            raise ValueError(f"{prefix}graph.edges[{index}].id is required")
        if edge_id in edge_ids:
            raise ValueError(f"{prefix}duplicate edge id `{edge_id}`")
        if not source_id or not target_id:
            raise ValueError(f"{prefix}graph.edges[{index}] must define `source` and `target`")
        if source_id not in node_ids or target_id not in node_ids:
            raise ValueError(f"{prefix}graph.edges[{index}] references an unknown node")
        if not relationship:
            raise ValueError(f"{prefix}graph.edges[{index}].relationship is required")
        edge_ids.add(edge_id)

    for section_name in ("scenarios", "evidence", "metrics"):
        value = graph.get(section_name)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"{prefix}graph.{section_name} must be an array when present")

    return payload


def _load_graph_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: dependency graph file must contain an object")
    payload = validate_dependency_graph_payload(payload, source=str(path))
    payload_copy = json.loads(json.dumps(payload))
    payload_copy["_path"] = str(path)
    return payload_copy


@lru_cache(maxsize=1)
def _load_all_dependency_graph_payloads() -> tuple[dict[str, Any], ...]:
    return tuple(_load_graph_payload(path) for path in dependency_graph_paths())


def clear_dependency_graph_cache() -> None:
    _load_all_dependency_graph_payloads.cache_clear()


def load_dependency_graph_payloads(
    *,
    graph_ids: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    requested_ids = _normalized_tokens(graph_ids)
    requested_tags = _normalized_tokens(tags)

    selected: list[dict[str, Any]] = []
    for payload in _load_all_dependency_graph_payloads():
        graph = dict(payload.get("graph") or {})
        graph_id = str(graph.get("id") or "").strip().lower()
        graph_tags = _normalized_tokens(list(graph.get("tags") or []))
        if requested_ids and graph_id not in requested_ids:
            continue
        if requested_tags and not requested_tags.issubset(graph_tags):
            continue
        selected.append(json.loads(json.dumps(payload)))
    return selected


def dependency_graph_catalog(
    *,
    graph_ids: list[str] | None = None,
    tags: list[str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for payload in load_dependency_graph_payloads(graph_ids=graph_ids, tags=tags):
        graph = dict(payload.get("graph") or {})
        rows.append(
            {
                "graph_id": str(graph.get("id") or ""),
                "title": str(graph.get("title") or ""),
                "description": str(graph.get("description") or ""),
                "created_at": str(graph.get("created_at") or ""),
                "time_horizon": str(graph.get("time_horizon") or ""),
                "tags": list(graph.get("tags") or []),
                "node_count": len(list(graph.get("nodes") or [])),
                "edge_count": len(list(graph.get("edges") or [])),
                "path": str(payload.get("_path") or ""),
            }
        )
    return pd.DataFrame(rows)


def _edge_weight(edge: dict[str, Any]) -> float:
    attributes = dict(edge.get("attributes") or {}) if isinstance(edge.get("attributes"), dict) else {}
    for key in ("display_weight", "weight"):
        value = attributes.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    for key in ("severity", "confidence"):
        value = edge.get(key)
        if isinstance(value, (int, float)):
            return max(float(value) * 3.0, 0.5)
    return 1.0


def dependency_graph_edges_frame(
    *,
    graph_ids: list[str] | None = None,
    tags: list[str] | None = None,
    allowed_node_ids: list[str] | set[str] | None = None,
) -> pd.DataFrame:
    allowed = {str(value).strip() for value in (allowed_node_ids or []) if str(value).strip()}
    rows: list[dict[str, Any]] = []

    for payload in load_dependency_graph_payloads(graph_ids=graph_ids, tags=tags):
        graph = dict(payload.get("graph") or {})
        node_lookup = {
            str(node.get("id") or "").strip(): dict(node)
            for node in list(graph.get("nodes") or [])
            if isinstance(node, dict)
        }
        for edge in list(graph.get("edges") or []):
            if not isinstance(edge, dict):
                continue
            source_id = str(edge.get("source") or "").strip()
            target_id = str(edge.get("target") or "").strip()
            if allowed and (source_id not in allowed or target_id not in allowed):
                continue
            source_node = node_lookup.get(source_id, {})
            target_node = node_lookup.get(target_id, {})
            lag = dict(edge.get("lag") or {}) if isinstance(edge.get("lag"), dict) else {}
            rows.append(
                {
                    "graph_id": str(graph.get("id") or ""),
                    "graph_title": str(graph.get("title") or ""),
                    "graph_tags": list(graph.get("tags") or []),
                    "graph_time_horizon": str(graph.get("time_horizon") or ""),
                    "source": source_id,
                    "target": target_id,
                    "source_label": str(source_node.get("label") or source_id),
                    "target_label": str(target_node.get("label") or target_id),
                    "source_type": str(source_node.get("type") or ""),
                    "target_type": str(target_node.get("type") or ""),
                    "source_status": str(source_node.get("status") or ""),
                    "target_status": str(target_node.get("status") or ""),
                    "source_description": str(source_node.get("description") or ""),
                    "target_description": str(target_node.get("description") or ""),
                    "relationship": str(edge.get("relationship") or ""),
                    "mechanism": str(edge.get("mechanism") or ""),
                    "polarity": str(edge.get("polarity") or ""),
                    "directness": str(edge.get("directness") or ""),
                    "severity": edge.get("severity"),
                    "confidence": edge.get("confidence"),
                    "weight": _edge_weight(edge),
                    "lag_value": lag.get("value"),
                    "lag_unit": str(lag.get("unit") or ""),
                    "conditions": list(edge.get("conditions") or []),
                    "attributes": dict(edge.get("attributes") or {}) if isinstance(edge.get("attributes"), dict) else {},
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "graph_id",
                "graph_title",
                "graph_tags",
                "graph_time_horizon",
                "source",
                "target",
                "source_label",
                "target_label",
                "source_type",
                "target_type",
                "source_status",
                "target_status",
                "source_description",
                "target_description",
                "relationship",
                "mechanism",
                "polarity",
                "directness",
                "severity",
                "confidence",
                "weight",
                "lag_value",
                "lag_unit",
                "conditions",
                "attributes",
            ]
        )

    frame = pd.DataFrame(rows)
    return frame.sort_values(["graph_id", "source_label", "target_label"]).reset_index(drop=True)


__all__ = [
    "DEPENDENCY_GRAPH_DATA_DIR",
    "DEPENDENCY_GRAPH_ROOT",
    "DEPENDENCY_GRAPH_SCHEMA_PATH",
    "clear_dependency_graph_cache",
    "dependency_graph_catalog",
    "dependency_graph_edges_frame",
    "dependency_graph_paths",
    "load_dependency_graph_payloads",
    "validate_dependency_graph_payload",
]
