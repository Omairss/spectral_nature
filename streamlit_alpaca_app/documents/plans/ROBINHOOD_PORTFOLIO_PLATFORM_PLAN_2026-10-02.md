# Robinhood Main-Account Portfolio Platform Plan

Date: 2026-10-02

Status: deployed to development and production after explicit production approval

## Goal

Turn the authenticated Robinhood main-account connection into one coherent
portfolio system that can:

1. inspect equity, option, and futures holdings with cost, value, movement,
   profit/loss, and exposure;
2. show broker-sourced historical portfolio performance;
3. create and execute a portfolio-wide batch of equity stop orders;
4. inspect Robinhood lists and saved screeners, measure their performance, and
   create executable long or short equity baskets; and
5. inspect option contracts with Greeks, price history, position exposure, and
   contribution to portfolio risk.

The hosted Robinhood MCP remains attached to the separate agentic account. The
main accounts continue through the encrypted app-session adapter.

## What Is Proven Now

- A real local authentication completed on 2026-10-02 and returned two active
  margin accounts. The encrypted connection is durable and reports
  `status=connected`.
- The first portfolio refresh exposed the retired classic historicals route;
  the adapter now uses Robinhood's current Bonfire performance route.
- Robinhood's current portfolio chart is available at
  `GET https://bonfire.robinhood.com/portfolio/performance/{account}/` with
  `display_span`, `include_all_hours`, and
  `chart_type=historical_portfolio`. The reliable curve is
  `lines[identifier=returns].segments[].points[]`; each point carries a broker
  dollar value, return fraction, and timestamp label.
- The latest clean local sync contains 26 equity rows, four option positions,
  one verified futures contract, 228 portfolio-performance points across two
  accounts, 58 catalog entries, and no stale sections or collector errors.
- Bounded opportunity-set preload retained 57 group resources and 215,732
  local history points across 471 equity symbols. Thirty-eight groups are
  analytically ready; very broad groups remain metadata-only instead of
  causing an abusive 8,983-symbol backfill.
- The adapter supports signed equities, option aggregates and Greeks, and only
  Ceres accounts explicitly typed as futures. Prediction-market positions are
  excluded rather than misreported as futures.
- Stop and basket submissions use version 7 JSON, explicit position effect, a
  live quote collar, stable idempotency references, and current web-request
  headers. Review and local acceptance did not submit a real order.

## Upstream Evidence

Implementation is grounded in current source, not old endpoint memory:

- `jamestford/pyhood` at commit
  `0e47a61bbc46e29ecd6e1938f1975c4908a18097` for the retired historicals
  endpoint, current Bonfire performance route, options/futures coverage, and a
  250 ms read-request floor.
- `kevin1chun/robinhood-for-agents` at commit
  `3cdb50455629e982ac41b02163a7ee14d712850d` for current multi-account reads,
  option aggregates, lists, scanner metadata, order version 7, and explicit
  `sell_short + position_effect=open` semantics.
- `zaydiscold/robinhood-cli-mcp-api` at commit
  `96b3230451f7063fbde06dc9f4844534922b2282` for the live-verified portfolio
  performance shape, Ceres futures routes, order collars, current web headers,
  write pacing, and server-directed 429 cooldown behavior.
- Robinhood's current support documentation confirms stop orders for equities,
  option stop/limit support, futures market/limit/stop support, and the rolling
  availability and constraints of direct equity short selling.

These are references for endpoint behavior. Spectral Nature owns its own
credential storage, data contracts, redaction, request policy, and UI.

## Architecture

### One Broker Client

`services.robinhood_main.RobinhoodAppClient` remains the sole Robinhood
transport for main accounts. It will own:

- a stable Spectral Nature user agent;
- trusted Robinhood origins (`api`, `bonfire`, and the explicitly required
  related service hosts only);
- content type selected per request;
- a process-wide request governor with serialized calls and a minimum delay;
- no automatic mutation retries;
- bounded retries for transient GET failures only;
- `Retry-After` handling and a shared cooldown after HTTP 429;
- safe endpoint/status diagnostics with credentials, account numbers, response
  bodies, and tokens excluded from logs and user errors;
