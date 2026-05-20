# Zopedia Product Eval Report

Run ID: `zopedia-eval-20260517-061751`
Started UTC: `2026-05-17T06:17:51.266106+00:00`
Decision: **hold**

## Summary

- Passed: 10
- Warnings: 0
- Failed: 1

## Checks

| Check | Status | Detail | Seconds |
| --- | --- | --- | ---: |
| `llm_runtime` | **pass** | configured | 3.338 |
| `dev_database` | **pass** | Dev database connection is usable. | 1.361 |
| `dev_deployed_endpoints` | **pass** | API health=200; UI root=200. | 0.322 |
| `youtube_transcripts` | **fail** | 0/3 real YouTube transcript fixtures returned text. | 8.452 |
| `source_ingest_and_page_generation` | **pass** | Ingested 3 text fixtures into 23 Zopedia pages; 3 were LLM-enriched. | 104.510 |
| `wiki_search_recall` | **pass** | 3/3 fixture searches found tagged expected content in top 5. | 2.446 |
| `exact_page_read` | **pass** | Exact page read returned the requested tagged source page. | 0.612 |
| `graph_neighborhood_determinism` | **pass** | Manual graph fixture returned 3 nodes and 8 edges. | 3.309 |
| `reviewable_add_delete_update_proposals` | **pass** | Created and listed 3 tagged reviewable proposals. | 1.319 |
| `agent_tool_zopedia_search_read_neighborhood` | **pass** | Zopedia tools returned 3 search rows and neighborhood nodes=3. | 4.325 |
| `zopedia_agent_memory_question` | **pass** | Agent status=completed; tools=['research.market_impact_map', 'research.live_event_evidence', 'zopedia.search_pages', 'zopedia.read_page']; answer_chars=2238. | 95.035 |

## Detailed Metrics

### llm_runtime

Status: **pass**

```json
{
  "deployment": "gpt-5.3-chat",
  "embeddings": "disabled \u2014 EMBEDDING_DEPLOYMENT not set. Semantic retrieval will not work. Set EMBEDDING_DEPLOYMENT to enable.",
  "model": "gpt-5.3-chat",
  "provider": "azure_openai"
}
```

### dev_database

Status: **pass**

```json
{}
```

### dev_deployed_endpoints

Status: **pass**

```json
{
  "api_health_status": 200,
  "ui_root_status": 200
}
```

### youtube_transcripts

Status: **fail**

```json
{
  "fixtures": [
    {
      "chars": 0,
      "provider": "watch_caption_tracks",
      "provider_errors": [
        "youtube_transcript_api:IpBlocked"
      ],
      "status": "ipblocked",
      "url": "https://www.youtube.com/watch?v=BOT2rrm10RM",
      "video_id": "BOT2rrm10RM"
    },
    {
      "chars": 0,
      "provider": "watch_caption_tracks",
      "provider_errors": [
        "youtube_transcript_api:IpBlocked"
      ],
      "status": "ipblocked",
      "url": "https://www.youtube.com/watch?v=t6y_VmxuO28",
      "video_id": "t6y_VmxuO28"
    },
    {
      "chars": 0,
      "provider": "watch_caption_tracks",
      "provider_errors": [
        "youtube_transcript_api:IpBlocked"
      ],
      "status": "ipblocked",
      "url": "https://www.youtube.com/watch?v=n889nI8sR84",
      "video_id": "n889nI8sR84"
    }
  ]
}
```

### source_ingest_and_page_generation

Status: **pass**

