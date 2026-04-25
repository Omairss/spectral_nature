# Attention vs AQL Consolidation Plan

**Date:** 2026-04-24
**Goal:** Clean separation — Attention = raw signal processing, AQL = reasoning/narration

---

## The Current Architecture (as-is)

The system has two parallel codepaths that grew organically:

**Old path (attention_*.py files):** Built first. Each file does both signal processing AND narration inline. LLM calls are scattered throughout.

**New path (services/aql/):** Built as a "bottom-up" replacement. AQL's `pipeline.py` re-implements much of what the attention files do, but with a more structured research -> claims -> synthesis pipeline.

The pipeline job (`attention_home_build.py`) currently calls into **both** — it uses `attention_home_1d` for shortlisting, then hands off to AQL's `build_bottom_up_attention_artifacts()` for the heavy lifting, but the old attention files still exist with their own parallel LLM calls.

---

## Specific Duplications Found

### 1. Event Text (title + what_happened + why_happened)

| Attention path | AQL path |
|---|---|
| `attention_market_events.py` `_llm_event_text()` — LLM generates title, what_happened, why_happened | `aql/writer.py` `_write_event_bundle()` — LLM generates title, surface_summary, what_happened_text, why_happened_text, affected_assets_summary_text |
| Uses `_EVENT_TEXT_SCHEMA` (3 fields) | Uses `EVENT_WRITER_SCHEMA` (6 fields, superset) |
| Has its own system prompt | Has its own `EVENT_WRITER_SYSTEM_PROMPT` |

**Verdict:** AQL's version is strictly richer. The attention version is dead weight.

### 2. Symbol/Mover Text (what_changed + why)

| Attention path | AQL path |
|---|---|
| `attention_home_1d.py` `_move_vs_expectation_text()` — LLM one-liner "AAPL rose 3.2% today" | `aql/writer.py` `_write_symbol_bundle()` — LLM generates title, surface_summary, what_changed_text, why_today_text, what_else_moved_text, background_context_text |
| `attention_live_research.py` `_synthesize_with_llm()` — generates why_now_text, what_else_moved_text, background_context_text | Same AQL writer does all of this |

**Verdict:** AQL's writer produces the same fields with a single call. The attention path makes 2-3 separate LLM calls for the same outputs.

### 3. Cause Status Judging

| Attention path | AQL path |
|---|---|
| `attention_live_research.py` `_judge_cause_status()` — examines same-day evidence, authority, themes -> returns (supported/continuation/unresolved, mode) | `aql/_shared.py` `_judge_cause_status()` — same logic, same return signature |

**Verdict:** Direct duplicate. Same function exists in both places.

### 4. Evidence Quality Labels

| Attention path | AQL path |
|---|---|
| `attention_live_research.py` `_quality_label()` -> (evidence_quality, freshness_quality) | `aql/_shared.py` `_quality_label()` -> same return |

**Verdict:** Direct duplicate.

### 5. Narrative Quality Filtering (stat dumps, causal language, low signal)

| Attention path | AQL path |
|---|---|
| `attention_surface.py` `clean_attention_text()`, `has_causal_language()`, `looks_like_stat_dump_text()`, `looks_like_model_math_explanation()` | `aql/_shared.py` `_has_causal_language()`, `_looks_like_stat_dump()`, `_is_low_signal()`, `_is_yield_only_explanation()`, `_looks_like_generic_market_activity_title()` |

**Verdict:** AQL's versions are more comprehensive (more patterns, more checks). Attention's versions are a subset.

### 6. News Search

| Attention path | AQL path |
|---|---|
| `attention_live_research.py` `search_symbol_news_payload()` — SerpAPI + Tavily with relevance gate | `aql/collector.py` `search_symbol_news_payload()` — SerpAPI + Tavily with LLM relevance gate |

**Verdict:** AQL's version adds LLM relevance gating. The attention version is the older path.

### 7. Evidence Normalization

| Attention path | AQL path |
|---|---|
| `attention_live_research.py` `_normalize_news_evidence()`, `_normalize_context_evidence()`, `_normalize_filing_evidence()`, `_normalize_event_news_evidence()` — scores by mention, freshness, causal | `aql/extractor.py` `_documents_from_search_results()`, `_chunk_source_documents()`, `_rank_evidence_chunks()` — chunks, ranks by 7 weighted factors, extracts structured claims |

**Verdict:** AQL's approach is fundamentally better — it chunks, ranks, and extracts structured claims rather than just scoring raw articles.

---

## Things Currently in the Wrong System

### In Attention but should be AQL (reasoning/narration)

| File | What it does | Why it belongs in AQL |
|---|---|---|
| `attention_market_events.py` | LLM event text, theme classification, expected reactions, market-activity narrative | All reasoning/narration over signals |
| `attention_home_1d.py` `_move_vs_expectation_text()` | LLM mover text generation | Narration |
| `attention_live_research.py` (entire file) | Web research, evidence normalization, LLM synthesis, cause judging | Research + reasoning |
| `attention_context_llm.py` | EDGAR evidence extraction, narrative generation | Reasoning over filings |
| `attention_feed_brief.py` | LLM feed card briefs | Narration |
| `attention_surface.py` text cleaning | Filtering stat dumps, cleaning narrative text | Narration quality control |

