from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from .llm import COPY_STYLE_RULE, LLMAPIError, get_prompt, load_llm_client, register_copy_prompt


MARKET_EVENT_COLUMNS = [
    "market_event_id",
    "event_type",
    "event_title",
    "event_score",
    "confidence_label",
    "asof_time_utc",
    "anchor_symbol",
    "anchor_direction",
    "anchor_move_pct",
    "anchor_attention_score",
    "what_happened_text",
    "why_happened_text",
    "affected_assets_summary_text",
    "headline_text",
    "source_line",
    "driver_symbols",
    "beneficiary_symbols",
    "supporting_event_ids",
    "supporting_symbols",
    "breadth_count",
]

_SYMBOL_BUCKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bucket": {
            "type": "string",
            "enum": ["oil", "travel", "broad_equities", "rates", "defensives", "energy_equities", "other"],
        }
    },
    "required": ["bucket"],
}
_symbol_bucket_cache: dict[str, str] = {}

_THEME_PRIORITY = {"oil": 100, "rates": 80, "defensives": 70, "risk": 60, "generic": 10}
_THEME_PRIMARY_BUCKETS = {
    "oil": {"oil", "energy_equities"},
    "rates": {"rates"},
    "defensives": {"defensives"},
    "risk": {"broad_equities", "travel"},
}

_THEME_KEYWORDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["keywords"],
}
_EXPECTED_REACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "oil": {"type": "string", "enum": ["up", "down", "neutral"]},
        "energy_equities": {"type": "string", "enum": ["up", "down", "neutral"]},
        "travel": {"type": "string", "enum": ["up", "down", "neutral"]},
        "broad_equities": {"type": "string", "enum": ["up", "down", "neutral"]},
        "rates": {"type": "string", "enum": ["up", "down", "neutral"]},
        "defensives": {"type": "string", "enum": ["up", "down", "neutral"]},
        "other": {"type": "string", "enum": ["up", "down", "neutral"]},
    },
    "required": ["oil", "energy_equities", "travel", "broad_equities", "rates", "defensives", "other"],
}
_TAPE_WHY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "narrative": {"type": "string"},
    },
    "required": ["narrative"],
}
_theme_keywords_cache: dict[str, list[str]] = {}
_expected_reaction_cache: dict[tuple[str, str], dict[str, str]] = {}
_tape_why_cache: dict[tuple[str, str], str] = {}

_EVENT_COPY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "what_happened": {"type": "string"},
        "why_happened": {"type": "string"},
    },
    "required": ["title", "what_happened", "why_happened"],
}
_event_copy_cache: dict[str, dict[str, str]] = {}
_EVENT_COPY_SYSTEM_PROMPT = register_copy_prompt(
    name="Event Copy (title / what_happened / why_happened)",
    file="services/attention_market_events.py",
    prompt=(
        "You are writing copy for a financial markets dashboard. "
        f"{COPY_STYLE_RULE}\n"
        "Given a cross-asset market event, write three things:\n"
        "1. title: A short, specific event title (under 10 words). Name the actual assets or group. "
        "Do not say 'move lower/higher together today' — say what specifically happened.\n"
        "2. what_happened: One sentence (under 30 words) describing what moved and by how much. "
        "Use the actual symbols and percentages.\n"
        "3. why_happened: One sentence (under 35 words) on the most likely cause. "
        "If unclear, say 'no clear catalyst confirmed' — do not invent a reason."
    ),
)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=MARKET_EVENT_COLUMNS)


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _trim(text: object, limit: int = 240) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _coerce_float(value: object) -> float:
    numeric = pd.to_numeric(value, errors="coerce")
    return float(numeric) if pd.notna(numeric) else float("nan")


def _primary_buckets(theme: str) -> set[str]:
    return set(_THEME_PRIMARY_BUCKETS.get(str(theme or ""), set()))


def _llm_theme_keywords(theme: str) -> list[str]:
    if theme in _theme_keywords_cache:
        return _theme_keywords_cache[theme]
    llm_client = load_llm_client()
    if llm_client is None:
        _theme_keywords_cache[theme] = []
        return []
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "You generate keyword lists for financial market theme classification. "
                "Return 8-12 short lowercase keywords or phrases that commonly appear in financial "
                "news when this market theme is active."
            ),
            user_prompt=f"Generate keywords for the '{theme}' market theme.",
            schema_name="theme_keywords",
            schema=_THEME_KEYWORDS_SCHEMA,
        )
        keywords = [str(k).strip().lower() for k in result.get("keywords") or [] if str(k).strip()]
    except (LLMAPIError, Exception):
        keywords = []
    _theme_keywords_cache[theme] = keywords
    return keywords


