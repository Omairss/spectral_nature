# Attention Candidate Scoring Hot-Swap - 2026-07-03

## Goal

Upgrade Home attention candidate ranking from the legacy additive score to an anomaly-first detector while keeping the old scorer ready for immediate rollback.

## Boundary

The active source boundary is `services/attention_home_1d.py`:

- `legacy` mode keeps the existing additive score.
- `anomaly_v2` scores a broader liquid mover pool, then ranks by standardized own-history abnormality.
- Downstream AQL/Zopedia research, group synthesis, public review, and UI rendering keep the same materialized payload contract.

## Runtime Switch

`ATTENTION_CANDIDATE_SCORING_MODE` controls the live scorer:

- `legacy`: current production behavior.
- `anomaly_v2`: standardized return/volume/range anomaly score.

The deploy script also carries these v2 knobs:

- `ATTENTION_ANOMALY_CANDIDATE_POOL_MAX_COUNT`
- `ATTENTION_ANOMALY_MIN_HISTORY_DAYS`
- `ATTENTION_ANOMALY_LOOKBACK_DAYS`
- `ATTENTION_ANOMALY_SCORE_MULT`

## What Changed

- Candidate rows now include `scoring_mode`, `anomaly_score`, `return_z`, `volume_z`, `range_z`, and `anomaly_history_count`.
- In `anomaly_v2`, the input pool is a broader liquid universe instead of the legacy fixed top-gainer/top-loser buckets.
- The final candidate frame remains bounded by `ATTENTION_SHORTLIST_DEFAULT_MAX_COUNT`.
- `coverage_summary` records the candidate scoring mode and anomaly-scored row count.

## Notebook Validation

`notebooks/attention_mechanism_lab.ipynb` now includes `Candidate Scoring Hot-Swap Replay`.

Workflow:

1. Load latest `daily_movers`, `macro_anchor_daily_movers`, `price_history`, and `attention_feed`.
2. Replay candidate generation in `legacy`.
3. Replay candidate generation in `anomaly_v2`.
4. Compare top names, rank deltas, anomaly components, and optional focus tickers.

Latest local replay on the 2026-07-02 materialized snapshot showed the expected behavior: v2 promoted names with rarer own-history moves while still retaining several large semiconductor declines.

## Rollback

No code rollback is required to hot-swap back. Set:

```bash
ATTENTION_CANDIDATE_SCORING_MODE=legacy
```

Then update/restart the scheduled attention job. The legacy scoring function remains in code as `_legacy_candidate_score`.