- exact-symbol resolution before every order write; and
- refresh-token renewal without replaying a password login.

Robinhood calls remain explicit actions or background snapshot jobs. Page
rendering reads durable snapshots and never fans out into hidden broker calls.

### Independent Collectors

A refresh is one operation, but each asset class is collected independently:

| Collector | Primary source | Output |
| --- | --- | --- |
| Account | accounts, portfolio profile, Bonfire live summary | cash, equity, buying power, asset-class totals |
| Equities | positions + batched quotes | long/short quantity, average cost, current value, day move, P/L, reserved quantity |
| Options | aggregate positions + batched option market data | strategy/legs, mark, cost, P/L, IV and signed Greeks |
| Futures | Ceres accounts, aggregated positions, quotes, P/L cost basis | direction, contracts, multiplier, current and realized/unrealized P/L |
| Performance | Bonfire performance route | timestamped dollar value and return for each account and display span |
| Lists | discovery list metadata/items + batched stock history | members and equal-weight performance |
| Screeners | saved Beacon/Bonfire screener definitions and available results | named screens, symbols, live columns, and performance where symbols are available |

An unsupported or empty asset class is recorded as a capability state. It does
not erase working equity or performance data. A failure in one account or
collector is visible in refresh metadata but does not discard successful
sections.

### Normalized Position Contract

Every holding is represented by the same core fields:

- opaque connection-bound position key;
- masked account label;
- asset class and instrument type;
- symbol and human label;
- signed quantity, direction, and multiplier;
- average cost, total cost basis, current mark, market value;
- unrealized P/L dollars and percent;
- day change dollars and percent;
- broker timestamp and quote timestamp; and
- asset-specific details under a typed `details` object.

Equity details include available/reserved shares and tradability. Option details
include strategy, expiry, strikes, side/effect per leg, IV, delta, gamma, theta,
vega, rho, open interest, and volume. Futures details preserve the verified
Ceres payload fields and calculate only values supported by the broker payload;
unknown fields remain unknown rather than guessed.

### Dedicated Robinhood Data Plane

Robinhood does not extend or reuse the Alpaca data path. Its dedicated snapshot,
append-only series store, durable resource cache, and refresh queue persist:

- account summary;
- normalized positions across asset classes;
- performance points by masked account and span;
- option history requested for current positions;
- lists/screeners and their performance series;
- capability/freshness/error metadata; and
- no raw account numbers, credentials, tokens, or write payloads.

Exact account and instrument identities stay encrypted or are resolved live for
execution. Browser-visible rows use opaque hashes and masked labels. The
Portfolio renderer reads this local model only; a separate worker owns every
background Robinhood request.

## Feature Design

### 1. Holdings and Movement

The Portfolio page becomes an asset-aware overview:

- summary cards for total equity, cash, buying power, day P/L, total P/L, and
  long/short exposure;
- allocation by asset class and underlying;
- a selected-lookback risk/return map using commensurate historical measures;
- covariance-based volatility contribution so concentrated risk can differ
  visibly from capital allocation;
- robust movement outliers measured against each holding's own return history;
- clustered holding correlations and principal-component loadings for common
  factor exposure;
- portfolio tail loss, drawdown, beta, concentration, and effective-risk-bet
  diagnostics;
- filterable holding tables for Equities, Options, and Futures; and
- a per-underlying exposure rollup combining shares, options delta, and futures
  notional when the source fields support it.

The UI shows data and actions only. Collector names, endpoint names, and raw
payloads remain in debug/admin state.

### 2. Portfolio Historical Performance

Replace the retired endpoint with the current Bonfire route for the selected
period. Fetch each account separately, preserve the broker's dollar curve, and
combine accounts by timestamp after forward-filling only within each account's
observed window. Do not extend an account before its first broker point.

The Performance page will show:

