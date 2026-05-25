from __future__ import annotations

import base64
import hashlib
import html
import logging
import os
import re
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import streamlit as st

from presentation import attention_content, dashboard_loaders
from services import auth_service
from services.alpaca_api import AlpacaAPI
from services.entity_taxonomy import (
    business_focus_label_from_taxonomy_row,
    dashboard_business_lens_from_taxonomy_row,
    taxonomy_lookup_by_symbol,
)
from services.fundamentals import plot_statement
from services.json_utils import to_list
from services.page_agentic_summary import page_summary_context_signature
from services.pipeline_store import pipeline_store_configured
from services.runtime_policy import (
    attention_ui_policy,
    presentation_layer_only_enabled,
    section_data_available,
)

APP_ROOT = Path(__file__).resolve().parents[1]
BRANDING_ROOT = APP_ROOT / "branding" / "Logo Files"
APP_SIDEBAR_LOGO_PATH = BRANDING_ROOT / "png" / "White logo - no background.png"

HOME_EXP_SECTION = "Experiment"
HOME_V2_SECTION = "Home v2"
AGENTIC_OMNIBAR_SECTION = "Zopedia"
STOCK_INVESTIGATOR_SECTION = "Stock Investigator"
PORTFOLIO_SECTION = "Portfolio"
PORTFOLIO_PERFORMANCE_SECTION = "Portfolio Performance"
MARKET_EXPLORER_SECTION = "Market Explorer"
BROAD_ECONOMY_SECTION = "Broad Economy"
TRADING_AGENT_SECTION = "Trading Agent"
ADMIN_SECTION = "Admin"

NAV_SEPARATOR = "---"
BASE_SECTION_OPTIONS = [
    "Home",
    AGENTIC_OMNIBAR_SECTION,
    BROAD_ECONOMY_SECTION,
    MARKET_EXPLORER_SECTION,
    NAV_SEPARATOR,
    PORTFOLIO_SECTION,
    PORTFOLIO_PERFORMANCE_SECTION,
    NAV_SEPARATOR,
    STOCK_INVESTIGATOR_SECTION,
    "Option Strategizer",
]

SOURCE_LABELS = {
    "equities": "Equities",
    "fred": "FRED",
    "commodities": "Commodities",
    "options": "Options",
    "news": "News",
    "attention": "Attention",
    "taxonomy": "Taxonomy",
    "fundamentals": "Fundamentals",
    "derivatives": "Derivatives",
    "trading_agent": "Trading Agent",
}

JOB_LABELS = {
    "equities-intraday-preload": "Equities Core Snapshots",
    "macro-fred-daily": "FRED Macro Snapshots",
    "commodities-regime": "Commodity Regime Snapshots",
    "options-liquid-universe": "Options Snapshot Refresh",
    "news-ingest-and-features": "News Snapshot Refresh",
    "attention-home-build": "Attention Home Build",
    "trading-agent-build": "Trading Agent Build",
    "entity-taxonomy-refresh": "Entity Taxonomy Refresh",
    "fundamentals-quarterly-refresh": "Fundamentals Quarterly Refresh",
}

_ATTENTION_UI_POLICY = attention_ui_policy()
ATTENTION_HORIZON_OPTIONS = list(_ATTENTION_UI_POLICY.horizon_options)
ATTENTION_HORIZON_LABELS = dict(_ATTENTION_UI_POLICY.horizon_labels)
ATTENTION_SENSITIVITY_ORDER = list(_ATTENTION_UI_POLICY.sensitivity_order)

MARKET_MOMENTUM_SCAN_DAYS = 3650
MARKET_MOMENTUM_HORIZON_LABELS = {
    "1d": "1 Day",
    "7d": "7 Day",
    "1m": "1 Month",
    "3m": "3 Month",
    "1yr": "1 Year",
    "5yr": "5 Year",
}
MARKET_MOMENTUM_HORIZON_COLUMNS = {
    "1d": "return_1d_pct",
    "7d": "return_7d_pct",
    "1m": "return_1m_pct",
    "3m": "return_3m_pct",
    "1yr": "return_1y_pct",
    "5yr": "return_5y_pct",
}