def _llm_expected_reaction_map(theme: str, anchor_direction: str) -> dict[str, str]:
    cache_key = (theme, anchor_direction)
    if cache_key in _expected_reaction_cache:
        return _expected_reaction_cache[cache_key]
    llm_client = load_llm_client()
    if llm_client is None:
        _expected_reaction_cache[cache_key] = {}
        return {}
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "You are a cross-asset market analyst. Given a market theme and the direction the "
                "primary driver moved, predict the expected directional reaction for each asset bucket. "
                "Use 'neutral' when the connection is weak or ambiguous."
            ),
            user_prompt=(
                f"Theme: {theme}\n"
                f"Anchor direction (the primary driver moved): {anchor_direction}\n"
                "For each asset bucket, what is the expected reaction?"
            ),
            schema_name="expected_reaction",
            schema=_EXPECTED_REACTION_SCHEMA,
        )
        mapping = {k: v for k, v in result.items() if v in {"up", "down"}}
    except (LLMAPIError, Exception):
        mapping = {}
    _expected_reaction_cache[cache_key] = mapping
    return mapping


def _matches_theme_text(theme: str, text_blob: str) -> bool:
    blob = str(text_blob or "").lower()
    keywords = _llm_theme_keywords(str(theme or ""))
    return any(keyword in blob for keyword in keywords)


