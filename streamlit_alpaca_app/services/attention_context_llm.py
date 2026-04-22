from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

import pandas as pd

from .llm import COPY_STYLE_RULE, LLMAPIError, OpenAIChatJSONClient, get_prompt, register_copy_prompt

_FILING_ANALYST_SYSTEM_PROMPT = register_copy_prompt(
    name="SEC Filing Analyst (evidence extraction)",
    file="services/attention_context_llm.py",
    prompt=(
        f"{COPY_STYLE_RULE} "
        "You are an SEC filing analyst. Use only the provided filing metadata and filing text. "
        "Do not invent facts, numbers, or motives. Summaries should be concise and readable."
    ),
)

_ATTENTION_NARRATIVE_SYSTEM_PROMPT = register_copy_prompt(
    name="Attention Feed Narrative (anomaly + EDGAR context)",
    file="services/attention_context_llm.py",
    prompt=(
        f"{COPY_STYLE_RULE} "
        "You write short, human-readable market narratives for an attention feed. "
        "Use only the supplied anomaly metadata and EDGAR-derived evidence. "
        "Be concrete and explicitly reflect uncertainty when evidence is thin."
    ),
)

EVIDENCE_COLUMNS = [
    "symbol",
    "company_name",
    "form",
    "filing_date",
    "accession_number",
    "filing_url",
    "document_text_hash",
    "evidence_input_hash",
    "filing_angle",
    "management_focus",
    "key_points_json",
    "catalysts_json",
    "risk_flags_json",
    "tone",
    "confidence_note",
    "model",
    "generated_at_utc",
    "schema_version",
]

NARRATIVE_COLUMNS = [
    "symbol",
    "company_name",
    "input_hash",
    "llm_headline",
    "llm_summary_text",
    "llm_narrative_text",
    "llm_why_now",
    "llm_management_signal",
    "llm_supporting_points_json",
    "llm_confidence",
    "llm_source_line",
    "model",
    "generated_at_utc",
    "schema_version",
]


EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "filing_angle": {"type": "string"},
        "management_focus": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string"},
        "confidence_note": {"type": "string"},
    },
    "required": [
        "filing_angle",
        "management_focus",
        "key_points",
        "catalysts",
        "risk_flags",
        "tone",
        "confidence_note",
    ],
}


NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary_text": {"type": "string"},
        "narrative_text": {"type": "string"},
        "why_now": {"type": "string"},
        "management_signal": {"type": "string"},
        "supporting_points": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
    },
    "required": [
        "headline",
        "summary_text",
        "narrative_text",
        "why_now",
        "management_signal",
        "supporting_points",
        "confidence",
    ],
}


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _coerce_timestamp(value: object) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except Exception:
        return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _trim(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _dedupe_seed(attention_frame: pd.DataFrame) -> pd.DataFrame:
    if attention_frame is None or attention_frame.empty or "entity_id" not in attention_frame.columns:
        return pd.DataFrame()
    seed = attention_frame.copy()
    if "entity_type" in seed.columns:
        entity_type = seed["entity_type"].astype(str).str.lower()
        seed = seed[entity_type.eq("symbol") | entity_type.eq("")].copy()
    if seed.empty:
        return pd.DataFrame()
    seed["symbol"] = seed["entity_id"].astype(str).str.upper().str.strip()
    if "attention_score" in seed.columns:
        seed["attention_score"] = pd.to_numeric(seed["attention_score"], errors="coerce")
        seed = seed.sort_values("attention_score", ascending=False, na_position="last")
    return seed.drop_duplicates(subset=["symbol"]).reset_index(drop=True)


def _attention_context_payload(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in [
        "symbol",
        "company_name",
        "title",
        "subtitle",
        "horizon",
        "direction",
        "direction_label",
        "peer_group_name",
        "peer_group",
        "anomaly_type",
        "attention_score",
        "severity_score",
        "impact_score",
        "relevance_score",
        "confidence_score",
        "observed_return_pct",
        "expected_return_pct",
        "residual_pct",
        "residual_zscore",
        "market_regime",
        "story_text",
    ]:
        if column not in row.index:
            continue
        value = row.get(column)
        if pd.isna(value):
            continue
        text = _coerce_text(value)
        payload[column] = text if text else value
    return payload


def build_edgar_evidence(
    filings_frame: pd.DataFrame,
    llm_client: OpenAIChatJSONClient | None,
    *,
    existing_frame: pd.DataFrame | None = None,
    asof_time_utc: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if llm_client is None or filings_frame is None or filings_frame.empty:
        return _empty_frame(EVIDENCE_COLUMNS)

    filings = filings_frame.copy()
    for column in ["symbol", "company_name", "form", "accession_number", "filing_url", "document_text", "document_text_hash", "items", "primary_doc_description"]:
        if column not in filings.columns:
            filings[column] = ""
    filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
    filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
    filings["document_text"] = filings["document_text"].astype(str)
    filings["document_text_hash"] = filings["document_text_hash"].astype(str).str.strip()
    filings = filings[(filings["document_text"].str.strip() != "") & (filings["document_text_hash"] != "")].copy()
    if filings.empty:
        return _empty_frame(EVIDENCE_COLUMNS)

    existing_by_hash: dict[str, dict[str, Any]] = {}
    existing = existing_frame.copy() if isinstance(existing_frame, pd.DataFrame) else pd.DataFrame()
    if not existing.empty and "document_text_hash" in existing.columns:
        for _, prior in existing.dropna(subset=["document_text_hash"]).drop_duplicates(subset=["document_text_hash"]).iterrows():
            existing_by_hash[_coerce_text(prior.get("document_text_hash"))] = prior.to_dict()

    generated_at = _coerce_timestamp(asof_time_utc if asof_time_utc is not None else datetime.now(timezone.utc))
    rows: list[dict[str, Any]] = []

    system_prompt = get_prompt(_FILING_ANALYST_SYSTEM_PROMPT)

    for _, filing in filings.iterrows():
        document_hash = _coerce_text(filing.get("document_text_hash"))
        if document_hash in existing_by_hash:
            rows.append(existing_by_hash[document_hash])
            continue

        filing_payload = {
            "symbol": _coerce_text(filing.get("symbol")),
            "company_name": _coerce_text(filing.get("company_name")),
            "form": _coerce_text(filing.get("form")),
            "filing_date": str(_coerce_timestamp(filing.get("filing_date"))),
            "items": _coerce_text(filing.get("items")),
            "primary_doc_description": _coerce_text(filing.get("primary_doc_description")),
            "filing_excerpt": _coerce_text(filing.get("filing_excerpt")),
            "document_text": _trim(_coerce_text(filing.get("document_text")), 10_000),
        }
        evidence_input_hash = _stable_hash(filing_payload)
        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=(
                "Extract the management signal from this SEC filing into structured JSON.\n"
                f"{json.dumps(filing_payload, ensure_ascii=False, default=str, indent=2)}"
            ),
            schema_name="edgar_evidence",
            schema=EVIDENCE_SCHEMA,
        )
        rows.append(
            {
                "symbol": filing_payload["symbol"],
                "company_name": filing_payload["company_name"],
                "form": filing_payload["form"],
                "filing_date": _coerce_timestamp(filing.get("filing_date")),
                "accession_number": _coerce_text(filing.get("accession_number")),
                "filing_url": _coerce_text(filing.get("filing_url")),
                "document_text_hash": document_hash,
                "evidence_input_hash": evidence_input_hash,
                "filing_angle": _coerce_text(data.get("filing_angle")),
                "management_focus": _coerce_text(data.get("management_focus")),
                "key_points_json": json.dumps(data.get("key_points") or [], ensure_ascii=False),
                "catalysts_json": json.dumps(data.get("catalysts") or [], ensure_ascii=False),
                "risk_flags_json": json.dumps(data.get("risk_flags") or [], ensure_ascii=False),
                "tone": _coerce_text(data.get("tone")),
                "confidence_note": _coerce_text(data.get("confidence_note")),
                "model": llm_client.config.model,
                "generated_at_utc": generated_at,
                "schema_version": "v1",
            }
        )

    if not rows:
        return _empty_frame(EVIDENCE_COLUMNS)
    return pd.DataFrame(rows)[EVIDENCE_COLUMNS].sort_values(["symbol", "filing_date"], ascending=[True, False], na_position="last").reset_index(drop=True)


def build_attention_context_narratives(
    attention_frame: pd.DataFrame,
    filings_frame: pd.DataFrame,
    evidence_frame: pd.DataFrame,
    llm_client: OpenAIChatJSONClient | None,
    *,
    existing_frame: pd.DataFrame | None = None,
    asof_time_utc: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if llm_client is None:
        return _empty_frame(NARRATIVE_COLUMNS)

    seed = _dedupe_seed(attention_frame)
    if seed.empty:
        return _empty_frame(NARRATIVE_COLUMNS)

    filings = filings_frame.copy() if isinstance(filings_frame, pd.DataFrame) else pd.DataFrame()
    evidence = evidence_frame.copy() if isinstance(evidence_frame, pd.DataFrame) else pd.DataFrame()
    for frame in [filings, evidence]:
        if not frame.empty and "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    if not filings.empty and "filing_date" in filings.columns:
        filings["filing_date"] = pd.to_datetime(filings["filing_date"], utc=True, errors="coerce")
    if not evidence.empty and "filing_date" in evidence.columns:
        evidence["filing_date"] = pd.to_datetime(evidence["filing_date"], utc=True, errors="coerce")

    existing_by_hash: dict[str, dict[str, Any]] = {}
    existing = existing_frame.copy() if isinstance(existing_frame, pd.DataFrame) else pd.DataFrame()
    if not existing.empty and "input_hash" in existing.columns:
        for _, prior in existing.dropna(subset=["input_hash"]).drop_duplicates(subset=["input_hash"]).iterrows():
            existing_by_hash[_coerce_text(prior.get("input_hash"))] = prior.to_dict()

    generated_at = _coerce_timestamp(asof_time_utc if asof_time_utc is not None else datetime.now(timezone.utc))
    rows: list[dict[str, Any]] = []
    system_prompt = get_prompt(_ATTENTION_NARRATIVE_SYSTEM_PROMPT)

    for _, attention_row in seed.iterrows():
        symbol = _coerce_text(attention_row.get("symbol"))
        symbol_filings = filings[filings["symbol"] == symbol].head(3).copy() if not filings.empty else pd.DataFrame()
        symbol_evidence = evidence[evidence["symbol"] == symbol].head(3).copy() if not evidence.empty else pd.DataFrame()
        if symbol_filings.empty and symbol_evidence.empty:
            continue

        filing_payload = []
        for _, filing in symbol_filings.iterrows():
            filing_payload.append(
                {
                    "form": _coerce_text(filing.get("form")),
                    "filing_date": str(_coerce_timestamp(filing.get("filing_date"))),
                    "items": _coerce_text(filing.get("items")),
                    "filing_excerpt": _coerce_text(filing.get("filing_excerpt")),
                    "document_text_hash": _coerce_text(filing.get("document_text_hash")),
                }
            )

        evidence_payload = []
        for _, item in symbol_evidence.iterrows():
            evidence_payload.append(
                {
                    "form": _coerce_text(item.get("form")),
                    "filing_date": str(_coerce_timestamp(item.get("filing_date"))),
                    "filing_angle": _coerce_text(item.get("filing_angle")),
                    "management_focus": _coerce_text(item.get("management_focus")),
                    "key_points": _json_list(item.get("key_points_json")),
                    "catalysts": _json_list(item.get("catalysts_json")),
                    "risk_flags": _json_list(item.get("risk_flags_json")),
                    "tone": _coerce_text(item.get("tone")),
                }
            )

        narrative_input = {
            "attention": _attention_context_payload(attention_row),
            "filings": filing_payload,
            "evidence": evidence_payload,
        }
        input_hash = _stable_hash(narrative_input)
        if input_hash in existing_by_hash:
            rows.append(existing_by_hash[input_hash])
            continue

        data = llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=(
                "Write a short feed-ready explanation of what is likely going on for this attention item. "
                "Tie the anomaly context to the EDGAR evidence.\n"
                f"{json.dumps(narrative_input, ensure_ascii=False, default=str, indent=2)}"
            ),
            schema_name="attention_context_narrative",
            schema=NARRATIVE_SCHEMA,
        )
        rows.append(
            {
                "symbol": symbol,
                "company_name": _coerce_text(attention_row.get("company_name")) or _coerce_text(symbol_filings.head(1).get("company_name").iloc[0] if not symbol_filings.empty and "company_name" in symbol_filings.columns else ""),
                "input_hash": input_hash,
                "llm_headline": _coerce_text(data.get("headline")),
                "llm_summary_text": _coerce_text(data.get("summary_text")),
                "llm_narrative_text": _coerce_text(data.get("narrative_text")),
                "llm_why_now": _coerce_text(data.get("why_now")),
                "llm_management_signal": _coerce_text(data.get("management_signal")),
                "llm_supporting_points_json": json.dumps(data.get("supporting_points") or [], ensure_ascii=False),
                "llm_confidence": _coerce_text(data.get("confidence")),
                "llm_source_line": "Synthesized from SEC EDGAR filings and anomaly context",
                "model": llm_client.config.model,
                "generated_at_utc": generated_at,
                "schema_version": "v1",
            }
        )

    if not rows:
        return _empty_frame(NARRATIVE_COLUMNS)
    return pd.DataFrame(rows)[NARRATIVE_COLUMNS].sort_values("symbol").reset_index(drop=True)


def merge_attention_context_with_llm(base_frame: pd.DataFrame, llm_frame: pd.DataFrame) -> pd.DataFrame:
    if base_frame is None or base_frame.empty:
        return base_frame if isinstance(base_frame, pd.DataFrame) else pd.DataFrame()
    if llm_frame is None or llm_frame.empty:
        enriched = base_frame.copy()
        for column in [
            "llm_headline",
            "llm_summary_text",
            "llm_narrative_text",
            "llm_why_now",
            "llm_management_signal",
            "llm_supporting_points_json",
            "llm_confidence",
            "llm_source_line",
        ]:
            if column not in enriched.columns:
                enriched[column] = ""
        return enriched

    merged = base_frame.copy()
    llm = llm_frame.drop_duplicates(subset=["symbol"]).copy()
    merged["symbol"] = merged["symbol"].astype(str).str.upper().str.strip()
    llm["symbol"] = llm["symbol"].astype(str).str.upper().str.strip()
    merged = merged.merge(
        llm[
            [
                "symbol",
                "llm_headline",
                "llm_summary_text",
                "llm_narrative_text",
                "llm_why_now",
                "llm_management_signal",
                "llm_supporting_points_json",
                "llm_confidence",
                "llm_source_line",
            ]
        ],
        on="symbol",
        how="left",
    )
    return merged


__all__ = [
    "LLMAPIError",
    "build_edgar_evidence",
    "build_attention_context_narratives",
    "merge_attention_context_with_llm",
]
