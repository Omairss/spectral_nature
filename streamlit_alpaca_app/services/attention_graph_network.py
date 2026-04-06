from __future__ import annotations

from collections import defaultdict
import json
import math
import re
from typing import Any

import networkx as nx
import pandas as pd
import plotly.graph_objects as go


STREAMLIT_DARK = {
    "paper": "#0e1117",
    "panel": "#111827",
    "grid": "#1f2937",
    "text": "#e5e7eb",
    "muted": "#94a3b8",
    "edge": "#cbd5e1",
    "outline": "#0f172a",
}


SECTOR_COLORS = {
    "Bridge Concepts": "#94a3b8",
    "Path Nodes": "#94a3b8",
    "Communication Services": "#2dd4bf",
    "Consumer Discretionary": "#60a5fa",
    "Consumer Staples": "#818cf8",
    "Energy": "#f59e0b",
    "Financials": "#c084fc",
    "Health Care": "#fb7185",
    "Industrials": "#f97316",
    "Materials": "#34d399",
    "Real Estate": "#f472b6",
    "Utilities": "#22d3ee",
    "Information Technology": "#38bdf8",
    "Unknown": "#94a3b8",
    "Unclassified": "#94a3b8",
}


BRIDGE_STOPWORDS = {
    "and",
    "company",
    "companies",
    "diversified",
    "for",
    "group",
    "holding",
    "holdings",
    "other",
    "service",
    "services",
    "with",
}


COMPANY_SUFFIXES = {
    "co",
    "co.",
    "company",
    "corp",
    "corp.",
    "corporation",
    "group",
    "holding",
    "holdings",
    "inc",
    "inc.",
    "limited",
    "ltd",
    "ltd.",
    "plc",
}


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _norm_symbol(value: object) -> str:
    return _text(value).upper()


