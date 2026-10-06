from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .signal_catalog import SignalSpec, signal_specs


RANKING_METHOD = "domain_balanced_all_signal_v1"
_RRF_K = 60.0
_MIN_PEER_SIZE = 5
_HORIZON_ALIASES = {
    "1d": {"1d"},
    "return_1d_pct": {"1d"},
    "1w": {"1w", "7d"},
    "return_7d_pct": {"1w", "7d"},
    "1m": {"1m", "1mo", "1month"},
    "return_1m_pct": {"1m", "1mo", "1month"},
    "3m": {"3m", "3mo", "3month"},
    "return_3m_pct": {"3m", "3mo", "3month"},
    "1y": {"1y", "1yr", "1year"},
    "return_1y_pct": {"1y", "1yr", "1year"},
    "5y": {"5y", "5yr", "5year"},
    "return_5y_pct": {"5y", "5yr", "5year"},
}

# These are the pre-decision, stock-linked datasets that can change a stock's
# cross-sectional rank. Global series remain research context until a declared
# stock exposure links them to a symbol.
RANKING_DATASET_NAMES: tuple[str, ...] = (
    "daily_movers",
    "universe_snapshot",
    "momentum_profiles",
    "technical_signals_latest",
    "correlation_phase_shift_summary",
    "price_expectations",
    "anomaly_events",
    "attention_candidates_1d",
    "attention_macro_context_1d",
    "commodity_regime_summary",
    "commodity_price_expectations",
    "commodity_attention_candidates",
    "option_contract_snapshots",
    "quarterly_fundamentals",
    "entity_taxonomy_labels",
    "positions_snapshot",
    "attention_ticker_background_snapshots",
)

_CATALOG_RANKING_ROLES = {"direct", "conditioning", "quality_gate"}
_CATALOG_GRAINS = {"symbol_snapshot", "symbol_horizon_snapshot", "symbol_horizon_event"}
_CUSTOM_DATASETS = {
    "option_contract_snapshots",
    "quarterly_fundamentals",
    "positions_snapshot",
    "attention_ticker_background_snapshots",
}
_FAMILY_DOMAINS = {
    "price_liquidity": "price_behavior",
    "momentum": "price_behavior",
    "trend_quality": "price_behavior",
    "technical": "price_behavior",
    "market_relationship": "relative_dislocation",
    "relative_expectation": "relative_dislocation",
    "attention": "event_evidence",
    "event_detection": "event_evidence",
    "evidence_quality": "event_evidence",
    "commodity_transmission": "commodity_exposure",
    "commodity_expectation": "commodity_exposure",
    "commodity_attention": "commodity_exposure",
    "macro_transmission": "macro_exposure",
    "fundamentals": "fundamentals",
    "options": "options",
    "portfolio": "portfolio",
}


@dataclass(frozen=True)
class _Feature:
    name: str
    family: str
    directionality: str
    dataset: str
    values: pd.Series


@dataclass(frozen=True)
class _PreparedFeatures:
    size: pd.Series
    peers: pd.Series
    features: tuple[_Feature, ...]


