# Zopedia Product Eval Report

Run ID: `zopedia-eval-20260517-060545`
Started UTC: `2026-05-17T06:05:45.260011+00:00`
Decision: **hold**

## Summary

- Passed: 9
- Warnings: 0
- Failed: 2

## Checks

| Check | Status | Detail | Seconds |
| --- | --- | --- | ---: |
| `llm_runtime` | **pass** | configured | 3.645 |
| `dev_database` | **pass** | Dev database connection is usable. | 1.705 |
| `dev_deployed_endpoints` | **pass** | API health=200; UI root=200. | 0.349 |
| `youtube_transcripts` | **fail** | 0/3 real YouTube transcript fixtures returned text. | 8.195 |
| `source_ingest_and_page_generation` | **pass** | Ingested 3 text fixtures into 21 Zopedia pages; 3 were LLM-enriched. | 123.979 |
| `wiki_search_recall` | **fail** | 2/3 fixture searches found tagged expected content in top 5. | 2.375 |
| `exact_page_read` | **pass** | Exact page read returned the requested tagged source page. | 0.623 |
| `graph_neighborhood_determinism` | **pass** | Manual graph fixture returned 3 nodes and 8 edges. | 3.479 |
| `reviewable_add_delete_update_proposals` | **pass** | Created and listed 3 tagged reviewable proposals. | 1.333 |
| `agent_tool_zopedia_search_read_neighborhood` | **pass** | Zopedia tools returned 3 search rows and neighborhood nodes=3. | 4.568 |
| `zopedia_agent_memory_question` | **pass** | Agent status=completed; tools=['research.market_impact_map', 'research.live_event_evidence', 'zopedia.search_pages']; answer_chars=1500. | 94.146 |

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
  "page_count": 21,
  "sources": [
    {
      "enrichment_status": "llm_enriched",
      "page_count": 6,
      "page_titles": [
        "zopedia-eval-20260517-060545 Diesel Pinch Point And Hormuz Risk",
        "Diesel Pinch Point",
        "Strait of Hormuz Risk",
        "Jeff Currie",
        "Middle Distillates",
        "Source: Diesel Pinch Point And Hormuz Risk (zopedia-eval-20260517-060545)"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-060545 Diesel Pinch Point And Hormuz Risk"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 9,
      "page_titles": [
        "zopedia-eval-20260517-060545 Inflation Surge And Bond Market Stress",
        "Source: Inflation Surge And Bond Market Stress (zopedia-eval-20260517-060545)",
        "Sticky Service Inflation",
        "Supply Pressure",
        "Nominal Yields",
        "Duration Risk",
        "Refinancing Risk",
        "Bond Market Stress"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-060545 Inflation Surge And Bond Market Stress"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 6,
      "page_titles": [
        "zopedia-eval-20260517-060545 Nasdaq Euphoria Hitting Its Limit",
        "Market Breadth",
        "Mega-Cap Concentration",
        "Nasdaq",
        "Narrow Leadership Risk in Technology-Led Markets",
        "Source: Nasdaq Euphoria Hitting Its Limit (Zopedia Eval 2026-05-17)"
      ],
      "status": "stored",
      "title": "zopedia-eval-20260517-060545 Nasdaq Euphoria Hitting Its Limit"
    }
  ]
}
```

### wiki_search_recall

Status: **fail**

```json
{
  "searches": [
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-060545 diesel pinch point Hormuz equity la la land",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "Source: Diesel Pinch Point And Hormuz Risk (zopedia-eval-20260517-060545)",
        "zopedia-eval-20260517-060545 Diesel Pinch Point And Hormuz Risk"
      ]
    },
    {
      "expected_hit": false,
      "query": "zopedia-eval-20260517-060545 inflation surge bond market disaster recession headed higher",
      "result_count": 0,
      "tag_hit": false,
      "top_titles": []
    },
    {
      "expected_hit": true,
      "query": "zopedia-eval-20260517-060545 Nasdaq euphoria hitting its limit mega cap concentration",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "zopedia-eval-20260517-060545 Nasdaq Euphoria Hitting Its Limit",
        "Source: Nasdaq Euphoria Hitting Its Limit (Zopedia Eval 2026-05-17)"
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
  "page_id": "zopedia::source::zopedia-eval-20260517-060545-diesel-pinch-point-and-hormuz-risk::b2df391ce5bf",
  "title": "zopedia-eval-20260517-060545 Diesel Pinch Point And Hormuz Risk"
}
```

### graph_neighborhood_determinism

Status: **pass**

```json
{
  "deterministic_edges": true,
  "edge_count": 8,
  "node_count": 3,
  "seed_page_id": "zopedia::theme::zopedia-eval-20260517-060545-manual-diesel-pinch-point::f20949cbaca5"
}
```

### reviewable_add_delete_update_proposals

Status: **pass**

```json
{
  "proposal_hits": 3,
  "titles": [
    "zopedia-eval-20260517-060545 Update page proposal",
    "zopedia-eval-20260517-060545 Delete stale link proposal",
    "zopedia-eval-20260517-060545 Add link proposal"
  ]
}
```

### agent_tool_zopedia_search_read_neighborhood

Status: **pass**

```json
{
  "neighborhood_edges": 4,
  "neighborhood_nodes": 3,
  "page_id": "zopedia::theme::zopedia-eval-20260517-060545-manual-diesel-pinch-point::f20949cbaca5",
  "read_status": "found",
  "search_rows": 3
}
```

### zopedia_agent_memory_question

Status: **pass**

```json
{
  "answer_preview": "**No Zopedia memory found for `zopedia-eval-20260517-060545` on a diesel pinch point or Hormuz risk.** ### Zopedia Memory Check - Zopedia page search returned **0 results** for `zopedia-eval-20260517-060545` tied to **diesel pinch point** or **Hormuz risk**. - That means there is **no stored Zopedia page or note** available in the current memory index for this topic. ### Market Impact Map (Theme Signal Only) The research tool still flags **oil exposure** as the likely theme if a diesel supply squeeze or Hormuz disruption were in play. Likely symbols to watch: - **XOM** \u2013 integrated major; higher crude/product prices typically **bias up**. - **CVX** \u2013 similar integrated exposure; **bias up** with stronger oil prices. - **OXY** \u2013 upstream producer; tends to **move with crude spikes**. - **PS",
  "confidence": "medium",
  "limitations": [
    "Zopedia memory search returned zero pages for the eval ID referenced.",
    "Live event evidence tool timed out, so no current news confirmation.",
    "Market impact mapping provides only a thematic expectation, not confirmation of the event."
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
    "zopedia.search_pages"
  ],
  "zopedia_ref_count": 0
}
```

## Cleanup

```json
{
  "deleted_pages": 24,
  "deleted_proposals": 3,
  "status": "completed"
}
```
