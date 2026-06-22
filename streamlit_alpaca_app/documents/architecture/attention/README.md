# Attention Architecture

This folder holds durable design docs for the Attention feed, homepage summaries, research quality, and UI/data wiring.

Boundary rule: Attention owns market activity detection, event grouping, signal graph construction, and homepage-ready payloads. It may call AQL public APIs for research-backed explanations, but should not import AQL private helpers.

## Docs

- `ATTENTION_FEED_IMPLEMENTATION_PLAN.md`: canonical Attention feed stack plan
- `ATTENTION_FEED_EVENT_REDESIGN_PLAN.md`: event redesign background and product context
- `ATTENTION_RESEARCH_QUALITY_FIX_2026-04-14.md`: staged fix for generic hypotheses and weak event text
- `ATTENTION_HOME_SUMMARY_SAA_WIRING_2026-04-15.md`: homepage summary evidence through SAA retrieval
- `ATTENTION_HOME_UI_RUNTIME_FIX_2026-04-15.md`: homepage UI trace and runtime behavior fixes
- `ATTENTION_FEED_GENERATION_TRACE_AND_OVERINDEXING_2026-05-19.md`: verified end-to-end trace of how each feed item is produced today, plus the root-cause diagnosis of solar/AEHR-style overindexing (selection + scoring artifact, not hardcoding)
- `ATTENTION_ANOMALY_DETECTOR_DESIGN_2026-05-26.md`: redesign — replace the additive percent-move score with a single standardized data-anomaly score (detection, not prediction); admin-config and AQL/Zopedia summarizer integration; empirical justification for no prediction layer
- `CLICKABLE_STOCK_SUMMARY_PLAN.md`: TODO — make stock summary sentences clickable for Zopedia deep-dive ("past context in light of new information")
