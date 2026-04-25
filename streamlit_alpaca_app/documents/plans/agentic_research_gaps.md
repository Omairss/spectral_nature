# Agentic Research Pipeline — Gaps & Suggested Improvements

**Date:** 2026-04-17  
**Context:** Evaluating whether the omnibar/AQL pipeline can run a full agentic research workflow:  
1. Detect anomalies in ticker data, find the first drop, write to a hypothesis scratchpad  
2. Search internal data + external (SERP/Tavily), write to scratchpad  
3. Re-run to clean up and verify the hypothesis  

---

## What's Already Built

| Capability | Where | Notes |
|---|---|---|
| Agentic tool loop | `omnibar_agent.py` | Up to 8 iterative tool calls with LLM planner |
| Anomaly detection on ticker data | `compute/anomalies.py`, `agent_tools.py` | Full pipeline + on-demand agent tool |
| External search (SERP + Tavily) | `aql/collector.py`, `omnibar_research.py` | LLM-based router decides SERP vs Tavily per query |
| Claim extraction from evidence | `aql/extractor.py` | Chunks documents, ranks relevance, extracts structured claims via LLM |
| Narrative synthesis | `aql/writer.py`, `aql/summarizer.py` | Writes symbol/event bundles from claims |
| Confidence scoring | `aql/extractor.py` | Multi-component: severity, impact, relevance, confidence (0-1 scale) |
| Internal data lookup | `omnibar_research.retained_context()` | Searches home payload for bundles, tickers, macro context |
| Market impact mapping | `omnibar_research.market_impact_map()` | LLM classifies theme + expected direction + symbols |
| Hypothesis verification | `aql/summarizer.py`, `agent_tools.py` | LLM-backed grading with gap queries |
| Hypothesis scratchpad | `aql/scratchpad.py`, `agent_tools.py` | Persistent per-session state for research |
| Write-back to index | `omnibar_agent.py` | Auto-persist findings after successful runs |
| Streaming thinking trace | `omnibar_agent.py`, `app.py` | Reasoning, tool args, result previews streamed to UI |
| Interactive steering | `app.py` | Follow-up and dig-deeper buttons post-answer |

---

## Gaps

### Gap 1: No Persistent Hypothesis Scratchpad — IMPLEMENTED (2026-04-21)

**Status:** Done.

**What was built:**
- `services/aql/scratchpad.py` — session-scoped JSON store keyed by run_id
- `write_entry(run_id, kind, content)` — appends anomaly events, claims, hypotheses, search queries, notes
- `read_entries(run_id, kind?, last_n?)` — filtered read-back
- `read_summary(run_id)` — compact entry count by kind + latest hypothesis
- `scratchpad.write` and `scratchpad.read` added as agent tools in `agent_tools.py`
- Agent can persist intermediate state between tool calls and across verification passes
- Run_id flows from `run_omnibar_agent` through `invoke_tool` to the scratchpad

---

### Gap 2: No Code Execution Tool in the Agent Loop — IMPLEMENTED (2026-04-21)

**Status:** Done.

**What was built:**
- `dataset.run_anomaly_check` tool in `agent_tools.py`
- Accepts: `symbols` (list), `horizon` (string, defaults to 1w)
- Reads from pre-computed attention candidates in the materialized home payload
- Returns: anomaly events per symbol with direction, z-score, classification, attention score
- `llm_context_text` field provides a compact text summary for the planner
- Falls back gracefully when no materialized data is available

---

### Gap 3: No Verification / Hypothesis Grading Step — IMPLEMENTED (2026-04-17)

**Status:** Done. Two complementary implementations:

#### 3a. Batch pipeline (single-pass verify)
- `verify_hypothesis()` in `summarizer.py` — public function, LLM-backed with heuristic fallback
  - Input: hypothesis text, claims, beats, llm_client, signal_context
  - Output: `{verdict, confidence, supporting_claims, contradicting_claims, gap_queries, reasoning}`
  - Verdicts: `supported`, `weak`, `conflicting`, `unsupported`
  - `gap_queries`: concrete search queries the verifier writes to fill evidence holes
- `_heuristic_verification()` — score-based fallback when LLM is unavailable
- `HYPOTHESIS_VERIFICATION_SCHEMA` in `constants.py`
- `build_attention_agentic_summary_with_trace()` calls verify once after synthesis
- Prompt registered via `register_narrative_prompt` -> editable in admin config at runtime

