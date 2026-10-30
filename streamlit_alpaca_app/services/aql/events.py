"""One market research agenda and focused investigations through AQL/Zopedia."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
import hashlib
import json
import os
from typing import Any, Callable

import pandas as pd
from jsonschema import Draft202012Validator
from data_access.query_service import QueryService

from ..aql_zopedia_engine import run_aql_zopedia_structured_agent
from ..llm_context import render_research_prompt
from ..llm_execution import (latest_research_artifact, event_research_context,
                            event_investigation_capacity, record_execution_issue)
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
    default=600,
    description="Whole execution window for each question selected by the common Home research agenda.",
)
_P_NOMINATION_TIMEOUT = register_config_param(
    "Source event nomination timeout seconds",
    group="AQL / Zopedia Engine",
    default=600,
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
    description="Recent completed source investigations supplied to current allocation; retained originals remain available in shared research memory.",
)

_NOMINATION_PROMPT = register_narrative_prompt(
    "Home market research plan",
    __file__,
    group="AQL / Zopedia Engine",
    prompt=(
        "Plan Home research from the underlying market and economic observations first. "
        "The broad numerical context covers the available universe before any name cut. Inspect multi-day "
        "leadership, weakness, participation and offsetting groups, including steady companies missing from the "
        "headlines. Return-correlation groups describe measured co-movement; sectors are labels, and neither "
        "establishes investor flows or a cause. Compare prior and current group membership and relationships, "
        "accounting for sparse history and data coverage. A changed cluster is a lead, not a required trigger. "
        "Then consider dated headlines, economic releases and upcoming decisions. Important disclosures and "
        "private-company or policy developments can deserve research before prices move. Provider tags do not "
        "restrict affected businesses. Use the supplied evidence reader and existing research tools for the "
        "original observations and sources when needed. Do not launch separate mover, macro or news agendas. "
        "Choose N focused questions across this common agenda using usefulness, available allowance and the "
        "original deadline; limits are ceilings, not quotas. Reserve completion resources for core review and "
        "publication. On a quiet run choose fewer investigations, while still assessing current conditions. "
        "Compare dated retained research and the prior publication. Prior attention is not evidence of importance. "
        "For each question choose investigate, update or reuse. Reuse only sound, source-backed findings whose "
        "question and explanation still hold against current observations; explain that assessment in rationale. "
        "For update or reuse select the exact prior_artifact_id, even if new articles use different URLs. "
        "Unchanged headlines do not establish unchanged conditions. New uncertainty, contrary evidence or "
        "unexplained behavior can require fresh investigation. Never re-date an old disclosure as new. "
        "Keep each investigation's context focused: select related_symbols, relevant group_ids and economic "
        "series IDs; broader observations remain accessible. Plan decisive follow-ups rather than checklists. "
        "A measured group can contain different businesses and explanations. Separate materially different "
        "questions instead of assigning a large group one assumed cause. Ask open questions; do not force a "
        "choice between a news explanation and a factor explanation before investigating. Preserve consequential "
        "numerical discoveries even when the headline collection has no matching article. "
        "Assess what offsets benchmark weakness as a market question in its own right; steady broad strength "
        "deserves explanation even without a fresh headline. Retained hypotheses and reviewer disagreements "
        "must remain distinct from established causes when deciding to reuse or extend research. "
        "Source IDs are supplied identities or exact URLs acquired by tools. A numerical lead needs no article. "
        "Return events in priority order, research_agenda as the current questions, and findings_markdown as "
        "substantive current assessment and unresolved questions, not a public Brief or a list of figures."

    ),
)

_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_title": {"type": "string"},
        "findings_markdown": {"type": "string", "description": "Substantive shared research: operating explanation, dated findings, alternatives, source references and unresolved questions."},
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
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
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
    empty = {
        "bundles": [],
        "plans": [],
        "results": [],
        "documents": documents,
        "input_count": len(documents),
        "market_research": [],
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
        | {"text_preview": doc["text"][:1200]}
        for doc in documents
    ]
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
        retained = pipeline_store.list_research_artifacts(
            namespace=f"research_artifact:{llm_settings_scope()}:{access}", task="source_event_research",
            limit=max(int(get_config_param(_P_PRIOR_EVENT_LIMIT)), 1)) if access else []
        retained = [artifact for artifact in retained
                    if pd.to_datetime(((artifact.get("result") or {}).get("artifact_context") or {}).get("asof_time_utc")
                                      or artifact.get("created_at_utc"), utc=True) <= asof]
        if previous_nomination and pd.to_datetime((previous_nomination.get("artifact_context") or {}).get("asof_time_utc")
                                                  or previous_nomination["created_at_utc"], utc=True) > asof:
            previous_nomination = None
        prior_investigations = [
            {key: artifact.get(key) for key in ("artifact_id", "subject", "task", "created_at_utc")}
            | {"payload": (artifact.get("result") or {}).get("payload") or {},
               "quality_review": ((artifact.get("result") or {}).get("agent_result") or {}).get("quality_review") or {},
               "limitations": ((artifact.get("result") or {}).get("agent_result") or {}).get("limitations") or [],
               "artifact_context": {key: value for key, value in ((artifact.get("result") or {}).get("artifact_context") or {}).items()
                                    if key != "source_documents"}}
            for artifact in retained
        ]
    except WorkCancelled:
        raise
    except Exception as exc:
        context_status = "unavailable"
        record_execution_issue("event_allocation_history", exc)
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
    source_records.update({"retained-research:" + artifact["artifact_id"]: {
        "title": artifact["subject"], "metadata": {"kind": "prior_model_analysis", "created_at_utc": artifact["created_at_utc"]},
        "content": to_jsonable(artifact)} for artifact in retained})
    service = QueryService.from_environment().with_supplied_evidence(source_records)
    nomination = run_aql_zopedia_structured_agent(
        query=get_prompt(_NOMINATION_PROMPT)
        + "\nContext JSON:\n"
        + json.dumps(
            to_jsonable(
                {"asof": asof, "market_context": market_context or {},
                 "sources": previews,
                 "economic_observations": macro_context or {},
                 "previous_market_context": {key: value for key, value in (((previous_nomination or {}).get("artifact_context") or {}).get("market_context") or {}).items()
                                             if key not in {"observations", "columns", "previous_groups"}},
                 "previous_publication": {"asof_time_utc": (previous_publication or {}).get("asof_time_utc"),
                    "brief": (previous_publication or {}).get("brief"),
                    "stories": [{key: story.get(key) for key in ("headline", "event_title", "supporting_symbols", "what_changed_text")}
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
        run_timeout_seconds=max(int(get_config_param(_P_NOMINATION_TIMEOUT)), 1),
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
             for reference in (doc["source_id"], doc["document_id"], doc.get("url")) if reference}
    retained_by_id = {artifact["artifact_id"]: artifact for artifact in retained}
    for plan in plans:
        prior = retained_by_id.get(plan["prior_artifact_id"])
        if plan["research_action"] in {"reuse", "update"} and prior is None:
            record_execution_issue("market_research_continuity", ValueError("Selected prior research is unavailable; investigating the question afresh"))
            plan["research_action"] = "investigate"
        plan["source_refs"] = list(plan["source_ids"])
        resolved = [by_id[ref]["source_id"] for ref in plan["source_refs"] if ref in by_id]
        plan["unresolved_source_refs"] = [ref for ref in plan["source_refs"] if ref not in by_id]
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
            "Investigate this selected market question and explain the observations, business transmission and what changed. "
            "The supplied source captures are collected evidence. Nominated questions and unacquired source references "
            "are research leads, not established facts. Read the captures first; retain their value if further "
            "acquisition is slow or unavailable. Use memory and current source tools for focused follow-ups chosen from "
            "the investigation plan. Provider symbol tags do not restrict the affected companies. Sources can concern "
            "private entities. Compare competing interpretations, and distinguish reported facts, established business "
            "relationships and plausible market effects. Do not claim a contemporaneous price move proves causation. "
            "A missing fresh article does not establish a factor explanation or erase a numerical discovery. "
            "Read relevant prior findings and their dated originals before repeating discovery. Investigate the "
            "business explanation and alternatives; retain unresolved observations without substituting an "
            "unsupported common cause. Different companies in a numerical group can have different causes. "
            "For company questions explain the business: products, customers, demand and operating economics, "
            "with the relevant competitive or policy connections and contrary evidence. "
            "Record one substantive shared research answer in findings_markdown. Explain the operating or policy "
            "mechanism, decisive detail, competing interpretations, original event dates and useful next observations. "
            "Preserve information needed for later synthesis; this stage does not write a Home card. "
            "Use source_id/source_url on claims, and set evidence_role to reported_fact, established_context or hypothesis. "
            "Keep a useful partial story when a connection needs follow-up. Do not invent a public ticker for a private entity.\n"
            "Keep tool/runtime limitations in the engine's internal limitations; findings explain the substantive "
            "remaining uncertainty and the observation that would change the interpretation.\n"
        )
        prior_research = [
            artifact for symbol in sorted(set(plan["related_symbols"]))
            if access and (artifact := latest_research_artifact(access_scope=access,
                subject="company:" + _normalize_symbol(symbol), task="ticker_business_model_research_dossier"))
        ]
        company_prior_research = list(prior_research)
        prior_event = ({key: prior.get(key) for key in ("artifact_id", "subject", "created_at_utc")}
                       | {"payload": prior["result"].get("payload") or {}}) if prior else None
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
            "market_observations": focused_observations, "numerical_columns": context_columns,
            "numerical_observations": focused_context, "groups": selected_groups,
            "economic_observations": focused_macro, "relevant_prior_publication": related_stories,
            "related_headline_hints": related_headlines,
            "context_note": "Symbol overlap supplies discovery hints, not relevance or truth judgments. The previous-publication reader retains original captures; all other headlines remain accessible.",
            "broader_evidence": service.supplied_evidence_manifest})
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
        with event_research_context({"work_id": "event:" + event_id,
                "evidence_ids": [*plan["source_ids"], *[row["evidence_id"] for row in plan["observation_refs"]]],
                "observations": plan["observation_refs"], "reason": plan["rationale"],
                "asof_time_utc": asof.isoformat()}):
            result = result or run_aql_zopedia_structured_agent(
                query=query,
                service=service,
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
                run_timeout_seconds=max(int(get_config_param(_P_EVENT_TIMEOUT)), 1),
                llm_step_timeout_seconds=max(int(get_config_param(_P_EVENT_TIMEOUT)), 1),
                tool_call_timeout_seconds=45,
                persist_findings=False,
                write_policy="none",
                initial_evidence=[{key: doc.get(key) for key in model_source_fields} for doc in selected],
                progress_callback=progress_callback,
            )
        agent = dict(result.get("agent_result") or {})
        acquired = materialize_research_artifacts(
            agent, candidate_id=event_id, subject=event_id,
            run_id=run_id, asof_time_utc=asof,
        )
        acquired_documents = [
            {**doc, "source_id": doc["document_id"], "text": doc["raw_text"]}
            for doc in acquired["documents"]
            if doc.get("source_kind") != "research_tool_result"
        ]
        by_url = {
            doc["url"]: doc for doc in [*selected, *acquired_documents] if doc.get("url")
        }
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
            ref = by_url.get(item["source_url"], {})
            supplied_doc = by_id.get(item["source_id"], {})
            doc = ref or (
                supplied_doc if supplied_doc.get("url") == item["source_url"] else {}
            )
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
            "research_completed_at_utc": prior.get("created_at_utc") if plan["research_action"] == "reuse" else pd.Timestamp.now(tz="UTC"),
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
        if nomination.get("status") == "completed" and len(bundles) == len(plans)
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
        "input_count": empty["input_count"],
    }