```json
{
  "page_count": 23,
  "sources": [
    {
      "enrichment_status": "llm_enriched",
      "page_count": 7,
      "page_titles": [
        "zopedia-eval-20260517-061751 Diesel Pinch Point And Hormuz Risk",
        "Jeff Currie",
        "Diesel Pinch Point",
        "Middle Distillates",
        "Strait of Hormuz Supply Risk",
        "Energy Bottlenecks vs Equity Market Pricing",
        "Diesel Pinch Point And Hormuz Risk (Source)"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061751 Diesel Pinch Point And Hormuz Risk"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 9,
      "page_titles": [
        "zopedia-eval-20260517-061751 Inflation Surge And Bond Market Stress",
        "Inflation Surge And Bond Market Stress",
        "Sticky Service Inflation",
        "Supply Pressure",
        "Nominal Yields",
        "Duration",
        "Refinancing Costs",
        "Recession Risk"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061751 Inflation Surge And Bond Market Stress"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 7,
      "page_titles": [
        "zopedia-eval-20260517-061751 Nasdaq Euphoria Hitting Its Limit",
        "Nasdaq Euphoria and Narrow Leadership",
        "Mega-Cap Concentration",
        "Market Breadth",
        "Index Fragility",
        "Technology Sector Expectations",
        "Source: Nasdaq Euphoria Hitting Its Limit (Eval zopedia-eval-20260517-061751)"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061751 Nasdaq Euphoria Hitting Its Limit"
    }
  ]
}
```

### wiki_search_recall

Status: **pass**

```json
{
  "searches": [
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-061751 diesel pinch point Hormuz equity la la land",
      "result_count": 1,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-061751 Diesel Pinch Point And Hormuz Risk"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-061751 inflation surge bond market disaster recession headed higher",
      "result_count": 5,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-061751 Inflation Surge And Bond Market Stress",
        "Source: Inflation Surge And Bond Market Stress Eval",
        "zopedia-eval-20260517-061751 Nasdaq Euphoria Hitting Its Limit",
        "Inflation Surge And Bond Market Stress",
        "zopedia-eval-20260517-061751 Diesel Pinch Point And Hormuz Risk"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-061751 Nasdaq euphoria hitting its limit mega cap concentration",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-061751 Nasdaq Euphoria Hitting Its Limit",
        "Source: Nasdaq Euphoria Hitting Its Limit (Eval zopedia-eval-20260517-061751)"
      ]
    }
  ]
}
```

### exact_page_read

Status: **pass**

```json
{
  "body_chars": 469,
  "page_id": "zopedia::source::zopedia-eval-20260517-061751-diesel-pinch-point-and-hormuz-risk::f34094f85e0b",
  "title": "zopedia-eval-20260517-061751 Diesel Pinch Point And Hormuz Risk"
}
```

### graph_neighborhood_determinism

Status: **pass**

```json
{
  "deterministic_edges": true,
  "edge_count": 8,
  "node_count": 3,
  "seed_page_id": "zopedia::theme::zopedia-eval-20260517-061751-manual-diesel-pinch-point::095ccb0acfdf"
}
```

### reviewable_add_delete_update_proposals

Status: **pass**

```json
{
  "proposal_hits": 3,
  "titles": [
    "zopedia-eval-20260517-061751 Update page proposal",
    "zopedia-eval-20260517-061751 Delete stale link proposal",
    "zopedia-eval-20260517-061751 Add link proposal"
  ]
}
```

### agent_tool_zopedia_search_read_neighborhood

Status: **pass**

```json
{
  "neighborhood_edges": 4,
  "neighborhood_nodes": 3,
  "page_id": "zopedia::theme::zopedia-eval-20260517-061751-manual-diesel-pinch-point::095ccb0acfdf",
  "read_status": "found",
  "search_rows": 3
}
```

### zopedia_agent_memory_question

Status: **pass**

