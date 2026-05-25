from __future__ import annotations

import base64
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
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
from urllib.parse import urlencode, urlparse

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit.components.v1 import html as components_html

from data_access.layer import DataAccessLayer
from presentation import attention_content, dashboard_loaders
from services import auth_service
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.attention_home_summary import (
    apply_display_limits,
    attention_mover_card_title as attention_mover_card_title_service,
    build_attention_home_narrative_beats,
    build_attention_home_summary_payload,
)
from services import attention_surface as attention_surface_module
from services.company import build_attention_news_narrative, summarize_recent_news
from services.config import AppConfig, alpaca_secret_name_settings, load_config
from services.data_cache import cache_data_root, cache_policy_path, dataset_scope
from services.elevenlabs_tts import (
    ElevenLabsTTSAPIError,
    load_elevenlabs_tts_config,
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
from services.aql_zopedia_engine import (
    load_aql_zopedia_llm_client,
    resolve_aql_zopedia_followup_query,
    run_aql_zopedia_agent,
)
from services.agents import (
    append_chat_message,
    create_chat_thread,
    list_chat_threads,
    load_chat_thread,
)
from services.page_browsing import browse_page
from services.saa import (
    build_zopedia_change_proposal,
    fetch_youtube_transcript,
    ingest_zopedia_source,
    list_zopedia_change_proposals,
    list_zopedia_maintenance_reports,
    list_zopedia_mutation_audits,
    persist_zopedia_change_proposals,
    prepare_zopedia_uploaded_source,
    rollback_zopedia_mutation,
    search_zopedia_pages,
    zopedia_read_source,
    zopedia_sources_for_page,
)
from services.secrets import resolve_secret_value
from services.zopedia_presentation import (
    parse_markdown_table,
    prepare_answer_markdown_blocks as prepare_zopedia_answer_markdown_blocks,
    trace_step_body,
    trace_step_title,
)
from services.market import (
    commodity_dependency_graph,
    commodity_proxy_profile,
    commodity_reference_universe,
)
from views._shared import (
    ADMIN_SECTION,
    AGENTIC_OMNIBAR_SECTION,
    APP_BRAND_KICKER,
    APP_BRAND_NAME,
    ATTENTION_HORIZON_LABELS,
    ATTENTION_HORIZON_OPTIONS,
    ATTENTION_SENSITIVITY_ORDER,
    BASE_SECTION_OPTIONS,
    BROAD_ECONOMY_SECTION,
    HOME_EXP_SECTION,
    HOME_V2_SECTION,
    JOB_LABELS,
    LOGGER,
    MARKET_EXPLORER_SECTION,
    MARKET_MOMENTUM_HORIZON_COLUMNS,
    MARKET_MOMENTUM_HORIZON_LABELS,
    MARKET_MOMENTUM_SCAN_DAYS,
    NAV_SEPARATOR,
    PORTFOLIO_PERFORMANCE_SECTION,
    PORTFOLIO_SECTION,
    SOURCE_LABELS,
    STOCK_INVESTIGATOR_SECTION,
    TRADING_AGENT_SECTION,
    _activity_link_key,
    _collect_evidence_links,
    _current_layout_mode,
    _current_user_context,
    _current_user_is_admin,
    _current_user_share_fraction,
    _dataframe_selected_rows,
    _first_substantive_company_context_line,
    _has_live_api,
    _inline_image_markup,
    _log_event,
    _market_business_filter_for_symbol,
    _mobile_layout_active,
    _normalize_workspace_section,
    _normalized_layout_mode,
    _open_attention_target,
    _open_workspace_section,
    _prepare_scatter_size,
    _presentation_layer_only,
    _prime_widget_choice,
    _record_usage_interaction,
    _render_compact_background_sections,
    _render_help_popover,
    _render_overview_fundamentals,
    _render_page_agentic_summary_panel,
    _render_page_intro,
    _render_section_back_button,
    _render_selectable_ticker_table,
    _render_tracked_activity_link,
    _request_ip_address,
    _request_user_agent,
    _responsive_columns,
    _responsive_two_panel,
    _section_options,
    _set_workspace_ticker,
    _taxonomy_summary_text,
    _timed,
    _to_float,
)

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

HOME_V3_SECTION = "Home v3"

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


_AUTH_COOKIE_NAME = "spectral_nature_ui_session"
_AUTH_COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60
_AUTH_COOKIE_REMEMBER_ME_TTL_SECONDS = 30 * 24 * 60 * 60
APP_BRAND_SUBTITLE = "Research, portfolio context, and market structure in one refined workspace."
_APP_SHELL_STYLE_VERSION = "2026-04-14-vertical-nav-v1"
_INLINE_LOADING_STYLE_VERSION = "2026-04-02-inline-loading-v1"

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


def _ensure_mobile_layout_styles() -> None:
    st.session_state["_sn_mobile_layout_styles_version"] = "mobile-shell-v2"
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100% !important;
            padding: 0.75rem 0.85rem 5rem 0.85rem !important;
        }
        h1 {
            font-size: 2.35rem !important;
            line-height: 0.98 !important;
            letter-spacing: 0 !important;
            margin-bottom: 0.8rem !important;
        }
        h2 {
            font-size: 1.55rem !important;
            letter-spacing: 0 !important;
        }
        h3 {
            font-size: 1.2rem !important;
            letter-spacing: 0 !important;
        }
        p, li, label, input, textarea, [data-testid="stMarkdownContainer"] {
            overflow-wrap: anywhere;
            word-break: normal;
        }
        div[data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
            gap: 0.68rem !important;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 0 !important;
        }
        div[data-testid="stMetric"] {
            padding: 0.72rem 0.82rem !important;
            border-radius: 0.5rem !important;
        }
        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.34rem !important;
            line-height: 1.08 !important;
        }
        .stButton > button,
        .stFormSubmitButton > button {
            min-height: 2.65rem;
            padding: 0.48rem 0.78rem !important;
            border-radius: 0.5rem !important;
            white-space: normal;
        }
        .stButton > button p,
        .stFormSubmitButton > button p {
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            line-height: 1.18 !important;
        }
        [class*="st-key-mobile_shell_"] {
            margin: 0 0 1rem 0;
            border-radius: 0.5rem;
            background: var(--sn-card);
            box-shadow: var(--sn-shadow-soft);
        }
        .sn-mobile-brand-row {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 0.75rem;
            margin-bottom: 0.72rem;
        }
        .sn-mobile-brand {
            color: var(--sn-ink);
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
            font-size: 1.24rem;
            font-weight: 720;
            line-height: 1.02;
        }
        .sn-mobile-kicker {
            color: var(--sn-muted);
            font-size: 0.68rem;
            font-weight: 650;
            line-height: 1.2;
            text-align: right;
        }
        .sn-mobile-caption {
            margin: 0.22rem 0 0.58rem 0;
            color: var(--sn-muted-strong);
            font-size: 0.8rem;
            line-height: 1.35;
        }
        [class*="st-key-mobile_shell_"] [data-testid="stSelectbox"] label,
        [class*="st-key-mobile_shell_"] [data-testid="stDateInput"] label {
            color: var(--sn-muted-strong) !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
        }
        div[data-baseweb="tab-list"] {
            overflow-x: auto;
            flex-wrap: nowrap;
        }
        div[data-baseweb="tab-list"] button {
            flex: 0 0 auto;
            white-space: nowrap;
        }
        div[data-testid="stDataFrame"] {
            overflow-x: auto;
        }
        div[data-testid="stTable"],
        div[data-testid="stDataFrame"],
        div[data-testid="stPlotlyChart"],
        .js-plotly-plot,
        .plot-container,
        .svg-container {
            max-width: 100% !important;
            overflow-x: auto !important;
        }
        img, svg, canvas, iframe {
            max-width: 100% !important;
        }
        [data-testid="stChatMessage"] {
            padding-left: 0.2rem !important;
            padding-right: 0.2rem !important;
        }
        [data-testid="stChatInput"] {
            left: 0.5rem !important;
            right: 0.5rem !important;
            width: calc(100% - 1rem) !important;
        }
        div[data-testid="stCode"] pre,
        div[data-testid="stCode"] code,
        div[data-testid="stCode"] code * {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
        }
        [class*="st-key-mobile_workspace_section"] {
            margin-bottom: 0.45rem;
        }
        [class*="st-key-mobile_workspace_section"] [data-baseweb="select"] {
            min-height: 2.75rem;
        }
        [class*="st-key-mobile_public_signin"] button,
        [class*="st-key-mobile_dashboard_logout"] button {
            width: 100%;
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


def _render_mobile_brand_header(caption: str = "") -> None:
    caption_markup = (
        f"<div class='sn-mobile-caption'>{html.escape(str(caption).strip())}</div>"
        if str(caption or "").strip()
        else ""
    )
    st.markdown(
        (
            "<div class='sn-mobile-brand-row'>"
            f"<div class='sn-mobile-brand'>{html.escape(APP_BRAND_NAME)}</div>"
            f"<div class='sn-mobile-kicker'>by {html.escape(APP_BRAND_KICKER)}</div>"
            "</div>"
            f"{caption_markup}"
        ),
        unsafe_allow_html=True,
    )


def _apply_homepage_replay_date(selected_date: object, today: object) -> None:
    if selected_date is not None and selected_date < today:
        st.session_state["_homepage_replay_date"] = str(selected_date)
    else:
        st.session_state.pop("_homepage_replay_date", None)


def _render_mobile_home_replay_date(key: str = "homepage_replay_date") -> None:
    today = pd.Timestamp.now(tz="UTC").normalize().date()
    selected = st.date_input(
        "Snapshot date",
        value=today,
        max_value=today,
        key=key,
    )
    _apply_homepage_replay_date(selected, today)


def _render_mobile_auth_shell() -> None:
    with st.container(border=True, key="mobile_shell_auth"):
        _render_mobile_brand_header("Secure access")


def _render_mobile_public_home_shell() -> None:
    with st.container(border=True, key="mobile_shell_public_home"):
        _render_mobile_brand_header("Market narrative home")
        _render_mobile_home_replay_date()
        if st.button("Sign in", type="primary", width="stretch", key="mobile_public_signin"):
            st.session_state["_show_login_form"] = True
            st.rerun()


def _render_mobile_workspace_shell(
    *,
    section_options: list[str],
    current_section: str,
    cache_disabled: bool,
    force_refresh_default: bool,
    current_user: auth_service.UserContext | None,
) -> tuple[str, object, object, object]:
    current = _normalize_workspace_section(current_section) or (section_options[0] if section_options else "Home")
    if current not in section_options and section_options:
        current = section_options[0]
    mobile_nav_key = "mobile_workspace_section"
    last_synced_current = _normalize_workspace_section(st.session_state.get("_mobile_workspace_synced_section"))
    mobile_nav_value = _normalize_workspace_section(st.session_state.get(mobile_nav_key))
    if mobile_nav_value not in section_options or last_synced_current != current:
        st.session_state["mobile_workspace_section"] = current
        st.session_state["_mobile_workspace_synced_section"] = current

    with st.container(border=True, key="mobile_shell_workspace"):
        _render_mobile_brand_header("Workspace")
        selected = st.selectbox(
            "Navigate",
            section_options,
            index=section_options.index(current) if current in section_options else 0,
            key=mobile_nav_key,
        )
        selected = _normalize_workspace_section(selected) or current
        if selected != current:
            st.session_state["_mobile_workspace_synced_section"] = selected
            st.session_state["_pending_workspace_section"] = selected
            st.rerun()

        if current == "Home":
            _render_mobile_home_replay_date()

        with st.expander("Workspace Status", expanded=False):
            if pipeline_store_configured():
                if _presentation_layer_only():
                    st.caption("Data mode: Curated presentation snapshots")
                else:
                    st.caption("Data mode: Curated metadata + parquet snapshots")
            else:
                st.caption("Data mode: Snapshot store unavailable")
            st.caption(f"Cache: {'disabled' if cache_disabled else 'enabled'}")
            st.caption(
                f"Inline force refresh: {'disabled' if _presentation_layer_only() else ('on' if force_refresh_default else 'off')}"
            )
            st.caption(f"CSV cache: {cache_data_root()}")
            st.caption(f"Cache policy: {cache_policy_path()}")
            if st.button("Logout", key="mobile_dashboard_logout", width="stretch"):
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

    return current, sidebar_connection, sidebar_status, sidebar_buying_power


def _make_api(cfg: AppConfig) -> AlpacaAPI:
    return AlpacaAPI(cfg)


def _data_access_layer(cfg: AppConfig | None = None, fred_api_key: str | None = None) -> DataAccessLayer:
    return DataAccessLayer(cfg=cfg, fred_api_key=fred_api_key, materialized_only=_presentation_layer_only())


def _alpaca_cache_scope(cfg: AppConfig) -> str:
    trading_scope = cfg.alpaca_trading_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    data_scope = cfg.alpaca_data_base_url.replace("https://", "").replace("http://", "").replace("/", "_")
    account_scope = dataset_scope("acct", cfg.alpaca_api_key)
    return f"{trading_scope}__{data_scope}__{account_scope}"


def _render_connection_issue(summary: str, *, details: str | None = None, setup_code: str | None = None) -> None:
    st.error(summary)
    if details:
        st.caption(details)
    if setup_code:
        st.code(setup_code, language="bash")


def _auth_enabled() -> bool:
    raw = (os.getenv("DASHBOARD_AUTH_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _store_user_context(context: auth_service.UserContext | None) -> None:
    st.session_state["_ui_user_context"] = context.to_dict() if context is not None else None


dashboard_loaders.configure_dashboard_loaders(
    current_user_context_provider=_current_user_context,
    data_access_layer_factory=_data_access_layer,
    presentation_layer_only_provider=_presentation_layer_only,
)


def _current_user_can_view_full_portfolio() -> bool:
    context = _current_user_context()
    if context is None:
        return True
    return bool(context.can_view_full_portfolio)


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


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _mobile_ui_enabled() -> bool:
    return _env_flag("STREAMLIT_MOBILE_UI_ENABLED", default=False)


def _mobile_user_agent(user_agent: object) -> bool:
    text = str(user_agent or "").lower()
    if not text:
        return False
    mobile_markers = ("iphone", "ipod", "android", "mobile", "windows phone")
    tablet_markers = ("ipad", "tablet")
    return any(marker in text for marker in mobile_markers) and not any(marker in text for marker in tablet_markers)


def _resolve_layout_mode() -> str:
    """Resolve desktop/mobile shell mode. Desktop is the rollback-safe default."""
    if not _mobile_ui_enabled():
        st.session_state["_ui_layout_mode"] = "desktop"
        return "desktop"

    query_mode = _normalized_layout_mode(_query_param_value("layout"))
    if query_mode:
        st.session_state["_ui_layout_mode_override"] = query_mode
        selected_mode = query_mode
    else:
        st.session_state.pop("_ui_layout_mode_override", None)
        selected_mode = _normalized_layout_mode(os.getenv("STREAMLIT_LAYOUT_MODE_DEFAULT")) or "desktop"
    if selected_mode == "auto":
        resolved = "mobile" if _mobile_user_agent(_request_user_agent()) else "desktop"
    else:
        resolved = selected_mode
    st.session_state["_ui_layout_mode"] = resolved
    return resolved


def _ensure_client_layout_auto_redirect(layout_mode: str) -> None:
    if not _mobile_ui_enabled():
        return
    if _normalized_layout_mode(os.getenv("STREAMLIT_LAYOUT_MODE_DEFAULT")) != "auto":
        return
    if _normalized_layout_mode(_query_param_value("layout")):
        return
    if _normalized_layout_mode(layout_mode) == "mobile":
        return

    components_html(
        """
        <script>
        (function () {
          try {
            const parentWindow = window.parent || window.top || window;
            const parentDocument = parentWindow.document || document;
            const currentUrl = new URL(parentWindow.location.href);
            if (currentUrl.searchParams.has("layout")) {
              return;
            }
            const viewportWidth = Math.min(
              parentWindow.innerWidth || Number.POSITIVE_INFINITY,
              parentDocument.documentElement ? parentDocument.documentElement.clientWidth : Number.POSITIVE_INFINITY
            );
            const userAgent = String(parentWindow.navigator && parentWindow.navigator.userAgent || "");
            const mobileUserAgent = /(iPhone|iPod|Windows Phone|Mobile|Android)/i.test(userAgent) && !/(iPad|Tablet)/i.test(userAgent);
            const coarsePhone = parentWindow.matchMedia
              ? parentWindow.matchMedia("(pointer: coarse) and (max-width: 900px)").matches
              : false;
            if (viewportWidth <= 760 || mobileUserAgent || coarsePhone) {
              currentUrl.searchParams.set("layout", "mobile");
              parentWindow.location.replace(currentUrl.toString());
            }
          } catch (error) {
            // Keep server-side desktop fallback if the component cannot read the parent window.
          }
        }());
        </script>
        """,
        height=0,
    )


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
    if _mobile_layout_active():
        _render_mobile_auth_shell()
        return
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
            _clear_auth_query_params()
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
                    _clear_auth_query_params()
                    st.session_state["_ui_authenticated"] = True
                    st.session_state["_ui_auth_session_id"] = session_token
                    st.session_state["_ui_auth_mode"] = "database"
                    st.session_state.pop("_show_login_form", None)
                    _store_user_context(context)
                    _render_auth_cookie_sync("set", session_token, persistent=remember_me)
                    _apply_post_login_destination()
                    return
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
                        return
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
        profile = dashboard_loaders._load_ticker_snapshot_profile(
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
        compact_cols = _responsive_columns(compact_spec, gap="small")
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
            header_cols = _responsive_columns(header_spec, gap="small")
            header_cols[0].caption("Ticker")
            header_cols[1].caption("Chart")
            if show_context_column:
                header_cols[2].caption("Context")
        else:
            header_spec = [0.9, 2.5, 1.6, 1.8] if show_context_column else [0.9, 2.7, 1.6]
            header_cols = _responsive_columns(header_spec, gap="small")
            header_cols[0].caption("Ticker")
            header_cols[1].caption("Name")
            header_cols[2].caption("Chart")
            if show_context_column:
                header_cols[3].caption("Context")
    for index, row in enumerate(rows):
        row_container = st.container(border=show_header or len(rows) > 1)
        with row_container:
            row_spec = [3.3, 1.6, 1.8] if interactive_preview and show_context_column else [3.5, 1.6] if interactive_preview else [0.9, 2.5, 1.6, 1.8] if show_context_column else [0.9, 2.7, 1.6]
            row_cols = _responsive_columns(row_spec, gap="small")
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
        out[bundle_id] = dashboard_loaders._safe_load_attention_research_bundle_cached(
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
    bundle = dashboard_loaders._safe_load_attention_research_bundle_cached(
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
        st.caption("No related news was available.")
        return
    st.caption("Source: news database")
    for index, (_, row) in enumerate(rows.iterrows()):
        headline = str(row.get("headline") or "Untitled").strip()
        source = str(row.get("source") or "News").strip()
        published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
        published_label = published_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published_at) else "n/a"
        url = str(row.get("url") or "").strip()
        excerpt = attention_content._clean_attention_text(row.get("summary") or row.get("description"))
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
                header_cols = _responsive_columns([5.4, 1.4 if show_open_button and show_clear_button else 0.7], gap="small")
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
            materialized_background = dashboard_loaders._load_attention_ticker_background_cached(
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
                price_frame = dashboard_loaders._load_public_price_history_cached(
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
                news_payload = dashboard_loaders._load_recent_news_cached(
                    cfg,
                    target,
                    days=14,
                    limit=6,
                    force_refresh=force_data_refresh,
                )
                attention_context = dashboard_loaders._load_attention_context_cached(
                    cfg,
                    target,
                    force_refresh=force_data_refresh,
                )
                price = dashboard_loaders._load_price_history_cached(
                    cfg,
                    target,
                    days=180,
                    force_refresh=force_data_refresh,
                )
        except Exception as exc:
            st.warning(f"Could not load background for {target}: {exc}")
            return

        if isinstance(price, pd.DataFrame) and price.empty:
            price = dashboard_loaders._load_public_price_history_cached(
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


def _queue_homepage_v2_active_panel(panel: str) -> None:
    normalized_panel = str(panel or "").strip().lower()
    if normalized_panel not in {HOMEPAGE_V2_RESEARCH_PANEL, HOMEPAGE_V2_COMPANY_PANEL}:
        return
    st.session_state["_pending_homepage_v2_active_panel"] = normalized_panel


def _consume_homepage_v2_pending_panel() -> None:
    pending_panel = str(st.session_state.pop("_pending_homepage_v2_active_panel", "") or "").strip().lower()
    if pending_panel in {HOMEPAGE_V2_RESEARCH_PANEL, HOMEPAGE_V2_COMPANY_PANEL}:
        st.session_state["homepage_v2_active_panel"] = pending_panel


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
    raw_name = str(tool_name or "").strip()
    normalized = raw_name.lower().replace("_", ".").replace(" ", ".")
    friendly_names = {
        "research.retained.context": "saved research",
        "research.market.impact.map": "market context",
        "research.live.event.evidence": "recent news",
        "research.search.evidence": "evidence search",
        "research.open.page": "source page",
        "zopedia.search.pages": "Zopedia memory",
        "zopedia.read.page": "memory page",
        "zopedia.read.source": "source evidence",
        "zopedia.sources.for.page": "page sources",
        "zopedia.trace.to.evidence": "source trail",
        "zopedia.neighborhood": "linked memory",
        "zopedia.ingest.source": "source intake",
        "zopedia.ingest.youtube": "transcript intake",
        "zopedia.propose.change": "memory review",
        "zopedia.list.proposals": "memory review queue",
        "zopedia.list.mutations": "memory changes",
        "zopedia.list.maintenance.reports": "memory health",
        "zopedia.apply.mutation": "memory update",
        "zopedia.rollback.mutation": "memory rollback",
        "investigator.technical.signals": "technical signals",
        "investigator.forecast": "forecast data",
        "investigator.company.context": "company context",
        "investigator.fundamentals": "fundamentals",
        "investigator.recent.news": "company news",
        "dataset.run.anomaly.check": "anomaly check",
        "analysis.run.python": "analysis workspace",
        "hypothesis.verify": "verification",
        "scratchpad.write": "research notes",
        "scratchpad.read": "research notes",
        "system.capabilities": "available data",
    }
    if normalized in friendly_names:
        return friendly_names[normalized]
    clean_name = raw_name.replace("_", " ").replace(".", " ").strip()
    if clean_name.startswith("zopedia."):
        return clean_name.replace("zopedia.", "Zopedia ")
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
        return "Starting the research."
    if stage == "tool_catalog_ready":
        return "Getting ready."
    if stage == "hidden_step_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Checking saved research... ({elapsed}s)"
    if stage == "hidden_step_timeout":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Saved-research check timed out after {elapsed}s. Continuing."
    if stage == "planner_start":
        return "Planning the next research step."
    if stage == "planner_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Still reasoning... ({elapsed}s)"
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
    if stage == "final_synthesis_heartbeat":
        elapsed = int(event.get("elapsed_seconds") or 0)
        return f"Writing the answer... ({elapsed}s)"
    if stage == "final_synthesis_timeout":
        return "Synthesis timed out. Returning the evidence gathered so far."
    if stage == "memory_reflection_start":
        return "Checking whether Zopedia memory should change."
    if stage == "memory_reflection_complete":
        return str(event.get("message") or "Zopedia memory checked.")
    if stage == "memory_reflection_timeout":
        return "Zopedia memory check timed out. Continuing."
    if stage == "memory_reflection_failed":
        return "Zopedia memory check failed. Continuing."
    if stage == "memory_mutation_start":
        return "Updating Zopedia memory."
    if stage == "memory_mutation_complete":
        return str(event.get("message") or "Zopedia memory updated.")
    if stage == "memory_mutation_failed":
        return "Zopedia memory update failed. Continuing."
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
    "BPS", "RHS", "LHS", "AVG", "MAX", "MIN", "NET", "PRE", "YTD", "UST",
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
    cols = _responsive_columns(min(len(metrics), 6))
    for i, m in enumerate(metrics):
        with cols[i]:
            st.metric(m["ticker"], m["value"])


def _is_public_web_source_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False
    return not host.endswith((".local", ".localhost", ".test", ".invalid"))


def _source_domain(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    return (parsed.hostname or "").replace("www.", "")[:30]


def _render_source_evidence_strip(sources: list[dict[str, str]]) -> None:
    """Render public web source cards. Internal/debug refs stay out of citations."""
    unique: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        if not url or url in seen_urls or not _is_public_web_source_url(url):
            continue
        seen_urls.add(url)
        unique.append(src)
    if not unique:
        return
    display = unique[:5]
    cols = _responsive_columns(min(len(display), 5))
    for i, src in enumerate(display):
        url = str(src.get("url") or "").strip()
        label = str(src.get("label") or "").strip()
        domain = _source_domain(url)
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
    """Render the product-visible research trace as readable steps."""

    def _render_analysis_result(render_payload: dict[str, object], *, key: str) -> None:
        analysis = dict(render_payload.get("analysis") or {})
        metrics = [dict(item) for item in list(analysis.get("metrics") or []) if isinstance(item, dict)]
        if metrics:
            st.dataframe(pd.DataFrame(metrics), use_container_width=True, hide_index=True)
        for table_idx, table in enumerate(list(analysis.get("tables") or [])[:2]):
            if not isinstance(table, dict):
                continue
            rows = list(table.get("rows") or [])
            if not rows:
                continue
            st.caption(str(table.get("name") or f"table_{table_idx + 1}"))
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        for chart_idx, chart in enumerate(list(analysis.get("charts") or [])[:2]):
            if not isinstance(chart, dict):
                continue
            x_values = list(chart.get("x") or [])
            y_values = pd.to_numeric(pd.Series(list(chart.get("y") or [])), errors="coerce")
            if not x_values or len(x_values) != len(y_values) or y_values.dropna().empty:
                continue
            chart_kind = str(chart.get("kind") or "line").strip().lower()
            fig = go.Figure()
            trace_name = str(chart.get("series_name") or chart.get("name") or "series").strip()
            if chart_kind == "bar":
                fig.add_trace(go.Bar(x=x_values, y=y_values, name=trace_name))
            else:
                mode = "markers" if chart_kind == "scatter" else "lines+markers"
                fig.add_trace(go.Scatter(x=x_values, y=y_values, mode=mode, name=trace_name))
            fig.update_layout(
                template="plotly_dark",
                title=str(chart.get("name") or "Analysis chart"),
                margin={"l": 18, "r": 18, "t": 42, "b": 18},
                height=260,
            )
            st.plotly_chart(fig, use_container_width=True, key=f"{key}_analysis_chart_{chart_idx}")
        error = str(analysis.get("error") or "").strip()
        if error:
            st.caption(f"Analysis error: {error}")

    if not trace:
        tool_calls = list((agent_result or {}).get("tool_calls") or [])
        for tc_idx, tc in enumerate(tool_calls):
            tool_name = str(tc.get("tool_name") or "tool")
            tc_status = str(tc.get("status") or "unknown")
            try:
                args_text = json.dumps(tc.get("arguments") or {}, sort_keys=True, default=str)
            except Exception:
                args_text = str(tc.get("arguments") or {})
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            st.markdown(
                "<div class='sn-zopedia-trace-step'>"
                f"<div class='sn-zopedia-trace-title'>{html.escape(str(tc_idx + 1))}. {html.escape(human_tool)}</div>"
                f"<div class='sn-zopedia-trace-body'>{html.escape(tc_status.capitalize())}"
                + (f"<br><span>{html.escape(args_text)}</span>" if args_text and args_text != "{}" else "")
                + "</div></div>",
                unsafe_allow_html=True,
            )
            preview = str((tc.get("result_summary") or {}).get("preview_text") or "").strip()
            if preview:
                for preview_block in _prepare_answer_markdown_blocks(preview)[:3]:
                    table = parse_markdown_table(preview_block)
                    if table:
                        st.dataframe(pd.DataFrame(table.rows, columns=table.columns), use_container_width=True, hide_index=True)
                    else:
                        st.markdown(preview_block)
            rp = (tc.get("result_summary") or {}).get("render_payload") if isinstance(tc.get("result_summary"), dict) else None
            if isinstance(rp, dict) and rp.get("kind") == "analysis_result":
                _render_analysis_result(rp, key=f"{key_prefix}_tool_{tc_idx}")
        return

    for step_idx, step in enumerate(trace):
        step_type = str(step.get("type") or "")
        tool_label = _humanize_agentic_omnibar_tool_name(step.get("tool_name"))
        title = trace_step_title(step, index=step_idx + 1, tool_label=tool_label)
        body = trace_step_body(step)
        if step_type in {"reasoning", "model_reasoning_trace", "tool_start", "tool_complete", "message"}:
            body_html = ""
            if body:
                body_paragraphs = [
                    html.escape(paragraph.strip())
                    for paragraph in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z0-9])", body)
                    if paragraph.strip()
                ]
                body_html = "".join(f"<p>{paragraph}</p>" for paragraph in body_paragraphs[:6])
            st.markdown(
                "<div class='sn-zopedia-trace-step'>"
                f"<div class='sn-zopedia-trace-title'>{html.escape(title)}</div>"
                f"<div class='sn-zopedia-trace-body'>{body_html}</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        if step_type == "reasoning":
            continue
        elif step_type == "model_reasoning_trace":
            continue
        elif step_type == "tool_start":
            continue
        elif step_type == "tool_complete":
            preview = str(step.get("preview") or "")
            if preview:
                for preview_block in _prepare_answer_markdown_blocks(preview)[:3]:
                    table = parse_markdown_table(preview_block)
                    if table:
                        st.dataframe(pd.DataFrame(table.rows, columns=table.columns), use_container_width=True, hide_index=True)
                    else:
                        st.markdown(preview_block)
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
            if isinstance(rp, dict) and rp.get("kind") == "analysis_result":
                _render_analysis_result(rp, key=f"{key_prefix}_{step_idx}")
            links = step.get("source_links")
            if isinstance(links, list) and links:
                link_parts = []
                for link in links[:5]:
                    if isinstance(link, dict):
                        url = str(link.get("url") or "").strip()
                        lbl = str(link.get("label") or url).strip()
                        if url and _is_public_web_source_url(url):
                            link_parts.append(f"[{lbl}]({url})")
                if link_parts:
                    st.caption("Sources: " + " · ".join(link_parts))
        elif step_type == "message":
            continue


def _thinking_trace_stable_id(panel_id: object) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(panel_id or "trace")).strip("_") or "trace"


def _thinking_trace_open_key(panel_id: object) -> str:
    return f"zopedia_thinking_trace_open_{_thinking_trace_stable_id(panel_id)}"


def _render_thinking_trace_panel(
    trace: list[dict[str, object]],
    agent_result: dict[str, object] | None = None,
    *,
    panel_id: str,
    key_prefix: str,
    default_open: bool = False,
) -> None:
    if not trace:
        return
    open_key = _thinking_trace_open_key(panel_id)
    if open_key not in st.session_state:
        st.session_state[open_key] = bool(default_open)
    is_open = st.toggle(
        f"Thinking Trace ({len(trace)} steps)",
        key=open_key,
    )
    if is_open:
        with st.container(border=True):
            _render_thinking_trace_content(trace, agent_result, key_prefix=key_prefix)


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
            result_cols = _responsive_columns([5, 2])
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


def _split_long_answer_paragraph(paragraph: str, *, max_chars: int = 520) -> list[str]:
    text = " ".join(str(paragraph or "").strip().split())
    if len(text) <= max_chars:
        return [text] if text else []
    if re.match(r"^\s*(```|#{1,6}\s|[-*]\s|\d+\.\s)", paragraph):
        return [paragraph.strip()]
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+(?=(?:\*\*)?[A-Z0-9$])", text)
        if item.strip()
    ]
    if len(sentences) <= 1:
        return [text[i : i + max_chars].strip() for i in range(0, len(text), max_chars)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _prepare_answer_markdown_blocks(answer: str) -> list[str]:
    return prepare_zopedia_answer_markdown_blocks(answer)


def _answer_block_label(block: str, *, idx: int) -> str:
    clean = re.sub(r"```.*?```", "code block", str(block or ""), flags=re.DOTALL)
    clean = re.sub(r"^#{1,6}\s+", "", clean.strip())
    clean = re.sub(r"[*_`>#\[\]()]|https?://\S+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -")
    if not clean:
        clean = "Answer passage"
    if len(clean) > 190:
        clean = clean[:187].rstrip() + "..."
    return f"{idx + 1}. {clean}"


def _answer_block_preview(block: str, *, max_chars: int = 520) -> str:
    text = str(block or "").strip()
    if len(text) <= max_chars:
        return text
    candidate = text[:max_chars].rstrip()
    last_space = candidate.rfind(" ")
    if last_space > max_chars * 0.7:
        candidate = candidate[:last_space].rstrip()
    return candidate + "..."


def _answer_deeper_query(
    *,
    original_query: str,
    selected_text: str,
) -> dict[str, str]:
    selected = str(selected_text or "").strip()
    snippet = re.sub(r"\s+", " ", re.sub(r"[*_`>#\[\]()]|https?://\S+", "", selected)).strip()
    if len(snippet) > 120:
        snippet = snippet[:117].rstrip() + "..."
    return {
        "display_query": f"Go deeper on: {snippet or 'selected passage'}",
        "agent_query": (
            "Dive deeper into this selected passage from the previous Zopedia answer.\n\n"
            f"Original user question:\n{str(original_query or '').strip() or 'Not available'}\n\n"
            f"Selected passage:\n{selected}\n\n"
            "Expand this passage with relevant Zopedia memory, source evidence, market data, and current research where needed. "
            "If the selected passage exposes a stale or missing memory fact, use the safe Zopedia memory mutation path or create a proposal."
        ),
    }


def _render_interactive_answer_markdown(
    answer: str,
    *,
    query: str,
    msg_id: str,
) -> None:
    blocks = _prepare_answer_markdown_blocks(answer)
    if not blocks:
        return
    stable_id = hashlib.sha256(str(msg_id or answer[:120]).encode("utf-8")).hexdigest()[:12]
    for idx, block in enumerate(blocks):
        block = str(block or "").strip()
        if not block:
            continue
        if re.match(r"^#{1,6}\s+", block):
            heading = re.sub(r"^#{1,6}\s+", "", block).strip()
            st.markdown(
                f"<div class='sn-zopedia-answer-heading'>{html.escape(heading)}</div>",
                unsafe_allow_html=True,
            )
            continue
        if block.startswith("```"):
            st.markdown(block)
            continue
        table = parse_markdown_table(block)
        if table:
            st.dataframe(
                pd.DataFrame(table.rows, columns=table.columns),
                use_container_width=True,
                hide_index=True,
                key=f"zopedia_answer_table_{stable_id}_{idx}",
            )
            continue
        state_key = f"zopedia_answer_block_open_{stable_id}_{idx}"
        expand_key = f"zopedia_answer_block_expand_{stable_id}_{idx}"
        is_open = bool(st.session_state.get(state_key))
        is_long = len(block) > 620
        visible_block = block if is_open or not is_long else _answer_block_preview(block)
        st.markdown(visible_block)
        if is_long:
            action_cols = _responsive_columns([1.1, 6.9])
            with action_cols[0]:
                if st.button(
                    "Show less" if is_open else "Read more",
                    key=expand_key,
                    width="stretch",
                ):
                    if is_open:
                        st.session_state.pop(state_key, None)
                    else:
                        st.session_state[state_key] = True
                    st.rerun()


def _render_omnibar_welcome(beats: list[dict[str, object]]) -> None:
    """Render the empty-state welcome screen with clickable example prompts."""
    st.markdown("#### What would you like to research?")

    def _example_label(value: str, *, max_chars: int = 72) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if len(text) <= max_chars:
            return text
        clipped = text[:max_chars].rstrip()
        last_space = clipped.rfind(" ")
        if last_space > max_chars * 0.65:
            clipped = clipped[:last_space].rstrip()
        return clipped + "..."

    examples: list[str] = []
    for beat in beats[:2]:
        sentence = str(beat.get("sentence") or "").strip()
        if sentence:
            examples.append(_example_label(sentence))
    defaults = ["What's driving oil stocks today?", "Analyze semis after CPI", "Compare banks vs software"]
    while len(examples) < 3 and defaults:
        examples.append(defaults.pop(0))
    cols = _responsive_columns(min(len(examples), 3))
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
        st.error(f"Zopedia encountered an error: {error}")

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
        _render_interactive_answer_markdown(
            answer,
            query=str(msg.get("query") or "").strip(),
            msg_id=str(msg.get("msg_id") or hashlib.sha256(answer.encode("utf-8")).hexdigest()[:12]),
        )

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

    # Thinking trace — persistent toggle so reruns do not collapse it.
    msg_id = str(msg.get("msg_id") or "hist")
    if trace:
        _render_thinking_trace_panel(
            trace,
            agent_result,
            panel_id=msg_id,
            key_prefix=f"hist_{msg_id}",
        )

    # Search results
    search_results = list(msg.get("search_results") or [])
    if search_results and (not answer or error):
        _render_inline_search_results(search_results, msg_id)

    # Dig deeper
    if answer and not error:
        if st.button(
            "Dig deeper",
            key=f"dig_{msg.get('msg_id', '')}",
            type="tertiary",
            icon=":material/travel_explore:",
        ):
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
    agent_query, followup_resolved = resolve_aql_zopedia_followup_query(
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

        resolution["agent_result"] = run_aql_zopedia_agent(
            query=agent_query,
            task="chat",
            surface="zopedia.chat",
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
        message="Zopedia response ready.",
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


def _render_agentic_omnibar_debug_panel(resolution: dict[str, object], *, embedded: bool = False) -> None:
    agent_result = dict(resolution.get("agent_result") or {})
    tool_calls = list(agent_result.get("tool_calls") or [])
    transcript = list(st.session_state.get("agentic_omnibar_transcript") or [])

    panel = st.container() if embedded else st.expander("Admin Debug", expanded=False)
    with panel:
        st.caption("Route")
        route_cols = _responsive_columns(4)
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
            agent_meta_cols = _responsive_columns(4)
            agent_meta_cols[0].metric("Agent Status", str(agent_result.get("status") or "n/a").capitalize())
            agent_meta_cols[1].metric("Tool Calls", str(len(tool_calls)))
            agent_meta_cols[2].metric("Agent Confidence", str(agent_result.get("confidence") or "low").capitalize())
            agent_meta_cols[3].metric("Model", str(agent_result.get("model") or "n/a"))
            limitations = [str(item).strip() for item in list(agent_result.get("limitations") or []) if str(item).strip()]
            if limitations:
                st.caption("Limitations: " + " | ".join(limitations[:3]))
            if tool_calls:
                with st.expander("Tool Calls", expanded=False):
                    for call_id, tool_call in enumerate(tool_calls):
                        with st.container(border=True):
                            header_cols = _responsive_columns([3.2, 1.1, 1.7])
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


def _zopedia_query_from_page(row: pd.Series) -> dict[str, str]:
    page_id = str(row.get("page_id") or "").strip()
    title = str(row.get("title") or page_id).strip()
    return {
        "display_query": f"Read Zopedia page: {title}",
        "agent_query": (
            "Read this Zopedia page and explain the useful implications for Spectral Nature. "
            f"Use zopedia.read_page with page_id={page_id}. "
            "If the page is stale, wrong, or missing linked context, use zopedia.apply_mutation for safe reversible updates "
            "or zopedia.propose_change for risky changes."
        ),
    }


def _zopedia_source_open_args(ref: dict[str, object]) -> dict[str, str]:
    return {
        "page_id": str(ref.get("page_id") or "").strip(),
        "ref": str(ref.get("ref") or "").strip(),
        "kind": str(ref.get("kind") or "").strip(),
        "chunk_record_id": str(ref.get("chunk_record_id") or "").strip(),
        "canonical_document_id": str(ref.get("canonical_document_id") or "").strip(),
        "url": str(ref.get("url") or ref.get("source_url") or "").strip(),
    }


def _render_zopedia_source_inspection() -> None:
    inspected = st.session_state.get("_zopedia_inspected_sources")
    if not isinstance(inspected, dict):
        return
    page = inspected.get("page") if isinstance(inspected.get("page"), dict) else {}
    page_title = str(page.get("title") or inspected.get("page_id") or "Selected page").strip()
    sources = [item for item in list(inspected.get("sources") or []) if isinstance(item, dict)]
    st.markdown("**Sources**")
    st.caption(f"{page_title} | {len(sources)} source reference(s)")
    if not sources:
        st.caption("No source references found for this page.")
        return
    for idx, ref in enumerate(sources[:12]):
        title = str(ref.get("title") or ref.get("source_title") or ref.get("url") or ref.get("ref") or "Source").strip()
        kind = str(ref.get("kind") or "source").strip()
        url = str(ref.get("url") or ref.get("source_url") or "").strip()
        excerpt = str(ref.get("body_excerpt") or ref.get("summary_text") or "").strip()
        ref_cols = _responsive_columns([5.5, 1.2])
        with ref_cols[0]:
            st.markdown(f"**{title}**")
            st.caption(" | ".join(part for part in (kind, url) if part))
            if excerpt:
                st.write(excerpt)
        with ref_cols[1]:
            open_key = hashlib.sha256(
                json.dumps(_zopedia_source_open_args(ref), sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
            if st.button("Open", key=f"zopedia_open_source_{idx}_{open_key}", use_container_width=True):
                try:
                    st.session_state["_zopedia_open_source"] = zopedia_read_source(**_zopedia_source_open_args(ref))
                except Exception as exc:
                    st.session_state["_zopedia_open_source"] = {
                        "status": "error",
                        "message": str(exc),
                        "title": title,
                    }
                st.rerun()
    opened = st.session_state.get("_zopedia_open_source")
    if isinstance(opened, dict):
        status = str(opened.get("status") or "").strip()
        title = str(opened.get("title") or opened.get("source_title") or opened.get("url") or "Source").strip()
        url = str(opened.get("url") or opened.get("source_url") or "").strip()
        text = str(opened.get("text") or opened.get("display_excerpt") or opened.get("message") or "").strip()
        st.markdown("**Source Text**")
        st.caption(" | ".join(part for part in (status, str(opened.get("source_kind") or "").strip(), title, url) if part))
        if text:
            st.text_area("Opened source", value=text, height=260, disabled=True, key="zopedia_open_source_text")
        else:
            st.caption("No retained text is available for this source reference.")


def _render_zopedia_page_results(frame: pd.DataFrame, *, key_prefix: str) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        st.caption("No Zopedia pages found.")
        return
    for idx, row in frame.head(12).iterrows():
        title = str(row.get("title") or "Untitled page").strip()
        page_type = str(row.get("page_type") or "page").strip()
        summary = str(row.get("summary") or "").strip()
        source_urls = list(row.get("source_urls") or [])
        cols = _responsive_columns([4.8, 1.2, 1.2])
        with cols[0]:
            st.markdown(f"**{title}**")
            meta = page_type
            if source_urls:
                meta += f" | {source_urls[0]}"
            st.caption(meta)
            if summary:
                st.write(summary)
        with cols[1]:
            if st.button("Read", key=f"{key_prefix}_read_{idx}", use_container_width=True):
                st.session_state["_omnibar_pending_query"] = _zopedia_query_from_page(row)
                st.rerun()
        with cols[2]:
            if st.button("Sources", key=f"{key_prefix}_sources_{idx}", use_container_width=True):
                page_id = str(row.get("page_id") or "").strip()
                try:
                    st.session_state["_zopedia_inspected_sources"] = zopedia_sources_for_page(page_id=page_id)
                    st.session_state.pop("_zopedia_open_source", None)
                except Exception as exc:
                    st.session_state["_zopedia_inspected_sources"] = {
                        "status": "error",
                        "page_id": page_id,
                        "sources": [],
                        "message": str(exc),
                    }
                st.rerun()


def _zopedia_chat_user_key() -> str:
    context = _current_user_context()
    if context is None:
        return "anonymous"
    return (
        str(context.email or context.user_id or context.label or "anonymous").strip()
        or "anonymous"
    )


def _zopedia_thread_messages_for_session(thread_payload: dict[str, object]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for item in list(thread_payload.get("messages") or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        payload = dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {}
        content = str(item.get("content") or "").strip()
        if role == "assistant":
            assistant_msg = {"role": "assistant", **payload}
            if content and not assistant_msg.get("content"):
                assistant_msg["content"] = content
            if item.get("run_id") and not assistant_msg.get("run_id"):
                assistant_msg["run_id"] = item.get("run_id")
            messages.append(assistant_msg)
        elif role == "user":
            messages.append({"role": "user", "content": content})
    return messages


def _ensure_zopedia_chat_thread(*, user_key: str, title: str) -> str:
    thread_id = str(st.session_state.get("agentic_omnibar_thread_id") or "").strip()
    if thread_id:
        return thread_id
    created = create_chat_thread(user_key=user_key, title=title, metadata={"source": "zopedia_chat"})
    thread_id = str((created or {}).get("thread_id") or "").strip()
    if thread_id:
        st.session_state["agentic_omnibar_thread_id"] = thread_id
        st.session_state["agentic_omnibar_thread_title"] = str((created or {}).get("title") or title).strip()
    return thread_id


def _persist_zopedia_chat_message(
    *,
    thread_id: str,
    user_key: str,
    role: str,
    content: str,
    payload: dict[str, object] | None = None,
    run_id: str = "",
    title: str = "",
) -> str:
    saved = append_chat_message(
        thread_id=thread_id or None,
        user_key=user_key,
        role=role,
        content=content,
        payload=payload or {},
        run_id=run_id,
        title=title or content,
    )
    saved_thread_id = str((saved or {}).get("thread_id") or thread_id or "").strip()
    if saved_thread_id:
        st.session_state["agentic_omnibar_thread_id"] = saved_thread_id
    return saved_thread_id


def _reset_zopedia_chat_session() -> None:
    for state_key in [
        "agentic_omnibar_chat",
        "agentic_omnibar_thread_id",
        "agentic_omnibar_thread_title",
        "agentic_omnibar_resolution",
        "agentic_omnibar_transcript",
        "agentic_omnibar_thinking_trace",
    ]:
        st.session_state.pop(state_key, None)


def _format_zopedia_thread_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return text[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%b %d, %I:%M %p").replace(" 0", " ")


def _zopedia_clean_title(value: object, *, fallback: str = "New Zopedia chat", max_chars: int = 72) -> str:
    title = re.sub(r"\s+", " ", str(value or "").strip())
    if not title:
        title = fallback
    if len(title) <= max_chars:
        return title
    clipped = title[:max_chars].rstrip()
    last_space = clipped.rfind(" ")
    if last_space > max_chars * 0.65:
        clipped = clipped[:last_space].rstrip()
    return clipped + "..."


def _zopedia_thread_meta(item: dict[str, object]) -> str:
    updated = _format_zopedia_thread_time(item.get("updated_at"))
    created = _format_zopedia_thread_time(item.get("created_at"))
    if updated:
        return f"Updated {updated}"
    if created:
        return f"Created {created}"
    return "Saved chat"


def _load_zopedia_thread_into_session(*, thread_id: str, user_key: str) -> bool:
    loaded = load_chat_thread(thread_id=thread_id, user_key=user_key)
    if not isinstance(loaded, dict):
        return False
    st.session_state["agentic_omnibar_chat"] = _zopedia_thread_messages_for_session(loaded)
    st.session_state["agentic_omnibar_thread_id"] = str(loaded.get("thread_id") or "")
    st.session_state["agentic_omnibar_thread_title"] = str(loaded.get("title") or "Zopedia chat")
    for state_key in list(st.session_state.keys()):
        if str(state_key).startswith("zopedia_thinking_trace_open_"):
            st.session_state.pop(state_key, None)
    return True


def _zopedia_recent_threads(*, user_key: str, limit: int = 30) -> list[dict[str, object]]:
    try:
        return list(list_chat_threads(user_key=user_key, limit=limit) or [])
    except Exception:
        return []


def _render_zopedia_new_chat_action(*, key: str, label: str = "New") -> None:
    if st.button(label, key=key, type="tertiary", icon=":material/add:", width="stretch"):
        _reset_zopedia_chat_session()
        st.session_state.pop("zopedia_workspace_active_panel", None)
        st.rerun()


def _render_zopedia_thread_list(
    *,
    user_key: str,
    threads: list[dict[str, object]] | None = None,
    limit: int = 12,
    key_prefix: str = "zopedia_thread",
    show_filter: bool = True,
) -> None:
    threads = list(threads if threads is not None else _zopedia_recent_threads(user_key=user_key, limit=30))
    if not threads:
        st.caption("No saved chats yet.")
        return

    search_text = ""
    if show_filter:
        search_text = st.text_input(
            "Search chats",
            key=f"{key_prefix}_filter",
            placeholder="Search recent chats...",
            label_visibility="collapsed",
        ).strip().lower()

    current_thread_id = str(st.session_state.get("agentic_omnibar_thread_id") or "").strip()
    current_chat_loaded = bool(st.session_state.get("agentic_omnibar_chat"))
    visible_threads: list[dict[str, object]] = []
    for item in threads:
        title = _zopedia_clean_title(item.get("title"), fallback="Zopedia chat", max_chars=120)
        if search_text and search_text not in title.lower():
            continue
        visible_threads.append(item)

    if not visible_threads:
        st.caption("No chats match that filter.")
        return

    for idx, item in enumerate(visible_threads[:limit]):
        thread_id = str(item.get("thread_id") or "").strip()
        if not thread_id:
            continue
        title = _zopedia_clean_title(item.get("title"), fallback="Zopedia chat", max_chars=68)
        meta = _zopedia_thread_meta(item)
        is_current = thread_id == current_thread_id and current_chat_loaded
        label = f"{title}\n{meta}{' · Current' if is_current else ''}"
        if st.button(
            label,
            key=f"{key_prefix}_row_{idx}_{hashlib.sha256(thread_id.encode('utf-8')).hexdigest()[:10]}",
            type="primary" if is_current else "tertiary",
            width="stretch",
            disabled=is_current,
        ):
            if _load_zopedia_thread_into_session(thread_id=thread_id, user_key=user_key):
                st.rerun()
            st.warning("Could not load that chat.")


def _render_zopedia_chat_history_controls(*, user_key: str) -> None:
    current_title = _zopedia_clean_title(st.session_state.get("agentic_omnibar_thread_title"))
    st.markdown(f"**{current_title}**")
    _render_zopedia_new_chat_action(key="zopedia_new_chat", label="New chat")
    _render_zopedia_thread_list(user_key=user_key, key_prefix="zopedia_history_panel")


def _ingest_zopedia_source_from_inputs(
    *,
    url: str,
    title: str,
    source_text: str,
    uploaded_source: object | None,
) -> dict[str, object]:
    resolved_title = str(title or "").strip()
    resolved_text = str(source_text or "").strip()
    source_type = "source"
    source_metadata: dict[str, object] = {}
    normalized_url = str(url or "").strip()
    if not resolved_text and uploaded_source is not None:
        upload_result = prepare_zopedia_uploaded_source(
            filename=str(getattr(uploaded_source, "name", "") or "uploaded-source"),
            content=uploaded_source.getvalue(),
            content_type=str(getattr(uploaded_source, "type", "") or ""),
        )
        if upload_result.get("status") != "ok":
            return {
                "status": upload_result.get("status") or "upload_unavailable",
                "message": upload_result.get("message") or "This upload could not be read.",
                "pages": [],
                "page_count": 0,
                "enrichment_status": "not_started",
            }
        resolved_text = str(upload_result.get("source_text") or "").strip()
        resolved_title = resolved_title or str(upload_result.get("title") or "").strip()
        source_type = str(upload_result.get("source_type") or "uploaded_file").strip()
        source_metadata = dict(upload_result.get("metadata") or {})
    if not resolved_text and normalized_url:
        if "youtube.com" in normalized_url or "youtu.be" in normalized_url:
            transcript = fetch_youtube_transcript(normalized_url)
            resolved_text = str(transcript.get("transcript") or "").strip()
            source_type = "youtube_transcript"
            if not resolved_title:
                resolved_title = f"YouTube {transcript.get('video_id') or normalized_url}"
        else:
            page = browse_page(normalized_url, max_text_chars=12000)
            resolved_text = str(page.get("text") or page.get("excerpt") or "").strip()
            resolved_title = resolved_title or str(page.get("title") or normalized_url).strip()
            source_type = "web_page"
    return ingest_zopedia_source(
        title=resolved_title or normalized_url or "Untitled Source",
        source_text=resolved_text,
        url=normalized_url,
        source_type=source_type,
        source_metadata=source_metadata,
        llm_client=load_aql_zopedia_llm_client(surface="zopedia.ui.ingest_source"),
    )


def _render_zopedia_source_upload_compact() -> None:
    url_tab, upload_tab, text_tab = st.tabs(["URL", "File", "Text"])
    with url_tab:
        with st.form("zopedia_compact_url_source_form"):
            url = st.text_input("URL or YouTube", key="zopedia_compact_add_url")
            title = st.text_input("Title", key="zopedia_compact_url_title")
            submitted = st.form_submit_button("Add URL")
        if submitted:
            st.session_state["_zopedia_last_compact_ingest"] = _ingest_zopedia_source_from_inputs(
                url=url,
                title=title,
                source_text="",
                uploaded_source=None,
            )
            st.rerun()
    with upload_tab:
        with st.form("zopedia_compact_file_source_form"):
            uploaded_source = st.file_uploader(
                "Upload file",
                key="zopedia_compact_source_file",
                accept_multiple_files=False,
            )
            title = st.text_input("Title", key="zopedia_compact_file_title")
            submitted = st.form_submit_button("Add File")
        if submitted:
            st.session_state["_zopedia_last_compact_ingest"] = _ingest_zopedia_source_from_inputs(
                url="",
                title=title,
                source_text="",
                uploaded_source=uploaded_source,
            )
            st.rerun()
    with text_tab:
        with st.form("zopedia_compact_text_source_form"):
            title = st.text_input("Title", key="zopedia_compact_text_title")
            url = st.text_input("Source URL", key="zopedia_compact_text_url")
            source_text = st.text_area(
                "Source text",
                key="zopedia_compact_add_source_text",
                height=180,
                placeholder="Paste memo text, transcript, notes, or article text.",
            )
            submitted = st.form_submit_button("Add Text")
        if submitted:
            st.session_state["_zopedia_last_compact_ingest"] = _ingest_zopedia_source_from_inputs(
                url=url,
                title=title,
                source_text=source_text,
                uploaded_source=None,
            )
            st.rerun()
    last_ingest = st.session_state.get("_zopedia_last_compact_ingest")
    if isinstance(last_ingest, dict):
        if last_ingest.get("status") == "stored":
            st.success(
                f"Stored {last_ingest.get('page_count') or 0} page(s). "
                f"Enrichment: {last_ingest.get('enrichment_status') or 'unknown'}."
            )
        else:
            st.warning(str(last_ingest.get("message") or last_ingest.get("status") or "Zopedia did not store a page."))


def _ensure_zopedia_shell_styles() -> None:
    st.markdown(
        """
        <style>
        [class*="st-key-zopedia_thread_rail_row_"] button,
        [class*="st-key-zopedia_history_panel_row_"] button,
        [class*="st-key-zopedia_mobile_thread_row_"] button {
            justify-content: flex-start !important;
            text-align: left !important;
            min-height: 3.1rem !important;
            border-radius: 0.45rem !important;
            padding: 0.62rem 0.72rem !important;
            white-space: pre-line !important;
        }
        [class*="st-key-zopedia_thread_rail_row_"] button p,
        [class*="st-key-zopedia_history_panel_row_"] button p,
        [class*="st-key-zopedia_mobile_thread_row_"] button p {
            text-align: left !important;
            white-space: pre-line !important;
            line-height: 1.22 !important;
        }
        [class*="st-key-zopedia_rail_new"] button,
        [class*="st-key-zopedia_mobile_new"] button,
        [class*="st-key-zopedia_attach"] button,
        [class*="st-key-zopedia_admin_drawer"] button {
            border-radius: 0.42rem !important;
        }
        .sn-zopedia-title {
            margin: 0 0 0.18rem 0;
            color: var(--sn-ink);
            font-size: 1.65rem;
            font-weight: 760;
            line-height: 1.1;
            letter-spacing: 0;
        }
        .sn-zopedia-thread-title {
            margin: 0 0 0.65rem 0;
            color: var(--sn-muted-strong);
            font-size: 0.88rem;
            line-height: 1.35;
        }
        .sn-zopedia-rail-title {
            color: var(--sn-muted);
            font-size: 0.74rem;
            font-weight: 720;
            letter-spacing: 0.02em;
            text-transform: uppercase;
            margin: 0.8rem 0 0.45rem 0;
        }
        .sn-zopedia-source-chip {
            display: inline-flex;
            align-items: center;
            max-width: 100%;
            margin: 0.15rem 0.25rem 0.25rem 0;
            padding: 0.24rem 0.48rem;
            border: 1px solid var(--sn-border);
            border-radius: 999px;
            color: var(--sn-muted-strong);
            font-size: 0.78rem;
            line-height: 1.2;
            background: rgba(255, 255, 255, 0.03);
        }
        .sn-zopedia-main-spacer {
            height: 0.25rem;
        }
        .sn-zopedia-answer-heading {
            margin: 1.15rem 0 0.45rem 0;
            color: var(--sn-ink);
            font-size: 1.08rem;
            font-weight: 760;
            line-height: 1.25;
            letter-spacing: 0;
        }
        .sn-zopedia-trace-step {
            margin: 0.48rem 0 0.62rem 0;
            padding: 0.76rem 0.88rem;
            border: 1px solid rgba(148, 163, 184, 0.22);
            border-radius: 0.45rem;
            background: rgba(15, 23, 42, 0.18);
        }
        .sn-zopedia-trace-title {
            color: var(--sn-ink);
            font-size: 0.9rem;
            font-weight: 760;
            line-height: 1.25;
            margin-bottom: 0.36rem;
        }
        .sn-zopedia-trace-body {
            color: var(--sn-muted-strong);
            font-size: 0.86rem;
            line-height: 1.48;
        }
        .sn-zopedia-trace-body p {
            margin: 0 0 0.42rem 0;
        }
        [data-testid="stChatMessageAvatarUser"],
        [data-testid="stChatMessageAvatarAssistant"] {
            background: rgba(148, 163, 184, 0.14) !important;
            color: var(--sn-muted-strong) !important;
            border: 1px solid rgba(148, 163, 184, 0.20) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_zopedia_source_status() -> None:
    last_ingest = st.session_state.get("_zopedia_last_compact_ingest")
    if not isinstance(last_ingest, dict):
        return
    if last_ingest.get("status") == "stored":
        pages = list(last_ingest.get("pages") or [])
        labels = [
            _zopedia_clean_title(page.get("title") if isinstance(page, dict) else page, fallback="Source", max_chars=44)
            for page in pages[:3]
        ]
        if not labels:
            labels = [f"{int(last_ingest.get('page_count') or 0)} page(s) stored"]
        chips = "".join(f"<span class='sn-zopedia-source-chip'>{html.escape(label)}</span>" for label in labels)
        st.markdown(chips, unsafe_allow_html=True)
    else:
        st.warning(str(last_ingest.get("message") or last_ingest.get("status") or "Zopedia did not store a page."))


def _render_zopedia_attach_popover(*, key_prefix: str = "zopedia_attach") -> None:
    with st.popover("Attach source", icon=":material/attach_file:", use_container_width=True):
        st.caption("Add source material to Zopedia memory before or during a chat.")
        url_tab, text_tab = st.tabs(["URL", "Text"])
        with url_tab:
            with st.form(f"{key_prefix}_url_form"):
                url = st.text_input("URL or YouTube", key=f"{key_prefix}_url")
                title = st.text_input("Title", key=f"{key_prefix}_url_title")
                submitted = st.form_submit_button("Attach URL")
            if submitted:
                st.session_state["_zopedia_last_compact_ingest"] = _ingest_zopedia_source_from_inputs(
                    url=url,
                    title=title,
                    source_text="",
                    uploaded_source=None,
                )
                st.rerun()
        with text_tab:
            with st.form(f"{key_prefix}_text_form"):
                title = st.text_input("Title", key=f"{key_prefix}_text_title")
                url = st.text_input("Source URL", key=f"{key_prefix}_text_url")
                source_text = st.text_area(
                    "Source text",
                    key=f"{key_prefix}_text",
                    height=160,
                    placeholder="Paste memo text, transcript, notes, or article text.",
                )
                submitted = st.form_submit_button("Attach Text")
            if submitted:
                st.session_state["_zopedia_last_compact_ingest"] = _ingest_zopedia_source_from_inputs(
                    url=url,
                    title=title,
                    source_text=source_text,
                    uploaded_source=None,
                )
                st.rerun()
        _render_zopedia_source_status()


def _ingest_zopedia_chat_uploads(files: object) -> list[dict[str, object]]:
    if not files:
        return []
    if not isinstance(files, list):
        files = [files]
    ingested: list[dict[str, object]] = []
    for uploaded in files:
        if uploaded is None:
            continue
        result = _ingest_zopedia_source_from_inputs(
            url="",
            title=str(getattr(uploaded, "name", "") or "Uploaded source"),
            source_text="",
            uploaded_source=uploaded,
        )
        ingested.append(result)
    if ingested:
        stored = [result for result in ingested if result.get("status") == "stored"]
        if stored:
            st.session_state["_zopedia_last_compact_ingest"] = stored[-1]
        else:
            st.session_state["_zopedia_last_compact_ingest"] = ingested[-1]
    return ingested


def _zopedia_chat_input_parts(value: object) -> tuple[str, list[object]]:
    if value is None:
        return "", []
    if isinstance(value, str):
        return value.strip(), []
    text = ""
    files: list[object] = []
    if isinstance(value, dict):
        text = str(value.get("text") or value.get("message") or "").strip()
        raw_files = value.get("files") or []
    else:
        text = str(getattr(value, "text", "") or getattr(value, "message", "") or "").strip()
        raw_files = getattr(value, "files", []) or []
    if isinstance(raw_files, list):
        files = raw_files
    elif raw_files:
        files = [raw_files]
    return text, files


def _render_zopedia_memory_panel() -> None:
    search_tab, proposals_tab, mutations_tab, health_tab = st.tabs(["Search", "Proposals", "Mutations", "Health"])

    with search_tab:
        include_debug_sources = st.checkbox(
            "Include eval/debug memory",
            value=False,
            key="zopedia_include_debug_memory",
            help="Admin-only view for product eval pages and other non-user memory.",
        )
        search_cols = _responsive_columns([4.6, 1.2])
        with search_cols[0]:
            query = st.text_input(
                "Search pages",
                key="zopedia_page_search_query",
                placeholder="Search concepts, events, tickers, transcripts...",
            )
        with search_cols[1]:
            limit = st.number_input("Limit", min_value=4, max_value=20, value=8, step=1, key="zopedia_page_search_limit")
        try:
            pages = search_zopedia_pages(query=query, limit=int(limit), include_debug_sources=include_debug_sources)
        except Exception as exc:
            pages = pd.DataFrame()
            st.warning(f"Zopedia memory is unavailable: {exc}")
        _render_zopedia_page_results(pages, key_prefix="zopedia_search")
        _render_zopedia_source_inspection()

    with proposals_tab:
        with st.form("zopedia_proposal_form"):
            proposal_type = st.selectbox(
                "Change",
                ["update", "delete", "add_page", "split", "merge"],
                key="zopedia_proposal_type",
            )
            page_id = st.text_input("Page ID", key="zopedia_proposal_page_id")
            title = st.text_input("Proposal title", key="zopedia_proposal_title")
            rationale = st.text_area("Rationale", key="zopedia_proposal_rationale", height=110)
            proposal_submitted = st.form_submit_button("Propose Change")
        if proposal_submitted:
            proposal = build_zopedia_change_proposal(
                proposal_type=proposal_type,
                page_id=page_id,
                title=title,
                rationale=rationale,
                payload={"source": "zopedia_ui"},
            )
            persist_zopedia_change_proposals([proposal])
            st.session_state["_zopedia_last_proposal"] = proposal
            st.rerun()
        last_proposal = st.session_state.get("_zopedia_last_proposal")
        if isinstance(last_proposal, dict):
            st.success(f"Proposed: {last_proposal.get('title')}")
        proposals = list_zopedia_change_proposals(status="open", limit=12)
        if isinstance(proposals, pd.DataFrame) and not proposals.empty:
            for _, row in proposals.iterrows():
                st.markdown(f"**{row.get('title') or row.get('proposal_id')}**")
                st.caption(f"{row.get('proposal_type')} | {row.get('page_id') or 'new page'}")
                rationale = str(row.get("rationale") or "").strip()
                if rationale:
                    st.write(rationale)
        else:
            st.caption("No open Zopedia proposals.")

    with mutations_tab:
        last_rollback = st.session_state.get("_zopedia_last_rollback")
        if isinstance(last_rollback, dict):
            st.info(
                f"Last rollback: {last_rollback.get('status') or 'unknown'} "
                f"for {last_rollback.get('mutation_id') or 'unknown mutation'}."
            )
        try:
            mutations = list_zopedia_mutation_audits(limit=12)
        except Exception as exc:
            mutations = pd.DataFrame()
            st.warning(f"Zopedia mutation audit is unavailable: {exc}")
        if isinstance(mutations, pd.DataFrame) and not mutations.empty:
            for idx, row in mutations.iterrows():
                mutation_id = str(row.get("mutation_id") or "").strip()
                mutation_type = str(row.get("mutation_type") or "mutation").strip()
                status = str(row.get("status") or "unknown").strip()
                risk = str(row.get("risk_level") or "unknown").strip()
                source = str(row.get("source") or "").strip()
                try:
                    page_ids = json.loads(str(row.get("page_ids_json") or "[]"))
                except Exception:
                    page_ids = []
                cols = _responsive_columns([4.5, 1.2])
                with cols[0]:
                    st.markdown(f"**{mutation_type}**")
                    st.caption(" | ".join(part for part in (status, risk, source, mutation_id) if part))
                    st.write(f"{len(page_ids) if isinstance(page_ids, list) else 0} page(s) affected.")
                with cols[1]:
                    rollback_disabled = status != "committed" or mutation_type == "rollback"
                    if st.button(
                        "Rollback",
                        key=f"zopedia_rollback_{idx}",
                        use_container_width=True,
                        disabled=rollback_disabled,
                    ):
                        try:
                            st.session_state["_zopedia_last_rollback"] = rollback_zopedia_mutation(
                                mutation_id=mutation_id,
                                source="zopedia.ui.rollback",
                            )
                        except Exception as exc:
                            st.session_state["_zopedia_last_rollback"] = {
                                "status": "error",
                                "mutation_id": mutation_id,
                                "message": str(exc),
                            }
                        st.rerun()
        else:
            st.caption("No Zopedia mutation audit rows yet.")

    with health_tab:
        try:
            reports = list_zopedia_maintenance_reports(limit=3)
        except Exception as exc:
            reports = pd.DataFrame()
            st.warning(f"Zopedia maintenance reports are unavailable: {exc}")
        if isinstance(reports, pd.DataFrame) and not reports.empty:
            latest = dict(reports.iloc[0].to_dict())
            try:
                summary = json.loads(str(latest.get("summary_json") or "{}"))
            except Exception:
                summary = {}
            metric_cols = _responsive_columns(4)
            metric_cols[0].metric("Pages", int(latest.get("page_count") or 0))
            metric_cols[1].metric("Edges", int(latest.get("edge_count") or 0))
            metric_cols[2].metric("Communities", int(summary.get("community_count") or 0))
            metric_cols[3].metric("Issues", int(latest.get("issue_count") or 0))
            st.caption(
                "Latest maintenance run: "
                + " | ".join(
                    str(part)
                    for part in (
                        latest.get("run_id"),
                        latest.get("status"),
                        latest.get("created_at_utc"),
                    )
                    if part
                )
            )
            communities = list(summary.get("top_communities") or []) if isinstance(summary, dict) else []
            if communities:
                st.markdown("**Important communities**")
                community_rows = [
                    {
                        "label": str(item.get("label") or "").strip(),
                        "page_count": int(item.get("page_count") or 0),
                        "score": float(item.get("score") or 0.0),
                        "central_page_ids": ", ".join(str(pid) for pid in list(item.get("central_page_ids") or [])[:3]),
                    }
                    for item in communities[:8]
                    if isinstance(item, dict)
                ]
                st.dataframe(pd.DataFrame(community_rows), hide_index=True, use_container_width=True)
            try:
                issues = json.loads(str(latest.get("issue_rows_json") or "[]"))
            except Exception:
                issues = []
            if issues:
                st.markdown("**Review queue**")
                issue_rows = [
                    {
                        "severity": str(item.get("severity") or "").strip(),
                        "issue_type": str(item.get("issue_type") or "").strip(),
                        "title": str(item.get("title") or "").strip(),
                        "suggested_action": str(item.get("suggested_action") or "").strip(),
                    }
                    for item in issues[:12]
                    if isinstance(item, dict)
                ]
                st.dataframe(pd.DataFrame(issue_rows), hide_index=True, use_container_width=True)
        else:
            st.caption("No Zopedia maintenance report has been written yet.")


def _render_zopedia_left_rail(*, user_key: str) -> None:
    st.markdown("<div class='sn-zopedia-rail-title'>Threads</div>", unsafe_allow_html=True)
    _render_zopedia_new_chat_action(key="zopedia_rail_new", label="New chat")
    _render_zopedia_thread_list(
        user_key=user_key,
        key_prefix="zopedia_thread_rail",
        limit=14,
        show_filter=True,
    )


def _render_zopedia_right_drawer(*, last_resolution: dict[str, object] | None) -> None:
    st.markdown("<div class='sn-zopedia-rail-title'>Context</div>", unsafe_allow_html=True)
    with st.popover("Sources", icon=":material/source_notes:", use_container_width=True, key="zopedia_sources_drawer"):
        _render_zopedia_source_status()
        st.divider()
        _render_zopedia_source_upload_compact()
    if _current_user_is_admin():
        with st.popover("Admin", icon=":material/admin_panel_settings:", use_container_width=True, key="zopedia_admin_drawer"):
            debug_tab, memory_tab = st.tabs(["Debug", "Memory"])
            with debug_tab:
                if last_resolution:
                    _render_agentic_omnibar_debug_panel(last_resolution, embedded=True)
                else:
                    st.caption("No agent run selected yet.")
            with memory_tab:
                _render_zopedia_memory_panel()


def _render_zopedia_mobile_context(*, user_key: str, last_resolution: dict[str, object] | None) -> None:
    action_cols = _responsive_columns([1, 1, 1])
    with action_cols[0]:
        _render_zopedia_new_chat_action(key="zopedia_mobile_new", label="New")
    with action_cols[1]:
        with st.popover("History", icon=":material/history:", use_container_width=True, key="zopedia_mobile_history"):
            _render_zopedia_thread_list(
                user_key=user_key,
                key_prefix="zopedia_mobile_thread",
                limit=12,
                show_filter=True,
            )
    with action_cols[2]:
        with st.popover("Sources", icon=":material/attach_file:", use_container_width=True, key="zopedia_mobile_sources"):
            _render_zopedia_source_status()
            st.divider()
            _render_zopedia_source_upload_compact()
    if _current_user_is_admin():
        with st.popover("Admin", icon=":material/admin_panel_settings:", use_container_width=True, key="zopedia_mobile_admin"):
            if last_resolution:
                _render_agentic_omnibar_debug_panel(last_resolution, embedded=True)
            else:
                st.caption("No agent run selected yet.")


def _render_zopedia_header() -> None:
    current_title = _zopedia_clean_title(
        st.session_state.get("agentic_omnibar_thread_title"),
        fallback="Ask about a market, company, source, or theme",
        max_chars=96,
    )
    st.markdown("<div class='sn-zopedia-title'>Zopedia</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='sn-zopedia-thread-title'>{html.escape(current_title)}</div>",
        unsafe_allow_html=True,
    )


def _render_agentic_omnibar_section(
    cfg: AppConfig,
    *,
    force_data_refresh: bool,
) -> None:
    _ensure_zopedia_shell_styles()

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
    zopedia_user_key = _zopedia_chat_user_key()
    last_admin_resolution = None
    if _current_user_is_admin() and chat:
        last_assistant = next(
            (m for m in reversed(chat) if m.get("role") == "assistant"),
            None,
        )
        if last_assistant and last_assistant.get("resolution"):
            last_admin_resolution = dict(last_assistant["resolution"])

    conversation_area = st.container()
    if _mobile_layout_active():
        _render_zopedia_header()
        st.markdown("<div class='sn-zopedia-main-spacer'></div>", unsafe_allow_html=True)

        conversation_area = st.container()
        with conversation_area:
            if not chat:
                _render_omnibar_welcome(beats)
            for msg in chat:
                with st.chat_message(str(msg.get("role") or "assistant")):
                    if msg.get("role") == "assistant":
                        _render_agent_response_message(msg)
                    else:
                        st.markdown(str(msg.get("content") or ""))

        _render_zopedia_source_status()
        _render_zopedia_attach_popover(key_prefix="zopedia_attach_mobile")
        _render_zopedia_mobile_context(user_key=zopedia_user_key, last_resolution=last_admin_resolution)
    else:
        rail_col, main_col, context_col = _responsive_columns([1.05, 3.15, 0.95], gap="large")
        with rail_col:
            _render_zopedia_left_rail(user_key=zopedia_user_key)
        with main_col:
            _render_zopedia_header()
            conversation_area = st.container()
            with conversation_area:
                if not chat:
                    _render_omnibar_welcome(beats)
                for msg in chat:
                    with st.chat_message(str(msg.get("role") or "assistant")):
                        if msg.get("role") == "assistant":
                            _render_agent_response_message(msg)
                        else:
                            st.markdown(str(msg.get("content") or ""))
            _render_zopedia_source_status()
            _render_zopedia_attach_popover(key_prefix="zopedia_attach_desktop")
        with context_col:
            _render_zopedia_right_drawer(last_resolution=last_admin_resolution)

    # ── Input handling ──
    pending_query = st.session_state.pop("_omnibar_pending_query", None)
    typed_value = st.chat_input(
        "Ask about any market, ticker, source, or event...",
        accept_file="multiple",
        file_type=["txt", "md", "csv", "json", "pdf"],
        key="zopedia_chat_input",
    )
    typed_query, uploaded_files = _zopedia_chat_input_parts(typed_value)
    ingested_uploads = _ingest_zopedia_chat_uploads(uploaded_files)
    if ingested_uploads and not typed_query and pending_query is None:
        st.rerun()
    if isinstance(pending_query, dict):
        display_query = str(
            pending_query.get("display_query") or pending_query.get("query") or pending_query.get("agent_query") or ""
        ).strip()
        active_query = str(pending_query.get("agent_query") or display_query).strip()
    else:
        active_query = str(pending_query or typed_query or "").strip()
        display_query = active_query

    stored_uploads = [result for result in ingested_uploads if isinstance(result, dict) and result.get("status") == "stored"]
    if active_query and stored_uploads:
        attached_titles: list[str] = []
        for result in stored_uploads:
            pages = list(result.get("pages") or [])
            for page in pages[:2]:
                if isinstance(page, dict):
                    title = _zopedia_clean_title(page.get("title"), fallback="Attached source", max_chars=80)
                    if title not in attached_titles:
                        attached_titles.append(title)
        if attached_titles:
            active_query = (
                f"{active_query}\n\n"
                "Attached source material was added to Zopedia memory for this turn: "
                + "; ".join(attached_titles[:4])
                + ". Use it if relevant and cite the underlying source evidence when making claims."
            )

    if active_query:
        thread_id = _ensure_zopedia_chat_thread(user_key=zopedia_user_key, title=display_query or active_query)
        visible_user_text = display_query or active_query
        last_turn = chat[-1] if chat else {}
        already_pending_user = (
            str(last_turn.get("role") or "").strip().lower() == "user"
            and " ".join(str(last_turn.get("content") or "").split()) == " ".join(str(visible_user_text or "").split())
        )
        # Add user message to history unless this is a retry of an unanswered identical turn.
        if not already_pending_user:
            chat.append({"role": "user", "content": visible_user_text})
        if thread_id and not already_pending_user:
            _persist_zopedia_chat_message(
                thread_id=thread_id,
                user_key=zopedia_user_key,
                role="user",
                content=visible_user_text,
                title=visible_user_text,
            )
        with conversation_area:
            if not already_pending_user:
                with st.chat_message("user"):
                    st.markdown(visible_user_text)
                    if stored_uploads:
                        st.caption(f"Attached {len(stored_uploads)} source{'s' if len(stored_uploads) != 1 else ''}.")

        # Run agent with live progress and render structured response
        # Pass prior turns (everything before the just-appended user message)
        # so the agent can resolve follow-up references.
        prior_turns = chat[:-1] if len(chat) > 1 else []
        with conversation_area:
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
        if thread_id:
            agent_result = dict(msg_data.get("agent_result") or {})
            _persist_zopedia_chat_message(
                thread_id=thread_id,
                user_key=zopedia_user_key,
                role="assistant",
                content=str(msg_data.get("content") or msg_data.get("answer") or ""),
                payload=msg_data,
                run_id=str(agent_result.get("run_id") or msg_data.get("msg_id") or ""),
                title=display_query or active_query,
            )
        st.session_state["agentic_omnibar_chat"] = chat


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

    def _update_live_status(label: str, *, state: str | None = None) -> None:
        kwargs: dict[str, object] = {"label": label, "expanded": True}
        if state:
            kwargs["state"] = state
        status_widget.update(**kwargs)

    def _progress_callback(event: dict[str, object]) -> None:
        nonlocal tool_count
        stage = str(event.get("stage") or "").strip().lower()
        message = _agentic_omnibar_progress_message(event)

        if stage == "planner_start":
            prior_tools = int(event.get("tool_call_count") or 0)
            if prior_tools > 0:
                _update_live_status(f"Reviewing {prior_tools} finding{'s' if prior_tools != 1 else ''}...")
            else:
                _update_live_status("Planning the research...")
        elif stage == "planner_heartbeat":
            elapsed = int(event.get("elapsed_seconds") or 0)
            prior_tools = int(event.get("tool_call_count") or 0)
            parts = []
            if prior_tools > 0:
                parts.append(f"{prior_tools} finding{'s' if prior_tools != 1 else ''}")
            parts.append(f"{elapsed}s")
            _update_live_status(f"Reasoning... ({', '.join(parts)})")
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
            _update_live_status(f"Checking {human_tool}...")
        elif stage == "tool_heartbeat":
            tool_name = str(event.get("tool_name") or "")
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            elapsed = int(event.get("elapsed_seconds") or 0)
            _update_live_status(f"Checking {human_tool}... ({elapsed}s)")
        elif stage == "tool_timeout":
            tool_name = str(event.get("tool_name") or "")
            human_tool = _humanize_agentic_omnibar_tool_name(tool_name)
            elapsed = int(event.get("elapsed_seconds") or 0)
            thinking_trace.append({"type": "message", "text": f"{human_tool} timed out after {elapsed}s; moving on."})
            with status_widget:
                st.caption(f"{human_tool} timed out after {elapsed}s; moving on.")
            _update_live_status(f"{human_tool} timed out; continuing...")
        elif stage == "tool_complete":
            preview = str(event.get("result_preview") or "").strip()
            trace_entry: dict[str, object] = {
                "type": "tool_complete",
                "preview": preview,
                "tool_name": str(event.get("tool_name") or ""),
            }
            links = event.get("source_links")
            if isinstance(links, list) and links:
                source_links_all.extend(links)
                trace_entry["source_links"] = links
            render_payload = event.get("render_payload")
            if isinstance(render_payload, dict) and render_payload.get("kind") in {"chart_model", "analysis_result"}:
                trace_entry["render_payload"] = render_payload
            if preview or render_payload or links:
                thinking_trace.append(trace_entry)
        elif stage == "planner_final":
            _update_live_status(f"Writing answer from {tool_count} source{'s' if tool_count != 1 else ''}...")
        elif stage == "final_synthesis_start":
            _update_live_status(f"Synthesizing answer from {tool_count} source{'s' if tool_count != 1 else ''}...")
        elif stage.startswith("memory_"):
            thinking_trace.append({"type": "message", "text": message})
            _update_live_status(message)
        elif stage == "tool_catalog_ready":
            tool_total = int(event.get("tool_count") or 0)
            if tool_total > 0:
                _update_live_status("Getting the research workspace ready...")
        elif stage == "hidden_step_heartbeat":
            elapsed = int(event.get("elapsed_seconds") or 0)
            _update_live_status(f"Checking saved research... ({elapsed}s)")
        elif stage == "hidden_step_timeout":
            elapsed = int(event.get("elapsed_seconds") or 0)
            thinking_trace.append({"type": "message", "text": f"Saved-research check timed out after {elapsed}s; continuing."})
            _update_live_status("Saved-research check timed out; continuing...")
        elif stage == "final_synthesis_heartbeat":
            elapsed = int(event.get("elapsed_seconds") or 0)
            _update_live_status(f"Writing the answer... ({elapsed}s)")
        elif stage == "final_synthesis_timeout":
            thinking_trace.append({"type": "message", "text": "Synthesis timed out; returning the evidence gathered so far."})
            _update_live_status("Synthesis timed out; returning gathered evidence...")
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
        _update_live_status(
            f"Error after {duration:.0f}s ({tool_count} source{'s' if tool_count != 1 else ''} checked)",
            state="error",
        )
    else:
        _update_live_status(
            f"Researched {tool_count} source{'s' if tool_count != 1 else ''} · {duration:.0f}s",
            state="complete",
        )

    # ── Render structured response ──
    agent_result = dict(resolution.get("agent_result") or {})
    answer = str(agent_result.get("answer_markdown") or "").strip()
    confidence = str(agent_result.get("confidence") or "").strip()
    limitations = [str(item).strip() for item in list(agent_result.get("limitations") or []) if str(item).strip()]
    search_results = list(resolution.get("search_results") or [])
    request_id = str(resolution.get("request_id") or "live")
    if thinking_trace:
        st.session_state[_thinking_trace_open_key(request_id)] = True

    # Error message
    if run_error:
        st.error(f"Zopedia encountered an error: {run_error}")

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
        _render_interactive_answer_markdown(
            answer,
            query=query,
            msg_id=request_id,
        )
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

    # Thinking trace — persistent toggle so reruns do not collapse it.
    if thinking_trace:
        _render_thinking_trace_panel(
            thinking_trace,
            agent_result,
            panel_id=request_id,
            key_prefix=f"live_{request_id}",
            default_open=True,
        )

    # Search results (simplified)
    if search_results and not answer:
        _render_inline_search_results(search_results, request_id)

    # Dig deeper button
    if answer:
        if st.button(
            "Dig deeper",
            key=f"dig_live_{request_id}",
            type="tertiary",
            icon=":material/travel_explore:",
        ):
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

    metric_cols = _responsive_columns(3)
    with metric_cols[0]:
        st.metric("Score", _format_scalar(row.get("attention_score"), digits=1))
    with metric_cols[1]:
        st.metric("Residual", _format_scalar(row.get("residual_value"), digits=2, suffix="%", signed=True))
    with metric_cols[2]:
        st.metric("Horizon", ATTENTION_HORIZON_LABELS.get(str(row.get("horizon") or "").strip(), str(row.get("horizon") or "n/a")))

    chart = attention_content._build_attention_micro_chart(row)
    if chart is not None:
        st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})

    item_summary = attention_content._homepage_v2_item_summary(
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
        header_cols = _responsive_columns([6.2, 1.4, 1.4])
        with header_cols[0]:
            st.markdown(f"##### {title}")
            meta = [item for item in [source_label, subtitle, entity_id, status] if item]
            st.caption(" | ".join(meta))
        with header_cols[1]:
            st.metric("Score", _format_scalar(row.get("attention_score"), digits=1))
        with header_cols[2]:
            st.metric("News", str(linked_news_count))

        story_text = str((brief_payload or {}).get("lead_text") or attention_content._attention_story_text(row)).strip()
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
        why_now_text = attention_content._clean_attention_text(row.get("why_now_text"))
        expected_text = attention_content._clean_attention_text(row.get("expected_vs_observed_text"))
        key_points_text = _attention_key_points_text(row)
        chart = attention_content._build_attention_micro_chart(row)

        insight_cols = _responsive_columns([1.05, 1.55], gap="large")
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

        footer_cols = _responsive_columns([5.6, 2.4])
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
    what_happened_text = attention_content._raw_attention_text(event.get("what_happened_text"))
    why_happened_text = attention_content._raw_attention_text(event.get("why_happened_text"))
    affected_assets_summary_text = attention_content._raw_attention_text(event.get("affected_assets_summary_text"))
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
        header_cols = _responsive_columns([5.4, 1.3, 1.3])
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
    suppress_ticker_tables: bool = False,
) -> None:
    bundle_type = str(bundle.get("bundle_type") or "").strip()
    if bundle_type == "event":
        what_happened = attention_content._raw_attention_text(bundle.get("what_happened_text"))
        why_happened = attention_content._raw_attention_text(bundle.get("why_happened_text"))
        affected_assets = attention_content._raw_attention_text(bundle.get("affected_assets_summary_text"))
        if what_happened:
            st.markdown(f"**What Happened**  \n{what_happened}")
        if why_happened:
            st.markdown(f"**Why It Happened**  \n{why_happened}")
        if affected_assets:
            st.markdown(f"**Affected Assets**  \n{affected_assets}")
    else:
        what_changed = attention_content._raw_attention_text(bundle.get("what_changed_text"))
        why_now = attention_content._raw_attention_text(bundle.get("why_now_text"))
        what_else_moved = attention_content._raw_attention_text(bundle.get("what_else_moved_text"))
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

    key_pfx = ticker_table_key_prefix or "attention_bundle"
    evidence = bundle.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        st.markdown("**Evidence**")
        for index, item in enumerate(evidence[:4]):
            headline = str((item or {}).get("headline") or "").strip()
            summary = attention_content._attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            authority = str((item or {}).get("authority_bucket") or "").strip()
            published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
            published_label = published_at.strftime("%b %d %H:%M UTC") if pd.notna(published_at) else ""
            if headline:
                _render_tracked_activity_link(
                    headline,
                    url,
                    key=_activity_link_key(f"{key_pfx}_evidence_{index}", label=headline, url=url),
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
            summary = attention_content._attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            if headline:
                _render_tracked_activity_link(
                    headline,
                    url,
                    key=_activity_link_key(f"{key_pfx}_background_{index}", label=headline, url=url),
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

    if not suppress_ticker_tables:
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
    what_happened = attention_content._raw_attention_text(payload.get("what_happened_text") or event.get("what_happened_text"))
    why_happened = attention_content._raw_attention_text(payload.get("why_happened_text") or event.get("why_happened_text"))
    affected_assets = attention_content._raw_attention_text(payload.get("affected_assets_summary_text") or event.get("affected_assets_summary_text"))
    event_title = str(payload.get("event_title") or event.get("event_title") or "Market event").strip()
    supporting_symbols = [
        str(item).upper().strip()
        for item in list(payload.get("supporting_symbols") or event.get("supporting_symbols") or [])
        if str(item).strip()
    ]
    with st.container(border=True):
        header_cols = _responsive_columns([5.2, 1.2, 1.6])
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
                bundle = dashboard_loaders._safe_load_attention_research_bundle_cached(cfg, bundle_id, force_refresh=force_refresh)
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
    what_changed = attention_content._raw_attention_text(payload.get("what_changed_text") or mover.get("what_changed_text"))
    why_today = attention_content._raw_attention_text(payload.get("why_now_text") or mover.get("why_now_text"))
    what_else_moved = attention_content._raw_attention_text(payload.get("what_else_moved_text") or mover.get("what_else_moved_text"))
    with st.container(border=True):
        header_cols = _responsive_columns([4.8, 1.0, 1.5])
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
                bundle = dashboard_loaders._safe_load_attention_research_bundle_cached(cfg, bundle_id, force_refresh=force_refresh)
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
    header_cols = _responsive_columns([4.8, 1.4])
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
            "Pulling top events, standout movers, and unresolved market signals.",
        ):
            home_payload = dashboard_loaders._load_attention_home_1d_cached(
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

    metric_cols = _responsive_columns(5)
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
        st.caption("Using live market data because the retained attention store is not configured.")

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
        st.info("No market-wide events cleared the bar in the latest market read.")
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
    second_cols = _responsive_two_panel()
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
            st.info("No large unresolved moves in the latest market read.")
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
            st.info("No multi-horizon taxonomy cohorts were produced in the latest market read.")
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
    header_cols = _responsive_columns([8.8, 0.65], gap="small")
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

    main_cols = _responsive_columns([1.45, 1.05], gap="large")
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

    main_cols = _responsive_columns([1.45, 1.05], gap="large")
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
            return dashboard_loaders._load_attention_home_1d_cached(
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
    _, _back_col = _responsive_columns([5, 1.4])
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

    if _mobile_layout_active():
        _render_attention_home_summary_card(
            home_payload,
            snapshot_label=snapshot_label,
            title="Market Summary",
        )
        with st.expander("Market graph", expanded=False):
            _render_homepage_v2_graph_banner(home_payload)
    else:
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


def _render_homepage_exp(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    from views.experiments import _render_experiment_placeholder_page
    _render_experiment_placeholder_page(
        cfg,
        force_data_refresh=force_data_refresh,
    )


# ---------------------------------------------------------------------------
# Homepage — narrative beats with nested stock summaries and fundamentals
# ---------------------------------------------------------------------------

def _load_zopedia_enrichments_lookup() -> dict[str, dict[str, object]]:
    def _cell_text(row: pd.Series, key: str) -> str:
        value = row.get(key)
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        return str(value or "").strip()

    def _json_cell(row: pd.Series, key: str, default: object) -> object:
        raw = _cell_text(row, key)
        if not raw:
            return default
        import json as _json
        try:
            return _json.loads(raw)
        except Exception:
            return default

    cache_key = "_homev3_zopedia_enrichments_lookup"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        from services.pipeline_store import load_latest_dataset_frame
        frame, _ = load_latest_dataset_frame("attention_ticker_zopedia_enrichments")
    except Exception:
        frame = pd.DataFrame()
    lookup: dict[str, dict[str, object]] = {}
    if isinstance(frame, pd.DataFrame) and not frame.empty and "symbol" in frame.columns:
        for _, row in frame.iterrows():
            symbol = _cell_text(row, "symbol").upper()
            if not symbol:
                continue
            lookup[symbol] = {
                "status": _cell_text(row, "status"),
                "answer_markdown": _cell_text(row, "answer_markdown"),
                "confidence": _cell_text(row, "confidence"),
                "limitations": _json_cell(row, "limitations_json", []),
                "tool_calls": _json_cell(row, "tool_calls_json", []),
                "quality_review": _json_cell(row, "quality_review_json", {}),
                "audio_text": _cell_text(row, "audio_text"),
                "audio_base64": _cell_text(row, "audio_base64"),
                "audio_text_hash": _cell_text(row, "audio_text_hash"),
                "audio_mime_type": _cell_text(row, "audio_mime_type"),
                "audio_file_extension": _cell_text(row, "audio_file_extension"),
                "voice_id": _cell_text(row, "voice_id"),
                "model_id": _cell_text(row, "model_id"),
                "output_format": _cell_text(row, "output_format"),
            }
    st.session_state[cache_key] = lookup
    return lookup


def _zopedia_audio_text(result: dict[str, object]) -> str:
    audio_text = str(result.get("audio_text") or "").strip()
    if audio_text:
        return audio_text
    answer = str(result.get("answer_markdown") or "").strip()
    if not answer:
        return ""
    normalized = re.sub(r"```.*?```", " ", answer, flags=re.DOTALL)
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    normalized = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", normalized)
    normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", normalized)
    normalized = re.sub(r"^#{1,6}\s*", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s*[-*+]\s+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"[*_~>|]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()[:4000]


def _render_zopedia_summary_audio(result: dict[str, object]) -> None:
    audio_text = _zopedia_audio_text(result)
    if not audio_text:
        return
    preloaded_audio_bytes = _try_decode_attention_summary_audio(result, audio_text=audio_text)
    if preloaded_audio_bytes:
        _render_attention_summary_audio_player(
            audio_bytes=preloaded_audio_bytes,
            mime_type=str(result.get("audio_mime_type") or "audio/mpeg"),
        )
        return
    elevenlabs_cfg = load_elevenlabs_tts_config()
    if not elevenlabs_cfg:
        return
    async_task_key = _attention_summary_audio_task_key(
        audio_text=audio_text,
        voice_id=str(getattr(elevenlabs_cfg, "voice_id", "") or ""),
        model_id=str(getattr(elevenlabs_cfg, "model_id", "") or ""),
        output_format=str(getattr(elevenlabs_cfg, "output_format", "") or ""),
        base_url=str(getattr(elevenlabs_cfg, "base_url", "") or ""),
    )
    async_audio_encoded = str(st.session_state.get(_attention_summary_audio_session_key(async_task_key)) or "").strip()
    if async_audio_encoded:
        try:
            async_audio_bytes = base64.b64decode(async_audio_encoded.encode("ascii"), validate=True)
        except Exception:
            async_audio_bytes = b""
        if async_audio_bytes:
            from services.elevenlabs_tts import audio_mime_type as elevenlabs_audio_mime_type

            _render_attention_summary_audio_player(
                audio_bytes=async_audio_bytes,
                mime_type=elevenlabs_audio_mime_type(str(getattr(elevenlabs_cfg, "output_format", "") or "")),
            )
            return
    if str(st.session_state.get(_attention_summary_audio_error_session_key(async_task_key)) or "").strip():
        return
    _render_attention_summary_async_audio_fallback(
        task_key=async_task_key,
        audio_text=audio_text,
        elevenlabs_cfg=elevenlabs_cfg,
    )


def _render_zopedia_ticker_result(result: dict[str, object], *, debug: bool = False) -> None:
    import re as _re

    answer = str(result.get("answer_markdown") or "").strip()
    confidence = str(result.get("confidence") or "").strip().lower()
    limitations = [str(l).strip() for l in list(result.get("limitations") or []) if str(l).strip()]
    tool_calls = list(result.get("tool_calls") or [])
    quality_review = dict(result.get("quality_review") or {})

    if not answer:
        error = str(result.get("error") or "Analysis unavailable").strip()
        st.warning(error)
        return

    answer = _re.sub(r"\(see Zopedia:[^)]*\)", "", answer).strip()

    sections = _split_zopedia_answer_sections(answer)
    for idx, (heading, body) in enumerate(sections):
        if heading:
            if idx > 0:
                st.markdown("---")
            st.markdown(f"###### {heading}")
        st.markdown(body)

    if confidence and confidence not in ("", "low", "medium"):
        st.caption(f"Confidence: {confidence.title()}")

    if debug:
        meta_parts: list[str] = []
        if confidence:
            meta_parts.append(f"Confidence: {confidence.title()}")
        critique = str(quality_review.get("critique_summary") or "").strip()
        if critique:
            meta_parts.append(critique)
        sources_used = list(dict.fromkeys(
            str(tc.get("tool_name") or "").replace("_", " ").title()
            for tc in tool_calls
            if str(tc.get("tool_name") or "").strip()
        ))
        if sources_used:
            meta_parts.append(f"Sources: {', '.join(sources_used)}")
        if meta_parts:
            st.caption(" · ".join(meta_parts))
        if limitations:
            with st.expander("Limitations", expanded=False):
                for lim in limitations:
                    st.markdown(f"- {lim}")


def _split_zopedia_answer_sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown answer into (heading, body) pairs for structured rendering."""
    import re
    normalized = re.sub(r"(?<!\n)(#{1,4}\s+)", r"\n\1", markdown)
    parts: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in normalized.split("\n"):
        match = re.match(r"^#{1,4}\s+(.+)$", line.strip())
        if match:
            if current_lines:
                parts.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        parts.append((current_heading, "\n".join(current_lines).strip()))
    return [(h, b) for h, b in parts if b]


@st.fragment
def _render_homepage_v3_story_fragment(
    cfg: AppConfig,
    beats: list[dict[str, object]],
    *,
    run_token: str,
    force_data_refresh: bool,
    zopedia_lookup: dict[str, dict[str, object]],
) -> None:
    active_run_token = str(run_token or "").strip()
    bundle_symbol_lookup = homepage_v2_bundle_symbol_lookup(beats)

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

        with st.container(border=True):
            st.markdown(f"**{index + 1}. {beat_sentence}**")
            meta = [item for item in [beat_kind, f"{len(beat_symbols)} symbols" if beat_symbols else ""] if item]
            if meta:
                st.caption(" · ".join(meta))
            if beat_summary:
                st.write(beat_summary)

            if bundle_id:
                with st.expander("Research", expanded=False, icon=":material/search:"):
                    if _has_cached_attention_bundle(bundle_id, run_token=active_run_token) and not force_data_refresh:
                        bundle = _load_attention_research_bundle_session_cached(
                            cfg, bundle_id, run_token=active_run_token, force_refresh=False,
                        )
                    else:
                        with st.spinner("Loading retained research..."):
                            bundle = _load_attention_research_bundle_session_cached(
                                cfg, bundle_id, run_token=active_run_token, force_refresh=force_data_refresh,
                            )
                    _render_attention_research_bundle_panel(
                        bundle,
                        ticker_table_key_prefix=f"homev3_bundle_{bundle_id}",
                        suppress_ticker_tables=True,
                    )

                    if beat_symbols:
                        st.markdown("---")
                        for symbol in beat_symbols[:6]:
                            enrichment = zopedia_lookup.get(symbol.upper())
                            has_enrichment = bool(enrichment and str(enrichment.get("answer_markdown") or "").strip())
                            with st.expander(symbol, expanded=False, icon=":material/query_stats:"):
                                if has_enrichment:
                                    _render_zopedia_ticker_result(enrichment, debug=_homev3_debug_mode())
                                else:
                                    st.caption("Attention stock summary not yet available.")
                                _render_overview_fundamentals(
                                    cfg,
                                    symbol,
                                    force_data_refresh=force_data_refresh,
                                )


def _homev3_debug_mode() -> bool:
    return bool(_current_user_is_admin() and st.session_state.get("_homev3_debug_mode"))


def _render_homepage_v3(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    _top_cols = _responsive_columns([5, 1.4])
    with _top_cols[0]:
        if _current_user_is_admin():
            st.toggle("Debug", key="_homev3_debug_mode", help="Show internal quality signals")
    with _top_cols[1]:
        _render_section_back_button("homepage_v3_back")

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
        st.info("No daily narrative beats were produced from the latest market tape.")
        return

    generated_at = pd.to_datetime(home_payload.get("generated_at_utc"), utc=True, errors="coerce")
    snapshot_label = generated_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(generated_at) else "just now"

    _render_homepage_v2_graph_banner(home_payload)

    zopedia_lookup = _load_zopedia_enrichments_lookup()
    market_summary = zopedia_lookup.get("__MARKET_SUMMARY__")
    if market_summary and str(market_summary.get("answer_markdown") or "").strip():
        with st.container(border=True):
            _render_zopedia_ticker_result(market_summary, debug=_homev3_debug_mode())
            _render_zopedia_summary_audio(market_summary)
    else:
        _render_attention_home_summary_card(
            home_payload,
            snapshot_label=snapshot_label,
            title="Market Summary",
        )

    st.markdown("---")
    st.subheader("Market Narrative")
    st.caption("Each beat is a distinct market signal. Expand to see retained research or a Zopedia deep dive on any ticker.")

    _render_homepage_v3_story_fragment(
        cfg,
        beats,
        run_token=str(home_payload.get("run_id") or home_payload.get("generated_at_utc") or "").strip(),
        zopedia_lookup=zopedia_lookup,
        force_data_refresh=force_data_refresh,
    )


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
            asset = dashboard_loaders._load_asset_metadata_cached(cfg, symbol, force_refresh=force_refresh)
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
    return dashboard_loaders._load_page_agentic_summary_cached(
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
            signal_summary = dashboard_loaders._load_technical_signal_summary_cached(
                cfg,
                symbol,
                None,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            signal_summary = {"error": str(exc)}
        try:
            forecast = dashboard_loaders._load_forecast_next_week_cached(
                cfg,
                symbol,
                days=365,
                signal_frame=None,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            forecast = {"error": str(exc)}
        try:
            news_payload = dashboard_loaders._load_recent_news_cached(
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
            attention_context = dashboard_loaders._load_attention_context_cached(
                cfg,
                symbol,
                force_refresh=force_data_refresh,
            )
        except Exception as exc:
            attention_context = {"error": str(exc)}
        try:
            background_payload = dashboard_loaders._load_attention_ticker_background_cached(
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
            recent_articles = attention_content._json_ready(
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
                "opportunity": attention_content._json_ready(opportunity_row),
                "technical_signals": attention_content._json_ready(signal_summary),
                "forecast": attention_content._json_ready(forecast),
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


def _trading_agent_number(value: object, *, suffix: str = "", decimals: int = 1) -> str:
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index, np.ndarray)):
        values = list(value)
        value = values[0] if values else None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return "n/a"
    return f"{float(numeric):.{max(int(decimals), 0)}f}{suffix}"


_layout_mode = _resolve_layout_mode()
_ensure_app_shell_styles()
_ensure_client_layout_auto_redirect(_layout_mode)
if _layout_mode == "mobile":
    _ensure_mobile_layout_styles()

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
    if _layout_mode == "mobile":
        _render_mobile_public_home_shell()
    else:
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
            _apply_homepage_replay_date(_pub_replay_selected, _pub_replay_today)
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

_nav_current = _normalize_workspace_section(st.session_state.get("workspace_section", section_options[0]))
if _layout_mode == "mobile":
    section, sidebar_connection, sidebar_status, sidebar_buying_power = _render_mobile_workspace_shell(
        section_options=[s for s in section_options if s != NAV_SEPARATOR],
        current_section=_nav_current,
        cache_disabled=cache_disabled,
        force_refresh_default=force_refresh_default,
        current_user=current_user,
    )
else:
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
        _apply_homepage_replay_date(_replay_selected, _replay_today)
        st.markdown('<p class="sn-nav-label">Navigate</p>', unsafe_allow_html=True)
        for _nav_opt in section_options:
            if _nav_opt == NAV_SEPARATOR:
                st.markdown(
                    '<hr style="margin:4px 0;border:none;border-top:1px solid rgba(255,255,255,0.08);">',
                    unsafe_allow_html=True,
                )
                continue
            _nav_slug = _nav_opt.lower().replace(" ", "_").replace("-", "_")
            _nav_key = f"sn_nav_active_{_nav_slug}" if _nav_opt == _nav_current else f"sn_nav_{_nav_slug}"
            if st.button(_nav_opt, key=_nav_key, use_container_width=True):
                st.session_state["_pending_workspace_section"] = _nav_opt
                st.rerun()
        section = _nav_current

        with st.expander("Workspace Status", expanded=False):
            if pipeline_store_configured():
                if _presentation_layer_only():
                    st.caption("Data mode: Curated presentation snapshots")
                else:
                    st.caption("Data mode: Curated metadata + parquet snapshots")
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
        sidebar_account = dashboard_loaders._load_account_cached(cfg, force_refresh=False)
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
    _render_homepage_v3(
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

elif section == HOME_V2_SECTION:
    _render_homepage_v2(
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
    from views.portfolio import _render_portfolio_section
    _render_portfolio_section(
        cfg,
        api,
        force_data_refresh=force_data_refresh,
        current_user=current_user,
        sidebar_status=sidebar_status,
        sidebar_buying_power=sidebar_buying_power,
    )

elif section == PORTFOLIO_PERFORMANCE_SECTION:
    from views.portfolio import _render_portfolio_performance_section
    _render_portfolio_performance_section(
        cfg,
        api,
        force_data_refresh=force_data_refresh,
        current_user=current_user,
    )

elif section == ADMIN_SECTION:
    from views.access_admin import _render_access_admin_section
    _render_access_admin_section()

elif section == TRADING_AGENT_SECTION:
    from views.trading_agent import _render_trading_agent_section
    _render_trading_agent_section(
        cfg,
        force_data_refresh=force_data_refresh,
    )

elif section == BROAD_ECONOMY_SECTION:
    from views.broad_economy import _render_broad_economy_section
    _render_broad_economy_section(force_data_refresh=force_data_refresh)

elif section == MARKET_EXPLORER_SECTION:
    from views.market_explorer import _render_market_explorer_section
    _render_market_explorer_section(cfg, api, force_data_refresh=force_data_refresh)

elif section == STOCK_INVESTIGATOR_SECTION:
    st.title("Stock Investigator")

    if not _has_live_api(
        api,
        "Stock Investigator requires a working live market connection or retained market snapshots.",
        allow_pipeline=True,
    ):
        st.info("Restore the live market connection or configure retained market snapshots to inspect ticker details.")
    else:
        from views.stock_investigator import _render_stock_investigator_workspace
        _render_stock_investigator_workspace(
            cfg,
            force_data_refresh=force_data_refresh,
        )

elif section == "Option Strategizer":
    from views.option_strategizer import _render_option_strategizer_section
    _render_option_strategizer_section(cfg, api, force_data_refresh=force_data_refresh)
