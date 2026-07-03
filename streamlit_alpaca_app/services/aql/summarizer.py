from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from ..attention_surface import attention_home_bundle_preview, attention_home_surface_summary, has_causal_language
from ..elevenlabs_tts import (
    ElevenLabsTTSClient,
    ElevenLabsTTSConfig,
)
from ..page_browsing import browse_page
from ..saa import prepare_retained_evidence_chunks, search_prepared_evidence_chunks
from ..common.hypothesis import verify_hypothesis as _common_verify_hypothesis
from .collector import _plan_summary_research, _search_query_results
from .config import _load_search_clients
from .constants import EmbeddingClient, LLMClient
from .critique import critique_home_summary, judge_revise_summary
from ..llm import NARRATIVE_STYLE_RULE, LLMAPIError, get_config_param, get_prompt, register_config_param, register_narrative_prompt
from ..aql_zopedia_engine import (
    attach_aql_zopedia_summary_audio,
    load_aql_zopedia_llm_client,
)
from .extractor import (
    _chunk_source_documents,
    _documents_from_search_results,
    _extract_claims,
    _rank_evidence_chunks,
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


# --- Display limits (applied at UI render time — changes take effect instantly) ---
_P_BEAT_HIGHLIGHT_SENTENCES = register_config_param(
    "Beat highlight max sentences", group="Display Limits", default=2,
    description="Max sentences kept per beat highlight bullet",
)
_P_SECTION_BULLET_CHARS = register_config_param(
    "Section bullet char limit", group="Display Limits", default=400,
    description="Max characters per bullet in summary sections",
)
_P_AUDIO_MAX_CHARS = register_config_param(
    "Audio text char limit", group="Display Limits", default=1400,
    description="Max characters for spoken audio summary",
)
_P_HYPOTHESIS_CHARS = register_config_param(
    "Hypothesis char limit", group="Display Limits", default=420,
    description="Max characters for the synthesized market hypothesis",
)
_P_SENTENCE_FRAGMENT_MAX = register_config_param(
    "Sentence fragment max sentences", group="Display Limits", default=1,
    description="Max sentences kept in sentence-fragment extraction",
)

# --- LLM context params (applied at pipeline job time — changes require re-run) ---
_P_HYPOTHESIS_BEATS = register_config_param(
    "Hypothesis context beats", group="LLM Context Window", default=6,
    description="Max beats passed to the hypothesis LLM call",
)
_P_HYPOTHESIS_CLAIMS = register_config_param(
    "Hypothesis context claims", group="LLM Context Window", default=8,
    description="Max supporting claims passed to the hypothesis LLM call",
)
_P_HYPOTHESIS_QUERIES = register_config_param(
    "Hypothesis context queries", group="LLM Context Window", default=5,
    description="Max research queries passed to the hypothesis LLM call",
)
_P_BEAT_SYMBOLS = register_config_param(
    "Beat context symbols", group="LLM Context Window", default=4,
    description="Max symbols shown per beat in LLM context",
)


def _looks_fragmentary_text(text: object) -> bool:
    clean = _normalize_text(text)
    if not clean:
        return True
    return clean.rstrip().endswith("...")


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

    normalized_text = _normalize_text(text)
    if not normalized_text or _looks_fragmentary_text(normalized_text):
        return ""
    return _ensure_sentence(normalized_text.rstrip(".!?"))


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
    clean = _narration_ready_text(text, max_sentences=3)
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


def _observed_beat_sentence(preview: Any, fallback: object) -> str:
    def _preview_get(key: str) -> object:
        if isinstance(preview, dict):
            return preview.get(key)
        return getattr(preview, key, "")

    for value in (
        _preview_get("what_changed_text"),
        _preview_get("summary_text"),
        fallback,
    ):
        sentence = _narration_ready_text(value, max_sentences=1)
        if sentence:
            return sentence
    return ""


def build_attention_home_narrative_beats(home_payload: dict[str, object]) -> list[dict[str, object]]:
    top_events = list(home_payload.get("top_events") or [])
    must_read = list(home_payload.get("must_read_movers") or [])
    unresolved = list(home_payload.get("unresolved_large_moves") or [])

    beats: list[dict[str, object]] = []
    for event in top_events:
        preview = attention_home_bundle_preview(event, bundle={})
        summary_text = attention_home_surface_summary(preview, is_event=True)
        cause_status = _coerce_text(preview.get("cause_status") if isinstance(preview, dict) else getattr(preview, "cause_status", "")).lower()
        sentence = _coerce_text(event.get("event_title"))
        if cause_status == "unresolved":
            sentence = _observed_beat_sentence(preview, sentence)
        beats.append(
            {
                "bundle_id": _coerce_text(event.get("bundle_id")),
                "sentence": sentence,
                "summary": summary_text,
                "business_context": _coerce_text(
                    event.get("business_resolution_text")
                    or event.get("business_context_text")
                    or event.get("surface_business_context_text")
                ),
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
                "business_context": _coerce_text(
                    mover.get("business_resolution_text")
                    or mover.get("business_context_text")
                    or mover.get("surface_business_context_text")
                ),
                "symbols": [str(mover.get("symbol") or "").upper().strip()],
                "kind": "mover",
            }
        )
    for mover in unresolved:
        preview = attention_home_bundle_preview(mover, bundle={})
        summary_text = attention_home_surface_summary(preview, is_event=False)
        beat = {
            "bundle_id": _coerce_text(mover.get("bundle_id")),
            "sentence": _observed_beat_sentence(preview, attention_mover_card_title(mover)),
            "summary": summary_text,
            "business_context": _coerce_text(
                mover.get("business_resolution_text")
                or mover.get("business_context_text")
                or mover.get("surface_business_context_text")
            ),
            "symbols": [str(mover.get("symbol") or "").upper().strip()],
            "kind": "unresolved",
        }
        if not _is_low_information_unresolved_beat(beat):
            beats.append(beat)
    return beats


def _is_low_information_unresolved_beat(beat: dict[str, object]) -> bool:
    if _coerce_text(beat.get("kind")) != "unresolved":
        return False
    business_context = _coerce_text(beat.get("business_context"))
    summary = _coerce_text(beat.get("summary"))
    if business_context:
        return False
    if not summary:
        return True
    if not has_causal_language(summary):
        return True
    lowered = summary.lower()
    if re.fullmatch(r"[a-z0-9.\-]+ moved (higher|lower|up|down) today\.?", lowered):
        return True
    if lowered.endswith(" moved higher today.") or lowered.endswith(" moved lower today."):
        return True
    return False


def _summary_prompt_beats(beats: list[dict[str, object]]) -> list[dict[str, object]]:
    return [beat for beat in list(beats or []) if not _is_low_information_unresolved_beat(beat)]


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

    signal_context = _build_signal_context_text(home_payload)
    prompt_beats = _summary_prompt_beats(beats)
    llm_result = _llm_home_summary(prompt_beats, signal_context=signal_context)

    if llm_result:
        overview_text = _coerce_text(llm_result.get("overview"))
        summary_lines: list[str] = []
        if overview_text:
            summary_lines.append(overview_text)
        for section in list(llm_result.get("sections") or []):
            title = _coerce_text(section.get("title"))
            bullets = [_coerce_text(b) for b in list(section.get("bullets") or []) if _coerce_text(b)]
            if not title or not bullets:
                continue
            if summary_lines:
                summary_lines.append("")
            summary_lines.append(f"**{title}**")
            summary_lines.extend(f"- {b}" for b in bullets)
        audio_text = _trim_text(_coerce_text(llm_result.get("audio_text")) or overview_text, limit=4000)
        summary_status = "ok"
        data_gaps: list[str] = []
    else:
        overview_text = ""
        summary_lines = []
        audio_text = ""
        summary_status = "unavailable"
        data_gaps = ["Homepage summary LLM unavailable; deterministic code did not synthesize a narrative."]

    summary_text = "\n".join(line for line in summary_lines if line is not None).strip()
    return {
        "headline": "Market Summary",
        "summary_text": summary_text,
        "audio_text": audio_text or overview_text,
        "event_count": len(event_beats),
        "must_read_count": len(mover_beats),
        "unresolved_count": len(unresolved_beats),
        "featured_symbols": _unique_symbols(prompt_beats or beats),
        "beats": beats,
        "summary_status": summary_status,
        "data_gaps": data_gaps,
    }


_HOME_SUMMARY_SYSTEM_PROMPT = register_narrative_prompt(
    name="Homepage Summary (overview / sections / audio_text)",
    file="services/aql/summarizer.py",
    group="AQL / Research",
    prompt=(
        "You are writing the homepage summary for a professional financial markets dashboard. "
        f"{NARRATIVE_STYLE_RULE}\n"
        "Given today's market events, movers, and structural signals, write:\n"
        "1. overview: One sentence (under 30 words) on the single most important thing happening. "
        "Name the actual asset classes, sectors, or moves. Never start with 'What matters now' or count items.\n"
        "2. sections: Group items into 2-3 natural sections with titles that say what happened "
        "(e.g. 'Healthcare splits by industry' not 'Top Events'). Each bullet is one specific sentence. "
        "When market structure or macro signals reinforce or contradict market activity, weave that context in "
        "(e.g. 'rising despite decelerating CPI', 'decoupling from SPY with r2=0.9'). "
        "Do not spend words on absent spillover, missing explanations, or missing confirmation; if the evidence is thin, "
        "keep the sentence to the observed move and move on. "
        "Do not create a separate 'signals' section — integrate them into the narrative.\n"
        "3. audio_text: 8-12 concise sentences, roughly 150-230 words, spoken aloud as a markets desk anchor would say them. "
        "It should cover the main hypothesis, the largest clusters, and what to watch next without reading every bullet."
    ),
)


def _build_signal_context_text(home_payload: dict[str, object]) -> str:
    """Build a compact text block from notable signal dicts in the home payload.

    Only signals that are actually moving — extreme z-scores, significant RoC,
    accelerating/decelerating regimes, or correlation breakdowns — are included.
    """
    try:
        from compute.signal_extraction import (
            filter_notable_signals,
            filter_notable_cross_signals,
            format_signals_for_prompt,
            format_cross_signals_for_prompt,
        )
    except ImportError:
        return ""
    parts: list[str] = []
    market_signals = home_payload.get("market_signals")
    if isinstance(market_signals, list) and market_signals:
        notable = filter_notable_signals(market_signals)
        if notable:
            parts.append(format_signals_for_prompt(notable, label="Market structure signals"))
    fred_signals = home_payload.get("fred_signals")
    if isinstance(fred_signals, list) and fred_signals:
        notable = filter_notable_signals(fred_signals)
        if notable:
            parts.append(format_signals_for_prompt(notable, label="Macro regime signals"))
    cross_signals = home_payload.get("cross_series_signals")
    if isinstance(cross_signals, list) and cross_signals:
        notable = filter_notable_cross_signals(cross_signals)
        if notable:
            parts.append(format_cross_signals_for_prompt(notable, label="Cross-series signals"))
    return "\n\n".join(parts)

_HOME_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "bullets"],
            },
        },
        "audio_text": {"type": "string"},
    },
    "required": ["overview", "sections", "audio_text"],
}


