# Platform Signal Index

**Date:** 2026-09-21  
**Status:** Indexed and integrated into the development Trading Agent ranker  
**Canonical contract:** `services/signal_catalog.py`

## Purpose

The Trading Agent should eventually rank stocks using the platform's useful evidence, not only the four fields in the current reciprocal-rank-fusion scanner. Before changing the ranker, the platform needs one semantic index that answers:

- What is the signal?
- Where does it come from?
- At what entity and time grain does it exist?
- Does it cover the full stock universe or only selected/event-triggered names?
- Is it a direct ranking input, market context, quality gate, existing composite, source input, or outcome label?
- Can it be reconstructed without using future information?
- Is it actually present in the current development snapshot?

The code-owned index expands every registered field into a stable `signal_id`. FRED and Treasury series are expanded by series identifier, and quarterly fundamentals are expanded by metric. This avoids a giant hand-maintained document while keeping the complete inventory queryable as a DataFrame.

## Scope

A signal is a structured market, business, macro, event, evidence-quality, or portfolio field that can affect retrieval, reranking, research priority, decision confidence, or evaluation.

The index intentionally excludes identifiers, timestamps, URLs, free-form narratives, source text, display labels, and audit JSON. Those are provenance or research evidence, not numeric/categorical ranking features. Raw text still belongs in AQL/Zopedia research; it should not be forced into a cross-sectional scoring table.

## Initial Audit

This audit preceded the 2026-09-21 development source refresh. It loaded the
newest local development frame where one existed, falling back to the newest
cached platform frame. The counts describe signal variants, not independent
evidence sources: for example, 35 FRED series multiplied by 17 available
summary or dense transforms produce 595 macro variants. The tables below are
retained as the gap inventory that drove the refresh.

| State | Signal variants | Meaning |
|---|---:|---|
| Available in the inspected snapshot | 246 | Field exists and has at least one value. |
| Derived on demand | 471 | Computation exists, but the result is not a durable broad-universe feature table. |
| Defined but not observed | 164 | Code contract exists, but no current cached development frame was observed. |
| Frame present but field/value missing | 14 | Current artifact is empty, immature, or predates the declared schema. |
| **Total** | **895** | 31 families across 39 source datasets. |

### Broad coverage available now

| Family | Source | Available variants | Coverage and use |
|---|---|---:|---|
| Price and liquidity | `daily_movers`, `universe_snapshot` | 6 | Roughly 999 liquid stocks; direct retrieval inputs. |
| Momentum and trend quality | `momentum_profiles` | 18 | 999 stocks across 1D through 5Y returns, slopes, acceleration, and trend fit. |
| Current opportunity retrieval | `market_opportunity_feed` | 7 | Existing score, component ranks, and horizon-availability gate; composites, not new evidence. |
| Taxonomy and exposures | `entity_taxonomy_labels` | 11 | 6,613 listings; peer and macro/business conditioning fields. |
| Price bars | `price_history` | 5 | 1,457,417 point-in-time OHLCV rows; source series for feature derivation. |
| FRED macro summaries | `fred_summary` | 140 | 35 global series with latest, previous-change, and year-over-year fields. |
| Treasury curve | `yield_curve_summary` | 30 | Seven tenors and three curve spreads with current, 1D, and 5D changes. |
| Current event candidates | `attention_candidates_1d` | 15 of 20 | 69 event-triggered stocks; not broad-universe coverage. |
| Research quality | `trading_agent_research_packages` | 2 | Only the selected research pool; valid as a decision gate, not initial retrieval. |

### Capabilities missing before the development refresh

| Source | Variants | Consequence |
|---|---:|---|
| `anomaly_events` | 16 | Peer-relative residual, persistence, attention, macro, and portfolio signals are not currently rankable. |
| `price_expectations` | 15 | Trend-, peer-, and benchmark-relative expected returns and residuals are absent. |
| `option_contract_snapshots` | 14 materialized fields | No current option surface; even when present it covers only a small liquid-symbol subset and still needs ticker-level aggregation. |
| `commodity_regime_summary` | 13 | Commodity sensitivity and transmission are unavailable for the current run. |
| `quarterly_fundamentals` | 11 metrics | Reported levels are absent and still require lag-safe growth/ratio features before cross-sectional use. |
| Technical latest/history | 11 | RSI, volatility, drawdown, and channel state are not currently materialized in development. |
| `correlation_phase_shift_summary` | 10 | Benchmark decoupling and correlation-break fields are unavailable. |
| `positions_snapshot` | 8 | Funded-account overlays are absent from the inspected snapshot. |
| Macro relationship/context/hypothesis/prior tables | 20 | Macro-to-stock transmission is defined but not currently materialized. |
| `macro_anchor_daily_movers` | 2 | Current cross-asset anchor move/activity context is absent. |
| Commodity expectation/attention rollups | 30 | Commodity-proxy residuals and breadth are defined but absent. |
| Attention claim and breadth tables | 13 | Claim-quality and market-breadth fields are defined but absent. |
| `attention_context_llm` | 1 | Filing-context synthesis confidence is defined but absent. |

