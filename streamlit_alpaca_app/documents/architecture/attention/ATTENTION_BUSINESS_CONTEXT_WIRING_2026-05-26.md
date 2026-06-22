# Attention Business Context Wiring

## Goal

The attention feed should not print unresolved trigger filler. It should either show the observed move, attach Zopedia business context, or leave the why field empty.

## Production Contract

- `zopedia_news_business_resolutions` and `zopedia_ticker_business_model_stacks` are loaded during `attention_home_build`.
- Matching rows are attached to `top_events`, `must_read_movers`, `unresolved_large_moves`, and `attention_bundle_snapshots`.
- If `cause_status == unresolved`, raw `why_now_text` / `why_happened_text` is cleared before materialization.
- Unresolved event titles are rebuilt from observed move text, not from the old cause writer output.
- Homepage summaries receive `business_context` per beat, so the LLM can connect price action to the company story without relying on absence prose.

## Verification

- Syntax check passed for the modified attention build, surface, summarizer, critique, writer, collector, dashboard loader, constants, and app files.
- A live materialized-layer scan over home payloads, bundle snapshots, and render-preview text found zero remaining no-catalyst leaks after applying the new contract transform.
- The renderer also applies the unresolved-state contract, so stale persisted rows are cleaned before display until the next attention build rewrites the materialized snapshots.
