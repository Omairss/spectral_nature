# Robinhood Main-Account Adapter

Date: 2026-10-02

Status: local data plane and analytics deployed to development; development account connection pending

## Intent

Use the user's main Robinhood investing accounts for the Portfolio and Portfolio
Performance pages, while keeping the hosted Robinhood MCP integration for the
separate agent-enabled account. Add batch stop-loss placement for main-account
positions without adding a second approval workflow.

## Source Boundary

The authentication and endpoint shapes were traced against current pinned
revisions of `robin_stocks_v2`, `robinhood-for-agents`, `pyhood`, and
`robinhood-cli-mcp`; exact commits are recorded in the portfolio platform plan.
Spectral Nature does not import those packages. Their source is evidence for
the private API boundary, not an application dependency.

`services.robinhood_main` therefore owns a small, isolated client with one
`requests.Session` per logical operation, trusted Robinhood origins, bounded
timeouts, GET-only retries, a process-wide request governor, and
application-owned persistence. The adapter is deliberately contained because
it uses Robinhood's app-session API rather than the hosted MCP account boundary.

## Authentication

1. An admin enters the Robinhood username and password on the Portfolio page.
2. The adapter requests a one-day app access token with a cryptographically
   random device token.
3. If Robinhood requires device verification, the page handles app approval,
   SMS, or email verification and then retries the same login.
4. Username and password exist only in the Streamlit session while that
   verification is pending. They are never written to Postgres, logs, tickets,
   snapshots, or audit rows.
5. Access, refresh, and device tokens are encrypted with the environment's
   Robinhood Fernet key before persistence. Expired sessions return directly to
   the connection form and reuse the encrypted device token.

The main-account connection is user- and environment-owned. A portfolio may
display its redacted snapshot to authorized investors, but only the admin who
owns the connection can load live positions, review stop orders, or execute
them.

## Portfolio Routing

The first successful refresh is atomic at the product boundary:

1. load every active investing account;
2. aggregate account equity, cash, buying power, positions, five-year daily
   equity history, and the existing benchmark set;
3. persist one redacted portfolio snapshot; and
4. point the portfolio's existing `brokerage_source` and
   `brokerage_account_ref` fields at that connection.

`data_access.layer` resolves account, positions, portfolio history, and
performance from that snapshot for both admins and investors. Existing
ownership projection remains in force for investors. Page rendering never
starts a hidden Robinhood request; only the explicit connect and refresh
actions contact Robinhood.

The snapshot stores no Robinhood account number. It contains the shared
equity-by-symbol frame plus redacted, asset-aware equity, option, and futures
holdings. Live execution rows use connection-bound SHA-256 keys and masked
account labels, so the browser and order ticket do not receive raw account,
option, futures-account, list, or instrument identifiers.

## Batch Stop Losses

The Portfolio page exposes one compact workflow:

1. `Load positions` fetches current positions, quotes, and broker-reserved
   shares.
2. The admin chooses positions, share quantities, and stop prices.
3. `Review N stop orders` re-fetches live holdings and rejects a quantity above
   unreserved shares or a stop at/above the current price.
4. `Place N stop orders` atomically consumes the unchanged, expiring batch
   ticket and revalidates the same live facts before submitting.

There is one application confirmation, not a committee or approval queue. The
ticket is bound to the user, environment, connection, exact canonical batch
hash, and a 30–600 second expiry. Each child order derives a deterministic
Robinhood `ref_id` from the batch reference and opaque position key.

Explicit broker rejections are returned per order. Once any POST has begun, a
transport failure marks the batch `unknown` and stops submission; the app does
not retry with a new identity. A consumed ticket cannot be reused.

## Durable State

The existing `app_access` brokerage schema gains:

- `device_token_ciphertext` on `brokerage_connections`; and
- `brokerage_portfolio_snapshots`, keyed by portfolio and environment, with
  redacted account summary, positions, timeseries, connection identity, and
  source timestamp.

The adapter reuses the existing audit-event and atomically consumed
order-ticket tables. Disconnecting erases encrypted credentials, expires open
tickets, and routes the portfolio back to Alpaca.

## Runtime Controls

- `ROBINHOOD_MAIN_EXECUTION_ENABLED` defaults off and controls only main-account
  order placement.
- `ROBINHOOD_MAIN_USER_AGENT` is a stable application identity; it is not
  rotated to impersonate a browser.