### In AQL but should be Attention (signal processing)

| File | What it does | Why it belongs in Attention |
|---|---|---|
| `aql/clusterer.py` `_graph_edges()` | Pairwise correlation/relationship graph construction | Raw signal — same-industry, correlation, role matching |
| `aql/_shared.py` `_history_correlation_map()`, `_return_series_from_bars()` | Correlation matrix computation from price bars | Pure signal processing |
| `aql/pipeline.py` candidate building | Builds `attention_event_candidates_1d` from daily movers | Candidate identification is signal work |
| `aql/macro.py` `_build_macro_relationship_checks()` | Observed vs expected sign checking | The observation part is signal; the hypothesis part is reasoning |

---

## Proposed Target Architecture

```
ATTENTION SYSTEM (signal processing — "what deserves attention")
├── compute/signal_extraction.py          (unchanged — slopes, z-scores, betas, regimes)
├── attention_home_1d.py                  (candidate scoring, shortlisting, entity master)
│   └── REMOVE: _move_vs_expectation_text, evidence gathering, cause_status
│   └── KEEP: shortlist_attention_symbols_1d, build_attention_entity_master,
│             _candidate_score, resolve_macro_anchor_symbols
├── attention_graph_network.py            (unchanged — network visualization)
├── attention_graph_topology.py           (unchanged — taxonomy graph)
├── attention_ticker_snapshots.py         (unchanged — ticker metadata)
├── attention_materialized.py             (unchanged — serialization)
├── NEW: correlation/clustering inputs    (absorb from aql/clusterer._graph_edges,
│                                          aql/_shared._history_correlation_map)
├── NEW: macro signal checks              (absorb observation logic from aql/macro.
│                                          _build_macro_relationship_checks)
└── attention_market_events.py            (STRIP all LLM calls — keep only:
                                           theme detection via roles/tags,
                                           anchor+member identification,
                                           event scoring by breadth/move size)

AQL SYSTEM (reasoning — "why, what, how")
├── collector.py                          (unchanged — web search, research planning)
├── extractor.py                          (unchanged — chunking, claim extraction)
├── writer.py                             (absorb ALL narration — event text from
│                                          attention_market_events, mover text from
│                                          attention_home_1d, feed briefs from
│                                          attention_feed_brief, context narratives
│                                          from attention_context_llm)
├── assembler.py                          (unchanged — payload assembly)
├── summarizer.py                         (unchanged — homepage summary)
├── macro.py                              (keep hypothesis generation & verification,
│                                          move observation checks to Attention)
├── clusterer.py                          (STRIP correlation computation — keep only
│                                          claim-based clustering logic)
├── _shared.py                            (single source for cause_status, quality_label,
│                                          narrative quality checks)
├── evidence_index.py                     (unchanged)
├── constants.py                          (unchanged)
├── config.py                             (unchanged)
├── chat_log.py                           (unchanged)
├── scratchpad.py                         (unchanged)
└── pipeline.py                           (orchestration — receives signal candidates
                                           from Attention, adds reasoning layer)

FILES TO RETIRE:
├── attention_live_research.py            -> fully absorbed by AQL collector + extractor + writer
├── attention_context_llm.py              -> absorbed by AQL writer (EDGAR reasoning)
├── attention_feed_brief.py               -> absorbed by AQL writer
├── attention_surface.py                  -> text cleaning moves to AQL _shared.py;
│                                           hydration/rebalance stays as thin display logic
├── attention_agentic.py (shim)           -> simplify once AQL is the sole reasoning path
└── attention_home_summary.py (shim)      -> already just re-exports from AQL, keep as-is
```

---

## The Clean Interface Between Systems

After consolidation, the handoff should be:

```
Attention produces:
  - candidate_frame: symbol, direction, change_pct, expected_move_pct,
    surprise_z, attention_score, candidate_score, asset metadata, roles/tags
  - correlation_edges: symbol_a, symbol_b, weight, edge_type
  - market_event_signals: event_type, anchor, members, breadth, score
    (NO narrative text — just structural signal)
  - macro_observation_checks: edge_id, expected_sign, observed_sign, consistency

AQL receives those and produces:
  - Research (web search, evidence collection)
  - Claims (structured extraction from evidence)
  - Hypotheses (generation + verification)
  - Narratives (event text, mover text, feed briefs, summaries)
  - Bundles (assembled payloads for UI)
```

---

## Execution Order (suggested phases)

### Phase 1 — Deduplicate the shared utilities

- Delete `_judge_cause_status` and `_quality_label` from `attention_live_research.py`, point callers to `aql/_shared.py`
- Delete narrative quality checks from `attention_surface.py`, point to `aql/_shared.py`
- Delete `search_symbol_news_payload` from `attention_live_research.py`, use `aql/collector.py`'s version everywhere

### Phase 2 — Move signal processing out of AQL

