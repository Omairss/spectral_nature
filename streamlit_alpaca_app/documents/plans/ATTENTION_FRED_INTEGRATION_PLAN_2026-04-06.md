# Attention + FRED Integration Plan (2026-04-06)

## Goal

Integrate FRED macro context into the **attention scoring architecture** (not only narrative evidence) so anomaly ranking can account for macro regime alignment when relevant.

## Current State

What already exists:

- `macro-fred-daily` persists `fred_summary`, `fred_observations`, `fred_series_index`, `fred_release_index`, and yield-curve datasets.
- `attention-home-build` already passes `fred_summary` and `yield_curve_facts_1d` into `attention_agentic` for explanation/evidence enrichment.
- `compute/anomalies.py` scoring (`severity`, `impact`, `relevance`, `confidence`) currently does **not** consume FRED datasets.

Implication:

- FRED helps narrative quality today, but does not influence anomaly rank ordering.

## Problem To Solve

- Macro-sensitive names (rates, inflation, commodities, dollar-sensitive equities) can be under-ranked or over-ranked because the ranking layer cannot distinguish:
  1. idiosyncratic surprises, versus
  2. moves consistent with a strong macro regime shift.

## Requested Behavioral Outcome

Build an internal macro relationship model that can do all of the following:

1. ingest a shock/event (for example, a BLS labor surprise),
2. traverse related nodes (Fed policy path, rates, inflation, dollar, yield curve, equity factor buckets),
3. check whether expected relationships still hold in live/near-live market data,
4. produce hypotheses with an LLM,
5. verify hypotheses with web evidence (SerpApi + Tavily),
6. expose a final status (`supported`, `continuation`, `conflicting`, `unresolved`) with traceable evidence.

The model must allow conditional or regime-dependent edges (for example, 10Y yield reaction can differ by growth-shock vs inflation-premium regime), not fixed one-direction assumptions.

## Design Principles

- Keep **source-first** architecture: precompute and materialize once, read many.
- Avoid hardcoded symbol/series logic in scoring code; use versioned runtime mapping config.
- Preserve current behavior by default; add FRED influence via staged rollout and shadow scoring first.
- Degrade honestly on stale/missing macro data (zero macro contribution + explicit provenance fields), not silent heuristic guesses.

## Target Architecture

### 1) New materialized macro-to-attention bridge datasets

1. `macro_signal_state_1d`
- Grain: one row per macro signal (`signal_id`) at `asof_time_utc`
- Inputs: `fred_summary`, `yield_curve_facts_1d`
- Fields: `signal_id`, `series_id`, `signal_group`, `direction`, `z_like_magnitude`, `delta_1d`, `delta_5d`, `freshness_hours`, `source_dataset`, `schema_version`

2. `symbol_macro_exposure_1d`
- Grain: one row per `symbol`
- Inputs: `entity_taxonomy_labels` (+ optional curated overrides)
- Fields: `symbol`, `macro_role_tags`, `exposure_weights_json`, `source`, `schema_version`

3. `attention_macro_context_1d`
- Grain: one row per `symbol x horizon`
- Inputs: `price_expectations`, `macro_signal_state_1d`, `symbol_macro_exposure_1d`
- Fields: `symbol`, `horizon`, `macro_alignment_score`, `macro_conflict_score`, `macro_signal_count`, `macro_staleness_hours`, `schema_version`

4. `macro_causal_graph_edges_v1`
- Grain: one row per directed edge (`from_node -> to_node`)
- Purpose: versioned causal relationship map for propagation and consistency checks
- Fields: `edge_id`, `from_node`, `to_node`, `expected_sign`, `lag_window`, `regime_filter`, `strength_weight`, `confidence_prior`, `source`, `schema_version`

5. `macro_relationship_checks_1d`
- Grain: one row per edge-check execution (`edge_id x asof_time_utc`)
- Purpose: record whether each relationship is currently holding
- Fields: `edge_id`, `from_node`, `to_node`, `observed_sign`, `observed_strength`, `consistency_status`, `regime_used`, `asof_time_utc`, `schema_version`

