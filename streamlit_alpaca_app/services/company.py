from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re

import pandas as pd

from .alpaca_api import AlpacaAPI, AlpacaAPIError


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


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
        raw_symbols = item.get("symbol") or item.get("symbols") or ""
        symbols = [
            symbol.upper()
            for symbol in str(raw_symbols).replace("|", ",").split(",")
            if symbol and _normalized(symbol) == target
        ]
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


def build_company_description(
    ticker: str,
    asset: dict[str, object] | None,
    fundamentals: dict[str, pd.DataFrame] | None,
    signal_summary: dict[str, object] | None = None,
) -> str:
    symbol = str(ticker or "").upper().strip()
    asset = asset or {}
    fundamentals = fundamentals or {}
    signal_summary = signal_summary or {}

    name = str(asset.get("name") or symbol).strip()
    exchange = str(asset.get("exchange") or "").strip()
    status = str(asset.get("status") or "").strip().lower()
    asset_class = str(asset.get("class") or "equity").replace("_", " ").strip()
    if asset_class.lower() == "us equity":
        asset_class = "US equity"

    first_sentence = f"{name} ({symbol})"
    descriptors = [part for part in [status, f"{exchange}-listed" if exchange else "", asset_class] if part]
    if descriptors:
        first_sentence += " is an " + " ".join(descriptors) + "."
    else:
        first_sentence += " is an actively tracked equity."

    income = fundamentals.get("income", pd.DataFrame())
    cashflow = fundamentals.get("cashflow", pd.DataFrame())
    revenue, report_date = _latest_metric(income, "Total Revenue")
    net_income, _ = _latest_metric(income, "Net Income")
    free_cash_flow, _ = _latest_metric(cashflow, "Free Cash Flow")

    details: list[str] = [first_sentence]
    if revenue is not None or net_income is not None or free_cash_flow is not None:
        report_label = report_date.strftime("%Y-%m-%d") if report_date is not None and not pd.isna(report_date) else "latest quarter"
        details.append(
            f"Latest quarterly results ({report_label}) show revenue of {_fmt_money(revenue)}, "
            f"net income of {_fmt_money(net_income)}, and free cash flow of {_fmt_money(free_cash_flow)}."
        )

    regime = str(signal_summary.get("regime") or "").strip()
    pullback = pd.to_numeric(signal_summary.get("pullback_from_ath_pct"), errors="coerce")
    room_to_resistance = pd.to_numeric(signal_summary.get("dist_to_resistance_pct"), errors="coerce")
    if regime:
        if pd.notna(pullback) and pd.notna(room_to_resistance):
            details.append(
                f"Current setup is {regime.lower()}, trading {float(pullback):.1f}% below its all-time high "
                f"with {float(room_to_resistance):.1f}% room to channel resistance."
            )
        else:
            details.append(f"Current setup is {regime.lower()}.")

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
