# Omnibar UI Redesign — Research Agent UX

**Date:** 2026-04-22  
**Context:** The agentic backend is complete. The presentation layer needs a redesign to match the quality of the research pipeline behind it.

---

## Design Principles

1. **Research agent, not chatbot** — every answer is grounded in evidence. The UI should show where claims come from.
2. **Progressive disclosure** — summary first, detail on demand. Don't front-load complexity.
3. **Data-forward** — tickers and metrics get visual treatment (cards, colors), not just text.
4. **Conversational** — follow-up is natural (same input), not a separate UI flow.
5. **Clean chrome** — no mode selectors, no implementation details, no clutter.

---

## What Changes

### 1. Chat conversation model
- **Before:** Form with text input + mode dropdown + "Resolve" button, separate follow-up text input + "Dig Deeper" button
- **After:** `st.chat_message` history + `st.chat_input` at bottom. Follow-up = just type again. "Dig deeper" is a button within the assistant's response.
- Removes: mode selector, form, separate follow-up area, 6 quick buttons

### 2. Source evidence strip (Perplexity-style)
- Numbered source cards at the top of each answer
- Each card: index number + title + source domain
- Collected from `source_links` across all tool results in the run
- Grounds the answer in visible evidence before the user even reads it

### 3. Extracted ticker metrics
- Regex-extract `TICKER: +X.XX%` patterns from the answer text
- Render as `st.metric` cards in a horizontal strip above the narrative
- Immediate visual signal of what moved and by how much

### 4. Tool trace → `st.status`
- **Before:** "Thinking Trace" expander with reasoning, code blocks, previews mixed together
- **After:** `st.status` widget — collapsed by default showing "Researched N sources · Xs"
- Expanded view: verbose trace with reasoning, tool calls, previews, inline charts, source links
- During the run: status is expanded, shows live progress with tool names as they fire

### 5. Structured answer from LLM
- Update final answer prompt to produce structured markdown:
  - Bold verdict/finding as first line
  - `###` section headings for multi-part answers
  - **Bold** tickers and key metrics
  - Brief takeaway at the end
- No schema change — just prompt guidance for better markdown

### 6. Simplified search result cards
- **Before:** 3 action buttons per card (Stock Investigator, Market Explorer, Home Research)
- **After:** One primary "Open →" action per card, compact layout

### 7. Welcome state
- **Before:** "Suggested entries" section with beats list + example prompts in two columns
- **After:** "What would you like to research?" + 3 clickable example pills (mix of today's beats and analysis prompts)

---

## Exit Criteria

| Item | Status | Done when |
|------|--------|-----------|
| Chat model | Done | Messages persist in history, follow-up works via same input, no form/mode selector |
| Source strip | Done | Numbered source cards appear above answer when sources are available |
| Metrics strip | Done | Ticker + percentage cards render above the narrative for answers with financial data |
| Tool trace | Done | `st.status` collapsed by default, verbose when expanded, shows live progress during run |
| Answer structure | Done | LLM produces markdown with bold verdict, ### sections, bold tickers |
| Search cards | Done | Single "Open →" button per result, compact layout |
| Welcome state | Done | Clean empty state with clickable example prompts |
| No regressions | Done | Admin debug panel still works, search results still navigable |

---

## Implementation

### Files modified
- `app.py` — main section rewrite + new helper functions
- `services/omnibar_agent.py` — final answer prompt update

### New helper functions (in app.py)
- `_render_omnibar_welcome(beats)` — welcome state with example pills
- `_run_and_render_agent_live(cfg, query, ...)` — runs agent with st.status progress, renders structured response, returns history dict
- `_render_agent_response_message(msg)` — renders a saved assistant message from history
- `_render_source_evidence_strip(sources)` — Perplexity-style numbered source cards
- `_extract_answer_metrics(answer_text)` — regex extraction of ticker:percentage pairs
- `_render_answer_metrics(metrics)` — metric cards strip
- `_render_inline_search_results(results, request_id)` — simplified result cards
- `_render_thinking_trace_content(trace, agent_result)` — verbose trace content for expanded status

### Conversation history threading (2026-04-22)
- Prior chat turns now flow from `app.py` → `_run_agentic_omnibar_resolution` → `run_omnibar_agent` → planner/final prompts
- Compact history: most recent answer up to 1500 chars, older answers ~300 chars, user messages in full, 3000 char total cap
- `conversation.prior_answers` tool: agent can search full text of prior turns by keyword when compact summary isn't enough
- Planner prompt updated to resolve ambiguous references from conversation context

### Removed
- `_render_agentic_omnibar_progress_panel` — replaced by st.status
- `_render_agentic_omnibar_result_card` — replaced by inline version
- Form with mode selector
- Separate follow-up text input + "Follow Up" button
- 6 quick beat/macro buttons
- "Suggested entries" two-column section
- "Ambiguous" intent force-resolve buttons
