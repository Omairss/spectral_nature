from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from services import api_auth, auth_service
from services.config import AppConfig, load_config
from services.data_cache import dataset_scope
from services.elevenlabs_tts import load_elevenlabs_tts_config
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
from services.pipeline_store import (
    SOURCE_DATASETS,
    SOURCE_JOB_MAP,
    connector_call_rollup,
    dataset_version_history,
    job_run_history,
    latest_job_status_table,
    latest_dataset_status_table,
    load_latest_dataset_frame,
    pipeline_store_configured,
    retained_connector_evidence_health,
    start_source_refresh_job,
)
from services.aql_zopedia_engine import load_aql_zopedia_llm_client
from views._shared import (
    ADMIN_SECTION,
    APP_BRAND_KICKER,
    APP_BRAND_NAME,
    APP_ROOT,
    BRANDING_ROOT,
    JOB_LABELS,
    LOGGER,
    SOURCE_LABELS,
    _current_user_context,
    _has_live_api,
    _inline_image_markup,
    _prime_widget_choice,
    _render_section_back_button,
    _responsive_columns,
    _responsive_two_panel,
    _timed,
)

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

    usage_metrics = _responsive_columns(6)
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
        st.plotly_chart(usage_sankey_figure, use_container_width=True, key="access_admin_usage_sankey")

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
        st.plotly_chart(section_chart, use_container_width=True, key="access_admin_section_usage_chart")
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
        st.plotly_chart(target_chart, use_container_width=True, key="access_admin_selected_user_target_chart")
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

    security_metrics = _responsive_columns(6)
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

    session_metrics = _responsive_columns(3)
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
        st.plotly_chart(admin_chart, use_container_width=True, key="access_admin_admin_usage_chart")

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
        coverage_metrics = _responsive_columns(4)
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

        action_cols = _responsive_columns([1.8, 1.0, 1.0, 1.0])
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

    action_load_col, action_active_col, action_delete_col = _responsive_columns(3)
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

    save_name_col, save_current_col, save_new_col = _responsive_columns([1.8, 1.1, 1.1])
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

    text_col, color_col = _responsive_two_panel()
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
        col1, col2, col3 = _responsive_columns(3)
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
    import services.aql_zopedia_engine  # noqa: F401
    import services.attention_market_events  # noqa: F401
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
        "Zopedia": "Powers the research memory agent. Changes take effect on the next query.",
        "Attention Pipeline": "Generate feed cards, narratives, and event text. Changes take effect on next pipeline run.",
        "AQL / Research": "Write research summaries, hypotheses, and event analysis. Changes take effect on next pipeline run.",
        "AQL / Zopedia Engine": "Shared research, memory, tool, evidence, and summary spine. Changes affect chat immediately and jobs on next run.",
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
        "Zopedia": "Applied to the research memory agent — changes take effect on the next query.",
        "AQL / Zopedia Engine": "Applied to the shared research/memory/tool spine. Chat changes take effect immediately; job changes apply on next run.",
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
                col_label, col_input, col_default = _responsive_columns([3, 1.5, 1.5])
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
                info_cols = _responsive_columns([1, 1, 1])
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
    header_cols = _responsive_columns([4.6, 1.4])
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

    admin_view_options = ["Access Management", "System Health", "Usage", "Security", "LLM Config"]
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
        invite_col, reset_col = _responsive_two_panel()
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
    elif admin_view == "System Health":
        _render_system_health_admin(source_refresh_flags=st.session_state.get("_source_force_refresh", {}))
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
            control_col_1, control_col_2, control_col_3, control_col_4 = _responsive_columns([1, 1, 1.6, 0.9])
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
            control_col_1, control_col_2, control_col_3 = _responsive_columns([1, 1, 1.8])
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


