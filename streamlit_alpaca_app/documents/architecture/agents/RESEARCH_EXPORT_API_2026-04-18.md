# Research Export API

Date: 2026-04-18

## Goal

Expose an async export endpoint that dumps all retained research data within a time window into an organized folder structure, zips it, uploads to blob storage, and returns a download link. Consumers: local analysis, offline reading, feeding into external systems.

## Current State

### What exists

- **Storage:** PostgreSQL (`saa_documents`, `saa_evidence_chunks`) + Azure Blob (raw JSON per document) + pipeline datasets (Parquet)
- **Existing API endpoints** (all via `POST /v1/dataset/{name}`):
  - `saa_document_search` — search retained documents from `saa_documents`
  - `saa_chunk_search` — search evidence chunks from `saa_evidence_chunks`
  - `saa_document` — single document with raw text fetched from blob
  - `attention_research_bundle` — single bundle from `attention_bundle_snapshots.payload_json`
- **Raw text:** full article/filing text stored in Azure Blob at `saa/raw_documents/provider={slug}/dt={date}/document={id}/content={sha}.json`
- **Search text:** first ~6K chars stored directly in `saa_documents.search_text` (cheap, always available)
- **Summaries:** already generated and stored in `attention_bundle_snapshots` as `payload_json` — not generated on export

### What's missing

1. No batch export that packages everything into a downloadable archive
2. No async job mechanism for long-running export builds
3. No organized folder structure for browsing research offline

---

## API Design

### Two-step async flow

**Step 1: Start export**

```
POST /v1/research/export
{
  "start_date": "2026-04-11",
  "end_date": "2026-04-18"
}
```

Response (immediate):
```json
{
  "job_id": "exp-20260418-143012-a1b2",
  "status": "building",
  "created_at": "2026-04-18T14:30:12Z",
  "filters": {
    "start_date": "2026-04-11",
    "end_date": "2026-04-18"
  }
}
```

**Step 2: Check status / get download link**

```
GET /v1/research/export/exp-20260418-143012-a1b2
```

While building:
```json
{
  "job_id": "exp-20260418-143012-a1b2",
  "status": "building",
  "progress": {
    "documents_processed": 87,
    "documents_total": 142
  }
}
```

When ready:
```json
{
  "job_id": "exp-20260418-143012-a1b2",
  "status": "ready",
  "download_url": "https://{storage}.blob.core.windows.net/exports/exp-20260418-143012-a1b2.zip?sv=...",
  "expires_at": "2026-04-19T14:30:12Z",
  "stats": {
    "total_documents": 142,
    "total_summaries": 18,
    "zip_size_bytes": 2841600,
    "date_range": {
      "earliest": "2026-04-11T06:12:00Z",
      "latest": "2026-04-17T22:45:00Z"
    },
    "providers": {
      "tavily": 68,
      "serpapi": 52,
      "seeking_alpha": 14,
      "sec": 8
    },
    "tickers": ["AAPL", "NVDA", "MSFT", "TSLA", "..."]
  }
}
```

On failure:
```json
{
  "job_id": "exp-20260418-143012-a1b2",
  "status": "failed",
  "error": "Blob storage unavailable"
}
```

### Request parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `start_date` | string | yes | Inclusive start (YYYY-MM-DD or ISO 8601) |
| `end_date` | string | yes | Inclusive end |

Time window is the only required filter. Everything in the window gets exported.

### Auth

Scope: `SCOPE_QUERY_EXECUTE` + `SCOPE_DATASET_READ`

Download URL is a time-limited SAS token on Azure Blob (expires after 24h).

---

## Folder Structure

```
export_2026-04-11_to_2026-04-18/
│
├── 2026-04-17/
│   ├── AAPL/
│   │   ├── tavily/
│   │   │   ├── reuters-apple-posts-record-q2_a1b2c3d4.json
│   │   │   └── cnbc-apple-services-growth_c9d8e7f6.json
│   │   ├── serpapi/
│   │   │   └── marketwatch-aapl-options-activity_b2c3d4e5.json
│   │   └── summary.json
│   │
│   ├── NVDA/
│   │   ├── tavily/
│   │   │   └── techcrunch-nvidia-data-center_d4e5f6a7.json
│   │   └── summary.json
│   │
│   └── _macro/
│       ├── tavily/
│       │   └── fed-rate-decision-april_f1e2d3c4.json
│       └── summary.json
│
├── 2026-04-16/
│   ├── TSLA/
│   │   ├── serpapi/
│   │   │   └── ...
│   │   └── summary.json
│   └── ...
│
├── 2026-04-11/
│   └── ...
│
└── manifest.json
```

### Organization rules

