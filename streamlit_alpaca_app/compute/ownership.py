from __future__ import annotations

import pandas as pd


ACCOUNT_SCALE_FIELDS = {
    "equity",
    "cash",
    "portfolio_value",
    "market_value",
    "long_market_value",
    "short_market_value",
    "last_equity",
    "last_maintenance_margin",
    "last_initial_margin",
}

POSITION_SCALE_FIELDS = {
    "qty",
    "market_value",
    "unrealized_pl",
    "cost_basis",
}

TIMESERIES_SCALE_FIELDS = {
    "portfolio",
}


def normalize_share_fraction(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0.0), 1.0))
    except Exception:
        return 0.0


def _scale_numeric(value: object, share_fraction: float) -> object:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return value
    return float(numeric) * share_fraction


def project_account_view(account: dict[str, object], share_fraction: object) -> dict[str, object]:
    if not isinstance(account, dict):
        return {}
    share = normalize_share_fraction(share_fraction)
    out = dict(account)
    for key in ACCOUNT_SCALE_FIELDS:
        if key in out:
            out[key] = _scale_numeric(out.get(key), share)
    out["viewer_share_fraction"] = share
    out["view_mode"] = "ownership_share"
    return out


def project_positions_view(frame: pd.DataFrame, share_fraction: object) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=getattr(frame, "columns", []))
    share = normalize_share_fraction(share_fraction)
    out = frame.copy()
    for column in POSITION_SCALE_FIELDS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce") * share
    if "qty" in out.columns:
        out = out.rename(columns={"qty": "effective_qty"})
    out["viewer_share_fraction"] = share
    return out


def project_portfolio_timeseries(frame: pd.DataFrame, share_fraction: object) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=getattr(frame, "columns", []))
    share = normalize_share_fraction(share_fraction)
    out = frame.copy()
    for column in TIMESERIES_SCALE_FIELDS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce") * share
    return out


__all__ = [
    "normalize_share_fraction",
    "project_account_view",
    "project_portfolio_timeseries",
    "project_positions_view",
]
