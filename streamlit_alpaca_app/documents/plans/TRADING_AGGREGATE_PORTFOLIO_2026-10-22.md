# Aggregate approved-paper portfolio — October 22

The owner wants the Trading screen to open on one aggregate portfolio, rather than
a list/selector of strategies. The owner confirmed approved paper calls as the
portfolio membership. Keep unapproved research and all existing approval controls
in Calls; this change neither approves calls nor places broker orders.

## Shared ownership and accounting

`services/trading_call_tracking.py` owns the read-only aggregate of immutable,
owner-scoped approvals and already-published daily outcomes. Both Next.js and
SwiftUI render its same portfolio contract. Preserve lazy individual call detail.
No new schema, research invocation, price fetch or account mutation on reads.

There is no accepted account allocation or starting balance. Use one equal unit of
initial paper capital per entered call. Funding enters at that call's recorded
entry close; it must not change the portfolio return. Chain daily returns around
those external contributions. Completed positions turn into cash and remain in
portfolio value; do not retrospectively rebalance into later approvals or drop
closed calls. Pending entries do not contribute capital. Benchmark the same dated
long/short allocations using retained SPY outcomes. Preserve incomplete coverage
explicitly; never treat unavailable marks as zero or fabricate a benchmark.

Aggregate holdings by symbol, retaining long and short exposure where theses
overlap. Show overall return, drawdown, dated equity/benchmark history, long/short
exposure, cash and positions with contribution to portfolio return. Individual
original theses, dates and performance remain available through the holding's
call drilldown; closed calls are secondary. Keep the first screen compact on iPhone.

## Acceptance and release

Review staggered starts, short signs, overlapping symbols, maturity/cash, funding
neutrality, missing prices/benchmark and owner isolation with objective arithmetic
checks. Personally inspect populated web and native views, holding/call drilldown,
empty/unavailable states and preserved approve/reject behavior. Do this before
packaging. Production requires a new explicit owner approval.

Build from an isolated accepted Git checkout and preserve the latest client-refresh
release. The current DEV API also contains the accepted isolated maintenance
rollout; do not overwrite that behavior to deploy Trading. Reconcile that source
integration and its consumer contracts before any DEV packaging. Unrelated
research in the shared IDE checkout remains untouched.

Status: implementing shared accounting and portfolio-first clients.
