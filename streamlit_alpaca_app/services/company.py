from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re

import pandas as pd

from .alpaca_api import AlpacaAPI, AlpacaAPIError
from .market import BUSINESS_FOCUS_UNIVERSES, commodity_proxy_profile


COMPANY_ROLE_HINTS: dict[str, str] = {
    "A": "sells analytical instruments, diagnostics, and lab workflow tools used across research and applied testing",
    "AAPL": "builds a consumer device, software, and services ecosystem centered on the iPhone and installed-base monetization",
    "MSFT": "sells enterprise software, cloud infrastructure, productivity tools, and AI-enabled platforms",
    "NVDA": "designs accelerated-computing chips and software used across AI, data-center, and graphics workloads",
    "AMZN": "combines e-commerce, logistics, advertising, and cloud infrastructure through AWS",
    "META": "runs large social platforms monetized through digital advertising and engagement",
    "GOOGL": "monetizes search, video, cloud, and broader digital advertising demand",
    "TSLA": "sells electric vehicles, energy storage, and software-driven mobility products",
    "NFLX": "sells subscription streaming and monetizes audience engagement through content and ad-supported tiers",
    "AMD": "designs CPUs, GPUs, and related silicon for data-center, PC, gaming, and AI workloads",
    "INTC": "designs and manufactures processors and semiconductor platforms for PCs, servers, and edge systems",
    "JPM": "provides consumer banking, payments, capital-markets, and investment-banking services",
    "V": "operates a global payments network that earns from transaction volume and payment flows",
    "MA": "operates a global card and payments network tied to consumer and business spend",
    "PYPL": "provides digital checkout, merchant tools, and consumer payments products",
    "SHOP": "sells merchant software, storefront tooling, and commerce infrastructure",
    "UBER": "runs ride-sharing, delivery, and mobility marketplaces",
    "XOM": "produces oil and gas and monetizes energy supply through upstream and downstream operations",
    "CVX": "produces oil and gas and monetizes energy supply across upstream, refining, and chemicals",
    "COP": "focuses on upstream oil and gas production tied to energy-price cycles",
    "SLB": "sells oilfield services and technology into upstream energy spending",
    "PFE": "develops and sells branded medicines and biopharma products",
    "JNJ": "sells pharmaceuticals and medical technologies across a broad healthcare portfolio",
    "LLY": "develops branded medicines with a narrative often tied to obesity, diabetes, and pipeline execution",
    "UNH": "sells managed-care coverage and healthcare-services infrastructure",
    "MRK": "develops branded medicines tied to oncology, vaccines, and pipeline execution",
    "ABBV": "sells branded pharmaceuticals with exposure to immunology, aesthetics, and new-product ramp",
}

BUSINESS_ROLE_HINTS: dict[str, str] = {
    "Housing": "operates a housing-linked business tied to homebuilding, renovation, and housing turnover",
    "Retail": "sells goods or retail services directly to consumers through stores, digital storefronts, or merchant platforms",
    "Media": "owns or distributes content, channels, streaming, or media platforms",
    "Social Media & Entertainment": "monetizes audience attention through social platforms, streaming, music, or interactive entertainment",
    "Advertising": "makes money from ad budgets, audience targeting, or performance-marketing infrastructure",
    "Commodity": "sells or produces energy, metals, mining, fertilizer, or other commodity-linked output",
    "Payments & Commerce": "sells transaction rails, merchant tooling, checkout, or commerce-enablement software",
    "Travel & Mobility": "monetizes travel demand through ride-sharing, booking, airline, lodging, or related mobility services",
    "Healthcare & Life Sciences": "sells drugs, medical tools, managed-care products, or life-science workflow services",
}

BUSINESS_NARRATIVE_HINTS: dict[str, str] = {
    "Housing": "rates, affordability, repair-and-remodel demand, and new-home supply",
    "Retail": "consumer spending, trade-down behavior, inventory discipline, and e-commerce share",
    "Media": "content cadence, streaming economics, affiliate-fee pressure, and the ad cycle",
    "Social Media & Entertainment": "engagement, subscriber momentum, creator monetization, and release cadence",
    "Advertising": "brand budgets, performance-marketing demand, targeting efficiency, and ad-platform product cycles",
    "Commodity": "underlying commodity prices, supply discipline, cost inflation, and capital spending",
    "Payments & Commerce": "payment volumes, checkout conversion, merchant adoption, and consumer spend mix",
    "Travel & Mobility": "travel demand, occupancy/load factors, pricing power, and consumer mobility trends",
    "Healthcare & Life Sciences": "drug uptake, reimbursement, medical utilization, and pipeline or tooling demand",
}