APP_BRAND_NAME = "Spectral Nature"
APP_BRAND_KICKER = "Torres Capital"

LOGGER = logging.getLogger("spectral_nature.ui_app")


def _inline_image_markup(image_path: Path, *, alt_text: str, css_class: str) -> str:
    try:
        image_bytes = image_path.read_bytes()
    except OSError:
        return ""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    class_attr = f"class='{html.escape(css_class)}' " if str(css_class).strip() else ""
    return (
        "<img "
        f"{class_attr}"
        f"src='data:image/png;base64,{image_b64}' "
        f"alt='{html.escape(alt_text)}' />"
    )


def _to_float(payload: dict, key: str) -> float:
    try:
        return float(payload.get(key, 0.0))
    except Exception:
        return 0.0


def _log_event(message: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        LOGGER.info("%s | %s", message, details)
    else:
        LOGGER.info("%s", message)


@contextmanager
def _timed(step: str, **fields: object):
    started = time.perf_counter()
    _log_event(f"START {step}", **fields)
    try:
        yield
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _log_event(f"ERROR {step}", elapsed_s=f"{elapsed:.3f}", error=type(exc).__name__)
        raise
    else:
        elapsed = time.perf_counter() - started
        _log_event(f"END {step}", elapsed_s=f"{elapsed:.3f}")


def _render_page_intro(kicker: str, title: str, body: str) -> None:
    kicker_text = str(kicker or "").strip()
    title_text = str(title or "").strip()
    body_text = str(body or "").strip()
    kicker_markup = f"<div class='sn-page-kicker'>{html.escape(kicker_text)}</div>" if kicker_text else ""
    title_markup = f"<div class='sn-page-title'>{html.escape(title_text)}</div>" if title_text else ""
    body_markup = f"<div class='sn-page-text'>{html.escape(body_text)}</div>" if body_text else ""
    st.markdown(
        (
            "<div class='sn-page-intro'>"
            f"{kicker_markup}"
            f"{title_markup}"
            f"{body_markup}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_help_popover(title: str, body: str, label: str = "How to read") -> None:
    with st.popover(label, help=title, use_container_width=True):
        st.markdown(body)


def _normalized_layout_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"desktop", "mobile", "auto"} else ""


def _current_layout_mode() -> str:
    return _normalized_layout_mode(st.session_state.get("_ui_layout_mode")) or "desktop"


def _mobile_layout_active() -> bool:
    return _current_layout_mode() == "mobile"


def _responsive_columns(spec: int | Sequence[float], *, gap: str = "small") -> list[object]:
    if _mobile_layout_active():
        count = int(spec) if isinstance(spec, int) else len(list(spec))
        return [st.container() for _ in range(max(count, 1))]
    return list(st.columns(spec, gap=gap))


def _responsive_two_panel(*, gap: str = "large") -> list[object]:
    return _responsive_columns(2, gap=gap)


def _current_user_context() -> auth_service.UserContext | None:
    return auth_service.UserContext.from_dict(st.session_state.get("_ui_user_context"))


def _current_user_share_fraction() -> float:
    context = _current_user_context()
    if context is None:
        return 1.0
    return max(float(context.share_fraction or 0.0), 0.0)


def _current_user_is_admin() -> bool:
    context = _current_user_context()
    return bool(context.is_admin) if context is not None else False


def _section_options() -> list[str]:
    options = list(BASE_SECTION_OPTIONS)
    if _current_user_is_admin():
        trading_idx = options.index(BROAD_ECONOMY_SECTION)
        options.insert(trading_idx, TRADING_AGENT_SECTION)
        options.extend([
            NAV_SEPARATOR,
            ADMIN_SECTION,
            HOME_EXP_SECTION,
            HOME_V2_SECTION,
        ])
    return options


def _normalize_workspace_section(section_name: object) -> str:
    normalized = str(section_name or "").strip()
    alias_map = {
        "Homepage - v2": HOME_V2_SECTION,
        "Home v3": "Home",
        "Homepage Exp": HOME_EXP_SECTION,
        "Home Experimental": HOME_EXP_SECTION,
        "Daily Market Overview": HOME_EXP_SECTION,
        "Portfolio Overview": PORTFOLIO_SECTION,
        "Performance": PORTFOLIO_PERFORMANCE_SECTION,
        "Market Opportunity": MARKET_EXPLORER_SECTION,
        "FRED Macro": BROAD_ECONOMY_SECTION,
        "Trading Experiment": TRADING_AGENT_SECTION,
        "Access Admin": ADMIN_SECTION,
        "Pipeline Jobs": ADMIN_SECTION,
        "System Health": ADMIN_SECTION,
    }
    if normalized in alias_map:
        return alias_map[normalized]
    return normalized


def _render_section_back_button(key: str) -> None:
    section_opts = _section_options()
    prev = _normalize_workspace_section(st.session_state.get("_prev_workspace_section", ""))
    if not prev or prev not in section_opts:
        return
    if st.button("← Back", key=key, use_container_width=True):
        st.session_state["_pending_workspace_section"] = prev
        st.rerun()


def _prime_widget_choice(
    key: str,
    options: list[str] | tuple[str, ...],
    *,
    fallback: str,
    pending_key: str | None = None,
) -> str:
    available = [str(option) for option in options]
    if not available:
        return ""
    default_value = fallback if fallback in available else available[0]
    queued_key = pending_key or f"_pending_{key}"
    pending_value = str(st.session_state.pop(queued_key, "") or "").strip()
    current_value = str(st.session_state.get(key, "") or "").strip()
    selected_value = pending_value or current_value or default_value
    if selected_value not in available:
        selected_value = default_value
    if current_value != selected_value:
        st.session_state[key] = selected_value
    return selected_value


def _set_workspace_ticker(ticker: str) -> str:
    normalized = str(ticker or "").upper().strip()
    if not normalized:
        return ""
    st.session_state["market_selected_ticker"] = normalized
    st.session_state["market_ticker_detail_widget"] = normalized
    st.session_state["opt_ticker"] = normalized
    st.session_state["stock_investigator_ticker"] = normalized
    st.session_state["stock_investigator_ticker_widget"] = normalized
    return normalized


def _open_workspace_section(section_name: str) -> None:
    target = _normalize_workspace_section(section_name)
    if not target:
        return
    st.session_state["_pending_workspace_section"] = target
    st.rerun()


def _presentation_layer_only() -> bool:
    return presentation_layer_only_enabled(os.getenv("APP_PRESENTATION_LAYER_ONLY"))


def _has_live_api(api: AlpacaAPI | None, message: str, *, allow_pipeline: bool = False) -> bool:
    if section_data_available(
        api_available=api is not None,
        pipeline_available=pipeline_store_configured(),
        presentation_only=_presentation_layer_only(),
        allow_pipeline=allow_pipeline,
    ):
        return True
    st.warning(message)
    return False


def _prepare_scatter_size(df: pd.DataFrame, column: str) -> tuple[pd.DataFrame, str | None]:
    if df.empty or column not in df.columns:
        return df, None
    out = df.copy()
    size_col = f"{column}_plot_size"
    out[size_col] = pd.to_numeric(out[column], errors="coerce").fillna(0).clip(lower=0)
    if not out[size_col].gt(0).any():
        return out, None
    return out, size_col


def _dataframe_selected_rows(event: object) -> list[int]:
    selection = getattr(event, "selection", None)
    if selection is not None:
        rows = getattr(selection, "rows", None)
        if rows is not None:
            return list(rows)
        if isinstance(selection, dict):
            return list(selection.get("rows", []))
    if isinstance(event, dict):
        return list(event.get("selection", {}).get("rows", []))
    return []


def _render_selectable_ticker_table(
    title: str,
    df: pd.DataFrame,
    columns: list[str],
    key: str,
    column_config: dict[str, object] | None = None,
) -> str | None:
    st.subheader(title)
    if df.empty:
        st.info("No rows available.")
        return None

    table = df[columns].copy()
    st.caption("Click a row to open the ticker details below.")
    event = st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        row_height=48 if "sparkline_3m" in table.columns else None,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = _dataframe_selected_rows(event)
    if not rows:
        return None
    row_idx = rows[0]
    if row_idx >= len(table):
        return None
    return str(table.iloc[row_idx]["symbol"])


# ---------------------------------------------------------------------------
# Group A — Request / session plumbing
# ---------------------------------------------------------------------------


def _request_headers() -> dict[str, str]:
    headers = getattr(st.context, "headers", {}) or {}
    if isinstance(headers, dict):
        return {str(key): str(value) for key, value in headers.items()}
    return {}


def _request_ip_address() -> str:
    headers = _request_headers()
    forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for") or ""
    if forwarded:
        return str(forwarded).split(",")[0].strip()
    return headers.get("X-Real-Ip") or headers.get("x-real-ip") or ""


def _request_user_agent() -> str:
    headers = _request_headers()
    return headers.get("User-Agent") or headers.get("user-agent") or ""


def _current_workspace_section_name() -> str:
    return _normalize_workspace_section(st.session_state.get("workspace_section"))


# ---------------------------------------------------------------------------
# Group B — Analytics tracking
# ---------------------------------------------------------------------------


def _record_usage_interaction(
    *,
    event_type: str,
    detail: dict[str, object] | None = None,
    section_name: str = "",
) -> None:
    if st.session_state.get("_ui_auth_mode") != "database":
        return
    current_user = _current_user_context()
    if not isinstance(current_user, auth_service.UserContext):
        return
    payload = dict(detail or {})
    payload.setdefault("app_track", (os.getenv("APP_TRACK") or "local").strip().lower())
    auth_service.record_access_event(
        event_type=str(event_type or "").strip().lower(),
        event_category="usage",
        user=current_user,
        section_name=_normalize_workspace_section(section_name or _current_workspace_section_name()),
        session_token=str(st.session_state.get("_ui_auth_session_id") or ""),
        ip_address=_request_ip_address(),
        user_agent=_request_user_agent(),
        detail=payload,
    )


def _activity_link_key(prefix: str, *, label: str, url: str = "") -> str:
    digest = hashlib.sha1(f"{prefix}|{label}|{url}".encode("utf-8")).hexdigest()[:12]
    cleaned_prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", str(prefix or "activity_link").strip()) or "activity_link"
    return f"{cleaned_prefix}_{digest}"


def _render_tracked_activity_link(
    label: str,
    url: str,
    *,
    key: str,
    surface: str,
    target_type: str = "content_link",
    source: str = "",
    published_at: str = "",
    extra_detail: dict[str, object] | None = None,
) -> None:
    clean_label = str(label or "").strip()
    clean_url = str(url or "").strip()
    if not clean_label:
        return
    if not clean_url:
        st.markdown(f"- {clean_label}")
        return
    payload = {
        "surface": str(surface or "").strip(),
        "source": str(source or "").strip(),
        "published_at": str(published_at or "").strip(),
        "target_type": str(target_type or "content_link").strip(),
        "target_label": clean_label,
        "target_id": clean_url,
        "target_url": clean_url,
    }
    for detail_key, detail_value in dict(extra_detail or {}).items():
        if detail_value in (None, "", [], {}):
            continue
        payload[str(detail_key)] = detail_value
    st.link_button(
        clean_label,
        clean_url,
        key=key,
        type="tertiary",
        on_click=_record_usage_interaction,
        kwargs={
            "event_type": "content_link_open",
            "detail": payload,
        },
    )


# ---------------------------------------------------------------------------
# Group C — Taxonomy helpers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=900, show_spinner=False)
def _load_taxonomy_lookup_cached(symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
    requested = [str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()]
    if not requested:
        return {}
    try:
        return taxonomy_lookup_by_symbol(requested)
    except Exception:
        return {}


def _taxonomy_row_for_symbol(symbol: str) -> dict[str, object]:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    return dict(_load_taxonomy_lookup_cached((normalized,)).get(normalized) or {})


def _market_business_filter_for_symbol(symbol: str) -> str:
    row = _taxonomy_row_for_symbol(symbol)
    label = business_focus_label_from_taxonomy_row(row)
    return label or "All Market"


def _company_narrative_lens_for_symbol(symbol: str) -> str:
    row = _taxonomy_row_for_symbol(symbol)
    return str(dashboard_business_lens_from_taxonomy_row(row) or "").strip()


def _taxonomy_summary_text(symbol: str) -> str:
    row = _taxonomy_row_for_symbol(symbol)
    if not row:
        return ""

    pieces: list[str] = []
    peer_group_name = str(row.get("peer_group_name") or "").strip()
    sector = str(row.get("sector") or "").strip()
    source = str(row.get("source_of_truth") or "").strip()

    if peer_group_name and peer_group_name not in {"", "Market", "Unknown"}:
        pieces.append(peer_group_name)
    elif sector and sector != "Unknown":
        pieces.append(sector)
    if source:
        pieces.append(f"source: {source}")

    return f"Taxonomy: {' | '.join(pieces)}" if pieces else ""


# ---------------------------------------------------------------------------
# Group D — Navigation
# ---------------------------------------------------------------------------


def _open_attention_target(section_name: str, params: dict[str, object] | None = None) -> None:
    target = _normalize_workspace_section(section_name) or MARKET_EXPLORER_SECTION
    payload = dict(params or {})
    ticker = str(payload.get("ticker") or "").upper().strip()
    market_view = str(payload.get("market_view") or "").strip()
    business_filter = str(payload.get("business_filter") or "").strip()
    commodity_focus = str(payload.get("commodity_focus") or "").strip()
    normalized_market_view = market_view or ("Commodity Section" if commodity_focus else "Markets")

    st.session_state["_pending_workspace_section"] = target
    if target == MARKET_EXPLORER_SECTION:
        st.session_state["_pending_market_view"] = normalized_market_view
        if normalized_market_view == "Commodity Section":
            st.session_state.pop("_pending_market_business_filter", None)
            st.session_state["_pending_market_commodity_focus"] = commodity_focus or "Broad Commodity Market"
        elif normalized_market_view == "Markets":
            st.session_state.pop("_pending_market_commodity_focus", None)
            inferred_business_filter = business_filter or _market_business_filter_for_symbol(ticker)
            st.session_state["_pending_market_business_filter"] = inferred_business_filter or "All Market"
        elif normalized_market_view == "Broad Markets":
            st.session_state.pop("_pending_market_commodity_focus", None)
            if business_filter:
                st.session_state["_pending_market_business_filter"] = business_filter
    if ticker:
        _set_workspace_ticker(ticker)
        if target == MARKET_EXPLORER_SECTION and normalized_market_view == "Commodity Section":
            st.session_state["market_commodity_selected_ticker"] = ticker
            st.session_state["market_commodity_ticker_widget"] = ticker
    st.rerun()


# ---------------------------------------------------------------------------
# Group E — Content helpers
# ---------------------------------------------------------------------------


def _compact_background_fallback_text(ticker: str) -> str:
    target = str(ticker or "").upper().strip()
    return f"No relevant catalyst found in web coverage for {target}."


def _is_low_signal_company_context_text(text: object) -> bool:
    cleaned = " ".join(str(text or "").split()).strip().lower()
    if not cleaned:
        return True
    if re.fullmatch(r"\d+\s+recent article\(s\) over roughly the last \d+\s+day\(s\); tone is [^.]+\.", cleaned):
        return True
    low_signal_fragments = [
        "no relevant catalyst found",
        "company background is unavailable",
        "the current narrative is still thin",
    ]
    return any(fragment in cleaned for fragment in low_signal_fragments)


def _first_substantive_company_context_line(lines: list[object], *, fallback: str = "") -> str:
    for item in lines:
        text = " ".join(str(item or "").split()).strip()
        if text and not _is_low_signal_company_context_text(text):
            return text
    return " ".join(str(fallback or "").split()).strip()


def _collect_evidence_links(*, recent_headlines: list[dict[str, object]] | None = None, articles: pd.DataFrame | None = None, limit: int = 8) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if isinstance(recent_headlines, list):
        for item in recent_headlines:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline") or "").strip()
            url = str(item.get("url") or "").strip()
            if not headline:
                continue
            dedupe_key = (headline.lower(), url.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append(
                {
                    "headline": headline,
                    "url": url,
                    "source": str(item.get("source") or "News").strip(),
                    "published_at": str(item.get("published_at") or "").strip(),
                }
            )
            if len(rows) >= max(int(limit), 1):
                return rows
    if isinstance(articles, pd.DataFrame) and not articles.empty:
        for _, row in articles.iterrows():
            headline = str(row.get("headline") or "").strip()
            url = str(row.get("url") or "").strip()
            if not headline:
                continue
            dedupe_key = (headline.lower(), url.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
            rows.append(
                {
                    "headline": headline,
                    "url": url,
                    "source": str(row.get("source") or "News").strip(),
                    "published_at": published_at.isoformat() if pd.notna(published_at) else "",
                }
            )
            if len(rows) >= max(int(limit), 1):
                return rows
    return rows


# ---------------------------------------------------------------------------
# Group F — Rendering helpers
# ---------------------------------------------------------------------------


def _render_compact_background_sections(
    ticker: str,
    *,
    background_summary: str,
    what_happened_summary: str,
    evidence_links: list[dict[str, str]],
) -> None:
    fallback = _compact_background_fallback_text(ticker)
    background_text = " ".join(str(background_summary or "").split()).strip()
    happened_text = " ".join(str(what_happened_summary or "").split()).strip()
    if _is_low_signal_company_context_text(background_text):
        background_text = ""
    if _is_low_signal_company_context_text(happened_text):
        happened_text = ""
    if not background_text:
        background_text = f"Company background is not available for {str(ticker or '').upper().strip()}."
    if not happened_text:
        happened_text = fallback

    st.markdown("**Background**")
    st.write(background_text)

    st.markdown("**What Happened**")
    st.write(happened_text)

    st.markdown("**Evidence**")
    if not evidence_links:
        st.caption("No relevant evidence links were available.")
        return
    for index, item in enumerate(evidence_links[:8]):
        headline = str((item or {}).get("headline") or "Untitled").strip()
        source = str((item or {}).get("source") or "News").strip()
        published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
        published_label = published_at.strftime("%Y-%m-%d") if pd.notna(published_at) else "n/a"
        url = str((item or {}).get("url") or "").strip()
        _render_tracked_activity_link(
            headline,
            url,
            key=_activity_link_key(f"ticker_background_evidence_{index}", label=headline, url=url),
            surface="ticker_background_evidence",
            target_type="evidence_link",
            source=source,
            published_at=published_label,
        )
        st.caption(" | ".join(part for part in [source, published_label] if part))


def _filter_fundamentals_asof(
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
        scoped = scoped[scoped["report_date"].notna() & (scoped["report_date"] <= cutoff)].copy()
        out[key] = scoped.reset_index(drop=True)
    return out


def _render_fundamental_statement_charts(
    ticker: str,
    fundamentals: dict[str, pd.DataFrame],
    *,
    quarterly_titles: bool = False,
    bottom_labels: bool = False,
) -> None:
    normalized_ticker = str(ticker or "").upper().strip()
    title_specs = [
        ("income", "Income Statement (Quarterly)" if quarterly_titles else "Income"),
        ("balance", "Balance Sheet (Quarterly)" if quarterly_titles else "Balance"),
        ("cashflow", "Cash Flow (Quarterly)" if quarterly_titles else "Cash Flow"),
    ]
    for statement_key, title_suffix in title_specs:
        frame = (fundamentals or {}).get(statement_key, pd.DataFrame())
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        title = f"{normalized_ticker} - {title_suffix}" if quarterly_titles else f"{normalized_ticker} {title_suffix}"
        fig = plot_statement(frame, title, legend_bottom=bottom_labels)
        st.plotly_chart(fig, use_container_width=True)


def _render_overview_fundamentals(
    cfg: object,
    ticker: str,
    *,
    force_data_refresh: bool,
    asof_time_utc: object | None = None,
) -> None:
    normalized_ticker = str(ticker or "").upper().strip()
    if not normalized_ticker:
        return
    try:
        fundamentals = dashboard_loaders._load_quarterly_fundamentals_cached(normalized_ticker, force_refresh=force_data_refresh)
    except Exception as exc:
        st.caption(f"Fundamentals unavailable: {exc}")
        return
    scoped = _filter_fundamentals_asof(fundamentals, asof_time_utc=asof_time_utc)
    has_any = any(isinstance((scoped or {}).get(key), pd.DataFrame) and not (scoped or {}).get(key).empty for key in ["income", "balance", "cashflow"])
    st.markdown("**Fundamentals**")
    if not has_any:
        st.caption("No quarterly fundamentals were available for this ticker.")
        return
    _fundamentals_staleness_days = 150
    try:
        _latest_report_dates = []
        for _fkey in ["income", "balance", "cashflow"]:
            _fframe = (scoped or {}).get(_fkey, pd.DataFrame())
            if isinstance(_fframe, pd.DataFrame) and not _fframe.empty and "report_date" in _fframe.columns:
                _parsed = pd.to_datetime(_fframe["report_date"], errors="coerce").dropna()
                if not _parsed.empty:
                    _latest_report_dates.append(_parsed.max())
        if _latest_report_dates:
            _most_recent = max(_latest_report_dates)
            _age_days = (pd.Timestamp.now() - _most_recent).days
            if _age_days > _fundamentals_staleness_days:
                st.warning(
                    f"Fundamentals data may be stale — last reported quarter ended "
                    f"{_most_recent.strftime('%b %Y')} ({_age_days} days ago)."
                )
    except Exception:
        pass
    _render_fundamental_statement_charts(normalized_ticker, scoped, quarterly_titles=True)


def _render_page_agentic_summary_panel(
    surface: str,
    context: dict[str, object],
    *,
    key_prefix: str,
) -> dict[str, object]:
    safe_context = attention_content._json_ready(context if isinstance(context, dict) else {})
    context_signature = page_summary_context_signature(safe_context)
    ticker = str(safe_context.get("ticker") or "").upper().strip()
    summary_key = f"{key_prefix}_agentic_summary"
    signature_key = f"{key_prefix}_agentic_summary_signature"
    summary = dashboard_loaders._load_page_agentic_summary_cached(str(surface or ""), context_signature, ticker)
    st.session_state[summary_key] = summary
    st.session_state[signature_key] = context_signature
    with st.container(border=True):
        st.subheader("Zopedia Summary")
        status = str(summary.get("status") or "").strip()
        headline = str(summary.get("headline") or "").strip()
        if status == "ok" and str(summary.get("summary_markdown") or "").strip():
            if headline:
                st.markdown(f"**{headline}**")
            st.markdown(str(summary.get("summary_markdown") or "").strip())
            watch_items = [
                str(item).strip()
                for item in to_list(summary.get("watch_items"))
                if str(item).strip()
            ]
            if watch_items:
                st.markdown("**Worth Looking Into**")
                st.markdown("\n".join(f"- {item}" for item in watch_items[:5]))
            data_gaps = [
                str(item).strip()
                for item in to_list(summary.get("data_gaps"))
                if str(item).strip()
            ]
            if data_gaps:
                with st.expander("Data gaps", expanded=False):
                    st.markdown("\n".join(f"- {item}" for item in data_gaps[:5]))
            confidence = str(summary.get("confidence") or "").strip()
            if confidence:
                st.caption(f"Confidence: {confidence}")
        else:
            st.info("Zopedia Summary is refreshing from the latest data.")
    return summary
