# Zopedia Deep Product Eval Report

Date: 2026-05-17

## Decision

**Dev-only. Hold for broad Zopedia ship.**

The native Zopedia path is now materially stronger than a renamed Chat + Search page. It can ingest pasted/source text, generate wiki pages, search/read memory, build a small graph neighborhood, create reviewable add/delete/update proposals, and answer from Zopedia memory with visible page support.

It is still not fully at standalone Zopedia bar because real YouTube transcript ingestion failed from this runtime for all three user-supplied fixtures. That is a core Zopedia journey, so broad ship stays blocked until transcript retrieval has a reliable provider path or a first-class pasted transcript fallback UX.

## What I Tested

Harness:

```bash
streamlit_alpaca_app/.venv/bin/python streamlit_alpaca_app/scripts/zopedia_product_eval.py --check-dev-urls
```

Latest run:

- JSON: `documents/architecture/new_features/zopedia/eval_runs/zopedia_product_eval_zopedia-eval-20260517-061751.json`
- Markdown: `documents/architecture/new_features/zopedia/eval_runs/ZOPEDIA_PRODUCT_EVAL_REPORT_zopedia-eval-20260517-061751.md`

Result:

| Check | Result |
| --- | ---: |
| LLM runtime | Pass |
| Dev database | Pass |
| Dev API/UI endpoints | Pass |
| Real YouTube transcript fixtures | Fail |
| Text-source ingest and LLM page generation | Pass |
| Wiki search recall | Pass |
| Exact page read | Pass |
| Graph neighborhood determinism | Pass |
| Reviewable add/delete/update proposals | Pass |
| Zopedia search/read/neighborhood tools | Pass |
| Zopedia agent memory question | Pass |

Final count: **10 passed, 1 failed**.

## Qualitative Read

The good:

- Source text became useful Zopedia memory. Three fixtures produced 23 source/entity/concept/theme pages.
- Search found the expected tagged content for diesel/Hormuz, inflation/bonds, and Nasdaq/euphoria queries.
- The agent used `zopedia.search_pages` and `zopedia.read_page`, not just a generic live search.
- The final answer cited the supporting Zopedia page title and page ID.
- The answer correctly separated retained memory from current-market confirmation:
  - It used memory for the diesel/Hormuz framework.
  - It said live event confirmation timed out.
  - It did not claim a current Hormuz event from stale memory.
- Add/delete/update graph changes stayed reviewable as proposals. No destructive mutation was applied silently.

The not-good-enough:

- Real YouTube transcript retrieval returned `ipblocked` for all three supplied URLs:
  - `BOT2rrm10RM`
  - `t6y_VmxuO28`
  - `n889nI8sR84`
- Embeddings are disabled in the dev config, so semantic retrieval is not available. The lexical fallback is better after this pass, but it is not enough for a high-quality memory product.
- Live event evidence timed out during the agent run. The agent handled it cleanly, but the run still took about 95 seconds.
- The graph eval is still headless and small. It proves deterministic memory graph behavior, not full UI usability at large graph sizes.

## Fixes Made During Eval

The first strict run exposed two real product issues:

1. Long natural-language Zopedia searches were too brittle.
   - Example: the inflation fixture existed, but the first search missed it because SQL full-text filtering was too strict before in-memory scoring.
   - Fix: `search_zopedia_pages` now falls back to a bounded recent-page candidate set when strict full-text search returns no rows, then applies the in-memory lexical scorer.

2. The agent could stop at `zopedia.search_pages` without reading the page.
   - Worse, the first harness version let this pass even when the final answer said no Zopedia memory was found.
   - Fix: seeded Zopedia search now auto-reads the top Zopedia page when budget allows, and the eval now fails unless the agent uses `zopedia.read_page`, surfaces Zopedia refs, and cites memory support in the answer.

3. Memory-backed answers did not have a strong enough citation instruction.
   - Fix: the Zopedia final-answer prompt now tells the model to cite the supporting Zopedia page title or page ID when memory is used.

## My Judgment

This is now a credible native Zopedia backend slice for dev testing.

It is not yet a finished Zopedia-quality product. The strongest reason is YouTube: standalone Zopedia's appeal includes pasting a video and getting durable memory from it. Our native pipeline correctly returns an unavailable state when blocked, but that is not parity.

The second reason is reliability under latency. A 95-second agent run with a live-source timeout is acceptable for a stress eval, not for the default product feel.

## Go / No-Go

Go:

- Keep dev testing native Zopedia.
- Use pasted transcripts/source text for reliable evaluation.
- Continue wiring AQL/SAA/attention surfaces through the Zopedia-native memory/evidence path.

No-go:

- Do not claim reliable server-side YouTube ingest.
- Do not broadly ship the rename as complete Zopedia parity.
- Do not call the graph explorer done from this headless graph pass alone.

## Next Required Work

1. Add a reliable transcript provider path or make pasted transcript upload first-class.
2. Enable/configure embeddings for Zopedia/SAA retrieval, then rerun the same product eval with semantic retrieval checks.
3. Add a UI/browser eval for graph exploration and source ingestion.
4. Add latency budgets to the eval gate:
   - first status under 3 seconds
   - no hidden tool over timeout without clear fallback
   - normal memory answer under a target threshold
5. Add cross-surface consistency evals:
   - Zopedia chat
   - attention summary
   - stock page summary
   - Trading Agent

