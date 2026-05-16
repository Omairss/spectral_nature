# Trading Agent Job And Admin Controls Plan - 2026-05-05

## Context

This plan covers the follow-up TODOs added on 2026-05-05:

- Inventory and move UI job trigger buttons to Admin > Pipeline Jobs.
- Move Trading Agent generation into a scheduled/on-demand pipeline job across 1 week, 1 month, 3 month, 1 year, and 5 year horizons.
- Persist trading-agent outputs and log admin Place / Reject decisions.

## Relevant Guardrails

- `mistakes.md` #37: main features do not run on page load.
- `mistakes.md` #39: fixed market opportunity feeds are job outputs.
- `mistakes.md` #40: multi-surface feature slices need one architecture pass.
- `mistakes.md` #41 and `learnings.md` #56: materialized AQL work needs bounded steps.
- `mistakes.md` #54 and `learnings.md` #71: runtime refresh triggers use runtime credentials, not Azure CLI on the request path.
- `learnings.md` #3: important product state needs a dataset or event.
- `learnings.md` #51: render paths stay cheap.

## Ranked Work

1. Low effort: hide/remove feature-page job trigger buttons and keep job controls in Admin > Pipeline Jobs.
2. Medium effort: register Trading Agent as a pipeline source/job with persisted datasets and deployment schedule.
3. Medium-high effort: build a reusable trading-agent materialization service that runs all required horizons from existing materialized Market Opportunity and page-summary datasets.
4. Medium effort: change Trading Agent UI to read the latest persisted run/candidates instead of running AQL on click.
5. Medium effort: add durable Place / Reject audit logging. Place is a logged admin decision, not broker order submission.
6. Medium effort: add focused tests for materialization, job registration, action logging, and UI trigger inventory.

## Current Decision

Do not submit broker orders from this change. The existing app labeled the page as a research experiment with no orders sent, and this request only specifies logging trades. The UI button can say `Place`, but the action records a `place_requested` audit event for review.

## Completion Notes

- Removed feature-page refresh/job trigger buttons. Admin > Pipeline Jobs is the remaining UI trigger surface.
- Registered `trading-agent-build` as a pipeline job/source and deployment schedule.
- Added `trading_agent_runs` and `trading_agent_candidates` materialized datasets.
- Trading Agent now reads the latest materialized job output and no longer runs AQL from the page.
- Added Place / Reject audit logging through `trading_agent_actions`. Place is `execution_mode=log_only`, `broker=alpaca`, and `broker_order_id=NULL` for the future Alpaca handoff.
- Added tests for trigger inventory, materialized horizon generation, job mapping/job execution, and log-only action persistence.
- Added timeout fallback for required Trading Agent horizons. If AQL times out for one horizon, the job persists conservative low-confidence candidates from the existing materialized opportunity feed and records the AQL gap instead of leaving that horizon empty.

## Dev Verification

- Deployed UI dev revision `sn-streamlit-ui-dev--0000295`; root smoke check returned HTTP 200.
- Deployed pipeline image `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260505003337`.
- Verified Azure Container Apps job `trading-agent-build` exists as a scheduled job on cron `35 14,16,18,20 * * 1-5`.
- Manually started dev execution `trading-agent-build-di4n0ej`; it succeeded and persisted `trading_agent_runs` rows=5 and `trading_agent_candidates` rows=16.
- Follow-up end-to-end check found the 1 week horizon had `status=error` from an AQL planner timeout and zero candidates. This was not acceptable as fully landed; the timeout fallback was added and will be redeployed/rerun.
- Redeployed UI dev revision `sn-streamlit-ui-dev--0000297`; root smoke check returned HTTP 200.
- Redeployed pipeline image `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260505010428`.
- Manually started dev execution `trading-agent-build-t6r0q5u`; it succeeded and persisted `trading_agent_runs` rows=5 and `trading_agent_candidates` rows=20.
- Verified latest persisted frames contain all required horizons. Each of `1w`, `1m`, `3m`, `1y`, and `5y` has `status=ok`, `candidate_count=4`, and 4 persisted candidate rows.
- Verified `trading_agent_actions` read/bootstrap path exposes broker-aware log-only columns without writing a fake action event.
