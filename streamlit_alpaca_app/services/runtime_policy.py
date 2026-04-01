from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from typing import Callable


def _env_text(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip()
    return value if value else default


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(value, int(minimum))
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    try:
        value = float((os.getenv(name) or str(default)).strip())
    except Exception:
        value = float(default)
    if minimum is not None:
        value = max(value, float(minimum))
    return value


def _env_csv(
    name: str,
    default: str,
    *,
    normalize: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    raw = (os.getenv(name) or default).strip()
    text = raw if raw else default
    items: list[str] = []
    seen: set[str] = set()
    for token in str(text).split(","):
        value = token.strip()
        if not value:
            continue
        normalized = normalize(value) if normalize is not None else value
        normalized = str(normalized).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
    return tuple(items)


def _env_int_csv(
    name: str,
    default: str,
    *,
    minimum: int = 1,
) -> tuple[int, ...]:
    values: list[int] = []
    for token in _env_csv(name, default):
        try:
            value = int(str(token).strip())
        except Exception:
            continue
        values.append(max(value, int(minimum)))
    return tuple(values)


@dataclass(frozen=True)
class AttentionCandidatePolicy:
    shortlist_default_max_count: int
    shortlist_liquidity_min_dollar_volume: float
    shortlist_macro_anchor_min_abs_change_pct: float
    confidence_high_abs_change_pct: float
    confidence_high_abs_surprise_z: float
    confidence_high_best_authority_rank_max: int
    confidence_high_evidence_min: int
    confidence_medium_abs_change_pct: float
    confidence_medium_abs_surprise_z: float
    move_score_mult: float
    move_score_cap: float
    surprise_score_mult: float
    surprise_score_cap: float
    surprise_fallback_mult: float
    surprise_fallback_cap: float
    liquidity_log10_offset: float
    liquidity_score_mult: float
    liquidity_score_cap: float
    attention_bonus_mult: float
    attention_bonus_cap: float
    macro_bonus: float
    portfolio_bonus: float
    evidence_score_base: float
    evidence_score_per_item: float
    evidence_score_cap: float
    authority_bonus_official: float
    authority_bonus_wire: float
    authority_bonus_press: float


@dataclass(frozen=True)
class AttentionGraphPolicy:
    peer_group_weight: float
    sector_weight: float
    tag_overlap_mult: float
    tag_overlap_cap: float
    claim_overlap_mult: float
    claim_overlap_cap: float
    history_corr_min: float
    history_corr_mult: float
    history_corr_cap: float
    history_corr_min_observations: int
    opposite_direction_bonus: float
    same_direction_bonus: float
    min_edge_weight: float


@dataclass(frozen=True)
class TaxonomyClassifierPolicy:
    classifier_version: str
    default_country: str
    default_batch_size: int
    repair_retry_batch_sizes: tuple[int, ...]
    allowed_sectors: tuple[str, ...]
    allow_unknown_prompt: str
    force_classify_prompt: str


@dataclass(frozen=True)
class SourceAuthorityPolicy:
    official_tokens: tuple[str, ...]
    wire_tokens: tuple[str, ...]
    press_tokens: tuple[str, ...]


@dataclass(frozen=True)
class AttentionUIPolicy:
    horizon_options: tuple[str, ...]
    horizon_labels: dict[str, str]
    sensitivity_order: tuple[str, ...]


@lru_cache(maxsize=1)
def attention_candidate_policy() -> AttentionCandidatePolicy:
    return AttentionCandidatePolicy(
        shortlist_default_max_count=_env_int("ATTENTION_SHORTLIST_DEFAULT_MAX_COUNT", 100, minimum=1),
        shortlist_liquidity_min_dollar_volume=_env_float(
            "ATTENTION_SHORTLIST_LIQUIDITY_MIN_DOLLAR_VOLUME",
            25_000_000.0,
            minimum=0.0,
        ),
        shortlist_macro_anchor_min_abs_change_pct=_env_float(
            "ATTENTION_SHORTLIST_MACRO_ANCHOR_MIN_ABS_CHANGE_PCT",
            1.0,
            minimum=0.0,
        ),
        confidence_high_abs_change_pct=_env_float("ATTENTION_CONFIDENCE_HIGH_ABS_CHANGE_PCT", 6.0, minimum=0.0),
        confidence_high_abs_surprise_z=_env_float("ATTENTION_CONFIDENCE_HIGH_ABS_SURPRISE_Z", 2.0, minimum=0.0),
        confidence_high_best_authority_rank_max=_env_int("ATTENTION_CONFIDENCE_HIGH_MAX_AUTHORITY_RANK", 1, minimum=0),
        confidence_high_evidence_min=_env_int("ATTENTION_CONFIDENCE_HIGH_EVIDENCE_MIN", 2, minimum=0),
        confidence_medium_abs_change_pct=_env_float("ATTENTION_CONFIDENCE_MEDIUM_ABS_CHANGE_PCT", 4.0, minimum=0.0),
        confidence_medium_abs_surprise_z=_env_float("ATTENTION_CONFIDENCE_MEDIUM_ABS_SURPRISE_Z", 1.2, minimum=0.0),
        move_score_mult=_env_float("ATTENTION_CANDIDATE_MOVE_SCORE_MULT", 6.0, minimum=0.0),
        move_score_cap=_env_float("ATTENTION_CANDIDATE_MOVE_SCORE_CAP", 75.0, minimum=0.0),
        surprise_score_mult=_env_float("ATTENTION_CANDIDATE_SURPRISE_SCORE_MULT", 11.0, minimum=0.0),
        surprise_score_cap=_env_float("ATTENTION_CANDIDATE_SURPRISE_SCORE_CAP", 45.0, minimum=0.0),
        surprise_fallback_mult=_env_float("ATTENTION_CANDIDATE_SURPRISE_FALLBACK_MULT", 2.5, minimum=0.0),
        surprise_fallback_cap=_env_float("ATTENTION_CANDIDATE_SURPRISE_FALLBACK_CAP", 18.0, minimum=0.0),
        liquidity_log10_offset=_env_float("ATTENTION_CANDIDATE_LIQUIDITY_LOG10_OFFSET", 6.0),
        liquidity_score_mult=_env_float("ATTENTION_CANDIDATE_LIQUIDITY_SCORE_MULT", 6.5, minimum=0.0),
        liquidity_score_cap=_env_float("ATTENTION_CANDIDATE_LIQUIDITY_SCORE_CAP", 26.0, minimum=0.0),
        attention_bonus_mult=_env_float("ATTENTION_CANDIDATE_ATTENTION_BONUS_MULT", 0.15, minimum=0.0),
        attention_bonus_cap=_env_float("ATTENTION_CANDIDATE_ATTENTION_BONUS_CAP", 18.0, minimum=0.0),
        macro_bonus=_env_float("ATTENTION_CANDIDATE_MACRO_BONUS", 10.0, minimum=0.0),
        portfolio_bonus=_env_float("ATTENTION_CANDIDATE_PORTFOLIO_BONUS", 6.0, minimum=0.0),
        evidence_score_base=_env_float("ATTENTION_CANDIDATE_EVIDENCE_SCORE_BASE", 6.0, minimum=0.0),
        evidence_score_per_item=_env_float("ATTENTION_CANDIDATE_EVIDENCE_SCORE_PER_ITEM", 2.0, minimum=0.0),
        evidence_score_cap=_env_float("ATTENTION_CANDIDATE_EVIDENCE_SCORE_CAP", 8.0, minimum=0.0),
        authority_bonus_official=_env_float("ATTENTION_CANDIDATE_AUTHORITY_BONUS_OFFICIAL", 8.0, minimum=0.0),
        authority_bonus_wire=_env_float("ATTENTION_CANDIDATE_AUTHORITY_BONUS_WIRE", 6.0, minimum=0.0),
        authority_bonus_press=_env_float("ATTENTION_CANDIDATE_AUTHORITY_BONUS_PRESS", 3.0, minimum=0.0),
    )


@lru_cache(maxsize=1)
def attention_graph_policy() -> AttentionGraphPolicy:
    return AttentionGraphPolicy(
        peer_group_weight=_env_float("ATTENTION_GRAPH_PEER_GROUP_WEIGHT", 0.40, minimum=0.0),
        sector_weight=_env_float("ATTENTION_GRAPH_SECTOR_WEIGHT", 0.18, minimum=0.0),
        tag_overlap_mult=_env_float("ATTENTION_GRAPH_TAG_OVERLAP_MULT", 0.45, minimum=0.0),
        tag_overlap_cap=_env_float("ATTENTION_GRAPH_TAG_OVERLAP_CAP", 0.30, minimum=0.0),
        claim_overlap_mult=_env_float("ATTENTION_GRAPH_CLAIM_OVERLAP_MULT", 0.55, minimum=0.0),
        claim_overlap_cap=_env_float("ATTENTION_GRAPH_CLAIM_OVERLAP_CAP", 0.35, minimum=0.0),
        history_corr_min=_env_float("ATTENTION_GRAPH_HISTORY_CORR_MIN", 0.50, minimum=0.0),
        history_corr_mult=_env_float("ATTENTION_GRAPH_HISTORY_CORR_MULT", 1.60, minimum=0.0),
        history_corr_cap=_env_float("ATTENTION_GRAPH_HISTORY_CORR_CAP", 0.45, minimum=0.0),
        history_corr_min_observations=_env_int("ATTENTION_GRAPH_HISTORY_CORR_MIN_OBS", 60, minimum=5),
        opposite_direction_bonus=_env_float("ATTENTION_GRAPH_OPPOSITE_DIRECTION_BONUS", 0.06, minimum=0.0),
        same_direction_bonus=_env_float("ATTENTION_GRAPH_SAME_DIRECTION_BONUS", 0.04, minimum=0.0),
        min_edge_weight=_env_float("ATTENTION_GRAPH_MIN_EDGE_WEIGHT", 0.42, minimum=0.0),
    )


@lru_cache(maxsize=1)
def taxonomy_classifier_policy() -> TaxonomyClassifierPolicy:
    default_allowed_sectors = (
        "Unknown",
        "Communication Services",
        "Consumer Discretionary",
        "Consumer Staples",
        "Credit",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Information Technology",
        "Materials",
        "Real Estate",
        "Utilities",
        "Broad Market",
        "Commodities",
        "Rates",
    )
    allowed_sectors = _env_csv(
        "ENTITY_TAXONOMY_ALLOWED_SECTORS",
        ",".join(default_allowed_sectors),
    ) or default_allowed_sectors
    if "Unknown" not in allowed_sectors:
        allowed_sectors = ("Unknown",) + tuple(value for value in allowed_sectors if value != "Unknown")

    default_batch_size = _env_int("ENTITY_TAXONOMY_LLM_BATCH_SIZE", 25, minimum=1)
    retry_defaults = tuple(
        value
        for value in (
            max(default_batch_size // 2, 1),
            max(default_batch_size // 5, 1),
            1,
        )
        if value > 0
    )
    retry_batch_sizes = _env_int_csv(
        "ENTITY_TAXONOMY_REPAIR_RETRY_BATCH_SIZES",
        ",".join(str(value) for value in retry_defaults),
        minimum=1,
    ) or retry_defaults
    if 1 not in retry_batch_sizes:
        retry_batch_sizes = tuple(list(retry_batch_sizes) + [1])

    return TaxonomyClassifierPolicy(
        classifier_version=_env_text("ENTITY_TAXONOMY_CLASSIFIER_VERSION", "llm_taxonomy_v2"),
        default_country=_env_text("ENTITY_TAXONOMY_DEFAULT_COUNTRY", "US").upper(),
        default_batch_size=default_batch_size,
        repair_retry_batch_sizes=retry_batch_sizes,
        allowed_sectors=tuple(allowed_sectors),
        allow_unknown_prompt=_env_text(
            "ENTITY_TAXONOMY_PROMPT_ALLOW_UNKNOWN",
            (
                "You classify US-listed securities into a lightweight internal taxonomy. "
                "Use only the provided symbol, exchange, ETF flag, and security name. "
                "Return a short readable industry, 1-4 reusable business_role_tags in snake_case for what the security is or does, "
                "and 0-4 reusable macro_role_tags in snake_case for cross-asset exposure channels only when they are genuinely material. "
                "Use commodity_role, rates_role, and defensive_role only when clearly applicable, otherwise return ''. "
                "If you are not confident, set sector to 'Unknown', industry to 'Unknown', business_role_tags to [], macro_role_tags to [], "
                "and leave the role fields blank."
            ),
        ),
        force_classify_prompt=_env_text(
            "ENTITY_TAXONOMY_PROMPT_FORCE_CLASSIFY",
            (
                "You classify US-listed securities into a lightweight internal taxonomy. "
                "Use only the provided symbol, exchange, ETF flag, and security name. "
                "You must choose the closest best-fit sector from the allowed list, provide a non-empty readable industry, "
                "and provide at least one reusable business_role_tag in snake_case. "
                "Use 0-4 macro_role_tags in snake_case only when they are clearly useful for cross-asset reasoning. "
                "Use commodity_role, rates_role, and defensive_role only when clearly applicable, otherwise return ''. "
                "Do not use 'Unknown' for sector or industry in this pass. Use low confidence when uncertain."
            ),
        ),
    )


@lru_cache(maxsize=1)
def source_authority_policy() -> SourceAuthorityPolicy:
    official_defaults = (
        "sec",
        "edgar",
        "federal reserve",
        "bureau of labor statistics",
        "bls",
        "u.s. treasury",
        "treasury",
        "fred",
        "st. louis fed",
        "investor relations",
        "investors",
        "press release",
        "company ir",
        "company release",
    )
    wire_defaults = ("reuters", "associated press", "ap", "dow jones", "bloomberg")
    press_defaults = (
        "wall street journal",
        "wsj",
        "financial times",
        "benzinga",
        "marketwatch",
        "cnbc",
        "barrons",
        "seeking alpha",
        "yahoo finance",
        "investing.com",
        "tipranks",
        "fool",
        "fortune",
    )
    return SourceAuthorityPolicy(
        official_tokens=_env_csv(
            "ATTENTION_SOURCE_AUTHORITY_OFFICIAL_TOKENS",
            ",".join(official_defaults),
            normalize=lambda value: value.lower(),
        )
        or official_defaults,
        wire_tokens=_env_csv(
            "ATTENTION_SOURCE_AUTHORITY_WIRE_TOKENS",
            ",".join(wire_defaults),
            normalize=lambda value: value.lower(),
        )
        or wire_defaults,
        press_tokens=_env_csv(
            "ATTENTION_SOURCE_AUTHORITY_PRESS_TOKENS",
            ",".join(press_defaults),
            normalize=lambda value: value.lower(),
        )
        or press_defaults,
    )


@lru_cache(maxsize=1)
def attention_ui_policy() -> AttentionUIPolicy:
    default_horizons = ("1d", "1w", "1mo", "3mo", "1yr")
    default_labels = {
        "1d": "1 Day",
        "1w": "1 Week",
        "1mo": "1 Month",
        "3mo": "3 Month",
        "1yr": "1 Year",
    }
    default_sensitivity = ("aggressive", "balanced", "conservative")

    raw_horizons = _env_csv(
        "ATTENTION_UI_HORIZON_OPTIONS",
        ",".join(default_horizons),
        normalize=lambda value: value.lower(),
    )
    allowed_horizons = set(default_horizons)
    horizon_options = tuple(value for value in raw_horizons if value in allowed_horizons) or default_horizons

    raw_sensitivity = _env_csv(
        "ATTENTION_UI_SENSITIVITY_ORDER",
        ",".join(default_sensitivity),
        normalize=lambda value: value.lower(),
    )
    allowed_sensitivity = set(default_sensitivity)
    sensitivity_order = tuple(value for value in raw_sensitivity if value in allowed_sensitivity) or default_sensitivity

    horizon_labels = {value: default_labels.get(value, value) for value in horizon_options}
    return AttentionUIPolicy(
        horizon_options=horizon_options,
        horizon_labels=horizon_labels,
        sensitivity_order=sensitivity_order,
    )


__all__ = [
    "AttentionCandidatePolicy",
    "AttentionGraphPolicy",
    "AttentionUIPolicy",
    "SourceAuthorityPolicy",
    "TaxonomyClassifierPolicy",
    "attention_candidate_policy",
    "attention_graph_policy",
    "attention_ui_policy",
    "source_authority_policy",
    "taxonomy_classifier_policy",
]
