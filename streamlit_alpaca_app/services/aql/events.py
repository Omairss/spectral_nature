"""One market research agenda and focused investigations through AQL/Zopedia."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from contextvars import copy_context
import hashlib
import json
import os
import time
from typing import Any, Callable

import pandas as pd
from jsonschema import Draft202012Validator
from data_access.query_service import QueryService

from ..aql_zopedia_engine import run_aql_zopedia_structured_agent
from ..llm_context import render_research_prompt
from ..llm_execution import (latest_research_artifact, event_research_context,
                            event_investigation_capacity, record_execution_issue, model_call_context,
                            protect_core_research, complete_core_research)
from ..work_scope import WorkCancelled
from ..common.news_freshness import coerce_article_published_at
from ..json_utils import to_jsonable
from .. import pipeline_store
from ..saa.storage import build_canonical_document_fields
from ..llm import (
    get_config_param,
    register_config_param,
    register_narrative_prompt,
    get_prompt,
    llm_settings_scope,
)
from services.common.market_activity import (
    _coerce_text,
    _normalize_symbol,
    _safe_list,
)
from .evidence_index import parse_json_list
from .evidence_pack import materialize_research_artifacts

_P_EVENT_LIMIT = register_config_param(
    "Source event investigation limit",
    group="AQL / Zopedia Engine",
    default=-1,
    description="-1 lets research usefulness, the run budget and original deadline set breadth; 0 disables source-event research; a positive value sets a base ceiling, with the configured event margin.",
)
_P_EVENT_TIMEOUT = register_config_param(
    "Source event run timeout seconds",
    group="AQL / Zopedia Engine",
    default=1200,
    description="Whole execution window for each question selected by the common Home research agenda.",
)
_P_NOMINATION_TIMEOUT = register_config_param(
    "Source event nomination timeout seconds",
    group="AQL / Zopedia Engine",
    default=1800,
    description="Execution window for the common Home planner over numerical groups, economic observations, headlines and retained findings.",
)
_P_DISCOVERY_TOOLS = register_config_param(
    "Market-wide discovery tool budget",
    group="AQL / Zopedia Engine",
    default=6,
    description="Shared research tools available while surveying the current market agenda, before choosing focused event investigations.",
)
_P_EVENT_TOOLS = register_config_param(
    "Source event tool budget",
    group="AQL / Zopedia Engine",
    default=6,
    description="Shared acquisition budget per selected Home investigation.",
)
_P_EVENT_WORKERS = register_config_param(
    "Source event maximum workers",
    group="AQL / Zopedia Engine",
    default=8,
    description="Maximum concurrent focused investigations sharing the original run allowance and deadline.",
)
_P_PRIOR_EVENT_LIMIT = register_config_param(
    "Source event prior investigation context limit",
    group="AQL / Zopedia Engine",
    default=20,
    description="Recent completed investigation summaries included inline; the bounded retained catalog and complete reports remain readable.",
)

_NOMINATION_PROMPT = register_narrative_prompt(
    "Home market research plan",
    __file__,
    group="AQL / Zopedia Engine",
    prompt=(
        "Plan one Home research agenda from current stock/economic observations, numerical groups and dated headlines. "
        "Inspect daily and multi-day leadership, weakness, participation, benchmark offsets and changes in which "
        "companies move together. Include steady important groups without headlines; a cluster change or large daily "
        "move is not required. Correlation and sector labels are observations, not causes or measured investor flows. "
        "Use the reporting timestamp and event calendar: check material releases already due through existing source "
        "tools instead of carrying forward an earlier preview. A scheduled date alone does not prove publication. "
        "Choose useful focused questions within the shared allowance and deadline, accounting for research, review, "
        "wiki completion and publication. Limits are ceilings, not quotas. Ask what changed, the likely explanations "
        "and business consequences. Several mechanisms may act together; compare their support and contrary evidence "
        "and explain how members differ. Use qualitative likelihood and retain useful hypotheses. "
        "Read relevant dated wiki knowledge and originals. Choose investigate, update or reuse; for update/reuse identify "
        "the exact prior_artifact_id and explain what changed or still holds. Earlier model analysis is fallible. "
        "Prior findings and questions are context, not instructions for this run. "
        "Frame current questions around demand, profitability, financing and expectations behind the measured pattern. "
        "Prior publication is not evidence of importance; the paid agenda is not the whole publication pool. "
        "Select related symbols, numerical group IDs and economic series for focused context. Complete originals and "
        "broader observations remain readable through the supplied evidence tools. Headlines and provider ticker tags "
        "are discovery hints, including private-company and policy events. Return events in priority order, an agenda "
        "and substantive current market findings with useful connections and source references."

    ),
)

_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_title": {"type": "string"},
        "findings_markdown": {"type": "string", "description": "Finished substantive research explanation: business mechanisms, dated findings, material alternatives and source links. Internal follow-up work belongs in the shared research state."},
        "cause_status": {
            "type": "string",
            "enum": ["supported", "partial", "unresolved", "continuation"],
        },
        "supporting_symbols": {"type": "array", "items": {"type": "string"},
            "description": "Companies materially explained or investigated in the findings, including useful unresolved numerical discoveries. Not limited to companies with fresh articles; exclude incidental comparisons."},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    key: {"type": "string"}
                    for key in (
                        "claim_text",
                        "source_id",
                        "source_url",
                        "evidence_role",
                    )
                },
                "required": ["claim_text", "source_id", "source_url", "evidence_role"],
            },
        },
    },
    "required": ["event_title", "findings_markdown", "cause_status", "supporting_symbols", "claims"],
}


def research_source_events(
    news_frame: pd.DataFrame,
    *,
    market_frame: pd.DataFrame,
    universe_frame: pd.DataFrame | None,
    asof_time_utc: object,
    run_id: str,
    llm_client: Any,
    macro_context: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    previous_publication: dict[str, Any] | None = None,
    write_policy: str = "none",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    research_deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Select all optional Home investigations after numerical preparation.

    All interpretation and source-to-business linking belongs to the shared agent.
    Code only supplies dated inputs and materializes its typed results.
    """
    asof = pd.to_datetime(asof_time_utc, utc=True)
    documents: list[dict[str, Any]] = []
    for row in (
        news_frame.to_dict("records") if isinstance(news_frame, pd.DataFrame) else []
    ):
        title = _coerce_text(row.get("headline") or row.get("title"))
        url = _coerce_text(row.get("url"))
        published = coerce_article_published_at(
            row.get("published_at"), url=url, asof_time_utc=asof
        )
        if not title:
            continue
        identity = url or title
        source_id = (
            "doc::event-news::" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        )
        text = (
            _coerce_text(row.get("content"))
            or _coerce_text(row.get("raw_text") or row.get("provider_text"))
            or _coerce_text(row.get("summary") or row.get("description"))
            or title
        )
        documents.append(
            {
                "run_id": run_id,
                "asof_time_utc": asof,
                "document_id": source_id,
                "source_id": source_id,
                "bundle_subject": "",
                "candidate_id": "",
                "title": title,
                "url": url,
                "published_at": published,
                "raw_text": text,
                "text": text,
                "source_kind": _coerce_text(row.get("source_kind")) or "news",
                "raw_text_origin": _coerce_text(row.get("raw_text_origin")) or "feed_record",
                "provider_payload_json": _coerce_text(row.get("provider_payload_json")) or json.dumps(to_jsonable(row), ensure_ascii=False),
                "source_provider": _coerce_text(row.get("source")),
                "provider_symbols": parse_json_list(row.get("symbols"))
                if isinstance(row.get("symbols"), str)
                else _safe_list(row.get("symbols")),
                "source_trace": _coerce_text(row.get("source_trace")) or json.dumps({
                    "capture_kind": "feed_record", "provider": row.get("source"),
                    "reported_publication_date": str(row.get("published_at") or ""),
                }),
            }
        )
    # The previous publication carries its original captures, not new reporting.
    # Keep them in the same reader so a narrower intake cannot erase known facts.
    for story in (previous_publication or {}).get("stories", []):
        for capture in story.get("evidence") or []:
            text = capture.get("raw_text") or capture.get("source_text") or ""
            if not text:
                continue
            doc = {**capture, "title": capture.get("title") or capture.get("headline") or "",
                "raw_text": text, "text": text, "source_kind": capture.get("source_kind") or "news",
                "source_provider": capture.get("source_provider") or capture.get("source") or "",
                "published_at": capture.get("published_at"), "url": capture.get("url") or "",
                "raw_text_origin": capture.get("raw_text_origin") or "retained_reference"}
            doc.update(build_canonical_document_fields(doc))
            if capture.get("canonical_document_id"):
                doc["canonical_document_id"] = capture["canonical_document_id"]
            doc["source_id"] = doc["document_id"] = doc["canonical_document_id"]
            documents.append(doc)
    documents = list({doc["document_id"]: doc for doc in documents}.values())
    # Keep original provider captures in the returned documents, while each
    # model receives source text once alongside its identity and provenance.
    model_source_fields = (
        "source_id", "document_id", "title", "url", "published_at", "text",
        "source_kind", "raw_text_origin", "source_provider", "provider_symbols", "source_trace",
    )
    access = os.getenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", "")
    previous_nomination = None
    prior_investigations = []
    retained = []
    context_status = "available" if access else "unconfigured"
    try:
        previous_nomination = latest_research_artifact(access_scope=access,
            subject="attention:source_event_nomination", task="source_event_nomination") if access else None
        # Read completed work independently of the last nomination. A quiet
        # nomination with zero new events must not erase the research context.
        # The inline history limit controls prompt size, not which completed
        # reports remain readable. Use the store's bounded catalog for readers.
        retained = pipeline_store.list_research_artifacts(
            namespace=f"research_artifact:{llm_settings_scope()}:{access}", task="source_event_research",
            limit=100) if access else []
        retained = [artifact for artifact in retained
                    if all(pd.notna(date) and date <= asof for date in (
                        pd.to_datetime(artifact.get("created_at_utc"), utc=True, errors="coerce"),
                        pd.to_datetime(((artifact.get("result") or {}).get("artifact_context") or {}).get("asof_time_utc")
                                       or artifact.get("created_at_utc"), utc=True, errors="coerce")))]
        if previous_nomination and not all(pd.notna(date) and date <= asof for date in (
            pd.to_datetime(previous_nomination.get("created_at_utc"), utc=True, errors="coerce"),
            pd.to_datetime((previous_nomination.get("artifact_context") or {}).get("asof_time_utc")
                           or previous_nomination.get("created_at_utc"), utc=True, errors="coerce"))):
            previous_nomination = None
        prior_investigations = [
            {key: artifact.get(key) for key in ("artifact_id", "subject", "task", "created_at_utc")}
            | {"source_id": "retained-research:" + artifact["artifact_id"],
               "research_asof_time_utc": ((artifact.get("result") or {}).get("artifact_context") or {}).get("asof_time_utc"),
               "research_summary": {key: ((artifact.get("result") or {}).get("payload") or {}).get(key)
                                    for key in ("event_title", "supporting_symbols")}}
            for artifact in retained[:max(int(get_config_param(_P_PRIOR_EVENT_LIMIT)), 1)]
        ]
    except WorkCancelled:
        raise
    except Exception as exc:
        context_status = "unavailable"
        record_execution_issue("event_allocation_history", exc)
    empty = {
        "bundles": [],
        "plans": [],
        "results": [],
        "documents": documents,
        "input_count": len(documents),
        "market_research": [],
        "retained_research": retained,
        "status": "no_sources",
    }
    if llm_client is None:
        return {
            **empty,
            "status": "unavailable" if llm_client is None else "no_sources",
        }
    limit = event_investigation_capacity(int(get_config_param(_P_EVENT_LIMIT)))
    if limit < -1:
        raise ValueError("Source event investigation limit must be -1, zero, or positive.")
    if limit == 0:
        return {**empty, "status": "disabled"}
    market = market_frame.copy()
    if (
        isinstance(universe_frame, pd.DataFrame)
        and "symbol" in universe_frame
        and "symbol" in market
    ):
        identity_cols = [
            key
            for key in (
                "symbol",
                "security_name",
                "company_name",
                "name",
                "sector",
                "industry",
            )
            if key in universe_frame and (key == "symbol" or key not in market)
        ]
        market = market.merge(
            universe_frame[identity_cols].drop_duplicates("symbol"),
            on="symbol",
            how="left",
        )
    observations = to_jsonable(market.to_dict("records"))
    nomination_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "research_agenda": {"type": "array", "items": {"type": "string"}},
            "findings_markdown": {"type": "string"},
            "events": {
                "type": "array",
                **({"maxItems": limit} if limit > 0 else {}),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "question": {"type": "string"},
                        "source_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "related_symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "followup_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "rationale": {"type": "string"},
                        "research_action": {"type": "string", "enum": ["investigate", "update", "reuse"]},
                        "prior_artifact_id": {"type": "string"},
                        "group_ids": {"type": "array", "items": {"type": "string"}},
                        "economic_series_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "title",
                        "question",
                        "source_ids",
                        "related_symbols",
                        "followup_questions",
                        "rationale", "research_action", "prior_artifact_id", "group_ids", "economic_series_ids",
                    ],
                },
            }
        },
        "required": ["research_agenda", "findings_markdown", "events"],
    }
    previews = [
        {
            key: doc.get(key)
            for key in ("source_id", "title", "url", "published_at", "provider_symbols", "source_trace", "raw_text_origin")
        }
        for doc in documents
    ]
    source_records = {doc["source_id"]: {"title": doc["title"],
        "metadata": {"kind": "source_document", "url": doc["url"], "published_at": doc["published_at"],
                     "provider_symbols": doc.get("provider_symbols", [])},
        "content": {key: doc.get(key) for key in model_source_fields}} for doc in documents}
    source_records.update({"market-observations": {"title": "Underlying current market observations",
        "metadata": {"asof_time_utc": asof.isoformat()}, "content": to_jsonable(market.to_dict("records"))},
        "market-groups": {"title": "Market-wide numerical context and groups", "metadata": {}, "content": to_jsonable(market_context or {})},
        "economic-observations": {"title": "Dated economic observations", "metadata": {}, "content": to_jsonable(macro_context or {})},
        "previous-publication": {"title": "Previous publication with original dated sources", "metadata": {},
                                 "content": to_jsonable(previous_publication or {})}})
    source_records.update({"market-calendar:" + day["id"]: {
        "title": "Scheduled and reported market events on " + day["id"],
        "metadata": {"date": day["id"], "timezone": "America/New_York", "kind": "event_calendar"},
        "content": day,
    } for day in ((macro_context or {}).get("market_calendar") or {}).get("days", []) if day.get("events")})
    for artifact in retained:
        retained_result = artifact.get("result") or {}
        retained_context = retained_result.get("artifact_context") or {}
        original_documents = [*(retained_context.get("source_documents") or []),
            *materialize_research_artifacts(retained_result.get("agent_result") or {},
                candidate_id="retained-research:" + artifact["artifact_id"], subject=artifact["subject"],
                run_id=(retained_result.get("agent_result") or {}).get("run_id") or artifact["artifact_id"],
                asof_time_utc=retained_context.get("asof_time_utc") or artifact["created_at_utc"])["documents"]]
        source_records["retained-research:" + artifact["artifact_id"]] = {
            "title": (retained_result.get("payload") or {}).get("event_title") or artifact["subject"],
            "read_pointer": "/payload",
            "metadata": {"kind": "prior_model_analysis", "created_at_utc": artifact["created_at_utc"],
                         "research_asof_time_utc": retained_context.get("asof_time_utc"),
                         "source_documents_pointer": "/source_documents", "source_document_count": len(original_documents)},
            # Findings and original captures are evidence; execution and prior
            # editorial judgments remain in the unchanged stored artifact.
            "content": to_jsonable({
                **{key: artifact.get(key) for key in ("artifact_id", "subject", "task", "created_at_utc")},
                "research_asof_time_utc": retained_context.get("asof_time_utc"),
                "question": retained_context.get("question"), "prior_artifact_id": retained_context.get("prior_artifact_id"),
                "payload": {key: value for key, value in (retained_result.get("payload") or {}).items()
                            if key != "cause_status"},
                "source_documents": original_documents,
            })}
    service = QueryService.from_environment().with_supplied_evidence(source_records)
    nomination_timeout = max(int(get_config_param(_P_NOMINATION_TIMEOUT)), 1)
    if research_deadline_monotonic is not None:
        nomination_timeout = min(nomination_timeout, research_deadline_monotonic - time.monotonic())
    if nomination_timeout <= 0:
        return {**empty, "status": "partial", "results": [{
            "run_id": run_id, "status": "cancelled", "error": "Home research deadline reached before nomination.",
        }]}
    nomination = run_aql_zopedia_structured_agent(
        query=get_prompt(_NOMINATION_PROMPT)
        + "\nContext JSON:\n"
        + json.dumps(
            to_jsonable(
                {"asof": asof, "market_context": market_context or {},
                 "sources": previews,
                 "economic_observations": {key: value for key, value in (macro_context or {}).items() if key != "market_calendar"},
                 "event_calendar": [{"date": day["id"], "timezone": "America/New_York", **{key: event.get(key) for key in
                     ("id", "name", "ticker", "type", "time", "timing_label", "confirmation_status", "source_as_of")}}
                     for day in ((macro_context or {}).get("market_calendar") or {}).get("days", [])
                     for event in day.get("events", [])
                     if day["id"] in {asof.tz_convert("America/New_York").date().isoformat(),
                         ((market_context or {}).get("benchmark") or {}).get("history_through")} or not event.get("ticker")],
                 "calendar_catalog": [{"date": day["id"], "event_count": len(day["events"]),
                     "source_id": "market-calendar:" + day["id"]}
                     for day in ((macro_context or {}).get("market_calendar") or {}).get("days", []) if day.get("events")],
                 "previous_market_context": {key: value for key, value in (((previous_nomination or {}).get("artifact_context") or {}).get("market_context") or {}).items()
                                             if key not in {"observations", "columns", "previous_groups"}},
                 "previous_publication": {"asof_time_utc": (previous_publication or {}).get("asof_time_utc"),
                    "stories": [{key: story.get(key) for key in ("headline", "event_title", "supporting_symbols")}
                                for story in (previous_publication or {}).get("stories", [])]},
                 "evidence_manifest": service.supplied_evidence_manifest,
                 "completed_research_context_status": context_status,
                 "dated_completed_investigations": prior_investigations}
            ),
            ensure_ascii=False,
        ),
        service=service,
        initial_evidence=[{"evidence_manifest": service.supplied_evidence_manifest}],
        schema_name="source_event_nominations",
        schema=nomination_schema,
        task="source_event_nomination",
        surface="aql.source_event_nomination",
        llm_client=llm_client,
        max_tool_calls=max(int(get_config_param(_P_DISCOVERY_TOOLS)), 0),
        run_timeout_seconds=nomination_timeout,
        llm_step_timeout_seconds=max(int(get_config_param(_P_NOMINATION_TIMEOUT)), 1),
        persist_findings=False,
        write_policy="none",
        call_type="research_grade",
        # A same-execution retry reuses the paid discovery, rather than adding
        # its own newly completed investigations to a different request identity.
        reuse_identity=render_research_prompt(evidence={"sources": documents,
            "market_context": market_context or {}, "economic_observations": macro_context or {}},
            request={"asof": asof}, instructions=get_prompt(_NOMINATION_PROMPT)),
        reuse_scope=access, research_subject="attention:source_event_nomination",
        artifact_context={"asof_time_utc": asof.isoformat(), "market_context": market_context or {}},
    )
    discovery_agent = nomination.get("agent_result") or {}
    discovery = materialize_research_artifacts(discovery_agent, candidate_id="market-discovery",
        subject="market", run_id=run_id, asof_time_utc=asof)
    original_by_url = {doc["url"]: doc["source_id"] for doc in documents if doc.get("url")}
    for captured in discovery["documents"]:
        identity = captured.get("url") or captured.get("canonical_document_id") or captured["document_id"]
        documents.append({**captured, "text": captured["raw_text"],
            "source_id": original_by_url.get(captured.get("url"))
                or "doc::market-discovery::" + hashlib.sha256(identity.encode()).hexdigest()[:20]})
    payload = nomination.get("payload")
    market_research = [{"run_id": run_id, "asof_time_utc": asof,
        "status": nomination.get("status"), "agent_run_id": discovery_agent.get("run_id", ""),
        "market_context_text": payload.get("findings_markdown", "") if isinstance(payload, dict) else "",
        "research_agenda_json": json.dumps(payload.get("research_agenda", []) if isinstance(payload, dict) else []),
        "market_context_json": json.dumps(to_jsonable(market_context or {}), ensure_ascii=False),
        "error": nomination.get("error", ""),
        "tool_failure_count": sum(call.get("status") in {"failed", "timeout", "cancelled"}
                                  for call in discovery_agent.get("tool_calls", [])),
        "review_status": (discovery_agent.get("quality_review") or {}).get("status", ""),
        "acquired_document_count": len(discovery["documents"]),
        "result_json": json.dumps(to_jsonable(nomination), ensure_ascii=False)}]
    if not isinstance(payload, dict) or list(
        Draft202012Validator(nomination_schema).iter_errors(payload)
    ):
        return {
            **empty,
            "documents": documents,
            "market_research": market_research,
            "status": "failed",
            "results": [
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error": nomination.get("error") or "Invalid nomination contract",
                }
            ],
        }
    # Keep the retained model response immutable when adapting source references.
    plans = [dict(plan) for plan in payload["events"]]
    by_id = {reference: doc for doc in documents
             for reference in (doc["source_id"], doc["document_id"]) if reference}
    retained_by_id = {artifact["artifact_id"]: artifact for artifact in retained}
    for plan in plans:
        prior = retained_by_id.get(plan["prior_artifact_id"])
        if plan["research_action"] in {"reuse", "update"} and prior is None:
            record_execution_issue("market_research_continuity", ValueError("Selected prior research is unavailable; investigating the question afresh"))
            plan["research_action"] = "investigate"
        plan["source_refs"] = list(plan["source_ids"])
        # A URL can name several captures. Supply every original rather than
        # silently replacing a current capture with an earlier version.
        matches = {ref: [by_id[ref]] if ref in by_id else [doc for doc in documents if doc.get("url") == ref]
                   for ref in plan["source_refs"]}
        resolved = [doc["source_id"] for captures in matches.values() for doc in captures]
        plan["unresolved_source_refs"] = [ref for ref, captures in matches.items() if not captures]
        plan["source_ids"] = list(dict.fromkeys(resolved))
        # A nominated market observation is evidence for investigating an
        # unexplained move; it is not evidence of the move's eventual cause.
        # Preserve that existing input at the financial authorization boundary.
        related = {_normalize_symbol(symbol) for symbol in plan["related_symbols"]}
        related.update(symbol for group in (market_context or {}).get("groups", [])
                       if group["id"] in plan["group_ids"] for symbol in group["members"])
        plan["related_symbols"] = sorted(related)
        plan["observation_refs"] = [
            {"evidence_id": "observation::" + hashlib.sha256(
                json.dumps({"asof": asof.isoformat(), "observation": row}, sort_keys=True).encode()
            ).hexdigest()[:20], "asof_time_utc": asof.isoformat(), "observation": row}
            for row in observations if _normalize_symbol(row.get("symbol")) in related
        ]
        for rows in (macro_context or {}).values():
            if isinstance(rows, list):
                for row in rows:
                    if str(row.get("series_id") or row.get("id") or "") in plan["economic_series_ids"]:
                        plan["observation_refs"].append({"evidence_id": "economic::" + hashlib.sha256(
                            json.dumps(to_jsonable(row), sort_keys=True).encode()).hexdigest()[:20],
                            "asof_time_utc": asof.isoformat(), "observation": to_jsonable(row)})
        if plan["unresolved_source_refs"]:
            record_execution_issue("market_discovery_source_reference", ValueError("A nominated source reference was not acquired"))

    def investigate(
        plan: dict[str, Any], index: int
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        deadline = time.monotonic() + max(int(get_config_param(_P_EVENT_TIMEOUT)), 1)
        if research_deadline_monotonic is not None:
            deadline = min(deadline, research_deadline_monotonic)
        if deadline <= time.monotonic():
            return {"run_id": run_id, "title": plan["title"], "status": "cancelled",
                    "error": "Home research deadline reached before investigation."}, None
        selected = [
            by_id[source_id] for source_id in plan["source_ids"] if source_id in by_id
        ]
        prior = retained_by_id.get(plan["prior_artifact_id"])
        prior_context = ((prior or {}).get("result") or {}).get("artifact_context") or {}
        if prior:
            selected = list({doc["source_id"]: doc for doc in [*prior_context.get("source_documents", []), *selected]}.values())
        # Execution identity is distinct from semantic continuity. Two selected
        # questions may extend the same prior research without overwriting either.
        event_id = "question-" + hashlib.sha256(
            (asof.isoformat() + "|" + str(index) + "|" + plan["question"]).encode()).hexdigest()[:20]
        instructions = (
            "Investigate the selected question against current observations. Explain the likely business mechanisms, "
            "their relative support, contrary evidence and wider consequences. Multiple causes can act together. "
            "Rank plausible explanations by supporting and conflicting evidence. Express "
            "qualitative likelihood at the evidence's strength and update it as new information arrives. "
            "Read relevant dated findings and original passages using the evidence tools; extend sound research "
            "instead of restarting it. Broad market context remains available. Preserve useful unexpected connections. "
            "Compare the reporting clock with event dates. For material releases already due, acquire the current "
            "results through existing source tools rather than repeat a preview as an upcoming event. "
            "Return substantive findings, key facts, comparisons, interpretations and useful next observations. "
            "Attach original source IDs/URLs and dates to claims. Separate reported facts, business context and hypotheses. "
            "When external research is available, read company-specific X reactions with research.company_sentiment for the central companies. "
            "Use them to assess investor expectations, disagreements and reported concerns; distinguish "
            "sampled opinions from established company facts and preserve original dates. "
            "This is research for later synthesis, not a Home card. Keep execution failures and research bookkeeping internal."
        )
        prior_research = [
            artifact for symbol in sorted(set(plan["related_symbols"]))
            if access and (artifact := latest_research_artifact(access_scope=access,
                subject="company:" + _normalize_symbol(symbol), task="ticker_business_model_research_dossier"))
        ]
        investigation_service = service.with_supplied_evidence({
            **service.supplied_evidence,
            **{"company-research:" + artifact["artifact_id"]: {
                "title": artifact["subject"],
                "metadata": {"created_at_utc": artifact["created_at_utc"], "kind": "prior_model_analysis"},
                "content": {**artifact, "result": {
                    **(artifact.get("result") or {}), "agent_result": {
                        key: value for key, value in ((artifact.get("result") or {}).get("agent_result") or {}).items()
                        if key != "quality_review"}}},
            } for artifact in prior_research},
        })
        prior_research = [{key: artifact.get(key) for key in ("artifact_id", "subject", "created_at_utc")}
                          | {"source_id": "company-research:" + artifact["artifact_id"]}
                          for artifact in prior_research]
        company_prior_research = list(prior_research)
        prior_event = ({key: prior.get(key) for key in ("artifact_id", "subject", "created_at_utc")}
                       | {"source_id": "retained-research:" + prior["artifact_id"],
                          "research_asof_time_utc": prior_context.get("asof_time_utc")}) if prior else None
        if prior_event:
            prior_research.insert(0, prior_event)
        related = {_normalize_symbol(symbol) for symbol in plan["related_symbols"]}
        selected_groups = [group for group in (market_context or {}).get("groups", []) if group["id"] in plan["group_ids"]]
        related.update(symbol for group in selected_groups for symbol in group["members"])
        focused_observations = [row for row in observations if row.get("symbol") in related]
        context_columns = (market_context or {}).get("columns", [])
        focused_context = [row for row in (market_context or {}).get("observations", [])
                           if row[context_columns.index("symbol")] in related] if "symbol" in context_columns else []
        economic_ids = set(plan["economic_series_ids"])
        focused_macro = {key: [row for row in rows if str(row.get("series_id") or row.get("id") or "") in economic_ids]
                         for key, rows in (macro_context or {}).items() if isinstance(rows, list)}
        related_stories = [{key: value for key, value in story.items() if key != "evidence"}
            for story in (previous_publication or {}).get("stories", [])
            if related.intersection(_normalize_symbol(s) for s in story.get("supporting_symbols") or [])]
        related_headlines = [preview for preview in previews
            if related.intersection(_normalize_symbol(s) for s in preview.get("provider_symbols") or [])]
        research_request = to_jsonable({"asof": asof, "investigation": {key: value for key, value in plan.items() if key != "observation_refs"},
            "snapshot_fields": ((market_context or {}).get("method") or {}).get("snapshot_fields", {}),
            "market_observations": focused_observations, "numerical_columns": context_columns,
            "numerical_observations": focused_context, "groups": selected_groups,
            "economic_observations": focused_macro,
            "relevant_calendar": [{"date": day["id"], "timezone": "America/New_York", **event}
                for day in ((macro_context or {}).get("market_calendar") or {}).get("days", [])
                for event in day.get("events", []) if _normalize_symbol(event.get("ticker")) in related],
            "relevant_prior_publication": related_stories,
            "related_headline_hints": related_headlines,
            "context_note": "Symbol overlap supplies discovery hints, not relevance or truth judgments. The previous-publication reader retains original captures; all other headlines remain accessible.",
            "broader_evidence": investigation_service.supplied_evidence_manifest})
        research_instructions = (instructions + " Reuse completed investigations before repeating acquisition. They are dated, "
                "fallible model analysis: compare the new source event against them and use their source references for "
                "focused follow-up, without counting earlier model interpretations as independent confirmations.")
        query = render_research_prompt(evidence={"completed_research": prior_research},
            request=research_request, instructions=research_instructions)
        reuse_identity = render_research_prompt(evidence={"completed_research": company_prior_research},
            request=research_request, instructions=research_instructions)
        if progress_callback:
            progress_callback(
                {
                    "stage": "source_event_research",
                    "message": plan["title"],
                    "event_id": event_id,
                }
            )
        if plan["research_action"] == "reuse":
            result = {**prior["result"], "artifact_reuse": {"artifact_id": prior["artifact_id"],
                "created_at_utc": prior["created_at_utc"], "current_assessment": plan["rationale"]}}
        else:
            result = None
        if deadline <= time.monotonic():
            return {"run_id": run_id, "title": plan["title"], "status": "cancelled",
                    "error": "Home research deadline reached before admission."}, None
        with event_research_context({"work_id": "event:" + event_id,
                "evidence_ids": [*plan["source_ids"], *[row["evidence_id"] for row in plan["observation_refs"]]],
                "observations": plan["observation_refs"], "reason": plan["rationale"],
                "asof_time_utc": asof.isoformat()}):
            core_id = "event:" + event_id
            funded = protect_core_research(config=llm_client.config,
                work={core_id: {"query": query, "response_schema": _EVENT_SCHEMA}},
                stages=2 + int(write_policy != "none"), required=False) if getattr(llm_client, "config", None) else False
            with model_call_context(core_work_id=core_id if funded else None,
                    **({"optional_core_unfunded": True, "completion_only": True} if not funded else {})):
                try:
                    if result is None and time.monotonic() >= deadline:
                        return {"run_id": run_id, "title": plan["title"], "status": "cancelled",
                                "error": "Home research deadline reached during admission."}, None
                    result = result or run_aql_zopedia_structured_agent(
                        query=query,
                        service=investigation_service,
                        artifact_context={"asof_time_utc": asof.isoformat(), "question": plan["question"],
                                          "prior_artifact_id": plan["prior_artifact_id"], "source_documents": selected},
                        schema_name="source_event_research",
                        schema=_EVENT_SCHEMA,
                        task="source_event_research",
                        surface="aql.source_event_research",
                        reuse_scope=os.getenv("LLM_RESEARCH_ARTIFACT_ACCESS_SCOPE", ""),
                        research_subject="event:" + event_id,
                        reuse_identity=reuse_identity,
                        llm_client=llm_client,
                        max_tool_calls=max(int(get_config_param(_P_EVENT_TOOLS)), 0),
                        run_timeout_seconds=max(0, (deadline - time.monotonic()) * (2 / 3 if write_policy != "none" else 1)),
                        llm_step_timeout_seconds=max(int(get_config_param(_P_EVENT_TIMEOUT)), 1),
                        tool_call_timeout_seconds=45,
                        persist_findings=False,
                        write_policy="none",
                        initial_evidence=[{key: doc.get(key) for key in model_source_fields} for doc in selected],
                        progress_callback=progress_callback,
                    )
                    agent = deepcopy(result.get("agent_result") or {})
                    if write_policy != "none" and time.monotonic() >= deadline:
                        agent["memory_update"] = {"status": "timeout", "reason": "Home research completion deadline reached."}
                    if (write_policy != "none" and result.get("status") == "completed"
                            and isinstance(result.get("payload"), dict)
                            and not list(Draft202012Validator(_EVENT_SCHEMA).iter_errors(result["payload"]))
                            and time.monotonic() < deadline):
                        from ..zopedia_agent import _run_post_answer_memory_agent

                        agent["memory_update"] = _run_post_answer_memory_agent(
                            llm=llm_client, service=investigation_service, run_id=agent.get("run_id") or event_id,
                            query=plan["question"],
                            answer_markdown=agent.get("answer_markdown") or "",
                            confidence=agent.get("confidence") or "low", limitations=agent.get("limitations") or [],
                            quality_review=agent.get("quality_review") or {}, tool_calls=agent.get("tool_calls") or [],
                            conversation_history=None, prefetched_context="",
                            timeout_seconds=max(0, deadline - time.monotonic()),
                            progress_callback=progress_callback, write_policy=write_policy,
                            finding_reference=result.get("research_artifact"),
                        )
                        result = {**result, "agent_result": agent}
                finally:
                    if funded:
                        complete_core_research(core_id)
        acquired = materialize_research_artifacts(
            agent, candidate_id=event_id, subject=event_id,
            run_id=run_id, asof_time_utc=asof,
        )
        acquired_documents = [
            {**doc, "source_id": doc["document_id"], "text": doc["raw_text"]}
            for doc in acquired["documents"]
            if doc.get("source_kind") != "research_tool_result"
        ]
        captured_by_id = {ref: doc for doc in [*selected, *acquired_documents]
                          for ref in (doc["source_id"], doc["document_id"])}
        output = result.get("payload")
        record = {
            "run_id": run_id,
            "asof_time_utc": asof,
            "event_id": event_id,
            "agent_run_id": agent.get("run_id", ""),
            "research_action": plan["research_action"],
            "prior_artifact_id": plan["prior_artifact_id"],
            "status": result.get("status"),
            "error": result.get("error", ""),
            "answer_status": agent.get("answer_status", ""),
            "tool_failure_count": (sum(call.get("status") in {"failed", "timeout", "cancelled"}
                                       for call in agent.get("tool_calls", []))
                                   if plan["research_action"] != "reuse" else 0),
            "review_status": (agent.get("quality_review") or {}).get("status", "")
                             if plan["research_action"] != "reuse" else "reused_prior_review",
            "memory_status": (agent.get("memory_update") or {}).get("status", "not_run"),
            "memory_error": (agent.get("memory_update") or {}).get("reason", ""),
            "result_json": json.dumps(to_jsonable(result), ensure_ascii=False),
        }
        record["_acquired_documents"] = list({doc["source_id"]: doc for doc in [*selected, *acquired_documents]}.values())
        if not isinstance(output, dict) or list(
            Draft202012Validator(_EVENT_SCHEMA).iter_errors(output)
        ):
            record.update(
                status="failed",
                error=result.get("error") or "Invalid event research contract",
            )
            return record, None
        symbols = list(
            dict.fromkeys(
                _normalize_symbol(value)
                for value in output["supporting_symbols"]
                if _normalize_symbol(value)
            )
        )
        claims = []
        for ordinal, item in enumerate(output["claims"]):
            supplied_doc = captured_by_id.get(item["source_id"], {})
            url_captures = {doc["document_id"]: doc for doc in [*selected, *acquired_documents]
                            if item["source_url"] and doc.get("url") == item["source_url"]}
            doc = (supplied_doc if supplied_doc and supplied_doc.get("url") == item["source_url"] else
                   next(iter(url_captures.values())) if len(url_captures) == 1 else {})
            date = pd.to_datetime(
                doc.get("published_at") or doc.get("published_date"),
                utc=True,
                errors="coerce",
            )
            item = {
                **item,
                "source_id": doc.get("source_id")
                or doc.get("ref")
                or item["source_id"],
                "citation_status": "resolved" if doc else "unresolved",
            }
            claims.append(
                {
                    **item,
                    "claim_id": f"claim::{event_id}::{ordinal}",
                    "run_id": run_id,
                    "asof_time_utc": asof,
                    "document_id": doc.get("document_id", ""),
                    "source": doc.get("source_provider", ""),
                    "url": item["source_url"] or doc.get("url", ""),
                    "published_at": date,
                    "source_trace": doc.get("source_trace", ""),
                    "raw_text_origin": doc.get("raw_text_origin", ""),
                }
            )
        evidence = [
            {
                "document_id": doc["document_id"],
                "headline": doc["title"],
                "source": doc["source_provider"],
                "url": doc["url"],
                "published_at": doc["published_at"],
                "source_text": doc["text"],
                "raw_text_origin": doc.get("raw_text_origin", ""),
                "source_trace": doc.get("source_trace", ""),
                "provider_payload_json": doc.get("provider_payload_json", ""),
            }
            for doc in [*selected, *acquired_documents]
        ]
        bundle = {
            **output,
            "bundle_id": f"event::{event_id}",
            "bundle_type": "event",
            "event_type": "market_research",
            "event_id": event_id,
            "run_id": run_id,
            "source_dataset": "market_research",
            "source_event": True,
            "research_priority": index,
            "research_action": plan["research_action"],
            "current_assessment": plan["rationale"],
            "research_asof_time_utc": prior_context.get("asof_time_utc") if plan["research_action"] == "reuse" else asof,
            "research_completed_at_utc": (prior.get("created_at_utc") if plan["research_action"] == "reuse" else
                                          (result.get("artifact_reuse") or {}).get("created_at_utc") or pd.Timestamp.now(tz="UTC")),
            "current_assessment_asof_time_utc": asof,
            "prior_artifact_id": plan["prior_artifact_id"],
            "event_score": 0.0,
            "supporting_symbols": symbols,
            "anchor_symbol": symbols[0] if symbols else "",
            "claims": claims,
            "evidence": evidence,
            "source_count": len({doc["url"] or doc["source_id"] for doc in selected}),
            "evidence_count": len(claims),
            "same_day_evidence_count": sum(
                bool(claim.get("is_same_day")) for claim in claims
            ),
            "supporting_claim_ids": [claim["claim_id"] for claim in claims],
            "freshness_quality": "",
            "evidence_quality": agent.get("confidence", "medium").title(),
            "confidence_label": agent.get("confidence", "medium").title(),
            "research_result": agent.get("answer_markdown", ""),
            "agent_run_id": agent.get("run_id", ""),
            "quality_review": agent.get("quality_review", {}),
        }
        bundle.update(
            asof_time_utc=asof,
            research_evidence=[
                {"tool_name": doc["source_provider"], "text": doc["raw_text"],
                 "source_refs": json.loads(doc["source_refs_json"])}
                for doc in acquired["documents"]
                if doc.get("source_kind") == "research_tool_result"
            ],
            related_symbols=[
                row for row in observations if row.get("symbol") in symbols
            ],
            source_count=len(
                {
                    claim["url"]
                    for claim in claims
                    if claim.get("url") and claim.get("citation_status") == "resolved"
                }
            ),
        )
        return record, bundle

    results: list[dict[str, Any]] = []
    bundles: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=min(len(plans), max(int(get_config_param(_P_EVENT_WORKERS)), 1)) or 1,
        thread_name_prefix="aql-source-events"
    ) as pool:
        pending = {
            pool.submit(copy_context().run, investigate, plan, index): plan
            for index, plan in enumerate(plans)
        }
        for future in as_completed(pending):
            try:
                record, bundle = future.result()
            except Exception as exc:
                record, bundle = (
                    {
                        "run_id": run_id,
                        "status": "cancelled" if isinstance(exc, WorkCancelled) else "failed",
                        "title": pending[future]["title"],
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    None,
                )
            documents.extend(record.pop("_acquired_documents", []))
            results.append(record)
            if bundle:
                bundles.append(bundle)
    return {
        "status": "completed"
        if (nomination.get("status") == "completed" and len(bundles) == len(plans)
            and not any(row.get("tool_failure_count") or row.get("review_status") in {"failed", "timeout"}
                        or row.get("memory_status") in {"failed", "timeout", "blocked", "deferred"}
                        for row in [*market_research, *results]))
        else "partial"
        if bundles or market_research[0]["market_context_text"]
        else "failed",
        "plans": [
            {
                "run_id": run_id,
                "asof_time_utc": asof,
                "plan_json": json.dumps(plan, ensure_ascii=False),
                **{key: plan[key] for key in ("title", "question", "rationale")},
            }
            for plan in plans
        ],
        "results": results,
        "bundles": sorted(bundles, key=lambda bundle: bundle["research_priority"]),
        "documents": documents,
        "market_research": market_research,
        "retained_research": retained,
        "input_count": empty["input_count"],
    }