- **Level 1: date** — `published_date` (falls back to `last_asof_time_utc` date)
- **Level 2: ticker** — `bundle_subject` (the primary ticker the doc was collected for). Documents not tied to a specific ticker go under `_macro/`
- **Level 3: provider** — `source_provider` (`tavily`, `serpapi`, `seeking_alpha`, `sec`, `fred`)
- **File name:** `{slugified_title}_{canonical_document_id[:8]}.json` (truncated title for readability, short hash for uniqueness)

### Document JSON (each file)

```json
{
  "canonical_document_id": "a1b2c3d4e5f6...",
  "canonical_url": "https://reuters.com/technology/apple-results-2026-04-17",
  "url_host": "reuters.com",
  "title": "Apple Posts Record Q2 Revenue on Services Growth",
  "display_excerpt": "Apple reported quarterly revenue of $124B...",

  "source_kind": "news_article",
  "source_provider": "tavily",
  "search_provider": "tavily",
  "bundle_subject": "AAPL",

  "published_at": "2026-04-17T08:30:00Z",
  "published_date": "2026-04-17",

  "mentioned_tickers": ["AAPL", "MSFT", "GOOG"],
  "mentioned_commodities": [],
  "event_tags": ["earnings_beat", "revenue_surprise"],
  "mentioned_dates": ["2026-04-17"],

  "last_run_id": "attn-20260417-210512",
  "raw_text_chars": 4820,
  "raw_text_origin": "tavily_raw_content",

  "raw_text": "Apple Inc. reported fiscal second-quarter results on Thursday...",
  "search_text": "Apple Inc. reported fiscal second-quarter results..."
}
```

Fields come from:
- Postgres `saa_documents` table: everything except `raw_text`
- Azure Blob JSON (via `raw_text_blob_path`): `raw_text` field
- `search_text` included as fallback — always available even if blob fetch fails for a given document

### Summary JSON (per ticker per date)

One `summary.json` per ticker-date folder. Contains the bundle write-up from `attention_bundle_snapshots.payload_json`:

```json
{
  "bundle_id": "symbol::AAPL",
  "bundle_type": "symbol",
  "run_id": "attn-20260417-210512",
  "symbol": "AAPL",
  "event_title": "AAPL +3.8% — Record Q2 on Services Growth",
  "headline": "AAPL +3.8% — Record Q2 on Services Growth",
  "surface_summary": "Apple beat Q2 estimates by 4% on services acceleration...",
  "what_changed_text": "Apple beat Q2 estimates by 4%, reporting $124B revenue...",
  "why_today_text": "Services acceleration confirmed the multi-quarter re-rating thesis...",
  "what_else_moved_text": "Peers moved in the same direction (MSFT +1.2%, GOOG +0.8%).",
  "background_context_text": "Apple's services segment has been the primary growth driver...",
  "cause_status": "supported",
  "evidence_quality": "high",
  "freshness_quality": "fresh",
  "source_summary": "Reuters, Bloomberg, Seeking Alpha",
  "generated_at_utc": "2026-04-17T21:05:12Z"
}
```

### manifest.json

```json
{
  "export_id": "exp-20260418-143012-a1b2",
  "generated_at": "2026-04-18T14:32:45Z",
  "filters": {
    "start_date": "2026-04-11",
    "end_date": "2026-04-18"
  },
  "stats": {
    "total_documents": 142,
    "total_summaries": 18,
    "dates": ["2026-04-11", "2026-04-12", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17"],
    "tickers": ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG", "META"],
    "providers": {
      "tavily": 68,
      "serpapi": 52,
      "seeking_alpha": 14,
      "sec": 8
    }
  },
  "documents": [
    {
      "path": "2026-04-17/AAPL/tavily/reuters-apple-posts-record-q2_a1b2c3d4.json",
      "canonical_document_id": "a1b2c3d4e5f6...",
      "title": "Apple Posts Record Q2 Revenue on Services Growth",
      "ticker": "AAPL",
      "provider": "tavily",
      "published_at": "2026-04-17T08:30:00Z"
    }
  ],
  "summaries": [
    {
      "path": "2026-04-17/AAPL/summary.json",
      "bundle_id": "symbol::AAPL",
      "ticker": "AAPL",
      "date": "2026-04-17"
    }
  ]
}
```

---

## Implementation Status

### Implemented (2026-04-18)

**`services/research_export.py`** — new module with:
- `create_export_job(start_date, end_date, created_by)` — creates job, kicks off background thread
- `get_export_job(job_id)` — returns job status, progress, download URL
- Background builder: queries `search_retained_documents()`, fetches raw text from blob per doc, loads bundle summaries from pipeline store, builds zip in memory, uploads to Azure Blob `exports/` container, generates SAS download URL (24h expiry)
- In-memory job tracking (dict + lock) — sufficient for single-process deployment
- Progress updates every 10 documents

