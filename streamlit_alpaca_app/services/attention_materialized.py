from __future__ import annotations

import json
from typing import Any

import pandas as pd


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: object, *, default: Any) -> Any:
    text = _coerce_text(value)
    if not text:
        return default
    try:
        loaded = json.loads(text)
    except Exception:
        return default
    return loaded if isinstance(loaded, type(default)) else default


def serialize_attention_home_payload(payload: dict[str, Any]) -> pd.DataFrame:
    row = {
        "run_id": _coerce_text((payload or {}).get("run_id")),
        "generated_at_utc": _coerce_text((payload or {}).get("generated_at_utc")),
        "coverage_summary_json": _json_dumps((payload or {}).get("coverage_summary") or {}),
        "taxonomy_horizon_trends_json": _json_dumps((payload or {}).get("taxonomy_horizon_trends") or []),
        "top_events_json": _json_dumps((payload or {}).get("top_events") or []),
        "must_read_movers_json": _json_dumps((payload or {}).get("must_read_movers") or []),
        "unresolved_large_moves_json": _json_dumps((payload or {}).get("unresolved_large_moves") or []),
        "event_candidates_1d_json": _json_dumps((payload or {}).get("event_candidates_1d") or []),
        "event_impacts_1d_json": _json_dumps((payload or {}).get("event_impacts_1d") or []),
        "entity_master_json": _json_dumps((payload or {}).get("entity_master") or []),
        "homepage_graph_json": _json_dumps((payload or {}).get("homepage_graph") or {}),
        "homepage_summary_json": _json_dumps((payload or {}).get("homepage_summary") or {}),
    }
    return pd.DataFrame([row])


