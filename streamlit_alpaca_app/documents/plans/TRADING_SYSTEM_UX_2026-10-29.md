# Trading as a managed paper portfolio — October 29, 2026

Status: implemented in the shared backend, Next.js and SwiftUI on `fix/trading-system-20261029`, accepted on DEV and promoted to [production Trading](https://spectral-nature.com/trading-agent) under owner approval. All 36 definitions retain their configurations; all five API/web revisions are Healthy at full traffic.212 focused checks and16 web contracts pass. Build 17 is installed on the owner’s iPhone and independently read back. Launch verification is waiting for the owner to unlock the phone. Production portfolio, full original thesis, approval mode and live broker refresh are independently verified. See the [release record](../verification/trading-system-2026-10-29/README.md).

## Product decision

Trading opens on the paper portfolio, with the same value/history hierarchy as
Portfolio. Below that, a single activity feed connects the agent's proposals,
owner decisions, submitted orders, fills, ongoing thesis reviews and exits.
Calls are the positions and decisions that produced the portfolio, not a separate
discovery page that loses context when the user wants performance.

The agent researches, proposes, sizes, monitors, revises and learns. The owner can
approve or deny calls, inspect any position, intervene, set execution policy and
pause new entries. Both individual calls and the overall paper account have
continuous, inspectable histories.

Interactive design:
[Trading system](../../output/visualizations/trading-system-ux-20261029.html).
All balances, fills, proposals and narratives in this preview are an illustrative
scenario. They are not current holdings, executable recommendations or new
research. No network access or broker submission exists in the preview.

## Gaps identified in the original experience

The October 28 release adds performance to individual retained approvals. At the start of this request, its
production API lacked the separately DEV-accepted Alpaca paper-account
integration. Native Trading conditionally changes its meaning when the response
contains no paper_portfolio: it labels the screen Paper performance and opens one
research approval. There is no aggregate broker portfolio in that response.
Showing a per-call chart does not repair the missing account experience.

DEV already has retained Alpaca balance/history, positions and recent orders,
scientific sizing and explicit paper-order confirmation. See the
[account design and DEV acceptance](TRADING_AGGREGATE_PORTFOLIO_2026-10-22.md).
That source was intentionally not promoted with unrelated DEV maintenance files.
The current redesign requires deliberately integrating the account capability on
production's source, not quietly importing the whole DEV API.

There are also source gaps beyond presentation:

- The report view selects the latest generated run and candidate batch. An
  undecided call can vanish when a new report replaces it.
- Recent broker orders are a bounded snapshot, not a durable per-fill ledger.
  They cannot establish long-lived call P/L attribution across resizes,
  overlapping calls, partial fills and closes.
- Existing paper review supports entering a sized call. Full reduce/close,
  cancellation, durable execution policy and monitoring-to-order proposals need
  explicit contracts; adding buttons does not implement them.
- Refresh requests cashflow-inclusive history but persists only the equity/P&L
  arrays. Performance cannot silently assume every balance change is investment
  return or that all account activity was created by the agent.

Relevant owners: services/trading_agent_view.py, trading_call_tracking.py,
alpaca_paper.py, existing brokerage store, compute/paper_sizing.py,
SwiftUI TradingView.swift and the web Trading page.

## Primary screen

Use one stable Trading destination on iPhone and web. It always presents the
paper account. Missing linkage produces an explicit unlinked account state;
missing data must not replace the portfolio with one research-call detail.

The top of the screen contains:

1. Paper portfolio, account/provider identity and actual observation time.
2. Prominent account equity, period dollar P/L and cashflow-adjusted return.
3. Scrubbable, dated equity history with comparable time windows and an optional
   normalized SPY reference. Opening the screen must reveal the timeline without
   opening a call or expanding another panel.
4. Compact cash, open P/L, drawdown and Sharpe. Dates and observation count are
   available with the statistic. Unavailable metrics remain undefined.
5. Execution mode and entry-pause controls. Agent activity is supported by the
   last successful monitoring record; an old report is not a running status.

On iPhone, retain the chart above a three-way Activity / Positions / Performance
switch. Activity is the default. On wider web layouts, show positions alongside
the activity feed. Avoid another Calls / Paper performance split.

Period selection changes chart and period return together. Current equity,
current cash and open P/L retain explicit current-account meaning. Overall
track-record statistics state their separate inception window. Never present
different horizons as one unlabeled metric group. Intraday periods need actual
intraday data; daily observations must not be interpolated into fictional
intraday returns.

## Activity and approval flow

Pin unresolved actionable proposals above chronological activity. Once resolved,
the proposal leaves that queue and its decision remains in the timeline. Do not
repeat the full card in both places.

A proposal card shows action, ticker/company, concise original thesis, direction,
horizon, proposed quantity/notional, portfolio allocation and the reason for an
entry, resize or exit. Approve and Deny remain visible without expanding a long
research disclosure. The complete original research is one tap away. If fresh
sizing is unavailable, show the actual blocker and retain the proposal; do not
invent a notional, outcome probability or size from narrative confidence.

Approve opens the exact order review, retaining the current payload-bound ticket
and final confirmation. The review shows before/after holdings, estimated cash,
exposure and risk, the applicable policy and expiry. It must refresh/recheck the
ticket when its facts change. Dismissing it is not an approval. Deny records the
decision without placing an order; an optional explanation can reach the shared
learner without blocking the action.

The activity feed contains dated, stable events:

- A new call or management change proposed.
- Approved, denied, expired, withdrawn or superseded, retaining who and when.
- An order submitted, rejected, partially filled, filled, cancel-requested or
  cancelled. Actual filled quantity is always visible.
- A material thesis review, changed plan or requested intervention.
- A position reduced or closed, with realized outcome and the linked original
  call. Routine price polling must not flood the feed.
- Mode/policy and pause changes that affected execution.

No approval is a fill. A queued order does not change position size or realized
P/L. An uncertain submission cannot be retried as a new purchase. A cancellation
request is not a cancellation; fills arriving during cancellation remain real.
Keep a working-order list accessible from both Activity and Positions.

Filtering and paging preserve unresolved proposals and the selected call. Do not
erase decisions or pending orders on a report refresh or app restart.

## Position and call detail

Positions show symbol, direction, shares, current value, P/L and return. Selecting
one opens its contributing calls; do not guess a call from ticker alone when
several trades overlap.

A call has one continuous detail view:

- Actual realized/unrealized P/L and return, plus a dated chart from its fills.
- Quantity, actual average entry, reductions/exit fills, fees and current mark
  with their observation times.
- Contribution to aggregate portfolio return, computed by the shared accounting
  service, with its denominator and period.
- Original thesis, horizon, expected review/exit date, invalidation and subsequent
  revisions. Preserve the original research after the latest report changes.
- Timeline from proposal through approval, fills, reviews, resizes and closure.
- Review an exit/reduction, ask the agent to reconsider, or inspect/cancel an
  unfilled order as the state permits. These create proposals or bounded order
  actions; a UI tap cannot silently sell the position.

Forecast/counterfactual returns are a separate named series from actual
fill-based performance. Retain the existing historical research observations
under Performance; never migrate old approvals into fictional Alpaca fills.
Denied and unexecuted calls may have counterfactual outcomes, but never enter
portfolio equity, realized P/L or the executed-trade win rate.

## Overall performance

Account equity, cash, positions and fills come from the owner's Alpaca paper
account. Aggregate performance includes closed positions and cash, and accounts
for external funding, fees, dividends, splits and other broker activities.

Show portfolio return and P/L, realized and open P/L, maximum drawdown, Sharpe,
exposure and a dated benchmark. Show the number and period behind win rate,
profit factor and holding duration; avoid a score with no population. Use the
existing shared statistical conventions, with cashflow-neutral daily returns
established first. Do not average call percentages or use account equity growth
as a cashflow-neutral return.

Agent attribution is a view of the same account ledger, not a second fabricated
portfolio. Match actual fills to approved call/order IDs and quantity
allocations. Manual or unmatched activity remains explicitly unattributed.
Per-call contributions, realized/open P/L, fees, distributions and unattributed
activity must reconcile to the account. Where history cannot support exact
attribution, show available coverage; do not assign the missing amount to the
nearest ticker or invent a historical starting balance.

The prototype's contribution values reconcile to its sample no-cashflow account.
Its preview does not implement a general return-attribution engine.

## Execution policy and self-driving behavior

Default to Review each order. Entries, increases, reductions and exits are
proposed by the shared agent and approved through the same order contract.

An explicit account-owned Trade within approved limits mode can authorize
automatic paper execution inside a versioned policy. Its activation review
states the allowed actions, paper-only account, risk/exposure/concentration
limits, sizing method, order types/session constraints and stale-data behavior.
The existing default risk preferences are starting controls, not empirically
optimal constants. Changes are owner requests handled through the existing
research/control surface, then reviewed as concrete policy changes.

Automatic execution needs durable scoped authorization enforced at the service
and execution boundary, not merely a client toggle or an LLM instruction. Check
current account state, policy version, exact allowed action, remaining quantity,
data freshness, capacity, prior in-flight/unknown orders and one-use order
identity under the existing account lock. A retry cannot create a second trade.
Initial entry and later reduce/exit orders need distinct stable IDs.

Pause new entries blocks new entry submissions, including a previously prepared
ticket, while monitoring and exit review continue. It does not cancel working
orders, flatten positions or revoke a fill. A full stop/cancel operation, if
provided, requires its own explicit scope and confirmation.

Monitoring and new recommendations must run in the existing shared Trading
producer/AQL-Zopedia system with actual account/position context and the original
call histories. No client-side research, hardcoded thesis interpretation,
separate local trading agent or hidden inference on portfolio reads. Market,
fundamental and news changes guide the agent's next investigation and management
proposal; results and outcome learning reuse the established shared paths.

Do not claim self-driving execution is implemented by the existing scheduled
outcome publisher. Its observation of a research horizon is not a broker exit.

## Shared source and data design

Retain the existing ownership boundaries:

| Owner | Required change |
|---|---|
| Trading producer / shared research | Consume owned portfolio context, preserve logical call and revision identity, propose bounded entry/management actions and publish meaningful monitoring updates. |
| Existing Trading state/store | Persist unresolved proposals and immutable revisions/decisions beyond a latest-report batch; retain original approval IDs and owner/environment scope. |
| Alpaca paper / existing brokerage store | Persist paginated broker activity and order state with idempotent reconciliation, stable order/fill IDs, cashflows and account snapshots. |
| Shared compute/account projection | Reconcile equity, realized/open P/L, cashflow-neutral performance and explicitly allocated call contributions. |
| Trading view / API | Project one owned workspace: account, period performance, attention-required proposals, working orders, positions and cursor-paged activity. Expose capability/state explicitly. |
| SwiftUI and Next.js | Render the same workspace and order contracts with adaptive layouts; maintain lazy detail, navigation, drafts, retry identity and focus. |

Use the existing database and account locks, not a shadow account or separate
event bus. Extend current Trading/brokerage persistence deliberately where it
does not represent the required state. Avoid a generic orchestration framework.
Every broker execution is associated with the call, revision, approved action,
policy/ticket and broker receipt. The UI receives no credentials or private
connection metadata.

GET reads retained state only. Bounded ingestion/reconciliation is background or
an explicit authenticated refresh. Partial failures retain each component's own
last-success time; one fresh account balance must not relabel an older position
set, activity ledger or historical series as fresh. Account visibility and
research progress stay usable when sizing or execution is blocked.

Broker contract references checked October 29:
[portfolio history](https://docs.alpaca.markets/us/reference/getaccountportfoliohistory-1)
returns equity/P&L series and supports cashflow inclusion;
[account activities](https://docs.alpaca.markets/us/reference/getaccountactivities-2)
is paginated and can be filtered by order ID to retrieve individual fills.
Persist the actual activities rather than treating the newest 100 orders as
complete history. Provider history percentage conventions and available
cashflows must be validated before assigning portfolio-return semantics.

## Delivery and acceptance

1. Reconcile the already accepted Alpaca capability on current production source,
   preserving active shared research/credit behavior and excluding unrelated DEV
   maintenance. Confirm the intended owner/environment account binding without
   copying DEV credentials or silently enabling execution.
2. Implement durable call/action/fill linkage and the shared workspace projection.
   Qualify accounting on partial fills, overlapping calls, shorts, resizes,
   closes, funding, fees/corporate actions and unmatched activity. Numerical
   checks establish accounting; they do not establish agent decision quality.
3. Replace the two client Trading flows with the portfolio-first design. Inspect
   populated, new-account, unlinked, stale, blocked, queued, partially filled and
   ambiguous-submission states on compact iPhone and web. A failed research
   refresh must not hide the account or open calls.
4. Connect monitoring and management proposals through the existing shared agent.
   Personally review complete decisions and outcomes with account context. Keep
   autonomous execution disabled until its authorization, risk and reconciliation
   behavior is qualified. The UX preview is not evidence of that qualification.
5. Accept locally, pass committed source review, deliver to DEV and inspect the
   actual signed-in paths. New production deployment requires explicit owner
   permission. There is no deployment as part of this design-only change.

The design preview exercises portfolio periods, benchmark visibility, proposal
detail, review/deny, submitted-but-unfilled orders, cancellation request,
position/call detail, performance and mode/pause controls. All interactions are
local and illustrative. Visual QA and remaining implementation status are recorded
in [the design review](../verification/trading-system-ux-2026-10-29/DESIGN.json).


## October 29 implementation

Active implementation lives in `fix/trading-system-20261029`, isolated worktree `/Users/omairs/Documents/code/spectral_nature_trading_system_20261029`. This combines accepted production Python 7c21bfae, accepted DEV behavior 79e95c65, and accepted native build 16 source 975b073c. It is not yet release accepted. No new production authorization is assumed.

Implemented source: durable owner/portfolio/environment calls, policies, exact order mappings and event history in the existing brokerage database; fill-based long/short accounting; cashflow-aware account analytics; review/confirm position changes and cancellation; existing Trading producer monitoring through AQL/Zopedia; account-first Next.js and SwiftUI screens. Pending: full numerical and PostgreSQL verification, populated/empty visual inspection, source integration review, release acceptance and DEV delivery. Autopilot is default off; no owner setting or broker order has been changed for QA.
