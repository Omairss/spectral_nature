from __future__ import annotations

import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.dependency_graphs import dependency_graph_catalog, dependency_graph_edges_frame, validate_dependency_graph_payload
from services.market import commodity_dependency_graph


def test_dependency_graph_catalog_lists_seed_graphs():
    catalog = dependency_graph_catalog(tags=["commodities"])

    assert not catalog.empty
    assert {"graph_id", "title", "node_count", "edge_count", "path"} <= set(catalog.columns)
    assert {"commodity_energy_inputs", "commodity_metals_electrification"} <= set(catalog["graph_id"])


def test_dependency_graph_edges_frame_filters_by_tags_and_allowed_nodes():
    edges = dependency_graph_edges_frame(tags=["commodities"], allowed_node_ids=["USO", "UNG", "CORN"])

    assert not edges.empty
    assert {"USO", "UNG", "CORN"} >= set(edges["source"]).union(set(edges["target"]))
    assert ((edges["source"] == "USO") & (edges["target"] == "UNG")).any()
    assert ((edges["source"] == "UNG") & (edges["target"] == "CORN")).any()


def test_commodity_dependency_graph_uses_json_backed_graphs():
    graph = commodity_dependency_graph(["CPER", "DBB", "REMX"])

    assert not graph.empty
    assert {"graph_id", "graph_title", "relation", "weight", "description"} <= set(graph.columns)
    assert (graph["graph_id"] == "commodity_metals_electrification").any()
    assert ((graph["source"] == "CPER") & (graph["target"] == "DBB")).any()


def test_validate_dependency_graph_payload_rejects_edges_that_reference_unknown_nodes():
    payload = {
        "schema_version": "1.0.0",
        "graph": {
            "id": "bad_graph",
            "title": "Bad Graph",
            "nodes": [
                {"id": "A", "type": "node", "label": "Node A"},
            ],
            "edges": [
                {"id": "edge_1", "source": "A", "target": "B", "relationship": "supports"},
            ],
        },
    }

    with pytest.raises(ValueError, match="unknown node"):
        validate_dependency_graph_payload(payload)
