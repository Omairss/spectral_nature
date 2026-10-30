from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Any

from data_access.contracts import DataProvenance
from data_access.query_service import QueryService
from presentation.visualization_artifacts import build_plotly_figure_visualization_artifact
from services.aql.summarizer import build_attention_home_narrative_beats


def _clean(value: object, *, limit: int | None = 4000) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text[:limit]


def _count(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _ticker_snapshot_view(symbol: str, raw: object, *, attention_change_pct: object = None) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, dict) else {}
    sparkline = _clean(payload.get("sparkline_data_uri"), limit=20_000)
    if sparkline and not sparkline.startswith("data:image/svg+xml;base64,"):
        sparkline = ""
    move = _number(payload.get("price_change_1d_pct"))
    if move is None:
        move = _number(attention_change_pct)
    return {
        "symbol": _clean(symbol, limit=20).upper(),
        "company_name": _clean(payload.get("company_name"), limit=160),
        "latest_close": _number(payload.get("latest_close")),
        "price_change_1d_pct": move,
        "price_change_1m_pct": _number(payload.get("price_change_1m_pct")),
        "market_cap": _number(payload.get("market_cap")),
        "market_cap_label": _clean(payload.get("market_cap_label"), limit=40),
        "revenue": _number(payload.get("revenue")),
        "revenue_yoy_pct": _number(payload.get("revenue_yoy_pct")),
        "operating_income": _number(payload.get("operating_income")),
        "net_income": _number(payload.get("net_income")),
        "net_income_yoy_pct": _number(payload.get("net_income_yoy_pct")),
        "operating_cash_flow": _number(payload.get("operating_cash_flow")),
        "free_cash_flow": _number(payload.get("free_cash_flow")),
        "total_assets": _number(payload.get("total_assets")),
        "stockholders_equity": _number(payload.get("stockholders_equity")),
        "fundamentals_period": _clean(payload.get("fundamentals_period"), limit=20),
        "fundamentals_report_date": _clean(payload.get("fundamentals_report_date"), limit=20),
        "sparkline_data_uri": sparkline,
        "price_trend": _trend_points(payload.get("price_trend_json"), limit=1300),
        "intraday_price_history": _intraday_price_history(payload.get("intraday_price_history_json")),
        "financial_trends": _financial_trends(payload.get("financial_trends_json")),
    }


def _json_value(raw: object, default: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError):
        return default


def _trend_points(raw: object, *, limit: int = 1200) -> list[dict[str, Any]]:
    points = []
    for point in _json_value(raw, [])[-limit:]:
        if not isinstance(point, dict):
            continue
        value = _number(point.get("value"))
        try:
            observed = date.fromisoformat(str(point.get("date") or "")[:10]).isoformat()
        except ValueError:
            continue
        if value is not None:
            points.append({"date": observed, "value": value, "period": _clean(point.get("period"), limit=20)})
    return sorted(points, key=lambda point: point["date"])


