from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from presentation import dashboard_loaders
from services import attention_surface as attention_surface_module
from services.company import build_attention_news_narrative, build_company_description
from services.config import AppConfig
from services.entity_taxonomy import dashboard_business_lens_from_taxonomy_row


def _attention_event_key(row: pd.Series) -> str:
    symbol = str(row.get("entity_id") or "").upper().strip()
    horizon = str(row.get("horizon") or "").strip() or "item"
    return str(row.get("_homepage_v2_event_id") or row.get("event_id") or f"{symbol}-{horizon}").strip()


def _clean_attention_text(text: object) -> str:
    return attention_surface_module.clean_attention_text(text)


def _raw_attention_text(text: object) -> str:
    clean = " ".join(str(text or "").split())
    return "" if clean.lower() == "nan" else clean


def _attention_evidence_display_text(item: dict[str, object]) -> str:
    headline = " ".join(str(item.get("headline") or "").split()).lower()
    for candidate in [
        item.get("display_excerpt"),
        item.get("excerpt"),
        item.get("summary"),
    ]:
        text = _clean_attention_text(candidate)
        if text and text.lower() != headline:
            return text
    return ""


def _attention_story_text(row: pd.Series) -> str:
    story = _clean_attention_text(row.get("story_text"))
    if story:
        return story
    why_now = _clean_attention_text(row.get("why_now_text"))
    if why_now:
        return why_now
    entity_id = str(row.get("entity_id") or "").upper().strip()
    return f"{entity_id} is moving away from expectation." if entity_id else ""


