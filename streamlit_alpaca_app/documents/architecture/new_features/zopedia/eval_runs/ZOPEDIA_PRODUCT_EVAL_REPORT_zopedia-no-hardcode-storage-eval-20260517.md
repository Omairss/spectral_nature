# Zopedia Product Eval Report

Run ID: `zopedia-no-hardcode-storage-eval-20260517`
Started UTC: `2026-05-17T07:23:30.737194+00:00`
Decision: **go**

## Summary

- Passed: 8
- Warnings: 0
- Failed: 0

## Checks

| Check | Status | Detail | Seconds |
| --- | --- | --- | ---: |
| `llm_runtime` | **pass** | configured | 3.307 |
| `dev_database` | **pass** | Dev database connection is usable. | 1.296 |
| `source_ingest_and_page_generation` | **pass** | Ingested 3 text fixtures into 19 Zopedia pages; 3 were LLM-enriched. | 132.129 |
| `wiki_search_recall` | **pass** | 3/3 fixture searches found tagged expected content in top 5. | 2.070 |
| `exact_page_read` | **pass** | Exact page read returned the requested tagged source page. | 0.500 |
| `graph_neighborhood_determinism` | **pass** | Manual graph fixture returned 3 nodes and 8 edges. | 2.801 |
| `reviewable_add_delete_update_proposals` | **pass** | Created and listed 3 tagged reviewable proposals. | 1.102 |
| `agent_tool_zopedia_search_read_neighborhood` | **pass** | Zopedia tools returned 3 search rows and neighborhood nodes=3. | 4.289 |

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

### source_ingest_and_page_generation

Status: **pass**

```json
{
  "page_count": 19,
  "sources": [
    {
      "enrichment_status": "llm_enriched",
      "page_count": 5,
      "page_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Diesel Pinch Point And Hormuz Risk",
        "Diesel Pinch Point",
        "Strait of Hormuz Supply Risk",
        "Jeff Currie",
        "Source: Diesel Pinch Point And Hormuz Risk (Product Eval)"
      ],
      "status": "stored",
      "title": "zopedia-no-hardcode-storage-eval-20260517 Diesel Pinch Point And Hormuz Risk"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 8,
      "page_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Inflation Surge And Bond Market Stress",
        "Inflation Surge And Bond Market Stress",
        "Sticky Service Inflation",
        "Bond Market Stress",
        "Nominal Yields",
        "Refinancing Costs",
        "Recession Risk",
        "Source: Inflation Surge And Bond Market Stress (Eval zopedia-no-hardcode-storage-eval-20260517)"
      ],
      "status": "stored",
      "title": "zopedia-no-hardcode-storage-eval-20260517 Inflation Surge And Bond Market Stress"
    },
    {
      "enrichment_status": "llm_enriched",
      "page_count": 6,
      "page_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Nasdaq Euphoria Hitting Its Limit",
        "Nasdaq Euphoria and Narrow Leadership Risk",
        "Mega-Cap Concentration",
        "Market Breadth",
        "Nasdaq Composite",
        "Source: Nasdaq Euphoria Hitting Its Limit (Product Eval)"
      ],
      "status": "stored",
      "title": "zopedia-no-hardcode-storage-eval-20260517 Nasdaq Euphoria Hitting Its Limit"
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
      "query": "zopedia-no-hardcode-storage-eval-20260517 diesel pinch point Hormuz equity la la land",
      "result_count": 1,
      "tag_hit": true,
      "top_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Diesel Pinch Point And Hormuz Risk"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-no-hardcode-storage-eval-20260517 inflation surge bond market disaster recession headed higher",
      "result_count": 5,
      "tag_hit": true,
      "top_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Inflation Surge And Bond Market Stress",
        "Source: Inflation Surge And Bond Market Stress (Eval zopedia-no-hardcode-storage-eval-20260517)",
        "zopedia-no-hardcode-eval-20260517b Inflation Surge And Bond Market Stress",
        "zopedia-no-hardcode-eval-20260517 Inflation Surge And Bond Market Stress",
        "Source: Inflation Surge And Bond Market Stress (zopedia-no-hardcode-eval-20260517b)"
      ]
    },
    {
      "expected_hit": true,
      "query": "zopedia-no-hardcode-storage-eval-20260517 Nasdaq euphoria hitting its limit mega cap concentration",
      "result_count": 2,
      "tag_hit": true,
      "top_titles": [
        "zopedia-no-hardcode-storage-eval-20260517 Nasdaq Euphoria Hitting Its Limit",
        "Source: Nasdaq Euphoria Hitting Its Limit (Product Eval)"
      ]
    }
  ]
}
```

### exact_page_read

Status: **pass**

```json
{
  "body_chars": 482,
  "page_id": "zopedia::source::zopedia-no-hardcode-storage-eval-20260517-diesel-pinch-point-and::cb7ff2015f9a",
  "title": "zopedia-no-hardcode-storage-eval-20260517 Diesel Pinch Point And Hormuz Risk"
}
```

### graph_neighborhood_determinism

Status: **pass**

```json
{
  "deterministic_edges": true,
  "edge_count": 8,
  "node_count": 3,
  "seed_page_id": "zopedia::theme::zopedia-no-hardcode-storage-eval-20260517-manual-diesel-pinch-po::6bd71f543f36"
}
```

### reviewable_add_delete_update_proposals

Status: **pass**

```json
{
  "proposal_hits": 3,
  "titles": [
    "zopedia-no-hardcode-storage-eval-20260517 Update page proposal",
    "zopedia-no-hardcode-storage-eval-20260517 Delete stale link proposal",
    "zopedia-no-hardcode-storage-eval-20260517 Add link proposal"
  ]
}
```

### agent_tool_zopedia_search_read_neighborhood

Status: **pass**

```json
{
  "neighborhood_edges": 4,
  "neighborhood_nodes": 3,
  "page_id": "zopedia::theme::zopedia-no-hardcode-storage-eval-20260517-manual-diesel-pinch-po::6bd71f543f36",
  "read_status": "found",
  "search_rows": 3
}
```

## Cleanup

```json
{
  "deleted_pages": 22,
  "deleted_proposals": 3,
  "status": "completed"
}
```
