# Attention home summary interface.
from typing import Any

from .aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio as attach_attention_home_summary_audio,
    build_aql_zopedia_attention_home_summary_with_trace,
)
from .aql import (
    apply_display_limits,
    attention_mover_card_title,
    build_attention_home_narrative_beats,
    build_attention_home_summary,
    build_attention_home_summary_payload,
    critique_home_summary,
    judge_revise_summary,
    verify_hypothesis,
)


def build_attention_agentic_summary_with_trace(*args: Any, **kwargs: Any):
    return build_aql_zopedia_attention_home_summary_with_trace(*args, **kwargs)


def build_attention_agentic_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
    summary, _ = build_aql_zopedia_attention_home_summary_with_trace(*args, **kwargs)
    return summary


__all__ = [
    "apply_display_limits",
    "attach_attention_home_summary_audio",
    "attention_mover_card_title",
    "build_attention_agentic_summary",
    "build_attention_agentic_summary_with_trace",
    "build_attention_home_narrative_beats",
    "build_attention_home_summary",
    "build_attention_home_summary_payload",
    "critique_home_summary",
    "judge_revise_summary",
    "verify_hypothesis",
]
