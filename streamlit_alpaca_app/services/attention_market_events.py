from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


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

_OIL_DRIVER_SYMBOLS = {
    "USO",
    "BNO",
    "UGA",
    "UNG",
    "DBC",
    "PDBC",
    "XLE",
    "XOP",
    "XOM",
    "CVX",
    "COP",
    "SLB",
    "OXY",
}
_TRAVEL_SYMBOLS = {"JETS", "UAL", "DAL", "AAL", "LUV", "UBER", "ABNB", "BKNG", "EXPE", "MAR", "HLT"}
_BROAD_MARKET_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM"}
_RATE_SYMBOLS = {"TLT", "IEF", "SHY", "LQD", "HYG"}
_DEFENSIVE_SYMBOLS = {"GLD", "SLV", "PPLT", "PALL", "VIX", "UVXY", "VIXY"}
_ENERGY_EQUITY_SYMBOLS = {"XOM", "CVX", "COP", "SLB", "OXY", "XLE", "XOP"}

_THEME_PRIORITY = {"oil": 100, "rates": 80, "defensives": 70, "risk": 60, "generic": 10}
_THEME_PRIMARY_BUCKETS = {
    "oil": {"oil", "energy_equities"},
    "rates": {"rates"},
    "defensives": {"defensives"},
    "risk": {"broad_equities", "travel"},
}

_OIL_KEYWORDS = (
    "oil",
    "crude",
    "crude oil",
    "wti",
    "brent",
    "brent crude",
    "gasoline",
    "hormuz",
    "opec",
    "iran",
)
_RATE_KEYWORDS = ("treasury", "yield", "rates", "bond", "fed")
_DEFENSIVE_KEYWORDS = ("gold", "silver", "haven", "defensive", "precious")
_RISK_KEYWORDS = ("risk-on", "risk off", "stocks", "equities", "small caps", "broad market")
_THEME_KEYWORDS = {
    "oil": _OIL_KEYWORDS,
    "rates": _RATE_KEYWORDS,
    "defensives": _DEFENSIVE_KEYWORDS,
    "risk": _RISK_KEYWORDS,
}


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


def _matches_theme_text(theme: str, text_blob: str) -> bool:
    blob = str(text_blob or "").lower()
    return any(keyword in blob for keyword in _THEME_KEYWORDS.get(str(theme or ""), ()))


def _observed_direction(row: pd.Series) -> str:
    observed = _coerce_float(row.get("observed_value"))
    if np.isfinite(observed):
        return "up" if observed >= 0 else "down"
    token = _coerce_text(row.get("direction")).lower()
    return token if token in {"up", "down"} else "down"


def _symbol_bucket(symbol: str) -> str:
    normalized = str(symbol or "").upper().strip()
    if normalized in _OIL_DRIVER_SYMBOLS:
        return "oil"
    if normalized in _TRAVEL_SYMBOLS:
        return "travel"
    if normalized in _BROAD_MARKET_SYMBOLS:
        return "broad_equities"
    if normalized in _RATE_SYMBOLS:
        return "rates"
    if normalized in _DEFENSIVE_SYMBOLS:
        return "defensives"
    if normalized in _ENERGY_EQUITY_SYMBOLS:
        return "energy_equities"
    return "other"


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
    if theme == "oil":
        if anchor_direction == "down":
            mapping = {
                "oil": "down",
                "energy_equities": "down",
                "travel": "up",
                "broad_equities": "up",
                "rates": "up",
                "defensives": "down",
            }
        else:
            mapping = {
                "oil": "up",
                "energy_equities": "up",
                "travel": "down",
                "broad_equities": "down",
                "rates": "down",
                "defensives": "up",
            }
        return mapping.get(bucket)
    if theme == "rates":
        if anchor_direction == "up":
            mapping = {
                "rates": "up",
                "broad_equities": "up",
                "travel": "up",
                "defensives": "down",
            }
        else:
            mapping = {
                "rates": "down",
                "broad_equities": "down",
                "travel": "down",
                "defensives": "up",
            }
        return mapping.get(bucket)
    if theme == "defensives":
        if anchor_direction == "up":
            mapping = {
                "defensives": "up",
                "broad_equities": "down",
                "rates": "up",
            }
        else:
            mapping = {
                "defensives": "down",
                "broad_equities": "up",
                "rates": "down",
            }
        return mapping.get(bucket)
    if theme == "risk":
        if anchor_direction == "up":
            mapping = {
                "broad_equities": "up",
                "travel": "up",
                "rates": "up",
                "defensives": "down",
            }
        else:
            mapping = {
                "broad_equities": "down",
                "travel": "down",
                "rates": "down",
                "defensives": "up",
            }
        return mapping.get(bucket)
    return None


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
    observed = _coerce_float(row.get("observed_value"))
    if np.isfinite(observed):
        return f"{symbol} {observed:+.1f}%"
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
    return _trim(_coerce_text(anchor.get("story_text")) or _coerce_text(anchor.get("title")) or examples, 280)


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
        pieces: list[str] = []
        if losers:
            pieces.append("Down: " + ", ".join(losers[:5]))
        if winners:
            pieces.append("Up: " + ", ".join(winners[:5]))
        return " | ".join(pieces) if pieces else "Cross-asset spillover is still developing.", winners, losers

    symbols = list(dict.fromkeys(_coerce_text(symbol).upper() for symbol in members.get("entity_id", pd.Series(dtype=str)).tolist() if _coerce_text(symbol)))
    return "Affected: " + ", ".join(symbols[:6]), [], symbols[:6]


def _event_title(theme: str, anchor: pd.Series) -> str:
    direction = _observed_direction(anchor)
    subject = _trim(_coerce_text(anchor.get("peer_group_name")) or _coerce_text(anchor.get("source_label")), 80)
    if not subject or subject.lower() in {"all market", "equities", "commodities"}:
        subject = _coerce_text(anchor.get("entity_id")).upper()
    move_text = "move lower together today" if direction == "down" else "move higher together today"
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
    anchor_symbol = _coerce_text(anchor.get("entity_id")).upper()
    return {
        "market_event_id": f"{theme}:{anchor_symbol}:{_coerce_text(anchor.get('horizon')) or 'event'}",
        "event_type": theme,
        "event_title": _event_title(theme, anchor),
        "event_score": score,
        "confidence_label": confidence,
        "asof_time_utc": pd.to_datetime(anchor.get("asof_time_utc"), utc=True, errors="coerce"),
        "anchor_symbol": anchor_symbol,
        "anchor_direction": _observed_direction(anchor),
        "anchor_move_pct": _coerce_float(anchor.get("observed_value")),
        "anchor_attention_score": _coerce_float(anchor.get("attention_score")),
        "what_happened_text": _what_happened_text(anchor, members, theme),
        "why_happened_text": why_text,
        "affected_assets_summary_text": affected_text,
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
        "what_happened_text": _trim(_coerce_text(anchor.get("story_text")) or _coerce_text(anchor.get("title")), 280),
        "why_happened_text": _trim(_coerce_text(anchor.get("why_now_text")) or _coerce_text(anchor.get("story_text")), 280),
        "affected_assets_summary_text": "Affected: " + symbol,
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