Five anomaly-v2 fields were declared in `attention_candidates_1d` but absent
from that inspected artifact: `anomaly_score`, `return_z`, `volume_z`,
`range_z`, and `anomaly_history_count`. Outcome fields remain evaluation-only
regardless of availability.

## Development Ranking Snapshot

The refreshed development job now loads 15 non-empty ranking datasets. Broad
stock coverage includes 999 daily movers, 1,000 universe and technical rows,
998 momentum rows, 993 correlation rows, 4,979 price-expectation rows, 217
anomaly events, 30,000 option contracts, 176,390 quarterly fundamental rows,
and 6,613 taxonomy rows. Current-event coverage remains sparse by design.

The rank trace proves which sources actually affected each stock. Across all
5,994 stock-horizon rows, 12 datasets contributed either numeric evidence or
peer normalization. The current stock ranks use five evidence domains: price,
relative dislocation, events, fundamentals, and options. Two registered inputs
are empty: `positions_snapshot` and `attention_macro_context_1d`. The three
commodity datasets contain commodity ETF/proxy rows rather than the stock
symbols in the ranking universe; they need an explicit exposure relation before
their values can validly alter a stock rank.

## Ranking Boundaries

The catalog prevents several invalid interpretations of "use every signal":

1. **Do not double count composites.** `opportunity_score`, attention scores, momentum composites, and their component fields carry overlapping information. Existing composites are indexed for provenance but cannot be naively fused beside every constituent.
2. **Do not leak outcomes.** Realized returns, benchmark-relative outcomes, maturity, and win labels are marked `post_decision_prohibited`. They may train or evaluate an as-of model, never score the same decision that produced them.
3. **Do not treat global macro as stock-specific alpha.** FRED and Treasury series need a declared exposure/peer transmission map. Repeating the same macro value for every ticker adds no cross-sectional rank information.
4. **Do not compare raw levels across company sizes.** Revenue, assets, cash flow, share count, price, and option contract fields need lag-safe ratios, growth, surprise, liquidity, or distribution transforms first.
5. **Do not confuse sparse research with broad coverage.** Options, current-event evidence, Zopedia research, and funded-account positions apply to subsets. Missing values must remain missing; zero is a real observation.
6. **Do not claim point-in-time backtests from current snapshots.** FRED uses release dates, fundamentals need reporting availability lags, taxonomy can change, and current summary tables need historical reconstruction before they can enter a true backtest.

## Replaced Trading Agent Gap

The previous development Trading Agent retrieved with four rank lists:

- absolute selected-horizon return;
- absolute momentum change;
- absolute daily move;
- inverse trend-fit gap.

That narrow retrieval path has been replaced in development by the all-signal
ranker below. It remains available in trace fields for comparison and rollback.

## Ranking Integration

The first all-signal ranking path is implemented in
`services/all_signal_ranker.py` and documented in
`documents/architecture/market_data/TRADING_AGENT_ALL_SIGNAL_RANKER_2026-09-21.md`.
It consumes every usable pre-decision stock-linked numeric source, balances
signals inside families and correlated families inside evidence domains, treats
missing sparse coverage as neutral, and saves the ranking trace as
`trading_agent_ranked_opportunities`.

## Next Validation Stage

1. Reconstruct the same feature contract point in time, keyed by
   `asof_time_utc`, symbol, and horizon, without using later revisions.
2. Build explicit stock exposure relations before commodity and global macro
   data receive a cross-sectional vote.
3. Run source, family, and domain ablations to measure incremental information
   rather than assuming every field helps.
4. Evaluate walk-forward information coefficient, hit rate, excess return,
   turnover, drawdown, coverage, and stability before selecting a learned model.

The ranker is still an auditable retrieval method, not a trained alpha model.
Its next validation stage is the point-in-time walk-forward evaluation described
above.
