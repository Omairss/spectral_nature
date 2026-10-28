from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from .json_utils import to_jsonable


RESEARCH_TIMELINE_GUIDANCE = (
    "Distinguish when an event happened, when a source reported it, and when we observed it. "
    "A newly published recap does not make its underlying event new. When older developments matter to the "
    "current briefing, date them and explain the current continuation or reaction. Do not imply a statement "
    "was repeated today unless a source supports a new statement. Apply this chronology to titles and summaries "
    "as well as the body: lead with the current observed "
    "development, and date an older cause or statement when it is material. Attribute the assessment to the "
    "speaker rather than presenting it as an independently established market fact. An article's publication "
    "or capture date alone does not establish an event date. Preserve explicit historical as-of constraints."
    " For a current scheduled briefing, assess the observations supplied for this run first. Previous investigations "
    "are dated background, not evidence that a development happened again. Produce the best useful current answer "
    "within the available resources; do not carry an unfinished news story into a future run as a new event."
    " A market observation is a snapshot with a measurement time and a source period. A provider field named close "
    "can be the latest value of a still-updating bar; the field name or bar's date alone does not establish a final "
    "session close. Read the actual observation time, as-of constraint and any session-finality information together. "
    "Describe an intraday or unfinalized value as the observed/traded price at that time, and reserve closing-price "
    "claims for evidence of a completed session."
)


def render_research_prompt(*, evidence: Any, request: Any, instructions: str) -> str:
    """Stable complete evidence precedes the changing task and question.

    Provider caching matches prefixes, not repeated text in the middle. This
    representation retains every supplied value and lets roles share evidence.
    """
    return ("Evidence JSON:\n" + serialize_llm_context(evidence)
            + "\n\nRequest JSON:\n" + serialize_llm_context({
                "execution_date_utc": datetime.now(timezone.utc).date().isoformat(),
                "task": request,
            })
            + "\n\nTimeline: " + RESEARCH_TIMELINE_GUIDANCE + "\n\n"
            + instructions)


def organize_source_captures(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One document identity with complete captures, not semantic filtering.

    Repeated searches often return the same document. Keep every capture,
    passage, query role and date while naming the document once for inference.
    """
    documents: dict[str, dict[str, Any]] = {}
    for record in records:
        url = str(record.get("url") or "").strip()
        identity = url or json.dumps(to_jsonable(record), sort_keys=True, default=str)
        key = "source_" + hashlib.sha256(identity.encode()).hexdigest()
        documents.setdefault(key, {"evidence_ref": key, "url": url, "captures": []})["captures"].append(record)
    return [documents[key] for key in sorted(documents)]


def serialize_llm_context(value: Any, *, append_only: bool = False) -> str:
    """Present every context value once, with references for exact repetitions.

    Research captures often contain JSON inside JSON and repeat the same source
    across investigations. This is a model input representation, not a storage
    transformation or a relevance filter. No records or source text are dropped.
    """
    counts: Counter[str] = Counter()

    def normalize(item: Any) -> Any:
        if isinstance(item, str) and item.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(item)
            except (ValueError, TypeError):
                pass
            else:
                if isinstance(decoded, (dict, list)):
                    return {"json_text": normalize(decoded)}
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        return item

    def encoded(item: Any) -> str:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def count(item: Any) -> None:
        if isinstance(item, (dict, list, str)):
            text = encoded(item)
            if len(text) >= 384:
                counts[text] += 1
        if isinstance(item, dict):
            for child in item.values():
                count(child)
        elif isinstance(item, list):
            for child in item:
                count(child)

    context = normalize(to_jsonable(value))
    if append_only:
        if not isinstance(context, list):
            raise TypeError("Append-only research context requires ordered records")
        defined: set[str] = set()

        def append_value(item: Any) -> Any:
            text = encoded(item) if isinstance(item, (dict, list, str)) else ""
            identity = "ctx_" + hashlib.sha256(text.encode()).hexdigest() if len(text) >= 384 else ""
            if identity and identity in defined:
                return {"context_ref": identity}
            if identity:
                defined.add(identity)
            if isinstance(item, dict):
                result = {key: append_value(item[key]) for key in sorted(item)}
            elif isinstance(item, list):
                result = [append_value(child) for child in item]
            else:
                result = item
            return {"context_id": identity, "value": result} if identity else result

        return ("Ordered research evidence records. Each context_ref resolves to the complete value of an earlier "
                "context_id. All passages, dates and records are retained; repeated content is not corroboration.\n"
                + "\n".join(encoded(append_value(record)) for record in context))
    count(context)
    shared: dict[str, Any] = {}

    def factor(item: Any) -> Any:
        text = encoded(item) if isinstance(item, (dict, list, str)) else ""
        if counts.get(text, 0) > 1:
            key = "ctx_" + hashlib.sha256(text.encode()).hexdigest()[:20]
            if key not in shared:
                shared[key] = children(item)
            return {"context_ref": key}
        return children(item)

    def children(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: factor(child) for key, child in item.items()}
        if isinstance(item, list):
            return [factor(child) for child in item]
        return item

    data = factor(context)
    if not shared:
        return encoded(data)
    return encoded({
        "context_format": (
            "context_ref points to the complete value in shared_context. Resolve references when reading. "
            "json_text contains decoded JSON from an original text field. All records and text are retained; "
            "references express identical content, not independent corroboration."
        ),
        "data": data,
        "shared_context": shared,
    })
