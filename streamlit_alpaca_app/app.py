from __future__ import annotations

import base64
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
from services import auth_service
from services.alpaca_api import AlpacaAPI, AlpacaAPIError
from services.analytics import build_metric_bar, build_portfolio_vs_benchmarks_fig, select_signed_ranked
from services import attention_surface as attention_surface_module
from services.company import build_attention_news_narrative, summarize_recent_news
from services.config import AppConfig, load_config
from services.data_cache import cache_bundle_exists, cache_data_root, cache_policy_path, dataset_scope
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
    latest_job_status_table,
    load_latest_dataset_frame,
    pipeline_store_configured,
    start_source_refresh_job,
)
from services.homepage_v2 import (
    HOMEPAGE_V2_COMPANY_PANEL,
    HOMEPAGE_V2_RESEARCH_PANEL,
    build_homepage_v2_market_digest,
    homepage_v2_bundle_symbol_lookup,
    normalize_homepage_v2_detail_state,
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
from services.technicals import build_technical_figure

_SIGNALS_IMPORT_ERROR: str | None = None
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

HOME_EXP_SECTION = "Home Exp"

BASE_SECTION_OPTIONS = [
    "Home",
    "Daily Tape",
    "Portfolio Overview",
    "Performance",
    "Market Opportunity",
    "Technical Strategizer",
    "Option Strategizer",
    "Fundamental Strategizer",
    "FRED Macro",
    "Pipeline Jobs",
]
ADMIN_SECTION = "Access Admin"

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
}

