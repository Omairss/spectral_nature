from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlparse

import pandas as pd

from data_access.contracts import ResolvedPayload
from services.json_utils import frame_to_records, to_jsonable
from data_access.query_service import QueryService


_TICKER_RE = re.compile(r"^[A-Z0-9.-]{1,12}$")


def _clean(value: object, *, limit: int = 4000) -> str:
    text = str(value if value is not None else "").strip()
    return "" if not text or text.lower() == "nan" else text[:limit]


def _safe_url(value: object) -> str:
    text = _clean(value, limit=2000)
    if not text:
        return ""
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _finite_number(value: object) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(number) or number in {float("inf"), float("-inf")}:
        return None
    return int(number) if number.is_integer() else number


def _dict_payload(resolved: ResolvedPayload | None) -> dict[str, Any]:
    return dict(resolved.payload) if resolved is not None and isinstance(resolved.payload, dict) else {}


def _records(value: object) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return frame_to_records(value)
    if isinstance(value, list):
        return [dict(item) for item in to_jsonable(value) if isinstance(item, dict)]
    return []


def build_stock_investigator_view(
    ticker: str,
    *,
    days: int = 365,
    service: QueryService | None = None,
) -> dict[str, Any]:
    """Build the Stock Investigator read model from existing data-access resolvers.

    This is a presentation DTO for the web client. It deliberately excludes raw
    simulation paths and other oversized compute payloads.
    """
    symbol = _clean(ticker, limit=12).upper()
    if not _TICKER_RE.fullmatch(symbol):
        raise ValueError("Ticker is invalid.")
    lookback_days = min(720, max(60, int(days)))
    resolved_service = service or QueryService.from_environment()
    access = resolved_service.data_access

    resolutions: dict[str, ResolvedPayload] = {}
    unavailable: list[str] = []

    def resolve(name: str, loader: Callable[[], ResolvedPayload]) -> None:
        try:
            resolutions[name] = loader()
        except Exception:
            unavailable.append(name)

    resolve("technical", lambda: access.resolve_technical_signal_summary(symbol))
    resolve("forecast", lambda: access.resolve_forecast_next_week(symbol, days=max(lookback_days, 180)))
    resolve("context", lambda: access.resolve_attention_context(symbol))
    resolve("background", lambda: access.resolve_attention_ticker_background(symbol))
    resolve("news", lambda: access.resolve_recent_news(symbol, days=14, limit=8))

    technical_source = _dict_payload(resolutions.get("technical"))
    technical_keys = (
        "close",
        "ath",
        "pullback_from_ath_pct",
        "channel_support",
        "channel_resistance",
        "channel_position",
        "dist_to_support_pct",
        "dist_to_resistance_pct",
        "ret_5_pct",
        "ret_21_pct",
        "ret_63_pct",
        "rsi_14",
        "vol_20_ann_pct",
    )
    technical = {key: _finite_number(technical_source.get(key)) for key in technical_keys}
    technical["regime"] = _clean(technical_source.get("regime"), limit=120)

    forecast_source = _dict_payload(resolutions.get("forecast"))
    forecast_keys = (
        "current_price",
        "support",
        "resistance",
        "up_probability",
        "breakout_probability",
        "support_break_probability",
        "expected_return_pct",
        "median_return_pct",
        "expected_path_return_pct",
        "analog_count",
    )
    forecast = {key: _finite_number(forecast_source.get(key)) for key in forecast_keys}
    percentile_rows = _records(forecast_source.get("percentiles"))
    terminal_band: dict[str, Any] = {}
    if percentile_rows:
        terminal = percentile_rows[-1]
        terminal_band = {
            "date": _clean(terminal.get("timestamp"), limit=80),
            **{key: _finite_number(terminal.get(key)) for key in ("p10", "p25", "p50", "p75", "p90")},
        }
    forecast["terminal_band"] = terminal_band

    context = _dict_payload(resolutions.get("context"))
    background = _dict_payload(resolutions.get("background"))
    company = {
        "name": _clean(context.get("company_name") or background.get("company_name"), limit=200),
        "business_lens": _clean(context.get("business_lens") or background.get("business_lens"), limit=400),
        "headline": _clean(context.get("llm_headline") or background.get("llm_headline"), limit=300),
        "summary": _clean(
            context.get("llm_summary_text")
            or context.get("context_story_text")
            or background.get("llm_summary_text"),
            limit=4000,
        ),
        "why_now": _clean(context.get("llm_why_now"), limit=2400),
        "management_signal": _clean(context.get("llm_management_signal"), limit=2400),
        "background": _clean(
            background.get("company_background_text") or background.get("description_text"),
            limit=4000,
        ),
        "as_of": _clean(background.get("asof_time_utc") or context.get("asof_time_utc"), limit=80),
    }

    news_payload = resolutions.get("news").payload if resolutions.get("news") is not None else None
    news_source = news_payload.get("articles") if isinstance(news_payload, dict) else news_payload
    articles: list[dict[str, str]] = []
    for row in _records(news_source)[:8]:
        headline = _clean(row.get("headline") or row.get("title"), limit=500)
        if not headline:
            continue
        summary = _clean(row.get("summary"), limit=1400)
        articles.append(
            {
                "headline": headline,
                "source": _clean(row.get("source"), limit=160),
                "published_at": _clean(row.get("published_at") or row.get("created_at"), limit=80),
                "summary": "" if summary == headline else summary,
                "url": _safe_url(row.get("url")),
            }
        )

    modes: list[str] = []
    datasets: list[str] = []
    for resolved in resolutions.values():
        mode = _clean(resolved.provenance.mode, limit=60)
        if mode and mode not in modes:
            modes.append(mode)
        for dataset in resolved.provenance.datasets:
            name = _clean(dataset, limit=120)
            if name and name not in datasets:
                datasets.append(name)

    has_content = bool(
        any(value is not None and value != "" for value in technical.values())
        or any(
            value is not None and value != "" and value != {}
            for value in forecast.values()
        )
        or any(company.values())
        or articles
    )
    return {
        "status": "available" if has_content else "unavailable",
        "ticker": symbol,
        "days": lookback_days,
        "company": company,
        "technical": technical,
        "forecast": forecast,
        "articles": articles,
        "unavailable": unavailable,
        "provenance": {"modes": modes, "datasets": datasets},
    }


__all__ = ["build_stock_investigator_view"]
