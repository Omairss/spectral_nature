from __future__ import annotations

import json

import pandas as pd

from services.aql.business_model_stack import (
    TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE,
    build_business_model_research_query_plan,
    build_ticker_business_model_stack_frames,
)
from services.saa import prepare_zopedia_pages
from services.web_research import WebSearchResult


class _FakeStackAgent:
    def __init__(self, *, slot_verdicts: dict | None = None, stack_slot_facts: dict | None = None):
        self.calls: list[dict[str, object]] = []
        self.slot_verdicts = slot_verdicts or {}
        self.stack_slot_facts = stack_slot_facts or {}

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        schema_name = kwargs["schema_name"]
        if schema_name == "ticker_business_model_research_query_plan":
            return {
                "status": "completed",
                "payload": {
                    "resolved_company_name": "CoreWeave",
                    "company_aliases": ["CoreWeave", "CRWV"],
                    "queries": [
                        {
                            "slot": slot,
                            "question": question,
                            "query": f"CoreWeave CRWV {slot.replace('_', ' ')}",
                            "topic": "general",
                            "source_intent": "llm_planned_business_evidence",
                            "requires_page_open": True,
                            "priority": priority,
                            "reason": f"Resolve {slot}.",
                        }
                        for priority, (slot, question) in enumerate(
                            [
                                ("business_model", "What does the company sell and how does it make money?"),
                                ("products_and_services", "Which products and services matter most?"),
                                ("customer_segments", "Who buys from the company?"),
                                ("customer_demand", "Is demand improving or weakening?"),
                                ("fundamentals", "What do revenue, margins, cash flow, balance sheet, and debt say?"),
                                ("execution_risks", "What can break the business plan?"),
                            ],
                            start=1,
                        )
                    ],
                    "data_gaps": [],
                },
                "agent_result": {"run_id": "agent-plan", "confidence": "medium", "tool_calls": []},
            }
        if schema_name == "ticker_business_model_research_dossier":
            return {
                "status": "completed",
                "payload": {
                    "source_inventory": [],
                    "slot_findings": {},
                    "source_gaps": [],
                    "next_research_actions": [],
                },
                "agent_result": {"run_id": "agent-dossier", "confidence": "medium", "tool_calls": []},
            }
        if schema_name == "ticker_business_model_slot_verdict":
            slot = str(kwargs["surface"]).split(".")[-1]
            verdict = self.slot_verdicts.get(slot)
            if verdict:
                return {
                    "status": "completed",
                    "payload": {
                        "slot": slot,
                        "verdict_markdown": verdict,
                        "confidence": "medium",
                        "evidence_refs": [f"zopedia.read_page::{slot}"],
                        "data_gaps": [],
                    },
                    "agent_result": {
                        "run_id": f"agent-{slot}",
                        "confidence": "medium",
                        "aql_evidence_pack_id": f"aqlpack::{slot}",
                        "tool_calls": [{"tool_name": "zopedia.search_pages", "status": "completed"}],
                    },
                }
            return {
                "status": "completed",
                "payload": {
                    "slot": slot,
                    "verdict_markdown": "",
                    "confidence": "low",
                    "evidence_refs": [],
                    "data_gaps": [f"{slot} evidence missing"],
                },
                "agent_result": {"run_id": f"agent-{slot}", "confidence": "low", "tool_calls": []},
            }
        return {
            "status": "completed",
            "payload": {
                "resolved_company_name": "CoreWeave",
                "slot_facts": self.stack_slot_facts,
                "business_story_markdown": "CoreWeave sells AI infrastructure; demand is improving, but workforce evidence remains incomplete.",
                "confidence": "medium",
                "slot_gaps": ["employee_sentiment", "web_or_developer_attention"],
            },
            "agent_result": {
                "run_id": "agent-stack",
                "confidence": "medium",
                "aql_evidence_pack_id": "aqlpack::stack",
                "tool_calls": [{"tool_name": "zopedia.search_pages", "status": "completed"}],
            },
        }


class _FakeSearchClient:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def search(self, query, *, news=False, num=10, max_results=None, topic=None):
        self.calls.append({"query": query, "news": news, "num": num, "max_results": max_results, "topic": topic})
        count = max_results or num or 1
        return [
            WebSearchResult(
                provider="fake",
                title="CoreWeave expands AI infrastructure demand with Meta",
                url=f"https://example.com/{len(self.calls)}",
                snippet="Meta expands contracted AI infrastructure capacity with CoreWeave.",
                raw_text="Meta expands contracted AI infrastructure capacity with CoreWeave.",
                source="ExampleWire",
                published_at="2026-05-21",
            )
        ][:count]


