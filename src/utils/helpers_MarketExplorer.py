# ...existing imports...
from typing import Optional, Tuple, List
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date as _date


# --- Helpers for stationarized plots and transition heatmap ---

def stationarize_closes(closes: pd.Series, method: str = "log_return", zscore_window: Optional[int] = None) -> pd.Series:
    closes = pd.Series(closes).astype(float)
    if method == "log_return":
        s = 100.0 * np.log(closes).diff()
    elif method == "pct_change":
        s = closes.pct_change() * 100.0
    elif method == "diff":
        s = closes.diff()
    else:
        raise ValueError(f"Unknown method: {method}")
    if zscore_window and zscore_window > 1:
        mu = s.rolling(zscore_window).mean()
        sd = s.rolling(zscore_window).std(ddof=0)
        s = (s - mu) / sd
    return s.dropna()

def plot_stationary_split(
    closes: pd.Series,
    method: str = "log_return",
    zscore_window: Optional[int] = None,
    threshold_pct: float = 1.5,
    use_abs: bool = True,
    separate: bool = False
) -> Tuple[go.Figure, Optional[go.Figure]]:
    s = stationarize_closes(closes, method=method, zscore_window=zscore_window)
    idx = s.index
    y = s.values
    if use_abs:
        low_mask = np.abs(y) < threshold_pct
        high_mask = np.abs(y) >= threshold_pct
        suffix = f"(abs < {threshold_pct}%)", f"(abs ≥ {threshold_pct}%)"
    else:
        low_mask = y < threshold_pct
        high_mask = y >= threshold_pct
        suffix = (f"(< {threshold_pct}%)", f"(≥ {threshold_pct}%)")

    def make_fig(mask, title_suffix):
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=idx, y=np.where(mask, y, np.nan), mode="lines",
                                 name=f"Stationary {title_suffix}"))
        fig.add_shape(type="line", x0=idx.min(), x1=idx.max(), y0=0, y1=0,
                      line=dict(color="#999", width=1, dash="dot"))
        y_title = {"log_return": "Log return (%, 1d)", "pct_change": "Percent change (%, 1d)", "diff": "First difference"}[method]
        if zscore_window and zscore_window > 1:
            y_title += f" (z-score {zscore_window})"
        fig.update_layout(
            title=f"Stationarized Series {title_suffix}",
            xaxis_title="Date",
            yaxis_title=y_title,
            legend=dict(orientation="h")
        )
        return fig

    if separate:
        return make_fig(low_mask, suffix[0]), make_fig(high_mask, suffix[1])

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=np.where(low_mask, y, np.nan), mode="lines",
                             name=f"Low {suffix[0]}", line=dict(color="#1f77b4")))
    fig.add_trace(go.Scatter(x=idx, y=np.where(high_mask, y, np.nan), mode="lines",
                             name=f"High {suffix[1]}", line=dict(color="#d62728", width=2)))
    fig.add_shape(type="line", x0=idx.min(), x1=idx.max(), y0=0, y1=0,
                  line=dict(color="#999", width=1, dash="dot"))
    y_title = {"log_return": "Log return (%, 1d)", "pct_change": "Percent change (%, 1d)", "diff": "First difference"}[method]
    if zscore_window and zscore_window > 1:
        y_title += f" (z-score {zscore_window})"
    fig.update_layout(
        title="Stationarized Series — Highlighted by Threshold",
        xaxis_title="Date",
        yaxis_title=y_title,
        legend=dict(orientation="h")
    )
    return fig, None

def make_symmetric_bins(thresholds: List[float]) -> Tuple[np.ndarray, List[str], np.ndarray]:
    th = np.array(sorted(set(abs(t) for t in thresholds)))
    edges = np.concatenate(([-np.inf], -th[::-1], [0.0], th, [np.inf]))
    labels: List[str] = []
    centers: List[float] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if np.isneginf(lo):
            labels.append(f"≤{-th[-1]:.1f}%"); centers.append(-th[-1] * 1.5)
        elif np.isposinf(hi):
            labels.append(f"≥{th[-1]:.1f}%"); centers.append(th[-1] * 1.5)
        elif hi == 0.0:
            labels.append(f"({lo:.1f}%, 0%]"); centers.append((lo + 0.0) / 2)
        elif lo == 0.0:
            labels.append(f"(0%, {hi:.1f}%]"); centers.append((0.0 + hi) / 2)
        else:
            labels.append(f"({lo:.1f}%, {hi:.1f}%]"); centers.append((lo + hi) / 2)
    return edges, labels, np.array(centers, dtype=float)

def bucketize(series: pd.Series, edges: np.ndarray, labels: List[str]) -> Tuple[np.ndarray, List[str]]:
    cats = pd.cut(series.values, bins=edges, labels=labels, right=True, include_lowest=True)
    return cats.astype("category").codes, labels

def transition_counts(codes: np.ndarray, n_bins: int, lag: int = 1) -> np.ndarray:
    mat = np.zeros((n_bins, n_bins), dtype=int)
    for i in range(len(codes) - lag):
        a, b = codes[i], codes[i + lag]
        if a >= 0 and b >= 0:
            mat[a, b] += 1
    return mat

def plot_transition_heatmap(labels: List[str], counts: np.ndarray, normalize: Optional[str] = None) -> go.Figure:
    z = counts.astype(float)
    if normalize == "row":
        row_sums = z.sum(axis=1, keepdims=True)
        z = np.divide(z, np.where(row_sums == 0, 1, row_sums))
        title = "Transition Probabilities (row-normalized)"
        zmin, zmax, colorscale = 0, 1, "Blues"
    else:
        title = "Transition Counts"
        zmin, zmax, colorscale = None, None, "Viridis"
    fig = go.Figure(go.Heatmap(z=z, x=labels, y=labels, colorscale=colorscale, zmin=zmin, zmax=zmax, hoverongaps=False))
    fig.update_layout(title=title, xaxis_title="Next bucket", yaxis_title="Current bucket", height=600)
    return fig
# ...existing code...