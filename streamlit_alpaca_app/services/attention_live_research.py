from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

import pandas as pd

from .common.market_activity import _judge_cause_status as _shared_judge_cause_status
from .common.market_activity import _quality_label as _shared_quality_label
from .common.news_freshness import (
    coerce_article_published_at,
    is_recent_for_attention,
)
from .aql import search_symbol_news_payload
from .attention_agentic import build_bottom_up_attention_bundle
from .attention_home_1d import build_attention_research_bundle
from .llm import NARRATIVE_STYLE_RULE, AzureOpenAIChatJSONClient, LLMAPIError, OpenAIChatJSONClient, get_prompt, register_narrative_prompt
from .runtime_policy import source_authority_policy
from .web_research import (
    SerperSearchClient,
    SerpAPISearchClient,
    TavilySearchClient,
    WebResearchError,
    load_serper_config,
    load_serpapi_config,
    load_tavily_config,
)
from .aql_zopedia_engine import load_aql_zopedia_llm_client


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient

MOVE_WORD_TOKENS = [
    "stock",
    "shares",
    "rose",
    "rise",
    "rises",
    "rallied",
    "jumped",
    "surged",
    "fell",
    "fall",
    "falls",
    "dropped",
    "drop",
    "drops",
    "slid",
    "gained",
    "gain",
    "gains",
    "higher",
    "lower",
    "climbed",
    "climb",
    "firmed",
    "firming",
]
CATALYST_TOKENS = [
    "earnings",
    "guidance",
    "results",
    "forecast",
    "outlook",
    "upgrade",
    "downgrade",
    "target",
    "deal",
    "iran",
    "ceasefire",
    "de-escalation",
    "relief",
    "truce",
    "contract",
    "approval",
    "trial",
    "auditor",
    "buyback",
    "merger",
    "acquisition",
    "partnership",
    "launch",
    "pricing",
    "restructuring",
]
LOW_SIGNAL_PHRASES = [
    "big stocks moving higher",
    "big stocks moving lower",
    "stocks moving higher",
    "stocks moving lower",
    "market today",
    "stock market today",
]
RATE_PROXY_DURATION = {
    "IEF": 7.5,
    "TLT": 16.5,
    "SHY": 1.9,
}
RATE_PROXY_SYMBOLS = {"IEF", "TLT", "SHY"}
OIL_PROXY_SYMBOLS = {"USO", "BNO"}
RISK_PROXY_SYMBOLS = {"SPY", "QQQ", "IWM", "DIA", "LQD", "HYG", "XLF", "XLK"}
DEFENSIVE_PROXY_SYMBOLS = {"GLD", "SLV", "PPLT", "PALL", "VIXY", "UVXY"}

_EVENT_SEARCH_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "search_query": {"type": "string"},
    },
    "required": ["search_query"],
}
_EVENT_MARKET_ACTIVITY_WHY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "narrative": {"type": "string"},
    },
    "required": ["narrative"],
}
_event_theme_keywords_cache: dict[str, list[str]] = {}
_event_market_activity_why_cache: dict[tuple[str, str], str] = {}
_MARKET_ACTIVITY_NARRATIVE_SYSTEM_PROMPT = register_narrative_prompt(
    name="Market Activity Narrative Sentence (live research event)",
    file="services/attention_live_research.py",
    group="Attention Pipeline",
    prompt=(
        f"{NARRATIVE_STYLE_RULE} "
        "Write one sentence (under 25 words) explaining what this market theme move "
        "signals for broader market activity. Be specific about the directional implication."
    ),
)

RESEARCH_SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "why_now_text": {"type": "string"},
        "what_else_moved_text": {"type": "string"},
        "background_context_text": {"type": "string"},
    },
    "required": ["why_now_text", "what_else_moved_text", "background_context_text"],
}


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _trim(text: object, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _first_sentence(text: object) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return parts[0].strip() if parts else clean


def _normalized_text(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _coerce_text(text).lower()).strip()


def _is_generic_filing_reference(text: object) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    normalized = re.sub(r"[^a-z0-9\s.&/-]", " ", clean.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    patterns = [
        r"^(?:form\s+)?(?:8-k|10-k|10-q|20-f|6-k|s-1|f-1)$",
        r"^(?:form\s+)?(?:8-k|10-k|10-q|20-f|6-k|s-1|f-1)\s+item\s+\d+(?:\.\d+)?(?:\s+.*)?$",
        r"^item\s+\d+(?:\.\d+)?(?:\s+.*)?$",
        r"^recent\s+(?:8-k|10-k|10-q|20-f|6-k|s-1|f-1)\s+filing\s+context$",
        r"^(?:annual|quarterly|current)\s+report$",
    ]
    return any(re.match(pattern, normalized) for pattern in patterns)


def _normalize_symbol(symbol: object) -> str:
    return _coerce_text(symbol).upper()


def _event_record(home_payload: dict[str, Any], bundle_id: str) -> dict[str, Any]:
    normalized_bundle_id = _coerce_text(bundle_id)
    for item in list(home_payload.get("top_events") or []):
        if _coerce_text((item or {}).get("bundle_id")) == normalized_bundle_id:
            return dict(item or {})
    return {}


def _safe_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


_SOURCE_BUCKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bucket": {"type": "string", "enum": ["official", "wire", "press", "web"]},
    },
    "required": ["bucket"],
}
_source_bucket_cache: dict[str, tuple[str, int]] = {}
_BUCKET_RANK: dict[str, int] = {"official": 0, "wire": 1, "press": 2, "web": 3}


def _source_authority_bucket(source: object) -> tuple[str, int]:
    text = _coerce_text(source).lower()
    if not text:
        return "unknown", 4
    policy = source_authority_policy()
    if any(token in text for token in policy.official_tokens):
        return "official", 0
    if any(token == text or token in text for token in policy.wire_tokens):
        return "wire", 1
    if any(token in text for token in policy.press_tokens):
        return "press", 2
    # Unknown source — ask LLM once per source name
    cache_key = text[:80]
    if cache_key in _source_bucket_cache:
        return _source_bucket_cache[cache_key]
    llm_client = load_aql_zopedia_llm_client(surface="attention.live_research.source_bucket")
    if llm_client is None:
        return "web", 3
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "Classify a financial news source into one authority bucket: "
                "'official' (SEC/EDGAR, government, central bank, exchange filings), "
                "'wire' (Reuters, Bloomberg, AP, Dow Jones, PR Newswire, Business Wire), "
                "'press' (major newspapers, magazines, established financial news sites), "
                "'web' (blogs, social media, unknown, miscellaneous). "
                "Return the single most appropriate bucket."
            ),
            user_prompt=f"Source name: {text[:120]}",
            schema_name="source_bucket",
            schema=_SOURCE_BUCKET_SCHEMA,
        )
        bucket = str(result.get("bucket") or "").strip().lower()
        if bucket not in _BUCKET_RANK:
            bucket = "web"
    except (LLMAPIError, Exception):
        bucket = "web"
    pair = (bucket, _BUCKET_RANK[bucket])
    _source_bucket_cache[cache_key] = pair
    return pair


def _title_case_bucket(bucket: str) -> str:
    return bucket.replace("_", " ").title() if bucket else ""


def _is_low_signal_article(headline: str, summary: str) -> bool:
    blob = f"{headline} {summary}".lower()
    return any(token in blob for token in LOW_SIGNAL_PHRASES)


