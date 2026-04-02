from __future__ import annotations

import pandas as pd
import plotly.express as px

from compute.fundamentals import BALANCE_METRICS, CASHFLOW_METRICS, INCOME_METRICS, STATEMENT_FILES, load_quarterly_fundamentals


def plot_statement(
    df: pd.DataFrame,
    title: str,
    *,
    show_title: bool = True,
    legend_bottom: bool = False,
):
    if df.empty:
        fig = px.line(title=title if show_title else None, template="plotly_dark")
        if legend_bottom:
            fig.update_layout(
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.22,
                    xanchor="left",
                    x=0,
                    title_text="",
                ),
                margin=dict(t=24 if not show_title else 60, b=90),
            )
        return fig

    fig = px.line(
        df.sort_values("report_date"),
        x="report_date",
        y="value",
        color="metric",
        markers=True,
        template="plotly_dark",
        title=title if show_title else None,
        hover_data=["year_quarter"] if "year_quarter" in df.columns else None,
    )
    layout_updates = {"hovermode": "x unified"}
    if legend_bottom:
        layout_updates.update(
            {
                "legend": {
                    "orientation": "h",
                    "yanchor": "top",
                    "y": -0.22,
                    "xanchor": "left",
                    "x": 0,
                    "title_text": "",
                },
                "margin": {"t": 24 if not show_title else 60, "b": 90},
            }
        )
    elif not show_title:
        layout_updates["margin"] = {"t": 24}
    fig.update_layout(**layout_updates)
    return fig
