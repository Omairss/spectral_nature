from __future__ import annotations

from services import knowledge_graph as kg
from services.entity_extraction import extract_entities, graph_add_node_candidates, linked_kg_node_ids


class FakeEntityLLM:
    def __init__(self, entities):
        self.entities = entities

    def generate_json(self, **kwargs):
        return {"entities": list(self.entities)}


def _snapshot(monkeypatch):
    monkeypatch.setattr(kg, "_db_connect", lambda: None)
    monkeypatch.setattr(kg, "load_embedding_client", lambda: None)
    kg.clear_knowledge_graph_cache()
    return kg.load_knowledge_graph_snapshot()


def test_extract_entities_links_ai_infrastructure_without_full_text_false_matches(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    text = "Bloom Energy-Oracle fuel cell buildout tied to AI data centers and AI infrastructure power demand."
    llm = FakeEntityLLM(
        [
            {
                "text": "Bloom Energy",
                "entity_type": "company",
                "canonical_hint": "Bloom Energy",
                "ticker": "BE",
                "rationale": "Stock-level subject company.",
                "confidence": 0.94,
            },
            {
                "text": "fuel cells",
                "entity_type": "technology",
                "canonical_hint": "Fuel Cells",
                "ticker": "",
                "rationale": "Technology used for data-center power.",
                "confidence": 0.89,
            },
            {
                "text": "Oracle",
                "entity_type": "company",
                "canonical_hint": "Oracle",
                "ticker": "ORCL",
                "rationale": "Named customer in the feed text.",
                "confidence": 0.88,
            },
            {
                "text": "datacenter power",
                "entity_type": "infrastructure",
                "canonical_hint": "Datacenter Power Demand",
                "ticker": "",
                "rationale": "AI data centers create power demand.",
                "confidence": 0.9,
            },
        ]
    )

    mentions = extract_entities(text, subject_symbol="BE", snapshot=snapshot, llm_client=llm)
    linked = set(linked_kg_node_ids(mentions))
    add_candidates = {item["node_id"] for item in graph_add_node_candidates(mentions)}

    assert {"ai_compute", "datacenter_power"}.issubset(linked)
    assert {"BE", "fuel_cells", "ORCL"}.issubset(add_candidates)


def test_extract_entities_does_not_activate_kg_for_uncovered_saas_text(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    text = "Enterprise software and cybersecurity stocks slide as weakness centers on ServiceNow guidance."

    mentions = extract_entities(text, snapshot=snapshot)
    linked = set(linked_kg_node_ids(mentions))

    assert "helium" not in linked
    assert "LIN" not in linked
    assert "LIT" not in linked
    assert "USO" not in linked


def test_extract_entities_links_exact_tickers_and_aliases(monkeypatch):
    snapshot = _snapshot(monkeypatch)
    text = "NVIDIA commentary lifted AI infrastructure and SMR developers."

    mentions = extract_entities(text, symbols=["NVDA", "SMR"], snapshot=snapshot)
    linked = set(linked_kg_node_ids(mentions))

    assert "NVDA" in linked
    assert "SMR" in linked
    assert "ai_compute" in linked
