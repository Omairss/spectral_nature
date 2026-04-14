from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from ..attention_surface import attention_home_bundle_preview, attention_home_surface_summary
from ..elevenlabs_tts import (
    ElevenLabsTTSClient,
    ElevenLabsTTSConfig,
    audio_file_extension,
    audio_mime_type,
    load_elevenlabs_tts_config,
)
from ..page_browsing import browse_page
from .collector import _plan_summary_research, _search_query_results
from .config import _load_search_clients
from .constants import LLMClient
from .evidence_index import annotate_source_documents
from .extractor import (
    _chunk_source_documents,
    _documents_from_search_results,
    _fallback_claims_from_chunks,
    _serialize_claims_frame,
)


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_text(text: object) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return ""
    clean = clean.replace("…", "...")
    clean = re.sub(r"\.\.\.+", "...", clean)
    return clean


def _looks_fragmentary_text(text: object) -> bool:
    clean = _normalize_text(text)
    if not clean:
        return True
    return "..." in clean


def _split_sentences(text: object) -> list[str]:
    clean = _normalize_text(text)
    if not clean:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]


def _narration_ready_text(text: object, *, max_sentences: int | None = None) -> str:
    sentences: list[str] = []
    for raw_sentence in _split_sentences(text):
        if _looks_fragmentary_text(raw_sentence):
            continue
        cleaned = re.sub(r"\.{2,}", ".", raw_sentence).strip()
        if not cleaned:
            continue
        sentences.append(_ensure_sentence(cleaned.rstrip(".!?")))
        if max_sentences is not None and len(sentences) >= max(int(max_sentences), 1):
            break
    if sentences:
        return " ".join(sentences)

    fallback = _normalize_text(text)
    if not fallback or _looks_fragmentary_text(fallback):
        return ""
    return _ensure_sentence(fallback.rstrip(".!?"))