6. `attention_hypotheses_1d`
- Grain: one row per hypothesis per focal candidate/event
- Purpose: persist generated and verified macro hypotheses for downstream ranking/narrative
- Fields: `hypothesis_id`, `candidate_id`, `hypothesis_text`, `support_status`, `support_score`, `contradiction_score`, `evidence_count`, `asof_time_utc`, `schema_version`

7. `macro_release_events_1d`
- Grain: one row per high-importance scheduled macro release instance (for example, NFP, CPI, FOMC decision)
- Purpose: explicit release event objects for homepage/event ranking, independent of symbol-mover gates
- Fields: `release_event_id`, `release_type`, `release_time_utc`, `surprise_score`, `importance_tier`, `primary_nodes`, `initial_hypothesis`, `status`, `asof_time_utc`, `schema_version`

### 2) Scoring integration in `compute/anomalies.py`

Add optional macro context input to `build_attention_candidates(...)`:

- New optional argument: `macro_context: pd.DataFrame | None = None`
- New output fields on anomaly candidates/events:
  - `macro_alignment_score`
  - `macro_conflict_score`
  - `macro_signal_count`
  - `macro_data_fresh`

### 3) Ranking strategy (phased)

Phase A (safe):
- Compute and persist macro fields.
- Keep existing `attention_score` unchanged.
- Add `attention_score_v2_shadow` with macro contribution for evaluation only.

Phase B (controlled):
- Gate macro-aware ranking with env/config switch.
- Promote `attention_score_v2` only after shadow metrics pass acceptance criteria.

### 4) Relationship propagation + check loop

For each detected macro shock:

1. map event to source node(s) (`labor`, `inflation`, `growth`, `policy`, `usd`, `real_yields`, `equity_duration`, etc.),
2. traverse outgoing edges from the causal graph with regime-aware filters,
3. compute expected directional impacts on connected nodes,
4. run edge-level consistency checks using observed market/FRED/yield data,
5. emit `macro_relationship_checks_1d` with pass/fail/mixed status.

This loop provides the machine-checkable state before any LLM narrative generation.

### 5) Hypothesis generation and verification workflow

1. Deterministic draft hypotheses
- Build candidate hypotheses from propagated paths where checks are mixed or strong.
- Example style: “Labor softening implies easier policy path, supporting duration-sensitive tech; USD weakness and curve behavior are partially consistent.”

2. LLM hypothesis refinement
- LLM rewrites deterministic candidates into concise, testable hypotheses.
- LLM output must include explicit claim units and expected evidence types.

3. Verification via SerpApi + Tavily
- Reuse existing `attention_agentic` retrieval clients and relevance filters.
- Score each claim for support/contradiction from retained documents.
- Persist final hypothesis status and trace rows in `attention_hypotheses_1d` and `attention_claims`.

4. Decision contract
- Final per-hypothesis status must be one of: `supported`, `continuation`, `conflicting`, `unresolved`.
- Ranking and narrative consume this status; they never bypass verification.

### 6) Release importance + surprise definitions (v1)

`importance_tier` defaults:

- `high`
  - NFP payrolls
  - unemployment rate
  - CPI headline/core (monthly release)
  - PCE headline/core (monthly release)
  - FOMC policy decision / SEP release
- `medium`
  - average hourly earnings, JOLTS, ISM PMI, retail sales, GDP advance estimate
- `low`
  - all other scheduled releases unless promoted by config

`surprise_score` defaults:

- If consensus is available:
  - `surprise_z = abs(actual - consensus) / max(forecast_error_std_rolling, series_min_sigma)`
  - `surprise_score = min(surprise_z / 2.5, 1.0) * 100`
- If consensus is missing:
  - fallback to first-window cross-asset reaction percentile (rates + dollar + equity index response)

`high surprise` threshold:

- `surprise_score >= 60` (roughly `surprise_z >= 1.5`)

Homepage promotion rule:

- force macro release into `top_events` when:
  - `importance_tier == high`, and
  - `surprise_score >= 60`

Non-suppression rule:

- a qualifying macro release cannot be dropped only because mover-based rails consumed slots.

