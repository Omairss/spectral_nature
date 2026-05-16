# Critique + Judge — Eval Results (2026-04-25)

## What was tested

Real LLM (gpt-5.3, Azure OpenAI) + real `QueryService` running against the
locally cached `attention_home_1d` row from 2026-04-13.

Harness: `scripts/eval_critique_harness.py` — loads a cached row,
reconstructs the home_payload, runs `build_attention_home_summary` to
produce the LLM summary, then runs `critique_home_summary` and
`judge_revise_summary` end to end. Prints before / issues / revised.

## Run 1 — baseline (initial implementation)

| Stage | Time | Output |
|---|---|---|
| `build_attention_home_summary` | 10.7 s | 3 sections, 12 bullets |
| `critique_home_summary` | **477.2 s** | 3 tool calls, **1 issue** |
| `judge_revise_summary` | 5.2 s | 1 rephrase decision |

The single flagged issue was a *correct but minor* orphan-symbol problem —
`featured_symbols` listed NI, KMI, WMB, PEG which never appear in the body
text. The judge's rewritten_text said "Featured symbols list revised…" but
because `featured_symbols` wasn't in the judge schema, the visible body
ended byte-identical to the original.

**Three concrete failures observed:**

1. **Critique missed the most important pattern.** The summary contained
   three "no clear catalyst confirmed" / "no single driver confirmed"
   phrases (utilities, BNO, small-cap space). The exact failure mode that
   motivated this work — and the critique did not flag any of them.
2. **Critique latency was 8 minutes** for 3 tool calls (gpt-5.3 with
   `reasoning_effort='high'`). Too slow for a per-build pipeline step.
3. **Judge produced no visible textual change.** The orphan-symbol fix
   lived outside the judge's schema, so the body was unchanged.

## Three improvements applied

