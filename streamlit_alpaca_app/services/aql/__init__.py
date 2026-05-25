"""
AQL — Attention Query Layer
===========================
Consolidated intelligence layer for the attention pipeline.

Covers the full research → synthesis → summarization stack:
  - Research planning (hypotheses, queries)
  - Evidence collection (web search, filings, FRED)
  - Claim extraction
  - Bundle writing (symbol + event narratives)
  - Event clustering + macro analysis
  - Home payload assembly
  - Deterministic and agentic homepage summarization + ElevenLabs audio

Public API
----------
Pipeline:
  build_bottom_up_attention_artifacts  — main entry point, returns AgenticAttentionArtifacts
  build_bottom_up_attention_home       — return home_payload only
  build_bottom_up_attention_bundle     — retrieve a specific bundle
  recompute_attention_candidate_graph  — rebuild candidate correlation graph

Evidence collection:
  search_symbol_news_payload           — fetch news for a symbol via SerpAPI/Tavily

Summarization:
  build_attention_agentic_summary      — engine-backed market hypothesis for the homepage
  build_attention_agentic_summary_with_trace — same, plus materializable search/doc/chunk/claim/AQL-Zopedia trace
  verify_hypothesis                    — grade a hypothesis against its claims (support/weak/conflicting/unsupported)
  critique_home_summary                — agentic fact-check loop over a freshly built summary
  judge_revise_summary                 — apply critique findings to produce a revised summary
  build_attention_home_narrative_beats — deterministic beats from home_payload
  build_attention_home_summary         — structured summary dict
  build_attention_home_summary_payload — sanitized summary payload
  attach_attention_home_summary_audio  — attach ElevenLabs audio through the AQL/Zopedia engine boundary
  attention_mover_card_title           — display title for a mover card

Chat log:
  log_chat_session                     — persist an agent session to Postgres + blob
  load_chat_session                    — retrieve a full session by run_id
  list_chat_sessions                   — list recent sessions with filters
  count_chat_sessions                  — count sessions matching filters
  bootstrap_chat_log                   — create the aql_chat_sessions table

Types:
  AgenticAttentionArtifacts            — dataclass returned by the pipeline
"""
from __future__ import annotations

from .constants import AgenticAttentionArtifacts
from .pipeline import (
    build_bottom_up_attention_artifacts,
    build_bottom_up_attention_bundle,
    build_bottom_up_attention_home,
)
from .clusterer import recompute_attention_candidate_graph
from .collector import search_symbol_news_payload
from .collector import _candidate_context_documents, _search_query_results
from .config import _load_search_clients
from .extractor import _chunk_source_documents, _documents_from_search_results, _fallback_claims_from_chunks
from .writer import _fallback_event_writer, _write_event_bundle
from ._shared import _augment_candidate_frame
from compute.signal_extraction import _history_correlation_map
from ..attention_signal_graph import _graph_edges
from ..aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio as attach_attention_home_summary_audio,
    build_aql_zopedia_attention_home_summary_with_trace,
)
from .summarizer import (
    apply_display_limits,
    attention_mover_card_title,
    build_attention_home_narrative_beats,
    build_attention_home_summary,
    build_attention_home_summary_payload,
    build_market_stories,
    verify_hypothesis,
)
from .critique import critique_home_summary, judge_revise_summary
from ..agents.chat_log import (
    bootstrap_chat_log,
    count_chat_sessions,
    list_chat_sessions,
    load_chat_session,
    log_chat_session,
)


def build_attention_agentic_summary_with_trace(*args, **kwargs):
    return build_aql_zopedia_attention_home_summary_with_trace(*args, **kwargs)


def build_attention_agentic_summary(*args, **kwargs):
    summary, _ = build_aql_zopedia_attention_home_summary_with_trace(*args, **kwargs)
    return summary


__all__ = [
    "AgenticAttentionArtifacts",
    "apply_display_limits",
    "attach_attention_home_summary_audio",
    "attention_mover_card_title",
    "build_attention_agentic_summary",
    "build_attention_agentic_summary_with_trace",
    "build_attention_home_narrative_beats",
    "build_attention_home_summary",
    "build_attention_home_summary_payload",
    "build_bottom_up_attention_artifacts",
    "bootstrap_chat_log",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "build_market_stories",
    "count_chat_sessions",
    "critique_home_summary",
    "judge_revise_summary",
    "list_chat_sessions",
    "load_chat_session",
    "log_chat_session",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
    "verify_hypothesis",
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
