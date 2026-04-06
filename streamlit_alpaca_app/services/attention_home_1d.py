from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .runtime_policy import attention_candidate_policy, source_authority_policy

ENTITY_MASTER_COLUMNS = [
    "symbol",
    "security_name",
    "asset_class",
    "security_type",
    "sector",
    "industry",
    "country",
    "commodity_role",
    "rates_role",
    "defensive_role",
    "macro_role_tags",
    "business_role_tags",
    "source_of_truth",
    "override_reason",
]

CANDIDATE_COLUMNS = [
    "candidate_id",
    "symbol",
    "security_name",
    "headline",
    "source_label",
    "peer_group_name",
    "direction",
    "change_pct",
    "abs_change_pct",
    "expected_move_pct",
    "surprise_pct",
    "surprise_z",
    "close",
    "prev_close",
    "volume",
    "dollar_volume",
    "attention_score",
    "severity_score",
    "candidate_score",
    "in_portfolio",
    "asset_class",
    "security_type",
    "sector",
    "industry",
    "country",
    "commodity_role",
    "rates_role",
    "defensive_role",
    "macro_role_tags",
    "business_role_tags",
    "what_changed_text",
    "why_now_text",
    "cause_status",
    "confidence_label",
    "top_source",
    "best_authority_rank",
    "source_count",
    "evidence_count",
    "same_day_evidence_count",
    "story_text",
    "bundle_id",
]

RESEARCH_EVIDENCE_COLUMNS = [
    "symbol",
    "source",
    "authority_bucket",
    "authority_rank",
    "headline",
    "summary",
    "url",
    "published_at",
]

LOW_SIGNAL_TEXT_FRAGMENTS = (
    "coverage from",
    "clustering around",
    "providing the clearest narrative context",
    "helps explain why the move looks idiosyncratic",
    "looks idiosyncratic",
)

_MODEL_MATH_PATTERNS = (
    r"\bobserved\b",
    r"\bexpected\b",
    r"\bresidual\b",
    r"\bzscore\b",
    r"\bz-score\b",
    r"\battention score\b",
    r"\b20-day baseline\b",
    r"\bversus an expected\b",
    r"\bleaving a residual\b",
)

def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _coerce_float(value: object) -> float:
    numeric = pd.to_numeric(value, errors="coerce")
    return float(numeric) if pd.notna(numeric) else float("nan")


def _safe_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, (tuple, set, pd.Series, pd.Index)):
        items = list(value)
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        text = _coerce_text(item)
        if text:
            out.append(text)
    return out


def _trim(text: object, limit: int = 240) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _normalize_symbol(symbol: object) -> str:
    return _coerce_text(symbol).upper()


def _direction_from_move(change_pct: object) -> str:
    move = _coerce_float(change_pct)
    return "up" if np.isfinite(move) and move >= 0 else "down"


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = _coerce_text(value).upper()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _is_macro_anchor_taxonomy_row(row: dict[str, Any] | pd.Series | None) -> bool:
    payload = row if isinstance(row, dict) else (row.to_dict() if isinstance(row, pd.Series) else {})
    if _coerce_text(payload.get("commodity_role")):
        return True
    if _coerce_text(payload.get("rates_role")):
        return True
    if _coerce_text(payload.get("defensive_role")):
        return True
    return len(_safe_list(payload.get("macro_role_tags"))) > 0


