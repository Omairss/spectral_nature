# Trading Agent Backtest Assessment

Date: 2026-07-03

## Short Answer

We have not built a real Trading Agent backtest yet.

User decision on 2026-07-03: only a true research-agent backtest is in scope.
Materialized-context replay, recorded-call audit, and paper-trial metrics can
exist as diagnostics, but they are not acceptable substitutes for the backtest.

A true backtest must reconstruct what the agent could have known at each
historical decision time, run or replay the research process from that state,
preserve the evidence and model-call trace, then score only future outcomes.

## What "Backtest" Means Here

There are four different levels. Only level 3 is the target.

1. **Outcome audit**
   - Score candidates the agent already generated.
   - Current state: implemented through `trading_agent_outcomes` and the
     performance notebook.
   - This answers: "How did recorded calls do?"
   - This does not answer: "Would the agent have made good calls historically?"

2. **Materialized-context replay**
   - For each historical simulation time, load historical
     `market_opportunity_feed` and `page_agentic_summaries`, run the current
     Trading Agent over those frozen frames, and score forward returns.
   - This can be a coverage/debug probe.
   - It is not a full research replay if AQL/tools read live web or current
     memory.

3. **Evidence-native historical replay**
   - Every tool, Zopedia page, retained source, article, filing, and evidence
     chunk is filtered by `published_at`, `asof_time_utc`, and preferably
     `ingested_at_utc`.
   - AQL research runs as if it were the historical date.
   - This is the only acceptable target for this project.

4. **Forward paper trial**
   - Run the agent live on schedule, without execution, and score outcomes as
     they mature.
   - This is still required because LLM behavior, provider behavior, source
     availability, and market regimes cannot be fully reconstructed.
   - This complements the true backtest but does not replace it.

## Current Assets

Already useful:

- `load_dataset_frame_asof(dataset_name, target_date)` exists for historical
  dataset lookup.
- Pipeline artifacts have `dataset_version_id`, `asof_time_utc`,
  `ingested_at_utc`, `universe_version`, `blob_path`, row count, schema columns,
  parameter hash, code version, and schema version in Postgres when DB tracking
  is available.
- Trading Agent generation is separated into service functions:
  `build_trading_agent_context`, `build_trading_agent_materialized_frames`,
  and `build_trading_agent_suggestions`.
- `build_trading_agent_outcomes` can score candidates against price history.
- Model calls now go through `generate_json_via_aql_zopedia_gateway` for
  telemetry and model-routing metadata.

Important gaps:

- `backtest_runs` and `backtest_run_inputs` are described in docs/notebooks but
  are not bootstrapped as production tables.
- `load_dataset_frame_asof` only accepts a date-like target and chooses by
  `asof_time_utc <= end of day`; it does not yet enforce strict
  `ingested_at_utc <= simulation_time`.
- The current scheduled Trading Agent job reads latest datasets. It is not a
  reusable replay runner.
- Gateway telemetry stores prompt/schema hashes and metadata, not the full
  replay package or model response body.
- AQL/tool retrieval is not guaranteed historical. Any live web/search/current
  memory read during replay can introduce lookahead.
- Existing historical materialized datasets may be sparse. If the dataset
  version history only begins recently, the realistic backtest window is short
  unless we build backfills.

## Core Design Decisions

### 1. Are We Testing Research Quality Or Trade Execution?

The current Trading Agent is a watchlist/research experiment, not an execution
strategy.

For a research backtest:

- `long` and `watch` can be scored as positive-direction calls.
- `short` and `avoid` can be scored as negative-direction calls.
- Metrics are directional hit rate, signed return, benchmark-relative return,
  thesis quality, and evidence verdict.

For a portfolio backtest:

- We must define position rules, weights, rebalance cadence, holding period,
  treatment of `watch`, treatment of `avoid`, short availability, transaction
  costs, slippage, duplicate ticker exposure, and capital constraints.
- Without that policy, portfolio-level Sharpe/return/drawdown numbers are
  misleading.

Recommendation: start with research-call backtesting. Add portfolio simulation
only after the research calls show stable value.

### 2. Do We Re-run The LLM Or Replay Recorded Outputs?

For the true backtest, recorded outputs and materialized-only replays are not
valid final modes.

Required mode:

- Run the research agent against historical evidence stores that enforce
  source time and ingestion time.
- Preserve the complete replay package: input dataset versions, tool evidence
  IDs, prompt hashes, model/provider config, model output payloads, and outcome
  labels.