## Runtime Config (No Hardcoding)

Add a versioned config profile (example: `config/attention_macro_signal_profile.v1.yaml`) containing:

- mapping from macro tags to eligible FRED series/signal groups
- normalization windows and caps
- staleness thresholds
- macro score weights for shadow/live scoring
- node/edge definitions for the causal graph
- regime predicates (for conditional edge activation)
- relationship-check tolerance thresholds
- hypothesis verification thresholds (`support_score`, contradiction cutoffs)
- release-promotion thresholds (`importance_tier`, surprise cutoffs, max forced macro events on homepage)

Scoring code must read this profile, not embed per-series magic numbers in code.

## Pipeline + Data Access Changes

### Pipeline

- Extend `equities-intraday-preload` attention stage:
  1. load latest macro datasets (`fred_summary`, `yield_curve_facts_1d`)
  2. build/persist `macro_signal_state_1d`
  3. build/persist `symbol_macro_exposure_1d`
  4. load causal graph config and materialize `macro_causal_graph_edges_v1`
  5. run propagation + consistency checker; persist `macro_relationship_checks_1d`
  6. build/persist `attention_macro_context_1d`
  7. detect + persist `macro_release_events_1d`
  8. generate + verify hypotheses; persist `attention_hypotheses_1d` (+ existing `attention_claims`)
  9. pass `attention_macro_context_1d` and release events into candidate/event generation and homepage assembly

### Data Access Layer

- Expose materialized reads for new datasets.
- Include macro provenance in `resolve_attention_feed` details payload:
  - macro dataset version ids
  - staleness summary
  - whether macro scoring was shadow/live
  - relationship-check summary (`holding`, `mixed`, `broken` counts)
  - hypothesis verification summary (`supported`, `conflicting`, `unresolved`)
  - release visibility summary (which macro releases were promoted/suppressed and why)

## Reliability and Complexity Assessment

- Reliability: **High** if staged rollout is followed (shadow first, then gated activation).
- Complexity: **Moderate**; main complexity is macro-tag-to-signal mapping quality, not infrastructure.
- Known risk:
  - bad mapping profile can bias ranking.
- Mitigation:
  - versioned mapping config + replay tests + shadow-vs-live diff checks.

## Test Plan

1. Unit tests
- macro signal derivation from FRED/yield inputs
- symbol exposure mapping from taxonomy tags
- causal graph traversal and regime-conditional edge activation
- relationship consistency checker (expected vs observed sign/strength)
- macro context join behavior for missing/stale data
- candidate scoring with and without macro context
- deterministic hypothesis builder + LLM contract parser

2. Integration tests
- `equities-intraday-preload` persists new datasets
- `resolve_attention_feed` returns macro provenance and stable ordering under default (non-macro) mode
- SerpApi/Tavily verification pipeline writes traceable claims/hypothesis statuses
- release-day scenario tests: high-surprise jobs/CPI release appears in homepage `top_events` even when no single-name move dominates

3. Regression checks
- existing attention scoring tests continue passing in default mode
- shadow score generation does not alter current `attention_score`
- cause-status labels remain constrained to allowed enum values

## Rollout Plan

1. Ship datasets + shadow score only.
2. Ship propagation + relationship checks, but do not affect ranking yet.
3. Ship LLM hypothesis + Serp/Tavily verification in shadow mode (display/log only).
4. Run 1-2 weeks of replay and daily shadow diff monitoring.
5. Enable macro-aware ranking in dev with conservative weights.
6. Promote to broader environments after acceptance metrics pass.

## Acceptance Criteria

- New datasets materialize successfully and are queryable.
- No ranking drift in default mode.
- Shadow scoring demonstrates improved ordering for macro-sensitive moves in replay cases.
- Stale/missing FRED input is explicitly surfaced and never silently treated as fresh.
- Relationship checks produce stable, explainable holding/broken signals for major macro events.
- Hypothesis outputs are evidence-backed and consistently classified (`supported` / `continuation` / `conflicting` / `unresolved`).
- On high-importance release days, at least one macro release event is present in homepage `top_events` with verification status and relationship-check context.
