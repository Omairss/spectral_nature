from __future__ import annotations

from io import BytesIO

import networkx as nx
import pandas as pd

from services import knowledge_graph as kg
from services import knowledge_graph_proposals as kgp


def _isolated_snapshot(monkeypatch) -> dict[str, object]:
    monkeypatch.setattr(kg, "_db_connect", lambda: None)
    monkeypatch.setattr(kg, "load_embedding_client", lambda: None)
    kg.clear_knowledge_graph_cache()
    return kg.load_knowledge_graph_snapshot()


def test_search_knowledge_graph_nodes_resolves_helium_seed(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    matches = kg.search_knowledge_graph_nodes("helium", snapshot=snapshot)

    assert matches
    assert matches[0]["node_id"] == "helium"
    assert "helium" in {item["node_id"] for item in matches[:3]}


def test_search_knowledge_graph_nodes_uses_context_and_avoids_weak_false_matches(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    matches = kg.search_knowledge_graph_nodes("fertilizer", snapshot=snapshot)
    top_ids = [item["node_id"] for item in matches[:5]]

    assert {"UNG", "CORN", "WEAT", "SOYB"} & set(top_ids)
    assert "uranium" not in top_ids
    assert "nuclear_fuel" not in top_ids


def test_build_knowledge_graph_draft_includes_seed_neighborhood(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    draft = kg.build_knowledge_graph_draft(
        "helium",
        include_agentic_expansion=False,
        snapshot=snapshot,
    )

    node_ids = {row["node_id"] for row in draft["nodes"]}
    edge_ids = {row["edge_id"] for row in draft["edges"]}

    assert "helium" in node_ids
    assert {"industrial_gases", "MRI_systems"} & node_ids
    assert "industrial_gases_packages_and_distributes_helium" in edge_ids


def test_build_knowledge_graph_draft_can_start_from_agentic_only_query(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    monkeypatch.setattr(
        kg,
        "_agentic_graph_expansion",
        lambda **kwargs: {
            "summary": "Expanded a new cryogenic supply concept.",
            "nodes": [
                {
                    "id": "novel_cryogenic_input",
                    "label": "Novel Cryogenic Input",
                    "node_type": "commodity",
                    "description": "Placeholder for a previously unseen cryogenic dependency seed.",
                    "aliases": ["new cryogenic gas"],
                    "confidence": 0.63,
                    "rationale": "The query itself should become a root node when no seed match exists.",
                },
                {
                    "id": "cryogenic_storage",
                    "label": "Cryogenic Storage",
                    "node_type": "infrastructure",
                    "description": "Storage and handling layer for cold-chain gases.",
                    "aliases": ["cryogenic tanks"],
                    "confidence": 0.61,
                    "rationale": "Needed to stage a supply chain for the raw query.",
                }
            ],
            "edges": [
                {
                    "id": "novel_cryogenic_input_enables_cryogenic_storage",
                    "source": "novel_cryogenic_input",
                    "target": "cryogenic_storage",
                    "relationship": "enables",
                    "mechanism": "The raw query implies a cryogenic input that requires storage.",
                    "confidence": 0.59,
                    "severity": 0.4,
                    "conditions": [],
                    "rationale": "Minimal edge to anchor the new concept.",
                }
            ],
            "limitations": [],
        },
    )

    draft = kg.build_knowledge_graph_draft(
        "novel cryogenic input",
        include_agentic_expansion=True,
        snapshot=snapshot,
    )

    node_ids = {row["node_id"] for row in draft["nodes"]}
    edge_ids = {row["edge_id"] for row in draft["edges"]}

    assert draft["selected_node_ids"] == []
    assert "novel_cryogenic_input" in node_ids
    assert "cryogenic_storage" in node_ids
    assert "novel_cryogenic_input_enables_cryogenic_storage" in edge_ids
    assert not draft["limitations"]


def test_normalize_review_tables_marks_deleted_seeded_rows(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)
    draft = kg.build_knowledge_graph_draft(
        "helium",
        include_agentic_expansion=False,
        snapshot=snapshot,
    )

    nodes_frame = kg.draft_nodes_frame(draft)
    edges_frame = kg.draft_edges_frame(draft)

    mri_node_id = str(
        nodes_frame.loc[nodes_frame["label"] == "MRI Systems", "node_id"].iloc[0]
    )
    nodes_frame.loc[nodes_frame["node_id"] == mri_node_id, "keep"] = False
    edges_frame.loc[edges_frame["target"] == mri_node_id, "keep"] = False

    normalized = kg._normalize_review_tables(
        draft=draft,
        nodes_frame=nodes_frame,
        edges_frame=edges_frame,
    )

    assert mri_node_id in normalized["deleted_node_ids"]
    assert any(mri_node_id in edge_id for edge_id in normalized["deleted_edge_ids"])


def test_plot_knowledge_graph_draft_returns_nonempty_figure(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)
    draft = kg.build_knowledge_graph_draft(
        "uranium",
        include_agentic_expansion=False,
        snapshot=snapshot,
    )

    fig = kg.plot_knowledge_graph_draft(draft)

    assert fig.data
    assert fig.layout.title.text == "Knowledge Graph Draft"
    assert fig.layout.annotations
    assert any("->" in str(trace.hovertext) for trace in fig.data if getattr(trace, "hovertext", None) is not None)


def test_build_knowledge_graph_overview_caps_and_plots(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    overview = kg.build_knowledge_graph_overview(snapshot=snapshot, max_nodes=8, max_edges=10)
    fig = kg.plot_knowledge_graph_draft(overview)

    assert overview["title"] == "Knowledge Graph Overview"
    assert len(overview["nodes"]) <= 8
    assert len(overview["edges"]) <= 10
    assert overview["nodes"]
    assert fig.data
    assert fig.layout.title.text == "Knowledge Graph Overview"


def test_component_packed_layout_separates_disconnected_components():
    graph = nx.Graph()
    graph.add_edge("a", "b")
    graph.add_edge("c", "d")
    graph.add_edge("e", "f")

    positions = kg._component_packed_layout(graph)
    centroids = []
    for component in nx.connected_components(graph):
        xs = [positions[node_id][0] for node_id in component]
        ys = [positions[node_id][1] for node_id in component]
        centroids.append((sum(xs) / len(xs), sum(ys) / len(ys)))

    distances = [
        ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5
        for index, left in enumerate(centroids)
        for right in centroids[index + 1 :]
    ]
    assert positions
    assert min(distances) > 2.0


def test_graph_expansion_schema_requires_all_defined_item_properties():
    node_item = kg._GRAPH_EXPANSION_SCHEMA["properties"]["nodes"]["items"]
    edge_item = kg._GRAPH_EXPANSION_SCHEMA["properties"]["edges"]["items"]

    assert set(node_item["required"]) == set(node_item["properties"])
    assert set(edge_item["required"]) == set(edge_item["properties"])


def test_attention_knowledge_graph_proposals_include_reviewable_macro_edges(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)

    proposals = kgp.build_attention_knowledge_graph_proposals(
        run_id="run-kg",
        asof_time_utc=pd.Timestamp("2026-04-27T12:00:00Z"),
        claims_frame=pd.DataFrame(),
        macro_edges_frame=pd.DataFrame(
            [
                {
                    "run_id": "run-kg",
                    "edge_id": "inflation_to_rates",
                    "from_node": "inflation",
                    "to_node": "rates",
                    "expected_sign": 1,
                    "lag_window": "same_day",
                    "regime_filter": "all",
                    "strength_weight": 1.4,
                    "confidence_prior": 0.72,
                }
            ]
        ),
        relationship_checks_frame=pd.DataFrame(
            [{"edge_id": "inflation_to_rates", "consistency_status": "holding"}]
        ),
        snapshot=snapshot,
    )

    edge_rows = proposals[proposals["proposal_type"] == "edge"]

    assert not proposals.empty
    assert {"inflation", "rates"} <= set(proposals["node_id"].astype(str)) | set(edge_rows["source_node_id"].astype(str)) | set(edge_rows["target_node_id"].astype(str))
    assert edge_rows.iloc[0]["operation"] == "add_edge"
    assert edge_rows.iloc[0]["relationship"] == "influences"
    assert edge_rows.iloc[0]["confidence"] == 0.72

    buffer = BytesIO()
    proposals.to_parquet(buffer, index=False)
    assert buffer.getbuffer().nbytes > 0


def test_knowledge_graph_draft_from_attention_proposals_is_editable(monkeypatch):
    snapshot = _isolated_snapshot(monkeypatch)
    proposals = pd.DataFrame(
        [
            {
                "proposal_id": "kgp::node",
                "proposal_type": "node",
                "operation": "add_node",
                "review_status": "proposed",
                "node_id": "optical_networking",
                "label": "Optical Networking",
                "node_type": "technology",
                "confidence": 0.8,
                "rationale": "Extracted from evidence.",
            },
            {
                "proposal_id": "kgp::edge",
                "proposal_type": "edge",
                "operation": "add_edge",
                "review_status": "proposed",
                "source_record_id": "ai_compute_influences_optical_networking",
                "source_node_id": "ai_compute",
                "target_node_id": "optical_networking",
                "relationship": "raises_demand",
                "severity": 0.7,
                "confidence": 0.75,
                "rationale": "Evidence links AI compute demand to optical networking.",
            },
        ]
    )

    draft = kgp.build_knowledge_graph_draft_from_proposals(proposals, snapshot=snapshot)

    assert draft["title"] == "Attention Knowledge Graph Proposals"
    assert "optical_networking" in {row["node_id"] for row in draft["nodes"]}
    assert "ai_compute_influences_optical_networking" in {row["edge_id"] for row in draft["edges"]}
    assert draft["edges"][0]["source_status"] == "agent_suggested"
