from __future__ import annotations

import base64
import json
from typing import Any

import pandas as pd

from compute.fundamentals import load_quarterly_fundamentals, share_count_asof
from services.company import build_company_description, summarize_recent_news
from services.entity_taxonomy import dashboard_business_lens_from_taxonomy_row, taxonomy_lookup_by_symbol
from services.market import commodity_proxy_profile


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    return _coerce_text(value).upper()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _symbol_tokens(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        items = list(value)
    else:
        tolist = getattr(value, "tolist", None)
        if callable(tolist):
            try:
                listed = tolist()
            except Exception:
                listed = value
            if isinstance(listed, list):
                items = listed
            elif isinstance(listed, tuple):
                items = list(listed)
            else:
                items = [listed]
        else:
            items = [value]
    tokens: list[str] = []
    for item in items:
        for token in str(item).replace("|", ",").split(","):
            cleaned = _normalize_symbol(token)
            if cleaned:
                tokens.append(cleaned)
    return tokens


def _latest_close_from_price_history(frame: pd.DataFrame) -> float | None:
    if frame.empty or "close" not in frame.columns:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return None
    return float(close.iloc[-1])


def _format_market_cap_label(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    amount = float(value)
    magnitude = abs(amount)
    if magnitude >= 1_000_000_000_000:
        return f"${amount / 1_000_000_000_000:.2f}T"
    if magnitude >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"${amount / 1_000_000:.0f}M"
    return f"${amount:,.0f}"


def _sparkline_svg(frame: pd.DataFrame, *, width: int = 164, height: int = 56) -> str:
    if frame.empty or "close" not in frame.columns:
        return ""
    series = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(30)
    if len(series) < 2:
        return ""

    values = series.astype(float).tolist()
    minimum = min(values)
    maximum = max(values)
    spread = maximum - minimum
    if spread == 0:
        spread = max(abs(maximum), 1.0) * 0.01 or 1.0
        minimum -= spread / 2.0
        maximum += spread / 2.0
        spread = maximum - minimum

    x_step = width / max(len(values) - 1, 1)
    points: list[str] = []
    for idx, value in enumerate(values):
        x_pos = round(idx * x_step, 2)
        y_pos = round(height - (((value - minimum) / spread) * height), 2)
        points.append(f"{x_pos},{y_pos}")

    stroke = "#16a34a" if values[-1] >= values[0] else "#dc2626"
    baseline = round(height - (((values[0] - minimum) / spread) * height), 2)
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"preserveAspectRatio='none' aria-hidden='true'>"
        f"<polyline fill='none' stroke='rgba(148,163,184,0.22)' stroke-width='1' points='0,{baseline} {width},{baseline}' />"
        f"<polyline fill='none' stroke='{stroke}' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round' "
        f"points='{' '.join(points)}' />"
        f"</svg>"
    )


def _sparkline_data_uri(frame: pd.DataFrame, *, width: int = 164, height: int = 56) -> str:
    svg = _sparkline_svg(frame, width=width, height=height)
    if not svg:
        return ""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _name_map(universe_snapshot_frame: pd.DataFrame | None) -> dict[str, str]:
    if not isinstance(universe_snapshot_frame, pd.DataFrame) or universe_snapshot_frame.empty:
        return {}
    if "symbol" not in universe_snapshot_frame.columns or "security_name" not in universe_snapshot_frame.columns:
        return {}
    table = universe_snapshot_frame[["symbol", "security_name"]].copy()
    table["symbol"] = table["symbol"].astype(str).str.upper().str.strip()
    table["security_name"] = table["security_name"].astype(str).str.strip()
    table = table[table["symbol"].ne("") & table["security_name"].ne("")]
    return dict(table.drop_duplicates(subset=["symbol"], keep="first").itertuples(index=False, name=None))


def _company_name(symbol: str, universe_snapshot_frame: pd.DataFrame | None) -> str:
    normalized = _normalize_symbol(symbol)
    universe_names = _name_map(universe_snapshot_frame)
    if normalized in universe_names:
        return universe_names[normalized]
    commodity_profile = commodity_proxy_profile(normalized)
    if commodity_profile:
        return _coerce_text(commodity_profile.get("name")) or normalized
    return normalized


def _price_window(
    price_history_frame: pd.DataFrame | None,
    symbol: str,
    *,
    asof_time_utc: object | None,
    lookback_days: int,
) -> pd.DataFrame:
    if not isinstance(price_history_frame, pd.DataFrame) or price_history_frame.empty:
        return pd.DataFrame()
    if "symbol" not in price_history_frame.columns or "timestamp" not in price_history_frame.columns:
        return pd.DataFrame()
    target = _normalize_symbol(symbol)
    rows = price_history_frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    rows = rows[rows["symbol"] == target].copy()
    if rows.empty:
        return rows
    rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True, errors="coerce")
    rows["close"] = pd.to_numeric(rows["close"], errors="coerce")
    rows = rows.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.notna(asof_ts):
        cutoff = asof_ts - pd.Timedelta(days=max(int(lookback_days), 1))
        rows = rows[(rows["timestamp"] >= cutoff) & (rows["timestamp"] <= asof_ts + pd.Timedelta(days=1))]
    return rows.reset_index(drop=True)


