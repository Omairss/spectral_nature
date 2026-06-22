from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from presentation import dashboard_loaders
from services import attention_surface as attention_surface_module
from services.config import AppConfig

_DISPLAY_SECTION_LABELS = {
    "affected assets": "Affected Assets",
    "background": "Background",
    "background context": "Background Context",
    "business context": "Business Context",
    "likely driver": "Likely Driver",
    "most likely driver": "Most Likely Driver",
    "what changed": "What Changed",
    "what changed vs expectation": "What Changed Vs Expectation",
    "what else moved": "What Else Moved",
    "what happened": "What Happened",
    "what to watch": "What To Watch",
    "why it happened": "Why It Happened",
    "why today": "Why Today",
}

_DISPLAY_SECTION_PATTERN = re.compile(
    r"(^|\s)#{1,6}\s*("
    + "|".join(re.escape(label) for label in sorted(_DISPLAY_SECTION_LABELS, key=len, reverse=True))
    + r")\b\s*",
    re.IGNORECASE,
)


def _clean_attention_text(text: object) -> str:
    return attention_surface_module.clean_attention_text(text)


def _raw_attention_text(text: object) -> str:
    clean = " ".join(str(text or "").split())
    return "" if clean.lower() == "nan" else clean


def streamlit_safe_markdown_text(text: object) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if not clean or clean.lower() == "nan":
        return ""
    clean = re.sub(r"`([^`]*)`", r"\1", clean)
    clean = re.sub(r"\s+#{1,6}\s+", " ", clean)
    clean = re.sub(r"(?m)^\s*#{1,6}\s+", "", clean)
    clean = re.sub(r"(?<!\\)\$", r"\\$", clean)
    return clean.strip()


def display_markdown_sections(text: object) -> list[tuple[str, str]]:
    raw = " ".join(str(text or "").split()).strip()
    if not raw or raw.lower() == "nan":
        return []

    matches = list(_DISPLAY_SECTION_PATTERN.finditer(raw))
    if not matches:
        body = streamlit_safe_markdown_text(raw)
        return [("", body)] if body else []

    sections: list[tuple[str, str]] = []
    preamble = raw[: matches[0].start()].strip()
    if preamble:
        body = streamlit_safe_markdown_text(preamble)
        if body:
            sections.append(("", body))

    for index, match in enumerate(matches):
        label = _DISPLAY_SECTION_LABELS.get(match.group(2).lower(), match.group(2).strip().title())
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        body = re.sub(r"^\s*[:\-–—]\s*", "", raw[start:end]).strip()
        body = streamlit_safe_markdown_text(body)
        if body:
            sections.append((label, body))
    return sections


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