def _mention_score(text: str, symbol: str, company_name: str) -> float:
    lowered = f" {text.lower()} "
    score = 0.0
    if symbol and re.search(rf"(?<![A-Z0-9]){re.escape(symbol.lower())}(?![A-Z0-9])", lowered):
        score += 0.65
    company = company_name.lower().strip()
    if company and company in lowered:
        score += 0.45
    return min(score, 1.0)


def _llm_event_theme_keywords(theme: str) -> list[str]:
    if theme in _event_theme_keywords_cache:
        return _event_theme_keywords_cache[theme]
    llm_client = load_aql_zopedia_llm_client(surface="attention.live_research.theme_keywords")
    if llm_client is None:
        _event_theme_keywords_cache[theme] = []
        return []
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "You generate keyword lists for financial market theme classification. "
                "Return 8-12 short lowercase keywords or phrases that commonly appear in financial "
                "news when this market theme is active."
            ),
            user_prompt=f"Generate keywords for the '{theme}' market theme.",
            schema_name="event_theme_keywords",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
                "required": ["keywords"],
            },
        )
        keywords = [str(k).strip().lower() for k in result.get("keywords") or [] if str(k).strip()]
    except (LLMAPIError, Exception):
        keywords = []
    _event_theme_keywords_cache[theme] = keywords
    return keywords


def _event_keyword_score(text: str, theme: str) -> float:
    blob = f" {str(text or '').lower()} "
    keywords = _llm_event_theme_keywords(_coerce_text(theme).lower())
    hits = sum(1 for keyword in keywords if keyword in blob)
    return min(hits * 0.18, 0.72)


def _event_symbol_score(text: str, symbols: list[str]) -> float:
    blob = f" {str(text or '').lower()} "
    hits = 0
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized and re.search(rf"(?<![A-Z0-9]){re.escape(normalized.lower())}(?![A-Z0-9])", blob):
            hits += 1
    return min(hits * 0.12, 0.28)


def _event_relevance_score(text: str, theme: str, symbols: list[str]) -> float:
    theme_score = _event_keyword_score(text, theme)
    symbol_score = _event_symbol_score(text, symbols)
    if _coerce_text(theme).lower() == "generic":
        return min(symbol_score, 1.0)
    return min(theme_score + symbol_score, 1.0)


_EVENT_QUERY_STOPWORDS = {
    "and",
    "are",
    "the",
    "for",
    "from",
    "with",
    "this",
    "that",
    "into",
    "today",
    "market",
    "markets",
    "stock",
    "stocks",
    "share",
    "shares",
    "rally",
    "rallies",
    "rallied",
    "surge",
    "surges",
    "surged",
    "jump",
    "jumps",
    "higher",
    "lower",
    "slide",
    "slides",
    "slid",
    "fall",
    "falls",
    "fell",
    "move",
    "moves",
    "moving",
    "clear",
    "driver",
    "event",
    "theme",
    "sector",
    "related",
}


def _distinctive_event_query_terms(query_text: object) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9&+-]{2,}", _coerce_text(query_text).lower()):
        normalized = token.strip("-+&")
        if len(normalized) < 4 or normalized in _EVENT_QUERY_STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms


def _event_query_term_match_count(text: str, query_text: object) -> int:
    blob = f" {_normalized_text(text)} "
    hits = 0
    for term in _distinctive_event_query_terms(query_text):
        normalized = _normalized_text(term)
        if normalized and re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", blob):
            hits += 1
    return hits


def _event_query_terms_in_text(text: str, query_text: object) -> list[str]:
    blob = f" {_normalized_text(text)} "
    hits: list[str] = []
    for term in _distinctive_event_query_terms(query_text):
        normalized = _normalized_text(term)
        if normalized and re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", blob):
            hits.append(term)
    return hits


def _passes_event_relevance_gate(
    headline: str, snippet: str, theme: str, symbols: list[str],
    query_text: str = "",
) -> bool:
    text = f"{headline} {snippet}"
    relevance = _event_relevance_score(text, theme, symbols)
    if _is_low_signal_article(headline, snippet) and relevance < 0.62:
        return False
    if query_text:
        query_terms = _event_query_terms_in_text(text, query_text)
        if len(query_terms) >= 2:
            return True
        headline_terms = _event_query_terms_in_text(headline, query_text)
        if query_terms and (relevance >= 0.18 or any(len(term) >= 5 for term in headline_terms)):
            return True
    if relevance < 0.32:
        return False
    return True


def _passes_search_relevance_gate(headline: str, snippet: str, symbol: str, company_name: str) -> bool:
    title_score = _mention_score(headline, symbol, company_name)
    body_score = _mention_score(snippet, symbol, company_name)
    combined_score = _mention_score(f"{headline} {snippet}", symbol, company_name)
    if max(title_score, body_score, combined_score) < 0.45:
        return False
    if title_score < 0.45 and body_score < 0.75:
        return False
    return True


