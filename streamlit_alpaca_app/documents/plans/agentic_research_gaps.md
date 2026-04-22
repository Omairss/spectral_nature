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
| Agentic tool loop | `omnibar_agent.py` | Up to 6 iterative tool calls with LLM planner |
| Anomaly detection on ticker data | `compute/anomalies.py` | Full statistical pipeline: expectations, residuals, z-scores, event classification |
| External search (SERP + Tavily) | `aql/collector.py`, `omnibar_research.py` | LLM-based router decides SERP vs Tavily per query |
| Claim extraction from evidence | `aql/extractor.py` | Chunks documents, ranks relevance, extracts structured claims via LLM |
| Narrative synthesis | `aql/writer.py`, `aql/summarizer.py` | Writes symbol/event bundles from claims |
| Confidence scoring | `aql/extractor.py` | Multi-component: severity, impact, relevance, confidence (0-1 scale) |
| Internal data lookup | `omnibar_research.retained_context()` | Searches home payload for bundles, tickers, macro context |
| Market impact mapping | `omnibar_research.market_impact_map()` | LLM classifies theme + expected direction + symbols |

---

## Gaps

### Gap 1: No Persistent Hypothesis Scratchpad

**Problem:** All intermediate results (anomaly events, search results, claims, hypotheses) live in ephemeral DataFrames. Nothing is written to a persistent store between steps. The agent can't "remember" what it found in step 1 when it gets to step 3.

**Impact:** Can't do multi-pass refinement. Each step starts from scratch.

**Suggested fix:**  
- Add a `HypothesisScratchpad` class that persists to JSON/parquet per research session.  
- Store: initial anomaly event, search queries run, evidence collected, claims extracted, current hypothesis text, confidence score.  
- Each agent tool call appends to the scratchpad rather than returning ephemeral results.  
- Location: new file `services/aql/scratchpad.py` or extend `evidence_index.py`.

---

### Gap 2: No Code Execution Tool in the Agent Loop

**Problem:** The omnibar agent has tools for search, context lookup, and impact mapping — but no tool to **run python on the data**. It can't call `anomalies.detect_anomaly_events()` from within the agent loop. Anomaly detection runs in the pipeline job, not as an on-demand agent tool.

**Impact:** The agent can't agentically say "let me check when ARCC first dropped" and run the computation. It can only reference pre-computed results.

**Suggested fix:**  
- Add a `dataset.run_anomaly_check` tool to the agent's tool registry.  
- Wraps `anomalies.detect_anomaly_events()` for a given symbol list.  
- Returns: first drop date, magnitude, z-score, classification.  
- Optionally: a more general `dataset.compute` tool that runs predefined analysis templates (anomaly detection, correlation, drawdown analysis) on available ticker data.

---

### Gap 3: No Verification / Hypothesis Grading Step

**Problem:** The pipeline generates a hypothesis and stops. There's no built-in step that cross-checks the hypothesis against the evidence, looks for contradictions, or grades confidence in a structured way.

**Impact:** Hypotheses can be plausible-sounding but unchecked. No way to distinguish a well-supported hypothesis from a weakly-supported one.

**Suggested fix:**  
- Add a verification pass after synthesis: an LLM call that receives the hypothesis + all claims and answers:
  - Is the hypothesis internally consistent?
  - Which claims support it? Which contradict it?
  - What's missing? (suggests follow-up searches)
  - Confidence grade: high / medium / low with reasoning.
- If confidence is low, trigger another search+extract cycle (loop back to step 2).
- Location: new function in `aql/summarizer.py` or a dedicated `aql/verifier.py`.

---

### Gap 4: No Multi-Pass / Iterative Refinement

**Problem:** The agent loop runs once (up to 6 tool calls) then synthesizes. There's no mechanism to say "the hypothesis is weak, run another research pass with refined queries."

**Impact:** First-pass research is often shallow. Real analysis requires iterating: find something, refine the question, search again.

**Suggested fix:**  
- After the verification step (Gap 3), if confidence < threshold, re-enter the agent loop with a refined prompt that includes what was already found and what's missing.  
- Cap at 2-3 outer iterations to avoid runaway costs.  
- The scratchpad (Gap 1) makes this possible — each pass reads prior state and builds on it.

---

### Gap 5: Agent Can't Write Back to Research Index

**Problem:** Even when the agent finds good evidence, it doesn't persist results to the research index or attention home data. Findings are lost after the session.

**Impact:** Repeated queries re-do all the work. Can't build up a knowledge base over time.

**Suggested fix:**  
- After a successful research run, write the final hypothesis + supporting claims to the research bundle store (whatever backs `attention_research_bundle`).  
- Tag with query, date, symbols, confidence score.  
- Future `retained_context()` calls can then find and reuse this work.

---

### Gap 6: No Transparent, Interactive Streaming of Agent Reasoning

**Problem:** The current UI shows a progress bar with short status messages ("Checking retained context", "Added evidence from live events", "Deciding the next step") — but hides all the interesting parts:

- **No reasoning visibility.** The LLM planner returns a `reasoning` field on every step (see `_STEP_SCHEMA` in `omnibar_agent.py:49`), but it's never surfaced to the UI. The user can't see *why* the agent chose a particular tool or what it's thinking.
- **No tool call detail.** Tool names are humanized into vague labels ("retained context", "live events"). The actual arguments (query strings, symbol lists, search terms) are hidden. Users can't see what the agent is actually searching for.
- **No result previews during execution.** Tool results are summarized internally for the LLM but never shown to the user while the agent is running. The user only sees the final synthesized answer.
- **No interactivity.** The agent runs synchronously in `_run_resolution_with_feedback()` — the user can't steer, pause, or inject follow-up questions mid-run.
- **Progress panel is wiped.** `progress_slot.empty()` in the `finally` block (app.py:5470) clears all progress history once done. The thinking trace is lost.