def _headline_items_from_news_payload(
    news_payload: dict[str, object] | None,
    *,
    limit: int = 3,
) -> list[dict[str, str]]:
    articles = news_payload.get("articles") if isinstance(news_payload, dict) else pd.DataFrame()
    if not isinstance(articles, pd.DataFrame) or articles.empty:
        return []
    rows: list[dict[str, str]] = []
    for _, item in articles.head(max(int(limit), 1)).iterrows():
        headline = str(item.get("headline") or "").strip()
        if not headline:
            continue
        published_at = pd.to_datetime(item.get("published_at"), utc=True, errors="coerce")
        rows.append(
            {
                "headline": headline,
                "summary": _raw_attention_text(item.get("summary")) or _raw_attention_text(item.get("description")),
                "source": str(item.get("source") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "published_at": published_at.isoformat() if pd.notna(published_at) else "",
            }
        )
    return rows


def _build_attention_brief_input(
    row: pd.Series,
    *,
    news_payload: dict[str, object] | None = None,
    context_payload: dict[str, object] | None = None,
    asset: dict[str, object] | None = None,
) -> dict[str, object]:
    symbol = str(row.get("entity_id") or "").upper().strip()
    peer_group_name = str(row.get("peer_group_name") or "").strip()
    active_lens = str(dashboard_business_lens_from_taxonomy_row(row.to_dict()) or "").strip()
    news_context = build_attention_news_narrative(symbol, news_payload, peer_group_name=peer_group_name)
    company_description = build_company_description(
        symbol,
        asset or {},
        {},
        {"regime": str(row.get("regime_label") or "").strip()},
        news_payload=news_payload,
        active_lens=active_lens,
    )
    linked_news_raw = pd.to_numeric(row.get("linked_news_count"), errors="coerce")
    linked_news_count = int(linked_news_raw) if pd.notna(linked_news_raw) else 0
    context = context_payload or {}
    return {
        "symbol": symbol,
        "company_name": str((asset or {}).get("name") or "").strip(),
        "title": str(row.get("title") or symbol or "Attention item").strip(),
        "subtitle": str(row.get("subtitle") or "").strip(),
        "story_text": _attention_story_text(row),
        "why_now_text": _clean_attention_text(row.get("why_now_text")),
        "peer_group_name": peer_group_name,
        "regime_label": str(row.get("regime_label") or "").strip(),
        "linked_news_count": linked_news_count,
        "news_narrative": str(news_context.get("narrative_text") or "").strip(),
        "headline_items": _headline_items_from_news_payload(news_payload, limit=3),
        "company_description": company_description,
        "context_headline": str(context.get("llm_headline") or "").strip(),
        "context_summary": str(context.get("llm_summary_text") or context.get("context_story_text") or "").strip(),
        "context_narrative": str(context.get("llm_narrative_text") or "").strip(),
        "context_why_now": str(context.get("llm_why_now") or "").strip(),
        "primary_source_excerpt": str(context.get("primary_source_excerpt") or "").strip(),
        "watchpoint_text": str(row.get("next_best_action") or "").strip(),
    }


def _load_attention_brief_payloads(
    cfg: AppConfig,
    rows: pd.DataFrame,
    *,
    news_payloads: dict[str, dict[str, object]],
    context_payloads: dict[str, dict[str, object]],
    force_refresh: bool = False,
    use_llm: bool = True,
) -> dict[str, dict[str, object]]:
    payloads: dict[str, dict[str, object]] = {}
    if rows.empty:
        return payloads
    asset_cache: dict[str, dict[str, object]] = {}
    for _, row in rows.iterrows():
        row_series = row if isinstance(row, pd.Series) else pd.Series(row)
        symbol = str(row_series.get("entity_id") or "").upper().strip()
        if not symbol:
            continue
        if symbol not in asset_cache:
            try:
                asset_cache[symbol] = dashboard_loaders._load_asset_metadata_cached(
                    cfg,
                    symbol,
                    force_refresh=force_refresh,
                )
            except Exception:
                asset_cache[symbol] = {}
        brief_input = _build_attention_brief_input(
            row_series,
            news_payload=news_payloads.get(symbol),
            context_payload=context_payloads.get(symbol),
            asset=asset_cache.get(symbol),
        )
        event_key = _attention_event_key(row_series)
        payloads[event_key] = dashboard_loaders._load_attention_feed_brief_cached(
            json.dumps(_json_ready(brief_input), ensure_ascii=False, sort_keys=True),
            use_llm=use_llm,
        )
    return payloads


def _build_attention_micro_chart(row: pd.Series) -> go.Figure | None:
    expected = pd.to_numeric(row.get("expected_value"), errors="coerce")
    observed = pd.to_numeric(row.get("observed_value"), errors="coerce")
    residual = pd.to_numeric(row.get("residual_value"), errors="coerce")

    if pd.isna(expected) or pd.isna(observed):
        return None

    expected_value = float(expected)
    observed_value = float(observed)
    accent = "#34d399" if observed_value >= expected_value else "#f87171"
    neutral = "#94a3b8"
    max_abs = max(abs(expected_value), abs(observed_value), 0.5)
    padding = max(0.75, max_abs * 0.25)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[expected_value, observed_value],
            y=["Expected", "Observed"],
            orientation="h",
            marker_color=[neutral, accent],
            text=[f"{expected_value:+.1f}%", f"{observed_value:+.1f}%"],
            textposition="auto",
            hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
        )
    )
    fig.add_vline(x=0.0, line_color="rgba(148, 163, 184, 0.45)", line_width=1, line_dash="dot")
    annotations: list[dict[str, object]] = []
    if pd.notna(residual):
        annotations.append(
            {
                "x": 0.99,
                "xref": "paper",
                "y": 1.12,
                "yref": "paper",
                "text": f"Gap {float(residual):+.2f}%",
                "showarrow": False,
                "font": {"size": 11, "color": accent},
                "xanchor": "right",
            }
        )
    fig.update_layout(
        template="plotly_dark",
        height=155,
        margin=dict(l=8, r=8, t=28, b=8),
        showlegend=False,
        bargap=0.35,
        annotations=annotations,
        xaxis=dict(
            title=None,
            ticksuffix="%",
            range=[-(max_abs + padding), max_abs + padding],
            showgrid=True,
            gridcolor="rgba(148, 163, 184, 0.12)",
            zeroline=False,
            fixedrange=True,
        ),
        yaxis=dict(title=None, fixedrange=True, automargin=True),
    )
    return fig


def _load_attention_news_payloads(
    cfg: AppConfig,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, object]]:
    payloads: dict[str, dict[str, object]] = {}
    for symbol in dict.fromkeys(str(value or "").upper().strip() for value in symbols):
        if not symbol:
            continue
        try:
            payloads[symbol] = dashboard_loaders._load_recent_news_cached(
                cfg,
                symbol,
                days=14,
                limit=6,
                force_refresh=force_refresh,
            )
        except Exception:
            payloads[symbol] = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
    return payloads