1. **Gap detection in critique.** Added issue type `gap` and an explicit
   prompt rule: when the summary uses vague catalyst filler ("no clear
   catalyst", "no single company catalyst", "broad de-risking"), the
   critique MUST attempt a tool call to look for a catalyst. If something
   credible surfaces, flag with the new evidence. If not, accept silently.
2. **Ground-truth context in critique.** Added `_ground_truth_text` which
   formats per-symbol `change_pct`, `surprise_z`, `cause_status`, and
   `top_source` from `home_payload.must_read_movers` /
   `unresolved_large_moves` / `top_events`. Critique uses this as
   authoritative reference for any numeric or directional claim. Includes
   an explicit "Unresolved large moves — DO NOT invent a catalyst" header
   so the critique correctly accepts honest "no catalyst" phrasings on
   genuinely unresolved bundles instead of fabricating one.
3. **`featured_symbols` in judge schema.** Added to `_JUDGE_SCHEMA` and
   `_JUDGE_SYSTEM_PROMPT` so the judge can edit the symbol list (drop
   orphans, add ones that the body discusses but the list omits) instead
   of producing dead-end revisions.

Issue type enum extended: `gap`, `orphan_symbol` added alongside the
existing `numeric / contradiction / unsupported / stale / other`.

## Run 2 — after improvements

| Stage | Time | Output |
|---|---|---|
| `build_attention_home_summary` | 9.9 s | 3 sections, 12 bullets |
| `critique_home_summary` | **6.4 s** | **0 tool calls**, **4 issues** |
| `judge_revise_summary` | 8.8 s | 2 rephrase + 2 drop decisions |

Issues flagged:

| # | Type | Severity | Claim | Evidence |
|---|---|---|---|---|
| 0 | unsupported | medium | "Natural gas prices fell as warmer U.S. weather forecasts pointed to weaker heating demand, pushing UNG lower." | Ground truth: UNG −18.30%, cause=continuation, top_source=TradingView. No weather catalyst in retained evidence. |
| 1 | unsupported | medium | "GFL Environmental fell as the market continued to digest its roughly $6.4B plan to acquire SECURE…" | Ground truth: GFL −8.88%, cause=continuation. Unresolved bucket guidance: do not invent a catalyst. |
| 2 | orphan_symbol | low | NI, KMI in `featured_symbols` but not in body | Body utilities paragraph names EIX, PCG, SRE, D only. |
| 3 | orphan_symbol | low | WMB, PEG in `featured_symbols` but not in body | No mention anywhere in summary. |

Judge applied real visible textual changes:
- *"Natural gas prices fell as warmer U.S. weather forecasts pointed to weaker
  heating demand, pushing UNG lower."* → *"Natural gas prices fell sharply,
  pushing UNG lower as the decline continued."*
- *"GFL Environmental fell as the market continued to digest its roughly
  $6.4B plan to acquire SECURE Waste Infrastructure alongside recent
  results and guidance."* → *"GFL Environmental fell as the decline
  continued with no confirmed new catalyst."*
- Orphan tickers dropped from `featured_symbols`.
- `audio_text` regenerated coherently to match revisions.

What the critique correctly did NOT flag (and was right not to):
- BNO "no clear new catalyst was identified" — BNO is in unresolved with
  cause=continuation; honest filler is correct here.
- Tech rebound "no clear single driver confirmed" — same reasoning.
- CoreWeave/Anthropic, RVMD daraxonrasib, SanDisk Citi raise — all real
  catalysts present in retained evidence.

## Bug found in run 2 — fixed

The judge LLM returned each section twice in `sections` (returned the
revised set followed by the original set). The rendered `summary_text`
ended with the entire summary duplicated.

Fix in `judge_revise_summary`: dedupe sections by case-insensitive title
before rendering. Plus added a belt-and-suspenders rule (#10) to the judge
prompt forbidding section duplication.

Regression test added: `test_judge_dedupes_repeated_sections`.

## Cost / latency summary

| Path | Old (run 1) | New (run 2) |
|---|---|---|
| Critique LLM calls | 4 | 1 |
| Critique tool calls | 3 | 0 |
| Critique wall time | 477 s | 6.4 s |
| Judge wall time | 5.2 s | 8.8 s |
| **Total added per pipeline build** | **~482 s** | **~15 s** |

Tool calls dropped to zero because the ground-truth block in the user
prompt is enough for the critique to spot the unsupported-catalyst and
orphan-symbol issues without needing to ask anything new. Tool calls
remain available for cases the ground truth doesn't cover (e.g. a named
news-driven catalyst the critique wants to confirm).

## Round 3 — sharpening for agentic search

After run 2, user feedback: the critique was making 0 tool calls because
the ground-truth block included an over-restrictive "DO NOT invent a
catalyst" header on unresolved bundles. The user noted real-world
catalysts (BNO ↔ Hormuz, tech rally ↔ short squeeze / company news) that
the agentic critique should be able to discover via Tavily/SerpAPI.

Two prompt fixes:
- Reframed the unresolved-bundle header from "DO NOT invent a catalyst"
  to "cause is OPEN in retained evidence — search the web to look for a
  catalyst before accepting filler phrasing." Treat `cause_status` as a
  starting point, not a stop sign.
- Added the retained `why_now_text` / `why_happened_text` to each
  ground-truth row so the critique distinguishes "no catalyst" from
  "catalyst exists in retention but might be incomplete." Direction and
  numbers stay authoritative; the `why` is a hint to verify.

Also expanded the critique system prompt with explicit gap-detection
examples ("BNO Brent crude rally Strait of Hormuz news today",
"utilities EIX PCG SRE selloff catalyst") and made tool use the default
behavior for any vague catalyst phrasing rather than something to consider.

### Run 3a — synthetic BNO/Hormuz scenario

Hand-crafted summary that says "BNO surged sharply with no clear catalyst
confirmed", with a payload that lists BNO and USO as unresolved with
empty `why_now_text`.

| Stage | Time | Output |
|---|---|---|
| Critique | 944.8 s | 1 tool call, **3 gap issues** |
| Judge | 4.5 s | 3 rephrase decisions |

Critique called `research.live_event_evidence` with query *"Brent crude
rally today catalyst Strait of Hormuz OPEC news oil prices jump today
BNO USO"*, surfaced *"Brent Crude Nears $120 as Strait of Hormuz Blockade
Drives Record March Surge"* and *"Crude oil price climbs on Iran fears,
lifting USO ETF and oil stocks"*, flagged all three vague-catalyst lines
with that evidence.

Judge rewrote:
- *"BNO surged sharply with no clear catalyst confirmed"* → *"BNO surged
  sharply as Brent crude spiked on geopolitical supply fears tied to a
  reported Strait of Hormuz blockade"*
- *"USO traded firmer alongside BNO with no single driver identified"* →
  *"USO traded firmer alongside BNO as crude prices climbed on Iran-related
  supply fears ahead of U.S. inventory data"*
- Audio rewrote to lead with the Hormuz catalyst.

### Run 3b — synthetic tech-rally scenario

Hand-crafted summary that says CRWV/NBIS/IREN/ORCL/NTNX rallied with no
clear single catalyst.

| Stage | Time | Output |
|---|---|---|
| Critique | 990.9 s | 1 tool call, **3 issues** (1 gap + 1 unsupported + 1 contradiction) |
| Judge | 11.9 s | 3 rephrase decisions |

Critique searched for `CRWV NBIS IREN stock rally news today AI compute
infrastructure`, surfaced *"Anthropic picks CoreWeave as AI cloud"*
(Stock Titan), *"CoreWeave stock price up today on Nvidia investment to
build AI factories"* (Fast Company), and *"Cantor Launches Nebius With
Overweight and $129 Target"* (24/7 Wall St.). Judge replaced "no clear
single catalyst" with the actual company-specific catalysts.

Note: the user's mental model framed this as "short squeeze adjacent",
but news-search tools surface *news catalysts*, not short-interest
metrics. To detect short-squeeze pressure specifically would need a
dedicated short-interest tool added to the critique catalog
(`investigator.short_interest` or similar).

### Run 3c — re-run on 2026-04-13 cached payload

Same blob as run 1 and 2, with the v3 prompt.

| Stage | Time | Output |
|---|---|---|
| Critique | 64.7 s | 1 tool call, **5 issues** |
| Judge | 19.5 s | 3 rephrase + 2 drop decisions |

Issues:
- Utilities (EIX/PCG/SRE/D) "broad sector de-risking with no clear
  catalyst" → critique searched, found utilities pressure tied to rising
  Treasury yields → judge rewrote.
- BNO "+68.62%" — critique flagged the claim as suspicious (a 68% move on
  a Brent crude ETF is implausible) and noted the summary's "no clear
  catalyst" filler. Judge dropped the misleading filler. **Worth noting:
  this looks like an upstream data-quality bug in the cached payload's
  `change_pct` for BNO that the critique surfaced as a side effect.**
- 4 orphan tickers (NI, KMI, WMB, PEG) → judge dropped from
  `featured_symbols`.
- Rubrik "rose **even as** filings showed CFO selling" → critique flagged
  the implied causal contrast as unsupported → judge changed *even as* →
  *as*.

### Run 3d — second non-synthetic blob (2026-04-01)

Different cached row, ~12 days older.

| Stage | Time | Output |
|---|---|---|
| Critique | 94.7 s | 1 tool call, **3 issues** |
| Judge | 16.6 s | 2 rephrase + 1 drop decisions |

Issues:
- Featured_symbols included 6 E&P tickers (SOC, BTE, CRGY, CRC, SM, CNQ)
  that the body never mentions → judge dropped them.
- "peers like Kosmos, Baytex, and Crescent Energy traded weaker" — the
  ground truth grouped those tickers with the Sable Offshore restart but
  didn't actually break down per-ticker direction. Judge trimmed to
  "peers like Kosmos traded in sympathy" (no direction claim).
- Entire Alcoa/Newmont/LyondellBasell bullet → ground truth doesn't carry
  these tickers. Judge dropped the bullet.

The critique correctly did NOT flag *"Intel ... despite no clear catalyst
confirmed"* and *"LanzaTech jumped sharply with no clear catalyst
confirmed"* because it searched and the search didn't surface anything.
Honest filler is preserved.

## Latency summary across all rounds

| Run | Critique tool calls | Critique time | Judge time | Total added |
|---|---|---|---|---|
| 1 — 0413 baseline | 3 | 477.2 s | 5.2 s | ~482 s |
| 2 — 0413 with ground truth (over-restrictive) | 0 | 6.4 s | 8.8 s | ~15 s |
| 3a — synthetic BNO/Hormuz | 1 | 944.8 s | 4.5 s | ~949 s |
| 3b — synthetic tech rally | 1 | 990.9 s | 11.9 s | ~1003 s |
| 3c — 0413 with v3 prompt | 1 | 64.7 s | 19.5 s | ~84 s |
| 3d — 0401 with v3 prompt | 1 | 94.7 s | 16.6 s | ~111 s |

The synthetic runs are slow because every gap requires a real Tavily
roundtrip and gpt-5.3 high-reasoning per planner step. The cached blob
runs (3c, 3d) settle to ~1.5 minutes — acceptable for a once-per-build
pipeline step. The 477 s baseline (run 1) is no longer representative.

## What still isn't covered

- **`investigator.short_interest`-equivalent tool** would let the critique
  identify squeeze-driven moves the news-search path can't see. Out of
  scope for this iteration but worth flagging.
- **Reasoning-effort tuning per call** — gpt-5.3 with `reasoning_effort='high'`
  is the global default. Dropping the critique to `medium` would cut
  latency materially at some accuracy cost. The LLMConfig wiring would
  need a per-call override; not yet supported.
- **One real production end-to-end run** — `build_attention_agentic_summary_with_trace`
  combines the existing research/synthesis/verify pipeline with critique +
  judge. Tested in unit tests (mocked LLM) but not run live with the full
  upstream agentic path. Worth a single dev-pipeline run to confirm.

## BNO 68.62% — root-cause investigation (2026-04-26)

The 0413 cached payload row for BNO has `change_pct=68.62`,
`cause_status=unresolved`, `confidence_label=Developing`, with the
retained `why_now_text` saying *"No new company-specific or fund-specific
catalyst was identified"*. A 68% one-day move on a Brent crude ETF is
implausible.

`change_pct` is computed in `services/universe.py:175-176`:

```python
if pd.notna(close) and pd.notna(prev_close) and float(prev_close) != 0.0:
    change_pct = ((float(close) / float(prev_close)) - 1.0) * 100.0
```

`close` and `prev_close` come from the Alpaca snapshot
(`dailyBar.c` and `prevDailyBar.c`). Today's `daily_movers` cache shows
BNO correctly at -0.19% (close=52.59, prev_close=52.69). The snapshot
path itself is not buggy.

The most plausible cause for the 0413 datapoint is a one-off Alpaca
snapshot inconsistency — `prevDailyBar.c` returning a value that didn't
match the actual previous trading day's close (the kind of thing that can
happen around dividend ex-dates or vendor data refreshes). The original
`daily_movers` cache that produced the 68.62% has rolled over (10-min
stale TTL), so the bad row can't be re-inspected directly.

