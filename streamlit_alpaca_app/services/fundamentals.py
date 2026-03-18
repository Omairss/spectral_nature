from __future__ import annotations

import pandas as pd
import plotly.express as px

from compute.fundamentals import BALANCE_METRICS, CASHFLOW_METRICS, INCOME_METRICS, STATEMENT_FILES, load_quarterly_fundamentals


def plot_statement(df: pd.DataFrame, title: str):
    if df.empty:
        return px.line(title=title, template="plotly_dark")

    fig = px.line(
        df.sort_values("report_date"),
        x="report_date",
        y="value",
        color="metric",
        markers=True,
        template="plotly_dark",
        title=title,
        hover_data=["year_quarter"] if "year_quarter" in df.columns else None,
    )
    fig.update_layout(hovermode="x unified")
    return fig