- aggregate dollar value;
- cumulative and periodic return;
- drawdown;
- portfolio versus benchmarks;
- per-account contribution; and
- locally sampled snapshot history as a continuity fallback, clearly separated
  from the broker curve.

### 3. Batch Stop Losses

The current one-review/one-place flow stays compact. It is generalized to all
eligible long equity positions across both accounts:

- load current holdings, open orders, and fresh quotes;
- calculate available shares after broker reservations;
- generate editable stop candidates from one user-selected portfolio rule;
- re-read the live facts at review and again at placement;
- submit sequentially with deterministic child `ref_id` values;
- use order form version 7, `position_effect=close`, JSON, current quote collar,
  regular-hours stop semantics, and the exact owning account;
- never retry after an ambiguous transport failure; and
- stop the remaining batch on shared rate limit, expired authorization, or an
  account-wide buying-power/eligibility failure.

The result table reports accepted, working, rejected, unknown, and skipped
orders separately. A broker order-history readback is the evidence that an
order exists.

### 4. Lists, Screeners, Batch Long/Short

The Opportunity Lists surface combines:

- the user's Robinhood lists;
- Robinhood-curated lists the account can read;
- saved screeners and their returned symbols when available; and
- Spectral Nature's existing signal lists through the same basket contract.

For each symbol set the system calculates equal-weight historical performance,
dispersion, current constituents, winners/losers, turnover when membership
timestamps exist, and benchmark-relative return.

The basket builder supports:

- invest a dollar budget across selected names using equal or editable weights;
- open whole-share short positions only when the selected account is margin
  enabled and Robinhood reports the symbol as shortable;
- explicit long (`buy`) versus short (`sell_short`) intent per row;
- GFD and supported session rules for shorts;
- live quote and pre-submit checks before creating an executable ticket; and
- the same exact-payload, one-time ticket and sequential execution model as
  batch stops.

The implementation will not silently translate `sell` into a short. A short is
always sent as `sell_short` with `position_effect=open`; covering uses `buy`
with close intent.

### 5. Option Greeks and History

Open option aggregates are expanded into legs, quoted in bounded batches, and
stored with:

- per-contract and position delta, gamma, theta, vega, rho, and IV;
- mark, bid/ask spread, volume, open interest, intrinsic/extrinsic value;
- days to expiration, moneyness, strategy, cost basis, value, and P/L; and
- portfolio totals by underlying and expiry.

Signed position Greeks use contract direction, ratio, quantity, and the 100x
multiplier. Current Greeks are local sensitivities, not a historical P/L claim.

The option detail view uses Robinhood option historical candles for price
history and overlays cost basis and underlying history. It does not fabricate
historical Greeks when the broker supplies only current Greeks.

## Request Policy

- One Robinhood request at a time per process.
- At least one second between reads by default; heavier option/list batches are naturally
  serialized.
- At least 2.5 seconds between live order submissions.
- Authentication POSTs are never retried automatically.
- Ordinary order POSTs are never replayed after timeouts or unknown responses.
- HTTP 429 activates the broker-directed cooldown for the whole connection.
- A 401/403 ends the operation and attempts refresh-token renewal only on the
  next safe read; mutations require a new live revalidation after renewal.
- Pagination rejects untrusted hosts and repeated next URLs.
- Batch sizes follow observed limits: quotes are batched, option market data is
  kept small, and instrument lookups are cached for the refresh.

## Implementation Sequence

1. Harden the client transport, trusted hosts, token refresh, diagnostics, and
   request governor.
2. Replace retired portfolio historicals and prove a real snapshot can be
   refreshed and linked.
3. Add equity, option, and futures collectors with independent capability
   states and persist the normalized snapshot.
4. Rebuild Portfolio and Portfolio Performance around the normalized snapshot.
5. Upgrade batch stops to the current order contract and validate through
   review without placing a real order.
6. Add lists/screeners, performance analysis, and the long/short basket ticket.
7. Add option exposure and option-history views.
8. Qualitatively inspect the real local output and update architecture,
   learnings, and mistakes. Stop before deployment until the owner explicitly
   approves the local result.