def _llm_home_summary(
    beats: list[dict[str, object]],
    *,
    signal_context: str = "",
) -> dict[str, Any] | None:
    llm_client = load_aql_zopedia_llm_client(surface="aql.home_summary")
    if llm_client is None:
        return None
    beat_lines = []
    for beat in beats:
        kind = str(beat.get("kind") or "")
        sentence = _coerce_text(beat.get("sentence"))
        summary = _coerce_text(beat.get("summary"))
        symbols = ", ".join(str(s) for s in list(beat.get("symbols") or [])[:int(get_config_param(_P_BEAT_SYMBOLS))])
        line = f"[{kind}] {sentence}"
        if symbols:
            line += f" ({symbols})"
        if summary:
            line += f" — {summary}"
        business_context = _coerce_text(beat.get("business_context"))
        if business_context:
            line += f" Business context: {business_context}"
        beat_lines.append(line)
    context = "\n".join(beat_lines)
    user_prompt = f"Today's market activity:\n{context}"
    if signal_context:
        user_prompt += f"\n\n{signal_context}"
    try:
        result = llm_client.generate_json(
            system_prompt=get_prompt(_HOME_SUMMARY_SYSTEM_PROMPT),
            user_prompt=user_prompt,
            schema_name="home_summary",
            schema=_HOME_SUMMARY_SCHEMA,
        )
        return result
    except (LLMAPIError, Exception):
        return None


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
        "company_name": "Market Activity",
        "display_name": "Market Activity",
        "name": "Market Activity",
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