JOB_LABELS = {
    "equities-intraday-preload": "Equities Core Snapshots",
    "macro-fred-daily": "FRED Macro Snapshots",
    "commodities-regime": "Commodity Regime Snapshots",
    "options-liquid-universe": "Options Snapshot Refresh",
    "news-ingest-and-features": "News Snapshot Refresh",
    "attention-home-build": "Attention Home Build",
    "entity-taxonomy-refresh": "Entity Taxonomy Refresh",
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
APP_BRAND_NAME = "Spectral Nature"
APP_BRAND_KICKER = "Torres Capital"
APP_BRAND_SUBTITLE = "Research, portfolio context, and market structure in one refined workspace."
_APP_SHELL_STYLE_VERSION = "2026-04-03-brand-assets-v4"
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
        .sn-sidebar-brand-copy {
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
        .sn-sidebar-kicker {
            margin-bottom: 0.7rem;
            color: var(--sn-accent);
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.2em;
            text-transform: uppercase;
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
        .sn-page-copy {
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar_brand_panel(app_track: str) -> None:
    logo_markup = ""
    has_logo = False
    try:
        logo_bytes = APP_SIDEBAR_LOGO_PATH.read_bytes()
        logo_b64 = base64.b64encode(logo_bytes).decode("ascii")
        logo_markup = (
            "<img "
            f"src='data:image/png;base64,{logo_b64}' "
            f"alt='{html.escape(APP_BRAND_NAME)} logo' />"
        )
        has_logo = True
    except OSError:
        logo_markup = ""
    brand_markup = (
        f"<div class='sn-sidebar-logo'>{logo_markup}</div>"
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
            "<div class='sn-sidebar-brand-copy'>"
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
            f"<div class='sn-page-copy'>{html.escape(body)}</div>"
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
_load_homepage_v2_digest_cached = dashboard_loaders._load_homepage_v2_digest_cached

_attention_event_key = attention_content._attention_event_key
_clean_attention_copy = attention_content._clean_attention_copy
_raw_attention_copy = attention_content._raw_attention_copy
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


def _section_options() -> list[str]:
    options = list(BASE_SECTION_OPTIONS)
    if _current_user_is_admin():
        options.append(ADMIN_SECTION)
    return options


def _normalize_workspace_section(section_name: object) -> str:
    normalized = str(section_name or "").strip()
    if normalized == "Homepage - v2":
        return "Home"
    if normalized in {"Homepage Exp", "Home Experimental"}:
        return HOME_EXP_SECTION
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
        st.session_state["_pending_workspace_section"] = "Market Opportunity"
        st.session_state["_pending_market_view"] = "Markets"
        st.session_state["market_selected_ticker"] = inspect_ticker
        st.session_state["market_ticker_detail_widget"] = inspect_ticker
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


def _render_auth_cookie_sync(action: str, value: str = "") -> None:
    cookie_name = json.dumps(_AUTH_COOKIE_NAME)
    cookie_value = json.dumps(value)
    if action == "clear":
        cookie_script = (
            "const docs = [document];"
            "try { if (window.parent?.document) docs.push(window.parent.document); } catch (e) {}"
            "try { if (window.top?.document) docs.push(window.top.document); } catch (e) {}"
            "const seen = new Set();"
            "const uniqueDocs = docs.filter((doc) => { if (!doc || seen.has(doc)) return false; seen.add(doc); return true; });"
            "const secureAttr = (() => {"
            "  try { return (window.top?.location?.protocol || window.parent?.location?.protocol || window.location.protocol) === 'https:' ? '; Secure' : ''; }"
            "  catch (e) { return window.location.protocol === 'https:' ? '; Secure' : ''; }"
            "})();"
            f"const cookieStr = {cookie_name} + '=; Max-Age=0; path=/; SameSite=Lax' + secureAttr;"
            "uniqueDocs.forEach((doc) => { try { doc.cookie = cookieStr; } catch (e) {} });"
        )
    else:
        cookie_script = (
            "const maxAge = "
            f"{_AUTH_COOKIE_TTL_SECONDS};"
            "const docs = [document];"
            "try { if (window.parent?.document) docs.push(window.parent.document); } catch (e) {}"
            "try { if (window.top?.document) docs.push(window.top.document); } catch (e) {}"
            "const seen = new Set();"
            "const uniqueDocs = docs.filter((doc) => { if (!doc || seen.has(doc)) return false; seen.add(doc); return true; });"
            "const secureAttr = (() => {"
            "  try { return (window.top?.location?.protocol || window.parent?.location?.protocol || window.location.protocol) === 'https:' ? '; Secure' : ''; }"
            "  catch (e) { return window.location.protocol === 'https:' ? '; Secure' : ''; }"
            "})();"
            f"const cookieStr = {cookie_name} + '=' + encodeURIComponent({cookie_value}) + '; Max-Age=' + maxAge + '; path=/; SameSite=Lax' + secureAttr;"
            "uniqueDocs.forEach((doc) => { try { doc.cookie = cookieStr; } catch (e) {} });"
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


def _restore_legacy_login_from_cookie() -> bool:
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
    return True


def _restore_database_login_from_cookie() -> bool:
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
    return True


def _render_legacy_login_gate() -> None:
    username_expected = _auth_username()
    password_expected = _auth_password()
    _render_page_intro(
        "Secure Access",
        "Welcome back",
        "Private client access to the Spectral Nature workspace for market intelligence, portfolio context, and daily research.",
    )

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
            _store_user_context(None)
            _render_auth_cookie_sync("set", session_id)
            return
        st.error("Invalid username or password.")
    st.stop()


def _render_database_login_gate() -> None:
    auth_state = auth_service.initialize_auth_system()
    _render_page_intro(
        "Secure Access",
        "Welcome back",
        "Private client access to the Spectral Nature workspace for market intelligence, portfolio context, and daily research.",
    )

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
            submitted = st.form_submit_button("Login", type="primary")
        if submitted:
            result = auth_service.authenticate_user(
                email=email,
                password=password,
                user_agent=_request_user_agent(),
                ip_address=_request_ip_address(),
            )
            if result.get("ok"):
                context = result.get("context")
                session_token = str(result.get("session_token") or "")
                if isinstance(context, auth_service.UserContext) and session_token:
                    st.session_state["_ui_authenticated"] = True
                    st.session_state["_ui_auth_session_id"] = session_token
                    st.session_state["_ui_auth_mode"] = "database"
                    _store_user_context(context)
                    _render_auth_cookie_sync("set", session_token)
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
                        _store_user_context(context)
                        _render_auth_cookie_sync("set", session_token)
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
                st.caption("Email delivery is not configured in this environment. Contact an administrator for a reset link.")

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


def _enforce_login_gate() -> None:
    if not _auth_enabled():
        st.session_state["_ui_authenticated"] = True
        st.session_state["_ui_auth_mode"] = "disabled"
        return

    if st.session_state.pop("_ui_clear_auth_cookie", False):
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

    copy_col, color_col = st.columns(2)
    with copy_col:
        st.text_input("Kicker", key=_invite_theme_widget_state_key("kicker"))
        st.text_input("Headline", key=_invite_theme_widget_state_key("headline"))
        st.text_area("Intro Copy", key=_invite_theme_widget_state_key("intro_text"), height=120)
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


def _render_access_admin_section() -> None:
    st.title("Access Admin")
    if st.session_state.get("_ui_auth_mode") != "database":
        st.info("Database-backed auth is required for user invites and password reset management.")
        return

    current_user = _current_user_context()
    if current_user is None or not current_user.is_admin:
        st.error("Only admin users can access this section.")
        return

    auth_state = auth_service.initialize_auth_system()
    st.caption(
        "Manage invite-based account creation, review pending invites, issue password reset links, and design invite emails."
    )
    st.caption(
        f"Email delivery: {'configured' if auth_state.get('email_delivery') else 'not configured'}"
    )

    access_tab, designer_tab = st.tabs(["Access Management", "Invite Email Designer"])
    with access_tab:
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

        users = pd.DataFrame(auth_service.list_users())
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
                    "last_login_at",
                ]
                if column in users.columns
            ]
            if "share_fraction" in users.columns:
                users["share_fraction"] = pd.to_numeric(users["share_fraction"], errors="coerce") * 100.0
            st.dataframe(users[display_cols], use_container_width=True, hide_index=True)

        invites = pd.DataFrame(auth_service.list_pending_invites())
        st.subheader("Pending Invites")
        if invites.empty:
            st.info("No pending invites.")
        else:
            if "proposed_share_fraction" in invites.columns:
                invites["proposed_share_fraction"] = pd.to_numeric(invites["proposed_share_fraction"], errors="coerce") * 100.0
            invite_event = st.dataframe(
                invites,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="_access_pending_invites_table",
            )
            selected_rows = _dataframe_selected_rows(invite_event)
            selected_pending_invite_id = ""
            selected_pending_invite_email = ""
            if selected_rows:
                selected_row_idx = selected_rows[0]
                if 0 <= selected_row_idx < len(invites.index):
                    selected_row = invites.iloc[selected_row_idx]
                    selected_pending_invite_id = str(selected_row.get("id") or "").strip()
                    selected_pending_invite_email = str(selected_row.get("email") or "").strip()

            info_col, action_col = st.columns([4.0, 1.2])
            if selected_pending_invite_id:
                short_invite_id = selected_pending_invite_id[:8]
                if selected_pending_invite_email:
                    info_col.caption(f"Selected invite: {selected_pending_invite_email} ({short_invite_id})")
                else:
                    info_col.caption(f"Selected invite id: {short_invite_id}")
            else:
                info_col.caption("Select a row in the Pending Invites table to enable deletion.")

            if action_col.button(
                "Delete Invite",
                key="_access_pending_invite_delete_button",
                use_container_width=True,
                disabled=not bool(selected_pending_invite_id),
            ):
                delete_result = auth_service.delete_pending_invite(
                    invite_id=selected_pending_invite_id,
                    requested_by=current_user,
                )
                if delete_result.get("ok"):
                    st.success(str(delete_result.get("message") or "Pending invite deleted."))
                    st.rerun()
                else:
                    st.error(str(delete_result.get("message") or "Unable to delete pending invite."))

    with designer_tab:
        _render_invite_email_designer(current_user=current_user)


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


def _section_refresh_button(key: str, *, source: str | None = None, label: str = "Refresh cached data") -> bool:
    if _presentation_layer_only():
        source_key = str(source or "").strip().lower()
        if not source_key:
            return False
        clicked = st.button(
            label,
            key=key,
            use_container_width=True,
            help="Triggers the upstream refresh job. This UI keeps reading the latest materialized snapshot.",
        )
        if clicked:
            ok, msg = start_source_refresh_job(source_key)
            if ok:
                st.success(msg)
            else:
                st.warning(msg)
        return False

    clicked = st.button(
        label,
        key=key,
        use_container_width=True,
        help="Bypasses the local CSV cache for this view and loads fresh data where available.",
    )
    if clicked:
        st.caption("Refreshing cached data for this view.")
    return bool(clicked)


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
    _open_attention_target(
        "Market Opportunity",
        {
            "ticker": cleaned_symbol,
            "market_view": "Markets",
            "business_filter": _market_business_filter_for_symbol(cleaned_symbol),
        },
    )


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
    for _, row in rows.iterrows():
        headline = str(row.get("headline") or "Untitled").strip()
        source = str(row.get("source") or "News").strip()
        published_at = pd.to_datetime(row.get("published_at"), utc=True, errors="coerce")
        published_label = published_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(published_at) else "n/a"
        url = str(row.get("url") or "").strip()
        excerpt = _clean_attention_copy(row.get("summary") or row.get("description"))
        meta = " | ".join(part for part in [source, published_label] if part)
        if url:
            st.markdown(f"- [{headline}]({url})")
        else:
            st.markdown(f"- {headline}")
        if excerpt:
            st.caption(excerpt)
        if meta:
            st.caption(meta)


def _compact_background_fallback_text(ticker: str) -> str:
    target = str(ticker or "").upper().strip()
    return f"No relevant catalyst found in web coverage for {target} in the latest agentic run."


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
    if not background_text and happened_text:
        background_text = happened_text
    if not happened_text and background_text:
        happened_text = background_text
    if not background_text:
        background_text = fallback
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
    for item in evidence_links[:8]:
        headline = str((item or {}).get("headline") or "Untitled").strip()
        source = str((item or {}).get("source") or "News").strip()
        published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
        published_label = published_at.strftime("%Y-%m-%d") if pd.notna(published_at) else "n/a"
        url = str((item or {}).get("url") or "").strip()
        if url:
            st.markdown(f"- [{headline}]({url})")
        else:
            st.markdown(f"- {headline}")
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
    open_button_label: str = "Open Market View",
    clear_button_label: str = "Clear",
    clear_button_key: str = "",
    show_header: bool = True,
    show_container_border: bool = True,
) -> None:
    target = str(ticker or "").upper().strip()
    if not target:
        return
    business_filter = _market_business_filter_for_symbol(target)
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
                        _open_attention_target(
                            "Market Opportunity",
                            {"ticker": target, "market_view": "Markets", "business_filter": business_filter},
                        )
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
    headline = str((mover or {}).get("headline") or "").strip()
    if headline:
        return headline
    symbol = str((mover or {}).get("symbol") or "").strip().upper()
    if symbol:
        return symbol
    return "Mover"


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


def _open_attention_target(section_name: str, params: dict[str, object] | None = None) -> None:
    target = str(section_name or "").strip() or "Market Opportunity"
    payload = dict(params or {})
    ticker = str(payload.get("ticker") or "").upper().strip()
    market_view = str(payload.get("market_view") or "").strip()
    business_filter = str(payload.get("business_filter") or "").strip()
    commodity_focus = str(payload.get("commodity_focus") or "").strip()
    normalized_market_view = market_view or ("Commodity Section" if commodity_focus else "Markets")

    st.session_state["_pending_workspace_section"] = target
    if target == "Market Opportunity":
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
        st.session_state["market_selected_ticker"] = ticker
        st.session_state["market_ticker_detail_widget"] = ticker
        if target == "Market Opportunity" and normalized_market_view == "Commodity Section":
            st.session_state["market_commodity_selected_ticker"] = ticker
            st.session_state["market_commodity_ticker_widget"] = ticker
        st.session_state["technical_ticker"] = ticker
        st.session_state["opt_ticker"] = ticker
        st.session_state["fund_ticker"] = ticker
    st.rerun()


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
    target_section = str(row.get("drilldown_section") or "Market Opportunity").strip() or "Market Opportunity"
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
        for item in headline_links[:3]:
            headline = str((item or {}).get("headline") or "").strip()
            url = str((item or {}).get("url") or "").strip()
            if not headline:
                continue
            if url:
                st.markdown(f"- [{headline}]({url})")
            else:
                st.markdown(f"- {headline}")

    if isinstance(filing_links, list) and filing_links:
        st.caption("Supporting filings")
        for item in filing_links[:3]:
            label = str((item or {}).get("label") or "").strip()
            url = str((item or {}).get("url") or "").strip()
            if not label:
                continue
            if url:
                st.markdown(f"- [{label}]({url})")
            else:
                st.markdown(f"- {label}")

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
    target_section = str(row.get("drilldown_section") or "Market Opportunity").strip() or "Market Opportunity"

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
        why_now_text = _clean_attention_copy(row.get("why_now_text"))
        expected_text = _clean_attention_copy(row.get("expected_vs_observed_text"))
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
                for item in headline_links[:2]:
                    headline = str((item or {}).get("headline") or "").strip()
                    if not headline:
                        continue
                    url = str((item or {}).get("url") or "").strip()
                    source = str((item or {}).get("source") or "News").strip()
                    published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
                    published_label = published_at.strftime("%b %d") if pd.notna(published_at) else "n/a"
                    if url:
                        st.markdown(f"- [{headline}]({url})")
                    else:
                        st.markdown(f"- {headline}")
                    st.caption(f"{source} | {published_label}")
            if isinstance(filing_links, list):
                if any(str((item or {}).get("label") or "").strip() for item in filing_links[:2]):
                    st.caption("SEC filings")
                for item in filing_links[:2]:
                    label = str((item or {}).get("label") or "").strip()
                    if not label:
                        continue
                    url = str((item or {}).get("url") or "").strip()
                    if url:
                        st.markdown(f"- [{label}]({url})")
                    else:
                        st.markdown(f"- {label}")
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
    what_happened_text = _raw_attention_copy(event.get("what_happened_text"))
    why_happened_text = _raw_attention_copy(event.get("why_happened_text"))
    affected_assets_summary_text = _raw_attention_copy(event.get("affected_assets_summary_text"))
    headline_text = str(event.get("headline_text") or "").strip()
    source_line = str(event.get("source_line") or "").strip()
    supporting_ids = [str(value).strip() for value in list(event.get("supporting_event_ids") or []) if str(value).strip()]
    supporting_symbols = [str(value).upper().strip() for value in list(event.get("supporting_symbols") or []) if str(value).strip()]
    breadth_count = int(pd.to_numeric(event.get("breadth_count"), errors="coerce") or 0)

    anchor_row = row_lookup.get(supporting_ids[0]) if supporting_ids else None
    params = _parse_drilldown_params(anchor_row.get("drilldown_params_json")) if anchor_row is not None else {}
    target_section = str(anchor_row.get("drilldown_section") or "Market Opportunity").strip() if anchor_row is not None else "Market Opportunity"

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
        what_happened = _raw_attention_copy(bundle.get("what_happened_text"))
        why_happened = _raw_attention_copy(bundle.get("why_happened_text"))
        affected_assets = _raw_attention_copy(bundle.get("affected_assets_summary_text"))
        if what_happened:
            st.markdown(f"**What Happened**  \n{what_happened}")
        if why_happened:
            st.markdown(f"**Why It Happened**  \n{why_happened}")
        if affected_assets:
            st.markdown(f"**Affected Assets**  \n{affected_assets}")
    else:
        what_changed = _raw_attention_copy(bundle.get("what_changed_text"))
        why_now = _raw_attention_copy(bundle.get("why_now_text"))
        what_else_moved = _raw_attention_copy(bundle.get("what_else_moved_text"))
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
        for item in evidence[:4]:
            headline = str((item or {}).get("headline") or "").strip()
            summary = _attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            authority = str((item or {}).get("authority_bucket") or "").strip()
            published_at = pd.to_datetime((item or {}).get("published_at"), utc=True, errors="coerce")
            published_label = published_at.strftime("%b %d %H:%M UTC") if pd.notna(published_at) else ""
            if headline:
                if url:
                    st.markdown(f"- [{headline}]({url})")
                else:
                    st.markdown(f"- {headline}")
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
        for item in background_context[:3]:
            headline = str((item or {}).get("headline") or "").strip()
            summary = _attention_evidence_display_text(item or {})
            source = str((item or {}).get("source") or "Source").strip()
            url = str((item or {}).get("url") or "").strip()
            if headline:
                if url:
                    st.markdown(f"- [{headline}]({url})")
                else:
                    st.markdown(f"- {headline}")
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
    what_happened = _raw_attention_copy(payload.get("what_happened_text") or event.get("what_happened_text"))
    why_happened = _raw_attention_copy(payload.get("why_happened_text") or event.get("why_happened_text"))
    affected_assets = _raw_attention_copy(payload.get("affected_assets_summary_text") or event.get("affected_assets_summary_text"))
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
    what_changed = _raw_attention_copy(payload.get("what_changed_text") or mover.get("what_changed_text"))
    why_today = _raw_attention_copy(payload.get("why_now_text") or mover.get("why_now_text"))
    what_else_moved = _raw_attention_copy(payload.get("what_else_moved_text") or mover.get("what_else_moved_text"))
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


def _render_home_attention(
    cfg: AppConfig,
    api: AlpacaAPI | None,
    *,
    force_data_refresh: bool,
    page_title: str = "Daily Tape",
    page_caption: str = "Today / 1d market tape: what changed versus expectation, why it changed today, and what else moved because of it.",
    show_trend_surface: bool = True,
    require_api: bool = True,
) -> None:
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title(page_title)
        st.caption(page_caption)
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "home_attention_refresh",
            source="attention",
            label="Run attention refresh job",
        )

    if require_api and api is None:
        st.info("Configure the live market connection to enable the daily tape, market context, and research bundle lookups.")
        return

    try:
        with _inline_loading_banner(
            "Loading today's attention tape",
            "Pulling the latest precomputed snapshot for top events, standout movers, and unresolved tape signals.",
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
        st.info("No daily attention items were produced from the latest tape. Refresh after the market data sources update.")
        return

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


def _select_homepage_v2_bundle(bundle_id: str, symbols: list[str] | None = None) -> None:
    st.session_state["homepage_v2_selected_bundle_id"] = str(bundle_id or "").strip()
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


def _select_homepage_exp_bundle(bundle_id: str, symbols: list[str] | None = None) -> None:
    st.session_state["homepage_exp_selected_bundle_id"] = str(bundle_id or "").strip()
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
                        args=(bundle_id, list(beat_symbols)),
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
                        args=(bundle_id, list(beat_symbols)),
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
    top_events = list(home_payload.get("top_events") or [])
    must_read = list(home_payload.get("must_read_movers") or [])
    unresolved = list(home_payload.get("unresolved_large_moves") or [])

    beats: list[dict[str, object]] = []
    for event in top_events:
        preview = _attention_home_bundle_preview(event, bundle={})
        summary_text = _attention_home_surface_summary(preview, is_event=True)
        beats.append(
            {
                "bundle_id": str(event.get("bundle_id") or "").strip(),
                "sentence": str(event.get("event_title") or "").strip(),
                "summary": summary_text,
                "symbols": [str(item).upper().strip() for item in list(event.get("supporting_symbols") or []) if str(item).strip()],
                "kind": "event",
            }
        )
    for mover in must_read:
        preview = _attention_home_bundle_preview(mover, bundle={})
        summary_text = _attention_home_surface_summary(preview, is_event=False)
        beats.append(
            {
                "bundle_id": str(mover.get("bundle_id") or "").strip(),
                "sentence": _attention_mover_card_title(mover),
                "summary": summary_text,
                "symbols": [str(mover.get("symbol") or "").upper().strip()],
                "kind": "mover",
            }
        )
    for mover in unresolved:
        preview = _attention_home_bundle_preview(mover, bundle={})
        summary_text = _attention_home_surface_summary(preview, is_event=False)
        beats.append(
            {
                "bundle_id": str(mover.get("bundle_id") or "").strip(),
                "sentence": _attention_mover_card_title(mover),
                "summary": summary_text or "Large move with insufficient retained evidence so far.",
                "symbols": [str(mover.get("symbol") or "").upper().strip()],
                "kind": "unresolved",
            }
        )
    return beats


def _load_homepage_narrative_payload(cfg: AppConfig, *, force_data_refresh: bool) -> dict[str, object] | None:
    try:
        with _inline_loading_banner(
            "Loading today's narrative home",
            "Assembling the day-only homepage summary from the latest event tape and research snapshot.",
        ):
            return _load_attention_home_1d_cached(
                cfg,
                force_refresh=force_data_refresh,
            )
    except Exception as exc:
        st.warning(f"Could not build Home: {exc}")
        return None


def _render_homepage_v2(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    force_data_refresh = force_data_refresh or _section_refresh_button(
        "homepage_v2_refresh",
        source="attention",
        label="Run attention refresh job",
    )

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

    _render_homepage_v2_graph_banner(home_payload)
    _render_homepage_exp_story_fragment(
        cfg,
        beats,
        run_token=str(home_payload.get("run_id") or home_payload.get("generated_at_utc") or "").strip(),
        force_data_refresh=force_data_refresh,
    )


def _render_homepage_exp(cfg: AppConfig, api: AlpacaAPI | None, *, force_data_refresh: bool) -> None:
    _render_homepage_v2(
        cfg,
        api,
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


def _load_symbol_name_map(
    cfg: AppConfig,
    symbols: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for symbol in sorted({str(value).upper().strip() for value in symbols if str(value).strip()}):
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


def _sync_market_ticker_from_widget() -> None:
    ticker = st.session_state.get("market_ticker_detail_widget")
    if ticker:
        st.session_state["market_selected_ticker"] = ticker


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


_ensure_app_shell_styles()
with _timed("load_config"):
    _enforce_login_gate()
    cfg = load_config()
api: AlpacaAPI | None = None
account: dict[str, object] = {}
startup_error_summary: str | None = None
startup_error_details: str | None = None
startup_setup_code: str | None = None

if cfg is None:
    _log_event("config_invalid")
    key_raw = (os.getenv("APCA_API_KEY") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or "").strip()
    secret_raw = (os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY") or "").strip()
    startup_setup_code = (
        "export APCA_API_KEY='...'\n"
        "export APCA_API_SECRET_KEY='...'\n"
        "export APCA_API_BASE_URL='https://paper-api.alpaca.markets'\n"
        "./scripts/run_ui_local.sh"
    )

    if key_raw.lower() == "your_key_here" or (secret_raw and secret_raw.lower() == "your_secret_here"):
        startup_error_summary = (
            "Market data credentials are incomplete: placeholder values were detected. Replace `your_key_here` and `your_secret_here`."
        )
    else:
        missing = []
        if not (os.getenv("APCA_API_KEY_ID") or os.getenv("APCA_API_KEY") or os.getenv("ALPACA_API_KEY")):
            missing.append("APCA_API_KEY (or APCA_API_KEY_ID)")
        if not (os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")):
            missing.append("APCA_API_SECRET_KEY")

        if missing:
            startup_error_summary = "Market data credentials are missing or incomplete."
            startup_error_details = "Missing: " + ", ".join(missing)
        else:
            startup_error_summary = (
                "Market data credentials are missing or incomplete. Set APCA_API_KEY and APCA_API_SECRET_KEY."
            )

elif cfg.alpaca_trading_base_url.startswith("hhttps://") or not cfg.alpaca_trading_base_url.startswith(("http://", "https://")):
    _log_event("config_invalid_base_url", base_url=cfg.alpaca_trading_base_url)
    startup_error_summary = (
        "Invalid APCA_API_BASE_URL value. Expected the paper or live trading endpoint."
    )
    startup_setup_code = "APCA_API_BASE_URL=https://api.alpaca.markets"

elif cfg.alpaca_api_key.strip().lower() in {"your_key_here", ""}:
    _log_event("config_placeholder_key")
    startup_error_summary = "APCA_API_KEY is still a placeholder. Set your real market data API key."

elif cfg.alpaca_secret_key.strip().lower() == "your_secret_here":
    _log_event("config_placeholder_secret")
    startup_error_summary = "APCA_API_SECRET_KEY is a placeholder. Set your real market data API secret key."

else:
    with _timed("create_api_client"):
        api = _make_api(cfg)

app_track = (os.getenv("APP_TRACK") or "local").strip().lower()
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
pending_workspace_section = _normalize_workspace_section(st.session_state.pop("_pending_workspace_section", ""))
current_workspace_section = _normalize_workspace_section(st.session_state.get("workspace_section"))
if pending_workspace_section in section_options:
    st.session_state["workspace_section"] = pending_workspace_section
elif current_workspace_section in section_options:
    st.session_state["workspace_section"] = current_workspace_section
elif st.session_state.get("workspace_section") not in section_options:
    st.session_state["workspace_section"] = section_options[0]

current_user = _current_user_context()

with st.sidebar:
    _render_sidebar_brand_panel(app_track)
    if app_track:
        st.caption(f"Environment: {_environment_label(app_track)}")
    if current_user is not None:
        st.caption(f"Signed in as {current_user.label}")
        if current_user.can_view_full_portfolio:
            st.caption("Access: Full portfolio")
        else:
            st.caption(f"Portfolio share: {_current_user_share_fraction() * 100:.2f}%")
    elif st.session_state.get("_ui_auth_mode") == "legacy":
        st.caption("Signed in via legacy admin login")

    section = st.selectbox("Workspace", section_options, key="workspace_section")

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
                auth_service.logout_session(str(st.session_state.get("_ui_auth_session_id") or ""))
            else:
                _invalidate_auth_session(st.session_state.get("_ui_auth_session_id"))
            st.session_state["_ui_authenticated"] = False
            st.session_state["_ui_auth_session_id"] = None
            st.session_state["_ui_auth_mode"] = None
            _store_user_context(None)
            st.session_state["_ui_clear_auth_cookie"] = True
            st.rerun()
        sidebar_connection = st.empty()
        sidebar_status = st.empty()
        sidebar_buying_power = st.empty()

_log_event("ui_sidebar_ready")
_log_event("section_selected", section=section)

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

elif section == "Daily Tape":
    _render_home_attention(cfg, api, force_data_refresh=force_data_refresh)

elif section == "Portfolio Overview":
    header_cols = st.columns([3.2, 1.6, 1.4])
    with header_cols[0]:
        st.title("Portfolio Overview")
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption(f"Viewing your {_current_user_share_fraction() * 100:.2f}% economic share of the master portfolio.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="portfolio_overview_period")
    with header_cols[2]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "portfolio_overview_refresh",
            source="equities",
            label="Run equities refresh job",
        )
    if not _has_live_api(api, "Portfolio Overview requires a working live account connection."):
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

elif section == "Performance":
    header_cols = st.columns([3.2, 1.6, 1.4])
    with header_cols[0]:
        st.title("Performance")
        if current_user is not None and not current_user.can_view_full_portfolio:
            st.caption("Return-based metrics match the master portfolio while your ownership share remains fixed.")
    with header_cols[1]:
        period = st.selectbox("History Period", ["1M", "3M", "6M", "1Y", "2Y", "5Y"], index=3, key="performance_period")
    with header_cols[2]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "performance_refresh",
            source="equities",
            label="Run equities refresh job",
        )
    if not _has_live_api(
        api,
        "Performance requires a working portfolio history snapshot or live account connection.",
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

elif section == "FRED Macro":
    header_cols = st.columns([5.2, 1.4])
    with header_cols[0]:
        st.title("FRED Macro")
        st.caption(
            "Economic indicators sourced from FRED. Observations are loaded from FRED v2 bulk release downloads, "
            "then filtered interactively in-app."
        )
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "fred_macro_refresh",
            source="fred",
            label="Run FRED refresh job",
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
        fred_control_cols = st.columns([2, 1])
        with fred_control_cols[0]:
            lookback_years = st.slider("Lookback (years)", 3, 20, 10, step=1)
        with fred_control_cols[1]:
            show_stationary_overlay = st.checkbox(
                "Overlay stationarized change",
                value=False,
                help="Adds an obs-to-obs transformed series on a secondary axis. Level series use percent change; rate-like series use first differences.",
            )
        fred_cache_key = f"{_fred_cache_scope(fred_api_key)}__{lookback_years}y"
        fred_cache_ready = cache_bundle_exists(
            "fred_dashboard",
            fred_cache_key,
            required_files=["summary.csv", "observations.csv"],
        )
        load_fred_now = st.button(
            "Load FRED Data",
            type="primary" if not fred_cache_ready else "secondary",
            help="Cold bulk downloads can take a while on remote sessions. Cached data loads immediately.",
        )
        allow_fred_defer = (not pipeline_store_configured()) and (not cache_disabled)
        if allow_fred_defer and not fred_cache_ready and not load_fred_now and not force_data_refresh:
            st.info(
                "FRED bulk downloads are deferred until requested. This prevents the app from appearing to hang on "
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
                "FRED v2 bulk is release-scoped rather than full-catalog. This dashboard loads the releases needed "
                "for inflation, labor, housing, credit distress, and money-supply analysis."
            )

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
        st.subheader("Indicator Snapshot")
        st.dataframe(
            overview[["category", "indicator", "latest", "prev", "yoy", "latest_date"]],
            use_container_width=True,
            hide_index=True,
        )

        if not series_index.empty and not observations.empty:
            st.subheader("Series Explorer")
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
                selected_frequency = str(selected_meta.get("frequency_short") or selected_meta.get("frequency") or "")
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
                )

        tabs = st.tabs(fred_categories())
        for tab, category in zip(tabs, fred_categories()):
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
                        st.caption(
                            f"{spec.blurb} | YoY: {format_fred_delta(yoy_delta, meta.get('units_short'))} | "
                            f"{meta.get('frequency_short', '')} | Last obs: {date_label}"
                        )
                        st.plotly_chart(
                            build_fred_figure(spec, meta, frame, show_stationary_overlay=show_stationary_overlay),
                            use_container_width=True,
                        )

elif section == "Pipeline Jobs":
    st.title("Pipeline Jobs")
    st.caption("Run remote snapshot refresh jobs here, then inspect the latest execution status below.")
    st.info(
        "Page-level `Refresh cached data` buttons bypass the local CSV cache for the current view. "
        "The controls below trigger the remote pipeline jobs that rebuild snapshot data."
    )

    if source_refresh_flags:
        if st.button("Clear local refresh overrides", key="clear_source_refresh_overrides"):
            source_refresh_flags = {}
            st.session_state["_source_force_refresh"] = source_refresh_flags
            st.rerun()

    job_cards = st.columns(2)
    for index, group in enumerate(_job_control_groups()):
        with job_cards[index % 2]:
            job_name = str(group["job_name"])
            sources = [SOURCE_LABELS.get(source_key, source_key.title()) for source_key in group["sources"]]
            datasets = [str(item) for item in group["datasets"]]
            st.markdown(f"#### {group['label']}")
            st.caption(f"Job: `{job_name}`")
            st.caption("Covers: " + ", ".join(sources))
            if datasets:
                preview = ", ".join(datasets[:4])
                if len(datasets) > 4:
                    preview += f", +{len(datasets) - 4} more"
                st.caption(f"Datasets: {preview}")
            if st.button("Run refresh job", key=f"run_job_{job_name}", use_container_width=True):
                ok, msg = start_source_refresh_job(str(group["sources"][0]))
                if ok:
                    for source_key in group["sources"]:
                        source_refresh_flags[str(source_key)] = True
                    st.session_state["_source_force_refresh"] = source_refresh_flags
                    st.success(msg)
                else:
                    st.warning(msg)
            st.markdown("---")

    with st.spinner("Loading latest job executions..."):
        with _timed("load_job_status_table"):
            status_table = latest_job_status_table()

    if status_table.empty:
        st.info("No job status rows returned.")
    else:
        succeeded = int((status_table["status"] == "Succeeded").sum()) if "status" in status_table.columns else 0
        running = int((status_table["status"] == "Running").sum()) if "status" in status_table.columns else 0
        failing = int((status_table["status"] == "Failed").sum()) if "status" in status_table.columns else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Succeeded", succeeded)
        c2.metric("Running", running)
        c3.metric("Failed", failing)

        display = status_table.rename(
            columns={
                "job_name": "Job Name",
                "run": "Run",
                "status": "Status",
                "progress_stage": "Stage",
                "progress_pct": "Progress %",
                "heartbeat_time_utc": "Heartbeat (UTC)",
                "start_time_utc": "Start (UTC)",
                "end_time_utc": "End (UTC)",
                "message": "Message",
            }
        )
        st.dataframe(display, use_container_width=True, hide_index=True)

elif section == "Market Opportunity":
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title("Market Opportunity")
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "market_opportunity_refresh",
            source="equities",
            label="Run equities refresh job",
        )
    if not _has_live_api(
        api,
        "Market Opportunity requires a working live market connection or pipeline snapshots.",
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
            momentum_days = MARKET_MOMENTUM_SCAN_DAYS
            selected_horizon_col = MARKET_MOMENTUM_HORIZON_COLUMNS.get(momentum_horizon, "return_1m_pct")
            selected_horizon_label = MARKET_MOMENTUM_HORIZON_LABELS.get(momentum_horizon, momentum_horizon)

            try:
                with st.spinner("Scanning market movers..."):
                    with _timed("scan_daily_movers"):
                        movers = _scan_daily_movers_cached(
                            cfg,
                            symbols=business_symbols,
                            force_refresh=force_data_refresh,
                        )
            except AlpacaAPIError as exc:
                _log_event("scan_daily_movers_failed", error=str(exc)[:200])
                st.warning(f"Could not scan movers: {exc}")
                movers = pd.DataFrame()

            try:
                with st.spinner("Scanning momentum profiles..."):
                    with _timed("scan_momentum_profiles", days=momentum_days, horizon=momentum_horizon):
                        momentum = _scan_momentum_profiles_cached(
                            cfg,
                            momentum_days,
                            symbols=business_symbols,
                            force_refresh=force_data_refresh,
                        )
            except AlpacaAPIError as exc:
                _log_event(
                    "scan_momentum_profiles_failed",
                    error=str(exc)[:200],
                    days=momentum_days,
                    horizon=momentum_horizon,
                )
                st.warning(f"Could not scan momentum profiles: {exc}")
                momentum = pd.DataFrame()

        if market_view != "Markets":
            st.stop()

        if not momentum.empty:
            selected_horizon_col = locals().get("selected_horizon_col", "return_1m_pct")
            selected_horizon_label = locals().get("selected_horizon_label", "1 Month")
            for horizon_col in set(MARKET_MOMENTUM_HORIZON_COLUMNS.values()):
                if horizon_col not in momentum.columns:
                    momentum[horizon_col] = np.nan

            ranking_col = selected_horizon_col
            if ranking_col not in momentum.columns or not pd.to_numeric(momentum[ranking_col], errors="coerce").notna().any():
                ranking_col = "momentum_score"
                st.caption(f"{selected_horizon_label} return unavailable for current snapshot. Falling back to momentum score.")
            else:
                st.caption(f"Ranking by {selected_horizon_label} return.")

            raw_columns = list(
                dict.fromkeys(
                    ["symbol", "sparkline_3m", "close", "daily_change_pct", selected_horizon_col, "momentum_score"]
                )
            )
            roc_columns = list(
                dict.fromkeys(
                    ["symbol", "sparkline_3m", "close", "return_1w_pct", selected_horizon_col, "roc_1m_to_3m", "momentum_roc_score"]
                )
            )
            raw_up = select_signed_ranked(momentum, ranking_col, direction="up", limit=20)[raw_columns]
            raw_down = select_signed_ranked(momentum, ranking_col, direction="down", limit=20)[raw_columns]
            roc_up = select_signed_ranked(momentum, "momentum_roc_score", direction="up", limit=20)[roc_columns]
            roc_down = select_signed_ranked(momentum, "momentum_roc_score", direction="down", limit=20)[roc_columns]

            st.markdown("##### Momentum Raw")
            visible_market_symbols = sorted(
                {
                    str(symbol).upper().strip()
                    for table in (raw_up, raw_down, roc_up, roc_down)
                    for symbol in table.get("symbol", pd.Series(dtype=str)).tolist()
                    if str(symbol).strip()
                }
            )
            row1_left, row1_right = st.columns(2)
            name_map = _load_symbol_name_map(
                cfg,
                visible_market_symbols,
                force_refresh=force_data_refresh,
            )
            raw_up_table, raw_up_column_config = _prepare_momentum_table(raw_up, name_map=name_map)
            raw_down_table, raw_down_column_config = _prepare_momentum_table(raw_down, name_map=name_map)
            with row1_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Top 20 Up",
                    raw_up_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                "daily_change_pct",
                                selected_horizon_col,
                                "momentum_score",
                            ]
                        )
                    ),
                    key="market_momentum_raw_up",
                    column_config=raw_up_column_config,
                ) or selected_market_ticker
            with row1_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Top 20 Down",
                    raw_down_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                "daily_change_pct",
                                selected_horizon_col,
                                "momentum_score",
                            ]
                        )
                    ),
                    key="market_momentum_raw_down",
                    column_config=raw_down_column_config,
                ) or selected_market_ticker

            st.markdown("##### Momentum RoC")
            row2_left, row2_right = st.columns(2)
            roc_up_table, roc_up_column_config = _prepare_momentum_table(roc_up, name_map=name_map)
            roc_down_table, roc_down_column_config = _prepare_momentum_table(roc_down, name_map=name_map)
            with row2_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Up",
                    roc_up_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                "return_1w_pct",
                                selected_horizon_col,
                                "momentum_roc_score",
                            ]
                        )
                    ),
                    key="market_momentum_roc_up",
                    column_config=roc_up_column_config,
                ) or selected_market_ticker
            with row2_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Down",
                    roc_down_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                "return_1w_pct",
                                selected_horizon_col,
                                "momentum_roc_score",
                            ]
                        )
                    ),
                    key="market_momentum_roc_down",
                    column_config=roc_down_column_config,
                ) or selected_market_ticker

            st.markdown("##### Momentum Consistency")
            st.caption("Sorted by the lowest 3-month trendline-fit gap; lower means price has stayed closer to trend.")
            momentum = momentum.copy()
            momentum["trend_consistency_pct"] = (1.0 - pd.to_numeric(momentum["trend_fit_gap"], errors="coerce")) * 100.0
            consistency_up = momentum[pd.to_numeric(momentum["momentum_score"], errors="coerce") > 0].nsmallest(20, "trend_fit_gap")[
                list(
                    dict.fromkeys(
                        [
                            "symbol",
                            "sparkline_3m",
                            "close",
                            selected_horizon_col,
                            "return_3m_pct",
                            "trend_consistency_pct",
                            "trend_fit_gap",
                        ]
                    )
                )
            ]
            consistency_down = momentum[pd.to_numeric(momentum["momentum_score"], errors="coerce") < 0].nsmallest(20, "trend_fit_gap")[
                list(
                    dict.fromkeys(
                        [
                            "symbol",
                            "sparkline_3m",
                            "close",
                            selected_horizon_col,
                            "return_3m_pct",
                            "trend_consistency_pct",
                            "trend_fit_gap",
                        ]
                    )
                )
            ]
            consistency_symbols = sorted(
                {
                    *visible_market_symbols,
                    *[
                        str(symbol).upper().strip()
                        for table in (consistency_up, consistency_down)
                        for symbol in table.get("symbol", pd.Series(dtype=str)).tolist()
                        if str(symbol).strip()
                    ],
                }
            )
            if len(consistency_symbols) != len(visible_market_symbols):
                name_map.update(
                    _load_symbol_name_map(
                        cfg,
                        consistency_symbols,
                        force_refresh=force_data_refresh,
                    )
                )
            consistency_up_table, consistency_up_column_config = _prepare_momentum_table(consistency_up, name_map=name_map)
            consistency_down_table, consistency_down_column_config = _prepare_momentum_table(consistency_down, name_map=name_map)
            row3_left, row3_right = st.columns(2)
            with row3_left:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Up",
                    consistency_up_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                selected_horizon_col,
                                "trend_consistency_pct",
                                "trend_fit_gap",
                            ]
                        )
                    ),
                    key="market_momentum_consistency_up",
                    column_config=consistency_up_column_config,
                ) or selected_market_ticker
            with row3_right:
                selected_market_ticker = _render_selectable_ticker_table(
                    "Down",
                    consistency_down_table,
                    list(
                        dict.fromkeys(
                            [
                                "symbol",
                                "company_name",
                                "sparkline_3m",
                                "close",
                                selected_horizon_col,
                                "trend_consistency_pct",
                                "trend_fit_gap",
                            ]
                        )
                    ),
                    key="market_momentum_consistency_down",
                    column_config=consistency_down_column_config,
                ) or selected_market_ticker
        else:
            st.info("No momentum profiles were returned for this market lens.")

        if not movers.empty:
            tree = px.treemap(
                movers,
                path=["symbol"],
                values="volume",
                color="change_pct",
                color_continuous_scale="RdYlGn",
                template="plotly_dark",
                title=f"Daily Movers - {business_filter} (Volume / Change %)",
            )
            st.plotly_chart(tree, use_container_width=True)
        else:
            st.info("Daily mover snapshots were unavailable for this market lens, so only the momentum-based views are shown.")

        detail_symbols = set(movers["symbol"].astype(str).tolist()) if not movers.empty else set()
        if not momentum.empty:
            detail_symbols.update(momentum["symbol"].astype(str).tolist())
        scanned_detail_symbols = {
            str(symbol).upper().strip()
            for symbol in detail_symbols
            if str(symbol).strip()
        }
        if requested_market_ticker:
            detail_symbols.add(requested_market_ticker)
        if not detail_symbols:
            st.info("No market symbols were available for the detail view.")
            st.stop()
        detail_symbol_options = sorted(detail_symbols)
        selected_key = "market_selected_ticker"
        widget_key = "market_ticker_detail_widget"
        fallback_market_ticker = st.session_state.get(selected_key) or detail_symbol_options[0]
        if fallback_market_ticker not in detail_symbol_options:
            fallback_market_ticker = detail_symbol_options[0]

        current_market_ticker = st.session_state.get(selected_key)
        if current_market_ticker not in detail_symbol_options:
            current_market_ticker = fallback_market_ticker

        if requested_market_ticker and requested_market_ticker not in scanned_detail_symbols:
            st.caption(
                f"{requested_market_ticker} was opened from attention and pinned into the detail view because it is outside the current {business_filter} scan lens."
            )

        if selected_market_ticker and selected_market_ticker in detail_symbol_options:
            if selected_market_ticker != current_market_ticker:
                st.session_state[selected_key] = selected_market_ticker
                st.session_state[widget_key] = selected_market_ticker
                st.rerun()
        elif selected_key not in st.session_state or st.session_state[selected_key] not in detail_symbol_options:
            st.session_state[selected_key] = fallback_market_ticker

        if widget_key not in st.session_state or st.session_state[widget_key] not in detail_symbol_options:
            st.session_state[widget_key] = st.session_state[selected_key]
        elif st.session_state[widget_key] != st.session_state[selected_key]:
            st.session_state[widget_key] = st.session_state[selected_key]

        ticker = st.selectbox(
            "Ticker Detail",
            detail_symbol_options,
            key=widget_key,
            on_change=_sync_market_ticker_from_widget,
        )
        st.session_state[selected_key] = ticker
        days = st.slider("Days", 60, 720, 365, step=30)

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

        signal_summary: dict[str, object] = {}
        if not price.empty:
            if _SIGNALS_IMPORT_ERROR:
                st.warning(
                    "Advanced signal charts are temporarily unavailable because the optional signals module "
                    f"did not load: {_SIGNALS_IMPORT_ERROR}"
                )
                fig = go.Figure(
                    go.Scatter(
                        x=price["timestamp"],
                        y=price["close"],
                        mode="lines",
                        name=ticker,
                    )
                )
                fig.update_layout(template="plotly_dark", title=f"{ticker} Price", xaxis_title="Date", yaxis_title="Price")
                st.plotly_chart(fig, use_container_width=True)

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

        st.subheader(f"{ticker} Overview")
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
        background_summary = (
            llm_summary_text
            or primary_context_text
            or company_background_text
            or str(background_payload.get("llm_summary_text") or "").strip()
            or description_text
        )
        what_happened_summary = llm_headline or (summary_lines[0] if summary_lines else "") or description_text
        evidence_links = _collect_evidence_links(
            recent_headlines=list(background_payload.get("recent_headlines") or []),
            articles=news_summary.get("articles", pd.DataFrame()),
            limit=8,
        )
        _render_compact_background_sections(
            ticker,
            background_summary=background_summary,
            what_happened_summary=what_happened_summary,
            evidence_links=evidence_links,
        )
        _render_overview_fundamentals(
            cfg,
            ticker,
            force_data_refresh=force_data_refresh,
            asof_time_utc=background_payload.get("asof_time_utc"),
        )

