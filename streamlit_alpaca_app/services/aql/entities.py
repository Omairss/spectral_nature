"""
Compatibility shim for the independent entity extraction system.

New code should import from `services.entity_extraction`.
"""
from __future__ import annotations

from ..entity_extraction import (
    AqlEntityMention,
    EntityMention,
    extract_aql_entities,
    extract_entities,
    graph_add_node_candidates,
    linked_kg_node_ids,
)

__all__ = [
    "AqlEntityMention",
    "EntityMention",
    "extract_aql_entities",
    "extract_entities",
    "graph_add_node_candidates",
    "linked_kg_node_ids",
]
