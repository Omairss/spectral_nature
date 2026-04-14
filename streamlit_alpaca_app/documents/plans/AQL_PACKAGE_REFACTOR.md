# AQL — Attention Query Layer Refactor

## Status: Phase 2 Complete (deployed to dev 2026-04-13)

## What AQL Is

AQL (Attention Query Layer) is the consolidated intelligence layer for the attention pipeline. It owns all research, synthesis, and summarization logic that was previously split across `services/attention_agentic.py` (~5,500 lines) and `services/attention_home_summary.py` (~370 lines).

## Problem Before

Intelligence was fragmented:
- `attention_agentic.py` — a 5,575-line monolith covering research planning, web search, document chunking, claim extraction, bundle writing, event clustering, macro analysis, and payload assembly
- `attention_home_summary.py` — deterministic summarization + ElevenLabs audio, orphaned outside the main file
- No single ownership boundary for "intelligence"

## Phase 1: Establish the Package Boundary (Done 2026-04-12)

### What changed

```
services/
  aql/                          ← NEW
    __init__.py                 ← Public API
    _agentic.py                 ← Moved from attention_agentic.py (imports updated)
    _summary.py                 ← Moved from attention_home_summary.py (imports updated)
  attention_agentic.py          ← Now a thin shim (re-exports from aql)
  attention_home_summary.py     ← Now a thin shim (re-exports from aql)
```

### What didn't change

All external callers (`attention_home_build.py`, `data_access/layer.py`, `omnibar_research.py`, `attention_live_research.py`, `app.py`, etc.) import unchanged. The shims preserve backward compatibility.

### Public API (services/aql)

**Pipeline:**
- `build_bottom_up_attention_artifacts` — main entry point, returns `AgenticAttentionArtifacts`
- `build_bottom_up_attention_home` — returns home_payload only
- `build_bottom_up_attention_bundle` — retrieves a specific bundle
- `recompute_attention_candidate_graph` — rebuilds candidate correlation graph

**Evidence collection:**
- `search_symbol_news_payload` — fetch news for a symbol via SerpAPI/Tavily

**Summarization:**
- `build_attention_home_narrative_beats` — deterministic beats from home_payload
- `build_attention_home_summary` — structured summary dict
- `build_attention_home_summary_payload` — sanitized summary payload
- `attach_attention_home_summary_audio` — attach ElevenLabs audio

**Types:**
- `AgenticAttentionArtifacts` — dataclass: `home_payload`, `bundle_map`, `frames`

## Phase 2: Internal Sub-module Split (Done)

Split `_agentic.py` into focused sub-modules. Final layout:

```
services/aql/
  __init__.py       (public API, unchanged)
  constants.py      (all constants, schemas, type aliases, dataclass)
  config.py         (_load_attention_macro_signal_profile, etc.)
  _shared.py        (utilities: coerce_text, normalize_symbol, text helpers, scoring)
  collector.py      (search_symbol_news_payload, _search_query_results, _candidate_context_documents)
  extractor.py      (_extract_claims, _chunk_source_documents, _documents_from_search_results)
  writer.py         (_write_symbol_bundle, _write_event_bundle, _plan_candidate_research)
  clusterer.py      (_cluster_candidates, _graph_edges, event clustering)
  macro.py          (_build_macro_release_events, _verify_macro_hypotheses_with_web_evidence, etc.)
  assembler.py      (_build_candidate_bundle, _build_event_bundle, _build_home_payload)
  summarizer.py     (moved from _summary.py, same content)
  pipeline.py       (build_bottom_up_attention_artifacts and orchestration wrappers)
```

**Dependency order (no cycles):**
constants → config → _shared → collector → extractor → writer → clusterer → macro → assembler → pipeline

## Why AQL

The name "AQL" (Attention Query Layer) reflects the module's role: it takes market attention signals as input and queries across multiple evidence sources (web, filings, macro data) to produce structured intelligence output. It is the complete query + synthesis stack, not just a web search client.
