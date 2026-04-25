# Agentic System — Remaining Items

**Date:** 2026-04-22

Three items from the future work list to close out:
1. Inline mini-charts in thinking trace for chart tool results
2. Clickable source links for search result previews
3. Write-back to retained documents store from agent findings (item 4 from the original list)

---

## Exit Criteria

### Item 1: Inline mini-charts in thinking trace

**Done when:**
- When a `chart.*` tool completes, the `render_payload` (which already contains chart_model data with traces/datasets) is carried through to the thinking trace
- The Thinking Trace expander in `app.py` renders a Plotly chart inline for `tool_complete` steps that have a `render_payload` with `kind == "chart_model"`
- Non-chart tool results continue to render as text previews (no regression)

### Item 2: Clickable source links in search result previews

**Done when:**
- `_summarize_tool_result()` extracts source URLs from research tool results (live_event_evidence, retained_context, open_page) into a `source_links` field on the result summary
- The `tool_complete` event and thinking trace step carry these links forward
- The Thinking Trace expander renders source links as clickable markdown links below the result preview
- Links appear only when present (no empty link sections)

### Item 3: Write-back to retained documents store

**Done when:**
- After a successful agent run, `_persist_agent_findings()` writes each completed tool result's evidence (claims, headlines, URLs) as retained evidence chunks in the SAA evidence store
- A subsequent `research.retained_context` call can find prior agent findings via the existing search_retained_evidence_chunks path
- Write-back is silent-fail (never blocks the agent response)
- The written entries include: run_id, query, symbols, source_kind=`agent_research`

---

## Implementation Status

| Item | Status | Date |
|------|--------|------|
| 1: Inline mini-charts | Done | 2026-04-22 |
| 2: Clickable source links | Done | 2026-04-22 |
| 3: Write-back to retained store | Done | 2026-04-22 |

---

## What was built

### Item 1: Inline mini-charts

- `_summarize_tool_result()` already produces `render_payload` with `kind=chart_model` for chart tool results — this was unused in the trace
- `tool_complete` progress events now carry `render_payload` from the result summary
- `_run_resolution_with_feedback` stores `render_payload` on the thinking trace entry when it's a chart_model
- Thinking Trace expander renders a Plotly chart inline from the chart_model spec (datasets + traces)
- Handles scatter and bar trace types, up to 6 traces per chart
- Chart height is 250px to keep the trace compact
- Graceful fallback: if Plotly rendering fails, the text preview still shows

### Item 2: Clickable source links

- `_summarize_tool_result()` now extracts `source_links` from research payloads: URLs from `summary` rows (live_event_evidence, retained_context), `articles` (investigator.recent_news), `open_page` results, and direct `rows`
- Each link is `{url, label}` where label is the headline/title + source
- `tool_complete` progress events carry `source_links`
- Thinking trace entries store `source_links` when present
- Both the live progress panel and the Thinking Trace expander render clickable markdown links (`[label](url)`)
- Live panel shows up to 3 links; expander shows up to 5

### Item 3: Write-back to retained documents store

- `_persist_agent_findings()` now calls `_write_back_agent_evidence()` after successful runs
- Writes to `saa_evidence_chunks` via `persist_retained_evidence_chunks()` — the same table that `search_retained_evidence_chunks` queries
- The answer is written as a chunk with `source_kind=agent_research`, `research_scope=agent_answer`
- Each claim (LLM context text from tool results) is written as a separate chunk with `research_scope=agent_evidence`
- All chunks carry `mentioned_tickers_json` and `bundle_subject` from the query
- `dataset_name=agent_research`, `dataset_version_id=run_id` for provenance
- Silent failure — never blocks the agent response
- A subsequent `research.retained_context` call can now find prior agent findings via the standard evidence search path
