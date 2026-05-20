# Zopedia Headless Evaluation Report

Date: 2026-05-15

## Decision

**Go for native integration work. No-go for shipping or renaming Chat + Search to Zopedia yet.**

The headless test shows the core path is technically viable:

```text
YouTube transcript -> normalized source document -> SAA retained document -> SAA chunks -> lexical retrieval
```

But Spectral Nature does not yet have the durable wiki/page layer, native YouTube transcript dependency, wiki graph maintenance, or reviewable wiki mutations needed to make the experience feel as good as standalone Zopedia.

## What I Tested

### 1. Zopedia Reference YouTube Ingestion

I used standalone Zopedia's `graphify.ingest` path against the three YouTube fixtures:

| URL | Result | Transcript entries | Output chars |
| --- | ---: | ---: | ---: |
| `https://www.youtube.com/watch?v=BOT2rrm10RM` | Pass | 801 | 36,807 |
| `https://www.youtube.com/watch?v=t6y_VmxuO28` | Pass | 745 | 35,817 |
| `https://www.youtube.com/watch?v=n889nI8sR84` | Pass | 2,565 | 113,381 |

All three produced markdown files with metadata and transcript text. This confirms the source intake approach is viable for these fixtures.

### 2. Spectral Nature Headless SAA Retention

Using the generated transcript markdown as input, I tested Spectral Nature's existing SAA document/chunk preparation and in-memory retrieval.

Results:

| Metric | Result |
| --- | ---: |
| Prepared source documents | 3 |
| Prepared document upload payloads | 3 |
| Prepared document DB records | 3 |
| Prepared evidence chunks | 46 |
| Prepared chunk DB records | 46 |
| All docs had canonical `saa_doc::` IDs | Pass |
| All chunks had `saa_chunk::` IDs | Pass |

Retrieval spot-checks:

| Query | Expected top document | Actual top document | Result |
| --- | --- | --- | --- |
| `diesel pinch point Hormuz equity la la land Jeff Currie` | `youtube::BOT2rrm10RM` | `youtube::BOT2rrm10RM` | Pass |
| `surging inflation bond market disaster recession headed higher` | `youtube::t6y_VmxuO28` | `youtube::t6y_VmxuO28` | Pass |
| `Nasdaq euphoria hitting its limit TCAF 242` | `youtube::n889nI8sR84` | `youtube::n889nI8sR84` | Pass |

This means SAA can already retain and retrieve transcript-derived evidence once text is provided.

### 3. Existing Regression Slice

Command:

```bash
PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_saa_storage.py \
  streamlit_alpaca_app/tests/test_knowledge_graph.py \
  streamlit_alpaca_app/tests/test_omnibar_agent.py \
  streamlit_alpaca_app/tests/test_omnibar_research.py
```

Result:

```text
35 passed in 18.54s
```

This confirms the current SAA, KG, and omnibar regression slice is healthy.

## Findings

### What Works Today

- SAA can normalize retained source documents.
- SAA can create searchable evidence chunks from transcript text.
- In-memory lexical retrieval can recover the expected video for targeted queries.
- The existing KG has typed nodes/edges, confidence, severity, direction-like source/target edges, and reviewable proposal scaffolding.
- Omnibar/Chat + Search already has a tool-running agent loop and retained context tools.

### What Is Missing

- `youtube-transcript-api` is not in `streamlit_alpaca_app/requirements.txt`.
- Native Spectral Nature does not have YouTube transcript ingestion yet.
- There is no native SAA wiki/page subsystem yet.
- There is no durable `source/entity/concept/analysis/godnode` page model.
- There is no wiki link graph built from retained pages.
- There is no wiki maintenance loop for broken links, stale pages, duplicates, or orphans.
- Existing KG proposals are useful, but they are market-relationship proposals, not Zopedia-style wiki memory proposals.
- We have not tested answer quality, citations, multi-step reasoning, or UI feel yet.

## Go / No-Go

### Go

Proceed with a native integration prototype because the important backend primitive works: transcript text can become retained SAA evidence and can be retrieved correctly.

The right next build is not a UI rename. It is a headless native Zopedia slice:

```text
ingest_youtube_url
  -> retain SAA source
  -> chunk evidence
  -> generate wiki source page
  -> extract entity/concept pages
  -> build wiki links
  -> expose read/search tools to the agent
  -> produce reviewable add/update/delete proposals
```

### No-Go

Do not ship this as Zopedia yet.

Do not rename Chat + Search in production until we can pass these gates:

- native YouTube ingest works without `/tmp` helpers
- transcript pages are durable and reopenable
- wiki pages and backlinks are generated
- graph exploration reflects the actual wiki memory graph
- AQL/agent answers cite both transcript chunks and wiki pages
- proposed wiki/KG changes are previewable and reviewable
- destructive changes never apply silently
- parity journeys score close to standalone Zopedia

## Recommended Next Test Gate

Build a small dev-only headless harness with these commands:

```text
zopedia_eval ingest-youtube <url>
zopedia_eval build-pages --source-id <id>
zopedia_eval ask "What is the key claim from this video?"
zopedia_eval graph --source-id <id>
zopedia_eval propose-maintenance
```

Acceptance for the next gate:

- all three YouTube fixtures ingest in dev
- each source has a durable SAA source record and evidence chunks
- each source has a generated wiki source page
- at least 5 useful entities/concepts are extracted per video
- graph has source/entity/concept edges with confidence
- agent answers cite transcript chunks and wiki pages
- add/remove/update proposals are written as reviewable records, not applied directly

