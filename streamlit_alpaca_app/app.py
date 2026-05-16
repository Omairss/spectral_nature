from __future__ import annotations

import base64
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import html
import importlib
import json
import logging
import os
from pathlib import Path
import re
import secrets as py_secrets
import threading
import time
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from PIL import Image
from streamlit.components.v1 import html as components_html

from compute.portfolio import normalize_timeseries_view
from data_access.layer import DataAccessLayer
from presentation import attention_content, dashboard_loaders
from services import api_auth, auth_service, omnibar_agent as omnibar_agent_service
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.analytics import build_metric_bar, build_portfolio_vs_benchmarks_fig
from services.attention_home_summary import (
    apply_display_limits,
    attention_mover_card_title as attention_mover_card_title_service,
    build_attention_home_narrative_beats,
    build_attention_home_summary_payload,
)
from services import attention_surface as attention_surface_module
from services.company import build_attention_news_narrative, summarize_recent_news
from services.config import AppConfig, alpaca_secret_name_settings, load_config
from services.data_cache import cache_bundle_exists, cache_data_root, cache_policy_path, dataset_scope
from services.elevenlabs_tts import (
    ElevenLabsTTSAPIError,
    load_elevenlabs_tts_config,
)
from services.entity_taxonomy import business_focus_label_from_taxonomy_row, dashboard_business_lens_from_taxonomy_row, taxonomy_lookup_by_symbol
from services.fred import (
    FredAPIError,
    FredSeriesSpec,
    build_fred_figure,
    build_fred_series_summary,
    format_fred_delta,
    format_fred_value,
    fred_categories,
    load_fred_api_key,
)
from services.pipeline_store import (
    SOURCE_DATASETS,
    SOURCE_JOB_MAP,
    dataset_version_history,
    job_run_history,
    latest_job_status_table,
    load_latest_dataset_frame,
    pipeline_store_configured,
    record_trading_agent_action,
    start_source_refresh_job,
    trading_agent_actions_table,
)
from services.homepage_v2 import (
    HOMEPAGE_V2_COMPANY_PANEL,
    HOMEPAGE_V2_RESEARCH_PANEL,
    build_homepage_v2_market_digest,
    homepage_v2_editorial_links,
    homepage_v2_bundle_symbol_lookup,
    normalize_homepage_v2_detail_state,
)
from services.entity_extraction import (
    extract_entities,
    graph_add_node_candidates,
    linked_kg_node_ids,
)
from services.knowledge_graph import (
    build_knowledge_graph_draft,
    build_knowledge_graph_overview,
    commit_knowledge_graph_review,
    default_seed_node_ids_from_matches,
    draft_edges_frame,
    draft_nodes_frame,
    knowledge_graph_runtime_status,
    list_recent_knowledge_graph_commits,
    load_knowledge_graph_snapshot,
    plot_knowledge_graph_draft,
    search_knowledge_graph_nodes,
)
from services.knowledge_graph_proposals import build_knowledge_graph_draft_from_proposals
from services.json_utils import to_list
from services.llm import (
    get_active_narrative_style_rule,
    list_config_params,
    list_narrative_prompts,
    load_llm_config,
    load_prompt_overrides,
    save_prompt_overrides,
    set_config_param_override,
    set_narrative_prompt_override,
    set_narrative_style_rule_override,
)
from services.runtime_policy import attention_ui_policy, presentation_layer_only_enabled, section_data_available
from services.secrets import resolve_secret_value
from services.fundamentals import plot_statement
from services.market import (
    business_focus_description,
    business_focus_options,
    business_focus_universe,
    commodity_dependency_graph,
    commodity_focus_description,
    commodity_focus_options,
    commodity_focus_universe,
    commodity_proxy_profile,
    commodity_reference_universe,
    extend_symbol_universe,
)
from services.options import rank_options
from services.page_agentic_summary import (
    broad_economy_summary_context,
    market_summary_context,
    page_summary_context_signature,
    stock_summary_context,
)
from services.technicals import build_technical_figure

_SIGNALS_IMPORT_ERROR: str | None = None
_ATTENTION_SUMMARY_AUDIO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="summary_audio")
_ATTENTION_SUMMARY_AUDIO_FUTURES: dict[str, Future[bytes]] = {}
_ATTENTION_SUMMARY_AUDIO_LOCK = threading.Lock()
try:
    importlib.invalidate_caches()
    _signals = importlib.import_module("services.signals")
    build_forecast_cone_figure = _signals.build_forecast_cone_figure
    build_price_channel_figure = _signals.build_price_channel_figure
    build_pullback_figure = _signals.build_pullback_figure
    build_terminal_distribution_figure = _signals.build_terminal_distribution_figure
except ModuleNotFoundError as exc:
    if exc.name != "services.signals":
        raise
    _SIGNALS_IMPORT_ERROR = str(exc)

    def build_price_channel_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Price Channel")
        return fig

    def build_pullback_figure(frame: pd.DataFrame, ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Pullback From ATH")
        return fig

    def build_forecast_cone_figure(history_frame: pd.DataFrame, forecast: dict[str, object], ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} Next-Week Probability Cone")
        return fig

    def build_terminal_distribution_figure(forecast: dict[str, object], ticker: str) -> go.Figure:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", title=f"{ticker} 5-Day Terminal Price Distribution")
        return fig

APP_ROOT = Path(__file__).resolve().parent
BRANDING_ROOT = APP_ROOT / "branding" / "Logo Files"
APP_FAVICON_PATH = BRANDING_ROOT / "Favicons" / "iPhone.png"
APP_SIDEBAR_LOGO_PATH = BRANDING_ROOT / "png" / "White logo - no background.png"
APP_SUBSTACK_ICON_PATH = APP_ROOT / "branding" / "substack" / "Substack.png"


def _load_page_icon() -> object:
    if APP_FAVICON_PATH.is_file():
        return str(APP_FAVICON_PATH)
    return "chart_with_upwards_trend"


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


st.set_page_config(
    page_title="Spectral Nature",
    page_icon=_load_page_icon(),
    layout="wide",
    menu_items={
        "Get help": None,
        "Report a Bug": None,
        "About": None,
    },
)

LOGGER = logging.getLogger("spectral_nature.ui_app")
if not LOGGER.handlers:
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)

    file_handler = logging.FileHandler("/tmp/spectral_nature_ui.log")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False

# Log LLM/embedding readiness once per app session so operators can see
# which capabilities are actually live (mistakes.md #15, #36).
if "_llm_readiness_logged" not in st.session_state:
    try:
        from services.llm import check_llm_readiness
        _readiness = check_llm_readiness()
        for _key, _val in _readiness.items():
            LOGGER.info("LLM readiness: %s = %s", _key, _val)
        st.session_state["_llm_readiness_logged"] = True
    except Exception as _exc:
        LOGGER.warning("LLM readiness check failed: %s", _exc)
        st.session_state["_llm_readiness_logged"] = True

HOME_EXP_SECTION = "Experiment"
AGENTIC_OMNIBAR_SECTION = "Chat + Search"
STOCK_INVESTIGATOR_SECTION = "Stock Investigator"
PORTFOLIO_SECTION = "Portfolio"
PORTFOLIO_PERFORMANCE_SECTION = "Portfolio Performance"
MARKET_EXPLORER_SECTION = "Market Explorer"
BROAD_ECONOMY_SECTION = "Broad Economy"
TRADING_AGENT_SECTION = "Trading Agent"
ADMIN_SECTION = "Admin"

BASE_SECTION_OPTIONS = [
    "Home",
    AGENTIC_OMNIBAR_SECTION,
    PORTFOLIO_SECTION,
    PORTFOLIO_PERFORMANCE_SECTION,
    MARKET_EXPLORER_SECTION,
    STOCK_INVESTIGATOR_SECTION,
    "Option Strategizer",
    BROAD_ECONOMY_SECTION,
]
OMNIBAR_POLICY_VERSION = "streamlit-agentic-omnibar-v1"
OMNIBAR_MACRO_RELEASES: tuple[dict[str, object], ...] = (
    {
        "release_id": "cpi",
        "label": "CPI Release",
        "subtitle": f"Inflation release context and price-level signals in {BROAD_ECONOMY_SECTION}.",
        "aliases": ("cpi", "consumer price index", "inflation release", "inflation print"),
    },
    {
        "release_id": "pce",
        "label": "PCE Release",
        "subtitle": f"Fed-focused inflation context and personal consumption expenditures signals in {BROAD_ECONOMY_SECTION}.",
        "aliases": ("pce", "core pce", "personal consumption expenditures"),
    },
    {
        "release_id": "nfp",
        "label": "NFP Release",
        "subtitle": f"Labor-market release context for payrolls and unemployment sensitivity in {BROAD_ECONOMY_SECTION}.",
        "aliases": ("nfp", "payrolls", "nonfarm payrolls", "jobs report"),
    },
    {
        "release_id": "fomc",
        "label": "FOMC Decision",
        "subtitle": f"Policy path and rates context through {BROAD_ECONOMY_SECTION}.",
        "aliases": ("fomc", "fed", "fed meeting", "rate decision", "powell"),
    },
    {
        "release_id": "retail_sales",
        "label": "Retail Sales",
        "subtitle": f"Consumer-demand release context in {BROAD_ECONOMY_SECTION}.",
        "aliases": ("retail sales", "consumer spending"),
    },
    {
        "release_id": "ism",
        "label": "ISM Survey",
        "subtitle": f"Manufacturing and services diffusion context in {BROAD_ECONOMY_SECTION}.",
        "aliases": ("ism", "pmi", "manufacturing pmi", "services pmi"),
    },
)
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
_ATTENTION_UI_POLICY = attention_ui_policy()
ATTENTION_HORIZON_OPTIONS = list(_ATTENTION_UI_POLICY.horizon_options)
ATTENTION_HORIZON_LABELS = dict(_ATTENTION_UI_POLICY.horizon_labels)
ATTENTION_SENSITIVITY_ORDER = list(_ATTENTION_UI_POLICY.sensitivity_order)

_AUTH_COOKIE_NAME = "spectral_nature_ui_session"
_AUTH_COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60
_AUTH_COOKIE_REMEMBER_ME_TTL_SECONDS = 30 * 24 * 60 * 60
APP_BRAND_NAME = "Spectral Nature"
APP_BRAND_KICKER = "Torres Capital"
APP_BRAND_SUBTITLE = "Research, portfolio context, and market structure in one refined workspace."
_APP_SHELL_STYLE_VERSION = "2026-04-14-vertical-nav-v1"
_INLINE_LOADING_STYLE_VERSION = "2026-04-02-inline-loading-v1"
_INVITE_THEME_WIDGET_PREFIX = "_access_invite_theme_"
_INVITE_TEMPLATE_INIT_KEY = "_access_invite_template_editor_initialized"
_INVITE_TEMPLATE_SELECTED_ID_KEY = "_access_invite_template_selected_id"
_INVITE_TEMPLATE_LOADED_ID_KEY = "_access_invite_template_loaded_id"
_INVITE_TEMPLATE_NAME_KEY = "_access_invite_template_name"
_INVITE_TEMPLATE_LOGO_VARIANT_KEY = "_access_invite_template_logo_variant"
_INVITE_TEMPLATE_CHART_SOURCE_KEY = "_access_invite_template_chart_source"
_INVITE_TEMPLATE_CHART_BUILTIN_KEY = "_access_invite_template_chart_builtin"
_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY = "_access_invite_template_chart_upload_filename"
_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY = "_access_invite_template_chart_upload_mime"
_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY = "_access_invite_template_chart_upload_data_b64"
_INVITE_TEMPLATE_CHART_UPLOAD_DIGEST_KEY = "_access_invite_template_chart_upload_digest"
_INVITE_TEMPLATE_UPLOAD_NONCE_KEY = "_access_invite_template_upload_nonce"
_INVITE_TEMPLATE_NOTICE_KEY = "_access_invite_template_notice"
_INVITE_TEMPLATE_PENDING_LOAD_KEY = "_access_invite_template_pending_load"
_INVITE_TEMPLATE_PENDING_SELECTED_ID_KEY = "_access_invite_template_pending_selected_id"
_ACCESS_PENDING_INVITE_NOTICE_KEY = "_access_pending_invite_notice"


def _invite_theme_widget_state_key(field: str) -> str:
    return f"{_INVITE_THEME_WIDGET_PREFIX}{field}"


def _set_invite_theme_widget_state(theme: dict[str, object]) -> None:
    resolved = auth_service.sanitize_invite_email_theme(theme)
    for field in [
        "kicker",
        "headline",
        "intro_text",
        "cta_label",
        "graph_caption",
        "footer_note",
        "background_color",
        "card_background_color",
        "title_color",
        "body_color",
        "muted_text_color",
        "button_color",
        "button_text_color",
        "link_color",
        "border_color",
        "show_graph",
    ]:
        st.session_state[_invite_theme_widget_state_key(field)] = resolved.get(field)


def _invite_theme_from_widget_state() -> dict[str, object]:
    raw_theme = {
        "kicker": str(st.session_state.get(_invite_theme_widget_state_key("kicker")) or ""),
        "headline": str(st.session_state.get(_invite_theme_widget_state_key("headline")) or ""),
        "intro_text": str(st.session_state.get(_invite_theme_widget_state_key("intro_text")) or ""),
        "cta_label": str(st.session_state.get(_invite_theme_widget_state_key("cta_label")) or ""),
        "graph_caption": str(st.session_state.get(_invite_theme_widget_state_key("graph_caption")) or ""),
        "footer_note": str(st.session_state.get(_invite_theme_widget_state_key("footer_note")) or ""),
        "background_color": str(st.session_state.get(_invite_theme_widget_state_key("background_color")) or ""),
        "card_background_color": str(st.session_state.get(_invite_theme_widget_state_key("card_background_color")) or ""),
        "title_color": str(st.session_state.get(_invite_theme_widget_state_key("title_color")) or ""),
        "body_color": str(st.session_state.get(_invite_theme_widget_state_key("body_color")) or ""),
        "muted_text_color": str(st.session_state.get(_invite_theme_widget_state_key("muted_text_color")) or ""),
        "button_color": str(st.session_state.get(_invite_theme_widget_state_key("button_color")) or ""),
        "button_text_color": str(st.session_state.get(_invite_theme_widget_state_key("button_text_color")) or ""),
        "link_color": str(st.session_state.get(_invite_theme_widget_state_key("link_color")) or ""),
        "border_color": str(st.session_state.get(_invite_theme_widget_state_key("border_color")) or ""),
        "show_graph": bool(st.session_state.get(_invite_theme_widget_state_key("show_graph"))),
    }
    return auth_service.sanitize_invite_email_theme(raw_theme)


def _invite_template_upload_widget_key() -> str:
    nonce = int(st.session_state.get(_INVITE_TEMPLATE_UPLOAD_NONCE_KEY) or 0)
    return f"_access_invite_template_chart_upload_{nonce}"


def _clear_invite_template_upload_chart(*, reset_widget: bool) -> None:
    st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY] = ""
    st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY] = ""
    st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY] = ""
    st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DIGEST_KEY] = ""
    if reset_widget:
        st.session_state[_INVITE_TEMPLATE_UPLOAD_NONCE_KEY] = int(st.session_state.get(_INVITE_TEMPLATE_UPLOAD_NONCE_KEY) or 0) + 1


def _set_invite_template_widget_state(template: dict[str, object]) -> None:
    if not isinstance(template, dict):
        return
    st.session_state[_INVITE_TEMPLATE_LOADED_ID_KEY] = str(template.get("template_id") or "")
    st.session_state[_INVITE_TEMPLATE_NAME_KEY] = str(template.get("name") or "Invite Template")
    logo_variant = str(template.get("logo_variant") or "color").strip().lower()
    if logo_variant not in {"color", "white"}:
        logo_variant = "color"
    st.session_state[_INVITE_TEMPLATE_LOGO_VARIANT_KEY] = logo_variant
    _set_invite_theme_widget_state(template.get("theme") if isinstance(template.get("theme"), dict) else {})

    chart_asset = template.get("chart_asset") if isinstance(template.get("chart_asset"), dict) else {}
    chart_kind = str(chart_asset.get("kind") or "").strip().lower()
    if chart_kind == "upload":
        st.session_state[_INVITE_TEMPLATE_CHART_SOURCE_KEY] = "upload"
        st.session_state[_INVITE_TEMPLATE_CHART_BUILTIN_KEY] = (
            "dark" if str(st.session_state.get(_INVITE_TEMPLATE_LOGO_VARIANT_KEY) or "color") == "white" else "light"
        )
        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY] = str(chart_asset.get("filename") or "uploaded-chart")
        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY] = str(chart_asset.get("mime_type") or "image/png")
        data_b64 = str(chart_asset.get("data_b64") or "")
        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY] = data_b64
        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DIGEST_KEY] = hashlib.sha256(data_b64.encode("ascii")).hexdigest() if data_b64 else ""
    else:
        st.session_state[_INVITE_TEMPLATE_CHART_SOURCE_KEY] = "builtin"
        builtin_name = str(chart_asset.get("name") or "").strip().lower()
        if builtin_name not in {"dark", "light"}:
            builtin_name = "dark" if str(st.session_state.get(_INVITE_TEMPLATE_LOGO_VARIANT_KEY) or "color") == "white" else "light"
        st.session_state[_INVITE_TEMPLATE_CHART_BUILTIN_KEY] = builtin_name
        _clear_invite_template_upload_chart(reset_widget=True)


def _invite_template_from_widget_state() -> dict[str, object]:
    template_name = str(st.session_state.get(_INVITE_TEMPLATE_NAME_KEY) or "").strip() or "Invite Template"
    logo_variant = str(st.session_state.get(_INVITE_TEMPLATE_LOGO_VARIANT_KEY) or "color").strip().lower()
    if logo_variant not in {"color", "white"}:
        logo_variant = "color"

    chart_source = str(st.session_state.get(_INVITE_TEMPLATE_CHART_SOURCE_KEY) or "builtin").strip().lower()
    builtin_name = str(st.session_state.get(_INVITE_TEMPLATE_CHART_BUILTIN_KEY) or "").strip().lower()
    if builtin_name not in {"dark", "light"}:
        builtin_name = "dark" if logo_variant == "white" else "light"
    chart_asset: dict[str, object] = {"kind": "builtin", "name": builtin_name}
    if chart_source == "upload":
        filename = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY) or "").strip()
        mime_type = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY) or "").strip().lower()
        data_b64 = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY) or "").strip()
        if filename and mime_type in auth_service.INVITE_EMAIL_UPLOAD_ALLOWED_MIME_TYPES and data_b64:
            chart_asset = {
                "kind": "upload",
                "filename": filename,
                "mime_type": mime_type,
                "data_b64": data_b64,
            }
    return {
        "name": template_name,
        "theme": _invite_theme_from_widget_state(),
        "logo_variant": logo_variant,
        "chart_asset": chart_asset,
    }


def _invite_template_label(template: dict[str, object], *, active_template_id: str) -> str:
    template_id = str(template.get("template_id") or "")
    name = str(template.get("name") or template_id or "Template")
    tags: list[str] = []
    if template_id == active_template_id:
        tags.append("active")
    if bool(template.get("protected")):
        tags.append("built-in")
    if tags:
        return f"{name} [{', '.join(tags)}]"
    return name


def _show_invite_template_notice() -> None:
    notice = st.session_state.pop(_INVITE_TEMPLATE_NOTICE_KEY, None)
    if isinstance(notice, dict):
        level = str(notice.get("level") or "").strip().lower()
        message = str(notice.get("message") or "").strip()
        if not message:
            return
        if level == "error":
            st.error(message)
            return
        if level == "warning":
            st.warning(message)
            return
        st.success(message)


def _show_access_pending_invite_notice() -> None:
    notice = st.session_state.pop(_ACCESS_PENDING_INVITE_NOTICE_KEY, None)
    if not isinstance(notice, dict):
        return

    level = str(notice.get("level") or "").strip().lower()
    message = str(notice.get("message") or "").strip()
    detail = str(notice.get("detail") or "").strip()
    code_value = str(notice.get("code") or "").strip()

    if message:
        if level == "error":
            st.error(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.success(message)
    if detail:
        st.caption(detail)
    if code_value:
        st.code(code_value, language="text")


def _apply_pending_invite_template_state() -> None:
    pending_selected_id = st.session_state.pop(_INVITE_TEMPLATE_PENDING_SELECTED_ID_KEY, None)
    if isinstance(pending_selected_id, str) and pending_selected_id.strip():
        st.session_state[_INVITE_TEMPLATE_SELECTED_ID_KEY] = pending_selected_id.strip()

    pending_template = st.session_state.pop(_INVITE_TEMPLATE_PENDING_LOAD_KEY, None)
    if isinstance(pending_template, dict):
        _set_invite_template_widget_state(pending_template)


def _queue_invite_template_state_update(
    *,
    selected_template_id: str | None = None,
    template_to_load: dict[str, object] | None = None,
    notice: dict[str, str] | None = None,
) -> None:
    if isinstance(selected_template_id, str) and selected_template_id.strip():
        st.session_state[_INVITE_TEMPLATE_PENDING_SELECTED_ID_KEY] = selected_template_id.strip()
    if isinstance(template_to_load, dict):
        st.session_state[_INVITE_TEMPLATE_PENDING_LOAD_KEY] = dict(template_to_load)
    if isinstance(notice, dict):
        st.session_state[_INVITE_TEMPLATE_NOTICE_KEY] = notice
    st.rerun()


def _queue_access_pending_invite_notice(
    *,
    level: str,
    message: str,
    detail: str = "",
    code: str = "",
) -> None:
    st.session_state[_ACCESS_PENDING_INVITE_NOTICE_KEY] = {
        "level": str(level or "").strip().lower() or "success",
        "message": str(message or "").strip(),
        "detail": str(detail or "").strip(),
        "code": str(code or "").strip(),
    }
    st.rerun()


def _format_access_admin_share_percent(value: object) -> str:
    try:
        share_pct = max(float(value or 0.0), 0.0) * 100.0
    except Exception:
        share_pct = 0.0
    rendered = f"{share_pct:.2f}".rstrip("0").rstrip(".")
    return f"{rendered}%"


def _format_pending_invite_expires(value: object) -> str:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


def _access_admin_int_state_value(key: str, *, fallback: int, allowed: tuple[int, ...]) -> int:
    try:
        value = int(st.session_state.get(key, fallback) or fallback)
    except Exception:
        value = fallback
    if value not in allowed:
        value = fallback
    st.session_state[key] = value
    return value


def _truncate_access_sankey_label(value: object, *, limit: int = 42) -> str:
    label = str(value or "").strip()
    if len(label) <= limit:
        return label
    return f"{label[: max(limit - 3, 1)].rstrip()}..."


def _format_access_sankey_target_label(target_label: object, target_type: object) -> str:
    label = str(target_label or "").strip()
    if not label:
        return ""
    target_kind = str(target_type or "").strip().replace("_", " ")
    if not target_kind:
        return label
    normalized_kind = target_kind.title()
    if normalized_kind.lower() == label.lower():
        return label
    return f"{label} ({normalized_kind})"


def _build_access_usage_sankey_figure(flow_rows: list[dict[str, object]]) -> go.Figure | None:
    if not flow_rows:
        return None

    node_lookup: dict[tuple[str, str], int] = {}
    node_labels: list[str] = []
    node_colors: list[str] = []
    node_x: list[float] = []
    user_section_links: dict[tuple[int, int], int] = {}
    section_target_links: dict[tuple[int, int], int] = {}

    def _node_index(kind: str, label: str) -> int:
        key = (kind, label)
        if key in node_lookup:
            return node_lookup[key]
        node_lookup[key] = len(node_labels)
        node_labels.append(_truncate_access_sankey_label(label))
        if kind == "user":
            node_colors.append("rgba(37, 99, 235, 0.85)")
            node_x.append(0.01)
        elif kind == "section":
            node_colors.append("rgba(14, 116, 144, 0.80)")
            node_x.append(0.48)
        else:
            node_colors.append("rgba(22, 163, 74, 0.80)")
            node_x.append(0.92)
        return node_lookup[key]

    for row in flow_rows:
        if not isinstance(row, dict):
            continue
        try:
            event_count = max(int(row.get("event_count") or 0), 0)
        except Exception:
            event_count = 0
        if event_count <= 0:
            continue
        user_label = str(row.get("user_label") or "").strip()
        section_label = str(row.get("section_label") or "").strip()
        target_label = _format_access_sankey_target_label(row.get("target_label"), row.get("target_type"))
        if not user_label or not section_label:
            continue

        user_index = _node_index("user", user_label)
        section_index = _node_index("section", section_label)
        user_section_links[(user_index, section_index)] = user_section_links.get((user_index, section_index), 0) + event_count

        if target_label:
            target_index = _node_index("target", target_label)
            section_target_links[(section_index, target_index)] = (
                section_target_links.get((section_index, target_index), 0) + event_count
            )

    if not user_section_links and not section_target_links:
        return None

    link_sources: list[int] = []
    link_targets: list[int] = []
    link_values: list[int] = []
    link_colors: list[str] = []

    for (source_index, target_index), value in sorted(user_section_links.items()):
        link_sources.append(source_index)
        link_targets.append(target_index)
        link_values.append(value)
        link_colors.append("rgba(37, 99, 235, 0.24)")
    for (source_index, target_index), value in sorted(section_target_links.items()):
        link_sources.append(source_index)
        link_targets.append(target_index)
        link_values.append(value)
        link_colors.append("rgba(22, 163, 74, 0.24)")

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=18,
                thickness=18,
                line=dict(color="rgba(15, 23, 42, 0.22)", width=0.6),
                label=node_labels,
                color=node_colors,
                x=node_x,
            ),
            link=dict(
                source=link_sources,
                target=link_targets,
                value=link_values,
                color=link_colors,
            ),
        )
    )
    fig.update_layout(
        margin=dict(l=20, r=20, t=20, b=20),
        height=min(max(460, 140 + len(node_labels) * 18), 900),
        font=dict(size=12),
    )
    return fig


def _render_access_usage_admin_dashboard(
    *,
    dashboard: dict[str, object],
    selected_user_id: str,
    selected_user_label: str,
    selected_user_email: str,
    usage_window_days: int,
    active_window_minutes: int,
    sankey_user_limit: int,
) -> None:
    summary = dict(dashboard.get("summary") or {})
    usage_label = f"{usage_window_days}d"
    active_label = f"{active_window_minutes}m"
    selected_user_usage_row: dict[str, object] = {}
    if selected_user_id:
        selected_user_usage_row = next(
            (
                dict(row)
                for row in list(dashboard.get("user_usage") or [])
                if str((row or {}).get("user_id") or "").strip() == selected_user_id
            ),
            {},
        )

    st.subheader("Usage")
    st.caption(
        "Section-level usage comes from the access event tracker. Detailed click trails only include the higher-signal actions we explicitly record."
    )
    if selected_user_id:
        st.caption(
            f"Filtered to {selected_user_label}. The detailed activity trail below only shows recorded usage behavior for this user."
        )

    usage_metrics = st.columns(6)
    if selected_user_id:
        usage_metrics[0].metric("User", selected_user_email or selected_user_label)
        usage_metrics[1].metric(f"Section Views ({usage_label})", int(summary.get("section_views_window") or 0))
        usage_metrics[2].metric(
            f"Distinct Sections ({usage_label})",
            int(selected_user_usage_row.get("distinct_section_count") or 0),
        )
        usage_metrics[3].metric(f"Successful Logins ({usage_label})", int(summary.get("login_success_window") or 0))
        usage_metrics[4].metric(f"Active Sessions ({active_label})", int(summary.get("active_sessions") or 0))
        usage_metrics[5].metric(
            "Last Activity",
            _format_access_admin_timestamp(selected_user_usage_row.get("last_activity_at")) or "n/a",
        )
    else:
        usage_metrics[0].metric("Total Users", int(summary.get("total_users") or 0))
        usage_metrics[1].metric(f"Active Users ({usage_label})", int(summary.get("active_users_window") or 0))
        usage_metrics[2].metric(f"Section Views ({usage_label})", int(summary.get("section_views_window") or 0))
        usage_metrics[3].metric(f"Successful Logins ({usage_label})", int(summary.get("login_success_window") or 0))
        usage_metrics[4].metric(f"Active Sessions ({active_label})", int(summary.get("active_sessions") or 0))
        usage_metrics[5].metric("Pending Invites", int(summary.get("pending_invites") or 0))

    section_usage = pd.DataFrame(dashboard.get("section_usage") or [])
    usage_sankey_rows = list(dashboard.get("usage_sankey") or [])
    st.subheader("Usage Flow" if not selected_user_id else "Selected User Usage Flow")
    if selected_user_id:
        st.caption(
            "This flow is limited to the selected user. Page-only views stop at the page node, while tracked item and feature clicks continue to the right."
        )
    else:
        st.caption(
            f"This flow is limited to the top {sankey_user_limit} active users in the selected usage window so the chart stays fast and readable."
        )
    usage_sankey_figure = _build_access_usage_sankey_figure(usage_sankey_rows)
    if usage_sankey_figure is None:
        st.info("Not enough page or item activity has been recorded to build the usage flow chart yet.")
    else:
        st.plotly_chart(usage_sankey_figure, use_container_width=True)

    st.subheader("Section Usage" if not selected_user_id else "Section Usage For Selected User")
    if section_usage.empty:
        st.info("No section usage events recorded yet.")
    else:
        section_usage["last_view_at"] = section_usage["last_view_at"].apply(_format_access_admin_timestamp)
        top_section_usage = section_usage.head(10).copy()
        section_chart = px.bar(
            top_section_usage,
            x="section_name",
            y="view_count",
            text="view_count",
            custom_data=["unique_user_count"],
        )
        section_chart.update_traces(
            hovertemplate="Section=%{x}<br>Views=%{y}<br>Users=%{customdata[0]}<extra></extra>"
        )
        section_chart.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="",
            yaxis_title="Views",
        )
        st.plotly_chart(section_chart, use_container_width=True)
        st.dataframe(
            section_usage[["section_name", "view_count", "unique_user_count", "last_view_at"]],
            use_container_width=True,
            hide_index=True,
        )

    user_usage = pd.DataFrame(dashboard.get("user_usage") or [])
    if not user_usage.empty and "role" in user_usage.columns and not selected_user_id:
        user_usage = user_usage[user_usage["role"].str.lower() != "admin"].copy()
    st.subheader("Who Is Using It" if not selected_user_id else "Selected User Overview")
    if user_usage.empty:
        st.info("No user usage rows are available yet.")
    else:
        for timestamp_col in ["last_login_at", "last_seen_at", "last_activity_at"]:
            if timestamp_col in user_usage.columns:
                user_usage[timestamp_col] = user_usage[timestamp_col].apply(_format_access_admin_timestamp)
        st.dataframe(
            user_usage[
                [
                    column
                    for column in [
                        "email",
                        "display_name",
                        "role",
                        "status",
                        "last_activity_at",
                        "top_section",
                        "section_view_count",
                        "distinct_section_count",
                        "active_session_count",
                        "open_session_count",
                        "last_seen_at",
                        "last_login_at",
                    ]
                    if column in user_usage.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    if not selected_user_id:
        return

    selected_user_targets = pd.DataFrame(dashboard.get("selected_user_targets") or [])
    st.subheader("Selected User Activity Targets")
    st.caption("This highlights the sections, bundles, tickers, and content links this user is actually opening.")
    if selected_user_targets.empty:
        st.info("No detailed activity targets have been recorded for this user yet.")
    else:
        if "last_event_at" in selected_user_targets.columns:
            selected_user_targets["last_event_at"] = selected_user_targets["last_event_at"].apply(_format_access_admin_timestamp)
        target_chart_rows = selected_user_targets.head(10).copy()
        target_chart = px.bar(
            target_chart_rows,
            x="target_label",
            y="event_count",
            color="target_type",
            text="event_count",
            custom_data=["last_event_at"],
        )
        target_chart.update_traces(
            hovertemplate="Target=%{x}<br>Events=%{y}<br>Last=%{customdata[0]}<extra></extra>"
        )
        target_chart.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="",
            yaxis_title="Events",
            legend_title="Target Type",
        )
        st.plotly_chart(target_chart, use_container_width=True)
        st.dataframe(
            selected_user_targets[
                [
                    column
                    for column in ["target_label", "target_type", "event_count", "last_event_at"]
                    if column in selected_user_targets.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    selected_user_activity = pd.DataFrame(dashboard.get("selected_user_activity") or [])
    st.subheader("Selected User Activity Trail")
    if "event_category" in selected_user_activity.columns:
        selected_user_activity = selected_user_activity[selected_user_activity["event_category"] == "usage"].copy()
    if selected_user_activity.empty:
        st.info("No selected-user usage trail is available yet.")
        return
    if "created_at" in selected_user_activity.columns:
        selected_user_activity["created_at"] = selected_user_activity["created_at"].apply(_format_access_admin_timestamp)
    if "user_agent" in selected_user_activity.columns:
        selected_user_activity["user_agent"] = selected_user_activity["user_agent"].apply(_short_user_agent)
    if "detail" in selected_user_activity.columns:
        selected_user_activity["detail_summary"] = selected_user_activity["detail"].apply(_format_access_admin_detail)
        selected_user_activity["surface"] = selected_user_activity["detail"].apply(
            lambda value: str(value.get("surface") or "") if isinstance(value, dict) else ""
        )
        selected_user_activity["source"] = selected_user_activity["detail"].apply(
            lambda value: str(value.get("source") or "") if isinstance(value, dict) else ""
        )
    st.dataframe(
        selected_user_activity[
            [
                column
                for column in [
                    "created_at",
                    "event_type",
                    "section_name",
                    "surface",
                    "target_type",
                    "target_label",
                    "source",
                    "ip_address",
                    "detail_summary",
                ]
                if column in selected_user_activity.columns
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_access_security_admin_dashboard(
    *,
    dashboard: dict[str, object],
    selected_user_id: str,
    selected_user_label: str,
    selected_user_email: str,
    security_window_days: int,
    active_window_minutes: int,
) -> None:
    summary = dict(dashboard.get("summary") or {})
    security_label = f"{security_window_days}d"
    active_label = f"{active_window_minutes}m"

    st.subheader("Security")
    st.caption(
        "Security covers login failures, account locks, password resets, live sessions, and Azure audit and diagnostic coverage."
    )
    if selected_user_id:
        st.caption(f"Filtered to {selected_user_label}.")

    security_metrics = st.columns(6)
    if selected_user_id:
        security_metrics[0].metric("User", selected_user_email or selected_user_label)
        security_metrics[1].metric(f"Failed Logins ({security_label})", int(summary.get("failed_logins_window") or 0))
        security_metrics[2].metric(f"Lock Events ({security_label})", int(summary.get("login_locks_window") or 0))
        security_metrics[3].metric(
            f"Reset Requests ({security_label})",
            int(summary.get("password_reset_requests_window") or 0),
        )
        security_metrics[4].metric(
            f"Admin Resets ({security_label})",
            int(summary.get("admin_password_resets_window") or 0),
        )
        security_metrics[5].metric(f"Unique IPs ({security_label})", int(summary.get("unique_ips_window") or 0))
    else:
        security_metrics[0].metric("Locked Users Now", int(summary.get("locked_users_now") or 0))
        security_metrics[1].metric(f"Failed Logins ({security_label})", int(summary.get("failed_logins_window") or 0))
        security_metrics[2].metric(f"Lock Events ({security_label})", int(summary.get("login_locks_window") or 0))
        security_metrics[3].metric(
            f"Reset Requests ({security_label})",
            int(summary.get("password_reset_requests_window") or 0),
        )
        security_metrics[4].metric(
            f"Admin Resets ({security_label})",
            int(summary.get("admin_password_resets_window") or 0),
        )
        security_metrics[5].metric(f"Unique IPs ({security_label})", int(summary.get("unique_ips_window") or 0))

    session_metrics = st.columns(3)
    session_metrics[0].metric(f"Active Sessions ({active_label})", int(summary.get("active_sessions") or 0))
    session_metrics[1].metric("Open Sessions", int(summary.get("open_sessions") or 0))
    session_metrics[2].metric("Active Users Now", int(summary.get("active_users_now") or 0))

    admin_usage = pd.DataFrame(dashboard.get("admin_usage") or [])
    st.subheader("Admin Usage")
    if admin_usage.empty:
        st.info("No admin usage recorded in this window.")
    else:
        admin_chart = px.bar(
            admin_usage,
            x="label",
            y="total_event_count",
            text="total_event_count",
            color="label",
            custom_data=["section_view_count", "other_event_count"],
        )
        admin_chart.update_traces(
            hovertemplate="Admin=%{x}<br>Total=%{y}<br>Section Views=%{customdata[0]}<br>Other=%{customdata[1]}<extra></extra>"
        )
        admin_chart.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="",
            yaxis_title="Events",
            showlegend=False,
        )
        st.plotly_chart(admin_chart, use_container_width=True)

    access_ips = pd.DataFrame(dashboard.get("access_ips") or [])
    st.subheader("Access IPs")
    if access_ips.empty:
        st.info("No access IP data recorded in this window.")
    else:
        if "last_seen_at" in access_ips.columns:
            access_ips["last_seen_at"] = access_ips["last_seen_at"].apply(_format_access_admin_timestamp)
        st.dataframe(
            access_ips[
                [
                    column
                    for column in [
                        "ip_address",
                        "event_count",
                        "unique_user_count",
                        "security_event_count",
                        "users",
                        "last_seen_at",
                    ]
                    if column in access_ips.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    cloud_security_status = dict(dashboard.get("cloud_security_status") or {})
    cloud_summary = dict(cloud_security_status.get("summary") or {})
    st.subheader("Cloud Audit Coverage")
    workspace_hint = str(cloud_security_status.get("expected_workspace_id") or "").strip()
    configured_resource_group = str(cloud_security_status.get("configured_resource_group") or "").strip()
    resolved_resource_group = str(cloud_security_status.get("resource_group") or "").strip()
    if workspace_hint:
        st.caption(f"Expected Log Analytics workspace: `{workspace_hint}`")
    if resolved_resource_group and configured_resource_group and resolved_resource_group != configured_resource_group:
        st.caption(
            f"Resolved resource group: `{resolved_resource_group}`. Configured hint: `{configured_resource_group}`."
        )
    error_text = str(cloud_security_status.get("error") or "").strip()
    if not bool(cloud_security_status.get("available")):
        st.warning(error_text or "Azure security observability status is unavailable.")
    else:
        coverage_metrics = st.columns(4)
        coverage_metrics[0].metric(
            "Healthy Resources",
            f"{int(cloud_summary.get('healthy_count') or 0)}/{int(cloud_summary.get('resource_count') or 0)}",
        )
        coverage_metrics[1].metric(
            "Audit Enabled",
            f"{int(cloud_summary.get('audit_enabled_count') or 0)}/{int(cloud_summary.get('audit_expected_count') or 0)}",
        )
        coverage_metrics[2].metric(
            "Diagnostics Enabled",
            f"{int(cloud_summary.get('diagnostics_enabled_count') or 0)}/{int(cloud_summary.get('diagnostics_expected_count') or 0)}",
        )
        coverage_metrics[3].metric(
            "Workspace Mismatches",
            int(cloud_summary.get("workspace_mismatch_count") or 0),
        )

        if int(cloud_summary.get("workspace_mismatch_count") or 0) > 0 or int(cloud_summary.get("error_count") or 0) > 0:
            st.warning("Some cloud audit or diagnostic resources are misconfigured or could not be inspected.")
        elif int(cloud_summary.get("healthy_count") or 0) == int(cloud_summary.get("resource_count") or 0):
            st.success("SQL auditing, SQL diagnostics, and Key Vault diagnostics are enabled on the tracked resources.")

        cloud_resources = pd.DataFrame(cloud_security_status.get("resources") or [])
        if cloud_resources.empty:
            st.info("No cloud security resources were resolved for this environment.")
        else:
            if "workspace_ids" in cloud_resources.columns:
                cloud_resources["workspace_ids"] = cloud_resources["workspace_ids"].apply(_format_access_admin_list)
            if "diagnostic_setting_names" in cloud_resources.columns:
                cloud_resources["diagnostic_setting_names"] = cloud_resources["diagnostic_setting_names"].apply(_format_access_admin_list)
            if "enabled_log_categories" in cloud_resources.columns:
                cloud_resources["enabled_log_categories"] = cloud_resources["enabled_log_categories"].apply(_format_access_admin_list)
            if "enabled_metric_categories" in cloud_resources.columns:
                cloud_resources["enabled_metric_categories"] = cloud_resources["enabled_metric_categories"].apply(_format_access_admin_list)
            st.dataframe(
                cloud_resources[
                    [
                        column
                        for column in [
                            "resource_type",
                            "resource_name",
                            "status",
                            "audit_enabled",
                            "diagnostics_enabled",
                            "workspace_status",
                            "diagnostic_setting_names",
                            "enabled_log_categories",
                            "enabled_metric_categories",
                            "workspace_ids",
                            "error",
                        ]
                        if column in cloud_resources.columns
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

    active_sessions = pd.DataFrame(dashboard.get("active_sessions") or [])
    st.subheader("Open Sessions" if not selected_user_id else "Open Sessions For Selected User")
    if active_sessions.empty:
        st.info("No open sessions found.")
    else:
        for timestamp_col in ["created_at", "last_seen_at", "expires_at"]:
            if timestamp_col in active_sessions.columns:
                active_sessions[timestamp_col] = active_sessions[timestamp_col].apply(_format_access_admin_timestamp)
        if "user_agent" in active_sessions.columns:
            active_sessions["user_agent"] = active_sessions["user_agent"].apply(_short_user_agent)
        st.dataframe(
            active_sessions[
                [
                    column
                    for column in [
                        "email",
                        "display_name",
                        "is_active_now",
                        "created_at",
                        "last_seen_at",
                        "expires_at",
                        "ip_address",
                        "user_agent",
                    ]
                    if column in active_sessions.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    security_events = pd.DataFrame(dashboard.get("recent_security_events") or [])
    st.subheader("Recent Security Events" if not selected_user_id else "Security Events For Selected User")
    if security_events.empty:
        st.info("No recent security events recorded for this window.")
    else:
        if "created_at" in security_events.columns:
            security_events["created_at"] = security_events["created_at"].apply(_format_access_admin_timestamp)
        if "user_agent" in security_events.columns:
            security_events["user_agent"] = security_events["user_agent"].apply(_short_user_agent)
        if "detail" in security_events.columns:
            security_events["detail_summary"] = security_events["detail"].apply(_format_access_admin_detail)
        st.dataframe(
            security_events[
                [
                    column
                    for column in [
                        "created_at",
                        "event_type",
                        "user_email",
                        "email",
                        "ip_address",
                        "user_agent",
                        "detail_summary",
                    ]
                    if column in security_events.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )


def _render_access_pending_invite_card(invite: dict[str, object], *, current_user: auth_service.UserContext) -> None:
    invite_id = str(invite.get("id") or "").strip()
    if not invite_id:
        return

    invite_email = str(invite.get("email") or "").strip() or "Unknown email"
    invite_role = str(invite.get("role") or "investor").strip().lower() or "investor"
    invite_status = str(invite.get("status") or "pending").strip().lower() or "pending"
    portfolio_slug = str(invite.get("portfolio_slug") or "").strip()
    share_value = invite.get("proposed_share_fraction")
    share_label = _format_access_admin_share_percent(share_value)
    expires_label = _format_pending_invite_expires(invite.get("expires_at")) or "n/a"
    share_key = f"_access_pending_invite_share_pct_{invite_id}"

    try:
        current_share_pct = max(float(share_value or 0.0), 0.0) * 100.0
    except Exception:
        current_share_pct = 0.0
    if share_key not in st.session_state:
        st.session_state[share_key] = current_share_pct

    detail_parts = [f"Role: {invite_role.title()}"]
    if invite_role == "investor":
        detail_parts.append(f"Stake: {share_label}")
    if portfolio_slug:
        detail_parts.append(f"Portfolio: {portfolio_slug}")
    if invite_status != "pending":
        detail_parts.append(f"Status: {invite_status.title()}")
    detail_parts.append(f"Expires: {expires_label}")
    detail_parts.append(f"Invite ID: {invite_id[:8]}")

    with st.container(border=True):
        st.markdown(f"**{invite_email}**")
        st.caption(" | ".join(detail_parts))

        action_cols = st.columns([1.8, 1.0, 1.0, 1.0])
        if invite_role == "investor":
            action_cols[0].number_input(
                "Stake %",
                min_value=0.0,
                max_value=100.0,
                step=0.25,
                key=share_key,
            )
            if action_cols[1].button(
                "Save Stake",
                key=f"_access_pending_invite_save_{invite_id}",
                use_container_width=True,
            ):
                update_result = auth_service.update_pending_invite(
                    invite_id=invite_id,
                    share_fraction=float(st.session_state.get(share_key) or 0.0) / 100.0,
                    requested_by=current_user,
                )
                if update_result.get("ok"):
                    updated_invite = update_result.get("invite") if isinstance(update_result.get("invite"), dict) else {}
                    st.session_state[share_key] = max(
                        float(updated_invite.get("proposed_share_fraction") or 0.0) * 100.0,
                        0.0,
                    )
                    _queue_access_pending_invite_notice(
                        level="success",
                        message=str(update_result.get("message") or "Pending invite updated."),
                    )
                else:
                    _queue_access_pending_invite_notice(
                        level="error",
                        message=str(update_result.get("message") or "Unable to update pending invite."),
                    )
        else:
            action_cols[0].caption("Stake editing is only used for investor invites.")

        if action_cols[2].button(
            "Resend Invite",
            key=f"_access_pending_invite_resend_{invite_id}",
            use_container_width=True,
        ):
            resend_result = auth_service.resend_pending_invite(
                invite_id=invite_id,
                requested_by=current_user,
            )
            if resend_result.get("ok"):
                _queue_access_pending_invite_notice(
                    level="success",
                    message=str(resend_result.get("message") or "Invite resent."),
                    detail=str(resend_result.get("email_message") or ""),
                    code="" if resend_result.get("email_sent") else str(resend_result.get("invite_url") or ""),
                )
            else:
                _queue_access_pending_invite_notice(
                    level="error",
                    message=str(resend_result.get("message") or "Unable to resend invite."),
                )

        if action_cols[3].button(
            "Delete Invite",
            key=f"_access_pending_invite_delete_{invite_id}",
            use_container_width=True,
        ):
            delete_result = auth_service.delete_pending_invite(
                invite_id=invite_id,
                requested_by=current_user,
            )
            if delete_result.get("ok"):
                st.session_state.pop(share_key, None)
                _queue_access_pending_invite_notice(
                    level="success",
                    message=str(delete_result.get("message") or "Pending invite deleted."),
                )
            else:
                _queue_access_pending_invite_notice(
                    level="error",
                    message=str(delete_result.get("message") or "Unable to delete pending invite."),
                )


def _environment_label(app_track: str) -> str:
    normalized = str(app_track or "").strip().lower()
    if normalized in {"prod", "production"}:
        return "Live"
    if normalized in {"dev", "development"}:
        return "Preview"
    if normalized:
        return normalized.title()
    return "Local"


def _ensure_app_shell_styles() -> None:
    st.session_state["_sn_app_shell_styles_version"] = _APP_SHELL_STYLE_VERSION
    st.markdown(
        """
        <style>
        :root {
            --sn-bg-0: #0a0d12;
            --sn-bg-1: #11161d;
            --sn-bg-2: #171d25;
            --sn-card: rgba(17, 22, 29, 0.94);
            --sn-card-strong: rgba(22, 28, 37, 0.98);
            --sn-line: rgba(148, 163, 184, 0.12);
            --sn-line-strong: rgba(148, 163, 184, 0.22);
            --sn-ink: #f3f5f7;
            --sn-muted: #9aa4b2;
            --sn-muted-strong: #c7d0db;
            --sn-accent: #a8b8c9;
            --sn-accent-strong: #d8e1ea;
            --sn-shadow: 0 18px 42px rgba(5, 8, 12, 0.22);
            --sn-shadow-soft: 0 12px 26px rgba(5, 8, 12, 0.16);
        }
        .stApp {
            background: linear-gradient(180deg, var(--sn-bg-0) 0%, var(--sn-bg-1) 100%);
            color: var(--sn-ink);
            font-family: "Avenir Next", "Plus Jakarta Sans", "IBM Plex Sans", "Segoe UI", sans-serif;
        }
        #MainMenu,
        footer,
        [data-testid="stMainMenu"],
        [data-testid="stToolbar"],
        [data-testid="stAppToolbar"],
        [data-testid="stHeader"],
        [data-testid="stAppHeader"] {
            display: none;
        }
        .block-container {
            padding-top: 0.8rem;
            padding-bottom: 2.5rem;
            max-width: 1440px;
        }
        h1, h2, h3 {
            color: var(--sn-ink);
            font-family: "Avenir Next", "Plus Jakarta Sans", "IBM Plex Sans", "Segoe UI", sans-serif;
            letter-spacing: -0.035em;
            font-weight: 650;
        }
        p, label, .stCaption {
            color: var(--sn-muted);
        }
        strong, b {
            color: var(--sn-ink);
        }
        .stApp a {
            color: var(--sn-accent-strong);
            font-weight: 600;
            text-decoration: none;
        }
        .stApp a:hover {
            text-decoration: underline;
        }
        .sn-trading-signal-row {
            display: grid;
            grid-template-columns: minmax(7.2rem, 1fr) minmax(7rem, 2fr) minmax(4.4rem, auto);
            gap: 0.65rem;
            align-items: center;
            margin: 0.42rem 0;
        }
        .sn-trading-signal-label {
            color: var(--sn-muted-strong);
            font-size: 0.78rem;
            line-height: 1.2;
        }
        .sn-trading-signal-track {
            height: 0.42rem;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(148, 163, 184, 0.15);
        }
        .sn-trading-signal-fill {
            height: 100%;
            border-radius: 999px;
            background: #8fb7e8;
        }
        .sn-trading-signal-value {
            color: var(--sn-ink);
            font-size: 0.78rem;
            text-align: right;
            white-space: nowrap;
        }
        .sn-trading-checklist {
            display: flex;
            flex-wrap: wrap;
            gap: 0.42rem;
            margin: 0.25rem 0 0.55rem 0;
        }
        .sn-trading-check {
            display: inline-flex;
            align-items: center;
            min-height: 1.55rem;
            padding: 0.22rem 0.52rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            color: var(--sn-muted-strong);
            background: rgba(148, 163, 184, 0.08);
            font-size: 0.76rem;
            line-height: 1.15;
        }
        [data-testid="stSidebar"] {
            background: #0f1319;
            border-right: 1px solid var(--sn-line);
        }
        [data-testid="stSidebar"] * {
            color: var(--sn-ink);
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stCaption {
            color: var(--sn-muted-strong);
        }
        .sn-sidebar-brand {
            margin: 0 0 1rem 0;
            padding: 1rem 1rem 0.95rem 1rem;
            border-radius: 0.95rem;
            border: 1px solid var(--sn-line-strong);
            background: var(--sn-card-strong);
            box-shadow: var(--sn-shadow-soft);
        }
        .sn-sidebar-brand-text {
            display: flex;
            flex-direction: column;
            gap: 0.12rem;
            width: 100%;
        }
        .sn-sidebar-brand-signoff {
            display: flex;
            justify-content: flex-end;
            margin-top: 0.42rem;
            padding-top: 0.48rem;
            border-top: 1px solid var(--sn-line);
            width: 100%;
        }
        .sn-sidebar-editorial-link {
            width: 100%;
            margin: 0 0 0.75rem 0;
        }
        .sn-sidebar-editorial-link-anchor {
            display: inline-flex;
            align-items: center;
            gap: 0.58rem;
            width: 100%;
            padding: 0.56rem 0.8rem;
            border-radius: 0.72rem;
            border: 1px solid var(--sn-line-strong);
            background: #1a2029;
            color: var(--sn-ink) !important;
            text-decoration: none !important;
            font-size: 0.84rem;
            font-weight: 650;
            line-height: 1.1;
            box-shadow: none;
            transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
        }
        .sn-sidebar-editorial-link-anchor:hover {
            border-color: rgba(216, 225, 234, 0.28);
            background: #202733;
            color: var(--sn-ink) !important;
            text-decoration: none !important;
        }
        .sn-sidebar-editorial-link-icon {
            width: 1.02rem;
            height: 1.02rem;
            flex: 0 0 1.02rem;
            display: inline-block;
            object-fit: contain;
        }
        .sn-sidebar-editorial-link-text {
            display: inline-flex;
            align-items: center;
            min-width: 0;
        }
        .sn-sidebar-kicker {
            margin-bottom: 0.7rem;
            color: var(--sn-accent);
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.2em;
            text-transform: uppercase;
        }
        .sn-sidebar-logo-link {
            display: block;
            text-decoration: none;
            opacity: 0.9;
            transition: opacity 0.15s;
        }
        .sn-sidebar-logo-link:hover {
            opacity: 1;
        }
        .sn-sidebar-logo {
            width: 100%;
            max-width: 15.5rem;
            margin: 0 0 0.64rem 0;
        }
        .sn-sidebar-logo img,
        .sn-sidebar-logo svg {
            width: 100%;
            height: auto;
            display: block;
        }
        .sn-sidebar-brand-title {
            color: var(--sn-ink);
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
            font-size: 1.28rem;
            font-weight: 700;
            line-height: 1.05;
        }
        .sn-sidebar-brand-subtitle {
            color: var(--sn-muted);
            font-family: "Bodoni Moda", "Didot", "Bodoni 72", "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            line-height: 1.25;
            text-align: right;
            max-width: max-content;
        }
        .sn-sidebar-wordmark {
            margin-bottom: 0.35rem;
            color: var(--sn-ink);
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
            font-size: 1.55rem;
            font-weight: 700;
            line-height: 1.05;
        }
        .sn-sidebar-subtitle {
            margin-bottom: 0.8rem;
            color: var(--sn-muted);
            font-size: 0.82rem;
            line-height: 1.45;
        }
        .sn-sidebar-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
        }
        .sn-sidebar-pill {
            display: inline-flex;
            align-items: center;
            padding: 0.24rem 0.58rem;
            border-radius: 0.55rem;
            border: 1px solid var(--sn-line);
            background: rgba(255, 255, 255, 0.03);
            color: var(--sn-muted-strong);
            font-size: 0.72rem;
            font-weight: 600;
        }
        .sn-page-intro {
            margin-bottom: 0.9rem;
            padding: 1.15rem 1.25rem 1.05rem 1.25rem;
            border-radius: 0.95rem;
            border: 1px solid var(--sn-line);
            background: var(--sn-card);
            box-shadow: var(--sn-shadow-soft);
        }
        .sn-page-kicker {
            margin-bottom: 0.35rem;
            color: var(--sn-accent);
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 0.2em;
            text-transform: uppercase;
        }
        .sn-page-title {
            margin-bottom: 0.38rem;
            color: var(--sn-ink);
            font-size: clamp(1.75rem, 2vw, 2.35rem);
            font-weight: 680;
            line-height: 1.02;
        }
        .sn-page-text {
            max-width: 58rem;
            color: var(--sn-muted-strong);
            font-size: 0.94rem;
            line-height: 1.6;
        }
        .sn-rail-title {
            margin: 0;
            color: var(--sn-ink);
            font-size: 1.08rem;
            font-weight: 675;
            line-height: 1.2;
            text-align: left;
        }
        .sn-rail-caption {
            margin-top: 0.22rem;
            color: var(--sn-muted);
            font-size: 0.8rem;
            line-height: 1.4;
            text-align: left;
        }
        [class*="st-key-homepage_v2_surface_idle_"] button,
        [class*="st-key-homepage_v2_surface_selected_"] button,
        [class*="st-key-homepage_exp_surface_idle_"] button,
        [class*="st-key-homepage_exp_surface_selected_"] button {
            width: 100%;
            align-items: flex-start;
            justify-content: flex-start;
            text-align: left;
            padding: 0.18rem 0.22rem 0.24rem 0.22rem;
            min-height: 0;
            border-radius: 0.62rem;
            border: 1px solid transparent;
            background: transparent;
            box-shadow: none;
            color: var(--sn-ink);
            line-height: 1.18;
        }
        [class*="st-key-homepage_v2_surface_idle_"] button > div,
        [class*="st-key-homepage_v2_surface_selected_"] button > div,
        [class*="st-key-homepage_exp_surface_idle_"] button > div,
        [class*="st-key-homepage_exp_surface_selected_"] button > div {
            width: 100%;
            justify-content: flex-start;
            text-align: left;
        }
        [class*="st-key-homepage_v2_surface_idle_"] button:hover,
        [class*="st-key-homepage_exp_surface_idle_"] button:hover {
            border-color: rgba(216, 225, 234, 0.2);
            background: rgba(168, 184, 201, 0.06);
            color: var(--sn-accent-strong);
        }
        [class*="st-key-homepage_v2_surface_selected_"] button,
        [class*="st-key-homepage_exp_surface_selected_"] button {
            border-color: rgba(168, 184, 201, 0.24);
            background: rgba(168, 184, 201, 0.08);
            color: var(--sn-accent-strong);
        }
        [class*="st-key-homepage_v2_surface_selected_"] button:hover,
        [class*="st-key-homepage_exp_surface_selected_"] button:hover {
            border-color: rgba(168, 184, 201, 0.28);
            background: rgba(168, 184, 201, 0.1);
            color: var(--sn-accent-strong);
        }
        [class*="st-key-homepage_v2_surface_idle_"] button p,
        [class*="st-key-homepage_v2_surface_selected_"] button p,
        [class*="st-key-homepage_exp_surface_idle_"] button p,
        [class*="st-key-homepage_exp_surface_selected_"] button p {
            margin: 0;
            width: 100%;
            font-size: 1.02rem;
            font-weight: 650;
            line-height: 1.18;
            text-align: left;
        }
        [class*="st-key-homepage_exp_surface_selected_"] button {
            box-shadow: inset 2px 0 0 rgba(216, 225, 234, 0.72);
        }
        [class*="st-key-homepage_exp_surface_selected_company_"] button {
            border-color: rgba(168, 184, 201, 0.3);
            background: rgba(168, 184, 201, 0.11);
            box-shadow: inset 3px 0 0 rgba(216, 225, 234, 0.82);
        }
        [class*="st-key-homepage_exp_surface_selected_company_"] button:hover {
            border-color: rgba(168, 184, 201, 0.34);
            background: rgba(168, 184, 201, 0.13);
        }
        [class*="st-key-ticker_preview_surface_idle_"] button,
        [class*="st-key-ticker_preview_surface_selected_"] button {
            width: 100%;
            align-items: flex-start;
            justify-content: flex-start;
            text-align: left;
            padding: 0.14rem 0.22rem 0.18rem 0.22rem;
            min-height: 0;
            border-radius: 0.58rem;
            border: 1px solid transparent;
            background: transparent;
            box-shadow: none;
            color: var(--sn-ink);
            line-height: 1.16;
        }
        [class*="st-key-ticker_preview_surface_idle_"] button > div,
        [class*="st-key-ticker_preview_surface_selected_"] button > div {
            width: 100%;
            justify-content: flex-start;
            text-align: left;
        }
        [class*="st-key-ticker_preview_surface_idle_"] button:hover {
            border-color: rgba(216, 225, 234, 0.18);
            background: rgba(168, 184, 201, 0.05);
            color: var(--sn-accent-strong);
        }
        [class*="st-key-ticker_preview_surface_selected_"] button {
            border-color: rgba(168, 184, 201, 0.22);
            background: rgba(168, 184, 201, 0.08);
            color: var(--sn-accent-strong);
            box-shadow: inset 2px 0 0 rgba(216, 225, 234, 0.72);
        }
        [class*="st-key-ticker_preview_surface_selected_"] button:hover {
            border-color: rgba(168, 184, 201, 0.26);
            background: rgba(168, 184, 201, 0.1);
            color: var(--sn-accent-strong);
        }
        [class*="st-key-ticker_preview_surface_idle_"] button p,
        [class*="st-key-ticker_preview_surface_selected_"] button p {
            margin: 0;
            width: 100%;
            font-size: 0.96rem;
            font-weight: 620;
            line-height: 1.16;
            text-align: left;
        }
        [class*="st-key-homepage_exp_close_"] button {
            min-height: 2rem;
            height: 2rem;
            padding: 0;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.03);
        }
        [class*="st-key-homepage_exp_close_"] button:hover {
            background: rgba(255, 255, 255, 0.06);
        }
        div[data-testid="stMetric"] {
            padding: 0.9rem 1rem;
            border-radius: 0.9rem;
            border: 1px solid var(--sn-line);
            background: var(--sn-card);
            box-shadow: var(--sn-shadow-soft);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            border-radius: 1rem;
            border: 1px solid var(--sn-line);
            background: var(--sn-card);
            box-shadow: var(--sn-shadow-soft);
        }
        .stButton > button,
        .stFormSubmitButton > button {
            padding: 0.56rem 0.96rem;
            border-radius: 0.72rem;
            border: 1px solid var(--sn-line-strong);
            background: #1a2029;
            color: var(--sn-ink);
            font-weight: 650;
            box-shadow: none;
            transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
        }
        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            border-color: rgba(216, 225, 234, 0.28);
            background: #202733;
        }
        div[data-baseweb="tab-list"] {
            gap: 0.45rem;
            padding: 0.2rem;
            border-radius: 0.95rem;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--sn-line);
        }
        button[data-baseweb="tab"] {
            padding: 0.38rem 0.85rem;
            border-radius: 0.72rem;
            border: 1px solid transparent;
            background: transparent;
            color: var(--sn-muted-strong);
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            border-color: var(--sn-line);
            background: var(--sn-card-strong);
            color: var(--sn-ink);
        }
        [data-testid="stExpander"] details {
            border-radius: 0.95rem;
            border: 1px solid var(--sn-line);
            background: var(--sn-card);
        }
        /* Sidebar replay date picker — compact, matches nav feel */
        [class*="st-key-homepage_replay_date"] {
            margin: 0.3rem 0 0.1rem 0;
        }
        [class*="st-key-homepage_replay_date"] input {
            font-size: 0.82rem !important;
            padding: 0.3rem 0.5rem !important;
            min-height: 0 !important;
        }
        /* Vertical nav tabs */
        .sn-nav-label {
            margin: 0.6rem 0 0.3rem 0;
            padding: 0 0.1rem;
            color: var(--sn-accent) !important;
            font-size: 0.62rem;
            font-weight: 700;
            letter-spacing: 0.18em;
            text-transform: uppercase;
        }
        [class*="st-key-sn_nav_"] {
            margin-bottom: 0 !important;
        }
        [class*="st-key-sn_nav_"] button {
            width: 100%;
            justify-content: flex-start;
            text-align: left;
            padding: 0.44rem 0.65rem !important;
            border: none !important;
            background: transparent !important;
            color: var(--sn-muted-strong) !important;
            font-size: 0.87rem;
            font-weight: 500;
            border-radius: 0.5rem;
            min-height: 0;
            box-shadow: none !important;
            line-height: 1.3;
        }
        [class*="st-key-sn_nav_"] button > div {
            justify-content: flex-start;
            text-align: left;
        }
        [class*="st-key-sn_nav_"] button p {
            font-size: 0.87rem;
            text-align: left;
        }
        [class*="st-key-sn_nav_"] button:hover {
            background: rgba(168, 184, 201, 0.07) !important;
            color: var(--sn-ink) !important;
        }
        [class*="st-key-sn_nav_active_"] button {
            background: rgba(168, 184, 201, 0.1) !important;
            color: var(--sn-ink) !important;
            font-weight: 650 !important;
        }
        [class*="st-key-sn_nav_active_"] button:hover {
            background: rgba(168, 184, 201, 0.12) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _sidebar_editorial_icon_svg(icon_name: str) -> str:
    if str(icon_name or "").strip().lower() != "substack":
        return ""
    return _inline_image_markup(
        APP_SUBSTACK_ICON_PATH,
        alt_text="Substack logo",
        css_class="sn-sidebar-editorial-link-icon",
    )


def _render_sidebar_editorial_links(*, placement: str = "sidebar_brand") -> None:
    editorial_markup_parts: list[str] = []
    for link in homepage_v2_editorial_links(placement=placement):
        editorial_url = str(link.get("url") or "").strip()
        editorial_label = str(link.get("button_label") or link.get("label") or "").strip()
        editorial_icon = _sidebar_editorial_icon_svg(str(link.get("icon_name") or "").strip())
        if not editorial_url or not editorial_label:
            continue
        editorial_markup_parts.append(
            "<div class='sn-sidebar-editorial-link'>"
            f"<a class='sn-sidebar-editorial-link-anchor' href='{html.escape(editorial_url)}' target='_blank' rel='noopener noreferrer'>"
            f"{editorial_icon}"
            f"<span class='sn-sidebar-editorial-link-text'>{html.escape(editorial_label)}</span>"
            "</a>"
            "</div>"
        )
    if editorial_markup_parts:
        st.markdown("".join(editorial_markup_parts), unsafe_allow_html=True)


def _render_sidebar_brand_panel() -> None:

    logo_markup = _inline_image_markup(
        APP_SIDEBAR_LOGO_PATH,
        alt_text=f"{APP_BRAND_NAME} logo",
        css_class="",
    )
    has_logo = bool(logo_markup)
    brand_markup = (
        f"<a href='?nav=home' class='sn-sidebar-logo-link'><div class='sn-sidebar-logo'>{logo_markup}</div></a>"
        if logo_markup
        else ""
    )
    title_markup = (
        ""
        if has_logo
        else f"<div class='sn-sidebar-brand-title'>{html.escape(APP_BRAND_NAME)}</div>"
    )
    st.markdown(
        (
            "<div class='sn-sidebar-brand'>"
            f"{brand_markup}"
            "<div class='sn-sidebar-brand-text'>"
            f"{title_markup}"
            "<div class='sn-sidebar-brand-signoff'>"
            f"<div class='sn-sidebar-brand-subtitle'>by {html.escape(APP_BRAND_KICKER)}</div>"
            "</div>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_page_intro(kicker: str, title: str, body: str) -> None:
    st.markdown(
        (
            "<div class='sn-page-intro'>"
            f"<div class='sn-page-kicker'>{html.escape(kicker)}</div>"
            f"<div class='sn-page-title'>{html.escape(title)}</div>"
            f"<div class='sn-page-text'>{html.escape(body)}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


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



def _to_float(payload: dict, key: str) -> float:
    try:
        return float(payload.get(key, 0.0))
    except Exception:
        return 0.0



def _make_api(cfg: AppConfig) -> AlpacaAPI:
    return AlpacaAPI(cfg)


def _presentation_layer_only() -> bool:
    return presentation_layer_only_enabled(os.getenv("APP_PRESENTATION_LAYER_ONLY"))


def _data_access_layer(cfg: AppConfig | None = None, fred_api_key: str | None = None) -> DataAccessLayer:
    return DataAccessLayer(cfg=cfg, fred_api_key=fred_api_key, materialized_only=_presentation_layer_only())


def _alpaca_cache_scope(cfg: AppConfig) -> str:
    trading_scope = cfg.alpaca_trading_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    data_scope = cfg.alpaca_data_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    account_scope = dataset_scope("acct", cfg.alpaca_api_key)
    return f"{trading_scope}__{data_scope}__{account_scope}"


def _fred_cache_scope(api_key: str) -> str:
    return dataset_scope("fred", api_key)


def _render_connection_issue(summary: str, *, details: str | None = None, setup_code: str | None = None) -> None:
    st.error(summary)
    if details:
        st.caption(details)
    if setup_code:
        st.code(setup_code, language="bash")


def _auth_enabled() -> bool:
    raw = (os.getenv("DASHBOARD_AUTH_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _current_user_context() -> auth_service.UserContext | None:
    return auth_service.UserContext.from_dict(st.session_state.get("_ui_user_context"))


def _store_user_context(context: auth_service.UserContext | None) -> None:
    st.session_state["_ui_user_context"] = context.to_dict() if context is not None else None


dashboard_loaders.configure_dashboard_loaders(
    current_user_context_provider=_current_user_context,
    data_access_layer_factory=_data_access_layer,
    presentation_layer_only_provider=_presentation_layer_only,
)

_load_account_cached = dashboard_loaders._load_account_cached
_load_positions_cached = dashboard_loaders._load_positions_cached
_load_timeseries_cached = dashboard_loaders._load_timeseries_cached
_load_portfolio_performance_cached = dashboard_loaders._load_portfolio_performance_cached
_load_holding_roc_cached = dashboard_loaders._load_holding_roc_cached
_scan_daily_movers_cached = dashboard_loaders._scan_daily_movers_cached
_scan_momentum_profiles_cached = dashboard_loaders._scan_momentum_profiles_cached
_load_market_opportunity_feed_cached = dashboard_loaders._load_market_opportunity_feed_cached
_load_correlation_phase_shift_cached = dashboard_loaders._load_correlation_phase_shift_cached
_load_commodity_regime_cached = dashboard_loaders._load_commodity_regime_cached
_load_price_history_cached = dashboard_loaders._load_price_history_cached
_load_technical_signal_history_cached = dashboard_loaders._load_technical_signal_history_cached
_load_technical_signal_summary_cached = dashboard_loaders._load_technical_signal_summary_cached
_load_forecast_next_week_cached = dashboard_loaders._load_forecast_next_week_cached
_load_option_chain_cached = dashboard_loaders._load_option_chain_cached
_load_option_surface_cached = dashboard_loaders._load_option_surface_cached
_load_option_candidates_cached = dashboard_loaders._load_option_candidates_cached
_load_quarterly_fundamentals_cached = dashboard_loaders._load_quarterly_fundamentals_cached
_load_asset_metadata_cached = dashboard_loaders._load_asset_metadata_cached
_load_public_price_history_cached = dashboard_loaders._load_public_price_history_cached
_load_ticker_snapshot_profile = dashboard_loaders._load_ticker_snapshot_profile
_load_recent_news_cached = dashboard_loaders._load_recent_news_cached
_load_attention_context_cached = dashboard_loaders._load_attention_context_cached
_load_attention_ticker_snapshot_map_cached = dashboard_loaders._load_attention_ticker_snapshot_map_cached
_load_attention_ticker_snapshot_cached = dashboard_loaders._load_attention_ticker_snapshot_cached
_load_attention_ticker_background_cached = dashboard_loaders._load_attention_ticker_background_cached
_load_attention_home_1d_cached = dashboard_loaders._load_attention_home_1d_cached
_load_attention_research_bundle_cached = dashboard_loaders._load_attention_research_bundle_cached
_safe_load_attention_research_bundle_cached = dashboard_loaders._safe_load_attention_research_bundle_cached
_load_fred_dashboard_cached = dashboard_loaders._load_fred_dashboard_cached
_load_attention_feed_cached = dashboard_loaders._load_attention_feed_cached
_load_attention_rollups_cached = dashboard_loaders._load_attention_rollups_cached
_load_attention_feed_brief_cached = dashboard_loaders._load_attention_feed_brief_cached
_load_page_agentic_summary_cached = dashboard_loaders._load_page_agentic_summary_cached
_build_trading_agent_suggestions_cached = dashboard_loaders._build_trading_agent_suggestions_cached

_attention_event_key = attention_content._attention_event_key
_clean_attention_text = attention_content._clean_attention_text
_raw_attention_text = attention_content._raw_attention_text
_attention_evidence_display_text = attention_content._attention_evidence_display_text
_attention_story_text = attention_content._attention_story_text
_headline_items_from_news_payload = attention_content._headline_items_from_news_payload
_build_attention_brief_input = attention_content._build_attention_brief_input
_load_attention_brief_payloads = attention_content._load_attention_brief_payloads
_build_attention_micro_chart = attention_content._build_attention_micro_chart
_load_attention_news_payloads = attention_content._load_attention_news_payloads
_load_attention_context_payloads = attention_content._load_attention_context_payloads
_json_ready = attention_content._json_ready
_build_homepage_v2_event_record = attention_content._build_homepage_v2_event_record
_homepage_v2_item_summary = attention_content._homepage_v2_item_summary


def _current_user_share_fraction() -> float:
    context = _current_user_context()
    if context is None:
        return 1.0
    return max(float(context.share_fraction or 0.0), 0.0)


def _current_user_can_view_full_portfolio() -> bool:
    context = _current_user_context()
    if context is None:
        return True
    return bool(context.can_view_full_portfolio)


def _current_user_is_admin() -> bool:
    context = _current_user_context()
    return bool(context.is_admin) if context is not None else False


def _format_access_admin_timestamp(value: object) -> str:
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


def _format_access_admin_detail(detail: object) -> str:
    if not isinstance(detail, dict):
        return ""
    ignored_keys = {"target_id", "target_label", "target_type", "target_url", "headline"}
    preferred_keys = [
        "surface",
        "source",
        "symbol",
        "published_at",
        "reason",
        "failed_login_count",
        "locked_until",
        "portfolio_slug",
        "role",
        "app_track",
    ]
    parts: list[str] = []
    ordered_items: list[tuple[str, object]] = []
    for key in preferred_keys:
        if key in detail:
            ordered_items.append((key, detail.get(key)))
    for key, value in detail.items():
        if key in preferred_keys:
            continue
        ordered_items.append((str(key), value))
    for key, value in ordered_items:
        if key in ignored_keys:
            continue
        if value in (None, "", [], {}):
            continue
        if isinstance(value, float):
            rendered = f"{value:.4f}".rstrip("0").rstrip(".")
        elif isinstance(value, list):
            rendered = ", ".join(str(item) for item in value[:4] if str(item).strip())
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return " | ".join(parts[:4])


def _short_user_agent(user_agent: object, *, max_len: int = 72) -> str:
    text = str(user_agent or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _format_access_admin_list(values: object, *, max_items: int = 4) -> str:
    if not isinstance(values, list):
        return ""
    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return ""
    rendered = ", ".join(items[:max_items])
    if len(items) > max_items:
        rendered += f" (+{len(items) - max_items})"
    return rendered


def _current_workspace_section_name() -> str:
    return _normalize_workspace_section(st.session_state.get("workspace_section"))


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


def _record_workspace_section_view(
    *,
    section_name: str,
    current_user: auth_service.UserContext | None,
    app_track: str = "",
) -> None:
    if st.session_state.get("_ui_auth_mode") != "database":
        return
    if not isinstance(current_user, auth_service.UserContext):
        return

    normalized_section = _normalize_workspace_section(section_name)
    session_token = str(st.session_state.get("_ui_auth_session_id") or "")
    signature = f"{session_token}:{normalized_section}"
    if signature == str(st.session_state.get("_ui_last_recorded_section_view") or ""):
        return

    _log_event("section_selected", section=normalized_section)
    auth_service.record_access_event(
        event_type="section_view",
        event_category="usage",
        user=current_user,
        section_name=normalized_section,
        session_token=session_token,
        ip_address=_request_ip_address(),
        user_agent=_request_user_agent(),
        detail={"app_track": app_track or "unknown"},
    )
    st.session_state["_ui_last_recorded_section_view"] = signature


def _section_options() -> list[str]:
    options = list(BASE_SECTION_OPTIONS)
    if _current_user_is_admin():
        options.insert(2, HOME_EXP_SECTION)
        options.append(TRADING_AGENT_SECTION)
        options.append(ADMIN_SECTION)
    return options


def _normalize_workspace_section(section_name: object) -> str:
    normalized = str(section_name or "").strip()
    alias_map = {
        "Homepage - v2": "Home",
        "Homepage Exp": HOME_EXP_SECTION,
        "Home Experimental": HOME_EXP_SECTION,
        "Daily Market Overview": HOME_EXP_SECTION,
        "Agentic Omnibar": AGENTIC_OMNIBAR_SECTION,
        "Agentic Ombibar": AGENTIC_OMNIBAR_SECTION,
        "Portfolio Overview": PORTFOLIO_SECTION,
        "Performance": PORTFOLIO_PERFORMANCE_SECTION,
        "Market Opportunity": MARKET_EXPLORER_SECTION,
        "FRED Macro": BROAD_ECONOMY_SECTION,
        "Trading Experiment": TRADING_AGENT_SECTION,
        "Access Admin": ADMIN_SECTION,
        "Pipeline Jobs": ADMIN_SECTION,
    }
    if normalized in alias_map:
        return alias_map[normalized]
    return normalized


def _query_param_value(name: str) -> str:
    try:
        raw = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(raw, list):
        return str(raw[0] or "").strip() if raw else ""
    return str(raw or "").strip()


def _clear_auth_query_params() -> None:
    try:
        params = st.query_params
        for key in ["invite_token", "reset_token"]:
            if key in params:
                del params[key]
    except Exception:
        return


def _auth_action_query_param_signature() -> str:
    invite_token = _query_param_value("invite_token")
    reset_token = _query_param_value("reset_token")
    if not invite_token and not reset_token:
        return ""
    return f"invite:{invite_token}|reset:{reset_token}"


def _auth_action_query_param_present() -> bool:
    return bool(_auth_action_query_param_signature())


def _clear_query_params(*keys: str) -> None:
    try:
        params = st.query_params
        for key in keys:
            if key in params:
                del params[key]
    except Exception:
        return


def _consume_cross_page_inspector_query_params() -> None:
    inspect_ticker = _query_param_value("inspect_ticker").upper().strip()
    inspect_view = _query_param_value("inspect_view").strip().lower()
    if not inspect_ticker:
        return
    if inspect_view == "home":
        st.session_state["_pending_workspace_section"] = "Home"
        st.session_state["home_selected_ticker"] = inspect_ticker
        st.session_state.pop("homepage_v2_selected_ticker", None)
        st.session_state.pop("homepage_v2_active_panel", None)
    elif inspect_view == "market":
        st.session_state["_pending_workspace_section"] = STOCK_INVESTIGATOR_SECTION
        _set_workspace_ticker(inspect_ticker)
    _clear_query_params("inspect_ticker", "inspect_view")


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


def _auth_username() -> str:
    return resolve_secret_value(
        ["DASHBOARD_AUTH_USERNAME"],
        secret_name_env="DASHBOARD_AUTH_USERNAME_SECRET",
        default_secret_name="dashboard-auth-username",
    )


def _auth_password() -> str:
    return resolve_secret_value(
        ["DASHBOARD_AUTH_PASSWORD"],
        secret_name_env="DASHBOARD_AUTH_PASSWORD_SECRET",
        default_secret_name="dashboard-auth-password",
    )


@st.cache_resource(show_spinner=False)
def _auth_session_registry() -> dict[str, dict[str, object]]:
    return {}


def _auth_cookie_value() -> str:
    raw = st.context.cookies.get(_AUTH_COOKIE_NAME)
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value or "").strip()


def _browser_session_cookie_enabled() -> bool:
    return auth_service.allow_insecure_browser_session_cookie()


def _render_auth_persistence_notice() -> None:
    app_track = (os.getenv("APP_TRACK") or "").strip().lower()
    if app_track in {"prod", "production"}:
        return
    if auth_service.browser_session_persistence_mode() != "session_only":
        return
    st.caption(auth_service.browser_session_persistence_message())


def _render_auth_cookie_sync(action: str, value: str = "", persistent: bool = True) -> None:
    if action != "clear" and not _browser_session_cookie_enabled():
        return
    cookie_name = json.dumps(_AUTH_COOKIE_NAME)
    cookie_value = json.dumps(value)
    doc_setup = (
        "const docs = [document];"
        "try { if (window.parent?.document) docs.push(window.parent.document); } catch (e) {}"
        "try { if (window.top?.document) docs.push(window.top.document); } catch (e) {}"
        "const seen = new Set();"
        "const uniqueDocs = docs.filter((doc) => { if (!doc || seen.has(doc)) return false; seen.add(doc); return true; });"
        "const secureAttr = (() => {"
        "  try { return (window.top?.location?.protocol || window.parent?.location?.protocol || window.location.protocol) === 'https:' ? '; Secure' : ''; }"
        "  catch (e) { return window.location.protocol === 'https:' ? '; Secure' : ''; }"
        "})();"
    )
    if action == "clear":
        cookie_script = (
            doc_setup
            + f"const cookieStr = {cookie_name} + '=; Max-Age=0; path=/; SameSite=Lax' + secureAttr;"
            + "uniqueDocs.forEach((doc) => { try { doc.cookie = cookieStr; } catch (e) {} });"
        )
    elif persistent:
        cookie_script = (
            f"const maxAge = {_AUTH_COOKIE_REMEMBER_ME_TTL_SECONDS};"
            + doc_setup
            + f"const cookieStr = {cookie_name} + '=' + encodeURIComponent({cookie_value}) + '; Max-Age=' + maxAge + '; path=/; SameSite=Lax' + secureAttr;"
            + "uniqueDocs.forEach((doc) => { try { doc.cookie = cookieStr; } catch (e) {} });"
        )
    else:
        # Session cookie: no Max-Age, cleared when the browser closes
        cookie_script = (
            doc_setup
            + f"const cookieStr = {cookie_name} + '=' + encodeURIComponent({cookie_value}) + '; path=/; SameSite=Lax' + secureAttr;"
            + "uniqueDocs.forEach((doc) => { try { doc.cookie = cookieStr; } catch (e) {} });"
        )
    components_html(f"<script>{cookie_script}</script>", height=0)


def _create_auth_session(username: str) -> str:
    registry = _auth_session_registry()
    now = time.time()
    expired = [session_id for session_id, payload in registry.items() if float(payload.get("expires_at", 0.0)) <= now]
    for session_id in expired:
        registry.pop(session_id, None)

    session_id = py_secrets.token_urlsafe(24)
    registry[session_id] = {
        "username": username,
        "expires_at": now + _AUTH_COOKIE_TTL_SECONDS,
    }
    return session_id


def _invalidate_auth_session(session_id: str | None) -> None:
    if not session_id:
        return
    _auth_session_registry().pop(session_id, None)


def _clear_local_auth_state() -> None:
    st.session_state["_ui_authenticated"] = False
    st.session_state["_ui_auth_session_id"] = None
    st.session_state["_ui_auth_mode"] = None
    st.session_state.pop("_ui_last_recorded_section_view", None)
    _store_user_context(None)


def _force_logged_out_for_auth_action() -> None:
    """Auth action links must never inherit an existing browser session."""
    if not _auth_enabled():
        return
    signature = _auth_action_query_param_signature()
    if not signature:
        return
    if str(st.session_state.get("_ui_auth_action_forced_logout") or "") == signature:
        return

    session_token = _auth_cookie_value()
    if session_token:
        if auth_service.database_auth_enabled():
            try:
                auth_service.logout_session(session_token)
            except Exception as exc:
                LOGGER.warning("Failed to revoke session before auth action: %s", exc)
        else:
            _invalidate_auth_session(session_token)
    _clear_local_auth_state()
    st.session_state["_show_login_form"] = True
    st.session_state["_ui_auth_action_forced_logout"] = signature
    _render_auth_cookie_sync("clear")


def _restore_legacy_login_from_cookie() -> bool:
    if _auth_action_query_param_present():
        return False
    if not _browser_session_cookie_enabled():
        return False
    session_id = _auth_cookie_value()
    if not session_id:
        return False

    registry = _auth_session_registry()
    payload = registry.get(session_id)
    now = time.time()
    if not payload or float(payload.get("expires_at", 0.0)) <= now:
        registry.pop(session_id, None)
        _render_auth_cookie_sync("clear")
        return False

    st.session_state["_ui_authenticated"] = True
    st.session_state["_ui_auth_session_id"] = session_id
    st.session_state["_ui_auth_mode"] = "legacy"
    _store_user_context(None)
    _apply_post_login_destination()
    return True


def _restore_database_login_from_cookie() -> bool:
    if _auth_action_query_param_present():
        return False
    if not _browser_session_cookie_enabled():
        return False
    session_token = _auth_cookie_value()
    if not session_token:
        return False
    context = auth_service.restore_user_from_session(session_token)
    if context is None:
        _render_auth_cookie_sync("clear")
        return False
    st.session_state["_ui_authenticated"] = True
    st.session_state["_ui_auth_session_id"] = session_token
    st.session_state["_ui_auth_mode"] = "database"
    _store_user_context(context)
    _apply_post_login_destination()
    auth_service.record_access_event(
        event_type="session_restored",
        event_category="usage",
        user=context,
        session_token=session_token,
        ip_address=_request_ip_address(),
        user_agent=_request_user_agent(),
        detail={"source": "cookie_restore"},
    )
    return True


def _render_login_gate_sidebar() -> None:
    app_track = (os.getenv("APP_TRACK") or "local").strip().lower()
    with st.sidebar:
        _render_sidebar_brand_panel()
        _render_sidebar_editorial_links(placement="sidebar_brand")


def _render_legacy_login_gate() -> None:
    _render_login_gate_sidebar()
    username_expected = _auth_username()
    password_expected = _auth_password()
    _render_page_intro(
        "Secure Access",
        "Welcome back",
        "Private client access to the Spectral Nature workspace for market intelligence, portfolio context, and daily research.",
    )
    _render_auth_persistence_notice()

    if not username_expected or not password_expected:
        st.error("Dashboard authentication is enabled, but legacy login credentials are not configured.")
        st.code(
            "export DASHBOARD_AUTH_ENABLED=true\n"
            "export DASHBOARD_AUTH_USERNAME='admin'\n"
            "export DASHBOARD_AUTH_PASSWORD='change-me'\n"
            "# or switch to database auth:\n"
            "# export DASHBOARD_AUTH_MODE='database'\n"
            "# export POSTGRES_CONNECTION_STRING='postgresql://...'\n"
            "# export DASHBOARD_AUTH_BOOTSTRAP_ADMIN_EMAIL='admin@example.com'\n"
            "# export DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD='ChangeMe1234'\n",
            language="bash",
        )
        st.stop()

    with st.form("dashboard_login_legacy", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")

    if submitted:
        if username.strip() == username_expected and password == password_expected:
            session_id = _create_auth_session(username_expected)
            st.session_state["_ui_authenticated"] = True
            st.session_state["_ui_auth_session_id"] = session_id
            st.session_state["_ui_auth_mode"] = "legacy"
            st.session_state.pop("_show_login_form", None)
            _store_user_context(None)
            _render_auth_cookie_sync("set", session_id)
            _apply_post_login_destination()
            return
        st.error("Invalid username or password.")
    st.stop()


def _render_database_login_gate() -> None:
    _render_login_gate_sidebar()
    auth_state = auth_service.initialize_auth_system()
    _render_page_intro(
        "Secure Access",
        "Welcome back",
        "Private client access to the Spectral Nature workspace for market intelligence, portfolio context, and daily research.",
    )
    _render_auth_persistence_notice()

    if not auth_state.get("available"):
        st.error("Database-backed authentication is enabled, but the auth store is unavailable.")
        st.code(
            "export DASHBOARD_AUTH_MODE='database'\n"
            "export POSTGRES_CONNECTION_STRING='postgresql://...'\n"
            "./scripts/run_ui_local.sh",
            language="bash",
        )
        st.stop()

    if not auth_state.get("has_users"):
        st.error("Database auth is configured, but no users are available yet.")
        st.code(
            "export DASHBOARD_AUTH_MODE='database'\n"
            "export POSTGRES_CONNECTION_STRING='postgresql://...'\n"
            "export DASHBOARD_AUTH_BOOTSTRAP_ADMIN_EMAIL='admin@example.com'\n"
            "export DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD='ChangeMe1234'\n"
            "./scripts/run_ui_local.sh",
            language="bash",
        )
        st.stop()

    invite_token = _query_param_value("invite_token")
    reset_token = _query_param_value("reset_token")
    login_tab, create_tab, forgot_tab, reset_tab = st.tabs(["Login", "Create Account", "Forgot Password", "Reset Password"])

    with login_tab:
        with st.form("dashboard_login_db", clear_on_submit=False):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            remember_me = st.checkbox("Remember me", value=True)
            submitted = st.form_submit_button("Login", type="primary")
        if submitted:
            result = auth_service.authenticate_user(
                email=email,
                password=password,
                user_agent=_request_user_agent(),
                ip_address=_request_ip_address(),
                remember_me=remember_me,
            )
            if result.get("ok"):
                context = result.get("context")
                session_token = str(result.get("session_token") or "")
                if isinstance(context, auth_service.UserContext) and session_token:
                    st.session_state["_ui_authenticated"] = True
                    st.session_state["_ui_auth_session_id"] = session_token
                    st.session_state["_ui_auth_mode"] = "database"
                    st.session_state.pop("_show_login_form", None)
                    _store_user_context(context)
                    _render_auth_cookie_sync("set", session_token, persistent=remember_me)
                    _apply_post_login_destination()
                    st.rerun()
            else:
                st.error(str(result.get("message") or "Login failed."))

    with create_tab:
        preview = auth_service.get_invite_preview(invite_token) if invite_token else None
        if preview:
            st.caption(f"Invite for {preview.get('email')} | role: {preview.get('role')} | share: {float(preview.get('proposed_share_fraction') or 0.0) * 100:.2f}%")
        with st.form("dashboard_create_account", clear_on_submit=False):
            invite_token_input = st.text_input("Invite token", value=invite_token)
            first_name = st.text_input("First name")
            last_name = st.text_input("Last name")
            display_name = st.text_input("Display name (optional)")
            password = st.text_input("Password", type="password", key="create_password")
            confirm_password = st.text_input("Confirm password", type="password")
            accepted = st.form_submit_button("Create account", type="primary")
        if accepted:
            if password != confirm_password:
                st.error("Passwords do not match.")
            else:
                result = auth_service.accept_invite(
                    invite_token=invite_token_input,
                    first_name=first_name,
                    last_name=last_name,
                    display_name=display_name,
                    password=password,
                    user_agent=_request_user_agent(),
                    ip_address=_request_ip_address(),
                )
                if result.get("ok"):
                    context = result.get("context")
                    session_token = str(result.get("session_token") or "")
                    if isinstance(context, auth_service.UserContext) and session_token:
                        _clear_auth_query_params()
                        st.session_state["_ui_authenticated"] = True
                        st.session_state["_ui_auth_session_id"] = session_token
                        st.session_state["_ui_auth_mode"] = "database"
                        st.session_state.pop("_show_login_form", None)
                        _store_user_context(context)
                        _render_auth_cookie_sync("set", session_token, persistent=True)
                        _apply_post_login_destination()
                        st.success("Account created. Loading your workspace...")
                        st.rerun()
                else:
                    st.error(str(result.get("message") or "Account creation failed."))

    with forgot_tab:
        with st.form("dashboard_forgot_password", clear_on_submit=False):
            forgot_email = st.text_input("Email", key="forgot_password_email")
            requested = st.form_submit_button("Send reset instructions")
        if requested:
            result = auth_service.request_password_reset(
                email=forgot_email,
                requested_ip=_request_ip_address(),
            )
            st.success(str(result.get("message") or "If an account exists, reset instructions have been sent."))
            if not auth_state.get("email_delivery"):
                email_status = auth_state.get("email_delivery_status") or {}
                st.caption(
                    str(
                        email_status.get("user_message")
                        or "Email delivery is not available right now. Contact an administrator for a reset link."
                    )
                )

    with reset_tab:
        with st.form("dashboard_reset_password", clear_on_submit=False):
            reset_token_input = st.text_input("Reset token", value=reset_token)
            new_password = st.text_input("New password", type="password")
            confirm_new_password = st.text_input("Confirm new password", type="password")
            reset_submitted = st.form_submit_button("Reset password", type="primary")
        if reset_submitted:
            if new_password != confirm_new_password:
                st.error("Passwords do not match.")
            else:
                result = auth_service.complete_password_reset(
                    reset_token=reset_token_input,
                    new_password=new_password,
                )
                if result.get("ok"):
                    _clear_auth_query_params()
                    st.success(str(result.get("message") or "Password reset complete."))
                else:
                    st.error(str(result.get("message") or "Password reset failed."))

    st.stop()


def _handle_auth_cookie_maintenance() -> None:
    """Run cookie cleanup logic. Always call this once per render before auth routing."""
    if not _auth_enabled():
        return
    if st.session_state.pop("_ui_clear_auth_cookie", False):
        _render_auth_cookie_sync("clear")
    elif not _browser_session_cookie_enabled() and _auth_cookie_value():
        _render_auth_cookie_sync("clear")


def _try_restore_session_from_cookie() -> bool:
    """Attempt to restore an authenticated session from a browser cookie.
    Returns True if the session was successfully restored."""
    if not _auth_enabled():
        return False
    if st.session_state.get("_ui_authenticated"):
        return True
    if auth_service.database_auth_enabled():
        return _restore_database_login_from_cookie()
    return _restore_legacy_login_from_cookie()


def _apply_post_login_destination() -> None:
    """After successful login, navigate to the section the user was trying to reach."""
    dest = st.session_state.pop("_pre_auth_destination", None)
    if dest:
        st.session_state["_pending_workspace_section"] = dest


def _render_section_back_button(key: str) -> None:
    """Render a ← Back button that navigates to the previously visited section."""
    section_opts = _section_options()
    prev = _normalize_workspace_section(st.session_state.get("_prev_workspace_section", ""))
    if not prev or prev not in section_opts:
        return
    if st.button("← Back", key=key, use_container_width=True):
        st.session_state["_pending_workspace_section"] = prev
        st.rerun()


def _enforce_login_gate() -> None:
    if not _auth_enabled():
        st.session_state["_ui_authenticated"] = True
        st.session_state["_ui_auth_mode"] = "disabled"
        return

    # Cookie maintenance and restore are expected to have run already via
    # _handle_auth_cookie_maintenance() and _try_restore_session_from_cookie().
    # This guard handles cases where _enforce_login_gate is called directly.
    if st.session_state.pop("_ui_clear_auth_cookie", False):
        _render_auth_cookie_sync("clear")
    elif not _browser_session_cookie_enabled() and _auth_cookie_value():
        _render_auth_cookie_sync("clear")

    if st.session_state.get("_ui_authenticated"):
        return

    if auth_service.database_auth_enabled():
        if _restore_database_login_from_cookie():
            return
        _render_database_login_gate()
        return

    if _restore_legacy_login_from_cookie():
        return
    _render_legacy_login_gate()


def _render_invite_email_designer(*, current_user: auth_service.UserContext) -> None:
    st.subheader("Invite Email Designer")
    st.caption("Manage dark/white invite templates, upload the primary chart image, and preview exactly what recipients will see.")
    _apply_pending_invite_template_state()
    _show_invite_template_notice()

    library = auth_service.get_invite_email_template_library()
    templates_raw = library.get("templates") if isinstance(library.get("templates"), list) else []
    template_by_id: dict[str, dict[str, object]] = {}
    for entry in templates_raw:
        if not isinstance(entry, dict):
            continue
        template_id = str(entry.get("template_id") or "").strip()
        if template_id:
            template_by_id[template_id] = entry

    if not template_by_id:
        st.error("No invite templates are available.")
        return

    active_template_id = str(library.get("active_template_id") or "")
    if active_template_id not in template_by_id:
        active_template_id = next(iter(template_by_id.keys()))

    if st.session_state.get(_INVITE_TEMPLATE_SELECTED_ID_KEY) not in template_by_id:
        st.session_state[_INVITE_TEMPLATE_SELECTED_ID_KEY] = active_template_id

    if not st.session_state.get(_INVITE_TEMPLATE_INIT_KEY):
        _set_invite_template_widget_state(template_by_id[active_template_id])
        st.session_state[_INVITE_TEMPLATE_SELECTED_ID_KEY] = active_template_id
        st.session_state[_INVITE_TEMPLATE_INIT_KEY] = True

    template_ids = list(template_by_id.keys())
    selected_template_id = st.selectbox(
        "Saved Templates",
        options=template_ids,
        key=_INVITE_TEMPLATE_SELECTED_ID_KEY,
        format_func=lambda template_id: _invite_template_label(template_by_id[template_id], active_template_id=active_template_id),
    )
    selected_template = template_by_id.get(selected_template_id) or {}
    loaded_template_id = str(st.session_state.get(_INVITE_TEMPLATE_LOADED_ID_KEY) or "")
    loaded_template = template_by_id.get(loaded_template_id) if loaded_template_id in template_by_id else None
    if loaded_template is not None:
        st.caption(f"Loaded in editor: {str(loaded_template.get('name') or loaded_template_id)}")

    action_load_col, action_active_col, action_delete_col = st.columns(3)
    with action_load_col:
        if st.button("Load Template", key="_access_invite_template_load", use_container_width=True):
            _queue_invite_template_state_update(
                template_to_load=selected_template,
                notice={"level": "success", "message": "Template loaded into editor."},
            )
    with action_active_col:
        if st.button("Set Active Template", key="_access_invite_template_set_active", use_container_width=True):
            try:
                result = auth_service.set_active_invite_email_template(
                    selected_template_id,
                    updated_by=current_user,
                )
                active_id = str(result.get("active_template_id") or selected_template_id)
                _queue_invite_template_state_update(
                    selected_template_id=active_id,
                    notice={"level": "success", "message": "Active invite template updated."},
                )
            except Exception as exc:
                st.error(str(exc))
    with action_delete_col:
        can_delete = bool(selected_template) and (not bool(selected_template.get("protected")))
        if st.button(
            "Delete Selected",
            key="_access_invite_template_delete",
            use_container_width=True,
            disabled=not can_delete,
        ):
            result = auth_service.delete_invite_email_template(
                selected_template_id,
                updated_by=current_user,
            )
            if result.get("ok"):
                _queue_invite_template_state_update(
                    selected_template_id=str(result.get("active_template_id") or active_template_id),
                    notice={"level": "success", "message": "Template deleted."},
                )
            else:
                st.error(str(result.get("message") or "Unable to delete template."))

    save_name_col, save_current_col, save_new_col = st.columns([1.8, 1.1, 1.1])
    with save_name_col:
        st.text_input("Template Name", key=_INVITE_TEMPLATE_NAME_KEY)
    with save_current_col:
        save_current_disabled = loaded_template is None
        if st.button(
            "Save Current",
            key="_access_invite_template_save_current",
            type="primary",
            use_container_width=True,
            disabled=save_current_disabled,
        ):
            payload = _invite_template_from_widget_state()
            result = auth_service.save_invite_email_template(
                template_name=str(payload.get("name") or "Invite Template"),
                theme=payload.get("theme") if isinstance(payload.get("theme"), dict) else {},
                logo_variant=str(payload.get("logo_variant") or "color"),
                chart_asset=payload.get("chart_asset") if isinstance(payload.get("chart_asset"), dict) else None,
                template_id=loaded_template_id,
                updated_by=current_user,
            )
            saved_template = result.get("template") if isinstance(result, dict) else None
            if isinstance(saved_template, dict):
                _queue_invite_template_state_update(
                    selected_template_id=str(saved_template.get("template_id") or loaded_template_id),
                    template_to_load=saved_template,
                    notice={"level": "success", "message": "Template changes saved."},
                )
            _queue_invite_template_state_update(
                notice={"level": "success", "message": "Template changes saved."},
            )
    with save_new_col:
        if st.button("Save As New", key="_access_invite_template_save_new", use_container_width=True):
            payload = _invite_template_from_widget_state()
            result = auth_service.save_invite_email_template(
                template_name=str(payload.get("name") or "Invite Template"),
                theme=payload.get("theme") if isinstance(payload.get("theme"), dict) else {},
                logo_variant=str(payload.get("logo_variant") or "color"),
                chart_asset=payload.get("chart_asset") if isinstance(payload.get("chart_asset"), dict) else None,
                template_id=None,
                updated_by=current_user,
            )
            saved_template = result.get("template") if isinstance(result, dict) else None
            if isinstance(saved_template, dict):
                _queue_invite_template_state_update(
                    selected_template_id=str(saved_template.get("template_id") or ""),
                    template_to_load=saved_template,
                    notice={"level": "success", "message": "New template saved and activated."},
                )
            _queue_invite_template_state_update(
                notice={"level": "success", "message": "New template saved and activated."},
            )

    text_col, color_col = st.columns(2)
    with text_col:
        st.text_input("Kicker", key=_invite_theme_widget_state_key("kicker"))
        st.text_input("Headline", key=_invite_theme_widget_state_key("headline"))
        st.text_area("Intro Text", key=_invite_theme_widget_state_key("intro_text"), height=120)
        st.text_input("CTA Button Label", key=_invite_theme_widget_state_key("cta_label"))
        st.checkbox("Show Graph", key=_invite_theme_widget_state_key("show_graph"))
        st.text_area("Graph Caption", key=_invite_theme_widget_state_key("graph_caption"), height=90)
        st.text_area("Footer Note", key=_invite_theme_widget_state_key("footer_note"), height=90)
        st.selectbox(
            "Logo Variant",
            options=["color", "white"],
            key=_INVITE_TEMPLATE_LOGO_VARIANT_KEY,
            format_func=lambda value: "Color logo (light backgrounds)" if value == "color" else "White logo (dark backgrounds)",
        )
        st.radio(
            "Main Chart Source",
            options=["builtin", "upload"],
            key=_INVITE_TEMPLATE_CHART_SOURCE_KEY,
            format_func=lambda value: "Built-in chart" if value == "builtin" else "Uploaded chart (.png/.gif)",
            horizontal=True,
        )
        if str(st.session_state.get(_INVITE_TEMPLATE_CHART_SOURCE_KEY) or "builtin") == "builtin":
            st.selectbox(
                "Built-in Chart",
                options=["dark", "light"],
                key=_INVITE_TEMPLATE_CHART_BUILTIN_KEY,
                format_func=lambda value: "Dark chart" if value == "dark" else "Light chart",
            )
        else:
            uploaded_chart = st.file_uploader(
                "Upload Chart Image",
                type=["png", "gif"],
                key=_invite_template_upload_widget_key(),
            )
            if uploaded_chart is not None:
                chart_bytes = uploaded_chart.getvalue()
                digest = hashlib.sha256(chart_bytes).hexdigest()
                if digest != str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_DIGEST_KEY) or ""):
                    guessed_mime = "image/gif" if str(uploaded_chart.name or "").strip().lower().endswith(".gif") else "image/png"
                    mime_type = str(uploaded_chart.type or guessed_mime).strip().lower()
                    if mime_type not in auth_service.INVITE_EMAIL_UPLOAD_ALLOWED_MIME_TYPES:
                        st.error("Only .png and .gif charts are supported.")
                    elif len(chart_bytes) > auth_service.INVITE_EMAIL_UPLOAD_MAX_BYTES:
                        max_mb = auth_service.INVITE_EMAIL_UPLOAD_MAX_BYTES // (1024 * 1024)
                        st.error(f"Chart image is too large. Maximum size is {max_mb} MB.")
                    elif not chart_bytes:
                        st.error("Uploaded chart is empty.")
                    else:
                        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY] = str(uploaded_chart.name or "uploaded-chart.png")
                        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY] = mime_type
                        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY] = base64.b64encode(chart_bytes).decode("ascii")
                        st.session_state[_INVITE_TEMPLATE_CHART_UPLOAD_DIGEST_KEY] = digest
            uploaded_name = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_FILENAME_KEY) or "")
            uploaded_mime = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_MIME_KEY) or "")
            uploaded_data = str(st.session_state.get(_INVITE_TEMPLATE_CHART_UPLOAD_DATA_KEY) or "")
            if uploaded_data:
                try:
                    bytes_size = len(base64.b64decode(uploaded_data.encode("ascii"), validate=True))
                except Exception:
                    bytes_size = 0
                kb_size = max(1, int(round(bytes_size / 1024.0)))
                st.caption(f"Uploaded chart: {uploaded_name} ({uploaded_mime}, {kb_size} KB)")
                if st.button("Clear Uploaded Chart", key="_access_invite_template_clear_upload", use_container_width=False):
                    _clear_invite_template_upload_chart(reset_widget=True)
                    st.rerun()
            else:
                st.caption("No upload selected yet. Preview will fall back to the selected built-in chart.")

    with color_col:
        st.color_picker("Background", key=_invite_theme_widget_state_key("background_color"))
        st.color_picker("Card Background", key=_invite_theme_widget_state_key("card_background_color"))
        st.color_picker("Title Color", key=_invite_theme_widget_state_key("title_color"))
        st.color_picker("Body Text Color", key=_invite_theme_widget_state_key("body_color"))
        st.color_picker("Muted Text Color", key=_invite_theme_widget_state_key("muted_text_color"))
        st.color_picker("Button Color", key=_invite_theme_widget_state_key("button_color"))
        st.color_picker("Button Text Color", key=_invite_theme_widget_state_key("button_text_color"))
        st.color_picker("Link Color", key=_invite_theme_widget_state_key("link_color"))
        st.color_picker("Border Color", key=_invite_theme_widget_state_key("border_color"))

    preview_base = (os.getenv("APP_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    default_preview_url = f"{preview_base}/?invite_token=preview-token" if preview_base else "?invite_token=preview-token"
    preview_email = st.text_input(
        "Preview Recipient Email",
        value=str(current_user.email or "client@example.com"),
        key="_access_invite_preview_email",
    )
    preview_role = st.selectbox(
        "Preview Role",
        ["investor", "viewer", "admin"],
        index=0,
        key="_access_invite_preview_role",
    )
    preview_url = st.text_input(
        "Preview Invite URL",
        value=default_preview_url,
        key="_access_invite_preview_url",
    )

    preview_payload = auth_service.build_invite_email_preview(
        invite_url=str(preview_url or default_preview_url),
        recipient_email=str(preview_email or "client@example.com"),
        role=str(preview_role or "investor"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        template_override=_invite_template_from_widget_state(),
    )

    components_html(str(preview_payload.get("html_body") or ""), height=980, scrolling=True)
    with st.expander("Plain Text Fallback", expanded=False):
        st.code(str(preview_payload.get("text_body") or ""), language="text")


def _render_llm_config_admin() -> None:
    load_prompt_overrides()

    st.subheader("LLM Configuration")
    config = load_llm_config()
    if config is None:
        st.warning("No LLM is configured. Set LLM_API_KEY (or OPENAI_API_KEY) to enable.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Provider", config.provider)
        col2.metric("Model", config.model or config.deployment)
        col3.metric("Temperature", str(config.temperature))
        if config.reasoning_effort:
            st.caption(f"Reasoning effort: {config.reasoning_effort}")

    st.divider()
    st.subheader("Narrative Style Rule")
    st.caption("Shared rule appended to every user-facing system prompt.")
    active_rule = get_active_narrative_style_rule()
    edited_rule = st.text_area(
        "Narrative Style Rule",
        value=active_rule,
        height=120,
        key="llm_admin_narrative_style_rule",
        label_visibility="collapsed",
    )
    rule_changed = edited_rule.strip() != active_rule.strip()

    st.divider()
    st.subheader("System Prompts & Tuning Parameters")
    st.caption("Edit prompts and numeric limits below. Changes apply after saving. Pipeline prompts take effect on next job run.")
    # Ensure modules are imported so their register_narrative_prompt calls run.
    import services.attention_market_events  # noqa: F401
    import services.attention_feed_brief  # noqa: F401
    import services.attention_home_1d  # noqa: F401
    import services.attention_live_research  # noqa: F401
    import services.attention_context_llm  # noqa: F401
    import services.aql.summarizer  # noqa: F401
    import services.aql.writer  # noqa: F401
    import services.aql.constants  # noqa: F401
    import services.omnibar_agent  # noqa: F401
    prompts = list_narrative_prompts()
    config_params = list_config_params()
    params_by_group: dict[str, list[dict]] = {}
    for param in config_params:
        params_by_group.setdefault(param["group"], []).append(param)

    prompt_edits: dict[str, str] = {}
    param_edits: dict[str, float | int] = {}
    any_prompt_changed = False
    any_param_changed = False
    if not prompts and not config_params:
        st.info("No prompts or parameters registered yet.")

    _PROMPT_GROUP_DESCRIPTIONS = {
        "Chat + Search": "Powers the omnibar research agent. Changes take effect on the next query.",
        "Attention Pipeline": "Generate feed cards, narratives, and event text. Changes take effect on next pipeline run.",
        "AQL / Research": "Write research summaries, hypotheses, and event analysis. Changes take effect on next pipeline run.",
    }
    prompts_by_group: dict[str, list[dict]] = {}
    for entry in prompts:
        prompts_by_group.setdefault(entry.get("group") or "Other", []).append(entry)

    for group_name, group_prompts in sorted(prompts_by_group.items()):
        st.markdown(f"**{group_name}**")
        group_desc = _PROMPT_GROUP_DESCRIPTIONS.get(group_name)
        if group_desc:
            st.caption(group_desc)
        for entry in group_prompts:
            label = entry["name"]
            if entry.get("is_override"):
                label += "  (overridden)"
            with st.expander(f"{label}  —  {entry['file']}"):
                edited = st.text_area(
                    entry["name"],
                    value=entry["prompt"],
                    height=200,
                    key=f"llm_admin_prompt_{entry['key']}",
                    label_visibility="collapsed",
                )
                prompt_edits[entry["key"]] = edited
                if edited.strip() != entry["prompt"].strip():
                    any_prompt_changed = True
                if entry.get("is_override"):
                    if st.button("Reset to default", key=f"llm_admin_reset_{entry['key']}"):
                        set_narrative_prompt_override(entry["key"], None)
                        saved = save_prompt_overrides()
                        if saved:
                            st.success(f"Reset '{entry['name']}' to default.")
                            st.rerun()
                        else:
                            st.error("Failed to save — check database connection.")

    _GROUP_DESCRIPTIONS = {
        "Display Limits": "Applied at render time — changes take effect instantly.",
        "LLM Context Window": "Applied at pipeline job time — changes require a pipeline re-run.",
        "Chat + Search": "Applied to the omnibar agent — changes take effect on the next query.",
    }
    if params_by_group:
        st.divider()
        st.subheader("Tuning Parameters")
        for group_name, group_params in sorted(params_by_group.items()):
            st.markdown(f"**{group_name}**")
            group_desc = _GROUP_DESCRIPTIONS.get(group_name)
            if group_desc:
                st.caption(group_desc)
            for param in group_params:
                col_label, col_input, col_default = st.columns([3, 1.5, 1.5])
                with col_label:
                    override_tag = " *(overridden)*" if param.get("is_override") else ""
                    st.markdown(f"{param['name']}{override_tag}")
                    st.caption(param["description"])
                with col_input:
                    is_int = isinstance(param["default"], int)
                    edited_val = st.number_input(
                        param["name"],
                        value=param["value"] if is_int else float(param["value"]),
                        step=1 if is_int else 0.1,
                        key=f"llm_admin_param_{param['key']}",
                        label_visibility="collapsed",
                    )
                    param_edits[param["key"]] = edited_val
                    if edited_val != param["value"]:
                        any_param_changed = True
                with col_default:
                    st.caption(f"Default: {param['default']}")
                    if param.get("is_override"):
                        if st.button("Reset", key=f"llm_admin_param_reset_{param['key']}"):
                            set_config_param_override(param["key"], None)
                            saved = save_prompt_overrides()
                            if saved:
                                st.success(f"Reset '{param['name']}' to default.")
                                st.rerun()
                            else:
                                st.error("Failed to save — check database connection.")

    st.divider()
    save_disabled = not (rule_changed or any_prompt_changed or any_param_changed)
    if st.button("Save all changes", type="primary", disabled=save_disabled):
        if rule_changed:
            set_narrative_style_rule_override(edited_rule)
        for key, edited_text in prompt_edits.items():
            registry_entry = next((p for p in prompts if p["key"] == key), None)
            if registry_entry and edited_text.strip() != registry_entry["default"].strip():
                set_narrative_prompt_override(key, edited_text)
            elif registry_entry:
                set_narrative_prompt_override(key, None)
        for key, edited_val in param_edits.items():
            registry_entry = next((p for p in config_params if p["key"] == key), None)
            if registry_entry and edited_val != registry_entry["default"]:
                set_config_param_override(key, edited_val)
            elif registry_entry:
                set_config_param_override(key, None)
        saved = save_prompt_overrides()
        if saved:
            st.success("Saved. UI prompts take effect immediately. Pipeline prompts take effect on next job run.")
            st.rerun()
        else:
            st.error("Failed to save — check database connection.")


def _render_api_keys_admin(
    *,
    current_user: auth_service.UserContext,
    user_rows: list[dict[str, Any]],
) -> None:
    """Admin UI for creating, viewing, revoking agent API keys, and API reference."""
    st.divider()
    st.subheader("API Keys")
    st.caption("Create scoped API keys for scripts, agents, or external integrations. Keys are shown once on creation.")

    # --- Create key form ---
    user_options: dict[str, str] = {"": "No user (standalone agent key)"}
    for row in user_rows:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("user_id") or "").strip()
        email = str(row.get("email") or "").strip()
        if uid and email:
            display_name = str(row.get("display_name") or "").strip()
            user_options[uid] = f"{display_name} ({email})" if display_name and display_name != email else email

    with st.form("admin_create_api_key", clear_on_submit=True):
        key_name = st.text_input("Key name", placeholder="e.g. research-export-script")
        assigned_user_id = str(
            st.selectbox("Assign to user", options=list(user_options.keys()), format_func=lambda uid: user_options.get(uid, uid))
            or ""
        ).strip()
        available_scopes = sorted(api_auth.AGENT_SCOPE_ALLOWLIST)
        selected_scopes = st.multiselect("Scopes", options=available_scopes, default=available_scopes)
        expires_days = st.selectbox("Expires in", options=[None, 7, 30, 90, 365], format_func=lambda v: "Never" if v is None else f"{v} days")
        key_notes = st.text_input("Notes", placeholder="Optional description")
        create_submitted = st.form_submit_button("Create API key", type="primary")

    if create_submitted:
        if not str(key_name or "").strip():
            st.error("Key name is required.")
        else:
            expires_at = None
            if expires_days is not None:
                expires_at = datetime.now(timezone.utc) + timedelta(days=int(expires_days))
            created_by = assigned_user_id if assigned_user_id else (current_user.user_id if current_user else None)
            result = api_auth.create_agent_api_key(
                name=str(key_name).strip(),
                scopes=selected_scopes,
                created_by=created_by,
                expires_at=expires_at,
                notes=str(key_notes or "").strip(),
            )
            raw_key = result.get("api_key", "")
            st.success("API key created. Save the key now; it will not be shown again.")
            st.code(raw_key, language="text")
            if assigned_user_id:
                st.caption(f"Assigned to: {user_options.get(assigned_user_id, assigned_user_id)}")

    # --- List existing keys ---
    st.divider()
    st.subheader("Existing Keys")
    existing_keys = api_auth.list_agent_api_keys()
    if not existing_keys:
        st.info("No API keys have been created yet.")
    else:
        for key_row in existing_keys:
            if not isinstance(key_row, dict):
                continue
            key_id = str(key_row.get("id") or "")
            key_name_display = str(key_row.get("name") or "unnamed")
            key_prefix = str(key_row.get("key_prefix") or "")
            key_status = str(key_row.get("status") or "unknown")
            key_scopes = list(key_row.get("scopes") or [])
            key_created_at = key_row.get("created_at")
            key_last_used = key_row.get("last_used_at")
            key_expires = key_row.get("expires_at")
            key_created_by = str(key_row.get("created_by") or "").strip()
            key_notes_text = str(key_row.get("notes") or "").strip()

            # Find assigned user name
            assigned_label = ""
            if key_created_by:
                assigned_label = user_options.get(key_created_by, key_created_by)

            status_icon = "active" if key_status == "active" else "revoked"
            with st.expander(f"{key_name_display}  |  {key_prefix}...  |  {status_icon}", expanded=False):
                info_cols = st.columns([1, 1, 1])
                with info_cols[0]:
                    st.caption(f"Status: **{key_status}**")
                    st.caption(f"Prefix: `{key_prefix}`")
                    if assigned_label:
                        st.caption(f"Assigned to: {assigned_label}")
                with info_cols[1]:
                    st.caption(f"Created: {_format_access_admin_timestamp(key_created_at)}")
                    st.caption(f"Last used: {_format_access_admin_timestamp(key_last_used) if key_last_used else 'never'}")
                    if key_expires:
                        st.caption(f"Expires: {_format_access_admin_timestamp(key_expires)}")
                with info_cols[2]:
                    st.caption(f"Scopes: {', '.join(key_scopes) if key_scopes else 'none'}")
                    if key_notes_text:
                        st.caption(f"Notes: {key_notes_text}")
                if key_status == "active":
                    if st.button("Revoke", key=f"revoke_key_{key_id}", type="secondary"):
                        api_auth.revoke_agent_api_key(
                            key_id=key_id,
                            revoked_by=current_user.user_id if current_user else None,
                        )
                        st.success(f"Key '{key_name_display}' revoked.")
                        st.rerun()

    # --- API Reference ---
    st.divider()
    st.subheader("API Reference")
    st.caption("Use your API key with the `X-API-Key` header or as a `Bearer` token. All endpoints return JSON.")

    _API_REF = """
**Authentication** — include with every request:
```
X-API-Key: snak_YOUR_KEY
```

---

**Datasets** — the core data query pattern. Replace `{name}` with any dataset below.

```bash
curl -X POST https://HOST/v1/dataset/{name} \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"params": {}}'
```

| Dataset | Required Params | Description |
|---------|----------------|-------------|
| `attention_home_1d` | — | Today's homepage snapshot: top events, must-read movers, summary |
| `attention_research_bundle` | `bundle_id` | Full research bundle (what changed, why, spillover, background) |
| `attention_ticker_snapshot` | `ticker` | Ticker attention card with events and context |
| `attention_ticker_background` | `ticker` | Background research for a ticker |
| `attention_feed` | — | Scored attention feed across all entities |
| `attention_rollups` | — | Aggregated attention rollups by theme/sector |
| `saa_document_search` | — | Search retained documents (filter by `tickers`, `providers`, `start_date`, `end_date`) |
| `saa_chunk_search` | — | Search evidence chunks with lexical + semantic matching |
| `saa_document` | `canonical_document_id` | Single document with full raw text |
| `recent_news` | `ticker` | Recent news articles for a ticker |
| `price_history` | `ticker` | Historical price data |
| `technical_signal_summary` | `ticker` | Technical signal snapshot |
| `positions` | — | Current portfolio positions |
| `daily_movers` | — | Today's biggest movers |
| `fred_dashboard` | — | Macro economic dashboard (FRED data) |
| `yield_curve_summary` | — | Current yield curve snapshot |
| `option_chain` | `ticker` | Options chain data |

---

**Example: Get homepage summary**
```bash
curl -X POST https://HOST/v1/dataset/attention_home_1d \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"params": {}}'
```

**Example: Search documents about AAPL from the last week**
```bash
curl -X POST https://HOST/v1/dataset/saa_document_search \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"params": {"tickers": ["AAPL"], "start_date": "2026-04-11", "limit": 20}}'
```

**Example: Get a research bundle**
```bash
curl -X POST https://HOST/v1/dataset/attention_research_bundle \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"params": {"bundle_id": "symbol::AAPL"}}'
```

**Example: Search evidence chunks**
```bash
curl -X POST https://HOST/v1/dataset/saa_chunk_search \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"params": {"query": "Iran oil sanctions", "providers": ["tavily"], "limit": 10}}'
```

---

**Research Export** — bulk download of all research in a time window as a zip file.

```bash
# 1. Start export
curl -X POST https://HOST/v1/research/export \\
  -H "X-API-Key: snak_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"start_date": "2026-04-11", "end_date": "2026-04-18"}'
# Returns: {"job_id": "exp-...", "status": "building"}

# 2. Poll for completion
curl https://HOST/v1/research/export/JOB_ID \\
  -H "X-API-Key: snak_YOUR_KEY"
# Returns: {"status": "ready", "download_url": "https://..."}

# 3. Download (no auth needed)
curl -o export.zip "DOWNLOAD_URL"
```

---

**Other endpoints**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/v1/capabilities` | List all available datasets and charts |
| `POST` | `/v1/query` | Generic query (specify `operation`, `name`, `params`) |
| `POST` | `/v1/chart/{name}` | Get chart data (e.g. `portfolio_vs_benchmarks`, `technical_price_channel`) |
| `POST` | `/v1/omnibar/resolve` | Resolve a search/analysis query |
| `GET` | `/v1/omnibar/suggestions` | Get omnibar suggestions |
| `GET` | `/v1/agent/tools` | List available agent tools (MCP compatible) |
| `POST` | `/v1/agent/tools/{name}/invoke` | Invoke an agent tool |
| `POST` | `/v1/agent/rpc` | JSON-RPC endpoint (MCP compatible) |
"""
    st.markdown(_API_REF)


def _render_access_admin_section() -> None:
    header_cols = st.columns([4.6, 1.4])
    with header_cols[0]:
        st.title(ADMIN_SECTION)
    with header_cols[1]:
        _render_section_back_button("admin_back")

    if st.session_state.get("_ui_auth_mode") != "database":
        st.info("Database-backed auth is required for user invites and password reset management.")
        return

    current_user = _current_user_context()
    if current_user is None or not current_user.is_admin:
        st.error("Only admin users can access this section.")
        return

    auth_state = auth_service.initialize_auth_system()
    email_status = auth_state.get("email_delivery_status") or {}
    st.caption(
        "Manage invite-based account creation, review pending invites, issue password reset links, and design invite emails."
    )
    st.caption(
        f"Email delivery: {'configured' if auth_state.get('email_delivery') else 'not configured'}"
    )
    if not auth_state.get("email_delivery"):
        st.caption(str(email_status.get("message") or "Email delivery is not configured."))
    _show_access_pending_invite_notice()
    user_rows = auth_service.list_users()
    analytics_user_options: dict[str, dict[str, str]] = {"": {"label": "All users", "email": ""}}
    for row in user_rows:
        if not isinstance(row, dict):
            continue
        option_user_id = str(row.get("user_id") or "").strip()
        option_email = str(row.get("email") or "").strip()
        if not option_user_id or not option_email:
            continue
        option_display_name = str(row.get("display_name") or "").strip()
        option_label = option_email if not option_display_name or option_display_name == option_email else f"{option_display_name} ({option_email})"
        analytics_user_options[option_user_id] = {"label": option_label, "email": option_email}

    admin_view_options = ["Access Management", "Pipeline Jobs", "Usage", "Security", "LLM Config"]
    _prime_widget_choice(
        "access_admin_view",
        admin_view_options,
        fallback="Access Management",
        pending_key="_pending_access_admin_view",
    )
    admin_view = st.segmented_control(
        "Admin View",
        admin_view_options,
        key="access_admin_view",
        width="stretch",
    )

    if admin_view == "Access Management":
        invite_col, reset_col = st.columns(2)
        with invite_col:
            st.subheader("Create Invite")
            with st.form("access_admin_invite", clear_on_submit=True):
                invite_email = st.text_input("Email")
                invite_role = st.selectbox("Role", ["investor", "viewer", "admin"], index=0)
                invite_share_pct = st.number_input("Portfolio share %", min_value=0.0, max_value=100.0, value=0.0, step=0.25)
                invite_submitted = st.form_submit_button("Create invite", type="primary")
            if invite_submitted:
                share_fraction = float(invite_share_pct) / 100.0 if invite_role == "investor" else 0.0
                result = auth_service.issue_invite(
                    email=invite_email,
                    role=invite_role,
                    share_fraction=share_fraction,
                    created_by=current_user,
                )
                if result.get("ok"):
                    st.success("Invite created.")
                    if result.get("email_sent"):
                        st.caption(str(result.get("email_message") or "Invite email sent."))
                    else:
                        st.caption(str(result.get("email_message") or "Email not sent."))
                        st.code(str(result.get("invite_url") or ""), language="text")
                else:
                    st.error(str(result.get("message") or "Invite creation failed."))

        with reset_col:
            st.subheader("Issue Password Reset")
            with st.form("access_admin_reset", clear_on_submit=True):
                reset_email = st.text_input("User email")
                reset_submitted = st.form_submit_button("Issue reset link", type="primary")
            if reset_submitted:
                result = auth_service.admin_issue_password_reset(
                    email=reset_email,
                    requested_by=current_user,
                )
                if result.get("ok"):
                    st.success("Password reset issued.")
                    if result.get("email_sent"):
                        st.caption(str(result.get("email_message") or "Reset email sent."))
                    else:
                        st.caption(str(result.get("email_message") or "Email not sent."))
                        st.code(str(result.get("reset_url") or ""), language="text")
                else:
                    st.error(str(result.get("message") or "Reset issuance failed."))

        users = pd.DataFrame(user_rows)
        st.subheader("Users")
        if users.empty:
            st.info("No users found.")
        else:
            display_cols = [
                column
                for column in [
                    "email",
                    "display_name",
                    "role",
                    "membership_role",
                    "share_fraction",
                    "can_view_full_portfolio",
                    "status",
                    "active_session_count",
                    "open_session_count",
                    "last_seen_at",
                    "failed_login_count",
                    "locked_until",
                    "last_login_at",
                ]
                if column in users.columns
            ]
            if "share_fraction" in users.columns:
                users["share_fraction"] = pd.to_numeric(users["share_fraction"], errors="coerce") * 100.0
            for timestamp_col in ["last_login_at", "last_seen_at", "locked_until"]:
                if timestamp_col in users.columns:
                    users[timestamp_col] = users[timestamp_col].apply(_format_access_admin_timestamp)
            st.dataframe(users[display_cols], use_container_width=True, hide_index=True)

        invites = auth_service.list_pending_invites()
        st.subheader("Pending Invites")
        if not invites:
            st.info("No pending invites.")
        else:
            st.caption("Each invite now has its own row actions.")
            for invite in invites:
                if isinstance(invite, dict):
                    _render_access_pending_invite_card(invite, current_user=current_user)

        _render_api_keys_admin(current_user=current_user, user_rows=user_rows)

        st.markdown("---")
        with st.expander("Invite Email Designer", expanded=False):
            _render_invite_email_designer(current_user=current_user)
    elif admin_view == "Pipeline Jobs":
        _render_pipeline_admin(source_refresh_flags=st.session_state.get("_source_force_refresh", {}))
    elif admin_view in {"Usage", "Security"}:
        usage_window_days = _access_admin_int_state_value(
            "_access_usage_window_days",
            fallback=14,
            allowed=(7, 14, 30, 90),
        )
        security_window_days = _access_admin_int_state_value(
            "_access_security_window_days",
            fallback=14,
            allowed=(1, 7, 14, 30, 90),
        )
        active_window_minutes = _access_admin_int_state_value(
            "_access_active_window_minutes",
            fallback=30,
            allowed=(15, 30, 60, 120),
        )
        sankey_user_limit = _access_admin_int_state_value(
            "_access_usage_sankey_user_limit",
            fallback=10,
            allowed=(3, 5, 10, 15, 20),
        )

        if admin_view == "Usage":
            control_col_1, control_col_2, control_col_3, control_col_4 = st.columns([1, 1, 1.6, 0.9])
            with control_col_1:
                usage_window_days = int(
                    st.selectbox(
                        "Usage window",
                        options=[7, 14, 30, 90],
                        index=[7, 14, 30, 90].index(usage_window_days),
                        key="_access_usage_window_days",
                    )
                )
            with control_col_2:
                active_window_minutes = int(
                    st.selectbox(
                        "Active session window",
                        options=[15, 30, 60, 120],
                        index=[15, 30, 60, 120].index(active_window_minutes),
                        key="_access_active_window_minutes",
                    )
                )
            with control_col_3:
                selected_user_id = str(
                    st.selectbox(
                        "User filter",
                        options=list(analytics_user_options.keys()),
                        index=0,
                        key="_access_usage_user_filter",
                        format_func=lambda option_id: analytics_user_options.get(str(option_id or ""), {}).get("label", "All users"),
                    )
                    or ""
                ).strip()
            with control_col_4:
                sankey_user_limit = int(
                    st.selectbox(
                        "Flow users",
                        options=[3, 5, 10, 15, 20],
                        index=[3, 5, 10, 15, 20].index(sankey_user_limit),
                        key="_access_usage_sankey_user_limit",
                    )
                )
        else:
            control_col_1, control_col_2, control_col_3 = st.columns([1, 1, 1.8])
            with control_col_1:
                security_window_days = int(
                    st.selectbox(
                        "Security window",
                        options=[1, 7, 14, 30, 90],
                        index=[1, 7, 14, 30, 90].index(security_window_days),
                        key="_access_security_window_days",
                    )
                )
            with control_col_2:
                active_window_minutes = int(
                    st.selectbox(
                        "Active session window",
                        options=[15, 30, 60, 120],
                        index=[15, 30, 60, 120].index(active_window_minutes),
                        key="_access_active_window_minutes",
                    )
                )
            with control_col_3:
                selected_user_id = str(
                    st.selectbox(
                        "User filter",
                        options=list(analytics_user_options.keys()),
                        index=0,
                        key="_access_usage_user_filter",
                        format_func=lambda option_id: analytics_user_options.get(str(option_id or ""), {}).get("label", "All users"),
                    )
                    or ""
                ).strip()

        selected_user_meta = analytics_user_options.get(selected_user_id, {"label": "All users", "email": ""})
        selected_user_label = str(selected_user_meta.get("label") or "All users")
        selected_user_email = str(selected_user_meta.get("email") or "").strip()

        with st.spinner(f"Loading {admin_view.lower()} analytics..."):
            dashboard = auth_service.get_access_admin_dashboard(
                usage_window_days=usage_window_days,
                security_window_days=security_window_days,
                active_window_minutes=active_window_minutes,
                sankey_user_limit=sankey_user_limit,
                user_id=selected_user_id,
                user_email=selected_user_email,
            )

        if admin_view == "Usage":
            _render_access_usage_admin_dashboard(
                dashboard=dashboard,
                selected_user_id=selected_user_id,
                selected_user_label=selected_user_label,
                selected_user_email=selected_user_email,
                usage_window_days=usage_window_days,
                active_window_minutes=active_window_minutes,
                sankey_user_limit=sankey_user_limit,
            )
        else:
            _render_access_security_admin_dashboard(
                dashboard=dashboard,
                selected_user_id=selected_user_id,
                selected_user_label=selected_user_label,
                selected_user_email=selected_user_email,
                security_window_days=security_window_days,
                active_window_minutes=active_window_minutes,
            )

    elif admin_view == "LLM Config":
        _render_llm_config_admin()


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


def _render_pipeline_admin(*, source_refresh_flags: dict[str, bool]) -> None:
    """Pipeline Jobs admin tab: timeline, failure/row-count plots, job control table."""

    history_days_options = [3, 7, 14, 30]
    _prime_widget_choice("_pipeline_admin_history_days", [str(d) for d in history_days_options], fallback="7", pending_key="_pending_pipeline_admin_history_days")
    history_days = int(
        st.selectbox(
            "History window (days)",
            options=history_days_options,
            index=history_days_options.index(int(st.session_state.get("_pipeline_admin_history_days", 7))),
            key="_pipeline_admin_history_days",
        )
    )

    with st.spinner("Loading pipeline history..."):
        with _timed("load_job_run_history"):
            runs = job_run_history(days=history_days)
        with _timed("load_dataset_version_history"):
            datasets = dataset_version_history(days=history_days)
        with _timed("load_job_status_table"):
            status_table = latest_job_status_table()

    # ── Timeline ────────────────────────────────────────────────────────
    st.subheader("Job Run Timeline")
    if runs.empty:
        st.info("No job runs in this window.")
    else:
        timeline_df = runs.dropna(subset=["start_time_utc"]).copy()
        # Fill missing end times with now (still running)
        timeline_df["end_time_utc"] = timeline_df["end_time_utc"].fillna(pd.Timestamp.now(tz="UTC"))
        timeline_df["label"] = timeline_df["job_name"].map(
            lambda n: JOB_LABELS.get(n, n.replace("-", " ").title())
        )
        status_color_map = {
            "Succeeded": "#2ecc71",
            "Running": "#3498db",
            "Failed": "#e74c3c",
            "Warning": "#f39c12",
        }
        fig_timeline = px.timeline(
            timeline_df,
            x_start="start_time_utc",
            x_end="end_time_utc",
            y="label",
            color="status",
            color_discrete_map=status_color_map,
            hover_data=["job_name", "run_id", "progress_stage"],
        )
        fig_timeline.update_layout(
            height=max(250, len(timeline_df["label"].unique()) * 50),
            yaxis_title="",
            xaxis_title="",
            showlegend=True,
            legend_title_text="Status",
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_timeline, use_container_width=True)

    # ── Failures ────────────────────────────────────────────────────────
    st.subheader("Failures")
    if runs.empty or (runs["status"] != "Failed").all():
        st.caption("No failures in this window.")
    else:
        failed = runs[runs["status"] == "Failed"].copy()
        failed["date"] = failed["start_time_utc"].dt.date
        failure_counts = failed.groupby(["date", "job_name"]).size().reset_index(name="failures")
        failure_counts["label"] = failure_counts["job_name"].map(
            lambda n: JOB_LABELS.get(n, n.replace("-", " ").title())
        )
        fig_fail = px.bar(
            failure_counts,
            x="date",
            y="failures",
            color="label",
            barmode="stack",
        )
        fig_fail.update_layout(
            height=280,
            yaxis_title="Failures",
            xaxis_title="",
            legend_title_text="Job",
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_fail, use_container_width=True)

    # ── Dataset Row Counts ──────────────────────────────────────────────
    st.subheader("Dataset Row Counts")
    if datasets.empty:
        st.caption("No dataset snapshots in this window.")
    else:
        # Show the top datasets by volume
        top_datasets = datasets.groupby("dataset_name")["row_count"].sum().nlargest(12).index.tolist()
        plot_data = datasets[datasets["dataset_name"].isin(top_datasets)].copy()
        fig_rows = px.scatter(
            plot_data,
            x="ingested_at_utc",
            y="row_count",
            color="dataset_name",
            hover_data=["run_id"],
            labels={"row_count": "Rows", "ingested_at_utc": "Ingested", "dataset_name": "Dataset"},
        )
        fig_rows.update_traces(mode="lines+markers", marker=dict(size=5))
        fig_rows.update_layout(
            height=350,
            yaxis_title="Row Count",
            xaxis_title="",
            legend_title_text="Dataset",
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_rows, use_container_width=True)

    # ── Latest Status Metrics ───────────────────────────────────────────
    st.subheader("Latest Status")
    if not status_table.empty and "status" in status_table.columns:
        succeeded = int((status_table["status"] == "Succeeded").sum())
        running = int((status_table["status"] == "Running").sum())
        failing = int((status_table["status"] == "Failed").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Succeeded", succeeded)
        c2.metric("Running", running)
        c3.metric("Failed", failing)

    # ── Job Controls Table ──────────────────────────────────────────────
    st.subheader("Job Controls")
    st.caption("Trigger remote snapshot refresh jobs.")
    groups = _job_control_groups()
    for group in groups:
        job_name = str(group["job_name"])
        sources = [SOURCE_LABELS.get(sk, sk.title()) for sk in group["sources"]]
        datasets_list = [str(d) for d in group["datasets"]]
        dataset_preview = ", ".join(datasets_list[:4])
        if len(datasets_list) > 4:
            dataset_preview += f", +{len(datasets_list) - 4} more"

        cols = st.columns([3, 2, 1.2])
        with cols[0]:
            st.markdown(f"**{group['label']}** — {', '.join(sources)}")
            st.caption(f"`{job_name}` · Datasets: {dataset_preview}")
        with cols[1]:
            # Show latest status for this job
            if not status_table.empty and "job_name" in status_table.columns:
                job_row = status_table[status_table["job_name"] == job_name]
                if not job_row.empty:
                    status_val = str(job_row.iloc[0].get("status", ""))
                    start_val = str(job_row.iloc[0].get("start_time_utc", ""))
                    msg_val = str(job_row.iloc[0].get("message", ""))[:80]
                    st.caption(f"{status_val} · {start_val}")
                    if msg_val:
                        st.caption(msg_val)
        with cols[2]:
            if st.button("Refresh", key=f"admin_run_job_{job_name}", use_container_width=True):
                ok, msg = start_source_refresh_job(str(group["sources"][0]))
                if ok:
                    for source_key in group["sources"]:
                        source_refresh_flags[str(source_key)] = True
                    st.session_state["_source_force_refresh"] = source_refresh_flags
                    st.success(msg)
                else:
                    st.warning(msg)

    # ── Detailed Run Table ──────────────────────────────────────────────
    with st.expander("Detailed Run History", expanded=False):
        if runs.empty:
            st.info("No run history available.")
        else:
            display_runs = runs.copy()
            display_runs["label"] = display_runs["job_name"].map(
                lambda n: JOB_LABELS.get(n, n.replace("-", " ").title())
            )
            display_runs = display_runs.rename(columns={
                "label": "Job",
                "run_id": "Run",
                "status": "Status",
                "start_time_utc": "Start (UTC)",
                "end_time_utc": "End (UTC)",
                "error_summary": "Error",
                "progress_stage": "Stage",
            })
            st.dataframe(
                display_runs[["Job", "Run", "Status", "Start (UTC)", "End (UTC)", "Stage", "Error"]],
                use_container_width=True,
                hide_index=True,
            )


def _job_control_groups() -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    for source_key, job_name in SOURCE_JOB_MAP.items():
        entry = groups.setdefault(
            job_name,
            {
                "job_name": job_name,
                "label": JOB_LABELS.get(job_name, job_name.replace("-", " ").title()),
                "sources": [],
                "datasets": [],
            },
        )
        entry["sources"].append(source_key)
        for dataset_name in SOURCE_DATASETS.get(source_key, []):
            if dataset_name not in entry["datasets"]:
                entry["datasets"].append(dataset_name)

    return list(groups.values())

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
    cfg: AppConfig,
    ticker: str,
    *,
    force_data_refresh: bool,
    asof_time_utc: object | None = None,
) -> None:
    normalized_ticker = str(ticker or "").upper().strip()
    if not normalized_ticker:
        return
    try:
        fundamentals = _load_quarterly_fundamentals_cached(normalized_ticker, force_refresh=force_data_refresh)
    except Exception as exc:
        st.caption(f"Fundamentals unavailable: {exc}")
        return
    scoped = _filter_fundamentals_asof(fundamentals, asof_time_utc=asof_time_utc)
    has_any = any(isinstance((scoped or {}).get(key), pd.DataFrame) and not (scoped or {}).get(key).empty for key in ["income", "balance", "cashflow"])
    st.markdown("**Fundamentals**")
    if not has_any:
        st.caption("No quarterly fundamentals were available for this ticker.")
        return
    # Staleness check: warn when the most recent report_date is more than 150 days ago.
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


def _ticker_inspector_href(symbol: str, *, target: str) -> str:
    cleaned_symbol = str(symbol or "").upper().strip()
    cleaned_target = str(target or "").strip().lower()
    if not cleaned_symbol or cleaned_target not in {"home", "market"}:
        return ""
    return "?" + urlencode({"inspect_ticker": cleaned_symbol, "inspect_view": cleaned_target})


def _open_ticker_snapshot_target(symbol: str, *, target: str) -> None:
    cleaned_symbol = str(symbol or "").upper().strip()
    cleaned_target = str(target or "").strip().lower()
    if not cleaned_symbol or cleaned_target not in {"home", "home_v2", "home_exp", "market"}:
        return
    _record_usage_interaction(
        event_type="ticker_open",
        detail={
            "surface": cleaned_target,
            "symbol": cleaned_symbol,
            "target_type": "ticker",
            "target_label": cleaned_symbol,
            "target_id": cleaned_symbol,
        },
    )
    if cleaned_target == "home":
        st.session_state["home_selected_ticker"] = cleaned_symbol
        return
    if cleaned_target == "home_v2":
        st.session_state["homepage_v2_selected_ticker"] = cleaned_symbol
        _queue_homepage_v2_active_panel(HOMEPAGE_V2_COMPANY_PANEL)
        return
    if cleaned_target == "home_exp":
        st.session_state["homepage_exp_selected_ticker"] = cleaned_symbol
        st.session_state["homepage_exp_active_panel"] = HOMEPAGE_V2_COMPANY_PANEL
        return
    _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": cleaned_symbol})


def _render_ticker_snapshot_chart(
    data_uri: str,
    *,
    symbol: str,
    click_target: str = "",
    button_key: str = "",
) -> None:
    normalized_uri = str(data_uri or "").strip()
    if normalized_uri:
        image_html = (
            "<img "
            f"src=\"{normalized_uri}\" alt=\"{html.escape(symbol)} chart\" "
            "style=\"width:100%;max-width:164px;height:auto;display:block;\" />"
        )
        st.markdown(image_html, unsafe_allow_html=True)
    else:
        st.caption("No recent chart available.")

    if not click_target:
        return

    normalized_click_target = str(click_target or "").strip().lower()
    if normalized_click_target in {"home", "home_v2", "home_exp"}:
        return
    widget_key = str(button_key or f"ticker_snapshot_{click_target}_{symbol}").strip()
    if st.button(
        "Open",
        key=widget_key,
        use_container_width=True,
    ):
        _open_ticker_snapshot_target(symbol, target=click_target)


def _ticker_preview_clickable(click_target: str) -> bool:
    return str(click_target or "").strip().lower() in {"home", "home_v2", "home_exp"}


def _ticker_preview_is_selected(symbol: str, *, click_target: str, click_bundle_id: str = "") -> bool:
    normalized_symbol = str(symbol or "").upper().strip()
    normalized_click_target = str(click_target or "").strip().lower()
    normalized_bundle_id = str(click_bundle_id or "").strip()
    if not normalized_symbol:
        return False
    if normalized_click_target == "home":
        return str(st.session_state.get("home_selected_ticker") or "").upper().strip() == normalized_symbol
    if normalized_click_target == "home_v2":
        return (
            str(st.session_state.get("homepage_v2_selected_ticker") or "").upper().strip() == normalized_symbol
            and str(st.session_state.get("homepage_v2_active_panel") or "").strip() == HOMEPAGE_V2_COMPANY_PANEL
            and (
                not normalized_bundle_id
                or str(st.session_state.get("homepage_v2_selected_bundle_id") or "").strip() == normalized_bundle_id
            )
        )
    if normalized_click_target == "home_exp":
        return (
            str(st.session_state.get("homepage_exp_selected_ticker") or "").upper().strip() == normalized_symbol
            and str(st.session_state.get("homepage_exp_active_panel") or "").strip() == HOMEPAGE_V2_COMPANY_PANEL
            and (
                not normalized_bundle_id
                or str(st.session_state.get("homepage_exp_selected_bundle_id") or "").strip() == normalized_bundle_id
            )
        )
    return False


def _ticker_preview_surface_key(
    symbol: str,
    *,
    click_target: str,
    key_prefix: str = "",
    index: int = 0,
    selected: bool = False,
) -> str:
    cleaned_symbol = re.sub(r"[^a-zA-Z0-9_]+", "_", str(symbol or index).strip())
    cleaned_prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", str(key_prefix or "ticker_preview").strip())
    cleaned_target = re.sub(r"[^a-zA-Z0-9_]+", "_", str(click_target or "none").strip())
    state_prefix = "ticker_preview_surface_selected" if selected else "ticker_preview_surface_idle"
    return f"{state_prefix}_{cleaned_target}_{cleaned_prefix}_{index}_{cleaned_symbol}"


def _render_ticker_preview_identity(
    row: dict[str, str],
    *,
    click_target: str,
    key_prefix: str = "",
    index: int = 0,
    click_bundle_id: str = "",
) -> None:
    symbol = str(row.get("symbol") or "").upper().strip()
    company_name = str(row.get("company_name") or symbol).strip()
    label = f"{symbol} · {company_name}" if company_name and company_name != symbol else symbol
    normalized_click_target = str(click_target or "").strip().lower()
    if _ticker_preview_clickable(normalized_click_target):
        click_handler = _open_ticker_snapshot_target
        click_args: tuple[object, ...] = (symbol,)
        click_kwargs: dict[str, object] = {"target": normalized_click_target}
        if normalized_click_target == "home_exp":
            click_handler = _open_homepage_exp_ticker
            click_args = (symbol, str(click_bundle_id or "").strip())
            click_kwargs = {}
        st.button(
            label,
            key=_ticker_preview_surface_key(
                symbol,
                click_target=normalized_click_target,
                key_prefix=key_prefix,
                index=index,
                selected=_ticker_preview_is_selected(
                    symbol,
                    click_target=normalized_click_target,
                    click_bundle_id=str(click_bundle_id or "").strip(),
                ),
            ),
            use_container_width=True,
            type="tertiary",
            on_click=click_handler,
            args=click_args,
            kwargs=click_kwargs,
        )
        return
    st.markdown(f"**{symbol}**")
    if company_name and company_name != symbol:
        st.markdown(f"**{company_name}**")


def _render_ticker_snapshot_table(
    cfg: AppConfig | None,
    items: list[dict[str, object]],
    *,
    force_refresh: bool = False,
    show_header: bool = True,
    click_target: str = "",
    key_prefix: str = "",
    click_bundle_id: str = "",
    allow_live_profile_fallback: bool = True,
) -> None:
    if not isinstance(items, list):
        return
    rows: list[dict[str, str]] = []
    for item in items:
        symbol = str((item or {}).get("symbol") or "").upper().strip()
        if not symbol:
            continue
        profile = _load_ticker_snapshot_profile(
            cfg,
            symbol,
            force_refresh=force_refresh,
            allow_live_fallback=allow_live_profile_fallback,
        )
        company_name = str(profile.get("company_name") or symbol).strip()
        market_cap_label = str(profile.get("market_cap_label") or "n/a").strip()
        extras = [
            str(value).strip()
            for value in list((item or {}).get("extras") or [])
            if str(value).strip() and str(value).strip().lower() != "unknown"
        ]
        subline_parts = [f"Market cap: {market_cap_label}"] if market_cap_label else []
        subline_parts.extend(extras)
        rows.append(
            {
                "symbol": symbol,
                "company_name": company_name,
                "subline": " | ".join(subline_parts),
                "sparkline_data_uri": str(profile.get("sparkline_data_uri") or "").strip(),
                "note": str((item or {}).get("note") or "").strip(),
                "note_secondary": str((item or {}).get("note_secondary") or "").strip(),
            }
        )
    if not rows:
        return
    interactive_preview = _ticker_preview_clickable(click_target)
    show_context_column = any(row["note"] or row["note_secondary"] for row in rows)
    if not show_header and len(rows) == 1:
        row = rows[0]
        compact_spec = [2.8, 1.45] if interactive_preview else [0.9, 2.5, 1.45]
        compact_cols = st.columns(compact_spec, gap="small")
        if interactive_preview:
            with compact_cols[0]:
                _render_ticker_preview_identity(
                    row,
                    click_target=click_target,
                    key_prefix=key_prefix or "ticker_snapshot",
                    index=0,
                    click_bundle_id=click_bundle_id,
                )
                if row["subline"]:
                    st.caption(row["subline"])
                note_parts = [part for part in [row["note"], row["note_secondary"]] if part]
                if note_parts:
                    st.caption(" | ".join(note_parts))
            with compact_cols[1]:
                _render_ticker_snapshot_chart(
                    row["sparkline_data_uri"],
                    symbol=row["symbol"],
                    click_target="",
                    button_key=f"{key_prefix or 'ticker_snapshot'}_{row['symbol']}_inspect",
                )
            return
        with compact_cols[0]:
            st.markdown(f"**{row['symbol']}**")
        with compact_cols[1]:
            st.markdown(f"**{row['company_name']}**")
            if row["subline"]:
                st.caption(row["subline"])
            note_parts = [part for part in [row["note"], row["note_secondary"]] if part]
            if note_parts:
                st.caption(" | ".join(note_parts))
        with compact_cols[2]:
            _render_ticker_snapshot_chart(
                row["sparkline_data_uri"],
                symbol=row["symbol"],
                click_target=click_target,
                button_key=f"{key_prefix or 'ticker_snapshot'}_{row['symbol']}_inspect",
            )
        return
    if show_header:
        if interactive_preview:
            header_spec = [3.3, 1.6, 1.8] if show_context_column else [3.5, 1.6]
            header_cols = st.columns(header_spec, gap="small")
            header_cols[0].caption("Ticker")
            header_cols[1].caption("Chart")
            if show_context_column:
                header_cols[2].caption("Context")
        else:
            header_spec = [0.9, 2.5, 1.6, 1.8] if show_context_column else [0.9, 2.7, 1.6]
            header_cols = st.columns(header_spec, gap="small")
            header_cols[0].caption("Ticker")
            header_cols[1].caption("Name")
            header_cols[2].caption("Chart")
            if show_context_column:
                header_cols[3].caption("Context")
    for index, row in enumerate(rows):
        row_container = st.container(border=show_header or len(rows) > 1)
        with row_container:
            row_spec = [3.3, 1.6, 1.8] if interactive_preview and show_context_column else [3.5, 1.6] if interactive_preview else [0.9, 2.5, 1.6, 1.8] if show_context_column else [0.9, 2.7, 1.6]
            row_cols = st.columns(row_spec, gap="small")
            if interactive_preview:
                with row_cols[0]:
                    _render_ticker_preview_identity(
                        row,
                        click_target=click_target,
                        key_prefix=key_prefix or "ticker_snapshot",
                        index=index,
                        click_bundle_id=click_bundle_id,
                    )
                    if row["subline"]:
                        st.caption(row["subline"])
                with row_cols[1]:
                    _render_ticker_snapshot_chart(
                        row["sparkline_data_uri"],
                        symbol=row["symbol"],
                        click_target="",
                        button_key=f"{key_prefix or 'ticker_snapshot'}_{index}_{row['symbol']}_inspect",
                    )
            else:
                with row_cols[0]:
                    st.markdown(f"**{row['symbol']}**")
                with row_cols[1]:
                    st.markdown(f"**{row['company_name']}**")
                    if row["subline"]:
                        st.caption(row["subline"])
                with row_cols[2]:
                    _render_ticker_snapshot_chart(
                        row["sparkline_data_uri"],
                        symbol=row["symbol"],
                        click_target=click_target,
                        button_key=f"{key_prefix or 'ticker_snapshot'}_{index}_{row['symbol']}_inspect",
                    )
            if show_context_column:
                context_col_index = 2 if interactive_preview else 3
                with row_cols[context_col_index]:
                    if row["note"]:
                        st.write(row["note"])
                    if row["note_secondary"]:
                        st.caption(row["note_secondary"])


def _ensure_inline_loading_banner_styles() -> None:
    st.session_state["_inline_loading_banner_styles_version"] = _INLINE_LOADING_STYLE_VERSION
    st.markdown(
        """
        <style>
        .sn-inline-loading-banner {
            margin: 0.4rem 0 1rem 0;
            padding: 0.82rem 0.95rem 0.74rem 0.95rem;
            border-radius: 0.85rem;
            border: 1px solid rgba(148, 163, 184, 0.16);
            background: rgba(22, 28, 37, 0.92);
            box-shadow: 0 12px 26px rgba(5, 8, 12, 0.14);
        }
        .sn-inline-loading-title {
            color: #f3f5f7;
            font-size: 0.96rem;
            font-weight: 700;
            line-height: 1.25;
            margin-bottom: 0.2rem;
        }
        .sn-inline-loading-detail {
            color: #94a3b8;
            font-size: 0.78rem;
            line-height: 1.35;
            margin-bottom: 0.55rem;
        }
        .sn-inline-loading-track {
            position: relative;
            width: 100%;
            height: 0.34rem;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(148, 163, 184, 0.12);
        }
        .sn-inline-loading-bar {
            position: absolute;
            inset: 0 auto 0 0;
            width: 38%;
            border-radius: 999px;
            background: #a8b8c9;
            animation: sn-inline-loading-slide 1.35s ease-in-out infinite;
        }
        @keyframes sn-inline-loading-slide {
            0% { transform: translateX(-108%); }
            100% { transform: translateX(250%); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@contextmanager
def _inline_loading_banner(title: str, detail: str = ""):
    _ensure_inline_loading_banner_styles()
    placeholder = st.empty()
    safe_title = html.escape(str(title or "").strip())
    safe_detail = html.escape(str(detail or "").strip())
    detail_html = f"<div class='sn-inline-loading-detail'>{safe_detail}</div>" if safe_detail else ""
    placeholder.markdown(
        (
            "<div class='sn-inline-loading-banner'>"
            f"<div class='sn-inline-loading-title'>{safe_title}</div>"
            f"{detail_html}"
            "<div class='sn-inline-loading-track'><div class='sn-inline-loading-bar'></div></div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    try:
        yield
    finally:
        placeholder.empty()


def _load_attention_bundle_map(
    cfg: AppConfig,
    bundle_ids: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for bundle_id in list(dict.fromkeys(str(item or "").strip() for item in bundle_ids if str(item or "").strip())):
        out[bundle_id] = _safe_load_attention_research_bundle_cached(
            cfg,
            bundle_id,
            force_refresh=force_refresh,
        )
    return out


def _attention_session_key(prefix: str, identifier: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(identifier or "").strip())
    return f"{prefix}_{cleaned}" if cleaned else prefix


def _attention_bundle_session_cache_key(bundle_id: str, *, run_token: str = "") -> str:
    composite = str(bundle_id or "").strip()
    token = str(run_token or "").strip()
    if token:
        composite = f"{composite}::{token}"
    return _attention_session_key("attention_bundle_cache", composite)


def _has_cached_attention_bundle(bundle_id: str, *, run_token: str = "") -> bool:
    cache_key = _attention_bundle_session_cache_key(bundle_id, run_token=run_token)
    cached = st.session_state.get(cache_key)
    return isinstance(cached, dict) and bool(cached)


def _load_attention_research_bundle_session_cached(
    cfg: AppConfig,
    bundle_id: str,
    *,
    run_token: str = "",
    force_refresh: bool = False,
) -> dict[str, object]:
    normalized_bundle_id = str(bundle_id or "").strip()
    if not normalized_bundle_id:
        return {}
    cache_key = _attention_bundle_session_cache_key(normalized_bundle_id, run_token=run_token)
    if force_refresh:
        st.session_state.pop(cache_key, None)
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict) and cached:
        return cached
    bundle = _safe_load_attention_research_bundle_cached(
        cfg,
        normalized_bundle_id,
        force_refresh=force_refresh,
    )
    st.session_state[cache_key] = bundle
    return bundle


def _news_row_matches_symbol(value: object, target_symbols: set[str]) -> bool:
    if not target_symbols:
        return False
    if value is None:
        return False
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
    tokens: set[str] = set()
    for item in items:
        for token in str(item).replace("|", ",").split(","):
            cleaned = token.upper().strip()
            if cleaned and cleaned.lower() != "nan":
                tokens.add(cleaned)
    return bool(tokens & target_symbols)


def _load_related_news_from_database(
    ticker: str,
    *,
    related_symbols: list[str] | None = None,
    limit: int = 6,
) -> pd.DataFrame:
    frame, _ = load_latest_dataset_frame("news_articles")
    if frame.empty:
        return pd.DataFrame()
    targets = {
        str(symbol).upper().strip()
        for symbol in [ticker, *(related_symbols or [])]
        if str(symbol).strip()
    }
    if not targets:
        return pd.DataFrame()
    rows = frame.copy()
    if "symbols" in rows.columns:
        rows = rows[rows["symbols"].apply(lambda value: _news_row_matches_symbol(value, targets))].copy()
    elif "symbol" in rows.columns:
        rows["symbol"] = rows["symbol"].astype(str).str.upper().str.strip()
        rows = rows[rows["symbol"].isin(targets)].copy()
    else:
        return pd.DataFrame()
    if rows.empty:
        return rows
    if "published_at" in rows.columns:
        rows["published_at"] = pd.to_datetime(rows["published_at"], utc=True, errors="coerce")
        rows = rows.sort_values("published_at", ascending=False, na_position="last")
    subset_cols = [col for col in ["headline", "published_at", "url"] if col in rows.columns]
    if subset_cols:
        rows = rows.drop_duplicates(subset=subset_cols, keep="first")
    return rows.head(limit).reset_index(drop=True)


def _render_related_news_database_section(
    ticker: str,
    *,
    related_symbols: list[str] | None = None,
    limit: int = 6,
    heading: str = "Related News From Database",
) -> None:
    rows = _load_related_news_from_database(
        ticker,
        related_symbols=related_symbols,
        limit=limit,
    )
    st.markdown(f"**{heading}**")
    if rows.empty:
        st.caption("No related database-backed news was available in the latest materialized snapshot.")
        return
    st.caption("Source: materialized `news_articles` dataset")
    for index, (_, row) in enumerate(rows.iterrows()):
        headline = str(row.get("headline") or "Untitled").strip()
        source = str(row.get("source") or "News").strip()
        published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
        published_label = published_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published_at) else "n/a"
        url = str(row.get("url") or "").strip()
        excerpt = _clean_attention_text(row.get("summary") or row.get("description"))
        meta = " | ".join(part for part in [source, published_label] if part)
        _render_tracked_activity_link(
            headline,
            url,
            key=_activity_link_key(f"related_news_{ticker}_{index}", label=headline, url=url),
            surface="related_news_database",
            target_type="news_article",
            source=source,
            published_at=published_label,
            extra_detail={"ticker": str(ticker or "").upper().strip()},
        )
        if excerpt:
            st.caption(excerpt)
        if meta:
            st.caption(meta)


def _compact_background_fallback_text(ticker: str) -> str:
    target = str(ticker or "").upper().strip()
    return f"No relevant catalyst found in web coverage for {target} in the latest agentic run."


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
        background_text = f"Company background is not available in the latest materialized context for {str(ticker or '').upper().strip()}."
    if not happened_text:
        happened_text = fallback

    st.markdown("**Background**")
    st.write(background_text)

    st.markdown("**What Happened**")
    st.write(happened_text)

    st.markdown("**Evidence**")
    if not evidence_links:
        st.caption("No relevant evidence links were available in the latest agentic run.")
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


def _render_home_ticker_background_panel(
    cfg: AppConfig,
    ticker: str,
    *,
    force_data_refresh: bool,
    session_key: str = "home_selected_ticker",
    clear_mode_key: str = "",
    clear_mode_value: str = HOMEPAGE_V2_RESEARCH_PANEL,
    panel_title: str = "",
    panel_caption: str = "Loaded from the Home page ticker preview selection.",
    open_button_label: str = "Open Stock Investigator",
    clear_button_label: str = "Clear",
    clear_button_key: str = "",
    show_header: bool = True,
    show_container_border: bool = True,
) -> None:
    target = str(ticker or "").upper().strip()
    if not target:
        return
    show_open_button = bool(str(open_button_label or "").strip())
    show_clear_button = bool(str(clear_button_label or "").strip())
    with st.container(border=show_container_border):
        if show_header:
            if show_open_button or show_clear_button:
                header_cols = st.columns([5.4, 1.4 if show_open_button and show_clear_button else 0.7], gap="small")
                title_col = header_cols[0]
                action_col = header_cols[1]
            else:
                title_col = st.container()
                action_col = None
            with title_col:
                st.markdown(f"### {panel_title or f'{target} Background'}")
                if str(panel_caption or "").strip():
                    st.caption(panel_caption)
                taxonomy_summary = _taxonomy_summary_text(target)
                if taxonomy_summary:
                    st.caption(taxonomy_summary)
            if action_col is not None:
                with action_col:
                    if show_open_button and st.button(
                        open_button_label,
                        key=f"home_background_open_market_{target}",
                        use_container_width=True,
                    ):
                        _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": target})
                    if show_clear_button and st.button(
                        clear_button_label,
                        key=str(clear_button_key or f"home_background_clear_{target}_{session_key or 'home'}").strip(),
                        use_container_width=True,
                    ):
                        st.session_state.pop(session_key or "home_selected_ticker", None)
                        if clear_mode_key == "homepage_v2_active_panel":
                            _queue_homepage_v2_active_panel(clear_mode_value)
                        elif clear_mode_key:
                            st.session_state[clear_mode_key] = clear_mode_value
                        st.rerun()

        materialized_background: dict[str, object] = {}
        try:
            materialized_background = _load_attention_ticker_background_cached(
                cfg,
                target,
                force_refresh=force_data_refresh,
            )
        except Exception:
            materialized_background = {}

        if str(materialized_background.get("symbol") or "").upper().strip() == target:
            price_points = materialized_background.get("price_points")
            price_frame = pd.DataFrame(price_points if isinstance(price_points, list) else [])
            if not price_frame.empty and {"timestamp", "close"}.issubset(price_frame.columns):
                price_frame["timestamp"] = pd.to_datetime(price_frame["timestamp"], utc=True, errors="coerce")
                price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
                price_frame = price_frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
            if price_frame.empty:
                price_frame = _load_public_price_history_cached(
                    target,
                    days=180,
                    force_refresh=force_data_refresh,
                )
            if not price_frame.empty:
                price_fig = go.Figure(
                    go.Scatter(
                        x=price_frame["timestamp"],
                        y=price_frame["close"],
                        mode="lines",
                        name=target,
                        line={"color": "#38bdf8", "width": 2.3},
                    )
                )
                price_fig.update_layout(
                    template="plotly_dark",
                    height=260,
                    margin={"l": 12, "r": 12, "t": 8, "b": 12},
                    xaxis_title="",
                    yaxis_title="Price",
                    showlegend=False,
                )
                st.plotly_chart(price_fig, use_container_width=True)

            description = str(materialized_background.get("description_text") or "").strip()
            summary_lines = [
                str(item).strip()
                for item in list(materialized_background.get("news_summary_lines") or [])
                if str(item).strip()
            ]
            llm_headline = str(materialized_background.get("llm_headline") or "").strip()
            llm_summary_text = str(materialized_background.get("llm_summary_text") or "").strip()
            primary_context_text = str(materialized_background.get("context_story_text") or "").strip()
            company_background_text = str(materialized_background.get("company_background_text") or "").strip()

            background_summary = llm_summary_text or primary_context_text or company_background_text or description
            what_happened_summary = llm_headline or (summary_lines[0] if summary_lines else "") or description
            evidence_links = _collect_evidence_links(
                recent_headlines=list(materialized_background.get("recent_headlines") or []),
                limit=8,
            )
            _render_compact_background_sections(
                target,
                background_summary=background_summary,
                what_happened_summary=what_happened_summary,
                evidence_links=evidence_links,
            )
            _render_overview_fundamentals(
                cfg,
                target,
                force_data_refresh=force_data_refresh,
                asof_time_utc=materialized_background.get("asof_time_utc"),
            )
            return

        try:
            with st.spinner("Loading company background..."):
                news_payload = _load_recent_news_cached(
                    cfg,
                    target,
                    days=14,
                    limit=6,
                    force_refresh=force_data_refresh,
                )
                attention_context = _load_attention_context_cached(
                    cfg,
                    target,
                    force_refresh=force_data_refresh,
                )
                price = _load_price_history_cached(
                    cfg,
                    target,
                    days=180,
                    force_refresh=force_data_refresh,
                )
        except Exception as exc:
            st.warning(f"Could not load background for {target}: {exc}")
            return

        if isinstance(price, pd.DataFrame) and price.empty:
            price = _load_public_price_history_cached(
                target,
                days=180,
                force_refresh=force_data_refresh,
            )

        if isinstance(price, pd.DataFrame) and not price.empty and {"timestamp", "close"}.issubset(price.columns):
            price_fig = go.Figure(
                go.Scatter(
                    x=price["timestamp"],
                    y=price["close"],
                    mode="lines",
                    name=target,
                    line={"color": "#38bdf8", "width": 2.3},
                )
            )
            price_fig.update_layout(
                template="plotly_dark",
                height=260,
                margin={"l": 12, "r": 12, "t": 8, "b": 12},
                xaxis_title="",
                yaxis_title="Price",
                showlegend=False,
            )
            st.plotly_chart(price_fig, use_container_width=True)

        news_summary = summarize_recent_news(target, news_payload)
        summary_lines = [
            str(item).strip()
            for item in list(news_summary.get("summary_lines") or [])
            if str(item).strip()
        ]
        llm_headline = str(attention_context.get("llm_headline") or "").strip()
        llm_summary_text = str(attention_context.get("llm_summary_text") or "").strip()
        primary_context_text = str(attention_context.get("context_story_text") or "").strip()
        background_summary = llm_summary_text or primary_context_text
        what_happened_summary = llm_headline or (summary_lines[0] if summary_lines else "")
        evidence_links = _collect_evidence_links(
            articles=news_summary.get("articles", pd.DataFrame()),
            limit=8,
        )
        _render_compact_background_sections(
            target,
            background_summary=background_summary,
            what_happened_summary=what_happened_summary,
            evidence_links=evidence_links,
        )
        _render_overview_fundamentals(
            cfg,
            target,
            force_data_refresh=force_data_refresh,
            asof_time_utc=attention_context.get("asof_time_utc"),
        )

def _parse_drilldown_params(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    blob = str(raw or "").strip()
    if not blob:
        return {}
    try:
        parsed = json.loads(blob)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_scalar(value: object, *, digits: int = 1, suffix: str = "", signed: bool = False) -> str:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "n/a"
    sign = "+" if signed else ""
    return f"{float(numeric):{sign},.{digits}f}{suffix}"


def _attention_snapshot_label(frame: pd.DataFrame) -> str:
    if frame.empty or "asof_time_utc" not in frame.columns:
        return "n/a"
    timestamps = pd.to_datetime(frame["asof_time_utc"], utc=True, errors="coerce").dropna()
    if timestamps.empty:
        return "n/a"
    return timestamps.max().strftime("%Y-%m-%d %H:%M UTC")


def _attention_home_bundle_preview(
    item: dict[str, object],
    *,
    bundle: dict[str, object] | None = None,
) -> dict[str, str]:
    return attention_surface_module.attention_home_bundle_preview(
        item if isinstance(item, dict) else {},
        bundle if isinstance(bundle, dict) else None,
    )


def _attention_home_surface_summary(
    preview: dict[str, str],
    *,
    is_event: bool,
) -> str:
    return attention_surface_module.attention_home_surface_summary(
        preview if isinstance(preview, dict) else {},
        is_event=is_event,
    )


def _attention_mover_card_title(mover: dict[str, object]) -> str:
    return attention_mover_card_title_service(mover if isinstance(mover, dict) else {})


def _attention_bundle_title(bundle: dict[str, object], *, fallback: dict[str, object] | None = None) -> str:
    bundle_type = str((bundle or {}).get("bundle_type") or "").strip().lower()
    if bundle_type == "event":
        return str((bundle or {}).get("event_title") or (fallback or {}).get("event_title") or "Market event").strip()

    headline = str((bundle or {}).get("headline") or (fallback or {}).get("headline") or "").strip()
    if headline:
        return headline
    symbol = str((bundle or {}).get("symbol") or (fallback or {}).get("symbol") or "").strip().upper()
    if symbol:
        return symbol
    return "Research bundle"


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


def _queue_homepage_v2_active_panel(panel: str) -> None:
    normalized_panel = str(panel or "").strip().lower()
    if normalized_panel not in {HOMEPAGE_V2_RESEARCH_PANEL, HOMEPAGE_V2_COMPANY_PANEL}:
        return
    st.session_state["_pending_homepage_v2_active_panel"] = normalized_panel


def _consume_homepage_v2_pending_panel() -> None:
    pending_panel = str(st.session_state.pop("_pending_homepage_v2_active_panel", "") or "").strip().lower()
    if pending_panel in {HOMEPAGE_V2_RESEARCH_PANEL, HOMEPAGE_V2_COMPANY_PANEL}:
        st.session_state["homepage_v2_active_panel"] = pending_panel


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


def _open_workspace_section(section_name: str) -> None:
    target = _normalize_workspace_section(section_name)
    if not target:
        return
    st.session_state["_pending_workspace_section"] = target
    st.rerun()


def _open_homepage_research_bundle_from_omnibar(bundle_id: str, symbols: list[str] | None = None) -> None:
    normalized_bundle_id = str(bundle_id or "").strip()
    if not normalized_bundle_id:
        _open_workspace_section("Home")
        return
    _select_homepage_v2_bundle(normalized_bundle_id, symbols=symbols, title=normalized_bundle_id)
    _queue_homepage_v2_active_panel(HOMEPAGE_V2_RESEARCH_PANEL)
    st.session_state["_pending_workspace_section"] = "Home"
    st.rerun()


def _open_homepage_company_from_omnibar(symbol: str, *, bundle_id: str = "") -> None:
    normalized_symbol = _set_workspace_ticker(symbol)
    if not normalized_symbol:
        return
    st.session_state["homepage_v2_selected_ticker"] = normalized_symbol
    normalized_bundle_id = str(bundle_id or "").strip()
    if normalized_bundle_id:
        st.session_state["homepage_v2_selected_bundle_id"] = normalized_bundle_id
    _queue_homepage_v2_active_panel(HOMEPAGE_V2_COMPANY_PANEL)
    st.session_state["_pending_workspace_section"] = "Home"
    st.rerun()


def _omnibar_normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _omnibar_trim(text: object, limit: int = 140) -> str:
    clean = _omnibar_normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _omnibar_exact_ticker_candidate(query: str) -> str:
    normalized = _omnibar_normalize_text(query).upper()
    if not normalized or " " in normalized:
        return ""
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,5}", normalized):
        return normalized
    return ""


def _omnibar_looks_like_agent_prompt(query: str) -> bool:
    normalized = _omnibar_normalize_text(query).lower()
    if not normalized:
        return False
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        return False
    if "?" in normalized:
        return True
    prompt_markers = {
        "after",
        "analyze",
        "analysis",
        "before",
        "compare",
        "explain",
        "how",
        "impact",
        "implications",
        "outlook",
        "reaction",
        "setup",
        "should",
        "thesis",
        "view",
        "vs",
        "versus",
        "what",
        "why",
    }
    return len(tokens) >= 4 or any(token in prompt_markers for token in tokens)


def _omnibar_match_score(query: str, candidates: list[str]) -> float:
    normalized_query = _omnibar_normalize_text(query).lower()
    if not normalized_query:
        return 0.0
    text = " ".join(_omnibar_normalize_text(candidate).lower() for candidate in candidates if _omnibar_normalize_text(candidate))
    if not text:
        return 0.0
    if normalized_query == text:
        return 1.0
    if normalized_query in text:
        coverage = len(normalized_query) / max(len(text), len(normalized_query), 1)
        return min(0.95, 0.74 + coverage * 0.18)
    query_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_query) if token]
    if not query_tokens:
        return 0.0
    hits = sum(1 for token in query_tokens if token in text)
    if hits <= 0:
        return 0.0
    coverage = hits / max(len(query_tokens), 1)
    return min(0.88, 0.34 + coverage * 0.42 + min(0.12, hits * 0.05))


def _build_agentic_omnibar_symbol_catalog(
    beats: list[dict[str, object]],
    symbol_name_map: dict[str, str],
) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    for beat in beats:
        bundle_id = str(beat.get("bundle_id") or "").strip()
        sentence = _omnibar_normalize_text(beat.get("sentence"))
        summary = _omnibar_normalize_text(beat.get("summary"))
        for raw_symbol in list(beat.get("symbols") or []):
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol:
                continue
            entry = catalog.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "company_name": "",
                    "bundle_ids": [],
                    "beat_titles": [],
                    "summaries": [],
                },
            )
            if bundle_id and bundle_id not in entry["bundle_ids"]:
                entry["bundle_ids"].append(bundle_id)
            if sentence and sentence not in entry["beat_titles"]:
                entry["beat_titles"].append(sentence)
            if summary and summary not in entry["summaries"]:
                entry["summaries"].append(summary)
    for symbol, entry in catalog.items():
        entry["company_name"] = str(symbol_name_map.get(symbol) or "").strip()
    return catalog


def _build_agentic_omnibar_results(
    cfg: AppConfig,
    query: str,
    beats: list[dict[str, object]],
    symbol_catalog: dict[str, dict[str, object]],
    *,
    force_data_refresh: bool,
) -> list[dict[str, object]]:
    normalized_query = _omnibar_normalize_text(query)
    if not normalized_query:
        return []

    results: list[dict[str, object]] = []
    exact_symbol = _omnibar_exact_ticker_candidate(normalized_query)
    if exact_symbol:
        symbol_entry = dict(symbol_catalog.get(exact_symbol) or {})
        if not symbol_entry:
            fallback_name_map = _load_symbol_name_map(
                cfg,
                [exact_symbol],
                force_refresh=force_data_refresh,
            )
            symbol_entry = {
                "symbol": exact_symbol,
                "company_name": str(fallback_name_map.get(exact_symbol) or "").strip(),
                "bundle_ids": [],
                "beat_titles": [],
                "summaries": [],
            }
        company_name = str(symbol_entry.get("company_name") or "").strip()
        bundle_ids = list(symbol_entry.get("bundle_ids") or [])
        subtitle_parts = []
        if company_name:
            subtitle_parts.append(company_name)
        if bundle_ids:
            subtitle_parts.append("linked to retained research")
        else:
            subtitle_parts.append("open ticker workspace")
        results.append(
            {
                "kind": "symbol",
                "ref": exact_symbol,
                "label": exact_symbol,
                "subtitle": " | ".join(subtitle_parts),
                "score": 1.0,
                "symbol": exact_symbol,
                "company_name": company_name,
                "bundle_ids": bundle_ids,
            }
        )

    normalized_query_lower = normalized_query.lower()
    for release in OMNIBAR_MACRO_RELEASES:
        aliases = [str(item).lower().strip() for item in list(release.get("aliases") or []) if str(item).strip()]
        score = 0.0
        if normalized_query_lower in aliases:
            score = 0.99
        elif any(normalized_query_lower and normalized_query_lower in alias for alias in aliases):
            score = 0.84
        elif any(alias and alias in normalized_query_lower for alias in aliases):
            score = 0.8
        if score <= 0:
            continue
        results.append(
            {
                "kind": "macro_release",
                "ref": str(release.get("release_id") or "").strip(),
                "label": str(release.get("label") or "Macro Release").strip(),
                "subtitle": str(release.get("subtitle") or "").strip(),
                "score": score,
            }
        )

    for beat in beats:
        bundle_id = str(beat.get("bundle_id") or "").strip()
        sentence = str(beat.get("sentence") or "").strip()
        summary = str(beat.get("summary") or "").strip()
        score = 1.0 if bundle_id and normalized_query == bundle_id else _omnibar_match_score(
            normalized_query,
            [sentence, summary, " ".join(list(beat.get("symbols") or [])), bundle_id],
        )
        if score < 0.46:
            continue
        results.append(
            {
                "kind": "bundle",
                "ref": bundle_id or sentence,
                "label": sentence or "Research bundle",
                "subtitle": _omnibar_trim(summary or "Retained research bundle", limit=180),
                "score": min(score, 0.96 if bundle_id and normalized_query != bundle_id else score),
                "bundle_id": bundle_id,
                "symbols": [str(item).upper().strip() for item in list(beat.get("symbols") or []) if str(item).strip()],
            }
        )

    for symbol, entry in symbol_catalog.items():
        if exact_symbol and symbol == exact_symbol:
            continue
        company_name = str(entry.get("company_name") or "").strip()
        score = _omnibar_match_score(
            normalized_query,
            [
                symbol,
                company_name,
                " ".join(list(entry.get("beat_titles") or [])),
                " ".join(list(entry.get("summaries") or [])),
            ],
        )
        if score < 0.52:
            continue
        subtitle_parts = []
        if company_name:
            subtitle_parts.append(company_name)
        beat_titles = list(entry.get("beat_titles") or [])
        if beat_titles:
            subtitle_parts.append(_omnibar_trim(beat_titles[0], limit=96))
        results.append(
            {
                "kind": "symbol",
                "ref": symbol,
                "label": symbol,
                "subtitle": " | ".join(subtitle_parts) if subtitle_parts else "Open ticker workspace",
                "score": min(score, 0.92),
                "symbol": symbol,
                "company_name": company_name,
                "bundle_ids": list(entry.get("bundle_ids") or []),
            }
        )

    deduped: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in sorted(results, key=lambda row: (-float(row.get("score") or 0.0), str(row.get("kind") or ""), str(row.get("label") or ""))):
        dedupe_key = (str(item.get("kind") or ""), str(item.get("ref") or ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(item)
    return deduped[:6]


def _agentic_omnibar_confidence_band(intent: str, top_score: float) -> str:
    if intent == "navigate" or top_score >= 0.92:
        return "high"
    if top_score >= 0.7:
        return "medium"
    return "low"


def _extract_agentic_omnibar_context_items(results: list[dict[str, object]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        kind = str(result.get("kind") or "").strip()
        if kind == "symbol":
            ref = str(result.get("symbol") or result.get("ref") or "").strip()
        elif kind == "bundle":
            ref = str(result.get("bundle_id") or result.get("ref") or "").strip()
        else:
            ref = str(result.get("ref") or "").strip()
        if not kind or not ref:
            continue
        dedupe_key = (kind, ref)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            {
                "kind": kind,
                "ref": ref,
                "label": str(result.get("label") or ref).strip(),
            }
        )
        if len(items) >= 4:
            break
    return items


def _build_agentic_omnibar_resolution(
    cfg: AppConfig,
    query: str,
    preferred_mode: str,
    beats: list[dict[str, object]],
    symbol_catalog: dict[str, dict[str, object]],
    *,
    force_data_refresh: bool,
) -> dict[str, object]:
    normalized_query = _omnibar_normalize_text(query)
    normalized_mode = str(preferred_mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "search", "agent"}:
        normalized_mode = "auto"
    search_results = _build_agentic_omnibar_results(
        cfg,
        normalized_query,
        beats,
        symbol_catalog,
        force_data_refresh=force_data_refresh,
    )
    top_score = float(search_results[0].get("score") or 0.0) if search_results else 0.0
    top_kind = str(search_results[0].get("kind") or "").strip() if search_results else ""
    looks_like_agent_prompt = _omnibar_looks_like_agent_prompt(normalized_query)

    if normalized_mode == "agent":
        intent = "agent"
    elif normalized_mode == "search":
        intent = "navigate" if top_kind in {"symbol", "macro_release"} and top_score >= 0.96 else "search"
    else:
        if top_kind in {"symbol", "macro_release"} and top_score >= 0.96:
            intent = "navigate"
        elif looks_like_agent_prompt:
            intent = "agent"
        elif search_results:
            intent = "search"
        else:
            intent = "ambiguous"

    request_seed = f"{time.time_ns()}::{normalized_mode}::{normalized_query}"
    request_id = f"omni_{hashlib.sha1(request_seed.encode('utf-8')).hexdigest()[:10]}"
    return {
        "request_id": request_id,
        "query": normalized_query,
        "preferred_mode": normalized_mode,
        "intent": intent,
        "policy_version": OMNIBAR_POLICY_VERSION,
        "confidence_band": _agentic_omnibar_confidence_band(intent, top_score),
        "search_results": search_results,
        "context_items": _extract_agentic_omnibar_context_items(search_results),
    }


def _build_agentic_omnibar_assistant_message(query: str, resolution: dict[str, object]) -> str:
    agent_result = dict(resolution.get("agent_result") or {})
    agent_answer = str(agent_result.get("answer_markdown") or "").strip()
    if agent_answer:
        return agent_answer

    answer_payload = dict(resolution.get("answer_payload") or {})
    answer_lines = [str(item).strip() for item in list(answer_payload.get("lines") or []) if str(item).strip()]
    answer_title = str(answer_payload.get("title") or "").strip()
    answer_caption = str(answer_payload.get("caption") or "").strip()
    if answer_lines:
        parts: list[str] = []
        if answer_title:
            parts.append(answer_title)
        parts.extend(answer_lines)
        if answer_caption:
            parts.append(answer_caption)
        return "\n\n".join(parts)

    context_items = list(resolution.get("context_items") or [])
    context_labels = ", ".join(str(item.get("label") or "").strip() for item in context_items if str(item.get("label") or "").strip())
    steps: list[str] = []
    if any(str(item.get("kind") or "") == "symbol" for item in context_items):
        steps.append("- Open Stock Investigator for ticker-specific technical and company context.")
    if any(str(item.get("kind") or "") == "bundle" for item in context_items):
        steps.append("- Open Home research for retained evidence and linked symbols.")
    if any(str(item.get("kind") or "") == "macro_release" for item in context_items):
        steps.append(f"- Open {BROAD_ECONOMY_SECTION} to compare the prompt against the macro release backdrop.")
    if not steps:
        steps.append("- Refine the prompt with a ticker, macro release, or concrete market question to tighten the first pass.")
    context_line = f"Starting context: {context_labels}." if context_labels else "Starting context is still broad."
    return (
        "Routing this to agent mode because it reads like analysis rather than a direct jump.\n\n"
        f"{context_line}\n\n"
        "Suggested next steps:\n"
        + "\n".join(steps)
        + f"\n\nPrompt: {_omnibar_trim(query, limit=220)}"
    )


def _append_agentic_omnibar_turn(query: str, preferred_mode: str, resolution: dict[str, object]) -> None:
    normalized_query = _omnibar_normalize_text(query)
    normalized_mode = str(preferred_mode or "auto").strip().lower()
    signature = f"{normalized_mode}::{normalized_query.lower()}"
    if st.session_state.get("agentic_omnibar_last_signature") == signature:
        return
    transcript = list(st.session_state.get("agentic_omnibar_transcript") or [])
    transcript.append({"role": "user", "content": normalized_query})
    transcript.append(
        {
            "role": "assistant",
            "content": _build_agentic_omnibar_assistant_message(normalized_query, resolution),
        }
    )
    st.session_state["agentic_omnibar_transcript"] = transcript[-10:]
    st.session_state["agentic_omnibar_last_signature"] = signature


def _dispatch_agentic_omnibar_progress(
    progress_callback: object | None,
    *,
    stage: str,
    message: str,
    progress: float,
    **extra: object,
) -> None:
    if not callable(progress_callback):
        return
    payload: dict[str, object] = {
        "stage": str(stage or "").strip(),
        "message": str(message or "").strip(),
        "progress": max(0.0, min(float(progress or 0.0), 1.0)),
    }
    payload.update(extra)
    try:
        progress_callback(payload)
    except Exception:
        return


def _humanize_agentic_omnibar_tool_name(tool_name: object) -> str:
    clean_name = str(tool_name or "").strip().replace("_", " ")
    return clean_name if clean_name else "the next data source"


def _agentic_omnibar_progress_message(event: dict[str, object]) -> str:
    stage = str(event.get("stage") or "").strip().lower()
    intent = str(event.get("intent") or "").strip().lower()
    matches = int(event.get("matches") or 0)
    tool_label = _humanize_agentic_omnibar_tool_name(event.get("tool_name"))

    if stage == "resolve_start":
        return "Checking for direct matches."
    if stage == "intent_ready":
        if intent == "agent":
            return "This needs analysis, so I am gathering evidence."
        if intent == "search":
            return "Found direct matches." if matches > 0 else "No direct match yet. Expanding the search."
        if intent == "ambiguous":
            return "This could be a lookup or an analysis request."
        return "Finished reading the request."
    if stage == "agent_dispatch":
        return "Starting the analysis."
    if stage == "start":
        return "Setting up the analysis agent."
    if stage == "tool_catalog_ready":
        return "Loading the available data sources."
    if stage == "hidden_step_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Still preparing evidence... ({elapsed}s)"
    if stage == "hidden_step_timeout":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"One preparation step timed out after {elapsed}s. Continuing."
    if stage == "planner_start":
        return "Deciding the next step."
    if stage == "planner_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Still thinking... ({elapsed}s)"
    if stage == "planner_reasoning":
        reasoning = str(event.get("reasoning") or "").strip()
        return reasoning if reasoning else "Thinking about the next step."
    if stage == "tool_start":
        return f"Checking {tool_label}."
    if stage == "tool_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Still checking {tool_label}... ({elapsed}s)"
    if stage == "tool_timeout":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"{tool_label.capitalize()} timed out after {elapsed}s. Moving on."
    if stage == "tool_complete":
        return f"Added evidence from {tool_label}."
    if stage == "tool_failed":
        return f"{tool_label.capitalize()} did not return usable data. Trying another path."
    if stage in {"planner_final", "final_synthesis_start"}:
        return "Writing the answer."
    if stage == "failed":
        return "The analysis hit an error."
    if stage in {"completed", "status"}:
        return "Answer ready."

    fallback = str(event.get("message") or "").strip()
    return fallback if fallback else "Working through the request."


def _looks_like_transient_agent_transport_error(text: object) -> bool:
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return False
    markers = (
        "remotedisconnected",
        "remote end closed connection without response",
        "connection aborted",
        "connection reset",
        "connectionerror",
        "readtimeout",
        "read timed out",
        "temporarily unavailable",
    )
    return any(marker in cleaned for marker in markers)


def _safe_research_agent_error_text(error: object) -> str:
    raw = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error or "")
    if _looks_like_transient_agent_transport_error(raw):
        return (
            "A research source connection dropped before it returned a response. "
            "This is usually transient; rerun the request to retry the evidence fetch."
        )
    return " ".join(raw.split()).strip() or "The research agent failed before it could produce an answer."


_METRIC_STOP_WORDS = frozenset({
    "THE", "AND", "FOR", "THIS", "BUT", "NOT", "WITH", "FROM", "ETF", "RSI",
    "USD", "EUR", "GDP", "CPI", "PCE", "NFP", "YOY", "QOQ", "MOM", "EPS",
    "IPO", "CEO", "CFO", "COO", "SEC", "FED", "OIL", "GAS", "ALL", "ANY",
    "BPS", "RHS", "LHS", "AVG", "MAX", "MIN", "NET", "PRE", "YTD",
})


def _extract_answer_metrics(answer_text: str) -> list[dict[str, str]]:
    """Extract ticker:percentage pairs from answer text for metric cards."""
    metrics: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b([A-Z]{2,5})\b[^a-zA-Z\n]*?([+-]?\d+\.?\d*%)", answer_text):
        ticker = match.group(1)
        value = match.group(2)
        if ticker in seen or ticker in _METRIC_STOP_WORDS:
            continue
        seen.add(ticker)
        try:
            numeric = float(value.rstrip("%").replace("+", ""))
        except ValueError:
            numeric = 0.0
        metrics.append({"ticker": ticker, "value": value, "direction": "up" if numeric >= 0 else "down"})
    return metrics[:6]


def _render_answer_metrics(metrics: list[dict[str, str]]) -> None:
    """Render extracted ticker metrics as a horizontal strip of cards."""
    if not metrics:
        return
    cols = st.columns(min(len(metrics), 6))
    for i, m in enumerate(metrics):
        with cols[i]:
            st.metric(m["ticker"], m["value"])


def _render_source_evidence_strip(sources: list[dict[str, str]]) -> None:
    """Render Perplexity-style numbered source cards."""
    unique: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(src)
    if not unique:
        return
    display = unique[:5]
    cols = st.columns(min(len(display), 5))
    for i, src in enumerate(display):
        url = str(src.get("url") or "").strip()
        label = str(src.get("label") or "").strip()
        domain = url.split("//")[-1].split("/")[0].replace("www.", "")[:30]
        short_label = (label[:45] + "...") if len(label) > 48 else label if label else domain
        with cols[i]:
            with st.container(border=True):
                st.markdown(f"**{i + 1}**&ensp;[{short_label}]({url})")
                st.caption(domain)


def _render_thinking_trace_content(
    trace: list[dict[str, object]],
    agent_result: dict[str, object] | None = None,
    key_prefix: str = "trace",
) -> None:
    """Render verbose thinking trace inside an expanded st.status widget."""
    if not trace:
        tool_calls = list((agent_result or {}).get("tool_calls") or [])
        for tc in tool_calls:
            tool_name = str(tc.get("tool_name") or "tool")
            tc_status = str(tc.get("status") or "unknown")
            try:
                args_text = json.dumps(tc.get("arguments") or {}, sort_keys=True, default=str)
            except Exception:
                args_text = str(tc.get("arguments") or {})
            st.code(f"{tool_name}({args_text})", language="python")
            preview = str((tc.get("result_summary") or {}).get("preview_text") or "").strip()
            if preview:
                st.caption(f"← [{tc_status}] {preview}")
            else:
                st.caption(f"← [{tc_status}]")
        return

    for step_idx, step in enumerate(trace):
        step_type = str(step.get("type") or "")
        if step_type == "reasoning":
            st.markdown(f"*{step.get('text', '')}*")
        elif step_type == "model_reasoning_trace":
            trace_text = str(step.get("text") or "").strip()
            if trace_text:
                st.code(trace_text, language="text")
        elif step_type == "tool_start":
            st.code(f"{step.get('tool_name', '')}({step.get('args_text', '')})", language="python")
        elif step_type == "tool_complete":
            preview = str(step.get("preview") or "")
            if preview:
                st.caption(f"← {preview}")
            rp = step.get("render_payload")
            if isinstance(rp, dict) and rp.get("kind") == "chart_model":
                try:
                    import plotly.graph_objects as _go_trace
                except ImportError:
                    _go_trace = None
                if _go_trace is not None:
                    try:
                        chart_model = dict(rp.get("chart_model") or {})
                        traces_list = list(chart_model.get("traces") or [])
                        datasets = dict(chart_model.get("datasets") or {})
                        if traces_list and datasets:
                            fig = _go_trace.Figure()
                            for trace_spec in traces_list[:6]:
                                trace_spec = dict(trace_spec)
                                ds_name = str(trace_spec.get("dataset") or "")
                                x_col = str(trace_spec.get("x") or "")
                                y_col = str(trace_spec.get("y") or "")
                                trace_type = str(trace_spec.get("type") or "scatter").lower()
                                label = str(trace_spec.get("name") or trace_spec.get("label") or y_col)
                                ds_rows = list(datasets.get(ds_name) or [])
                                if not ds_rows or not x_col or not y_col:
                                    continue
                                x_vals = [row.get(x_col) for row in ds_rows if isinstance(row, dict)]
                                y_vals = [row.get(y_col) for row in ds_rows if isinstance(row, dict)]
                                if trace_type == "bar":
                                    fig.add_trace(_go_trace.Bar(x=x_vals, y=y_vals, name=label))
                                else:
                                    fig.add_trace(_go_trace.Scatter(x=x_vals, y=y_vals, mode="lines", name=label))
                            chart_title = str(chart_model.get("title") or "")
                            fig.update_layout(
                                height=250,
                                margin=dict(l=30, r=10, t=30 if chart_title else 10, b=20),
                                title_text=chart_title if chart_title else None,
                            )
                            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart_{step_idx}")
                    except Exception:
                        pass
            links = step.get("source_links")
            if isinstance(links, list) and links:
                link_parts = []
                for link in links[:5]:
                    if isinstance(link, dict):
                        url = str(link.get("url") or "").strip()
                        lbl = str(link.get("label") or url).strip()
                        if url:
                            link_parts.append(f"[{lbl}]({url})")
                if link_parts:
                    st.caption("Sources: " + " · ".join(link_parts))
        elif step_type == "message":
            st.markdown(f"- {step.get('text', '')}")


def _render_inline_search_results(
    results: list[dict[str, object]],
    request_id: str,
) -> None:
    """Render search results as compact inline cards with one primary action."""
    for result in results[:4]:
        kind = str(result.get("kind") or "").strip()
        label = str(result.get("label") or "Result").strip()
        subtitle = str(result.get("subtitle") or "").strip()
        with st.container(border=True):
            result_cols = st.columns([5, 2])
            with result_cols[0]:
                st.markdown(f"**{label}**")
                if subtitle:
                    st.caption(subtitle)
            with result_cols[1]:
                if kind == "symbol":
                    symbol = str(result.get("symbol") or result.get("ref") or "").upper().strip()
                    if st.button("Open →", key=f"{request_id}_{kind}_{symbol}_open", use_container_width=True, disabled=not bool(symbol)):
                        _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": symbol})
                elif kind == "bundle":
                    bundle_id = str(result.get("bundle_id") or result.get("ref") or "").strip()
                    symbols = [str(item).upper().strip() for item in list(result.get("symbols") or []) if str(item).strip()]
                    if st.button("Open →", key=f"{request_id}_{kind}_{bundle_id}_open", use_container_width=True, disabled=not bool(bundle_id)):
                        _open_homepage_research_bundle_from_omnibar(bundle_id, symbols=symbols)
                elif kind == "macro_release":
                    ref = str(result.get("ref") or "").strip()
                    if st.button("Open →", key=f"{request_id}_{kind}_{ref}_open", use_container_width=True):
                        _open_workspace_section(BROAD_ECONOMY_SECTION)


def _render_omnibar_welcome(beats: list[dict[str, object]]) -> None:
    """Render the empty-state welcome screen with clickable example prompts."""
    st.markdown("#### What would you like to research?")
    examples: list[str] = []
    for beat in beats[:2]:
        sentence = str(beat.get("sentence") or "").strip()
        if sentence:
            examples.append(sentence[:65])
    defaults = ["What's driving oil stocks today?", "Analyze semis after CPI", "Compare banks vs software"]
    while len(examples) < 3 and defaults:
        examples.append(defaults.pop(0))
    cols = st.columns(min(len(examples), 3))
    for i, ex in enumerate(examples[:3]):
        with cols[i]:
            if st.button(ex, key=f"omnibar_welcome_{i}", use_container_width=True):
                st.session_state["_omnibar_pending_query"] = ex
                st.rerun()


def _render_agent_response_message(msg: dict[str, object]) -> None:
    """Render a saved assistant message from chat history."""
    trace = list(msg.get("thinking_trace") or [])
    agent_result = dict(msg.get("agent_result") or {})
    error = str(msg.get("error") or "").strip()

    # Error message
    if error:
        st.error(f"The research agent encountered an error: {error}")

    # Source evidence strip
    sources = list(msg.get("source_links") or [])
    if sources:
        _render_source_evidence_strip(sources)

    # Key metrics
    answer = str(msg.get("content") or "").strip()
    if answer and not error:
        metrics = _extract_answer_metrics(answer)
        if metrics:
            _render_answer_metrics(metrics)

    # Answer text
    if answer and not error:
        st.markdown(answer)

    # Confidence and limitations
    confidence = str(msg.get("confidence") or "").strip()
    limitations = [str(item).strip() for item in list(msg.get("limitations") or []) if str(item).strip()]
    if confidence or limitations:
        footer_parts = []
        if confidence:
            footer_parts.append(f"Confidence: **{confidence}**")
        if limitations:
            footer_parts.append(" · ".join(limitations[:2]))
        st.caption(" | ".join(footer_parts))

    # Thinking trace — explicit expander
    msg_id = str(msg.get("msg_id") or "hist")
    if trace:
        with st.expander(f"Thinking Trace ({len(trace)} steps)", expanded=False):
            _render_thinking_trace_content(trace, agent_result, key_prefix=f"hist_{msg_id}")

    # Search results
    search_results = list(msg.get("search_results") or [])
    if search_results:
        _render_inline_search_results(search_results, msg_id)

    # Dig deeper
    if answer and not error:
        if st.button("Dig deeper", key=f"dig_{msg.get('msg_id', '')}"):
            original_query = str(msg.get("query") or "").strip()
            st.session_state["_omnibar_pending_query"] = f"{original_query} — verify and expand with more evidence"
            st.rerun()


def _run_agentic_omnibar_resolution(
    cfg: AppConfig,
    query: str,
    preferred_mode: str,
    beats: list[dict[str, object]],
    symbol_catalog: dict[str, dict[str, object]],
    *,
    force_data_refresh: bool,
    progress_callback: object | None = None,
    conversation_history: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
    normalized_query = _omnibar_normalize_text(query)
    agent_query, followup_resolved = omnibar_agent_service.resolve_conversation_followup_query(
        normalized_query,
        conversation_history,
    )
    resolution_query = agent_query if followup_resolved else normalized_query
    resolution_mode = "agent" if followup_resolved else preferred_mode
    _dispatch_agentic_omnibar_progress(
        progress_callback,
        stage="resolve_start",
        message="Resolving intent and direct matches.",
        progress=0.08,
        query=normalized_query,
    )
    if followup_resolved:
        _dispatch_agentic_omnibar_progress(
            progress_callback,
            stage="conversation_followup_resolved",
            message="Resolved the reply against the prior chat turn.",
            progress=0.14,
            original_query=normalized_query,
            resolved_query=agent_query,
        )
    resolution = _build_agentic_omnibar_resolution(
        cfg,
        resolution_query,
        resolution_mode,
        beats,
        symbol_catalog,
        force_data_refresh=force_data_refresh,
    )
    if followup_resolved:
        resolution["followup_resolved"] = True
        resolution["original_query"] = normalized_query
        resolution["resolved_query"] = agent_query
    router_intent = str(resolution.get("intent") or "search").strip().lower()
    if router_intent != "agent":
        resolution["router_intent"] = router_intent
        resolution["intent"] = "agent"
        resolution["routing_note"] = "Deterministic routing supplied context; the agent remains responsible for the response."
    _dispatch_agentic_omnibar_progress(
        progress_callback,
        stage="intent_ready",
        message=f"Resolved intent: {str(resolution.get('intent') or 'search').capitalize()}.",
        progress=0.26,
        intent=str(resolution.get("intent") or "search"),
        router_intent=router_intent,
        matches=len(list(resolution.get("search_results") or [])),
    )
    if str(resolution.get("intent") or "") == "agent":
        _dispatch_agentic_omnibar_progress(
            progress_callback,
            stage="agent_dispatch",
            message="Running shared agent across available modules.",
            progress=0.34,
        )

        def _agent_progress_bridge(event: dict[str, object]) -> None:
            agent_progress = max(0.0, min(float(event.get("progress") or 0.0), 1.0))
            bridged_event = dict(event)
            bridged_event["progress"] = 0.34 + (agent_progress * 0.62)
            _dispatch_agentic_omnibar_progress(progress_callback, **bridged_event)

        resolution["agent_result"] = omnibar_agent_service.run_omnibar_agent(
            query=agent_query,
            force_refresh=force_data_refresh,
            progress_callback=_agent_progress_bridge,
            conversation_history=conversation_history,
        )
    st.session_state["agentic_omnibar_resolution"] = resolution
    if str(resolution.get("intent") or "") == "agent":
        _append_agentic_omnibar_turn(query, preferred_mode, resolution)
    _dispatch_agentic_omnibar_progress(
        progress_callback,
        stage="completed",
        message="Chat + Search response ready.",
        progress=1.0,
        intent=str(resolution.get("intent") or "search"),
    )
    return resolution


def _build_agentic_omnibar_tool_figure(render_payload: dict[str, object]) -> go.Figure | None:
    kind = str(render_payload.get("kind") or "").strip().lower()
    if kind == "timeseries":
        x_values = [str(item).strip() for item in list(render_payload.get("x") or []) if str(item).strip()]
        raw_y_values = list(render_payload.get("y") or [])
        if not x_values or len(x_values) != len(raw_y_values):
            return None
        y_values = pd.to_numeric(pd.Series(raw_y_values), errors="coerce")
        if y_values.dropna().empty:
            return None
        title = str(render_payload.get("title") or "Timeseries").strip()
        subtitle = str(render_payload.get("subtitle") or "").strip()
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines",
                name=title,
                line={"width": 2.5},
            )
        )
        fig.update_layout(
            template="plotly_dark",
            title=title,
            hovermode="x unified",
            margin={"l": 18, "r": 18, "t": 54, "b": 18},
            showlegend=False,
        )
        if subtitle:
            fig.add_annotation(
                text=subtitle,
                xref="paper",
                yref="paper",
                x=0,
                y=1.08,
                showarrow=False,
                font={"size": 11, "color": "#94a3b8"},
                align="left",
            )
        return fig

    if kind != "chart_model":
        return None

    chart_model = dict(render_payload.get("chart_model") or {})
    datasets = dict(chart_model.get("datasets") or {})
    traces = list(chart_model.get("traces") or [])
    if not datasets or not traces:
        return None

    fig = go.Figure()
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        if str(trace.get("trace_type") or "").strip().lower() != "line":
            continue
        dataset_name = str(trace.get("dataset") or "primary").strip() or "primary"
        rows = list(datasets.get(dataset_name) or [])
        frame = pd.DataFrame(rows)
        if frame.empty:
            continue
        where = dict(trace.get("where") or {})
        for key, expected_value in where.items():
            if key in frame.columns:
                frame = frame[frame[key].astype(str) == str(expected_value)]
        x_key = str(trace.get("x") or "").strip()
        y_key = str(trace.get("y") or "").strip()
        if not x_key or not y_key or x_key not in frame.columns or y_key not in frame.columns:
            continue
        style = dict(trace.get("style") or {})
        line_style: dict[str, object] = {}
        if style.get("color"):
            line_style["color"] = str(style.get("color"))
        if style.get("dash"):
            line_style["dash"] = str(style.get("dash"))
        if style.get("width") is not None:
            line_style["width"] = style.get("width")
        fig.add_trace(
            go.Scatter(
                x=frame[x_key],
                y=pd.to_numeric(frame[y_key], errors="coerce"),
                mode="lines",
                name=str(trace.get("name") or dataset_name),
                line=line_style or None,
            )
        )

    if not fig.data:
        return None

    layout = dict(chart_model.get("layout") or {})
    fig.update_layout(
        template="plotly_dark",
        title=str(chart_model.get("title") or chart_model.get("chart_id") or "Chart").strip(),
        hovermode=str(layout.get("hovermode") or "x unified"),
        margin={"l": 18, "r": 18, "t": 54, "b": 18},
    )
    if layout.get("xaxis_title"):
        fig.update_xaxes(title_text=str(layout.get("xaxis_title")))
    if layout.get("yaxis_title"):
        fig.update_yaxes(title_text=str(layout.get("yaxis_title")))
    return fig


def _render_agentic_omnibar_debug_panel(resolution: dict[str, object]) -> None:
    agent_result = dict(resolution.get("agent_result") or {})
    tool_calls = list(agent_result.get("tool_calls") or [])
    transcript = list(st.session_state.get("agentic_omnibar_transcript") or [])

    with st.expander("Admin Debug", expanded=False):
        st.caption("Route")
        route_cols = st.columns(4)
        route_cols[0].metric("Intent", str(resolution.get("intent") or "n/a").capitalize())
        route_cols[1].metric("Confidence", str(resolution.get("confidence_band") or "n/a").capitalize())
        route_cols[2].metric("Mode", str(resolution.get("preferred_mode") or "auto").capitalize())
        route_cols[3].metric("Matches", str(len(list(resolution.get("search_results") or []))))
        router_intent = str(resolution.get("router_intent") or "").strip()
        if router_intent:
            st.caption(f"Router intent: {router_intent}. The router enriched context but did not block the agent run.")
        st.caption(
            f"request_id={resolution.get('request_id')} | policy_version={resolution.get('policy_version')}"
        )

        if agent_result:
            st.caption("Agent")
            agent_meta_cols = st.columns(4)
            agent_meta_cols[0].metric("Agent Status", str(agent_result.get("status") or "n/a").capitalize())
            agent_meta_cols[1].metric("Tool Calls", str(len(tool_calls)))
            agent_meta_cols[2].metric("Agent Confidence", str(agent_result.get("confidence") or "low").capitalize())
            agent_meta_cols[3].metric("Model", str(agent_result.get("model") or "n/a"))
            limitations = [str(item).strip() for item in list(agent_result.get("limitations") or []) if str(item).strip()]
            if limitations:
                st.caption("Limitations: " + " | ".join(limitations[:3]))
            if tool_calls:
                with st.expander("Tool Calls", expanded=False):
                    for tool_call in tool_calls:
                        with st.container(border=True):
                            header_cols = st.columns([3.2, 1.1, 1.7])
                            with header_cols[0]:
                                st.markdown(f"**{tool_call.get('tool_name') or 'tool'}**")
                                try:
                                    arguments_text = json.dumps(tool_call.get("arguments") or {}, sort_keys=True)
                                except Exception:
                                    arguments_text = str(tool_call.get("arguments") or {})
                                st.caption(arguments_text)
                            with header_cols[1]:
                                st.metric("Status", str(tool_call.get("status") or "n/a").capitalize())
                            with header_cols[2]:
                                provenance = dict((tool_call.get("result_summary") or {}).get("provenance") or {})
                                st.caption(
                                    "datasets="
                                    + ", ".join(str(item) for item in list(provenance.get("datasets") or [])[:4])
                                )
                            render_payload = dict((tool_call.get("result_summary") or {}).get("render_payload") or {})
                            if render_payload:
                                chart = _build_agentic_omnibar_tool_figure(render_payload)
                                if chart is not None:
                                    st.plotly_chart(
                                        chart,
                                        use_container_width=True,
                                        config={"displayModeBar": False},
                                        key=f"debug_chart_{call_id}",
                                    )
                            preview_text = str((tool_call.get("result_summary") or {}).get("preview_text") or "").strip()
                            if preview_text:
                                st.write(preview_text)

        if transcript:
            with st.expander("Transcript", expanded=False):
                for message in transcript:
                    role = str(message.get("role") or "assistant").strip() or "assistant"
                    with st.chat_message(role):
                        st.markdown(str(message.get("content") or "").strip())


def _render_agentic_omnibar_section(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    # ── Minimal header ──
    header_cols = st.columns([5.5, 1.2, 1.3])
    with header_cols[1]:
        _render_section_back_button("agentic_omnibar_back")
    with header_cols[2]:
        if st.button("Clear", key="agentic_omnibar_clear", use_container_width=True):
            for state_key in [
                "agentic_omnibar_chat",
                "agentic_omnibar_resolution",
                "agentic_omnibar_transcript",
                "agentic_omnibar_thinking_trace",
            ]:
                st.session_state.pop(state_key, None)
            st.rerun()

    # ── Load context ──
    home_payload = _load_homepage_narrative_payload(
        cfg,
        force_data_refresh=force_data_refresh,
    )
    beats = _build_homepage_narrative_beats(home_payload) if isinstance(home_payload, dict) else []
    tracked_symbols = sorted(
        {
            str(symbol).upper().strip()
            for beat in beats
            for symbol in list(beat.get("symbols") or [])
            if str(symbol).strip()
        }
    )
    symbol_name_map = _load_symbol_name_map(
        cfg,
        tracked_symbols,
        force_refresh=force_data_refresh,
    ) if tracked_symbols else {}
    symbol_catalog = _build_agentic_omnibar_symbol_catalog(beats, symbol_name_map)

    # ── Chat history ──
    if "agentic_omnibar_chat" not in st.session_state:
        st.session_state["agentic_omnibar_chat"] = []
    chat: list[dict[str, object]] = st.session_state["agentic_omnibar_chat"]

    # ── Empty state ──
    if not chat:
        _render_omnibar_welcome(beats)

    # ── Render history ──
    for msg in chat:
        with st.chat_message(str(msg.get("role") or "assistant")):
            if msg.get("role") == "assistant":
                _render_agent_response_message(msg)
            else:
                st.markdown(str(msg.get("content") or ""))

    # ── Input handling ──
    pending_query = st.session_state.pop("_omnibar_pending_query", None)
    typed_query = st.chat_input("Ask about any market, ticker, or event...")
    active_query = str(pending_query or typed_query or "").strip()

    if active_query:
        # Add user message to history
        chat.append({"role": "user", "content": active_query})
        with st.chat_message("user"):
            st.markdown(active_query)

        # Run agent with live progress and render structured response
        # Pass prior turns (everything before the just-appended user message)
        # so the agent can resolve follow-up references.
        prior_turns = chat[:-1] if len(chat) > 1 else []
        with st.chat_message("assistant"):
            msg_data = _run_and_render_agent_live(
                cfg,
                active_query,
                beats,
                symbol_catalog,
                force_data_refresh=force_data_refresh,
                conversation_history=prior_turns,
            )

        # Save assistant message to history
        chat.append({"role": "assistant", **msg_data})
        st.session_state["agentic_omnibar_chat"] = chat

    # ── Admin debug (last resolution) ──
    if _current_user_is_admin() and chat:
        last_assistant = next(
            (m for m in reversed(chat) if m.get("role") == "assistant"),
            None,
        )
        if last_assistant and last_assistant.get("resolution"):
            _render_agentic_omnibar_debug_panel(dict(last_assistant["resolution"]))


def _run_and_render_agent_live(
    cfg: AppConfig,
    query: str,
    beats: list[dict[str, object]],
    symbol_catalog: dict[str, dict[str, object]],
    *,
    force_data_refresh: bool,
    conversation_history: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Run the agent with live st.status progress, render structured response, return history dict."""
    thinking_trace: list[dict[str, object]] = []
    source_links_all: list[dict[str, str]] = []
    tool_count = 0

    status_widget = st.status("Researching...", expanded=True)

    def _progress_callback(event: dict[str, object]) -> None:
        nonlocal tool_count
        stage = str(event.get("stage") or "").strip().lower()
        message = _agentic_omnibar_progress_message(event)

        if stage == "planner_start":
            iteration = int(event.get("iteration") or 1)
            prior_tools = int(event.get("tool_call_count") or 0)
            if prior_tools > 0:
                status_widget.update(label=f"Thinking... (step {iteration}, {prior_tools} source{'s' if prior_tools != 1 else ''} so far)")
            else:
                status_widget.update(label=f"Thinking... (step {iteration})")
        elif stage == "planner_heartbeat":
            elapsed = int(event.get("elapsed_seconds") or 0)
            iteration = int(event.get("iteration") or 1)
            prior_tools = int(event.get("tool_call_count") or 0)
            parts = [f"step {iteration}"]
            if prior_tools > 0:
                parts.append(f"{prior_tools} source{'s' if prior_tools != 1 else ''}")
            parts.append(f"{elapsed}s")
            status_widget.update(label=f"Thinking... ({', '.join(parts)})")
        elif stage == "planner_reasoning":
            reasoning = str(event.get("reasoning") or "").strip()
            if reasoning:
                thinking_trace.append({"type": "reasoning", "text": reasoning})
                with status_widget:
                    st.caption(reasoning)
        elif stage == "model_reasoning_trace":
            reasoning_trace = str(event.get("reasoning_trace") or "").strip()
            if reasoning_trace:
                thinking_trace.append({"type": "model_reasoning_trace", "text": reasoning_trace})
        elif stage == "tool_start":
            tool_count += 1
            tool_name = str(event.get("tool_name") or "")
            tool_args = event.get("tool_arguments")
            try:
                args_text = json.dumps(tool_args or {}, sort_keys=True, default=str)
            except Exception:
                args_text = str(tool_args or {})
            if len(args_text) > 120:
                args_text = args_text[:117] + "..."
            thinking_trace.append({"type": "tool_start", "tool_name": tool_name, "args_text": args_text})
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            with status_widget:
                st.markdown(f"→ **{human_tool}**")
            status_widget.update(label=f"Checking {human_tool}... (step {tool_count})")
        elif stage == "tool_heartbeat":
            tool_name = str(event.get("tool_name") or "")
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            elapsed = int(event.get("elapsed_seconds") or 0)
            status_widget.update(label=f"Checking {human_tool}... (step {tool_count}, {elapsed}s)")
        elif stage == "tool_timeout":
            tool_name = str(event.get("tool_name") or "")
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            elapsed = int(event.get("elapsed_seconds") or 0)
            thinking_trace.append({"type": "message", "text": f"{human_tool} timed out after {elapsed}s; moving on."})
            with status_widget:
                st.caption(f"{human_tool} timed out after {elapsed}s; moving on.")
            status_widget.update(label=f"{human_tool} timed out; continuing...")
        elif stage == "tool_complete":
            preview = str(event.get("result_preview") or "").strip()
            trace_entry: dict[str, object] = {"type": "tool_complete", "preview": preview}
            links = event.get("source_links")
            if isinstance(links, list) and links:
                source_links_all.extend(links)
                trace_entry["source_links"] = links
            render_payload = event.get("render_payload")
            if isinstance(render_payload, dict) and render_payload.get("kind") == "chart_model":
                trace_entry["render_payload"] = render_payload
            if preview or render_payload or links:
                thinking_trace.append(trace_entry)
        elif stage == "planner_final":
            status_widget.update(label=f"Writing answer from {tool_count} source{'s' if tool_count != 1 else ''}...")
        elif stage == "final_synthesis_start":
            status_widget.update(label=f"Synthesizing answer from {tool_count} source{'s' if tool_count != 1 else ''}...")
        elif stage == "tool_catalog_ready":
            tool_total = int(event.get("tool_count") or 0)
            if tool_total > 0:
                status_widget.update(label=f"Loaded {tool_total} tools, thinking...")
        elif stage == "hidden_step_heartbeat":
            elapsed = int(event.get("elapsed_seconds") or 0)
            status_widget.update(label=f"Preparing evidence... ({elapsed}s)")
        elif stage == "hidden_step_timeout":
            elapsed = int(event.get("elapsed_seconds") or 0)
            thinking_trace.append({"type": "message", "text": f"Evidence preparation timed out after {elapsed}s; continuing."})
            status_widget.update(label="Evidence preparation timed out; continuing...")
        elif stage not in {"completed", "status", "resolve_start", "intent_ready", "agent_dispatch", "start"}:
            thinking_trace.append({"type": "message", "text": message})

    import time as _time_mod
    run_start = _time_mod.monotonic()

    resolution: dict[str, object] = {}
    run_error: str = ""
    try:
        resolution = _run_agentic_omnibar_resolution(
            cfg,
            query,
            "auto",
            beats,
            symbol_catalog,
            force_data_refresh=force_data_refresh,
            progress_callback=_progress_callback,
            conversation_history=conversation_history,
        )
    except Exception as exc:
        run_error = _safe_research_agent_error_text(exc)

    duration = round(_time_mod.monotonic() - run_start, 1)

    # Close status — show error state if failed
    if run_error:
        status_widget.update(
            label=f"Error after {duration:.0f}s ({tool_count} source{'s' if tool_count != 1 else ''} checked)",
            state="error",
            expanded=False,
        )
    else:
        status_widget.update(
            label=f"Researched {tool_count} source{'s' if tool_count != 1 else ''} · {duration:.0f}s",
            state="complete",
            expanded=False,
        )

    # ── Render structured response ──
    agent_result = dict(resolution.get("agent_result") or {})
    answer = str(agent_result.get("answer_markdown") or "").strip()
    confidence = str(agent_result.get("confidence") or "").strip()
    limitations = [str(item).strip() for item in list(agent_result.get("limitations") or []) if str(item).strip()]
    search_results = list(resolution.get("search_results") or [])
    request_id = str(resolution.get("request_id") or "live")

    # Error message
    if run_error:
        st.error(f"The research agent encountered an error: {run_error}")

    # Source evidence strip
    if source_links_all:
        _render_source_evidence_strip(source_links_all)

    # Key metrics
    if answer:
        metrics = _extract_answer_metrics(answer)
        if metrics:
            _render_answer_metrics(metrics)

    # Answer
    if answer:
        st.markdown(answer)
    elif not search_results and not run_error:
        st.markdown("No answer could be generated for this query.")

    # Confidence + limitations footer
    if confidence or limitations:
        footer_parts = []
        if confidence:
            footer_parts.append(f"Confidence: **{confidence}**")
        if limitations:
            footer_parts.append(" · ".join(limitations[:2]))
        st.caption(" | ".join(footer_parts))

    # Thinking trace — explicit expander so it's always findable
    if thinking_trace:
        with st.expander(f"Thinking Trace ({len(thinking_trace)} steps)", expanded=False):
            _render_thinking_trace_content(thinking_trace, agent_result, key_prefix=f"live_{request_id}")

    # Search results (simplified)
    if search_results:
        _render_inline_search_results(search_results, request_id)

    # Dig deeper button
    if answer:
        if st.button("Dig deeper", key=f"dig_live_{request_id}"):
            st.session_state["_omnibar_pending_query"] = f"{query} — verify and expand with more evidence"
            st.rerun()

    # Return data for history re-rendering
    return {
        "content": answer or run_error or "No answer available.",
        "answer": answer,
        "query": query,
        "resolved_query": str(resolution.get("resolved_query") or agent_result.get("query") or "").strip(),
        "followup_resolved": bool(resolution.get("followup_resolved") or agent_result.get("followup_resolved")),
        "source_links": source_links_all,
        "thinking_trace": thinking_trace,
        "confidence": confidence,
        "limitations": limitations,
        "search_results": search_results,
        "tool_count": tool_count,
        "duration_seconds": duration,
        "intent": str(resolution.get("intent") or ""),
        "resolution": resolution,
        "agent_result": agent_result,
        "error": run_error,
        "msg_id": request_id,
    }


def _attention_key_points_text(row: pd.Series) -> str:
    pieces: list[str] = []
    horizon = str(row.get("horizon") or "").strip()
    if horizon:
        pieces.append(ATTENTION_HORIZON_LABELS.get(horizon, horizon))

    peer_group_name = str(row.get("peer_group_name") or "").strip()
    if peer_group_name and peer_group_name != "All Market":
        pieces.append(peer_group_name)

    regime_label = str(row.get("regime_label") or "").strip()
    if regime_label:
        pieces.append(regime_label)

    linked_news_count = pd.to_numeric(row.get("linked_news_count"), errors="coerce")
    if pd.notna(linked_news_count) and int(linked_news_count) > 0:
        count = int(linked_news_count)
        pieces.append(f"{count} headline{'s' if count != 1 else ''}")

    if float(pd.to_numeric(row.get("portfolio_exposure_weight"), errors="coerce") or 0.0) > 0:
        pieces.append("Portfolio overlap")

    return " | ".join(piece for piece in pieces if piece)


def _render_homepage_v2_detail_panel(
    row: pd.Series,
    *,
    news_payload: dict[str, object] | None = None,
    context_payload: dict[str, object] | None = None,
    brief_payload: dict[str, object] | None = None,
) -> None:
    title = str(row.get("title") or row.get("entity_id") or "Attention detail").strip()
    entity_id = str(row.get("entity_id") or "").upper().strip()
    subtitle = str(row.get("subtitle") or "").strip()
    source_label = str(row.get("source_label") or "").strip()
    target_section = _normalize_workspace_section(row.get("drilldown_section")) or MARKET_EXPLORER_SECTION
    params = _parse_drilldown_params(row.get("drilldown_params_json"))

    st.markdown(f"### {title}")
    meta = [item for item in [source_label, subtitle, entity_id] if item]
    if meta:
        st.caption(" | ".join(meta))

    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric("Score", _format_scalar(row.get("attention_score"), digits=1))
    with metric_cols[1]:
        st.metric("Residual", _format_scalar(row.get("residual_value"), digits=2, suffix="%", signed=True))
    with metric_cols[2]:
        st.metric("Horizon", ATTENTION_HORIZON_LABELS.get(str(row.get("horizon") or "").strip(), str(row.get("horizon") or "n/a")))

    chart = _build_attention_micro_chart(row)
    if chart is not None:
        st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})

    item_summary = _homepage_v2_item_summary(
        row,
        news_payload=news_payload,
        context_payload=context_payload,
        brief_payload=brief_payload,
    )
    if item_summary:
        st.write(item_summary)

    llm_headline = str((context_payload or {}).get("llm_headline") or "").strip()
    llm_summary_text = str((context_payload or {}).get("llm_summary_text") or "").strip()
    llm_narrative_text = str((context_payload or {}).get("llm_narrative_text") or "").strip()
    llm_why_now = str((context_payload or {}).get("llm_why_now") or "").strip()
    lead_text = str((brief_payload or {}).get("lead_text") or "").strip()
    cluster_text = str((brief_payload or {}).get("cluster_text") or "").strip()
    headline_text = str((brief_payload or {}).get("headline_text") or "").strip()
    company_text = str((brief_payload or {}).get("company_text") or "").strip()
    explainer_text = str((brief_payload or {}).get("explainer_text") or "").strip()
    watchpoint_text = str((brief_payload or {}).get("watchpoint_text") or "").strip()
    news_context = build_attention_news_narrative(
        entity_id,
        news_payload,
        peer_group_name=str(row.get("peer_group_name") or "").strip(),
    )
    news_story = str(news_context.get("narrative_text") or "").strip()
    headline_links = news_context.get("headline_links", [])
    filing_links = (context_payload or {}).get("top_filing_links", [])

    if lead_text:
        st.markdown(f"**What Is Likely Going On**  \n{lead_text}")
    if cluster_text:
        st.markdown(f"**Coverage Cluster**  \n{cluster_text}")
    if headline_text:
        st.markdown(f"**Fresh Headline**  \n{headline_text}")
    if company_text:
        st.markdown(f"**Company In Plain English**  \n{company_text}")
    if explainer_text:
        st.markdown(f"**Term Explainer**  \n{explainer_text}")
    if watchpoint_text:
        st.caption(f"Watchpoint: {watchpoint_text}")

    if llm_headline:
        st.markdown(f"**Primary Source Read**  \n{llm_headline}")
    if llm_summary_text:
        st.write(llm_summary_text)
    if llm_narrative_text:
        st.caption(llm_narrative_text)
    if llm_why_now:
        st.caption(f"Why now: {llm_why_now}")

    if news_story:
        st.markdown(f"**News Summary**  \n{news_story}")
    if isinstance(headline_links, list):
        for index, item in enumerate(headline_links[:3]):
            headline = str((item or {}).get("headline") or "").strip()
            url = str((item or {}).get("url") or "").strip()
            source = str((item or {}).get("source") or "News").strip()
            published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
            published_label = published_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published_at) else ""
            if not headline:
                continue
            _render_tracked_activity_link(
                headline,
                url,
                key=_activity_link_key(f"home_ticker_news_summary_{ticker}_{index}", label=headline, url=url),
                surface="home_ticker_news_summary",
                target_type="news_article",
                source=source,
                published_at=published_label,
                extra_detail={"symbol": str(ticker or "").upper().strip()},
            )

    if isinstance(filing_links, list) and filing_links:
        st.caption("Supporting filings")
        for index, item in enumerate(filing_links[:3]):
            label = str((item or {}).get("label") or "").strip()
            url = str((item or {}).get("url") or "").strip()
            if not label:
                continue
            _render_tracked_activity_link(
                label,
                url,
                key=_activity_link_key(f"homepage_v2_filing_{entity_id}_{index}", label=label, url=url),
                surface="homepage_v2_filing_link",
                target_type="filing_link",
                extra_detail={"symbol": entity_id},
            )

    if st.button(
        f"Open {target_section}",
        key=f"homepage_v2_detail_open_{str(row.get('event_id') or entity_id or 'item')}",
        use_container_width=True,
        disabled=not target_section,
    ):
        _open_attention_target(target_section, params=params)


def _render_attention_card(
    row: pd.Series,
    *,
    key_prefix: str,
    news_payload: dict[str, object] | None = None,
    context_payload: dict[str, object] | None = None,
    brief_payload: dict[str, object] | None = None,
) -> None:
    params = _parse_drilldown_params(row.get("drilldown_params_json"))
    title = str(row.get("title") or row.get("entity_id") or "Untitled anomaly").strip()
    subtitle = str(row.get("subtitle") or "").strip()
    entity_id = str(row.get("entity_id") or "").upper().strip()
    status = str(row.get("status") or "active").replace("_", " ").title()
    source_label = str(row.get("source_label") or "").strip()
    target_section = _normalize_workspace_section(row.get("drilldown_section")) or MARKET_EXPLORER_SECTION

    linked_news_raw = pd.to_numeric(row.get("linked_news_count"), errors="coerce")
    linked_news_count = int(linked_news_raw) if pd.notna(linked_news_raw) else 0

    with st.container(border=True):
        header_cols = st.columns([6.2, 1.4, 1.4])
        with header_cols[0]:
            st.markdown(f"##### {title}")
            meta = [item for item in [source_label, subtitle, entity_id, status] if item]
            st.caption(" | ".join(meta))
        with header_cols[1]:
            st.metric("Score", _format_scalar(row.get("attention_score"), digits=1))
        with header_cols[2]:
            st.metric("News", str(linked_news_count))

        story_text = str((brief_payload or {}).get("lead_text") or _attention_story_text(row)).strip()
        news_context = build_attention_news_narrative(
            entity_id,
            news_payload,
            peer_group_name=str(row.get("peer_group_name") or "").strip(),
        )
        news_story_text = str(news_context.get("narrative_text") or "").strip()
        news_source_line = str(news_context.get("source_line") or "").strip()
        headline_links = news_context.get("headline_links", [])
        brief_cluster_text = str((brief_payload or {}).get("cluster_text") or news_story_text).strip()
        brief_headline_text = str((brief_payload or {}).get("headline_text") or "").strip()
        brief_company_text = str((brief_payload or {}).get("company_text") or "").strip()
        brief_explainer_text = str((brief_payload or {}).get("explainer_text") or "").strip()
        brief_watchpoint_text = str((brief_payload or {}).get("watchpoint_text") or "").strip()
        context_story_text = str((context_payload or {}).get("context_story_text") or "").strip()
        primary_source_excerpt = str((context_payload or {}).get("primary_source_excerpt") or "").strip()
        primary_source_line = str((context_payload or {}).get("source_line") or "").strip()
        llm_headline = str((context_payload or {}).get("llm_headline") or "").strip()
        llm_summary_text = str((context_payload or {}).get("llm_summary_text") or "").strip()
        llm_narrative_text = str((context_payload or {}).get("llm_narrative_text") or "").strip()
        llm_why_now = str((context_payload or {}).get("llm_why_now") or "").strip()
        llm_management_signal = str((context_payload or {}).get("llm_management_signal") or "").strip()
        llm_confidence = str((context_payload or {}).get("llm_confidence") or "").strip()
        llm_source_line = str((context_payload or {}).get("llm_source_line") or "").strip()
        llm_supporting_points = (context_payload or {}).get("llm_supporting_points", [])
        filing_links = (context_payload or {}).get("top_filing_links", [])
        why_now_text = _clean_attention_text(row.get("why_now_text"))
        expected_text = _clean_attention_text(row.get("expected_vs_observed_text"))
        key_points_text = _attention_key_points_text(row)
        chart = _build_attention_micro_chart(row)

        insight_cols = st.columns([1.05, 1.55], gap="large")
        with insight_cols[0]:
            if chart is not None:
                st.plotly_chart(
                    chart,
                    use_container_width=True,
                    config={"displayModeBar": False, "staticPlot": True},
                )
        with insight_cols[1]:
            if story_text:
                st.markdown(f"**What Is Likely Going On**  \n{story_text}")
            if key_points_text:
                st.caption(key_points_text)
            if brief_cluster_text:
                st.markdown(f"**Coverage Cluster**  \n{brief_cluster_text}")
            if news_source_line:
                st.caption(news_source_line)
            if brief_headline_text:
                st.markdown(f"**Fresh Headline**  \n{brief_headline_text}")
            if brief_company_text:
                st.markdown(f"**Company In Plain English**  \n{brief_company_text}")
            if brief_explainer_text:
                st.markdown(f"**Term Explainer**  \n{brief_explainer_text}")
            if brief_watchpoint_text:
                st.caption(f"Watchpoint: {brief_watchpoint_text}")
            if llm_headline:
                st.markdown(f"**EDGAR Read**  \n{llm_headline}")
            if llm_summary_text:
                st.write(llm_summary_text)
            if llm_narrative_text:
                st.caption(llm_narrative_text)
            if isinstance(llm_supporting_points, list):
                points = [str(item).strip() for item in llm_supporting_points if str(item).strip()]
                if points:
                    st.markdown("\n".join(f"- {point}" for point in points[:3]))
            if llm_why_now:
                st.caption(f"Why now: {llm_why_now}")
            if llm_management_signal:
                st.caption(f"Management signal: {llm_management_signal}")
            if llm_confidence:
                st.caption(f"Confidence: {llm_confidence}")
            if llm_source_line:
                st.caption(llm_source_line)
            if context_story_text:
                st.markdown(f"**Primary Source**  \n{context_story_text}")
            if primary_source_excerpt:
                st.caption(primary_source_excerpt)
            if primary_source_line:
                st.caption(primary_source_line)
            if isinstance(headline_links, list):
                for index, item in enumerate(headline_links[:2]):
                    headline = str((item or {}).get("headline") or "").strip()
                    if not headline:
                        continue
                    url = str((item or {}).get("url") or "").strip()
                    source = str((item or {}).get("source") or "News").strip()
                    published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
                    published_label = published_at.strftime("%b %d") if pd.notna(published_at) else "n/a"
                    _render_tracked_activity_link(
                        headline,
                        url,
                        key=_activity_link_key(f"attention_card_primary_source_{entity_id}_{index}", label=headline, url=url),
                        surface="attention_card_primary_source",
                        target_type="news_article",
                        source=source,
                        published_at=published_label,
                        extra_detail={"symbol": entity_id},
                    )
                    st.caption(f"{source} | {published_label}")
            if isinstance(filing_links, list):
                if any(str((item or {}).get("label") or "").strip() for item in filing_links[:2]):
                    st.caption("SEC filings")
                for index, item in enumerate(filing_links[:2]):
                    label = str((item or {}).get("label") or "").strip()
                    if not label:
                        continue
                    url = str((item or {}).get("url") or "").strip()
                    _render_tracked_activity_link(
                        label,
                        url,
                        key=_activity_link_key(f"attention_card_filing_{entity_id}_{index}", label=label, url=url),
                        surface="attention_card_filing_link",
                        target_type="filing_link",
                        extra_detail={"symbol": entity_id},
                    )
            if why_now_text and why_now_text != story_text:
                st.caption(why_now_text)
            if expected_text:
                st.caption(expected_text)

        footer_cols = st.columns([5.6, 2.4])
        with footer_cols[0]:
            next_action = str(row.get("next_best_action") or "").strip()
            if next_action:
                st.caption(f"Next: {next_action}")
        with footer_cols[1]:
            if st.button(
                f"Open {target_section}",
                key=f"{key_prefix}_{str(row.get('event_id') or entity_id or 'item')}_open",
                use_container_width=True,
                disabled=not target_section,
            ):
                _open_attention_target(target_section, params=params)


def _priority_attention_feed(
    feed: pd.DataFrame,
    market_events: pd.DataFrame,
    *,
    limit: int = 8,
) -> pd.DataFrame:
    if feed.empty:
        return feed.copy()
    if market_events.empty or "event_id" not in feed.columns:
        return feed.head(max(int(limit), 1)).copy()

    row_lookup = {
        str(row.get("event_id") or "").strip(): row
        for _, row in feed.iterrows()
        if str(row.get("event_id") or "").strip()
    }
    prioritized_rows: list[pd.Series] = []
    seen_event_ids: set[str] = set()

    for _, event in market_events.iterrows():
        for event_id in list(event.get("supporting_event_ids") or []):
            key = str(event_id or "").strip()
            if not key or key in seen_event_ids:
                continue
            row = row_lookup.get(key)
            if row is None:
                continue
            prioritized_rows.append(row.copy())
            seen_event_ids.add(key)
            if len(prioritized_rows) >= max(int(limit), 1):
                break
        if len(prioritized_rows) >= max(int(limit), 1):
            break

    if len(prioritized_rows) < max(int(limit), 1):
        for _, row in feed.iterrows():
            key = str(row.get("event_id") or "").strip()
            if key and key in seen_event_ids:
                continue
            prioritized_rows.append(row.copy())
            if len(prioritized_rows) >= max(int(limit), 1):
                break

    return pd.DataFrame(prioritized_rows).reset_index(drop=True)


def _render_market_event_card(
    event: pd.Series,
    *,
    row_lookup: dict[str, pd.Series],
    key_prefix: str,
) -> None:
    title = str(event.get("event_title") or "Market event").strip()
    anchor_symbol = str(event.get("anchor_symbol") or "").upper().strip()
    confidence_label = str(event.get("confidence_label") or "Developing").strip()
    what_happened_text = _raw_attention_text(event.get("what_happened_text"))
    why_happened_text = _raw_attention_text(event.get("why_happened_text"))
    affected_assets_summary_text = _raw_attention_text(event.get("affected_assets_summary_text"))
    headline_text = str(event.get("headline_text") or "").strip()
    source_line = str(event.get("source_line") or "").strip()
    supporting_ids = [str(value).strip() for value in list(event.get("supporting_event_ids") or []) if str(value).strip()]
    supporting_symbols = [str(value).upper().strip() for value in list(event.get("supporting_symbols") or []) if str(value).strip()]
    breadth_count = int(pd.to_numeric(event.get("breadth_count"), errors="coerce") or 0)

    anchor_row = row_lookup.get(supporting_ids[0]) if supporting_ids else None
    params = _parse_drilldown_params(anchor_row.get("drilldown_params_json")) if anchor_row is not None else {}
    target_section = (
        _normalize_workspace_section(anchor_row.get("drilldown_section"))
        if anchor_row is not None
        else MARKET_EXPLORER_SECTION
    ) or MARKET_EXPLORER_SECTION

    with st.container(border=True):
        header_cols = st.columns([5.4, 1.3, 1.3])
        with header_cols[0]:
            st.markdown(f"##### {title}")
            meta = [item for item in [anchor_symbol, confidence_label, f"{breadth_count} buckets" if breadth_count else ""] if item]
            if meta:
                st.caption(" | ".join(meta))
        with header_cols[1]:
            st.metric("Event", _format_scalar(event.get("event_score"), digits=1))
        with header_cols[2]:
            st.metric("Signals", str(len(supporting_ids) or len(supporting_symbols)))

        if what_happened_text:
            st.markdown(f"**What Happened**  \n{what_happened_text}")
        if why_happened_text:
            st.markdown(f"**Why It Happened**  \n{why_happened_text}")
        if affected_assets_summary_text:
            st.markdown(f"**Affected Assets**  \n{affected_assets_summary_text}")
        if headline_text:
            st.caption(headline_text)
        if source_line:
            st.caption(source_line)

        supporting_rows = [row_lookup.get(event_id) for event_id in supporting_ids[:4]]
        supporting_rows = [row for row in supporting_rows if row is not None]
        if supporting_rows:
            st.markdown("**Supporting anomaly items**")
            for row in supporting_rows:
                symbol = str(row.get("entity_id") or "").upper().strip()
                supporting_title = str(row.get("title") or symbol or "Attention item").strip()
                score_text = _format_scalar(row.get("attention_score"), digits=1)
                st.markdown(f"- {supporting_title} (`{symbol}`, score {score_text})")

        if anchor_row is not None and st.button(
            "Open anchor detail",
            key=f"{key_prefix}_{str(event.get('market_event_id') or anchor_symbol or 'event')}_open",
            use_container_width=False,
        ):
            _open_attention_target(target_section, params=params)


def _bundle_toggle_key(bundle_id: str, key_prefix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(bundle_id or "").strip())
    return f"{key_prefix}_{cleaned}_research_open"


def _render_attention_research_bundle_panel(
    bundle: dict[str, object],
    *,
    ticker_click_target: str = "",
    ticker_table_key_prefix: str = "",
) -> None:
    bundle_type = str(bundle.get("bundle_type") or "").strip()
    if bundle_type == "event":
        what_happened = _raw_attention_text(bundle.get("what_happened_text"))
        why_happened = _raw_attention_text(bundle.get("why_happened_text"))
        affected_assets = _raw_attention_text(bundle.get("affected_assets_summary_text"))
        if what_happened:
            st.markdown(f"**What Happened**  \n{what_happened}")
        if why_happened:
            st.markdown(f"**Why It Happened**  \n{why_happened}")
        if affected_assets:
            st.markdown(f"**Affected Assets**  \n{affected_assets}")
    else:
        what_changed = _raw_attention_text(bundle.get("what_changed_text"))
        why_now = _raw_attention_text(bundle.get("why_now_text"))
        what_else_moved = _raw_attention_text(bundle.get("what_else_moved_text"))
        if what_changed:
            st.markdown(f"**What Changed Vs Expectation**  \n{what_changed}")
        if why_now:
            st.markdown(f"**Why Today**  \n{why_now}")
        if what_else_moved:
            st.markdown(f"**What Else Moved**  \n{what_else_moved}")
        meta = [part for part in [str(bundle.get("sector") or "").strip(), str(bundle.get("industry") or "").strip()] if part]
        if meta:
            st.caption(" | ".join(meta))

    quality_parts = []
    cause_status = str(bundle.get("cause_status") or "").strip()
    if cause_status:
        quality_parts.append(f"Cause status: {cause_status.replace('_', ' ').title()}")
    evidence_quality = str(bundle.get("evidence_quality") or "").strip()
    if evidence_quality:
        quality_parts.append(f"Evidence quality: {evidence_quality}")
    freshness_quality = str(bundle.get("freshness_quality") or "").strip()
    if freshness_quality:
        quality_parts.append(f"Freshness: {freshness_quality}")
    source_summary = str(bundle.get("source_summary") or "").strip()
    if source_summary:
        quality_parts.append(f"Sources: {source_summary}")
    if quality_parts:
        st.caption(" | ".join(quality_parts))

    evidence = bundle.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        st.markdown("**Evidence**")
        for index, item in enumerate(evidence[:4]):
            headline = str((item or {}).get("headline") or "").strip()
            summary = _attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            authority = str((item or {}).get("authority_bucket") or "").strip()
            published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
            published_label = published_at.strftime("%b %d %H:%M UTC") if pd.notna(published_at) else ""
            if headline:
                _render_tracked_activity_link(
                    headline,
                    url,
                    key=_activity_link_key(f"attention_bundle_evidence_{index}", label=headline, url=url),
                    surface="attention_bundle_evidence",
                    target_type="evidence_link",
                    source=source,
                    published_at=published_label,
                )
            if summary:
                st.caption(summary)
            meta_parts = [part for part in [source, authority.title() if authority else "", published_label] if part]
            evidence_role = str((item or {}).get("evidence_role") or "").strip()
            if evidence_role:
                meta_parts.append(evidence_role.replace("_", " ").title())
            if meta_parts:
                st.caption(" | ".join(meta_parts))

    background_context = bundle.get("background_context") or []
    if isinstance(background_context, list) and background_context:
        st.markdown("**Background Context**")
        for index, item in enumerate(background_context[:3]):
            headline = str((item or {}).get("headline") or "").strip()
            summary = _attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            if headline:
                _render_tracked_activity_link(
                    headline,
                    url,
                    key=_activity_link_key(f"attention_bundle_background_{index}", label=headline, url=url),
                    surface="attention_bundle_background_context",
                    target_type="background_link",
                    source=source,
                )
            if summary:
                st.caption(summary)
            if source:
                st.caption(source)
    elif str(bundle.get("background_context_text") or "").strip():
        st.markdown(f"**Background Context**  \n{str(bundle.get('background_context_text') or '').strip()}")

    peer_moves = bundle.get("peer_moves") or []
    if isinstance(peer_moves, list) and peer_moves:
        st.markdown("**Peer Moves**")
        try:
            snapshot_cfg = load_config()
        except Exception:
            snapshot_cfg = None
        peer_rows: list[dict[str, object]] = []
        for item in peer_moves[:6]:
            symbol = str((item or {}).get("symbol") or "").strip()
            change_pct = pd.to_numeric((item or {}).get("change_pct"), errors="coerce")
            relationship = str((item or {}).get("relationship") or "").strip()
            headline = str((item or {}).get("headline") or "").strip()
            note_parts = []
            if pd.notna(change_pct):
                note_parts.append(f"{float(change_pct):+.1f}%")
            if relationship:
                note_parts.append(relationship)
            peer_rows.append(
                {
                    "symbol": symbol,
                    "note": " | ".join(note_parts),
                    "note_secondary": headline,
                }
            )
        _render_ticker_snapshot_table(
            snapshot_cfg,
            peer_rows,
            show_header=True,
            click_target=ticker_click_target,
            key_prefix=f"{ticker_table_key_prefix or 'attention_bundle'}_peer_moves",
        )

    related_symbols = bundle.get("related_symbols") or []
    if isinstance(related_symbols, list) and related_symbols:
        st.markdown("**Related Symbols**")
        try:
            snapshot_cfg = load_config()
        except Exception:
            snapshot_cfg = None
        related_rows: list[dict[str, object]] = []
        for item in related_symbols[:8]:
            symbol = str((item or {}).get("symbol") or "").strip()
            headline = str((item or {}).get("headline") or "").strip()
            change_pct = pd.to_numeric((item or {}).get("change_pct"), errors="coerce")
            note = f"{float(change_pct):+.1f}%" if pd.notna(change_pct) else ""
            related_rows.append(
                {
                    "symbol": symbol,
                    "note": note,
                    "note_secondary": headline or "Linked move",
                }
            )
        _render_ticker_snapshot_table(
            snapshot_cfg,
            related_rows,
            show_header=True,
            click_target=ticker_click_target,
            key_prefix=f"{ticker_table_key_prefix or 'attention_bundle'}_related_symbols",
        )


def _render_attention_home_event_card(
    cfg: AppConfig,
    event: dict[str, object],
    *,
    research_bundle: dict[str, object] | None = None,
    key_prefix: str,
    force_refresh: bool,
    click_target: str = "",
) -> None:
    bundle_id = str(event.get("bundle_id") or "").strip()
    toggle_key = _bundle_toggle_key(bundle_id, key_prefix)
    bundle = research_bundle if isinstance(research_bundle, dict) else {}
    payload = bundle if bundle else event
    what_happened = _raw_attention_text(payload.get("what_happened_text") or event.get("what_happened_text"))
    why_happened = _raw_attention_text(payload.get("why_happened_text") or event.get("why_happened_text"))
    affected_assets = _raw_attention_text(payload.get("affected_assets_summary_text") or event.get("affected_assets_summary_text"))
    event_title = str(payload.get("event_title") or event.get("event_title") or "Market event").strip()
    supporting_symbols = [
        str(item).upper().strip()
        for item in list(payload.get("supporting_symbols") or event.get("supporting_symbols") or [])
        if str(item).strip()
    ]
    with st.container(border=True):
        header_cols = st.columns([5.2, 1.2, 1.6])
        with header_cols[0]:
            st.markdown(f"##### {event_title}")
            _render_ticker_snapshot_table(
                cfg,
                [
                    {
                        "symbol": str(payload.get("anchor_symbol") or event.get("anchor_symbol") or "").strip(),
                        "extras": [
                            str(payload.get("confidence_label") or event.get("confidence_label") or "").strip(),
                            f"{int(pd.to_numeric(payload.get('source_count', event.get('source_count')), errors='coerce') or 0)} sources",
                            f"{int(pd.to_numeric(payload.get('evidence_count', event.get('evidence_count')), errors='coerce') or 0)} evidence",
                        ],
                    }
                ],
                force_refresh=force_refresh,
                show_header=False,
                click_target=click_target,
                key_prefix=f"{key_prefix}_{bundle_id or 'event'}_anchor",
            )
        with header_cols[1]:
            st.metric("Event", _format_scalar(payload.get("event_score", event.get("event_score")), digits=1))
        with header_cols[2]:
            if st.button(
                "Show research" if not st.session_state.get(toggle_key, False) else "Hide research",
                key=f"{toggle_key}_button",
                use_container_width=True,
            ):
                st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)

        if what_happened:
            st.markdown(f"**What Happened**  \n{what_happened}")
        if why_happened:
            st.markdown(f"**Why It Happened**  \n{why_happened}")
        if affected_assets:
            st.markdown(f"**Affected Assets**  \n{affected_assets}")
        if supporting_symbols:
            st.markdown("**Supporting Symbols**")
            _render_ticker_snapshot_table(
                cfg,
                [{"symbol": symbol} for symbol in supporting_symbols[:6]],
                force_refresh=force_refresh,
                show_header=True,
                click_target=click_target,
                key_prefix=f"{key_prefix}_{bundle_id or 'event'}_supporting",
            )

        meta_line = [
            part
            for part in [
                str(payload.get("confidence_label") or event.get("confidence_label") or "").strip(),
                str(payload.get("cause_status") or event.get("cause_status") or "").replace("_", " ").title().strip(),
                str(payload.get("evidence_quality") or "").strip(),
                str(payload.get("freshness_quality") or "").strip(),
                str(payload.get("source_summary") or event.get("source_summary") or "").strip(),
            ]
            if part
        ]
        if meta_line:
            st.caption(" | ".join(meta_line))

        if st.session_state.get(toggle_key, False) and bundle_id:
            if not bundle:
                bundle = _safe_load_attention_research_bundle_cached(cfg, bundle_id, force_refresh=force_refresh)
            st.markdown("---")
            _render_attention_research_bundle_panel(
                bundle,
                ticker_table_key_prefix=f"{key_prefix}_{bundle_id or 'event'}_bundle",
            )


def _render_attention_home_mover_card(
    cfg: AppConfig,
    mover: dict[str, object],
    *,
    title: str,
    research_bundle: dict[str, object] | None = None,
    key_prefix: str,
    force_refresh: bool,
    click_target: str = "",
) -> None:
    bundle_id = str(mover.get("bundle_id") or "").strip()
    toggle_key = _bundle_toggle_key(bundle_id, key_prefix)
    bundle = research_bundle if isinstance(research_bundle, dict) else {}
    payload = bundle if bundle else mover
    what_changed = _raw_attention_text(payload.get("what_changed_text") or mover.get("what_changed_text"))
    why_today = _raw_attention_text(payload.get("why_now_text") or mover.get("why_now_text"))
    what_else_moved = _raw_attention_text(payload.get("what_else_moved_text") or mover.get("what_else_moved_text"))
    with st.container(border=True):
        header_cols = st.columns([4.8, 1.0, 1.5])
        with header_cols[0]:
            st.markdown(f"##### {title}")
            _render_ticker_snapshot_table(
                cfg,
                [
                    {
                        "symbol": str(mover.get("symbol") or "").strip(),
                        "extras": [
                            str(mover.get("sector") or "").strip(),
                            str(mover.get("industry") or "").strip(),
                        ],
                    }
                ],
                force_refresh=force_refresh,
                show_header=False,
                click_target=click_target,
                key_prefix=f"{key_prefix}_{bundle_id or mover.get('symbol') or 'mover'}_anchor",
            )
        with header_cols[1]:
            st.metric("Move", _format_scalar(payload.get("change_pct", mover.get("change_pct")), digits=1, suffix="%", signed=True))
        with header_cols[2]:
            if st.button(
                "Show research" if not st.session_state.get(toggle_key, False) else "Hide research",
                key=f"{toggle_key}_button",
                use_container_width=True,
            ):
                st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)

        if what_changed:
            st.markdown(f"**What Changed Vs Expectation**  \n{what_changed}")
        if why_today:
            st.markdown(f"**Why Today**  \n{why_today}")
        if what_else_moved:
            st.markdown(f"**What Else Moved**  \n{what_else_moved}")
        meta_line = [
            part
            for part in [
                str(payload.get("confidence_label") or mover.get("confidence_label") or "").strip(),
                str(payload.get("cause_status") or mover.get("cause_status") or "").replace("_", " ").title().strip(),
                str(payload.get("evidence_quality") or "").strip(),
                str(payload.get("freshness_quality") or "").strip(),
                str(payload.get("source_summary") or mover.get("top_source") or "").strip(),
            ]
            if part
        ]
        if meta_line:
            st.caption(" | ".join(meta_line))

        if st.session_state.get(toggle_key, False) and bundle_id:
            if not bundle:
                bundle = _safe_load_attention_research_bundle_cached(cfg, bundle_id, force_refresh=force_refresh)
            st.markdown("---")
            _render_attention_research_bundle_panel(
                bundle,
                ticker_table_key_prefix=f"{key_prefix}_{bundle_id or mover.get('symbol') or 'mover'}_bundle",
            )


@st.cache_data(ttl=21600, show_spinner=False)
def _synthesize_attention_summary_audio_cached(
    summary_text: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    base_url: str,
) -> bytes:
    from services.elevenlabs_tts import ElevenLabsTTSClient, ElevenLabsTTSConfig

    cfg = load_elevenlabs_tts_config()
    if cfg is None:
        raise ElevenLabsTTSAPIError("ElevenLabs is not configured.")
    return ElevenLabsTTSClient(
        ElevenLabsTTSConfig(
            api_key=cfg.api_key,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
            base_url=base_url,
            timeout_seconds=cfg.timeout_seconds,
        )
    ).synthesize(summary_text)


def _attention_summary_audio_task_key(
    *,
    audio_text: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    base_url: str,
) -> str:
    return hashlib.sha256(
        "||".join(
            [
                audio_text.strip(),
                voice_id.strip(),
                model_id.strip(),
                output_format.strip(),
                base_url.strip(),
            ]
        ).encode("utf-8")
    ).hexdigest()


def _ensure_attention_summary_audio_future(
    *,
    task_key: str,
    audio_text: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    base_url: str,
) -> Future[bytes]:
    with _ATTENTION_SUMMARY_AUDIO_LOCK:
        future = _ATTENTION_SUMMARY_AUDIO_FUTURES.get(task_key)
        if future is None:
            future = _ATTENTION_SUMMARY_AUDIO_EXECUTOR.submit(
                _synthesize_attention_summary_audio_cached,
                audio_text,
                voice_id,
                model_id,
                output_format,
                base_url,
            )
            _ATTENTION_SUMMARY_AUDIO_FUTURES[task_key] = future
        return future


def _attention_summary_audio_session_key(task_key: str) -> str:
    return f"attention_summary_audio::{task_key}"


def _attention_summary_audio_error_session_key(task_key: str) -> str:
    return f"attention_summary_audio_error::{task_key}"


def _try_decode_attention_summary_audio(summary_payload: dict[str, object], *, audio_text: str = "") -> bytes:
    encoded = str(summary_payload.get("audio_base64") or "").strip()
    if not encoded:
        return b""
    stored_hash = str(summary_payload.get("audio_text_hash") or "").strip()
    if stored_hash and audio_text:
        current_hash = hashlib.sha256(audio_text.strip().encode("utf-8")).hexdigest()
        if stored_hash != current_hash:
            return b""
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception:
        return b""


def _render_attention_summary_audio_player(
    *,
    audio_bytes: bytes,
    mime_type: str,
) -> None:
    if not audio_bytes:
        return
    st.audio(audio_bytes, format=mime_type)


def _render_attention_summary_async_audio_fallback(
    *,
    task_key: str,
    audio_text: str,
    elevenlabs_cfg: object,
) -> None:
    voice_id = str(getattr(elevenlabs_cfg, "voice_id", "") or "").strip()
    model_id = str(getattr(elevenlabs_cfg, "model_id", "") or "").strip()
    output_format = str(getattr(elevenlabs_cfg, "output_format", "") or "").strip()
    base_url = str(getattr(elevenlabs_cfg, "base_url", "") or "").strip()
    if not audio_text or not voice_id or not model_id or not output_format or not base_url:
        return

    @st.fragment(run_every=2)
    def _audio_fragment() -> None:
        audio_session_key = _attention_summary_audio_session_key(task_key)
        error_session_key = _attention_summary_audio_error_session_key(task_key)
        future = _ensure_attention_summary_audio_future(
            task_key=task_key,
            audio_text=audio_text,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
            base_url=base_url,
        )
        if future.done():
            try:
                audio_bytes = future.result()
            except ElevenLabsTTSAPIError as exc:
                with _ATTENTION_SUMMARY_AUDIO_LOCK:
                    _ATTENTION_SUMMARY_AUDIO_FUTURES.pop(task_key, None)
                st.session_state[error_session_key] = f"Could not generate audio feed: {exc}"
                st.rerun()
            except Exception as exc:
                with _ATTENTION_SUMMARY_AUDIO_LOCK:
                    _ATTENTION_SUMMARY_AUDIO_FUTURES.pop(task_key, None)
                st.session_state[error_session_key] = f"Unexpected audio feed error: {exc}"
                st.rerun()
            st.session_state[audio_session_key] = base64.b64encode(audio_bytes).decode("ascii")
            st.session_state.pop(error_session_key, None)
            with _ATTENTION_SUMMARY_AUDIO_LOCK:
                _ATTENTION_SUMMARY_AUDIO_FUTURES.pop(task_key, None)
            st.rerun()

    _audio_fragment()


def _render_attention_home_summary_card(
    home_payload: dict[str, object],
    *,
    snapshot_label: str,
    title: str = "Market Summary",
) -> None:
    def _render_summary_trace(summary_payload: dict[str, object]) -> None:
        queries = [str(item).strip() for item in list(summary_payload.get("research_queries") or []) if str(item).strip()]
        top_sources = [item for item in list(summary_payload.get("top_sources") or []) if isinstance(item, dict)]
        supporting_claims = [item for item in list(summary_payload.get("supporting_claims") or []) if isinstance(item, dict)]
        if not queries and not top_sources and not supporting_claims:
            return
        with st.expander("Research trace", expanded=False):
            if queries:
                st.markdown("**Searches run**")
                for query in queries[:4]:
                    st.markdown(f"- `{query}`")
            if top_sources:
                st.markdown("**Sources used**")
                for item in top_sources[:4]:
                    source = str(item.get("source") or "Source").strip()
                    title_text = str(item.get("title") or "").strip()
                    url = str(item.get("url") or "").strip()
                    match_source = str(item.get("match_source") or "").strip()
                    suffix = f" ({match_source})" if match_source else ""
                    if url:
                        st.markdown(f"- **{source}**: [{title_text or url}]({url}){suffix}")
                    else:
                        st.markdown(f"- **{source}**: {title_text or 'Untitled'}{suffix}")
            if supporting_claims:
                st.markdown("**Supporting evidence**")
                for item in supporting_claims[:3]:
                    text = str(item.get("text") or "").strip()
                    source = str(item.get("source") or "").strip()
                    if not text:
                        continue
                    label = f"**{source}**: " if source else ""
                    st.markdown(f"- {label}{text}")

    stored_summary_payload = home_payload.get("homepage_summary")
    summary_payload = dict(stored_summary_payload) if isinstance(stored_summary_payload, dict) else {}
    if not summary_payload:
        summary_payload = build_attention_home_summary_payload(home_payload)
    summary_payload = apply_display_limits(summary_payload)
    summary_text = str(summary_payload.get("summary_text") or "").strip()
    audio_text = str(summary_payload.get("audio_text") or summary_text).strip()
    elevenlabs_cfg = load_elevenlabs_tts_config()
    preloaded_audio_bytes = _try_decode_attention_summary_audio(summary_payload, audio_text=audio_text)
    async_task_key = ""
    async_audio_bytes = b""
    async_audio_error = ""
    if not preloaded_audio_bytes and audio_text and elevenlabs_cfg:
        async_task_key = _attention_summary_audio_task_key(
            audio_text=audio_text,
            voice_id=str(getattr(elevenlabs_cfg, "voice_id", "") or ""),
            model_id=str(getattr(elevenlabs_cfg, "model_id", "") or ""),
            output_format=str(getattr(elevenlabs_cfg, "output_format", "") or ""),
            base_url=str(getattr(elevenlabs_cfg, "base_url", "") or ""),
        )
        async_audio_encoded = str(
            st.session_state.get(_attention_summary_audio_session_key(async_task_key)) or ""
        ).strip()
        async_audio_error = str(
            st.session_state.get(_attention_summary_audio_error_session_key(async_task_key)) or ""
        ).strip()
        if async_audio_encoded:
            try:
                async_audio_bytes = base64.b64decode(async_audio_encoded.encode("ascii"), validate=True)
            except Exception:
                async_audio_bytes = b""

    with st.container(border=True):
        if summary_text:
            st.markdown(summary_text)
        _render_summary_trace(summary_payload)

        if not audio_text:
            return
        if preloaded_audio_bytes:
            _render_attention_summary_audio_player(
                audio_bytes=preloaded_audio_bytes,
                mime_type=str(summary_payload.get("audio_mime_type") or "audio/mpeg"),
            )
            return
        if async_audio_bytes:
            from services.elevenlabs_tts import audio_mime_type as elevenlabs_audio_mime_type

            _render_attention_summary_audio_player(
                audio_bytes=async_audio_bytes,
                mime_type=elevenlabs_audio_mime_type(str(getattr(elevenlabs_cfg, "output_format", "") or "")),
            )
            return
        if not elevenlabs_cfg:
            return
        if async_audio_error:
            return

        _render_attention_summary_async_audio_fallback(
            task_key=async_task_key,
            audio_text=audio_text,
            elevenlabs_cfg=elevenlabs_cfg,
        )


def _render_home_attention(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
    page_title: str = "Attention Market Overview",
    page_caption: str = "Today / 1d market activity: what changed versus expectation, why it changed today, and what else moved because of it.",
    show_trend_surface: bool = True,
    require_api: bool = True,
) -> None:
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title(page_title)
        st.caption(page_caption)
    with header_cols[1]:
        _render_section_back_button("home_attention_back")

    if require_api and api is None:
        st.info("Configure the live market connection to enable the daily market overview, market context, and research bundle lookups.")
        return

    try:
        with _inline_loading_banner(
            "Loading today's attention overview",
            "Pulling the latest precomputed snapshot for top events, standout movers, and unresolved market signals.",
        ):
            home_payload = _load_attention_home_1d_cached(
                cfg,
                force_refresh=force_data_refresh,
            )
    except Exception as exc:
        st.warning(f"Could not load the attention layer: {exc}")
        return

    top_events = list(home_payload.get("top_events") or [])
    must_read = list(home_payload.get("must_read_movers") or [])
    unresolved = list(home_payload.get("unresolved_large_moves") or [])
    taxonomy_trends = list(home_payload.get("taxonomy_horizon_trends") or [])
    coverage_summary = dict(home_payload.get("coverage_summary") or {})
    generated_at = pd.to_datetime(home_payload.get("generated_at_utc"), utc=True, errors="coerce")
    snapshot_label = generated_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(generated_at) else "just now"
    trend_cohort_count = sum(len(list(item.get("cohorts") or [])) for item in taxonomy_trends)

    metric_cols = st.columns(5)
    with metric_cols[0]:
        st.metric("Snapshot", snapshot_label)
    with metric_cols[1]:
        st.metric("Top Events", str(len(top_events)))
    with metric_cols[2]:
        st.metric("Must-Read Movers", str(len(must_read)))
    with metric_cols[3]:
        st.metric("Unresolved Large Moves", str(len(unresolved)))
    with metric_cols[4]:
        st.metric("Trend Cohorts", str(trend_cohort_count))

    if not pipeline_store_configured():
        st.caption("Pipeline snapshots are not configured, so this page is running on the live on-demand fallback.")

    if not top_events and not must_read and not unresolved:
        st.info("No daily attention items were produced from the latest market activity. Refresh after the market data sources update.")
        return

    _render_attention_home_summary_card(
        home_payload,
        snapshot_label=snapshot_label,
    )
    st.markdown("---")
    st.subheader("Top Market Events Today")
    if not top_events:
        st.info("No market-wide events cleared the bar in the latest run.")
    else:
        for event in top_events:
                _render_attention_home_event_card(
                    cfg,
                    event,
                    research_bundle={},
                    key_prefix="home_top_event",
                    force_refresh=force_data_refresh,
                    click_target="home",
                )

    st.markdown("---")
    second_cols = st.columns(2, gap="large")
    with second_cols[0]:
        st.subheader("Must-Read Movers Today")
        if not must_read:
            st.info("No standalone movers survived after event clustering.")
        else:
            for mover in must_read:
                _render_attention_home_mover_card(
                    cfg,
                    mover,
                    title=_attention_mover_card_title(mover),
                    research_bundle={},
                    key_prefix="home_must_read",
                    force_refresh=force_data_refresh,
                    click_target="home",
                )

    with second_cols[1]:
        st.subheader("Unresolved Large Moves")
        if not unresolved:
            st.info("No large unresolved moves in the latest run.")
        else:
            for mover in unresolved:
                _render_attention_home_mover_card(
                    cfg,
                    mover,
                    title=_attention_mover_card_title(mover),
                    research_bundle={},
                    key_prefix="home_unresolved",
                    force_refresh=force_data_refresh,
                    click_target="home",
                )

    if show_trend_surface:
        st.markdown("---")
        st.subheader("Taxonomy Trend Surface")
        if not taxonomy_trends:
            st.info("No multi-horizon taxonomy cohorts were produced in this run.")
        else:
            for horizon_row in taxonomy_trends:
                horizon_label = str(horizon_row.get("horizon_label") or horizon_row.get("horizon") or "").strip()
                cohorts = list(horizon_row.get("cohorts") or [])
                if not cohorts:
                    continue
                st.caption(horizon_label or "Horizon")
                trend_table = pd.DataFrame(
                    [
                        {
                            "Cohort": str(item.get("peer_group_name") or "").strip(),
                            "Members": int(pd.to_numeric(item.get("member_count"), errors="coerce") or 0),
                            "Breadth": f"{int(pd.to_numeric(item.get('breadth_up'), errors='coerce') or 0)} up / {int(pd.to_numeric(item.get('breadth_down'), errors='coerce') or 0)} down",
                            "Mean Attention": float(pd.to_numeric(item.get("mean_attention_score"), errors="coerce") or 0.0),
                            "Mean |z|": float(pd.to_numeric(item.get("mean_abs_residual_zscore"), errors="coerce") or 0.0),
                            "Leader": str(item.get("leader_symbol") or "").strip(),
                        }
                        for item in cohorts
                    ]
                )
                if not trend_table.empty:
                    st.dataframe(trend_table, hide_index=True, use_container_width=True)

        with st.expander("Coverage Summary"):
            st.write(
                f"Universe: {int(pd.to_numeric(coverage_summary.get('equity_universe_count'), errors='coerce') or 0)} equities "
                f"+ {int(pd.to_numeric(coverage_summary.get('macro_anchor_target_count'), errors='coerce') or 0)} macro anchors."
            )
            st.write(
                f"Candidates: {int(pd.to_numeric(coverage_summary.get('candidate_count'), errors='coerce') or 0)} | "
                f"News-backed: {int(pd.to_numeric(coverage_summary.get('news_backed_count'), errors='coerce') or 0)} | "
                f"Portfolio overlap: {int(pd.to_numeric(coverage_summary.get('portfolio_overlap_count'), errors='coerce') or 0)}"
            )
            st.caption(
                f"Trend surface: {int(pd.to_numeric(coverage_summary.get('taxonomy_trend_horizon_count'), errors='coerce') or 0)} horizons / "
                f"{int(pd.to_numeric(coverage_summary.get('taxonomy_trend_cohort_count'), errors='coerce') or 0)} cohorts."
            )

    selected_home_ticker = str(st.session_state.get("home_selected_ticker") or "").upper().strip()
    if selected_home_ticker:
        st.markdown("---")
        _render_home_ticker_background_panel(
            cfg,
            selected_home_ticker,
            force_data_refresh=force_data_refresh,
        )


def _select_homepage_v2_bundle(bundle_id: str, symbols: list[str] | None = None, title: str = "") -> None:
    normalized_bundle_id = str(bundle_id or "").strip()
    if normalized_bundle_id:
        _record_usage_interaction(
            event_type="bundle_open",
            detail={
                "surface": "home_v2",
                "target_type": "bundle",
                "target_id": normalized_bundle_id,
                "target_label": str(title or normalized_bundle_id).strip(),
                "symbols": [str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()][:8],
            },
        )
    st.session_state["homepage_v2_selected_bundle_id"] = normalized_bundle_id
    st.session_state["homepage_v2_active_panel"] = HOMEPAGE_V2_RESEARCH_PANEL
    current_ticker = str(st.session_state.get("homepage_v2_selected_ticker") or "").upper().strip()
    if current_ticker:
        st.session_state["homepage_v2_selected_ticker"] = current_ticker
        return
    symbol_options = [str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()]
    st.session_state["homepage_v2_selected_ticker"] = symbol_options[0] if symbol_options else ""


def _homepage_v2_surface_key(bundle_id: str, *, index: int, selected: bool) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(bundle_id or index).strip())
    prefix = "homepage_v2_surface_selected" if selected else "homepage_v2_surface_idle"
    return f"{prefix}_{cleaned}"


def _select_homepage_exp_bundle(bundle_id: str, symbols: list[str] | None = None, title: str = "") -> None:
    normalized_bundle_id = str(bundle_id or "").strip()
    if normalized_bundle_id:
        _record_usage_interaction(
            event_type="bundle_open",
            detail={
                "surface": "home_exp",
                "target_type": "bundle",
                "target_id": normalized_bundle_id,
                "target_label": str(title or normalized_bundle_id).strip(),
                "symbols": [str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()][:8],
            },
        )
    st.session_state["homepage_exp_selected_bundle_id"] = normalized_bundle_id
    st.session_state["homepage_exp_active_panel"] = HOMEPAGE_V2_RESEARCH_PANEL
    symbol_options = [str(symbol).upper().strip() for symbol in list(symbols or []) if str(symbol).strip()]
    current_ticker = str(st.session_state.get("homepage_exp_selected_ticker") or "").upper().strip()
    if current_ticker and current_ticker in symbol_options:
        st.session_state["homepage_exp_selected_ticker"] = current_ticker
        return
    if symbol_options:
        st.session_state["homepage_exp_selected_ticker"] = symbol_options[0]


def _open_homepage_exp_ticker(symbol: str, bundle_id: str = "") -> None:
    cleaned_symbol = str(symbol or "").upper().strip()
    cleaned_bundle_id = str(bundle_id or "").strip()
    if not cleaned_symbol:
        return
    _record_usage_interaction(
        event_type="ticker_open",
        detail={
            "surface": "home_exp",
            "symbol": cleaned_symbol,
            "target_type": "ticker",
            "target_label": cleaned_symbol,
            "target_id": cleaned_symbol,
            "bundle_id": cleaned_bundle_id,
        },
    )
    if cleaned_bundle_id:
        st.session_state["homepage_exp_selected_bundle_id"] = cleaned_bundle_id
    st.session_state["homepage_exp_selected_ticker"] = cleaned_symbol
    st.session_state["homepage_exp_active_panel"] = HOMEPAGE_V2_COMPANY_PANEL


def _homepage_exp_surface_key(bundle_id: str, *, index: int, state: str = "idle") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(bundle_id or index).strip())
    if state == "selected_company":
        prefix = "homepage_exp_surface_selected_company"
    elif state == "selected_research":
        prefix = "homepage_exp_surface_selected_research"
    else:
        prefix = "homepage_exp_surface_idle"
    return f"{prefix}_{cleaned}"


def _render_homepage_exp_rail_header(
    title: str,
    *,
    caption: str = "",
    clear_button_label: str = "",
    clear_button_key: str = "",
    clear_session_key: str = "",
    clear_mode_key: str = "",
    clear_mode_value: str = HOMEPAGE_V2_RESEARCH_PANEL,
) -> None:
    cleaned_title = str(title or "").strip()
    cleaned_caption = str(caption or "").strip()
    if not cleaned_title:
        return
    if not str(clear_button_label or "").strip():
        st.markdown(
            f"<div class='sn-rail-title'>{html.escape(cleaned_title)}</div>",
            unsafe_allow_html=True,
        )
        if cleaned_caption:
            st.markdown(
                f"<div class='sn-rail-caption'>{html.escape(cleaned_caption)}</div>",
                unsafe_allow_html=True,
            )
        return
    header_cols = st.columns([8.8, 0.65], gap="small")
    with header_cols[0]:
        st.markdown(
            f"<div class='sn-rail-title'>{html.escape(cleaned_title)}</div>",
            unsafe_allow_html=True,
        )
        if cleaned_caption:
            st.markdown(
                f"<div class='sn-rail-caption'>{html.escape(cleaned_caption)}</div>",
                unsafe_allow_html=True,
            )
    with header_cols[1]:
        if st.button(
            clear_button_label,
            key=str(clear_button_key or f"homepage_exp_rail_clear_{cleaned_title}").strip(),
            use_container_width=True,
        ):
            st.session_state.pop(clear_session_key or "homepage_exp_selected_ticker", None)
            if clear_mode_key == "homepage_v2_active_panel":
                _queue_homepage_v2_active_panel(clear_mode_value)
            elif clear_mode_key:
                st.session_state[clear_mode_key] = clear_mode_value
            st.rerun()


@st.fragment
def _render_homepage_v2_story_fragment(
    cfg: AppConfig,
    beats: list[dict[str, object]],
    *,
    run_token: str,
    force_data_refresh: bool,
) -> None:
    active_run_token = str(run_token or "").strip()
    previous_run_token = str(st.session_state.get("homepage_v2_bundle_run_token") or "").strip()
    if active_run_token and previous_run_token and active_run_token != previous_run_token:
        stale_keys = [key for key in st.session_state.keys() if str(key).startswith("attention_bundle_cache_")]
        for key in stale_keys:
            st.session_state.pop(key, None)
    st.session_state["homepage_v2_bundle_run_token"] = active_run_token
    _consume_homepage_v2_pending_panel()

    selection_state = normalize_homepage_v2_detail_state(
        beats,
        selected_bundle_id=str(st.session_state.get("homepage_v2_selected_bundle_id") or "").strip(),
        selected_ticker=str(st.session_state.get("homepage_v2_selected_ticker") or "").strip(),
        active_panel=str(st.session_state.get("homepage_v2_active_panel") or "").strip(),
    )
    selected_bundle_id = str(selection_state.get("selected_bundle_id") or "").strip()
    selected_ticker = str(selection_state.get("selected_ticker") or "").upper().strip()
    active_panel = str(selection_state.get("active_panel") or HOMEPAGE_V2_RESEARCH_PANEL).strip()
    st.session_state["homepage_v2_selected_bundle_id"] = selected_bundle_id
    st.session_state["homepage_v2_selected_ticker"] = selected_ticker
    st.session_state["homepage_v2_active_panel"] = active_panel

    bundle_symbol_lookup = homepage_v2_bundle_symbol_lookup(beats)
    panel_labels = {
        HOMEPAGE_V2_RESEARCH_PANEL: "Retained research",
        HOMEPAGE_V2_COMPANY_PANEL: f"Company background{f' · {selected_ticker}' if selected_ticker else ''}",
    }

    main_cols = st.columns([1.45, 1.05], gap="large")
    with main_cols[0]:
        st.subheader("Narrative Thread")
        st.caption("Each beat stays compact. Choose any headline to load retained research in the rail, or choose any ticker preview to switch the rail into company background.")
        for index, beat in enumerate(beats):
            beat_sentence = str(beat.get("sentence") or "").strip()
            bundle_id = str(beat.get("bundle_id") or "").strip()
            if not beat_sentence:
                continue
            beat_summary = str(beat.get("summary") or "").strip()
            beat_kind = str(beat.get("kind") or "").replace("_", " ").title().strip()
            beat_symbols = bundle_symbol_lookup.get(bundle_id) or [
                str(symbol).upper().strip()
                for symbol in list(beat.get("symbols") or [])
                if str(symbol).strip()
            ]
            is_selected = bool(bundle_id) and bundle_id == selected_bundle_id and active_panel == HOMEPAGE_V2_RESEARCH_PANEL
            with st.container(border=True):
                if bundle_id:
                    st.button(
                        f"{index + 1}. {beat_sentence}",
                        key=_homepage_v2_surface_key(bundle_id, index=index, selected=is_selected),
                        use_container_width=True,
                        type="tertiary",
                        help="Load retained research for this beat in the drilldown rail.",
                        on_click=_select_homepage_v2_bundle,
                        args=(bundle_id, list(beat_symbols), beat_sentence),
                    )
                else:
                    st.markdown(f"##### {index + 1}. {beat_sentence}")
                meta = [item for item in [beat_kind, f"{len(beat_symbols)} symbols" if beat_symbols else ""] if item]
                if is_selected:
                    meta.append("In rail")
                if meta:
                    st.caption(" | ".join(meta))
                if beat_summary:
                    st.write(beat_summary)
                if beat_symbols:
                    _render_ticker_snapshot_table(
                        cfg,
                        [{"symbol": symbol} for symbol in beat_symbols[:8] if str(symbol).strip()],
                        force_refresh=force_data_refresh,
                        show_header=True,
                        click_target="home_v2",
                        key_prefix=f"homepage_v2_beat_{bundle_id or index}_symbols",
                        allow_live_profile_fallback=False,
                    )

    with main_cols[1]:
        st.subheader("Drilldown Rail")
        available_panels = [HOMEPAGE_V2_RESEARCH_PANEL]
        if selected_ticker:
            available_panels.append(HOMEPAGE_V2_COMPANY_PANEL)
        st.radio(
            "Rail view",
            available_panels,
            key="homepage_v2_active_panel",
            horizontal=True,
            label_visibility="collapsed",
            format_func=lambda key: panel_labels.get(key, key.replace("_", " ").title()),
        )

        with st.container(border=True):
            active_panel = str(st.session_state.get("homepage_v2_active_panel") or HOMEPAGE_V2_RESEARCH_PANEL).strip()
            if active_panel == HOMEPAGE_V2_COMPANY_PANEL:
                if not selected_ticker:
                    st.info("Choose a ticker preview from the narrative thread to load company background here.")
                else:
                    _render_home_ticker_background_panel(
                        cfg,
                        selected_ticker,
                        force_data_refresh=force_data_refresh,
                        session_key="homepage_v2_selected_ticker",
                        clear_mode_key="homepage_v2_active_panel",
                        clear_mode_value=HOMEPAGE_V2_RESEARCH_PANEL,
                        panel_title=f"{selected_ticker} Company Background",
                        panel_caption="Loaded from the narrative rail ticker preview selection.",
                        clear_button_label="Close",
                    )
            elif not selected_bundle_id:
                st.info("Pick a beat from the narrative thread to inspect the retained research.")
            else:
                if _has_cached_attention_bundle(selected_bundle_id, run_token=active_run_token) and not force_data_refresh:
                    bundle = _load_attention_research_bundle_session_cached(
                        cfg,
                        selected_bundle_id,
                        run_token=active_run_token,
                        force_refresh=False,
                    )
                else:
                    with _inline_loading_banner(
                        "Loading research",
                        "Pulling the retained evidence, price context, and linked symbols for this beat.",
                    ):
                        bundle = _load_attention_research_bundle_session_cached(
                            cfg,
                            selected_bundle_id,
                            run_token=active_run_token,
                            force_refresh=force_data_refresh,
                        )
                fallback_item = next((beat for beat in beats if str(beat.get("bundle_id") or "").strip() == selected_bundle_id), {})
                title = _attention_bundle_title(bundle, fallback=fallback_item)
                st.markdown(f"### {title}")
                _render_attention_research_bundle_panel(
                    bundle,
                    ticker_click_target="home_v2",
                    ticker_table_key_prefix=f"homepage_v2_bundle_{selected_bundle_id or 'selected'}",
                )


@st.fragment
def _render_homepage_exp_story_fragment(
    cfg: AppConfig,
    beats: list[dict[str, object]],
    *,
    run_token: str,
    force_data_refresh: bool,
) -> None:
    active_run_token = str(run_token or "").strip()
    previous_run_token = str(st.session_state.get("homepage_exp_bundle_run_token") or "").strip()
    if active_run_token and previous_run_token and active_run_token != previous_run_token:
        stale_keys = [key for key in st.session_state.keys() if str(key).startswith("attention_bundle_cache_")]
        for key in stale_keys:
            st.session_state.pop(key, None)
    st.session_state["homepage_exp_bundle_run_token"] = active_run_token

    selection_state = normalize_homepage_v2_detail_state(
        beats,
        selected_bundle_id=str(st.session_state.get("homepage_exp_selected_bundle_id") or "").strip(),
        selected_ticker=str(st.session_state.get("homepage_exp_selected_ticker") or "").strip(),
        active_panel=str(st.session_state.get("homepage_exp_active_panel") or "").strip(),
    )
    selected_bundle_id = str(selection_state.get("selected_bundle_id") or "").strip()
    selected_ticker = str(selection_state.get("selected_ticker") or "").upper().strip()
    active_panel = str(selection_state.get("active_panel") or HOMEPAGE_V2_RESEARCH_PANEL).strip()
    st.session_state["homepage_exp_selected_bundle_id"] = selected_bundle_id
    st.session_state["homepage_exp_selected_ticker"] = selected_ticker
    st.session_state["homepage_exp_active_panel"] = active_panel

    bundle_symbol_lookup = homepage_v2_bundle_symbol_lookup(beats)
    beat_lookup = {
        str(beat.get("bundle_id") or "").strip(): beat
        for beat in beats
        if str((beat or {}).get("bundle_id") or "").strip()
    }
    selected_beat = beat_lookup.get(selected_bundle_id, {})

    main_cols = st.columns([1.45, 1.05], gap="large")
    with main_cols[0]:
        for index, beat in enumerate(beats):
            beat_sentence = str(beat.get("sentence") or "").strip()
            bundle_id = str(beat.get("bundle_id") or "").strip()
            if not beat_sentence:
                continue
            beat_summary = str(beat.get("summary") or "").strip()
            beat_kind = str(beat.get("kind") or "").replace("_", " ").title().strip()
            beat_symbols = bundle_symbol_lookup.get(bundle_id) or [
                str(symbol).upper().strip()
                for symbol in list(beat.get("symbols") or [])
                if str(symbol).strip()
            ]
            is_selected = bool(bundle_id) and bundle_id == selected_bundle_id
            company_focus = (
                active_panel == HOMEPAGE_V2_COMPANY_PANEL
                and is_selected
                and bool(selected_ticker)
                and selected_ticker in beat_symbols
            )
            surface_state = "selected_company" if company_focus else "selected_research" if is_selected else "idle"
            with st.container(border=True):
                if bundle_id:
                    st.button(
                        f"{index + 1}. {beat_sentence}",
                        key=_homepage_exp_surface_key(bundle_id, index=index, state=surface_state),
                        use_container_width=True,
                        type="tertiary",
                        on_click=_select_homepage_exp_bundle,
                        args=(bundle_id, list(beat_symbols), beat_sentence),
                    )
                else:
                    st.markdown(f"##### {index + 1}. {beat_sentence}")
                meta = [item for item in [beat_kind, f"{len(beat_symbols)} symbols" if beat_symbols else ""] if item]
                if meta:
                    st.caption(" | ".join(meta))
                if beat_summary:
                    st.write(beat_summary)
                if beat_symbols:
                    _render_ticker_snapshot_table(
                        cfg,
                        [{"symbol": symbol} for symbol in beat_symbols[:8] if str(symbol).strip()],
                        force_refresh=force_data_refresh,
                        show_header=True,
                        click_target="home_exp",
                        key_prefix=f"homepage_exp_beat_{bundle_id or index}_symbols",
                        click_bundle_id=bundle_id,
                        allow_live_profile_fallback=False,
                    )

    with main_cols[1]:
        if active_panel == HOMEPAGE_V2_COMPANY_PANEL:
            with st.container(border=True):
                if not selected_ticker:
                    st.info("Select a ticker to load company background.")
                else:
                    _render_homepage_exp_rail_header(
                        f"{selected_ticker} Background",
                        clear_button_label="X",
                        clear_button_key=f"homepage_exp_close_{selected_ticker}",
                        clear_session_key="homepage_exp_selected_ticker",
                        clear_mode_key="homepage_exp_active_panel",
                        clear_mode_value=HOMEPAGE_V2_RESEARCH_PANEL,
                    )
                    _render_home_ticker_background_panel(
                        cfg,
                        selected_ticker,
                        force_data_refresh=force_data_refresh,
                        session_key="homepage_exp_selected_ticker",
                        clear_mode_key="homepage_exp_active_panel",
                        clear_mode_value=HOMEPAGE_V2_RESEARCH_PANEL,
                        panel_title="",
                        panel_caption="",
                        open_button_label="",
                        clear_button_label="",
                        clear_button_key="",
                        show_header=False,
                        show_container_border=False,
                    )
        elif not selected_bundle_id:
            with st.container(border=True):
                st.info("Select a beat to load retained research.")
        else:
            if _has_cached_attention_bundle(selected_bundle_id, run_token=active_run_token) and not force_data_refresh:
                bundle = _load_attention_research_bundle_session_cached(
                    cfg,
                    selected_bundle_id,
                    run_token=active_run_token,
                    force_refresh=False,
                )
            else:
                with _inline_loading_banner(
                    "Loading research",
                    "Pulling the retained evidence, price context, and linked symbols for this beat.",
                ):
                    bundle = _load_attention_research_bundle_session_cached(
                        cfg,
                        selected_bundle_id,
                        run_token=active_run_token,
                        force_refresh=force_data_refresh,
                    )
            fallback_item = selected_beat if isinstance(selected_beat, dict) else {}
            title = _attention_bundle_title(bundle, fallback=fallback_item)
            with st.container(border=True):
                _render_homepage_exp_rail_header(title)
                _render_attention_research_bundle_panel(
                    bundle,
                    ticker_click_target="home_exp",
                    ticker_table_key_prefix=f"homepage_exp_bundle_{selected_bundle_id or 'selected'}",
                )


def _render_homepage_v2_graph_banner(home_payload: dict[str, object]) -> None:
    homepage_graph = dict(home_payload.get("homepage_graph") or {}) if isinstance(home_payload, dict) else {}
    figure_json = homepage_graph.get("figure")
    if not isinstance(figure_json, dict) or not figure_json:
        return
    try:
        figure = go.Figure(figure_json)
    except Exception:
        return
    with st.container(border=True):
        st.plotly_chart(
            figure,
            use_container_width=True,
            config={"displayModeBar": False, "displaylogo": False, "scrollZoom": False},
        )

def _build_homepage_narrative_beats(home_payload: dict[str, object]) -> list[dict[str, object]]:
    return build_attention_home_narrative_beats(home_payload if isinstance(home_payload, dict) else {})


def _load_homepage_narrative_payload(cfg: AppConfig, *, force_data_refresh: bool) -> dict[str, object] | None:
    try:
        with _inline_loading_banner(
            "Loading today's narrative home",
            "Assembling the day-only homepage summary from the latest event set and research snapshot.",
        ):
            return _load_attention_home_1d_cached(
                cfg,
                force_refresh=force_data_refresh,
            )
    except Exception as exc:
        st.warning(f"Could not build Home: {exc}")
        return None


def _load_homepage_replay_payload(target_date: str) -> dict[str, object] | None:
    """Load a full homepage payload for a historical date."""
    try:
        from data_access.layer import resolve_homepage_asof

        with _inline_loading_banner(
            f"Loading {target_date} snapshot",
            f"Retrieving the stored homepage from {target_date}.",
        ):
            replay = resolve_homepage_asof(target_date)
        home_payload = replay.get("home_payload")
        if not isinstance(home_payload, dict) or not home_payload:
            st.warning(f"No homepage snapshot found for {target_date}.")
            return None
        return home_payload
    except Exception as exc:
        st.warning(f"Could not load historical snapshot: {exc}")
        return None


def _render_homepage_v2(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    _, _back_col = st.columns([5, 1.4])
    with _back_col:
        _render_section_back_button("homepage_v2_back")

    # --- Historical replay: read date from sidebar selector ---
    replay_date = st.session_state.get("_homepage_replay_date")

    if replay_date is not None:
        home_payload = _load_homepage_replay_payload(replay_date)
    else:
        home_payload = _load_homepage_narrative_payload(
            cfg,
            force_data_refresh=force_data_refresh,
        )
    if not isinstance(home_payload, dict):
        return

    beats = _build_homepage_narrative_beats(home_payload)
    if not beats:
        st.info("No daily narrative beats were produced from the latest market activity.")
        return

    generated_at = pd.to_datetime(home_payload.get("generated_at_utc"), utc=True, errors="coerce")
    snapshot_label = generated_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(generated_at) else "just now"

    _render_homepage_v2_graph_banner(home_payload)
    _render_attention_home_summary_card(
        home_payload,
        snapshot_label=snapshot_label,
        title="Market Summary",
    )
    _render_homepage_exp_story_fragment(
        cfg,
        beats,
        run_token=str(home_payload.get("run_id") or home_payload.get("generated_at_utc") or "").strip(),
        force_data_refresh=force_data_refresh,
    )


def _render_experiment_placeholder_page(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    del cfg, force_data_refresh

    def _clear_experiment_graph_state(*, keep_query: bool = True) -> None:
        state_keys = [
            "_knowledge_graph_matches",
            "_knowledge_graph_resolved_query",
            "_knowledge_graph_draft",
            "_knowledge_graph_draft_query",
            "_knowledge_graph_draft_key",
            "_knowledge_graph_entity_mentions",
            "_knowledge_graph_entity_query",
            "_knowledge_graph_add_candidates",
            "knowledge_graph_selected_node_ids",
        ]
        if not keep_query:
            state_keys.extend(
                [
                    "knowledge_graph_query",
                    "_knowledge_graph_last_input_query",
                    "_knowledge_graph_commit_feedback",
                ]
            )
        for key in state_keys:
            st.session_state.pop(key, None)

    def _knowledge_graph_commit_actor() -> str:
        current_user = _current_user_context()
        if current_user is None:
            return "admin"
        return str(current_user.email or current_user.user_id or current_user.label or "admin").strip() or "admin"

    def _knowledge_graph_entity_rows(mentions: list[object]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for mention in mentions:
            to_dict = getattr(mention, "to_dict", None)
            row = to_dict() if callable(to_dict) else dict(mention or {})
            rows.append(
                {
                    "text": str(row.get("text") or ""),
                    "type": str(row.get("entity_type") or ""),
                    "source": str(row.get("source") or ""),
                    "link_status": str(row.get("link_status") or ""),
                    "kg_node_id": str(row.get("kg_node_id") or ""),
                    "kg_label": str(row.get("kg_label") or ""),
                    "canonical_id": str(row.get("canonical_id") or ""),
                    "confidence": float(row.get("confidence") or 0.0),
                    "reason": str(row.get("link_reason") or ""),
                }
            )
        return rows

    def _knowledge_graph_matches_from_entities(
        mentions: list[object],
        graph_snapshot: dict[str, object],
    ) -> list[dict[str, object]]:
        nodes_by_id = dict(graph_snapshot.get("nodes_by_id") or {})
        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in _knowledge_graph_entity_rows(mentions):
            node_id = str(row.get("kg_node_id") or "").strip()
            if not node_id or node_id in seen or node_id not in nodes_by_id:
                continue
            node = dict(nodes_by_id.get(node_id) or {})
            seen.add(node_id)
            rows.append(
                {
                    "node_id": node_id,
                    "canonical_label": str(node.get("canonical_label") or row.get("kg_label") or node_id),
                    "node_type": str(node.get("node_type") or row.get("type") or ""),
                    "description": str(node.get("description") or ""),
                    "matched_alias": str(row.get("text") or ""),
                    "match_source": f"entity:{row.get('source') or 'extraction'}",
                    "score": float(row.get("confidence") or 0.0),
                }
            )
        return rows

    def _merge_knowledge_graph_matches(*match_groups: list[dict[str, object]]) -> list[dict[str, object]]:
        merged: dict[str, dict[str, object]] = {}
        for group in match_groups:
            for match in group:
                node_id = str(match.get("node_id") or "").strip()
                if not node_id:
                    continue
                existing = merged.get(node_id)
                if existing is None or float(match.get("score") or 0.0) > float(existing.get("score") or 0.0):
                    merged[node_id] = dict(match)
        return sorted(merged.values(), key=lambda item: float(item.get("score") or 0.0), reverse=True)

    def _run_knowledge_graph_entity_extraction(
        query_text: str,
        graph_snapshot: dict[str, object],
    ) -> list[object]:
        status_box = st.status("Extracting entities", expanded=True)
        try:
            mentions = extract_entities(
                query_text,
                snapshot=graph_snapshot,
                include_taxonomy=False,
            )
        except Exception as exc:
            status_box.update(label="Entity extraction failed", state="error", expanded=True)
            status_box.write(f"{type(exc).__name__}: {exc}")
            return []

        linked_count = len(linked_kg_node_ids(mentions))
        add_count = len(graph_add_node_candidates(mentions))
        status_box.write(f"Linked {linked_count} existing graph node(s).")
        if add_count:
            status_box.write(f"Found {add_count} possible new node(s) to review.")
        status_box.update(
            label=f"Entity extraction complete: {len(mentions)} mention(s)",
            state="complete",
            expanded=False,
        )
        return list(mentions)

    def _default_selected_knowledge_graph_nodes(
        matches: list[dict[str, object]],
        mentions: list[object],
    ) -> list[str]:
        selected: list[str] = []
        mention_rows = _knowledge_graph_entity_rows(mentions)
        for node_id in [str(row.get("kg_node_id") or "").strip() for row in mention_rows]:
            if node_id not in selected:
                selected.append(node_id)
        for node_id in default_seed_node_ids_from_matches(matches):
            if node_id not in selected:
                selected.append(node_id)
        return selected[:6]

    def _inject_entity_add_candidates(
        draft: dict[str, object],
        candidates: list[dict[str, object]],
        graph_snapshot: dict[str, object],
    ) -> dict[str, object]:
        if not candidates:
            return draft
        nodes_by_id = dict(graph_snapshot.get("nodes_by_id") or {})
        out = dict(draft)
        node_rows = [dict(row) for row in list(out.get("nodes") or [])]
        existing_ids = {str(row.get("node_id") or "").strip() for row in node_rows}
        for candidate in candidates:
            node_id = str(candidate.get("node_id") or "").strip()
            if not node_id or node_id in existing_ids or node_id in nodes_by_id:
                continue
            existing_ids.add(node_id)
            node_rows.append(
                {
                    "keep": True,
                    "node_id": node_id,
                    "label": str(candidate.get("label") or node_id),
                    "node_type": str(candidate.get("node_type") or "concept"),
                    "description": "",
                    "status": "candidate",
                    "aliases": str(candidate.get("label") or node_id),
                    "source_status": "entity_candidate",
                    "confidence": candidate.get("confidence"),
                    "reason": str(candidate.get("reason") or f"Extracted from {candidate.get('source') or 'query'}"),
                }
            )
        out["nodes"] = node_rows
        return out

    def _run_knowledge_graph_search(query_text: str, graph_snapshot: dict[str, object]) -> list[dict[str, object]]:
        stage_labels = {
            "resolve_scan": "Scanning ids, aliases, and descriptions in the current graph.",
            "resolve_semantic": "Checking semantic similarity across existing nodes.",
            "resolve_done": "Finished scanning the current graph.",
        }
        status_box = st.status("Finding existing anchors", expanded=True)

        def _status_callback(stage: str, detail: str) -> None:
            message = str(detail or stage_labels.get(stage) or stage.replace("_", " ").title()).strip()
            if message:
                status_box.write(message)

        try:
            matches = search_knowledge_graph_nodes(
                query_text,
                snapshot=graph_snapshot,
                status_callback=_status_callback,
            )
        except Exception as exc:
            status_box.update(label="Existing anchor scan failed", state="error", expanded=True)
            status_box.write(f"{type(exc).__name__}: {exc}")
            return []

        if matches:
            status_box.update(
                label=f"Found {len(matches)} existing anchor(s)",
                state="complete",
                expanded=False,
            )
        else:
            status_box.update(
                label="No confident existing anchors found",
                state="complete",
                expanded=False,
            )
        return matches

    def _run_knowledge_graph_builder(
        query_text: str,
        *,
        graph_snapshot: dict[str, object],
        selected_ids: list[str],
        include_agentic: bool,
    ) -> dict[str, object] | None:
        stage_labels = {
            "draft_search": "Finding existing anchors for the query.",
            "draft_neighborhood": "Loading the nearby neighborhood from the committed graph.",
            "research": "Collecting optional external research context.",
            "llm": "Asking the LLM for proposed nodes and edges.",
            "llm_error": "LLM expansion failed. Keeping the local draft only.",
            "agent_unavailable": "LLM runtime is unavailable. Keeping the local draft only.",
            "draft_done": "Draft graph is ready.",
        }
        status_box = st.status("Running graph builder", expanded=True)

        def _status_callback(stage: str, detail: str) -> None:
            message = str(detail or stage_labels.get(stage) or stage.replace("_", " ").title()).strip()
            if message:
                status_box.write(message)

        try:
            draft_payload = build_knowledge_graph_draft(
                query_text,
                selected_node_ids=selected_ids,
                include_agentic_expansion=include_agentic,
                snapshot=graph_snapshot,
                status_callback=_status_callback,
            )
        except Exception as exc:
            status_box.update(label="Graph builder failed", state="error", expanded=True)
            status_box.write(f"{type(exc).__name__}: {exc}")
            return None

        limitation_lines = [
            str(item).strip()
            for item in list(draft_payload.get("limitations") or [])
            if str(item).strip()
        ]
        if limitation_lines:
            status_box.write("Limits: " + " | ".join(limitation_lines[:3]))
        status_box.update(
            label=f"Draft ready: {len(list(draft_payload.get('nodes') or []))} nodes, {len(list(draft_payload.get('edges') or []))} edges",
            state="complete",
            expanded=False,
        )
        return draft_payload

    snapshot = load_knowledge_graph_snapshot()
    runtime_status = knowledge_graph_runtime_status()

    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title(HOME_EXP_SECTION)
        st.caption("Open knowledge graph PoC. This is separate from the homepage attention graph.")
    with header_cols[1]:
        if st.button("Reset Draft", key="experiment_reset_draft", use_container_width=True):
            _clear_experiment_graph_state(keep_query=False)
            st.rerun()

    feedback = st.session_state.get("_knowledge_graph_commit_feedback")
    if isinstance(feedback, dict) and str(feedback.get("message") or "").strip():
        if bool(feedback.get("ok")):
            st.success(str(feedback.get("message")))
        else:
            st.error(str(feedback.get("message")))

    status_cols = st.columns(5)
    status_cols[0].metric("Nodes", f"{len(list(snapshot.get('nodes') or []))}")
    status_cols[1].metric("Edges", f"{len(list(snapshot.get('edges') or []))}")
    status_cols[2].metric(
        "Store",
        "Ready" if runtime_status.get("store_ready") else ("Configured" if runtime_status.get("store_configured") else "Preview only"),
    )
    status_cols[3].metric("LLM", "Ready" if runtime_status.get("llm_ready") else "Off")
    status_cols[4].metric(
        "Retrieval",
        "Embeddings + Web"
        if runtime_status.get("embedding_ready") and runtime_status.get("web_search_ready")
        else ("Embeddings" if runtime_status.get("embedding_ready") else ("Web" if runtime_status.get("web_search_ready") else "Local only")),
    )

    st.caption(
        "Flow: extract entities -> choose anchors -> visualize the draft -> add, update, or remove nodes and edges -> commit the reviewed delta."
    )

    query = st.text_input(
        "Seed query",
        key="knowledge_graph_query",
        placeholder="helium, HBM, uranium, ASML, fertilizer",
    )
    normalized_query = str(query or "").strip()
    last_input_query = str(st.session_state.get("_knowledge_graph_last_input_query") or "").strip()
    if normalized_query != last_input_query:
        st.session_state["_knowledge_graph_last_input_query"] = normalized_query
        st.session_state.pop("_knowledge_graph_commit_feedback", None)
        _clear_experiment_graph_state(keep_query=True)

    control_cols = st.columns([1.4, 1.4, 2.2])
    include_agentic_expansion = control_cols[0].toggle(
        "Use agentic expansion",
        value=True,
        key="knowledge_graph_include_agentic",
    )
    resolve_clicked = control_cols[1].button(
        "Find Existing Anchors",
        key="knowledge_graph_resolve_query",
        use_container_width=True,
        disabled=not normalized_query,
    )
    generate_clicked = control_cols[2].button(
        "Build Editable Graph",
        key="knowledge_graph_generate_draft",
        use_container_width=True,
        type="primary",
        disabled=not normalized_query,
    )
    st.caption(
        "Find Existing Anchors extracts entities, links them to the current graph, and falls back to graph search. Build Editable Graph creates a reviewable add/remove draft from those anchors."
    )

    if not normalized_query:
        total_nodes = len(list(snapshot.get("nodes") or []))
        total_edges = len(list(snapshot.get("edges") or []))
        overview = build_knowledge_graph_overview(
            snapshot=snapshot,
            max_nodes=max(total_nodes, 1),
            max_edges=max(total_edges, 0),
        )
        overview_meta = dict(overview.get("overview") or {})
        st.subheader("Graph Overview")
        st.caption(
            f"Showing {overview_meta.get('shown_nodes', len(list(overview.get('nodes') or [])))} of "
            f"{overview_meta.get('total_nodes', total_nodes)} nodes and "
            f"{overview_meta.get('shown_edges', len(list(overview.get('edges') or [])))} of "
            f"{overview_meta.get('total_edges', total_edges)} edges. Arrows show stored source -> target direction; thicker edges have higher severity and more opaque edges have higher confidence."
        )
        st.plotly_chart(plot_knowledge_graph_draft(overview), use_container_width=True)
        overview_nodes = draft_nodes_frame(overview)
        if not overview_nodes.empty:
            st.dataframe(
                overview_nodes[["node_id", "label", "node_type", "source_status", "reason"]],
                use_container_width=True,
                hide_index=True,
            )
        overview_edges = draft_edges_frame(overview)
        if not overview_edges.empty:
            st.dataframe(
                overview_edges[
                    [
                        "edge_id",
                        "source",
                        "target",
                        "relationship",
                        "polarity",
                        "directness",
                        "severity",
                        "confidence",
                        "source_status",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
        try:
            proposal_frame, proposal_metadata = load_latest_dataset_frame("knowledge_graph_update_proposals")
        except Exception:
            proposal_frame, proposal_metadata = pd.DataFrame(), None
        if isinstance(proposal_frame, pd.DataFrame) and not proposal_frame.empty:
            st.subheader("Latest Attention KG Proposals")
            st.caption(
                f"{len(proposal_frame)} proposal row(s) from "
                f"{getattr(proposal_metadata, 'dataset_version_id', '') or 'the latest Attention run'}. "
                "Reviewing them here does not update the core graph until you commit."
            )
            st.dataframe(
                proposal_frame[
                    [
                        column
                        for column in [
                            "proposal_type",
                            "operation",
                            "node_id",
                            "source_node_id",
                            "target_node_id",
                            "relationship",
                            "severity",
                            "confidence",
                            "rationale",
                        ]
                        if column in proposal_frame.columns
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
            if st.button("Load Latest Attention Proposals", key="knowledge_graph_load_attention_proposals"):
                proposal_draft = build_knowledge_graph_draft_from_proposals(proposal_frame, snapshot=snapshot)
                st.session_state["_knowledge_graph_draft"] = proposal_draft
                st.session_state["_knowledge_graph_draft_query"] = normalized_query
                st.session_state["knowledge_graph_selected_node_ids"] = list(proposal_draft.get("selected_node_ids") or [])
                st.session_state["_knowledge_graph_draft_key"] = hashlib.sha1(
                    json.dumps(proposal_draft, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:12]
                st.rerun()

    if resolve_clicked and normalized_query:
        mentions = _run_knowledge_graph_entity_extraction(normalized_query, snapshot)
        entity_matches = _knowledge_graph_matches_from_entities(mentions, snapshot)
        search_matches = _run_knowledge_graph_search(normalized_query, snapshot)
        matches = _merge_knowledge_graph_matches(entity_matches, search_matches)
        st.session_state["_knowledge_graph_matches"] = matches
        st.session_state["_knowledge_graph_resolved_query"] = normalized_query
        st.session_state["_knowledge_graph_entity_mentions"] = _knowledge_graph_entity_rows(mentions)
        st.session_state["_knowledge_graph_entity_query"] = normalized_query
        st.session_state["_knowledge_graph_add_candidates"] = graph_add_node_candidates(mentions)
        st.session_state["knowledge_graph_selected_node_ids"] = _default_selected_knowledge_graph_nodes(matches, mentions)
        st.session_state.pop("_knowledge_graph_draft", None)
        st.session_state.pop("_knowledge_graph_draft_query", None)
        st.session_state.pop("_knowledge_graph_draft_key", None)

    resolved_query = str(st.session_state.get("_knowledge_graph_resolved_query") or "").strip()
    matches = list(st.session_state.get("_knowledge_graph_matches") or []) if resolved_query == normalized_query else []
    entity_query = str(st.session_state.get("_knowledge_graph_entity_query") or "").strip()
    entity_rows = (
        list(st.session_state.get("_knowledge_graph_entity_mentions") or [])
        if entity_query == normalized_query
        else []
    )
    add_candidates = (
        list(st.session_state.get("_knowledge_graph_add_candidates") or [])
        if entity_query == normalized_query
        else []
    )

    if entity_rows:
        st.subheader("Extracted Entities")
        st.dataframe(
            pd.DataFrame(entity_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "confidence": st.column_config.NumberColumn("confidence", format="%.2f"),
            },
        )
        if add_candidates:
            st.caption("Unlinked high-confidence entities will be added to the draft as proposed new node rows.")
            st.dataframe(
                pd.DataFrame(add_candidates),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "confidence": st.column_config.NumberColumn("confidence", format="%.2f"),
                },
            )

    if matches:
        st.subheader("Existing Anchors")
        match_frame = pd.DataFrame(
            [
                {
                    "node_id": str(item.get("node_id") or ""),
                    "label": str(item.get("canonical_label") or ""),
                    "type": str(item.get("node_type") or ""),
                    "match_source": str(item.get("match_source") or ""),
                    "matched_on": str(item.get("matched_alias") or ""),
                    "score": float(item.get("score") or 0.0),
                }
                for item in matches
            ]
        )
        st.dataframe(
            match_frame,
            use_container_width=True,
            hide_index=True,
            column_config={
                "score": st.column_config.NumberColumn("score", format="%.2f"),
            },
        )
        option_lookup = {
            str(item.get("node_id") or ""): f"{str(item.get('canonical_label') or item.get('node_id') or '')} ({str(item.get('node_id') or '')})"
            for item in matches
            if str(item.get("node_id") or "").strip()
        }
        valid_selected = [
            node_id
            for node_id in list(st.session_state.get("knowledge_graph_selected_node_ids") or [])
            if node_id in option_lookup
        ]
        st.session_state["knowledge_graph_selected_node_ids"] = valid_selected
        st.multiselect(
            "Seed nodes",
            options=list(option_lookup.keys()),
            key="knowledge_graph_selected_node_ids",
            format_func=lambda node_id: option_lookup.get(node_id, node_id),
            help="These seed nodes anchor the local neighborhood before agent suggestions are added.",
        )
    elif normalized_query and resolved_query == normalized_query:
        st.info("No confident existing anchors were found. You can still run the graph builder directly from the raw query.")

    if generate_clicked and normalized_query:
        mentions: list[object] = []
        if entity_query == normalized_query and entity_rows:
            mentions = list(entity_rows)
        else:
            mentions = _run_knowledge_graph_entity_extraction(normalized_query, snapshot)
            entity_matches = _knowledge_graph_matches_from_entities(mentions, snapshot)
            search_matches = _run_knowledge_graph_search(normalized_query, snapshot)
            matches = _merge_knowledge_graph_matches(entity_matches, search_matches)
            st.session_state["_knowledge_graph_matches"] = matches
            st.session_state["_knowledge_graph_resolved_query"] = normalized_query
            st.session_state["_knowledge_graph_entity_mentions"] = _knowledge_graph_entity_rows(mentions)
            st.session_state["_knowledge_graph_entity_query"] = normalized_query
            st.session_state["_knowledge_graph_add_candidates"] = graph_add_node_candidates(mentions)
            if not list(st.session_state.get("knowledge_graph_selected_node_ids") or []):
                st.session_state["knowledge_graph_selected_node_ids"] = _default_selected_knowledge_graph_nodes(matches, mentions)
        add_candidates = (
            list(st.session_state.get("_knowledge_graph_add_candidates") or [])
            if str(st.session_state.get("_knowledge_graph_entity_query") or "").strip() == normalized_query
            else []
        )
        draft = _run_knowledge_graph_builder(
            normalized_query,
            graph_snapshot=snapshot,
            selected_ids=list(st.session_state.get("knowledge_graph_selected_node_ids") or []),
            include_agentic=bool(include_agentic_expansion),
        )
        if isinstance(draft, dict):
            draft = _inject_entity_add_candidates(draft, add_candidates, snapshot)
            st.session_state["_knowledge_graph_matches"] = _merge_knowledge_graph_matches(
                _knowledge_graph_matches_from_entities(mentions, snapshot),
                list(draft.get("seed_matches") or []),
            )
            st.session_state["_knowledge_graph_resolved_query"] = normalized_query
            if not list(st.session_state.get("knowledge_graph_selected_node_ids") or []):
                st.session_state["knowledge_graph_selected_node_ids"] = _default_selected_knowledge_graph_nodes(
                    list(draft.get("seed_matches") or []),
                    mentions,
                )
            st.session_state["_knowledge_graph_draft"] = draft
            st.session_state["_knowledge_graph_draft_query"] = normalized_query
            st.session_state["_knowledge_graph_draft_key"] = hashlib.sha1(
                json.dumps(draft, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:12]

    draft = st.session_state.get("_knowledge_graph_draft")
    draft_query = str(st.session_state.get("_knowledge_graph_draft_query") or "").strip()
    if isinstance(draft, dict) and draft_query == normalized_query:
        st.subheader("Editable Graph Draft")
        if str(draft.get("agentic_summary") or "").strip():
            st.caption(str(draft.get("agentic_summary") or ""))
        for limitation in list(draft.get("limitations") or []):
            if str(limitation or "").strip():
                st.warning(str(limitation))

        st.plotly_chart(plot_knowledge_graph_draft(draft), use_container_width=True)
        st.caption("If you change a node id, update any edges that reference it before commit.")

        draft_key = str(st.session_state.get("_knowledge_graph_draft_key") or "draft")
        nodes_editor = st.data_editor(
            draft_nodes_frame(draft),
            key=f"knowledge_graph_nodes_editor_{draft_key}",
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "keep": st.column_config.CheckboxColumn("keep"),
                "confidence": st.column_config.NumberColumn("confidence", format="%.2f"),
            },
            disabled=["source_status"],
        )
        edges_editor = st.data_editor(
            draft_edges_frame(draft),
            key=f"knowledge_graph_edges_editor_{draft_key}",
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "keep": st.column_config.CheckboxColumn("keep"),
                "severity": st.column_config.NumberColumn("severity", format="%.2f"),
                "confidence": st.column_config.NumberColumn("confidence", format="%.2f"),
            },
            disabled=["source_status"],
        )
        commit_summary = st.text_area(
            "Commit summary",
            key=f"knowledge_graph_commit_summary_{draft_key}",
            placeholder="Describe what you approved, removed, or added in this draft.",
        )
        if not runtime_status.get("store_ready"):
            st.info("Commit is disabled because the Postgres knowledge-graph store is not ready in this environment.")
        if st.button(
            "Commit to Core Knowledge Graph",
            key=f"knowledge_graph_commit_button_{draft_key}",
            type="primary",
            disabled=not bool(runtime_status.get("store_ready")),
            use_container_width=True,
        ):
            result = commit_knowledge_graph_review(
                query=normalized_query,
                draft=draft,
                nodes_frame=nodes_editor,
                edges_frame=edges_editor,
                summary=commit_summary,
                created_by=_knowledge_graph_commit_actor(),
            )
            st.session_state["_knowledge_graph_commit_feedback"] = result
            if bool(result.get("ok")):
                _clear_experiment_graph_state(keep_query=True)
                st.rerun()
            st.error(str(result.get("message") or "Commit failed."))

    st.subheader("Recent Commits")
    recent_commits = list_recent_knowledge_graph_commits(limit=10)
    if recent_commits.empty:
        st.caption("No committed graph reviews yet.")
    else:
        if "created_at" in recent_commits.columns:
            recent_commits["created_at"] = pd.to_datetime(recent_commits["created_at"], utc=True, errors="coerce")
        st.dataframe(recent_commits, use_container_width=True, hide_index=True)


def _render_homepage_exp(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    _render_experiment_placeholder_page(
        cfg,
        force_data_refresh=force_data_refresh,
    )


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


def _render_help_popover(title: str, body: str, label: str = "How to read") -> None:
    with st.popover(label, help=title, use_container_width=True):
        st.markdown(body)


def _render_page_agentic_summary_panel(
    surface: str,
    context: dict[str, object],
    *,
    key_prefix: str,
) -> dict[str, object]:
    safe_context = _json_ready(context if isinstance(context, dict) else {})
    context_signature = page_summary_context_signature(safe_context)
    ticker = str(safe_context.get("ticker") or "").upper().strip()
    summary_key = f"{key_prefix}_agentic_summary"
    signature_key = f"{key_prefix}_agentic_summary_signature"
    summary = _load_page_agentic_summary_cached(str(surface or ""), context_signature, ticker)
    st.session_state[summary_key] = summary
    st.session_state[signature_key] = context_signature
    with st.container(border=True):
        st.subheader("Agentic Summary")
        status = str(summary.get("status") or "").strip()
        headline = str(summary.get("headline") or "").strip()
        if headline:
            st.markdown(f"**{headline}**")
        if status in {"ok", "fallback"} and str(summary.get("summary_markdown") or "").strip():
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
            if status == "fallback":
                st.caption("Using materialized page facts because the scheduled AQL summary was unavailable.")
        else:
            error = str(summary.get("error") or "").strip()
            if error:
                st.info(error)
            elif ticker and str(surface or "").strip() == STOCK_INVESTIGATOR_SECTION:
                st.info(
                    f"No precomputed Agentic Summary matched {ticker}. "
                    "The attention job precomputes Stock Investigator summaries for a configured candidate set, "
                    "not every possible ticker."
                )
            else:
                st.info("No materialized summary is available for this view yet.")
    return summary


def _load_symbol_name_map(
    cfg: AppConfig,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, str]:
    normalized_symbols = sorted({str(value).upper().strip() for value in symbols if str(value).strip()})
    out: dict[str, str] = {}
    if not normalized_symbols:
        return out
    try:
        universe_frame, _ = load_latest_dataset_frame("universe_snapshot")
    except Exception:
        universe_frame = pd.DataFrame()
    if isinstance(universe_frame, pd.DataFrame) and not universe_frame.empty and "symbol" in universe_frame.columns:
        name_column = next(
            (
                column
                for column in ["security_name", "company_name", "name"]
                if column in universe_frame.columns
            ),
            "",
        )
        if name_column:
            names = universe_frame[["symbol", name_column]].copy()
            names["symbol"] = names["symbol"].astype(str).str.upper().str.strip()
            names[name_column] = names[name_column].astype(str).str.strip()
            names = names[names["symbol"].isin(normalized_symbols) & names[name_column].ne("")]
            out.update(dict(names.drop_duplicates(subset=["symbol"], keep="first").itertuples(index=False, name=None)))

    missing_symbols = [symbol for symbol in normalized_symbols if symbol not in out]
    if len(missing_symbols) > 12 and not force_refresh:
        return out
    for symbol in missing_symbols:
        try:
            asset = _load_asset_metadata_cached(cfg, symbol, force_refresh=force_refresh)
        except Exception:
            asset = {}
        name = str(asset.get("name") or "").strip()
        if name:
            out[symbol] = name
    return out


def _prepare_momentum_table(
    df: pd.DataFrame,
    *,
    name_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    table = df.copy()
    if "symbol" in table.columns:
        table["company_name"] = [
            str((name_map or {}).get(str(symbol).upper().strip()) or "")
            for symbol in table["symbol"]
        ]
    if "sparkline_3m" in table.columns:
        table["sparkline_3m"] = [
            list(value) if isinstance(value, (list, tuple, np.ndarray)) else []
            for value in table["sparkline_3m"]
        ]

    column_config: dict[str, object] = {
        "symbol": st.column_config.TextColumn("Ticker"),
    }
    if "company_name" in table.columns:
        column_config["company_name"] = st.column_config.TextColumn(
            "Company",
            help="Company name is shown directly because the current selectable table does not keep row hover tooltips reliable across refreshes.",
            width="medium",
        )
    if "sparkline_3m" in table.columns:
        column_config["sparkline_3m"] = st.column_config.LineChartColumn(
            "Mini Chart",
            help="Normalized 3-month price path.",
            y_min=80,
            y_max=140,
            width="medium",
        )
    return table, column_config


def _build_rank_timeseries_figure(
    history: pd.DataFrame,
    rank_df: pd.DataFrame,
    title: str,
    *,
    days: int,
    value_col: str = "asset_norm",
) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template="plotly_dark", title=title, xaxis_title="Date", yaxis_title="Normalized Price", hovermode="x unified")
    if history.empty or rank_df.empty or value_col not in history.columns:
        return fig

    symbols = [str(symbol).upper().strip() for symbol in rank_df.get("symbol", pd.Series(dtype=str)).tolist() if str(symbol).strip()]
    if not symbols:
        return fig

    frame = history[history["symbol"].astype(str).isin(symbols)].copy()
    if frame.empty:
        return fig

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp", value_col]).sort_values("timestamp")
    if frame.empty:
        return fig

    cutoff = frame["timestamp"].max() - pd.Timedelta(days=max(int(days), 30))
    frame = frame[frame["timestamp"] >= cutoff].copy()
    if frame.empty:
        return fig

    label_map: dict[str, str] = {}
    for row in rank_df.itertuples(index=False):
        symbol = str(getattr(row, "symbol", "")).upper().strip()
        name = str(getattr(row, "commodity_label", "") or getattr(row, "name", "") or symbol).strip()
        label_map[symbol] = f"{name} ({symbol})" if name and name != symbol else symbol

    for symbol in symbols:
        symbol_frame = frame[frame["symbol"].astype(str) == symbol].copy()
        if symbol_frame.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=symbol_frame["timestamp"],
                y=pd.to_numeric(symbol_frame[value_col], errors="coerce"),
                mode="lines",
                name=label_map.get(symbol, symbol),
            )
        )

    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    return fig


def _sync_stock_investigator_ticker_from_widget() -> None:
    ticker = str(st.session_state.get("stock_investigator_ticker_widget") or "").upper().strip()
    if ticker:
        _set_workspace_ticker(ticker)


def _render_stock_investigator_workspace(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    default_ticker = (
        str(st.session_state.get("stock_investigator_ticker") or "").upper().strip()
        or str(st.session_state.get("market_selected_ticker") or "").upper().strip()
        or "AAPL"
    )
    if not str(st.session_state.get("stock_investigator_ticker") or "").strip():
        _set_workspace_ticker(default_ticker)
    if not str(st.session_state.get("stock_investigator_ticker_widget") or "").strip():
        st.session_state["stock_investigator_ticker_widget"] = default_ticker

    control_cols = st.columns([2.4, 2.2, 1.4])
    with control_cols[0]:
        ticker = st.text_input(
            "Ticker",
            key="stock_investigator_ticker_widget",
            placeholder="AAPL",
            on_change=_sync_stock_investigator_ticker_from_widget,
        ).upper().strip()
    with control_cols[1]:
        days = st.slider("Days", 60, 720, 365, step=30, key="stock_investigator_days")
    with control_cols[2]:
        if st.button(
            f"Open {MARKET_EXPLORER_SECTION}",
            key="stock_investigator_open_market",
            use_container_width=True,
            disabled=not bool(ticker),
        ):
            _open_attention_target(
                MARKET_EXPLORER_SECTION,
                {
                    "ticker": ticker,
                    "market_view": "Markets",
                    "business_filter": _market_business_filter_for_symbol(ticker),
                },
            )

    ticker = str(ticker or "").upper().strip()
    if not ticker:
        st.info("Enter a ticker to load technicals, company context, and fundamentals.")
        return

    if ticker != str(st.session_state.get("stock_investigator_ticker") or "").upper().strip():
        _set_workspace_ticker(ticker)

    taxonomy_summary = _taxonomy_summary_text(ticker)
    if taxonomy_summary:
        st.caption(taxonomy_summary)
    stock_agentic_summary_slot = st.empty()

    analysis_days = max(days, 3650)
    try:
        with st.spinner("Loading price history..."):
            with _timed("load_price_history", ticker=ticker, days=analysis_days):
                price = _load_price_history_cached(
                    cfg,
                    ticker,
                    analysis_days,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("load_price_history_failed", ticker=ticker, error=str(exc)[:200])
        st.warning(f"Could not load price history: {exc}")
        price = pd.DataFrame()

    st.subheader(f"{ticker} Technicals")
    signal_summary: dict[str, object] = {}
    forecast: dict[str, object] = {}
    if not price.empty:
        try:
            recent_cutoff = price["timestamp"].max() - pd.Timedelta(days=days)
            visible_price = price[price["timestamp"] >= recent_cutoff].copy()
            if visible_price.empty:
                visible_price = price.tail(min(len(price), 220)).copy()
            st.plotly_chart(build_technical_figure(visible_price, f"Technical View - {ticker}"), use_container_width=True)
        except Exception as exc:
            _log_event("stock_investigator_technical_overview_failed", ticker=ticker, error=str(exc)[:200])
            st.caption("Technical overview chart unavailable for this ticker.")

        with st.expander("Show Recent Price Data", expanded=False):
            st.dataframe(price.tail(40), use_container_width=True, hide_index=True)

        if _SIGNALS_IMPORT_ERROR:
            st.warning(
                "Advanced signal charts are temporarily unavailable because the optional signals module "
                f"did not load: {_SIGNALS_IMPORT_ERROR}"
            )

        signal_frame = _load_technical_signal_history_cached(
            cfg,
            ticker,
            days=max(days, 180),
            force_refresh=force_data_refresh,
        )
        if signal_frame.empty:
            if not _SIGNALS_IMPORT_ERROR:
                st.info("Not enough valid price history to compute signals.")
        else:
            cutoff = signal_frame["timestamp"].max() - pd.Timedelta(days=days)
            visible_signal_frame = signal_frame[signal_frame["timestamp"] >= cutoff].copy()
            if visible_signal_frame.empty:
                visible_signal_frame = signal_frame.tail(min(len(signal_frame), 120)).copy()

            signal_summary = _load_technical_signal_summary_cached(
                cfg,
                ticker,
                signal_frame,
                force_refresh=force_data_refresh,
            )
            forecast = _load_forecast_next_week_cached(
                cfg,
                ticker,
                days=max(days, 180),
                signal_frame=signal_frame,
                force_refresh=force_data_refresh,
            )

            metric_cols = st.columns(6)
            with metric_cols[0]:
                st.metric("Pullback vs ATH", f"{signal_summary.get('pullback_from_ath_pct', np.nan):.1f}%")
            with metric_cols[1]:
                st.metric("Channel Position", f"{signal_summary.get('channel_position', np.nan) * 100:.0f}%")
            with metric_cols[2]:
                st.metric("Support Buffer", f"{signal_summary.get('dist_to_support_pct', np.nan):.1f}%")
            with metric_cols[3]:
                st.metric("Room to Resistance", f"{signal_summary.get('dist_to_resistance_pct', np.nan):.1f}%")
            with metric_cols[4]:
                up_probability = forecast.get("up_probability", np.nan) if forecast else np.nan
                st.metric("1W Up Probability", f"{up_probability * 100:.0f}%")
            with metric_cols[5]:
                breakout_probability = forecast.get("breakout_probability", np.nan) if forecast else np.nan
                st.metric("1W Breakout Prob", f"{breakout_probability * 100:.0f}%")

            if signal_summary:
                st.caption(
                    f"Signal regime: {signal_summary.get('regime', 'n/a')} | "
                    f"RSI 14: {signal_summary.get('rsi_14', np.nan):.1f} | "
                    f"20D realized vol: {signal_summary.get('vol_20_ann_pct', np.nan):.1f}% | "
                    "ATH and channel signals are computed from up to 10 years of daily bars."
                )

            channel_left, channel_right = st.columns(2)
            with channel_left:
                st.plotly_chart(build_price_channel_figure(visible_signal_frame, ticker), use_container_width=True)
            with channel_right:
                st.plotly_chart(build_pullback_figure(visible_signal_frame, ticker), use_container_width=True)

            forecast_left, forecast_right = st.columns(2)
            with forecast_left:
                if forecast:
                    st.plotly_chart(
                        build_forecast_cone_figure(visible_signal_frame, forecast, ticker),
                        use_container_width=True,
                    )
                else:
                    st.info("Not enough history to build the next-week probability model.")
            with forecast_right:
                if forecast:
                    st.plotly_chart(
                        build_terminal_distribution_figure(forecast, ticker),
                        use_container_width=True,
                    )
                    st.caption(
                        f"Analog model: {forecast.get('analog_count', 0)} similar historical setups, "
                        f"expected 5-day return {forecast.get('expected_return_pct', np.nan):.2f}%."
                    )
                else:
                    st.info("Forecast distribution unavailable for this ticker.")
    else:
        st.info("No price history was available for this ticker.")

    try:
        with st.spinner("Loading company profile and recent news..."):
            with _timed("load_market_detail_context", ticker=ticker):
                news_payload = _load_recent_news_cached(
                    cfg,
                    ticker,
                    days=14,
                    limit=6,
                    force_refresh=force_data_refresh,
                )
                attention_context = _load_attention_context_cached(
                    cfg,
                    ticker,
                    force_refresh=force_data_refresh,
                )
                background_payload = _load_attention_ticker_background_cached(
                    cfg,
                    ticker,
                    force_refresh=force_data_refresh,
                )
    except Exception as exc:
        _log_event("load_market_detail_context_failed", ticker=ticker, error=str(exc)[:200])
        st.warning(f"Could not load company context for {ticker}: {exc}")
        news_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
        attention_context = {
            "context_story_text": "",
            "llm_headline": "",
            "llm_summary_text": "",
        }
        background_payload = {}

    st.subheader(f"{ticker} Company Context")
    news_summary = summarize_recent_news(ticker, news_payload)
    summary_lines = [
        str(item).strip()
        for item in list(news_summary.get("summary_lines") or [])
        if str(item).strip()
    ]
    llm_headline = str(attention_context.get("llm_headline") or "").strip()
    llm_summary_text = str(attention_context.get("llm_summary_text") or "").strip()
    primary_context_text = str(attention_context.get("context_story_text") or "").strip()
    description_text = str(background_payload.get("description_text") or "").strip()
    company_background_text = str(background_payload.get("company_background_text") or "").strip()
    background_summary = _first_substantive_company_context_line(
        [
            llm_summary_text,
            primary_context_text,
            company_background_text,
            str(background_payload.get("llm_summary_text") or "").strip(),
            description_text,
        ]
    )
    what_happened_summary = _first_substantive_company_context_line(
        [
            llm_headline,
            *summary_lines,
            description_text,
        ]
    )
    evidence_links = _collect_evidence_links(
        recent_headlines=list(background_payload.get("recent_headlines") or []),
        articles=news_summary.get("articles", pd.DataFrame()),
        limit=8,
    )
    with stock_agentic_summary_slot.container():
        _render_page_agentic_summary_panel(
            STOCK_INVESTIGATOR_SECTION,
            stock_summary_context(
                ticker=ticker,
                taxonomy_summary=taxonomy_summary,
                signal_summary=signal_summary,
                forecast=forecast,
                news_summary=news_summary,
                attention_context=attention_context,
                background_payload=background_payload,
            ),
            key_prefix="stock_investigator",
        )

    _render_compact_background_sections(
        ticker,
        background_summary=background_summary,
        what_happened_summary=what_happened_summary,
        evidence_links=evidence_links,
    )

    st.subheader(f"{ticker} Fundamentals")
    _render_overview_fundamentals(
        cfg,
        ticker,
        force_data_refresh=force_data_refresh,
        asof_time_utc=background_payload.get("asof_time_utc"),
    )


def _render_market_opportunity_experiments(
    cfg: AppConfig,
    force_data_refresh: bool,
    advanced_view: str,
    lens_label: str,
    lens_name: str,
    lens_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.subheader("Advanced")
        st.caption("Less-standard scanners for regime shifts, structural leadership, and commodity transmission.")
        st.caption(f"{lens_label}: {lens_name} | {len(lens_symbols)} names")
    with heading_cols[1]:
        _render_help_popover(
            "How to read advanced views",
            """
Use this area when you want signals that are a little more structural than simple movers or momentum tables.

- `Broad Markets` looks for names breaking away from or re-linking with a market benchmark.
- `Commodity Section` is commodity-first and answers what is moving, in what direction, and how moves can transmit across the chain.
            """,
        )

    if advanced_view == "Commodity Section":
        _render_commodity_experiment(cfg, force_data_refresh, lens_name, lens_symbols)
        return

    _render_phase_shift_experiment(cfg, force_data_refresh, lens_name, lens_symbols)


def _render_phase_shift_experiment(
    cfg: AppConfig,
    force_data_refresh: bool,
    business_filter: str,
    business_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.markdown("##### Broad Markets")
        st.caption(
            "Correlation phase-shift analyzer: searches for changes in rolling market correlation versus multi-horizon compounding "
            "momentum to surface decoupling leaders, beta-linked breakouts, and crowded unwinds."
        )
        st.caption(f"Business lens: {business_filter}")
    with heading_cols[1]:
        _render_help_popover(
            "How to read this experiment",
            """
**Plain-English version**

- `Correlation` means how tightly a stock is moving with the benchmark, like `SPY`.
- `RoC` means `rate of change`, which is just how fast a signal is changing.
- `Compounding momentum` means returns are stacking across short and medium windows, not just drifting up a little.

**What this experiment is trying to find**

- `Decoupling leaders`: strong momentum while correlation to the market is falling.
- `Beta-linked breakouts`: strong momentum while correlation to the market is still rising or staying high.
- `Crowded unwinds / washouts`: weak momentum with unstable or rising correlation stress.
            """,
        )

    control_cols = st.columns(5)
    with control_cols[0]:
        benchmark = st.selectbox(
            "Benchmark",
            ["SPY", "QQQ", "DIA", "IWM", "XLK", "XLF", "XLE", "TLT"],
            index=0,
            key="market_experiment_benchmark",
        )
    with control_cols[1]:
        experiment_days = st.slider("History (days)", 126, 504, 252, step=21, key="market_experiment_days")
    with control_cols[2]:
        corr_window = st.slider("Corr Window", 10, 60, 20, step=5, key="market_experiment_corr_window")
    with control_cols[3]:
        roc_window = st.slider("Corr RoC", 5, 30, 10, step=1, key="market_experiment_roc_window")
    with control_cols[4]:
        momentum_window = st.slider("Comp Momentum", 21, 126, 63, step=21, key="market_experiment_momentum_window")

    try:
        with st.spinner("Scanning correlation phase shifts..."):
            with _timed(
                "scan_correlation_phase_shifts",
                benchmark=benchmark,
                days=experiment_days,
                corr_window=corr_window,
                roc_window=roc_window,
                momentum_window=momentum_window,
            ):
                experiment_data = _load_correlation_phase_shift_cached(
                    cfg,
                    benchmark=benchmark,
                    days=experiment_days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                    symbols=business_symbols,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("scan_correlation_phase_shifts_failed", benchmark=benchmark, error=str(exc)[:200])
        st.error(f"Could not run the phase-shift analyzer: {exc}")
        return

    summary = experiment_data.get("summary", pd.DataFrame())
    history = experiment_data.get("history", pd.DataFrame())
    if summary.empty or history.empty:
        st.info("Not enough market history was returned to run the experiment.")
        return

    selected_experiment_ticker: str | None = None
    decoupling = summary.nlargest(10, "decoupling_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "decoupling_score",
            "phase_regime",
        ]
    ]
    beta_breakouts = summary.nlargest(10, "beta_breakout_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "beta_breakout_score",
            "phase_regime",
        ]
    ]
    correlation_breaks = summary.nlargest(10, "correlation_break_score")[
        [
            "symbol",
            "close",
            "correlation_now",
            "correlation_roc",
            "compounding_momentum_pct",
            "momentum_roc_pct",
            "correlation_break_score",
            "phase_regime",
        ]
    ]

    table_left, table_right = st.columns(2)
    with table_left:
        selected_experiment_ticker = _render_selectable_ticker_table(
            "Decoupling Leaders",
            decoupling,
            ["symbol", "correlation_now", "correlation_roc", "compounding_momentum_pct", "decoupling_score", "phase_regime"],
            key="market_experiment_decoupling",
        ) or selected_experiment_ticker
    with table_right:
        selected_experiment_ticker = _render_selectable_ticker_table(
            "Beta-Linked Breakouts",
            beta_breakouts,
            ["symbol", "correlation_now", "correlation_roc", "compounding_momentum_pct", "beta_breakout_score", "phase_regime"],
            key="market_experiment_beta_breakouts",
        ) or selected_experiment_ticker

    selected_experiment_ticker = _render_selectable_ticker_table(
        "Correlation Break Alerts",
        correlation_breaks,
        ["symbol", "correlation_now", "correlation_roc", "momentum_roc_pct", "correlation_break_score", "phase_regime"],
        key="market_experiment_correlation_breaks",
    ) or selected_experiment_ticker

    scatter_frame = summary.copy()
    scatter_frame["corr_break_abs"] = pd.to_numeric(scatter_frame["correlation_break_score"], errors="coerce").abs()
    scatter_frame, phase_size_col = _prepare_scatter_size(scatter_frame, "corr_break_abs")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Phase Shift Map",
                """
This is the quickest chart to read.

- `X axis`: how fast correlation is changing.
- `Y axis`: how strong compounded momentum is.
- upper-left: strong momentum, but becoming less tied to the benchmark
- upper-right: strong momentum, still moving with the benchmark
- lower half: weak or fading momentum

The further a ticker is from the center, the more unusual the regime shift is.
                """,
            )
        fig_phase = px.scatter(
            scatter_frame,
            x="correlation_roc",
            y="compounding_momentum_pct",
            color="phase_regime",
            size=phase_size_col,
            hover_name="symbol",
            hover_data={
                "correlation_now": ":.2f",
                "momentum_roc_pct": ":.2f",
                "decoupling_score": ":.1f",
                "beta_breakout_score": ":.1f",
            },
            template="plotly_dark",
            title=f"Phase Shift Map vs {benchmark}",
            labels={
                "correlation_roc": "Correlation RoC",
                "compounding_momentum_pct": "Compounding Momentum %",
            },
        )
        fig_phase.add_hline(y=0, line_dash="dot", line_color="#666")
        fig_phase.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_phase, use_container_width=True)

    with chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Correlation Phase Surface",
                """
This is the 3D version of the phase map.

- `Current Corr`: how linked the stock is to the benchmark right now
- `Corr RoC`: whether that link is tightening or breaking
- `Comp Momentum %`: whether returns are reinforcing across time windows

Use this when you want to compare several names at once and see which ones are both strong and changing regime.
                """,
            )
        fig_phase_3d = px.scatter_3d(
            scatter_frame,
            x="correlation_now",
            y="correlation_roc",
            z="compounding_momentum_pct",
            color="decoupling_score",
            size=phase_size_col,
            hover_name="symbol",
            hover_data={
                "phase_regime": True,
                "momentum_roc_pct": ":.2f",
                "beta_breakout_score": ":.1f",
                "correlation_break_score": ":.1f",
            },
            template="plotly_dark",
            title=f"Correlation Phase Surface vs {benchmark}",
            labels={
                "correlation_now": "Current Corr",
                "correlation_roc": "Corr RoC",
                "compounding_momentum_pct": "Comp Momentum %",
                "decoupling_score": "Decoupling Score",
            },
        )
        fig_phase_3d.update_traces(marker=dict(opacity=0.8))
        st.plotly_chart(fig_phase_3d, use_container_width=True)

    experiment_symbol_options = sorted(summary["symbol"].astype(str).unique().tolist())
    exp_selected_key = "market_experiment_selected_ticker"
    exp_widget_key = "market_experiment_ticker_widget"
    fallback_experiment_ticker = st.session_state.get(exp_selected_key) or experiment_symbol_options[0]
    if fallback_experiment_ticker not in experiment_symbol_options:
        fallback_experiment_ticker = experiment_symbol_options[0]

    current_experiment_ticker = st.session_state.get(exp_selected_key)
    if current_experiment_ticker not in experiment_symbol_options:
        current_experiment_ticker = fallback_experiment_ticker

    if selected_experiment_ticker and selected_experiment_ticker in experiment_symbol_options:
        if selected_experiment_ticker != current_experiment_ticker:
            st.session_state[exp_selected_key] = selected_experiment_ticker
            st.session_state[exp_widget_key] = selected_experiment_ticker
            st.rerun()
    elif exp_selected_key not in st.session_state or st.session_state[exp_selected_key] not in experiment_symbol_options:
        st.session_state[exp_selected_key] = fallback_experiment_ticker

    if exp_widget_key not in st.session_state or st.session_state[exp_widget_key] not in experiment_symbol_options:
        st.session_state[exp_widget_key] = st.session_state[exp_selected_key]
    elif st.session_state[exp_widget_key] != st.session_state[exp_selected_key]:
        st.session_state[exp_widget_key] = st.session_state[exp_selected_key]

    experiment_ticker = st.selectbox(
        "Experiment Detail",
        experiment_symbol_options,
        key=exp_widget_key,
        on_change=lambda: st.session_state.__setitem__(exp_selected_key, st.session_state.get(exp_widget_key)),
    )
    st.session_state[exp_selected_key] = experiment_ticker

    detail_row = summary[summary["symbol"] == experiment_ticker].head(1)
    detail_history = history[history["symbol"] == experiment_ticker].copy()
    if detail_row.empty or detail_history.empty:
        st.info("No detail history available for the selected experiment ticker.")
        return

    detail = detail_row.iloc[0]
    metric_cols = st.columns(6)
    with metric_cols[0]:
        st.metric("Phase Regime", str(detail.get("phase_regime") or "n/a"))
    with metric_cols[1]:
        st.metric("Current Corr", f"{pd.to_numeric(detail.get('correlation_now'), errors='coerce'):.2f}")
    with metric_cols[2]:
        st.metric("Corr RoC", f"{pd.to_numeric(detail.get('correlation_roc'), errors='coerce'):.2f}")
    with metric_cols[3]:
        st.metric("Comp Momentum", f"{pd.to_numeric(detail.get('compounding_momentum_pct'), errors='coerce'):.1f}%")
    with metric_cols[4]:
        st.metric("Momentum RoC", f"{pd.to_numeric(detail.get('momentum_roc_pct'), errors='coerce'):.1f}%")
    with metric_cols[5]:
        st.metric("Decoupling Score", f"{pd.to_numeric(detail.get('decoupling_score'), errors='coerce'):.1f}")

    detail_history = detail_history.sort_values("timestamp").copy()
    visible_cutoff = detail_history["timestamp"].max() - pd.Timedelta(days=min(experiment_days, 180))
    visible_history = detail_history[detail_history["timestamp"] >= visible_cutoff].copy()
    if visible_history.empty:
        visible_history = detail_history.copy()

    detail_chart_left, detail_chart_right = st.columns(2)
    with detail_chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Relative Path",
                """
This compares the selected stock against the benchmark on the same starting scale.

- if the stock line is rising faster than the benchmark line, it is outperforming
- if the gap is widening, leadership is strengthening
- if the stock starts to outperform while correlation is falling, that usually points to a real phase shift instead of simple market beta
                """,
            )
        fig_relative = go.Figure()
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["asset_norm"],
                mode="lines",
                name=experiment_ticker,
            )
        )
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["benchmark_norm"],
                mode="lines",
                name=benchmark,
            )
        )
        fig_relative.update_layout(
            template="plotly_dark",
            title=f"{experiment_ticker} vs {benchmark} Relative Path",
            xaxis_title="Date",
            yaxis_title="Normalized Price",
            hovermode="x unified",
        )
        st.plotly_chart(fig_relative, use_container_width=True)

    with detail_chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Correlation Regime",
                """
This shows the market linkage directly.

- `Rolling Corr`: the current relationship to the benchmark
- `Corr RoC`: the speed and direction of the change in that relationship

If `Rolling Corr` is still high but `Corr RoC` is dropping, the stock may be starting to break away from the market.
                """,
            )
        fig_corr = go.Figure()
        fig_corr.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["rolling_correlation"],
                mode="lines",
                name="Rolling Corr",
            )
        )
        fig_corr.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["correlation_roc"],
                mode="lines",
                name="Corr RoC",
            )
        )
        fig_corr.update_layout(
            template="plotly_dark",
            title=f"{experiment_ticker} Correlation Regime vs {benchmark}",
            xaxis_title="Date",
            yaxis_title="Correlation",
            hovermode="x unified",
        )
        st.plotly_chart(fig_corr, use_container_width=True)

    momentum_help_cols = st.columns([10, 2])
    with momentum_help_cols[1]:
        _render_help_popover(
            "Compounding Momentum Phase",
            """
This is not just raw price momentum.

- `Comp Momentum %`: returns compounded across multiple windows
- `Momentum RoC %`: whether that compounded momentum is accelerating or fading

Easy interpretation:

- above zero and rising: trend is strengthening
- above zero but falling: still strong, but losing speed
- below zero: the move is weakening or reversing
            """,
        )
    fig_momentum = go.Figure()
    fig_momentum.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["compounding_momentum"] * 100.0,
            mode="lines",
            name="Comp Momentum %",
        )
    )
    fig_momentum.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["momentum_roc"] * 100.0,
            mode="lines",
            name="Momentum RoC %",
        )
    )
    fig_momentum.update_layout(
        template="plotly_dark",
        title=f"{experiment_ticker} Compounding Momentum Phase",
        xaxis_title="Date",
        yaxis_title="Percent",
        hovermode="x unified",
    )
    st.plotly_chart(fig_momentum, use_container_width=True)


def _render_commodity_experiment(
    cfg: AppConfig,
    force_data_refresh: bool,
    commodity_focus: str,
    commodity_symbols: list[str],
) -> None:
    heading_cols = st.columns([10, 2])
    with heading_cols[0]:
        st.markdown("##### Commodity Section")
        st.caption(
            "Commodity-first view that answers what is moving, in what direction, and how those moves can transmit across upstream and downstream links."
        )
        st.caption(f"Commodity filter: {commodity_focus}")
    with heading_cols[1]:
        _render_help_popover(
            "How to read this section",
            """
**Plain-English version**

- `Direction` tells you whether a commodity is rising, falling, or changing pace.
- `Relative strength` tells you whether it is outperforming the broad commodity basket.
- `Dependency graph` shows where one commodity often transmits into another through energy inputs, fertilizer costs, electrification demand, or soft-commodity spillovers.

**What this section is trying to answer**

- Which commodities are moving up or down right now?
- Which moves are accelerating versus cooling off?
- Which commodities may be pressuring or feeding into other commodities?
            """,
        )

    control_cols = st.columns(4)
    with control_cols[0]:
        experiment_days = st.slider("History (days)", 126, 504, 252, step=21, key="market_commodity_days")
    with control_cols[1]:
        corr_window = st.slider("Beta Window", 10, 60, 20, step=5, key="market_commodity_corr_window")
    with control_cols[2]:
        roc_window = st.slider("Beta RoC", 5, 30, 10, step=1, key="market_commodity_roc_window")
    with control_cols[3]:
        momentum_window = st.slider("Transmission Window", 21, 126, 63, step=21, key="market_commodity_momentum_window")

    st.caption(commodity_focus_description(commodity_focus))
    reference_symbols = commodity_reference_universe()
    st.caption(
        "Broad commodity reference basket: "
        + ", ".join(f"{commodity_proxy_profile(symbol)['commodity']} ({symbol})" for symbol in reference_symbols)
    )
    with st.expander("Commodity Lens Constituents", expanded=False):
        lens_profiles = pd.DataFrame([commodity_proxy_profile(symbol) for symbol in commodity_symbols])
        st.dataframe(
            lens_profiles[["symbol", "name", "commodity", "description"]],
            use_container_width=True,
            hide_index=True,
        )

    try:
        with st.spinner("Scanning commodity market structure..."):
            with _timed(
                "scan_commodity_regimes",
                basket=commodity_focus,
                days=experiment_days,
                corr_window=corr_window,
                roc_window=roc_window,
                momentum_window=momentum_window,
            ):
                experiment_data = _load_commodity_regime_cached(
                    cfg,
                    commodity_symbols=reference_symbols,
                    days=experiment_days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                    symbols=commodity_symbols,
                    force_refresh=force_data_refresh,
                )
                momentum = _scan_momentum_profiles_cached(
                    cfg,
                    experiment_days,
                    symbols=commodity_symbols,
                    force_refresh=force_data_refresh,
                )
    except AlpacaAPIError as exc:
        _log_event("scan_commodity_regimes_failed", basket=commodity_focus, error=str(exc)[:200])
        st.error(f"Could not run the commodity analyzer: {exc}")
        return

    summary = experiment_data.get("summary", pd.DataFrame())
    history = experiment_data.get("history", pd.DataFrame())
    if summary.empty or history.empty or momentum.empty:
        st.info("Not enough commodity history was returned to build this section.")
        return

    summary = summary.merge(
        momentum[
            [
                "symbol",
                "daily_change_pct",
                "return_1w_pct",
                "return_1m_pct",
                "return_3m_pct",
                "momentum_score",
                "momentum_roc_score",
                "trend_r2_3m",
                "trend_fit_gap",
            ]
        ],
        on="symbol",
        how="left",
    )
    for column in ["name", "commodity_label", "description"]:
        if column not in summary.columns:
            summary[column] = None

    for row_idx, symbol in summary["symbol"].astype(str).items():
        profile = commodity_proxy_profile(symbol)
        if pd.isna(summary.at[row_idx, "name"]) or not str(summary.at[row_idx, "name"]).strip():
            summary.at[row_idx, "name"] = profile["name"]
        if pd.isna(summary.at[row_idx, "commodity_label"]) or not str(summary.at[row_idx, "commodity_label"]).strip():
            summary.at[row_idx, "commodity_label"] = profile["commodity"]
        if pd.isna(summary.at[row_idx, "description"]) or not str(summary.at[row_idx, "description"]).strip():
            summary.at[row_idx, "description"] = profile["description"]

    def commodity_direction_label(row: pd.Series) -> str:
        return_1m = pd.to_numeric(row.get("return_1m_pct"), errors="coerce")
        roc = pd.to_numeric(row.get("momentum_roc_score"), errors="coerce")
        daily = pd.to_numeric(row.get("daily_change_pct"), errors="coerce")
        if pd.notna(return_1m) and return_1m >= 0 and pd.notna(roc) and roc >= 0:
            return "Up and accelerating"
        if pd.notna(return_1m) and return_1m >= 0:
            return "Up but cooling"
        if pd.notna(return_1m) and return_1m < 0 and pd.notna(roc) and roc < 0:
            return "Down and worsening"
        if pd.notna(return_1m) and return_1m < 0:
            return "Down but stabilizing"
        if pd.notna(daily) and daily >= 0:
            return "Positive turn"
        return "Mixed"

    summary["direction_label"] = summary.apply(commodity_direction_label, axis=1)
    summary["trend_consistency_pct"] = pd.to_numeric(summary["trend_r2_3m"], errors="coerce") * 100.0
    summary["pullback_abs"] = pd.to_numeric(summary["pullback_from_high_pct"], errors="coerce").abs()

    breadth_positive = int(pd.to_numeric(summary["daily_change_pct"], errors="coerce").gt(0).sum())
    breadth_negative = int(pd.to_numeric(summary["daily_change_pct"], errors="coerce").lt(0).sum())
    leader = summary.sort_values("return_1m_pct", ascending=False, na_position="last").head(1)
    laggard = summary.sort_values("return_1m_pct", ascending=True, na_position="last").head(1)
    accelerator = summary.sort_values("momentum_roc_score", ascending=False, na_position="last").head(1)

    metric_cols = st.columns(4)
    with metric_cols[0]:
        if not leader.empty:
            st.metric(
                "1M Leader",
                str(leader.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(leader.iloc[0]['return_1m_pct'], errors='coerce'):.1f}%",
            )
    with metric_cols[1]:
        if not laggard.empty:
            st.metric(
                "1M Laggard",
                str(laggard.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(laggard.iloc[0]['return_1m_pct'], errors='coerce'):.1f}%",
            )
    with metric_cols[2]:
        st.metric("Daily Breadth", f"{breadth_positive} up / {breadth_negative} down")
    with metric_cols[3]:
        if not accelerator.empty:
            st.metric(
                "Fastest Rotation",
                str(accelerator.iloc[0]["commodity_label"]),
                f"{pd.to_numeric(accelerator.iloc[0]['momentum_roc_score'], errors='coerce'):.2f}",
            )

    selected_commodity_ticker: str | None = None
    moving_up = summary[pd.to_numeric(summary["return_1m_pct"], errors="coerce") > 0].sort_values(
        ["return_1m_pct", "daily_change_pct"],
        ascending=[False, False],
        na_position="last",
    ).head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "daily_change_pct",
            "return_1w_pct",
            "return_1m_pct",
            "direction_label",
        ]
    ]
    moving_down = summary[pd.to_numeric(summary["return_1m_pct"], errors="coerce") < 0].sort_values(
        ["return_1m_pct", "daily_change_pct"],
        ascending=[True, True],
        na_position="last",
    ).head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "daily_change_pct",
            "return_1w_pct",
            "return_1m_pct",
            "direction_label",
        ]
    ]
    consistent_trends = summary.sort_values(["trend_fit_gap", "return_1m_pct"], ascending=[True, False], na_position="last").head(10)[
        [
            "symbol",
            "name",
            "commodity_label",
            "trend_consistency_pct",
            "pullback_from_high_pct",
            "relative_strength_pct",
        ]
    ]

    table_left, table_right = st.columns(2)
    with table_left:
        selected_commodity_ticker = _render_selectable_ticker_table(
            "Moving Up",
            moving_up,
            ["symbol", "name", "commodity_label", "daily_change_pct", "return_1w_pct", "return_1m_pct"],
            key="market_commodity_beneficiaries",
        ) or selected_commodity_ticker
    with table_right:
        selected_commodity_ticker = _render_selectable_ticker_table(
            "Moving Down",
            moving_down,
            ["symbol", "name", "commodity_label", "daily_change_pct", "return_1w_pct", "return_1m_pct"],
            key="market_commodity_squeezes",
        ) or selected_commodity_ticker

    selected_commodity_ticker = _render_selectable_ticker_table(
        "Most Consistent Trends",
        consistent_trends,
        ["symbol", "name", "commodity_label", "trend_consistency_pct", "pullback_from_high_pct", "relative_strength_pct"],
        key="market_commodity_decouplers",
    ) or selected_commodity_ticker

    series_left, series_right = st.columns(2)
    with series_left:
        if moving_up.empty:
            st.info("No commodities are currently in the `up` bucket for this filter.")
        else:
            st.plotly_chart(
                _build_rank_timeseries_figure(
                    history,
                    moving_up,
                    f"Moving Up Time Series: {commodity_focus}",
                    days=experiment_days,
                ),
                use_container_width=True,
            )
    with series_right:
        if moving_down.empty:
            st.info("No commodities are currently in the `down` bucket for this filter.")
        else:
            st.plotly_chart(
                _build_rank_timeseries_figure(
                    history,
                    moving_down,
                    f"Moving Down Time Series: {commodity_focus}",
                    days=experiment_days,
                ),
                use_container_width=True,
            )

    if consistent_trends.empty:
        st.info("No consistent trend series were available for this commodity filter.")
    else:
        st.plotly_chart(
            _build_rank_timeseries_figure(
                history,
                consistent_trends,
                f"Most Consistent Trend Time Series: {commodity_focus}",
                days=experiment_days,
            ),
            use_container_width=True,
        )

    heatmap_frame = summary.sort_values("return_1m_pct", ascending=False, na_position="last").reset_index(drop=True)
    heatmap_values = heatmap_frame[
        ["daily_change_pct", "return_1w_pct", "return_1m_pct", "return_3m_pct", "relative_strength_pct"]
    ].apply(pd.to_numeric, errors="coerce")
    heatmap_labels = [f"{row['commodity_label']} ({row['symbol']})" for _, row in heatmap_frame.iterrows()]

    scatter_frame = summary.copy()
    scatter_frame["rotation_abs"] = pd.to_numeric(scatter_frame["momentum_roc_score"], errors="coerce").abs()
    scatter_frame, commodity_size_col = _prepare_scatter_size(scatter_frame, "rotation_abs")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Direction Heatmap",
                """
This is the fastest answer to "what is moving and in what direction?"

- rows are commodities in the selected filter
- columns are return horizons
- green means rising, red means falling
- the further from zero, the stronger the move
                """,
            )
        fig_heatmap = go.Figure(
            data=go.Heatmap(
                z=heatmap_values.to_numpy(dtype=float),
                x=["1D %", "1W %", "1M %", "3M %", "Rel Strength %"],
                y=heatmap_labels,
                colorscale="RdYlGn",
                zmid=0,
                colorbar=dict(title="%"),
                hovertemplate="%{y}<br>%{x}: %{z:.2f}%<extra></extra>",
            )
        )
        fig_heatmap.update_layout(template="plotly_dark", title=f"Commodity Direction Heatmap: {commodity_focus}")
        st.plotly_chart(fig_heatmap, use_container_width=True)

    with chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Leadership vs Pullback",
                """
This separates strong leaders from weak laggards.

- `X axis`: relative strength versus the broad commodity basket
- `Y axis`: 1-month direction of travel
- `Marker size`: change in momentum pace
- higher and further right usually means stronger commodity leadership
                """,
            )
        fig_commodity = px.scatter(
            scatter_frame,
            x="relative_strength_pct",
            y="return_1m_pct",
            color="direction_label",
            size=commodity_size_col,
            hover_name="name",
            hover_data={
                "symbol": True,
                "commodity_label": True,
                "daily_change_pct": ":.2f",
                "return_3m_pct": ":.2f",
                "transmission_gap_pct": ":.2f",
                "pullback_from_high_pct": ":.2f",
            },
            template="plotly_dark",
            title=f"Commodity Leadership Map: {commodity_focus}",
            labels={
                "relative_strength_pct": "Relative Strength vs Broad Basket %",
                "return_1m_pct": "1M Return %",
            },
        )
        fig_commodity.add_hline(y=0, line_dash="dot", line_color="#666")
        fig_commodity.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_commodity, use_container_width=True)

    dependency_edges = commodity_dependency_graph(commodity_symbols)
    if not dependency_edges.empty:
        sankey_left, sankey_right = st.columns([2.2, 1.2])
        with sankey_left:
            chart_help_cols = st.columns([10, 2])
            with chart_help_cols[1]:
                _render_help_popover(
                    "Commodity Dependency Graph",
                    """
This graph shows common transmission paths.

- links point from likely upstream pressure into likely downstream reaction
- thicker links represent stronger expected transmission
- node color reflects current 1-month direction of the commodity
                    """,
                )

            value_map = summary.set_index("symbol")["return_1m_pct"].to_dict()

            def _node_color(value: float) -> str:
                if not np.isfinite(value):
                    return "#888888"
                if value >= 8:
                    return "#1f9d55"
                if value > 0:
                    return "#7bc96f"
                if value <= -8:
                    return "#c23b22"
                if value < 0:
                    return "#f28b82"
                return "#888888"

            def _link_color(value: float) -> str:
                if not np.isfinite(value):
                    return "rgba(160,160,160,0.35)"
                if value >= 0:
                    return "rgba(31,157,85,0.35)"
                return "rgba(194,59,34,0.35)"

            nodes = list(dict.fromkeys(dependency_edges["source"].tolist() + dependency_edges["target"].tolist()))
            node_index = {symbol: idx for idx, symbol in enumerate(nodes)}
            node_labels = [f"{commodity_proxy_profile(symbol)['commodity']} ({symbol})" for symbol in nodes]
            node_colors = [_node_color(pd.to_numeric(value_map.get(symbol), errors="coerce")) for symbol in nodes]
            link_colors = [_link_color(pd.to_numeric(value_map.get(symbol), errors="coerce")) for symbol in dependency_edges["source"]]
            link_labels = [
                f"{row.source_commodity} -> {row.target_commodity}<br>{row.relation}: {row.description}"
                for row in dependency_edges.itertuples(index=False)
            ]

            fig_sankey = go.Figure(
                go.Sankey(
                    arrangement="snap",
                    node=dict(label=node_labels, color=node_colors, pad=18, thickness=18),
                    link=dict(
                        source=[node_index[symbol] for symbol in dependency_edges["source"]],
                        target=[node_index[symbol] for symbol in dependency_edges["target"]],
                        value=dependency_edges["weight"].astype(float).tolist(),
                        color=link_colors,
                        label=link_labels,
                        hovertemplate="%{label}<extra></extra>",
                    ),
                )
            )
            fig_sankey.update_layout(template="plotly_dark", title=f"Commodity Dependency Graph: {commodity_focus}")
            st.plotly_chart(fig_sankey, use_container_width=True)

        with sankey_right:
            edge_view = dependency_edges[
                ["source_commodity", "target_commodity", "relation", "weight"]
            ].rename(
                columns={
                    "source_commodity": "Upstream",
                    "target_commodity": "Downstream",
                    "relation": "Link",
                    "weight": "Weight",
                }
            )
            st.subheader("Key Links")
            st.dataframe(edge_view, use_container_width=True, hide_index=True)

    commodity_symbol_options = sorted(summary["symbol"].astype(str).unique().tolist())
    commodity_selected_key = "market_commodity_selected_ticker"
    commodity_widget_key = "market_commodity_ticker_widget"
    fallback_commodity_ticker = st.session_state.get(commodity_selected_key) or commodity_symbol_options[0]
    if fallback_commodity_ticker not in commodity_symbol_options:
        fallback_commodity_ticker = commodity_symbol_options[0]

    current_commodity_ticker = st.session_state.get(commodity_selected_key)
    if current_commodity_ticker not in commodity_symbol_options:
        current_commodity_ticker = fallback_commodity_ticker

    if selected_commodity_ticker and selected_commodity_ticker in commodity_symbol_options:
        if selected_commodity_ticker != current_commodity_ticker:
            st.session_state[commodity_selected_key] = selected_commodity_ticker
            st.session_state[commodity_widget_key] = selected_commodity_ticker
            st.rerun()
    elif commodity_selected_key not in st.session_state or st.session_state[commodity_selected_key] not in commodity_symbol_options:
        st.session_state[commodity_selected_key] = fallback_commodity_ticker

    if commodity_widget_key not in st.session_state or st.session_state[commodity_widget_key] not in commodity_symbol_options:
        st.session_state[commodity_widget_key] = st.session_state[commodity_selected_key]
    elif st.session_state[commodity_widget_key] != st.session_state[commodity_selected_key]:
        st.session_state[commodity_widget_key] = st.session_state[commodity_selected_key]

    commodity_ticker = st.selectbox(
        "Commodity Detail",
        commodity_symbol_options,
        key=commodity_widget_key,
        on_change=lambda: st.session_state.__setitem__(commodity_selected_key, st.session_state.get(commodity_widget_key)),
    )
    st.session_state[commodity_selected_key] = commodity_ticker

    detail_row = summary[summary["symbol"] == commodity_ticker].head(1)
    detail_history = history[history["symbol"] == commodity_ticker].copy()
    if detail_row.empty or detail_history.empty:
        st.info("No detail history available for the selected commodity ticker.")
        return

    detail = detail_row.iloc[0]
    commodity_name = str(detail.get("name") or commodity_proxy_profile(commodity_ticker)["name"])
    commodity_description = str(detail.get("description") or commodity_proxy_profile(commodity_ticker)["description"])
    commodity_label = str(detail.get("commodity_label") or commodity_proxy_profile(commodity_ticker)["commodity"])
    st.subheader(f"{commodity_name} ({commodity_ticker})")
    st.write(commodity_description)
    st.caption(f"Commodity type: {commodity_label} | Filter: {commodity_focus}")

    metric_cols = st.columns(6)
    with metric_cols[0]:
        st.metric("Direction", str(detail.get("direction_label") or "n/a"))
    with metric_cols[1]:
        st.metric("1D Move", f"{pd.to_numeric(detail.get('daily_change_pct'), errors='coerce'):.1f}%")
    with metric_cols[2]:
        st.metric("1W Move", f"{pd.to_numeric(detail.get('return_1w_pct'), errors='coerce'):.1f}%")
    with metric_cols[3]:
        st.metric("1M Move", f"{pd.to_numeric(detail.get('return_1m_pct'), errors='coerce'):.1f}%")
    with metric_cols[4]:
        st.metric("Relative Strength", f"{pd.to_numeric(detail.get('relative_strength_pct'), errors='coerce'):.1f}%")
    with metric_cols[5]:
        st.metric("Pullback vs High", f"{pd.to_numeric(detail.get('pullback_from_high_pct'), errors='coerce'):.1f}%")

    detail_history = detail_history.sort_values("timestamp").copy()
    visible_cutoff = detail_history["timestamp"].max() - pd.Timedelta(days=min(experiment_days, 180))
    visible_history = detail_history[detail_history["timestamp"] >= visible_cutoff].copy()
    if visible_history.empty:
        visible_history = detail_history.copy()

    detail_chart_left, detail_chart_right = st.columns(2)
    with detail_chart_left:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Relative Path vs Commodity Basket",
                """
This compares the selected commodity against the broad commodity basket from the same starting scale.

- if the commodity line rises faster, it is leading the broad market
- if the basket rises faster, leadership is broad and this commodity is lagging
- a widening gap often matters more than the absolute beta reading
                """,
            )
        fig_relative = go.Figure()
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["asset_norm"],
                mode="lines",
                name=commodity_ticker,
            )
        )
        fig_relative.add_trace(
            go.Scatter(
                x=visible_history["timestamp"],
                y=visible_history["commodity_norm"],
                mode="lines",
                name="Broad Commodity Basket",
            )
        )
        fig_relative.update_layout(
            template="plotly_dark",
            title=f"{commodity_name} vs Broad Commodity Basket",
            xaxis_title="Date",
            yaxis_title="Normalized Price",
            hovermode="x unified",
        )
        st.plotly_chart(fig_relative, use_container_width=True)

    with detail_chart_right:
        chart_help_cols = st.columns([10, 2])
        with chart_help_cols[1]:
            _render_help_popover(
                "Return Ladder",
                """
This condenses the move across horizons into one chart.

- left of zero means the commodity is still under pressure
- right of zero means it is moving higher
- compare short and medium horizons to see whether the move is strengthening or fading
                """,
            )
        return_ladder = pd.DataFrame(
            {
                "horizon": ["1D", "1W", "1M", "3M", "Rel Strength"],
                "value": [
                    pd.to_numeric(detail.get("daily_change_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_1w_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_1m_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("return_3m_pct"), errors="coerce"),
                    pd.to_numeric(detail.get("relative_strength_pct"), errors="coerce"),
                ],
            }
        )
        fig_ladder = px.bar(
            return_ladder,
            x="value",
            y="horizon",
            orientation="h",
            color="value",
            color_continuous_scale="RdYlGn",
            template="plotly_dark",
            title=f"{commodity_name} Return Ladder",
            labels={"value": "Percent", "horizon": ""},
        )
        fig_ladder.add_vline(x=0, line_dash="dot", line_color="#666")
        st.plotly_chart(fig_ladder, use_container_width=True)

    transmission_help_cols = st.columns([10, 2])
    with transmission_help_cols[1]:
        _render_help_popover(
            "Trend Structure",
            """
This compares the commodity's trend strength against the broad basket and its pullback from a recent high.

- `Commodity Comp Momentum %`: the selected commodity's own stacked return impulse
- `Broad Basket Momentum %`: the same idea for the broad commodity market
- `Pullback %`: how stretched or washed out the commodity is versus its recent high

Strong momentum with a shallow pullback usually signals leadership. Weak momentum with a deep pullback usually signals stress.
            """,
        )
    fig_transmission = go.Figure()
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["asset_compounding_momentum"] * 100.0,
            mode="lines",
            name="Commodity Comp Momentum %",
        )
    )
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["commodity_compounding_momentum"] * 100.0,
            mode="lines",
            name="Broad Basket Momentum %",
        )
    )
    fig_transmission.add_trace(
        go.Scatter(
            x=visible_history["timestamp"],
            y=visible_history["pullback_from_high"] * 100.0,
            mode="lines",
            name="Pullback vs High %",
        )
    )
    fig_transmission.update_layout(
        template="plotly_dark",
        title=f"{commodity_name} Trend and Pullback",
        xaxis_title="Date",
        yaxis_title="Percent",
        hovermode="x unified",
    )
    st.plotly_chart(fig_transmission, use_container_width=True)

    related_edges = dependency_edges[
        (dependency_edges["source"] == commodity_ticker) | (dependency_edges["target"] == commodity_ticker)
    ].copy() if not dependency_edges.empty else pd.DataFrame()
    st.subheader("Dependency Context")
    if related_edges.empty:
        st.info("No curated upstream/downstream dependency links are defined for this commodity in the current filter.")
    else:
        related_edges["flow"] = [
            f"{row.source_commodity} -> {row.target_commodity}" for row in related_edges.itertuples(index=False)
        ]
        st.dataframe(
            related_edges[["flow", "relation", "description", "weight"]],
            use_container_width=True,
            hide_index=True,
        )


def _summary_payload_for_agent(summary: dict[str, object]) -> dict[str, object]:
    def text(value: object) -> str:
        if value is None:
            return ""
        out = str(value).strip()
        return "" if out.lower() == "nan" else out

    return {
        "status": text((summary or {}).get("status")),
        "headline": text((summary or {}).get("headline")),
        "summary_markdown": text((summary or {}).get("summary_markdown")),
        "watch_items": to_list((summary or {}).get("watch_items")),
        "data_gaps": to_list((summary or {}).get("data_gaps")),
        "confidence": text((summary or {}).get("confidence")),
        "aql_agent": (summary or {}).get("aql_agent") if isinstance((summary or {}).get("aql_agent"), dict) else {},
    }


def _latest_materialized_agentic_summary(surface: str, ticker: str = "") -> dict[str, object]:
    return _load_page_agentic_summary_cached(
        surface,
        "",
        str(ticker or "").upper().strip(),
    )


def _trading_agent_ticker_evidence(
    cfg: AppConfig,
    opportunity_feed: pd.DataFrame,
    *,
    max_candidates: int,
    force_data_refresh: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(opportunity_feed, pd.DataFrame) or opportunity_feed.empty or "symbol" not in opportunity_feed.columns:
        return [], []

    def text(value: object) -> str:
        if value is None:
            return ""
        out = str(value).strip()
        return "" if out.lower() == "nan" else out

    symbols = [
        str(symbol).upper().strip()
        for symbol in opportunity_feed["symbol"].astype(str).tolist()
        if str(symbol).strip()
    ][: max(int(max_candidates), 1)]
    ticker_evidence: list[dict[str, object]] = []
    stock_summaries: list[dict[str, object]] = []
    feed_lookup = {
        str(row.get("symbol") or "").upper().strip(): row
        for _, row in opportunity_feed.iterrows()
        if str(row.get("symbol") or "").strip()
    }
    for symbol in symbols:
        row = feed_lookup.get(symbol)
        opportunity_row = {}
        if row is not None:
            opportunity_row = {
                key: row.get(key)
                for key in [
                    "symbol",
                    "company_name",
                    "opportunity",
                    "direction",
                    "opportunity_score",
                    "close",
                    "daily_change_pct",
                    "return_1w_pct",
                    "return_1m_pct",
                    "return_3m_pct",
                    "momentum_score",
                    "momentum_roc_score",
                    "trend_fit_gap",
                    "details",
                ]
                if key in row.index
            }
        try:
            signal_summary = _load_technical_signal_summary_cached(
                cfg,
                symbol,
                None,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            signal_summary = {"error": str(exc)}
        try:
            forecast = _load_forecast_next_week_cached(
                cfg,
                symbol,
                days=365,
                signal_frame=None,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            forecast = {"error": str(exc)}
        try:
            news_payload = _load_recent_news_cached(
                cfg,
                symbol,
                days=14,
                limit=5,
                force_refresh=force_data_refresh,
            )
            news_summary = summarize_recent_news(symbol, news_payload)
        except Exception as exc:
            news_payload = {"articles": pd.DataFrame(), "fallback_summary": None, "source": None}
            news_summary = {"summary_lines": [f"Recent news unavailable: {exc}"], "articles": pd.DataFrame()}
        try:
            attention_context = _load_attention_context_cached(
                cfg,
                symbol,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            attention_context = {"error": str(exc)}
        try:
            background_payload = _load_attention_ticker_background_cached(
                cfg,
                symbol,
                force_refresh=force_data_refresh,
            )
        except Exception:
            background_payload = {}

        stock_summary = _latest_materialized_agentic_summary(STOCK_INVESTIGATOR_SECTION, symbol)
        stock_summaries.append({"ticker": symbol, **_summary_payload_for_agent(stock_summary)})

        articles = news_summary.get("articles")
        recent_articles = []
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            recent_articles = _json_ready(
                articles[
                    [
                        column
                        for column in ["headline", "title", "source", "published_at", "summary", "url"]
                        if column in articles.columns
                    ]
                ].head(5)
            )
        ticker_evidence.append(
            {
                "symbol": symbol,
                "opportunity": _json_ready(opportunity_row),
                "technical_signals": _json_ready(signal_summary),
                "forecast": _json_ready(forecast),
                "stock_summary": _summary_payload_for_agent(stock_summary),
                "attention_context": {
                    key: text((attention_context or {}).get(key))
                    for key in [
                        "llm_headline",
                        "llm_summary_text",
                        "llm_why_now",
                        "llm_management_signal",
                        "context_story_text",
                    ]
                    if text((attention_context or {}).get(key))
                },
                "news_summary_lines": [
                    str(item).strip()
                    for item in to_list((news_summary or {}).get("summary_lines"))
                    if str(item).strip()
                ][:5],
                "recent_articles": recent_articles,
            }
        )
    return ticker_evidence, stock_summaries


def _trading_agent_text(value: object) -> str:
    if value is None:
        return ""
    out = str(value).strip()
    return "" if out.lower() == "nan" else out


def _trading_agent_number(value: object, *, suffix: str = "", decimals: int = 1) -> str:
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index, np.ndarray)):
        values = list(value)
        value = values[0] if values else None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "n/a"
    return f"{float(numeric):.{max(int(decimals), 0)}f}{suffix}"


def _trading_agent_float(value: object) -> float | None:
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index, np.ndarray)):
        values = list(value)
        value = values[0] if values else None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    return float(numeric)


def _trading_agent_pct(value: object) -> str:
    numeric = _trading_agent_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:+.1f}%"


def _trading_agent_strength(value: float | None, *, scale: float, inverse: bool = False) -> int:
    if value is None:
        return 0
    if inverse:
        score = (1.0 - min(abs(value), scale) / scale) * 100.0
    else:
        score = min(abs(value), scale) / scale * 100.0
    return int(max(0.0, min(100.0, score)))


def _trading_agent_trend_quality(trend_gap: float | None) -> tuple[str, int]:
    if trend_gap is None:
        return "Unavailable", 0
    if trend_gap <= 0.12:
        return "Clean trend", 88
    if trend_gap <= 0.28:
        return "Usable trend", 64
    if trend_gap <= 0.50:
        return "Choppy trend", 38
    return "Noisy trend", 18


def _trading_agent_momentum_label(momentum_roc: float | None) -> str:
    if momentum_roc is None:
        return "Pace unavailable"
    if momentum_roc >= 0.25:
        return "Accelerating"
    if momentum_roc <= -0.25:
        return "Fading"
    return "Stable pace"


def _trading_agent_source_freshness(opportunity: dict[str, object]) -> str:
    asof = _trading_agent_text(opportunity.get("asof_time_utc"))
    if asof:
        return f"As of {asof}"
    run_id = _trading_agent_text(opportunity.get("run_id"))
    if run_id:
        return f"Run {run_id[:8]}"
    return ""


def _trading_agent_sparkline_values(value: object) -> list[float]:
    values: list[float] = []
    raw_values = to_list(value)
    if len(raw_values) == 1 and isinstance(raw_values[0], str):
        try:
            parsed = json.loads(raw_values[0])
            raw_values = to_list(parsed)
        except Exception:
            raw_values = []
    for item in raw_values:
        numeric = _trading_agent_float(item)
        if numeric is not None:
            values.append(numeric)
    return values[-90:]


def _trading_agent_signal_model(
    opportunity: dict[str, object],
    controls: dict[str, object],
) -> dict[str, object]:
    selected_horizon_col = _trading_agent_text(
        controls.get("selected_horizon_col") or opportunity.get("selected_horizon_col") or "return_1m_pct"
    )
    selected_horizon_label = _trading_agent_text(
        controls.get("momentum_horizon") or opportunity.get("selected_horizon_label") or "1 Month"
    )
    horizon_return = _trading_agent_float(opportunity.get(selected_horizon_col))
    daily_move = _trading_agent_float(opportunity.get("daily_change_pct"))
    momentum_roc = _trading_agent_float(opportunity.get("momentum_roc_score"))
    trend_gap = _trading_agent_float(opportunity.get("trend_fit_gap"))
    trend_label, trend_strength = _trading_agent_trend_quality(trend_gap)
    rank = _trading_agent_float(opportunity.get("opportunity_score"))

    missing = []
    for label, value in [
        ("market signal rank", rank),
        ("1D move", daily_move),
        (f"{selected_horizon_label} return", horizon_return),
        ("momentum pace", momentum_roc),
        ("trend quality", trend_gap),
    ]:
        if value is None:
            missing.append(label)

    return {
        "rank": rank,
        "rank_label": "n/a" if rank is None else f"{rank:.0f}/100",
        "market_pattern": _trading_agent_text(opportunity.get("direction") or opportunity.get("opportunity")) or "Pattern unavailable",
        "opportunity_label": _trading_agent_text(opportunity.get("opportunity")),
        "selected_horizon_col": selected_horizon_col,
        "selected_horizon_label": selected_horizon_label,
        "horizon_return": horizon_return,
        "daily_move": daily_move,
        "momentum_roc": momentum_roc,
        "momentum_label": _trading_agent_momentum_label(momentum_roc),
        "trend_gap": trend_gap,
        "trend_label": trend_label,
        "score_components": [
            {
                "label": f"{selected_horizon_label} move",
                "value": _trading_agent_strength(horizon_return, scale=20.0),
                "display": _trading_agent_pct(horizon_return),
            },
            {
                "label": "Momentum pace",
                "value": _trading_agent_strength(momentum_roc, scale=1.0),
                "display": "n/a" if momentum_roc is None else f"{momentum_roc:+.2f}",
            },
            {
                "label": "1D move",
                "value": _trading_agent_strength(daily_move, scale=5.0),
                "display": _trading_agent_pct(daily_move),
            },
            {
                "label": "Trend quality",
                "value": trend_strength,
                "display": trend_label,
            },
        ],
        "sparkline": _trading_agent_sparkline_values(opportunity.get("sparkline_3m")),
        "freshness": _trading_agent_source_freshness(opportunity),
        "missing": missing,
    }


def _render_trading_agent_signal_bar(label: str, value: int, display: str) -> None:
    safe_label = html.escape(str(label))
    safe_display = html.escape(str(display))
    bounded = int(max(0, min(100, value)))
    st.markdown(
        "<div class='sn-trading-signal-row'>"
        f"<div class='sn-trading-signal-label'>{safe_label}</div>"
        "<div class='sn-trading-signal-track'>"
        f"<div class='sn-trading-signal-fill' style='width:{bounded}%'></div>"
        "</div>"
        f"<div class='sn-trading-signal-value'>{safe_display}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_trading_agent_sparkline(values: list[float], *, key: str) -> None:
    if len(values) < 2:
        st.caption("No 3M sparkline available from the materialized feed.")
        return
    frame = pd.DataFrame({"point": range(len(values)), "value": values})
    fig = go.Figure(
        go.Scatter(
            x=frame["point"],
            y=frame["value"],
            mode="lines",
            line={"color": "#2563eb", "width": 2},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=92,
        margin={"l": 4, "r": 4, "t": 4, "b": 4},
        xaxis={"visible": False},
        yaxis={"visible": False},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def _render_trading_agent_evidence_checklist(
    *,
    evidence: list[str],
    invalidation: str,
    risks: list[str],
    ticker_evidence: dict[str, object],
    aql_agent: dict[str, object],
) -> None:
    stock_summary = ticker_evidence.get("stock_summary") if isinstance(ticker_evidence, dict) else {}
    attention_context = ticker_evidence.get("attention_context") if isinstance(ticker_evidence, dict) else {}
    news_lines = to_list(ticker_evidence.get("news_summary_lines")) if isinstance(ticker_evidence, dict) else []
    checks = [
        ("Market evidence", bool(evidence)),
        ("AQL synthesis", _trading_agent_text((aql_agent or {}).get("answer_markdown")) != ""),
        ("Stock summary", isinstance(stock_summary, dict) and bool(_trading_agent_text(stock_summary.get("headline")))),
        (
            "Recent context",
            bool(news_lines)
            or (
                isinstance(attention_context, dict)
                and any(_trading_agent_text(attention_context.get(key)) for key in ["llm_headline", "llm_why_now"])
            ),
        ),
        ("Invalidation", bool(invalidation)),
        ("Tail risk", bool(risks)),
    ]
    st.markdown(
        "<div class='sn-trading-checklist'>"
        + "".join(
            "<span class='sn-trading-check'>"
            f"{'&#10003;' if ok else '&ndash;'} {html.escape(label)}"
            "</span>"
            for label, ok in checks
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _trading_agent_context_lookup(context: dict[str, object]) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for row in to_list((context or {}).get("market_opportunity_feed")):
        if not isinstance(row, dict):
            continue
        symbol = _trading_agent_text(row.get("symbol") or row.get("ticker")).upper()
        if not symbol:
            continue
        lookup.setdefault(symbol, {})["opportunity"] = row
    for item in to_list((context or {}).get("ticker_evidence")):
        if not isinstance(item, dict):
            continue
        symbol = _trading_agent_text(item.get("symbol") or item.get("ticker")).upper()
        if not symbol:
            continue
        lookup.setdefault(symbol, {})["ticker_evidence"] = item
        opportunity = item.get("opportunity")
        if isinstance(opportunity, dict) and opportunity:
            lookup.setdefault(symbol, {}).setdefault("opportunity", opportunity)
    source_summaries = dict((context or {}).get("source_summaries") or {})
    for item in to_list(source_summaries.get("stock_investigator")):
        if not isinstance(item, dict):
            continue
        symbol = _trading_agent_text(item.get("ticker") or item.get("symbol")).upper()
        if symbol:
            lookup.setdefault(symbol, {})["stock_summary"] = item
    return lookup


def _trading_agent_company_name(symbol: str, context_item: dict[str, object]) -> str:
    opportunity = context_item.get("opportunity") if isinstance(context_item, dict) else {}
    if isinstance(opportunity, dict):
        company = _trading_agent_text(opportunity.get("company_name") or opportunity.get("security_name") or opportunity.get("name"))
        if company and company.upper() != symbol.upper():
            return company
    return ""


def _render_trading_agent_source_summaries(context: dict[str, object], result: dict[str, object]) -> None:
    source_summaries = dict((context or {}).get("source_summaries") or {})
    if source_summaries:
        with st.expander("Source summaries", expanded=False):
            for label, key in [
                ("Market Explorer", "market_explorer"),
                ("Broad Economy", "broad_economy"),
            ]:
                summary = source_summaries.get(key)
                if not isinstance(summary, dict):
                    continue
                headline = _trading_agent_text(summary.get("headline"))
                body = _trading_agent_text(summary.get("summary_markdown"))
                confidence = _trading_agent_text(summary.get("confidence"))
                st.markdown(f"**{label}**")
                if headline:
                    st.caption(headline)
                if body:
                    st.markdown(body)
                if confidence:
                    st.caption(f"Confidence: {confidence}")
            stock_summaries = [
                item
                for item in to_list(source_summaries.get("stock_investigator"))
                if isinstance(item, dict)
            ]
            if stock_summaries:
                rows = []
                for item in stock_summaries:
                    rows.append(
                        {
                            "Ticker": _trading_agent_text(item.get("ticker") or item.get("symbol")).upper(),
                            "Headline": _trading_agent_text(item.get("headline")),
                            "Confidence": _trading_agent_text(item.get("confidence")),
                        }
                    )
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    aql_agent = result.get("aql_agent") if isinstance(result.get("aql_agent"), dict) else {}
    tool_calls = [
        item
        for item in to_list((aql_agent or {}).get("tool_calls"))
        if isinstance(item, dict)
    ]
    if tool_calls:
        with st.expander("AQL trace", expanded=False):
            rows = []
            for call in tool_calls:
                rows.append(
                    {
                        "Tool": _trading_agent_text(call.get("tool_name")),
                        "Status": _trading_agent_text(call.get("status")),
                        "Preview": _trading_agent_text(call.get("preview")),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_trading_agent_candidate_card(
    candidate: dict[str, object],
    *,
    context_item: dict[str, object],
    controls: dict[str, object],
    aql_agent: dict[str, object],
    idx: int,
    action_state: dict[str, object] | None = None,
) -> None:
    ticker = _trading_agent_text(candidate.get("ticker")).upper()
    direction = _trading_agent_text(candidate.get("direction") or "watch").upper()
    confidence = _trading_agent_text(candidate.get("confidence") or "low").title()
    horizon = _trading_agent_text(candidate.get("suggested_horizon"))
    company_name = _trading_agent_company_name(ticker, context_item)
    opportunity = context_item.get("opportunity") if isinstance(context_item, dict) else {}
    ticker_evidence = context_item.get("ticker_evidence") if isinstance(context_item, dict) else {}
    if not isinstance(opportunity, dict):
        opportunity = {}
    if not isinstance(ticker_evidence, dict):
        ticker_evidence = {}
    else:
        ticker_evidence = dict(ticker_evidence)
    stock_summary = context_item.get("stock_summary") if isinstance(context_item, dict) else {}
    if isinstance(stock_summary, dict) and stock_summary:
        ticker_evidence.setdefault("stock_summary", stock_summary)
    signal_model = _trading_agent_signal_model(opportunity, controls)

    with st.container(border=True):
        header_cols = st.columns([3.7, 1.0, 1.0, 1.1])
        identity = ticker or "n/a"
        if company_name:
            identity = f"{identity} · {company_name}"
        header_cols[0].markdown(f"### {identity}")
        header_cols[1].markdown(f"**{direction}**")
        header_cols[2].markdown(f"**{confidence}**")
        header_cols[3].caption(horizon or "Horizon n/a")

        setup = _trading_agent_text(candidate.get("setup"))
        if setup:
            st.markdown(f"**Setup:** {setup}")

        signal_cols = st.columns([1.05, 1.35, 1.2])
        with signal_cols[0]:
            st.markdown("**Market signal rank**")
            st.metric(
                "Relative scanner rank",
                str(signal_model.get("rank_label") or "n/a"),
                label_visibility="collapsed",
            )
            st.caption("0-100 relative rank, not expected return or conviction.")
        with signal_cols[1]:
            st.markdown("**Signal breakdown**")
            for component in to_list(signal_model.get("score_components")):
                if not isinstance(component, dict):
                    continue
                _render_trading_agent_signal_bar(
                    _trading_agent_text(component.get("label")),
                    int(component.get("value") or 0),
                    _trading_agent_text(component.get("display")) or "n/a",
                )
        with signal_cols[2]:
            st.markdown("**Price action**")
            _render_trading_agent_sparkline(
                to_list(signal_model.get("sparkline")),
                key=f"trading_agent_sparkline_{idx}_{ticker or 'na'}",
            )

        price_cols = st.columns(4)
        price_cols[0].metric("1D move", _trading_agent_pct(signal_model.get("daily_move")))
        price_cols[1].metric(
            f"{_trading_agent_text(signal_model.get('selected_horizon_label'))} return",
            _trading_agent_pct(signal_model.get("horizon_return")),
        )
        price_cols[2].metric("Momentum pace", _trading_agent_text(signal_model.get("momentum_label")) or "n/a")
        price_cols[3].metric("Trend quality", _trading_agent_text(signal_model.get("trend_label")) or "n/a")
        pattern = _trading_agent_text(signal_model.get("market_pattern"))
        opportunity_label = _trading_agent_text(signal_model.get("opportunity_label"))
        freshness = _trading_agent_text(signal_model.get("freshness"))
        captions = [item for item in [f"Market pattern: {pattern}" if pattern else "", opportunity_label, freshness] if item]
        if captions:
            st.caption(" | ".join(captions))

        hypothesis = _trading_agent_text(candidate.get("hypothesis"))
        if hypothesis:
            st.write(hypothesis)

        evidence = [_trading_agent_text(item) for item in to_list(candidate.get("evidence")) if _trading_agent_text(item)]
        invalidation = _trading_agent_text(candidate.get("invalidation"))
        risks = [_trading_agent_text(item) for item in to_list(candidate.get("tail_risks")) if _trading_agent_text(item)]
        _render_trading_agent_evidence_checklist(
            evidence=evidence,
            invalidation=invalidation,
            risks=risks,
            ticker_evidence=ticker_evidence,
            aql_agent=aql_agent,
        )
        news_lines = [
            _trading_agent_text(item)
            for item in to_list(ticker_evidence.get("news_summary_lines"))
            if _trading_agent_text(item)
        ]
        attention_context = ticker_evidence.get("attention_context") if isinstance(ticker_evidence, dict) else {}
        attention_lines = []
        if isinstance(attention_context, dict):
            attention_lines = [
                _trading_agent_text(attention_context.get(key))
                for key in ["llm_headline", "llm_why_now", "llm_management_signal"]
                if _trading_agent_text(attention_context.get(key))
            ]

        body_cols = st.columns([1.15, 1.0])
        with body_cols[0]:
            if evidence:
                st.markdown("**Evidence**")
                st.markdown("\n".join(f"- {item}" for item in evidence[:5]))
            if invalidation:
                st.markdown(f"**Invalidation**  \n{invalidation}")
        with body_cols[1]:
            if risks:
                st.markdown("**Tail Risks**")
                st.markdown("\n".join(f"- {item}" for item in risks[:5]))
            if attention_lines or news_lines:
                with st.expander("Ticker context", expanded=False):
                    for item in attention_lines[:3]:
                        st.markdown(f"- {item}")
                    for item in news_lines[:3]:
                        st.markdown(f"- {item}")
            missing = [_trading_agent_text(item) for item in to_list(signal_model.get("missing")) if _trading_agent_text(item)]
            if missing:
                with st.expander("Missing signal fields", expanded=False):
                    st.markdown("\n".join(f"- {item}" for item in missing[:6]))
            with st.expander("Raw signal values", expanded=False):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Field": "Market signal rank",
                                "Value": signal_model.get("rank_label"),
                            },
                            {
                                "Field": f"{signal_model.get('selected_horizon_label')} return",
                                "Value": _trading_agent_pct(signal_model.get("horizon_return")),
                            },
                            {"Field": "1D move", "Value": _trading_agent_pct(signal_model.get("daily_move"))},
                            {
                                "Field": "Momentum pace",
                                "Value": "n/a"
                                if signal_model.get("momentum_roc") is None
                                else f"{float(signal_model.get('momentum_roc')):+.2f}",
                            },
                            {
                                "Field": "Trend gap",
                                "Value": "n/a"
                                if signal_model.get("trend_gap") is None
                                else f"{float(signal_model.get('trend_gap')):.2f}",
                            },
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

        candidate_id = _trading_agent_text(candidate.get("candidate_id"))
        latest_action = _trading_agent_text((action_state or {}).get("action"))
        latest_status = _trading_agent_text((action_state or {}).get("status"))
        action_label = ""
        if latest_action == "place_requested":
            action_label = "Place requested"
        elif latest_action == "rejected":
            action_label = "Rejected"
        if action_label:
            st.caption(f"Decision: {action_label}" + (f" ({latest_status})" if latest_status else ""))

        action_cols = st.columns([1.0, 1.0, 2.4])
        action_disabled = bool(latest_action in {"place_requested", "rejected"}) or not candidate_id
        with action_cols[0]:
            if st.button(
                "Place",
                key=f"trading_agent_place_{idx}_{candidate_id or ticker}",
                use_container_width=True,
                disabled=action_disabled,
                help="Logs a place decision for admin review. No broker order is submitted.",
            ):
                user = _current_user_context()
                ok, msg = record_trading_agent_action(
                    candidate=dict(candidate),
                    action="place",
                    requested_by=str(getattr(user, "user_id", "") or ""),
                    requested_email=str(getattr(user, "email", "") or ""),
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
        with action_cols[1]:
            if st.button(
                "Reject",
                key=f"trading_agent_reject_{idx}_{candidate_id or ticker}",
                use_container_width=True,
                disabled=action_disabled,
            ):
                user = _current_user_context()
                ok, msg = record_trading_agent_action(
                    candidate=dict(candidate),
                    action="reject",
                    requested_by=str(getattr(user, "user_id", "") or ""),
                    requested_email=str(getattr(user, "email", "") or ""),
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
        with action_cols[2]:
            if ticker and st.button(
                "Open Stock Investigator",
                key=f"trading_agent_open_{idx}_{ticker}_{candidate_id or 'na'}",
                use_container_width=True,
            ):
                _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": ticker})


def _trading_agent_json_value(value: object, fallback: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    text = _trading_agent_text(value)
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback


def _load_latest_trading_agent_output() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, object | None]:
    try:
        runs, metadata = load_latest_dataset_frame("trading_agent_runs")
    except Exception:
        runs, metadata = pd.DataFrame(), None
    try:
        candidates, _ = load_latest_dataset_frame("trading_agent_candidates")
    except Exception:
        candidates = pd.DataFrame()
    actions = trading_agent_actions_table(limit=500)
    return (
        runs.copy() if isinstance(runs, pd.DataFrame) else pd.DataFrame(),
        candidates.copy() if isinstance(candidates, pd.DataFrame) else pd.DataFrame(),
        actions.copy() if isinstance(actions, pd.DataFrame) else pd.DataFrame(),
        metadata,
    )


def _latest_trading_agent_action_map(actions: pd.DataFrame) -> dict[str, dict[str, object]]:
    if not isinstance(actions, pd.DataFrame) or actions.empty or "candidate_id" not in actions.columns:
        return {}
    out = actions.copy()
    if "created_at_utc" in out.columns:
        out["_created_at_utc"] = pd.to_datetime(out["created_at_utc"], utc=True, errors="coerce")
        out = out.sort_values("_created_at_utc", ascending=False, na_position="last")
    latest: dict[str, dict[str, object]] = {}
    for _, row in out.iterrows():
        candidate_id = _trading_agent_text(row.get("candidate_id"))
        if candidate_id and candidate_id not in latest:
            latest[candidate_id] = row.to_dict()
    return latest


def _trading_agent_candidate_from_row(row: pd.Series) -> dict[str, object]:
    candidate = _trading_agent_json_value(row.get("candidate_json"), {})
    if not isinstance(candidate, dict):
        candidate = {}
    candidate = dict(candidate)
    candidate.update(
        {
            "candidate_id": _trading_agent_text(row.get("candidate_id")),
            "trading_agent_run_id": _trading_agent_text(row.get("trading_agent_run_id")),
            "run_id": _trading_agent_text(row.get("run_id")),
            "horizon_key": _trading_agent_text(row.get("horizon_key")),
            "horizon_label": _trading_agent_text(row.get("horizon_label")),
            "selected_horizon_col": _trading_agent_text(row.get("selected_horizon_col")),
            "ticker": _trading_agent_text(candidate.get("ticker") or row.get("ticker")).upper(),
            "direction": _trading_agent_text(candidate.get("direction") or row.get("direction")),
            "setup": _trading_agent_text(candidate.get("setup") or row.get("setup")),
            "hypothesis": _trading_agent_text(candidate.get("hypothesis") or row.get("hypothesis")),
            "invalidation": _trading_agent_text(candidate.get("invalidation") or row.get("invalidation")),
            "suggested_horizon": _trading_agent_text(candidate.get("suggested_horizon") or row.get("suggested_horizon")),
            "confidence": _trading_agent_text(candidate.get("confidence") or row.get("confidence") or "low"),
        }
    )
    evidence = candidate.get("evidence")
    if not to_list(evidence):
        evidence = _trading_agent_json_value(row.get("evidence_json"), [])
    candidate["evidence"] = [_trading_agent_text(item) for item in to_list(evidence) if _trading_agent_text(item)]
    risks = candidate.get("tail_risks")
    if not to_list(risks):
        risks = _trading_agent_json_value(row.get("tail_risks_json"), [])
    candidate["tail_risks"] = [_trading_agent_text(item) for item in to_list(risks) if _trading_agent_text(item)]
    return candidate


def _trading_agent_context_from_run(row: pd.Series) -> dict[str, object]:
    context = _trading_agent_json_value(row.get("context_json"), {})
    return context if isinstance(context, dict) else {}


def _trading_agent_result_from_run(row: pd.Series) -> dict[str, object]:
    result = _trading_agent_json_value(row.get("result_json"), {})
    if not isinstance(result, dict):
        result = {}
    result.setdefault("status", _trading_agent_text(row.get("status")))
    result.setdefault("regime_read", _trading_agent_text(row.get("regime_read")))
    result.setdefault("portfolio_posture", _trading_agent_text(row.get("portfolio_posture")))
    result.setdefault("data_gaps", _trading_agent_json_value(row.get("data_gaps_json"), []))
    aql_agent = _trading_agent_json_value(row.get("aql_agent_json"), {})
    if isinstance(aql_agent, dict):
        result.setdefault("aql_agent", aql_agent)
    return result


def _trading_agent_horizon_sort_key(value: object) -> int:
    order = {"1w": 0, "1m": 1, "3m": 2, "1y": 3, "5y": 4}
    return order.get(_trading_agent_text(value), 99)


def _render_trading_agent_section(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    if not _current_user_is_admin():
        st.error("Only admin users can access this section.")
        return

    st.title(TRADING_AGENT_SECTION)
    st.caption(
        "Admin-only research log. Place and Reject are audit decisions; Place is stored for future Alpaca handoff and does not submit an order."
    )

    runs, candidates_frame, actions, metadata = _load_latest_trading_agent_output()
    if metadata is not None:
        st.caption(
            f"Latest Trading Agent snapshot: {_trading_agent_text(getattr(metadata, 'asof_time_utc', ''))} "
            f"run `{_trading_agent_text(getattr(metadata, 'dataset_version_id', ''))}`"
        )

    if runs.empty or candidates_frame.empty:
        st.info("No materialized Trading Agent candidates are available. Trading Agent Build is managed from Admin > Pipeline Jobs.")
        return

    if "generated_at_utc" in runs.columns:
        runs = runs.assign(_generated_at=pd.to_datetime(runs["generated_at_utc"], utc=True, errors="coerce"))
        latest_generated = runs["_generated_at"].max()
        if pd.notna(latest_generated):
            runs = runs[runs["_generated_at"].eq(latest_generated)].copy()
    if "run_id" in runs.columns and not runs.empty:
        latest_run_id = _trading_agent_text(runs.iloc[0].get("run_id"))
        if latest_run_id and "run_id" in candidates_frame.columns:
            candidates_frame = candidates_frame[candidates_frame["run_id"].astype(str).eq(latest_run_id)].copy()

    horizon_values = sorted(
        {
            _trading_agent_text(value)
            for value in runs.get("horizon_key", pd.Series(dtype=str)).tolist()
            if _trading_agent_text(value)
        },
        key=_trading_agent_horizon_sort_key,
    )
    if not horizon_values:
        st.info("Trading Agent run metadata did not include horizon keys.")
        return
    horizon_labels = {
        _trading_agent_text(row.get("horizon_key")): _trading_agent_text(row.get("horizon_label")) or _trading_agent_text(row.get("horizon_key"))
        for _, row in runs.iterrows()
    }
    selected_horizon = st.segmented_control(
        "Horizon",
        horizon_values,
        default=horizon_values[0],
        format_func=lambda key: horizon_labels.get(str(key), str(key)),
        key="trading_agent_materialized_horizon",
        width="stretch",
    )
    selected_horizon = _trading_agent_text(selected_horizon) or horizon_values[0]

    run_rows = runs[runs["horizon_key"].astype(str).eq(selected_horizon)].copy() if "horizon_key" in runs.columns else runs.head(1)
    if run_rows.empty:
        st.info("No Trading Agent run was found for this horizon.")
        return
    run_row = run_rows.iloc[0]
    result = _trading_agent_result_from_run(run_row)
    context = _trading_agent_context_from_run(run_row)
    context_lookup = _trading_agent_context_lookup(context)
    controls_context = dict(context.get("controls") or {})
    controls_context.setdefault("selected_horizon_label", _trading_agent_text(run_row.get("horizon_label")))
    controls_context.setdefault("selected_horizon_col", _trading_agent_text(run_row.get("selected_horizon_col")))
    aql_agent = result.get("aql_agent") if isinstance(result.get("aql_agent"), dict) else {}

    status = _trading_agent_text(result.get("status"))
    if status != "ok":
        st.info(_trading_agent_text(result.get("error")) or "Trading Agent output is unavailable for this horizon.")
        gaps = [_trading_agent_text(item) for item in to_list(result.get("data_gaps")) if _trading_agent_text(item)]
        if gaps:
            st.markdown("\n".join(f"- {gap}" for gap in gaps[:6]))
        return

    st.subheader("Regime Read")
    st.write(_trading_agent_text(result.get("regime_read")))
    posture = _trading_agent_text(result.get("portfolio_posture"))
    if posture:
        st.caption(posture)

    horizon_candidates = candidates_frame.copy()
    if "horizon_key" in horizon_candidates.columns:
        horizon_candidates = horizon_candidates[horizon_candidates["horizon_key"].astype(str).eq(selected_horizon)].copy()
    if "rank" in horizon_candidates.columns:
        horizon_candidates["_rank"] = pd.to_numeric(horizon_candidates["rank"], errors="coerce")
        horizon_candidates = horizon_candidates.sort_values("_rank", ascending=True, na_position="last")

    if horizon_candidates.empty:
        st.info("No watchlist candidates cleared the evidence bar for this horizon.")
        return

    action_map = _latest_trading_agent_action_map(actions)
    candidates = [_trading_agent_candidate_from_row(row) for _, row in horizon_candidates.iterrows()]
    long_watch_candidates = [
        item
        for item in candidates
        if _trading_agent_text(item.get("direction")).lower() not in {"short", "avoid"}
    ]
    short_candidates = [
        item
        for item in candidates
        if _trading_agent_text(item.get("direction")).lower() in {"short", "avoid"}
    ]

    if long_watch_candidates:
        st.subheader("Long / Watch Setups")
        for idx, candidate in enumerate(long_watch_candidates):
            ticker = _trading_agent_text(candidate.get("ticker")).upper()
            candidate_id = _trading_agent_text(candidate.get("candidate_id"))
            _render_trading_agent_candidate_card(
                candidate,
                context_item=context_lookup.get(ticker, {}),
                controls=controls_context,
                aql_agent=aql_agent,
                idx=idx,
                action_state=action_map.get(candidate_id, {}),
            )

    if short_candidates:
        st.subheader("Short / Avoid Setups")
        offset = len(long_watch_candidates)
        for idx, candidate in enumerate(short_candidates, start=offset):
            ticker = _trading_agent_text(candidate.get("ticker")).upper()
            candidate_id = _trading_agent_text(candidate.get("candidate_id"))
            _render_trading_agent_candidate_card(
                candidate,
                context_item=context_lookup.get(ticker, {}),
                controls=controls_context,
                aql_agent=aql_agent,
                idx=idx,
                action_state=action_map.get(candidate_id, {}),
            )

    if context:
        _render_trading_agent_source_summaries(context, result)

    gaps = [_trading_agent_text(item) for item in to_list(result.get("data_gaps")) if _trading_agent_text(item)]
    if gaps:
        with st.expander("Data gaps", expanded=False):
            st.markdown("\n".join(f"- {gap}" for gap in gaps[:6]))

    with st.expander("Decision Log", expanded=False):
        if actions.empty:
            st.info("No Trading Agent decisions have been logged yet.")
        else:
            display_actions = actions.copy()
            show_cols = [
                column
                for column in [
                    "created_at_utc",
                    "ticker",
                    "horizon_key",
                    "action",
                    "execution_mode",
                    "status",
                    "broker",
                    "broker_order_id",
                    "requested_email",
                    "candidate_id",
                ]
                if column in display_actions.columns
            ]
            st.dataframe(display_actions[show_cols], use_container_width=True, hide_index=True)


_ensure_app_shell_styles()

# Detect logo click (?nav=home) and queue navigation to Home.
if _query_param_value("nav") == "home":
    try:
        del st.query_params["nav"]
    except Exception:
        pass
    st.session_state["_pending_workspace_section"] = "Home"

# Auth action links are public forms, not workspace entry points. Clear existing
# sessions before cookie restore so reset/invite URLs cannot inherit a login.
_force_logged_out_for_auth_action()

# Run cookie maintenance and attempt session restore before routing decisions.
_handle_auth_cookie_maintenance()
_try_restore_session_from_cookie()
if not _auth_enabled():
    st.session_state["_ui_authenticated"] = True
    st.session_state.setdefault("_ui_auth_mode", "disabled")

app_track = (os.getenv("APP_TRACK") or "local").strip().lower()

with _timed("load_config"):
    cfg = load_config()

api: AlpacaAPI | None = None
account: dict[str, object] = {}
startup_error_summary: str | None = None
startup_error_details: str | None = None
startup_setup_code: str | None = None

if cfg is None:
    _log_event("config_invalid")
    key_secret_name, secret_secret_name = alpaca_secret_name_settings()
    key_vault_name = (os.getenv("AZURE_KEY_VAULT_NAME") or os.getenv("KEY_VAULT_NAME") or "").strip()
    key_vault_url = (os.getenv("AZURE_KEY_VAULT_URL") or "").strip()
    startup_setup_code = (
        "az login\n"
        "export AZURE_KEY_VAULT_NAME='spectral-nature-kvault'\n"
        f"export APCA_API_KEY_SECRET_NAME='{key_secret_name}'\n"
        f"export APCA_API_SECRET_KEY_SECRET_NAME='{secret_secret_name}'\n"
        "export APCA_API_BASE_URL='https://paper-api.alpaca.markets'\n"
        "./scripts/run_ui_local.sh"
    )
    if not key_vault_name and not key_vault_url:
        startup_error_summary = "Market data credentials are unavailable."
        startup_error_details = (
            "Set AZURE_KEY_VAULT_NAME or AZURE_KEY_VAULT_URL so the app can load Alpaca secrets from Key Vault."
        )
    else:
        startup_error_summary = "Market data credentials are unavailable from Key Vault."
        startup_error_details = (
            f"Expected secrets `{key_secret_name}` and `{secret_secret_name}` in the configured vault."
        )

elif cfg.alpaca_trading_base_url.startswith("hhttps://") or not cfg.alpaca_trading_base_url.startswith(("http://", "https://")):
    _log_event("config_invalid_base_url", base_url=cfg.alpaca_trading_base_url)
    startup_error_summary = (
        "Invalid APCA_API_BASE_URL value. Expected the paper or live trading endpoint."
    )
    startup_setup_code = "APCA_API_BASE_URL=https://api.alpaca.markets"

else:
    with _timed("create_api_client"):
        api = _make_api(cfg)

# Determine where the user wants to go before the auth gate fires.
_routing_pending = _normalize_workspace_section(st.session_state.get("_pending_workspace_section", ""))
_routing_current = _normalize_workspace_section(st.session_state.get("workspace_section", ""))
_routing_target = _routing_pending or _routing_current or "Home"
_show_login_forced = bool(st.session_state.get("_show_login_form", False))
_is_authenticated = bool(st.session_state.get("_ui_authenticated"))

# Public home path: unauthenticated visitor heading to Home (or no destination).
if (
    not _is_authenticated
    and _auth_enabled()
    and not _show_login_forced
    and not _auth_action_query_param_present()
    and _routing_target == "Home"
):
    with st.sidebar:
        _render_sidebar_brand_panel()
        _render_sidebar_editorial_links(placement="sidebar_brand")
        _pub_replay_today = pd.Timestamp.now(tz="UTC").normalize().date()
        _pub_replay_selected = st.date_input(
            "Snapshot date",
            value=_pub_replay_today,
            max_value=_pub_replay_today,
            key="homepage_replay_date",
            label_visibility="collapsed",
        )
        if _pub_replay_selected is not None and _pub_replay_selected < _pub_replay_today:
            st.session_state["_homepage_replay_date"] = str(_pub_replay_selected)
        else:
            st.session_state.pop("_homepage_replay_date", None)
        if st.button("Sign in", type="primary", use_container_width=True, key="public_home_signin"):
            st.session_state["_show_login_form"] = True
            st.rerun()
    _render_homepage_v2(cfg, None, force_data_refresh=False)
    st.stop()

# Gate: unauthenticated visitor trying to reach a specific section.
if not _is_authenticated and _auth_enabled():
    if _routing_target and _routing_target != "Home":
        st.session_state["_pre_auth_destination"] = _routing_target
    _enforce_login_gate()
    # _enforce_login_gate calls st.stop() if login not yet completed.

cache_disabled = (os.getenv("APP_DISABLE_CACHE") or "").strip().lower() in {"1", "true", "yes", "on"}
force_refresh_default_raw = os.getenv("APP_FORCE_DATA_REFRESH_DEFAULT")
if force_refresh_default_raw is None or not str(force_refresh_default_raw).strip():
    force_refresh_default = False
else:
    force_refresh_default = str(force_refresh_default_raw).strip().lower() in {"1", "true", "yes", "on"}
source_refresh_flags = dict(st.session_state.get("_source_force_refresh", {}))
st.session_state["_source_force_refresh"] = source_refresh_flags
_consume_cross_page_inspector_query_params()
section_options = _section_options()

# Track previous section for the ← Back button before resolving any pending change.
_section_before_transition = _normalize_workspace_section(st.session_state.get("workspace_section"))
pending_workspace_section = _normalize_workspace_section(st.session_state.pop("_pending_workspace_section", ""))
current_workspace_section = _normalize_workspace_section(st.session_state.get("workspace_section"))
if pending_workspace_section in section_options:
    if pending_workspace_section != current_workspace_section:
        st.session_state["_prev_workspace_section"] = _section_before_transition
    st.session_state["workspace_section"] = pending_workspace_section
elif current_workspace_section in section_options:
    st.session_state["workspace_section"] = current_workspace_section
elif st.session_state.get("workspace_section") not in section_options:
    st.session_state["workspace_section"] = section_options[0]

current_user = _current_user_context()

with st.sidebar:
    _render_sidebar_brand_panel()
    _render_sidebar_editorial_links(placement="sidebar_brand")
    # Snapshot date picker — only meaningful on Home, but always rendered
    # in the sidebar to keep the key stable across section changes.
    _replay_today = pd.Timestamp.now(tz="UTC").normalize().date()
    _replay_selected = st.date_input(
        "Snapshot date",
        value=_replay_today,
        max_value=_replay_today,
        key="homepage_replay_date",
        label_visibility="collapsed",
    )
    if _replay_selected is not None and _replay_selected < _replay_today:
        st.session_state["_homepage_replay_date"] = str(_replay_selected)
    else:
        st.session_state.pop("_homepage_replay_date", None)
    _nav_current = _normalize_workspace_section(st.session_state.get("workspace_section", section_options[0]))
    st.markdown('<p class="sn-nav-label">Navigate</p>', unsafe_allow_html=True)
    for _nav_opt in section_options:
        _nav_slug = _nav_opt.lower().replace(" ", "_").replace("-", "_")
        _nav_key = f"sn_nav_active_{_nav_slug}" if _nav_opt == _nav_current else f"sn_nav_{_nav_slug}"
        if st.button(_nav_opt, key=_nav_key, use_container_width=True):
            st.session_state["_pending_workspace_section"] = _nav_opt
            st.rerun()
    section = _nav_current

    with st.expander("Workspace Status", expanded=False):
        if pipeline_store_configured():
            if _presentation_layer_only():
                st.caption("Data mode: Presentation-only pipeline snapshots")
            else:
                st.caption("Data mode: Pipeline metadata + parquet snapshots")
        else:
            st.caption("Data mode: Snapshot store unavailable")
        st.caption(f"Cache: {'disabled' if cache_disabled else 'enabled'}")
        st.caption(f"Inline force refresh: {'disabled' if _presentation_layer_only() else ('on' if force_refresh_default else 'off')}")
        st.caption(f"CSV cache: {cache_data_root()}")
        st.caption(f"Cache policy: {cache_policy_path()}")
        if st.button("Logout", key="dashboard_logout", use_container_width=True):
            if st.session_state.get("_ui_auth_mode") == "database":
                auth_service.record_access_event(
                    event_type="logout",
                    event_category="usage",
                    user=current_user,
                    session_token=str(st.session_state.get("_ui_auth_session_id") or ""),
                    ip_address=_request_ip_address(),
                    user_agent=_request_user_agent(),
                )
                auth_service.logout_session(str(st.session_state.get("_ui_auth_session_id") or ""))
            else:
                _invalidate_auth_session(st.session_state.get("_ui_auth_session_id"))
            _clear_local_auth_state()
            st.session_state["_ui_clear_auth_cookie"] = True
            st.rerun()
        sidebar_connection = st.empty()
        sidebar_status = st.empty()
        sidebar_buying_power = st.empty()

_log_event("ui_sidebar_ready")
_record_workspace_section_view(section_name=section, current_user=current_user, app_track=app_track)

sidebar_connection.metric(
    "Live Data",
    "Snapshot Mode" if _presentation_layer_only() else ("Connected" if api is not None else "Unavailable"),
)
if current_user is not None and not current_user.can_view_full_portfolio:
    sidebar_status.metric("Access", "Investor")
    sidebar_buying_power.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
elif _presentation_layer_only():
    sidebar_status.metric("Account Status", "SNAPSHOT MODE")
    sidebar_buying_power.metric("Buying Power", "Unavailable")
else:
    sidebar_status.metric("Account Status", "NOT LOADED" if api is not None else "UNAVAILABLE")
    sidebar_buying_power.metric("Buying Power", "Not loaded" if api is not None else "Unavailable")

if (
    not _presentation_layer_only()
    and api is not None
    and not (current_user is not None and not current_user.can_view_full_portfolio)
):
    try:
        sidebar_account = _load_account_cached(cfg, force_refresh=False)
    except AlpacaAPIError as exc:
        _log_event("sidebar_account_failed", error=str(exc)[:200])
        sidebar_status.metric("Account Status", "ERROR")
        sidebar_buying_power.metric("Buying Power", "Unavailable")
    except Exception as exc:
        _log_event("sidebar_account_failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        sidebar_status.metric("Account Status", "ERROR")
        sidebar_buying_power.metric("Buying Power", "Unavailable")
    else:
        sidebar_status.metric("Account Status", str(sidebar_account.get("status", "unknown")).upper())
        sidebar_buying_power.metric("Buying Power", f"${_to_float(sidebar_account, 'buying_power'):,.2f}")

if startup_error_summary:
    _render_connection_issue(
        startup_error_summary,
        details=startup_error_details,
        setup_code=startup_setup_code,
    )

force_data_refresh = force_refresh_default

if section == "Home":
    _render_homepage_v2(
        cfg,
        api,
        force_data_refresh=force_data_refresh,
    )

elif section == HOME_EXP_SECTION:
    _render_homepage_exp(
        cfg,
        api,
        force_data_refresh=force_data_refresh,
    )

elif section == AGENTIC_OMNIBAR_SECTION:
    _render_agentic_omnibar_section(
        cfg,
        force_data_refresh=force_data_refresh,
    )

elif section == PORTFOLIO_SECTION:
    header_cols = st.columns([3.2, 1.6])
    with header_cols[0]:
        st.title(PORTFOLIO_SECTION)
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption(f"Viewing your {_current_user_share_fraction() * 100:.2f}% economic share of the master portfolio.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="portfolio_overview_period")
    if not _has_live_api(api, f"{PORTFOLIO_SECTION} requires a working live account connection."):
        st.info("Restore the live account connection to load positions, portfolio history, and benchmark comparisons.")
    else:
        try:
            with st.spinner("Loading account summary..."):
                with _timed("get_account"):
                    account = _load_account_cached(cfg, force_refresh=force_data_refresh)
            if current_user is not None and not current_user.can_view_full_portfolio:
                sidebar_status.metric("Access", "Investor")
                sidebar_buying_power.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
            else:
                sidebar_status.metric("Account Status", str(account.get("status", "unknown")).upper())
                sidebar_buying_power.metric("Buying Power", f"${_to_float(account, 'buying_power'):,.2f}")
        except AlpacaAPIError as exc:
            _log_event("get_account_failed", error=str(exc)[:200])
            st.warning(f"Could not load account summary: {exc}")
            account = {}
            if current_user is not None and not current_user.can_view_full_portfolio:
                sidebar_status.metric("Access", "ERROR")
                sidebar_buying_power.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
            else:
                sidebar_status.metric("Account Status", "ERROR")
                sidebar_buying_power.metric("Buying Power", "Unavailable")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Equity", f"${_to_float(account, 'equity'):,.2f}")
        col2.metric("Cash", f"${_to_float(account, 'cash'):,.2f}")
        col3.metric("Portfolio Value", f"${_to_float(account, 'portfolio_value'):,.2f}")
        if current_user is not None and not current_user.can_view_full_portfolio:
            col4.metric("Portfolio Share", f"{_current_user_share_fraction() * 100:.2f}%")
        else:
            col4.metric("Daytrade Count", str(account.get("daytrade_count", "0")))

        try:
            with st.spinner("Loading positions..."):
                with _timed("get_positions"):
                    positions = _load_positions_cached(cfg, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("get_positions_failed", error=str(exc)[:200])
            st.warning(f"Could not load positions: {exc}")
            positions = pd.DataFrame()

        left, right = st.columns([1.2, 1.8])

        with left:
            st.subheader("Current Positions")
            if positions.empty:
                st.info("No open positions.")
            else:
                show_cols = [
                    c
                    for c in [
                        "symbol",
                        "effective_qty",
                        "qty",
                        "avg_entry_price",
                        "current_price",
                        "market_value",
                        "unrealized_pl",
                        "unrealized_plpc",
                        "change_today",
                    ]
                    if c in positions.columns
                ]
                st.dataframe(positions[show_cols], use_container_width=True, hide_index=True)

                if "market_value" in positions.columns and "symbol" in positions.columns:
                    pie = px.pie(
                        positions,
                        values="market_value",
                        names="symbol",
                        title="Position Weights",
                        template="plotly_dark",
                    )
                    st.plotly_chart(pie, use_container_width=True)

        with right:
            st.subheader("Portfolio vs Benchmarks")
            try:
                with st.spinner("Loading portfolio history and benchmarks..."):
                    with _timed("build_timeseries", period=period):
                        raw = _load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                if current_user is not None and not current_user.can_view_full_portfolio and not raw.empty and "portfolio" in raw.columns:
                    dollar_fig = px.line(
                        raw,
                        x="timestamp",
                        y="portfolio",
                        template="plotly_dark",
                        title="Your Dollar Equity",
                    )
                    st.plotly_chart(dollar_fig, use_container_width=True)
                norm = normalize_timeseries_view(raw)
                if norm.empty:
                    st.info("No portfolio history available.")
                else:
                    fig = build_portfolio_vs_benchmarks_fig(norm)
                    st.plotly_chart(fig, use_container_width=True)
            except AlpacaAPIError as exc:
                _log_event("build_timeseries_failed", error=str(exc)[:200])
                st.warning(f"Could not load portfolio history: {exc}")

        st.subheader("RoC Momentum (Holdings Snapshot)")
        if positions.empty or "symbol" not in positions.columns:
            st.info("No holdings available for momentum view.")
        else:
            symbols = positions["symbol"].astype(str).str.upper().tolist()[:12]
            try:
                with _timed("compute_holding_roc", symbols=len(symbols)):
                    roc = _load_holding_roc_cached(cfg, symbols, force_refresh=force_data_refresh)
                if roc.empty:
                    st.info("Not enough bar history to compute momentum.")
                else:
                    long = roc.melt(id_vars=["symbol"], var_name="metric", value_name="value")
                    fig = px.bar(
                        long,
                        x="symbol",
                        y="value",
                        color="metric",
                        barmode="group",
                        template="plotly_dark",
                        title="Slope Rate-of-Change by Holding",
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(roc, use_container_width=True, hide_index=True)
            except AlpacaAPIError as exc:
                _log_event("compute_holding_roc_failed", error=str(exc)[:200])
                st.warning(f"Could not compute momentum: {exc}")

elif section == PORTFOLIO_PERFORMANCE_SECTION:
    header_cols = st.columns([3.2, 1.6])
    with header_cols[0]:
        st.title(PORTFOLIO_PERFORMANCE_SECTION)
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption("Return-based metrics match the master portfolio while your ownership share remains fixed.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="performance_period")
    if not _has_live_api(
        api,
        f"{PORTFOLIO_PERFORMANCE_SECTION} requires a working portfolio history snapshot or live account connection.",
        allow_pipeline=True,
    ):
        st.info("Restore the live account connection or portfolio history snapshot to compute portfolio and benchmark performance.")
    else:
        try:
            with st.spinner("Loading performance data..."):
                with _timed("load_portfolio_timeseries", period=period):
                    raw_timeseries = _load_timeseries_cached(cfg, period, force_refresh=force_data_refresh)
                with _timed("load_portfolio_performance", period=period):
                    perf = _load_portfolio_performance_cached(cfg, period, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("load_portfolio_performance_failed", error=str(exc)[:200])
            st.error(f"Could not load performance data: {exc}")
            st.stop()

        if perf.empty:
            st.info("No performance data available.")
            st.stop()
        if current_user is not None and not current_user.can_view_full_portfolio and not raw_timeseries.empty and "portfolio" in raw_timeseries.columns:
            dollar_fig = px.line(
                raw_timeseries,
                x="timestamp",
                y="portfolio",
                template="plotly_dark",
                title="Your Dollar Equity Over Time",
            )
            st.plotly_chart(dollar_fig, use_container_width=True)
        st.dataframe(perf, use_container_width=True, hide_index=True)

        metric = st.selectbox(
            "Metric",
            ["annual_return", "sharpe_ratio", "beta_vs_spy", "alpha_vs_spy", "max_drawdown"],
        )
        st.plotly_chart(build_metric_bar(perf, metric), use_container_width=True)

elif section == ADMIN_SECTION:
    _render_access_admin_section()

elif section == TRADING_AGENT_SECTION:
    _render_trading_agent_section(
        cfg,
        force_data_refresh=force_data_refresh,
    )

elif section == BROAD_ECONOMY_SECTION:
    st.title(BROAD_ECONOMY_SECTION)
    st.caption(
        "Economic indicators sourced from FRED. The dashboard now defaults to the fresher per-series path, "
        "with derived YoY and stationarized views rebuilt from the underlying observations."
    )

    fred_api_key = load_fred_api_key()
    if not fred_api_key:
        st.info(
            "The macro dashboard looks for Azure Key Vault secret `Fred` in `spectral-nature-kvault` first, "
            "then falls back to `FRED_API_KEY`."
        )
        st.code(
            "export AZURE_KEY_VAULT_NAME='spectral-nature-kvault'\n"
            "# or fallback:\n"
            "export FRED_API_KEY='...'\n"
            "./scripts/run_ui_local.sh",
            language="bash",
        )
    else:
        lookback_years = st.slider("Lookback (years)", 3, 20, 10, step=1)
        show_stationary_overlay = True
        fred_cache_key = f"{_fred_cache_scope(fred_api_key)}__{lookback_years}y"
        fred_cache_ready = cache_bundle_exists(
            "fred_dashboard",
            fred_cache_key,
            required_files=["summary.csv", "observations.csv"],
        )
        load_fred_now = st.button(
            "Load FRED Data",
            type="primary" if not fred_cache_ready else "secondary",
            help="Cold FRED loads can take a while on remote sessions. Cached data loads immediately.",
        )
        allow_fred_defer = (not pipeline_store_configured()) and (not cache_disabled)
        if allow_fred_defer and not fred_cache_ready and not load_fred_now and not force_data_refresh:
            st.info(
                "FRED downloads are deferred until requested. This prevents the app from appearing to hang on "
                "a cold remote load. Click `Load FRED Data` once, or use cached data on the next visit."
            )
            st.stop()
        try:
            with st.spinner("Loading FRED macro dashboard..."):
                with _timed("load_fred_dashboard", years=lookback_years):
                    dashboard = _load_fred_dashboard_cached(
                        fred_api_key,
                        lookback_years,
                        force_refresh=(force_data_refresh or load_fred_now),
                    )
        except FredAPIError as exc:
            _log_event("load_fred_dashboard_failed", error=str(exc)[:200], years=lookback_years)
            st.error(f"Could not load FRED data: {exc}")
            st.stop()

        summary = dashboard["summary"].copy()
        category_blurbs = dashboard["category_blurbs"]
        specs_by_category = dashboard["specs_by_category"]
        metadata_by_id = dashboard["metadata"]
        series_data = dashboard["series_data"]
        series_index = dashboard.get("series_index", pd.DataFrame())
        observations = dashboard.get("observations", pd.DataFrame())
        release_index = dashboard.get("release_index", pd.DataFrame())

        if summary.empty:
            st.info("No macro indicators were returned from FRED.")
            st.stop()

        overview = summary.copy()
        overview["latest"] = [
            format_fred_value(value, units)
            for value, units in zip(overview["latest_value"], overview["units_short"])
        ]
        overview["prev"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["prev_delta"], overview["units_short"])
        ]
        overview["yoy"] = [
            format_fred_delta(value, units)
            for value, units in zip(overview["yoy_delta"], overview["units_short"])
        ]
        overview["latest_date"] = pd.to_datetime(overview["latest_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        st.caption(
            "Stationarized change is on by default across Broad Economy. Level series use obs-to-obs percent change; "
            "rate-like series use first differences."
        )
        _render_page_agentic_summary_panel(
            BROAD_ECONOMY_SECTION,
            broad_economy_summary_context(
                overview=overview,
                release_index=release_index,
                lookback_years=lookback_years,
            ),
            key_prefix="broad_economy",
        )

        money_supply_specs = {spec.series_id: spec for spec in specs_by_category.get("Money Supply", [])}
        m2_spec = money_supply_specs.get("M2SL")
        if m2_spec is not None:
            m2_row = summary[summary["series_id"] == m2_spec.series_id]
            m2_meta = metadata_by_id.get(m2_spec.series_id, {})
            m2_frame = series_data.get(m2_spec.series_id, pd.DataFrame())
            m2_latest_value = m2_row["latest_value"].iloc[0] if not m2_row.empty else None
            m2_prev_delta = m2_row["prev_delta"].iloc[0] if not m2_row.empty else None
            m2_yoy_delta = m2_row["yoy_delta"].iloc[0] if not m2_row.empty else None
            m2_latest_date = pd.to_datetime(m2_row["latest_date"].iloc[0], errors="coerce") if not m2_row.empty else pd.NaT

            st.subheader("M2 Money Supply")
            hero_metric_cols = st.columns(4)
            with hero_metric_cols[0]:
                st.metric(
                    "Latest",
                    format_fred_value(m2_latest_value, m2_meta.get("units_short")),
                )
            with hero_metric_cols[1]:
                st.metric(
                    "Obs-to-obs",
                    format_fred_delta(m2_prev_delta, m2_meta.get("units_short")),
                )
            with hero_metric_cols[2]:
                st.metric(
                    "YoY",
                    format_fred_delta(m2_yoy_delta, m2_meta.get("units_short")),
                )
            with hero_metric_cols[3]:
                st.metric("Last Obs", m2_latest_date.strftime("%Y-%m-%d") if pd.notna(m2_latest_date) else "n/a")

            st.plotly_chart(
                build_fred_figure(
                    m2_spec,
                    m2_meta,
                    m2_frame,
                    show_stationary_overlay=show_stationary_overlay,
                ),
                use_container_width=True,
                key="broad-economy-m2-hero-chart",
            )

        category_labels = [*fred_categories(), "Series Explorer"]
        tabs = st.tabs(category_labels)
        for tab, category in zip(tabs[: len(fred_categories())], fred_categories()):
            with tab:
                st.caption(category_blurbs.get(category, ""))
                category_summary = summary[summary["category"] == category].copy()
                if category_summary.empty:
                    st.info("No indicators available for this category.")
                    continue

                table = category_summary.copy()
                table["latest"] = [
                    format_fred_value(value, units)
                    for value, units in zip(table["latest_value"], table["units_short"])
                ]
                table["prev"] = [
                    format_fred_delta(value, units)
                    for value, units in zip(table["prev_delta"], table["units_short"])
                ]
                table["yoy"] = [
                    format_fred_delta(value, units)
                    for value, units in zip(table["yoy_delta"], table["units_short"])
                ]
                table["latest_date"] = pd.to_datetime(table["latest_date"], errors="coerce").dt.strftime("%Y-%m-%d")
                st.dataframe(
                    table[["indicator", "latest", "prev", "yoy", "latest_date"]],
                    use_container_width=True,
                    hide_index=True,
                )

                chart_cols = st.columns(2)
                for idx, spec in enumerate(specs_by_category.get(category, [])):
                    with chart_cols[idx % 2]:
                        row = category_summary[category_summary["series_id"] == spec.series_id]
                        meta = metadata_by_id.get(spec.series_id, {})
                        frame = series_data.get(spec.series_id, pd.DataFrame())
                        latest_value = row["latest_value"].iloc[0] if not row.empty else None
                        prev_delta = row["prev_delta"].iloc[0] if not row.empty else None
                        yoy_delta = row["yoy_delta"].iloc[0] if not row.empty else None
                        latest_date = pd.to_datetime(row["latest_date"].iloc[0], errors="coerce") if not row.empty else pd.NaT

                        st.metric(
                            spec.label,
                            format_fred_value(latest_value, meta.get("units_short")),
                            format_fred_delta(prev_delta, meta.get("units_short")),
                        )
                        date_label = latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else "n/a"
                        frequency_label = str(meta.get("frequency") or meta.get("frequency_short") or "")
                        st.caption(
                            f"{spec.blurb} | YoY: {format_fred_delta(yoy_delta, meta.get('units_short'))} | "
                            f"{frequency_label} | Last obs: {date_label}"
                        )
                        st.plotly_chart(
                            build_fred_figure(spec, meta, frame, show_stationary_overlay=show_stationary_overlay),
                            use_container_width=True,
                            key=f"broad-economy-category-chart-{category}-{spec.series_id}",
                        )

        with tabs[-1]:
            if series_index.empty or observations.empty:
                st.info("Series Explorer becomes available when both loaded series metadata and observations are present.")
            else:
                explorer_series = series_index.copy()
                if "title" not in explorer_series.columns:
                    if "source_title" in explorer_series.columns:
                        explorer_series["title"] = explorer_series["source_title"]
                    else:
                        explorer_series["title"] = explorer_series.get("series_id", pd.Series(dtype=str)).astype(str)
                if "notes" not in explorer_series.columns:
                    explorer_series["notes"] = ""
                if "frequency" not in explorer_series.columns:
                    explorer_series["frequency"] = explorer_series.get(
                        "frequency_short",
                        pd.Series(pd.NA, index=explorer_series.index),
                    )
                if "units" not in explorer_series.columns:
                    explorer_series["units"] = explorer_series.get(
                        "units_short",
                        pd.Series(pd.NA, index=explorer_series.index),
                    )
                if "release_name" not in explorer_series.columns:
                    explorer_series["release_name"] = pd.Series(pd.NA, index=explorer_series.index)

                explorer_cols = st.columns([2, 1])
                with explorer_cols[0]:
                    search_query = st.text_input(
                        "Search loaded series",
                        key="fred_series_search",
                        placeholder="cpi, mortgage, delinquency, money stock",
                    ).strip()
                with explorer_cols[1]:
                    release_options = sorted(explorer_series["release_name"].dropna().astype(str).unique().tolist())
                    selected_release_names = st.multiselect(
                        "Filter releases",
                        release_options,
                        key="fred_release_filter",
                    )

                filtered_series = explorer_series.copy()
                if selected_release_names:
                    filtered_series = filtered_series[filtered_series["release_name"].isin(selected_release_names)]
                if search_query:
                    search_mask = (
                        filtered_series["series_id"].astype(str).str.contains(search_query, case=False, na=False)
                        | filtered_series["title"].astype(str).str.contains(search_query, case=False, na=False)
                        | filtered_series["notes"].astype(str).str.contains(search_query, case=False, na=False)
                    )
                    filtered_series = filtered_series[search_mask]

                filtered_series = filtered_series.sort_values(["release_name", "title", "series_id"], na_position="last")
                st.dataframe(
                    filtered_series[["release_name", "series_id", "title", "frequency", "units"]].head(250),
                    use_container_width=True,
                    hide_index=True,
                )

                if not filtered_series.empty:
                    option_rows = filtered_series[["series_id", "title"]].drop_duplicates().copy()
                    option_labels = option_rows.apply(
                        lambda row: f"{row['series_id']} | {row['title']}",
                        axis=1,
                    ).tolist()
                    label_by_series = {label.split(" | ", 1)[0]: label for label in option_labels}
                    selected_series_id = st.session_state.get("fred_explorer_series_id")
                    if selected_series_id not in label_by_series:
                        selected_series_id = option_rows.iloc[0]["series_id"]
                    selected_label = st.selectbox(
                        "Explorer series",
                        option_labels,
                        index=option_labels.index(label_by_series[selected_series_id]),
                        key="fred_explorer_series_label",
                    )
                    selected_series_id = selected_label.split(" | ", 1)[0]
                    st.session_state["fred_explorer_series_id"] = selected_series_id

                    selected_meta = metadata_by_id.get(selected_series_id, {})
                    selected_frame = observations[observations["series_id"] == selected_series_id][["date", "value"]].copy()
                    selected_spec = FredSeriesSpec(
                        "Explorer",
                        selected_series_id,
                        str(selected_meta.get("title") or selected_series_id),
                        "",
                    )
                    selected_summary = build_fred_series_summary(selected_spec, selected_meta, selected_frame)

                    explorer_metric_cols = st.columns(3)
                    with explorer_metric_cols[0]:
                        st.metric(
                            "Latest",
                            format_fred_value(selected_summary.get("latest_value"), selected_meta.get("units_short")),
                            format_fred_delta(selected_summary.get("prev_delta"), selected_meta.get("units_short")),
                        )
                    with explorer_metric_cols[1]:
                        st.metric(
                            "YoY",
                            format_fred_delta(selected_summary.get("yoy_delta"), selected_meta.get("units_short")),
                        )
                    with explorer_metric_cols[2]:
                        last_obs = pd.to_datetime(selected_summary.get("latest_date"), errors="coerce")
                        st.metric("Last Obs", last_obs.strftime("%Y-%m-%d") if pd.notna(last_obs) else "n/a")

                    selected_release_name = str(selected_meta.get("release_name") or "n/a")
                    selected_frequency = str(selected_meta.get("frequency") or selected_meta.get("frequency_short") or "")
                    st.caption(
                        f"{selected_release_name} | {selected_frequency} | Units: {selected_meta.get('units_short') or selected_meta.get('units') or 'n/a'}"
                    )
                    selected_notes = str(selected_meta.get("notes") or "").strip()
                    if selected_notes:
                        st.caption(selected_notes[:600] + ("..." if len(selected_notes) > 600 else ""))
                    st.plotly_chart(
                        build_fred_figure(
                            selected_spec,
                            selected_meta,
                            selected_frame,
                            show_stationary_overlay=show_stationary_overlay,
                        ),
                        use_container_width=True,
                        key=f"broad-economy-explorer-chart-{selected_spec.series_id}",
                    )

        st.subheader("Indicator Snapshot")
        st.dataframe(
            overview[["category", "indicator", "latest", "prev", "yoy", "latest_date"]],
            use_container_width=True,
            hide_index=True,
        )

        if not series_index.empty:
            release_count = int(release_index["release_id"].nunique()) if not release_index.empty else 0
            series_count = int(series_index["series_id"].nunique())
            bulk_cols = st.columns(3)
            with bulk_cols[0]:
                st.metric("Loaded Releases", f"{release_count}")
            with bulk_cols[1]:
                st.metric("Loaded Series", f"{series_count}")
            with bulk_cols[2]:
                st.metric("Curated Indicators", f"{len(summary)}")
            st.caption(
                "The curated dashboard tracks the releases and series needed for inflation, labor, growth, housing, "
                "credit distress, policy, and money-supply analysis."
            )

elif section == MARKET_EXPLORER_SECTION:
    st.title(MARKET_EXPLORER_SECTION)
    if not _has_live_api(
        api,
        f"{MARKET_EXPLORER_SECTION} requires a working live market connection or pipeline snapshots.",
        allow_pipeline=True,
    ):
        st.info("Restore the live market connection or configure pipeline snapshots to scan movers and load price history.")
    else:
        market_view_options = ["Markets", "Broad Markets", "Commodity Section"]
        _prime_widget_choice("market_view", market_view_options, fallback="Markets", pending_key="_pending_market_view")
        market_view = st.segmented_control(
            "Market View",
            market_view_options,
            key="market_view",
            width="stretch",
        )
        movers = pd.DataFrame()
        momentum = pd.DataFrame()
        selected_market_ticker: str | None = None
        business_filter = ""
        requested_market_ticker = str(st.session_state.get("market_selected_ticker") or "").upper().strip()
        if market_view == "Commodity Section":
            commodity_options = commodity_focus_options()
            _prime_widget_choice(
                "market_commodity_focus",
                commodity_options,
                fallback="Broad Commodity Market",
                pending_key="_pending_market_commodity_focus",
            )
            lens_cols = st.columns([2.2, 3.8])
            with lens_cols[0]:
                experiment_filter = st.selectbox(
                    "Commodity Filter",
                    commodity_options,
                    key="market_commodity_focus",
                )
            with lens_cols[1]:
                st.caption("Commodity-market lens for the experiment view.")
                st.caption(commodity_focus_description(experiment_filter))
            experiment_symbols = commodity_focus_universe(experiment_filter)
            _render_market_opportunity_experiments(
                cfg,
                force_data_refresh,
                market_view,
                "Commodity Filter",
                experiment_filter,
                experiment_symbols,
            )
        elif market_view == "Broad Markets":
            business_options = business_focus_options()
            _prime_widget_choice(
                "market_business_filter",
                business_options,
                fallback="All Market",
                pending_key="_pending_market_business_filter",
            )
            lens_cols = st.columns([2.2, 3.8])
            with lens_cols[0]:
                experiment_filter = st.selectbox(
                    "Business Filter",
                    business_options,
                    key="market_business_filter",
                )
            with lens_cols[1]:
                st.caption(
                    "Custom business lens based on what the company primarily sells, not standard sector classifications."
                )
                st.caption(business_focus_description(experiment_filter))
            experiment_symbols = business_focus_universe(experiment_filter)
            _render_market_opportunity_experiments(
                cfg,
                force_data_refresh,
                market_view,
                "Business Filter",
                experiment_filter,
                experiment_symbols,
            )
        else:
            business_options = business_focus_options()
            _prime_widget_choice(
                "market_business_filter",
                business_options,
                fallback="All Market",
                pending_key="_pending_market_business_filter",
            )

            lens_cols = st.columns([2.2, 3.8])
            with lens_cols[0]:
                business_filter = st.selectbox(
                    "Business Filter",
                    business_options,
                    key="market_business_filter",
                )
            with lens_cols[1]:
                st.caption(
                    "Custom business lens based on what the company primarily sells, not standard sector classifications."
                )
                st.caption(business_focus_description(business_filter))
            business_symbols = extend_symbol_universe(
                business_focus_universe(business_filter),
                [requested_market_ticker] if requested_market_ticker else None,
            )
            horizon_options = list(MARKET_MOMENTUM_HORIZON_COLUMNS.keys())
            momentum_horizon = st.selectbox(
                "Momentum Horizon",
                horizon_options,
                index=horizon_options.index("1m"),
                format_func=lambda key: MARKET_MOMENTUM_HORIZON_LABELS.get(key, str(key)),
                key="market_momentum_horizon",
            )
            selected_horizon_col = MARKET_MOMENTUM_HORIZON_COLUMNS.get(momentum_horizon, "return_1m_pct")
            selected_horizon_label = MARKET_MOMENTUM_HORIZON_LABELS.get(momentum_horizon, momentum_horizon)
            opportunity_feed = _load_market_opportunity_feed_cached(
                cfg,
                business_filter=business_filter,
                selected_horizon_col=selected_horizon_col,
                selected_horizon_label=selected_horizon_label,
                symbols=business_symbols,
                limit=80,
                force_refresh=force_data_refresh,
            )

        if market_view != "Markets":
            st.stop()

        selected_horizon_col = locals().get("selected_horizon_col", "return_1m_pct")
        selected_horizon_label = locals().get("selected_horizon_label", "1 Month")
        opportunity_feed = locals().get("opportunity_feed", pd.DataFrame())

        if opportunity_feed.empty:
            st.info("No materialized market opportunity rows were available for this market lens. The feed is rebuilt by the scheduled attention job.")
        else:
            _render_page_agentic_summary_panel(
                MARKET_EXPLORER_SECTION,
                market_summary_context(
                    business_filter=business_filter,
                    selected_horizon_label=selected_horizon_label,
                    opportunity_feed=opportunity_feed,
                    movers=movers,
                    momentum=momentum,
                ),
                key_prefix="market_explorer",
            )
            opportunity_column_config = {
                "symbol": st.column_config.TextColumn("Ticker", width="small"),
                "company_name": st.column_config.TextColumn("Company", width="medium"),
                "opportunity": st.column_config.TextColumn("Opportunity", width="medium"),
                "direction": st.column_config.TextColumn("Direction", width="medium"),
                "opportunity_score": st.column_config.NumberColumn("Score", format="%.1f", width="small"),
                "sparkline_3m": st.column_config.LineChartColumn(
                    "Mini Chart",
                    y_min=80,
                    y_max=140,
                    width="medium",
                ),
                "close": st.column_config.NumberColumn("Close", format="$%.2f", width="small"),
                "daily_change_pct": st.column_config.NumberColumn("1D", format="%+.1f%%", width="small"),
                selected_horizon_col: st.column_config.NumberColumn(selected_horizon_label, format="%+.1f%%", width="small"),
                "return_1w_pct": st.column_config.NumberColumn("1W", format="%+.1f%%", width="small"),
                "return_3m_pct": st.column_config.NumberColumn("3M", format="%+.1f%%", width="small"),
                "momentum_score": st.column_config.NumberColumn("Momentum", format="%.2f", width="small"),
                "momentum_roc_score": st.column_config.NumberColumn("RoC", format="%+.2f", width="small"),
                "trend_fit_gap": st.column_config.NumberColumn("Trend Gap", format="%.2f", width="small"),
                "details": st.column_config.TextColumn("Details", width="large"),
            }
            selected_market_ticker = _render_selectable_ticker_table(
                "Market Opportunity Feed",
                opportunity_feed,
                [
                    "symbol",
                    "company_name",
                    "opportunity",
                    "direction",
                    "opportunity_score",
                    "sparkline_3m",
                    "close",
                    "daily_change_pct",
                    selected_horizon_col,
                    "momentum_roc_score",
                    "trend_fit_gap",
                    "details",
                ],
                key="market_opportunity_feed",
                column_config=opportunity_column_config,
            ) or selected_market_ticker

        tree_frame = pd.DataFrame()
        if not opportunity_feed.empty and {"symbol", "volume", "daily_change_pct"}.issubset(opportunity_feed.columns):
            tree_frame = opportunity_feed.copy()
            tree_frame["volume"] = pd.to_numeric(tree_frame["volume"], errors="coerce")
            tree_frame["change_pct"] = pd.to_numeric(tree_frame["daily_change_pct"], errors="coerce")
            tree_frame = tree_frame[tree_frame["symbol"].astype(str).str.strip().ne("")]
            tree_frame = tree_frame[tree_frame["volume"].fillna(0) > 0]
        if not tree_frame.empty:
            tree = px.treemap(
                tree_frame,
                path=["symbol"],
                values="volume",
                color="change_pct",
                color_continuous_scale="RdYlGn",
                template="plotly_dark",
                title=f"Daily Movers - {business_filter} (Volume / Change %)",
            )
            st.plotly_chart(tree, use_container_width=True)
        else:
            st.info("Daily mover volume was not available in the materialized feed, so the treemap is hidden.")

        scanned_detail_symbols: set[str] = set()
        if not opportunity_feed.empty and "symbol" in opportunity_feed.columns:
            scanned_detail_symbols.update(
                str(symbol).upper().strip()
                for symbol in opportunity_feed["symbol"].astype(str).tolist()
                if str(symbol).strip()
            )

        focus_ticker = str(st.session_state.get("market_selected_ticker") or "").upper().strip()
        if selected_market_ticker:
            focus_ticker = _set_workspace_ticker(selected_market_ticker) or focus_ticker
        elif requested_market_ticker:
            focus_ticker = _set_workspace_ticker(requested_market_ticker) or focus_ticker

        if requested_market_ticker and requested_market_ticker not in scanned_detail_symbols:
            st.caption(
                f"{requested_market_ticker} was opened from attention and pinned into the ticker handoff because it is outside the current {business_filter} scan lens."
            )

        if focus_ticker:
            handoff_cols = st.columns([4.8, 1.4])
            with handoff_cols[0]:
                st.caption(
                    f"Stock focus: {focus_ticker}. Open Stock Investigator for technicals, fundamentals, and company context."
                )
            with handoff_cols[1]:
                if st.button(
                    "Open Stock Investigator",
                    key=f"market_open_stock_investigator_{focus_ticker}",
                    use_container_width=True,
                ):
                    _open_attention_target(STOCK_INVESTIGATOR_SECTION, {"ticker": focus_ticker})
        else:
            st.info("Select a ticker from a market table to continue in Stock Investigator.")

elif section == STOCK_INVESTIGATOR_SECTION:
    st.title("Stock Investigator")

    if not _has_live_api(
        api,
        "Stock Investigator requires a working live market connection or pipeline snapshots.",
        allow_pipeline=True,
    ):
        st.info("Restore the live market connection or configure pipeline snapshots to inspect ticker details.")
    else:
        _render_stock_investigator_workspace(
            cfg,
            force_data_refresh=force_data_refresh,
        )

elif section == "Option Strategizer":
    st.title("Option Strategizer")
    ticker = st.text_input("Ticker", value="AAPL", key="opt_ticker").upper().strip()

    if ticker and _has_live_api(
        api,
        "Option Strategizer requires a working live market connection or pipeline snapshots.",
        allow_pipeline=True,
    ):
        spot_price = np.nan
        try:
            with _timed("option_reference_price", ticker=ticker):
                reference_frame = _load_price_history_cached(cfg, ticker, days=30, force_refresh=force_data_refresh)
            if not reference_frame.empty and "close" in reference_frame.columns:
                spot_price = float(pd.to_numeric(reference_frame["close"], errors="coerce").dropna().iloc[-1])
        except Exception as exc:
            _log_event("option_reference_price_failed", ticker=ticker, error=str(exc)[:200])

        try:
            with st.spinner("Loading option expirations..."):
                with _timed("load_option_chain", ticker=ticker):
                    expirations, _, _ = _load_option_chain_cached(
                        cfg,
                        ticker,
                        force_refresh=force_data_refresh,
                    )
        except AlpacaAPIError as exc:
            _log_event("load_option_chain_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load option chain: {exc}")
            expirations = []

        if not expirations:
            st.info("No options chain available for this ticker.")
        else:
            exp = st.selectbox("Expiration", expirations)
            try:
                with st.spinner("Loading option quotes..."):
                    with _timed("load_option_chain_expiration", ticker=ticker, expiration=exp):
                        _, calls, puts = _load_option_chain_cached(
                            cfg,
                            ticker,
                            exp,
                            force_refresh=force_data_refresh,
                        )
            except AlpacaAPIError as exc:
                _log_event("load_option_chain_expiration_failed", ticker=ticker, expiration=exp, error=str(exc)[:200])
                st.warning(f"Could not load option quotes: {exc}")
                calls = pd.DataFrame()
                puts = pd.DataFrame()

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Top Calls")
                st.dataframe(rank_options(calls), use_container_width=True, hide_index=True)
            with c2:
                st.subheader("Top Puts")
                st.dataframe(rank_options(puts), use_container_width=True, hide_index=True)

            plot_calls = calls.dropna(subset=["strike", "impliedVolatility"]).copy()
            plot_calls, size_col = _prepare_scatter_size(plot_calls, "openInterest")
            if not plot_calls.empty:
                fig_c = px.scatter(
                    plot_calls,
                    x="strike",
                    y="impliedVolatility",
                    size=size_col,
                    color="volume" if "volume" in plot_calls.columns else None,
                    template="plotly_dark",
                    title=f"{ticker} Calls IV Surface ({exp})",
                )
                st.plotly_chart(fig_c, use_container_width=True)
            elif not calls.empty:
                st.info("No call rows had both strike and implied volatility for plotting.")

            st.markdown("---")
            st.subheader("Greek-Based Scenario Selector")
            if np.isfinite(spot_price):
                st.caption(
                    "Scenario model uses delta, gamma, and theta to approximate option value at your target date, "
                    "then scores contracts for projected return, leverage, lower decay, lower cost, and liquidity."
                )
                control_cols = st.columns(3)
                with control_cols[0]:
                    st.metric("Spot Reference", f"${spot_price:,.2f}")
                with control_cols[1]:
                    expected_price = st.number_input(
                        "Expected Price",
                        min_value=0.01,
                        value=float(round(spot_price * 1.05, 2)),
                        step=float(max(round(spot_price * 0.01, 2), 0.5)),
                        key=f"option_expected_price_{ticker}",
                    )
                with control_cols[2]:
                    scenario_days = st.slider(
                        "Timespan (days from now)",
                        1,
                        180,
                        30,
                        key=f"option_scenario_days_{ticker}",
                    )

                scenario_bias = "Bullish calls" if expected_price >= spot_price else "Bearish puts"
                st.caption(
                    f"Scenario bias: {scenario_bias}. "
                    "Projected option change ~= delta*dS + 0.5*gamma*dS^2 + theta*days. Vega and vol-surface changes are not modeled."
                )

                try:
                    with st.spinner("Building cross-expiration option surface..."):
                        with _timed(
                            "load_option_surface",
                            ticker=ticker,
                            expected_price=f"{expected_price:.2f}",
                            horizon_days=scenario_days,
                        ):
                            surface = _load_option_surface_cached(
                                cfg,
                                ticker,
                                float(expected_price),
                                int(scenario_days),
                                float(spot_price),
                                force_refresh=force_data_refresh,
                            )
                except AlpacaAPIError as exc:
                    _log_event("load_option_surface_failed", ticker=ticker, error=str(exc)[:200])
                    st.warning(f"Could not build option surface: {exc}")
                    surface = pd.DataFrame()

                if surface.empty:
                    st.info("No option surface data was available for this scenario.")
                else:
                    candidate_payload = _load_option_candidates_cached(
                        cfg,
                        ticker,
                        expected_price=float(expected_price),
                        horizon_days=int(scenario_days),
                        underlying_price=float(spot_price),
                        surface=surface,
                        force_refresh=force_data_refresh,
                    )
                    candidates = candidate_payload.get("candidates", pd.DataFrame())
                    summary = candidate_payload.get("summary", {})

                    if candidates.empty:
                        st.info("No option candidates had enough quote and Greek data for scenario analysis.")
                    else:
                        best = candidates.iloc[0]
                        metric_cols = st.columns(5)
                        with metric_cols[0]:
                            st.metric("Best Contract", str(best.get("contractSymbol") or "n/a"))
                        with metric_cols[1]:
                            st.metric("Selection Score", f"{pd.to_numeric(best.get('selection_score'), errors='coerce'):.1f}")
                        with metric_cols[2]:
                            st.metric("Projected Return", f"{pd.to_numeric(best.get('projected_return_pct'), errors='coerce'):.1f}%")
                        with metric_cols[3]:
                            st.metric("Upfront Cost", f"${pd.to_numeric(best.get('contracts_cost'), errors='coerce'):,.0f}")
                        with metric_cols[4]:
                            st.metric("Theta Drag to Target", f"{pd.to_numeric(best.get('theta_drag_pct'), errors='coerce'):.1f}%")

                        st.caption(
                            f"Top side: {summary.get('preferred_side', 'n/a')} | "
                            f"Expected move: {summary.get('expected_move_pct', np.nan):.1f}% | "
                            f"Candidates scored: {summary.get('candidate_count', 0)}"
                        )

                        plot_surface = candidates.dropna(
                            subset=["strike", "dte", "selection_score", "projected_return_pct"]
                        ).copy()
                        plot_surface, surface_size_col = _prepare_scatter_size(plot_surface, "delta_leverage")
                        if not plot_surface.empty:
                            fig_surface = px.scatter_3d(
                                plot_surface.head(250),
                                x="strike",
                                y="dte",
                                z="selection_score",
                                color="projected_return_pct",
                                size=surface_size_col,
                                hover_name="contractSymbol",
                                hover_data={
                                    "type": True,
                                    "premium": ":.2f",
                                    "contracts_cost": ":.0f",
                                    "delta": ":.3f",
                                    "gamma": ":.4f",
                                    "theta": ":.3f",
                                    "vega": ":.3f",
                                    "delta_leverage": ":.2f",
                                    "theta_drag_pct": ":.1f",
                                    "projected_return_pct": ":.1f",
                                    "selection_score": ":.1f",
                                },
                                template="plotly_dark",
                                title=f"{ticker} Option Opportunity Surface",
                                labels={
                                    "strike": "Strike",
                                    "dte": "Days to Expiry",
                                    "selection_score": "Selection Score",
                                    "projected_return_pct": "Projected Return %",
                                },
                            )
                            fig_surface.update_traces(marker=dict(opacity=0.78))
                            st.plotly_chart(fig_surface, use_container_width=True)

                        greek_tradeoff = candidates.dropna(
                            subset=["delta", "theta_drag_pct", "projected_return_pct", "selection_score"]
                        ).copy()
                        greek_tradeoff, greek_size_col = _prepare_scatter_size(greek_tradeoff, "gamma_convexity")
                        if not greek_tradeoff.empty:
                            fig_greeks = px.scatter_3d(
                                greek_tradeoff.head(250),
                                x="delta",
                                y="theta_drag_pct",
                                z="projected_return_pct",
                                color="selection_score",
                                size=greek_size_col,
                                hover_name="contractSymbol",
                                hover_data={
                                    "strike": ":.2f",
                                    "dte": ":.0f",
                                    "premium": ":.2f",
                                    "contracts_cost": ":.0f",
                                    "delta_leverage": ":.2f",
                                    "gamma_convexity": ":.2f",
                                    "selection_score": ":.1f",
                                },
                                template="plotly_dark",
                                title=f"{ticker} Greek Tradeoff Surface",
                                labels={
                                    "delta": "Delta",
                                    "theta_drag_pct": "Theta Drag to Target %",
                                    "projected_return_pct": "Projected Return %",
                                    "selection_score": "Selection Score",
                                },
                            )
                            fig_greeks.update_traces(marker=dict(opacity=0.78))
                            st.plotly_chart(fig_greeks, use_container_width=True)

                        st.dataframe(
                            candidates.head(15)[
                                [
                                    "contractSymbol",
                                    "type",
                                    "expiration",
                                    "dte",
                                    "strike",
                                    "premium",
                                    "contracts_cost",
                                    "delta",
                                    "gamma",
                                    "theta",
                                    "delta_leverage",
                                    "theta_drag_pct",
                                    "projected_return_pct",
                                    "selection_score",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )
            else:
                st.info("A recent stock price was not available, so the Greek-based scenario selector is hidden for this ticker.")
