# Attention Research Quality Fix

Date: 2026-04-14

## Problem Statement

The current "agentic" summary and narrative-card outputs are useful, but they still feel too shallow.

Observed symptoms:

- The homepage `Market Hypothesis` often reads like a generic macro caption instead of a researched market-activity explanation.
- The wording does not make it obvious that the system searched, opened pages, compared sources, or used Seeking Alpha when available.
- `Top Events` and `Key Movers` are often clipped, repetitive, or too long for the amount of value they add.
- Narrative attention cards still fall back to generic text such as `with no clear catalyst` or `the most plausible chain is...` even when the system should do more research before settling there.

Example symptom:

- `Market Hypothesis Trading shows a rotation toward speculative growth and travel-linked cyclicals while parts of traditional resource and infrastructure groups lag.`

This is directionally plausible, but it is too flat. It does not show enough breadth, source color, or explicit handling of gaps in the evidence.

## What Is Going Wrong

### 1. The homepage writer is still a one-paragraph compression step

Current path:

- `services/aql/summarizer.py`
- `build_attention_agentic_summary_with_trace(...)`
- `_synthesize_attention_home_hypothesis(...)`

Current behavior:

- plan `3-5` queries
- collect search results
- convert results into chunks and claims
- pass only the top few claims into one LLM write call
- ask for `one short paragraph`

Why that leads to flat output:

- the prompt explicitly asks for one short paragraph
- the writer only sees a narrow evidence slice
- source identity is reduced to short claim rows instead of richer document summaries
- there is no explicit second pass that asks, `what is still missing?`

## 2. Evidence is clipped too early

Current path:

- `services/aql/summarizer.py`
- `services/aql/extractor.py`

Current behavior:

- Seeking Alpha enrichment is capped before chunking
- `_chunk_source_documents(...)` only keeps the first `3` pieces of each document
- each chunk is trimmed to about `700` chars
- `_fallback_claims_from_chunks(...)` and `_extract_claims(...)` only inspect `chunks.head(6)`

Why that hurts quality:

- the system is biased toward the top of the first few pages it opened
- long articles lose the later sections where the real thesis, risks, and counterpoints often live
- Seeking Alpha access exists, but the summary path still behaves more like `snippet-plus-first-paragraph` than `article reading`

## 3. The system does not explicitly detect evidence gaps

Current path:

- `services/aql/collector.py`
- `services/aql/summarizer.py`

Current behavior:

- the planner proposes broad market-wide queries
- the summary loop does not score whether each beat is actually explained
- there is no `gap detection -> targeted follow-up query -> re-read -> rewrite` loop

Why that matters:

- if one beat is unexplained, the writer still produces a smooth macro paragraph
- the summary sounds confident even when coverage is uneven
- the system is not yet behaving like an analyst who notices missing links and goes back out to research them

## 4. Surface-summary text is still doing too much work

Current path:

- `services/attention_surface.py`
- `services/aql/summarizer.py`

Current behavior:

- homepage `Top Events` and `Key Movers` reuse `attention_home_surface_summary(...)`
- those lines are then trimmed again in `build_attention_home_summary(...)`

Why that hurts readability:

- the same long, feed-style surface summary is being reused in a compact homepage summary
- the result can feel like half a card pasted into a bullet
- the homepage needs shorter, denser labels than the feed cards do

## 5. Narrative event cards still use canned theme fallbacks

Current path:

- `services/attention_market_events.py`
- `_why_happened_text(...)`

Current behavior:

- for themes such as `oil`, `rates`, `defensives`, and `risk`, the system falls back to fixed theme text
- if context is weak, it uses generic lines like `Cause remains unresolved` or `Coverage remains conflicting`

Why that hurts trust:

- the card sounds like a template instead of a researched explanation
- it does not tell the user what the system checked
- it does not differentiate `no clear cause found after searching` from `we did not really look hard enough`

## Root Cause Summary

The current system is agentic at collection time, but still too single-pass at reasoning time.

It can search and read pages, including Seeking Alpha, but:

- it compresses evidence too early
- it does not measure coverage gaps explicitly
- it does not do a targeted second research pass when the first pass is thin
- it uses the same compact writer style for cases that need richer explanation
- it still relies on canned fallbacks in important narrative-card paths

## Proposed Fix

## 1. Add gap-aware research loops

Add a deterministic coverage pass after the first evidence collection step.

For each homepage beat and narrative card:

- score whether it has same-day evidence
- score whether it has a concrete driver
- score whether source diversity is acceptable
- score whether the explanation is still generic

If coverage is weak:

- generate `1-3` targeted follow-up queries only for the missing gap
- re-run collection and page reading for that gap
- rewrite using the expanded evidence set

Example gaps:

- no same-day source for the main driver
- no explanation tying rates / oil / dollar / sector rotation together
- no company-specific catalyst for the lead mover
- only one weak snippet and no opened article

This keeps cost down because the second pass only runs when needed.

## 2. Stop using `first N chunks` as retrieval

