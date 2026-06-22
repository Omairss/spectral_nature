from __future__ import annotations

import json

import numpy as np
import pandas as pd

from services.aql.news_business_resolution import build_news_business_resolution_frames
from services.saa import prepare_zopedia_pages


class _FakeResolutionAgent:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        del kwargs["schema"]
        self.calls.append(dict(kwargs))
        return {
            "status": "completed",
            "payload": {
                "slot_facts": {
                    "customer_demand": [
                        {
                            "text": "CoreWeave has a source-backed demand signal in the incoming news.",
                            "source": "news_articles",
                            "confidence": "medium",
                        }
                    ],
                    "execution_risks": [
                        {
                            "text": "Capacity delivery and financing remain gating risks.",
                            "source": "aql_zopedia_agent::execution_risks::aqlpack::resolution",
                            "confidence": "medium",
                        }
                    ],
                },
                "resolved_changes": [
                    {
                        "label": "extends_existing_story",
                        "text": "The news extends the demand side of the business story.",
                        "evidence_refs": ["news_articles", "aqlpack::resolution"],
                    }
                ],
                "coherent_story_markdown": "CoreWeave demand is improving, but execution risk remains.",
                "confidence": "medium",
                "data_gaps": [],
            },
            "agent_result": {
                "run_id": "agent-resolution",
                "confidence": "medium",
                "aql_evidence_pack_id": "aqlpack::resolution",
                "tool_calls": [{"tool_name": "zopedia.read_page", "status": "completed"}],
            },
        }