## Acceptance

- A local refresh creates a linked snapshot for both connected accounts.
- Equity, option, and futures sections either show real broker data or an exact
  capability state without breaking the rest of the page.
- The broker performance curve renders for the selected period and reconciles
  its latest point to the current account value within source timing.
- No raw account number, credential, or token reaches the browser, snapshot,
  logs, or error messages.
- Stop and basket reviews make no mutation request.
- Order bodies use current version/session/effect semantics and deterministic
  references.
- No real order is placed during development validation.
- Development is deployed only after the local read path and rendered pages are
  personally inspected. Production is not changed without explicit permission.

## 2026-10-02 Implementation Record

Implemented:

- one serialized Robinhood client with stable user agent, separate read/write
  pacing, shared 429 cooldown, trusted origins, safe GET retries, no mutation
  replay, refresh-token renewal, and sanitized diagnostics;
- Bonfire portfolio performance parsing across both connected accounts;
- independent equity, option, futures, performance, benchmark, list, curated
  list, and saved-screener collectors;
- redacted asset-aware snapshot details and JSON-safe persistence;
- current option marks, signed position Greeks, IV, open interest, volume, P/L,
  daily movement, and supported day/week contract history;
- Portfolio asset tabs, exposure and P/L charts, broker performance, drawdown,
  account contribution, list performance, and editable basket views;
- version-7 JSON stop orders with explicit close effect and live quote collar;
- payload-bound batch buy/short tickets with exact-symbol resolution, margin,
  whole-share short, shortability, duplicate-open-order, quote, ownership, and
  deterministic child-reference checks; and
- 37 focused service/render checks, including option/futures exposure math,
  stale-section retention, local-only lookbacks, bounded group preload, and
  a synthetic all-asset UI render, plus membership-versus-execution ownership
  checks on cached and live paths.

Live result:

- real authentication and two margin-account enumeration succeeded;
- the retired historical route was reproduced and replaced;
- the current route returned and persisted the combined real portfolio curve;
- the shared serializer converted a missing benchmark value to JSON `null`;
- the rendered Portfolio page showed 26 equities, four options with Greek
  exposure, and one verified `MCLX26` futures position; Ceres prediction-market
  positions were excluded by account and contract identity;
- Portfolio Performance rendered the aggregate curve, benchmark comparison,
  drawdown, and correctly formatted cash-flow-adjusted per-account summaries;
- the selected Robinhood list rendered its equal-weight one-year history and
  editable long/short basket rows; and
- the persisted snapshot audit found two masked account labels, 228 portfolio
  points, 58 catalog entries, no collector errors, no raw identity keys, no
  invalid opaque keys, and no non-finite numbers;
- the opportunity-set inventory found 15,463 memberships and 8,983 unique raw
  symbols, so historical preload was bounded before fan-out; and
- the final local cache contains 215,732 compacted history points for 471
  equity symbols, with broad groups retained as metadata-only.

The live read acceptance is complete. No real order and no production change
were made. Development revision 452 was deployed before the owner approved the
local result; no further Robinhood deployment may occur without an explicit
go-ahead after local review.

Durable stale-while-revalidate caching is now implemented for the portfolio,
holdings, option history, list membership, and group performance inputs. Page
rendering and lookback changes issue no Robinhood requests. Order review,
available quantity, open orders, quote collar, and final placement validation
remain deliberately live and fail closed.

Robinhood remains separate from the Alpaca architecture. The revised source of
truth, sync queue, time-series store, resource cache, cadence, and group
analytics contract are specified in
`documents/architecture/market_data/ROBINHOOD_LOCAL_DATA_PLANE_2026-10-02.md`.
The shared Portfolio surface may select the Robinhood read model, but Robinhood
must not enter Alpaca loaders, caches, pipeline datasets, or refresh paths.

## Scientific Analytics Upgrade — 2026-10-02

The first movement scatter mixed a one-day price change with lifetime P/L. Both
fields were individually useful, but their horizons were not comparable and
the chart did not explain portfolio risk. It has been replaced at the source by
one local analytical model over held-symbol history.