#### 3b. Agentic verification (dynamic, multi-pass, full tool access)
- `hypothesis.verify` added as a real tool in the omnibar agent's tool catalog (`agent_tools.py`)
- The LLM planner dynamically decides when to verify and what to do with the results
- Agent workflow: gather evidence → form hypothesis → call `hypothesis.verify` → if gaps, use ANY available tool (research, dataset, chart) to fill them → re-verify or answer
- NOT a fixed loop — the agent decides loop count, tool selection, and when to stop
- `_invoke_hypothesis_tool()` in `agent_tools.py` routes the tool call, imports `verify_hypothesis` and `load_llm_client`
- `DEFAULT_MAX_TOOL_CALLS` raised from 6 to 8 to accommodate verify + gap-fill steps
- Planner system prompt updated with verify-search-verify instructions
- 6 tests passing: 3 for `verify_hypothesis()`, 1 agent tool invocation, 2 existing updated

**What's NOT built yet (future work):**
- UI display of verification verdict and pass history (depends on Gap 6: streaming trace)

---

### Gap 4: No Multi-Pass / Iterative Refinement — SUBSUMED BY GAP 3

The omnibar agent handles multi-pass dynamically. The agent planner decides whether to
search for gap queries and re-verify based on the verdict. Loop count is not fixed —
the agent uses its tool budget (up to `DEFAULT_MAX_TOOL_CALLS`) as needed.
No separate implementation needed.

---

### Gap 5: Agent Can't Write Back to Research Index — IMPLEMENTED (2026-04-21)

**Status:** Done.

**What was built:**
- `_persist_agent_findings()` in `omnibar_agent.py` — called automatically after successful agent runs
- Writes to the scratchpad with kind=`agent_result`: query, answer, confidence, symbols, claim count
- Extracts symbols from all successful tool call arguments
- Silent failure (never blocks the agent response)
- Future: extend to write to the retained documents store for `retained_context()` lookups

---

### Gap 6: No Transparent, Interactive Streaming of Agent Reasoning — IMPLEMENTED (2026-04-21)

**Status:** Done. All three layers implemented.

#### Layer 1: Stream reasoning + tool details — DONE
- `omnibar_agent.py` now emits `planner_reasoning` events with the LLM's reasoning text
- `tool_start` events include `tool_arguments` (the actual args, not humanized labels)
- `tool_complete` events include `result_preview` (truncated preview text)
- Progress panel in `app.py` renders reasoning as 💭 italic text, tool calls as code blocks, and result previews as captions
- Thinking trace is built in `_run_resolution_with_feedback` from raw events

#### Layer 2: Prettified thinking trace — DONE
- Trace persisted in `st.session_state["agentic_omnibar_thinking_trace"]`
- Survives `progress_slot.empty()` — no longer wiped after completion
- Rendered as a collapsible "Thinking Trace" expander below the Agent Answer
- Shows: reasoning steps, tool calls as Python-like code blocks, result previews
- Falls back to `tool_calls` from agent_result when trace events aren't available
- Shows confidence and limitations at the bottom
- Cleared when user clicks "Clear Chat + Search"

#### Layer 3: Interactive steering — DONE
- Follow-up text input + "Follow Up" button below the agent answer
- Combines original query with user override as `{query} — follow-up: {followup}`
- "Dig Deeper" button re-runs with `— verify and expand with more evidence`
- Works within Streamlit's rerun model — each button triggers a fresh agent run with the modified query
- True mid-loop pause/resume would require websockets or async; this post-run steering is the practical Streamlit-compatible approach

---

## All Gaps — Implementation Status

| Gap | Status | Date |
|-----|--------|------|
| Gap 1: Scratchpad | Done | 2026-04-21 |
| Gap 2: Anomaly check tool | Done | 2026-04-21 |
| Gap 3: Hypothesis verification | Done | 2026-04-17 |
| Gap 4: Multi-pass (subsumed by 3) | Done | 2026-04-17 |
| Gap 5: Write-back to index | Done | 2026-04-21 |
| Gap 6 Layer 1: Streaming trace | Done | 2026-04-21 |
| Gap 6 Layer 2: Prettified trace | Done | 2026-04-21 |
| Gap 6 Layer 3: Interactive steering | Done | 2026-04-21 |

---

## Architecture (as built)