- Keep recorded outputs only as comparators when they exist.
- Keep materialized-context replay only as a coverage/debug probe while
  building the historical evidence layer.

The run type for the actual backtest should be `evidence_native_replay`.

### 3. How Strict Should Lookahead Be?

Minimum rule:

- All feature/source datasets must have `asof_time_utc <= simulation_time`.

Better rule:

- Also require `ingested_at_utc <= simulation_time`.

Strict source rule:

- Any article/filing/evidence row must have source publish/filing time
  `<= simulation_time`, and if an ingestion timestamp exists, ingestion must
  also be `<= simulation_time`.

Recommendation: implement strict mode first and allow a clearly labeled
`asof_only` diagnostic mode only for coverage analysis. Final backtest metrics
must come from strict mode.

## Required Build

### Phase A: Historical Evidence Contract

Goal: make AQL/Zopedia/tool reads historical before any agent replay is trusted.

Required contracts:

- Every searchable source document has source time fields:
  `published_at`, `filed_at`, `observed_at`, or an explicit unknown timestamp.
- Every retained source/evidence row has `ingested_at_utc`, `dataset_version_id`,
  `source_document_id`, and source provenance.
- Zopedia memory reads are bitemporal:
  - content valid/evidence time
  - system write time
  - only pages/mutations visible by `simulation_time` are readable.
- Web/search tools are disabled in historical replay unless they read an
  archived retained-source store filtered by `simulation_time`.
- AQL tools accept `simulation_time_utc` and must pass it through every source,
  memory, and dataset lookup.
- Tool outputs carry evidence IDs, source timestamps, and ingestion timestamps.

Acceptance gate:

- A test replay for a past date proves no AQL tool can return a source,
  evidence chunk, or memory page whose source time or ingestion time is after
  the simulation timestamp.

### Phase B: Coverage Audit

Goal: learn what historical evidence and market data exist after the historical
read contract is enforced.

Deliverables:

- `trading_agent_backtest_coverage` dataset or notebook section.
- For each candidate simulation date:
  - available `market_opportunity_feed` version
  - available `page_agentic_summaries` version
  - available retained source/evidence versions
  - available Zopedia memory snapshot or bitemporal read coverage
  - available `price_history` version for features
  - future `price_history` coverage for labels
  - row counts and dataset version IDs
  - whether strict source-time plus `ingested_at_utc` mode is possible

Acceptance gate:

- We can name the honest backtest window and sample count before any model
  calls run.

### Phase C: Backtest Metadata Contract

Goal: make backtest runs auditable and replayable.

Add/implement:

- `backtest_runs`
- `backtest_run_inputs`
- `trading_agent_backtest_runs`
- `trading_agent_backtest_candidates`
- `trading_agent_backtest_outcomes`
- `trading_agent_backtest_summary`

Each simulated decision time should store:

- simulation timestamp
- replay mode, always `evidence_native_replay` for official runs
- strategy/research policy version
- code version
- model/provider config
- all input dataset version IDs
- all source/evidence pack IDs
- all model call IDs and output payload IDs
- all output dataset version IDs
- status and failure reason

Acceptance gate:

- Given a `backtest_run_id`, we can list every dataset version used for every
  simulated decision.

### Phase D: As-Of Dataset Resolver

Goal: replace "latest" reads with explicit simulation-time reads.

Build a resolver that supports:

- full timestamp, not only date
- `asof_time_utc <= simulation_time`
- optional `ingested_at_utc <= simulation_time`
- required dataset set
- missing/stale policy
- returned metadata map
- no fallback to latest unless the run is explicitly marked diagnostic

Acceptance gate:

- A no-lookahead test proves every feature input is at or before the simulation
  time.

### Phase E: Historical AQL Replay Harness

Goal: run the real research process with historical tools only.

Build:

- `HistoricalAQLContext` carrying `simulation_time_utc`, strictness mode,
  allowed dataset versions, and source/evidence visibility rules.
- Historical adapters for:
  - Zopedia page search/read
  - retained source search/read
  - retained evidence chunk search/read
  - filings/news/article lookup
  - market data lookup
- A hard failure path for any tool that cannot prove historical visibility.
- Replay evidence packs that persist the exact sources used by each simulated
  candidate.

Acceptance gate:

- The replay harness refuses live web/current-memory reads and produces an
  evidence pack whose rows all pass the no-lookahead validator.

### Phase F: Trading Agent Replay Runner

Goal: run the existing Trading Agent service without touching live scheduled
datasets.

Refactor or add a runner that accepts:

- simulation timestamp
- historical input frames
- replay mode: `evidence_native_replay`
- LLM client or replay model
- historical AQL adapter
- output sink

It should return separate backtest frames, not write into live
`trading_agent_runs` or `trading_agent_candidates`.

Acceptance gate:

- A fake-LLM/historical-AQL unit replay over two dates produces deterministic
  candidates and outcomes without reading latest datasets or live tools.

### Phase G: Model Call Replay Ledger

Goal: make LLM-mediated decisions auditable and replayable.

Gateway telemetry is not enough because it intentionally omits prompt and
response bodies. The true backtest needs a separate replay artifact with a
redaction policy.

Persist:

- model call ID
- simulation timestamp
- surface and purpose
- provider/model/deployment/temperature/reasoning config
- prompt hash and schema hash
- non-secret prompt package or a blob reference to it
- response payload or a blob reference to it
- evidence pack IDs referenced by the prompt
- failure status and error details

Acceptance gate:

- Given a backtest candidate, we can inspect the exact evidence package and
  model output that produced it.

### Phase H: Label And Outcome Engine

Goal: score forward returns without leaking labels into features.

Extend current `build_trading_agent_outcomes` for backtests:

- benchmark returns, at least SPY
- optional sector ETF benchmark
- excess signed return
- duplicate ticker exposure grouping
- horizon-specific maturity rules
- not-yet-mature rows excluded from realized metrics
- configurable interpretation of `watch` and `avoid`

Acceptance gate:

- Matured, mark-to-market, no-future-bar, missing-price, and missing-entry rows
  are reproducible and separated in summary metrics.

### Phase I: Reporting Surface

Goal: make results hard to misread.

Report:

- candidate count
- scored count
- matured count
- coverage gaps
- matured win rate
- mark-to-market win rate
- average/median signed return
- benchmark-relative return
- by horizon
- by direction
- by confidence
- duplicate exposure
- failure/rejection rate
- provider/model failure rate
- qualitative review buckets

Do not optimize only for raw win rate.

Acceptance gate:

- Notebook/admin output always separates realized matured performance from
  mark-to-market reads.

## Complexity Estimate

### True Research-Agent Backtest: 4-6+ weeks

Scope:

- Historical AQL/tool contracts.
- Retained source/document/evidence lookup by as-of time.
- Historical Zopedia memory snapshots or bitemporal memory reads.
- No live web in replay.
- Full evidence trace and model-call replay ledger.
- Larger simulation windows and cost controls.
- Strict source-time and ingestion-time no-lookahead validation.
- Separate persisted backtest datasets and run metadata.
- Benchmark/sector-relative outcome scoring.
- Admin/notebook reporting.
- Failure accounting for provider, evidence coverage, tool coverage, and
  maturity.

Useful for:

- Claiming the agent's research process was backtested, not only its current
  prompt over old summary tables.

Limit:

- Expensive and still needs forward paper validation.
- Historical coverage may still force a short initial window unless retained
  source and memory backfills exist.

## Risks

- **Historical coverage may be too short.** If dataset version history starts
  recently, the backtest window will be small unless we backfill.
- **Live web is not historical.** Re-running search today for a past date is a
  lookahead path.
- **LLM non-determinism matters.** Even with stable prompts, model/provider
  behavior changes. Store outputs and model config.
- **Watchlist semantics are not portfolio semantics.** Directional call quality
  and executable strategy performance are different products.
- **Long horizons mature slowly.** 1y and 5y calls cannot support realized
  performance quickly.
- **Provider/account failures bias samples.** Failed model calls must count in
  coverage and reliability metrics, not disappear.
- **Multiple testing risk.** Do not tune filters or prompts until sample size
  is large enough and changes are tested out-of-sample.

## Recommendation

Build in this order:

1. Historical evidence contract for AQL/Zopedia/tools.
2. Strict as-of resolver with source-time and ingestion-time enforcement.
3. Coverage audit over that strict resolver.
4. Backtest metadata tables and replay artifact storage.
5. Historical AQL replay harness.
6. Trading Agent `evidence_native_replay` runner.
7. Model-call replay ledger with prompt package and response payload refs.
8. Outcome scoring with SPY/sector benchmark and strict matured vs
   mark-to-market split.
9. Notebook/admin report that exposes coverage, failures, evidence trace, and
   performance.
10. Continue forward paper scoring through `trading_agent_outcomes` in
    parallel.

Do not build a materialized-context replay as the product. It can be a temporary
coverage probe only. Do not start large historical LLM runs until the historical
tool contract and no-lookahead validator pass.