elif section == "Technical Strategizer":
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title("Technical Strategizer")
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "technical_refresh",
            source="derivatives",
            label="Run derivatives refresh job",
        )
    ticker = st.text_input("Ticker", value="AAPL", key="technical_ticker").upper().strip()
    days = st.slider("Lookback (days)", 90, 1095, 365, step=15)

    if ticker and _has_live_api(
        api,
        "Technical Strategizer requires a working live market connection or pipeline snapshots.",
        allow_pipeline=True,
    ):
        try:
            with st.spinner("Loading technical data..."):
                with _timed("technical_load_price_history", ticker=ticker, days=days):
                    frame = _load_price_history_cached(cfg, ticker, days=days, force_refresh=force_data_refresh)
        except AlpacaAPIError as exc:
            _log_event("technical_load_price_history_failed", ticker=ticker, error=str(exc)[:200])
            st.error(f"Could not load technical data: {exc}")
            st.stop()

        if frame.empty:
            st.info("No bars returned for this ticker.")
        else:
            st.plotly_chart(build_technical_figure(frame, f"Technical View - {ticker}"), use_container_width=True)
            st.dataframe(frame.tail(40), use_container_width=True, hide_index=True)

            signal_frame = _load_technical_signal_history_cached(
                cfg,
                ticker,
                days=max(days, 180),
                force_refresh=force_data_refresh,
            )
            signal_summary = _load_technical_signal_summary_cached(
                cfg,
                ticker,
                signal_frame,
                force_refresh=force_data_refresh,
            )
            if signal_summary:
                metric_cols = st.columns(5)
                with metric_cols[0]:
                    st.metric("Signal Regime", str(signal_summary.get("regime") or "n/a"))
                with metric_cols[1]:
                    st.metric("RSI 14", f"{pd.to_numeric(signal_summary.get('rsi_14'), errors='coerce'):.1f}")
                with metric_cols[2]:
                    st.metric("Pullback vs ATH", f"{pd.to_numeric(signal_summary.get('pullback_from_ath_pct'), errors='coerce'):.1f}%")
                with metric_cols[3]:
                    st.metric("Channel Position", f"{pd.to_numeric(signal_summary.get('channel_position'), errors='coerce') * 100:.0f}%")
                with metric_cols[4]:
                    st.metric("20D Vol (ann)", f"{pd.to_numeric(signal_summary.get('vol_20_ann_pct'), errors='coerce'):.1f}%")

