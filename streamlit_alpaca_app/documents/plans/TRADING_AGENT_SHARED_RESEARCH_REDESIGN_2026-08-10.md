# Trading Agent Shared Research Redesign

**October 16 retirement:** the inactive per-horizon rollback entrypoints, mode and timeout branches have been removed. Only the accepted shared-research producer is supported. Earlier rollback instructions below are historical evidence and must not be implemented.

Date: 2026-08-10

## Decision

Replace repeated horizon-by-horizon research with one bounded Zopedia/AQL
research pass over a tunable pool of unique companies. Reuse those completed
research packages for the 1-week, 1-month, 3-month, 1-year, and 5-year decision
syntheses.

The default generation mode is `shared_research_v2`. The previous
per-horizon algorithm remains available as `legacy_per_horizon_v1` so it can
be restored through configuration without a code rollback.

## Why The Current Design Fails

The production job independently researches up to four companies for each of
five horizons. A horizon-level 300-second thread timeout returns control but
does not stop the research thread. The next horizon then starts while the old
work remains active. This multiplies provider calls and database connections,
then persists price-derived fallback candidates as though the job succeeded.

## New Flow

1. Build ranked scout lists for every horizon from the materialized Market
   Opportunity feed.
2. Select a balanced, deduplicated research pool by walking the horizon lists
   round-robin until the global research budget is filled.
3. Run one Zopedia/AQL research package per unique company.
4. Allow Zopedia to reuse current memory and apply useful updates through its
   existing safe write policy. Research-package audit rows stay separate from
   chat finding rows.
5. Materialize the completed/failed company packages for auditability.
6. For each horizon, synthesize decisions only from packages belonging to
   companies scouted for that horizon.
7. Fail closed when no grounded package is available. Do not manufacture
   candidates from price rows.

## Tunable Recall And Scale

Recall is controlled by independent settings rather than by duplicating the
whole research pipeline:

- `TRADING_AGENT_SCOUT_LIMIT_PER_HORIZON`: breadth considered for each
  horizon before research. Default: 6.
- `TRADING_AGENT_RESEARCH_POOL_LIMIT`: maximum unique companies researched in
  one run. Default: 8.
- `TRADING_AGENT_RESEARCH_PARALLELISM`: concurrent company packages. Default:
  2 to protect shared providers and Postgres.
- `TRADING_AGENT_RESEARCH_MAX_TOOL_CALLS`: maximum AQL tool calls per company.
  Default: 4.
- `TRADING_AGENT_RESEARCH_ATTEMPTS`: package attempts. Default: 1; retrying the
  whole research graph is not the default recovery strategy.
- Tool, prefetch, and model-step timeouts are passed into the shared AQL
  engine, where the underlying operation is bounded.

Increasing scout breadth raises possible recall without provider cost until a
name enters the global pool. Increasing the pool raises company coverage.
Increasing tool depth raises evidence coverage per company. These dimensions
can be tuned and measured separately.

## Failure Contract

- A failed company package is retained as failed package state with its exact
  limitation.
- A horizon with some completed packages may still synthesize from those
  packages.
- A horizon with no completed package produces an `error` run and zero
  candidates.
- A run with zero successful horizon syntheses fails the pipeline execution
  after diagnostic datasets are persisted.
- Timeout-generated fallback candidates are confined to the inactive legacy
  implementation and are never used by `shared_research_v2`.

## Materialized Products

- `trading_agent_research_packages`: one row per researched company and run,
  including package status, confidence, evidence-pack ID, limitations, tool
  trace summary, horizon coverage, and the market contexts that selected it.
- `trading_agent_runs`: one row per horizon synthesis.
- `trading_agent_candidates`: grounded decisions emitted by those syntheses.
- Existing outcomes and qualitative reviews continue to consume candidates.

## Verification

Required checks:

1. Prove a company present in multiple horizons is researched once.
2. Prove each horizon receives only its relevant completed packages.
3. Prove research-pool limits change coverage without changing the output
   candidate limit.
4. Prove failed research yields no fabricated candidate in V2.
5. Run the service against a representative materialized feed and inspect the
   package and candidate quality, not only schema validity.

## Production Result

Deployed 2026-08-10 to the isolated production `trading-agent-build` job.
Verification execution `trading-agent-build-ob5zvqr` succeeded in 25m33s:

- eight unique companies selected from balanced horizon scout lists
- seven completed company packages and one retained failure
- five successful horizon syntheses
- 13 grounded candidates
- zero timeout or price-only fallback candidates
- maximum company research calls reduced from 30 to eight, a 73 percent
  reduction

The failed LUNR package reported `DeepSeek returned empty content`; it did not
block other companies or horizons. Failed packages remain materialized for
diagnostics but are excluded from candidate eligibility. A synthesis replay
over persisted packages produced concise watch/avoid classifications without
imperative trade instructions or trading-floor shorthand. The active image is
pinned by digest to
`sha256:3e6d744b4eda950e4a6e947a3840b433a84a4245dd45e7bed01f193642fdd7a0`.

## Candidate Retrieval Upgrade

Updated 2026-09-21. The original redesign fixed duplicated research and
timeouts but inherited the Market Opportunity weighted percentile score. It
also stopped the horizon round-robin as soon as the eighth symbol was reached,
which allowed earlier horizon order to decide a cutoff inside an equal-rank
tier.

The development path now uses three bounded ranking stages:

1. Market Opportunity keeps the full All Market corpus and uses
   reciprocal-rank fusion over selected-horizon move, momentum change, current
   daily move, and trend quality. The four component ranks, signal count, and
   `reciprocal_rank_fusion_v1` method are materialized with the existing
   `opportunity_score` compatibility field.
2. Each Trading Agent horizon retrieves its top `scout_limit * 4` companies by
   primary horizon rank, then reranks that bounded set by the fused score. This
   prevents a daily-only dislocation from entering a long-horizon scout list.
3. The shared company pool processes a complete rank tier across all horizons
   before applying its limit. When a tier does not fit, cross-horizon RRF and
   the fused opportunity score choose among the tied tier; fixed horizon order
   no longer owns the cutoff. Selected packages persist a compact
   `horizon_tier_rrf_v1` trace.

This is an auditable IR baseline, not a trained return predictor. It removes
scale-sensitive feature addition and ordering bias without inventing labels we
do not have. Learning-to-rank weights or evidence-quality reranking remain
blocked on strict historical replay and forward outcome labels.

A replay over the current development inputs ranked 999 momentum profiles from
the 1,000-symbol universe. The final All Market corpus contains 5,994 rows
across six horizon variants rather than truncating each variant to 80 before
the Trading Agent retrieval stage.

Development execution `trading-agent-build-dev-j6jgxgt` succeeded on
2026-09-21 using image `pipeline-jobs:dev-ranking-v2-20260921032137`, digest
`sha256:3e70286d68231eb40e5dd7ea2cbc414a46d503842cf590218093946f27bda2a6`.
Run `194a70a0-cfaf-47c2-ba6b-bc5b72df0c0c` completed all eight packages and all
five horizons with this pool: TWST, FLNC, FIX, VICR, MSTR, SBET, SNDK, and
STRL. It produced 12 candidates: one supported one-week FLNC short and 11
research-only rows. The exact UI adapters accepted all five runs and all 12
candidates under `plain_investor_research_v2`; no action row was created.

The focused Market Opportunity, Trading Agent, pipeline, store, and view suite
passed with 111 tests. This validates retrieval, research, persistence, and
presentation behavior. It does not establish predictive improvement; that
claim remains gated by strict historical replay and forward outcomes.
