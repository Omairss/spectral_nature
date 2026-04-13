from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any
import uuid

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .dependency_graphs import load_dependency_graph_payloads
from .llm import LLMAPIError, load_embedding_client, load_llm_client
from .secrets import resolve_secret_value
from .web_research import (
    SerpAPISearchClient,
    TavilySearchClient,
    WebResearchError,
    load_serpapi_config,
    load_tavily_config,
)


try:
    import psycopg
except Exception:
    psycopg = None


APP_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_GRAPH_ROOT = APP_ROOT / "data" / "knowledge_graph"
KNOWLEDGE_GRAPH_SEED_DIR = KNOWLEDGE_GRAPH_ROOT / "seed_graphs"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_loads(value: object, *, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _clean(value)
    if not text:
        return default
    try:
        parsed = json.loads(text)
    except Exception:
        return default
    return parsed


def _coerce_float(value: object, *, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(out):
        return float(default)
    return float(out)


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _slug(value: object, *, fallback: str = "node") -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _normalize_node_id(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    if re.fullmatch(r"[A-Z0-9.\-]+", text):
        return text.upper()
    return _slug(text, fallback="node")


def _edge_id(source: str, target: str, relationship: str) -> str:
    return _slug(f"{source}_{relationship}_{target}", fallback="edge")


def _split_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, tuple):
        raw = list(value)
    else:
        text = _clean(value)
        if not text:
            return []
        raw = re.split(r"[\n,;|]+", text)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        clean = _clean(item)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _tokenize(value: object) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", _clean(value).lower())
        if token
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_arr = np.asarray(left, dtype=float)
    right_arr = np.asarray(right, dtype=float)
    left_norm = float(np.linalg.norm(left_arr))
    right_norm = float(np.linalg.norm(right_arr))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(np.dot(left_arr, right_arr) / (left_norm * right_norm))


def _row_dict(cursor: Any, row: Any) -> dict[str, Any]:
    columns = [item.name if hasattr(item, "name") else item[0] for item in cursor.description or []]
    return {str(key): value for key, value in zip(columns, row)}


def _fetchall_dicts(cursor: Any) -> list[dict[str, Any]]:
    return [_row_dict(cursor, row) for row in cursor.fetchall() or []]


def _schema_name() -> str:
    raw = (os.getenv("APP_KNOWLEDGE_GRAPH_SCHEMA") or "app_knowledge_graph").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw
    return "app_knowledge_graph"


def _postgres_connection_string() -> str:
    return resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )


def knowledge_graph_store_configured() -> bool:
    return bool(_postgres_connection_string() and psycopg is not None)


def _db_connect() -> Any | None:
    conn_str = _postgres_connection_string()
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str)
    except Exception:
        return None


