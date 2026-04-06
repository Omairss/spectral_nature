from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import os
import re
import time
from typing import Any

import pandas as pd
import requests


DEFAULT_EDGAR_FORMS: tuple[str, ...] = (
    "8-K",
    "10-Q",
    "10-K",
    "20-F",
    "6-K",
    "S-1",
    "DEF 14A",
    "SC 13D",
    "SC 13G",
)

FORM_LABELS: dict[str, str] = {
    "8-K": "current report",
    "10-Q": "quarterly report",
    "10-K": "annual report",
    "20-F": "foreign annual report",
    "6-K": "foreign issuer report",
    "S-1": "registration statement",
    "DEF 14A": "proxy statement",
    "SC 13D": "beneficial ownership filing",
    "SC 13G": "passive ownership filing",
}
DEFAULT_EXCERPT_CHARS = 340
DEFAULT_CONTEXT_CHARS = 12_000


class EdgarAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class EdgarConfig:
    user_agent: str
    data_base_url: str = "https://data.sec.gov"
    reference_base_url: str = "https://www.sec.gov"
    archives_base_url: str = "https://www.sec.gov/Archives/edgar/data"
    timeout_seconds: int = 20
    pause_seconds: float = 0.25
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS
    max_context_chars: int = DEFAULT_CONTEXT_CHARS


def _coerce_timestamp(value: object) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _coerce_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_form(value: object) -> str:
    return _coerce_text(value).upper()