NEWS_THEME_KEYWORDS: list[tuple[str, list[str]]] = [
    ("AI rollout", [" ai ", "artificial intelligence", "copilot", "model", "llm", "on-device ai", "ai spending", "ai capex"]),
    ("data center buildout", ["data center", "datacenter", "gpu cluster", "training cluster", "server demand", "rack build"]),
    ("product cycle", ["product cycle", "replacement cycle", "upgrade cycle", "device", "iphone", "launch"]),
    ("services monetization", ["services", "subscription", "attach rate", "installed base", "monetization"]),
    ("cloud and enterprise spend", ["cloud", "enterprise", "software", "seat growth", "data center", "workload"]),
    ("digital ad demand", ["advertising", "ad spend", "ad market", "performance marketing", "brand budgets"]),
    ("consumer spending", ["consumer", "traffic", "inventory", "value", "spending", "merchandise"]),
    ("housing demand", ["housing", "mortgage", "affordability", "homebuilder", "home sales", "repair and remodel"]),
    ("travel demand", ["travel", "booking", "airline", "hotel", "mobility", "rides"]),
    ("commodity prices", ["oil", "copper", "gold", "gas", "commodity", "metals", "mining"]),
    ("supply tightness", ["shortage", "tight supply", "supply tightness", "supply squeeze", "inventory draw", "inventories", "physical supply", "mine disruption", "smelter", "shortfall", "bottleneck"]),
    ("electrification buildout", ["electrification", "grid", "transmission", "battery", "power demand", "load growth"]),
    ("drug pipeline", ["drug", "trial", "approval", "therapy", "obesity", "pipeline", "clinical"]),
    ("regulation", ["regulation", "antitrust", "tariff", "policy", "approval", "scrutiny"]),
]

