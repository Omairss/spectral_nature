"""Source-led investigations in the shared AQL/Zopedia research pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
import hashlib
import json
import os
from typing import Any, Callable

import pandas as pd
from jsonschema import Draft202012Validator

from ..aql_zopedia_engine import run_aql_zopedia_structured_agent
from ..llm_context import render_research_prompt
from ..llm_execution import latest_research_artifact
from ..common.news_freshness import coerce_article_published_at
from ..json_utils import to_jsonable
from ..llm import (
    get_config_param,
    register_config_param,
    register_narrative_prompt,
    get_prompt,
)
from ._shared import _coerce_text, _normalize_symbol, _safe_list
from .evidence_index import parse_json_list

_P_EVENT_LIMIT = register_config_param(
    "Source event investigation limit",
    group="AQL / Zopedia Engine",
    default=4,
    description="Maximum independently nominated source events per Attention research run.",
)
_P_EVENT_TIMEOUT = register_config_param(
    "Source event run timeout seconds",
    group="AQL / Zopedia Engine",
    default=600,
    description="Whole budget for each focused source-event investigation.",
)
_P_NOMINATION_TIMEOUT = register_config_param(
    "Source event nomination timeout seconds",
    group="AQL / Zopedia Engine",
    default=180,
    description="Budget for allocating focused research across source reports; all reports remain available to final synthesis.",
)
_P_EVENT_TOOLS = register_config_param(
    "Source event tool budget",
    group="AQL / Zopedia Engine",
    default=6,
    description="Tool budget for investigating each nominated source event.",
)

_NOMINATION_PROMPT = register_narrative_prompt(
    "Source event research nomination",
    __file__,
    group="AQL / Zopedia Engine",
    prompt=(
        "Find the most useful current investigations from these source reports and market observations. "
        "Group reports about the same underlying development. Do not merge separate developments just because they "
        "share a broad sector. Each investigation has one specific new disclosure or event as its center. "
        "Events may concern private companies, customers, "
        "suppliers, policy, commodities, financing or concepts without their own listed instrument. "
        "Provider ticker tags are hints, never boundaries. Infer relevant businesses and possible transmission paths. "
        "A small move or absent price row does not exclude a major development. Distinguish a reported fact from an "
        "historical background or a hypothesis about the market reaction. "
        "Choose investigations that explain the shared changes or reveal a consequential business development. "
        "Avoid routine price recaps, duplicate stories and generic advice. Headlines alone are valid research leads. "
        "Each event needs a focused question and the exact source_ids that motivated it. Plan useful follow-up questions "
        "for the shared agent, prioritizing the decisive missing fact over broad checklists. "
        "Do not attribute price observations to news published after those observations; unknown observation times "
        "also do not establish event chronology. Return events in order of research usefulness; select fewer if appropriate."
    ),
)

_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **{
            key: {"type": "string"}
            for key in (
                "event_title",
                "surface_summary_text",
                "what_happened_text",
                "why_happened_text",
                "affected_assets_summary_text",
                "business_context_text",
                "watch_next_text",
            )
        },
        "cause_status": {
            "type": "string",
            "enum": ["supported", "partial", "unresolved", "continuation"],
        },
        "supporting_symbols": {"type": "array", "items": {"type": "string"}},
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
    "required": [
        "event_title",
        "surface_summary_text",
        "what_happened_text",
        "why_happened_text",
        "affected_assets_summary_text",
        "business_context_text",
        "watch_next_text",
        "cause_status",
        "supporting_symbols",
        "claims",
    ],
}


def research_source_events(
    news_frame: pd.DataFrame,
    *,
    market_frame: pd.DataFrame,
    universe_frame: pd.DataFrame | None,
    asof_time_utc: object,
    run_id: str,
    llm_client: Any,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Nominate and investigate events independently of a mover shortlist.

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
                "source_kind": "news",
                "source_provider": _coerce_text(row.get("source")),
                "provider_symbols": parse_json_list(row.get("symbols"))
                if isinstance(row.get("symbols"), str)
                else _safe_list(row.get("symbols")),
                "source_trace": json.dumps(
                    {"source": "news_articles", "selection": "source_event_intake"}
                ),
            }
        )
    documents = list({doc["document_id"]: doc for doc in documents}.values())
    empty = {
        "bundles": [],
        "plans": [],
        "results": [],
        "documents": documents,
        "input_count": len(documents),
        "status": "no_sources",
    }
    if not documents or llm_client is None:
        return {
            **empty,
            "status": "unavailable" if llm_client is None else "no_sources",
        }
    limit = max(int(get_config_param(_P_EVENT_LIMIT)), 0)
    if not limit:
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
    market_cols = [
        key
        for key in (
            "symbol",
            "change_pct",
            "observed_at_utc",
            "asof_time_utc",
            "security_name",
            "company_name",
            "name",
            "sector",
            "industry",
            "business_tags",
        )
        if key in market
    ]
    observations = to_jsonable(market[market_cols].to_dict("records"))
    nomination_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "events": {
                "type": "array",
                "maxItems": limit,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "question": {"type": "string"},
                        "source_ids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [doc["source_id"] for doc in documents],
                            },
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
                    },
                    "required": [
                        "title",
                        "question",
                        "source_ids",
                        "related_symbols",
                        "followup_questions",
                        "rationale",
                    ],
                },
            }
        },
        "required": ["events"],
    }
    previews = [
        {
            key: doc[key]
            for key in ("source_id", "title", "url", "published_at", "provider_symbols")
        }
        | {"text_preview": doc["text"][:1200]}
        for doc in documents
    ]
    nomination = run_aql_zopedia_structured_agent(
        query=get_prompt(_NOMINATION_PROMPT)
        + "\nContext JSON:\n"
        + json.dumps(
            to_jsonable(
                {"asof": asof, "sources": previews}
            ),
            ensure_ascii=False,
        ),
        schema_name="source_event_nominations",
        schema=nomination_schema,
        task="source_event_nomination", stage='researcher',
        surface="aql.source_event_nomination",
        llm_client=llm_client,
        max_tool_calls=0,
        run_timeout_seconds=max(int(get_config_param(_P_NOMINATION_TIMEOUT)), 1),
        persist_findings=False,
        write_policy="none",
        call_type="research_grade",
    )
    payload = nomination.get("payload")
    if not isinstance(payload, dict) or list(
        Draft202012Validator(nomination_schema).iter_errors(payload)
    ):
        return {
            **empty,
            "status": "failed",
            "results": [
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error": nomination.get("error") or "Invalid nomination contract",
                }
            ],
        }
    plans = payload["events"]
    by_id = {doc["source_id"]: doc for doc in documents}

    def investigate(
        plan: dict[str, Any], index: int
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        selected = [
            by_id[source_id] for source_id in plan["source_ids"] if source_id in by_id
        ]
        event_id = (
            "source-"
            + hashlib.sha256(
                "|".join(sorted(doc["source_id"] for doc in selected)).encode()
            ).hexdigest()[:20]
        )
        instructions = (
            "Investigate this source event and explain what changed, the business transmission and the affected market. "
            "The supplied source reports are already collected evidence. Read them first; retain their value if further "
            "acquisition is slow or unavailable. Use memory and current source tools for focused follow-ups chosen from "
            "the investigation plan. Provider symbol tags do not restrict the affected companies. Sources can concern "
            "private entities. Compare competing interpretations, and distinguish reported facts, established business "
            "relationships and plausible market effects. Do not claim a contemporaneous price move proves causation. "
            "Produce one coherent story with specific connections, not a list of headlines or a price recap. "
            "Use source_id/source_url on claims, and set evidence_role to reported_fact, established_context or hypothesis. "
            "Keep a useful partial story when a connection needs follow-up. Do not invent a public ticker for a private entity.\n"
            "Public text fields contain concise business analysis. Keep tool/runtime limitations in the engine's internal "
            "limitations. watch_next_text names the decisive business question, not an inventory of failed tools or missing datasets.\n"
        )
        prior_research = [
            artifact for symbol in sorted(set(plan["related_symbols"]))
            if (artifact := latest_research_artifact(access_scope="public_company_research",
                subject="company:" + _normalize_symbol(symbol), task="ticker_business_model_research_dossier"))
        ]
        company_prior_research = list(prior_research)
        prior_event = latest_research_artifact(access_scope="public_company_research",
            subject="event:" + event_id, task="source_event_research")
        if prior_event:
            prior_research.insert(0, prior_event)
        research_request = to_jsonable({"asof": asof, "investigation": plan, "market_observations": observations})
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
        result = run_aql_zopedia_structured_agent(
            query=query,
            schema_name="source_event_research",
            schema=_EVENT_SCHEMA,
            task="source_event_research", stage='synthesis',
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
            initial_evidence=selected,
            progress_callback=progress_callback,
        )
        agent = dict(result.get("agent_result") or {})
        collected = [
            {
                "tool_name": call.get("tool_name"),
                "text": call["result_summary"].get("evidence_context_text", ""),
                "source_refs": call["result_summary"].get("evidence_refs") or [],
            }
            for call in agent.get("tool_calls", [])
            if call.get("status") == "completed"
            and isinstance(call.get("result_summary"), dict)
        ]
        refs = list(
            (agent.get("aql_evidence_pack") or {}).get("source_documents") or []
        )
        by_url = {}
        for ref in refs:
            if ref.get("url"):
                by_url[ref["url"]] = {
                    **by_url.get(ref["url"], {}),
                    **{
                        key: value
                        for key, value in ref.items()
                        if value is not None and value != ""
                    },
                }
        acquired_documents = []
        for ordinal, item in enumerate(collected):
            if item["tool_name"] == "research.supplied_context" or not item["text"]:
                continue
            source_refs = item["source_refs"]
            ref = source_refs[0] if len(source_refs) == 1 else {}
            document_id = (
                f"doc::source-event::{agent.get('run_id', event_id)}::{ordinal}"
            )
            acquired_documents.append(
                {
                    "run_id": run_id,
                    "asof_time_utc": asof,
                    "document_id": document_id,
                    "source_id": document_id,
                    "bundle_subject": event_id,
                    "title": ref.get("title") or plan["title"],
                    "url": ref.get("url", ""),
                    "published_at": pd.to_datetime(
                        ref.get("published_date"), utc=True, errors="coerce"
                    ),
                    "raw_text": item["text"],
                    "text": item["text"],
                    "source_kind": "research",
                    "source_provider": ref.get("source") or item["tool_name"],
                    "source_trace": json.dumps(source_refs, default=str),
                }
            )
            for source_ref in source_refs:
                url = source_ref.get("url")
                if (
                    url
                    and url in by_url
                    and (
                        len(source_refs) == 1
                        or by_url[url].get("document_id")
                        not in {
                            doc["document_id"] for doc in documents + acquired_documents
                        }
                    )
                ):
                    by_url[url].update(document_id=document_id, source_id=document_id)
        output = result.get("payload")
        record = {
            "run_id": run_id,
            "asof_time_utc": asof,
            "event_id": event_id,
            "agent_run_id": agent.get("run_id", ""),
            "status": result.get("status"),
            "error": result.get("error", ""),
            "answer_status": agent.get("answer_status", ""),
            "result_json": json.dumps(to_jsonable(result), ensure_ascii=False),
        }
        record["_acquired_documents"] = acquired_documents
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
                    "is_same_day": bool(pd.notna(date) and date.date() == asof.date()),
                    "freshness_class": "current"
                    if pd.notna(date) and 0 <= (asof - date).total_seconds() <= 259200
                    else "background",
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
            }
            for doc in selected
        ]
        bundle = {
            **output,
            "bundle_id": f"event::{event_id}",
            "bundle_type": "event",
            "event_type": "source_event",
            "event_id": event_id,
            "run_id": run_id,
            "source_dataset": "news_articles",
            "source_event": True,
            "research_priority": index,
            "event_score": 0.0,
            "supporting_symbols": symbols,
            "anchor_symbol": symbols[0] if symbols else "",
            "claims": claims,
            "evidence": evidence,
            "source_count": len({doc["url"] or doc["source_id"] for doc in selected}),
            "evidence_count": len(claims),
            "same_day_evidence_count": sum(
                bool(claim["is_same_day"]) for claim in claims
            ),
            "supporting_claim_ids": [claim["claim_id"] for claim in claims],
            "freshness_quality": "High",
            "evidence_quality": agent.get("confidence", "medium").title(),
            "confidence_label": agent.get("confidence", "medium").title(),
            "research_result": agent.get("answer_markdown", ""),
            "agent_run_id": agent.get("run_id", ""),
            "quality_review": agent.get("quality_review", {}),
        }
        bundle.update(
            asof_time_utc=asof,
            research_evidence=collected,
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
        max_workers=min(len(plans), 3) or 1, thread_name_prefix="aql-source-events"
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
                        "status": "failed",
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
        if len(bundles) == len(plans)
        else "partial"
        if bundles
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
        "input_count": empty["input_count"],
    }
