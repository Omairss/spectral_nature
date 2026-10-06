# Production Promotion 2026-10-01

## Scope

Promote the already validated development UI, Broad Economy pipeline, and all-signal Trading Agent to production without rebuilding the shared dirty worktree. Preserve production authentication, schedules, dataset identities, and broker-action policy.

## Promoted Artifacts

- UI: `sn-streamlit-ui--0000085`
- UI image: `streamlit-ui@sha256:b22bdbb6bbd168f7e6ab0a6398f888a5e11b5f02e2d78df67a03d85d5c9b84fd`
- Broad Economy image: `pipeline-jobs:dev-namespace-20260921020436`
- Trading Agent image: `pipeline-jobs:dev-all-signal-v3-20260921051835`

Production retained its existing empty dataset namespace, database authentication, schedules, and production write identities. Development remains isolated under its own namespace and manual jobs.

## Live Verification

- `https://spectral-nature.com/` and `/_stcore/health` returned HTTP 200 after the UI promotion.
- `macro-fred-daily-79t3j8i` succeeded. It published `page_agentic_summaries__20261002T043246Z__aff7e7fe` with `status=ok`, a substantive long-rate summary, concrete watch items, and no public failure or disclaimer copy.
- `trading-agent-build-6sh4si9` succeeded. The all-signal ranker processed 480 rows across 16 datasets, completed research for 8 companies, and completed all 5 horizons.
- Trading Agent persisted 15 candidates across the five horizons. All are explicitly research-only, with `stance=no_call`; 8 are classified `avoid` and 7 `watch`. No research classification was converted into a directional performance claim.
- The materialized prose was reviewed candidate by candidate. It is generally specific and readable, including company-event evidence for LQDA, FICO, MRNA, MSTR, BMNR, and UTHR.

## Residuals

- One Investing.com FICO source timed out twice and a browser worker reported an interception error. The bounded research run continued with other evidence and completed normally.
- One one-year MSTR watch candidate included evidence but no risks or follow-up questions. It remains a non-directional research-only item. No canned fallback text was added; the durable fix is a semantic completeness critic and revision pass.
- The production deployment was verified through image identity, runtime configuration, health endpoints, job executions, persisted artifacts, and direct qualitative reads. An authenticated browser session was not available for a final visual pass.