class _FailingAgent:
    def __call__(self, **kwargs):
        del kwargs
        raise RuntimeError("provider rejected key sk-test1234567890abcdef")


def test_business_model_research_plan_uses_facet_questions_not_fixed_source_templates():
    plan = build_business_model_research_query_plan(
        symbol="MAIN",
        company_name="Main Street Capital Corporation",
        missing_slots=["business_model", "fundamentals"],
        max_queries=2,
    )

    queries = [item["query"] for item in plan]
    assert '"Main Street Capital Corporation" MAIN' in queries[0]
    assert "What does the company sell" in queries[0]
    assert "What do revenue, margins" in queries[1]
    assert "investor relations" not in " ".join(queries).lower()
    assert "glassdoor" not in " ".join(queries).lower()


def test_ticker_business_model_stack_redacts_provider_secret_shaped_warnings():
    frames = build_ticker_business_model_stack_frames(
        symbols=["CRWV"],
        company_baselines_frame=pd.DataFrame([{"symbol": "CRWV", "company_name": "CoreWeave"}]),
        zopedia_agent_runner=_FailingAgent(),
        run_id="run-stack",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    warnings = frames["zopedia_ticker_business_model_stacks"].iloc[0]["synthesis_warnings_json"]
    assert "sk-test" not in warnings
    assert "[redacted_api_key]" in warnings


def test_ticker_business_model_stack_does_not_promote_search_evidence_without_zopedia_verdict():
    serp = _FakeSearchClient()

    frames = build_ticker_business_model_stack_frames(
        symbols=["CRWV"],
        company_baselines_frame=pd.DataFrame([{"symbol": "CRWV", "company_name": "CoreWeave"}]),
        serp_client=serp,
        execute_research=True,
        max_research_queries=3,
        max_search_results_per_query=1,
        llm_client=None,
        run_id="run-stack",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_ticker_business_model_stacks"].iloc[0]
    assert row["status"] == "insufficient_evidence"
    assert json.loads(row["search_result_ids_json"]) == []
    assert json.loads(row["slot_facts_json"]) == {}
    assert frames["zopedia_company_business_memory_pages"].empty
    assert "aql_zopedia_research_plan_unavailable::agent_not_configured" in json.loads(row["synthesis_warnings_json"])


def test_ticker_business_model_stack_promotes_only_zopedia_agent_verdicts_and_creates_memory_page():
    baselines = pd.DataFrame(
        [
            {
                "symbol": "CRWV",
                "company_name": "CoreWeave",
                "company_background_text": "CoreWeave sells specialized AI cloud infrastructure and GPU capacity.",
            }
        ]
    )
    fundamentals = pd.DataFrame(
        [
            {
                "ticker": "CRWV",
                "statement": "income",
                "metric": "Total Revenue",
                "report_date": pd.Timestamp("2026-03-31"),
                "value": 2_078_000_000,
            }
        ]
    )
    agent = _FakeStackAgent(
        slot_verdicts={
            "business_model": "CoreWeave sells GPU cloud capacity to AI customers; the operating question is contracted utilization and capex funding.",
            "products_and_services": "CoreWeave's main product is specialized cloud infrastructure built around GPU capacity for AI workloads.",
            "customer_segments": "CoreWeave primarily serves AI model builders and enterprise customers that need accelerated compute.",
            "customer_demand": "Customer demand is improving because customers are contracting for AI infrastructure capacity.",
            "fundamentals": "Fundamentals are scaling: revenue is visible in quarterly data, but capex and financing still need monitoring.",
            "execution_risks": "Execution risk centers on funding capex, securing GPU supply, and converting contracted demand into profitable utilization.",
        }
    )

    frames = build_ticker_business_model_stack_frames(
        symbols=["CRWV"],
        company_baselines_frame=baselines,
        fundamentals_frame=fundamentals,
        zopedia_agent_runner=agent,
        run_id="run-stack",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
    )

    row = frames["zopedia_ticker_business_model_stacks"].iloc[0]
    pages = frames["zopedia_company_business_memory_pages"]
    slot_facts = json.loads(row["slot_facts_json"])
    slot_calls = [call for call in agent.calls if call["schema_name"] == "ticker_business_model_slot_verdict"]
    assert slot_calls
    assert row["status"] == "ready"
    assert slot_facts["business_model"][0]["source"].startswith("aql_zopedia_agent::business_model::")
    assert "GPU cloud capacity" in slot_facts["business_model"][0]["text"]
    assert not pages.empty
    metadata = json.loads(pages.iloc[0]["metadata_json"])
    assert metadata["source_type"] == TICKER_BUSINESS_MODEL_STACK_SOURCE_TYPE


def test_ticker_business_model_stack_filters_source_noise_but_keeps_clean_evidence_for_agent_context(monkeypatch):
    import services.aql.business_model_stack as business_model_stack

    class _OfficialSearchClient:
        def search(self, query, *, news=False, num=10, max_results=None, topic=None):
            del query, news, num, max_results, topic
            return [
                WebSearchResult(
                    provider="fake",
                    title="Annual Reports",
                    url="https://www.mainstcapital.com/investors/sec-filings/annual-reports",
                    snippet="Annual reports and 10-K filing index.",
                    raw_text="Annual reports and 10-K filing index.",
                    source="Main Street Capital Corporation",
                    published_at="",
                ),
                WebSearchResult(
                    provider="fake",
                    title="SEC filing",
                    url="https://www.sec.gov/example-10k.htm",
                    snippet="Main Street Capital Corporation company filing.",
                    raw_text="Main Street Capital Corporation company filing.",
                    source="SEC.gov",
                    published_at="",
                ),
            ]

    def _fake_open(url, max_chars=4000):
        if "annual-reports" in url:
            return {
                "text": "Filing Type: View All 10-K Year: View All 2026 Date Form Description PDF XBRL Pages",
                "mode": "scrapling",
                "warning": "",
                "quality_issue": "",
            }
        return {
            "text": "Main Street Capital Corporation source filing text for the Zopedia agent to inspect.",
            "mode": "http",
            "warning": "",
            "quality_issue": "",
        }

    monkeypatch.setattr(business_model_stack, "_open_research_page_payload", _fake_open)

    frames = build_ticker_business_model_stack_frames(
        symbols=["MAIN"],
        company_baselines_frame=pd.DataFrame([{"symbol": "MAIN", "company_name": "Main Street Capital Corporation"}]),
        serp_client=_OfficialSearchClient(),
        execute_research=True,
        max_research_queries=1,
        max_search_results_per_query=2,
        zopedia_agent_runner=_FakeStackAgent(),
        run_id="run-stack",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    results = frames["zopedia_business_model_search_results"]
    annual_report = results[results["title"].eq("Annual Reports")].iloc[0]
    sec_result = results[results["source"].eq("SEC.gov")].iloc[0]
    row = frames["zopedia_ticker_business_model_stacks"].iloc[0]
    assert annual_report["page_quality_issue"] == "source_boilerplate"
    assert "Filing Type: View All" not in annual_report["raw_text"]
    assert sec_result["page_quality_issue"] == ""
    assert json.loads(row["slot_facts_json"]) == {}
    assert row["status"] == "needs_zopedia_verdict"


def test_open_research_page_payload_requires_main_content_crawler_path(monkeypatch):
    import services.aql.business_model_stack as business_model_stack

    calls: list[dict[str, object]] = []

    def _fake_browse_page(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return {
            "url": url,
            "final_url": url,
            "title": "Main Street Capital 10-K",
            "excerpt": "Business section",
            "text": "Main Street Capital provides customized debt and equity capital to lower middle market companies.",
            "mode": "scrapling",
            "warning": "Playwright quality issue: shallow:12<800.",
        }

    monkeypatch.setattr(business_model_stack, "browse_page", _fake_browse_page)

    payload = business_model_stack._open_research_page_payload("https://www.sec.gov/example", max_chars=1200)

    assert "customized debt and equity capital" in payload["text"]
    assert payload["mode"] == "scrapling"
    assert calls == [
        {
            "url": "https://www.sec.gov/example",
            "max_text_chars": 1200,
            "require_main_content": True,
            "min_text_chars": 800,
        }
    ]


def test_ticker_business_model_stack_keeps_related_theme_as_context_only():
    zopedia_pages, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "theme",
                "title": "Market Breadth vs Index Strength Divergence",
                "summary": "A market index rises while many stocks lag.",
                "body_markdown": "This is market context, not a company business model.",
                "entity_refs": ["MRVL"],
            }
        ],
        now=pd.Timestamp("2026-05-24T12:00:00Z").to_pydatetime(),
    )

    frames = build_ticker_business_model_stack_frames(
        symbols=["MRVL"],
        zopedia_pages_frame=zopedia_pages,
        run_id="run-stack",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_ticker_business_model_stacks"].iloc[0]
    assert row["status"] == "insufficient_evidence"
    assert json.loads(row["slot_facts_json"]) == {}
    assert row["business_memory_body_markdown"] == ""
