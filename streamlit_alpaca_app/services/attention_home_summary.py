from __future__ import annotations

import base64
import re
from typing import Any

from .attention_surface import attention_home_bundle_preview, attention_home_surface_summary
from .elevenlabs_tts import (
    ElevenLabsTTSClient,
    ElevenLabsTTSConfig,
    audio_file_extension,
    audio_mime_type,
    load_elevenlabs_tts_config,
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
    "build_attention_home_narrative_beats",
    "build_attention_home_summary",
    "build_attention_home_summary_payload",
]
