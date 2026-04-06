from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots



def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["sma_20"] = out["close"].rolling(20).mean()
    out["sma_50"] = out["close"].rolling(50).mean()

    delta = out["close"].diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain_ema = pd.Series(gain, index=out.index).ewm(alpha=1 / 14, adjust=False).mean()
    loss_ema = pd.Series(loss, index=out.index).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain_ema / loss_ema.replace(0, np.nan)
    out["rsi_14"] = 100 - (100 / (1 + rs))

    ema_12 = out["close"].ewm(span=12, adjust=False).mean()
    ema_26 = out["close"].ewm(span=26, adjust=False).mean()
    out["macd"] = ema_12 - ema_26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    return out



def build_technical_figure(df: pd.DataFrame, title: str) -> go.Figure:
    frame = add_indicators(df)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=["Price", "RSI (14)", "MACD"],
    )

    fig.add_trace(
        go.Candlestick(
            x=frame["timestamp"],
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name="OHLC",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(x=frame["timestamp"], y=frame["sma_20"], mode="lines", name="SMA 20"),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=frame["timestamp"], y=frame["sma_50"], mode="lines", name="SMA 50"),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(x=frame["timestamp"], y=frame["rsi_14"], mode="lines", name="RSI 14"),
        row=2,
        col=1,
    )
    fig.add_hline(y=70, row=2, col=1, line_dash="dash", line_color="red")
    fig.add_hline(y=30, row=2, col=1, line_dash="dash", line_color="green")

    fig.add_trace(
        go.Bar(x=frame["timestamp"], y=frame["macd_hist"], name="MACD Hist"),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=frame["timestamp"], y=frame["macd"], mode="lines", name="MACD"),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=frame["timestamp"], y=frame["macd_signal"], mode="lines", name="Signal"),
        row=3,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        title=title,
        xaxis_rangeslider_visible=False,
        height=950,
        hovermode="x unified",
    )
    return fig
