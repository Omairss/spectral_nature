"""Shared schemas and constants for service module boundaries."""
from __future__ import annotations

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

_MACRO_RELATIONSHIP_SCHEMA_VERSION = "macro_relationship_checks_1d.v1"

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

HYPOTHESIS_VERIFICATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["supported", "weak", "conflicting", "unsupported"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "supporting_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
        "contradicting_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
        "gap_queries": {
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
        "reasoning": {"type": "string"},
    },
    "required": [
        "verdict",
        "confidence",
        "supporting_claims",
        "contradicting_claims",
        "gap_queries",
        "reasoning",
    ],
}
