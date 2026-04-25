# Critique + Judge Layer for Homepage Summary

## Status: In progress (2026-04-24)

## What This Is

Two new personas added after the existing AQL homepage summarization step:

1. **Critiquer** — agentic loop with tool access. Reads the rendered summary,
   plans tool calls to fact-check specific claims (numeric moves, named
   catalysts, internal contradictions), and returns a structured list of
   issues with grounding evidence.
2. **Judge** — single LLM call (no tools). Reads the original summary plus
   the critiquer's issues and decides per-issue whether to drop, rephrase,
   or keep. Emits a revised summary in the same `{overview, sections,
   audio_text}` shape.

Both run inside `build_attention_agentic_summary_with_trace` after the
initial summary is built. If either step fails or produces nothing
actionable, the original summary is returned unchanged.

## Why

The current homepage summary LLM (`_llm_home_summary` in
`services/aql/summarizer.py`) hallucinates concrete details that aren't in
its inputs. Concrete observed failure (BNO/Hormuz, 2026-04-24):

> "BNO surged nearly 70% in extreme trading tied to the Hormuz narrative,
> with no clear catalyst confirmed."

Three problems in one sentence:
- "70%" — invented; the LLM never saw a numeric move.
- "no clear catalyst confirmed" — fallback text from
  `attention_home_bundle_preview` paraphrased into the summary, even
  though Hormuz IS the named catalyst.
- "Hormuz narrative" — likely from upstream search trace; the LLM cannot
  contradict itself if asked to fact-check.

Verification (`verify_hypothesis`) already exists for the agentic
hypothesis but operates on a single hypothesis sentence with no tool
access. The critique layer extends that pattern to the full summary with
tools, and the judge layer applies the corrections.

## Where It Plugs In

```
build_attention_agentic_summary_with_trace
  base_summary = build_attention_home_summary(home_payload)        # existing
  queries = _plan_summary_research(...)                              # existing
  trace   = _collect_summary_research_trace(...)                     # existing
  hypothesis  = _synthesize_attention_home_hypothesis(...)           # existing
  verification = verify_hypothesis(hypothesis, claims, ...)          # existing

  # NEW
  critique = critique_home_summary(
      summary=base_summary,
      home_payload=home_payload,
      llm_client=llm_client,
      query_service=query_service,
  )
  if critique.get("issues"):
      revised = judge_revise_summary(
          original=base_summary,
          critique=critique,
          llm_client=llm_client,
      )
      base_summary = revised  # used downstream by hypothesis prepend etc.
```

The deterministic `build_attention_home_summary` path stays untouched —
no llm_client/query_service needed there.

## Critique Loop

New module: `services/aql/critique.py`.

- Tool subset (a focused slice of `agent_tools.build_tool_catalog`):
  - `research.search_evidence` — search retained SAA evidence
  - `research.retained_context` — narrative context lookup
  - `research.live_event_evidence` — fresh web evidence
  - `investigator.technical_signals` — actual price / move / regime
  - `investigator.recent_news` — recent headlines per ticker
- Loop bound: `_P_CRITIQUE_MAX_TOOL_CALLS` (default 4).
- Step schema: `{action: tool_call|final, tool_name, tool_arguments,
  reasoning, issues}`. Mirrors `_STEP_SCHEMA` in `omnibar_agent.py`.
- Final issue schema:
  `{type: numeric|contradiction|unsupported|stale, location, claim,
    severity: high|medium|low, evidence}`.
- System prompt: persona is "fact-check editor", told explicitly to flag
  invented numbers, internal contradictions, and unsupported named
  catalysts. Forbidden from rephrasing — only flagging.

## Judge Call

Single LLM call. No tools.

- Input: original summary JSON, critique issues, signal context.
- Output: same `_HOME_SUMMARY_SCHEMA` (overview / sections / audio_text)
  plus `revisions_applied: [{issue_index, decision: drop|rephrase|keep,
  rewritten_text}]`.
- System prompt enforces: preserve specifics that weren't flagged;
  only modify text tied to a flagged issue; never introduce new claims
  the critique didn't surface.

## Reliability / Failure Modes

- LLM unavailable → critique returns `{issues: []}`; judge skipped;
  original summary returned.
- Tool errors during critique → logged, loop continues, partial issues
  still flagged.
- Judge call fails → original summary returned. Critique issues still
  persisted in trace.
- Config-disable: `_P_CRITIQUE_ENABLED` (default True).

## Trace / Persistence

Two new keys on the returned summary:
- `critique_issues: list[dict]` — raw issues with evidence.
- `judge_revisions: list[dict]` — what the judge changed.

These flow through `build_attention_agentic_summary_with_trace`'s return
tuple but are NOT new trace dataframes for now. If they prove useful
they can be promoted into `attention_*` tables later.

## Token / Latency Cost

- Critique: up to 4 planner calls + tool roundtrips → ~10–25s.
- Judge: 1 call → ~3–5s.
- Net: roughly doubles agentic-summary build time. Pipeline-only path,
  not on the request path.

## Out of Scope

- Truncation of beats (existing behavior preserved).
- New trace tables (defer until usage shows we need them).
- Fact-checking the agentic hypothesis itself (kept on
  `verify_hypothesis`; could be unified later).