def _clean_symbols(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.upper().str.strip()


def _symbol_column(frame: pd.DataFrame) -> str:
    for column in ("symbol", "ticker", "entity_id"):
        if column in frame.columns:
            return column
    return ""


def _normalize_horizon(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _horizon_values(horizon_key: str, horizon_col: str) -> set[str]:
    values = set(_HORIZON_ALIASES.get(_normalize_horizon(horizon_key), set()))
    values.update(_HORIZON_ALIASES.get(_normalize_horizon(horizon_col), set()))
    values.update({_normalize_horizon(horizon_key), _normalize_horizon(horizon_col)})
    return {value for value in values if value}


def _scope_horizon(frame: pd.DataFrame, *, horizon_key: str, horizon_col: str) -> pd.DataFrame:
    if "horizon" not in frame.columns:
        return frame
    accepted = _horizon_values(horizon_key, horizon_col)
    values = frame["horizon"].map(_normalize_horizon)
    scoped = frame[values.isin(accepted)].copy()
    return scoped


def _latest_rows(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ("asof_time_utc", "timestamp", "report_date", "generated_at_utc"):
        if column not in out.columns:
            continue
        out["_rank_time"] = pd.to_datetime(out[column], utc=True, errors="coerce")
        out = out.sort_values("_rank_time", na_position="first")
        break
    return out


def _aggregate_signal(values: pd.Series, symbols: pd.Series, directionality: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    work = pd.DataFrame({"symbol": symbols, "value": numeric}).dropna(subset=["symbol", "value"])
    if work.empty:
        return pd.Series(dtype=float)
    grouped = work.groupby("symbol", sort=False)["value"]
    if directionality == "higher":
        return grouped.max()
    if directionality == "lower":
        return grouped.min()
    indices = grouped.apply(lambda chunk: (chunk - chunk.median()).abs().idxmax())
    return work.loc[indices.to_numpy()].set_index("symbol")["value"]


def _catalog_specs_by_dataset() -> dict[str, list[SignalSpec]]:
    grouped: dict[str, list[SignalSpec]] = {}
    for spec in signal_specs():
        if spec.source_dataset not in RANKING_DATASET_NAMES:
            continue
        if spec.source_dataset in _CUSTOM_DATASETS:
            continue
        if spec.ranking_role not in _CATALOG_RANKING_ROLES:
            continue
        if spec.grain not in _CATALOG_GRAINS:
            continue
        if spec.signal_kind not in {"numeric", "mixed"}:
            continue
        if spec.directionality not in {"two_sided", "higher", "lower"}:
            continue
        grouped.setdefault(spec.source_dataset, []).append(spec)
    return grouped


_SPECS_BY_DATASET = _catalog_specs_by_dataset()


def _catalog_features(
    dataset: str,
    frame: pd.DataFrame,
    *,
    symbols: pd.Index,
    horizon_key: str,
    horizon_col: str,
) -> list[_Feature]:
    specs = _SPECS_BY_DATASET.get(dataset, [])
    symbol_col = _symbol_column(frame)
    if not specs or not symbol_col or frame.empty:
        return []
    scoped = _scope_horizon(frame, horizon_key=horizon_key, horizon_col=horizon_col)
    if scoped.empty:
        return []
    scoped["_rank_symbol"] = _clean_symbols(scoped[symbol_col])
    scoped = scoped[scoped["_rank_symbol"].isin(symbols)]
    if scoped.empty:
        return []

    features: list[_Feature] = []
    for spec in specs:
        if spec.source_field not in scoped.columns:
            continue
        aggregated = _aggregate_signal(scoped[spec.source_field], scoped["_rank_symbol"], spec.directionality)
        values = aggregated.reindex(symbols)
        if values.notna().sum() < 2 or values.nunique(dropna=True) < 2:
            continue
        features.append(
            _Feature(
                name=spec.signal_id,
                family=spec.family,
                directionality=spec.directionality,
                dataset=dataset,
                values=values,
            )
        )
    return features


def _symmetric_change(current: pd.Series, prior: pd.Series) -> pd.Series:
    denominator = pd.concat([current.abs(), prior.abs()], axis=1).max(axis=1).replace(0.0, np.nan)
    return (current - prior) / denominator * 100.0


def _fundamental_features(frame: pd.DataFrame, symbols: pd.Index) -> list[_Feature]:
    required = {"ticker", "metric", "value", "report_date"}
    if frame.empty or not required.issubset(frame.columns):
        return []
    source = frame.copy()
    source["symbol"] = _clean_symbols(source["ticker"])
    source = source[source["symbol"].isin(symbols)]
    source["report_date"] = pd.to_datetime(source["report_date"], utc=True, errors="coerce")
    source["value"] = pd.to_numeric(source["value"], errors="coerce")
    source = source.dropna(subset=["report_date", "value"])
    if source.empty:
        return []

    features: list[_Feature] = []
    latest_metrics: dict[str, pd.Series] = {}
    for metric, metric_rows in source.groupby("metric", sort=False):
        metric_key = str(metric).lower().replace(" ", "_")
        ordered = metric_rows.sort_values("report_date").drop_duplicates(["symbol", "report_date"], keep="last")
        grouped = ordered.groupby("symbol", sort=False)["value"]
        latest = grouped.agg(lambda values: values.iloc[-1]).reindex(symbols)
        latest_metrics[str(metric)] = latest
        prior_quarter = grouped.agg(lambda values: values.iloc[-2] if len(values) >= 2 else np.nan).reindex(symbols)
        prior_year = grouped.agg(lambda values: values.iloc[-5] if len(values) >= 5 else np.nan).reindex(symbols)
        for suffix, values in (
            ("qoq_change", _symmetric_change(latest, prior_quarter)),
            ("yoy_change", _symmetric_change(latest, prior_year)),
        ):
            if values.notna().sum() < 2 or values.nunique(dropna=True) < 2:
                continue
            features.append(
                _Feature(
                    name=f"fundamentals.{metric_key}.{suffix}",
                    family="fundamentals",
                    directionality="two_sided",
                    dataset="quarterly_fundamentals",
                    values=values,
                )
            )

    ratio_specs = {
        "operating_margin": ("Operating Income", "Total Revenue"),
        "net_margin": ("Net Income", "Total Revenue"),
        "operating_cash_flow_margin": ("Operating Cash Flow", "Total Revenue"),
        "free_cash_flow_margin": ("Free Cash Flow", "Total Revenue"),
        "capital_expenditure_intensity": ("Capital Expenditure", "Total Revenue"),
        "liabilities_to_assets": ("Total Liabilities", "Total Assets"),
        "equity_to_assets": ("Stockholders Equity", "Total Assets"),
    }
    for name, (numerator_name, denominator_name) in ratio_specs.items():
        numerator = latest_metrics.get(numerator_name)
        denominator = latest_metrics.get(denominator_name)
        if numerator is None or denominator is None:
            continue
        values = _ratio(numerator, denominator)
        if values.notna().sum() < 2 or values.nunique(dropna=True) < 2:
            continue
        features.append(
            _Feature(
                name=f"fundamentals.{name}",
                family="fundamentals",
                directionality="two_sided",
                dataset="quarterly_fundamentals",
                values=values,
            )
        )
    return features


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def _option_features(frame: pd.DataFrame, symbols: pd.Index) -> list[_Feature]:
    symbol_col = _symbol_column(frame)
    if frame.empty or not symbol_col:
        return []
    source = frame.copy()
    source["symbol"] = _clean_symbols(source[symbol_col])
    source = source[source["symbol"].isin(symbols)]
    if source.empty:
        return []
    for column in (
        "dte", "lastPrice", "bid", "ask", "premium", "impliedVolatility", "delta",
        "gamma", "theta", "vega", "rho", "volume", "openInterest",
    ):
        source[column] = pd.to_numeric(source.get(column), errors="coerce")
    if "premium" not in frame.columns:
        valid_quote = source["bid"].gt(0) & source["ask"].gt(0)
        source["premium"] = ((source["bid"] + source["ask"]) / 2.0).where(valid_quote, source["lastPrice"])
    source["relative_spread"] = _ratio(source["ask"] - source["bid"], source["premium"].abs())
    source["abs_delta"] = source["delta"].abs()
    source["abs_theta"] = source["theta"].abs()

    grouped = source.groupby("symbol", sort=False)
    aggregates = pd.DataFrame(index=symbols)
    aggregates["contract_count"] = grouped.size().reindex(symbols)
    for column in ("dte", "premium", "impliedVolatility", "abs_delta", "gamma", "abs_theta", "vega", "rho", "relative_spread"):
        aggregates[f"median_{column}"] = grouped[column].median().reindex(symbols)
    for column in ("volume", "openInterest"):
        aggregates[f"total_{column}"] = grouped[column].sum(min_count=1).reindex(symbols)

    if "type" in source.columns:
        option_type = source["type"].fillna("").astype(str).str.lower()
        for column in ("volume", "openInterest"):
            calls = source[option_type.eq("call")].groupby("symbol")[column].sum(min_count=1).reindex(symbols)
            puts = source[option_type.eq("put")].groupby("symbol")[column].sum(min_count=1).reindex(symbols)
            aggregates[f"{column}_call_put_imbalance"] = _ratio(calls - puts, calls.abs() + puts.abs())

    features: list[_Feature] = []
    for column in aggregates.columns:
        values = aggregates[column]
        if values.notna().sum() < 2 or values.nunique(dropna=True) < 2:
            continue
        direction = "lower" if column == "median_relative_spread" else "two_sided"
        features.append(
            _Feature(
                name=f"options.{column}",
                family="options",
                directionality=direction,
                dataset="option_contract_snapshots",
                values=values,
            )
        )
    return features


def _portfolio_features(frame: pd.DataFrame, symbols: pd.Index) -> list[_Feature]:
    symbol_col = _symbol_column(frame)
    if frame.empty or not symbol_col:
        return []
    source = _latest_rows(frame)
    source["symbol"] = _clean_symbols(source[symbol_col])
    source = source[source["symbol"].isin(symbols)].drop_duplicates("symbol", keep="last")
    if source.empty:
        return []
    source = source.set_index("symbol")
    def numeric(column: str) -> pd.Series:
        if column not in source.columns:
            return pd.Series(np.nan, index=source.index, dtype=float)
        return pd.to_numeric(source[column], errors="coerce")

    market_value = numeric("market_value")
    total_value = market_value.abs().sum(min_count=1)
    derived = {
        "portfolio_exposure_weight": market_value.abs() / total_value if pd.notna(total_value) and total_value else market_value * np.nan,
        "unrealized_plpc": numeric("unrealized_plpc"),
        "change_today": numeric("change_today"),
    }
    features: list[_Feature] = []
    for name, series in derived.items():
        values = series.reindex(symbols)
        if values.notna().sum() < 1:
            continue
        features.append(_Feature(f"portfolio.{name}", "portfolio", "two_sided", "positions_snapshot", values))
    return features


def _background_size(frame: pd.DataFrame, symbols: pd.Index) -> pd.Series:
    symbol_col = _symbol_column(frame)
    if frame.empty or not symbol_col or "market_cap" not in frame.columns:
        return pd.Series(np.nan, index=symbols, dtype=float)
    source = _latest_rows(frame)
    source["symbol"] = _clean_symbols(source[symbol_col])
    source["market_cap"] = pd.to_numeric(source["market_cap"], errors="coerce")
    source = source[source["symbol"].isin(symbols)].drop_duplicates("symbol", keep="last")
    return np.log1p(source.set_index("symbol")["market_cap"].clip(lower=0)).reindex(symbols)


def _covered_symbols(frame: pd.DataFrame, symbols: pd.Index) -> set[str]:
    symbol_col = _symbol_column(frame)
    if frame.empty or not symbol_col:
        return set()
    return set(_clean_symbols(frame[symbol_col])).intersection(symbols)


def _taxonomy_peers(frame: pd.DataFrame, symbols: pd.Index, size: pd.Series) -> pd.Series:
    peers = pd.Series("all", index=symbols, dtype="object")
    symbol_col = _symbol_column(frame)
    if not frame.empty and symbol_col:
        source = frame.copy()
        source["symbol"] = _clean_symbols(source[symbol_col])
        source = source[source["symbol"].isin(symbols)]
        if "label_type" in source.columns and "label_value" in source.columns:
            label_type = source["label_type"].fillna("").astype(str).str.lower()
            source = source[label_type.isin({"industry", "sector"})]
            if not source.empty:
                source["_priority"] = label_type.map({"industry": 0, "sector": 1}).fillna(2)
                source = source.sort_values("_priority").drop_duplicates("symbol")
                peers.update(source.set_index("symbol")["label_value"].astype(str))
        else:
            source = source.drop_duplicates("symbol", keep="last").set_index("symbol")
            for column in ("industry", "sector"):
                if column in source.columns:
                    values = source[column].fillna("").astype(str).str.strip()
                    fallback = peers.eq("all")
                    peers.update(values[values.ne("") & fallback])

    valid_size = pd.to_numeric(size, errors="coerce")
    if valid_size.notna().sum() >= 20:
        size_bucket = pd.qcut(valid_size.rank(method="first"), q=min(5, valid_size.notna().sum()), labels=False, duplicates="drop")
        peers = peers.astype(str) + "|size" + size_bucket.fillna(-1).astype(int).astype(str)
    return peers


def _percentile(values: pd.Series, peers: pd.Series, directionality: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    valid = numeric.notna()
    if valid.sum() < 2:
        return result

    peer_counts = peers[valid].value_counts()
    eligible_peers = set(peer_counts[peer_counts.ge(_MIN_PEER_SIZE)].index)
    for peer, indices in peers[valid].groupby(peers[valid]).groups.items():
        if peer not in eligible_peers:
            continue
        chunk = numeric.loc[indices]
        if directionality == "two_sided":
            chunk = (chunk - chunk.median()).abs()
        result.loc[indices] = chunk.rank(pct=True, method="average", ascending=directionality != "lower")

    fallback = valid & result.isna()
    if fallback.any():
        chunk = numeric[valid]
        if directionality == "two_sided":
            chunk = (chunk - chunk.median()).abs()
        global_rank = chunk.rank(pct=True, method="average", ascending=directionality != "lower")
        result.loc[fallback] = global_rank.loc[fallback]
    return result


def _embedded_feed_features(frame: pd.DataFrame, symbols: pd.Index) -> list[_Feature]:
    source = frame.drop_duplicates("symbol", keep="first").set_index("symbol")
    mapping = {
        "daily_change_pct": ("price_liquidity", "two_sided"),
        "return_1w_pct": ("momentum", "two_sided"),
        "return_1m_pct": ("momentum", "two_sided"),
        "return_3m_pct": ("momentum", "two_sided"),
        "return_1y_pct": ("momentum", "two_sided"),
        "return_5y_pct": ("momentum", "two_sided"),
        "momentum_roc_score": ("momentum", "two_sided"),
        "trend_fit_gap": ("trend_quality", "lower"),
    }
    features: list[_Feature] = []
    for column, (family, direction) in mapping.items():
        if column not in source.columns:
            continue
        values = pd.to_numeric(source[column], errors="coerce").reindex(symbols)
        if values.notna().sum() < 2 or values.nunique(dropna=True) < 2:
            continue
        features.append(_Feature(f"market_feed.{column}", family, direction, "market_opportunity_feed", values))
    return features


def _rank_one_feed(
    frame: pd.DataFrame,
    signal_frames: Mapping[str, pd.DataFrame],
    *,
    horizon_key: str,
    horizon_col: str,
    prepared: _PreparedFeatures | None = None,
) -> pd.DataFrame:
    out = frame.copy()
    out["symbol"] = _clean_symbols(out["symbol"])
    symbols = pd.Index(out["symbol"].drop_duplicates(), name="symbol")
    if prepared is None:
        prepared = _prepare_horizon_independent_features(signal_frames, symbols)
    size = prepared.size
    peers = prepared.peers

    features = list(prepared.features)
    for dataset, specs in _SPECS_BY_DATASET.items():
        source = signal_frames.get(dataset, pd.DataFrame())
        if isinstance(source, pd.DataFrame) and not source.empty and "horizon" in source.columns:
            features.extend(
                _catalog_features(
                    dataset,
                    source,
                    symbols=symbols,
                    horizon_key=horizon_key,
                    horizon_col=horizon_col,
                )
            )
    present_families = {feature.family for feature in features}
    features.extend(
        feature
        for feature in _embedded_feed_features(out, symbols)
        if feature.family not in present_families
    )

    family_values: dict[str, list[pd.Series]] = {}
    family_signals: dict[str, list[str]] = {}
    family_datasets: dict[str, set[str]] = {}
    for feature in features:
        normalized = _percentile(feature.values, peers, feature.directionality)
        if feature.family == "portfolio" and normalized.notna().sum() == 0 and feature.values.notna().any():
            normalized.loc[feature.values.notna()] = 1.0
        if normalized.notna().sum() < 2:
            if feature.family != "portfolio":
                continue
        family_values.setdefault(feature.family, []).append(normalized)
        family_signals.setdefault(feature.family, []).append(feature.name)
        family_datasets.setdefault(feature.family, set()).add(feature.dataset)

    family_scores = pd.DataFrame(index=symbols)
    family_signal_counts = pd.DataFrame(index=symbols)
    for family, values in family_values.items():
        signal_matrix = pd.concat(values, axis=1)
        family_scores[family] = signal_matrix.mean(axis=1, skipna=True)
        family_signal_counts[family] = signal_matrix.notna().sum(axis=1)

    if family_scores.empty:
        return out

    domain_families: dict[str, list[pd.Series]] = {}
    for family in family_scores.columns:
        domain = _FAMILY_DOMAINS.get(family, family)
        domain_families.setdefault(domain, []).append(family_scores[family])
    domain_scores = pd.DataFrame(
        {
            domain: pd.concat(values, axis=1).mean(axis=1, skipna=True)
            for domain, values in domain_families.items()
        },
        index=symbols,
    )

    contributions = pd.DataFrame(index=symbols)
    neutral_rank = (len(symbols) + 1.0) / 2.0
    for domain in domain_scores.columns:
        domain_percentile = domain_scores[domain].rank(
            method="average",
            pct=True,
            ascending=True,
            na_option="keep",
        )
        comparable_rank = 1.0 + (1.0 - domain_percentile) * max(len(symbols) - 1, 0)
        contributions[domain] = 1.0 / (_RRF_K + comparable_rank.fillna(neutral_rank))
    maximum = len(contributions.columns) / (_RRF_K + 1.0)
    score = contributions.sum(axis=1, min_count=1) / maximum * 100.0
    family_count = family_scores.notna().sum(axis=1)
    domain_count = domain_scores.notna().sum(axis=1)
    signal_count = family_signal_counts.sum(axis=1, min_count=1)
    taxonomy_symbols = _covered_symbols(signal_frames.get("entity_taxonomy_labels", pd.DataFrame()), symbols)
    background_symbols = set(size[size.notna()].index)
    dataset_sets = {
        symbol: sorted(
            {
                dataset
                for family in family_scores.columns
                if pd.notna(family_scores.at[symbol, family])
                for dataset in family_datasets.get(family, set())
            }
            | ({"entity_taxonomy_labels"} if symbol in taxonomy_symbols else set())
            | ({"attention_ticker_background_snapshots"} if symbol in background_symbols else set())
        )
        for symbol in symbols
    }
    family_json = {
        symbol: json.dumps(
            {
                family: round(float(family_scores.at[symbol, family] * 100.0), 1)
                for family in family_scores.columns
                if pd.notna(family_scores.at[symbol, family])
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for symbol in symbols
    }
    domain_json = {
        symbol: json.dumps(
            {
                domain: round(float(domain_scores.at[symbol, domain] * 100.0), 1)
                for domain in domain_scores.columns
                if pd.notna(domain_scores.at[symbol, domain])
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for symbol in symbols
    }

    out["scanner_opportunity_score"] = pd.to_numeric(out.get("opportunity_score"), errors="coerce")
    out["all_signal_score"] = out["symbol"].map(score).round(1)
    out["opportunity_score"] = out["all_signal_score"]
    out["all_signal_family_count"] = out["symbol"].map(family_count).fillna(0).astype(int)
    out["all_signal_domain_count"] = out["symbol"].map(domain_count).fillna(0).astype(int)
    out["all_signal_signal_count"] = out["symbol"].map(signal_count).fillna(0).astype(int)
    out["all_signal_dataset_count"] = out["symbol"].map(lambda symbol: len(dataset_sets.get(symbol, []))).astype(int)
    out["all_signal_coverage_ratio"] = (out["all_signal_domain_count"] / len(domain_scores.columns)).round(4)
    out["all_signal_family_scores_json"] = out["symbol"].map(family_json).fillna("{}")
    out["all_signal_domain_scores_json"] = out["symbol"].map(domain_json).fillna("{}")
    out["all_signal_datasets_json"] = out["symbol"].map(
        lambda symbol: json.dumps(dataset_sets.get(symbol, []), separators=(",", ":"))
    )
    out["retrieval_method"] = RANKING_METHOD
    out["retrieval_signal_count"] = out["all_signal_signal_count"]

    horizon_values = pd.to_numeric(out.get(horizon_col), errors="coerce").abs()
    out["_horizon_sort"] = horizon_values
    out = out.sort_values(
        ["all_signal_score", "_horizon_sort", "symbol"],
        ascending=[False, False, True],
        na_position="last",
    ).drop(columns="_horizon_sort")
    out["all_signal_rank"] = range(1, len(out) + 1)
    if "rank" in out.columns:
        out["rank"] = out["all_signal_rank"]
    return out.reset_index(drop=True)


def _prepare_horizon_independent_features(
    signal_frames: Mapping[str, pd.DataFrame],
    symbols: pd.Index,
) -> _PreparedFeatures:
    size = _background_size(signal_frames.get("attention_ticker_background_snapshots", pd.DataFrame()), symbols)
    peers = _taxonomy_peers(signal_frames.get("entity_taxonomy_labels", pd.DataFrame()), symbols, size)
    features: list[_Feature] = []
    for dataset in _SPECS_BY_DATASET:
        source = signal_frames.get(dataset, pd.DataFrame())
        if not isinstance(source, pd.DataFrame) or source.empty or "horizon" in source.columns:
            continue
        features.extend(
            _catalog_features(
                dataset,
                source,
                symbols=symbols,
                horizon_key="",
                horizon_col="",
            )
        )
    features.extend(_fundamental_features(signal_frames.get("quarterly_fundamentals", pd.DataFrame()), symbols))
    features.extend(_option_features(signal_frames.get("option_contract_snapshots", pd.DataFrame()), symbols))
    features.extend(_portfolio_features(signal_frames.get("positions_snapshot", pd.DataFrame()), symbols))
    return _PreparedFeatures(size=size, peers=peers, features=tuple(features))


def rank_trading_agent_opportunities(
    opportunity_feed: pd.DataFrame,
    signal_frames: Mapping[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Rerank each market/horizon slice with every usable pre-decision signal."""
    if not isinstance(opportunity_feed, pd.DataFrame) or opportunity_feed.empty or "symbol" not in opportunity_feed.columns:
        return pd.DataFrame() if not isinstance(opportunity_feed, pd.DataFrame) else opportunity_feed.copy()
    frames = dict(signal_frames or {})
    source = opportunity_feed.copy()
    group_columns = [column for column in ("business_filter", "horizon_key", "selected_horizon_col") if column in source.columns]
    if not group_columns:
        source["_ranking_group"] = "all"
        group_columns = ["_ranking_group"]

    ranked: list[pd.DataFrame] = []
    prepared_cache: dict[tuple[str, ...], _PreparedFeatures] = {}
    for keys, chunk in source.groupby(group_columns, sort=False, dropna=False):
        key_values = keys if isinstance(keys, tuple) else (keys,)
        identity = dict(zip(group_columns, key_values))
        symbol_key = tuple(sorted(set(_clean_symbols(chunk["symbol"])) - {""}))
        if symbol_key not in prepared_cache:
            prepared_cache[symbol_key] = _prepare_horizon_independent_features(
                frames,
                pd.Index(symbol_key, name="symbol"),
            )
        ranked.append(
            _rank_one_feed(
                chunk,
                frames,
                horizon_key=str(identity.get("horizon_key") or identity.get("selected_horizon_col") or ""),
                horizon_col=str(identity.get("selected_horizon_col") or "return_1m_pct"),
                prepared=prepared_cache[symbol_key],
            )
        )
    out = pd.concat(ranked, ignore_index=True) if ranked else source
    return out.drop(columns=["_ranking_group"], errors="ignore")


__all__ = [
    "RANKING_DATASET_NAMES",
    "RANKING_METHOD",
    "rank_trading_agent_opportunities",
]