The system actually behaved well: it gave BNO `cause_status=unresolved`,
`confidence_label=Developing`, no story to back it, and routed it into
`unresolved_large_moves`. The original homepage summary then said
*"BNO traded higher relative to recent levels with no clear catalyst
confirmed"* — honest, but the underlying move was a data artifact.

The critique surfaced this naturally: in run 3c it flagged the BNO claim
because *"such a large move implies a specific catalyst yet summary
states none and does not investigate drivers"* — and the judge dropped
the misleading filler. So the new layer catches at least one class of
upstream data bug as a side effect.

**Suggested follow-up (not done in this iteration):** add a defensive
sanity guard in `services/universe.py` after the `change_pct`
computation:

1. Cap-and-flag: if `abs(change_pct) > 30%` for an ETF or `> 50%` for a
   stock, cross-check `prev_close` against the prior day's bar from
   `get_stock_bars`. If they disagree by more than a small tolerance,
   set `change_pct` to NaN and add a `data_quality_warning` column.
2. Pipeline-time alert: when `daily_movers` contains any row with
   `abs(change_pct) > 30%` AND no corroborating event in news, emit a
   warning to the run log.

Either is a small, isolated change to `services/universe.py` plus a
small test. Tracking as a known follow-up.

## Files touched in this iteration

- `services/aql/critique.py` — gap-detection prompt, ground-truth context
  with retained `why`, looser unresolved-bundle framing,
  `featured_symbols` in judge schema, section dedupe, broader issue-type
  enum (`gap`, `orphan_symbol`).
- `tests/test_aql_critique.py` — regression test for section dedupe; all
  8 unit tests pass.
- `scripts/eval_critique_harness.py` — harness with `--blob` and
  `--scenario` modes; two scenarios bundled (`bno_hormuz`,
  `tech_squeeze`).
- `documents/architecture/agents/CRITIQUE_JUDGE_HOMEPAGE_SUMMARY_2026-04-24.md`
  — original design doc (unchanged this round).
- This document.
