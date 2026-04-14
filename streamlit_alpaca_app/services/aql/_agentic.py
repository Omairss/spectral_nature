"""
AQL _agentic — thin shim.

All implementation has been moved to focused sub-modules:
  constants, config, _shared, collector, extractor, writer,
  clusterer, macro, assembler, summarizer, pipeline.

This file re-exports the public API and private functions used by
downstream modules (e.g. attention_agentic.py) for backward compatibility.
"""
from __future__ import annotations

from .constants import AgenticAttentionArtifacts
from .pipeline import (
    build_bottom_up_attention_artifacts,
    build_bottom_up_attention_bundle,
    build_bottom_up_attention_home,
)
from .clusterer import recompute_attention_candidate_graph, _graph_edges
from .collector import (
    search_symbol_news_payload,
    _candidate_context_documents,
    _search_query_results,
)
from .extractor import _chunk_source_documents, _documents_from_search_results, _fallback_claims_from_chunks
from .writer import _write_event_bundle, _fallback_event_writer
from ._shared import _augment_candidate_frame, _history_correlation_map
from .config import _load_search_clients

__all__ = [
    "AgenticAttentionArtifacts",
    "build_bottom_up_attention_artifacts",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
    "_augment_candidate_frame",
    "_candidate_context_documents",
    "_chunk_source_documents",
    "_documents_from_search_results",
    "_fallback_claims_from_chunks",
    "_fallback_event_writer",
    "_graph_edges",
    "_history_correlation_map",
    "_load_search_clients",
    "_search_query_results",
    "_write_event_bundle",
]