- Read and write pacing default to 350 ms and 2.5 seconds. A shared 429 cooldown
  prevents one collector from continuing to pressure the account.
- `ROBINHOOD_ORDER_CONFIRMATION_TTL_SECONDS` controls both Robinhood ticket
  flows within the bounded range.
- `ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY` remains the environment-specific
  encryption root for both Robinhood transports.
- Production is unchanged until explicitly promoted.

## Local Acceptance

Completed locally:

- authentication request and device-token contract;
- encrypted token persistence with masked account metadata;
- aggregation across multiple main accounts with no account numbers in the
  durable snapshot;
- connection-owner enforcement at the execution service boundary;
- exact-payload batch review and atomic one-time confirmation;
- deterministic child order references and fail-closed changed-ticket logic;
- Portfolio and Portfolio Performance routing through the main-account
  snapshot; and
- rendered batch UI with one review action and one placement action.

No real order is part of development verification.

On 2026-10-02 a real local authentication completed and enumerated two active
margin accounts. The persisted refresh and browser acceptance then showed 26
equities, four option positions with Greek exposure, one verified `MCLX26`
futures position, 220 performance points across both accounts, and 58
Robinhood lists/screeners with no collector errors. The snapshot audit found
only masked account labels and opaque execution keys, no raw identity fields,
and no non-finite JSON values.

That acceptance also corrected two source assumptions. Ceres returned
prediction-market positions beside the futures account, so the collector now
requires `accountType=FUTURES` and a resolvable futures market contract. The
actual futures row uses broker signed quantity, average trade price, P/L cost
basis, bid/ask midpoint, expiry, and contract multiplier. Robinhood's account
performance summaries sometimes omit a starting value while retaining a
cash-flow-adjusted return; the adapter derives the missing baseline from the
current value and that broker return instead of displaying zero or changing
the return semantics.

The expanded implementation adds independent equity, option, futures,
performance, list, and screener collectors; current option Greeks and supported
week/day option price history; current version-7 JSON stop orders; and
payload-bound batch buy/short tickets with exact-symbol, margin-account,
shortability, live-quote, and duplicate-open-order checks.

The focused service and rendered-view suite passes 26 checks. No real order was
submitted during local acceptance.

## Cache And Availability Boundary

Normal Portfolio and Portfolio Performance renders read the durable Postgres
snapshot and make no hidden Robinhood request. A refresh writes only after the
normalized snapshot succeeds, so the last successful snapshot remains usable
when a later connection attempt fails. GET requests are serialized, paced,
retried once for transport/5xx failures, and share a process-wide 429 cooldown.

This boundary is not complete yet. Option history, selected-list membership and
performance, and trading-account discovery are stored only in Streamlit session
state after a live request. A new session or replica therefore repeats those
calls, and a full manual refresh refetches every collector rather than reusing
fresh independent sections. These read-only resources need durable per-resource
timestamps and stale-while-revalidate fallback before the integration can be
described as fully cached or designed around a flaky connection.

Trade-critical facts are deliberately outside that cache: current eligibility,
available quantity, open orders, quotes/collars, review, and final pre-submit
validation must be read live. If Robinhood is unavailable, execution fails
closed while the last read-only snapshot remains visible.

## Development Verification

Deployed 2026-10-02 as an overlay on the previously verified Robinhood MCP
development image, so unrelated working-tree changes were excluded.

- Development revision: `sn-streamlit-ui-dev--0000448`.
- Image digest:
  `sha256:e330ef765976025a7219489f19f4781d8259a4dd2e11dd0feec266d946821828`.
- Root and Streamlit health endpoints return HTTP 200.
- The running container imports the main-account adapter and Portfolio view,
  creates the snapshot/schema changes, and reports
  `ROBINHOOD_MAIN_EXECUTION_ENABLED=true`.
- The rollout was first started with main-account execution disabled. The
  switch was enabled only after the in-container schema and import probe
  succeeded.
- Production remains on revision `sn-streamlit-ui--0000085` and was not
  changed.

The later local-data-plane and scientific-analytics rollout is development UI
revision `sn-streamlit-ui-dev--0000453`, paired with the isolated
`robinhood-main-sync-dev` scheduled worker. The control schema is installed and
both runtimes use the same development encryption identity. The existing real
main-account connection remains local-only, so development requires one admin
connection before its first cached snapshot can be materialized. No credentials
or brokerage rows were copied across environments, and production remains
unchanged.