**`api/main.py`** — two new endpoints:
- `POST /v1/research/export` — starts export, returns job_id immediately
- `GET /v1/research/export/{job_id}` — returns status/progress/download URL
- Both require `SCOPE_QUERY_EXECUTE` + `SCOPE_DATASET_READ`

**`app.py`** — new "API Keys" tab in Admin section:
- Create scoped API keys with name, scope selection, expiry, notes
- Assign keys to existing users
- List all keys with status, prefix, scopes, timestamps
- Revoke active keys inline

**Separate API container app (`sn-api-dev`):**
- `Dockerfile.api` — same base as `Dockerfile.app` but runs `uvicorn api.main:app` on port 8080 (no Streamlit, no playwright)
- `scripts/deploy_api_azure.sh` — mirrors `deploy_ui_azure.sh` pattern: ACR build, container app update, revision wait, health check
- Container app created in same managed environment (`sn-pipeline-env`), same user-assigned identities
- Env vars inherited from UI app on first deploy, then self-maintained
- URL: `https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`
- Health check: `GET /health` returns `{"status": "ok"}`

### Not implemented (deferred)

- Query registry registration — async job pattern doesn't fit the sync DatasetSpec model
- Postgres-backed job tracking — in-memory is fine for now, would need migration for multi-process
- MCP tool exposure — would need a wrapper that starts export and returns job_id
- Prod API container app (`sn-api`) — create when ready to promote

---

## Decisions

- **Async with download link** instead of streaming zip — avoids connection timeout on large exports
- **Date/ticker/provider** folder hierarchy — matches how you'd browse research
- **`_macro/`** for untickered documents — macro events, broad market commentary
- **`search_text` always included** in document JSON as fallback when blob fetch fails
- **Summaries are pre-existing** — pulled from `attention_bundle_snapshots`, not generated on export
- **No chunks in export** — full documents + summaries are sufficient for an organized dump
- **24h SAS expiry** on download links — reasonable for manual download, re-exportable anytime
- **Separate container app** for API — Azure Container Apps only support one ingress port per app; simpler than reverse proxy co-location

## Quick Reference: curl Examples

### 1. Create an API key (admin, via UI or API)

```bash
curl -X POST https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io/v1/auth/agent-keys \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "research-export-script",
    "scopes": ["query:execute", "dataset:read"],
    "expires_in_days": 90,
    "notes": "Weekly research dump"
  }'
# Response includes "api_key": "snak_..." — save this, shown only once.
```

### 2. Start an export

```bash
curl -X POST https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io/v1/research/export \
  -H "X-API-Key: snak_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-04-11",
    "end_date": "2026-04-18"
  }'
# Returns: {"job_id": "exp-20260418-143012-a1b2", "status": "building", ...}
```

### 3. Poll for completion

```bash
curl https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io/v1/research/export/exp-20260418-143012-a1b2 \
  -H "X-API-Key: snak_YOUR_KEY_HERE"
# Returns: {"status": "ready", "download_url": "https://...blob.../exports/exp-...zip?sv=...", ...}
```

### 4. Download the zip (no auth needed — SAS token in URL)

```bash
curl -o research_export.zip "DOWNLOAD_URL_FROM_STEP_3"
```

### Full script

```bash
#!/usr/bin/env bash
set -euo pipefail

API="https://sn-api-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io"
KEY="snak_YOUR_KEY_HERE"

# Start export
JOB_ID=$(curl -s -X POST "$API/v1/research/export" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2026-04-11", "end_date": "2026-04-18"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo "Export started: $JOB_ID"

# Poll until ready
while true; do
  RESULT=$(curl -s "$API/v1/research/export/$JOB_ID" -H "X-API-Key: $KEY")
  STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  if [ "$STATUS" = "ready" ]; then
    URL=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['download_url'])")
    echo "Downloading..."
    curl -o "research_export_${JOB_ID}.zip" "$URL"
    echo "Done: research_export_${JOB_ID}.zip"
    break
  elif [ "$STATUS" = "failed" ]; then
    echo "Export failed: $RESULT"
    exit 1
  fi
  echo "Status: $STATUS — waiting..."
  sleep 5
done
```

## Related Plans

- `AQL_SAA_V1_IMPLEMENTATION_ROADMAP_2026-04-14.md` — retention + retrieval foundation
- `CHAT_SEARCH_RESEARCH_ENABLEMENT_2026-04-12.md` — Chat + Search uses same data paths
- `spectral_nature_2/IPHONE_APP_STRATEGY_2026-04-05.md` — iPhone app consumes same API layer
