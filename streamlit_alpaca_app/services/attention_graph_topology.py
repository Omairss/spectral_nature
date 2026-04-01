from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import Any

import networkx as nx
import pandas as pd
import plotly.graph_objects as go


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
    if isinstance(value, tuple | set):
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
        if isinstance(parsed, tuple | set):
            return list(parsed)
    return []


def _dictify(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _first_informative(*values: object, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text and text.lower() != "unknown":
            return text
    return default


def _event_label(row: pd.Series) -> str:
    event_title = _text(row.get("event_title"))
    if event_title:
        return event_title
    event_type = _text(row.get("event_type")) or "event"
    event_id = _text(row.get("event_id")) or "cluster"
    return f"{event_type}:{event_id}"


def _node_id(node_type: str, label: str) -> str:
    return f"{node_type}::{label}"


def _symbol_from_item(value: object) -> str:
    if isinstance(value, dict):
        return _norm_symbol(value.get("symbol"))
    return _norm_symbol(value)


def _member_symbols_from_event_facts(value: object) -> list[str]:
    facts = _dictify(value)
    members = facts.get("members") or []
    return [symbol for symbol in (_symbol_from_item(item) for item in _listify(members)) if symbol]


def _cluster_member_symbols(row: pd.Series) -> list[str]:
    symbols: list[str] = []
    for column in (
        "member_symbols",
        "driver_symbols",
        "beneficiary_symbols",
        "loser_symbols",
        "driver_symbols_json",
        "beneficiary_symbols_json",
        "loser_symbols_json",
    ):
        symbols.extend(_symbol_from_item(item) for item in _listify(row.get(column)))
    for column in ("event_facts", "event_facts_json"):
        symbols.extend(_member_symbols_from_event_facts(row.get(column)))
    return [symbol for symbol in dict.fromkeys(symbols) if symbol]


def _slug(value: object) -> str:
    lowered = _text(value).lower()
    if not lowered:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def _humanize_slug(value: object) -> str:
    slug = _slug(value)
    if not slug:
        return ""
    words: list[str] = []
    for token in slug.split("_"):
        if not token:
            continue
        if token in {"ai", "api", "ev", "gpu", "ipo", "oil", "reit", "saas", "us"}:
            words.append(token.upper())
        elif token == "and":
            words.append("&")
        else:
            words.append(token.title())
    return " ".join(words).strip()


def _topology_tags(row: pd.Series) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    values: list[object] = []
    for column in (
        "macro_role_tags",
        "business_role_tags",
        "taxonomy_macro_role_tags",
        "taxonomy_business_role_tags",
        "macro_exposure_tags",
        "business_tags",
    ):
        values.extend(_listify(row.get(column)))
    values.extend(
        value
        for value in (
            row.get("commodity_role"),
            row.get("rates_role"),
            row.get("defensive_role"),
        )
        if _text(value)
    )
    for value in values:
        slug = _slug(value)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append((slug, _humanize_slug(value) or slug.replace("_", " ")))
    return out


def build_attention_topology_graph(
    candidate_frame: pd.DataFrame,
    cluster_frame: pd.DataFrame | None = None,
    *,
    universe_frame: pd.DataFrame | None = None,
    include_tag_nodes: bool = True,
) -> nx.Graph:
    graph = nx.Graph()
    candidates = candidate_frame.copy() if isinstance(candidate_frame, pd.DataFrame) else pd.DataFrame()
    clusters = cluster_frame.copy() if isinstance(cluster_frame, pd.DataFrame) else pd.DataFrame()
    universe = universe_frame.copy() if isinstance(universe_frame, pd.DataFrame) else pd.DataFrame()

    if candidates.empty and universe.empty:
        return graph

    peer_to_sector: dict[str, str] = {}
    symbol_to_peer: dict[str, str] = {}
    candidate_lookup: dict[str, dict[str, Any]] = {}
    for _, row in candidates.iterrows():
        symbol = _norm_symbol(row.get("symbol") or row.get("symbol_upper"))
        if symbol:
            candidate_lookup[symbol] = row.to_dict()

    if universe.empty:
        universe = candidates.copy()

    if not candidates.empty:
        missing_candidates = [
            symbol
            for symbol in candidate_lookup
            if symbol not in {
                _norm_symbol(value)
                for value in universe.get("symbol", universe.get("symbol_upper", pd.Series(dtype=str))).tolist()
            }
        ]
        if missing_candidates:
            candidate_only = candidates[
                candidates.get("symbol", candidates.get("symbol_upper", pd.Series(dtype=str))).astype(str).str.upper().isin(set(missing_candidates))
            ].copy()
            universe = pd.concat([universe, candidate_only], ignore_index=True, sort=False)

    for _, row in universe.iterrows():
        symbol = _norm_symbol(row.get("symbol") or row.get("symbol_upper"))
        if not symbol:
            continue
        candidate_row = candidate_lookup.get(symbol, {})
        sector = _first_informative(
            row.get("effective_sector"),
            row.get("taxonomy_sector"),
            row.get("sector"),
            default="Unclassified",
        )
        peer_group = _first_informative(
            row.get("effective_peer_group_id"),
            row.get("taxonomy_peer_group_id"),
            row.get("peer_group_id"),
            row.get("effective_industry"),
            row.get("taxonomy_industry"),
            row.get("industry"),
            default=f"{sector} / Other",
        )

        sector_id = _node_id("sector", sector)
        peer_id = _node_id("peer", peer_group)

        graph.add_node(
            sector_id,
            node_type="sector",
            label=sector,
            layer=0,
            sector=sector,
        )
        graph.add_node(
            peer_id,
            node_type="peer",
            label=peer_group,
            layer=1,
            sector=sector,
        )
        graph.add_node(
            symbol,
            node_type="symbol",
            label=symbol,
            layer=3,
            sector=sector,
            peer_group=peer_group,
            change_pct=_float(candidate_row.get("change_pct", row.get("change_pct"))),
            candidate_score=_float(candidate_row.get("candidate_score", row.get("candidate_score"))),
            industry=_first_informative(
                row.get("effective_industry"),
                row.get("taxonomy_industry"),
                row.get("industry"),
                default="Unknown",
            ),
            taxonomy_source=_text(row.get("taxonomy_source_of_truth")) or "none",
            is_candidate=symbol in candidate_lookup,
            source_label=_text(candidate_row.get("source_label")) or ("candidate" if symbol in candidate_lookup else "intermediate path"),
        )

        graph.add_edge(sector_id, peer_id, edge_type="taxonomy", weight=1.0)
        graph.add_edge(
            peer_id,
            symbol,
            edge_type="membership",
            weight=max(_float(candidate_row.get("candidate_score", row.get("candidate_score"))), 1.0),
        )

        if include_tag_nodes:
            for tag_slug, tag_label in _topology_tags(row):
                tag_id = _node_id("tag", tag_slug)
                graph.add_node(
                    tag_id,
                    node_type="tag",
                    label=tag_label,
                    layer=2,
                    sector=sector,
                    tag_slug=tag_slug,
                )
                graph.add_edge(tag_id, symbol, edge_type="tag", weight=1.0)

        peer_to_sector[peer_id] = sector_id
        symbol_to_peer[symbol] = peer_id

    if not clusters.empty:
        for _, row in clusters.iterrows():
            members = [symbol for symbol in _cluster_member_symbols(row) if symbol in symbol_to_peer]
            members = list(dict.fromkeys(members))
            if len(members) < 2:
                continue
            event_id = _node_id("event", _text(row.get("event_id")) or _event_label(row))
            graph.add_node(
                event_id,
                node_type="event",
                label=_event_label(row),
                layer=2,
                event_type=_text(row.get("event_type")) or "event",
                event_score=_float(row.get("event_score")),
                member_count=len(members),
            )
            for symbol in members:
                graph.add_edge(
                    event_id,
                    symbol,
                    edge_type="event",
                    weight=max(_float(row.get("event_score")), 1.0),
                )
    return graph


def topology_graph_summary(graph: nx.Graph) -> dict[str, int]:
    counts = defaultdict(int)
    for _, attrs in graph.nodes(data=True):
        counts[str(attrs.get("node_type") or "unknown")] += 1
    return {
        "nodes": int(graph.number_of_nodes()),
        "edges": int(graph.number_of_edges()),
        "sector_nodes": int(counts.get("sector", 0)),
        "peer_nodes": int(counts.get("peer", 0)),
        "event_nodes": int(counts.get("event", 0)),
        "tag_nodes": int(counts.get("tag", 0)),
        "symbol_nodes": int(counts.get("symbol", 0)),
    }


def focus_topology_subgraph(graph: nx.Graph, symbol: str, radius: int = 2) -> nx.Graph:
    target = _norm_symbol(symbol)
    if not target or target not in graph:
        return nx.Graph()
    lengths = nx.single_source_shortest_path_length(graph, target, cutoff=max(int(radius), 1))
    nodes = sorted(lengths.keys())
    return graph.subgraph(nodes).copy()


def filter_attention_topology_graph(
    graph: nx.Graph,
    *,
    node_types: set[str] | list[str] | tuple[str, ...] | None = None,
    edge_types: set[str] | list[str] | tuple[str, ...] | None = None,
    drop_isolates: bool = False,
) -> nx.Graph:
    node_type_filter = {str(item) for item in node_types} if node_types else None
    edge_type_filter = {str(item) for item in edge_types} if edge_types else None

    filtered = nx.Graph()
    for node, attrs in graph.nodes(data=True):
        node_type = str(attrs.get("node_type") or "")
        if node_type_filter and node_type not in node_type_filter:
            continue
        filtered.add_node(node, **attrs)

    for left, right, attrs in graph.edges(data=True):
        if left not in filtered or right not in filtered:
            continue
        edge_type = str(attrs.get("edge_type") or "")
        if edge_type_filter and edge_type not in edge_type_filter:
            continue
        filtered.add_edge(left, right, **attrs)

    if drop_isolates:
        filtered.remove_nodes_from([node for node, degree in filtered.degree() if int(degree) == 0])
    return filtered


def _layer_groups(graph: nx.Graph) -> dict[int, list[str]]:
    groups: dict[int, list[str]] = defaultdict(list)
    for node, attrs in graph.nodes(data=True):
        groups[int(attrs.get("layer") or 0)].append(node)
    for layer, items in groups.items():
        groups[layer] = sorted(
            items,
            key=lambda node: (
                str(graph.nodes[node].get("sector") or ""),
                str(graph.nodes[node].get("label") or node),
            ),
        )
    return groups


def _assign_positions(graph: nx.Graph) -> dict[str, tuple[float, float]]:
    if graph.number_of_nodes() == 0:
        return {}

    positions: dict[str, tuple[float, float]] = {}
    layer_x = {0: 0.0, 1: 1.8, 2: 3.4, 3: 5.2}
    groups = _layer_groups(graph)

    sector_nodes = groups.get(0, [])
    sector_order = {
        node: idx
        for idx, node in enumerate(sector_nodes)
    }
    sector_symbol_counts: dict[str, int] = defaultdict(int)
    for node, attrs in graph.nodes(data=True):
        if str(attrs.get("node_type")) != "symbol":
            continue
        sector_symbol_counts[str(attrs.get("sector") or "Unclassified")] += 1

    cursor = 0.0
    sector_bands: dict[str, tuple[float, float]] = {}
    for sector_node in sector_nodes:
        sector = str(graph.nodes[sector_node].get("label") or sector_node)
        height = max(sector_symbol_counts.get(sector, 1) * 0.7, 1.8)
        start = cursor
        end = cursor + height
        sector_bands[sector_node] = (start, end)
        positions[sector_node] = (layer_x[0], (start + end) / 2.0)
        cursor = end + 1.0

    peer_nodes = groups.get(1, [])
    peer_symbols: dict[str, list[str]] = defaultdict(list)
    for left, right, attrs in graph.edges(data=True):
        if str(attrs.get("edge_type")) != "membership":
            continue
        if str(graph.nodes[left].get("node_type")) == "peer" and str(graph.nodes[right].get("node_type")) == "symbol":
            peer_symbols[left].append(right)
        elif str(graph.nodes[right].get("node_type")) == "peer" and str(graph.nodes[left].get("node_type")) == "symbol":
            peer_symbols[right].append(left)

    peer_y: dict[str, float] = {}
    symbols_by_sector: dict[str, list[str]] = defaultdict(list)
    for peer_node in peer_nodes:
        sector = str(graph.nodes[peer_node].get("sector") or "Unclassified")
        sector_node = _node_id("sector", sector)
        start, end = sector_bands.get(sector_node, (0.0, 1.0))
        sector_peers = [node for node in peer_nodes if str(graph.nodes[node].get("sector") or "Unclassified") == sector]
        peer_index = sector_peers.index(peer_node) if peer_node in sector_peers else 0
        peer_count = max(len(sector_peers), 1)
        y = start + (peer_index + 0.5) * ((end - start) / peer_count)
        peer_y[peer_node] = y
        positions[peer_node] = (layer_x[1], y)
        symbols_by_sector[sector].extend(peer_symbols.get(peer_node, []))

    symbol_nodes = groups.get(3, [])
    for peer_node, symbols in peer_symbols.items():
        ranked = sorted(
            list(dict.fromkeys(symbols)),
            key=lambda node: (
                -_float(graph.nodes[node].get("candidate_score")),
                str(graph.nodes[node].get("label") or node),
            ),
        )
        if not ranked:
            continue
        center = peer_y.get(peer_node, 0.0)
        spacing = 0.55
        start = center - (len(ranked) - 1) * spacing / 2.0
        for idx, node in enumerate(ranked):
            positions[node] = (layer_x[3], start + idx * spacing)

    unplaced_symbols = [node for node in symbol_nodes if node not in positions]
    if unplaced_symbols:
        for idx, node in enumerate(sorted(unplaced_symbols)):
            positions[node] = (layer_x[3], cursor + idx * 0.65)

    event_nodes = groups.get(2, [])
    used_event_y: list[float] = []
    for event_node in event_nodes:
        member_positions = [
            positions[neighbor][1]
            for neighbor in graph.neighbors(event_node)
            if neighbor in positions and str(graph.nodes[neighbor].get("node_type")) == "symbol"
        ]
        if member_positions:
            y = sum(member_positions) / len(member_positions)
        else:
            y = cursor + len(used_event_y) * 0.7
        while any(abs(y - existing) < 0.28 for existing in used_event_y):
            y += 0.32
        used_event_y.append(y)
        positions[event_node] = (layer_x[2], y)
    tag_nodes = groups.get(2, [])
    tag_nodes = [node for node in tag_nodes if str(graph.nodes[node].get("node_type")) == "tag"]
    if tag_nodes:
        for idx, tag_node in enumerate(tag_nodes):
            member_positions = [
                positions[neighbor][1]
                for neighbor in graph.neighbors(tag_node)
                if neighbor in positions and str(graph.nodes[neighbor].get("node_type")) == "symbol"
            ]
            y = (sum(member_positions) / len(member_positions)) if member_positions else (cursor + idx * 0.7)
            positions[tag_node] = (layer_x[2], y)
    return positions


def plot_attention_topology_graph(
    graph: nx.Graph,
    *,
    title: str,
    show_symbol_labels: bool = False,
    height: int = 900,
) -> go.Figure:
    positions = _assign_positions(graph)

    edge_styles = {
        "taxonomy": {"color": "#94a3b8", "width": 1.4},
        "membership": {"color": "#475569", "width": 2.2},
        "event": {"color": "#ea580c", "width": 2.6},
    }
    edge_traces = []
    for edge_type, style in edge_styles.items():
        x: list[float | None] = []
        y: list[float | None] = []
        for left, right, attrs in graph.edges(data=True):
            if str(attrs.get("edge_type")) != edge_type:
                continue
            if left not in positions or right not in positions:
                continue
            x0, y0 = positions[left]
            x1, y1 = positions[right]
            x.extend([x0, x1, None])
            y.extend([y0, y1, None])
        edge_traces.append(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                hoverinfo="none",
                line=dict(color=style["color"], width=style["width"]),
                opacity=0.9,
                showlegend=True,
                name=edge_type,
            )
        )

    node_specs = [
        ("sector", "#0f766e", "square", 28, True),
        ("peer", "#1d4ed8", "diamond", 22, True),
        ("event", "#ea580c", "hexagon", 20, True),
        ("tag", "#64748b", "diamond", 16, True),
        ("symbol", None, "circle", None, show_symbol_labels),
    ]
    node_traces = []
    for node_type, color, symbol, size, label_on in node_specs:
        x: list[float] = []
        y: list[float] = []
        text: list[str] = []
        hovertext: list[str] = []
        sizes: list[float] = []
        colors: list[float | str] = []
        for node, attrs in graph.nodes(data=True):
            if str(attrs.get("node_type")) != node_type:
                continue
            if node not in positions:
                continue
            px, py = positions[node]
            x.append(px)
            y.append(py)
            label = str(attrs.get("label") or node)
            text.append(label if label_on else "")
            if node_type == "symbol":
                sizes.append(max(10.0, 10.0 + _float(attrs.get("candidate_score")) * 0.12))
                colors.append(_float(attrs.get("change_pct")))
                hovertext.append(
                    "<br>".join(
                        [
                            f"<b>{label}</b>",
                            f"sector={_text(attrs.get('sector'))}",
                            f"peer_group={_text(attrs.get('peer_group'))}",
                            f"industry={_text(attrs.get('industry'))}",
                            f"change_pct={_float(attrs.get('change_pct')):+.2f}%",
                            f"candidate_score={_float(attrs.get('candidate_score')):.1f}",
                        ]
                    )
                )
            else:
                sizes.append(float(size or 18))
                colors.append(color or "#334155")
                hovertext.append(f"<b>{label}</b><br>type={node_type}")
        marker = dict(
            size=sizes,
            symbol=symbol,
            line=dict(width=1.0, color="#0f172a"),
            opacity=0.9 if node_type != "symbol" else 0.82,
        )
        if node_type == "symbol":
            marker["color"] = colors
            marker["colorscale"] = "RdYlGn"
            marker["colorbar"] = dict(title="change_pct")
        else:
            marker["color"] = color
        node_traces.append(
            go.Scatter(
                x=x,
                y=y,
                mode="markers+text" if label_on else "markers",
                text=text if label_on else None,
                textposition="middle right" if node_type != "symbol" else "top center",
                hoverinfo="text",
                hovertext=hovertext,
                marker=marker,
                showlegend=True,
                name=node_type,
            )
        )

    fig = go.Figure(data=[*edge_traces, *node_traces])
    fig.update_layout(
        title=title,
        template="plotly_white",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0.0),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(l=20, r=20, t=80, b=20),
        height=height,
    )
    return fig


__all__ = [
    "build_attention_topology_graph",
    "filter_attention_topology_graph",
    "focus_topology_subgraph",
    "plot_attention_topology_graph",
    "topology_graph_summary",
]