**What exists today:**
- `progress_callback` mechanism emits `{stage, message, progress}` events
- `_render_agentic_omnibar_progress_panel()` shows a progress bar + last 4 messages
- Agent returns full `tool_calls` array with `result_summary` on each call
- Each tool call has: `tool_name`, `arguments`, `status`, `result_summary.preview_text`
- LLM planner returns `reasoning` on each step (but it's discarded)

**Impact:** The research feels like a black box. Users can't learn from the agent's process, can't catch mistakes early, and can't build trust in the output.

**Suggested fix — three layers:**

**Layer 1: Stream reasoning + tool details (low effort)**
- Surface the `reasoning` field from each planner step in the progress panel.
- Show tool name + arguments (not just humanized label) when a tool is invoked.
- Show a truncated preview of each tool result as it arrives.
- Don't wipe the progress panel — keep it as a collapsible "Thinking trace" after the answer renders.

Implementation:
- Extend `_emit_progress` calls in `omnibar_agent.py` to include `reasoning=decision.get("reasoning")` and `arguments=arguments` in the event payload.
- Extend `_render_agentic_omnibar_progress_panel()` to render these as an expandable chain:
  ```
  ▸ Step 1: "The user is asking about PE/credit stress. I need BDC ticker data first."
    → research.retained_context(query="private equity private credit crisis...")
    ← 8 results: ARCC, OBDC, MAIN, BXSL, BIZD, BX...
  ▸ Step 2: "I have the symbols but no price context. Let me check anomalies."
    → dataset.run_anomaly_check(symbols=["ARCC","OBDC","MAIN",...])
    ← ARCC: -2.1σ since 2026-04-03, OBDC: -1.8σ since 2026-04-05...
  ```
- Keep the trace in `st.session_state` so it persists after `progress_slot.empty()`.

**Layer 2: Prettified thinking trace (medium effort)**
- After the agent finishes, render the full trace as a structured timeline in the UI:
  - Each step shows: reasoning (markdown), tool call (code block), result (table/chart preview), duration.
  - Use `st.expander` per step so users can drill into details.
  - For chart_model results, render inline mini-charts.
  - For search results, show clickable source links.
- This replaces the current "Agent Answer" box with an answer + trace view.

**Layer 3: Interactive steering (higher effort)**
- Break the synchronous agent loop into an async/streaming pattern.
- After each tool result, briefly pause and let the user:
  - Approve the next step ("continue")
  - Override ("search for X instead")
  - Add context ("also check Y")
  - Stop early ("that's enough, synthesize now")
- This requires moving from `st.form` submission to a chat-like interaction model (e.g., `st.chat_input` inside the agent loop, or a callback-based step confirmation).
- Streamlit's execution model makes true mid-loop interactivity hard — consider a websocket-based approach or breaking the loop into discrete reruns with state stored in `st.session_state`.

---

## Suggested Implementation Order

1. **Streaming reasoning trace** (Gap 6, Layer 1) — highest UX impact, low effort. Surface reasoning + tool details already being generated but discarded.  
2. **Scratchpad** (Gap 1) — foundation for multi-pass and persistence  
3. **Anomaly check tool** (Gap 2) — lets the agent trigger analysis on demand  
4. **Prettified thinking trace** (Gap 6, Layer 2) — structured timeline with expandable steps  
5. **Verification pass** (Gap 3) — grades hypothesis quality  
6. **Multi-pass loop** (Gap 4) — iterates when confidence is low  
7. **Write-back to index** (Gap 5) — persists results for future use  
8. **Interactive steering** (Gap 6, Layer 3) — user can guide the agent mid-run  

Steps 1-5 are medium effort and deliver most of the value. Steps 6-8 are larger but make the system genuinely agentic and transparent.

---

## Architecture Sketch

```
User Query
    │
    ▼
┌──────────────────────────────────────┐
│  Omnibar Agent Loop                   │  (existing)
│  - plan tool calls                    │
│  - invoke tools                       │
│  - collect evidence                   │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │  STREAMING TRACE  ← Gap 6       │  │
│  │  - reasoning per step           │  │
│  │  - tool name + args             │  │
│  │  - result preview               │  │
│  │  - user steering (Layer 3)      │  │
│  └─────────────────────────────────┘  │
└────────┬─────────────────────────────┘
         │
    ┌────▼─────┐
    │ NEW TOOLS │
    ├──────────┤
    │ dataset.run_anomaly_check  │  ← Gap 2
    │ scratchpad.write           │  ← Gap 1
    │ scratchpad.read            │  ← Gap 1
    └────────┬─────────┘
             │
    ┌────────▼────────┐
    │  AQL Synthesis   │  (existing)
    │  - extract claims │
    │  - write narrative│
    └────────┬─────────┘
             │
    ┌────────▼────────┐
    │  Verification    │  ← Gap 3
    │  - grade hypothesis │
    │  - find contradictions │
    │  - suggest follow-ups  │
    └────────┬─────────┘
             │
       confidence < threshold?
        yes ──► loop back (Gap 4)
        no  ──► persist to index (Gap 5)
             │
    ┌────────▼────────┐
    │  Thinking Trace   │  ← Gap 6, Layer 2
    │  - expandable steps │
    │  - inline charts    │
    │  - source links     │
    │  - persisted in     │
    │    session_state    │
    └─────────────────┘
```