For equities, the model now calculates selected-lookback return, trailing
annualized volatility, downside deviation, maximum and current drawdown, 5%
expected shortfall, SPY beta, robust latest-move z-score, covariance-based
volatility contribution, concentration, momentum regime, clustered return
correlation, and principal-component loadings. The renderer exposes these as a
risk/return map, risk-contribution ranking, correlation/factor views, movement
outlier chart, and a dense diagnostics table. Market-value coverage is shown so
partial history cannot look complete.

For lists and screeners, the equal-weight comparison remains the common return
baseline. Each group additionally receives breadth, volatility, downside and
tail risk, drawdown, dispersion, contribution concentration, average
correlation, first-component share, effective independent risk bets,
diversification ratio, and momentum regime. The group comparison is now a
risk/return/breadth map with clustered overlap diagnostics. The Sankey has
selectable return-driver, risk-concentration, and momentum-regime lenses, with
balanced best/worst tails ranked across every analytics-ready group for the
selected lookback. Group nodes show the realized return and whether they belong
to the best or worst tail; a visible tail-depth control bounds node count. The
equal-weight comparison selection remains independent and does not truncate the
Sankey's ranking universe.

All calculations read the Robinhood local series store. Changing a chart,
lookback, lens, or selected group makes no provider request.

Authorization is deliberately asymmetric. Any active portfolio member can
read the complete cached portfolio and analytics. Only an active portfolio
admin can connect, disconnect, enqueue refreshes, edit/submit baskets, or
review/confirm stop orders. The service rechecks active admin membership when
the confirmation ticket is consumed; hiding controls in the page is not the
security boundary.

Real-cache visual inspection rendered 15 interactive charts with no editor or
order controls in the member path. Focused authorization, local-data, analytics,
and renderer checks pass 52/52. Held-symbol history currently covers 16 of 25
equity symbols and about 90% of held market value. The collector now fingerprints
the held-symbol requirement and, while the larger catalog is still fresh,
requests only newly missing holdings.

The attempted local incremental backfill did not reach Robinhood: the standalone
worker could not decrypt the existing connection because it was launched under
a different encryption identity from the application that created it. That
local runtime configuration remains unresolved. The nine missing held-symbol
histories will backfill after a worker inherits the same environment-scoped key.
No Robinhood request or order was made during the local analytics pass.

## Development Rollout — 2026-10-03

The owner approved a development deployment after local inspection.

- Development UI revision `sn-streamlit-ui-dev--0000454` runs digest
  `sha256:edf27d8dca08b8c5cf31c2d4f2f458e2da6b3b06ceb3fce57de3d2d1aa6b7595`.
- The release is a five-file overlay on the previously healthy revision 452
  image. It contains only the accepted broker service, local store, analytics
  engine, authorization change, and Portfolio renderer; unrelated dirty-tree
  work was excluded.
- Root and Streamlit health return HTTP 200, the deployed file hashes match the
  locally accepted files, `APP_TRACK=development`, and main-account execution
  remains enabled.
- Revision 453 exposed an authenticated-path contract mismatch: its inherited
  data-access layer called the newly user-authorized snapshot service without a
  `user_id`. Revision 454 updates all three account, position, and portfolio
  history callers to pass the signed-in user identity. The image build asserted
  exactly three replacements, compiled the patched module, and the running
  container reports the user-context boundary intact.
- The dedicated `robinhood-main-sync-dev` Container Apps job runs hourly with
  parallelism one, retry limit zero, a 40-minute ceiling, and
  worker image digest
  `sha256:088ad172b57b32bf4c325a646d5eef89d1a9cdd57641c2fcc1774a4aa9a0fb74`.
  The image owns its exact sync command and shares the UI's development-only
  encryption and control-database secrets.
- A separate one-time migration image created the control-plane tables. The
  recurring worker never performs schema DDL.