def _has_causal_language(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    patterns = (
        r"\bbecause\b",
        r"\bdue to\b",
        r"\bafter\b",
        r"\bamid\b",
        r"\bdriven by\b",
        r"\bsuggest(?:s|ing)\b",
        r"\bimply(?:s|ing)\b",
        r"\bmargins?\b",
        r"\bdemand\b",
        r"\binflation\b",
        r"\bsupply\b",
    )
    return any(re.search(pattern, clean) for pattern in patterns)


def _looks_like_stat_dump_text(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    lowered = clean.lower()
    pct_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?%", clean))
    bps_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?\s*bps\b", lowered))
    ticker_count = len(re.findall(r"\b[A-Z]{2,5}\b", clean))
    ticker_pct_pairs = len(re.findall(r"\b[A-Z]{2,5}\s*[+\-]\d+(?:\.\d+)?%", clean))
    if ticker_pct_pairs >= 2:
        return True
    if pct_count + bps_count >= 4:
        return True
    if ticker_count >= 4 and pct_count + bps_count >= 3:
        return True
    if re.search(r"\bup:\b|\bdown:\b", lowered):
        return True
    return False


def _narrative_or_fallback(text: object, *, fallback: str, limit: int = 280) -> str:
    clean = _trim(text, limit)
    if not clean:
        return _trim(fallback, limit)
    if _looks_like_stat_dump_text(clean) and not _has_causal_language(clean):
        return _trim(fallback, limit)
    return clean


def _join_symbols(symbols: list[str], *, limit: int = 5) -> str:
    unique = list(dict.fromkeys([_coerce_text(item).upper() for item in symbols if _coerce_text(item)]))
    scoped = unique[: max(int(limit), 1)]
    if not scoped:
        return ""
    if len(scoped) == 1:
        return scoped[0]
    if len(scoped) == 2:
        return f"{scoped[0]} and {scoped[1]}"
    return ", ".join(scoped[:-1]) + f", and {scoped[-1]}"


def _theme_tape_why(theme: str, direction: str) -> str:
    theme = _coerce_text(theme).lower()
    direction = _coerce_text(direction).lower() or "down"
    cache_key = (theme, direction)
    if cache_key in _tape_why_cache:
        return _tape_why_cache[cache_key]
    llm_client = load_llm_client()
    if llm_client is not None:
        try:
            result = llm_client.generate_json(
                system_prompt=(
                    "Write one concise sentence (under 25 words) explaining what this market theme move "
                    "signals for the broader tape. Be specific about the directional implication."
                ),
                user_prompt=f"Theme: {theme}\nDirection: {direction}\nWrite the tape narrative sentence.",
                schema_name="tape_narrative",
                schema=_TAPE_WHY_SCHEMA,
            )
            narrative = str(result.get("narrative") or "").strip()
        except (LLMAPIError, Exception):
            narrative = ""
    else:
        narrative = ""
    text = narrative or "Cross-asset spillover is still developing and the causal picture is not yet clear."
    _tape_why_cache[cache_key] = text
    return text


def _observed_direction(row: pd.Series) -> str:
    observed = _coerce_float(row.get("observed_value"))
    if np.isfinite(observed):
        return "up" if observed >= 0 else "down"
    token = _coerce_text(row.get("direction")).lower()
    return token if token in {"up", "down"} else "down"


def _llm_symbol_bucket(symbol: str) -> str:
    if symbol in _symbol_bucket_cache:
        return _symbol_bucket_cache[symbol]
    llm_client = load_llm_client()
    if llm_client is None:
        _symbol_bucket_cache[symbol] = "other"
        return "other"
    try:
        result = llm_client.generate_json(
            system_prompt=(
                "Classify a US-listed ETF or stock into one market theme bucket. "
                "oil: oil/energy commodity ETFs and pure upstream producers. "
                "energy_equities: diversified energy sector equities. "
                "travel: airlines, hotels, ride-sharing, booking platforms. "
                "broad_equities: broad market index ETFs (e.g. SPY, QQQ, IWM). "
                "rates: bond and treasury ETFs. "
                "defensives: gold, silver, volatility instruments. "
                "other: everything else."
            ),
            user_prompt=f"Symbol: {symbol}",
            schema_name="symbol_bucket",
            schema=_SYMBOL_BUCKET_SCHEMA,
        )
        bucket = str(result.get("bucket") or "other").strip()
    except (LLMAPIError, Exception):
        bucket = "other"
    _symbol_bucket_cache[symbol] = bucket
    return bucket


def _symbol_bucket(symbol: str) -> str:
    normalized = str(symbol or "").upper().strip()
    return _llm_symbol_bucket(normalized)


def _headline_items(payload: dict[str, Any] | None, *, limit: int = 2) -> list[dict[str, str]]:
    articles = payload.get("articles") if isinstance(payload, dict) else pd.DataFrame()
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return []
    rows: list[dict[str, str]] = []
    for _, item in articles.head(max(int(limit), 1)).iterrows():
        headline = _coerce_text(item.get("headline"))
        if not headline:
            continue
        rows.append(
            {
                "headline": headline,
                "summary": _coerce_text(item.get("summary") or item.get("description")),
                "source": _coerce_text(item.get("source")),
            }
        )
    return rows


def _combined_text_blob(
    row: pd.Series,
    *,
    news_payload: dict[str, Any] | None = None,
    context_payload: dict[str, Any] | None = None,
) -> str:
    headlines = _headline_items(news_payload, limit=2)
    headline_text = " ".join(
        " ".join([item.get("headline", ""), item.get("summary", ""), item.get("source", "")])
        for item in headlines
    )
    context = context_payload or {}
    parts = [
        _coerce_text(row.get("entity_id")),
        _coerce_text(row.get("title")),
        _coerce_text(row.get("subtitle")),
        _coerce_text(row.get("peer_group_name")),
        _coerce_text(row.get("source_label")),
        _coerce_text(row.get("story_text")),
        _coerce_text(row.get("why_now_text")),
        headline_text,
        _coerce_text(context.get("llm_headline")),
        _coerce_text(context.get("llm_summary_text")),
        _coerce_text(context.get("llm_why_now")),
        _coerce_text(context.get("context_story_text")),
        _coerce_text(context.get("primary_source_excerpt")),
    ]
    return " ".join(part for part in parts if part).lower()


def _infer_themes(symbol: str, bucket: str, text_blob: str) -> list[str]:
    themes: list[str] = []
    if bucket in _primary_buckets("oil") or _matches_theme_text("oil", text_blob):
        themes.append("oil")
    if bucket in _primary_buckets("rates") or _matches_theme_text("rates", text_blob):
        themes.append("rates")
    if bucket in _primary_buckets("defensives") or _matches_theme_text("defensives", text_blob):
        themes.append("defensives")
    if bucket in _primary_buckets("risk") or _matches_theme_text("risk", text_blob):
        themes.append("risk")
    if not themes:
        themes.append("generic")
    return list(dict.fromkeys(themes))


def _prepare_rows(
    feed: pd.DataFrame,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    if feed.empty:
        return feed.copy()

    rows = feed.copy()
    rows["entity_id"] = rows.get("entity_id", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
    rows["attention_score"] = pd.to_numeric(rows.get("attention_score"), errors="coerce")
    rows["observed_value"] = pd.to_numeric(rows.get("observed_value"), errors="coerce")
    rows["severity_score"] = pd.to_numeric(rows.get("severity_score"), errors="coerce")
    rows["asof_time_utc"] = pd.to_datetime(rows.get("asof_time_utc"), utc=True, errors="coerce")

    buckets: list[str] = []
    themes: list[list[str]] = []
    for _, row in rows.iterrows():
        symbol = _coerce_text(row.get("entity_id")).upper()
        bucket = _symbol_bucket(symbol)
        text_blob = _combined_text_blob(
            row,
            news_payload=(news_payloads or {}).get(symbol),
            context_payload=(context_payloads or {}).get(symbol),
        )
        buckets.append(bucket)
        themes.append(_infer_themes(symbol, bucket, text_blob))
    rows["_market_event_bucket"] = buckets
    rows["_market_event_themes"] = themes
    rows = rows.sort_values(["attention_score", "severity_score"], ascending=[False, False], na_position="last").reset_index(drop=True)
    return rows


def _theme_anchor_score(row: pd.Series, theme: str) -> float:
    base = _coerce_float(row.get("attention_score"))
    bucket = _coerce_text(row.get("_market_event_bucket"))
    symbol = _coerce_text(row.get("entity_id")).upper()
    bonus = 0.0
    if theme == "oil" and (bucket == "oil" or symbol in {"USO", "BNO"}):
        bonus = 15.0
    elif theme == "rates" and bucket == "rates":
        bonus = 12.0
    elif theme == "defensives" and bucket == "defensives":
        bonus = 10.0
    elif theme == "risk" and bucket == "broad_equities":
        bonus = 8.0
    return (base if np.isfinite(base) else 0.0) + bonus


def _expected_reaction(theme: str, anchor_direction: str, bucket: str) -> str | None:
    mapping = _llm_expected_reaction_map(str(theme or ""), str(anchor_direction or ""))
    return mapping.get(str(bucket or "")) or None


def _select_members(rows: pd.DataFrame, anchor: pd.Series, theme: str) -> pd.DataFrame:
    if rows.empty:
        return rows
    anchor_direction = _observed_direction(anchor)
    min_score = max(_coerce_float(anchor.get("attention_score")) * 0.35, 35.0)
    anchor_bucket = _coerce_text(anchor.get("_market_event_bucket"))
    selected_index: list[int] = []
    for idx, row in rows.iterrows():
        row_score = _coerce_float(row.get("attention_score"))
        if np.isfinite(row_score) and row_score < min_score:
            continue
        row_bucket = _coerce_text(row.get("_market_event_bucket"))
        row_themes = row.get("_market_event_themes", [])
        row_direction = _observed_direction(row)
        expected_direction = _expected_reaction(theme, anchor_direction, row_bucket)
        if theme in row_themes:
            if row_bucket in _primary_buckets(theme):
                selected_index.append(idx)
                continue
            if row_bucket == anchor_bucket and row_direction == anchor_direction:
                selected_index.append(idx)
                continue
            if expected_direction and row_direction == expected_direction:
                selected_index.append(idx)
                continue
        if expected_direction and row_direction == expected_direction:
            selected_index.append(idx)
    if anchor.name not in selected_index:
        selected_index.append(int(anchor.name))
    return rows.loc[sorted(set(selected_index))].copy()


def _top_headline_text(symbols: list[str], news_payloads: dict[str, dict[str, Any]] | None) -> tuple[str, str]:
    for symbol in symbols:
        items = _headline_items((news_payloads or {}).get(symbol), limit=1)
        if not items:
            continue
        item = items[0]
        headline = item.get("headline", "")
        source = item.get("source", "")
        summary = item.get("summary", "")
        source_line = f"Fresh evidence: {source}" if source else "Fresh evidence"
        if headline and summary:
            return _trim(f"{headline}. {summary}", 280), source_line
        if headline:
            return _trim(headline, 280), source_line
    return "", ""


def _event_confidence_label(theme: str, breadth_count: int, headline_text: str) -> str:
    if breadth_count >= 4 and headline_text:
        return "High"
    if breadth_count >= 3:
        return "High" if theme == "oil" else "Medium"
    if breadth_count >= 2 or headline_text:
        return "Medium"
    return "Developing"


def _direction_label(direction: str) -> str:
    token = str(direction or "").strip().lower()
    return "rose" if token == "up" else "fell"


def _move_snippet(row: pd.Series) -> str:
    symbol = _coerce_text(row.get("entity_id")).upper()
    return symbol


def _what_happened_text(anchor: pd.Series, members: pd.DataFrame, theme: str) -> str:
    if theme == "oil":
        driver_buckets = {"oil", "energy_equities"}
    elif theme == "rates":
        driver_buckets = {"rates"}
    elif theme == "defensives":
        driver_buckets = {"defensives"}
    elif theme == "risk":
        driver_buckets = {"broad_equities", "travel"}
    else:
        driver_buckets = {"oil", "energy_equities", "rates", "defensives", "broad_equities", "travel"}
    drivers = members[members["_market_event_bucket"].isin(driver_buckets)].copy()
    if drivers.empty:
        drivers = members.copy()
    drivers = drivers.sort_values(["attention_score", "severity_score"], ascending=[False, False], na_position="last")
    examples = ", ".join(_move_snippet(row) for _, row in drivers.head(3).iterrows())
    anchor_direction = _observed_direction(anchor)
    if theme == "oil":
        return _trim(f"Oil proxies {_direction_label(anchor_direction)} sharply today, led by {examples}.", 280)
    if theme == "rates":
        if anchor_direction == "up":
            return _trim(f"Treasury proxies rose today, led by {examples}.", 280)
        return _trim(f"Treasury proxies fell today, led by {examples}.", 280)
    if theme == "defensives":
        return _trim(f"Defensive proxies {_direction_label(anchor_direction)} today, led by {examples}.", 280)
    if theme == "risk":
        return _trim(f"Risk proxies {_direction_label(anchor_direction)} together today, with {examples} leading the move.", 280)
    return _narrative_or_fallback(
        _coerce_text(anchor.get("story_text")) or _coerce_text(anchor.get("title")) or examples,
        fallback=f"{_coerce_text(anchor.get('entity_id')).upper()} is driving a broader peer move today.",
        limit=280,
    )


def _why_happened_text(
    theme: str,
    anchor: pd.Series,
    members: pd.DataFrame,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    ordered_members = members.copy()
    anchor_symbol = _coerce_text(anchor.get("entity_id")).upper()
    if not ordered_members.empty:
        ordered_members["_context_rank"] = ordered_members.apply(
            lambda row: (
                0
                if _coerce_text(row.get("entity_id")).upper() == anchor_symbol
                else 1
                if _coerce_text(row.get("_market_event_bucket")) in _primary_buckets(theme)
                else 2
            ),
            axis=1,
        )
        ordered_members = ordered_members.sort_values(
            ["_context_rank", "attention_score", "severity_score"],
            ascending=[True, False, False],
            na_position="last",
        )
    symbols = [_coerce_text(value).upper() for value in ordered_members.get("entity_id", pd.Series(dtype=str)).tolist() if _coerce_text(value)]
    headline_text, source_line = _top_headline_text(symbols, news_payloads)

    for _, member in ordered_members.iterrows():
        symbol = _coerce_text(member.get("entity_id")).upper()
        context = (context_payloads or {}).get(symbol, {})
        context_why_now = _trim(context.get("llm_why_now") or context.get("llm_summary_text") or "", 260)
        if theme != "generic" and context_why_now:
            if not (_matches_theme_text(theme, context_why_now) or symbol == anchor_symbol):
                continue
        if context_why_now:
            return context_why_now, headline_text, source_line

    if headline_text:
        if theme == "oil":
            return _trim(
                "Fresh coverage points to less supply-risk and less inflation pressure in oil.",
                280,
            ), headline_text, source_line
        if theme == "rates":
            return _trim(
                "Fresh coverage points to lower yields, and the cross-asset tape is confirming that read.",
                280,
            ), headline_text, source_line

    direction = _observed_direction(anchor)
    if theme == "oil":
        if direction == "down":
            text = (
                "Oil is lower, which points to less supply-risk and less inflation pressure across the tape."
            )
        else:
            text = (
                "Oil is higher, which points to more supply-risk and more inflation pressure across the tape."
            )
        return _trim(text, 280), headline_text, source_line
    if theme == "rates":
        if direction == "up":
            text = "Treasuries are rallying, which points to lower yields and relief in rate-sensitive assets."
        else:
            text = "Treasuries are falling, which points to higher yields and more pressure on risk assets."
        return _trim(text, 280), headline_text, source_line
    if theme == "defensives":
        if direction == "up":
            text = "Defensive assets are rising, which points to a more cautious tone."
        else:
            text = "Defensive demand is fading, which lines up with a broader relief move."
        return _trim(text, 280), headline_text, source_line
    if theme == "risk":
        if direction == "up":
            text = "Risk assets are rising together, which points to a broader risk-on move."
        else:
            text = "Risk assets are weakening together, which points to a broader risk-off move."
        return _trim(text, 280), headline_text, source_line

    return _trim(_coerce_text(anchor.get("why_now_text")) or _coerce_text(anchor.get("story_text")), 280), headline_text, source_line


def _affected_assets_summary(theme: str, anchor: pd.Series, members: pd.DataFrame) -> tuple[str, list[str], list[str]]:
    anchor_direction = _observed_direction(anchor)
    driver_rows = members[members["_market_event_bucket"].isin({"oil", "energy_equities", "rates", "defensives", "broad_equities"})].copy()
    if driver_rows.empty:
        driver_rows = members.copy()

    if theme != "generic":
        winners = []
        losers = []
        for _, row in members.iterrows():
            symbol = _coerce_text(row.get("entity_id")).upper()
            bucket = _coerce_text(row.get("_market_event_bucket"))
            direction = _observed_direction(row)
            expected_direction = _expected_reaction(theme, anchor_direction, bucket)
            if expected_direction == "up" and direction == "up":
                winners.append(symbol)
            elif expected_direction == "down" and direction == "down":
                losers.append(symbol)
            elif bucket in {"oil", "energy_equities"} and direction == anchor_direction:
                losers.append(symbol) if anchor_direction == "down" else winners.append(symbol)
        winners = list(dict.fromkeys(symbol for symbol in winners if symbol))
        losers = list(dict.fromkeys(symbol for symbol in losers if symbol))
        loser_text = _join_symbols(losers, limit=5)
        winner_text = _join_symbols(winners, limit=5)
        if loser_text and winner_text:
            return f"Pressure showed up in {loser_text}, while relative strength showed up in {winner_text}.", winners, losers
        if loser_text:
            return f"Pressure showed up in {loser_text}.", winners, losers
        if winner_text:
            return f"Relative strength showed up in {winner_text}.", winners, losers
        return "Cross-asset spillover is still developing.", winners, losers

    symbols = list(dict.fromkeys(_coerce_text(symbol).upper() for symbol in members.get("entity_id", pd.Series(dtype=str)).tolist() if _coerce_text(symbol)))
    symbol_text = _join_symbols(symbols, limit=6)
    if symbol_text:
        return f"Spillover was concentrated in {symbol_text}.", [], symbols[:6]
    return "Cross-asset spillover is still developing.", [], symbols[:6]


def _llm_event_copy(
    *,
    theme: str,
    anchor_symbol: str,
    anchor_direction: str,
    anchor_move_pct: float,
    peer_group: str,
    member_snippets: list[str],
    headline_text: str,
    context_why: str,
) -> dict[str, str] | None:
    """Generate event title, what_happened, and why_happened in one LLM call.

    Returns None when LLM is unavailable or errors.
    """
    move_str = f"{anchor_move_pct:+.1f}%" if np.isfinite(anchor_move_pct) else anchor_direction
    members_str = ", ".join(member_snippets[:6]) if member_snippets else anchor_symbol
    cache_key = f"{theme}::{anchor_symbol}::{anchor_direction}::{round(anchor_move_pct, 1) if np.isfinite(anchor_move_pct) else ''}::{members_str[:80]}"
    if cache_key in _event_copy_cache:
        return _event_copy_cache[cache_key]
    llm_client = load_llm_client()
    if llm_client is None:
        return None
    context_lines = [
        f"Theme: {theme}",
        f"Anchor: {anchor_symbol} {move_str} ({anchor_direction})",
        f"Peer group: {peer_group}" if peer_group else "",
        f"Other movers: {members_str}",
        f"Top headline: {headline_text}" if headline_text else "",
        f"Context: {context_why[:300]}" if context_why else "",
    ]
    context = "\n".join(line for line in context_lines if line)
    try:
        result = llm_client.generate_json(
            system_prompt=get_prompt(_EVENT_COPY_SYSTEM_PROMPT),
            user_prompt=context,
            schema_name="event_copy",
            schema=_EVENT_COPY_SCHEMA,
        )
        copy = {
            "title": _trim(str(result.get("title") or "").strip(), 120),
            "what_happened": _trim(str(result.get("what_happened") or "").strip(), 280),
            "why_happened": _trim(str(result.get("why_happened") or "").strip(), 280),
        }
    except (LLMAPIError, Exception):
        return None
    _event_copy_cache[cache_key] = copy
    return copy


def _event_title(theme: str, anchor: pd.Series) -> str:
    direction = _observed_direction(anchor)
    subject = _trim(_coerce_text(anchor.get("peer_group_name")) or _coerce_text(anchor.get("source_label")), 80)
    if not subject or subject.lower() in {"all market", "equities", "commodities"}:
        subject = _coerce_text(anchor.get("entity_id")).upper()
    move_text = "moved lower" if direction == "down" else "moved higher"
    return _trim(f"{subject} {move_text}", 120)


def _event_score(anchor: pd.Series, members: pd.DataFrame, headline_text: str) -> float:
    anchor_score = _coerce_float(anchor.get("attention_score"))
    member_scores = pd.to_numeric(members.get("attention_score"), errors="coerce").dropna()
    breadth_count = len({_coerce_text(value) for value in members.get("_market_event_bucket", pd.Series(dtype=str)).tolist() if _coerce_text(value) and _coerce_text(value) != "other"})
    breadth_bonus = min(float(breadth_count) * 8.0, 32.0)
    headline_bonus = 10.0 if headline_text else 0.0
    spillover_bonus = min(float(member_scores.iloc[1:].sum()) * 0.08, 20.0) if len(member_scores) > 1 else 0.0
    return round((anchor_score if np.isfinite(anchor_score) else 0.0) + breadth_bonus + headline_bonus + spillover_bonus, 1)


def _build_theme_event(
    rows: pd.DataFrame,
    theme: str,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> dict[str, object] | None:
    themed = rows[rows["_market_event_themes"].map(lambda value: theme in value if isinstance(value, list) else False)].copy()
    if themed.empty:
        return None

    primary_candidates = themed[themed["_market_event_bucket"].isin(_primary_buckets(theme))].copy()
    anchor_candidates = primary_candidates if not primary_candidates.empty else themed.copy()
    anchor_candidates["_anchor_score"] = anchor_candidates.apply(lambda row: _theme_anchor_score(row, theme), axis=1)
    anchor = anchor_candidates.sort_values(["_anchor_score", "attention_score"], ascending=[False, False], na_position="last").iloc[0]
    members = _select_members(rows, anchor, theme)
    breadth_count = len({_coerce_text(value) for value in members.get("_market_event_bucket", pd.Series(dtype=str)).tolist() if _coerce_text(value) and _coerce_text(value) != "other"})
    if len(members) < 2 and _coerce_float(anchor.get("attention_score")) < 80.0:
        return None

    why_text, headline_text, source_line = _why_happened_text(
        theme,
        anchor,
        members,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
    )
    affected_text, winners, losers = _affected_assets_summary(theme, anchor, members)
    anchor_symbol = _coerce_text(anchor.get("entity_id")).upper()
    anchor_direction = _observed_direction(anchor)
    anchor_move_pct = _coerce_float(anchor.get("observed_value"))
    member_snippets = [_move_snippet(row) for _, row in members.head(6).iterrows()]
    peer_group = _trim(_coerce_text(anchor.get("peer_group_name")) or _coerce_text(anchor.get("source_label")), 80)

    llm_copy = _llm_event_copy(
        theme=theme,
        anchor_symbol=anchor_symbol,
        anchor_direction=anchor_direction,
        anchor_move_pct=anchor_move_pct,
        peer_group=peer_group,
        member_snippets=member_snippets,
        headline_text=headline_text,
        context_why=why_text,
    )

    what_happened_text = (
        llm_copy["what_happened"]
        if llm_copy and llm_copy.get("what_happened")
        else _narrative_or_fallback(
            _what_happened_text(anchor, members, theme),
            fallback=f"{peer_group or anchor_symbol} moved together today.",
        )
    )
    why_happened_text = (
        llm_copy["why_happened"]
        if llm_copy and llm_copy.get("why_happened")
        else _narrative_or_fallback(
            why_text,
            fallback=_theme_tape_why(theme, anchor_direction),
        )
    )
    event_title = (
        llm_copy["title"]
        if llm_copy and llm_copy.get("title")
        else _event_title(theme, anchor)
    )
    affected_assets_summary_text = _narrative_or_fallback(
        affected_text,
        fallback="Cross-asset spillover is still developing.",
    )
    score = _event_score(anchor, members, headline_text)
    confidence = _event_confidence_label(theme, breadth_count, headline_text)
    supporting_event_ids = [
        _coerce_text(value)
        for value in members.get("event_id", pd.Series(dtype=str)).tolist()
        if _coerce_text(value)
    ]
    supporting_symbols = [
        _coerce_text(value).upper()
        for value in members.get("entity_id", pd.Series(dtype=str)).tolist()
        if _coerce_text(value)
    ]
    return {
        "market_event_id": f"{theme}:{anchor_symbol}:{_coerce_text(anchor.get('horizon')) or 'event'}",
        "event_type": theme,
        "event_title": event_title,
        "event_score": score,
        "confidence_label": confidence,
        "asof_time_utc": pd.to_datetime(anchor.get("asof_time_utc"), utc=True, errors="coerce"),
        "anchor_symbol": anchor_symbol,
        "anchor_direction": anchor_direction,
        "anchor_move_pct": _coerce_float(anchor.get("observed_value")),
        "anchor_attention_score": _coerce_float(anchor.get("attention_score")),
        "what_happened_text": what_happened_text,
        "why_happened_text": why_happened_text,
        "affected_assets_summary_text": affected_assets_summary_text,
        "headline_text": headline_text,
        "source_line": source_line,
        "driver_symbols": list(dict.fromkeys(losers[:6] if theme == "oil" and _observed_direction(anchor) == "down" else supporting_symbols[:6])),
        "beneficiary_symbols": list(dict.fromkeys(winners[:6])),
        "supporting_event_ids": list(dict.fromkeys(supporting_event_ids)),
        "supporting_symbols": list(dict.fromkeys(supporting_symbols)),
        "breadth_count": breadth_count,
    }


def _build_fallback_event(rows: pd.DataFrame) -> dict[str, object] | None:
    if rows.empty:
        return None
    anchor = rows.iloc[0]
    symbol = _coerce_text(anchor.get("entity_id")).upper()
    return {
        "market_event_id": f"generic:{symbol}",
        "event_type": "generic",
        "event_title": _trim(_coerce_text(anchor.get("title")) or f"{symbol} leads the attention feed", 120),
        "event_score": round(_coerce_float(anchor.get("attention_score")), 1) if np.isfinite(_coerce_float(anchor.get("attention_score"))) else 0.0,
        "confidence_label": "Developing",
        "asof_time_utc": pd.to_datetime(anchor.get("asof_time_utc"), utc=True, errors="coerce"),
        "anchor_symbol": symbol,
        "anchor_direction": _observed_direction(anchor),
        "anchor_move_pct": _coerce_float(anchor.get("observed_value")),
        "anchor_attention_score": _coerce_float(anchor.get("attention_score")),
        "what_happened_text": _narrative_or_fallback(
            _coerce_text(anchor.get("story_text")) or _coerce_text(anchor.get("title")),
            fallback=f"{symbol} is driving the latest attention move.",
        ),
        "why_happened_text": _narrative_or_fallback(
            _coerce_text(anchor.get("why_now_text")) or _coerce_text(anchor.get("story_text")),
            fallback="Cause remains unresolved; retained evidence is still thin.",
        ),
        "affected_assets_summary_text": f"Spillover is currently concentrated in {symbol}.",
        "headline_text": "",
        "source_line": "",
        "driver_symbols": [symbol],
        "beneficiary_symbols": [],
        "supporting_event_ids": [_coerce_text(anchor.get("event_id"))] if _coerce_text(anchor.get("event_id")) else [],
        "supporting_symbols": [symbol],
        "breadth_count": 1,
    }


def build_attention_market_events(
    feed: pd.DataFrame,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    max_events: int = 4,
) -> pd.DataFrame:
    rows = _prepare_rows(
        feed,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
    )
    if rows.empty:
        return _empty_frame()

    built: list[dict[str, object]] = []
    used_symbols: set[str] = set()
    for theme in ["oil", "rates", "defensives", "risk"]:
        event = _build_theme_event(
            rows,
            theme,
            news_payloads=news_payloads,
            context_payloads=context_payloads,
        )
        if event is None:
            continue
        symbols = {str(symbol).upper().strip() for symbol in list(event.get("supporting_symbols") or []) if str(symbol).strip()}
        if used_symbols and len(symbols & used_symbols) >= max(2, len(symbols) // 2):
            continue
        built.append(event)
        used_symbols.update(symbols)

    if not built:
        fallback = _build_fallback_event(rows)
        if fallback is not None:
            built.append(fallback)

    out = pd.DataFrame(built)
    if out.empty:
        return _empty_frame()
    out["_theme_priority"] = out.get("event_type", pd.Series(dtype=str)).map(lambda value: _THEME_PRIORITY.get(str(value), 0))
    out["event_score"] = pd.to_numeric(out.get("event_score"), errors="coerce")
    out = out.sort_values(["_theme_priority", "event_score"], ascending=[False, False], na_position="last").head(max(int(max_events), 1)).reset_index(drop=True)
    out = out.drop(columns=["_theme_priority"], errors="ignore")
    return out[MARKET_EVENT_COLUMNS]


__all__ = ["MARKET_EVENT_COLUMNS", "build_attention_market_events"]
