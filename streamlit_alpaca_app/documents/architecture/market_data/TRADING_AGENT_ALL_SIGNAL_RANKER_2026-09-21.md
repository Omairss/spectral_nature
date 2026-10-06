# Trading Agent All-Signal Ranker

**Date:** 2026-09-21  
**Status:** Implemented and verified in development  
**Code:** `services/all_signal_ranker.py`

## Goal

Rank the full stock universe with every usable, pre-decision, stock-linked
signal available to the Trading Agent. The rank chooses which companies receive
expensive Zopedia/AQL research; it does not make the final long, short, or
no-call decision.

## Inputs

The job loads current stock data from:

- daily price and liquidity;
- multi-horizon momentum and trend quality;
- technical indicators;
- benchmark and peer-relative expectations;
- correlation and market-relationship shifts;
- anomaly and current-event measurements;
- stock-linked macro and commodity transmission;
- quarterly fundamental growth;
- ticker-level option-surface aggregates;
- funded-account exposure and current position performance;
- taxonomy and company size for peer normalization.

The canonical field definitions and roles remain in `services/signal_catalog.py`.
New numeric fields in an eligible catalog dataset enter the appropriate signal
family without adding another hand-tuned stock score.

## Ranking Method

1. Select the row matching the requested horizon when a source is
   horizon-specific.
2. Convert raw option contracts and quarterly statement history into comparable
   ticker-level aggregates. Raw company-size accounting levels are not compared
   directly.
3. Normalize each numeric signal as a cross-sectional percentile inside the
   ticker's industry and size peer group when that group has enough members;
   otherwise use the full universe.
4. Average signals inside their family. A family with 20 correlated columns
   therefore does not receive 20 times the weight of a family with one column.
5. Average correlated families into independent evidence domains: price
   behavior, relative dislocation, event evidence, commodity exposure,
   fundamentals, options, macro exposure, and portfolio exposure.
6. Fuse domain percentiles with reciprocal-rank fusion. Each domain has the
   same maximum contribution, so one price move repeated across several source
   tables is not counted several times.
7. Treat a missing sparse family as neutral. Options coverage, event research,
   and portfolio membership do not improve rank merely because a row exists.

Every ranked row records the final rank and score, observed domain and family
counts, signal count, dataset count, coverage ratio, domain and family scores,
and source datasets.
The previous four-field scanner score is retained as `scanner_opportunity_score`
for comparison.

## Boundaries

- Realized returns, win labels, and research reviews are evaluation data and
  never enter the decision that produced them.
- Existing composite scores are not fused beside all of their components.
- Raw prose, URLs, IDs, timestamps, and source text remain research evidence,
  not numeric ranking features.
- Global FRED and Treasury values do not vary by ticker. They remain market
  context until `attention_macro_context_1d` or another declared exposure model
  links them to a stock.
- The rank is an auditable retrieval method, not evidence of predictive edge.
  Predictive quality still requires a point-in-time walk-forward backtest.

## Runtime

`pipeline/jobs/main.py` reranks `market_opportunity_feed` before any company
research starts and saves the result as `trading_agent_ranked_opportunities`.
`services/trading_agent.py` uses `all_signal_rank` for the research shortlist;
the old four-field horizon rank remains available for audit and rollback.

## Development Verification

Development execution `trading-agent-build-dev-ue1wrmg` succeeded on
2026-09-21 with internal run ID `0ce40dea-5b75-4460-a654-69df89f13773`.
It used image `pipeline-jobs:dev-all-signal-v3-20260921051835`, digest
`sha256:4d32fd0ea536235309151e1f76f2a09ae35040944d38a9e9681c910b2c2aede5`.
Production was not changed.

The saved rank table contains 5,994 rows: 999 stocks for each of six market
horizons. All rows use `domain_balanced_all_signal_v1`. The current snapshot
reaches as many as 112 numeric signals, 11 families, five evidence domains,
and 12 row-level source datasets. The job loaded 15 non-empty ranking sources;
taxonomy and company size also shape peer comparisons.

Five domains can currently affect stock rank: price behavior, relative
dislocation, event evidence, fundamentals, and options. Funded positions and
stock-linked macro context are empty. Commodity proxy datasets are current,
but their ETF symbols do not overlap the 999-stock corpus and there is no
declared stock-to-proxy exposure table, so they correctly cast no stock vote.
This is a visible data-model gap, not a reason to assign every commodity move
to every company.

The same execution completed 8/8 company research packages and 5/5 Trading
Agent horizons, then saved 18 candidates. Each run recorded
`candidate_retrieval_method=all_signal_rank`. This verifies runtime and data
flow only; it does not establish predictive performance.