```
User Query
    │
    ▼
┌──────────────────────────────────────────┐
│  Omnibar Agent Loop (omnibar_agent.py)   │
│  - LLM planner decides next tool call    │
│  - up to 8 tool calls per run            │
│  - emits reasoning + tool args + previews│
│                                          │
│  Available tools:                        │
│  ├── research.retained_context           │
│  ├── research.live_event_evidence        │
│  ├── research.market_impact_map          │
│  ├── research.open_page                  │
│  ├── dataset.run_anomaly_check           │  ← Gap 2 (on-demand)
│  ├── investigator.technical_signals      │  ← Stock Investigator
│  ├── investigator.forecast               │  ← Stock Investigator
│  ├── investigator.company_context        │  ← Stock Investigator
│  ├── investigator.fundamentals           │  ← Stock Investigator
│  ├── investigator.recent_news            │  ← Stock Investigator
│  ├── dataset.* (all registered datasets) │
│  ├── chart.* (all registered charts)     │
│  ├── hypothesis.verify                   │  ← Gap 3
│  ├── scratchpad.write                    │  ← Gap 1
│  └── scratchpad.read                     │  ← Gap 1
│                                          │
│  Post-run: _persist_agent_findings()     │  ← Gap 5
└────────┬─────────────────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │  UI (app.py)                   │
    │                                │
    │  During run:                   │  ← Gap 6 L1
    │  - 💭 reasoning per step      │
    │  - → tool_name(args)          │
    │  - ← result preview           │
    │                                │
    │  After run:                    │  ← Gap 6 L2
    │  - Agent Answer                │
    │  - Thinking Trace (expander)   │
    │  - Follow Up / Dig Deeper      │  ← Gap 6 L3
    └────────────────────────────────┘
```

## Post-gap enhancements (2026-04-21)

### On-demand anomaly detection
- `dataset.run_anomaly_check` now computes on-demand when symbols are missing from materialized data
- Fetches price history, builds peer groups, momentum, correlation, and runs the full expectation → anomaly pipeline
- Non-standard horizon aliases like `30d` are mapped to the nearest canonical horizon (e.g. `30d` → `1mo`)

### Durable chat session logging (AQL chat_log)
- New module `services/aql/chat_log.py` — Postgres + Azure Blob backed
- Postgres table `aql_chat_sessions`: searchable index with query, status, model, confidence, symbols, tool names, duration, timestamps
- Blob path `aql/chat_logs/{date}/{run_id}.json`: full session payload with all tool call arguments, results, and complete answer markdown
- `log_chat_session()` — called after every agent run (completed and failed)
- `load_chat_session(run_id)` — retrieve full session (blob first, Postgres fallback)
- `list_chat_sessions()` — list recent sessions with filters (status, symbol, query text)
- `count_chat_sessions()` — count matching sessions
- `bootstrap_chat_log()` — auto-creates table on first write
- Full answer and tool results stored without truncation

### Stock Investigator agent tools
- 5 new `investigator.*` tools added to the agent catalog:
  - `investigator.technical_signals` — regime, RSI, channel, support/resistance, volatility
  - `investigator.forecast` — Monte Carlo next-week probability, breakout, confidence interval
  - `investigator.company_context` — narrative from filings/news, management signals, why-now
  - `investigator.fundamentals` — quarterly income/balance/cashflow
  - `investigator.recent_news` — latest headlines with source and date
- Planner system prompt updated to prefer investigator tools for single-ticker deep dives

### tsvector full-text search upgrade (2026-04-22)
- Added `search_tsvector` generated column to `saa_evidence_chunks` — weighted: title (A), excerpt+chunk (B), search_text (C)
- GIN index `idx_saa_chunks_search_tsvector` for fast full-text queries
- `search_retained_evidence_chunks()` now uses `@@ plainto_tsquery()` as primary search path (indexed, with stemming)
- ILIKE kept as fallback for exact substring matches (ticker symbols, acronyms) that tsvector stemming misses
- `ts_rank()` scores from SQL flow into Python scoring as `score_ts_rank` (weighted 12x in combined score)
- Combined score: `score_lexical + score_ts_rank * 12 + score_embedding * 8 + score_rerank`
- Backward compatible: `search_prepared_evidence_chunks()` (in-memory) unaffected — `ts_rank_score` column handled gracefully when absent

## Future work

- True mid-loop pause/resume (requires websockets or async Streamlit)