Replace `pieces[:3]` and `chunks.head(6)` with ranked chunk selection.

Rank chunks by:

- same-day freshness
- overlap with beat symbols
- overlap with extracted tickers, commodities, dates, and event tags
- authority bucket
- title relevance

Then pass the best chunks into claim extraction, not just the earliest chunks.

This is a source fix and is cheaper than jumping straight to a vector DB.

## 3. Summarize Documents Before Summarizing Market Activity

For long pages, especially Seeking Alpha:

- chunk the full page
- build a short `document summary` with:
  - main thesis
  - catalysts
  - risks / counterpoints
  - dated facts
- feed those document summaries into the market-activity writer

This should be map-reduce style:

- map: summarize each strong document once
- reduce: write the market-wide hypothesis from those summaries

That preserves more color without blowing the context window.

## 4. Change the homepage output format

The homepage summary should not be one paragraph plus bulky bullets.

Recommended shape:

- `Market Hypothesis`: 2-3 sentences max
- `What We Found`: 2 short bullets with concrete drivers or source color
- `Watchouts`: 1 short uncertainty line when evidence conflicts
- compressed `Top Events / Key Movers` labels, not mini paragraphs

Example direction:

- `Market Hypothesis`: Rates relief and lower oil are helping higher-beta growth and travel outperform, while pipelines and utilities lag as defensive demand softens.
- `What We Found`: Reuters and Treasury coverage both point to falling yield pressure. Seeking Alpha and company news around the lead movers reinforce an AI / storage demand and travel-risk-on read.
- `Watchouts`: Parts of the resource lag still look underexplained, so this is not yet a clean one-driver market.

That is still short, but it has more color, more explicit evidence use, and more honest uncertainty.

## 5. Split Homepage Summary Text From Card Text

Do not reuse the same surface summary text everywhere.

Add dedicated compact writers:

- homepage beat label writer
- narrative-card writer
- audio writer

Homepage beat labels should be short and dense.

Example:

- instead of: `Utilities and gas infrastructure stocks slide with no clear catalyst: A cluster of regulated utilities and gas infrastructure names traded lower together.`
- prefer: `Utilities and gas infrastructure lag together; same-day cause still thin.`

Narrative cards can be a little longer, but they should not use canned macro templates unless the system has truly exhausted the evidence budget.

## 6. Add generic-output quality gates

Add deterministic checks for weak final text.

Trigger rewrite or extra research if the output contains too much of:

- `with no clear catalyst`
- `most plausible chain`
- `rotation toward`
- `appears to`
- `points to`
- `broader risk-on move`

These phrases are not always wrong, but a high density of them is a sign that the system is smoothing over evidence gaps.

Quality gates should check:

- source diversity count
- number of opened pages
- whether Seeking Alpha page text was actually used
- same-day claim count
- generic-language density

## 7. Reuse the same gap-aware loop for attention cards

The event-card path in `services/attention_market_events.py` should call the same shared evidence stack when:

- `why_happened_text` is generic
- `headline_text` is empty
- the event is important enough to deserve more work

That means:

- planner
- search
- page open
- chunk ranking
- claim extraction
- short writer

One shared research loop is better than one strong homepage path and one weaker card path.

## Implementation Plan

## Phase 1. Coverage and gap scoring

- add deterministic `coverage_gaps` scoring for homepage beats and event cards
- persist gap rows into the shared AQL trace
- add generic-language detection helpers

## Phase 2. Retrieval quality

- replace first-chunk selection with ranked chunk selection
- keep full-page text longer before trimming
- add document-level summaries for strong long-form pages

## Phase 3. Writer changes

- rewrite homepage summary schema and output shape
- add compact homepage beat labels
- add a stronger `what we found / watchouts` layer

## Phase 4. Event-card repair

- route weak event cards through the shared research loop
- only allow canned theme text as the final fallback

## Phase 5. Evaluation

Track before/after on:

- percentage of summaries with at least `2` distinct source families
- percentage of summaries with at least `1` opened page
- percentage of summaries with explicit unresolved language when coverage is thin
- reduction in generic-phrase frequency
- human review on `breadth`, `color`, and `trust`

## Cost and Speed Strategy

Do not make every summary more expensive by default.

Keep the fast path:

- current first-pass search and evidence collection

Only pay for more work when:

- coverage score is weak
- output reads as generic
- a top beat is still unexplained
- a strong long-form source is present and worth reading

This keeps the fix aligned with cost and speed:

- deterministic gap scoring first
- targeted second-pass research only when needed
- long-doc summarization only for selected high-value pages
- no mandatory vector DB for the first quality upgrade

## Recommendation

The next implementation pass should focus on three source fixes first:

1. gap scoring and targeted second-pass queries
2. ranked chunk retrieval instead of first-chunk retrieval
3. document-summary map-reduce for long-form pages such as Seeking Alpha

Those three changes should materially improve both the homepage `Market Hypothesis` and the narrative-card `Why It Happened` quality without forcing a heavy new infrastructure layer.