def test_news_business_resolution_cold_starts_company_memory_pages():
    news = pd.DataFrame(
        [
            {
                "headline": "CoreWeave expands AI infrastructure agreement with Meta",
                "summary": "Meta is expanding a multi-year AI capacity agreement with CoreWeave.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "source": "ExampleWire",
                "url": "https://example.com/coreweave-meta",
                "symbols": [["CRWV"]],
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {
                "symbol": "CRWV",
                "company_name": "CoreWeave",
                "business_lens": "AI Infrastructure",
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
    agent = _FakeResolutionAgent()

    frames = build_news_business_resolution_frames(
        news_frame=news,
        company_baselines_frame=baselines,
        fundamentals_frame=fundamentals,
        zopedia_pages_frame=pd.DataFrame(),
        zopedia_agent_runner=agent,
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
    )

    resolutions = frames["zopedia_news_business_resolutions"]
    pages = frames["zopedia_company_business_memory_pages"]

    assert [call["schema_name"] for call in agent.calls] == ["news_business_resolution"]
    assert len(resolutions) == 1
    row = resolutions.iloc[0]
    assert row["symbol"] == "CRWV"
    assert row["status"] == "cold_start_prepared"
    assert bool(row["cold_start_used"]) is True
    assert "no_existing_company_memory_page" in json.loads(row["data_gaps_json"])
    slot_facts = json.loads(row["slot_facts_json"])
    assert "business_model" in slot_facts
    assert "customer_demand" in slot_facts
    assert json.loads(row["proposal_ids_json"])
    assert not pages.empty
    assert pages.iloc[0]["page_type"] == "ticker"
    assert "CoreWeave sells specialized AI cloud infrastructure" in pages.iloc[0]["body_markdown"]


def test_news_business_resolution_reads_existing_memory_before_live_gaps():
    zopedia_pages, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "ticker",
                "title": "CoreWeave Business Memory",
                "summary": "CoreWeave sells AI infrastructure to hyperscalers and AI labs.",
                "body_markdown": "CoreWeave has large AI cloud customers and capacity-delivery risk.",
                "entity_refs": ["CRWV", "CoreWeave"],
                "metadata": {"symbol": "CRWV"},
            }
        ],
        now=pd.Timestamp("2026-05-24T12:00:00Z").to_pydatetime(),
    )
    news = pd.DataFrame(
        [
            {
                "headline": "CoreWeave signs new AI lab capacity deal",
                "summary": "The deal adds contracted AI compute demand.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/coreweave-ai-lab",
                "symbols": [["CRWV"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        zopedia_pages_frame=zopedia_pages,
        symbols=["CRWV"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    assert row["status"] == "needs_synthesis"
    assert bool(row["cold_start_used"]) is False
    assert json.loads(row["zopedia_page_ids_read_json"])
    assert json.loads(row["proposal_ids_json"]) == []
    assert row["coherent_story_markdown"] == ""
    assert "business_synthesis_unavailable" in json.loads(row["data_gaps_json"])
    slot_facts = json.loads(row["slot_facts_json"])
    assert "business_model" in slot_facts
    assert slot_facts["business_model"][0]["source"] == "zopedia_business_memory"


def test_news_business_resolution_does_not_promote_related_theme_pages_to_memory_slots():
    zopedia_pages, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "theme",
                "title": "Market Breadth vs Index Strength Divergence",
                "summary": "A market index rises while a large portion of stocks lag.",
                "body_markdown": "This is broad market context, not a company business model.",
                "entity_refs": ["MRVL"],
            }
        ],
        now=pd.Timestamp("2026-05-24T12:00:00Z").to_pydatetime(),
    )
    news = pd.DataFrame(
        [
            {
                "headline": "Marvell earnings preview flags AI networking demand",
                "summary": "Analysts focus on AI networking demand and valuation before earnings.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/marvell-ai-networking",
                "symbols": [["MRVL"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        zopedia_pages_frame=zopedia_pages,
        symbols=["MRVL"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    assert row["status"] == "cold_start_needs_synthesis"
    assert bool(row["cold_start_used"]) is True
    assert json.loads(row["zopedia_page_ids_read_json"])
    assert row["coherent_story_markdown"] == ""
    assert "business_synthesis_unavailable" in json.loads(row["data_gaps_json"])
    slot_facts = json.loads(row["slot_facts_json"])
    business_text = " ".join(item["text"] for item in slot_facts.get("business_model", []))
    assert "Market Breadth" not in business_text


def test_news_business_resolution_does_not_recursively_promote_generated_memory_summary():
    zopedia_pages, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "ticker",
                "title": "MRVL Business Memory",
                "summary": "Market Breadth vs Index Strength Divergence: broad macro context.",
                "body_markdown": "\n".join(
                    [
                        "# MRVL Business Memory",
                        "",
                        "Symbol: MRVL",
                        "",
                        "## Business Model",
                        "- Market Breadth vs Index Strength Divergence: broad macro context. (zopedia_pages)",
                        "",
                        "## Fundamentals",
                        "- Total Revenue: 1000000 (2026-03-31) (quarterly_fundamentals)",
                    ]
                ),
                "entity_refs": ["MRVL"],
                "metadata": {
                    "symbol": "MRVL",
                    "source_type": "news_business_resolution_business_memory",
                },
            }
        ],
        now=pd.Timestamp("2026-05-24T12:00:00Z").to_pydatetime(),
    )
    news = pd.DataFrame(
        [
            {
                "headline": "Marvell earnings preview flags AI networking demand",
                "summary": "Analysts focus on AI networking demand and valuation before earnings.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/marvell-ai-networking",
                "symbols": [["MRVL"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        zopedia_pages_frame=zopedia_pages,
        symbols=["MRVL"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    assert row["status"] == "needs_synthesis"
    assert row["coherent_story_markdown"] == ""
    slot_facts = json.loads(row["slot_facts_json"])
    assert "business_model" not in slot_facts
    assert slot_facts["fundamentals"][0]["source"] == "quarterly_fundamentals"
    assert frames["zopedia_company_business_memory_pages"].empty


def test_news_business_resolution_reads_parquet_symbol_arrays():
    news = pd.DataFrame(
        [
            {
                "headline": "AI infrastructure demand lifts cooling suppliers",
                "summary": "The article names several tickers in a shared demand theme.",
                "published_at": pd.Timestamp("2026-05-24T12:00:00Z"),
                "symbols": [np.array(["AMD", "SNDK"], dtype=object)],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        symbols=["SNDK"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    resolutions = frames["zopedia_news_business_resolutions"]
    assert len(resolutions) == 1
    assert resolutions.iloc[0]["symbol"] == "SNDK"


def test_news_business_resolution_reads_ticker_business_model_stack_first():
    stack = pd.DataFrame(
        [
            {
                "symbol": "MRVL",
                "company_name": "Marvell Technology",
                "status": "ready",
                "confidence": "medium",
                "business_memory_page_id": "ticker::mrvl-business-memory",
                "source_page_ids_json": json.dumps(["ticker::mrvl-business-memory"]),
                "zopedia_page_ids_read_json": json.dumps([]),
                "fundamental_datasets_used_json": json.dumps(["quarterly_fundamentals"]),
                "slot_facts_json": json.dumps(
                    {
                        "business_model": [
                            {
                                "text": "Marvell sells data infrastructure semiconductors for cloud, AI networking, storage, and carrier infrastructure.",
                                "source": "company_baselines",
                                "confidence": "medium",
                            }
                        ],
                        "customer_demand": [
                            {
                                "text": "AI networking demand is the key customer-demand question for the current cycle.",
                                "source": "news_articles",
                                "confidence": "medium",
                            }
                        ],
                        "employee_sentiment": [
                            {
                                "text": "Employee reviews flag culture and retention as a watch item.",
                                "source": "zopedia_business_model_search_result::glassdoor",
                                "confidence": "medium",
                            }
                        ],
                        "execution_risks": [
                            {
                                "text": "Customer concentration and execution around AI networking ramps are the main risks.",
                                "source": "zopedia_business_model_search_result::risk",
                                "confidence": "medium",
                            }
                        ],
                    }
                ),
                "slot_gaps_json": json.dumps(["employee_sentiment", "web_or_developer_attention"]),
                "business_story_markdown": "Marvell is an AI/data-infrastructure semiconductor business.",
                "asof_time_utc": "2026-05-24T12:00:00Z",
            }
        ]
    )
    news = pd.DataFrame(
        [
            {
                "headline": "Marvell earnings preview flags AI networking demand",
                "summary": "Analysts focus on AI networking demand before earnings.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/marvell-ai-networking",
                "symbols": [["MRVL"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        business_model_stack_frame=stack,
        symbols=["MRVL"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    assert row["status"] == "needs_synthesis"
    assert bool(row["cold_start_used"]) is False
    assert "no_existing_company_memory_page" not in json.loads(row["data_gaps_json"])
    assert "ticker::mrvl-business-memory" in json.loads(row["zopedia_page_ids_read_json"])
    slot_facts = json.loads(row["slot_facts_json"])
    assert "customer_demand" in slot_facts
    assert row["coherent_story_markdown"] == ""
    assert "business_synthesis_unavailable" in json.loads(row["data_gaps_json"])


def test_news_business_resolution_rehydrates_search_backed_business_wiki_slots():
    zopedia_pages, _ = prepare_zopedia_pages(
        [
            {
                "page_type": "ticker",
                "title": "CoreWeave Business Memory",
                "summary": "CoreWeave sells AI cloud infrastructure.",
                "body_markdown": "\n".join(
                    [
                        "# CoreWeave Business Memory",
                        "",
                        "Symbol: CRWV",
                        "",
                        "## Business Model",
                        "- CoreWeave sells specialized AI cloud infrastructure and GPU capacity. (company_baselines)",
                        "",
                        "## Employee Sentiment",
                        "- Employee reviews point to mixed morale during rapid scaling. (zopedia_business_model_search_result::glassdoor)",
                        "",
                        "## Web Or Developer Attention",
                        "- Similarweb-style traffic signal shows rising attention to the company site. (zopedia_business_model_search_result::traffic)",
                    ]
                ),
                "entity_refs": ["CRWV", "CoreWeave"],
                "metadata": {
                    "symbol": "CRWV",
                    "source_type": "ticker_business_model_stack_business_memory",
                },
            }
        ],
        now=pd.Timestamp("2026-05-24T12:00:00Z").to_pydatetime(),
    )
    news = pd.DataFrame(
        [
            {
                "headline": "CoreWeave expands AI infrastructure agreement with Meta",
                "summary": "Meta is expanding contracted AI capacity with CoreWeave.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/coreweave-meta",
                "symbols": [["CRWV"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        zopedia_pages_frame=zopedia_pages,
        symbols=["CRWV"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    slot_facts = json.loads(row["slot_facts_json"])
    assert "employee_sentiment" in slot_facts
    assert "web_or_developer_attention" in slot_facts
    assert row["status"] == "needs_synthesis"
    assert row["coherent_story_markdown"] == ""
    assert "business_synthesis_unavailable" in json.loads(row["data_gaps_json"])


def test_news_business_resolution_does_not_synthesize_business_story_without_llm():
    stack = pd.DataFrame(
        [
            {
                "symbol": "CRWV",
                "company_name": "CoreWeave, Inc. - Class A Common Stock",
                "status": "ready",
                "confidence": "medium",
                "business_memory_page_id": "ticker::crwv-business-memory",
                "source_page_ids_json": json.dumps(["ticker::crwv-business-memory"]),
                "zopedia_page_ids_read_json": json.dumps([]),
                "fundamental_datasets_used_json": json.dumps(["quarterly_fundamentals"]),
                "slot_facts_json": json.dumps(
                    {
                        "business_model": [
                            {
                                "text": "CoreWeave sells specialized AI cloud infrastructure and GPU capacity.",
                                "source": "company_baselines",
                                "confidence": "medium",
                            }
                        ],
                        "customer_demand": [
                            {
                                "text": "Meta is expanding contracted AI capacity with CoreWeave.",
                                "source": "news_articles",
                                "confidence": "medium",
                            }
                        ],
                        "fundamentals": [
                            {
                                "text": "Revenue doubled, while operating loss widened.",
                                "source": "zopedia_business_model_search_result::quarterly",
                                "confidence": "medium",
                            }
                        ],
                        "employee_sentiment": [
                            {
                                "text": "Employee reviews are mixed, with work-life balance pressure.",
                                "source": "zopedia_business_model_search_result::glassdoor",
                                "confidence": "medium",
                            }
                        ],
                    }
                ),
                "slot_gaps_json": json.dumps(["cash_and_runway", "web_or_developer_attention"]),
                "business_story_markdown": "CoreWeave demand is strong but economics are pressured.",
                "asof_time_utc": "2026-05-24T12:00:00Z",
            }
        ]
    )
    news = pd.DataFrame(
        [
            {
                "headline": "CoreWeave expands AI infrastructure agreement with Meta",
                "summary": "Meta expands contracted AI capacity with CoreWeave.",
                "published_at": pd.Timestamp("2026-05-21T12:00:00Z"),
                "url": "https://example.com/coreweave-meta",
                "symbols": [["CRWV"]],
            }
        ]
    )

    frames = build_news_business_resolution_frames(
        news_frame=news,
        business_model_stack_frame=stack,
        symbols=["CRWV"],
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
        write_policy="none",
    )

    row = frames["zopedia_news_business_resolutions"].iloc[0]
    slot_facts = json.loads(row["slot_facts_json"])
    assert row["status"] == "needs_synthesis"
    assert row["coherent_story_markdown"] == ""
    assert "business_synthesis_unavailable" in json.loads(row["data_gaps_json"])
    assert slot_facts["fundamentals"][0]["text"] == "Revenue doubled, while operating loss widened."
    assert slot_facts["employee_sentiment"][0]["text"] == "Employee reviews are mixed, with work-life balance pressure."