- The recurring worker then completed normally with zero queued work. This is
  expected because the authenticated Robinhood main-account connection exists
  only in the local environment. Credentials and financial rows were not copied
  into development.

The administrator completed the development-scoped Robinhood connection on
2026-10-03. The initial refresh entered the dedicated queue and the single
worker began materializing portfolio performance and held/list equity history
without another password login. While that first snapshot is pending, both
Portfolio surfaces now treat `queued` and `running` as the same user-facing
`In progress` state and block the old Alpaca fallback. A later refresh may keep
showing the last-good Robinhood snapshot with the same progress state.

That first development refresh completed successfully in 6 minutes 41 seconds.
The development cache now contains one snapshot for two accounts, 25 normalized
positions including four option positions and one futures position, 221
portfolio-performance points, 1,044 option-history points, and 218,468 bounded
equity-history points across 478 symbols. One unsupported Options Watchlist
pagination mode was retained as an isolated collector error; it did not discard
the snapshot or the other 57 group resources. Development revision
`sn-streamlit-ui-dev--0000455` runs the explicit progress-state contract at
image digest
`sha256:9e7682cc62712ad50b8ced843c6a3848854ee510d9d431b65caea30699a90d69`.

## Production Rollout — 2026-10-03

The best/worst Sankey contract was verified against all 38 analytics-ready
development groups before promotion. The current one-year cache selected three
best and three worst groups consistently across return-driver, risk, and
momentum lenses; the three lenses change the constituent explanation, not the
performance-tail membership. Forty-five focused service and renderer checks
pass in isolated suites.

Development revision `sn-streamlit-ui-dev--0000456` and production revision
`sn-streamlit-ui--0000087` now run the identical image digest
`sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5`.
Both revisions are healthy, the production revision receives 100% of production
traffic, and its deployed Portfolio renderer hash matches the accepted local
file.

Production received its own encryption key rather than the development key,
and main-account execution is enabled behind the existing admin, review-ticket,
and live revalidation boundaries. The production-only
`robinhood-main-sync-prod` job runs hourly with one replica, one item
per execution, no platform retries, and a 40-minute ceiling. Its image digest is
`sha256:979b06ffd1a82919488ab38ac873f26d6720380ba40d56d6f1afb4561f1f4f75`.
The first empty-queue probe completed with zero enqueued and zero processed work,
so no Robinhood request occurred during worker verification.

The initial production rollout intentionally left production unlinked. The
explicitly approved ownership cutover on 2026-10-07 supersedes that initial
state; the current state is recorded below.

## Connection and Prefetch Audit — 2026-10-07

The development scheduler was parked before credential movement. The existing
session was decrypted only in memory, re-encrypted with the production key,
and inserted as a production-scoped connection. One current snapshot, 219,849
series points, and 64 resource rows were copied to production in the same
controlled handoff, after which the portfolio link moved to the production
connection. No password login or device challenge occurred.

One controlled production `accounts` read returned both expected accounts.
Only after that read succeeded were all three development credential columns
cleared and the development connection marked disconnected. Production now has
one active connection; development has zero token-bearing connection rows.

Production runs `robinhood-main-sync-prod` hourly at `0 * * * *` with one
replica, one item, no platform retries, and the production secret identity.
Development is parked at `0 0 1 1 *`. Both jobs use environment-neutral worker
digest
`sha256:2a8e36cdb6d9fc84e2d6fe568d3f6ac2d03f267c8634977e831e0be35c1a16c3`;
`APP_TRACK` selects the environment. Freshness now uses cadence buckets and
blocks a second request in the same bucket, avoiding the every-other-hour skip
caused by comparing an exact one-hour interval to refresh completion time.

Post-deployment no-work executions succeeded in both environments without a
Robinhood call. The current production bucket is correctly fresh and the next
hour bucket resolves exactly one due portfolio. An exact-value security audit
found no connection token, token ciphertext, or encryption key in the current
workspace, any Git revision, Azure resource definitions, 14 days of centralized
logs, recent live logs, or 24 database tables outside the encrypted credential
columns.