def _fundamentals_asof(
    fundamentals: dict[str, pd.DataFrame],
    *,
    asof_time_utc: object | None,
) -> dict[str, pd.DataFrame]:
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.isna(asof_ts):
        return dict(fundamentals or {})

    cutoff = asof_ts.tz_localize(None)
    out: dict[str, pd.DataFrame] = {}
    for key in ["income", "balance", "cashflow"]:
        frame = (fundamentals or {}).get(key, pd.DataFrame())
        if not isinstance(frame, pd.DataFrame) or frame.empty or "report_date" not in frame.columns:
            out[key] = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
            continue
        scoped = frame.copy()
        scoped["report_date"] = pd.to_datetime(scoped["report_date"], errors="coerce")
        out[key] = scoped[scoped["report_date"].notna() & (scoped["report_date"] <= cutoff)].reset_index(drop=True)
    return out


def _news_payload_from_frame(news_frame: pd.DataFrame | None, symbol: str, *, limit: int = 6) -> dict[str, object]:
    if not isinstance(news_frame, pd.DataFrame) or news_frame.empty:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": "pipeline"}
    target = _normalize_symbol(symbol)
    rows = news_frame.copy()
    if "symbols" in rows.columns:
        rows = rows[rows["symbols"].apply(lambda value: target in set(_symbol_tokens(value)))].copy()
    elif "symbol" in rows.columns:
        rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
        rows = rows[rows["symbol"] == target].copy()
    else:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": "pipeline"}
    if rows.empty:
        return {"articles": pd.DataFrame(), "fallback_summary": None, "source": "pipeline"}
    if "published_at" in rows.columns:
        rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
        rows = rows.sort_values("published_at", ascending=False, na_position="last")
    subset_cols = [col for col in ["headline", "published_at", "url"] if col in rows.columns]
    if subset_cols:
        rows = rows.drop_duplicates(subset=subset_cols, keep="first")
    keep = [col for col in ["headline", "summary", "description", "published_at", "source", "url", "sentiment", "symbols"] if col in rows.columns]
    return {"articles": rows[keep].head(limit).reset_index(drop=True), "fallback_summary": None, "source": "pipeline"}


def _attention_context_payload(attention_context_frame: pd.DataFrame | None, symbol: str) -> dict[str, object]:
    if not isinstance(attention_context_frame, pd.DataFrame) or attention_context_frame.empty or "symbol" not in attention_context_frame.columns:
        return {}
    rows = attention_context_frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    match = rows[rows["symbol"] == _normalize_symbol(symbol)].head(1)
    if match.empty:
        return {}
    payload = match.iloc[0].to_dict()
    links_raw = payload.get("top_filing_links_json")
    if isinstance(links_raw, str) and links_raw.strip():
        try:
            parsed = json.loads(links_raw)
            if isinstance(parsed, list):
                payload["top_filing_links"] = [item for item in parsed if isinstance(item, dict)]
        except Exception:
            payload["top_filing_links"] = []
    return payload