def _result_text_length(row: dict[str, Any]) -> int:
    return len(_coerce_text(row.get("page_text")) or _coerce_text(row.get("provider_text")) or _coerce_text(row.get("snippet")))


def _page_enrichment_priority(row: dict[str, Any], *, min_text_chars: int) -> float:
    authority_rank = int(row.get("authority_rank") or 3)
    text_len = _result_text_length(row)
    source = _coerce_text(row.get("source")).lower()
    score = 0.0
    score += max(0.0, 2.4 - authority_rank * 0.4)
    score += max(0.0, float(min_text_chars - min(text_len, min_text_chars)) / max(float(min_text_chars), 1.0))
    if _is_seeking_alpha_result(row):
        score += 2.0
    if source in {"reuters", "bloomberg", "financial times", "the wall street journal", "cnbc", "sec edgar"}:
        score += 0.9
    return round(score, 3)


def _enrich_result_pages(result_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total_limit = _env_int("ATTENTION_HOME_PAGE_ENRICH_LIMIT", 4, minimum=0, maximum=8)
    seeking_alpha_limit = _env_int("ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT", 2, minimum=0, maximum=4)
    if total_limit <= 0:
        return list(result_rows or [])

    max_chars = _env_int("ATTENTION_HOME_PAGE_ENRICH_MAX_CHARS", 12000, minimum=3000, maximum=20000)
    min_text_chars = _env_int("ATTENTION_HOME_PAGE_ENRICH_MIN_TEXT_CHARS", 220, minimum=80, maximum=1200)
    enriched_rows = [dict(row or {}) for row in list(result_rows or [])]
    candidate_rows: list[tuple[int, float, bool]] = []
    seen_urls: set[str] = set()
    for idx, item in enumerate(enriched_rows):
        url = _coerce_text(item.get("url"))
        if not url or url in seen_urls or _coerce_text(item.get("result_kind")) == "error":
            continue
        seen_urls.add(url)
        if _coerce_text(item.get("page_text")):
            continue
        text_len = _result_text_length(item)
        is_sa = _is_seeking_alpha_result(item)
        if text_len >= min_text_chars and not is_sa:
            continue
        candidate_rows.append((idx, _page_enrichment_priority(item, min_text_chars=min_text_chars), is_sa))

    opened = 0
    opened_seeking_alpha = 0
    for idx, _, is_seeking_alpha in sorted(candidate_rows, key=lambda item: (-item[1], item[0])):
        if opened >= total_limit:
            break
        if is_seeking_alpha and opened_seeking_alpha >= seeking_alpha_limit:
            continue
        item = enriched_rows[idx]
        url = _coerce_text(item.get("url"))
        if not url:
            continue
        try:
            page = browse_page(url, max_text_chars=max_chars)
        except Exception:
            continue
        page_text = _coerce_text(page.get("text"))
        if not page_text:
            continue
        item["page_text"] = page_text
        item["snippet"] = _coerce_text(page.get("excerpt")) or _coerce_text(item.get("snippet")) or _coerce_text(item.get("title"))
        item["source"] = _coerce_text(item.get("source")) or ("Seeking Alpha" if is_seeking_alpha else _coerce_text(item.get("provider")))
        item["browse_mode"] = _coerce_text(page.get("mode"))
        item["browse_warning"] = _coerce_text(page.get("warning"))
        opened += 1
        if is_seeking_alpha:
            opened_seeking_alpha += 1
    return enriched_rows


def _retrieve_summary_evidence_chunks(
    chunks: pd.DataFrame,
    *,
    queries: list[str],
    run_id: str,
    asof_time_utc: pd.Timestamp,
    candidate: dict[str, Any],
    embedding_client: EmbeddingClient | None = None,
) -> pd.DataFrame:
    if not isinstance(chunks, pd.DataFrame) or chunks.empty:
        return pd.DataFrame()
    prepared_chunks, _ = prepare_retained_evidence_chunks(
        chunks,
        dataset_name="attention_evidence_chunks",
        dataset_version_id=f"attention_home_summary__{run_id}",
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    if prepared_chunks.empty:
        return prepared_chunks
    prepared_chunks = prepared_chunks.copy()
    prepared_chunks["research_scope"] = "home_summary"
    retrieved_frames: list[pd.DataFrame] = []
    for query in list(queries or []):
        retrieved = search_prepared_evidence_chunks(
            prepared_chunks,
            query=query,
            research_scopes=["home_summary"],
            run_id=run_id,
            limit=6,
            use_semantic=True,
            embedding_client=embedding_client,
        )
        if isinstance(retrieved, pd.DataFrame) and not retrieved.empty:
            retrieved_frames.append(retrieved)
    if retrieved_frames:
        merged_scores = (
            pd.concat(retrieved_frames, ignore_index=True, sort=False)
            .sort_values(["search_score", "published_at", "authority_rank"], ascending=[False, False, True], na_position="last")
            .drop_duplicates(subset=["chunk_record_id"], keep="first")
        )
        score_columns = [
            column
            for column in ("chunk_record_id", "search_score", "score_embedding", "score_lexical", "score_rerank", "match_source", "query_embedding_model")
            if column in merged_scores.columns
        ]
        if score_columns:
            prepared_chunks = prepared_chunks.merge(
                merged_scores[score_columns],
                on="chunk_record_id",
                how="left",
            )
    prepared_chunks = _rank_evidence_chunks(
        prepared_chunks,
        candidate=candidate,
        asof_time_utc=asof_time_utc,
    )
    return prepared_chunks


def _summary_trace_top_sources(chunks: pd.DataFrame, *, limit: int = 4) -> list[dict[str, Any]]:
    if not isinstance(chunks, pd.DataFrame) or chunks.empty:
        return []
    ranked = chunks.copy()
    sort_columns = [column for column in ("search_score", "retrieval_score", "published_at") if column in ranked.columns]
    if sort_columns:
        ascending = [False if column != "published_at" else False for column in sort_columns]
        ranked = ranked.sort_values(sort_columns, ascending=ascending, na_position="last")
    dedupe_keys = [column for column in ("canonical_document_id", "url", "title") if column in ranked.columns]
    if dedupe_keys:
        ranked = ranked.drop_duplicates(subset=dedupe_keys, keep="first")
    rows: list[dict[str, Any]] = []
    for _, row in ranked.head(max(int(limit), 1)).iterrows():
        rows.append(
            {
                "source": _coerce_text(row.get("source_provider")) or _coerce_text(row.get("search_provider")),
                "title": _coerce_text(row.get("title")),
                "url": _coerce_text(row.get("url")) or _coerce_text(row.get("canonical_url")),
                "match_source": _coerce_text(row.get("match_source")),
                "raw_text_origin": _coerce_text(row.get("raw_text_origin")),
            }
        )
    return rows


def _summary_trace_supporting_claims(
    claims: list[dict[str, Any]],
    *,
    chunks: pd.DataFrame,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not claims:
        return []
    chunk_lookup: dict[str, pd.Series] = {}
    if isinstance(chunks, pd.DataFrame) and not chunks.empty and "chunk_id" in chunks.columns:
        chunk_lookup = {
            _coerce_text(row.get("chunk_id")): row
            for _, row in chunks.iterrows()
            if _coerce_text(row.get("chunk_id"))
        }
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        list(claims or []),
        key=lambda item: (
            -float(item.get("confidence_score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            -float(item.get("causal_score") or 0.0),
        ),
    )
    for item in ordered[: max(int(limit), 1)]:
        chunk_ids = list(item.get("evidence_chunk_ids") or [])
        chunk_row = chunk_lookup.get(_coerce_text(chunk_ids[0])) if chunk_ids else None
        source = _coerce_text(item.get("source"))
        if not source and chunk_row is not None:
            source = _coerce_text(chunk_row.get("source_provider"))
        text = _coerce_text(item.get("claim_text"))
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "source": source,
                "confidence_score": float(item.get("confidence_score") or 0.0),
                "title": _coerce_text(chunk_row.get("title")) if chunk_row is not None else "",
                "url": _coerce_text(chunk_row.get("url")) if chunk_row is not None else "",
            }
        )
    return rows


def _supporting_claims_from_results(
    home_payload: dict[str, object],
    *,
    queries: list[str],
    llm_client: LLMClient,
    search_clients: list[Any] | None,
    embedding_client: EmbeddingClient | None = None,
) -> list[dict[str, Any]]:
    trace = _collect_summary_research_trace(
        home_payload,
        queries=queries,
        llm_client=llm_client,
        search_clients=search_clients,
        embedding_client=embedding_client,
    )
    return list(trace.get("claims") or [])


def _collect_summary_research_trace(
    home_payload: dict[str, object],
    *,
    queries: list[str],
    llm_client: LLMClient,
    search_clients: list[Any] | None,
    embedding_client: EmbeddingClient | None = None,
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
            company_name="Market Activity",
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            serp_client=serp_client,
            tavily_client=tavily_client,
            llm_client=llm_client,
            budget=6,
            include_provider_payload=True,
            include_provider_text=True,
        )
        request_rows.extend(query_requests)
        result_rows.extend(query_results)

    for row in request_rows:
        row["research_scope"] = "home_summary"
    for row in result_rows:
        row["research_scope"] = "home_summary"

    result_rows = _enrich_result_pages(result_rows)
    documents = _documents_from_search_results(
        candidate,
        result_rows,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
    )
    for row in documents:
        row["research_scope"] = "home_summary"
    chunks = _chunk_source_documents(
        documents,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        embedding_client=embedding_client,
    )
    if not chunks.empty:
        chunks["research_scope"] = "home_summary"
        chunks = _retrieve_summary_evidence_chunks(
            chunks,
            queries=queries,
            run_id=run_id,
            asof_time_utc=asof_time_utc,
            candidate=candidate,
            embedding_client=embedding_client,
        )
    claims = _extract_claims(
        candidate,
        chunks,
        run_id=run_id,
        asof_time_utc=asof_time_utc,
        hypotheses=[{"kind": "cross_market", "text": "A shared cross-market explanation may connect market activity."}],
        llm_client=llm_client,
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
        "top_sources": _summary_trace_top_sources(chunks),
        "supporting_claims": _summary_trace_supporting_claims(claims, chunks=chunks),
    }


def _synthesize_attention_home_hypothesis(
    *,
    beats: list[dict[str, object]],
    claims: list[dict[str, Any]],
    queries: list[str],
    llm_client: LLMClient,
    signal_context: str = "",
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

    user_data = {
        "beats": [
            {
                "kind": _coerce_text(beat.get("kind")),
                "sentence": _coerce_text(beat.get("sentence")),
                "summary": _coerce_text(beat.get("summary")),
                "business_context": _coerce_text(beat.get("business_context")),
                "symbols": [str(symbol).upper().strip() for symbol in list(beat.get("symbols") or []) if str(symbol).strip()],
            }
            for beat in _summary_prompt_beats(beats)[:int(get_config_param(_P_HYPOTHESIS_BEATS))]
        ],
        "supporting_claims": evidence_rows[:int(get_config_param(_P_HYPOTHESIS_CLAIMS))],
        "research_queries": queries[:int(get_config_param(_P_HYPOTHESIS_QUERIES))],
    }
    user_prompt = json.dumps(user_data, ensure_ascii=False, default=str)
    if signal_context:
        user_prompt += f"\n\n{signal_context}"

    data = llm_client.generate_json(
        system_prompt=(
            "You write a market hypothesis for a homepage summary. "
            "Use the supplied beats, evidence, and structural signals. "
            "Explain the likely macro, sector, or cross-asset narrative in one tight paragraph of 2 to 3 sentences using simple language. "
            "Name the concrete themes behind market activity, not vague rotations. "
            "When market structure signals (trend acceleration, regime shifts, correlation breaks, z-score extremes) "
            "reinforce or contradict market activity, reference them concretely. "
            "When useful, mention the strongest source families or business triggers behind the call. "
            "Do not repeat each beat. Do not speculate beyond the evidence. "
            "If a trigger is unsupported, omit trigger language and use the supplied business context or exact missing evidence slot."
        ),
        user_prompt=user_prompt,
        schema_name="attention_home_hypothesis",
        schema=_ATTENTION_HOME_HYPOTHESIS_SCHEMA,
    )
    hypothesis = _trim_text(data.get("hypothesis"), limit=2000)
    if not hypothesis:
        raise RuntimeError("Homepage hypothesis synthesis returned empty text")
    return hypothesis


def _prepend_hypothesis_section(summary_text: object, hypothesis: object) -> str:
    base_text = _coerce_text(summary_text)
    hypothesis_text = _trim_text(hypothesis, limit=2000)
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
    embedding_client: EmbeddingClient | None = None,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
    query_service: Any | None = None,
) -> dict[str, Any]:
    summary, _ = build_attention_agentic_summary_with_trace(
        home_payload,
        llm_client=llm_client,
        embedding_client=embedding_client,
        search_clients=search_clients,
        max_search_queries=max_search_queries,
        max_chars=max_chars,
        query_service=query_service,
    )
    return summary


def build_attention_agentic_summary_with_trace(
    home_payload: dict[str, object],
    *,
    llm_client: LLMClient,
    embedding_client: EmbeddingClient | None = None,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
    query_service: Any | None = None,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    """Batch pipeline entry point: research → synthesize → verify once.

    For agentic multi-pass verification with tool access, use the zopedia
    agent with the hypothesis.verify tool instead.

    A critique+judge layer runs after the initial homepage summary is built:
    the critique agent fact-checks the summary against tool-grounded evidence,
    and the judge revises the summary based on flagged issues. Both are
    best-effort — if either step fails, the original summary is used. See
    documents/architecture/agents/CRITIQUE_JUDGE_HOMEPAGE_SUMMARY_2026-04-24.md.
    """
    if llm_client is None:
        raise ValueError("llm_client is required for build_attention_agentic_summary_with_trace")

    base_summary = build_attention_home_summary(
        home_payload,
        max_chars=max_chars,
    )

    critique_result: dict[str, Any] = {"issues": [], "tool_calls": [], "skipped": True}
    try:
        critique_result = critique_home_summary(
            summary=base_summary,
            home_payload=home_payload,
            llm_client=llm_client,
            query_service=query_service,
        )
    except Exception:
        critique_result = {"issues": [], "tool_calls": [], "skipped": True, "error": True}

    judge_revisions: list[dict[str, Any]] = []
    if critique_result.get("issues"):
        try:
            revised = judge_revise_summary(
                original=base_summary,
                critique=critique_result,
                llm_client=llm_client,
            )
        except Exception:
            revised = None
        if isinstance(revised, dict):
            judge_revisions = list(revised.get("judge_revisions") or [])
            base_summary = revised

    base_summary["critique_issues"] = list(critique_result.get("issues") or [])
    base_summary["critique_tool_calls"] = list(critique_result.get("tool_calls") or [])
    base_summary["judge_revisions"] = judge_revisions

    queries = _plan_summary_research(home_payload, llm_client=llm_client)[: max(int(max_search_queries), 1)]
    if not queries:
        raise RuntimeError("Summary research planner returned no queries")

    trace = _collect_summary_research_trace(
        home_payload,
        queries=queries,
        llm_client=llm_client,
        search_clients=search_clients,
        embedding_client=embedding_client,
    )
    claims = list(trace.get("claims") or [])
    if not claims:
        raise RuntimeError("Summary research produced no supporting claims")

    signal_context = _build_signal_context_text(home_payload)
    hypothesis = _synthesize_attention_home_hypothesis(
        beats=list(base_summary.get("beats") or []),
        claims=claims,
        queries=queries,
        llm_client=llm_client,
        signal_context=signal_context,
    )

    verification = verify_hypothesis(
        hypothesis=hypothesis,
        claims=claims,
        stories=list(base_summary.get("beats") or []),
        llm_client=llm_client,
        signal_context=signal_context,
    )

    summary_text = _prepend_hypothesis_section(base_summary.get("summary_text"), hypothesis)
    audio_text = _trim_text(
        f"Market hypothesis: {_coerce_text(hypothesis)} {_coerce_text(base_summary.get('audio_text'))}",
        limit=4000,
    )

    summary = {
        **base_summary,
        "hypothesis": hypothesis,
        "verification": verification,
        "summary_text": summary_text,
        "audio_text": audio_text or _coerce_text(base_summary.get("audio_text")) or hypothesis,
        "research_queries": queries,
        "top_sources": list(trace.get("top_sources") or []),
        "supporting_claims": list(trace.get("supporting_claims") or []),
    }
    asof_time_utc = _summary_asof_time(home_payload)
    verification_row = {
        **verification,
        "hypothesis": hypothesis,
        "run_id": _summary_run_id(home_payload),
        "asof_time_utc": asof_time_utc,
        "research_scope": "home_summary",
        "supporting_claims_json": json.dumps(verification.get("supporting_claims") or []),
        "contradicting_claims_json": json.dumps(verification.get("contradicting_claims") or []),
        "gap_queries_json": json.dumps(verification.get("gap_queries") or []),
    }
    trace_frames = {
        "attention_search_requests": pd.DataFrame(trace.get("request_rows") or []),
        "attention_search_results": pd.DataFrame(trace.get("result_rows") or []),
        "attention_source_documents": pd.DataFrame(trace.get("documents") or []),
        "attention_evidence_chunks": trace.get("chunks") if isinstance(trace.get("chunks"), pd.DataFrame) else pd.DataFrame(),
        "attention_claims": _serialize_claims_frame(claims, asof_time_utc=asof_time_utc),
        "attention_hypothesis_verification": pd.DataFrame([verification_row]),
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
        "headline": _coerce_text(summary.get("headline")) or "Market Summary",
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
        "summary_status": _coerce_text(summary.get("summary_status")),
        "data_gaps": [str(item).strip() for item in list(summary.get("data_gaps") or []) if str(item).strip()],
    }


def apply_display_limits(summary: dict[str, Any]) -> dict[str, Any]:
    """Apply UI-layer display limits to a summary payload.

    These limits are tunable in Admin → LLM Config → Display Limits
    and take effect instantly without a pipeline re-run.
    """
    result = dict(summary)

    # --- Audio text ---
    audio_limit = int(get_config_param(_P_AUDIO_MAX_CHARS))
    raw_audio = _coerce_text(result.get("audio_text"))
    if raw_audio:
        result["audio_text"] = _trim_text(raw_audio, limit=max(audio_limit, 240))

    # --- Hypothesis ---
    hyp_limit = int(get_config_param(_P_HYPOTHESIS_CHARS))
    raw_hypothesis = _coerce_text(result.get("hypothesis"))
    if raw_hypothesis:
        result["hypothesis"] = _trim_text(raw_hypothesis, limit=hyp_limit)

    # --- Summary text: truncate bullets and re-compose hypothesis header ---
    raw_summary = _coerce_text(result.get("summary_text"))
    if raw_summary:
        bullet_limit = int(get_config_param(_P_SECTION_BULLET_CHARS))
        max_sentences = int(get_config_param(_P_BEAT_HIGHLIGHT_SENTENCES))
        fragment_sentences = int(get_config_param(_P_SENTENCE_FRAGMENT_MAX))
        lines = raw_summary.split("\n")
        processed: list[str] = []
        in_hypothesis = False
        for line in lines:
            # Skip the old hypothesis block — we'll re-prepend
            if line.strip() == "**Market Hypothesis**":
                in_hypothesis = True
                continue
            if in_hypothesis:
                if line.strip() == "" and processed:
                    in_hypothesis = False
                elif line.startswith("**"):
                    in_hypothesis = False
                else:
                    continue
            if not in_hypothesis and line.startswith("- "):
                bullet_text = line[2:]
                trimmed = _trim_text(
                    _narration_ready_text(bullet_text, max_sentences=max_sentences),
                    limit=bullet_limit,
                )
                if not trimmed:
                    trimmed = _trim_text(
                        _narration_ready_text(bullet_text, max_sentences=fragment_sentences),
                        limit=bullet_limit,
                    )
                processed.append(f"- {trimmed}" if trimmed else line)
            else:
                processed.append(line)

        base_text = "\n".join(processed).strip()
        if result.get("hypothesis"):
            result["summary_text"] = _prepend_hypothesis_section(base_text, result["hypothesis"])
        else:
            result["summary_text"] = base_text

    return result


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

    return attach_aql_zopedia_summary_audio(
        payload,
        tts_config=tts_config,
        tts_client=tts_client,
    )


# Backward-compatible export: hypothesis verification is owned by services.common.
verify_hypothesis = _common_verify_hypothesis

build_market_stories = build_attention_home_narrative_beats


__all__ = [
    "attach_attention_home_summary_audio",
    "attention_mover_card_title",
    "build_attention_agentic_summary",
    "build_attention_home_narrative_beats",
    "build_attention_home_summary",
    "build_attention_home_summary_payload",
    "build_market_stories",
    "verify_hypothesis",
]