def _trim_text(text: object, *, limit: int = 1400) -> str:
    clean = _narration_ready_text(text)
    if len(clean) <= limit:
        return clean

    kept: list[str] = []
    for sentence in _split_sentences(clean):
        candidate = " ".join(kept + [sentence]).strip()
        if len(candidate) > limit:
            break
        kept.append(sentence)
    if kept:
        return " ".join(kept)

    clipped = clean[: max(int(limit), 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    if not clipped:
        clipped = clean[: max(int(limit), 1)].rstrip(" ,;:-")
    return _ensure_sentence(clipped.rstrip(".!?"))


def _ensure_sentence(text: object) -> str:
    clean = _coerce_text(text)
    if not clean:
        return ""
    if clean[-1] in ".!?":
        return clean
    return f"{clean}."


def _sentence_fragment(text: object) -> str:
    clean = _narration_ready_text(text, max_sentences=1)
    if not clean:
        return ""
    return clean.rstrip(".!?").strip()


def _join_human(items: list[str]) -> str:
    parts = [item for item in items if _coerce_text(item)]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _unique_symbols(beats: list[dict[str, object]], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for beat in beats:
        for raw_symbol in list(beat.get("symbols") or []):
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            out.append(symbol)
            if len(out) >= max(int(limit), 1):
                return out
    return out


def attention_mover_card_title(mover: dict[str, object]) -> str:
    headline = _coerce_text((mover or {}).get("headline"))
    if headline:
        return headline
    symbol = _coerce_text((mover or {}).get("symbol")).upper()
    if symbol:
        return symbol
    return "Mover"


def build_attention_home_narrative_beats(home_payload: dict[str, object]) -> list[dict[str, object]]:
    top_events = list(home_payload.get("top_events") or [])
    must_read = list(home_payload.get("must_read_movers") or [])
    unresolved = list(home_payload.get("unresolved_large_moves") or [])

    beats: list[dict[str, object]] = []
    for event in top_events:
        preview = attention_home_bundle_preview(event, bundle={})
        summary_text = attention_home_surface_summary(preview, is_event=True)
        beats.append(
            {
                "bundle_id": _coerce_text(event.get("bundle_id")),
                "sentence": _coerce_text(event.get("event_title")),
                "summary": summary_text,
                "symbols": [
                    str(item).upper().strip()
                    for item in list(event.get("supporting_symbols") or [])
                    if str(item).strip()
                ],
                "kind": "event",
            }
        )
    for mover in must_read:
        preview = attention_home_bundle_preview(mover, bundle={})
        summary_text = attention_home_surface_summary(preview, is_event=False)
        beats.append(
            {
                "bundle_id": _coerce_text(mover.get("bundle_id")),
                "sentence": attention_mover_card_title(mover),
                "summary": summary_text,
                "symbols": [str(mover.get("symbol") or "").upper().strip()],
                "kind": "mover",
            }
        )
    for mover in unresolved:
        preview = attention_home_bundle_preview(mover, bundle={})
        summary_text = attention_home_surface_summary(preview, is_event=False)
        beats.append(
            {
                "bundle_id": _coerce_text(mover.get("bundle_id")),
                "sentence": attention_mover_card_title(mover),
                "summary": summary_text or "Large move with insufficient retained evidence so far.",
                "symbols": [str(mover.get("symbol") or "").upper().strip()],
                "kind": "unresolved",
            }
        )
    return beats


def _beat_highlight(beat: dict[str, object]) -> str:
    sentence = _sentence_fragment(beat.get("sentence"))
    summary = _narration_ready_text(beat.get("summary"), max_sentences=2)

    if sentence and sentence.upper() == sentence and len(sentence) <= 8:
        sentence = ""

    if sentence and summary and sentence.lower() not in summary.lower():
        return _ensure_sentence(f"{sentence}: {summary}")
    if summary:
        return _ensure_sentence(summary)
    return _ensure_sentence(sentence)


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural or f'{singular}s'}"


def _build_section_lines(title: str, items: list[str]) -> list[str]:
    clean_items = [_trim_text(_ensure_sentence(item), limit=220) for item in items if _coerce_text(item)]
    if not clean_items:
        return []
    lines = [f"**{title}**"]
    lines.extend(f"- {item}" for item in clean_items)
    return lines


def build_attention_home_summary(
    home_payload: dict[str, object],
    *,
    max_event_highlights: int = 1,
    max_mover_highlights: int = 2,
    max_unresolved_highlights: int = 1,
    max_chars: int = 1400,
) -> dict[str, Any]:
    beats = build_attention_home_narrative_beats(home_payload if isinstance(home_payload, dict) else {})
    event_beats = [beat for beat in beats if str(beat.get("kind")) == "event"]
    mover_beats = [beat for beat in beats if str(beat.get("kind")) == "mover"]
    unresolved_beats = [beat for beat in beats if str(beat.get("kind")) == "unresolved"]

    lead_parts: list[str] = []
    if event_beats:
        lead_parts.append(_count_phrase(len(event_beats), "top event"))
    if mover_beats:
        lead_parts.append(_count_phrase(len(mover_beats), "key mover"))
    if unresolved_beats:
        lead_parts.append(_count_phrase(len(unresolved_beats), "unresolved move"))

    overview_text = ""
    if lead_parts:
        overview_text = _ensure_sentence(f"What matters now: {_join_human(lead_parts)}")
    elif beats:
        overview_text = "What matters now: several distinct moves across the tape."
    else:
        overview_text = "No tape items were available in the latest snapshot."

    event_highlights = [
        _beat_highlight(beat)
        for beat in event_beats[: max(int(max_event_highlights), 0)]
    ]
    mover_highlights = [
        _beat_highlight(beat)
        for beat in mover_beats[: max(int(max_mover_highlights), 0)]
    ]
    unresolved_highlights = [
        _beat_highlight(beat)
        for beat in unresolved_beats[: max(int(max_unresolved_highlights), 0)]
    ]

    summary_lines: list[str] = []
    if overview_text:
        summary_lines.extend(["**What Matters Now**", overview_text])
    for section_title, items in [
        ("Top Events", event_highlights),
        ("Key Movers", mover_highlights),
        ("Still Unresolved", unresolved_highlights),
    ]:
        section_lines = _build_section_lines(section_title, items)
        if section_lines:
            if summary_lines:
                summary_lines.append("")
            summary_lines.extend(section_lines)

    audio_sentences = [overview_text]
    audio_sentences.extend(
        f"Top event: {_narration_ready_text(item, max_sentences=2)}"
        for item in event_highlights
        if _coerce_text(item)
    )
    audio_sentences.extend(
        f"Key mover: {_narration_ready_text(item, max_sentences=2)}"
        for item in mover_highlights
        if _coerce_text(item)
    )
    audio_sentences.extend(
        f"Still unresolved: {_narration_ready_text(item, max_sentences=2)}"
        for item in unresolved_highlights
        if _coerce_text(item)
    )

    summary_text = "\n".join(line for line in summary_lines if line is not None).strip()
    audio_text = _trim_text(" ".join(sentence for sentence in audio_sentences if _coerce_text(sentence)), limit=max(int(max_chars), 240))
    return {
        "headline": "Tape Summary",
        "summary_text": summary_text,
        "audio_text": audio_text or overview_text,
        "event_count": len(event_beats),
        "must_read_count": len(mover_beats),
        "unresolved_count": len(unresolved_beats),
        "featured_symbols": _unique_symbols(beats),
        "beats": beats,
    }


_ATTENTION_HOME_HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hypothesis": {"type": "string"},
    },
    "required": ["hypothesis"],
}


