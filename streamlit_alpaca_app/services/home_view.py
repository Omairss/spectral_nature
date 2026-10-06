from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
from typing import Any

from data_access.contracts import DataProvenance
from data_access.query_service import QueryService
from presentation.visualization_artifacts import build_plotly_figure_visualization_artifact
from services.aql.summarizer import build_attention_home_narrative_beats


def _clean(value: object, *, limit: int = 4000) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text[:limit]


def _count(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


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
) -> tuple[dict[str, Any], DataProvenance, bool]:
    if not target_date:
        resolved = service.data_access.resolve_attention_home_1d(force_refresh=False)
        payload = dict(resolved.payload) if isinstance(resolved.payload, dict) else {}
        return payload, resolved.provenance, False

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
    return payload, provenance, True


def build_public_home_view(
    service: QueryService | None = None,
    *,
    target_date: str | None = None,
) -> dict[str, Any]:
    """Build the public Home DTO from the materialized attention snapshot only."""
    resolved_service = service or QueryService.from_environment()
    replay_date = _validated_target_date(target_date)
    payload, resolved_provenance, is_replay = _home_payload(resolved_service, target_date=replay_date)
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

    beats: list[dict[str, Any]] = []
    for item in build_attention_home_narrative_beats(payload)[:16]:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("sentence"), limit=400)
        body = _clean(item.get("summary"), limit=1600)
        if not title and not body:
            continue
        beats.append(
            {
                "id": _clean(item.get("bundle_id"), limit=160),
                "kind": _clean(item.get("kind"), limit=40),
                "title": title,
                "summary": body,
                "business_context": _clean(item.get("business_context"), limit=1200),
                "symbols": [
                    _clean(symbol, limit=20).upper()
                    for symbol in list(item.get("symbols") or [])[:12]
                    if _clean(symbol, limit=20)
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
    payload, _, _ = _home_payload(resolved_service, target_date=replay_date)
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
