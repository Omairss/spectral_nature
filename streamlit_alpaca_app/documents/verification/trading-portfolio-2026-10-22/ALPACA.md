# Alpaca paper portfolio — active verification

The owner replaced the equal-capital prototype with Alpaca paper accounting and
authorized an explicit order review/confirmation flow with scientific sizing.
The prototype's older screenshots/builds in this folder are historical evidence.

## Account and data ownership

Dedicated Key Vault secrets `alpaca-paper-trading-key` and
`alpaca-paper-trading-secret` are stored in `spectral-nature-kvault`. The existing
market-data credentials were preserved. A read-only probe and subsequent owned
development connection refresh returned an active USD account, $100,000 equity
and cash, $400,000 buying power, zero positions/orders and 251 history rows.
The owner/environment-bound connection stores Key Vault references, not keys.
A final refresh at 23:31 UTC confirms the same empty account and 147 funded history points after trimming leading pre-funding zeros. No operator paper order was submitted. No production connection was registered.

The existing brokerage store now supports distinct connections in one portfolio.
An isolated PostgreSQL schema verified the old unique-key migration, two broker
snapshots coexisting, owner/environment isolation and one-use order tickets; the
QA schema was removed. The actual development snapshot was saved and read back. Later integration checks also verify component-wise freshness under out-of-order writes, disabled-user rejection and positive-proof recovery from unknown to executed. Six existing Alpaca adapter checks pass.
Main Portfolio/Robinhood linking was not changed. GET is retained-data only;
bounded explicit refresh retains last-good components and their observation times. Both snapshots and order reviews pin the provider account identity; reconnecting cannot reuse an older account's cache or ticket.

## Sizing evidence and limits

The current research forecast has no calibrated conditional return distribution.
The baseline budgets volatility with Ledoit-Wolf covariance; it does not claim a
Kelly fraction, proven edge or growth-optimal allocation. Review limits are user
preferences, initially 2% position volatility, 10% portfolio volatility, 10%
single-name exposure and 100% gross exposure, all annualized where applicable.
Defaults are owned by the shared model and supplied to both clients.

The [rolling comparison](sizing-risk-evaluation.json) uses seven selected surviving
assets, up to 252 training sessions, subsequent 21-session windows and a 2024+
evaluation period. This is a narrow risk-forecast diagnostic, not a Trading-alpha
backtest. In the 32 held-out inverse-volatility-book periods, diagonal covariance
underestimated realized volatility by more than 50% in 46.9%, versus 9.4% with
252-session shrinkage. The latter still missed the March–April 2025 shock by
3.33 times. Fast shrinkage/envelope alternatives are retained; no universal
superiority, guaranteed loss limit or production calibration is claimed.

Order review acquires current completed daily history through the existing Alpaca
adapter with an overall acquisition deadline. The dev materialized history is
stale through October 16 and is not substituted for current execution inputs.
Market-calendar alignment preserves missing sessions; prices, retained return
inputs, policy and covariance are pinned in the durable review. Confirmation
rechecks current account/risk capacity and records its fresh inputs too.

## Local checks and remaining acceptance

Twenty-one objective numerical/account/confirmation checks pass, including cash and
exposure caps, correlation, no duplicated target allocation, missing history,
no implicit reversal, partial refresh retention, owner isolation, expired tickets,
changed risk capacity, paper-only transport and ambiguous submission without a
second POST. These checks do not assess NLP quality. Existing auth/Home checks
pass; web types, scoped new-code lint and Swift syntax pass. The API request schemas import successfully with the shared policy defaults.

The normal local gateway and CSRF checks were exercised using an isolated QA
backend. The account overview uses captured broker data; populated holdings and
call/order-review navigation use declared presentation fixtures. No broker order
endpoint is connected to that fixture. The review shows its computed amount,
policy limits, history dates and volatility before/after.

Signed native build 10 compiled successfully and was inspected in the iPhone 16 simulator. The account view renders broker balances and ISO timestamps; the review shows exact shares, amount and shared risk inputs. Captures: alpaca-native-overview.png and alpaca-native-review.png. Native request handling rejects malformed objects and retains recoverable errors. Final exact-source packaging and authenticated development verification remain pending. No application image from this task is deployed and build 10 is not installed on the physical phone. Source/build
qualification does not authorize production. Preserve the separately accepted
seventeen-file dev maintenance runtime during integration, and do not promote
that experimental research source to production incidentally.

The restored Health maintenance section is exact accepted source. Whole-file lint
still flags its pre-existing synchronous state update in an unchanged effect;
new Trading files pass scoped lint. This distinction remains part of the review.

The native review exposed broker decimal-string values masked by browser coercion. The shared public DTO now emits finite numbers for amounts/prices and ISO8601 timestamps, retaining exact order quantity strings. All 21 paper checks passed after this correction.