def _summary_run_id(home_payload: dict[str, object]) -> str:
    return _coerce_text(home_payload.get("run_id")) or "attention-home-summary"


def _summary_asof_time(home_payload: dict[str, object]) -> pd.Timestamp:
    parsed = pd.to_datetime(home_payload.get("generated_at_utc"), utc=True, errors="coerce")
    if pd.notna(parsed):
        return parsed
    return pd.Timestamp.now(tz="UTC")


def _summary_candidate(home_payload: dict[str, object]) -> dict[str, Any]:
    run_id = _summary_run_id(home_payload)
    return {
        "candidate_id": f"summary::{run_id}",
        "symbol": "",
        "company_name": "Market Tape",
        "display_name": "Market Tape",
        "name": "Market Tape",
    }


def _resolve_summary_search_clients(search_clients: list[Any] | None) -> tuple[Any | None, Any | None]:
    if search_clients is None:
        return _load_search_clients()
    items = list(search_clients or [])
    serp_client = items[0] if len(items) >= 1 else None
    tavily_client = items[1] if len(items) >= 2 else None
    return serp_client, tavily_client


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.getenv(name) or default)
    except Exception:
        parsed = int(default)
    return min(max(parsed, minimum), maximum)


def _is_seeking_alpha_result(row: dict[str, Any]) -> bool:
    url = _coerce_text(row.get("url"))
    host = urlparse(url).netloc.lower().strip(".")
    source = _coerce_text(row.get("source")).lower()
    return host == "seekingalpha.com" or host.endswith(".seekingalpha.com") or source == "seeking alpha"


