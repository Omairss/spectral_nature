"""
Entity extraction and linking.

This module turns metadata, claim entities, and optional LLM extraction into
typed entity mentions that can be safely linked to the knowledge graph.
It is deliberately stricter than free-text graph search: graph traversal should
start from explicit entities, not from full narrative blobs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any

from . import knowledge_graph as kg
from .entity_taxonomy import taxonomy_lookup_by_symbol
from .market import COMMODITY_PROXY_METADATA


_ENTITY_TYPES = (
    "company",
    "ticker",
    "commodity",
    "product",
    "technology",
    "policy",
    "infrastructure",
    "macro_concept",
    "supply_chain_input",
    "end_market",
    "theme",
    "other",
)
_ENTITY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "entity_type": {"type": "string", "enum": list(_ENTITY_TYPES)},
                    "canonical_hint": {"type": "string"},
                    "ticker": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "text",
                    "entity_type",
                    "canonical_hint",
                    "ticker",
                    "rationale",
                    "confidence",
                ],
            },
        },
    },
    "required": ["entities"],
}
_UPPER_TOKEN_PATTERN = re.compile(r"(?<![A-Z0-9])\$?([A-Z][A-Z0-9.\-]{1,5})(?![A-Z0-9])")
_COMMON_NON_TICKERS = {
    "AI",
    "API",
    "CEO",
    "CFO",
    "COO",
    "CPI",
    "EPS",
    "ETF",
    "GDP",
    "IPO",
    "LLM",
    "NYSE",
    "SEC",
    "USA",
    "USD",
}
_GENERIC_ENTITY_TERMS = {
    "stock",
    "stocks",
    "share",
    "shares",
    "company",
    "companies",
    "market",
    "markets",
    "demand",
    "supply",
    "growth",
    "risk",
    "sentiment",
    "rally",
    "selloff",
}


def _coerce_text(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    return _coerce_text(value).upper()


@dataclass(frozen=True)
class EntityMention:
    text: str
    entity_type: str
    source: str
    confidence: float
    canonical_id: str = ""
    kg_node_id: str = ""
    kg_label: str = ""
    link_status: str = "unlinked"
    link_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalized_phrase(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(value).lower()).strip()


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _coerce_text(value).lower()).strip("_")


def _safe_float(value: object, *, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return min(max(out, 0.0), 1.0)


def _safe_json_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return [text]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _known_symbols() -> set[str]:
    # Keep this local and non-blocking. Broad universe/taxonomy loading can touch
    # the pipeline store, so feed-provided symbols are handled separately.
    return {_normalize_symbol(symbol) for symbol in COMMODITY_PROXY_METADATA.keys() if _normalize_symbol(symbol)}


def _kg_alias_entries(snapshot: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    entries: list[tuple[str, str, str, str]] = []
    for node_id, node in dict(snapshot.get("nodes_by_id") or {}).items():
        label = _coerce_text(node.get("canonical_label") or node_id)
        aliases = [node_id, label, *list(node.get("aliases") or [])]
        for alias in aliases:
            clean = _coerce_text(alias)
            if not clean:
                continue
            entries.append((clean, _normalized_phrase(clean), _coerce_text(node_id), label))
    return entries


def _exact_alias_matches(text: str, snapshot: dict[str, Any]) -> list[EntityMention]:
    haystack = f" {_normalized_phrase(text)} "
    original = _coerce_text(text)
    mentions: list[EntityMention] = []
    seen: set[tuple[str, str]] = set()
    for alias, normalized_alias, node_id, label in _kg_alias_entries(snapshot):
        if not normalized_alias or normalized_alias in _GENERIC_ENTITY_TERMS:
            continue
        # Short aliases are only safe when they appear as uppercase ticker-like tokens.
        if len(normalized_alias) <= 3:
            if not re.search(r"(?<![A-Z0-9])" + re.escape(alias.upper()) + r"(?![A-Z0-9])", original.upper()):
                continue
        elif f" {normalized_alias} " not in haystack:
            continue
        key = (node_id, normalized_alias)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            EntityMention(
                text=alias,
                entity_type="ticker" if re.fullmatch(r"[A-Z0-9.\-]{2,6}", alias) else "other",
                source="kg_alias",
                confidence=0.94,
                canonical_id=node_id,
                kg_node_id=node_id,
                kg_label=label,
                link_status="linked",
                link_reason="exact KG alias match",
            )
        )
    return mentions


def _ticker_mentions(text: str, *, subject_symbol: object = "") -> list[EntityMention]:
    known = _known_symbols()
    mentions: list[EntityMention] = []
    seen: set[str] = set()
    anchor = _normalize_symbol(subject_symbol)
    if anchor:
        seen.add(anchor)
        mentions.append(
            EntityMention(
                text=anchor,
                entity_type="ticker",
                source="feed_symbol",
                confidence=1.0,
                canonical_id=anchor,
                link_status="symbol_only",
                link_reason="feed subject symbol",
            )
        )
    for token in _UPPER_TOKEN_PATTERN.findall(_coerce_text(text)):
        symbol = _normalize_symbol(token)
        if not symbol or symbol in seen or symbol in _COMMON_NON_TICKERS:
            continue
        if symbol not in known:
            continue
        seen.add(symbol)
        mentions.append(
            EntityMention(
                text=symbol,
                entity_type="ticker",
                source="ticker_regex",
                confidence=0.91,
                canonical_id=symbol,
                link_status="symbol_only",
                link_reason="known ticker token",
            )
        )
    return mentions


def _taxonomy_mentions(symbols: list[str]) -> list[EntityMention]:
    normalized = [_normalize_symbol(symbol) for symbol in symbols if _normalize_symbol(symbol)]
    if not normalized:
        return []
    try:
        lookup = taxonomy_lookup_by_symbol(normalized)
    except Exception:
        lookup = {}
    mentions: list[EntityMention] = []
    for symbol, row in lookup.items():
        name = _coerce_text(row.get("security_name"))
        if not name:
            continue
        mentions.append(
            EntityMention(
                text=name,
                entity_type="company" if _coerce_text(row.get("security_type")) != "etf" else "ticker",
                source="entity_taxonomy",
                confidence=0.86,
                canonical_id=symbol,
                link_status="symbol_only",
                link_reason="entity taxonomy lookup",
                metadata={"symbol": symbol, "sector": _coerce_text(row.get("sector")), "industry": _coerce_text(row.get("industry"))},
            )
        )
    return mentions


def _claim_entity_mentions(claim_entities: object) -> list[EntityMention]:
    mentions: list[EntityMention] = []
    for value in _safe_json_list(claim_entities):
        text = _coerce_text(value)
        if not text or _normalized_phrase(text) in _GENERIC_ENTITY_TERMS:
            continue
        mentions.append(
            EntityMention(
                text=text,
                entity_type="other",
                source="claim_entity",
                confidence=0.78,
                canonical_id=_slug(text),
                link_status="unlinked",
                link_reason="claim entity",
            )
        )
    return mentions


def _llm_entity_mentions(text: str, *, llm_client: Any | None) -> list[EntityMention]:
    if llm_client is None or not _coerce_text(text):
        return []
    try:
        data = llm_client.generate_json(
            system_prompt=(
                "Extract finance-relevant named entities from market text. "
                "Prefer companies, tickers, commodities, products, technologies, policies, infrastructure, macro concepts, "
                "supply-chain inputs, end markets, and durable themes. "
                "Do not return generic words such as stock, market, company, demand, supply, growth, risk, or sentiment."
            ),
            user_prompt=json.dumps({"text": _coerce_text(text)[:4000]}, ensure_ascii=False),
            schema_name="aql_entity_extraction",
            schema=_ENTITY_EXTRACTION_SCHEMA,
        )
    except Exception:
        return []
    mentions: list[EntityMention] = []
    for item in list(data.get("entities") or [])[:24]:
        if not isinstance(item, dict):
            continue
        mention_text = _coerce_text(item.get("text"))
        normalized = _normalized_phrase(mention_text)
        if not mention_text or normalized in _GENERIC_ENTITY_TERMS:
            continue
        entity_type = _coerce_text(item.get("entity_type")) or "other"
        if entity_type not in _ENTITY_TYPES:
            entity_type = "other"
        ticker = _normalize_symbol(item.get("ticker"))
        canonical_hint = _coerce_text(item.get("canonical_hint"))
        mentions.append(
            EntityMention(
                text=mention_text,
                entity_type=entity_type,
                source="llm",
                confidence=_safe_float(item.get("confidence"), default=0.7),
                canonical_id=ticker or _slug(canonical_hint or mention_text),
                link_status="unlinked",
                link_reason=_coerce_text(item.get("rationale")),
                metadata={"ticker": ticker, "canonical_hint": canonical_hint},
            )
        )
    return mentions


def _link_mention_to_kg(mention: EntityMention, snapshot: dict[str, Any]) -> EntityMention:
    if mention.kg_node_id:
        return mention
    nodes_by_id = dict(snapshot.get("nodes_by_id") or {})
    candidates = [_coerce_text(mention.canonical_id), _coerce_text(mention.metadata.get("ticker") if isinstance(mention.metadata, dict) else ""), mention.text]
    aliases = _kg_alias_entries(snapshot)
    for candidate in candidates:
        if not candidate:
            continue
        normalized_candidate = _normalized_phrase(candidate)
        candidate_symbol = _normalize_symbol(candidate)
        if candidate_symbol in nodes_by_id:
            node = nodes_by_id[candidate_symbol]
            return EntityMention(
                **{**mention.to_dict(), "kg_node_id": candidate_symbol, "kg_label": _coerce_text(node.get("canonical_label") or candidate_symbol), "link_status": "linked", "link_reason": "exact KG node id match"}
            )
        for alias, normalized_alias, node_id, label in aliases:
            if normalized_candidate and normalized_candidate == normalized_alias:
                return EntityMention(
                    **{**mention.to_dict(), "kg_node_id": node_id, "kg_label": label, "link_status": "linked", "link_reason": f"exact KG alias match: {alias}"}
                )
    return mention


def _dedupe_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    best: dict[tuple[str, str, str], EntityMention] = {}
    for mention in mentions:
        key = (
            _coerce_text(mention.kg_node_id or mention.canonical_id or _slug(mention.text)),
            _normalized_phrase(mention.text),
            _coerce_text(mention.entity_type),
        )
        existing = best.get(key)
        if existing is None or mention.confidence > existing.confidence:
            best[key] = mention
    return sorted(best.values(), key=lambda item: (item.link_status != "linked", -item.confidence, item.text.lower()))


def extract_entities(
    text: object,
    *,
    subject_symbol: object = "",
    symbols: list[str] | None = None,
    claim_entities: object = None,
    snapshot: dict[str, Any] | None = None,
    llm_client: Any | None = None,
    include_taxonomy: bool = False,
) -> list[EntityMention]:
    """Extract and link finance-relevant entities for AQL/KG use.

    This function is intentionally conservative. It returns unlinked LLM/claim
    mentions for graph add-node proposals, but only linked mentions should drive
    knowledge-graph traversal.
    """
    clean_text = _coerce_text(text)
    snapshot = snapshot or kg.load_knowledge_graph_snapshot()
    metadata_symbols = [_normalize_symbol(value) for value in list(symbols or []) if _normalize_symbol(value)]
    anchor = _normalize_symbol(subject_symbol)
    if anchor and anchor not in metadata_symbols:
        metadata_symbols.insert(0, anchor)

    mentions: list[EntityMention] = []
    mentions.extend(_ticker_mentions(clean_text, subject_symbol=anchor))
    for symbol in metadata_symbols:
        if symbol and symbol != anchor:
            mentions.append(
                EntityMention(
                    text=symbol,
                    entity_type="ticker",
                    source="feed_symbols",
                    confidence=0.96,
                    canonical_id=symbol,
                    link_status="symbol_only",
                    link_reason="feed member symbol",
                )
            )
    if include_taxonomy:
        mentions.extend(_taxonomy_mentions(metadata_symbols))
    mentions.extend(_exact_alias_matches(clean_text, snapshot))
    mentions.extend(_claim_entity_mentions(claim_entities))
    mentions.extend(_llm_entity_mentions(clean_text, llm_client=llm_client))
    linked = [_link_mention_to_kg(mention, snapshot) for mention in mentions]
    return _dedupe_mentions(linked)


def linked_kg_node_ids(mentions: list[EntityMention], *, min_confidence: float = 0.75) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for mention in mentions:
        node_id = _coerce_text(mention.kg_node_id)
        if not node_id or mention.confidence < min_confidence or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node_id)
    return out


def graph_add_node_candidates(mentions: list[EntityMention], *, min_confidence: float = 0.7) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mention in mentions:
        if mention.kg_node_id or mention.confidence < min_confidence:
            continue
        if mention.entity_type in {"ticker"} and mention.source not in {"feed_symbol", "feed_symbols", "llm"}:
            continue
        node_id = _coerce_text(mention.canonical_id) or _slug(mention.text)
        if not node_id or node_id in seen or _normalized_phrase(mention.text) in _GENERIC_ENTITY_TERMS:
            continue
        seen.add(node_id)
        rows.append(
            {
                "node_id": node_id,
                "label": mention.text,
                "node_type": mention.entity_type,
                "confidence": mention.confidence,
                "source": mention.source,
                "reason": mention.link_reason,
                "metadata": dict(mention.metadata or {}),
            }
        )
    return rows


AqlEntityMention = EntityMention
extract_aql_entities = extract_entities


__all__ = [
    "AqlEntityMention",
    "EntityMention",
    "extract_aql_entities",
    "extract_entities",
    "graph_add_node_candidates",
    "linked_kg_node_ids",
]
