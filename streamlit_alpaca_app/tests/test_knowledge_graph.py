from __future__ import annotations

from services import knowledge_graph as kg


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