def _enrich_seeking_alpha_results(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limit = _env_int("ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT", 2, minimum=0, maximum=4)
    if limit <= 0:
        return list(result_rows or [])

    max_chars = _env_int("ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS", 2800, minimum=1200, maximum=6000)
    seen_urls: set[str] = set()
    opened = 0
    enriched_rows: list[dict[str, Any]] = []

    for row in list(result_rows or []):
        item = dict(row or {})
        url = _coerce_text(item.get("url"))
        if opened >= limit or not url or url in seen_urls or not _is_seeking_alpha_result(item):
            enriched_rows.append(item)
            continue

        seen_urls.add(url)
        try:
            page = browse_page(url, max_text_chars=max_chars)
        except Exception:
            enriched_rows.append(item)
            continue

        page_text = _coerce_text(page.get("text"))
        if page_text:
            item["page_text"] = page_text
            item["snippet"] = _coerce_text(page.get("excerpt")) or _coerce_text(item.get("snippet"))
            item["source"] = _coerce_text(item.get("source")) or "Seeking Alpha"
            item["browse_mode"] = _coerce_text(page.get("mode"))
            item["browse_warning"] = _coerce_text(page.get("warning"))
            opened += 1
        enriched_rows.append(item)
    return enriched_rows


def _supporting_claims_from_results(
    home_payload: dict[str, object],
    *,
    queries: list[str],
    llm_client: LLMClient,
    search_clients: list[Any] | None,
) -> list[dict[str, Any]]:
    trace = _collect_summary_research_trace(
        home_payload,
        queries=queries,
        llm_client=llm_client,
        search_clients=search_clients,
    )
    return list(trace.get("claims") or [])


def _collect_summary_research_trace(
    home_payload: dict[str, object],
    *,
    queries: list[str],
    llm_client: LLMClient,
    search_clients: list[Any] | None,
) -> dict[str, Any]:
    if not queries:
        return {"request_rows": [], "result_rows": [], "documents": [], "chunks": pd.DataFrame(), "claims": []}

    serp_client, tavily_client = _resolve_summary_search_clients(search_clients)
    if serp_client is None and tavily_client is None:
        raise RuntimeError("No search clients available for agentic homepage summary")

    run_id = _summary_run_id(home_payload)
    asof_time_utc = _summary_asof_time(home_payload)
    candidate = _summary_candidate(home_payload)
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []

    for query in queries:
        query_requests, query_results = _search_query_results(
            query,
            candidate_id=str(candidate["candidate_id"]),
            symbol="",
            company_name="Market Tape",
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            serp_client=serp_client,
            tavily_client=tavily_client,
            llm_client=llm_client,
            budget=6,
        )
        request_rows.extend(query_requests)
        result_rows.extend(query_results)

    for row in request_rows:
        row["research_scope"] = "home_summary"
    for row in result_rows:
        row["research_scope"] = "home_summary"

    result_rows = _enrich_seeking_alpha_results(result_rows)
    documents = annotate_source_documents(
        _documents_from_search_results(
            candidate,
            result_rows,
            run_id=run_id,
            asof_time_utc=asof_time_utc,
        ),
        asof_time_utc=asof_time_utc,
    )
    for row in documents:
        row["research_scope"] = "home_summary"
    chunks = _chunk_source_documents(
        documents,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    if not chunks.empty:
        chunks = chunks.copy()
        chunks["research_scope"] = "home_summary"
    claims = _fallback_claims_from_chunks(
        candidate,
        chunks,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        hypotheses=[{"kind": "cross_market", "text": "A shared cross-market explanation may connect the tape."}],
    )
    claims = sorted(
        claims,
        key=lambda item: (
            -float(item.get("confidence_score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            -float(item.get("causal_score") or 0.0),
        ),
    )
    for item in claims:
        item["research_scope"] = "home_summary"
    return {
        "request_rows": request_rows,
        "result_rows": result_rows,
        "documents": documents,
        "chunks": chunks,
        "claims": claims,
    }


def _synthesize_attention_home_hypothesis(
    *,
    beats: list[dict[str, object]],
    claims: list[dict[str, Any]],
    queries: list[str],
    llm_client: LLMClient,
) -> str:
    evidence_rows = [
        {
            "claim_text": _coerce_text(item.get("claim_text")),
            "source": _coerce_text(item.get("source")),
            "freshness_class": _coerce_text(item.get("freshness_class")),
            "confidence_score": float(item.get("confidence_score") or 0.0),
        }
        for item in claims
        if _coerce_text(item.get("claim_text"))
    ]
    if not evidence_rows:
        raise RuntimeError("No supporting claims available for homepage hypothesis")

    data = llm_client.generate_json(
        system_prompt=(
            "You write a market hypothesis for a homepage summary. "
            "Use the supplied beats and evidence only. "
            "Explain the likely macro, sector, or cross-asset narrative in one short paragraph using simple language. "
            "Do not repeat each beat. Do not speculate beyond the evidence."
        ),
        user_prompt=json.dumps(
            {
                "beats": [
                    {
                        "kind": _coerce_text(beat.get("kind")),
                        "sentence": _coerce_text(beat.get("sentence")),
                        "summary": _coerce_text(beat.get("summary")),
                        "symbols": [str(symbol).upper().strip() for symbol in list(beat.get("symbols") or []) if str(symbol).strip()],
                    }
                    for beat in beats[:6]
                ],
                "supporting_claims": evidence_rows[:6],
                "research_queries": queries[:5],
            },
            ensure_ascii=False,
            default=str,
        ),
        schema_name="attention_home_hypothesis",
        schema=_ATTENTION_HOME_HYPOTHESIS_SCHEMA,
    )
    hypothesis = _trim_text(data.get("hypothesis"), limit=420)
    if not hypothesis:
        raise RuntimeError("Homepage hypothesis synthesis returned empty text")
    return hypothesis


def _prepend_hypothesis_section(summary_text: object, hypothesis: object) -> str:
    base_text = _coerce_text(summary_text)
    hypothesis_text = _trim_text(hypothesis, limit=420)
    if not hypothesis_text:
        return base_text
    lines = ["**Market Hypothesis**", hypothesis_text]
    if base_text:
        lines.extend(["", base_text])
    return "\n".join(lines).strip()


def build_attention_agentic_summary(
    home_payload: dict[str, object],
    *,
    llm_client: LLMClient,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
) -> dict[str, Any]:
    summary, _ = build_attention_agentic_summary_with_trace(
        home_payload,
        llm_client=llm_client,
        search_clients=search_clients,
        max_search_queries=max_search_queries,
        max_chars=max_chars,
    )
    return summary


def build_attention_agentic_summary_with_trace(
    home_payload: dict[str, object],
    *,
    llm_client: LLMClient,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    if llm_client is None:
        raise ValueError("llm_client is required for build_attention_agentic_summary_with_trace")

    base_summary = build_attention_home_summary(
        home_payload,
        max_chars=max_chars,
    )
    queries = _plan_summary_research(home_payload, llm_client=llm_client)[: max(int(max_search_queries), 1)]
    if not queries:
        raise RuntimeError("Summary research planner returned no queries")

    trace = _collect_summary_research_trace(
        home_payload,
        queries=queries,
        llm_client=llm_client,
        search_clients=search_clients,
    )
    claims = list(trace.get("claims") or [])
    if not claims:
        raise RuntimeError("Summary research produced no supporting claims")

    hypothesis = _synthesize_attention_home_hypothesis(
        beats=list(base_summary.get("beats") or []),
        claims=claims,
        queries=queries,
        llm_client=llm_client,
    )

    summary_text = _prepend_hypothesis_section(base_summary.get("summary_text"), hypothesis)
    audio_text = _trim_text(
        f"Market hypothesis: {_coerce_text(hypothesis)} {_coerce_text(base_summary.get('audio_text'))}",
        limit=max(int(max_chars), 240),
    )

    summary = {
        **base_summary,
        "hypothesis": hypothesis,
        "summary_text": summary_text,
        "audio_text": audio_text or _coerce_text(base_summary.get("audio_text")) or hypothesis,
        "research_queries": queries,
    }
    asof_time_utc = _summary_asof_time(home_payload)
    trace_frames = {
        "attention_search_requests": pd.DataFrame(trace.get("request_rows") or []),
        "attention_search_results": pd.DataFrame(trace.get("result_rows") or []),
        "attention_source_documents": pd.DataFrame(trace.get("documents") or []),
        "attention_evidence_chunks": trace.get("chunks") if isinstance(trace.get("chunks"), pd.DataFrame) else pd.DataFrame(),
        "attention_claims": _serialize_claims_frame(claims, asof_time_utc=asof_time_utc),
    }
    if not trace_frames["attention_claims"].empty:
        trace_frames["attention_claims"] = trace_frames["attention_claims"].copy()
        trace_frames["attention_claims"]["research_scope"] = "home_summary"
    return summary, trace_frames


def build_attention_home_summary_payload(
    home_payload: dict[str, object],
    *,
    max_event_highlights: int = 1,
    max_mover_highlights: int = 2,
    max_unresolved_highlights: int = 1,
    max_chars: int = 1400,
) -> dict[str, Any]:
    summary = build_attention_home_summary(
        home_payload,
        max_event_highlights=max_event_highlights,
        max_mover_highlights=max_mover_highlights,
        max_unresolved_highlights=max_unresolved_highlights,
        max_chars=max_chars,
    )
    return {
        "headline": _coerce_text(summary.get("headline")) or "Tape Summary",
        "summary_text": _coerce_text(summary.get("summary_text")),
        "audio_text": _coerce_text(summary.get("audio_text")) or _coerce_text(summary.get("summary_text")),
        "event_count": max(int(summary.get("event_count") or 0), 0),
        "must_read_count": max(int(summary.get("must_read_count") or 0), 0),
        "unresolved_count": max(int(summary.get("unresolved_count") or 0), 0),
        "featured_symbols": [
            str(symbol).upper().strip()
            for symbol in list(summary.get("featured_symbols") or [])
            if str(symbol).strip()
        ],
    }


def attach_attention_home_summary_audio(
    summary_payload: dict[str, object],
    *,
    tts_config: ElevenLabsTTSConfig | None = None,
    tts_client: ElevenLabsTTSClient | None = None,
) -> dict[str, Any]:
    payload = dict(summary_payload or {})
    audio_text = _coerce_text(payload.get("audio_text"))
    if not audio_text:
        return payload

    resolved_config = tts_config or load_elevenlabs_tts_config()
    if resolved_config is None:
        return payload

    client = tts_client or ElevenLabsTTSClient(resolved_config)
    audio_bytes = client.synthesize(audio_text)
    payload.update(
        {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "audio_mime_type": audio_mime_type(resolved_config.output_format),
            "audio_file_extension": audio_file_extension(resolved_config.output_format),
            "voice_id": resolved_config.voice_id,
            "model_id": resolved_config.model_id,
            "output_format": resolved_config.output_format,
        }
    )
    return payload


__all__ = [
    "attach_attention_home_summary_audio",
    "attention_mover_card_title",
    "build_attention_agentic_summary",
    "build_attention_home_narrative_beats",
    "build_attention_home_summary",
    "build_attention_home_summary_payload",
]