def _freshness_score(published_at: pd.Timestamp, asof_time_utc: pd.Timestamp) -> float:
    if pd.isna(published_at):
        return 0.15
    age_hours = max((asof_time_utc - published_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.8
    if age_hours <= 7 * 24:
        return 0.55
    if age_hours <= 30 * 24:
        return 0.3
    return 0.1


def _catalyst_score(text: str) -> float:
    lowered = f" {text.lower()} "
    score = 0.0
    for token in MOVE_WORD_TOKENS:
        if f" {token.lower()} " in lowered:
            score += 0.08
    for token in CATALYST_TOKENS:
        if token.lower() in lowered:
            score += 0.12
    if "today" in lowered:
        score += 0.08
    if "why" in lowered or "driving" in lowered:
        score += 0.08
    return min(score, 1.0)


def _theme_tag(text: str) -> str:
    lowered = text.lower()
    theme_map = {
        "earnings": ["earnings", "results", "guidance", "outlook"],
        "analyst": ["upgrade", "downgrade", "price target"],
        "deal": ["deal", "acquisition", "merger", "takeover", "iran", "ceasefire"],
        "product": ["launch", "approval", "trial", "contract", "partnership"],
        "governance": ["auditor", "ceo", "cfo", "board"],
        "macro": ["inflation", "treasury", "oil", "rates", "dollar"],
    }
    for theme, tokens in theme_map.items():
        if any(token in lowered for token in tokens):
            return theme
    return "general"


def _rank_value(item: dict[str, Any]) -> float:
    return (
        float(item.get("relevance_score") or 0.0) * 0.42
        + float(item.get("causal_score") or 0.0) * 0.28
        + float(item.get("freshness_score") or 0.0) * 0.2
        + max(0.0, 0.15 - float(item.get("authority_rank") or 0.0) * 0.03)
    )


def _same_day_rows(evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in evidence_rows if item.get("is_same_day")]


def _same_day_authoritative_rows(
    evidence_rows: list[dict[str, Any]],
    *,
    min_causal: float,
    min_relevance: float,
) -> list[dict[str, Any]]:
    return [
        item
        for item in evidence_rows
        if item.get("is_same_day")
        and int(item.get("authority_rank") or 9) <= 1
        and float(item.get("causal_score") or 0.0) >= min_causal
        and float(item.get("relevance_score") or 0.0) >= min_relevance
    ]


def _corroborating_same_day_rows(
    evidence_rows: list[dict[str, Any]],
    *,
    min_causal: float,
    min_relevance: float,
) -> list[dict[str, Any]]:
    return [
        item
        for item in evidence_rows
        if item.get("is_same_day")
        and float(item.get("causal_score") or 0.0) >= min_causal
        and float(item.get("relevance_score") or 0.0) >= min_relevance
    ]


def _dedupe_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in rows:
        key = (
            _coerce_text(item.get("url")).lower(),
            _coerce_text(item.get("headline")).lower(),
            _coerce_text(item.get("summary")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    out.sort(
        key=lambda item: (
            -_rank_value(item),
            int(item.get("authority_rank") or 9),
            _coerce_text(item.get("headline")).lower(),
        )
    )
    return out


def _display_excerpt_from_article(headline: object, summary: object, *, limit: int = 180) -> str:
    snippet = _trim(_first_sentence(summary), limit)
    if not snippet or _is_generic_filing_reference(snippet):
        return ""
    if _normalized_text(snippet) == _normalized_text(headline):
        return ""
    return snippet


def _best_evidence_excerpt(item: dict[str, Any], *, limit: int = 180) -> str:
    headline = _coerce_text(item.get("headline"))
    for candidate in [item.get("display_excerpt"), item.get("excerpt"), item.get("summary")]:
        text = _trim(_first_sentence(candidate), limit)
        if not text or _is_generic_filing_reference(text):
            continue
        if headline and _normalized_text(text) == _normalized_text(headline):
            continue
        return text
    return ""


def _serialize_evidence_item(item: dict[str, Any], *, symbol: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol or _coerce_text(item.get("symbol")),
        "source": _coerce_text(item.get("source")),
        "authority_bucket": _coerce_text(item.get("authority_bucket")),
        "headline": _coerce_text(item.get("headline")),
        "summary": _coerce_text(item.get("summary")),
        "excerpt": _coerce_text(item.get("excerpt")),
        "display_excerpt": _best_evidence_excerpt(item),
        "url": _coerce_text(item.get("url")),
        "published_at": _coerce_text(item.get("published_at")),
        "evidence_role": _coerce_text(item.get("evidence_role")),
    }


def _event_candidate_lookup(home_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _normalize_symbol(item.get("symbol")): dict(item or {})
        for item in list(home_payload.get("event_candidates_1d") or [])
        if _normalize_symbol((item or {}).get("symbol"))
    }


def _event_supporting_rows(event: dict[str, Any], home_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_lookup = _event_candidate_lookup(home_payload)
    rows: list[dict[str, Any]] = []
    for symbol in _safe_list(event.get("supporting_symbols")):
        normalized = _normalize_symbol(symbol)
        row = candidate_lookup.get(normalized)
        if row:
            rows.append(row)
    return rows


def _format_move_value(move: float) -> str:
    return f"{float(move):+.1f}%"


def _looks_like_numeric_market_activity_recap(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    pct_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?%", clean))
    bps_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?\s*bps\b", clean.lower()))
    ticker_count = len(re.findall(r"\b[A-Z]{2,5}\b", clean))
    ticker_pct_pairs = len(re.findall(r"\b[A-Z]{2,5}\s*[+\-]\d+(?:\.\d+)?%", clean))
    if ticker_pct_pairs >= 2:
        return True
    if pct_count + bps_count >= 4:
        return True
    if ticker_count >= 4 and (pct_count + bps_count) >= 3:
        return True
    return False


def _join_move_examples(rows: list[dict[str, Any]], *, limit: int = 2) -> str:
    examples = []
    for row in rows[:limit]:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        examples.append(symbol)
    examples = list(dict.fromkeys(examples))
    if not examples:
        return ""
    if len(examples) == 1:
        return examples[0]
    if len(examples) == 2:
        return f"{examples[0]} and {examples[1]}"
    return ", ".join(examples[:-1]) + f", and {examples[-1]}"


def _approx_yield_move_bps(rate_rows: list[dict[str, Any]]) -> int | None:
    estimates: list[float] = []
    for row in rate_rows:
        symbol = _normalize_symbol(row.get("symbol"))
        duration = RATE_PROXY_DURATION.get(symbol)
        move = pd.to_numeric(row.get("change_pct"), errors="coerce")
        if duration is None or pd.isna(move):
            continue
        estimates.append((-float(move) / duration) * 100.0)
    if not estimates:
        return None
    return int(round(sum(estimates) / len(estimates)))


def _event_market_context_text(event: dict[str, Any], home_payload: dict[str, Any]) -> str:
    theme = _coerce_text(event.get("event_type")).lower()
    direction = _coerce_text(event.get("anchor_direction")).lower()
    supporting_rows = _event_supporting_rows(event, home_payload)
    if not supporting_rows:
        return ""

    def _sorted_rows(symbols: set[str], *, positive: bool | None = None) -> list[dict[str, Any]]:
        rows = []
        for row in supporting_rows:
            symbol = _normalize_symbol(row.get("symbol"))
            move = pd.to_numeric(row.get("change_pct"), errors="coerce")
            if not symbol or symbol not in symbols or pd.isna(move):
                continue
            if positive is True and float(move) <= 0:
                continue
            if positive is False and float(move) >= 0:
                continue
            rows.append(row)
        rows.sort(key=lambda item: -abs(float(pd.to_numeric(item.get("change_pct"), errors="coerce") or 0.0)))
        return rows

    if theme == "rates":
        rate_rows = _sorted_rows(RATE_PROXY_SYMBOLS, positive=True if direction == "up" else False)
        proxy_text = _join_move_examples(rate_rows)
        if direction == "up":
            sentence = "Treasury proxies rallied, pointing to lower yields"
            if proxy_text:
                sentence += f": {proxy_text}"
            sentence += "."
            spill_rows = _sorted_rows(RISK_PROXY_SYMBOLS, positive=True)
            spill_text = _join_move_examples(spill_rows)
            if spill_text:
                sentence += f" Rate-sensitive assets also rose, including {spill_text}."
            return sentence
        sentence = "Treasury proxies fell, pointing to higher yields"
        if proxy_text:
            sentence += f": {proxy_text}"
        sentence += "."
        spill_rows = _sorted_rows(DEFENSIVE_PROXY_SYMBOLS, positive=True)
        spill_text = _join_move_examples(spill_rows)
        if spill_text:
            sentence += f" Defensive assets firmed, including {spill_text}."
        return sentence

    if theme == "oil":
        oil_rows = _sorted_rows(OIL_PROXY_SYMBOLS, positive=True if direction == "up" else False)
        oil_text = _join_move_examples(oil_rows)
        if direction == "down":
            sentence = "Oil proxies fell"
            if oil_text:
                sentence += f": {oil_text}"
            sentence += "."
            spill_rows = _sorted_rows(RISK_PROXY_SYMBOLS | RATE_PROXY_SYMBOLS, positive=True)
            spill_text = _join_move_examples(spill_rows)
            if spill_text:
                sentence += f" Relief showed up in {spill_text}."
            return sentence
        sentence = "Oil proxies rose"
        if oil_text:
            sentence += f": {oil_text}"
        sentence += "."
        spill_rows = _sorted_rows(RISK_PROXY_SYMBOLS | RATE_PROXY_SYMBOLS, positive=False)
        spill_text = _join_move_examples(spill_rows)
        if spill_text:
            sentence += f" Pressure showed up in {spill_text}."
        return sentence

    if theme == "defensives":
        defensive_rows = _sorted_rows(DEFENSIVE_PROXY_SYMBOLS, positive=True if direction == "up" else False)
        defensive_text = _join_move_examples(defensive_rows)
        if defensive_text:
            verb = "rose" if direction == "up" else "fell"
            return f"Defensive proxies {verb}: {defensive_text}."

    if theme == "risk":
        risk_rows = _sorted_rows(RISK_PROXY_SYMBOLS, positive=True if direction == "up" else False)
        risk_text = _join_move_examples(risk_rows)
        if risk_text:
            verb = "rose" if direction == "up" else "fell"
            return f"Risk proxies {verb}: {risk_text}."

    return ""


def _to_article_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = pd.DataFrame(rows)
    for column in ["headline", "summary", "description", "source", "url"]:
        if column not in frame.columns:
            frame[column] = ""
    frame["published_at"] = frame.apply(
        lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
        axis=1,
    )
    frame = frame.dropna(subset=["headline"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["headline", "summary", "description", "source", "published_at", "url"])
    frame = frame.sort_values("published_at", ascending=False, na_position="last")
    return frame.drop_duplicates(subset=["headline", "url"], keep="first").reset_index(drop=True)


def merge_news_payloads(*payloads: dict[str, Any] | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source_parts: list[str] = []
    fallback_summary = ""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        articles = payload.get("articles")
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            frame = articles.copy()
            frame["published_at"] = frame.apply(
                lambda row: coerce_article_published_at(row.get("published_at"), url=row.get("url")),
                axis=1,
            )
            for _, row in frame.iterrows():
                rows.append(
                    {
                        "headline": _coerce_text(row.get("headline")),
                        "summary": _coerce_text(row.get("summary") or row.get("description")),
                        "description": _coerce_text(row.get("description") or row.get("summary")),
                        "source": _coerce_text(row.get("source")),
                        "published_at": coerce_article_published_at(row.get("published_at"), url=row.get("url")),
                        "url": _coerce_text(row.get("url")),
                    }
                )
        source = _coerce_text(payload.get("source"))
        if source and source not in source_parts:
            source_parts.append(source)
        if not fallback_summary:
            fallback_summary = _coerce_text(payload.get("fallback_summary"))
    frame = _to_article_frame(rows)
    return {
        "articles": frame,
        "fallback_summary": fallback_summary or None,
        "source": "+".join(source_parts) if source_parts else None,
    }


def _event_search_query(event: dict[str, Any]) -> str:
    theme = _coerce_text(event.get("event_type")).lower() or "generic"
    direction = _coerce_text(event.get("anchor_direction")).lower() or "down"
    event_title = _coerce_text(event.get("event_title"))
    anchor_symbol = _normalize_symbol(event.get("anchor_symbol"))
    supporting_symbols = [
        _normalize_symbol(symbol)
        for symbol in list(event.get("supporting_symbols") or [])
        if _normalize_symbol(symbol)
    ]
    llm_client = load_aql_zopedia_llm_client(surface="attention.live_research.event_search_query")
    if llm_client is not None:
        try:
            symbol_context = ", ".join(([anchor_symbol] if anchor_symbol else []) + supporting_symbols[:4])
            result = llm_client.generate_json(
                system_prompt=(
                    "You generate concise financial news search queries. "
                    "Return one search query that can explain a market move. "
                    "Use wider-circle market reasoning: exact companies first, then close peers, "
                    "customers, suppliers, private leaders, IPO rumors, policy, rates, commodities, "
                    "or sector themes when those are likely to explain the move."
                ),
                user_prompt=(
                    f"User's original question: {event_title}\n"
                    f"Market event: {event_title or theme} ({direction})\n"
                    f"Theme: {theme}, Direction: {direction}\n"
                    f"Key symbols: {symbol_context}\n"
                    "Generate a targeted search query to find relevant financial news. "
                    "Preserve the important event terms, but do not require every result to name the listed tickers "
                    "if a private-company, macro, policy, or sector event plausibly explains the move."
                ),
                schema_name="event_search_query",
                schema=_EVENT_SEARCH_QUERY_SCHEMA,
            )
            query = str(result.get("search_query") or "").strip()
            if query:
                return query
        except (LLMAPIError, Exception):
            pass
    parts = [anchor_symbol] if anchor_symbol else []
    parts.extend(supporting_symbols[:3])
    if event_title:
        return f"{event_title} {' '.join(parts[:2])} today".strip()
    return " ".join(part for part in parts if part) + " market move today"


def _minimal_event_search_query(event: dict[str, Any]) -> str:
    event_title = _coerce_text(event.get("event_title"))
    anchor_symbol = _normalize_symbol(event.get("anchor_symbol"))
    supporting_symbols = [
        _normalize_symbol(symbol)
        for symbol in list(event.get("supporting_symbols") or [])
        if _normalize_symbol(symbol)
    ]
    parts = [event_title] if event_title else []
    parts.extend(([anchor_symbol] if anchor_symbol else []) + supporting_symbols[:3])
    return f"{' '.join(part for part in parts if part)} today".strip() or "market news today"


def search_market_event_news_payload(
    event: dict[str, Any],
    *,
    max_results: int = 8,
    serp_client: SerpAPISearchClient | None = None,
    serper_client: SerperSearchClient | None = None,
    tavily_client: TavilySearchClient | None = None,
    search_query: str = "",
    allow_llm_query: bool = True,
) -> dict[str, Any]:
    if not isinstance(event, dict) or not event:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": None, "messages": []}

    if serp_client is None:
        cfg = load_serpapi_config()
        serp_client = SerpAPISearchClient(cfg) if cfg is not None else None
    if serper_client is None:
        cfg = load_serper_config()
        serper_client = SerperSearchClient(cfg) if cfg is not None else None
    if tavily_client is None:
        cfg = load_tavily_config()
        tavily_client = TavilySearchClient(cfg) if cfg is not None else None

    theme = _coerce_text(event.get("event_type")).lower() or "generic"
    event_title = _coerce_text(event.get("event_title"))
    symbols = [
        _normalize_symbol(symbol)
        for symbol in list(event.get("supporting_symbols") or [])
        if _normalize_symbol(symbol)
    ]
    query_base = _coerce_text(search_query)
    if not query_base:
        query_base = _event_search_query(event) if allow_llm_query else _minimal_event_search_query(event)
    article_rows: list[dict[str, Any]] = []
    sources: list[str] = []
    errors: list[str] = []

    if serp_client is not None:
        try:
            for item in serp_client.search(query_base, news=True, num=max(max_results, 3)):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title:
                    continue
                if not _passes_event_relevance_gate(title, snippet, theme, symbols, query_text=f"{event_title} {query_base}"):
                    continue
                article_rows.append(
                    {
                        "headline": title,
                        "summary": snippet,
                        "description": snippet,
                        "source": _coerce_text(item.source) or "SerpApi",
                        "published_at": coerce_article_published_at(item.published_at, url=item.url),
                        "url": _coerce_text(item.url),
                    }
                )
            sources.append("serpapi")
        except Exception as exc:
            errors.append(f"SerpApi failed: {type(exc).__name__}")

    if serper_client is not None:
        try:
            for item in serper_client.search(query_base, news=True, num=max(max_results, 3)):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title:
                    continue
                if not _passes_event_relevance_gate(title, snippet, theme, symbols, query_text=f"{event_title} {query_base}"):
                    continue
                article_rows.append(
                    {
                        "headline": title,
                        "summary": snippet,
                        "description": snippet,
                        "source": _coerce_text(item.source) or "Serper",
                        "published_at": coerce_article_published_at(item.published_at, url=item.url),
                        "url": _coerce_text(item.url),
                    }
                )
            sources.append("serper")
        except Exception as exc:
            errors.append(f"Serper failed: {type(exc).__name__}")

    if tavily_client is not None:
        try:
            for item in tavily_client.search(query_base, max_results=max(max_results // 2, 3), topic="news"):
                title = _coerce_text(item.title)
                snippet = _coerce_text(item.snippet)
                if not title and not snippet:
                    continue
                if not _passes_event_relevance_gate(title, snippet, theme, symbols, query_text=f"{event_title} {query_base}"):
                    continue
                article_rows.append(
                    {
                        "headline": title or f"{theme.title()} market event result",
                        "summary": snippet,
                        "description": snippet,
                        "source": _coerce_text(item.source) or "Tavily",
                        "published_at": coerce_article_published_at(item.published_at, url=item.url),
                        "url": _coerce_text(item.url),
                    }
                )
            sources.append("tavily")
        except Exception as exc:
            errors.append(f"Tavily failed: {type(exc).__name__}")

    frame = _to_article_frame(article_rows).head(max(int(max_results), 1))
    fallback_summary = None
    if errors and frame.empty:
        fallback_summary = errors[0]
    return {
        "articles": frame,
        "fallback_summary": fallback_summary,
        "source": "+".join(sources) if sources else None,
        "messages": errors,
        "search_query": query_base,
    }


def _normalize_news_evidence(
    symbol: str,
    company_name: str,
    news_payload: dict[str, Any] | None,
    *,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    payload = news_payload or {}
    rows: list[dict[str, Any]] = []
    articles = payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return rows

    for _, article in articles.iterrows():
        headline = _coerce_text(article.get("headline"))
        summary = _coerce_text(article.get("summary") or article.get("description"))
        excerpt = _display_excerpt_from_article(headline, summary)
        published_at = coerce_article_published_at(
            article.get("published_at"),
            url=article.get("url"),
            asof_time_utc=asof_time_utc,
        )
        if not is_recent_for_attention(published_at, asof_time_utc=asof_time_utc, include_undated=True):
            continue
        source = _coerce_text(article.get("source"))
        authority_bucket, authority_rank = _source_authority_bucket(source)
        blob = f"{headline} {summary}"
        relevance_score = _mention_score(blob, symbol, company_name)
        if relevance_score < 0.45:
            continue
        if _is_low_signal_article(headline, summary) and relevance_score < 0.75:
            continue
        freshness_score = _freshness_score(published_at, asof_time_utc)
        causal_score = min(1.0, _catalyst_score(blob) + relevance_score * 0.25)
        rows.append(
            {
                "symbol": symbol,
                "source": source or "News",
                "source_type": "news_article",
                "authority_bucket": authority_bucket,
                "authority_rank": authority_rank,
                "headline": headline,
                "summary": summary,
                "excerpt": excerpt,
                "url": _coerce_text(article.get("url")),
                "published_at": published_at.isoformat() if pd.notna(published_at) else "",
                "published_at_ts": published_at,
                "freshness_score": round(freshness_score, 2),
                "relevance_score": round(relevance_score, 2),
                "causal_score": round(causal_score, 2),
                "theme_tag": _theme_tag(blob),
                "is_same_day": bool(pd.notna(published_at) and published_at.date() == asof_time_utc.date()),
                "evidence_role": "same_day_confirmation" if pd.notna(published_at) and published_at.date() == asof_time_utc.date() else "background_context",
            }
        )
    return rows


def _normalize_event_news_evidence(
    theme: str,
    supporting_symbols: list[str],
    news_payload: dict[str, Any] | None,
    *,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    payload = news_payload or {}
    rows: list[dict[str, Any]] = []
    articles = payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return rows

    for _, article in articles.iterrows():
        headline = _coerce_text(article.get("headline"))
        summary = _coerce_text(article.get("summary") or article.get("description"))
        excerpt = _display_excerpt_from_article(headline, summary)
        source = _coerce_text(article.get("source"))
        published_at = coerce_article_published_at(
            article.get("published_at"),
            url=article.get("url"),
            asof_time_utc=asof_time_utc,
        )
        if not is_recent_for_attention(published_at, asof_time_utc=asof_time_utc, include_undated=True):
            continue
        authority_bucket, authority_rank = _source_authority_bucket(source)
        blob = f"{headline} {summary}"
        relevance_score = _event_relevance_score(blob, theme, supporting_symbols)
        if relevance_score < 0.32:
            continue
        if _is_low_signal_article(headline, summary) and relevance_score < 0.62:
            continue
        freshness_score = _freshness_score(published_at, asof_time_utc)
        theme_bonus = 0.12 if _event_keyword_score(blob, theme) >= 0.18 else 0.0
        causal_score = min(1.0, _catalyst_score(blob) + relevance_score * 0.3 + theme_bonus)
        rows.append(
            {
                "symbol": "",
                "source": source or "News",
                "source_type": "event_news",
                "authority_bucket": authority_bucket,
                "authority_rank": authority_rank,
                "headline": headline,
                "summary": summary,
                "excerpt": excerpt,
                "url": _coerce_text(article.get("url")),
                "published_at": published_at.isoformat() if pd.notna(published_at) else "",
                "published_at_ts": published_at,
                "freshness_score": round(freshness_score, 2),
                "relevance_score": round(relevance_score, 2),
                "causal_score": round(causal_score, 2),
                "theme_tag": theme or _theme_tag(blob),
                "is_same_day": bool(pd.notna(published_at) and published_at.date() == asof_time_utc.date()),
                "evidence_role": "same_day_confirmation" if pd.notna(published_at) and published_at.date() == asof_time_utc.date() else "background_context",
            }
        )
    return rows


def _normalize_context_evidence(
    symbol: str,
    context_payload: dict[str, Any] | None,
    *,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    payload = context_payload or {}
    rows: list[dict[str, Any]] = []
    summary = _coerce_text(payload.get("llm_why_now") or payload.get("llm_summary_text") or payload.get("context_story_text"))
    excerpt = _trim(_first_sentence(payload.get("primary_source_excerpt") or payload.get("latest_filing_excerpt")))
    if _is_generic_filing_reference(summary) and excerpt:
        summary = excerpt
    if summary:
        source = _coerce_text(payload.get("llm_source_line") or payload.get("source_line") or "Primary source")
        authority_bucket, authority_rank = _source_authority_bucket(source)
        generic_summary = _is_generic_filing_reference(summary)
        relevance_score = 0.7
        causal_score = min(1.0, 0.2 + _catalyst_score(summary))
        if generic_summary:
            relevance_score = 0.45
            causal_score = min(causal_score, 0.18)
        rows.append(
            {
                "symbol": symbol,
                "source": source,
                "source_type": "context_summary",
                "authority_bucket": authority_bucket if authority_bucket != "unknown" else "official",
                "authority_rank": min(authority_rank, 1),
                "headline": _coerce_text(payload.get("llm_headline")) or f"{symbol} context summary",
                "summary": summary,
                "excerpt": excerpt,
                "url": "",
                "published_at": "",
                "published_at_ts": pd.NaT,
                "freshness_score": 0.18,
                "relevance_score": relevance_score,
                "causal_score": causal_score,
                "theme_tag": _theme_tag(summary),
                "is_same_day": False,
                "evidence_role": "background_context",
            }
        )
    return rows


def _normalize_filing_evidence(
    symbol: str,
    filings_frame: pd.DataFrame | None,
    *,
    asof_time_utc: pd.Timestamp,
) -> list[dict[str, Any]]:
    if not isinstance(filings_frame, pd.DataFrame) or filings_frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    filings = filings_frame.copy()
    if "symbol" not in filings.columns:
        return rows
    filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
    filings = filings[filings["symbol"] == symbol].copy()
    if filings.empty:
        return rows
    filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
    filings = filings.sort_values("filing_date", ascending=False, na_position="last").head(4)
    for _, filing in filings.iterrows():
        filing_date = pd.to_datetime(filing.get("filing_date"), utc=True, errors="coerce")
        excerpt = _trim(_first_sentence(filing.get("filing_excerpt") or filing.get("document_text")))
        form = _coerce_text(filing.get("form"))
        items = _coerce_text(filing.get("items"))
        desc = _coerce_text(filing.get("primary_doc_description"))
        if not excerpt and not desc and not form:
            continue
        generic_desc = _is_generic_filing_reference(desc)
        if desc and not generic_desc:
            summary = desc
            if excerpt and excerpt.lower() not in desc.lower():
                summary = f"{desc}. {excerpt}"
        elif excerpt:
            summary = excerpt
        elif desc:
            summary = f"Recent {form or 'SEC'} filing context."
        else:
            summary = f"{symbol} filed a {form}"
        summary = _trim(summary)
        generic_summary = _is_generic_filing_reference(summary)
        relevance_score = 0.85
        freshness_score = _freshness_score(filing_date, asof_time_utc)
        causal_score = 0.45
        lowered = f"{items} {summary}".lower()
        if any(token in lowered for token in ["2.02", "results of operations", "guidance", "earnings", "approval", "contract", "acquisition", "trial"]):
            causal_score = 0.8
        elif any(token in lowered for token in ["auditor", "board", "director", "officer"]):
            causal_score = 0.3
        if generic_desc and not excerpt:
            relevance_score = 0.45
            causal_score = min(causal_score, 0.15)
        elif generic_summary:
            relevance_score = 0.45
            causal_score = min(causal_score, 0.15)
        is_same_day = bool(pd.notna(filing_date) and filing_date.date() == asof_time_utc.date())
        rows.append(
            {
                "symbol": symbol,
                "source": "SEC EDGAR",
                "source_type": "sec_filing",
                "authority_bucket": "official",
                "authority_rank": 0,
                "headline": f"{form} • {filing_date.strftime('%b %d') if pd.notna(filing_date) else 'Recent'}",
                "summary": summary,
                "excerpt": excerpt,
                "url": _coerce_text(filing.get("filing_url")),
                "published_at": filing_date.isoformat() if pd.notna(filing_date) else "",
                "published_at_ts": filing_date,
                "freshness_score": round(freshness_score, 2),
                "relevance_score": round(relevance_score, 2),
                "causal_score": round(causal_score, 2),
                "theme_tag": _theme_tag(f"{items} {summary}"),
                "is_same_day": is_same_day,
                "evidence_role": "fresh_catalyst" if is_same_day and causal_score >= 0.72 else "background_context",
                "filing_form": form,
                "filing_items": items,
            }
        )
    return rows


def _evidence_rows_as_aql_claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        claims.append(
            {
                "claim_text": _best_evidence_excerpt(item, limit=260) or _coerce_text(item.get("headline")),
                "is_same_day": bool(item.get("is_same_day")),
                "causal_score": float(item.get("causal_score") or 0.0),
                "relevance_score": float(item.get("relevance_score") or 0.0),
                "supports_hypothesis": _coerce_text(item.get("theme_tag")) or "unresolved",
                "source": _coerce_text(item.get("source")),
                "source_authority_bucket": _coerce_text(item.get("authority_bucket")),
                "authority_rank": int(item.get("authority_rank") or 9),
            }
        )
    return claims


def _judge_cause_status(evidence_rows: list[dict[str, Any]]) -> tuple[str, str]:
    return _shared_judge_cause_status(_evidence_rows_as_aql_claims(evidence_rows))


def _quality_label(retained: list[dict[str, Any]], background: list[dict[str, Any]], cause_status: str) -> tuple[str, str]:
    return _shared_quality_label(_evidence_rows_as_aql_claims(list(retained or []) + list(background or [])), cause_status)


def _event_market_activity_why_text(theme: str, direction: str) -> str:
    theme = _coerce_text(theme).lower()
    direction = _coerce_text(direction).lower() or "down"
    cache_key = (theme, direction)
    if cache_key in _event_market_activity_why_cache:
        return _event_market_activity_why_cache[cache_key]
    llm_client = load_aql_zopedia_llm_client(surface="attention.live_research.market_activity")
    if llm_client is not None:
        try:
            result = llm_client.generate_json(
                system_prompt=get_prompt(_MARKET_ACTIVITY_NARRATIVE_SYSTEM_PROMPT),
                user_prompt=f"Theme: {theme}\nDirection: {direction}\nWrite the market activity narrative sentence.",
                schema_name="event_market_activity_narrative",
                schema=_EVENT_MARKET_ACTIVITY_WHY_SCHEMA,
            )
            narrative = str(result.get("narrative") or "").strip()
        except (LLMAPIError, Exception):
            narrative = ""
    else:
        narrative = ""
    text = narrative
    _event_market_activity_why_cache[cache_key] = text
    return text


def _judge_event_cause_status(evidence_rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not evidence_rows:
        return "unresolved", "unresolved"

    same_day = [item for item in evidence_rows if item.get("is_same_day")]
    strong_same_day = [
        item
        for item in same_day
        if (
            float(item.get("causal_score") or 0.0) >= 0.62
            and float(item.get("relevance_score") or 0.0) >= 0.55
        )
        or (
            int(item.get("authority_rank") or 9) <= 1
            and float(item.get("causal_score") or 0.0) >= 0.5
            and float(item.get("relevance_score") or 0.0) >= 0.5
        )
    ]
    if strong_same_day:
        themes = {
            _coerce_text(item.get("theme_tag"))
            for item in strong_same_day
            if _coerce_text(item.get("theme_tag"))
        }
        if len(themes) >= 2 and "general" not in themes:
            return "conflicting", "conflicting"
        return "supported", "same_day_confirmation"

    authoritative_same_day = _same_day_authoritative_rows(
        same_day,
        min_causal=0.45,
        min_relevance=0.5,
    )
    corroborating_same_day = _corroborating_same_day_rows(
        same_day,
        min_causal=0.42,
        min_relevance=0.45,
    )
    if authoritative_same_day and corroborating_same_day:
        return "supported", "same_day_confirmation"

    background = [
        item
        for item in evidence_rows
        if not item.get("is_same_day") and float(item.get("relevance_score") or 0.0) >= 0.55
    ]
    if background:
        return "continuation", "continuation"
    return "unresolved", "unresolved"


def _evidence_event_why_text(
    event: dict[str, Any],
    cause_status: str,
    retained: list[dict[str, Any]],
    home_payload: dict[str, Any],
) -> str:
    if cause_status == "supported" and retained:
        top = retained[0]
        summary = _best_evidence_excerpt(top, limit=220)
        if summary:
            return _trim(summary, 280)
    if cause_status == "continuation":
        for item in retained:
            summary = _best_evidence_excerpt(item, limit=220)
            if summary:
                return _trim(summary, 280)
    return ""


def _best_background_item(background: list[dict[str, Any]]) -> dict[str, Any]:
    def _item_rank(item: dict[str, Any]) -> tuple[float, float, float, float]:
        summary = _coerce_text(item.get("summary"))
        excerpt = _coerce_text(item.get("excerpt"))
        generic_penalty = 1.0 if _is_generic_filing_reference(summary) and not excerpt else 0.0
        return (
            generic_penalty,
            -float(item.get("causal_score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            float(item.get("authority_rank") or 9.0),
        )

    candidates = [item for item in background if isinstance(item, dict)]
    if not candidates:
        return {}
    return sorted(candidates, key=_item_rank)[0]


def _best_background_text(item: dict[str, Any]) -> str:
    for candidate in [item.get("summary"), item.get("excerpt"), item.get("headline")]:
        text = _trim(_first_sentence(candidate), limit=180)
        if text and not _is_generic_filing_reference(text):
            return text.rstrip(".")
    return ""


def _evidence_why_text(symbol: str, cause_status: str, retained: list[dict[str, Any]], background: list[dict[str, Any]]) -> str:
    if cause_status == "supported" and retained:
        top = retained[0]
        summary = _best_evidence_excerpt(top, limit=220)
        if summary:
            return summary
    if cause_status == "continuation" and background:
        top = _best_background_item(background)
        summary = _best_background_text(top)
        if summary:
            return summary
    return ""


def _find_peer_moves(candidate: dict[str, Any], home_payload: dict[str, Any]) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(candidate.get("symbol"))
    sector = _coerce_text(candidate.get("sector"))
    industry = _coerce_text(candidate.get("industry"))
    candidates = list(home_payload.get("event_candidates_1d") or [])
    out: list[dict[str, Any]] = []

    for item in candidates:
        peer_symbol = _normalize_symbol(item.get("symbol"))
        if not peer_symbol or peer_symbol == symbol:
            continue
        if industry and industry != "Unknown" and _coerce_text(item.get("industry")) == industry:
            relation = industry
        elif sector and sector != "Unknown" and _coerce_text(item.get("sector")) == sector:
            relation = sector
        else:
            continue
        change_pct = pd.to_numeric(item.get("change_pct"), errors="coerce")
        if pd.isna(change_pct) or abs(float(change_pct)) < 2.0:
            continue
        out.append(
            {
                "symbol": peer_symbol,
                "change_pct": float(change_pct),
                "headline": _coerce_text(item.get("headline")),
                "relationship": relation,
            }
        )
    out.sort(key=lambda item: (-abs(float(item.get("change_pct") or 0.0)), item.get("symbol", "")))
    return out[:4]


def _find_event_context(symbol: str, home_payload: dict[str, Any]) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    for event in list(home_payload.get("top_events") or []):
        supporting = {_normalize_symbol(item) for item in _safe_list(event.get("supporting_symbols"))}
        if normalized_symbol in supporting:
            return event
    return {}


def _evidence_what_else_moved(symbol: str, candidate: dict[str, Any], home_payload: dict[str, Any], peer_moves: list[dict[str, Any]]) -> str:
    event = _find_event_context(symbol, home_payload)
    affected_summary = _coerce_text(event.get("affected_assets_summary_text"))
    if affected_summary:
        return affected_summary
    return ""


def _background_context_text(background: list[dict[str, Any]]) -> str:
    top = _best_background_item(background)
    if not top:
        return ""
    return _best_background_text(top)


def _synthesize_with_llm(
    llm_client: LLMClient | None,
    *,
    symbol: str,
    candidate: dict[str, Any],
    cause_status: str,
    why_today_mode: str,
    retained: list[dict[str, Any]],
    background: list[dict[str, Any]],
    peer_moves: list[dict[str, Any]],
    evidence_why_text: str,
    evidence_what_else_moved_text: str,
) -> dict[str, str]:
    if llm_client is None:
        return {
            "why_now_text": evidence_why_text,
            "what_else_moved_text": evidence_what_else_moved_text,
            "background_context_text": _background_context_text(background),
        }

    system_prompt = (
        "You write concise market drilldown text. Use only the supplied evidence. "
        "Do not invent catalysts. Distinguish same-day evidence from older background context. "
        "Avoid ticker/percent market recaps in all text fields. "
        "If the supplied evidence is insufficient for a causal explanation, leave the causal field empty."
    )
    user_prompt = json.dumps(
        {
            "symbol": symbol,
            "candidate": {
                "headline": _coerce_text(candidate.get("headline")),
                "what_changed_text": _coerce_text(candidate.get("what_changed_text")),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
            },
            "cause_status": cause_status,
            "why_today_mode": why_today_mode,
            "same_day_evidence": [
                {
                    "source": _coerce_text(item.get("source")),
                    "authority_bucket": _coerce_text(item.get("authority_bucket")),
                    "headline": _coerce_text(item.get("headline")),
                    "summary": _coerce_text(item.get("summary")),
                    "theme_tag": _coerce_text(item.get("theme_tag")),
                }
                for item in retained[:4]
            ],
            "background_context": [
                {
                    "source": _coerce_text(item.get("source")),
                    "headline": _coerce_text(item.get("headline")),
                    "summary": _coerce_text(item.get("summary")),
                }
                for item in background[:3]
            ],
            "peer_moves": peer_moves[:3],
            "evidence_why_text": evidence_why_text,
            "evidence_what_else_moved_text": evidence_what_else_moved_text,
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    )
    try:
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="attention_symbol_research_bundle",
            schema=RESEARCH_SYNTHESIS_SCHEMA,
        )
    except Exception:
        return {
            "why_now_text": evidence_why_text,
            "what_else_moved_text": evidence_what_else_moved_text,
            "background_context_text": _background_context_text(background),
        }
    why_now_text = _coerce_text(data.get("why_now_text")) or evidence_why_text
    what_else_moved_text = _coerce_text(data.get("what_else_moved_text")) or evidence_what_else_moved_text
    if _looks_like_numeric_market_activity_recap(why_now_text):
        why_now_text = evidence_why_text
    if _looks_like_numeric_market_activity_recap(what_else_moved_text):
        what_else_moved_text = evidence_what_else_moved_text
    return {
        "why_now_text": why_now_text,
        "what_else_moved_text": what_else_moved_text,
        "background_context_text": _coerce_text(data.get("background_context_text")) or _background_context_text(background),
    }


def build_live_attention_research_bundle(
    bundle_id: str,
    home_payload: dict[str, Any],
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    search_payloads: dict[str, dict[str, Any]] | None = None,
    filings_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    asof_time_utc: datetime | pd.Timestamp | None = None,
    allow_live_event_search: bool = False,
) -> dict[str, Any]:
    normalized_bundle_id = _coerce_text(bundle_id)
    agentic_bundle = build_bottom_up_attention_bundle(normalized_bundle_id, home_payload)
    if agentic_bundle and any(agentic_bundle.get(key) for key in ["claims", "evidence", "background_context"]):
        return agentic_bundle
    base_bundle = build_attention_research_bundle(
        normalized_bundle_id,
        home_payload,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
    )
    asof_ts = pd.to_datetime(asof_time_utc if asof_time_utc is not None else datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(asof_ts):
        asof_ts = pd.Timestamp.now(tz="UTC")

    if base_bundle.get("bundle_type") != "symbol":
        event = _event_record(home_payload, normalized_bundle_id)
        supporting_symbols = [
            _normalize_symbol(symbol)
            for symbol in list(event.get("supporting_symbols") or [])
            if _normalize_symbol(symbol)
        ]
        theme = _coerce_text(event.get("event_type")).lower() or "generic"
        symbol_payloads = [
            merge_news_payloads(
                (news_payloads or {}).get(symbol),
                (search_payloads or {}).get(symbol),
            )
            for symbol in supporting_symbols[:8]
        ]
        merged_symbol_news = merge_news_payloads(*symbol_payloads)
        event_search_payload = (search_payloads or {}).get(normalized_bundle_id)
        if not isinstance(event_search_payload, dict) and allow_live_event_search:
            try:
                event_search_payload = search_market_event_news_payload(event)
            except Exception:
                event_search_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}

        evidence_rows = _normalize_event_news_evidence(
            theme,
            supporting_symbols,
            merged_symbol_news,
            asof_time_utc=asof_ts,
        )
        evidence_rows.extend(
            _normalize_event_news_evidence(
                theme,
                supporting_symbols,
                event_search_payload,
                asof_time_utc=asof_ts,
            )
        )
        evidence_rows = _dedupe_evidence_rows(evidence_rows)
        cause_status, why_today_mode = _judge_event_cause_status(evidence_rows)
        retained = [
            item
            for item in evidence_rows
            if item.get("is_same_day") and float(item.get("relevance_score") or 0.0) >= 0.55
        ][:6]
        background = [
            item
            for item in evidence_rows
            if not item.get("is_same_day") and float(item.get("relevance_score") or 0.0) >= 0.55
        ][:3]
        why_text = _evidence_event_why_text(event, cause_status, retained or evidence_rows[:3], home_payload)
        evidence_quality, freshness_quality = _quality_label(retained, background, cause_status)
        base_bundle["why_happened_text"] = why_text
        base_bundle["cause_status"] = cause_status
        base_bundle["why_today_mode"] = why_today_mode
        base_bundle["evidence_quality"] = evidence_quality
        base_bundle["freshness_quality"] = freshness_quality
        base_bundle["source_summary"] = ", ".join(
            dict.fromkeys(
                _coerce_text(item.get("source"))
                for item in (retained or evidence_rows[:6])
                if _coerce_text(item.get("source"))
            )
        )
        base_bundle["background_context"] = []
        base_bundle["background_context_text"] = ""
        base_bundle["evidence"] = [
            _serialize_evidence_item(item)
            for item in (retained or evidence_rows[:6])
        ]
        if cause_status == "supported":
            base_bundle["confidence_label"] = "High" if len(retained) >= 2 else "Medium"
        elif cause_status == "conflicting":
            base_bundle["confidence_label"] = "Developing"
        elif not _coerce_text(base_bundle.get("confidence_label")):
            base_bundle["confidence_label"] = "Developing"
        return base_bundle

    symbol = _normalize_symbol(base_bundle.get("symbol"))
    candidates = {
        _normalize_symbol(item.get("symbol")): item
        for item in list(home_payload.get("event_candidates_1d") or [])
        if _normalize_symbol(item.get("symbol"))
    }
    candidate = dict(candidates.get(symbol) or {})
    company_name = _coerce_text(candidate.get("company_name"))
    merged_news_payload = merge_news_payloads(
        (news_payloads or {}).get(symbol),
        (search_payloads or {}).get(symbol),
    )
    evidence_rows = _normalize_news_evidence(
        symbol,
        company_name,
        merged_news_payload,
        asof_time_utc=asof_ts,
    )
    evidence_rows.extend(
        _normalize_context_evidence(
            symbol,
            (context_payloads or {}).get(symbol),
            asof_time_utc=asof_ts,
        )
    )
    evidence_rows.extend(_normalize_filing_evidence(symbol, filings_frame, asof_time_utc=asof_ts))
    evidence_rows = _dedupe_evidence_rows(evidence_rows)

    cause_status, why_today_mode = _judge_cause_status(evidence_rows)
    retained = [
        item
        for item in evidence_rows
        if item.get("is_same_day") and float(item.get("relevance_score") or 0.0) >= 0.55
    ][:6]
    background = [
        item
        for item in evidence_rows
        if not item.get("is_same_day") and float(item.get("relevance_score") or 0.0) >= 0.55
    ][:4]
    if cause_status == "continuation" and not background:
        background = evidence_rows[:2]
    peer_moves = _find_peer_moves(candidate, home_payload)
    evidence_why_text = _evidence_why_text(symbol, cause_status, retained, background)
    evidence_what_else = _evidence_what_else_moved(symbol, candidate, home_payload, peer_moves)
    synthesized = _synthesize_with_llm(
        llm_client,
        symbol=symbol,
        candidate=candidate,
        cause_status=cause_status,
        why_today_mode=why_today_mode,
        retained=retained,
        background=background,
        peer_moves=peer_moves,
        evidence_why_text=evidence_why_text,
        evidence_what_else_moved_text=evidence_what_else,
    )
    evidence_quality, freshness_quality = _quality_label(retained, background, cause_status)

    def _serialize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_serialize_evidence_item(item, symbol=symbol) for item in items]

    base_bundle.update(
        {
            "why_now_text": synthesized["why_now_text"],
            "what_else_moved_text": synthesized["what_else_moved_text"],
            "background_context_text": synthesized["background_context_text"],
            "background_context": _serialize(background),
            "peer_moves": peer_moves,
            "cause_status": cause_status,
            "why_today_mode": why_today_mode,
            "evidence_quality": evidence_quality,
            "freshness_quality": freshness_quality,
            "evidence": _serialize(retained) or _serialize(evidence_rows[:6]),
            "same_day_evidence_count": len(retained),
            "background_evidence_count": len(background),
            "source_summary": ", ".join(
                dict.fromkeys(_coerce_text(item.get("source")) for item in (retained or evidence_rows[:6]) if _coerce_text(item.get("source")))
            ),
            "related_symbols": [
                {
                    "symbol": _coerce_text(item.get("symbol")),
                    "headline": _coerce_text(item.get("headline")),
                    "change_pct": item.get("change_pct"),
                    "sector": _coerce_text(item.get("sector")),
                    "industry": _coerce_text(item.get("industry")),
                }
                for item in list(base_bundle.get("related_symbols") or [])
            ],
        }
    )
    if peer_moves and not base_bundle.get("related_symbols"):
        base_bundle["related_symbols"] = [
            {
                "symbol": item["symbol"],
                "headline": item.get("headline", ""),
                "change_pct": item.get("change_pct"),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
            }
            for item in peer_moves
        ]
    return base_bundle


__all__ = [
    "build_live_attention_research_bundle",
    "merge_news_payloads",
    "search_symbol_news_payload",
]