def _ensure_schema(conn: Any) -> None:
    schema = _schema_name()
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.knowledge_graph_nodes (
            node_id TEXT PRIMARY KEY,
            canonical_label TEXT NOT NULL,
            node_type TEXT NOT NULL,
            description TEXT NULL,
            status TEXT NULL,
            attributes_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_status TEXT NOT NULL DEFAULT 'committed',
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_by TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_knowledge_graph_nodes_label_lower ON {schema}.knowledge_graph_nodes ((lower(canonical_label)))",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.knowledge_graph_aliases (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            created_by TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{schema}_knowledge_graph_aliases_node_lower ON {schema}.knowledge_graph_aliases (node_id, lower(alias))",
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_knowledge_graph_aliases_alias_lower ON {schema}.knowledge_graph_aliases ((lower(alias)))",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.knowledge_graph_edges (
            edge_id TEXT PRIMARY KEY,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            relationship TEXT NOT NULL,
            mechanism TEXT NULL,
            polarity TEXT NULL,
            directness TEXT NULL,
            severity DOUBLE PRECISION NULL,
            confidence DOUBLE PRECISION NULL,
            lag_value DOUBLE PRECISION NULL,
            lag_unit TEXT NULL,
            conditions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            attributes_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_status TEXT NOT NULL DEFAULT 'committed',
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_by TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_knowledge_graph_edges_nodes ON {schema}.knowledge_graph_edges (source_node_id, target_node_id)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.knowledge_graph_commits (
            commit_id UUID PRIMARY KEY,
            query_text TEXT NOT NULL,
            summary TEXT NULL,
            created_by TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            draft_payload_json JSONB NOT NULL,
            applied_delta_json JSONB NOT NULL
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{schema}_knowledge_graph_commits_created_at ON {schema}.knowledge_graph_commits (created_at DESC)",
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def _base_node_row(
    *,
    node_id: str,
    canonical_label: str,
    node_type: str,
    description: str = "",
    status: str = "",
    attributes: dict[str, Any] | None = None,
    source_status: str = "seeded",
    is_deleted: bool = False,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "canonical_label": canonical_label,
        "node_type": node_type or "concept",
        "description": description,
        "status": status,
        "attributes_json": dict(attributes or {}),
        "source_status": source_status,
        "is_deleted": bool(is_deleted),
    }


def _base_edge_row(
    *,
    edge_id: str,
    source_node_id: str,
    target_node_id: str,
    relationship: str,
    mechanism: str = "",
    polarity: str = "",
    directness: str = "",
    severity: float | None = None,
    confidence: float | None = None,
    lag_value: float | None = None,
    lag_unit: str = "",
    conditions: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    source_status: str = "seeded",
    is_deleted: bool = False,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "relationship": relationship or "related_to",
        "mechanism": mechanism,
        "polarity": polarity,
        "directness": directness,
        "severity": severity,
        "confidence": confidence,
        "lag_value": lag_value,
        "lag_unit": lag_unit,
        "conditions_json": list(conditions or []),
        "attributes_json": dict(attributes or {}),
        "source_status": source_status,
        "is_deleted": bool(is_deleted),
    }


def _base_alias_row(
    *,
    node_id: str,
    alias: str,
    alias_type: str = "alias",
) -> dict[str, Any]:
    return {
        "alias_id": f"{node_id}::{_slug(alias, fallback='alias')}",
        "node_id": node_id,
        "alias": alias,
        "alias_type": alias_type or "alias",
    }


def _merge_node_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("canonical_label", "node_type", "description", "status", "source_status", "is_deleted"):
        value = overlay.get(key)
        if value not in (None, ""):
            merged[key] = value
    merged["attributes_json"] = {
        **dict(base.get("attributes_json") or {}),
        **dict(overlay.get("attributes_json") or {}),
    }
    return merged


def _merge_edge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in (
        "source_node_id",
        "target_node_id",
        "relationship",
        "mechanism",
        "polarity",
        "directness",
        "severity",
        "confidence",
        "lag_value",
        "lag_unit",
        "source_status",
        "is_deleted",
    ):
        value = overlay.get(key)
        if value not in (None, ""):
            merged[key] = value
    merged["conditions_json"] = list(overlay.get("conditions_json") or base.get("conditions_json") or [])
    merged["attributes_json"] = {
        **dict(base.get("attributes_json") or {}),
        **dict(overlay.get("attributes_json") or {}),
    }
    return merged


def _read_seed_graph_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return None
    return payload


def _records_from_custom_seed_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    node_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    for node in list(payload.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = _normalize_node_id(node.get("id") or node.get("node_id") or node.get("label"))
        label = _clean(node.get("label") or node_id)
        if not node_id or not label:
            continue
        node_rows.append(
            _base_node_row(
                node_id=node_id,
                canonical_label=label,
                node_type=_clean(node.get("node_type") or node.get("type") or "concept"),
                description=_clean(node.get("description")),
                status=_clean(node.get("status")),
                attributes=dict(node.get("attributes") or {}),
                source_status="seeded",
            )
        )
        alias_values = [node_id, label, *list(node.get("aliases") or [])]
        symbol = _clean((node.get("attributes") or {}).get("ticker"))
        if symbol:
            alias_values.append(symbol)
        for alias in _split_text_list(alias_values):
            alias_type = "ticker" if alias.upper() == symbol.upper() and symbol else "alias"
            alias_rows.append(_base_alias_row(node_id=node_id, alias=alias, alias_type=alias_type))

    for edge in list(payload.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        source_node_id = _normalize_node_id(edge.get("source") or edge.get("source_node_id"))
        target_node_id = _normalize_node_id(edge.get("target") or edge.get("target_node_id"))
        relationship = _clean(edge.get("relationship") or "related_to")
        if not source_node_id or not target_node_id:
            continue
        edge_rows.append(
            _base_edge_row(
                edge_id=_clean(edge.get("id")) or _edge_id(source_node_id, target_node_id, relationship),
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relationship=relationship,
                mechanism=_clean(edge.get("mechanism")),
                polarity=_clean(edge.get("polarity")),
                directness=_clean(edge.get("directness")),
                severity=edge.get("severity"),
                confidence=edge.get("confidence"),
                lag_value=(edge.get("lag") or {}).get("value") if isinstance(edge.get("lag"), dict) else edge.get("lag_value"),
                lag_unit=_clean((edge.get("lag") or {}).get("unit")) if isinstance(edge.get("lag"), dict) else _clean(edge.get("lag_unit")),
                conditions=list(edge.get("conditions") or []),
                attributes=dict(edge.get("attributes") or {}),
                source_status="seeded",
            )
        )
    return node_rows, edge_rows, alias_rows


def _records_from_dependency_graph_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    graph = dict(payload.get("graph") or {})
    graph_tags = list(graph.get("tags") or [])
    node_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    for node in list(graph.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = _normalize_node_id(node.get("id") or node.get("label"))
        label = _clean(node.get("label") or node_id)
        if not node_id or not label:
            continue
        attributes = dict(node.get("attributes") or {})
        attributes.setdefault("graph_tags", graph_tags)
        proxy_name = _clean(attributes.get("proxy_name"))
        node_rows.append(
            _base_node_row(
                node_id=node_id,
                canonical_label=label,
                node_type=_clean(node.get("type") or "concept"),
                description=_clean(node.get("description")),
                status=_clean(node.get("status")),
                attributes=attributes,
                source_status="seeded",
            )
        )
        alias_values = [node_id, label]
        if proxy_name:
            alias_values.append(proxy_name)
        for alias in _split_text_list(alias_values):
            alias_type = "ticker" if alias == node_id and re.fullmatch(r"[A-Z0-9.\-]+", node_id) else "alias"
            alias_rows.append(_base_alias_row(node_id=node_id, alias=alias, alias_type=alias_type))

    for edge in list(graph.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        source_node_id = _normalize_node_id(edge.get("source"))
        target_node_id = _normalize_node_id(edge.get("target"))
        relationship = _clean(edge.get("relationship") or "related_to")
        if not source_node_id or not target_node_id:
            continue
        lag = dict(edge.get("lag") or {}) if isinstance(edge.get("lag"), dict) else {}
        edge_rows.append(
            _base_edge_row(
                edge_id=_clean(edge.get("id")) or _edge_id(source_node_id, target_node_id, relationship),
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relationship=relationship,
                mechanism=_clean(edge.get("mechanism")),
                polarity=_clean(edge.get("polarity")),
                directness=_clean(edge.get("directness")),
                severity=edge.get("severity"),
                confidence=edge.get("confidence"),
                lag_value=lag.get("value"),
                lag_unit=_clean(lag.get("unit")),
                conditions=list(edge.get("conditions") or []),
                attributes=dict(edge.get("attributes") or {}),
                source_status="seeded",
            )
        )
    return node_rows, edge_rows, alias_rows


@lru_cache(maxsize=1)
def _baseline_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}
    aliases_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for path in sorted(KNOWLEDGE_GRAPH_SEED_DIR.glob("*.json")) if KNOWLEDGE_GRAPH_SEED_DIR.exists() else []:
        payload = _read_seed_graph_payload(path)
        if payload is None:
            continue
        node_rows, edge_rows, alias_rows = _records_from_custom_seed_payload(payload)
        for row in node_rows:
            existing = nodes_by_id.get(row["node_id"])
            nodes_by_id[row["node_id"]] = _merge_node_dict(existing, row) if existing else row
        for row in edge_rows:
            existing = edges_by_id.get(row["edge_id"])
            edges_by_id[row["edge_id"]] = _merge_edge_dict(existing, row) if existing else row
        for row in alias_rows:
            aliases_by_key[(row["node_id"], row["alias"].lower())] = row

    for payload in load_dependency_graph_payloads(tags=["commodities"]):
        node_rows, edge_rows, alias_rows = _records_from_dependency_graph_payload(payload)
        for row in node_rows:
            existing = nodes_by_id.get(row["node_id"])
            nodes_by_id[row["node_id"]] = _merge_node_dict(existing, row) if existing else row
        for row in edge_rows:
            existing = edges_by_id.get(row["edge_id"])
            edges_by_id[row["edge_id"]] = _merge_edge_dict(existing, row) if existing else row
        for row in alias_rows:
            aliases_by_key[(row["node_id"], row["alias"].lower())] = row

    return (
        list(nodes_by_id.values()),
        list(edges_by_id.values()),
        list(aliases_by_key.values()),
    )


def clear_knowledge_graph_cache() -> None:
    _baseline_records.cache_clear()
    _NODE_VECTOR_CACHE.clear()


def _load_db_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conn = _db_connect()
    if conn is None:
        return [], [], []
    schema = _schema_name()
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT node_id, canonical_label, node_type, description, status, attributes_json,
                       source_status, is_deleted
                FROM {schema}.knowledge_graph_nodes
                """
            )
            node_rows = _fetchall_dicts(cur)
            cur.execute(
                f"""
                SELECT edge_id, source_node_id, target_node_id, relationship, mechanism, polarity,
                       directness, severity, confidence, lag_value, lag_unit, conditions_json,
                       attributes_json, source_status, is_deleted
                FROM {schema}.knowledge_graph_edges
                """
            )
            edge_rows = _fetchall_dicts(cur)
            cur.execute(
                f"""
                SELECT alias_id, node_id, alias, alias_type
                FROM {schema}.knowledge_graph_aliases
                """
            )
            alias_rows = _fetchall_dicts(cur)
        return node_rows, edge_rows, alias_rows
    except Exception:
        return [], [], []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _assemble_snapshot(
    node_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = sorted(node_rows, key=lambda row: (str(row.get("canonical_label") or row.get("node_id") or "").lower(), str(row.get("node_id") or "")))
    nodes_by_id = {str(row.get("node_id") or ""): dict(row) for row in nodes if _clean(row.get("node_id"))}
    aliases_by_node: dict[str, list[dict[str, Any]]] = {}
    alias_lookup: dict[str, set[str]] = {}
    for row in alias_rows:
        node_id = _clean(row.get("node_id"))
        alias = _clean(row.get("alias"))
        if not node_id or not alias or node_id not in nodes_by_id:
            continue
        aliases_by_node.setdefault(node_id, []).append(dict(row))
        alias_lookup.setdefault(node_id, set()).add(alias)
    for node_id, node in nodes_by_id.items():
        node["aliases"] = sorted(alias_lookup.get(node_id, set()), key=str.lower)
    edges: list[dict[str, Any]] = []
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    for row in edge_rows:
        source_node_id = _clean(row.get("source_node_id"))
        target_node_id = _clean(row.get("target_node_id"))
        if source_node_id not in nodes_by_id or target_node_id not in nodes_by_id:
            continue
        edge = dict(row)
        edges.append(edge)
        adjacency.setdefault(source_node_id, set()).add(target_node_id)
        adjacency.setdefault(target_node_id, set()).add(source_node_id)
    edges = sorted(edges, key=lambda row: (str(row.get("source_node_id") or ""), str(row.get("target_node_id") or ""), str(row.get("relationship") or "")))
    return {
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "aliases": alias_rows,
        "nodes_by_id": nodes_by_id,
        "aliases_by_node": aliases_by_node,
        "adjacency": adjacency,
    }


def load_knowledge_graph_snapshot() -> dict[str, Any]:
    baseline_nodes, baseline_edges, baseline_aliases = _baseline_records()
    db_nodes, db_edges, db_aliases = _load_db_records()

    nodes_by_id = {str(row.get("node_id") or ""): dict(row) for row in baseline_nodes if _clean(row.get("node_id"))}
    deleted_node_ids: set[str] = set()
    for row in db_nodes:
        node_id = _clean(row.get("node_id"))
        if not node_id:
            continue
        if _coerce_bool(row.get("is_deleted")):
            deleted_node_ids.add(node_id)
            nodes_by_id.pop(node_id, None)
            continue
        existing = nodes_by_id.get(node_id)
        nodes_by_id[node_id] = _merge_node_dict(existing, dict(row)) if existing else dict(row)

    edges_by_id = {str(row.get("edge_id") or ""): dict(row) for row in baseline_edges if _clean(row.get("edge_id"))}
    deleted_edge_ids: set[str] = set()
    for row in db_edges:
        edge_id = _clean(row.get("edge_id"))
        if not edge_id:
            continue
        if _coerce_bool(row.get("is_deleted")):
            deleted_edge_ids.add(edge_id)
            edges_by_id.pop(edge_id, None)
            continue
        existing = edges_by_id.get(edge_id)
        edges_by_id[edge_id] = _merge_edge_dict(existing, dict(row)) if existing else dict(row)

    for edge_id in list(edges_by_id):
        row = edges_by_id[edge_id]
        if _clean(row.get("source_node_id")) in deleted_node_ids or _clean(row.get("target_node_id")) in deleted_node_ids:
            edges_by_id.pop(edge_id, None)

    baseline_aliases_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in baseline_aliases:
        node_id = _clean(row.get("node_id"))
        if not node_id or node_id in deleted_node_ids:
            continue
        baseline_aliases_by_node.setdefault(node_id, []).append(dict(row))

    db_aliases_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in db_aliases:
        node_id = _clean(row.get("node_id"))
        if not node_id or node_id in deleted_node_ids:
            continue
        db_aliases_by_node.setdefault(node_id, []).append(dict(row))

    final_alias_rows: list[dict[str, Any]] = []
    for node_id in nodes_by_id:
        chosen = db_aliases_by_node.get(node_id)
        if chosen is None:
            chosen = baseline_aliases_by_node.get(node_id, [])
        final_alias_rows.extend(chosen)

    return _assemble_snapshot(list(nodes_by_id.values()), list(edges_by_id.values()), final_alias_rows)


def knowledge_graph_runtime_status() -> dict[str, Any]:
    store_configured = knowledge_graph_store_configured()
    conn = _db_connect() if store_configured else None
    store_ready = False
    if conn is not None:
        try:
            _ensure_schema(conn)
            store_ready = True
        except Exception:
            store_ready = False
        finally:
            try:
                conn.close()
            except Exception:
                pass
    llm_client = load_llm_client()
    embedding_client = load_embedding_client()
    tavily_ready = load_tavily_config() is not None
    serp_ready = load_serpapi_config() is not None
    return {
        "store_configured": store_configured,
        "store_ready": store_ready,
        "llm_ready": llm_client is not None,
        "embedding_ready": embedding_client is not None,
        "web_search_ready": bool(tavily_ready or serp_ready),
        "tavily_ready": tavily_ready,
        "serp_ready": serp_ready,
    }


def _deterministic_alias_score(query: str, alias: str, node_id: str, label: str) -> float:
    query_clean = query.lower().strip()
    alias_clean = alias.lower().strip()
    node_clean = node_id.lower().strip()
    label_clean = label.lower().strip()
    if not query_clean or not alias_clean:
        return 0.0
    if query_clean == alias_clean:
        return 1.0
    if query_clean == node_clean:
        return 0.995
    if query_clean == label_clean:
        return 0.99

    query_tokens = _tokenize(query_clean)
    alias_tokens = _tokenize(alias_clean)
    label_tokens = _tokenize(label_clean)
    overlap_alias = len(query_tokens & alias_tokens) / max(len(query_tokens | alias_tokens), 1)
    overlap_label = len(query_tokens & label_tokens) / max(len(query_tokens | label_tokens), 1)
    partial = 0.0
    if query_clean in alias_clean or alias_clean in query_clean:
        partial = 0.78
    elif query_clean in label_clean or label_clean in query_clean:
        partial = 0.74
    seq = max(
        SequenceMatcher(None, query_clean, alias_clean).ratio(),
        SequenceMatcher(None, query_clean, label_clean).ratio(),
        SequenceMatcher(None, query_clean, node_clean).ratio(),
    )
    return float(max(partial, overlap_alias * 0.92, overlap_label * 0.88, seq * 0.65))


def _embedding_scores(query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
    embedding_client = load_embedding_client()
    if embedding_client is None or not candidates:
        return {}
    texts = [query] + [str(item.get("matched_alias") or item.get("canonical_label") or item.get("node_id") or "") for item in candidates]
    try:
        vectors = embedding_client.generate_embeddings(texts)
    except Exception:
        return {}
    if len(vectors) != len(texts):
        return {}
    query_vec = vectors[0]
    scores: dict[str, float] = {}
    for item, vector in zip(candidates, vectors[1:]):
        node_id = _clean(item.get("node_id"))
        if not node_id:
            continue
        scores[node_id] = max(scores.get(node_id, 0.0), (_cosine_similarity(query_vec, vector) + 1.0) / 2.0)
    return scores


# Cache: frozenset(node_ids) -> {node_id: embedding_vector}
_NODE_VECTOR_CACHE: dict[frozenset, dict[str, list[float]]] = {}


def _node_embedding_text(node: dict[str, Any]) -> str:
    """Rich text for a node: label + description + aliases. Used for semantic indexing."""
    label = _clean(node.get("canonical_label") or node.get("node_id"))
    description = _clean(node.get("description"))
    aliases = [a for a in (node.get("aliases") or []) if _clean(a)]
    parts = [label]
    if description:
        parts.append(description)
    if aliases:
        parts.append("Also known as: " + ", ".join(aliases[:8]))
    return ". ".join(parts)


def _get_node_vectors(nodes_by_id: dict[str, Any]) -> dict[str, list[float]]:
    """Batch-embed all nodes using rich text; result is cached by node-id set."""
    cache_key = frozenset(nodes_by_id.keys())
    if cache_key in _NODE_VECTOR_CACHE:
        return _NODE_VECTOR_CACHE[cache_key]
    embedding_client = load_embedding_client()
    if embedding_client is None or not nodes_by_id:
        return {}
    node_ids = list(nodes_by_id.keys())
    texts = [_node_embedding_text(nodes_by_id[nid]) for nid in node_ids]
    try:
        vectors = embedding_client.generate_embeddings(texts)
    except Exception:
        return {}
    if len(vectors) != len(node_ids):
        return {}
    result = dict(zip(node_ids, vectors))
    _NODE_VECTOR_CACHE[cache_key] = result
    return result


def search_knowledge_graph_nodes(
    query: str,
    *,
    limit: int = 8,
    snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_query = _clean(query)
    if not normalized_query:
        return []
    snapshot = snapshot or load_knowledge_graph_snapshot()
    nodes_by_id = dict(snapshot.get("nodes_by_id") or {})

    # Stage 1: deterministic string match across all node aliases
    candidate_rows: dict[str, dict[str, Any]] = {}
    for node_id, node in nodes_by_id.items():
        label = _clean(node.get("canonical_label") or node_id)
        aliases = list(node.get("aliases") or []) or [node_id, label]
        for alias in aliases:
            score = _deterministic_alias_score(normalized_query, alias, node_id, label)
            if score <= 0.15:
                continue
            existing = candidate_rows.get(node_id)
            row = {
                "node_id": node_id,
                "canonical_label": label,
                "node_type": _clean(node.get("node_type")),
                "description": _clean(node.get("description")),
                "matched_alias": _clean(alias),
                "score_deterministic": score,
            }
            if existing is None or score > _coerce_float(existing.get("score_deterministic")):
                candidate_rows[node_id] = row

    # Stage 2: semantic search across ALL nodes using pre-computed vectors.
    # This runs regardless of deterministic results, so novel queries (e.g. "fertilizer",
    # "coffee") can surface semantically relevant nodes even with no lexical overlap.
    embedding_scores: dict[str, float] = {}
    node_vectors = _get_node_vectors(nodes_by_id)
    if node_vectors:
        embedding_client = load_embedding_client()
        if embedding_client is not None:
            try:
                query_vecs = embedding_client.generate_embeddings([normalized_query])
                if query_vecs:
                    query_vec = query_vecs[0]
                    for node_id, node_vec in node_vectors.items():
                        embedding_scores[node_id] = (_cosine_similarity(query_vec, node_vec) + 1.0) / 2.0
            except Exception:
                pass

    # Stage 3: merge — union of deterministic hits and strong semantic hits
    SEMANTIC_ONLY_THRESHOLD = 0.60
    all_node_ids = set(candidate_rows) | (
        {nid for nid, s in embedding_scores.items() if s >= SEMANTIC_ONLY_THRESHOLD}
    )
    result_rows: dict[str, dict[str, Any]] = {}
    for node_id in all_node_ids:
        node = nodes_by_id.get(node_id, {})
        det_row = candidate_rows.get(node_id)
        det_score = _coerce_float(det_row.get("score_deterministic")) if det_row else 0.0
        emb_score = float(embedding_scores.get(node_id, 0.0))
        label = _clean(node.get("canonical_label") or node_id)

        if det_score >= 0.15:
            score = max(det_score, det_score * 0.7 + emb_score * 0.3)
        else:
            # Pure semantic hit — scale slightly below deterministic quality to prefer direct matches
            score = emb_score * 0.9

        result_rows[node_id] = {
            "node_id": node_id,
            "canonical_label": label,
            "node_type": _clean(node.get("node_type")),
            "description": _clean(node.get("description")),
            "matched_alias": det_row.get("matched_alias") if det_row else label,
            "score_deterministic": det_score,
            "score_embedding": emb_score,
            "score": score,
        }

    ranked = sorted(result_rows.values(), key=lambda r: (r["score"], r["score_deterministic"]), reverse=True)
    return ranked[: max(int(limit), 1)]


def _seed_selection_default(matches: list[dict[str, Any]]) -> list[str]:
    if not matches:
        return []
    top_score = _coerce_float(matches[0].get("score"))
    default_ids = [
        _clean(item.get("node_id"))
        for item in matches
        if _coerce_float(item.get("score")) >= max(top_score - 0.08, 0.78)
    ]
    return [item for item in default_ids[:3] if item]


def default_seed_node_ids_from_matches(matches: list[dict[str, Any]]) -> list[str]:
    return _seed_selection_default(matches)


def _collect_seed_neighborhood(
    seed_node_ids: list[str],
    snapshot: dict[str, Any],
    *,
    depth: int = 2,
    max_nodes: int = 18,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    adjacency = dict(snapshot.get("adjacency") or {})
    nodes_by_id = dict(snapshot.get("nodes_by_id") or {})
    frontier = list(seed_node_ids)
    seen = {node_id for node_id in seed_node_ids if node_id in nodes_by_id}
    all_nodes = list(seen)
    for _ in range(max(int(depth), 0)):
        next_frontier: list[str] = []
        for node_id in frontier:
            for neighbor in sorted(adjacency.get(node_id, set())):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                next_frontier.append(neighbor)
                all_nodes.append(neighbor)
                if len(all_nodes) >= max_nodes:
                    break
            if len(all_nodes) >= max_nodes:
                break
        frontier = next_frontier
        if not frontier or len(all_nodes) >= max_nodes:
            break
    node_set = set(all_nodes)
    nodes = [dict(nodes_by_id[node_id]) for node_id in all_nodes if node_id in nodes_by_id]
    edges = [
        dict(edge)
        for edge in list(snapshot.get("edges") or [])
        if _clean(edge.get("source_node_id")) in node_set and _clean(edge.get("target_node_id")) in node_set
    ]
    return nodes, edges


def _web_research_lines(query: str, seed_nodes: list[dict[str, Any]], *, limit: int = 4) -> list[str]:
    query_base = _clean(query)
    seed_labels = [str(node.get("canonical_label") or node.get("node_id") or "").strip() for node in seed_nodes if str(node.get("canonical_label") or node.get("node_id") or "").strip()]
    if seed_labels:
        query_base = f"{query_base} {' '.join(seed_labels[:2])} dependencies downstream uses supply chain"
    if not query_base:
        return []
    rows: list[str] = []
    tavily_config = load_tavily_config()
    if tavily_config is not None:
        try:
            client = TavilySearchClient(tavily_config)
            for item in client.search(query_base, max_results=max(limit, 2), topic="general"):
                title = _clean(item.title)
                snippet = _clean(item.snippet)
                source = _clean(item.source)
                if title or snippet:
                    rows.append(" | ".join(part for part in [title, source, snippet] if part))
        except WebResearchError:
            pass
    if rows:
        return rows[:limit]
    serp_config = load_serpapi_config()
    if serp_config is not None:
        try:
            client = SerpAPISearchClient(serp_config)
            for item in client.search(query_base, news=False, num=max(limit, 2)):
                title = _clean(item.title)
                snippet = _clean(item.snippet)
                source = _clean(item.source)
                if title or snippet:
                    rows.append(" | ".join(part for part in [title, source, snippet] if part))
        except WebResearchError:
            pass
    return rows[:limit]


_GRAPH_EXPANSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "nodes", "edges", "limitations"],
    "properties": {
        "summary": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "label", "node_type"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "node_type": {"type": "string"},
                    "description": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "source", "target", "relationship"],
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relationship": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "polarity": {"type": "string"},
                    "directness": {"type": "string"},
                    "severity": {"type": "number"},
                    "confidence": {"type": "number"},
                    "conditions": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def _agentic_graph_expansion(
    *,
    query: str,
    seed_nodes: list[dict[str, Any]],
    context_nodes: list[dict[str, Any]],
    context_edges: list[dict[str, Any]],
) -> dict[str, Any]:
    llm_client = load_llm_client()
    if llm_client is None:
        return {
            "summary": "",
            "nodes": [],
            "edges": [],
            "limitations": ["LLM runtime is unavailable, so no agentic graph suggestions were added."],
        }

    research_lines = _web_research_lines(query, seed_nodes)
    node_catalog_lines = [
        f"- id={_clean(node.get('node_id'))} | label={_clean(node.get('canonical_label'))} | type={_clean(node.get('node_type'))}"
        for node in context_nodes[:18]
    ]
    edge_catalog_lines = [
        f"- id={_clean(edge.get('edge_id'))} | {_clean(edge.get('source_node_id'))} -> {_clean(edge.get('target_node_id'))} | {_clean(edge.get('relationship'))} | {_clean(edge.get('mechanism'))}"
        for edge in context_edges[:20]
    ]
    seed_lines = [
        f"- id={_clean(node.get('node_id'))} | label={_clean(node.get('canonical_label'))} | type={_clean(node.get('node_type'))} | desc={_clean(node.get('description'))}"
        for node in seed_nodes
    ]
    prompt = (
        "You build compact dependency graph expansions for a market and supply-chain knowledge graph. "
        "Reuse existing node ids when the catalog already contains the concept. "
        "Only propose nodes and edges that are materially relevant to the query. "
        "Prefer 3 to 8 nodes and 3 to 10 edges. "
        "If you are unsure, return fewer items. "
        "For new nodes, create concise stable ids in snake_case. "
        "Return only JSON."
    )
    user_prompt = (
        f"Query: {query}\n\n"
        "Selected seed nodes:\n"
        + ("\n".join(seed_lines) if seed_lines else "- none")
        + "\n\nCurrent nearby node catalog:\n"
        + ("\n".join(node_catalog_lines) if node_catalog_lines else "- none")
        + "\n\nCurrent nearby edge catalog:\n"
        + ("\n".join(edge_catalog_lines) if edge_catalog_lines else "- none")
        + "\n\nOptional web research snippets:\n"
        + ("\n".join(f"- {line}" for line in research_lines) if research_lines else "- none")
        + "\n\nReturn a compact expansion around the seed query. "
        "Use existing ids when possible and add only the missing important neighbors."
    )
    try:
        return llm_client.generate_json(
            system_prompt=prompt,
            user_prompt=user_prompt,
            schema_name="knowledge_graph_expansion",
            schema=_GRAPH_EXPANSION_SCHEMA,
        )
    except LLMAPIError as exc:
        return {
            "summary": "",
            "nodes": [],
            "edges": [],
            "limitations": [f"LLM expansion failed: {exc}"],
        }


def _candidate_lookup(snapshot: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for node in list(snapshot.get("nodes") or []):
        node_id = _clean(node.get("node_id"))
        label = _clean(node.get("canonical_label"))
        if not node_id:
            continue
        lookup[node_id.lower()] = node_id
        if label:
            lookup[label.lower()] = node_id
        for alias in list(node.get("aliases") or []):
            clean_alias = _clean(alias)
            if clean_alias:
                lookup[clean_alias.lower()] = node_id
    return lookup


def _normalize_agentic_expansion(
    payload: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    selected_node_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    limitations = [_clean(item) for item in list(payload.get("limitations") or []) if _clean(item)]
    summary = _clean(payload.get("summary"))
    existing_nodes = dict(snapshot.get("nodes_by_id") or {})
    lookup = _candidate_lookup(snapshot)
    proposed_nodes: dict[str, dict[str, Any]] = {}
    existing_node_ids = set(existing_nodes)

    for item in list(payload.get("nodes") or []):
        if not isinstance(item, dict):
            continue
        raw_id = _clean(item.get("id"))
        label = _clean(item.get("label"))
        resolved_existing_id = lookup.get(raw_id.lower()) or lookup.get(label.lower())
        if resolved_existing_id and resolved_existing_id in existing_nodes:
            continue
        node_id = _normalize_node_id(raw_id or label)
        if not node_id:
            continue
        if node_id in existing_node_ids or node_id in proposed_nodes:
            node_id = f"{node_id}_{uuid.uuid4().hex[:6]}"
        proposed_nodes[node_id] = {
            "node_id": node_id,
            "canonical_label": label or node_id,
            "node_type": _clean(item.get("node_type") or "concept"),
            "description": _clean(item.get("description")),
            "status": "",
            "attributes_json": {
                "aliases": _split_text_list(item.get("aliases")),
                "rationale": _clean(item.get("rationale")),
            },
            "source_status": "agent_suggested",
            "confidence": _coerce_float(item.get("confidence"), default=0.55),
        }
        for alias in [label, *_split_text_list(item.get("aliases"))]:
            if _clean(alias):
                lookup[_clean(alias).lower()] = node_id

    proposed_edges: dict[str, dict[str, Any]] = {}
    for item in list(payload.get("edges") or []):
        if not isinstance(item, dict):
            continue
        raw_source = _clean(item.get("source"))
        raw_target = _clean(item.get("target"))
        relationship = _clean(item.get("relationship") or "related_to")
        source_node_id = lookup.get(raw_source.lower()) or _normalize_node_id(raw_source)
        target_node_id = lookup.get(raw_target.lower()) or _normalize_node_id(raw_target)
        if source_node_id not in existing_nodes and source_node_id not in proposed_nodes:
            continue
        if target_node_id not in existing_nodes and target_node_id not in proposed_nodes:
            continue
        edge_id = _clean(item.get("id")) or _edge_id(source_node_id, target_node_id, relationship)
        if edge_id in proposed_edges or any(_clean(edge.get("edge_id")) == edge_id for edge in list(snapshot.get("edges") or [])):
            edge_id = f"{edge_id}_{uuid.uuid4().hex[:6]}"
        proposed_edges[edge_id] = {
            "edge_id": edge_id,
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "relationship": relationship,
            "mechanism": _clean(item.get("mechanism")),
            "polarity": _clean(item.get("polarity")),
            "directness": _clean(item.get("directness")),
            "severity": _coerce_float(item.get("severity"), default=0.55),
            "confidence": _coerce_float(item.get("confidence"), default=0.55),
            "lag_value": None,
            "lag_unit": "",
            "conditions_json": _split_text_list(item.get("conditions")),
            "attributes_json": {"rationale": _clean(item.get("rationale"))},
            "source_status": "agent_suggested",
        }
    return list(proposed_nodes.values()), list(proposed_edges.values()), limitations, summary


def build_knowledge_graph_draft(
    query: str,
    *,
    selected_node_ids: list[str] | None = None,
    include_agentic_expansion: bool = True,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_query = _clean(query)
    snapshot = snapshot or load_knowledge_graph_snapshot()
    matches = search_knowledge_graph_nodes(normalized_query, snapshot=snapshot)
    selected = [_clean(node_id) for node_id in list(selected_node_ids or []) if _clean(node_id)]
    if not selected:
        selected = _seed_selection_default(matches)
    selected = [node_id for node_id in selected if node_id in dict(snapshot.get("nodes_by_id") or {})]

    context_seed_ids = list(selected)
    if not context_seed_ids and matches:
        context_seed_ids = [
            _clean(item.get("node_id"))
            for item in matches[:3]
            if _clean(item.get("node_id")) in dict(snapshot.get("nodes_by_id") or {})
        ]

    existing_nodes, existing_edges = _collect_seed_neighborhood(context_seed_ids, snapshot, depth=2, max_nodes=20)
    node_rows: list[dict[str, Any]] = []
    for row in existing_nodes:
        node_rows.append(
            {
                "keep": True,
                "node_id": _clean(row.get("node_id")),
                "label": _clean(row.get("canonical_label")),
                "node_type": _clean(row.get("node_type")),
                "description": _clean(row.get("description")),
                "status": _clean(row.get("status")),
                "aliases": ", ".join(_split_text_list(row.get("aliases"))),
                "source_status": _clean(row.get("source_status") or "seeded"),
                "confidence": "",
                "reason": "",
            }
        )
    edge_rows: list[dict[str, Any]] = []
    for row in existing_edges:
        edge_rows.append(
            {
                "keep": True,
                "edge_id": _clean(row.get("edge_id")),
                "source": _clean(row.get("source_node_id")),
                "target": _clean(row.get("target_node_id")),
                "relationship": _clean(row.get("relationship")),
                "mechanism": _clean(row.get("mechanism")),
                "polarity": _clean(row.get("polarity")),
                "directness": _clean(row.get("directness")),
                "severity": row.get("severity"),
                "confidence": row.get("confidence"),
                "conditions": ", ".join(_split_text_list(row.get("conditions_json"))),
                "source_status": _clean(row.get("source_status") or "seeded"),
                "reason": "",
            }
        )

    limitations: list[str] = []
    agentic_summary = ""
    if include_agentic_expansion and normalized_query:
        seed_nodes = [dict(snapshot.get("nodes_by_id", {}).get(node_id) or {}) for node_id in context_seed_ids]
        raw_expansion = _agentic_graph_expansion(
            query=normalized_query,
            seed_nodes=seed_nodes,
            context_nodes=existing_nodes,
            context_edges=existing_edges,
        )
        proposed_nodes, proposed_edges, agentic_limitations, agentic_summary = _normalize_agentic_expansion(
            raw_expansion,
            snapshot=snapshot,
            selected_node_ids=selected,
        )
        limitations.extend(agentic_limitations)
        for row in proposed_nodes:
            node_rows.append(
                {
                    "keep": True,
                    "node_id": _clean(row.get("node_id")),
                    "label": _clean(row.get("canonical_label")),
                    "node_type": _clean(row.get("node_type")),
                    "description": _clean(row.get("description")),
                    "status": _clean(row.get("status")),
                    "aliases": ", ".join(_split_text_list((row.get("attributes_json") or {}).get("aliases"))),
                    "source_status": _clean(row.get("source_status") or "agent_suggested"),
                    "confidence": row.get("confidence"),
                    "reason": _clean((row.get("attributes_json") or {}).get("rationale")),
                }
            )
        for row in proposed_edges:
            edge_rows.append(
                {
                    "keep": True,
                    "edge_id": _clean(row.get("edge_id")),
                    "source": _clean(row.get("source_node_id")),
                    "target": _clean(row.get("target_node_id")),
                    "relationship": _clean(row.get("relationship")),
                    "mechanism": _clean(row.get("mechanism")),
                    "polarity": _clean(row.get("polarity")),
                    "directness": _clean(row.get("directness")),
                    "severity": row.get("severity"),
                    "confidence": row.get("confidence"),
                    "conditions": ", ".join(_split_text_list(row.get("conditions_json"))),
                    "source_status": _clean(row.get("source_status") or "agent_suggested"),
                    "reason": _clean((row.get("attributes_json") or {}).get("rationale")),
                }
            )
    elif normalized_query and not selected:
        limitations.append("No current node matched strongly enough, so the draft uses agent suggestions without a committed neighborhood.")

    return {
        "query": normalized_query,
        "seed_matches": matches,
        "selected_node_ids": selected,
        "nodes": node_rows,
        "edges": edge_rows,
        "agentic_summary": agentic_summary,
        "limitations": limitations,
        "runtime_status": knowledge_graph_runtime_status(),
    }


def draft_nodes_frame(draft: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(list(draft.get("nodes") or []))
    if frame.empty:
        return pd.DataFrame(columns=["keep", "node_id", "label", "node_type", "description", "status", "aliases", "source_status", "confidence", "reason"])
    return frame[
        ["keep", "node_id", "label", "node_type", "description", "status", "aliases", "source_status", "confidence", "reason"]
    ].copy()


def draft_edges_frame(draft: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(list(draft.get("edges") or []))
    if frame.empty:
        return pd.DataFrame(columns=["keep", "edge_id", "source", "target", "relationship", "mechanism", "polarity", "directness", "severity", "confidence", "conditions", "source_status", "reason"])
    return frame[
        ["keep", "edge_id", "source", "target", "relationship", "mechanism", "polarity", "directness", "severity", "confidence", "conditions", "source_status", "reason"]
    ].copy()


def _normalize_review_tables(
    *,
    draft: dict[str, Any],
    nodes_frame: pd.DataFrame,
    edges_frame: pd.DataFrame,
) -> dict[str, Any]:
    normalized_nodes: dict[str, dict[str, Any]] = {}
    original_nodes = {str(item.get("node_id") or ""): dict(item) for item in list(draft.get("nodes") or []) if _clean(item.get("node_id"))}
    for row in nodes_frame.to_dict(orient="records") if isinstance(nodes_frame, pd.DataFrame) else []:
        if not _coerce_bool(row.get("keep"), default=True):
            continue
        label = _clean(row.get("label"))
        if not label:
            continue
        node_id = _normalize_node_id(row.get("node_id") or label)
        normalized_nodes[node_id] = {
            "node_id": node_id,
            "canonical_label": label,
            "node_type": _clean(row.get("node_type") or "concept"),
            "description": _clean(row.get("description")),
            "status": _clean(row.get("status")),
            "aliases": _split_text_list([node_id, label, row.get("aliases")]),
            "source_status": _clean(row.get("source_status") or "user_added"),
            "confidence": _coerce_float(row.get("confidence"), default=0.0) if _clean(row.get("confidence")) else None,
            "reason": _clean(row.get("reason")),
        }

    original_existing_node_ids = {
        node_id
        for node_id, row in original_nodes.items()
        if _clean(row.get("source_status")) in {"seeded", "committed"}
    }
    deleted_node_ids = sorted(original_existing_node_ids - set(normalized_nodes))

    normalized_edges: dict[str, dict[str, Any]] = {}
    original_edges = {str(item.get("edge_id") or ""): dict(item) for item in list(draft.get("edges") or []) if _clean(item.get("edge_id"))}
    for row in edges_frame.to_dict(orient="records") if isinstance(edges_frame, pd.DataFrame) else []:
        if not _coerce_bool(row.get("keep"), default=True):
            continue
        source = _normalize_node_id(row.get("source"))
        target = _normalize_node_id(row.get("target"))
        relationship = _clean(row.get("relationship"))
        if not source or not target or not relationship:
            continue
        if source not in normalized_nodes or target not in normalized_nodes:
            continue
        edge_id = _clean(row.get("edge_id")) or _edge_id(source, target, relationship)
        normalized_edges[edge_id] = {
            "edge_id": edge_id,
            "source_node_id": source,
            "target_node_id": target,
            "relationship": relationship,
            "mechanism": _clean(row.get("mechanism")),
            "polarity": _clean(row.get("polarity")),
            "directness": _clean(row.get("directness")),
            "severity": _coerce_float(row.get("severity"), default=0.0) if _clean(row.get("severity")) else None,
            "confidence": _coerce_float(row.get("confidence"), default=0.0) if _clean(row.get("confidence")) else None,
            "conditions_json": _split_text_list(row.get("conditions")),
            "source_status": _clean(row.get("source_status") or "user_added"),
            "reason": _clean(row.get("reason")),
        }

    original_existing_edge_ids = {
        edge_id
        for edge_id, row in original_edges.items()
        if _clean(row.get("source_status")) in {"seeded", "committed"}
    }
    deleted_edge_ids = sorted(original_existing_edge_ids - set(normalized_edges))
    return {
        "nodes": list(normalized_nodes.values()),
        "edges": list(normalized_edges.values()),
        "deleted_node_ids": deleted_node_ids,
        "deleted_edge_ids": deleted_edge_ids,
    }


def commit_knowledge_graph_review(
    *,
    query: str,
    draft: dict[str, Any],
    nodes_frame: pd.DataFrame,
    edges_frame: pd.DataFrame,
    summary: str,
    created_by: str,
) -> dict[str, Any]:
    conn = _db_connect()
    if conn is None:
        return {"ok": False, "message": "Knowledge graph store is unavailable. Commit requires a working Postgres connection."}
    schema = _schema_name()
    normalized = _normalize_review_tables(draft=draft, nodes_frame=nodes_frame, edges_frame=edges_frame)
    created_at = _now_utc()
    commit_id = str(uuid.uuid4())
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            for node in normalized["nodes"]:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.knowledge_graph_nodes (
                        node_id, canonical_label, node_type, description, status, attributes_json,
                        source_status, is_deleted, created_by, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, FALSE, %s, %s, %s)
                    ON CONFLICT (node_id) DO UPDATE SET
                        canonical_label = EXCLUDED.canonical_label,
                        node_type = EXCLUDED.node_type,
                        description = EXCLUDED.description,
                        status = EXCLUDED.status,
                        attributes_json = EXCLUDED.attributes_json,
                        source_status = EXCLUDED.source_status,
                        is_deleted = FALSE,
                        created_by = EXCLUDED.created_by,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        node["node_id"],
                        node["canonical_label"],
                        node["node_type"],
                        node["description"],
                        node["status"] or None,
                        _json_dumps(
                            {
                                "aliases": node["aliases"],
                                "review_reason": node["reason"],
                            }
                        ),
                        "committed",
                        created_by or None,
                        created_at,
                        created_at,
                    ),
                )
                cur.execute(
                    f"DELETE FROM {schema}.knowledge_graph_aliases WHERE node_id = %s",
                    (node["node_id"],),
                )
                for alias in node["aliases"]:
                    cur.execute(
                        f"""
                        INSERT INTO {schema}.knowledge_graph_aliases (
                            alias_id, node_id, alias, alias_type, created_by, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (alias_id) DO UPDATE SET
                            alias = EXCLUDED.alias,
                            alias_type = EXCLUDED.alias_type,
                            created_by = EXCLUDED.created_by,
                            created_at = EXCLUDED.created_at
                        """,
                        (
                            f"{node['node_id']}::{_slug(alias, fallback='alias')}",
                            node["node_id"],
                            alias,
                            "alias",
                            created_by or None,
                            created_at,
                        ),
                    )
            for node_id in normalized["deleted_node_ids"]:
                original = next((row for row in list(draft.get("nodes") or []) if _clean(row.get("node_id")) == node_id), {})
                cur.execute(
                    f"""
                    INSERT INTO {schema}.knowledge_graph_nodes (
                        node_id, canonical_label, node_type, description, status, attributes_json,
                        source_status, is_deleted, created_by, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, TRUE, %s, %s, %s)
                    ON CONFLICT (node_id) DO UPDATE SET
                        canonical_label = EXCLUDED.canonical_label,
                        node_type = EXCLUDED.node_type,
                        description = EXCLUDED.description,
                        status = EXCLUDED.status,
                        attributes_json = EXCLUDED.attributes_json,
                        source_status = EXCLUDED.source_status,
                        is_deleted = TRUE,
                        created_by = EXCLUDED.created_by,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        node_id,
                        _clean(original.get("label") or node_id),
                        _clean(original.get("node_type") or "concept"),
                        _clean(original.get("description")),
                        _clean(original.get("status")) or None,
                        _json_dumps({"suppressed": True}),
                        "committed",
                        created_by or None,
                        created_at,
                        created_at,
                    ),
                )
            for edge in normalized["edges"]:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.knowledge_graph_edges (
                        edge_id, source_node_id, target_node_id, relationship, mechanism, polarity,
                        directness, severity, confidence, lag_value, lag_unit, conditions_json,
                        attributes_json, source_status, is_deleted, created_by, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, FALSE, %s, %s, %s)
                    ON CONFLICT (edge_id) DO UPDATE SET
                        source_node_id = EXCLUDED.source_node_id,
                        target_node_id = EXCLUDED.target_node_id,
                        relationship = EXCLUDED.relationship,
                        mechanism = EXCLUDED.mechanism,
                        polarity = EXCLUDED.polarity,
                        directness = EXCLUDED.directness,
                        severity = EXCLUDED.severity,
                        confidence = EXCLUDED.confidence,
                        lag_value = EXCLUDED.lag_value,
                        lag_unit = EXCLUDED.lag_unit,
                        conditions_json = EXCLUDED.conditions_json,
                        attributes_json = EXCLUDED.attributes_json,
                        source_status = EXCLUDED.source_status,
                        is_deleted = FALSE,
                        created_by = EXCLUDED.created_by,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        edge["edge_id"],
                        edge["source_node_id"],
                        edge["target_node_id"],
                        edge["relationship"],
                        edge["mechanism"] or None,
                        edge["polarity"] or None,
                        edge["directness"] or None,
                        edge["severity"],
                        edge["confidence"],
                        None,
                        None,
                        _json_dumps(edge["conditions_json"]),
                        _json_dumps({"review_reason": edge["reason"]}),
                        "committed",
                        created_by or None,
                        created_at,
                        created_at,
                    ),
                )
            for edge_id in normalized["deleted_edge_ids"]:
                original = next((row for row in list(draft.get("edges") or []) if _clean(row.get("edge_id")) == edge_id), {})
                cur.execute(
                    f"""
                    INSERT INTO {schema}.knowledge_graph_edges (
                        edge_id, source_node_id, target_node_id, relationship, mechanism, polarity,
                        directness, severity, confidence, lag_value, lag_unit, conditions_json,
                        attributes_json, source_status, is_deleted, created_by, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, TRUE, %s, %s, %s)
                    ON CONFLICT (edge_id) DO UPDATE SET
                        source_node_id = EXCLUDED.source_node_id,
                        target_node_id = EXCLUDED.target_node_id,
                        relationship = EXCLUDED.relationship,
                        mechanism = EXCLUDED.mechanism,
                        polarity = EXCLUDED.polarity,
                        directness = EXCLUDED.directness,
                        severity = EXCLUDED.severity,
                        confidence = EXCLUDED.confidence,
                        lag_value = EXCLUDED.lag_value,
                        lag_unit = EXCLUDED.lag_unit,
                        conditions_json = EXCLUDED.conditions_json,
                        attributes_json = EXCLUDED.attributes_json,
                        source_status = EXCLUDED.source_status,
                        is_deleted = TRUE,
                        created_by = EXCLUDED.created_by,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        edge_id,
                        _normalize_node_id(original.get("source")),
                        _normalize_node_id(original.get("target")),
                        _clean(original.get("relationship") or "related_to"),
                        _clean(original.get("mechanism")) or None,
                        _clean(original.get("polarity")) or None,
                        _clean(original.get("directness")) or None,
                        _coerce_float(original.get("severity"), default=0.0) if _clean(original.get("severity")) else None,
                        _coerce_float(original.get("confidence"), default=0.0) if _clean(original.get("confidence")) else None,
                        None,
                        None,
                        _json_dumps(_split_text_list(original.get("conditions"))),
                        _json_dumps({"suppressed": True}),
                        "committed",
                        created_by or None,
                        created_at,
                        created_at,
                    ),
                )
            cur.execute(
                f"""
                INSERT INTO {schema}.knowledge_graph_commits (
                    commit_id, query_text, summary, created_by, created_at, draft_payload_json, applied_delta_json
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    commit_id,
                    _clean(query),
                    _clean(summary) or None,
                    created_by or None,
                    created_at,
                    _json_dumps(draft),
                    _json_dumps(normalized),
                ),
            )
        conn.commit()
        return {
            "ok": True,
            "commit_id": commit_id,
            "message": (
                f"Committed {len(normalized['nodes'])} nodes, {len(normalized['edges'])} edges, "
                f"{len(normalized['deleted_node_ids'])} node deletions, and {len(normalized['deleted_edge_ids'])} edge deletions."
            ),
            "delta": normalized,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_recent_knowledge_graph_commits(*, limit: int = 8) -> pd.DataFrame:
    conn = _db_connect()
    if conn is None:
        return pd.DataFrame(columns=["commit_id", "query_text", "summary", "created_by", "created_at"])
    schema = _schema_name()
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT commit_id, query_text, summary, created_by, created_at
                FROM {schema}.knowledge_graph_commits
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (max(int(limit), 1),),
            )
            rows = _fetchall_dicts(cur)
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["commit_id", "query_text", "summary", "created_by", "created_at"])
    finally:
        try:
            conn.close()
        except Exception:
            pass


def plot_knowledge_graph_draft(draft: dict[str, Any]) -> go.Figure:
    nodes = draft_nodes_frame(draft)
    edges = draft_edges_frame(draft)
    fig = go.Figure()
    if nodes.empty:
        fig.update_layout(template="plotly_dark", title="Knowledge Graph Draft", height=520)
        return fig

    graph = nx.Graph()
    selected_ids = {str(value).strip() for value in list(draft.get("selected_node_ids") or []) if str(value).strip()}
    node_lookup: dict[str, dict[str, Any]] = {}
    for row in nodes.to_dict(orient="records"):
        if not _coerce_bool(row.get("keep"), default=True):
            continue
        node_id = _clean(row.get("node_id"))
        if not node_id:
            continue
        node_lookup[node_id] = dict(row)
        graph.add_node(node_id)

    for row in edges.to_dict(orient="records"):
        if not _coerce_bool(row.get("keep"), default=True):
            continue
        source = _clean(row.get("source"))
        target = _clean(row.get("target"))
        if source not in node_lookup or target not in node_lookup:
            continue
        graph.add_edge(source, target, source_status=_clean(row.get("source_status") or "seeded"))

    positions = nx.spring_layout(graph, seed=11, k=max(0.8, 1.8 / max(math.sqrt(max(graph.number_of_nodes(), 1)), 1.0))) if graph.number_of_nodes() > 1 else {next(iter(graph.nodes)): (0.0, 0.0)} if graph.number_of_nodes() == 1 else {}

    edge_groups = {
        "seeded": {"color": "rgba(125, 211, 252, 0.45)", "width": 1.6},
        "committed": {"color": "rgba(56, 189, 248, 0.75)", "width": 2.4},
        "agent_suggested": {"color": "rgba(251, 191, 36, 0.8)", "width": 2.2},
        "user_added": {"color": "rgba(248, 113, 113, 0.82)", "width": 2.2},
    }
    for group, style in edge_groups.items():
        x: list[float | None] = []
        y: list[float | None] = []
        for left, right, attrs in graph.edges(data=True):
            if _clean(attrs.get("source_status") or "seeded") != group:
                continue
            x0, y0 = positions[left]
            x1, y1 = positions[right]
            x.extend([x0, x1, None])
            y.extend([y0, y1, None])
        if x:
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    mode="lines",
                    hoverinfo="none",
                    line=dict(color=style["color"], width=style["width"]),
                    name=group.replace("_", " ").title(),
                    showlegend=True,
                )
            )

    color_map = {
        "seeded": "#38bdf8",
        "committed": "#0ea5e9",
        "agent_suggested": "#f59e0b",
        "user_added": "#ef4444",
    }
    x: list[float] = []
    y: list[float] = []
    text: list[str] = []
    hover: list[str] = []
    color: list[str] = []
    size: list[float] = []
    for node_id, row in node_lookup.items():
        if node_id not in positions:
            continue
        px, py = positions[node_id]
        x.append(px)
        y.append(py)
        label = _clean(row.get("label") or node_id)
        node_type = _clean(row.get("node_type"))
        source_status = _clean(row.get("source_status") or "seeded")
        text.append(label)
        color.append(color_map.get(source_status, "#94a3b8"))
        size.append(28.0 if node_id in selected_ids else 22.0)
        hover.append(
            "<br>".join(
                part
                for part in [
                    f"<b>{label}</b>",
                    f"id={node_id}",
                    f"type={node_type}" if node_type else "",
                    f"source={source_status}" if source_status else "",
                    _clean(row.get("description")),
                    f"reason={_clean(row.get('reason'))}" if _clean(row.get("reason")) else "",
                ]
                if part
            )
        )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="markers+text",
            text=text,
            textposition="top center",
            hovertext=hover,
            hoverinfo="text",
            marker=dict(size=size, color=color, line=dict(width=1.2, color="#0f172a")),
            name="Nodes",
            showlegend=False,
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title="Knowledge Graph Draft",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        height=520,
        margin=dict(l=20, r=20, t=48, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0.0),
    )
    return fig


__all__ = [
    "build_knowledge_graph_draft",
    "clear_knowledge_graph_cache",
    "commit_knowledge_graph_review",
    "default_seed_node_ids_from_matches",
    "draft_edges_frame",
    "draft_nodes_frame",
    "knowledge_graph_runtime_status",
    "knowledge_graph_store_configured",
    "list_recent_knowledge_graph_commits",
    "load_knowledge_graph_snapshot",
    "plot_knowledge_graph_draft",
    "search_knowledge_graph_nodes",
]
