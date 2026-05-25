from __future__ import annotations

import pandas as pd
import streamlit as st

from presentation import dashboard_loaders
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
from services.page_agentic_summary import broad_economy_summary_context
from views._shared import (
    BROAD_ECONOMY_SECTION,
    _log_event,
    _render_page_agentic_summary_panel,
    _responsive_columns,
    _responsive_two_panel,
    _timed,
)


def _render_broad_economy_section(
    *,
    force_data_refresh: bool,
) -> None:
    st.title(BROAD_ECONOMY_SECTION)

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
        try:
            with st.spinner("Loading FRED macro dashboard..."):
                with _timed("load_fred_dashboard", years=lookback_years):
                    dashboard = dashboard_loaders._load_fred_dashboard_cached(
                        fred_api_key,
                        lookback_years,
                        force_refresh=force_data_refresh,
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
            hero_metric_cols = _responsive_columns(4)
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

                chart_cols = _responsive_two_panel()
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

                explorer_cols = _responsive_columns([2, 1])
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

                    explorer_metric_cols = _responsive_columns(3)
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
            bulk_cols = _responsive_columns(3)
            with bulk_cols[0]:
                st.metric("Loaded Releases", f"{release_count}")
            with bulk_cols[1]:
                st.metric("Loaded Series", f"{series_count}")
            with bulk_cols[2]:
                st.metric("Curated Indicators", f"{len(summary)}")
