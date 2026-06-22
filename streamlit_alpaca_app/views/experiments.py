from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from presentation import dashboard_loaders
from services.alpaca_api import AlpacaAPIError
from services.config import AppConfig
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
from services.market import (
    commodity_dependency_graph,
    commodity_focus_description,
    commodity_proxy_profile,
    commodity_reference_universe,
)
from services.pipeline_store import load_latest_dataset_frame
from views._shared import (
    _current_user_context,
    _log_event,
    _prepare_scatter_size,
    _render_help_popover,
    _render_selectable_ticker_table,
    _responsive_columns,
    _responsive_two_panel,
    _timed,
)

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

    header_cols = _responsive_columns([4.8, 1.4])
    with header_cols[0]:
        st.title("Knowledge Graph Lab")
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

    status_cols = _responsive_columns(5)
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

    control_cols = _responsive_columns([1.4, 1.4, 2.2])
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
        st.plotly_chart(
            plot_knowledge_graph_draft(overview),
            use_container_width=True,
            key="experiments_knowledge_graph_overview_chart",
        )
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

        st.plotly_chart(
            plot_knowledge_graph_draft(draft),
            use_container_width=True,
            key="experiments_knowledge_graph_editable_draft_chart",
        )
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


def _render_market_opportunity_experiments(
    cfg: AppConfig,
    force_data_refresh: bool,
    advanced_view: str,
    lens_label: str,
    lens_name: str,
    lens_symbols: list[str],
) -> None:
    heading_cols = _responsive_columns([10, 2])
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
    heading_cols = _responsive_columns([10, 2])
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

    control_cols = _responsive_columns(5)
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
                experiment_data = dashboard_loaders._load_correlation_phase_shift_cached(
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

    table_left, table_right = _responsive_two_panel()
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

    chart_left, chart_right = _responsive_two_panel()
    with chart_left:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_phase, use_container_width=True, key=f"experiments_phase_shift_{benchmark}_chart")

    with chart_right:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_phase_3d, use_container_width=True, key=f"experiments_phase_surface_{benchmark}_chart")

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
    metric_cols = _responsive_columns(6)
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

    detail_chart_left, detail_chart_right = _responsive_two_panel()
    with detail_chart_left:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_relative, use_container_width=True, key=f"experiments_{experiment_ticker}_{benchmark}_relative_chart")

    with detail_chart_right:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_corr, use_container_width=True, key=f"experiments_{experiment_ticker}_{benchmark}_correlation_chart")

    momentum_help_cols = _responsive_columns([10, 2])
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
    st.plotly_chart(fig_momentum, use_container_width=True, key=f"experiments_{experiment_ticker}_momentum_chart")

def _render_commodity_experiment(
    cfg: AppConfig,
    force_data_refresh: bool,
    commodity_focus: str,
    commodity_symbols: list[str],
) -> None:
    heading_cols = _responsive_columns([10, 2])
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

    control_cols = _responsive_columns(4)
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
                experiment_data = dashboard_loaders._load_commodity_regime_cached(
                    cfg,
                    commodity_symbols=reference_symbols,
                    days=experiment_days,
                    corr_window=corr_window,
                    roc_window=roc_window,
                    momentum_window=momentum_window,
                    symbols=commodity_symbols,
                    force_refresh=force_data_refresh,
                )
                momentum = dashboard_loaders._scan_momentum_profiles_cached(
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

    metric_cols = _responsive_columns(4)
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

    table_left, table_right = _responsive_two_panel()
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

    series_left, series_right = _responsive_two_panel()
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
                key=f"experiments_{commodity_focus}_moving_up_chart",
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
                key=f"experiments_{commodity_focus}_moving_down_chart",
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
            key=f"experiments_{commodity_focus}_consistent_trends_chart",
        )

    heatmap_frame = summary.sort_values("return_1m_pct", ascending=False, na_position="last").reset_index(drop=True)
    heatmap_values = heatmap_frame[
        ["daily_change_pct", "return_1w_pct", "return_1m_pct", "return_3m_pct", "relative_strength_pct"]
    ].apply(pd.to_numeric, errors="coerce")
    heatmap_labels = [f"{row['commodity_label']} ({row['symbol']})" for _, row in heatmap_frame.iterrows()]

    scatter_frame = summary.copy()
    scatter_frame["rotation_abs"] = pd.to_numeric(scatter_frame["momentum_roc_score"], errors="coerce").abs()
    scatter_frame, commodity_size_col = _prepare_scatter_size(scatter_frame, "rotation_abs")

    chart_left, chart_right = _responsive_two_panel()
    with chart_left:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_heatmap, use_container_width=True, key=f"experiments_{commodity_focus}_direction_heatmap")

    with chart_right:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_commodity, use_container_width=True, key=f"experiments_{commodity_focus}_leadership_map")

    dependency_edges = commodity_dependency_graph(commodity_symbols)
    if not dependency_edges.empty:
        sankey_left, sankey_right = _responsive_columns([2.2, 1.2])
        with sankey_left:
            chart_help_cols = _responsive_columns([10, 2])
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
            st.plotly_chart(fig_sankey, use_container_width=True, key=f"experiments_{commodity_focus}_dependency_sankey")

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

    metric_cols = _responsive_columns(6)
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

    detail_chart_left, detail_chart_right = _responsive_two_panel()
    with detail_chart_left:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_relative, use_container_width=True, key=f"experiments_{commodity_ticker}_commodity_relative_chart")

    with detail_chart_right:
        chart_help_cols = _responsive_columns([10, 2])
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
        st.plotly_chart(fig_ladder, use_container_width=True, key=f"experiments_{commodity_ticker}_return_ladder_chart")

    transmission_help_cols = _responsive_columns([10, 2])
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
    st.plotly_chart(fig_transmission, use_container_width=True, key=f"experiments_{commodity_ticker}_trend_structure_chart")

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
