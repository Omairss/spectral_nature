from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.config import AppConfig
from services.json_utils import to_list
from services.pipeline_store import (
    load_latest_dataset_frame,
    load_recent_dataset_frames,
    record_trading_agent_action,
    trading_agent_actions_table,
)
from views._shared import (
    LOGGER,
    TRADING_AGENT_SECTION,
    _current_user_context,
    _current_user_is_admin,
    _responsive_columns,
)

def _trading_agent_text(value: object) -> str:
    if value is None:
        return ""
    out = str(value).strip()
    return "" if out.lower() == "nan" else out

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
        st.caption("No 3M sparkline available.")
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
        header_cols = _responsive_columns([3.7, 1.0, 1.0, 1.1])
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

        signal_cols = _responsive_columns([1.05, 1.35, 1.2])
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

        price_cols = _responsive_columns(4)
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

        body_cols = _responsive_columns([1.15, 1.0])
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

        action_cols = _responsive_columns([1.0, 1.0, 2.4])
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
                from views._shared import _set_workspace_ticker, _open_workspace_section, STOCK_INVESTIGATOR_SECTION
                _set_workspace_ticker(ticker)
                _open_workspace_section(STOCK_INVESTIGATOR_SECTION)

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
    actions = trading_agent_actions_table(limit=500)
    try:
        run_versions = load_recent_dataset_frames("trading_agent_runs", limit=8)
    except Exception:
        run_versions = []
    try:
        candidate_versions = load_recent_dataset_frames("trading_agent_candidates", limit=8)
    except Exception:
        candidate_versions = []

    for runs, metadata in run_versions:
        if not isinstance(runs, pd.DataFrame) or runs.empty or "run_id" not in runs.columns:
            continue
        run_ids = [
            _trading_agent_text(value)
            for value in runs["run_id"].dropna().astype(str).tolist()
            if _trading_agent_text(value)
        ]
        if not run_ids:
            continue
        run_id_set = set(run_ids)
        for candidates, _candidate_metadata in candidate_versions:
            if not isinstance(candidates, pd.DataFrame) or candidates.empty or "run_id" not in candidates.columns:
                continue
            scoped_candidates = candidates[
                candidates["run_id"].astype(str).map(_trading_agent_text).isin(run_id_set)
            ].copy()
            if scoped_candidates.empty:
                continue
            return (
                runs.copy(),
                scoped_candidates,
                actions.copy() if isinstance(actions, pd.DataFrame) else pd.DataFrame(),
                metadata,
            )

    try:
        runs, metadata = load_latest_dataset_frame("trading_agent_runs")
    except Exception:
        runs, metadata = pd.DataFrame(), None
    try:
        candidates, _ = load_latest_dataset_frame("trading_agent_candidates")
    except Exception:
        candidates = pd.DataFrame()
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
        st.info("No Trading Agent candidates are available yet.")
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