def deserialize_attention_home_payload(frame: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    row = frame.iloc[0]
    return {
        "run_id": _coerce_text(row.get("run_id")),
        "generated_at_utc": _coerce_text(row.get("generated_at_utc")),
        "coverage_summary": _json_loads(row.get("coverage_summary_json"), default={}),
        "taxonomy_horizon_trends": _json_loads(row.get("taxonomy_horizon_trends_json"), default=[]),
        "top_events": _json_loads(row.get("top_events_json"), default=[]),
        "must_read_movers": _json_loads(row.get("must_read_movers_json"), default=[]),
        "unresolved_large_moves": _json_loads(row.get("unresolved_large_moves_json"), default=[]),
        "event_candidates_1d": _json_loads(row.get("event_candidates_1d_json"), default=[]),
        "event_impacts_1d": _json_loads(row.get("event_impacts_1d_json"), default=[]),
        "entity_master": _json_loads(row.get("entity_master_json"), default=[]),
        "homepage_graph": _json_loads(row.get("homepage_graph_json"), default={}),
        "homepage_summary": _json_loads(row.get("homepage_summary_json"), default={}),
    }


def serialize_attention_research_bundles(
    bundles: dict[str, dict[str, Any]] | list[dict[str, Any]],
    *,
    generated_at_utc: object | None = None,
) -> pd.DataFrame:
    if isinstance(bundles, dict):
        items = list(bundles.values())
    else:
        items = list(bundles or [])
    rows: list[dict[str, Any]] = []
    generated_label = _coerce_text(generated_at_utc)
    for bundle in items:
        if not isinstance(bundle, dict):
            continue
        rows.append(
            {
                "bundle_id": _coerce_text(bundle.get("bundle_id")),
                "bundle_type": _coerce_text(bundle.get("bundle_type")),
                "run_id": _coerce_text(bundle.get("run_id")),
                "symbol": _coerce_text(bundle.get("symbol")),
                "event_title": _coerce_text(bundle.get("event_title")),
                "headline": _coerce_text(bundle.get("headline")),
                "cause_status": _coerce_text(bundle.get("cause_status")),
                "evidence_quality": _coerce_text(bundle.get("evidence_quality")),
                "freshness_quality": _coerce_text(bundle.get("freshness_quality")),
                "source_summary": _coerce_text(bundle.get("source_summary")),
                "generated_at_utc": generated_label,
                "payload_json": _json_dumps(bundle),
            }
        )
    return pd.DataFrame(rows)


def deserialize_attention_research_bundles(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        bundle_id = _coerce_text(row.get("bundle_id"))
        if not bundle_id:
            continue
        payload = _json_loads(row.get("payload_json"), default={})
        if isinstance(payload, dict) and payload:
            out[bundle_id] = payload
    return out


def deserialize_attention_research_bundle_frame(frame: pd.DataFrame, bundle_id: str) -> dict[str, Any]:
    normalized_bundle_id = _coerce_text(bundle_id)
    if not normalized_bundle_id or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    scoped = frame[frame.get("bundle_id", pd.Series(dtype=str)).astype(str) == normalized_bundle_id].head(1)
    if scoped.empty:
        return {}
    return _json_loads(scoped.iloc[0].get("payload_json"), default={})


def deserialize_attention_ticker_snapshot_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    from .attention_ticker_snapshots import deserialize_attention_ticker_snapshot_frame as _deserialize

    return _deserialize(frame, symbol)


def deserialize_attention_ticker_background_frame(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    from .attention_ticker_snapshots import deserialize_attention_ticker_background_frame as _deserialize

    return _deserialize(frame, symbol)


def flatten_search_payloads(
    search_payloads: dict[str, dict[str, Any]],
    *,
    asof_time_utc: object | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asof_label = _coerce_text(asof_time_utc)
    for symbol, payload in dict(search_payloads or {}).items():
        normalized_symbol = _coerce_text(symbol).upper()
        articles = (payload or {}).get("articles")
        fallback_summary = _coerce_text((payload or {}).get("fallback_summary"))
        source = _coerce_text((payload or {}).get("source"))
        if isinstance(articles, pd.DataFrame) and not articles.empty:
            for _, article in articles.iterrows():
                rows.append(
                    {
                        "symbol": normalized_symbol,
                        "row_type": "article",
                        "headline": _coerce_text(article.get("headline")),
                        "summary": _coerce_text(article.get("summary") or article.get("description")),
                        "source": _coerce_text(article.get("source")) or source,
                        "published_at": _coerce_text(pd.to_datetime(article.get("published_at"), utc=True, errors="coerce").isoformat() if pd.notna(pd.to_datetime(article.get("published_at"), utc=True, errors="coerce")) else ""),
                        "url": _coerce_text(article.get("url")),
                        "payload_source": source,
                        "fallback_summary": fallback_summary,
                        "asof_time_utc": asof_label,
                    }
                )
        elif fallback_summary:
            rows.append(
                {
                    "symbol": normalized_symbol,
                    "row_type": "fallback",
                    "headline": "",
                    "summary": "",
                    "source": source,
                    "published_at": "",
                    "url": "",
                    "payload_source": source,
                    "fallback_summary": fallback_summary,
                    "asof_time_utc": asof_label,
                }
            )
    return pd.DataFrame(rows)


def search_payloads_from_frame(frame: pd.DataFrame, symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    allowed = {str(symbol or "").upper().strip() for symbol in list(symbols or []) if str(symbol or "").strip()}
    scoped = frame.copy()
    scoped["symbol"] = scoped["symbol"].astype(str).str.upper().str.strip()
    if allowed:
        scoped = scoped[scoped["symbol"].isin(allowed)].copy()
    if scoped.empty:
        return {}
    for symbol, group in scoped.groupby("symbol", dropna=False):
        articles = group[group.get("row_type", pd.Series(dtype=str)).astype(str).ne("fallback")].copy()
        payload_source = _coerce_text(group["payload_source"].dropna().astype(str).head(1).tolist()[0] if "payload_source" in group.columns and not group["payload_source"].dropna().empty else "")
        fallback_summary = _coerce_text(group["fallback_summary"].dropna().astype(str).head(1).tolist()[0] if "fallback_summary" in group.columns and not group["fallback_summary"].dropna().empty else "")
        if not articles.empty:
            articles = articles[[col for col in ["headline", "summary", "source", "published_at", "url"] if col in articles.columns]].reset_index(drop=True)
        else:
            articles = pd.DataFrame(columns=["headline", "summary", "source", "published_at", "url"])
        out[str(symbol)] = {
            "articles": articles,
            "fallback_summary": fallback_summary or None,
            "source": payload_source or "pipeline",
        }
    return out


def bars_by_symbol_from_price_history(
    frame: pd.DataFrame,
    symbols: list[str],
    *,
    asof_time_utc: object | None = None,
    lookback_days: int = 120,
) -> dict[str, pd.DataFrame]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "symbol" not in frame.columns or "timestamp" not in frame.columns:
        return {}
    normalized_symbols = [str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()]
    if not normalized_symbols:
        return {}
    out = frame.copy()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out[out["symbol"].isin(set(normalized_symbols)) & out["timestamp"].notna()].copy()
    asof_ts = pd.to_datetime(asof_time_utc, utc=True, errors="coerce")
    if pd.notna(asof_ts):
        cutoff = asof_ts - pd.Timedelta(days=max(int(lookback_days), 1))
        out = out[(out["timestamp"] >= cutoff) & (out["timestamp"] <= asof_ts + pd.Timedelta(days=1))].copy()
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol, group in out.groupby("symbol", dropna=False):
        bars_by_symbol[str(symbol)] = group.sort_values("timestamp").reset_index(drop=True)
    return bars_by_symbol


__all__ = [
    "bars_by_symbol_from_price_history",
    "deserialize_attention_home_payload",
    "deserialize_attention_research_bundle_frame",
    "deserialize_attention_research_bundles",
    "deserialize_attention_ticker_background_frame",
    "deserialize_attention_ticker_snapshot_frame",
    "flatten_search_payloads",
    "search_payloads_from_frame",
    "serialize_attention_home_payload",
    "serialize_attention_research_bundles",
]