def _intraday_price_history(raw: object) -> dict[str, Any]:
    source = _json_value(raw, {})
    points = []
    for point in list(source.get("points") or [])[-100:]:
        if not isinstance(point, dict):
            continue
        value = _number(point.get("value"))
        try:
            observed = datetime.fromisoformat(str(point.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if value is not None and observed.tzinfo is not None:
            points.append({"date": observed.astimezone(timezone.utc).isoformat(), "value": value})
    points.sort(key=lambda point: point["date"])
    return {
        "status": "available" if points else "unavailable", "points": points,
        "interval": _clean(source.get("interval"), limit=20),
        "feed": _clean(source.get("feed"), limit=20),
        "session": _clean(source.get("session"), limit=20),
        "as_of": points[-1]["date"] if points else "",
        "prefetched_at": _clean(source.get("prefetched_at"), limit=80),
        "change_pct": _number(source.get("change_pct")),
    }


def _financial_trends(raw: object) -> dict[str, list[dict[str, Any]]]:
    source = _json_value(raw, {})
    return {metric: _trend_points(source.get(metric), limit=20) for metric in ("revenue", "net_income", "free_cash_flow", "operating_cash_flow", "total_assets", "stockholders_equity")}


def _daily_datapoints_view(raw: object, home_run_id: str = "") -> list[dict[str, Any]]:
    rows = _json_value(raw, [])
    result = []
    for row in sorted([item for item in rows if isinstance(item, dict)], key=lambda item: _count(item.get("rank"))):
        if (home_run_id and _clean(row.get("home_run_id")) != home_run_id) or row.get("selection_method") != "aql_zopedia":
            continue
        points = _trend_points(row.get("points_json"))
        title = _clean(row.get("title"), limit=100)
        if not points or not title:
            continue
        result.append({
            "id": _clean(row.get("id"), limit=160), "title": title,
            "selected_at": _clean(row.get("asof_time_utc"), limit=80),
            "significance": _clean(row.get("significance"), limit=260),
            "label": _clean(row.get("label"), limit=160), "kind": _clean(row.get("kind"), limit=20),
            "symbol": _clean(row.get("symbol"), limit=20), "series_id": _clean(row.get("series_id"), limit=40),
            "unit": _clean(row.get("unit"), limit=80), "date": points[-1]["date"],
            "value": points[-1]["value"], "period": _clean(row.get("period"), limit=20), "points": points,
        })
    return list({item["id"]: item for item in result if item["id"]}.values())[:5]


def _validated_target_date(value: str | None) -> str:
    text = _clean(value, limit=20)
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Replay date must use YYYY-MM-DD.") from exc
    if parsed > datetime.now(timezone.utc).date():
        raise ValueError("Replay date cannot be in the future.")
    return parsed.isoformat()


def _home_payload(
    service: QueryService,
    *,
    target_date: str = "",
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], DataProvenance, bool]:
    if not target_date:
        resolved = service.data_access.resolve_attention_home_1d(force_refresh=False)
        payload = dict(resolved.payload) if isinstance(resolved.payload, dict) else {}
        embedded = payload.get("ticker_snapshots")
        ticker_snapshots = dict(embedded) if isinstance(embedded, dict) else {}
        return payload, ticker_snapshots, resolved.provenance, False

    from data_access.layer import resolve_homepage_asof

    replay = resolve_homepage_asof(target_date)
    raw_payload = replay.get("home_payload")
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    metadata = dict(replay.get("metadata") or {})
    datasets = tuple(name for name, item in metadata.items() if item is not None)
    provenance = DataProvenance(
        mode="historical",
        datasets=datasets or ("attention_home_1d",),
        details={"target_date": target_date},
    )
    ticker_snapshots = replay.get("ticker_snapshots")
    return payload, dict(ticker_snapshots) if isinstance(ticker_snapshots, dict) else {}, provenance, True


def build_public_home_view(
    service: QueryService | None = None,
    *,
    target_date: str | None = None,
) -> dict[str, Any]:
    """Build the public Home DTO from the materialized attention snapshot only."""
    resolved_service = service or QueryService.from_environment()
    replay_date = _validated_target_date(target_date)
    payload, ticker_snapshots, resolved_provenance, is_replay = _home_payload(resolved_service, target_date=replay_date)
    provenance = resolved_provenance.to_dict()
    if not payload:
        return {
            "status": "unavailable",
            "view_date": replay_date,
            "is_replay": is_replay,
            "as_of": "",
            "run_id": "",
            "summary": None,
            "graph": None,
            "beats": [],
            "coverage": {},
            "daily_datapoints": [],
            "provenance": {
                "mode": _clean(provenance.get("mode"), limit=40),
                "datasets": [_clean(item, limit=120) for item in list(provenance.get("datasets") or [])[:8]],
            },
        }

    raw_summary = payload.get("homepage_summary")
    summary_payload = dict(raw_summary) if isinstance(raw_summary, dict) else {}
    summary_text = _clean(
        summary_payload.get("summary_text") or summary_payload.get("answer_markdown"),
        limit=12_000,
    )
    summary = None
    if summary_text:
        audio_text = _clean(summary_payload.get("audio_text") or summary_text, limit=40_000)
        encoded_audio = _clean(summary_payload.get("audio_base64"), limit=40_000_000)
        stored_hash = _clean(summary_payload.get("audio_text_hash"), limit=128)
        current_hash = hashlib.sha256(audio_text.encode("utf-8")).hexdigest() if audio_text else ""
        summary = {
            "headline": _clean(summary_payload.get("headline"), limit=160) or "Market Summary",
            "markdown": summary_text,
            "status": _clean(summary_payload.get("summary_status"), limit=40) or "ok",
            "featured_symbols": [
                _clean(item, limit=20).upper()
                for item in list(summary_payload.get("featured_symbols") or [])[:16]
                if _clean(item, limit=20)
            ],
            "audio_available": bool(encoded_audio and (not stored_hash or stored_hash == current_hash)),
            "audio_mime_type": _clean(summary_payload.get("audio_mime_type"), limit=80) or "audio/mpeg",
        }

    narrative_beats = [item for item in build_attention_home_narrative_beats(payload) if isinstance(item, dict)]
    if not is_replay:
        requested_symbols = list(
            dict.fromkeys(
                _clean(symbol, limit=20).upper()
                for item in narrative_beats
                for symbol in list(item.get("symbols") or [])
                if _clean(symbol, limit=20)
            )
        )
        if requested_symbols:
            resolved_snapshots = resolved_service.data_access.resolve_attention_ticker_snapshots(requested_symbols)
            independent = dict(resolved_snapshots.payload) if isinstance(resolved_snapshots.payload, dict) else {}
            for symbol, candidate in independent.items():
                embedded = ticker_snapshots.get(symbol) or {}
                if str(candidate.get("asof_time_utc") or "") >= str(embedded.get("asof_time_utc") or ""):
                    ticker_snapshots[symbol] = candidate

    daily_datapoints = payload.get("daily_datapoints") or []
    if not is_replay:
        resolved_datapoints = resolved_service.data_access.resolve_daily_market_datapoints()
        current_points = _daily_datapoints_view(resolved_datapoints.payload)
    else:
        current_points = []
    embedded_points = _daily_datapoints_view(daily_datapoints, str(payload.get("run_id") or ""))
    daily_datapoints = max((current_points, embedded_points),
                           key=lambda items: max((item["selected_at"] for item in items), default=""))

    beats: list[dict[str, Any]] = []
    for item in narrative_beats:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("sentence"), limit=400)
        body = _clean(item.get("summary"), limit=None)
        if not title and not body:
            continue
        symbols = [
            _clean(symbol, limit=20).upper()
            for symbol in list(item.get("symbols") or [])[:12]
            if _clean(symbol, limit=20)
        ]
        primary_symbol = _clean(item.get("primary_symbol"), limit=20).upper() or (symbols[0] if symbols else "")
        ordered_symbols = list(dict.fromkeys(([primary_symbol] if primary_symbol else []) + symbols))
        attention_change_pct = item.get("change_pct")
        beats.append(
            {
                "id": _clean(item.get("bundle_id"), limit=160),
                "kind": _clean(item.get("kind"), limit=40),
                "title": title,
                "summary": body,
                "what_changed": _clean(item.get("what_changed"), limit=None),
                "why": _clean(item.get("why"), limit=None),
                "business_context": _clean(item.get("business_context"), limit=None),
                "watch_next": _clean(item.get("watch_next"), limit=None),
                "symbols": ordered_symbols,
                "primary_symbol": primary_symbol,
                "change_pct": _number(attention_change_pct),
                "tickers": [
                    _ticker_snapshot_view(
                        symbol,
                        ticker_snapshots.get(symbol),
                        attention_change_pct=attention_change_pct if symbol == primary_symbol else None,
                    )
                    for symbol in ordered_symbols[:4]
                ],
            }
        )

    coverage = dict(payload.get("coverage_summary") or {})
    graph = None
    raw_graph = payload.get("homepage_graph")
    graph_payload = dict(raw_graph) if isinstance(raw_graph, dict) else {}
    graph_figure = graph_payload.get("figure")
    if isinstance(graph_figure, dict) and graph_figure:
        try:
            graph = build_plotly_figure_visualization_artifact(
                graph_figure,
                title="Market attention network",
                provenance=resolved_provenance,
                linked_selection_group="home-attention",
            ).to_dict()
        except (TypeError, ValueError):
            graph = None
    return {
        "status": "available",
        "view_date": replay_date,
        "is_replay": is_replay,
        "as_of": _clean(payload.get("generated_at_utc"), limit=80),
        "run_id": _clean(payload.get("run_id"), limit=160),
        "summary": summary,
        "graph": graph,
        "beats": beats,
        "daily_datapoints": daily_datapoints,
        "daily_datapoints_as_of": max((item["selected_at"] for item in daily_datapoints), default=""),
        "coverage": {
            "candidate_count": _count(coverage.get("candidate_count")),
            "event_count": _count(coverage.get("event_count")),
            "must_read_count": _count(coverage.get("must_read_count")),
            "unresolved_count": _count(coverage.get("unresolved_count")),
            "news_backed_count": _count(coverage.get("news_backed_count")),
        },
        "provenance": {
            "mode": _clean(provenance.get("mode"), limit=40),
            "datasets": [_clean(item, limit=120) for item in list(provenance.get("datasets") or [])[:8]],
        },
    }


