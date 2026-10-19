"""Shared attention research, evidence acquisition, and artifact assembly."""
from __future__ import annotations

from .constants import AgenticAttentionArtifacts
from .pipeline import (
    build_bottom_up_attention_artifacts,
    build_bottom_up_attention_bundle,
    build_bottom_up_attention_home,
)
from .clusterer import recompute_attention_candidate_graph
from .collector import search_symbol_news_payload
from .business_model_stack import build_ticker_business_model_stack_frames
from .summarizer import (
    attention_mover_card_title,
    build_attention_home_narrative_beats,
    build_market_stories,
)

__all__ = [
    "AgenticAttentionArtifacts",
    "attention_mover_card_title",
    "build_attention_home_narrative_beats",
    "build_bottom_up_attention_artifacts",
    "build_bottom_up_attention_bundle",
    "build_bottom_up_attention_home",
    "build_market_stories",
    "build_ticker_business_model_stack_frames",
    "recompute_attention_candidate_graph",
    "search_symbol_news_payload",
]