def collect_attention_ticker_symbols(
    home_payload: dict[str, Any] | None,
    bundle_map: dict[str, dict[str, Any]] | None = None,
    *,
    max_symbols: int = 120,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        normalized = _normalize_symbol(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    payload = dict(home_payload or {})
    bundles = dict(bundle_map or {})

    for event in list(payload.get("top_events") or []):
        add((event or {}).get("anchor_symbol"))
        for symbol in list((event or {}).get("supporting_symbols") or []):
            add(symbol)
        bundle = bundles.get(_coerce_text((event or {}).get("bundle_id")))
        for item in list((bundle or {}).get("related_symbols") or []) + list((bundle or {}).get("peer_moves") or []):
            add((item or {}).get("symbol"))

    for mover in list(payload.get("must_read_movers") or []) + list(payload.get("unresolved_large_moves") or []):
        add((mover or {}).get("symbol"))
        bundle = bundles.get(_coerce_text((mover or {}).get("bundle_id")))
        for item in list((bundle or {}).get("related_symbols") or []) + list((bundle or {}).get("peer_moves") or []):
            add((item or {}).get("symbol"))

    return ordered[: max(int(max_symbols), 1)]


def build_attention_ticker_snapshot_frame(
    symbols: list[str],
    *,
    price_history_frame: pd.DataFrame | None,
    universe_snapshot_frame: pd.DataFrame | None,
    asof_time_utc: object | None,
    run_id: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asof_label = _coerce_text(pd.to_datetime(asof_time_utc, utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(asof_time_utc, utc=True, errors="coerce")) else asof_time_utc)
    for symbol in dict.fromkeys(_normalize_symbol(item) for item in symbols if _normalize_symbol(item)):
        price_history = _price_window(price_history_frame, symbol, asof_time_utc=asof_time_utc, lookback_days=60)
        latest_close = _latest_close_from_price_history(price_history)
        share_count, share_report_date, share_metric = share_count_asof(symbol, asof_time_utc=asof_time_utc)
        market_cap = (latest_close * share_count) if latest_close is not None and share_count else None
        rows.append(
            {
                "symbol": symbol,
                "company_name": _company_name(symbol, universe_snapshot_frame),
                "latest_close": latest_close,
                "market_cap": market_cap,
                "market_cap_label": _format_market_cap_label(market_cap),
                "share_count": share_count,
                "share_count_metric": _coerce_text(share_metric),
                "share_count_report_date": _coerce_text(pd.to_datetime(share_report_date, errors="coerce").date().isoformat() if pd.notna(pd.to_datetime(share_report_date, errors="coerce")) else ""),
                "sparkline_data_uri": _sparkline_data_uri(price_history),
                "run_id": _coerce_text(run_id),
                "asof_time_utc": asof_label,
                "prompt_version": "",
                "model_name": "deterministic",
                "source_trace_json": _json_dumps(
                    {
                        "datasets": ["price_history", "universe_snapshot"],
                        "share_count_source": "local_quarterly_fundamentals",
                        "price_points": int(len(price_history)),
                    }
                ),
            }
        )
    return pd.DataFrame(rows)


def build_attention_ticker_background_snapshot_frame(
    symbols: list[str],
    *,
    price_history_frame: pd.DataFrame | None,
    universe_snapshot_frame: pd.DataFrame | None,
    news_frame: pd.DataFrame | None,
    attention_context_frame: pd.DataFrame | None,
    asof_time_utc: object | None,
    run_id: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    asof_label = _coerce_text(asof_ts.isoformat() if pd.notna(asof_ts) else asof_time_utc)
    ordered_symbols = [symbol for symbol in dict.fromkeys(_normalize_symbol(item) for item in symbols if _normalize_symbol(item))]
    taxonomy_lookup = taxonomy_lookup_by_symbol(ordered_symbols)

    for symbol in ordered_symbols:
        company_name = _company_name(symbol, universe_snapshot_frame)
        price_history = _price_window(price_history_frame, symbol, asof_time_utc=asof_time_utc, lookback_days=180)
        latest_close = _latest_close_from_price_history(price_history)
        share_count, share_report_date, share_metric = share_count_asof(symbol, asof_time_utc=asof_time_utc)
        market_cap = (latest_close * share_count) if latest_close is not None and share_count else None
        news_payload = _news_payload_from_frame(news_frame, symbol, limit=6)
        attention_context = _attention_context_payload(attention_context_frame, symbol)
        fundamentals = _fundamentals_asof(
            load_quarterly_fundamentals(symbol),
            asof_time_utc=asof_time_utc,
        )
        business_lens = dashboard_business_lens_from_taxonomy_row(taxonomy_lookup.get(symbol))
        description_text = build_company_description(
            symbol,
            {"name": company_name},
            fundamentals,
            {},
            news_payload=news_payload,
            active_lens=business_lens,
        )
        news_summary = summarize_recent_news(symbol, news_payload)
        articles = news_summary.get("articles", pd.DataFrame())
        price_points = [
            {
                "timestamp": pd.to_datetime(row.get("timestamp"), utc=True, errors="coerce").isoformat(),
                "close": float(pd.to_numeric(row.get("close"), errors="coerce")),
            }
            for _, row in price_history.tail(180).iterrows()
            if pd.notna(pd.to_datetime(row.get("timestamp"), utc=True, errors="coerce"))
            and pd.notna(pd.to_numeric(row.get("close"), errors="coerce"))
        ]
        recent_headlines = []
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            for _, row in articles.head(6).iterrows():
                published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
                recent_headlines.append(
                    {
                        "headline": _coerce_text(row.get("headline")),
                        "source": _coerce_text(row.get("source")),
                        "published_at": _coerce_text(published_at.isoformat() if pd.notna(published_at) else ""),
                        "url": _coerce_text(row.get("url")),
                    }
                )
        rows.append(
            {
                "symbol": symbol,
                "company_name": company_name,
                "business_lens": business_lens,
                "description_text": _coerce_text(description_text),
                "news_summary_lines_json": _json_dumps(list(news_summary.get("summary_lines") or [])),
                "recent_headlines_json": _json_dumps(recent_headlines),
                "llm_source_line": _coerce_text(attention_context.get("llm_source_line")),
                "llm_headline": _coerce_text(attention_context.get("llm_headline")),
                "llm_summary_text": _coerce_text(attention_context.get("llm_summary_text")),
                "context_story_text": _coerce_text(attention_context.get("context_story_text")),
                "price_points_json": _json_dumps(price_points),
                "market_cap": market_cap,
                "market_cap_label": _format_market_cap_label(market_cap),
                "share_count": share_count,
                "share_count_metric": _coerce_text(share_metric),
                "share_count_report_date": _coerce_text(pd.to_datetime(share_report_date, errors="coerce").date().isoformat() if pd.notna(pd.to_datetime(share_report_date, errors="coerce")) else ""),
                "run_id": _coerce_text(run_id),
                "asof_time_utc": asof_label,
                "prompt_version": "",
                "model_name": "deterministic",
                "source_trace_json": _json_dumps(
                    {
                        "datasets": ["price_history", "news_articles", "attention_context_bundle", "universe_snapshot"],
                        "share_count_source": "local_quarterly_fundamentals",
                        "price_points": int(len(price_points)),
                        "headline_count": int(len(recent_headlines)),
                    }
                ),
            }
        )
    return pd.DataFrame(rows)


def deserialize_attention_ticker_snapshot_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    target = _normalize_symbol(symbol)
    if not target or "symbol" not in frame.columns:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    match = rows[rows["symbol"] == target].head(1)
    if match.empty:
        return {}
    payload = match.iloc[0].to_dict()
    try:
        payload["source_trace"] = json.loads(_coerce_text(payload.get("source_trace_json")) or "{}")
    except Exception:
        payload["source_trace"] = {}
    return payload


def deserialize_attention_ticker_background_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    target = _normalize_symbol(symbol)
    if not target or "symbol" not in frame.columns:
        return {}
    rows = frame.copy()
    rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
    match = rows[rows["symbol"] == target].head(1)
    if match.empty:
        return {}
    payload = match.iloc[0].to_dict()
    for source_key, target_key, default in [
        ("news_summary_lines_json", "news_summary_lines", []),
        ("recent_headlines_json", "recent_headlines", []),
        ("price_points_json", "price_points", []),
        ("source_trace_json", "source_trace", {}),
    ]:
        try:
            payload[target_key] = json.loads(_coerce_text(payload.get(source_key)) or _json_dumps(default))
        except Exception:
            payload[target_key] = default
    return payload


__all__ = [
    "build_attention_ticker_background_snapshot_frame",
    "build_attention_ticker_snapshot_frame",
    "collect_attention_ticker_symbols",
    "deserialize_attention_ticker_background_frame",
    "deserialize_attention_ticker_snapshot_frame",
]
