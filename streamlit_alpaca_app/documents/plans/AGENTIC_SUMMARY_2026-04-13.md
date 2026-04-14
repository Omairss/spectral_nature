# Agentic Market Summary

## Status: Implemented

## What This Is

Replace the deterministic `_build_materialized_homepage_summary` in the attention home build job with an agentic loop that thinks, plans searches, executes research, and generates a market hypothesis — not just a templated recap of beats.

## Problem Today

`_build_materialized_homepage_summary` (job line 484) is fully deterministic. It templates beats from the already-built `home_payload` into `summary_text` and `audio_text`. It has no LLM, no additional evidence, no cross-beat synthesis. The `llm_client` is available in the job but never passed to this step.

## Target Behavior

```
home_payload (beats, events, movers)
  → LLM: what macro/sector questions do these raise?
  → 3–5 targeted search queries (cross-market, sector, macro — not per-symbol)
  → Execute via SerpAPI / Tavily
  → Extract claims from results
  → LLM: synthesize beats + existing claims + new claims → market hypothesis
  → Output: hypothesis paragraph + summary_text + audio_text
```

Falls back to the existing deterministic formatter if LLM is unavailable or the agentic step fails.

## Implementation Notes

- `services/aql/collector.py` now includes `_plan_summary_research(...)`, which plans 3-5 whole-tape search queries with an LLM-first path and a bounded heuristic fallback.
- `services/aql/summarizer.py` now includes `build_attention_agentic_summary(...)`, which:
  - builds deterministic beats first
  - plans whole-tape queries
  - runs shared search collection
  - deepens selected Seeking Alpha hits with authenticated page text when that source shows up in search results
  - extracts fallback claims from returned evidence
  - synthesizes a search-backed `hypothesis`
  - prepends that hypothesis to the persisted homepage summary text and audio text
- `pipeline/jobs/attention_home_build.py` now passes `llm_client` into the homepage summary step and falls back to the deterministic summary if the agentic path fails.
- Authenticated Seeking Alpha access now lives in the shared page-browsing layer:
  - `services/seeking_alpha_access.py` resolves credentials from Key Vault or env
  - `services/page_browsing.py` prefers that path for `seekingalpha.com` URLs
  - `services/aql/extractor.py` prefers captured `page_text` over raw search snippets when available
- The pipeline deploy path now pushes non-sensitive Seeking Alpha runtime env keys:
  - `SEEKING_ALPHA_USERNAME_SECRET_NAME`
  - `SEEKING_ALPHA_PASSWORD_SECRET_NAME`
  - `SEEKING_ALPHA_BROWSER_HEADLESS`
  - `ATTENTION_HOME_SEEKING_ALPHA_PAGE_LIMIT`
  - `ATTENTION_HOME_SEEKING_ALPHA_PAGE_MAX_CHARS`
- Public exports now expose `build_attention_agentic_summary` through both `services/aql/__init__.py` and `services/attention_home_summary.py`.
- Tests cover the planner, the agentic summary builder, and the job-level fallback boundary.

## Implementation Plan

### Step 1 — Summary-level research planner (new)
Add `_plan_summary_research` to `services/aql/collector.py`.

- Input: `home_payload` (top events, must-read movers, unresolved moves)
- LLM prompt: given these tape items, generate 3–5 cross-market search queries to identify the macro/sector narrative driving them
- Output: `list[str]` of search queries
- Pattern: same structure as `_plan_candidate_research`, but reasoning over the whole tape not a single symbol

### Step 2 — Agentic summary function (new)
Add `build_attention_agentic_summary` to `services/aql/summarizer.py`.

Signature:
```python
def build_attention_agentic_summary(
    home_payload: dict[str, object],
    *,
    llm_client: LLMClient,
    search_clients: list[Any] | None = None,
    max_search_queries: int = 5,
    max_chars: int = 1400,
) -> dict[str, Any]:
```

Loop:
1. Call `_plan_summary_research(home_payload, llm_client=llm_client)` → queries
2. Execute each query via `_search_query_results` (collector) → raw results
3. Chunk + extract claims via `_chunk_source_documents` + `_fallback_claims_from_chunks` (extractor)
4. LLM synthesis prompt: beats from `build_attention_home_narrative_beats` + new claims → hypothesis paragraph
5. Return same shape as `build_attention_home_summary` but with `hypothesis` field added

### Step 3 — Wire into the job
Update `_build_materialized_homepage_summary` in `pipeline/jobs/attention_home_build.py`:

```python
def _build_materialized_homepage_summary(
    payload: dict[str, Any],
    *,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    if llm_client is not None:
        try:
            return build_attention_agentic_summary(payload, llm_client=llm_client)
        except Exception as exc:
            print(f"[warn] agentic summary failed, falling back: {exc}")
    # existing deterministic path
    summary_payload = build_attention_home_summary_payload(payload)
    ...
```

Pass `llm_client` from `run_attention_home_build` through to this call.

### Step 4 — Expose via public API
Add `build_attention_agentic_summary` to `services/aql/__init__.py` and `services/attention_home_summary.py` shim.

### Step 5 — Tests
- Unit test for `_plan_summary_research` with a mock LLM that returns queries
- Unit test for `build_attention_agentic_summary` with mock LLM + mock search clients
- Integration smoke test: run with real `home_payload` fixture, assert `hypothesis` key present

## Files Touched

| File | Change |
|------|--------|
| `services/aql/collector.py` | Add `_plan_summary_research` |
| `services/aql/summarizer.py` | Add `build_attention_agentic_summary` |
| `services/aql/__init__.py` | Export `build_attention_agentic_summary` |
| `services/attention_home_summary.py` | Re-export shim |
| `pipeline/jobs/attention_home_build.py` | Thread `llm_client` into summary step, try agentic path first |
| `tests/test_aql_summarizer.py` | New tests |

## Dependency

All building blocks exist in AQL:
- `collector._search_query_results` — search execution
- `collector._plan_candidate_research` — pattern for LLM research planning
- `extractor._chunk_source_documents`, `_fallback_claims_from_chunks` — claim extraction
- `summarizer.build_attention_home_narrative_beats` — beat formatting for the synthesis prompt

No new external service is needed, but authenticated page access does require browser runtime support in the shared images.

## Output Shape (addition to existing summary)

```json
{
  "headline": "Tape Summary",
  "hypothesis": "Rate transmission fears are driving today's tape...",
  "summary_text": "...",
  "audio_text": "...",
  "event_count": 3,
  "must_read_count": 5,
  "unresolved_count": 2,
  "featured_symbols": ["TSLA", "SPY", ...],
  "beats": [...]
}
```

## Fallback Behavior

If `llm_client` is None, or if the agentic loop raises, fall back to the existing deterministic `build_attention_home_summary_payload` path. Same fallback pattern used throughout the job today.