def _format_system_health_timestamp(value: object) -> str:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _render_system_health_admin(*, source_refresh_flags: dict[str, bool]) -> None:
    """System Health admin tab: jobs, datasets, connector telemetry, controls."""

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

    with st.spinner("Loading system health..."):
        with _timed("load_job_run_history"):
            runs = job_run_history(days=history_days)
        with _timed("load_dataset_version_history"):
            datasets = dataset_version_history(days=history_days)
        with _timed("load_job_status_table"):
            status_table = latest_job_status_table()
        with _timed("load_latest_dataset_status_table"):
            dataset_status = latest_dataset_status_table()
        with _timed("load_connector_call_rollup"):
            connector_rollup = connector_call_rollup(days=history_days)
        with _timed("load_retained_connector_evidence_health"):
            retained_provider_health = retained_connector_evidence_health(days=history_days)

    # ── System Overview ─────────────────────────────────────────────────
    st.subheader("System Overview")
    expected_dataset_names = sorted({dataset for items in SOURCE_DATASETS.values() for dataset in items})
    latest_dataset_names = set(dataset_status["dataset_name"].astype(str)) if not dataset_status.empty and "dataset_name" in dataset_status.columns else set()
    missing_dataset_count = len([name for name in expected_dataset_names if name not in latest_dataset_names])
    older_than_window_count = 0
    if not dataset_status.empty and "age_hours" in dataset_status.columns:
        older_than_window_count = int((pd.to_numeric(dataset_status["age_hours"], errors="coerce") > history_days * 24).sum())
    failed_jobs = int((status_table["status"] == "Failed").sum()) if not status_table.empty and "status" in status_table.columns else 0
    running_jobs = int((status_table["status"] == "Running").sum()) if not status_table.empty and "status" in status_table.columns else 0
    connector_failures = int(connector_rollup["failure_count"].sum()) if not connector_rollup.empty and "failure_count" in connector_rollup.columns else 0
    provider_error_rows = int(retained_provider_health["provider_error_rows"].sum()) if not retained_provider_health.empty and "provider_error_rows" in retained_provider_health.columns else 0

    c1, c2, c3, c4 = _responsive_columns(4)
    c1.metric("Failed Jobs", failed_jobs)
    c2.metric("Running Jobs", running_jobs)
    c3.metric("Connector Failures", connector_failures)
    c4.metric("Provider Error Rows", provider_error_rows)

    if missing_dataset_count or older_than_window_count:
        st.warning(
            f"{missing_dataset_count} expected datasets have no known snapshot; "
            f"{older_than_window_count} latest snapshots are older than the selected window."
        )
    elif failed_jobs or connector_failures or provider_error_rows:
        st.warning("One or more health signals need attention.")
    else:
        st.success("No failed jobs or connector failures in the selected window.")

    # ── Connector Health ────────────────────────────────────────────────
    st.subheader("Connector Calls")
    if connector_rollup.empty:
        st.caption("No connector-call telemetry has been recorded in this window.")
    else:
        connector_plot = connector_rollup.melt(
            id_vars=["provider", "operation"],
            value_vars=["success_count", "failure_count"],
            var_name="status",
            value_name="calls",
        )
        connector_plot["label"] = connector_plot["provider"].astype(str) + " · " + connector_plot["operation"].astype(str)
        fig_connectors = px.bar(
            connector_plot,
            x="label",
            y="calls",
            color="status",
            barmode="stack",
            color_discrete_map={"success_count": "#2ecc71", "failure_count": "#e74c3c"},
            labels={"label": "Connector", "calls": "Calls", "status": "Status"},
        )
        fig_connectors.update_layout(height=300, xaxis_title="", yaxis_title="Calls", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_connectors, use_container_width=True, key="access_admin_connector_health_chart")
        connector_display = connector_rollup.copy()
        connector_display["last_call_at_utc"] = connector_display["last_call_at_utc"].apply(_format_system_health_timestamp)
        connector_display = connector_display.rename(
            columns={
                "provider": "Provider",
                "operation": "Operation",
                "call_count": "Calls",
                "success_count": "Succeeded",
                "failure_count": "Failed",
                "result_count": "Results",
                "avg_duration_ms": "Avg ms",
                "last_call_at_utc": "Last Call",
                "last_error_summary": "Latest Error",
            }
        )
        st.dataframe(connector_display, use_container_width=True, hide_index=True)

    with st.expander("Retained Provider Evidence", expanded=connector_rollup.empty):
        if retained_provider_health.empty:
            st.info("No retained provider evidence found in this window.")
        else:
            provider_display = retained_provider_health.copy()
            provider_display["last_seen_at_utc"] = provider_display["last_seen_at_utc"].apply(_format_system_health_timestamp)
            provider_display = provider_display.rename(
                columns={
                    "provider": "Provider",
                    "evidence_rows": "Evidence Rows",
                    "document_rows": "Documents",
                    "chunk_rows": "Chunks",
                    "provider_error_rows": "Provider Error Rows",
                    "last_seen_at_utc": "Last Seen",
                }
            )
            st.dataframe(provider_display, use_container_width=True, hide_index=True)

    # ── Latest Dataset Snapshots ────────────────────────────────────────
    st.subheader("Dataset Freshness")
    if dataset_status.empty:
        st.info("No dataset snapshot metadata is available.")
    else:
        dataset_display = dataset_status.copy()
        dataset_display["health"] = "Recent"
        dataset_display.loc[pd.to_numeric(dataset_display["age_hours"], errors="coerce") > history_days * 24, "health"] = "Older Than Window"
        dataset_display["ingested_at_utc"] = dataset_display["ingested_at_utc"].apply(_format_system_health_timestamp)
        dataset_display = dataset_display.rename(
            columns={
                "dataset_name": "Dataset",
                "dataset_version_id": "Version",
                "row_count": "Rows",
                "ingested_at_utc": "Ingested",
                "run_id": "Run",
                "age_hours": "Age Hours",
                "health": "Health",
            }
        )
        st.dataframe(
            dataset_display[["Health", "Dataset", "Rows", "Age Hours", "Ingested", "Run", "Version"]],
            use_container_width=True,
            hide_index=True,
        )

    # ── Timeline ────────────────────────────────────────────────────────
    st.subheader("Pipeline Job Runs")
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
        st.plotly_chart(fig_timeline, use_container_width=True, key="access_admin_pipeline_timeline_chart")

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
        st.plotly_chart(fig_fail, use_container_width=True, key="access_admin_pipeline_failures_chart")

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
        st.plotly_chart(fig_rows, use_container_width=True, key="access_admin_dataset_rows_chart")

    # ── Latest Status Metrics ───────────────────────────────────────────
    st.subheader("Latest Status")
    if not status_table.empty and "status" in status_table.columns:
        succeeded = int((status_table["status"] == "Succeeded").sum())
        running = int((status_table["status"] == "Running").sum())
        failing = int((status_table["status"] == "Failed").sum())
        c1, c2, c3 = _responsive_columns(3)
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

        cols = _responsive_columns([3, 2, 1.2])
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