def _float(value: object, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _listify(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _first_informative(*values: object, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text and text.lower() != "unknown":
            return text
    return default


def _direction_symbol(direction: object, change_pct: object) -> str:
    text = _text(direction).lower()
    if text == "bridge":
        return "diamond"
    if text == "path":
        return "circle-open"
    change = _float(change_pct)
    if text == "up" or change > 0:
        return "triangle-up"
    if text == "down" or change < 0:
        return "triangle-down"
    return "circle"


def build_attention_candidate_network(
    candidate_frame: pd.DataFrame,
    edge_frame: pd.DataFrame,
) -> nx.Graph:
    graph = nx.Graph()
    candidates = candidate_frame.copy() if isinstance(candidate_frame, pd.DataFrame) else pd.DataFrame()
    edges = edge_frame.copy() if isinstance(edge_frame, pd.DataFrame) else pd.DataFrame()

    for _, row in candidates.iterrows():
        symbol = _norm_symbol(row.get("symbol") or row.get("symbol_upper"))
        if not symbol:
            continue
        sector = _first_informative(
            row.get("effective_sector"),
            row.get("taxonomy_sector"),
            row.get("sector"),
            default="Unknown",
        )
        industry = _first_informative(
            row.get("effective_industry"),
            row.get("taxonomy_industry"),
            row.get("industry"),
            default="Unknown",
        )
        peer_group = _first_informative(
            row.get("effective_peer_group_id"),
            row.get("taxonomy_peer_group_id"),
            row.get("peer_group_id"),
            row.get("peer_group_name"),
            default=industry,
        )
        graph.add_node(
            symbol,
            symbol=symbol,
            node_type="security",
            sector=sector,
            industry=industry,
            peer_group=peer_group,
            direction=_text(row.get("direction")) or ("up" if _float(row.get("change_pct")) > 0 else "down"),
            change_pct=_float(row.get("change_pct")),
            candidate_score=_float(row.get("candidate_score")),
            attention_score=_float(row.get("attention_score")),
            headline=_text(row.get("headline")),
            source_label=_text(row.get("source_label")),
            security_name=_text(row.get("security_name")),
            macro_role_tags=_listify(row.get("taxonomy_macro_role_tags")) or _listify(row.get("macro_role_tags")),
            business_role_tags=_listify(row.get("taxonomy_business_role_tags")) or _listify(row.get("business_role_tags")),
            macro_exposure_tags=_listify(row.get("macro_exposure_tags")),
            business_tags=_listify(row.get("business_tags")),
        )

    for _, row in edges.iterrows():
        left = _norm_symbol(row.get("left_symbol"))
        right = _norm_symbol(row.get("right_symbol"))
        if not left or not right or left == right:
            continue
        if left not in graph:
            graph.add_node(left, symbol=left, node_type="security", sector="Unknown", industry="Unknown", peer_group="Unknown", direction="flat", change_pct=0.0, candidate_score=0.0)
        if right not in graph:
            graph.add_node(right, symbol=right, node_type="security", sector="Unknown", industry="Unknown", peer_group="Unknown", direction="flat", change_pct=0.0, candidate_score=0.0)
        reasons = _listify(row.get("edge_reasons")) or _listify(row.get("edge_reasons_json"))
        graph.add_edge(
            left,
            right,
            edge_weight=_float(row.get("edge_weight")),
            edge_reasons=reasons,
            edge_reason_text=", ".join(str(item) for item in reasons if _text(item)),
        )
    return graph


def _normalized_bridge_token(value: object) -> str:
    token = _text(value).lower()
    if len(token) < 4:
        return ""
    if token.endswith("s") and len(token) > 5:
        token = token[:-1]
    return "" if token in BRIDGE_STOPWORDS else token


def _sector_stop_tokens() -> set[str]:
    tokens: set[str] = set()
    for sector in SECTOR_COLORS:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", sector.lower()):
            normalized = _normalized_bridge_token(token)
            if normalized:
                tokens.add(normalized)
    return tokens


def _bridge_concepts_for_node(attrs: dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    phrases = [
        attrs.get("industry"),
        attrs.get("peer_group"),
    ]
    for phrase in phrases:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", _text(phrase).lower()):
            normalized = _normalized_bridge_token(token)
            if normalized:
                concepts.add(normalized)
    for field in ("macro_role_tags", "business_role_tags", "macro_exposure_tags", "business_tags"):
        for item in _listify(attrs.get(field)):
            for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", _text(item).replace("_", " ").lower()):
                normalized = _normalized_bridge_token(token)
                if normalized:
                    concepts.add(normalized)
    return concepts


def _bridge_edge_weight(*, support_count: int, component_span: int) -> float:
    specificity = 1.0 / max(math.sqrt(max(support_count, 1)), 1.0)
    span_bonus = min(max(component_span - 1, 0), 4) * 0.12
    return round(min(0.92, 0.34 + (specificity * 0.34) + span_bonus), 3)


def _bridge_label(token: str) -> str:
    parts = [part for part in token.split() if part]
    if not parts:
        return "Bridge"
    return " ".join(part.capitalize() for part in parts)


def _topology_edge_reason(edge_type: object) -> str:
    text = _text(edge_type) or "path"
    return f"topology_{text}"


def _topology_plot_weight(edge_type: object) -> float:
    text = _text(edge_type)
    if text == "event":
        return 0.44
    if text == "tag":
        return 0.34
    if text == "membership":
        return 0.30
    if text == "taxonomy":
        return 0.26
    return 0.28


def _path_node_attrs(node: str, attrs: dict[str, Any]) -> dict[str, Any]:
    topology_type = _text(attrs.get("node_type")) or "path"
    label = _text(attrs.get("label")) or node
    actual_sector = _text(attrs.get("sector")) or "Unknown"
    actual_peer_group = _text(attrs.get("peer_group")) or ""
    actual_industry = _text(attrs.get("industry")) or ""
    if topology_type == "symbol":
        return {
            "symbol": label,
            "node_type": "security",
            "sector": "Path Nodes",
            "industry": actual_industry or "Intermediate Symbol",
            "peer_group": actual_peer_group or actual_industry or label,
            "direction": "path",
            "change_pct": _float(attrs.get("change_pct")),
            "candidate_score": max(16.0, _float(attrs.get("candidate_score"), 0.0)),
            "attention_score": 0.0,
            "headline": f"Intermediate symbol from topology path",
            "source_label": "intermediate path",
            "security_name": label,
            "topology_node_type": topology_type,
            "topology_sector": actual_sector,
            "topology_peer_group": actual_peer_group,
            "topology_industry": actual_industry,
            "is_intermediate_path": True,
        }
    return {
        "symbol": label,
        "node_type": "path",
        "sector": "Path Nodes",
        "industry": label,
        "peer_group": label,
        "direction": "path",
        "change_pct": 0.0,
        "candidate_score": 18.0,
        "attention_score": 0.0,
        "headline": f"Intermediate {topology_type} node from topology path",
        "source_label": "intermediate path",
        "security_name": label,
        "topology_node_type": topology_type,
        "topology_sector": actual_sector,
        "topology_peer_group": actual_peer_group,
        "topology_industry": actual_industry,
        "is_intermediate_path": True,
    }


def expand_network_real_paths(
    graph: nx.Graph,
    topology_graph: nx.Graph,
    *,
    max_path_length: int = 8,
    max_paths: int | None = None,
) -> nx.Graph:
    expanded = graph.copy()
    if graph.number_of_nodes() == 0 or topology_graph.number_of_nodes() == 0:
        return expanded

    candidate_nodes = [
        node
        for node, attrs in graph.nodes(data=True)
        if not bool(attrs.get("is_intermediate_path"))
        and _text(attrs.get("node_type")) != "bridge"
        and int(graph.degree(node)) > 0
    ]
    if not candidate_nodes:
        return expanded

    component_graph = graph.subgraph(candidate_nodes).copy()
    components = [
        sorted(nodes)
        for nodes in nx.connected_components(component_graph)
        if nodes
    ]
    if len(components) <= 1:
        return expanded

    pair_candidates: list[dict[str, Any]] = []
    for left_index, left_nodes in enumerate(components):
        sources = [node for node in left_nodes if node in topology_graph]
        if not sources:
            continue
        try:
            distances, paths = nx.multi_source_dijkstra(topology_graph, sources, weight=None)
        except Exception:
            continue
        for right_index in range(left_index + 1, len(components)):
            targets = [node for node in components[right_index] if node in distances]
            if not targets:
                continue
            best_target = min(targets, key=lambda node: (distances[node], node))
            path = list(paths.get(best_target, []))
            if len(path) <= 2:
                continue
            hop_count = len(path) - 1
            if hop_count > max(int(max_path_length), 1):
                continue
            pair_candidates.append(
                {
                    "left_component": left_index,
                    "right_component": right_index,
                    "hop_count": hop_count,
                    "path": path,
                }
            )

    if not pair_candidates:
        return expanded

    parent = {index: index for index in range(len(components))}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> bool:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return False
        parent[right_root] = left_root
        return True

    selected_paths: list[list[str]] = []
    path_limit = max_paths if max_paths is not None else max(len(components) - 1, 0)
    for item in sorted(pair_candidates, key=lambda entry: (int(entry["hop_count"]), len(entry["path"]))):
        if len(selected_paths) >= max(int(path_limit), 0):
            break
        if not union(int(item["left_component"]), int(item["right_component"])):
            continue
        selected_paths.append(list(item["path"]))

    for path in selected_paths:
        for node in path:
            if node in expanded:
                continue
            if node not in topology_graph:
                continue
            expanded.add_node(node, **_path_node_attrs(str(node), dict(topology_graph.nodes[node])))
        for left, right in zip(path, path[1:]):
            if expanded.has_edge(left, right):
                continue
            if not topology_graph.has_edge(left, right):
                continue
            edge_attrs = dict(topology_graph.edges[left, right])
            edge_type = _text(edge_attrs.get("edge_type")) or "path"
            expanded.add_edge(
                left,
                right,
                edge_weight=_topology_plot_weight(edge_type),
                edge_reasons=[_topology_edge_reason(edge_type)],
                edge_reason_text=f"topology path ({edge_type})",
                is_intermediate_path=True,
            )
    return expanded


def expand_network_bridge_nodes(
    graph: nx.Graph,
    *,
    min_component_span: int = 2,
    min_security_support: int = 2,
    max_bridge_nodes: int = 12,
    max_support_share: float = 0.45,
) -> nx.Graph:
    expanded = graph.copy()
    security_nodes = [
        node
        for node, attrs in expanded.nodes(data=True)
        if _text(attrs.get("node_type")) != "bridge"
    ]
    if not security_nodes:
        return expanded

    security_graph = expanded.subgraph(security_nodes).copy()
    component_ids: dict[str, int] = {}
    for index, nodes in enumerate(nx.connected_components(security_graph), start=1):
        for node in nodes:
            component_ids[node] = index

    generic_tokens = _sector_stop_tokens()
    concept_members: dict[str, set[str]] = defaultdict(set)
    for node, attrs in security_graph.nodes(data=True):
        for concept in _bridge_concepts_for_node(attrs):
            if concept in generic_tokens:
                continue
            concept_members[concept].add(node)

    bridge_specs: list[dict[str, Any]] = []
    total_security_nodes = max(len(security_nodes), 1)
    for concept, members in concept_members.items():
        support_count = len(members)
        if support_count < max(int(min_security_support), 1):
            continue
        if (support_count / total_security_nodes) > float(max_support_share):
            continue
        component_span = len({component_ids[node] for node in members if node in component_ids})
        if component_span < max(int(min_component_span), 1):
            continue
        member_scores = [_float(security_graph.nodes[node].get("candidate_score")) for node in members]
        avg_score = sum(member_scores) / len(member_scores) if member_scores else 0.0
        bridge_specs.append(
            {
                "concept": concept,
                "members": sorted(members),
                "support_count": support_count,
                "component_span": component_span,
                "avg_score": avg_score,
            }
        )

    bridge_specs = sorted(
        bridge_specs,
        key=lambda item: (
            -int(item["component_span"]),
            -int(item["support_count"]),
            -float(item["avg_score"]),
            str(item["concept"]),
        ),
    )[: max(int(max_bridge_nodes), 0)]

    for spec in bridge_specs:
        concept = str(spec["concept"])
        label = _bridge_label(concept)
        node_id = f"bridge::{concept}"
        members = list(spec["members"])
        support_count = int(spec["support_count"])
        component_span = int(spec["component_span"])
        avg_score = float(spec["avg_score"])
        expanded.add_node(
            node_id,
            symbol=label,
            node_type="bridge",
            sector="Bridge Concepts",
            industry=label,
            peer_group=label,
            direction="bridge",
            change_pct=0.0,
            candidate_score=max(24.0, (avg_score * 0.35) + component_span * 4.0),
            attention_score=float(component_span * support_count),
            headline=f"Shared concept across {component_span} components and {support_count} symbols",
            source_label="bridge concept",
            security_name=label,
            bridge_concept=concept,
            bridge_support_count=support_count,
            bridge_component_count=component_span,
            bridge_members=members,
        )
        edge_weight = _bridge_edge_weight(
            support_count=support_count,
            component_span=component_span,
        )
        for member in members:
            if expanded.has_edge(node_id, member):
                continue
            expanded.add_edge(
                node_id,
                member,
                edge_weight=edge_weight,
                edge_reasons=["bridge_concept"],
                edge_reason_text=f"bridge concept={label}",
            )
    return expanded


def network_graph_summary(graph: nx.Graph) -> dict[str, int]:
    connected_nodes = [node for node, degree in graph.degree() if int(degree) > 0]
    connected_subgraph = graph.subgraph(connected_nodes).copy()
    return {
        "candidate_nodes": int(graph.number_of_nodes()),
        "graph_edges": int(graph.number_of_edges()),
        "connected_nodes": int(len(connected_nodes)),
        "isolated_nodes": int(graph.number_of_nodes() - len(connected_nodes)),
        "connected_components": int(nx.number_connected_components(connected_subgraph) if connected_subgraph.number_of_nodes() else 0),
        "largest_component_nodes": int(max((len(nodes) for nodes in nx.connected_components(connected_subgraph)), default=0)),
    }


def connected_candidate_subgraph(graph: nx.Graph) -> nx.Graph:
    nodes = [node for node, degree in graph.degree() if int(degree) > 0]
    return graph.subgraph(nodes).copy()


def build_homepage_attention_graph_payload(
    candidate_frame: pd.DataFrame,
    edge_frame: pd.DataFrame,
    cluster_frame: pd.DataFrame | None = None,
    *,
    universe_frame: pd.DataFrame | None = None,
    height: int = 320,
    seed: int = 7,
    label_top_n: int = 12,
) -> dict[str, Any]:
    if not isinstance(candidate_frame, pd.DataFrame) or candidate_frame.empty:
        return {}
    if not isinstance(edge_frame, pd.DataFrame) or edge_frame.empty:
        return {}

    from .attention_graph_topology import build_attention_topology_graph

    candidate_network = build_attention_candidate_network(candidate_frame, edge_frame)
    if candidate_network.number_of_edges() == 0:
        return {}
    backbone = build_network_backbone(candidate_network)
    topology_graph = build_attention_topology_graph(
        candidate_frame,
        cluster_frame,
        universe_frame=universe_frame,
    )
    plot_graph = expand_network_real_paths(backbone, topology_graph)
    display_graph = connected_candidate_subgraph(plot_graph)
    if display_graph.number_of_nodes() == 0 or display_graph.number_of_edges() == 0:
        return {}

    fig = plot_attention_candidate_network(
        display_graph,
        title="",
        height=height,
        seed=seed,
        label_top_n=label_top_n,
        show_isolates=False,
        show_legend=False,
        show_summary=False,
        show_footer=False,
        compact=True,
        show_component_labels=True,
    )
    return {
        "figure": fig.to_plotly_json(),
        "summary": network_graph_summary(display_graph),
        "height": int(height),
        "label_top_n": int(label_top_n),
        "seed": int(seed),
    }


def build_network_backbone(
    graph: nx.Graph,
    *,
    per_node_k: int = 2,
    keep_quantile: float = 0.8,
) -> nx.Graph:
    backbone = nx.Graph()
    backbone.add_nodes_from(graph.nodes(data=True))

    for nodes in nx.connected_components(graph):
        subgraph = graph.subgraph(nodes).copy()
        if subgraph.number_of_edges() == 0:
            continue

        weights = pd.Series([_float(attrs.get("edge_weight")) for _, _, attrs in subgraph.edges(data=True)])
        cutoff = float(weights.quantile(float(keep_quantile))) if len(weights) else 0.0
        keep_edges: set[tuple[str, str]] = set()

        for left, right, attrs in subgraph.edges(data=True):
            if _float(attrs.get("edge_weight")) >= cutoff:
                keep_edges.add(tuple(sorted((left, right))))

        max_edges = max(int(per_node_k), 0)
        if max_edges > 0:
            for node in subgraph.nodes():
                ranked = sorted(
                    [
                        (left, right, _float(attrs.get("edge_weight")))
                        for left, right, attrs in subgraph.edges(node, data=True)
                    ],
                    key=lambda item: item[2],
                    reverse=True,
                )
                for left, right, _ in ranked[:max_edges]:
                    keep_edges.add(tuple(sorted((left, right))))

        tree = nx.maximum_spanning_tree(subgraph, weight="edge_weight")
        for left, right in tree.edges():
            keep_edges.add(tuple(sorted((left, right))))

        for left, right in sorted(keep_edges):
            if subgraph.has_edge(left, right):
                backbone.add_edge(left, right, **subgraph.edges[left, right])
    return backbone


def focus_ego_network(graph: nx.Graph, symbol: str, radius: int = 1) -> nx.Graph:
    target = _norm_symbol(symbol)
    if not target or target not in graph:
        return nx.Graph()
    nodes = sorted(nx.single_source_shortest_path_length(graph, target, cutoff=max(int(radius), 0)).keys())
    return graph.subgraph(nodes).copy()


def _component_layout(subgraph: nx.Graph, seed: int) -> dict[str, tuple[float, float]]:
    if subgraph.number_of_nodes() == 0:
        return {}
    if subgraph.number_of_nodes() == 1:
        node = next(iter(subgraph.nodes()))
        return {node: (0.0, 0.0)}
    if subgraph.number_of_nodes() == 2:
        left, right = list(subgraph.nodes())
        return {left: (-0.8, 0.0), right: (0.8, 0.0)}

    distance_graph = nx.Graph()
    distance_graph.add_nodes_from(subgraph.nodes())
    for left, right, attrs in subgraph.edges(data=True):
        weight = max(_float(attrs.get("edge_weight")), 0.05)
        distance_graph.add_edge(left, right, distance=1.0 / weight)

    try:
        distances = dict(nx.all_pairs_dijkstra_path_length(distance_graph, weight="distance"))
        base = nx.kamada_kawai_layout(subgraph, dist=distances, weight=None, scale=1.0)
    except Exception:
        base = nx.spring_layout(
            subgraph,
            seed=int(seed),
            weight="edge_weight",
            k=1.7 / max(subgraph.number_of_nodes() ** 0.5, 1.0),
            iterations=400,
            scale=1.0,
        )
    density = nx.density(subgraph) if subgraph.number_of_nodes() > 1 else 0.0
    scale = (1.4 + min(subgraph.number_of_nodes(), 12) * 0.18) * (1.0 + min(density, 0.85) * 1.1)
    return {
        node: (point[0] * scale, point[1] * scale)
        for node, point in base.items()
    }


def _pack_component_positions(graph: nx.Graph, seed: int = 7) -> dict[str, tuple[float, float]]:
    if graph.number_of_nodes() == 0:
        return {}

    components = [
        graph.subgraph(nodes).copy()
        for nodes in sorted(nx.connected_components(graph), key=len, reverse=True)
        if any(int(graph.degree(node)) > 0 for node in nodes)
    ]
    if not components:
        return {}

    packed: dict[str, tuple[float, float]] = {}
    layout_items: list[tuple[dict[str, tuple[float, float]], float, float, float, float]] = []
    total_area = 0.0

    for index, subgraph in enumerate(components):
        local = _component_layout(subgraph, seed + index)
        xs = [point[0] for point in local.values()]
        ys = [point[1] for point in local.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = (max_x - min_x) + 2.4
        height = (max_y - min_y) + 2.4
        total_area += width * height
        layout_items.append((local, min_x, min_y, width, height))

    component_count = len(layout_items)
    # Wider packing keeps multi-component graphs from collapsing into a tall stack.
    width_multiplier = 1.4 + (min(max(component_count - 1, 0), 8) * 0.05)
    target_width = max(8.0, math.sqrt(total_area) * width_multiplier)
    column_gap = 2.6
    row_gap = 2.0
    x_cursor = 0.0
    y_cursor = 0.0
    row_height = 0.0

    for local, min_x, min_y, width, height in layout_items:
        if x_cursor > 0 and (x_cursor + width) > target_width:
            x_cursor = 0.0
            y_cursor -= row_height + row_gap
            row_height = 0.0
        shift_x = x_cursor - min_x
        shift_y = y_cursor - min_y
        for node, (x_pos, y_pos) in local.items():
            packed[node] = (x_pos + shift_x, y_pos + shift_y)
        x_cursor += width + column_gap
        row_height = max(row_height, height)
    return packed


def _position_bounds(positions: dict[str, tuple[float, float]]) -> tuple[float, float, float, float]:
    if not positions:
        return -4.0, 4.0, -1.0, 1.0
    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    return min(xs), max(xs), min(ys), max(ys)


def _isolated_sector_groups(graph: nx.Graph) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for sector, nodes in _sector_groups(graph).items():
        isolated = [
            node
            for node in nodes
            if int(graph.degree(node)) == 0
        ]
        if not isolated:
            continue
        grouped[sector] = sorted(
            isolated,
            key=lambda node: (
                -_float(graph.nodes[node].get("candidate_score")),
                node,
            ),
        )
    return grouped


def _layout_isolated_band(
    graph: nx.Graph,
    connected_positions: dict[str, tuple[float, float]],
) -> tuple[
    dict[str, tuple[float, float]],
    list[dict[str, object]],
    dict[str, object] | None,
    int,
]:
    isolate_groups = _isolated_sector_groups(graph)
    if not isolate_groups:
        return {}, [], None, 0

    min_x, max_x, min_y, _ = _position_bounds(connected_positions)
    total_isolates = sum(len(nodes) for nodes in isolate_groups.values())
    span = max(max_x - min_x, 9.0)
    if not connected_positions:
        span = max(span, min(18.0, 1.3 * max(total_isolates, 6)))
        min_x = -(span / 2.0)
        max_x = span / 2.0
        min_y = 1.0

    cols = max(6, min(14, int(span // 1.45) + 1))
    x_step = span / max(cols - 1, 1)
    x_start = min_x
    heading_y = min_y - 1.7
    current_y = heading_y - 0.95
    positions: dict[str, tuple[float, float]] = {}
    annotations: list[dict[str, object]] = []
    isolate_row_count = 0

    for sector, nodes in isolate_groups.items():
        row_count = max(int(math.ceil(len(nodes) / cols)), 1)
        isolate_row_count += row_count
        annotations.append(
            {
                "x": (min_x + max_x) / 2.0,
                "y": current_y + 0.45,
                "text": f"{sector} isolates ({len(nodes)})",
                "font": {"color": STREAMLIT_DARK["muted"], "size": 11},
            }
        )
        for index, node in enumerate(nodes):
            row_index = index // cols
            col_index = index % cols
            positions[node] = (
                x_start + (col_index * x_step),
                current_y - (row_index * 1.15),
            )
        current_y -= (row_count * 1.15) + 1.05

    separator = None
    if connected_positions:
        separator = {
            "x0": min_x - 0.35,
            "x1": max_x + 0.35,
            "y": heading_y + 0.55,
        }

    extra_height = min(420, max(120, int(62 + isolate_row_count * 34 + len(isolate_groups) * 16)))
    return (
        positions,
        annotations,
        separator,
        extra_height,
    )


def _sector_groups(graph: nx.Graph) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for node, attrs in graph.nodes(data=True):
        sector = "Path Nodes" if bool(attrs.get("is_intermediate_path")) else (_text(attrs.get("sector")) or "Unknown")
        groups[sector].append(node)
    return dict(sorted(groups.items(), key=lambda item: (item[0] == "Unknown", item[0])))


def _label_priority(graph: nx.Graph, node: str) -> float:
    attrs = graph.nodes[node]
    degree = int(graph.degree(node))
    if bool(attrs.get("is_intermediate_path")):
        topology_type = _text(attrs.get("topology_node_type")) or _text(attrs.get("node_type"))
        topology_bonus = {
            "sector": 30.0,
            "peer": 24.0,
            "event": 22.0,
            "tag": 18.0,
            "symbol": 16.0,
        }.get(topology_type, 14.0)
        return 26.0 + topology_bonus + (degree * 14.0)
    if _text(attrs.get("node_type")) == "bridge":
        return 24.0 + (degree * 12.0)
    return (
        _float(attrs.get("candidate_score"))
        + (_float(attrs.get("attention_score")) * 0.08)
        + (degree * 8.0)
    )


def _label_spacing_radius(graph: nx.Graph, node: str) -> float:
    attrs = graph.nodes[node]
    degree = int(graph.degree(node))
    if bool(attrs.get("is_intermediate_path")):
        return 1.7 + min(degree, 4) * 0.18
    if _text(attrs.get("node_type")) == "bridge":
        return 1.55 + min(degree, 4) * 0.14
    return 1.15 + min(degree, 5) * 0.10


def _label_nodes(
    graph: nx.Graph,
    positions: dict[str, tuple[float, float]],
    show_labels: bool | str,
    label_top_n: int,
) -> set[str]:
    bridge_nodes = {
        node
        for node, attrs in graph.nodes(data=True)
        if _text(attrs.get("node_type")) == "bridge"
    }
    path_nodes = {
        node
        for node, attrs in graph.nodes(data=True)
        if bool(attrs.get("is_intermediate_path"))
    }
    if show_labels is True:
        return set(graph.nodes())
    if show_labels is False:
        return set()
    if graph.number_of_nodes() <= 16:
        return set(graph.nodes())
    target_count = max(int(label_top_n), 0)
    if target_count == 0:
        return set()
    ranked = sorted(
        [
            node
            for node in graph.nodes()
            if node in positions
        ],
        key=lambda node: (
            -_label_priority(graph, node),
            -int(graph.degree(node)),
            node,
        ),
    )
    selected: list[str] = []
    selected_path_labels = 0
    max_path_labels = max(1, min(4, target_count // 3 or 1))
    for node in ranked:
        attrs = graph.nodes[node]
        is_path_node = bool(attrs.get("is_intermediate_path"))
        if is_path_node and selected_path_labels >= max_path_labels:
            continue
        node_radius = _label_spacing_radius(graph, node)
        if any(
            math.dist(positions[node], positions[existing]) < max(node_radius, _label_spacing_radius(graph, existing))
            for existing in selected
        ):
            continue
        selected.append(node)
        if is_path_node:
            selected_path_labels += 1
        if len(selected) >= target_count:
            break

    if path_nodes and selected_path_labels == 0:
        top_path = max(
            [node for node in path_nodes if node in positions],
            key=lambda node: (_label_priority(graph, node), node),
            default="",
        )
        if top_path:
            if len(selected) < target_count:
                selected.append(top_path)
            elif selected:
                selected[-1] = top_path
    if bridge_nodes and not any(node in bridge_nodes for node in selected):
        top_bridge = max(
            [node for node in bridge_nodes if node in positions],
            key=lambda node: (_label_priority(graph, node), node),
            default="",
        )
        if top_bridge:
            if len(selected) < target_count:
                selected.append(top_bridge)
            elif selected:
                selected[-1] = top_bridge
    return set(selected[: max(target_count, 1)])


def _label_text_position(
    graph: nx.Graph,
    node: str,
    positions: dict[str, tuple[float, float]],
) -> str:
    if node not in positions:
        return "top center"
    x_pos, y_pos = positions[node]
    nearest_dx = 0.0
    nearest_dy = 0.0
    nearest_dist = math.inf
    for other, (other_x, other_y) in positions.items():
        if other == node:
            continue
        distance = math.dist((x_pos, y_pos), (other_x, other_y))
        if distance < nearest_dist:
            nearest_dist = distance
            nearest_dx = x_pos - other_x
            nearest_dy = y_pos - other_y
    if not math.isfinite(nearest_dist) or nearest_dist > 2.6:
        component_nodes = list(nx.node_connected_component(graph, node)) if node in graph else [node]
        center_x = sum(positions[item][0] for item in component_nodes if item in positions) / max(
            len([item for item in component_nodes if item in positions]),
            1,
        )
        center_y = sum(positions[item][1] for item in component_nodes if item in positions) / max(
            len([item for item in component_nodes if item in positions]),
            1,
        )
        nearest_dx = x_pos - center_x
        nearest_dy = y_pos - center_y
    min_x, max_x, min_y, max_y = _position_bounds(positions)
    horizontal = "center"
    if x_pos <= (min_x + 0.65):
        horizontal = "right"
    elif x_pos >= (max_x - 0.65):
        horizontal = "left"
    elif abs(nearest_dx) >= 0.25:
        horizontal = "right" if nearest_dx >= 0 else "left"
    vertical = "middle"
    if y_pos <= (min_y + 0.45):
        vertical = "top"
    elif y_pos >= (max_y - 0.45):
        vertical = "bottom"
    elif abs(nearest_dy) >= 0.2:
        vertical = "top" if nearest_dy >= 0 else "bottom"
    return f"{vertical} {horizontal}"


def _marker_size(score: float, min_score: float, score_span: float) -> float:
    scaled = (score - min_score) / score_span if score_span > 0 else 0.5
    return 13.0 + 15.0 * scaled


def _short_company_name(value: object, max_chars: int = 24) -> str:
    text = _text(value)
    if not text:
        return ""
    words = [part for part in re.split(r"\s+", text) if part]
    while len(words) > 1:
        trailing = words[-1].lower().strip(",.()")
        if trailing in COMPANY_SUFFIXES:
            words.pop()
            continue
        break
    compact = " ".join(words).strip(", ")
    if len(compact) <= max_chars:
        return compact
    truncated = compact[: max(max_chars - 1, 1)].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}..."


def _security_label(node: str, attrs: dict[str, Any], *, include_company: bool) -> str:
    symbol = _text(attrs.get("symbol")) or node
    if not include_company:
        return symbol
    company_name = _short_company_name(attrs.get("security_name"))
    if not company_name or company_name.upper() == symbol.upper():
        return symbol
    return f"{symbol}<br>{company_name}"


def _connected_component_cluster_label(graph: nx.Graph, nodes: list[str]) -> str:
    security_nodes = [
        node
        for node in nodes
        if _text(graph.nodes[node].get("node_type")) != "bridge"
        and not bool(graph.nodes[node].get("is_intermediate_path"))
    ]
    if not security_nodes:
        return ""

    def ranked_counts(field: str) -> list[tuple[str, int]]:
        counts: dict[str, int] = defaultdict(int)
        for node in security_nodes:
            value = _text(graph.nodes[node].get(field))
            if not value or value.lower() == "unknown":
                continue
            counts[value] += 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    peer_counts = ranked_counts("peer_group")
    industry_counts = ranked_counts("industry")
    sector_counts = ranked_counts("sector")

    labels: list[str] = []
    for value, count in peer_counts[:2]:
        if count >= 2 and value not in labels:
            labels.append(value)
    if not labels:
        for value, count in industry_counts[:2]:
            if count >= 2 and value not in labels:
                labels.append(value)
    if not labels and sector_counts:
        labels.append(sector_counts[0][0])
    if not labels and peer_counts:
        labels.append(peer_counts[0][0])
    if not labels and industry_counts:
        labels.append(industry_counts[0][0])
    if not labels:
        return ""
    return " + ".join(labels[:2])


def _component_subcluster_annotations(
    graph: nx.Graph,
    component_nodes: list[str],
    positions: dict[str, tuple[float, float]],
    *,
    compact: bool,
    max_labels: int,
    used_points: list[tuple[float, float]] | None = None,
) -> list[dict[str, object]]:
    security_nodes = [
        node
        for node in component_nodes
        if node in positions
        and _text(graph.nodes[node].get("node_type")) != "bridge"
        and not bool(graph.nodes[node].get("is_intermediate_path"))
    ]
    if len(security_nodes) < 5 or max_labels <= 0:
        return []

    groups: dict[str, list[str]] = defaultdict(list)
    for node in security_nodes:
        attrs = graph.nodes[node]
        label = _first_informative(
            attrs.get("peer_group"),
            attrs.get("industry"),
            attrs.get("sector"),
        )
        if not label:
            continue
        label_text = _text(label)
        if not label_text:
            continue
        groups[label_text].append(node)

    ranked_groups = sorted(
        [
            (label, members)
            for label, members in groups.items()
            if len(members) >= 2
        ],
        key=lambda item: (-len(item[1]), item[0]),
    )
    # Dense components can still be informative even when peer groups are sparse.
    # In that case, allow a few single-name buckets as muted context labels.
    if len(ranked_groups) < max_labels and len(security_nodes) >= 10:
        fallback_groups = sorted(
            [
                (label, members)
                for label, members in groups.items()
                if len(members) == 1
            ],
            key=lambda item: (
                -max((_float(graph.nodes[node].get("candidate_score")) for node in item[1]), default=0.0),
                item[0],
            ),
        )
        ranked_groups.extend(fallback_groups)
    if not ranked_groups:
        return []

    selected: list[dict[str, object]] = []
    spacing = 1.0 if compact else 1.2
    occupied = list(used_points or [])
    node_spacing = 1.05 if compact else 1.25
    for label, members in ranked_groups:
        if len(selected) >= max_labels:
            break
        xs = [positions[node][0] for node in members]
        ys = [positions[node][1] for node in members]
        if not xs or not ys:
            continue
        center_x = sum(xs) / len(xs)
        center_y = sum(ys) / len(ys)
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        x_pad = 0.55 if compact else 0.68
        y_pad = 0.48 if compact else 0.62
        candidate_points = [
            (center_x, max_y + y_pad),
            (center_x, min_y - y_pad),
            (max_x + x_pad, center_y),
            (min_x - x_pad, center_y),
        ]
        anchor: tuple[float, float] | None = None
        for point in candidate_points:
            if any(math.dist(point, existing) < spacing for existing in occupied):
                continue
            if any(math.dist(point, positions[node]) < node_spacing for node in members):
                continue
            anchor = point
            break
        if anchor is None:
            anchor = (center_x, max_y + y_pad)
            if any(math.dist(anchor, existing) < spacing for existing in occupied):
                continue
        selected.append(
            {
                "x": anchor[0],
                "y": anchor[1],
                "text": label,
                "font": {"color": "rgba(148, 163, 184, 0.64)", "size": 8 if compact else 9},
                "bgcolor": "rgba(15, 23, 42, 0.74)",
                "bordercolor": "rgba(148, 163, 184, 0.18)",
                "borderpad": 2,
            }
        )
        occupied.append(anchor)
    return selected


def _component_cluster_annotations(
    graph: nx.Graph,
    positions: dict[str, tuple[float, float]],
    *,
    compact: bool,
) -> list[dict[str, object]]:
    connected_nodes = [
        node
        for node, degree in graph.degree()
        if int(degree) > 0 and node in positions
    ]
    if not connected_nodes:
        return []
    connected_subgraph = graph.subgraph(connected_nodes).copy()
    components = sorted(
        nx.connected_components(connected_subgraph),
        key=len,
        reverse=True,
    )
    if not components:
        return []

    max_labels = 6 if compact else 8
    subcluster_budget = 4 if compact else (8 if len(components) <= 2 else 6)
    per_component_subcluster_limit = 2 if compact else (4 if len(components) == 1 else 2)
    annotations: list[dict[str, object]] = []
    used_points: list[tuple[float, float]] = []
    for index, component_nodes in enumerate(components[:max_labels], start=1):
        nodes = sorted(component_nodes)
        if len(nodes) <= 1:
            continue
        label = _connected_component_cluster_label(graph, nodes)
        if not label:
            continue
        xs = [positions[node][0] for node in nodes if node in positions]
        ys = [positions[node][1] for node in nodes if node in positions]
        if not xs or not ys:
            continue
        annotations.append(
            {
                "x": sum(xs) / len(xs),
                "y": max(ys) + (0.72 if compact else 0.95),
                "text": f"CC{index}: {label}",
                "font": {"color": "rgba(148, 163, 184, 0.78)", "size": 9 if compact else 10},
                "bgcolor": "rgba(15, 23, 42, 0.62)",
                "bordercolor": "rgba(148, 163, 184, 0.20)",
                "borderpad": 2,
            }
        )
        used_points.append((sum(xs) / len(xs), max(ys) + (0.72 if compact else 0.95)))

        if subcluster_budget <= 0:
            continue
        subcluster_annotations = _component_subcluster_annotations(
            graph,
            nodes,
            positions,
            compact=compact,
            max_labels=min(per_component_subcluster_limit, subcluster_budget),
            used_points=used_points,
        )
        for item in subcluster_annotations:
            used_points.append((float(item["x"]), float(item["y"])))
        annotations.extend(subcluster_annotations)
        subcluster_budget -= len(subcluster_annotations)
    return annotations


def plot_attention_candidate_network(
    graph: nx.Graph,
    *,
    title: str,
    height: int = 900,
    seed: int = 7,
    show_labels: bool | str = "auto",
    label_top_n: int = 14,
    show_isolates: bool = True,
    show_legend: bool = True,
    show_summary: bool = True,
    show_footer: bool = True,
    compact: bool = False,
    show_component_labels: bool | str = "auto",
) -> go.Figure:
    plot_graph = graph.copy() if show_isolates else connected_candidate_subgraph(graph)
    connected_positions = _pack_component_positions(plot_graph, seed=seed)
    if show_isolates:
        isolate_positions, isolate_annotations, isolate_separator, extra_height = _layout_isolated_band(
            plot_graph,
            connected_positions,
        )
    else:
        isolate_positions = {}
        isolate_annotations = []
        isolate_separator = None
        extra_height = 0
    positions = {
        **connected_positions,
        **isolate_positions,
    }
    render_component_labels = (not compact) if show_component_labels == "auto" else bool(show_component_labels)
    component_annotations = (
        _component_cluster_annotations(plot_graph, positions, compact=compact)
        if render_component_labels
        else []
    )
    label_nodes = _label_nodes(plot_graph, positions, show_labels, label_top_n)

    weights = [_float(attrs.get("edge_weight")) for _, _, attrs in plot_graph.edges(data=True)]
    min_weight = min(weights) if weights else 0.0
    max_weight = max(weights) if weights else 1.0
    weight_span = max(max_weight - min_weight, 1e-6)
    scores = [_float(attrs.get("candidate_score")) for _, attrs in plot_graph.nodes(data=True)]
    min_score = min(scores) if scores else 0.0
    max_score = max(scores) if scores else 1.0
    score_span = max(max_score - min_score, 1e-6)
    marker_scale = 0.92 if compact else 1.0
    security_text_size = 10 if compact else 11
    context_text_size = 9 if compact else 10

    edge_traces: list[go.Scatter] = []
    for left, right, attrs in sorted(plot_graph.edges(data=True), key=lambda item: _float(item[2].get("edge_weight"))):
        if left not in positions or right not in positions:
            continue
        x0, y0 = positions[left]
        x1, y1 = positions[right]
        weight = _float(attrs.get("edge_weight"))
        scaled = (weight - min_weight) / weight_span
        edge_reasons = _listify(attrs.get("edge_reasons"))
        is_bridge_edge = "bridge_concept" in edge_reasons
        is_topology_path_edge = any(str(reason).startswith("topology_") for reason in edge_reasons)
        edge_color = (
            f"rgba(148, 163, 184, {0.22 + 0.18 * scaled:.3f})"
            if is_topology_path_edge
            else (
                f"rgba(148, 163, 184, {0.14 + 0.26 * scaled:.3f})"
                if is_bridge_edge
                else f"rgba(203, 213, 225, {0.18 + 0.62 * scaled:.3f})"
            )
        )
        edge_width = (
            (0.9 + 1.2 * scaled)
            if is_topology_path_edge
            else ((0.8 + 1.9 * scaled) if is_bridge_edge else (1.0 + 4.0 * scaled))
        )
        edge_traces.append(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                hoverinfo="text",
                text=[f"{left} - {right}<br>weight={weight:.2f}<br>reasons={_text(attrs.get('edge_reason_text')) or 'n/a'}"] * 2,
                line=dict(color=edge_color, width=edge_width),
                showlegend=False,
            )
        )

    node_traces: list[go.Scatter] = []
    for sector, nodes in _sector_groups(plot_graph).items():
        is_bridge_sector = sector == "Bridge Concepts"
        is_path_sector = sector == "Path Nodes"
        x_values: list[float] = []
        y_values: list[float] = []
        text: list[str] = []
        textpositions: list[str] = []
        hovertext: list[str] = []
        sizes: list[float] = []
        symbols: list[str] = []
        marker_opacity = 0.44 if is_path_sector else (0.52 if is_bridge_sector else 0.74)
        for node in sorted(
            nodes,
            key=lambda value: (
                _text(graph.nodes[value].get("node_type")) != "bridge",
                -_float(graph.nodes[value].get("candidate_score")),
                value,
            ),
        ):
            if node not in positions:
                continue
            attrs = graph.nodes[node]
            is_path_node = bool(attrs.get("is_intermediate_path"))
            x_pos, y_pos = positions[node]
            x_values.append(x_pos)
            y_values.append(y_pos)
            is_bridge_node = _text(attrs.get("node_type")) == "bridge"
            display_label = (
                _security_label(node, attrs, include_company=True)
                if not is_path_node and not is_bridge_node
                else (_text(attrs.get("symbol")) or node)
            )
            has_label = node in label_nodes
            text.append(display_label if has_label else "")
            textpositions.append(_label_text_position(plot_graph, node, positions) if has_label else "top center")
            if is_bridge_node:
                hovertext.append(
                    "<br>".join(
                        [
                            f"<b>{_text(attrs.get('symbol')) or node}</b>",
                            "type=bridge concept",
                            f"components={int(_float(attrs.get('bridge_component_count')))}",
                            f"supporting_symbols={int(_float(attrs.get('bridge_support_count')))}",
                            f"members={', '.join(_listify(attrs.get('bridge_members'))[:8]) or 'n/a'}",
                            f"degree={plot_graph.degree(node)}",
                        ]
                    )
                )
            elif is_path_node:
                hovertext.append(
                    "<br>".join(
                        [
                            f"<b>{display_label}</b>",
                            f"type={_text(attrs.get('topology_node_type')) or _text(attrs.get('node_type')) or 'path'}",
                            f"sector={_text(attrs.get('topology_sector')) or _text(attrs.get('sector')) or 'Unknown'}",
                            f"peer_group={_text(attrs.get('topology_peer_group')) or _text(attrs.get('peer_group')) or 'Unknown'}",
                            f"industry={_text(attrs.get('topology_industry')) or _text(attrs.get('industry')) or 'Unknown'}",
                            f"degree={plot_graph.degree(node)}",
                            f"source={_text(attrs.get('source_label')) or 'intermediate path'}",
                        ]
                    )
                )
            else:
                hovertext.append(
                    "<br>".join(
                        [
                            f"<b>{node}</b>",
                            *(
                                [f"company={_text(attrs.get('security_name'))}"]
                                if _text(attrs.get("security_name"))
                                and _text(attrs.get("security_name")).upper() != node.upper()
                                else []
                            ),
                            f"sector={_text(attrs.get('sector')) or 'Unknown'}",
                            f"industry={_text(attrs.get('industry')) or 'Unknown'}",
                            f"peer_group={_text(attrs.get('peer_group')) or 'Unknown'}",
                            f"change_pct={_float(attrs.get('change_pct')):+.2f}%",
                            f"candidate_score={_float(attrs.get('candidate_score')):.1f}",
                            f"attention_score={_float(attrs.get('attention_score')):.1f}",
                            f"node_size={_marker_size(_float(attrs.get('candidate_score')), min_score, score_span):.1f}",
                            f"degree={plot_graph.degree(node)}",
                            *([f"source={_text(attrs.get('source_label'))}"] if _text(attrs.get("source_label")) else []),
                            *([f"headline={_text(attrs.get('headline'))}"] if _text(attrs.get("headline")) else []),
                        ]
                    )
                )
            sizes.append((11.0 if is_path_node else _marker_size(_float(attrs.get("candidate_score")), min_score, score_span)) * marker_scale)
            symbols.append(
                "circle-open"
                if is_path_node and _text(attrs.get("topology_node_type")) == "symbol"
                else ("diamond-open" if is_path_node else _direction_symbol(attrs.get("direction"), attrs.get("change_pct")))
            )
        if not is_bridge_sector and not is_path_sector:
            visible_count = len(x_values)
            marker_opacity = 0.74
            if visible_count >= 18:
                marker_opacity = 0.52
            elif visible_count >= 10:
                marker_opacity = 0.60
            elif visible_count >= 6:
                marker_opacity = 0.68
        node_traces.append(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="markers+text",
                cliponaxis=False,
                text=text,
                textposition=textpositions,
                hoverinfo="text",
                hovertext=hovertext,
                name=sector,
                textfont=dict(
                    color=STREAMLIT_DARK["muted"] if (is_bridge_sector or is_path_sector) else STREAMLIT_DARK["text"],
                    size=context_text_size if (is_bridge_sector or is_path_sector) else security_text_size,
                ),
                marker=dict(
                    size=sizes,
                    symbol=symbols,
                    color=SECTOR_COLORS.get(sector, SECTOR_COLORS["Unknown"]),
                    line=dict(
                        width=0.8 if (is_bridge_sector or is_path_sector) else 1.0,
                        color=STREAMLIT_DARK["grid"] if (is_bridge_sector or is_path_sector) else STREAMLIT_DARK["outline"],
                    ),
                    opacity=marker_opacity,
                ),
            )
        )

    fig = go.Figure(data=[*edge_traces, *node_traces])
    connected_nodes = [node for node, degree in plot_graph.degree() if int(degree) > 0]
    connected_components = (
        nx.number_connected_components(plot_graph.subgraph(connected_nodes).copy())
        if connected_nodes
        else 0
    )
    isolate_count = int(plot_graph.number_of_nodes() - len(connected_nodes))
    margin = dict(l=12, r=12, t=26, b=12) if compact else dict(l=20, r=20, t=95, b=52)
    fig.update_layout(
        title=title,
        template="plotly_dark",
        showlegend=show_legend,
        paper_bgcolor=STREAMLIT_DARK["paper"],
        plot_bgcolor=STREAMLIT_DARK["paper"],
        font=dict(color=STREAMLIT_DARK["text"]),
        hoverlabel=dict(bgcolor=STREAMLIT_DARK["panel"], bordercolor=STREAMLIT_DARK["grid"], font=dict(color=STREAMLIT_DARK["text"])),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0.0,
            bgcolor="rgba(17, 24, 39, 0.65)",
            bordercolor="rgba(148, 163, 184, 0.20)",
            borderwidth=1,
            font=dict(color=STREAMLIT_DARK["text"]),
            title=dict(text="Node color = sector"),
        ),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, showline=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, showline=False),
        margin=margin,
        height=height + extra_height,
    )
    if show_summary:
        fig.add_annotation(
            x=0.0,
            y=1.05,
            xref="paper",
            yref="paper",
            showarrow=False,
            align="left",
            font=dict(color=STREAMLIT_DARK["muted"], size=12),
            text=(
                (
                    f"{plot_graph.number_of_nodes()} visible symbols | "
                    f"{plot_graph.number_of_edges()} edges | "
                    f"{connected_components} connected component{'s' if connected_components != 1 else ''}"
                )
                if not show_isolates
                else (
                    f"{plot_graph.number_of_nodes()} symbols | "
                    f"{plot_graph.number_of_edges()} edges | "
                    f"{connected_components} connected component{'s' if connected_components != 1 else ''} | "
                    f"{isolate_count} isolate{'s' if isolate_count != 1 else ''}"
                )
            ),
        )
    if show_isolates and isolate_count:
        fig.add_annotation(
            x=0.5,
            y=0.0,
            xref="paper",
            yref="paper",
            showarrow=False,
            align="center",
            font=dict(color=STREAMLIT_DARK["text"], size=12),
            text=f"Isolated symbols ({isolate_count})",
        )
    if show_isolates and isolate_separator:
        fig.add_shape(
            type="line",
            x0=float(isolate_separator["x0"]),
            x1=float(isolate_separator["x1"]),
            y0=float(isolate_separator["y"]),
            y1=float(isolate_separator["y"]),
            line=dict(color="rgba(148, 163, 184, 0.32)", width=1.0, dash="dot"),
        )
    for annotation in component_annotations:
        annotation_kwargs: dict[str, object] = {
            "x": float(annotation["x"]),
            "y": float(annotation["y"]),
            "xref": "x",
            "yref": "y",
            "showarrow": False,
            "align": "center",
            "font": dict(annotation["font"]),
            "text": str(annotation["text"]),
        }
        for key in ("bgcolor", "bordercolor", "borderpad", "opacity"):
            if key in annotation:
                annotation_kwargs[key] = annotation[key]
        fig.add_annotation(**annotation_kwargs)
    if show_isolates:
        for annotation in isolate_annotations:
            fig.add_annotation(
                x=float(annotation["x"]),
                y=float(annotation["y"]),
                xref="x",
                yref="y",
                showarrow=False,
                align="center",
                font=dict(annotation["font"]),
                text=str(annotation["text"]),
            )
    if show_footer:
        fig.add_annotation(
            x=0.0,
            y=-0.08,
            xref="paper",
            yref="paper",
            showarrow=False,
            align="left",
            font=dict(color=STREAMLIT_DARK["muted"], size=11),
            text=(
                "Edge width/opacity = edge weight | Node size = candidate score or path role | "
                "Node shape = move direction | gray nodes/edges = intermediate real path nodes or bridge concepts"
                + (" | isolate band keeps single-name coverage visible" if show_isolates else "")
            ),
        )
    return fig


__all__ = [
    "build_attention_candidate_network",
    "build_homepage_attention_graph_payload",
    "build_network_backbone",
    "connected_candidate_subgraph",
    "expand_network_bridge_nodes",
    "expand_network_real_paths",
    "focus_ego_network",
    "network_graph_summary",
    "plot_attention_candidate_network",
]
