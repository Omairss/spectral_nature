from __future__ import annotations

import json

import numpy as np
import pandas as pd

from services.aql.news_business_resolution import build_news_business_resolution_frames
from services.saa import prepare_zopedia_pages


class _FakeResolutionLLM:
    def __init__(self):
        self.calls: list[str] = []

    def generate_json(self, *, system_prompt, user_prompt, schema_name, schema):
        del system_prompt, schema
        self.calls.append(schema_name)
        payload = json.loads(user_prompt)
        company = payload["company_name"]
        return {
            "slot_facts": {
                "customer_demand": [
                    {
                        "text": f"{company} has a source-backed demand signal in the incoming news.",
                        "source": "news_articles",
                        "confidence": "medium",
                    }
                ],
                "execution_risks": [
                    {
                        "text": "Capacity delivery and financing remain gating risks.",
                        "source": "resolver_context",
                        "confidence": "medium",
                    }
                ],
            },
            "resolved_changes": [
                {
                    "label": "extends_existing_story",
                    "text": "The news extends the demand side of the business story.",
                    "evidence_refs": ["news_articles"],
                }
            ],
            "coherent_story_markdown": f"{company} demand is improving, but execution risk remains.",
            "confidence": "medium",
            "data_gaps": [],
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
    llm = _FakeResolutionLLM()

    frames = build_news_business_resolution_frames(
        news_frame=news,
        company_baselines_frame=baselines,
        fundamentals_frame=fundamentals,
        zopedia_pages_frame=pd.DataFrame(),
        llm_client=llm,
        run_id="run-news",
        asof_time_utc=pd.Timestamp("2026-05-24T12:00:00Z"),
    )

    resolutions = frames["zopedia_news_business_resolutions"]
    pages = frames["zopedia_company_business_memory_pages"]

    assert llm.calls == ["news_business_resolution"]
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
    assert row["status"] == "resolved"
    assert bool(row["cold_start_used"]) is False
    assert json.loads(row["zopedia_page_ids_read_json"])
    assert json.loads(row["proposal_ids_json"]) == []
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
    assert row["status"] == "cold_start_prepared"
    assert bool(row["cold_start_used"]) is True
    assert json.loads(row["zopedia_page_ids_read_json"])
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
    assert row["status"] == "resolved"
    slot_facts = json.loads(row["slot_facts_json"])
    assert "business_model" not in slot_facts
    assert slot_facts["fundamentals"][0]["source"] == "quarterly_fundamentals"
    page_body = frames["zopedia_company_business_memory_pages"].iloc[0]["body_markdown"]
    assert "Market Breadth" not in page_body
    assert "Total Revenue" in page_body


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