def _load_attention_context_payloads(
    cfg: AppConfig,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, object]]:
    payloads: dict[str, dict[str, object]] = {}
    for symbol in dict.fromkeys(str(value or "").upper().strip() for value in symbols):
        if not symbol:
            continue
        try:
            payloads[symbol] = dashboard_loaders._load_attention_context_cached(
                cfg,
                symbol,
                force_refresh=force_refresh,
            )
        except Exception:
            payloads[symbol] = {
                "symbol": symbol,
                "context_story_text": "",
                "primary_source_excerpt": "",
                "source_line": "",
                "llm_headline": "",
                "llm_summary_text": "",
                "llm_narrative_text": "",
                "llm_why_now": "",
                "llm_management_signal": "",
                "llm_confidence": "",
                "llm_source_line": "",
                "llm_supporting_points": [],
                "top_filing_links": [],
            }
    return payloads


def _json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _build_homepage_v2_event_record(
    row: pd.Series,
    *,
    news_payload: dict[str, object] | None = None,
    context_payload: dict[str, object] | None = None,
    brief_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    symbol = str(row.get("entity_id") or "").upper().strip()
    news_context = build_attention_news_narrative(
        symbol,
        news_payload,
        peer_group_name=str(row.get("peer_group_name") or "").strip(),
    )
    articles = news_payload.get("articles") if isinstance(news_payload, dict) else pd.DataFrame()
    headline_values: list[str] = []
    if isinstance(articles, pd.DataFrame) and not articles.empty and "headline" in articles.columns:
        headline_values = [
            str(headline).strip()
            for headline in articles["headline"].dropna().astype(str).tolist()
            if str(headline).strip()
        ][:3]
    context = context_payload or {}
    return {
        "event_id": str(_attention_event_key(row)).strip(),
        "symbol": symbol,
        "entity_id": symbol,
        "title": str(row.get("title") or symbol or "Untitled anomaly").strip(),
        "subtitle": str(row.get("subtitle") or "").strip(),
        "source_label": str(row.get("source_label") or "").strip(),
        "horizon": str(row.get("horizon") or "").strip(),
        "anomaly_type": str(row.get("anomaly_type") or "").strip(),
        "attention_score": _json_ready(row.get("attention_score")),
        "story_text": str((brief_payload or {}).get("lead_text") or _attention_story_text(row)).strip(),
        "cluster_text": str((brief_payload or {}).get("cluster_text") or news_context.get("narrative_text") or "").strip(),
        "headline_text": str((brief_payload or {}).get("headline_text") or "").strip(),
        "company_text": str((brief_payload or {}).get("company_text") or "").strip(),
        "explainer_text": str((brief_payload or {}).get("explainer_text") or "").strip(),
        "why_now_text": str(row.get("why_now_text") or "").strip(),
        "expected_vs_observed_text": "",
        "next_best_action": str(row.get("next_best_action") or "").strip(),
        "news_summary_text": str(news_context.get("narrative_text") or "").strip(),
        "news_headlines": headline_values,
        "context_headline": str(context.get("llm_headline") or "").strip(),
        "context_summary_text": str(context.get("llm_summary_text") or context.get("context_story_text") or "").strip(),
        "context_why_now": str(context.get("llm_why_now") or "").strip(),
        "management_signal": str(context.get("llm_management_signal") or "").strip(),
    }


def _homepage_v2_item_summary(
    row: pd.Series,
    *,
    news_payload: dict[str, object] | None = None,
    context_payload: dict[str, object] | None = None,
    brief_payload: dict[str, object] | None = None,
) -> str:
    del news_payload

    pieces: list[str] = []
    llm_summary = str((context_payload or {}).get("llm_summary_text") or "").strip()
    context_story = str((context_payload or {}).get("context_story_text") or "").strip()
    next_action = str((brief_payload or {}).get("watchpoint_text") or row.get("next_best_action") or "").strip()

    for candidate in [
        str((brief_payload or {}).get("lead_text") or "").strip(),
        str((brief_payload or {}).get("headline_text") or "").strip(),
        str((brief_payload or {}).get("company_text") or "").strip(),
        str((brief_payload or {}).get("explainer_text") or "").strip(),
        llm_summary or context_story,
    ]:
        text = str(candidate or "").strip()
        if text and text not in pieces:
            pieces.append(text)
    if next_action:
        pieces.append(f"Next watchpoint: {next_action}.")
    return " ".join(pieces[:3]).strip()