def _normalize_symbols(symbols: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    cleaned = [str(symbol or "").upper().strip() for symbol in symbols]
    return [symbol for symbol in dict.fromkeys(cleaned) if symbol]


def _form_label(form: str) -> str:
    return FORM_LABELS.get(form, form or "filing")


def _clean_snippet(text: object, limit: int = 340) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _recent_form_list(forms: list[str], limit: int = 3) -> str:
    seen = [form for form in dict.fromkeys(form for form in forms if form)]
    if not seen:
        return ""
    if len(seen) == 1:
        return seen[0]
    if len(seen) == 2:
        return f"{seen[0]} and {seen[1]}"
    preview = ", ".join(seen[: max(limit - 1, 1)])
    remaining = len(seen) - max(limit - 1, 1)
    if remaining > 0:
        return f"{preview}, and {remaining} more"
    return f"{preview}, and {seen[-1]}"


def _textish_document(primary_document: str) -> bool:
    lowered = _coerce_text(primary_document).lower()
    return lowered.endswith((".htm", ".html", ".txt")) or lowered == ""


def _normalize_filing_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r", "\n")
    normalized = re.sub(r"(?is)<script.*?>.*?</script>", " ", normalized)
    normalized = re.sub(r"(?is)<style.*?>.*?</style>", " ", normalized)
    normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
    normalized = re.sub(r"(?i)</p\s*>", "\n\n", normalized)
    normalized = re.sub(r"(?i)</div\s*>", "\n", normalized)
    normalized = re.sub(r"(?i)</tr\s*>", "\n", normalized)
    normalized = re.sub(r"(?is)<[^>]+>", " ", normalized)
    normalized = unescape(normalized)
    normalized = normalized.replace("\xa0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _candidate_paragraphs(text: str) -> list[str]:
    normalized = _normalize_filing_text(text)
    if not normalized:
        return []
    paragraphs = [
        " ".join(chunk.split())
        for chunk in re.split(r"\n\s*\n+", normalized)
        if " ".join(chunk.split())
    ]
    return [chunk for chunk in paragraphs if len(chunk) >= 40]


def _ranked_filing_paragraphs(form: str, items: str, text: str) -> list[str]:
    paragraphs = _candidate_paragraphs(text)
    if not paragraphs:
        return []

    item_tokens = [token.strip() for token in re.split(r"[;,]", items or "") if token.strip()]
    preferred_patterns: list[re.Pattern[str]] = []
    for item in item_tokens[:3]:
        compact = re.escape(item)
        preferred_patterns.append(re.compile(rf"\bitem\s+{compact}\b", re.IGNORECASE))

    if form in {"10-Q", "10-K", "20-F"}:
        preferred_patterns.extend(
            [
                re.compile(r"management'?s discussion", re.IGNORECASE),
                re.compile(r"results of operations", re.IGNORECASE),
                re.compile(r"liquidity and capital resources", re.IGNORECASE),
            ]
        )
    elif form in {"8-K", "6-K"}:
        preferred_patterns.extend(
            [
                re.compile(r"results of operations", re.IGNORECASE),
                re.compile(r"financial condition", re.IGNORECASE),
                re.compile(r"press release", re.IGNORECASE),
            ]
        )

    boilerplate_patterns = [
        re.compile(r"forward-looking statements", re.IGNORECASE),
        re.compile(r"commission file number", re.IGNORECASE),
        re.compile(r"check the appropriate box", re.IGNORECASE),
        re.compile(r"signature", re.IGNORECASE),
    ]

    preferred: list[str] = []
    fallback: list[str] = []
    for paragraph in paragraphs:
        if any(pattern.search(paragraph) for pattern in boilerplate_patterns):
            continue
        if any(pattern.search(paragraph) for pattern in preferred_patterns):
            preferred.append(paragraph)
            continue
        if len(paragraph.split()) >= 8:
            fallback.append(paragraph)

    def _heading_like(paragraph: str) -> bool:
        stripped = paragraph.strip()
        if re.match(r"(?i)^item\s+\d", stripped) and len(stripped) < 120:
            return True
        if len(stripped.split()) <= 10 and stripped == stripped.title():
            return True
        return False

    selected = preferred or fallback
    if preferred and all(_heading_like(paragraph) for paragraph in preferred):
        selected = preferred + fallback[:2]
    return selected


def _meaningful_filing_excerpt(form: str, items: str, text: str, *, limit: int) -> str:
    selected = _ranked_filing_paragraphs(form, items, text)
    if not selected:
        return ""

    excerpt_parts: list[str] = []
    total_length = 0
    for paragraph in selected:
        cleaned = _clean_snippet(paragraph, limit=limit)
        if not cleaned:
            continue
        projected = total_length + len(cleaned) + (1 if excerpt_parts else 0)
        if projected > limit and excerpt_parts:
            break
        excerpt_parts.append(cleaned)
        total_length = projected
        if total_length >= limit * 0.8:
            break
    return _clean_snippet(" ".join(excerpt_parts), limit=limit)


def _meaningful_filing_context(form: str, items: str, text: str, *, limit: int) -> str:
    if limit <= 0:
        return ""

    selected = _ranked_filing_paragraphs(form, items, text)
    if not selected:
        normalized = _normalize_filing_text(text)
        return _clean_snippet(normalized, limit=limit)

    context_parts: list[str] = []
    total_length = 0
    for paragraph in selected:
        cleaned = _clean_snippet(paragraph, limit=max(limit, DEFAULT_EXCERPT_CHARS))
        if not cleaned:
            continue
        projected = total_length + len(cleaned) + (1 if context_parts else 0)
        if projected > limit and context_parts:
            break
        context_parts.append(cleaned)
        total_length = projected
        if total_length >= limit:
            break

    if not context_parts:
        normalized = _normalize_filing_text(text)
        return _clean_snippet(normalized, limit=limit)
    return _clean_snippet(" ".join(context_parts), limit=limit)


def _document_hash(text: str) -> str:
    payload = _coerce_text(text)
    if not payload:
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EdgarClient:
    def __init__(
        self,
        *,
        user_agent: str | None = None,
        session: requests.Session | None = None,
        data_base_url: str | None = None,
        reference_base_url: str | None = None,
        archives_base_url: str | None = None,
        timeout_seconds: int | None = None,
        pause_seconds: float | None = None,
        max_context_chars: int | None = None,
    ) -> None:
        resolved_user_agent = (
            user_agent
            or os.getenv("SEC_USER_AGENT")
            or "spectral-nature attention-context research@example.com"
        ).strip()
        self.config = EdgarConfig(
            user_agent=resolved_user_agent,
            data_base_url=(data_base_url or os.getenv("SEC_DATA_BASE_URL") or "https://data.sec.gov").strip(),
            reference_base_url=(reference_base_url or os.getenv("SEC_REFERENCE_BASE_URL") or "https://www.sec.gov").strip(),
            archives_base_url=(archives_base_url or os.getenv("SEC_ARCHIVES_BASE_URL") or "https://www.sec.gov/Archives/edgar/data").strip(),
            timeout_seconds=max(int(timeout_seconds or int(os.getenv("SEC_TIMEOUT_SECONDS", "20"))), 5),
            pause_seconds=max(float(pause_seconds if pause_seconds is not None else os.getenv("SEC_PAUSE_SECONDS", "0.25")), 0.0),
            max_context_chars=max(
                int(max_context_chars or int(os.getenv("EDGAR_DOCUMENT_CONTEXT_CHARS", str(DEFAULT_CONTEXT_CHARS)))),
                500,
            ),
        )
        self.session = session or requests.Session()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.config.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json, text/plain, */*",
        }

    def _sleep(self) -> None:
        if self.config.pause_seconds > 0:
            time.sleep(self.config.pause_seconds)

    def _request_json(self, url: str) -> Any:
        response = self.session.get(url, headers=self._headers, timeout=self.config.timeout_seconds)
        if response.status_code != 200:
            raise EdgarAPIError(f"SEC request failed status={response.status_code} url={url}")
        self._sleep()
        try:
            return response.json()
        except Exception as exc:
            raise EdgarAPIError(f"SEC returned invalid JSON for {url}: {exc}") from exc

    def _request_text(self, url: str) -> str:
        response = self.session.get(url, headers=self._headers, timeout=self.config.timeout_seconds)
        if response.status_code != 200:
            raise EdgarAPIError(f"SEC document request failed status={response.status_code} url={url}")
        self._sleep()
        return response.text

    def load_company_reference(self) -> pd.DataFrame:
        payload = self._request_json(f"{self.config.reference_base_url}/files/company_tickers.json")
        rows: list[dict[str, object]] = []

        if isinstance(payload, dict):
            values = payload.values()
        elif isinstance(payload, list):
            values = payload
        else:
            values = []

        for entry in values:
            if not isinstance(entry, dict):
                continue
            symbol = str(entry.get("ticker") or "").upper().strip()
            company_name = _coerce_text(entry.get("title"))
            cik_raw = pd.to_numeric(entry.get("cik_str"), errors="coerce")
            if not symbol or pd.isna(cik_raw):
                continue
            cik = int(cik_raw)
            rows.append(
                {
                    "symbol": symbol,
                    "company_name": company_name,
                    "cik": cik,
                    "cik_padded": f"{cik:010d}",
                }
            )

        if not rows:
            return pd.DataFrame(columns=["symbol", "company_name", "cik", "cik_padded"])
        return pd.DataFrame(rows).drop_duplicates(subset=["symbol"]).sort_values("symbol").reset_index(drop=True)

    def load_recent_filings(
        self,
        symbols: list[str] | tuple[str, ...] | set[str],
        *,
        days: int = 120,
        forms: list[str] | tuple[str, ...] | set[str] | None = None,
        max_filings_per_symbol: int = 4,
        fetch_document_text: bool = True,
        max_document_fetches_per_symbol: int = 1,
        existing_frame: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        target_symbols = _normalize_symbols(symbols)
        if not target_symbols:
            return pd.DataFrame()

        reference = self.load_company_reference()
        if reference.empty:
            return pd.DataFrame()

        form_filter = {_normalize_form(form) for form in (forms or DEFAULT_EDGAR_FORMS) if _normalize_form(form)}
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=max(int(days), 1))
        rows: list[dict[str, object]] = []
        existing = existing_frame.copy() if isinstance(existing_frame, pd.DataFrame) else pd.DataFrame()
        if not existing.empty:
            for column in ["symbol", "accession_number", "filing_url", "document_text", "document_text_hash", "document_text_chars", "filing_excerpt"]:
                if column not in existing.columns:
                    existing[column] = ""
            existing["symbol"] = existing["symbol"].astype(str).str.upper().str.strip()
            existing["accession_number"] = existing["accession_number"].astype(str).str.strip()
            existing["filing_url"] = existing["filing_url"].astype(str).str.strip()
            existing = existing.sort_values(["symbol"], ascending=[True], na_position="last")

        for symbol in target_symbols:
            match = reference[reference["symbol"] == symbol]
            if match.empty:
                continue
            ref = match.iloc[0]
            cik = int(ref["cik"])
            payload = self._request_json(f"{self.config.data_base_url}/submissions/CIK{cik:010d}.json")
            recent = ((payload or {}).get("filings") or {}).get("recent") or {}
            frame = pd.DataFrame(recent)
            if frame.empty:
                continue

            frame["filing_date"] = pd.to_datetime(frame.get("filingDate"), utc=True, errors="coerce")
            frame["form"] = frame.get("form", pd.Series(dtype=object)).astype(str).str.upper().str.strip()
            frame = frame[frame["filing_date"].notna()].copy()
            if form_filter:
                frame = frame[frame["form"].isin(form_filter)].copy()
            frame = frame[frame["filing_date"] >= cutoff].copy()
            if frame.empty:
                continue

            frame = frame.sort_values("filing_date", ascending=False, na_position="last").head(max(int(max_filings_per_symbol), 1))
            fetched_documents = 0

            for _, filing in frame.iterrows():
                accession_number = _coerce_text(filing.get("accessionNumber"))
                accession_nodash = accession_number.replace("-", "")
                primary_document = _coerce_text(filing.get("primaryDocument"))
                filing_url = ""
                if accession_nodash:
                    if primary_document:
                        filing_url = f"{self.config.archives_base_url}/{cik}/{accession_nodash}/{primary_document}"
                    else:
                        filing_url = f"{self.config.archives_base_url}/{cik}/{accession_nodash}/{accession_number}-index.html"

                items = filing.get("items")
                if isinstance(items, list):
                    items_text = ", ".join(str(item).strip() for item in items if str(item).strip())
                else:
                    items_text = _coerce_text(items)

                filing_excerpt = ""
                document_text = ""
                document_text_hash = ""
                document_text_chars = 0
                existing_match = pd.DataFrame()
                if not existing.empty:
                    if accession_number:
                        existing_match = existing[(existing["symbol"] == symbol) & (existing["accession_number"] == accession_number)].head(1)
                    if existing_match.empty and filing_url:
                        existing_match = existing[(existing["symbol"] == symbol) & (existing["filing_url"] == filing_url)].head(1)
                if not existing_match.empty:
                    prior = existing_match.iloc[0]
                    filing_excerpt = _coerce_text(prior.get("filing_excerpt"))
                    document_text = _coerce_text(prior.get("document_text"))
                    document_text_hash = _coerce_text(prior.get("document_text_hash"))
                    try:
                        document_text_chars = int(pd.to_numeric(prior.get("document_text_chars"), errors="coerce") or 0)
                    except Exception:
                        document_text_chars = len(document_text)

                if (
                    fetch_document_text
                    and filing_url
                    and _textish_document(primary_document)
                    and not document_text
                    and fetched_documents < max(int(max_document_fetches_per_symbol), 0)
                ):
                    try:
                        raw_text = self._request_text(filing_url)
                        document_text = _meaningful_filing_context(
                            _coerce_text(filing.get("form")).upper(),
                            items_text,
                            raw_text,
                            limit=self.config.max_context_chars,
                        )
                        filing_excerpt = _meaningful_filing_excerpt(
                            _coerce_text(filing.get("form")).upper(),
                            items_text,
                            raw_text,
                            limit=self.config.max_excerpt_chars,
                        )
                        document_text_hash = _document_hash(document_text)
                        document_text_chars = len(document_text)
                        fetched_documents += 1
                    except EdgarAPIError:
                        filing_excerpt = ""
                        document_text = ""
                        document_text_hash = ""
                        document_text_chars = 0
                elif document_text and not document_text_hash:
                    document_text_hash = _document_hash(document_text)
                    document_text_chars = len(document_text)

                rows.append(
                    {
                        "symbol": symbol,
                        "company_name": _coerce_text(ref.get("company_name")),
                        "cik": cik,
                        "cik_padded": f"{cik:010d}",
                        "filing_date": filing.get("filing_date"),
                        "form": _coerce_text(filing.get("form")).upper(),
                        "accession_number": accession_number,
                        "primary_document": primary_document,
                        "primary_doc_description": _coerce_text(filing.get("primaryDocDescription")),
                        "items": items_text,
                        "is_xbrl": filing.get("isXBRL"),
                        "is_inline_xbrl": filing.get("isInlineXBRL"),
                        "filing_url": filing_url,
                        "filing_excerpt": filing_excerpt,
                        "document_text": document_text,
                        "document_text_hash": document_text_hash,
                        "document_text_chars": document_text_chars,
                    }
                )

        if not rows:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "company_name",
                    "cik",
                    "cik_padded",
                    "filing_date",
                    "form",
                    "accession_number",
                    "primary_document",
                    "primary_doc_description",
                    "items",
                    "is_xbrl",
                    "is_inline_xbrl",
                    "filing_url",
                    "filing_excerpt",
                    "document_text",
                    "document_text_hash",
                    "document_text_chars",
                ]
            )

        out = pd.DataFrame(rows)
        out["filing_date"] = pd.to_datetime(out["filing_date"], utc=True, errors="coerce")
        return out.sort_values(["symbol", "filing_date"], ascending=[True, False], na_position="last").reset_index(drop=True)


def build_attention_context_bundle(
    attention_frame: pd.DataFrame,
    filings_frame: pd.DataFrame,
    *,
    asof_time_utc: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if attention_frame is None or attention_frame.empty or "entity_id" not in attention_frame.columns:
        return pd.DataFrame(
            columns=[
                "symbol",
                "company_name",
                "cik",
                "latest_filing_date",
                "latest_form",
                "latest_items",
                "filing_count_lookback",
                "context_story_text",
                "primary_source_excerpt",
                "source_line",
                "latest_filing_excerpt",
                "top_filing_links_json",
                "asof_time_utc",
                "schema_version",
            ]
        )

    seed = attention_frame.copy()
    if "entity_type" in seed.columns:
        entity_type = seed["entity_type"].astype(str).str.lower()
        seed = seed[entity_type.eq("symbol") | entity_type.eq("")].copy()
    if seed.empty:
        return pd.DataFrame()

    seed["entity_id"] = seed["entity_id"].astype(str).str.upper().str.strip()
    if "attention_score" in seed.columns:
        seed["attention_score"] = pd.to_numeric(seed["attention_score"], errors="coerce")
        seed = seed.sort_values("attention_score", ascending=False, na_position="last")
    seed = seed.drop_duplicates(subset=["entity_id"]).reset_index(drop=True)

    filings = filings_frame.copy() if isinstance(filings_frame, pd.DataFrame) else pd.DataFrame()
    if not filings.empty:
        filings["symbol"] = filings["symbol"].astype(str).str.upper().str.strip()
        filings["filing_date"] = pd.to_datetime(filings.get("filing_date"), utc=True, errors="coerce")
        filings = filings.sort_values(["symbol", "filing_date"], ascending=[True, False], na_position="last")

    asof = _coerce_timestamp(asof_time_utc if asof_time_utc is not None else datetime.now(timezone.utc))
    rows: list[dict[str, object]] = []

    for symbol in seed["entity_id"].tolist():
        symbol_filings = filings[filings["symbol"] == symbol].copy() if not filings.empty else pd.DataFrame()
        if symbol_filings.empty:
            continue

        latest = symbol_filings.iloc[0]
        filing_links = []
        for _, filing in symbol_filings.head(3).iterrows():
            filing_date = _coerce_timestamp(filing.get("filing_date"))
            filing_links.append(
                {
                    "label": f"{_coerce_text(filing.get('form')) or 'Filing'} • {filing_date.strftime('%b %d') if pd.notna(filing_date) else 'Recent'}",
                    "url": _coerce_text(filing.get("filing_url")),
                }
            )

        recent_forms = symbol_filings["form"].dropna().astype(str).tolist()
        latest_form = _coerce_text(latest.get("form")).upper()
        latest_items = _coerce_text(latest.get("items"))
        latest_desc = _coerce_text(latest.get("primary_doc_description"))
        latest_date = _coerce_timestamp(latest.get("filing_date"))
        latest_excerpt = _clean_snippet(latest.get("filing_excerpt"), limit=DEFAULT_EXCERPT_CHARS)

        lead = f"{symbol} filed a {latest_form or 'recent SEC update'}"
        if pd.notna(latest_date):
            lead += f" on {latest_date.strftime('%b %d')}"
        if latest_items:
            lead += f" covering {latest_items}"
        elif latest_desc:
            lead += f" focused on {latest_desc.lower()}"
        else:
            lead += f", which usually flags a {_form_label(latest_form).lower()} update"
        lead += "."

        filing_count = int(len(symbol_filings))
        forms_text = _recent_form_list(recent_forms)
        follow = ""
        if filing_count > 1 and forms_text:
            follow = f" Over the current lookback, primary-source context clusters around {forms_text} across {filing_count} filing(s)."
        elif forms_text:
            follow = f" Primary-source context is currently centered on {forms_text}."

        excerpt = latest_excerpt or latest_desc
        if latest_items:
            if excerpt and latest_excerpt:
                excerpt = f"{excerpt} Item focus: {latest_items}."
            elif excerpt:
                excerpt = f"{excerpt}; items: {latest_items}"
            else:
                excerpt = f"Items: {latest_items}"

        if latest_excerpt:
            follow += " The filing text itself gives a more concrete read on what management is emphasizing."

        rows.append(
            {
                "symbol": symbol,
                "company_name": _coerce_text(latest.get("company_name")),
                "cik": int(pd.to_numeric(latest.get("cik"), errors="coerce")) if pd.notna(pd.to_numeric(latest.get("cik"), errors="coerce")) else None,
                "latest_filing_date": latest_date,
                "latest_form": latest_form,
                "latest_items": latest_items,
                "filing_count_lookback": filing_count,
                "context_story_text": lead + follow,
                "primary_source_excerpt": excerpt,
                "source_line": "Primary sources: SEC EDGAR filings",
                "latest_filing_excerpt": latest_excerpt,
                "top_filing_links_json": json.dumps([link for link in filing_links if link.get("url")], ensure_ascii=False),
                "asof_time_utc": asof,
                "schema_version": "v1",
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "symbol",
                "company_name",
                "cik",
                "latest_filing_date",
                "latest_form",
                "latest_items",
                "filing_count_lookback",
                "context_story_text",
                "primary_source_excerpt",
                "source_line",
                "latest_filing_excerpt",
                "top_filing_links_json",
                "asof_time_utc",
                "schema_version",
            ]
        )

    return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)


__all__ = [
    "DEFAULT_EDGAR_FORMS",
    "EdgarAPIError",
    "EdgarClient",
    "build_attention_context_bundle",
]