def resolve_macro_anchor_symbols(
    symbols: list[str],
    *,
    taxonomy_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    ordered_symbols = _ordered_unique(symbols)
    if not ordered_symbols:
        return []
    lookup = taxonomy_lookup
    if lookup is None:
        try:
            from .entity_taxonomy import taxonomy_lookup_by_symbol

            lookup = taxonomy_lookup_by_symbol(ordered_symbols)
        except Exception:
            lookup = {}
    return [
        symbol
        for symbol in ordered_symbols
        if _is_macro_anchor_taxonomy_row((lookup or {}).get(symbol, {}))
    ]


def _abs_change_sort(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["change_pct"] = pd.to_numeric(out.get("change_pct"), errors="coerce")
    out["dollar_volume"] = pd.to_numeric(out.get("dollar_volume"), errors="coerce")
    out["_abs_change_pct"] = out["change_pct"].abs()
    out = out.sort_values(["_abs_change_pct", "dollar_volume", "symbol"], ascending=[False, False, True], na_position="last")
    return out.drop(columns=["_abs_change_pct"], errors="ignore").reset_index(drop=True)


def _is_low_signal_text(value: object) -> bool:
    text = _coerce_text(value).lower()
    if not text:
        return True
    return any(fragment in text for fragment in LOW_SIGNAL_TEXT_FRAGMENTS)


def _sentence_split(text: object) -> list[str]:
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]


def _looks_like_model_math_text(text: object) -> bool:
    clean = _coerce_text(text).lower()
    if not clean:
        return False
    return any(re.search(pattern, clean) for pattern in _MODEL_MATH_PATTERNS)


def _looks_like_numeric_tape_sentence(text: object) -> bool:
    clean = _coerce_text(text)
    if not clean:
        return False
    lowered = clean.lower()
    pct_count = len(re.findall(r"[+\-]?\d+(?:\.\d+)?%", clean))
    ticker_pct_pairs = len(re.findall(r"\b[A-Z]{2,5}\s*[+\-]?\d+(?:\.\d+)?%", clean))
    if "20-day baseline" in lowered:
        return True
    if re.search(r"\bup:\b|\bdown:\b", lowered):
        return True
    if ticker_pct_pairs >= 2:
        return True
    return pct_count >= 4


def _clean_source_explanation(text: object, *, limit: int = 240) -> str:
    sentences = _sentence_split(text)
    kept: list[str] = []
    for sentence in sentences:
        if _looks_like_model_math_text(sentence):
            continue
        if _looks_like_numeric_tape_sentence(sentence):
            continue
        kept.append(sentence)
    if kept:
        return _trim(" ".join(kept[:2]), limit)
    clean = _trim(text, limit)
    return "" if _looks_like_model_math_text(clean) else clean


def _clean_source_title(text: object, *, limit: int = 120) -> str:
    clean = _trim(text, limit)
    if not clean:
        return ""
    if _is_low_signal_text(clean):
        return ""
    if _looks_like_model_math_text(clean):
        return ""
    return clean


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
    return "web", 3


def _quality_label(authority_rank: int, evidence_count: int) -> str:
    if authority_rank <= 1 and evidence_count >= 2:
        return "High"
    if authority_rank <= 2 and evidence_count >= 1:
        return "Medium"
    if evidence_count >= 1:
        return "Developing"
    return "Low"


def _build_article_evidence(
    symbol: str,
    news_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(news_payload, dict):
        return []
    articles = news_payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return []

    rows: list[dict[str, Any]] = []
    for _, article in articles.iterrows():
        headline = _trim(article.get("headline"), 220)
        summary = _trim(article.get("summary") or article.get("description"), 260)
        source = _coerce_text(article.get("source")) or "Unknown"
        authority_bucket, authority_rank = _source_authority_bucket(source)
        if not headline and not summary:
            continue
        rows.append(
            {
                "symbol": symbol,
                "source": source,
                "authority_bucket": authority_bucket,
                "authority_rank": authority_rank,
                "headline": headline,
                "summary": summary,
                "url": _coerce_text(article.get("url")),
                "published_at": pd.to_datetime(article.get("published_at"), utc=True, errors="coerce"),
            }
        )
    rows.sort(
        key=lambda item: (
            int(item.get("authority_rank", 9)),
            -(item.get("published_at").value if pd.notna(item.get("published_at")) else 0),
            _coerce_text(item.get("source")).lower(),
        )
    )
    return rows


def _build_context_evidence(
    symbol: str,
    context_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(context_payload, dict):
        return []
    llm_headline = _trim(context_payload.get("llm_headline"), 220)
    llm_why_now = _trim(context_payload.get("llm_why_now"), 260)
    llm_summary = _trim(context_payload.get("llm_summary_text") or context_payload.get("context_story_text"), 260)
    source = _coerce_text(context_payload.get("llm_source_line") or context_payload.get("source_line") or "Primary source")
    authority_bucket, authority_rank = _source_authority_bucket(source or "primary source")

    evidence: list[dict[str, Any]] = []
    if llm_headline or llm_why_now or llm_summary:
        summary = llm_why_now if not _is_low_signal_text(llm_why_now) else llm_summary
        if summary and not _is_low_signal_text(summary):
            evidence.append(
                {
                    "symbol": symbol,
                    "source": source or "Primary source",
                    "authority_bucket": "official" if authority_bucket == "unknown" else authority_bucket,
                    "authority_rank": 0 if authority_rank >= 3 else authority_rank,
                    "headline": llm_headline,
                    "summary": summary,
                    "url": "",
                    "published_at": pd.NaT,
                }
            )

    for item in list(context_payload.get("top_filing_links") or [])[:3]:
        label = _trim((item or {}).get("label"), 160)
        url = _coerce_text((item or {}).get("url"))
        if not label:
            continue
        evidence.append(
            {
                "symbol": symbol,
                "source": "SEC filing",
                "authority_bucket": "official",
                "authority_rank": 0,
                "headline": label,
                "summary": "",
                "url": url,
                "published_at": pd.NaT,
            }
        )
    return evidence


def _sort_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in rows:
        key = (
            _coerce_text(item.get("source")).lower(),
            _coerce_text(item.get("headline")).lower(),
            _coerce_text(item.get("summary")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(
        key=lambda item: (
            int(item.get("authority_rank", 9)),
            -(item.get("published_at").value if pd.notna(item.get("published_at")) else 0),
            _coerce_text(item.get("source")).lower(),
        )
    )
    return deduped


def _symbol_evidence_rows(
    symbol: str,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    evidence = _build_context_evidence(symbol, (context_payloads or {}).get(symbol))
    evidence.extend(_build_article_evidence(symbol, (news_payloads or {}).get(symbol)))
    return _sort_evidence_rows(evidence)


def _best_explanation_text(
    symbol: str,
    evidence_rows: list[dict[str, Any]],
    attention_row: pd.Series | None = None,
    context_payload: dict[str, Any] | None = None,
) -> str:
    attention_series = attention_row if isinstance(attention_row, pd.Series) else pd.Series(dtype=object)
    for item in evidence_rows:
        summary = _clean_source_explanation(item.get("summary"), limit=240)
        if summary and not _is_low_signal_text(summary):
            return summary
        headline = _clean_source_explanation(item.get("headline"), limit=240)
        if headline and not _is_low_signal_text(headline):
            return headline

    for candidate in [
        _coerce_text((context_payload or {}).get("llm_why_now")),
        _coerce_text((context_payload or {}).get("llm_summary_text")),
        _coerce_text((context_payload or {}).get("context_story_text")),
        _coerce_text(attention_series.get("why_now_text")),
        _coerce_text(attention_series.get("story_text")),
    ]:
        cleaned = _clean_source_explanation(candidate, limit=240)
        if cleaned and not _is_low_signal_text(cleaned):
            return cleaned
    return ""


def _headline_text_from_evidence(
    symbol: str,
    evidence_rows: list[dict[str, Any]],
    *,
    attention_row: pd.Series | None = None,
    context_payload: dict[str, Any] | None = None,
) -> str:
    for item in evidence_rows:
        headline = _clean_source_title(item.get("headline"), limit=120)
        if headline:
            return headline
    attention_series = attention_row if isinstance(attention_row, pd.Series) else pd.Series(dtype=object)
    for candidate in [
        (context_payload or {}).get("llm_headline"),
        attention_series.get("headline"),
        attention_series.get("title"),
    ]:
        headline = _clean_source_title(candidate, limit=120)
        if headline:
            return headline
    return symbol


def _peer_group_name(row: pd.Series) -> str:
    industry = _coerce_text(row.get("industry"))
    sector = _coerce_text(row.get("sector"))
    commodity_role = _coerce_text(row.get("commodity_role"))
    rates_role = _coerce_text(row.get("rates_role"))
    defensive_role = _coerce_text(row.get("defensive_role"))
    business_tags = _safe_list(row.get("business_role_tags"))
    macro_tags = _safe_list(row.get("macro_role_tags"))
    for role in [commodity_role, rates_role, defensive_role]:
        if role:
            return role.replace("_", " ").title()
    if industry and industry != "Unknown":
        return industry
    if sector and sector != "Unknown":
        return sector
    if business_tags:
        return business_tags[0].replace("_", " ").title()
    if macro_tags:
        return macro_tags[0].replace("_", " ").title()
    return "Market"


def _confidence_from_candidate(evidence_rows: list[dict[str, Any]], surprise_z: float, change_pct: float) -> str:
    policy = attention_candidate_policy()
    best_rank = min((int(item.get("authority_rank", 9)) for item in evidence_rows), default=9)
    evidence_count = len(evidence_rows)
    if (
        abs(change_pct) >= policy.confidence_high_abs_change_pct
        and abs(surprise_z) >= policy.confidence_high_abs_surprise_z
        and best_rank <= policy.confidence_high_best_authority_rank_max
        and evidence_count >= policy.confidence_high_evidence_min
    ):
        return "High"
    if (
        abs(change_pct) >= policy.confidence_medium_abs_change_pct
        and (abs(surprise_z) >= policy.confidence_medium_abs_surprise_z or evidence_count >= 1)
    ):
        return "Medium"
    return "Developing"


def _move_vs_expectation_text(symbol: str, change_pct: float, expected_move_pct: float, surprise_z: float) -> str:
    direction = "rose" if change_pct >= 0 else "fell"
    intensity = "modestly"
    if (np.isfinite(surprise_z) and abs(surprise_z) >= 2.5) or abs(change_pct) >= 4.0:
        intensity = "sharply"
    elif (np.isfinite(surprise_z) and abs(surprise_z) >= 1.4) or abs(change_pct) >= 2.0:
        intensity = "meaningfully"
    if np.isfinite(expected_move_pct):
        return f"{symbol} {direction} {intensity} today relative to its recent baseline."
    return f"{symbol} {direction} {intensity} today."


def _compute_expectation_stats(frame: pd.DataFrame | None) -> tuple[float, float, float]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "close" not in frame.columns:
        return float("nan"), float("nan"), float("nan")
    out = frame.copy()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["close"]).sort_values("timestamp" if "timestamp" in out.columns else out.index.name or "close")
    if len(out) < 6:
        return float("nan"), float("nan"), float("nan")

    returns = out["close"].pct_change() * 100.0
    returns = returns.dropna()
    if returns.empty:
        return float("nan"), float("nan"), float("nan")
    today = float(returns.iloc[-1])
    baseline = returns.iloc[:-1].tail(20)
    if baseline.empty:
        return today, float("nan"), float("nan")
    expected = float(baseline.mean())
    std = float(baseline.std(ddof=0))
    surprise = today - expected
    if not np.isfinite(std) or std <= 0.25:
        std = 0.25
    return expected, surprise, surprise / std


def _build_entity_row(
    symbol: str,
    asset_metadata: dict[str, Any] | None = None,
    *,
    taxonomy_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    if taxonomy_row is not None:
        try:
            from .entity_taxonomy import informative_taxonomy_row
        except Exception:
            informative_taxonomy_row = None
        if informative_taxonomy_row is not None and informative_taxonomy_row(taxonomy_row):
            security_name = (
                _coerce_text(taxonomy_row.get("security_name"))
                or _coerce_text(taxonomy_row.get("company_name"))
                or _coerce_text(taxonomy_row.get("name"))
            )
            return {
                "symbol": normalized,
                "security_name": security_name,
                "asset_class": _coerce_text(taxonomy_row.get("asset_class")) or "equity",
                "security_type": _coerce_text(taxonomy_row.get("security_type")) or "common_stock",
                "sector": _coerce_text(taxonomy_row.get("sector")) or "Unknown",
                "industry": _coerce_text(taxonomy_row.get("industry")) or "Unknown",
                "country": _coerce_text(taxonomy_row.get("country")) or "US",
                "commodity_role": _coerce_text(taxonomy_row.get("commodity_role")),
                "rates_role": _coerce_text(taxonomy_row.get("rates_role")),
                "defensive_role": _coerce_text(taxonomy_row.get("defensive_role")),
                "macro_role_tags": _safe_list(taxonomy_row.get("macro_role_tags")),
                "business_role_tags": _safe_list(taxonomy_row.get("business_role_tags")),
                "source_of_truth": _coerce_text(taxonomy_row.get("source_of_truth")) or "entity_taxonomy",
                "override_reason": _coerce_text(taxonomy_row.get("override_reason")) or "Loaded from entity taxonomy store.",
            }

    asset = asset_metadata or {}
    name = _coerce_text(asset.get("name"))
    asset_class = _coerce_text(asset.get("class")) or "equity"
    security_type = "etf" if any(token in name.lower() for token in ["etf", "trust", "fund"]) else "common_stock"
    return {
        "symbol": normalized,
        "security_name": name,
        "asset_class": asset_class,
        "security_type": security_type,
        "sector": "Unknown",
        "industry": "Unknown",
        "country": "US",
        "commodity_role": "",
        "rates_role": "",
        "defensive_role": "",
        "macro_role_tags": [],
        "business_role_tags": [],
        "source_of_truth": "listing_metadata",
        "override_reason": "No dynamic taxonomy row was available for this symbol at build time.",
    }


def build_attention_entity_master(
    symbols: list[str],
    *,
    asset_metadata_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    ordered_symbols = _ordered_unique(symbols)
    taxonomy_lookup: dict[str, dict[str, Any]] = {}
    if ordered_symbols:
        try:
            from .entity_taxonomy import taxonomy_lookup_by_symbol

            taxonomy_lookup = taxonomy_lookup_by_symbol(ordered_symbols)
        except Exception:
            taxonomy_lookup = {}
    rows = [
        _build_entity_row(
            symbol,
            (asset_metadata_by_symbol or {}).get(_normalize_symbol(symbol), {}),
            taxonomy_row=taxonomy_lookup.get(_normalize_symbol(symbol)),
        )
        for symbol in ordered_symbols
    ]
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=ENTITY_MASTER_COLUMNS)
    for column in ["macro_role_tags", "business_role_tags"]:
        out[column] = out[column].apply(lambda value: _safe_list(value))
    return out[ENTITY_MASTER_COLUMNS].copy()


def shortlist_attention_symbols_1d(
    daily_movers: pd.DataFrame,
    *,
    holdings: list[str] | None = None,
    attention_rows: pd.DataFrame | None = None,
    max_count: int | None = None,
) -> list[str]:
    policy = attention_candidate_policy()
    movers = daily_movers.copy() if isinstance(daily_movers, pd.DataFrame) else pd.DataFrame()
    if movers.empty and (attention_rows is None or attention_rows.empty):
        return []
    if not movers.empty:
        movers["symbol"] = movers.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
        movers["change_pct"] = pd.to_numeric(movers.get("change_pct"), errors="coerce")
        movers["close"] = pd.to_numeric(movers.get("close"), errors="coerce")
        movers["volume"] = pd.to_numeric(movers.get("volume"), errors="coerce")
        if "dollar_volume" not in movers.columns:
            movers["dollar_volume"] = movers["close"] * movers["volume"]
        movers["dollar_volume"] = pd.to_numeric(movers.get("dollar_volume"), errors="coerce")
        movers = movers.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="first")

    ordered: list[str] = []

    def _extend(values: list[str]) -> None:
        for value in values:
            item = _normalize_symbol(value)
            if not item or item in ordered:
                continue
            ordered.append(item)

    macro_anchor_symbols: set[str] = set()
    if not movers.empty:
        macro_anchor_symbols.update(
            resolve_macro_anchor_symbols(
                movers["symbol"].dropna().astype(str).tolist(),
            )
        )

    if not movers.empty:
        liquid = movers[
            movers["dollar_volume"].fillna(0).ge(policy.shortlist_liquidity_min_dollar_volume)
            | movers["symbol"].isin(macro_anchor_symbols)
        ].copy()
        if liquid.empty:
            liquid = movers.copy()

        gainers = liquid[liquid["change_pct"] > 0].sort_values(["change_pct", "dollar_volume"], ascending=[False, False], na_position="last")
        losers = liquid[liquid["change_pct"] < 0].sort_values(["change_pct", "dollar_volume"], ascending=[True, False], na_position="last")
        macro = _abs_change_sort(liquid[liquid["symbol"].isin(macro_anchor_symbols)].copy())
        large = _abs_change_sort(
            liquid[
                liquid["change_pct"].abs().ge(policy.confidence_medium_abs_change_pct)
                | (
                    liquid["symbol"].isin(macro_anchor_symbols)
                    & liquid["change_pct"].abs().ge(policy.shortlist_macro_anchor_min_abs_change_pct)
                )
            ].copy()
        )
        _extend(macro["symbol"].head(20).tolist())
        _extend(losers["symbol"].head(25).tolist())
        _extend(gainers["symbol"].head(25).tolist())
        _extend(large["symbol"].head(40).tolist())

        holding_set = {_normalize_symbol(item) for item in list(holdings or []) if _normalize_symbol(item)}
        if holding_set:
            held = _abs_change_sort(movers[movers["symbol"].isin(holding_set)].copy())
            _extend(held["symbol"].head(20).tolist())

    if isinstance(attention_rows, pd.DataFrame) and not attention_rows.empty:
        rows = attention_rows.copy()
        rows["entity_id"] = rows.get("entity_id", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
        rows["attention_score"] = pd.to_numeric(rows.get("attention_score"), errors="coerce")
        rows = rows.sort_values(["attention_score", "entity_id"], ascending=[False, True], na_position="last")
        _extend(rows["entity_id"].head(25).tolist())

    effective_max = policy.shortlist_default_max_count if max_count is None else max_count
    return ordered[: max(int(effective_max), 1)]


def _candidate_score(
    *,
    change_pct: float,
    surprise_z: float,
    dollar_volume: float,
    best_authority_rank: int,
    evidence_count: int,
    attention_score: float,
    is_macro_anchor: bool,
    in_portfolio: bool,
) -> float:
    policy = attention_candidate_policy()
    move_score = min(abs(change_pct) * policy.move_score_mult, policy.move_score_cap)
    surprise_score = (
        min(abs(surprise_z) * policy.surprise_score_mult, policy.surprise_score_cap)
        if np.isfinite(surprise_z)
        else min(abs(change_pct) * policy.surprise_fallback_mult, policy.surprise_fallback_cap)
    )
    if np.isfinite(dollar_volume) and dollar_volume > 0:
        liquidity_score = min(
            max(math.log10(dollar_volume) - policy.liquidity_log10_offset, 0.0) * policy.liquidity_score_mult,
            policy.liquidity_score_cap,
        )
    else:
        liquidity_score = 0.0
    evidence_score = 0.0
    if evidence_count > 0:
        evidence_score = policy.evidence_score_base + min(evidence_count * policy.evidence_score_per_item, policy.evidence_score_cap)
        if best_authority_rank <= 0:
            evidence_score += policy.authority_bonus_official
        elif best_authority_rank <= 1:
            evidence_score += policy.authority_bonus_wire
        elif best_authority_rank <= 2:
            evidence_score += policy.authority_bonus_press
    attention_bonus = min(attention_score * policy.attention_bonus_mult, policy.attention_bonus_cap) if np.isfinite(attention_score) else 0.0
    macro_bonus = policy.macro_bonus if is_macro_anchor else 0.0
    portfolio_bonus = policy.portfolio_bonus if in_portfolio else 0.0
    return round(move_score + surprise_score + liquidity_score + evidence_score + attention_bonus + macro_bonus + portfolio_bonus, 1)


def build_attention_event_candidates_1d(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    asof_time_utc: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    movers = daily_movers.copy() if isinstance(daily_movers, pd.DataFrame) else pd.DataFrame()
    if movers.empty and (attention_rows is None or attention_rows.empty):
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    if not movers.empty:
        movers["symbol"] = movers.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
        movers["change_pct"] = pd.to_numeric(movers.get("change_pct"), errors="coerce")
        movers["close"] = pd.to_numeric(movers.get("close"), errors="coerce")
        movers["prev_close"] = pd.to_numeric(movers.get("prev_close"), errors="coerce")
        movers["volume"] = pd.to_numeric(movers.get("volume"), errors="coerce")
        if "dollar_volume" not in movers.columns:
            movers["dollar_volume"] = movers["close"] * movers["volume"]
        movers["dollar_volume"] = pd.to_numeric(movers.get("dollar_volume"), errors="coerce")
        movers = movers.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)

    attention = attention_rows.copy() if isinstance(attention_rows, pd.DataFrame) else pd.DataFrame()
    if not attention.empty:
        attention["entity_id"] = attention.get("entity_id", pd.Series(dtype=str)).astype(str).str.upper().str.strip()
        attention["attention_score"] = pd.to_numeric(attention.get("attention_score"), errors="coerce")
        attention["severity_score"] = pd.to_numeric(attention.get("severity_score"), errors="coerce")
        attention = attention.sort_values(["attention_score", "severity_score"], ascending=[False, False], na_position="last")

    symbols = shortlist_attention_symbols_1d(movers, holdings=holdings, attention_rows=attention)
    if entity_master is None:
        entity_master = build_attention_entity_master(symbols)
    macro_anchor_symbols = {
        _normalize_symbol(row.get("symbol"))
        for _, row in entity_master.iterrows()
        if _normalize_symbol(row.get("symbol")) and _is_macro_anchor_taxonomy_row(row)
    }
    entity_lookup = {
        _normalize_symbol(row.get("symbol")): row
        for _, row in entity_master.iterrows()
    }
    mover_lookup = {
        _normalize_symbol(row.get("symbol")): row
        for _, row in movers.iterrows()
    }
    attention_lookup = {
        _normalize_symbol(row.get("entity_id")): row
        for _, row in attention.iterrows()
        if _normalize_symbol(row.get("entity_id"))
    }
    holding_set = {_normalize_symbol(item) for item in list(holdings or []) if _normalize_symbol(item)}
    asof_ts = pd.to_datetime(asof_time_utc if asof_time_utc is not None else datetime.now(timezone.utc), utc=True, errors="coerce")
    if pd.isna(asof_ts):
        asof_ts = pd.Timestamp.now(tz="UTC")

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        mover_row = mover_lookup.get(symbol, pd.Series(dtype=object))
        attention_row = attention_lookup.get(symbol, pd.Series(dtype=object))
        entity_row = entity_lookup.get(symbol, pd.Series(dtype=object))
        change_pct = _coerce_float(mover_row.get("change_pct"))
        if not np.isfinite(change_pct):
            change_pct = _coerce_float(attention_row.get("observed_value"))
        if not np.isfinite(change_pct):
            continue
        expected_move_pct, surprise_pct, surprise_z = _compute_expectation_stats((bars_by_symbol or {}).get(symbol))
        if not np.isfinite(surprise_pct) and np.isfinite(change_pct):
            surprise_pct = change_pct - (expected_move_pct if np.isfinite(expected_move_pct) else 0.0)
        evidence_rows = _symbol_evidence_rows(symbol, news_payloads=news_payloads, context_payloads=context_payloads)
        best_authority_rank = min((int(item.get("authority_rank", 9)) for item in evidence_rows), default=9)
        same_day_evidence_count = sum(
            1
            for item in evidence_rows
            if pd.notna(pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"))
            and pd.to_datetime(item.get("published_at"), utc=True, errors="coerce").date() == asof_ts.date()
        )
        cause_status = "supported" if evidence_rows else "unresolved"
        why_now_text = _best_explanation_text(
            symbol,
            evidence_rows,
            attention_row=attention_row,
            context_payload=(context_payloads or {}).get(symbol),
        )
        if why_now_text and cause_status == "unresolved":
            cause_status = "developing"
        top_source = _coerce_text(evidence_rows[0].get("source")) if evidence_rows else ""
        source_count = len({str(item.get("source") or "").strip() for item in evidence_rows if str(item.get("source") or "").strip()})
        confidence_label = _confidence_from_candidate(evidence_rows, surprise_z, change_pct)
        attention_score = _coerce_float(attention_row.get("attention_score"))
        severity_score = _coerce_float(attention_row.get("severity_score"))
        dollar_volume = _coerce_float(mover_row.get("dollar_volume"))
        in_portfolio = symbol in holding_set
        candidate_score = _candidate_score(
            change_pct=change_pct,
            surprise_z=surprise_z,
            dollar_volume=dollar_volume,
            best_authority_rank=best_authority_rank,
            evidence_count=len(evidence_rows),
            attention_score=attention_score,
            is_macro_anchor=symbol in macro_anchor_symbols,
            in_portfolio=in_portfolio,
        )
        source_label = "Macro anchor" if symbol in macro_anchor_symbols else _coerce_text(entity_row.get("sector")) or "Equities"
        what_changed_text = _move_vs_expectation_text(
            symbol,
            change_pct,
            expected_move_pct if np.isfinite(expected_move_pct) else 0.0,
            surprise_z,
        )
        headline = _headline_text_from_evidence(
            symbol,
            evidence_rows,
            attention_row=attention_row,
            context_payload=(context_payloads or {}).get(symbol),
        )
        story_text = what_changed_text
        rows.append(
            {
                "candidate_id": f"candidate::{symbol}",
                "symbol": symbol,
                "security_name": (
                    _coerce_text(entity_row.get("security_name"))
                    or _coerce_text(mover_row.get("security_name"))
                    or _coerce_text(mover_row.get("company_name"))
                    or _coerce_text(mover_row.get("name"))
                ),
                "headline": headline,
                "source_label": source_label,
                "peer_group_name": _peer_group_name(pd.Series(entity_row)),
                "direction": _direction_from_move(change_pct),
                "change_pct": round(change_pct, 2),
                "abs_change_pct": round(abs(change_pct), 2),
                "expected_move_pct": round(expected_move_pct, 2) if np.isfinite(expected_move_pct) else np.nan,
                "surprise_pct": round(surprise_pct, 2) if np.isfinite(surprise_pct) else np.nan,
                "surprise_z": round(surprise_z, 2) if np.isfinite(surprise_z) else np.nan,
                "close": _coerce_float(mover_row.get("close")),
                "prev_close": _coerce_float(mover_row.get("prev_close")),
                "volume": _coerce_float(mover_row.get("volume")),
                "dollar_volume": dollar_volume,
                "attention_score": attention_score,
                "severity_score": severity_score if np.isfinite(severity_score) else candidate_score,
                "candidate_score": candidate_score,
                "in_portfolio": in_portfolio,
                "asset_class": _coerce_text(entity_row.get("asset_class")) or "equity",
                "security_type": _coerce_text(entity_row.get("security_type")) or "common_stock",
                "sector": _coerce_text(entity_row.get("sector")) or "Unknown",
                "industry": _coerce_text(entity_row.get("industry")) or "Unknown",
                "country": _coerce_text(entity_row.get("country")) or "US",
                "commodity_role": _coerce_text(entity_row.get("commodity_role")),
                "rates_role": _coerce_text(entity_row.get("rates_role")),
                "defensive_role": _coerce_text(entity_row.get("defensive_role")),
                "macro_role_tags": _safe_list(entity_row.get("macro_role_tags")),
                "business_role_tags": _safe_list(entity_row.get("business_role_tags")),
                "what_changed_text": what_changed_text,
                "why_now_text": why_now_text,
                "cause_status": cause_status,
                "confidence_label": confidence_label,
                "top_source": top_source,
                "best_authority_rank": int(best_authority_rank),
                "source_count": int(source_count),
                "evidence_count": int(len(evidence_rows)),
                "same_day_evidence_count": int(same_day_evidence_count),
                "story_text": story_text,
                "bundle_id": f"symbol::{symbol}",
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    out["candidate_score"] = pd.to_numeric(out["candidate_score"], errors="coerce")
    out["dollar_volume"] = pd.to_numeric(out["dollar_volume"], errors="coerce")
    out["change_pct"] = pd.to_numeric(out["change_pct"], errors="coerce")
    out = out.sort_values(["candidate_score", "dollar_volume", "change_pct"], ascending=[False, False, False], na_position="last").reset_index(drop=True)
    return out[CANDIDATE_COLUMNS].copy()


def _enriched_context_payloads(
    candidates: pd.DataFrame,
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for _, row in candidates.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        current = dict((context_payloads or {}).get(symbol) or {})
        evidence_rows = _symbol_evidence_rows(symbol, news_payloads=news_payloads, context_payloads=context_payloads)
        if evidence_rows:
            best = evidence_rows[0]
            if _is_low_signal_text(current.get("llm_why_now")):
                current["llm_why_now"] = _coerce_text(best.get("summary") or best.get("headline"))
            if not _coerce_text(current.get("llm_summary_text")):
                current["llm_summary_text"] = _coerce_text(best.get("summary") or best.get("headline"))
            if not _coerce_text(current.get("llm_headline")):
                current["llm_headline"] = _coerce_text(best.get("headline"))
            if not _coerce_text(current.get("llm_source_line")):
                current["llm_source_line"] = _coerce_text(best.get("source"))
        out[symbol] = current
    return out


def _candidate_feed_for_events(candidates: pd.DataFrame, generated_at_utc: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        rows.append(
            {
                "event_id": _coerce_text(row.get("candidate_id")) or f"evt-{_normalize_symbol(row.get('symbol')).lower()}",
                "asof_time_utc": generated_at_utc,
                "entity_id": _normalize_symbol(row.get("symbol")),
                "direction": _coerce_text(row.get("direction")),
                "observed_value": _coerce_float(row.get("change_pct")),
                "attention_score": _coerce_float(row.get("candidate_score")),
                "severity_score": _coerce_float(row.get("severity_score")) if np.isfinite(_coerce_float(row.get("severity_score"))) else _coerce_float(row.get("candidate_score")),
                "title": _coerce_text(row.get("headline")) or f"{_normalize_symbol(row.get('symbol'))} moved sharply today",
                "subtitle": "Today vs expectation",
                "horizon": "1d",
                "peer_group_name": _coerce_text(row.get("peer_group_name")) or _coerce_text(row.get("sector")) or "Market",
                "source_label": _coerce_text(row.get("source_label")) or "Market",
                "story_text": _coerce_text(row.get("what_changed_text")),
                "why_now_text": _coerce_text(row.get("why_now_text")),
            }
        )
    return pd.DataFrame(rows)


def _event_impacts_rows(
    top_events: list[dict[str, Any]],
    candidate_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in top_events:
        event_id = _coerce_text(event.get("market_event_id"))
        drivers = {_normalize_symbol(item) for item in list(event.get("driver_symbols") or []) if _normalize_symbol(item)}
        beneficiaries = {_normalize_symbol(item) for item in list(event.get("beneficiary_symbols") or []) if _normalize_symbol(item)}
        supporting = [_normalize_symbol(item) for item in list(event.get("supporting_symbols") or []) if _normalize_symbol(item)]
        for symbol in supporting:
            candidate = candidate_lookup.get(symbol, {})
            if symbol in drivers:
                impact_role = "driver"
            elif symbol in beneficiaries:
                impact_role = "beneficiary"
            else:
                impact_role = "affected"
            rows.append(
                {
                    "market_event_id": event_id,
                    "symbol": symbol,
                    "impact_role": impact_role,
                    "direction": _coerce_text(candidate.get("direction")),
                    "change_pct": candidate.get("change_pct"),
                    "sector": candidate.get("sector"),
                    "industry": candidate.get("industry"),
                    "bundle_id": f"symbol::{symbol}",
                }
            )
    return rows


def build_attention_home_1d(
    daily_movers: pd.DataFrame,
    *,
    attention_rows: pd.DataFrame | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
    entity_master: pd.DataFrame | None = None,
    holdings: list[str] | None = None,
    generated_at_utc: datetime | str | None = None,
    top_events_limit: int = 5,
    must_read_limit: int = 10,
    unresolved_limit: int = 5,
) -> dict[str, Any]:
    from .attention_agentic import build_bottom_up_attention_home

    return build_bottom_up_attention_home(
        daily_movers,
        attention_rows=attention_rows,
        bars_by_symbol=bars_by_symbol,
        news_payloads=news_payloads,
        context_payloads=context_payloads,
        entity_master=entity_master,
        holdings=holdings,
        generated_at_utc=generated_at_utc,
        top_events_limit=top_events_limit,
        must_read_limit=must_read_limit,
        unresolved_limit=unresolved_limit,
    )


def build_attention_research_bundle(
    bundle_id: str,
    home_payload: dict[str, Any],
    *,
    news_payloads: dict[str, dict[str, Any]] | None = None,
    context_payloads: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .attention_agentic import build_bottom_up_attention_bundle

    normalized_bundle_id = _coerce_text(bundle_id)
    agentic_bundle = build_bottom_up_attention_bundle(normalized_bundle_id, home_payload)
    if agentic_bundle and any(agentic_bundle.get(key) for key in ["claims", "evidence", "background_context"]):
        return agentic_bundle
    top_events = list(home_payload.get("top_events") or [])
    candidates = list(home_payload.get("event_candidates_1d") or [])
    candidate_lookup = {_normalize_symbol(item.get("symbol")): item for item in candidates if _normalize_symbol(item.get("symbol"))}

    if normalized_bundle_id.startswith("symbol::"):
        symbol = _normalize_symbol(normalized_bundle_id.split("::", 1)[1])
        candidate = candidate_lookup.get(symbol, {})
        evidence_rows = _symbol_evidence_rows(symbol, news_payloads=news_payloads, context_payloads=context_payloads)
        authority_rank = min((int(item.get("authority_rank", 9)) for item in evidence_rows), default=9)
        return {
            "bundle_id": normalized_bundle_id,
            "bundle_type": "symbol",
            "symbol": symbol,
            "headline": _coerce_text(candidate.get("headline")) or symbol,
            "what_changed_text": _coerce_text(candidate.get("what_changed_text")),
            "why_now_text": _coerce_text(candidate.get("why_now_text")),
            "cause_status": _coerce_text(candidate.get("cause_status")) or "unresolved",
            "confidence_label": _coerce_text(candidate.get("confidence_label")) or "Developing",
            "sector": _coerce_text(candidate.get("sector")),
            "industry": _coerce_text(candidate.get("industry")),
            "evidence_quality": _quality_label(authority_rank, len(evidence_rows)),
            "evidence": [
                {
                    "source": _coerce_text(item.get("source")),
                    "authority_bucket": _coerce_text(item.get("authority_bucket")),
                    "headline": _coerce_text(item.get("headline")),
                    "summary": _coerce_text(item.get("summary")),
                    "url": _coerce_text(item.get("url")),
                    "published_at": pd.to_datetime(item.get("published_at"), utc=True, errors="coerce").isoformat()
                    if pd.notna(pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"))
                    else "",
                }
                for item in evidence_rows
            ],
            "related_symbols": [],
        }

    event = next((item for item in top_events if _coerce_text(item.get("bundle_id")) == normalized_bundle_id), {})
    supporting_symbols = [_normalize_symbol(item) for item in list(event.get("supporting_symbols") or []) if _normalize_symbol(item)]
    supporting_candidates = [candidate_lookup.get(symbol, {}) for symbol in supporting_symbols]
    evidence_rows: list[dict[str, Any]] = []
    for symbol in supporting_symbols:
        evidence_rows.extend(_symbol_evidence_rows(symbol, news_payloads=news_payloads, context_payloads=context_payloads))
    evidence_rows = _sort_evidence_rows(evidence_rows)
    authority_rank = min((int(item.get("authority_rank", 9)) for item in evidence_rows), default=9)
    return {
        "bundle_id": normalized_bundle_id,
        "bundle_type": "event",
        "event_title": _coerce_text(event.get("event_title")) or "Market event",
        "what_happened_text": _coerce_text(event.get("what_happened_text")),
        "why_happened_text": _coerce_text(event.get("why_happened_text")),
        "affected_assets_summary_text": _coerce_text(event.get("affected_assets_summary_text")),
        "cause_status": _coerce_text(event.get("cause_status")) or "unresolved",
        "confidence_label": _coerce_text(event.get("confidence_label")) or "Developing",
        "evidence_quality": _quality_label(authority_rank, len(evidence_rows)),
        "evidence": [
            {
                "symbol": _coerce_text(item.get("symbol")),
                "source": _coerce_text(item.get("source")),
                "authority_bucket": _coerce_text(item.get("authority_bucket")),
                "headline": _coerce_text(item.get("headline")),
                "summary": _coerce_text(item.get("summary")),
                "url": _coerce_text(item.get("url")),
                "published_at": pd.to_datetime(item.get("published_at"), utc=True, errors="coerce").isoformat()
                if pd.notna(pd.to_datetime(item.get("published_at"), utc=True, errors="coerce"))
                else "",
            }
            for item in evidence_rows[:12]
        ],
        "related_symbols": [
            {
                "symbol": _normalize_symbol(candidate.get("symbol")),
                "headline": _coerce_text(candidate.get("headline")),
                "change_pct": _coerce_float(candidate.get("change_pct")),
                "sector": _coerce_text(candidate.get("sector")),
                "industry": _coerce_text(candidate.get("industry")),
            }
            for candidate in supporting_candidates
            if candidate
        ],
    }


__all__ = [
    "CANDIDATE_COLUMNS",
    "ENTITY_MASTER_COLUMNS",
    "RESEARCH_EVIDENCE_COLUMNS",
    "build_attention_entity_master",
    "build_attention_event_candidates_1d",
    "build_attention_home_1d",
    "build_attention_research_bundle",
    "resolve_macro_anchor_symbols",
    "shortlist_attention_symbols_1d",
]