REGIME_TEXT: dict[str, str] = {
    "Trend continuation": "Price action still looks like a trend-continuation story rather than a broken setup.",
    "Compression near breakout": "Price action looks coiled, with the market waiting for a catalyst to confirm the story.",
    "Range-bound": "Price action looks indecisive, so the narrative is present but not yet cleanly confirmed.",
    "Mean reversion watch": "Price action suggests the market is testing whether the story has gone too far too fast.",
    "Breakdown risk": "Price action suggests the narrative is under pressure and needs a fresh catalyst.",
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _coerce_items(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        return list(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            items = tolist()
        except Exception:
            items = value
        if isinstance(items, list):
            return items
        if isinstance(items, tuple):
            return list(items)
        return [items]
    return [value]


def _symbol_tokens(value: object) -> list[str]:
    tokens: list[str] = []
    for item in _coerce_items(value):
        for token in str(item).replace("|", ",").split(","):
            cleaned = token.upper().strip()
            if cleaned and cleaned.lower() != "nan":
                tokens.append(cleaned)
    return tokens


def _candidate_news_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "data" / "common" / "news",
        here.parents[2] / "data" / "common" / "news",
    ]

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _news_files() -> list[Path]:
    files: list[Path] = []
    for root in _candidate_news_dirs():
        if not root.exists():
            continue
        files.extend(sorted(root.glob("*.pkl")))
    return sorted(dict.fromkeys(files))


@lru_cache(maxsize=64)
def _load_news_blob(path_str: str):
    return pd.read_pickle(path_str)


def _as_article_rows(items: list[dict], ticker: str) -> list[dict[str, object]]:
    target = _normalized(ticker)
    rows: list[dict[str, object]] = []
    for item in items:
        symbols = [symbol for value in [item.get("symbol"), item.get("symbols")] for symbol in _symbol_tokens(value) if _normalized(symbol) == target]
        if not symbols:
            continue
        rows.append(
            {
                "headline": item.get("title") or item.get("headline"),
                "summary": item.get("summary"),
                "description": item.get("description"),
                "published_at": pd.to_datetime(item.get("publishedAt") or item.get("published_at"), utc=True, errors="coerce"),
                "source": item.get("source"),
                "url": item.get("url"),
                "sentiment": item.get("sentimentRating") or item.get("sentiment"),
                "symbols": symbols,
            }
        )
    return rows


def _load_cached_news_context(ticker: str, limit: int = 8) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    fallback_summary: str | None = None
    target = str(ticker or "").upper().strip()
    for path in reversed(_news_files()):
        blob = _load_news_blob(str(path))
        if isinstance(blob, dict):
            if fallback_summary is None:
                candidate = blob.get("perplexity_summaries", {}).get(target)
                if candidate:
                    fallback_summary = str(candidate).strip()

            for section in blob.values():
                if not isinstance(section, dict):
                    continue
                news_by_ticker = section.get("news")
                if not isinstance(news_by_ticker, dict):
                    continue
                payload = news_by_ticker.get(target)
                if payload is None:
                    for value in news_by_ticker.values():
                        if not isinstance(value, dict):
                            continue
                        items = ((value.get("data") or {}).get("target")) or []
                        matched = _as_article_rows(items, target)
                        if matched:
                            rows.extend(matched)
                    continue
                items = ((payload.get("data") or {}).get("target")) or []
                rows.extend(_as_article_rows(items, target))

        if len(rows) >= limit and fallback_summary:
            break

    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"articles": frame, "fallback_summary": fallback_summary, "source": None}

    frame = frame.dropna(subset=["headline"]).copy()
    if "published_at" in frame.columns:
        frame = frame.sort_values("published_at", ascending=False, na_position="last")
    frame = frame.drop_duplicates(subset=["headline", "published_at"], keep="first").head(limit).reset_index(drop=True)
    return {"articles": frame, "fallback_summary": fallback_summary, "source": "cache"}


def load_asset_metadata(api: AlpacaAPI, ticker: str) -> dict[str, object]:
    try:
        return api.get_asset(ticker)
    except AlpacaAPIError:
        return {}


def load_recent_news(api: AlpacaAPI, ticker: str, days: int = 14, limit: int = 8) -> dict[str, object]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        articles = api.get_news([ticker], start=start, end=end, limit=limit)
    except AlpacaAPIError:
        articles = pd.DataFrame()

    if not articles.empty:
        if "published_at" in articles.columns:
            articles = articles.sort_values("published_at", ascending=False, na_position="last")
        return {
            "articles": articles.head(limit).reset_index(drop=True),
            "fallback_summary": None,
            "source": "alpaca",
        }
    return _load_cached_news_context(ticker, limit=limit)


def _fmt_money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    value = float(value)
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    if magnitude >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _latest_metric(frame: pd.DataFrame, metric: str) -> tuple[float | None, pd.Timestamp | None]:
    if frame.empty:
        return None, None
    rows = frame[frame["metric"] == metric].sort_values("report_date")
    if rows.empty:
        return None, None
    row = rows.iloc[-1]
    value = pd.to_numeric(row.get("value"), errors="coerce")
    report_date = pd.to_datetime(row.get("report_date"), errors="coerce")
    return (None if pd.isna(value) else float(value), report_date)


def _matched_business_lenses(symbol: str) -> list[str]:
    target = str(symbol or "").upper().strip()
    if not target:
        return []
    matches: list[str] = []
    for lens, symbols in BUSINESS_FOCUS_UNIVERSES.items():
        if lens == "All Market":
            continue
        if target in {str(item).upper().strip() for item in symbols}:
            matches.append(lens)
    return matches


def _join_phrases(values: list[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _extract_news_themes(payload: dict[str, object] | None) -> list[str]:
    payload = payload or {}
    articles = payload.get("articles")
    texts: list[str] = []
    if isinstance(articles, pd.DataFrame) and not articles.empty:
        for _, row in articles.head(6).iterrows():
            texts.append(
                " ".join(
                    str(row.get(field) or "")
                    for field in ["headline", "summary", "description"]
                )
            )
    fallback_summary = str(payload.get("fallback_summary") or "").strip()
    if fallback_summary:
        texts.append(fallback_summary)
    blob = f" {' '.join(texts).lower()} "
    if not blob.strip():
        return []

    scored: list[tuple[int, str]] = []
    for theme, keywords in NEWS_THEME_KEYWORDS:
        score = sum(blob.count(keyword.lower()) for keyword in keywords)
        if score > 0:
            scored.append((score, theme))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [theme for _, theme in scored[:3]]


def _top_news_sources(payload: dict[str, object] | None, limit: int = 3) -> list[str]:
    payload = payload or {}
    articles = payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty or "source" not in articles.columns:
        return []

    counts: dict[str, int] = {}
    first_seen: list[str] = []
    for value in articles["source"].dropna().astype(str).tolist():
        source = value.strip()
        if not source:
            continue
        if source not in counts:
            counts[source] = 0
            first_seen.append(source)
        counts[source] += 1

    ranked = sorted(first_seen, key=lambda item: (-counts[item], first_seen.index(item)))
    return ranked[: max(int(limit), 1)]


def _headline_links(payload: dict[str, object] | None, limit: int = 2) -> list[dict[str, object]]:
    payload = payload or {}
    articles = payload.get("articles")
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return []

    rows: list[dict[str, object]] = []
    for _, row in articles.head(max(int(limit), 1)).iterrows():
        rows.append(
            {
                "headline": str(row.get("headline") or "Untitled").strip(),
                "source": str(row.get("source") or "").strip(),
                "published_at": pd.to_datetime(row.get("published_at"), utc=True, errors="coerce"),
                "url": str(row.get("url") or "").strip(),
            }
        )
    return rows


def _compose_attention_news_story(
    symbol: str,
    themes: list[str],
    *,
    peer_group_name: str | None = None,
    source_labels: list[str] | None = None,
) -> str:
    normalized_symbol = str(symbol or "").upper().strip()
    source_text = _join_phrases(source_labels or [])
    source_prefix = f"Coverage from {source_text}" if source_text else "Recent coverage"
    normalized_peer_group = str(peer_group_name or "").strip()
    theme_set = set(themes)

    commodity_profile = commodity_proxy_profile(normalized_symbol)
    commodity_name = str(commodity_profile.get("commodity") or "").strip()
    is_commodity_proxy = commodity_name not in {"", "Commodity proxy"}

    if commodity_name == "Copper":
        if theme_set & {"AI rollout", "data center buildout", "cloud and enterprise spend"}:
            lead = f"{source_prefix} is tying the move to AI and data-center spending feeding into copper demand."
            if "supply tightness" in theme_set:
                return lead + " The same coverage also points to tighter supply, which can amplify the squeeze."
            return lead
        if "supply tightness" in theme_set:
            return f"{source_prefix} is leaning into a tighter physical copper market, with supply and inventory pressure driving the story."

    if is_commodity_proxy and normalized_peer_group:
        lowered_group = normalized_peer_group.lower()
        if "supply tightness" in theme_set:
            return f"{source_prefix} is framing this as a {lowered_group} supply story, with tighter availability pushing the tape away from expectation."
        if themes:
            theme_text = _join_phrases([theme.lower() for theme in themes[:2]])
            return f"{source_prefix} is clustering around {theme_text}, which fits the current {lowered_group} move."

    matched_lenses = [normalized_peer_group] if normalized_peer_group and normalized_peer_group not in {"All Market", "Broad Commodity Market"} else _matched_business_lenses(normalized_symbol)
    if matched_lenses and themes:
        theme_text = _join_phrases([theme.lower() for theme in themes[:2]])
        return f"{source_prefix} is clustering around {theme_text}, which lines up with the {matched_lenses[0].lower()} narrative."
    if themes:
        theme_text = _join_phrases([theme.lower() for theme in themes[:2]])
        return f"{source_prefix} is clustering around {theme_text}, which helps explain why the move looks idiosyncratic."
    if source_text:
        return f"{source_prefix} is providing the clearest narrative context behind the current move."
    return ""


def build_attention_news_narrative(
    ticker: str,
    payload: dict[str, object] | None,
    *,
    peer_group_name: str | None = None,
) -> dict[str, object]:
    payload = payload or {}
    summary = summarize_recent_news(ticker, payload)
    themes = _extract_news_themes(payload)
    source_labels = _top_news_sources(payload, limit=3)
    narrative_text = _compose_attention_news_story(
        ticker,
        themes,
        peer_group_name=peer_group_name,
        source_labels=source_labels,
    )

    if not narrative_text and summary.get("summary_lines"):
        narrative_text = str(summary["summary_lines"][0]).strip()

    source_line = ""
    if source_labels:
        source_line = f"Sources: {_join_phrases(source_labels)}"

    return {
        "narrative_text": narrative_text,
        "source_line": source_line,
        "source_labels": source_labels,
        "themes": themes,
        "headline_links": _headline_links(payload, limit=2),
        "articles": summary.get("articles", pd.DataFrame()),
    }


def build_company_description(
    ticker: str,
    asset: dict[str, object] | None,
    fundamentals: dict[str, pd.DataFrame] | None,
    signal_summary: dict[str, object] | None = None,
    *,
    news_payload: dict[str, object] | None = None,
    active_lens: str | None = None,
) -> str:
    symbol = str(ticker or "").upper().strip()
    asset = asset or {}
    signal_summary = signal_summary or {}

    name = str(asset.get("name") or symbol).strip()
    matched_lenses = _matched_business_lenses(symbol)
    if active_lens and active_lens not in {"", "All Market"}:
        ordered_lenses = [active_lens] + [lens for lens in matched_lenses if lens != active_lens]
    else:
        ordered_lenses = matched_lenses

    role_hint = COMPANY_ROLE_HINTS.get(symbol)
    if not role_hint and ordered_lenses:
        role_hint = BUSINESS_ROLE_HINTS.get(ordered_lenses[0])
    if not role_hint:
        role_hint = "is being tracked here as an individual company narrative inside the market dashboard"

    details: list[str] = [f"{name} ({symbol}) {role_hint}."]

    if ordered_lenses:
        lens_text = _join_phrases(ordered_lenses[:2])
        narrative_inputs = [
            BUSINESS_NARRATIVE_HINTS.get(lens, "")
            for lens in ordered_lenses[:2]
        ]
        details.append(
            f"In this dashboard it maps most naturally to the {lens_text} narrative, where investors usually watch "
            f"{_join_phrases([value for value in narrative_inputs if value])}."
        )

    theme_hits = _extract_news_themes(news_payload)
    if theme_hits:
        details.append(f"Recent coverage is reinforcing a story around {_join_phrases(theme_hits)}.")

    regime = str(signal_summary.get("regime") or "").strip()
    if regime:
        details.append(REGIME_TEXT.get(regime, f"From a price-action standpoint, the tape still fits a {regime.lower()} story."))

    if len(details) == 1:
        details.append("The current narrative is still thin, so the best read comes from the linked price action and recent headlines.")

    return " ".join(details)


def _clean_line(text: str, limit: int = 220) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def summarize_recent_news(ticker: str, payload: dict[str, object]) -> dict[str, object]:
    articles = payload.get("articles")
    fallback_summary = str(payload.get("fallback_summary") or "").strip()
    source = payload.get("source")

    if not isinstance(articles, pd.DataFrame):
        articles = pd.DataFrame()

    if articles.empty and not fallback_summary:
        return {"summary_lines": [], "articles": articles, "source": source}

    summary_lines: list[str] = []
    if not articles.empty:
        published = articles["published_at"] if "published_at" in articles.columns else pd.Series(dtype="datetime64[ns, UTC]")
        published = pd.to_datetime(published, utc=True, errors="coerce").dropna()
        span_days = 0
        if not published.empty:
            span_days = max(int((published.max() - published.min()).days), 0)
        sentiment = "mixed"
        if "sentiment" in articles.columns:
            modes = articles["sentiment"].dropna().astype(str)
            if not modes.empty:
                sentiment = modes.mode().iloc[0].lower()

        summary_lines.append(
            f"{len(articles)} recent article(s) over roughly the last {span_days + 1} day(s); tone is {sentiment}."
        )

        for _, row in articles.head(3).iterrows():
            published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
            prefix = published_at.strftime("%b %d") + ": " if pd.notna(published_at) else ""
            snippet = row.get("summary") or row.get("description") or row.get("headline")
            summary_lines.append(prefix + _clean_line(str(snippet or row.get("headline") or "")))
    elif fallback_summary:
        for line in fallback_summary.splitlines():
            cleaned = _clean_line(line.strip())
            if cleaned:
                summary_lines.append(cleaned)
            if len(summary_lines) >= 5:
                break

    return {
        "summary_lines": summary_lines[:5],
        "articles": articles.head(6).copy(),
        "source": source,
    }
