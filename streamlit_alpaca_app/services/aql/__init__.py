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
  build_attention_agentic_summary      — search-backed market hypothesis for the homepage
  build_attention_agentic_summary_with_trace — same, plus materializable search/doc/chunk/claim trace
  build_attention_home_narrative_beats — deterministic beats from home_payload
  build_attention_home_summary         — structured summary dict
  build_attention_home_summary_payload — sanitized summary payload
  attach_attention_home_summary_audio  — attach ElevenLabs audio to a summary payload
  attention_mover_card_title           — display title for a mover card

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
from .summarizer import (
    apply_display_limits,
    attach_attention_home_summary_audio,
    attention_mover_card_title,
    build_attention_agentic_summary,
    build_attention_agentic_summary_with_trace,
    build_attention_home_narrative_beats,
    build_attention_home_summary,
    build_attention_home_summary_payload,
)

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
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
]