```json
{
  "answer_markdown": "**Zopedia memory flags the key risk as a diesel (middle\u2011distillate) supply squeeze tied to freight and industry, with Hormuz disruption as a trigger that could tighten global oil flows.** ### Diesel \u201cPinch Point\u201d in Zopedia From Zopedia memory, the stress point is not just crude prices. The issue is **middle\u2011distillate supply \u2014 especially diesel**. - Zopedia notes that **diesel tightness feeds straight into freight, industry, and parts of the real economy**. - When distillate supply gets tight, **transport costs rise and economic pressure spreads through logistics and manufacturing**. - This theme is captured in the Zopedia page **\"zopedia-eval-20260517 Manual Diesel Pinch Point\"**. Source reference: Zopedia page_id **zopedia::theme::zopedia-eval-20260517-061751-manual-diesel-pinch-point::095ccb0acfdf**. ### Hormuz Risk Link Zopedia connects that diesel risk to **Hormuz disruption**. - The **Strait of Hormuz** is a key route for physical oil flows. - If flows tighten or shipping slows there, **crude supply into refining systems can drop**. - That can **tighten refined products even faster than crude headlines suggest**, especially diesel. This relationship appears in **\"zopedia-eval-20260517 Manual Hormuz Risk\"**, which is linked directly from the diesel pinch\u2011point page. ### Market Exposure If This Plays Out The market map tool flags typical winners if oil and distillate spreads tighten: - **XOM**, **CVX**, **OXY** \u2013 upstream producers. Higher crude usually helps. - **VLO**, **MPC**, **PSX** \u2013 refiners. They benefit when **diesel crack spreads widen**. (Theme identified in the market\u2011impact map tool: oil.) ### Limits of the Evidence - Live event confirmation failed due to a tool timeout. - Zopedia memory shows the **framework and thesis**, but **no fresh event confirming a Hormuz disruption right now**. ### What to Watch If this thesis activates, the early signals would likely be: - **Diesel crack spreads rising** - **Freight rates moving up** - Shipping or security headlines around **Hormuz** Those would confirm the diesel pinch point moving from theory to market impact. Sources: Zopedia pages **\"zopedia-eval-20260517 Manual Diesel Pinch Point\"** and **\"zopedia-eval-20260517 Manual Hormuz Risk\"**.",
  "answer_preview": "**Zopedia memory flags the key risk as a diesel (middle\u2011distillate) supply squeeze tied to freight and industry, with Hormuz disruption as a trigger that could tighten global oil flows.** ### Diesel \u201cPinch Point\u201d in Zopedia From Zopedia memory, the stress point is not just crude prices. The issue is **middle\u2011distillate supply \u2014 especially diesel**. - Zopedia notes that **diesel tightness feeds straight into freight, industry, and parts of the real economy**. - When distillate supply gets tight, **transport costs rise and economic pressure spreads through logistics and manufacturing**. - This theme is captured in the Zopedia page **\"zopedia-eval-20260517 Manual Diesel Pinch Point\"**. Source reference: Zopedia page_id **zopedia::theme::zopedia-eval-20260517-061751-manual-diesel-pinch-point::",
  "cites_zopedia_memory": true,
  "confidence": "medium",
  "limitations": [
    "Live event evidence tool timed out so no current news confirmation.",
    "Zopedia memory provides the framework but limited numeric data on diesel spreads or inventory levels.",
    "Hormuz disruption scenario described conceptually, not tied to a specific dated incident."
  ],
  "progress_stages": [
    "start",
    "tool_catalog_ready",
    "hidden_step_heartbeat",
    "hidden_step_heartbeat",
    "bootstrap_start",
    "tool_start",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_complete",
    "tool_start",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_heartbeat",
    "tool_timeout",
    "tool_failed",
    "tool_start",
    "tool_complete",
    "tool_start",
    "tool_complete",
    "planner_skipped",
    "final_synthesis_start",
    "final_synthesis_heartbeat",
    "final_synthesis_heartbeat",
    "final_synthesis_heartbeat",
    "final_synthesis_heartbeat",
    "completed"
  ],
  "status": "completed",
  "tool_names": [
    "research.market_impact_map",
    "research.live_event_evidence",
    "zopedia.search_pages",
    "zopedia.read_page"
  ],
  "zopedia_ref_count": 5
}
```

## Cleanup

```json
{
  "deleted_pages": 26,
  "deleted_proposals": 3,
  "status": "completed"
}
```
