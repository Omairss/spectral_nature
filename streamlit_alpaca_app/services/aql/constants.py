"""
AQL constants — all module-level constants, schemas, type aliases, and the dataclass.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..llm import (
    AzureOpenAIChatJSONClient,
    AzureOpenAIEmbeddingClient,
    OpenAIChatJSONClient,
    OpenAIEmbeddingClient,
)

LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient
EmbeddingClient = OpenAIEmbeddingClient | AzureOpenAIEmbeddingClient

DEFAULT_PROMPT_VERSION = (os.getenv("ATTENTION_PROMPT_VERSION") or "attention-bottom-up-v1").strip() or "attention-bottom-up-v1"
DEFAULT_WRITER_MODEL = "planner"

LOW_SIGNAL_PHRASES = (
    "other big stocks moving",
    "stocks moving higher",
    "stocks moving lower",
    "market today",
    "stock market today",
)
YIELD_RELEVANT_TAGS = {
    "rates",
    "duration",
    "credit",
    "inflation_proxy",
    "real_rates",
    "treasury",
    "yield",
    "yields",
}
CAUSAL_LANGUAGE_PATTERNS = (
    r"\bbecause\b",
    r"\bdue to\b",
    r"\bafter\b",
    r"\bamid\b",
    r"\bdriven by\b",
    r"\bsuggest(?:s|ing)\b",
    r"\bimply(?:s|ing)\b",
    r"\btherefore\b",
    r"\bwhich (?:lifted|helped|pressured|hurt|weighed|boosted)\b",
    r"\bpressure on\b",
    r"\bmargins?\b",
    r"\bdemand\b",
    r"\binflation\b",
    r"\binput costs?\b",
)
RATE_TRANSMISSION_PATTERNS = (
    r"\bborrow(?:ing|ed)? costs?\b",
    r"\bfinanc(?:e|ing|ed)\b",
    r"\bdiscount rates?\b",
    r"\bvaluation(?:s)?\b",
    r"\brisk appetite\b",
    r"\bcredit conditions?\b",
    r"\bmargins?\b",
    r"\bdemand\b",
    r"\bfuel costs?\b",
    r"\binput costs?\b",
)
RESEARCH_PROVIDER_ERROR_MARKERS = (
    "request failed status=",
    "exceeds your plan's set usage limit",
    "please upgrade your plan",
    "contact support@",
    "invalid api key",
    "unauthorized",
    "forbidden",
    "rate limit",
    "quota exceeded",
)
LOW_SIGNAL_CLAIM_MARKERS = (
    "top wall street analysts changed outlook on top names",
    "for all changes, including upgrades/downgrades",
    "see analyst ratings page",
    "analyst ratings page",
    "other analysts' views on",
    "other analysts&#39; views on",
    "other analysts views on",
)
IRRELEVANT_NEWS_PATTERNS = (
    r"\bdividend[- ]equivalent\b",
    r"\brsu rights?\b",
    r"\bform\s*4\b",
    r"\bbeneficial ownership\b",
    r"\btrust holdings?\b",
    r"\bdirector\b.*\b(adds?|gets?|gains?|disclos(?:e|es|ed)|holds?)\b.*\bshares?\b",
    r"\binsider\b.*\b(holdings?|transactions?|buys?|sells?)\b",
    r"\bspousal share stakes?\b",
)
GENERIC_GRAPH_BUCKETS = {
    "unknown",
    "market",
    "all market",
    "broad commodity market",
    "cluster",
    "macro anchor",
    "equities",
    "commodities",
    "assets",
}

_MACRO_RELEASE_SCHEMA_VERSION = "macro_release_events_1d.v1"
_MACRO_CAUSAL_GRAPH_SCHEMA_VERSION = "macro_causal_graph_edges_v1"
_MACRO_RELATIONSHIP_SCHEMA_VERSION = "macro_relationship_checks_1d.v1"
_MACRO_HYPOTHESES_SCHEMA_VERSION = "attention_hypotheses_1d.v1"
_ATTENTION_MACRO_PROFILE_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "attention_macro_signal_profile.v1.yaml"

_MACRO_RELEASE_EVENT_COLUMNS = [
    "run_id",
    "release_event_id",
    "release_type",
    "release_time_utc",
    "surprise_score",
    "importance_tier",
    "primary_nodes",
    "initial_hypothesis",
    "status",
    "asof_time_utc",
    "schema_version",
    "is_forced_macro_release",
    "promotion_reason",
    "source_dataset",
    "component_series_ids",
    "component_labels",
    "supporting_symbols",
    "surprise_source",
    "surprise_z",
    "cross_asset_reaction_pct",
    "release_direction",
]
_MACRO_RELATIONSHIP_CHECK_COLUMNS = [
    "run_id",
    "release_event_id",
    "release_type",
    "edge_id",
    "from_node",
    "to_node",
    "expected_sign",
    "observed_sign",
    "observed_strength",
    "consistency_status",
    "regime_used",
    "lag_window",
    "strength_weight",
    "confidence_prior",
    "evidence_symbols",
    "asof_time_utc",
    "schema_version",
]
_MACRO_CAUSAL_GRAPH_EDGE_COLUMNS = [
    "run_id",
    "edge_id",
    "from_node",
    "to_node",
    "expected_sign",
    "lag_window",
    "regime_filter",
    "min_abs_change_pct",
    "strength_weight",
    "confidence_prior",
    "target_symbols",
    "source",
    "schema_version",
]
_MACRO_HYPOTHESES_COLUMNS = [
    "run_id",
    "hypothesis_id",
    "candidate_id",
    "release_event_id",
    "hypothesis_text",
    "support_status",
    "support_score",
    "contradiction_score",
    "evidence_count",
    "asof_time_utc",
    "schema_version",
]
_ATTENTION_MACRO_CONTEXT_COLUMNS = [
    "run_id",
    "asof_time_utc",
    "symbol",
    "horizon",
    "macro_alignment_score",
    "macro_conflict_score",
    "macro_signal_count",
    "macro_staleness_hours",
    "schema_version",
]

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "research_subjects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["subject", "role"],
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["kind", "text"],
            },
        },
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["query", "rationale"],
            },
        },
        "official_routes": {"type": "array", "items": {"type": "string"}},
        "priority_entities": {"type": "array", "items": {"type": "string"}},
        "evidence_budget": {"type": "integer"},
    },
    "required": [
        "research_subjects",
        "hypotheses",
        "queries",
        "official_routes",
        "priority_entities",
        "evidence_budget",
    ],
}

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_text": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "claim_entities": {"type": "array", "items": {"type": "string"}},
                    "supports_hypothesis": {"type": "string"},
                    "freshness_class": {"type": "string"},
                    "relevance_score": {"type": "number"},
                    "causal_score": {"type": "number"},
                    "confidence_score": {"type": "number"},
                    "is_same_day": {"type": "boolean"},
                },
                "required": [
                    "claim_text",
                    "claim_type",
                    "claim_entities",
                    "supports_hypothesis",
                    "freshness_class",
                    "relevance_score",
                    "causal_score",
                    "confidence_score",
                    "is_same_day",
                ],
            },
        }
    },
    "required": ["claims"],
}

SEARCH_ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "use_tavily": {"type": "boolean"},
        "tavily_topic": {"type": "string", "enum": ["news", "general"]},
        "tavily_query": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["use_tavily", "tavily_topic", "tavily_query", "reason"],
}

SEARCH_RELEVANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevant_indices": {"type": "array", "items": {"type": "integer"}},
        "reason": {"type": "string"},
    },
    "required": ["relevant_indices", "reason"],
}

MACRO_HYPOTHESIS_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "result_id": {"type": "string"},
                    "label": {"type": "string", "enum": ["support", "contradict", "neutral"]},
                    "confidence": {"type": "number"},
                },
                "required": ["result_id", "label", "confidence"],
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["verdicts", "reason"],
}

SYMBOL_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "surface_summary": {"type": "string"},
        "what_changed_text": {"type": "string"},
        "why_today_text": {"type": "string"},
        "what_else_moved_text": {"type": "string"},
        "background_context_text": {"type": "string"},
    },
    "required": [
        "title",
        "surface_summary",
        "what_changed_text",
        "why_today_text",
        "what_else_moved_text",
        "background_context_text",
    ],
}

EVENT_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "surface_summary": {"type": "string"},
        "what_happened_text": {"type": "string"},
        "why_happened_text": {"type": "string"},
        "affected_assets_summary_text": {"type": "string"},
        "background_context_text": {"type": "string"},
    },
    "required": [
        "title",
        "surface_summary",
        "what_happened_text",
        "why_happened_text",
        "affected_assets_summary_text",
        "background_context_text",
    ],
}

EVENT_WRITER_SYSTEM_PROMPT = (
    "You are a senior cross-asset strategist writing for PMs. "
    "Return concise JSON only. Use only supplied facts and claims; do not invent facts. "
    "Write institutional-quality event summaries that are specific and mechanism-first but NOT wordy."
    "Use clear writing style like The Economist. Avoid using complicated work "
    "Keep surface_summary to at most 4 sentences. "
    "Critical rule for why_happened_text: lead with a causal chain in plain English before any numbers. "
    "Use this structure when evidence supports it: catalyst -> transmission channel -> market pricing reaction. "
    "Transmission channels must be concrete, such as input costs, margins, volumes, funding costs, duration, policy, operations, demand, or risk appetite. "
    "Do not write generic tape titles or generic cluster copy. Keep the title anchored to the strongest supported event theme. "
    "Avoid ticker and percentage tape recaps across all text fields. "
    "Do not list more than two tickers in why_happened_text. "
    "Never open why_happened_text with Treasury, yield, ticker, or percentage statistics. "
    "What_happened_text should summarize the directional relationship across the cluster, not enumerate the tape. "
    "Affected_assets_summary_text should focus on second-order spillover and cross-asset breadth, not restate what_happened_text. "
    "If causality is mixed, say what is uncertain and why in plain language. "
    "When Treasury yield context is relevant, summarize direction and transmission in plain language without quoting bp numbers unless the rate move itself is the event."
)


@dataclass
class AgenticAttentionArtifacts:
    home_payload: dict[str, Any]
    bundle_map: dict[str, dict[str, Any]]
    frames: dict[str, pd.DataFrame]
