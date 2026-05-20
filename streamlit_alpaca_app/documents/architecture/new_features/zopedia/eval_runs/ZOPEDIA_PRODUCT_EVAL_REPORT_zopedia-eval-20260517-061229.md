# Zopedia Product Eval Report

Run ID: `zopedia-eval-20260517-061229`
Started UTC: `2026-05-17T06:12:29.353797+00:00`
Decision: **hold**

## Summary

- Passed: 9
- Warnings: 1
- Failed: 1

## Checks

| Check | Status | Detail | Seconds |
| --- | --- | --- | ---: |
| `llm_runtime` | **pass** | configured | 3.223 |
| `dev_database` | **pass** | Dev database connection is usable. | 1.293 |
| `dev_deployed_endpoints` | **warn** | API health=None; UI root=200. | 20.285 |
| `youtube_transcripts` | **fail** | 0/3 real YouTube transcript fixtures returned text. | 8.408 |
| `source_ingest_and_page_generation` | **pass** | Ingested 3 text fixtures into 26 Zopedia pages; 3 were LLM-enriched. | 112.581 |
| `wiki_search_recall` | **pass** | 3/3 fixture searches found tagged expected content in top 5. | 2.130 |
| `exact_page_read` | **pass** | Exact page read returned the requested tagged source page. | 0.499 |
| `graph_neighborhood_determinism` | **pass** | Manual graph fixture returned 3 nodes and 8 edges. | 2.712 |
| `reviewable_add_delete_update_proposals` | **pass** | Created and listed 3 tagged reviewable proposals. | 1.098 |
| `agent_tool_zopedia_search_read_neighborhood` | **pass** | Zopedia tools returned 3 search rows and neighborhood nodes=3. | 4.230 |
| `zopedia_agent_memory_question` | **pass** | Agent status=completed; tools=['research.market_impact_map', 'research.live_event_evidence', 'zopedia.search_pages', 'zopedia.read_page']; answer_chars=2005. | 88.340 |

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

Status: **warn**

```json
{
  "api_health_status": null,
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
  "page_count": 26,
  "sources": [
    {
      "enrichment_status": "llm_enriched",
      "page_count": 9,
      "page_titles": [
        "zopedia-eval-20260517-061229 Diesel Pinch Point And Hormuz Risk",
        "Jeff Currie",
        "Diesel Pinch Point",
        "Middle Distillates",
        "Diesel",
        "Hormuz Supply Risk",
        "Strait of Hormuz",
        "Equity Markets"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061229 Diesel Pinch Point And Hormuz Risk"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 11,
      "page_titles": [
        "zopedia-eval-20260517-061229 Inflation Surge And Bond Market Stress",
        "Inflation Surge And Bond Market Stress",
        "Sticky Service Inflation",
        "Supply Pressure",
        "Nominal Yields",
        "Duration",
        "Refinancing Costs",
        "Recession Risk"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061229 Inflation Surge And Bond Market Stress"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 6,
      "page_titles": [
        "zopedia-eval-20260517-061229 Nasdaq Euphoria Hitting Its Limit",
        "Mega-Cap Concentration Masking Market Breadth",
        "Market Breadth",
        "Narrow Leadership Risk in Equity Indexes",
        "Nasdaq Euphoria Hitting Its Limit",
        "Nasdaq"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-061229 Nasdaq Euphoria Hitting Its Limit"
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
      "query": "zopedia-eval-20260517-061229 diesel pinch point Hormuz equity la la land",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "Source: Diesel Pinch Point And Hormuz Risk (Eval zopedia-eval-20260517-061229)",
        "zopedia-eval-20260517-061229 Diesel Pinch Point And Hormuz Risk"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-061229 inflation surge bond market disaster recession headed higher",
      "result_count": 5,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-061229 Inflation Surge And Bond Market Stress",
        "Source: Inflation Surge And Bond Market Stress Eval",
        "Inflation Surge And Bond Market Stress",
        "zopedia-eval-20260517-061229 Nasdaq Euphoria Hitting Its Limit",
        "Source: Diesel Pinch Point And Hormuz Risk (Eval zopedia-eval-20260517-061229)"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-061229 Nasdaq euphoria hitting its limit mega cap concentration",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-061229 Nasdaq Euphoria Hitting Its Limit",
        "Nasdaq Euphoria Hitting Its Limit"
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
  "page_id": "zopedia::source::zopedia-eval-20260517-061229-diesel-pinch-point-and-hormuz-risk::86a2fa5f9dc0",
  "title": "zopedia-eval-20260517-061229 Diesel Pinch Point And Hormuz Risk"
}
```

### graph_neighborhood_determinism

Status: **pass**

```json
{
  "deterministic_edges": true,
  "edge_count": 8,
  "node_count": 3,
  "seed_page_id": "zopedia::theme::zopedia-eval-20260517-061229-manual-diesel-pinch-point::9cc10f42d78e"
}
```

### reviewable_add_delete_update_proposals

Status: **pass**

```json
{
  "proposal_hits": 3,
  "titles": [
    "zopedia-eval-20260517-061229 Update page proposal",
    "zopedia-eval-20260517-061229 Delete stale link proposal",
    "zopedia-eval-20260517-061229 Add link proposal"
  ]
}
```

### agent_tool_zopedia_search_read_neighborhood

Status: **pass**

```json
{
  "neighborhood_edges": 4,
  "neighborhood_nodes": 3,
  "page_id": "zopedia::theme::zopedia-eval-20260517-061229-manual-diesel-pinch-point::9cc10f42d78e",
  "read_status": "found",
  "search_rows": 3
}
```

### zopedia_agent_memory_question

Status: **pass**

```json
{
  "answer_preview": "**Zopedia memory says the key signal is a diesel supply pinch tied to possible Strait of Hormuz disruption, which can hit the real economy before equity markets react.** ### Diesel pinch point (distillate stress) Zopedia memory from **zopedia-eval-20260517-061229** highlights a warning from **Jeff Currie**: watch diesel, not just crude. - **Diesel / middle distillates** power freight, heavy industry, and logistics. - Tight diesel supply creates pressure across shipping and manufacturing first. - This can show up in the commodity chain **before equity indexes react**. In short: if diesel gets tight, the real economy feels it quickly because trucks, ships, and equipment run on it. ### Hormuz risk The same Zopedia note links that diesel stress to the **Strait of Hormuz**. - Hormuz is a key ro",
  "confidence": "medium",
  "limitations": [
    "Live event evidence tool timed out, so no current market confirmation",
    "Zopedia entry summarizes an argument framework rather than real-time market conditions"
  ],
  "progress_stages": [
    "start",
    "tool_catalog_ready",
    "hidden_step_heartbeat",
    "bootstrap_start",
    "tool_start",
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
  "deleted_pages": 29,
  "deleted_proposals": 3,
  "status": "completed"
}
```