def build_public_home_audio(
    service: QueryService | None = None,
    *,
    target_date: str | None = None,
) -> tuple[bytes, str] | None:
    """Return retained summary audio without placing base64 media in the Home DTO."""
    resolved_service = service or QueryService.from_environment()
    replay_date = _validated_target_date(target_date)
    payload, _, _, _ = _home_payload(resolved_service, target_date=replay_date)
    summary = payload.get("homepage_summary")
    summary_payload = dict(summary) if isinstance(summary, dict) else {}
    encoded = _clean(summary_payload.get("audio_base64"), limit=40_000_000)
    if not encoded:
        return None
    audio_text = _clean(
        summary_payload.get("audio_text")
        or summary_payload.get("summary_text")
        or summary_payload.get("answer_markdown"),
        limit=40_000,
    )
    stored_hash = _clean(summary_payload.get("audio_text_hash"), limit=128)
    if stored_hash and hashlib.sha256(audio_text.encode("utf-8")).hexdigest() != stored_hash:
        return None
    try:
        audio = base64.b64decode(encoded, validate=True)
    except Exception:
        return None
    if not audio or len(audio) > 20_000_000:
        return None
    mime_type = _clean(summary_payload.get("audio_mime_type"), limit=80) or "audio/mpeg"
    if mime_type not in {"audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav"}:
        mime_type = "audio/mpeg"
    return audio, mime_type


__all__ = ["build_public_home_audio", "build_public_home_view"]