- Extract `_graph_edges()` correlation computation from `aql/clusterer.py` into an attention signal module
- Extract `_history_correlation_map` from `aql/_shared.py` into `compute/signal_extraction.py`
- Split `aql/macro.py`: observation checks -> Attention, hypothesis reasoning -> stays in AQL

### Phase 3 — Move narration out of Attention

- Strip LLM calls from `attention_market_events.py` (keep structural event detection, remove `_llm_event_text`, `_llm_symbol_bucket`, `_llm_theme_keywords`, `_llm_expected_reaction_map`, `_theme_market_activity_why`)
- Route event narration through `aql/writer.py::_write_event_bundle()`
- Strip `_move_vs_expectation_text` from `attention_home_1d.py`, route through AQL writer
- Move EDGAR narrative logic from `attention_context_llm.py` into AQL
- Move feed brief logic from `attention_feed_brief.py` into AQL

### Phase 4 — Retire dead files

- Delete `attention_live_research.py` (all functionality now in AQL)
- Delete `attention_context_llm.py` (absorbed by AQL)
- Delete `attention_feed_brief.py` (absorbed by AQL)
- Slim down `attention_surface.py` to just display hydration (no LLM, no filtering)
- Update all imports in pipeline jobs, tests, app.py

### Phase 5 — Update tests

- Retarget tests in `test_services.py` that test retired functions to their AQL equivalents
- Verify pipeline end-to-end produces same outputs

---

## Conflicts / Things to Watch

1. **`attention_market_events.py` theme detection uses LLM for bucket classification** (`_llm_symbol_bucket`). Under the new model, bucket classification by role/tag is a signal concern (deterministic), but when the tag is missing, the LLM fallback is reasoning. Decision: keep deterministic path in Attention, add an "unclassified" bucket, let AQL reason about unclassified symbols later.

2. **`aql/macro.py` straddles both systems.** The relationship checks (observed vs expected sign) are signal processing. The hypothesis generation and web-evidence verification are reasoning. This file needs to be split.

3. **`attention_surface.py` hydration/rebalance logic** (`hydrate_home_item_with_bundle`, `rebalance_attention_home_payload`) is display-layer work. It doesn't fit neatly in either system — it's really presentation. Consider keeping it as a thin display adapter that sits downstream of both.

4. **The pipeline job `attention_home_build.py`** currently calls `attention_home_1d` for shortlisting then AQL for everything else. After consolidation, the flow becomes cleaner: Attention signal modules produce candidates + edges + event signals -> AQL pipeline receives those and produces all narrative artifacts.

---

## Implementation Status — 2026-04-24

### Completed

- Moved historical return correlation helpers into `compute/signal_extraction.py`; `aql/_shared.py` now imports compatibility aliases from compute.
- Moved candidate graph edge construction into `services/attention_signal_graph.py`; AQL clusterer and shims import the Attention-owned graph builder.
- Moved macro relationship observation checks into `services/attention_macro_signals.py`; `aql/macro.py` keeps hypothesis generation and imports the signal checks.
- Removed the duplicate `search_symbol_news_payload()` implementation from `attention_live_research.py`; legacy callers now use `aql/collector.py`.
- Pointed legacy live-research cause-status and quality-label adapters at `aql/_shared.py`, and extended AQL quality labels to preserve authoritative same-day evidence behavior.
- Pointed `attention_surface.py` causal/stat-dump checks at `aql/_shared.py`; hydration and rebalance remain as presentation adapters.
- Removed the per-candidate mover-text LLM call from `attention_home_1d.py`; candidates now carry structured move fields and AQL writer owns prose.
- Removed LLM-backed implementations from legacy `attention_market_events.py` for theme keywords, expected reactions, symbol buckets, and event text; deterministic signal rules now produce structural event fields. The old `_llm_*` private names remain as non-LLM compatibility adapters for tests and monkeypatch callers.

### Compatibility Kept

- `attention_live_research.py`, `attention_context_llm.py`, and `attention_feed_brief.py` were not deleted yet because pipeline jobs, presentation loaders, and tests still import them directly.
- `attention_live_research.py` remains a compatibility path for event search and live bundle hydration until the remaining EDGAR/feed brief writers are fully moved under AQL modules.

### Verification

- `python -m py_compile` passed for the touched compute, Attention, and AQL modules using the project `.venv`.
- `pytest tests/test_services.py -k "graph_edges or recompute_attention_candidate_graph or attention_home_surface_summary"` passed: 8 selected tests.
- `pytest tests/test_services.py::test_build_attention_market_events_promotes_oil_shock_into_market_event tests/test_services.py::test_build_attention_market_events_uses_observed_move_direction_for_event_text tests/test_services.py::test_build_attention_market_events_does_not_pull_generic_energy_names_into_oil_event` passed.
- `pytest tests/test_services.py::test_build_live_attention_research_bundle_prefers_same_day_news_and_separates_background_context` passed after the AQL quality-label adjustment.
- A broader selected service run passed 14/15 before the quality-label fix; it was not repeated because the sandbox spends several minutes trying blocked Azure credential network paths during live-research tests.