elif section == "Option Strategizer":
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title("Option Strategizer")
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "option_refresh",
            source="options",
            label="Run options refresh job",
        )
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

elif section == "Fundamental Strategizer":
    header_cols = st.columns([4.8, 1.4])
    with header_cols[0]:
        st.title("Fundamental Strategizer")
    with header_cols[1]:
        force_data_refresh = force_data_refresh or _section_refresh_button(
            "fundamental_refresh",
            source="fundamentals",
            label="Run fundamentals refresh job",
        )
    ticker = st.text_input("Ticker", value="AAPL", key="fund_ticker").upper().strip()

    if ticker:
        try:
            with st.spinner("Loading quarterly fundamentals..."):
                with _timed("load_quarterly_fundamentals", ticker=ticker):
                    data = _load_quarterly_fundamentals_cached(ticker, force_refresh=force_data_refresh)
        except Exception as exc:
            _log_event("load_quarterly_fundamentals_failed", ticker=ticker, error=str(exc)[:200])
            st.warning(f"Could not load quarterly fundamentals: {exc}")
            data = {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
        income = data.get("income", pd.DataFrame())
        balance = data.get("balance", pd.DataFrame())
        cashflow = data.get("cashflow", pd.DataFrame())

        if income.empty and balance.empty and cashflow.empty:
            st.info("No quarterly fundamentals found for this ticker in the local quarterly dataset.")
        else:
            _render_fundamental_statement_charts(ticker, data, quarterly_titles=True)

            with st.expander("Show Raw Fundamental Tables"):
                st.subheader("Income")
                st.dataframe(income, use_container_width=True, hide_index=True)
                st.subheader("Balance")
                st.dataframe(balance, use_container_width=True, hide_index=True)
                st.subheader("Cash Flow")
                st.dataframe(cashflow, use_container_width=True, hide_index=True)
