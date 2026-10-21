from __future__ import annotations

from collections import Counter
import json
import math
import re
from typing import Any

from data_access.contracts import QueryRequest, QueryValidationError, coerce_object
from data_access.query_service import QueryService
from . import zopedia_research
from . import zopedia_analysis
from .aql_zopedia_engine import load_aql_zopedia_llm_client


_TOOL_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tool_tokens(value: object) -> list[str]:
    normalized: list[str] = []
    for token in _TOOL_TOKEN_PATTERN.findall(str(value or "").lower()):
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        normalized.append(token)
    return normalized


def _tool_search_text(tool: dict[str, Any]) -> str:
    searchable = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "input_schema": tool.get("inputSchema"),
        "examples": tool.get("examples"),
        "failure_modes": tool.get("failure_modes"),
        "empty_result_guidance": tool.get("empty_result_guidance"),
    }
    return json.dumps(searchable, ensure_ascii=True, sort_keys=True, default=str)


class ToolRegistry:
    """Canonical tool definitions with exact lookup and bounded BM25 search."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        ordered: list[dict[str, Any]] = []
        by_name: dict[str, dict[str, Any]] = {}
        for raw_tool in tools:
            tool = dict(raw_tool or {})
            name = str(tool.get("name") or "").strip()
            if not name:
                raise ValueError("Tool definitions require a non-empty name.")
            if name in by_name:
                raise ValueError(f"Duplicate tool definition: {name}")
            tool["name"] = name
            ordered.append(tool)
            by_name[name] = tool
        self._tools = ordered
        self._by_name = by_name
        self._token_counts = [Counter(_tool_tokens(_tool_search_text(tool))) for tool in ordered]
        self._document_lengths = [sum(counts.values()) for counts in self._token_counts]
        self._average_document_length = (
            sum(self._document_lengths) / len(self._document_lengths)
            if self._document_lengths
            else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for counts in self._token_counts:
            self._document_frequency.update(counts.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self._tools]

    def get(self, name: object) -> dict[str, Any] | None:
        tool = self._by_name.get(str(name or "").strip())
        return dict(tool) if tool is not None else None

    def namespaces(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for name in self._by_name:
            namespace = name.split(".", 1)[0] if "." in name else "system"
            counts[namespace] += 1
        return dict(sorted(counts.items()))

    def search(
        self,
        query: object,
        *,
        namespace: object = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        query_text = str(query or "").strip().lower()
        query_tokens = _tool_tokens(query_text)
        if not query_tokens:
            return []
        namespace_text = str(namespace or "").strip().lower().rstrip(".")
        limit = max(1, min(int(limit or 8), 20))
        document_count = max(len(self._tools), 1)
        average_length = self._average_document_length or 1.0
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for index, tool in enumerate(self._tools):
            name = str(tool["name"])
            if namespace_text and not name.lower().startswith(f"{namespace_text}."):
                continue
            counts = self._token_counts[index]
            document_length = self._document_lengths[index]
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if frequency <= 0:
                    continue
                document_frequency = self._document_frequency.get(token, 0)
                inverse_document_frequency = math.log(
                    1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + 1.2 * (1.0 - 0.75 + 0.75 * document_length / average_length)
                score += inverse_document_frequency * (frequency * 2.2) / denominator
            lower_name = name.lower()
            if query_text == lower_name:
                score += 100.0
            elif query_text in lower_name:
                score += 12.0
            name_tokens = set(_tool_tokens(lower_name))
            score += 2.5 * len(name_tokens.intersection(query_tokens))
            if score > 0:
                ranked.append((score, name, tool))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {**dict(tool), "search_score": round(score, 6)}
            for score, _, tool in ranked[:limit]
        ]

    def search_result(
        self,
        query: object,
        *,
        namespace: object = "",
        limit: int = 8,
    ) -> dict[str, Any]:
        matches = self.search(query, namespace=namespace, limit=limit)
        namespace_text = str(namespace or "").strip().lower().rstrip(".")
        lines = [
            f"- {tool['name']}: {str(tool.get('description') or '').strip()}"
            for tool in matches
        ]
        return {
            "request": {
                "operation": "tool_search",
                "name": "tools.search",
                "params": {
                    "query": str(query or "").strip(),
                    "namespace": namespace_text,
                    "limit": max(1, min(int(limit or 8), 20)),
                },
            },
            "result_type": "tool_search",
            "payload": {
                "tools": matches,
                "match_count": len(matches),
                "namespaces": self.namespaces(),
                "llm_context_text": "\n".join(lines) if lines else "No matching tools were found.",
            },
            "provenance": {
                "mode": "computed",
                "datasets": [],
                "details": {"index": "tool_registry_bm25"},
            },
            "messages": [],
        }


_TOOL_SEARCH_TOOL: dict[str, Any] = {
    "name": "tools.search",
    "description": (
        "Search the shared tool registry when the needed capability is not obvious. "
        "Returns matching tool names, descriptions, and input schemas. Search by the "
        "evidence or computation needed, not by a guessed implementation name."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Capability or evidence needed."},
            "namespace": {
                "type": ["string", "null"],
                "description": "Optional namespace such as research, zopedia, dataset, or analysis.",
            },
            "limit": {"type": "integer", "description": "Maximum matches, from 1 to 20."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def tool_schema(
    params: list[str] | tuple[str, ...],
    *,
    required: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": ["string", "number", "boolean", "object", "array", "null"]}
            for name in list(params or [])
        },
        "required": list(required or []),
        "additionalProperties": False,
    }


def _schema_from_capability(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("param_schema")
    if isinstance(schema, dict):
        return dict(schema)
    return tool_schema(
        list(spec.get("params") or []),
        required=list(spec.get("required_params") or []),
    )


_ZOPEDIA_SAFE_MUTATION_TYPES = ["metadata_patch", "link_pages", "upsert_pages"]
_ZOPEDIA_PAGE_TYPES = ["source", "concept", "entity", "theme", "market_event", "ticker", "macro", "question", "index"]
_ZOPEDIA_MEMORY_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "page_id": {"type": "string"},
        "page_type": {"type": "string", "enum": _ZOPEDIA_PAGE_TYPES},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "body_markdown": {"type": "string"},
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "source_document_ids": {"type": "array", "items": {"type": "string"}},
        "entity_refs": {"type": "array", "items": {"type": "string"}},
        "outgoing_links": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
    },
    "required": ["page_type", "title", "summary", "body_markdown", "source_urls", "entity_refs", "metadata"],
}


def _hypothesis_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "hypothesis.verify",
            "description": (
                "Grade a hypothesis against collected evidence. Returns verdict "
                "(supported/weak/conflicting/unsupported), confidence, supporting and "
                "contradicting claims, and gap_queries — concrete search queries to fill "
                "evidence holes. Call this after collecting evidence to decide if the "
                "hypothesis is solid or needs more research."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim_text": {"type": "string"},
                                "source": {"type": "string"},
                                "is_same_day": {"type": "boolean"},
                            },
                            "required": ["claim_text"],
                        },
                    },
                    "stories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sentence": {"type": "string"},
                                "symbols": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["sentence"],
                        },
                    },
                },
                "required": ["hypothesis", "claims"],
                "additionalProperties": False,
            },
        },
    ]


def _research_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "research.completed_investigations",
            "description": (
                "Read completed shared company investigations from the current environment. "
                "Reuse their dated findings, sources and open questions before repeating research. "
                "Subjects use company:<ticker> identifiers. Omit subjects to list metadata; pass subjects to read complete findings. "
                "New evidence, contradictions and useful exploration can warrant further research."
            ),
            "inputSchema": {"type": "object", "additionalProperties": False,
                "properties": {"subjects": {"type": "array", "items": {"type": "string"}},
                               "max_items": {"type": "integer"}}, "required": []},
        },
        {
            "name": "research.retained_context",
            "description": (
                "Look up retained narrative context already in Spectral Nature for the query. "
                "This is internal memory, not live web search. Use it for background, prior findings, "
                "and source-backed context that Spectral Nature has already retained. Pass focus_symbols when the task is about "
                "a named company, ticker, person, or other scoped subject so retained lookup stays anchored. "
                "Current reports may already be retained; choose additional research when it helps the question. "
                "Adjacent peer, sector, macro, and spillover context is useful; organize it around the primary subject."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_items": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.market_impact_map",
            "description": (
                "Expand a live event or macro query into likely impacted assets and spillover symbols."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_symbols": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.live_event_evidence",
            "description": (
                "Fetch fresh web evidence for a current query, using event-level search and symbol-level search when relevant. "
                "Returns recent search/article rows with headlines, snippets, sources, timestamps, and URLs. "
                "Use research.open_page on the highest-value URL when article-body support is needed for a strong claim."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "focus_symbols": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer"},
                    "force_refresh": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.search_evidence",
            "description": (
                "Search all retained sources and knowledge by meaning and words. Supply varied queries for different explanations, "
                "entities or concepts. Results distinguish original sources, knowledge and generated analysis with dependencies. "
                "The tool interprets time context and adds broad reporting from the relevant period alongside unrestricted context searches. "
                "Useful current sources may already be retained. Interpret the catalog by meaning and read useful sources in full with research.read_evidence or zopedia.read_page."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The information need. Defaults to the current user question; an empty string browses sources without a word query.",
                    },
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Independent search formulations; include related concepts and alternatives.",
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional company hints. Broad source recall remains enabled unless strict_tickers is true.",
                    },
                    "strict_tickers": {
                        "type": "boolean",
                        "description": "Restrict all sources to these tickers only when the user explicitly requires that source restriction. Default false.",
                    },
                    "max_results": {"type": "integer", "description": "Max results (default 10)."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.read_evidence",
            "description": "Read a retained passage in full with dates and source type. Set include_source_references=true to also inspect its complete inherited provenance. No browsing or knowledge write.",
            "inputSchema": {
                "type": "object",
                "properties": {"chunk_record_id": {"type": "string"}, "include_source_references": {"type": "boolean"}},
                "required": ["chunk_record_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "research.open_page",
            "description": (
                "Open one selected web page. When main content is required, the shared browser escalates "
                "from Playwright/HTTP to crawler-backed acquisition for blocked or shallow pages."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "require_main_content": {
                        "type": "boolean",
                        "description": "Use true when snippets/previews are too shallow and page body evidence is required.",
                    },
                    "min_text_chars": {
                        "type": "integer",
                        "description": "Minimum visible text before escalating to crawler-backed acquisition.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    ]


def _zopedia_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "zopedia.search_pages",
            "description": (
                "Search Zopedia wiki memory for durable pages about concepts, entities, themes, "
                "events, sources, tickers, and macro topics. Use this early so answers can build "
                "on retained knowledge before fetching new evidence."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "page_types": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.read_page",
            "description": "Read a retained Zopedia page and its source dependencies. Long pages return next_offset for continued reading.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000},
                },
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.read_source",
            "description": (
                "Open the concrete source behind a Zopedia source reference: a Zopedia source page, "
                "retained evidence chunk, retained source document, or source URL. Use this after "
                "sources_for_page or trace_to_evidence when final claims need original evidence."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "ref": {"type": "string"},
                    "kind": {"type": "string"},
                    "chunk_record_id": {"type": "string"},
                    "canonical_document_id": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.sources_for_page",
            "description": (
                "Find original source and evidence references for one Zopedia page. "
                "Use this when the underlying reports would add context or resolve a useful uncertainty."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"page_id": {"type": "string"}},
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.trace_to_evidence",
            "description": (
                "Build a bounded page-link and source-evidence trace around a Zopedia page. "
                "Use this when an answer depends on a page plus its neighboring wiki context."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.neighborhood",
            "description": "Load linked Zopedia pages around a page_id to inspect local graph context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["page_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.ingest_source",
            "description": (
                "Store user-supplied source text as Zopedia wiki memory and ask the LLM to extract "
                "linked pages from it. Use when the user supplies a document, transcript, or source text."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "source_text": {"type": "string"},
                    "url": {"type": "string"},
                    "source_type": {"type": "string"},
                },
                "required": ["title", "source_text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.ingest_youtube",
            "description": "Fetch a YouTube transcript and store it in Zopedia memory when captions are available.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.propose_change",
            "description": (
                "Create a reviewable Zopedia change proposal when a page is wrong, stale, missing, "
                "or should be split/merged/deleted."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "proposal_type": {"type": "string"},
                    "page_id": {"type": "string"},
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["proposal_type", "title", "rationale"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.list_proposals",
            "description": "List open or historical Zopedia change proposals.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.list_mutations",
            "description": (
                "List automatic Zopedia memory mutations with risk, status, affected pages, "
                "source path, and rollback metadata."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "mutation_type": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.list_maintenance_reports",
            "description": (
                "List recent Zopedia maintenance reports: backlink/community index health, issue counts, "
                "top communities, and automatic maintenance mutation ids. Use when checking memory quality."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.apply_mutation",
            "description": (
                "Apply a safe, audited Zopedia memory mutation. Safe writes commit with rollback metadata; "
                "destructive or unsupported changes are converted into review proposals. "
                "Use mutation_type=upsert_pages exactly for page creation or updates; never use upsert or upsert_page. "
                "For upsert_pages, pass complete page objects in pages."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mutation_type": {"type": "string", "enum": _ZOPEDIA_SAFE_MUTATION_TYPES},
                    "page_id": {"type": "string"},
                    "target_page_id": {"type": "string"},
                    "pages": {"type": "array", "items": _ZOPEDIA_MEMORY_PAGE_SCHEMA},
                    "metadata_patch": {"type": "object"},
                    "evidence_refs": {"type": "array", "items": {"type": "object"}},
                    "rationale": {"type": "string"},
                    "payload": {"type": "object"},
                    "allow_risky": {"type": "boolean"},
                },
                "required": ["mutation_type"],
                "additionalProperties": False,
            },
        },
        {
            "name": "zopedia.rollback_mutation",
            "description": (
                "Rollback one audited automatic Zopedia mutation by restoring before-state pages "
                "and archiving pages created by that mutation. Use only when the user asks to undo "
                "or when a judged maintenance action identifies a bad committed mutation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"mutation_id": {"type": "string"}},
                "required": ["mutation_id"],
                "additionalProperties": False,
            },
        },
    ]


def _investigator_tools() -> list[dict[str, Any]]:
    """Stock Investigator tools — technicals, forecast, company context, fundamentals, news."""
    return [
        {
            "name": "investigator.technical_signals",
            "description": (
                "Get technical signal summary for a ticker: current price, ATH, pullback "
                "from ATH, channel support/resistance/position, RSI, annualized volatility, "
                "and regime classification (e.g. Trend continuation, Deep pullback)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.forecast",
            "description": (
                "Get a Monte Carlo forecast for the next week for a ticker. Returns "
                "probability of being up, probability of breakout, median expected "
                "return, and confidence interval."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.company_context",
            "description": (
                "Get company context for a ticker: business description, recent narrative "
                "from filings/news, management signals, and what's driving attention now."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.fundamentals",
            "description": (
                "Get quarterly financial fundamentals for a ticker: income statement, "
                "balance sheet, and cash flow statement data. Returns the most recent "
                "quarters available."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
        {
            "name": "investigator.recent_news",
            "description": (
                "Get recent news headlines for a ticker. Returns the most recent articles "
                "with headline, source, date, summary, and URL. If summaries are headline-only, "
                "open the URL with research.open_page before treating the article as verified evidence."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker symbol (e.g. AAPL)"},
                    "days": {"type": "integer", "description": "Lookback days (default 14)"},
                    "limit": {"type": "integer", "description": "Max articles (default 8)"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
        },
    ]


def _anomaly_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "dataset.run_anomaly_check",
            "description": (
                "Run anomaly detection on ticker data. Returns the most significant "
                "anomaly events for the given symbols: first drop/rise date, magnitude, "
                "z-score, classification, and attention score. Uses pre-computed "
                "attention data when available, falls back to on-demand computation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of ticker symbols to check (e.g. ['ARCC', 'OBDC', 'MAIN']).",
                    },
                    "horizon": {
                        "type": "string",
                        "description": "Time horizon: 1d, 1w, 1mo, 3mo, or 1yr. Defaults to 1w.",
                    },
                },
                "required": ["symbols"],
                "additionalProperties": False,
            },
        },
    ]


def _scratchpad_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "scratchpad.write",
            "description": (
                "Write an entry to the hypothesis scratchpad. Use this to persist "
                "anomaly events, evidence claims, hypothesis drafts, and search queries "
                "between tool calls. Kind should be one of: anomaly, claim, hypothesis, "
                "search_query, note."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "content": {"type": "object"},
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "scratchpad.read",
            "description": (
                "Read entries from the hypothesis scratchpad. Optionally filter by "
                "kind (anomaly, claim, hypothesis, search_query, note) and limit to "
                "the last N entries."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": ["string", "null"]},
                    "last_n": {"type": ["integer", "null"]},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


def _analysis_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "analysis.run_python",
            "description": (
                "Run bounded pandas/numpy/scipy/scikit-learn analysis over approved Spectral Nature "
                "datasets, inline tables or self-contained numerical calculations. Analytical input data is preserved in full "
                "within a 128 MiB estimated-frame input allocation; only display previews are shortened. Use this for EDA, statistical checks, regressions, "
                "clustering, classification, feature importance, or other quantitative analysis that "
                "needs computation beyond direct dataset lookup. Provide dataset_refs that name QueryService "
                "datasets plus typed params, or inline_datasets for supplied tabular data. Omit both for a self-contained calculation. "
                "Code must be valid multiline Python. Inputs are preloaded as pandas DataFrames in "
                "`datasets` and as variables named by each dataset alias. Do not call load_dataset, "
                "get_dataset, context, globals, open, or filesystem/network APIs from the code. "
                "Results include retained numerical outputs as evidence. analysis.read_raw_output "
                "can inspect retained logs by analysis_run_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "code": {"type": "string"},
                    "dataset_refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "alias": {"type": "string"},
                                "params": {"type": "object"},
                            },
                            "required": ["name"],
                            "additionalProperties": False,
                        },
                    },
                    "inline_datasets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "rows": {"type": "array", "items": {"type": "object"}},
                            },
                            "required": ["name", "rows"],
                            "additionalProperties": False,
                        },
                    },
                    "timeout_seconds": {"type": "integer"},
                    "max_rows": {"type": "integer", "minimum": 1, "description": "Optional explicit input admission bound. Omit to use all rows within the byte allocation. Oversized input is rejected rather than sampled."},
                },
                "required": ["objective", "code"],
                "additionalProperties": False,
            },
        },
        {
            "name": "analysis.read_raw_output",
            "description": (
                "Explicitly read bounded raw stdout, stderr, error, traceback, or all output for a persisted "
                "analysis.run_python run. Use only when exact logs are needed for debugging or answering "
                "a user question about what the analysis printed; ordinary analysis context already includes "
                "retained numerical results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "analysis_run_id": {"type": "string"},
                    "stream": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["analysis_run_id"],
                "additionalProperties": False,
            },
        },
    ]


def is_query_service_tool(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    return name in {"system.capabilities", "query.execute"} or name.startswith("dataset.") or name.startswith("chart.")


def is_research_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("research.")


def is_zopedia_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("zopedia.")


def is_hypothesis_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("hypothesis.")


def is_scratchpad_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("scratchpad.")


def is_anomaly_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() == "dataset.run_anomaly_check"


def is_investigator_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().startswith("investigator.")


def is_analysis_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in {"analysis.run_python", "analysis.read_raw_output"}


def is_tool_search_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() == "tools.search"


def build_tool_registry(service: QueryService) -> ToolRegistry:
    capabilities = service.list_capabilities()
    tools: list[dict[str, Any]] = [
        _TOOL_SEARCH_TOOL,
        {
            "name": "system.capabilities",
            "description": "Return dataset and chart capability metadata.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]
    tools.extend(_research_tools())
    if getattr(service, "supplied_evidence", None):
        tools.extend([
            {"name": "research.list_supplied_evidence", "description": "Browse the complete invocation-owned evidence catalog. Optional terms match literal titles/metadata (OR). Omit terms to browse every source; follow next_offset. This does not acquire new evidence.",
             "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
                 "terms": {"type": "array", "items": {"type": "string"}}, "offset": {"type": "integer", "minimum": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
            {"name": "research.read_supplied_evidence", "description": "Read a supplied source's complete original content, optionally at a JSON Pointer (e.g. /points). Continue with next_offset. All originals remain available; catalog metadata alone does not establish a trend or cause.",
             "inputSchema": {"type": "object", "additionalProperties": False, "required": ["source_id"], "properties": {
                 "source_id": {"type": "string"}, "pointer": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                 "max_characters": {"type": "integer", "minimum": 1, "maximum": 60000}}}},
        ])
    tools.extend(_zopedia_tools())
    tools.extend(_analysis_tools())
    tools.extend(_hypothesis_tools())
    tools.extend(_scratchpad_tools())
    tools.extend(_anomaly_tools())
    tools.extend(_investigator_tools())
    for dataset_name, spec in dict(capabilities.get("datasets") or {}).items():
        desc = str(spec.get("description") or "").strip() or f"Fetch dataset '{dataset_name}'."
        tools.append(
            {
                "name": f"dataset.{dataset_name}",
                "description": desc,
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    for chart_name, spec in dict(capabilities.get("charts") or {}).items():
        desc = str(spec.get("description") or "").strip() or f"Build chart model '{chart_name}'."
        tools.append(
            {
                "name": f"chart.{chart_name}",
                "description": desc,
                "inputSchema": _schema_from_capability(dict(spec or {})),
                "resolution": spec.get("resolution"),
            }
        )
    for tool in tools:
        tool["source_access"] = (
            "supplied" if tool["name"] in {"research.list_supplied_evidence", "research.read_supplied_evidence"} else
            "web"
            if tool["name"]
            in {"research.live_event_evidence", "research.open_page", "zopedia.ingest_youtube"}
            else "retained_or_data"
        )
        tool["knowledge_effect"] = (
            "write"
            if tool["name"]
            in {
                "zopedia.ingest_source",
                "zopedia.ingest_youtube",
                "zopedia.apply_mutation",
                "zopedia.rollback_mutation",
            }
            else "propose" if tool["name"] == "zopedia.propose_change" else "read"
        )
    return ToolRegistry(tools)


def build_tool_catalog(service: QueryService) -> list[dict[str, Any]]:
    """Compatibility view of the canonical shared tool registry."""
    return build_tool_registry(service).catalog()


def build_query_request_for_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> QueryRequest:
    name = str(tool_name or "").strip()
    args = coerce_object(arguments, field_name="arguments")
    if name == "system.capabilities":
        return QueryRequest(operation="capabilities", name="", params={})
    if name == "query.execute":
        payload = {
            "operation": str(args.get("operation") or "").strip().lower(),
            "name": str(args.get("name") or ""),
            "params": coerce_object(args.get("params"), field_name="params"),
        }
        return QueryRequest.from_dict(payload)
    if name.startswith("dataset."):
        return QueryRequest(operation="dataset", name=name.split(".", 1)[1], params=args)
    if name.startswith("chart."):
        return QueryRequest(operation="chart", name=name.split(".", 1)[1], params=args)
    raise ValueError(f"Unsupported tool '{name}'.")


def _resolve_research_layer(service: QueryService) -> Any:
    data_access = getattr(service, "data_access", None)
    if data_access is not None and hasattr(data_access, "resolve_attention_home_1d"):
        return data_access
    return None


def _invoke_research_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    layer = _resolve_research_layer(service)
    if tool_name == "research.list_supplied_evidence":
        payload = service.list_supplied_evidence(**args)
        datasets = ("invocation_supplied_evidence",)
    elif tool_name == "research.read_supplied_evidence":
        payload = service.read_supplied_evidence(**args)
        datasets = ("invocation_supplied_evidence",)
    elif tool_name == "research.completed_investigations":
        from .llm import llm_settings_scope
        from .pipeline_store import list_research_artifacts
        artifacts = list_research_artifacts(
            namespace=f"research_artifact:{llm_settings_scope()}:public_company_research",
            subjects=args.get("subjects"), limit=int(args.get("max_items") or 20),
        )
        payload = {"summary": [
            {key: item.get(key) for key in ("artifact_id", "subject", "task", "created_at_utc")} | {
                **({"findings": (item.get("result") or {}).get("payload") or {},
                    "evidence": (item.get("result") or {}).get("artifact_context") or {}} if args.get("subjects") else {}),
            } for item in artifacts
        ]}
        datasets = ("aql_zopedia_execution_state",)
    elif tool_name == "research.retained_context":
        payload = zopedia_research.retained_context(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_items=int(args.get("max_items") or 5),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("attention_home_1d", "attention_research_bundle", "attention_ticker_background")
    elif tool_name == "research.market_impact_map":
        payload = zopedia_research.market_impact_map(
            query=str(args.get("query") or ""),
            max_symbols=int(args.get("max_symbols") or 8),
        )
        datasets = ("attention_market_events", "commodity_proxy_profile")
    elif tool_name == "research.read_evidence":
        payload = zopedia_research.read_evidence(
            chunk_record_id=str(args.get("chunk_record_id") or ""),
            include_source_references=bool(args.get("include_source_references")),
        )
        datasets = ("saa_evidence_chunks", "saa_documents")
    elif tool_name == "research.search_evidence":
        payload = zopedia_research.search_evidence(
            query=str(args.get("query", (request_context or {}).get("original_query", ""))),
            tickers=args.get("tickers"),
            queries=args.get("queries"),
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            strict_tickers=bool(args.get("strict_tickers")),
            asof_time_utc=(request_context or {}).get("asof_time_utc"),
            timezone=(request_context or {}).get("timezone", "UTC"),
            original_query=(request_context or {}).get("original_query", ""),
            max_results=int(args.get("max_results") or 10),
        )
        datasets = ("saa_evidence_chunks", "saa_documents")
    elif tool_name == "research.live_event_evidence":
        payload = zopedia_research.live_event_evidence(
            query=str(args.get("query") or ""),
            focus_symbols=args.get("focus_symbols"),
            max_results=int(args.get("max_results") or 6),
            force_refresh=bool(args.get("force_refresh")),
            layer=layer,
        )
        datasets = ("web_research", "attention_search_results")
    elif tool_name == "research.open_page":
        payload = zopedia_research.open_page(
            url=str(args.get("url") or ""),
            max_chars=int(args.get("max_chars") or 5000),
            require_main_content=bool(args.get("require_main_content")),
            min_text_chars=int(args.get("min_text_chars") or 500),
        )
        datasets = ("page_browsing",)
    else:
        raise ValueError(f"Unsupported tool '{tool_name}'.")

    tool_messages: list[str] = []
    if isinstance(payload, dict):
        tool_messages = [
            str(item).strip() for item in list(payload.get("messages") or []) if str(item).strip()
        ]

    return {
        "request": {"operation": "research", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": list(datasets),
            "details": {"tool_name": tool_name},
        },
        "messages": tool_messages,
    }


def _invoke_zopedia_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "zopedia.search_pages":
        payload = zopedia_research.zopedia_search_pages(
            query=str(args.get("query") or ""),
            max_results=int(args.get("max_results") or 8),
            page_types=args.get("page_types"),
        )
    elif tool_name == "zopedia.read_page":
        payload = zopedia_research.zopedia_read_page(
            page_id=str(args.get("page_id") or ""),
            offset=int(args.get("offset") or 0),
            max_chars=int(args.get("max_chars") or 20000),
        )
    elif tool_name == "zopedia.read_source":
        payload = zopedia_research.zopedia_read_source(
            page_id=str(args.get("page_id") or ""),
            ref=str(args.get("ref") or ""),
            kind=str(args.get("kind") or ""),
            chunk_record_id=str(args.get("chunk_record_id") or ""),
            canonical_document_id=str(args.get("canonical_document_id") or ""),
            url=str(args.get("url") or ""),
        )
    elif tool_name == "zopedia.sources_for_page":
        payload = zopedia_research.zopedia_sources_for_page(page_id=str(args.get("page_id") or ""))
    elif tool_name == "zopedia.trace_to_evidence":
        payload = zopedia_research.zopedia_trace_to_evidence(
            page_id=str(args.get("page_id") or ""),
            depth=int(args.get("depth") or 1),
        )
    elif tool_name == "zopedia.neighborhood":
        payload = zopedia_research.zopedia_neighborhood(
            page_id=str(args.get("page_id") or ""),
            depth=int(args.get("depth") or 1),
        )
    elif tool_name == "zopedia.ingest_source":
        payload = zopedia_research.zopedia_ingest_source(
            title=str(args.get("title") or ""),
            source_text=str(args.get("source_text") or ""),
            url=str(args.get("url") or ""),
            source_type=str(args.get("source_type") or "source"),
        )
    elif tool_name == "zopedia.ingest_youtube":
        payload = zopedia_research.zopedia_ingest_youtube(
            url=str(args.get("url") or ""),
            title=str(args.get("title") or ""),
        )
    elif tool_name == "zopedia.propose_change":
        payload = zopedia_research.zopedia_propose_change(
            proposal_type=str(args.get("proposal_type") or ""),
            page_id=str(args.get("page_id") or ""),
            title=str(args.get("title") or ""),
            rationale=str(args.get("rationale") or ""),
            payload=coerce_object(args.get("payload"), field_name="payload"),
        )
    elif tool_name == "zopedia.list_proposals":
        payload = zopedia_research.zopedia_list_proposals(
            status=str(args.get("status") or "open"),
            max_results=int(args.get("max_results") or 12),
        )
    elif tool_name == "zopedia.list_mutations":
        payload = zopedia_research.zopedia_list_mutations(
            status=str(args.get("status") or ""),
            mutation_type=str(args.get("mutation_type") or ""),
            max_results=int(args.get("max_results") or 12),
        )
    elif tool_name == "zopedia.list_maintenance_reports":
        payload = zopedia_research.zopedia_list_maintenance_reports(
            status=str(args.get("status") or ""),
            max_results=int(args.get("max_results") or 6),
        )
    elif tool_name == "zopedia.apply_mutation":
        raw_pages = args.get("pages")
        raw_evidence_refs = args.get("evidence_refs")
        payload = zopedia_research.zopedia_apply_mutation(
            mutation_type=str(args.get("mutation_type") or ""),
            page_id=str(args.get("page_id") or ""),
            target_page_id=str(args.get("target_page_id") or ""),
            pages=list(raw_pages or []) if isinstance(raw_pages, list) else [],
            metadata_patch=coerce_object(args.get("metadata_patch"), field_name="metadata_patch"),
            evidence_refs=list(raw_evidence_refs or []) if isinstance(raw_evidence_refs, list) else [],
            rationale=str(args.get("rationale") or ""),
            payload=coerce_object(args.get("payload"), field_name="payload"),
            allow_risky=bool(args.get("allow_risky")),
        )
    elif tool_name == "zopedia.rollback_mutation":
        payload = zopedia_research.zopedia_rollback_mutation(
            mutation_id=str(args.get("mutation_id") or ""),
        )
    else:
        raise ValueError(f"Unsupported tool '{tool_name}'.")

    return {
        "request": {"operation": "zopedia", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": [
                "saa_zopedia_pages",
                "saa_zopedia_change_proposals",
                "saa_zopedia_mutation_audit",
                "saa_zopedia_backlinks",
                "saa_zopedia_community_index",
                "saa_zopedia_maintenance_reports",
            ],
            "details": {"tool_name": tool_name},
        },
        "messages": [],
    }


def _invoke_analysis_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "analysis.read_raw_output":
        payload = zopedia_analysis.read_analysis_raw_output(
            analysis_run_id=str(args.get("analysis_run_id") or ""),
            stream=str(args.get("stream") or "stdout"),
            max_chars=_int_arg(args.get("max_chars"), default=zopedia_analysis.DEFAULT_RAW_OUTPUT_MAX_CHARS),
        )
        return {
            "request": {"operation": "analysis", "name": tool_name, "params": args},
            "result_type": "analysis_raw_output",
            "payload": payload,
            "provenance": {
                "mode": "computed",
                "datasets": [zopedia_analysis.ANALYSIS_RUN_TABLE],
                "details": {"tool_name": tool_name, "analysis_run_id": payload.get("analysis_run_id")},
            },
            "messages": [],
        }
    if tool_name != "analysis.run_python":
        raise ValueError(f"Unsupported tool '{tool_name}'.")
    normalized_args = _normalize_analysis_arguments(args)
    payload = _run_analysis_payload(service=service, args=normalized_args)
    return {
        "request": {"operation": "analysis", "name": tool_name, "params": normalized_args},
        "result_type": "analysis_result",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": [
                "query_service_datasets",
                zopedia_analysis.ANALYSIS_RUN_TABLE,
                zopedia_analysis.ANALYSIS_ARTIFACT_TABLE,
            ],
            "details": {"tool_name": tool_name, "analysis_run_id": payload.get("analysis_run_id")},
        },
        "messages": list(payload.get("messages") or []),
    }


def _run_analysis_payload(*, service: QueryService, args: dict[str, Any]) -> dict[str, Any]:
    payload = zopedia_analysis.run_analysis_python(
        service=service,
        objective=str(args.get("objective") or ""),
        code=str(args.get("code") or ""),
        dataset_refs=list(args.get("dataset_refs") or []),
        inline_datasets=list(args.get("inline_datasets") or []),
        timeout_seconds=_int_arg(args.get("timeout_seconds"), default=zopedia_analysis.DEFAULT_TIMEOUT_SECONDS),
        max_rows=args.get("max_rows"),
    )
    return payload


def _int_arg(value: object, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        try:
            return int(float(str(value)))
        except Exception:
            return int(default)


def _normalize_analysis_arguments(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args or {})
    normalized["dataset_refs"] = _coerce_analysis_dataset_refs(normalized.get("dataset_refs"))
    normalized["inline_datasets"] = _coerce_analysis_inline_datasets(normalized.get("inline_datasets"))
    return normalized


def _coerce_analysis_dataset_refs(value: object) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in list(value or []) if isinstance(value, list) else []:
        parsed = item
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = {"name": text}
        if not isinstance(parsed, dict):
            continue
        name = str(parsed.get("name") or parsed.get("dataset") or parsed.get("dataset_name") or "").strip()
        if not name:
            continue
        ref: dict[str, Any] = {"name": name}
        alias = str(parsed.get("alias") or "").strip()
        if alias:
            ref["alias"] = alias
        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        ref["params"] = dict(params or {})
        refs.append(ref)
    return refs


def _coerce_analysis_inline_datasets(value: object) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    items: list[object]
    if isinstance(value, list):
        items = list(value)
    elif isinstance(value, dict):
        if "rows" in value or "records" in value:
            items = [value]
        else:
            items = [
                {"name": key, "rows": rows}
                for key, rows in value.items()
                if isinstance(rows, (list, dict))
            ]
    else:
        items = []
    for item in items:
        parsed = item
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except Exception:
                continue
        if isinstance(parsed, dict):
            rows = parsed.get("rows")
            if rows is None:
                rows = parsed.get("records")
            if isinstance(rows, list):
                datasets.append({"name": str(parsed.get("name") or parsed.get("alias") or "inline"), "rows": rows})
    return datasets


def _invoke_hypothesis_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "hypothesis.verify":
        from .agents import verify_hypothesis

        llm_client = load_aql_zopedia_llm_client(surface="hypothesis.verify")
        if llm_client is None:
            return {
                "request": {"operation": "hypothesis", "name": tool_name, "params": args},
                "result_type": "error",
                "payload": {"error": "LLM runtime is not configured."},
                "provenance": {"mode": "error", "datasets": [], "details": {}},
                "messages": ["LLM runtime is not configured for hypothesis verification."],
            }

        claims = [
            {
                "claim_text": str(c.get("claim_text") or ""),
                "source": str(c.get("source") or ""),
                "is_same_day": bool(c.get("is_same_day")),
                "confidence_score": float(c.get("confidence_score") or 0.5),
            }
            for c in list(args.get("claims") or [])
            if str(c.get("claim_text") or "").strip()
        ]
        stories = [
            {
                "kind": "story",
                "sentence": str(b.get("sentence") or ""),
                "symbols": list(b.get("symbols") or []),
            }
            for b in list(args.get("stories") or args.get("beats") or [])
        ]
        result = verify_hypothesis(
            hypothesis=str(args.get("hypothesis") or ""),
            claims=claims,
            stories=stories,
            llm_client=llm_client,
        )
        return {
            "request": {"operation": "hypothesis", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": result,
            "provenance": {
                "mode": "computed",
                "datasets": ("hypothesis_verification",),
                "details": {"tool_name": tool_name},
            },
            "messages": [],
        }
    raise ValueError(f"Unsupported tool '{tool_name}'.")


def _invoke_anomaly_tool(
    *,
    service: QueryService,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import pandas as pd
    from .market_data import (
        AttentionConfig,
        ExpectationConfig,
        build_attention_candidates,
        build_peer_group_membership,
        build_price_expectations,
        normalize_horizon,
        HORIZON_PERIODS,
    )

    args = coerce_object(arguments, field_name="arguments")
    raw_symbols = list(args.get("symbols") or [])
    symbols = [str(s).upper().strip() for s in raw_symbols if str(s).strip()]
    raw_horizon = str(args.get("horizon") or "1w")
    horizon = normalize_horizon(raw_horizon)
    # Fallback for unsupported aliases like "30d" — map to nearest canonical horizon
    if horizon not in HORIZON_PERIODS:
        horizon = _nearest_horizon(raw_horizon)

    if not symbols:
        return {
            "request": {"operation": "dataset", "name": "run_anomaly_check", "params": args},
            "result_type": "error",
            "payload": {"error": "No symbols provided."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["No symbols provided."],
        }

    data_access = getattr(service, "data_access", None)
    events: list[dict[str, Any]] = []

    # --- Step 1: Try materialized attention candidates ---
    if data_access is not None and hasattr(data_access, "resolve_attention_home_1d"):
        try:
            resolved = data_access.resolve_attention_home_1d()
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            if isinstance(payload, dict):
                candidates_frame = payload.get("attention_candidates_1d")
                if candidates_frame is None:
                    candidates_frame = payload.get("attention_feed")
                if isinstance(candidates_frame, pd.DataFrame) and not candidates_frame.empty:
                    frame = candidates_frame.copy()
                    if "entity_id" in frame.columns:
                        frame = frame[frame["entity_id"].str.upper().isin(symbols)]
                    if horizon and "horizon" in frame.columns:
                        frame = frame[frame["horizon"] == horizon]
                    frame = frame.sort_values("attention_score", ascending=False) if "attention_score" in frame.columns else frame
                    for _, row in frame.head(20).iterrows():
                        events.append(_event_from_row(row))
        except Exception:
            pass

    # --- Step 2: On-demand computation for missing symbols ---
    missing = [s for s in symbols if s not in {e["symbol"] for e in events}]
    if missing and data_access is not None and hasattr(data_access, "resolve_price_history"):
        try:
            # Fetch price history for all missing symbols + SPY as benchmark
            all_needed = list(set(missing + ["SPY"]))
            frames: list[pd.DataFrame] = []
            for sym in all_needed:
                try:
                    resolved = data_access.resolve_price_history(sym, days=400)
                    ph = resolved.payload if hasattr(resolved, "payload") else resolved
                    if isinstance(ph, pd.DataFrame) and not ph.empty:
                        ph = ph.copy()
                        if "symbol" not in ph.columns:
                            ph["symbol"] = sym
                        frames.append(ph)
                except Exception:
                    pass
            if frames:
                combined_prices = pd.concat(frames, ignore_index=True)
                peer_membership = build_peer_group_membership(
                    asof_time_utc=pd.Timestamp.now(tz="UTC"),
                    symbols=missing + ["SPY"],
                )
                # Momentum and correlation are optional — use empty frames as fallback
                momentum = pd.DataFrame()
                correlation = pd.DataFrame()
                try:
                    mp_resolved = data_access.resolve_momentum_profiles(symbols=missing + ["SPY"])
                    mp = mp_resolved.payload if hasattr(mp_resolved, "payload") else mp_resolved
                    if isinstance(mp, pd.DataFrame):
                        momentum = mp
                except Exception:
                    pass
                try:
                    cs_resolved = data_access.resolve_correlation_phase_shift(
                        benchmark="SPY", days=180, corr_window=60,
                        roc_window=20, momentum_window=60, symbols=missing + ["SPY"],
                    )
                    cs = cs_resolved.payload if hasattr(cs_resolved, "payload") else cs_resolved
                    if isinstance(cs, pd.DataFrame):
                        correlation = cs
                    elif isinstance(cs, dict):
                        correlation = cs.get("summary", pd.DataFrame())
                except Exception:
                    pass

                expectations = build_price_expectations(
                    combined_prices, momentum, correlation, peer_membership,
                    config=ExpectationConfig(horizons=(horizon,)),
                )
                if not expectations.empty:
                    candidates = build_attention_candidates(
                        expectations, config=AttentionConfig(residual_zscore_threshold=1.0),
                    )
                    if not candidates.empty:
                        candidates = candidates[candidates["entity_id"].isin(missing)].copy()
                        candidates = candidates.sort_values("attention_score", ascending=False)
                        for _, row in candidates.head(20).iterrows():
                            events.append(_event_from_row(row))
        except Exception:
            pass

    missing = [s for s in symbols if s not in {e["symbol"] for e in events}]
    return {
        "request": {"operation": "dataset", "name": "run_anomaly_check", "params": args},
        "result_type": "research",
        "payload": {
            "events": events,
            "symbols_checked": symbols,
            "symbols_with_anomalies": list({e["symbol"] for e in events}),
            "symbols_no_data": missing,
            "horizon": horizon,
            "llm_context_text": _anomaly_context_text(events, missing),
        },
        "provenance": {
            "mode": "computed",
            "datasets": ("attention_candidates_1d", "price_history"),
            "details": {"symbols": symbols, "horizon": horizon},
        },
        "messages": [],
    }


def _event_from_row(row: Any) -> dict[str, Any]:
    import pandas as pd
    return {
        "symbol": str(row.get("entity_id") or ""),
        "horizon": str(row.get("horizon") or ""),
        "direction": str(row.get("direction") or ""),
        "anomaly_type": str(row.get("anomaly_type") or ""),
        "observed_return_pct": float(pd.to_numeric(row.get("observed_value"), errors="coerce")),
        "expected_return_pct": float(pd.to_numeric(row.get("expected_value"), errors="coerce")),
        "residual_pct": float(pd.to_numeric(row.get("residual_value"), errors="coerce")),
        "residual_zscore": float(pd.to_numeric(row.get("residual_zscore"), errors="coerce")),
        "attention_score": float(pd.to_numeric(row.get("attention_score"), errors="coerce")),
        "severity_score": float(pd.to_numeric(row.get("severity_score"), errors="coerce")),
        "status": str(row.get("status") or ""),
        "why_now_text": str(row.get("why_now_text") or ""),
    }


def _nearest_horizon(raw: str) -> str:
    """Map non-standard horizon strings like '30d' to the nearest canonical horizon."""
    import re
    from .market_data import HORIZON_PERIODS
    text = str(raw or "").strip().lower()
    match = re.match(r"^(\d+)\s*(d|w|m|y)", text)
    if not match:
        return "1w"
    count = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        days = count
    elif unit == "w":
        days = count * 5
    elif unit == "m":
        days = count * 21
    else:
        days = count * 252
    # Find closest canonical horizon
    best = "1w"
    best_diff = abs(days - 5)
    for name, periods in HORIZON_PERIODS.items():
        diff = abs(days - periods)
        if diff < best_diff:
            best = name
            best_diff = diff
    return best


def _anomaly_context_text(events: list[dict[str, Any]], missing: list[str]) -> str:
    if not events and not missing:
        return "No anomaly data available."
    lines: list[str] = []
    for e in events:
        lines.append(
            f"{e['symbol']}: {e['direction']} {e['residual_zscore']:+.2f}σ "
            f"(observed {e['observed_return_pct']:+.2f}% vs expected {e['expected_return_pct']:+.2f}%), "
            f"type={e['anomaly_type']}, attention={e['attention_score']:.0f}"
        )
    if missing:
        lines.append(f"No anomaly data for: {', '.join(missing)}")
    return "\n".join(lines)


_INVESTIGATOR_FUNDAMENTAL_METRICS: tuple[str, ...] = (
    "Total Revenue",
    "Revenue",
    "Operating Income",
    "Net Income",
    "Gross Profit",
    "Cash from Operating Activities",
    "Operating Cash Flow",
    "Capital Expenditure",
    "Capex",
    "Free Cash Flow",
    "Cash and Cash Equivalents",
    "Cash",
    "Total Assets",
    "Total Liabilities",
    "Total Equity",
    "Total Debt",
    "Long Term Debt",
    "NAV",
    "Net Asset Value",
    "Investment Income",
    "Net Investment Income",
    "Distributable Earnings",
)


def _compact_investigator_fundamentals(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    import pandas as pd

    quarter_records: dict[str, dict[str, Any]] = {}
    for statement_type in ["income", "balance", "cashflow"]:
        frame = payload.get(statement_type)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        rows = frame.copy()
        if "report_date" in rows.columns:
            rows["_report_date"] = pd.to_datetime(rows["report_date"], errors="coerce")
            rows = rows.sort_values("_report_date", na_position="first")
        rows["_quarter_key"] = rows.get("year_quarter", pd.Series(index=rows.index, dtype=object)).astype(str)
        if "report_date" in rows.columns:
            date_key = rows["report_date"].astype(str)
            rows.loc[rows["_quarter_key"].isin({"", "nan", "None", "NaT"}), "_quarter_key"] = date_key
        metric_series = rows["metric"].astype(str) if "metric" in rows.columns else pd.Series("", index=rows.index, dtype=str)
        metric_rows = rows[metric_series.isin(_INVESTIGATOR_FUNDAMENTAL_METRICS)].copy()
        if metric_rows.empty:
            metric_rows = rows.copy()
        quarter_keys = [key for key in metric_rows["_quarter_key"].dropna().astype(str).drop_duplicates().tolist() if key and key.lower() != "nan"]
        for quarter_key in quarter_keys[-4:]:
            qrows = metric_rows[metric_rows["_quarter_key"].astype(str) == quarter_key]
            if qrows.empty:
                continue
            record = quarter_records.setdefault(
                quarter_key,
                {
                    "quarter": quarter_key,
                    "report_date": str(qrows.get("report_date", pd.Series(dtype=object)).dropna().iloc[-1])
                    if "report_date" in qrows.columns and not qrows.get("report_date", pd.Series(dtype=object)).dropna().empty
                    else "",
                },
            )
            for _, row in qrows.iterrows():
                metric = str(row.get("metric") or "").strip()
                if not metric:
                    continue
                value = row.get("value")
                try:
                    if pd.isna(value):
                        value = ""
                except Exception:
                    pass
                if value == "":
                    continue
                record[metric] = value
    ordered = [quarter_records[key] for key in sorted(quarter_records.keys())][-4:]
    for record in ordered:
        revenue = record.get("Total Revenue", record.get("Revenue"))
        operating_income = record.get("Operating Income")
        try:
            if revenue not in (None, "", 0) and operating_income not in (None, ""):
                record["Operating Margin"] = float(operating_income) / float(revenue)
        except Exception:
            pass
    lines = [f"Quarterly fundamentals for {ticker}:"]
    for record in ordered:
        pieces: list[str] = []
        for metric in _INVESTIGATOR_FUNDAMENTAL_METRICS:
            if metric in record:
                pieces.append(f"{metric}={record[metric]}")
        if "Operating Margin" in record:
            pieces.append(f"Operating Margin={record['Operating Margin']:.2%}")
        if pieces:
            lines.append(f"  {record.get('quarter')}: " + "; ".join(pieces[:10]))
    return {
        "compact_quarters": ordered,
        "llm_context_text": "\n".join(lines) if len(lines) > 1 else f"No fundamentals data for {ticker}.",
    }


def _invoke_investigator_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import pandas as pd

    args = coerce_object(arguments, field_name="arguments")
    data_access = getattr(service, "data_access", None)
    if data_access is None:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": "Data access layer is not available."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["Data access layer is not available."],
        }

    ticker = str(args.get("ticker") or "").upper().strip()
    if not ticker:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": "No ticker provided."},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": ["No ticker provided."],
        }

    try:
        if tool_name == "investigator.technical_signals":
            resolved = data_access.resolve_technical_signal_summary(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            summary = dict(payload) if isinstance(payload, dict) else {}
            text_lines = [f"Technical signals for {ticker}:"]
            for key in ["close", "ath", "pullback_from_ath_pct", "channel_support", "channel_resistance",
                         "channel_position", "dist_to_support_pct", "dist_to_resistance_pct",
                         "rsi_14", "vol_20_ann_pct", "regime"]:
                val = summary.get(key)
                if val is not None and str(val).strip() and str(val) != "nan":
                    label = key.replace("_", " ").replace("pct", "%").title()
                    text_lines.append(f"  {label}: {val}")
            summary["llm_context_text"] = "\n".join(text_lines) if len(text_lines) > 1 else f"No technical signal data for {ticker}."
            return _investigator_result(tool_name, args, summary, ("technical_signals_latest",))

        if tool_name == "investigator.forecast":
            resolved = data_access.resolve_forecast_next_week(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            forecast = dict(payload) if isinstance(payload, dict) else {}
            text_lines = [f"Next-week forecast for {ticker}:"]
            for key in ["up_probability", "breakout_probability", "median_return_pct",
                         "ci_low_pct", "ci_high_pct", "simulations"]:
                val = forecast.get(key)
                if val is not None and str(val).strip() and str(val) != "nan":
                    label = key.replace("_", " ").replace("pct", "%").title()
                    text_lines.append(f"  {label}: {val}")
            forecast["llm_context_text"] = "\n".join(text_lines) if len(text_lines) > 1 else f"No forecast data for {ticker}."
            return _investigator_result(tool_name, args, forecast, ("technical_forecast",))

        if tool_name == "investigator.company_context":
            resolved = data_access.resolve_attention_context(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            context = dict(payload) if isinstance(payload, dict) else {}
            parts = []
            for key in ["llm_headline", "llm_summary_text", "llm_narrative_text",
                         "llm_why_now", "llm_management_signal"]:
                val = str(context.get(key) or "").strip()
                if val:
                    parts.append(val)
            if not parts:
                story = str(context.get("context_story_text") or "").strip()
                if story:
                    parts.append(story)
            if not parts:
                try:
                    background_resolved = data_access.resolve_attention_ticker_background(ticker)
                    background_payload = (
                        background_resolved.payload if hasattr(background_resolved, "payload") else background_resolved
                    )
                    background = dict(background_payload) if isinstance(background_payload, dict) else {}
                    company_name = str(background.get("company_name") or "").strip()
                    business_lens = str(background.get("business_lens") or "").strip()
                    background_text = (
                        str(background.get("company_background_text") or "").strip()
                        or str(background.get("description_text") or "").strip()
                    )
                    if company_name and not str(context.get("company_name") or "").strip():
                        context["company_name"] = company_name
                    if business_lens:
                        context["business_lens"] = business_lens
                    if background_text:
                        parts.append(background_text)
                    for line in list(background.get("news_summary_lines") or [])[:3]:
                        text = str(line or "").strip()
                        if text:
                            parts.append(text)
                    trace = context.get("source_trace") if isinstance(context.get("source_trace"), dict) else {}
                    background_trace = background.get("source_trace") if isinstance(background.get("source_trace"), dict) else {}
                    if background_trace:
                        context["source_trace"] = {**trace, **background_trace}
                except Exception:
                    pass
            context["llm_context_text"] = "\n\n".join(parts) if parts else f"No company context for {ticker}."
            return _investigator_result(tool_name, args, context, ("attention_context_bundle", "attention_ticker_background", "company_baselines"))

        if tool_name == "investigator.fundamentals":
            resolved = data_access.resolve_quarterly_fundamentals(ticker)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            if isinstance(payload, dict):
                result: dict[str, Any] = _compact_investigator_fundamentals(payload, ticker)
                for statement_type in ["income", "balance", "cashflow"]:
                    frame = payload.get(statement_type)
                    if isinstance(frame, pd.DataFrame) and not frame.empty:
                        recent = frame.tail(24)
                        records = recent.to_dict(orient="records")
                        result[statement_type] = records
                return _investigator_result(tool_name, args, result, ("quarterly_fundamentals",))
            return _investigator_result(tool_name, args, {"llm_context_text": f"No fundamentals data for {ticker}."}, ("quarterly_fundamentals",))

        if tool_name == "investigator.recent_news":
            days = int(args.get("days") or 14)
            limit = int(args.get("limit") or 8)
            resolved = data_access.resolve_recent_news(ticker, days=days, limit=limit)
            payload = resolved.payload if hasattr(resolved, "payload") else resolved
            articles_frame = payload
            if isinstance(payload, dict):
                articles_frame = payload.get("articles")
            if isinstance(articles_frame, pd.DataFrame) and not articles_frame.empty:
                articles = []
                for _, row in articles_frame.head(limit).iterrows():
                    articles.append({
                        "headline": str(row.get("headline") or row.get("title") or ""),
                        "source": str(row.get("source") or ""),
                        "published_at": str(row.get("published_at") or ""),
                        "summary": str(row.get("summary") or ""),
                        "url": str(row.get("url") or ""),
                    })
                text_lines = [f"Recent news for {ticker} ({len(articles)} articles):"]
                for a in articles:
                    text_lines.append(f"  - {a['headline']} ({a['source']}, {a['published_at']})")
                    if a.get("url"):
                        text_lines.append(f"    URL: {a['url']}")
                    if a["headline"].strip() and a["summary"].strip() == a["headline"].strip():
                        text_lines.append("    Note: headline-only summary; open the URL before using this as verified evidence.")
                result = {
                    "articles": articles,
                    "llm_context_text": "\n".join(text_lines),
                }
                if isinstance(payload, dict):
                    for key in ("fallback_summary", "source"):
                        value = payload.get(key)
                        if value:
                            result[key] = value
                datasets = tuple(getattr(getattr(resolved, "provenance", None), "datasets", None) or ("news_articles",))
                return _investigator_result(tool_name, args, result, datasets)
            datasets = tuple(getattr(getattr(resolved, "provenance", None), "datasets", None) or ("news_articles",))
            return _investigator_result(tool_name, args, {"articles": [], "llm_context_text": f"No recent news for {ticker}."}, datasets)

    except Exception as exc:
        return {
            "request": {"operation": "investigator", "name": tool_name, "params": args},
            "result_type": "error",
            "payload": {"error": str(exc)},
            "provenance": {"mode": "error", "datasets": [], "details": {}},
            "messages": [str(exc)],
        }

    raise ValueError(f"Unsupported investigator tool '{tool_name}'.")


def _investigator_result(
    tool_name: str,
    args: dict[str, Any],
    payload: dict[str, Any],
    datasets: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "request": {"operation": "investigator", "name": tool_name, "params": args},
        "result_type": "research",
        "payload": payload,
        "provenance": {
            "mode": "computed",
            "datasets": list(datasets),
            "details": {"tool_name": tool_name},
        },
        "messages": [],
    }


def _invoke_scratchpad_tool(
    *,
    tool_name: str,
    run_id: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .agents import read_entries, read_summary, write_entry

    args = coerce_object(arguments, field_name="arguments")
    if tool_name == "scratchpad.write":
        entry = write_entry(
            run_id=run_id,
            kind=str(args.get("kind") or "note"),
            content=coerce_object(args.get("content"), field_name="content"),
        )
        return {
            "request": {"operation": "scratchpad", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": {"status": "written", "entry": entry},
            "provenance": {"mode": "computed", "datasets": ("hypothesis_scratchpad",), "details": {"tool_name": tool_name}},
            "messages": [],
        }
    if tool_name == "scratchpad.read":
        entries = read_entries(
            run_id=run_id,
            kind=args.get("kind"),
            last_n=int(args["last_n"]) if args.get("last_n") is not None else None,
        )
        summary = read_summary(run_id=run_id)
        return {
            "request": {"operation": "scratchpad", "name": tool_name, "params": args},
            "result_type": "research",
            "payload": {"summary": summary, "entries": entries},
            "provenance": {"mode": "computed", "datasets": ("hypothesis_scratchpad",), "details": {"tool_name": tool_name}},
            "messages": [],
        }
    raise ValueError(f"Unsupported tool '{tool_name}'.")


def invoke_tool(
    *,
    service: QueryService,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    run_id: str = "",
    registry: ToolRegistry | None = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if registry is not None and registry.get(tool_name) is None:
        raise QueryValidationError("The requested tool is outside this research run's permissions.")
    if is_tool_search_tool(tool_name):
        args = coerce_object(arguments, field_name="arguments")
        resolved_registry = registry or build_tool_registry(service)
        return resolved_registry.search_result(
            args.get("query"),
            namespace=args.get("namespace"),
            limit=int(args.get("limit") or 8),
        )
    if is_research_tool(tool_name):
        return _invoke_research_tool(
            service=service,
            tool_name=tool_name,
            arguments=arguments,
            request_context=request_context,
        )
    if is_zopedia_tool(tool_name):
        return _invoke_zopedia_tool(tool_name=tool_name, arguments=arguments)
    if is_analysis_tool(tool_name):
        return _invoke_analysis_tool(service=service, tool_name=tool_name, arguments=arguments)
    if is_hypothesis_tool(tool_name):
        return _invoke_hypothesis_tool(tool_name=tool_name, arguments=arguments)
    if is_scratchpad_tool(tool_name):
        return _invoke_scratchpad_tool(tool_name=tool_name, run_id=run_id or "default", arguments=arguments)
    if is_anomaly_tool(tool_name):
        return _invoke_anomaly_tool(service=service, arguments=arguments)
    if is_investigator_tool(tool_name):
        return _invoke_investigator_tool(service=service, tool_name=tool_name, arguments=arguments)
    request = build_query_request_for_tool(tool_name=tool_name, arguments=arguments)
    return service.execute(request).to_dict()


__all__ = [
    "ToolRegistry",
    "build_tool_catalog",
    "build_tool_registry",
    "build_query_request_for_tool",
    "is_analysis_tool",
    "is_investigator_tool",
    "is_query_service_tool",
    "is_research_tool",
    "is_scratchpad_tool",
    "is_tool_search_tool",
    "is_zopedia_tool",
    "invoke_tool",
    "tool_schema",
]
