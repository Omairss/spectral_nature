# Attention Architecture

This folder holds durable design docs for the Attention feed, homepage summaries, research quality, and UI/data wiring.

Boundary rule: Attention owns market activity detection, event grouping, signal graph construction, and homepage-ready payloads. It may call AQL public APIs for research-backed explanations, but should not import AQL private helpers.

## Docs

- `ATTENTION_FEED_IMPLEMENTATION_PLAN.md`: canonical Attention feed stack plan
- `ATTENTION_FEED_EVENT_REDESIGN_PLAN.md`: event redesign background and product context
- `ATTENTION_RESEARCH_QUALITY_FIX_2026-04-14.md`: staged fix for generic hypotheses and weak event text
- `ATTENTION_HOME_SUMMARY_SAA_WIRING_2026-04-15.md`: homepage summary evidence through SAA retrieval
- `ATTENTION_HOME_UI_RUNTIME_FIX_2026-04-15.md`: homepage UI trace and runtime behavior fixes
