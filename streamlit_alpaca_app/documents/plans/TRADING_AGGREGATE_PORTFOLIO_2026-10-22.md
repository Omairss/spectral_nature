# Aggregate approved-paper portfolio — October 22

The owner wants the Trading screen to open on one aggregate portfolio, rather than
a list/selector of strategies. The owner confirmed approved paper calls as the
portfolio membership. Keep unapproved research and all existing approval controls
in Calls; this change neither approves calls nor places broker orders.

## Owner correction: Alpaca paper account

On October 22 the owner selected Alpaca paper trading as the accounting authority
and supplied credentials for Key Vault. Dedicated secrets
`alpaca-paper-trading-key` and `alpaca-paper-trading-secret` are stored in
`spectral-nature-kvault`; the market-data secrets were preserved. A read-only
probe returned an active USD account with $100,000 cash, no positions/orders and
251 equity-history observations. No order was submitted.

The equal-capital aggregate below was an implementation assumption, not an
accepted allocation policy. It is superseded. Do not ship its synthetic capital,
cash or matched-SPY portfolio as broker accounting. Retain the original per-call
research observations separately; historical approvals are not Alpaca fills.

Reuse the existing Alpaca adapter and durable brokerage connection/snapshot store.
Bind credentials by server-side Key Vault references to the owner's identity and
environment. Persist broker-reported balances, positions, orders and equity
history. GET renders retained state; an explicit authenticated refresh performs
bounded read-only acquisition. Snapshots must include provider and owner identity
so the paper account cannot replace the separately linked Robinhood portfolio.
Both clients render the same contract. A failed component refresh retains its
last successful data and observation time. The client never receives credentials.

The owner confirmed that approving a call should proceed to a sized paper-order
review and required scientifically grounded sizing. Implement review/confirmation;
do not submit an operator test order or backfill historical approvals.

## Sizing and paper-order contract

The current Trading thesis has direction and narrative confidence, not calibrated
conditional return distributions. The existing analog forecast is not a validated
edge model for all Trading horizons. Do not derive win odds, expected return or
Kelly fractions from narrative confidence. Kelly and risk-constrained Kelly need
an outcome distribution: [Busseti, Ryu and Boyd (2016)](https://www.web.stanford.edu/~boyd/papers/kelly.html).

The initial model budgets volatility rather than claiming growth-optimal bets.
Estimate annualized covariance from up to 252 completed daily adjusted returns
with Ledoit-Wolf shrinkage, requiring 126 common daily returns. Use the established
[Ledoit-Wolf estimator](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html)
and [covariance-shrinkage paper](https://ledoit.net/honey.pdf). Solve the portfolio
variance quadratic for the proposed holding, retaining all existing broker
positions, then intersect that interval with standalone position volatility,
single-name exposure, gross exposure and funding limits. No mean/alpha estimate
is used. Estimate and show historical one-day 95% expected shortfall too.

Risk preferences are explicit controls, not results established by a paper. The
initial review defaults are 2% standalone annualized position volatility, 10%
portfolio volatility, 10% maximum single-name exposure, 100% gross exposure.
These values need empirical assessment and owner review; they are not a claim
of optimality. A size recommendation must expose the policy, binding constraint,
estimated risk before/after, history dates/version, sample size and covariance
shrinkage. Compare rolling out-of-sample risk forecasts against equal-notional and
inverse-volatility controls; report results before enabling production execution.

Size actual Alpaca positions, not a reconstructed call portfolio. No new position
silently closes/reverses an existing opposite holding. Initially support US-equity
market/day orders during the open session, after earlier paper orders settle;
unsupported holdings or incomplete/stale history block a new recommendation,
not research or account visibility. Short eligibility comes from Alpaca. Shares
round down to provider-supported increments.

The order review creates a two-minute owner/environment/payload-bound ticket in
the existing brokerage store. Confirmation rechecks active admin membership,
current broker facts, risk capacity and price before a paper-only submission.
One account lock serializes submissions across replicas/environments. A stable
client order ID makes duplicate submissions identifiable; POST is never retried.
Ambiguous submissions stay unknown and use Alpaca lookup for reconciliation.
Research approval and real broker fills retain distinct identities and meanings.

## Superseded aggregate prototype

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

Status: replacing the local aggregate prototype with Alpaca-owned accounting.

## Native and source acceptance update

Signed build 10 compiled and account/review screens were inspected in iPhone 16 Simulator. Shared account projection now normalizes broker amounts and timestamps for both clients. Development integration retains all 17 maintenance files byte-for-byte. API/web builds are in progress; production and physical-phone install remain unauthorized for this task.
