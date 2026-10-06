# Robinhood Local Data Plane

Date: 2026-10-02

Status: implemented and accepted locally; no deployment authorized

## Product Contract

Robinhood is an intermittent ingestion source, not a dashboard backend. The
application-owned database is the source for every Robinhood chart, holding,
option-history view, list, screener, and analytical table. A page render never
opens a Robinhood session.

Every successful sync appends or upserts observations by stable local identity
and timestamp. A partial or failed sync preserves all previously materialized
data. The UI always exposes the newest successful local state and its source
time.

Trade-critical reads are the only exception to cached data. Reviewing and
placing an order must re-read eligibility, available quantity, open orders,
quotes, and collars from Robinhood and fail closed when Robinhood is
unavailable. Those calls belong to the explicit execution service, not the
dashboard data path.

## Separation From Alpaca

Robinhood has its own data plane:

- `services.robinhood_main` owns authentication, the rate-governed provider
  client, redaction, and execution policy;
- `services.robinhood_main_store` owns only normalized Robinhood materialized
  data and refresh requests;
- `services.robinhood_main_sync` owns collection, incremental merge, refresh
  leases, and preload policy;
- a Robinhood sync command/job consumes refresh requests outside Streamlit;
- the Portfolio surface reads a provider-specific Robinhood view model; and
- Alpaca loaders, Alpaca caches, pipeline datasets, and Alpaca refresh flags do
  not participate in the Robinhood path.

The existing generic brokerage store retains only cross-provider control-plane
state: encrypted credentials, connection ownership, provider linkage, audit
events, and atomically consumed order tickets.

## Dedicated Storage

### Current snapshot

`robinhood_main_snapshots` contains the newest normalized summary, holdings,
redacted account choices, capability state, and collector health for one
portfolio and environment. It contains no raw account number, token,
instrument URL, list id, or contract id.

### Append-only series

`robinhood_main_series_points` stores observations keyed by portfolio,
environment, series kind, opaque series key, and timestamp. Re-reading an
overlapping provider window updates that timestamp; it never replaces older
points outside the returned window.

Series kinds include:

- combined portfolio value and benchmark closes;
- redacted per-account portfolio value;
- locally sampled current portfolio value;
- equity close history for the union of list constituents; and
- option contract history for open legs when supported.

### Durable resources

`robinhood_main_resources` stores redacted, independently refreshed resources:

- list/screener catalog;
- list membership by opaque list key;
- saved screener definitions and, only when the source exposes them, results;
- symbol quote summaries used for list member tables;
- open-option history metadata; and
- sync capability and source-shape metadata.

Each row has `source_updated_at`, `refresh_after`, and the last successful
payload. A failed refresh records health separately and does not destroy that
payload.

### Refresh queue

`robinhood_main_refresh_requests` is a small leased queue. Streamlit may enqueue
or coalesce a request, but cannot consume it. A separate sync worker claims one
request for one connection, serializes all Robinhood calls, and records the
result. This makes it structurally impossible for chart rendering to fan out
into broker calls.

## Incremental Merge Rules

1. Normalize timestamps to UTC and values to finite JSON before persistence.
2. Upsert overlapping time-series observations by local series identity and
   timestamp.
3. Retain points absent from a later short or partial response.
4. Replace current holdings only when the holdings collector succeeds; retain
   the previous section and mark it stale when that collector fails.
5. Update resource membership atomically after the complete list response is
   normalized.
6. Calculate derived portfolio and group analytics from local points only.
7. Keep raw provider identifiers in memory only for the duration of a sync or
   owner-authorized execution action.
8. Union current equity holdings with bounded list/screener symbols before
   deciding historical coverage; open positions are never optional analytics.
9. Fingerprint the required held-symbol set. While the catalog history marker
   is fresh, fetch only held symbols with missing coverage instead of replaying
   the 471-symbol catalog backfill.

## Supported Lookbacks

The modern Robinhood portfolio performance route exposes `day`, `week`,
`month`, `3month`, `ytd`, `year`, and `all`. The local UI exposes:

- 1D
- 1W
- 1M
- 3M
- YTD
- 1Y
- 5Y
- All

Five-year is cut from the locally retained all-time portfolio curve. Equity
historicals used for list analytics are acquired once at the five-year/week
grain and all shorter group lookbacks are local slices. The UI does not make
one broker request per chart or per lookback.

## Polite Preload And Refresh Policy

One worker may read one Robinhood connection at a time. The provider client
uses one stable application user agent, one process-wide request lock, a
default one-second minimum read interval, bounded GET-only retry, and the
shared provider-directed 429 cooldown. Mutations are never retried.

The initial preload is staged:

1. accounts, current holdings, options, futures, and the all-time portfolio
   curve;
2. list/screener catalog and list membership;
3. an inventory of group sizes and asset-compatible symbols;
4. one de-duplicated union of current equity holdings and equity symbols from
   analytically bounded groups;
5. batched five-year equity history for that union; and
6. history for the small set of currently open option legs.

It never requests the same symbol history separately for each list.
Groups larger than `ROBINHOOD_MAIN_ANALYTICS_GROUP_MAX_MEMBERS` (100 by
default, bounded to 25–250) remain visible as metadata but do not trigger a
historical backfill. Slash-prefixed futures contracts, crypto pairs, and
symbols that do not resolve through equity quotes are excluded from equity
history batches. Each successful history batch is committed independently, so
a later provider failure preserves earlier progress. Recent history remains
daily; older history is compacted to weekly points.

Normal cadence:

| Resource | Target cadence | Reason |
| --- | --- | --- |
| Current holdings and live summaries | 15 minutes during market/extended hours; 2 hours otherwise | Useful portfolio freshness without using Robinhood as a quote stream |
| Full portfolio performance curve | Every 20 hours | Local samples fill intraday continuity; long history rarely changes |
| Option/futures current state | 15 minutes while relevant markets are active; 2 hours otherwise | Position risk changes, but remains bounded by the core sync |
| User lists and membership | Daily | User-edited membership can change more often |
| Curated list/screener metadata and membership | Weekly | Broad provider catalogs are low-frequency metadata |
| Five-year bounded-union symbol history | Every six days | One batched overlap captures corrections and the newest bars without treating Robinhood as a market-data feed |
| Open-option history | Daily and when the open-leg set changes | Small, contract-scoped history |

Manual refresh requests advance only resources that are stale unless the sync
command is explicitly run in repair mode. A 429, authorization failure, or
repeated transport failure ends the run and defers the remaining work; it does
not start concurrent recovery traffic.

The worker and application must resolve the same environment-scoped credential
encryption key. This is an operational identity invariant, not a retryable
Robinhood error: a worker that cannot decrypt the connection must stop before a
provider call. The 2026-10-02 local incremental backfill exposed this mismatch;
no Robinhood request was made and the last-good cache remained intact.

## Group Analytics Surface

Lists and screeners are treated as comparable portfolios rather than isolated
tables.

For a selected lookback, the local analytics layer produces:

- one normalized multi-line chart for all selected groups;
- group return, breadth, volatility, downside deviation, tail loss, maximum and
  current drawdown, member dispersion, contribution concentration, average
  correlation, first-component share, effective risk bets, diversification
  ratio, and momentum regime;
- a risk/return/breadth map and clustered group-return correlation view;
- a selectable Sankey from group through return direction, volatility
  amplification, or momentum regime to the largest constituent contributors;
  flow width follows the selected contribution measure and node counts remain
  bounded by materiality; and
- an editable member table for basket construction using the same locally
  materialized constituents when the viewer is an active admin. Members see the
  same constituents as a non-editable table.

The equity holdings read model uses the same local history to produce selected
lookback return, trailing volatility, downside risk, drawdown, expected
shortfall, SPY beta, robust movement z-score, covariance risk contribution,
momentum regime, clustered correlation, and principal-component loadings. This
keeps every analytical chart reproducible from the local database and makes
history coverage an explicit metric.

Screener definitions without provider result rows remain definitions. The
system does not fabricate constituents or backtests. When a reliable result
feed becomes available, it enters through the same membership and series
contracts.

## Security Boundary

- Credentials remain encrypted in the generic brokerage connection store.
- The data plane stores only masked labels, symbols, and connection-bound
  opaque hashes.
- Refresh requests contain local UUIDs, never credentials or provider ids.
- The worker resolves credentials only after claiming an owner/environment
  scoped request.
- Snapshot, option-history, and group-detail readers require an active
  portfolio membership even though they touch local data only. Active members
  receive the complete cached portfolio rather than an ownership projection.
  Connection changes, refresh, trading-account discovery, review, and placement
  require active admin membership.
- Page processes cannot import or instantiate the Robinhood transport through
  the read-model API.
- Execution remains admin-authorized, payload-bound, single-use, and separate
  from background sync. Admin membership is checked both when reviewing and
  when consuming the confirmation ticket so revoked authority cannot be reused.

## Local Acceptance Before Any Deployment

- [x] Existing persisted portfolio history survives an overlapping refresh and
  gains new observations.
- [x] A failed collector leaves its prior successful local data visible and marks
  only that section stale.
- [x] Portfolio and group charts render every supported lookback without a
  Robinhood call.
- [x] One initial preload de-duplicates group symbols and respects the request
  governor.
- [x] Real-data inspection confirms the combined group chart and contributor Sankey
  are genuinely useful, readable, and decision-oriented.
- [x] No real order is submitted.
- [x] Development deployment occurred only after explicit user approval and
  excluded unrelated working-tree changes.
- [ ] Production deployment requires separate explicit user approval.

The accepted local snapshot contains two masked accounts, 31 normalized
holdings across equities, options, and futures, a combined portfolio curve from
2019 onward, four open-option histories, 58 catalog entries, and 471 locally
materialized equity histories. The latest clean sync reported no stale sections
or collector errors. The dashboard read path was inspected separately from the
worker and contains no Robinhood transport construction.

## Development Runtime

Development runs UI revision `sn-streamlit-ui-dev--0000454` and the dedicated
scheduled job `robinhood-main-sync-dev`. The job owns all provider reads, runs
with parallelism one and no platform-level retry, and shares the UI's
development encryption identity. Its 15-minute trigger calls the internal
freshness scheduler; it does not imply every resource is fetched every 15
minutes.

Schema bootstrap ran as a one-time migration execution. The recurring image is
restored to the non-DDL sync command. The first recurring execution completed
with no queued portfolios because the real connection is currently local-only.
No credential or cached financial data was copied between environments. Once an
admin creates the development connection, the same worker will materialize the
development snapshot and series without making Streamlit a broker client.

The first analytics revision inherited three older data-access callers that
omitted the newly required `user_id`, so authenticated Portfolio loading raised
a `TypeError` even though process health stayed green. Revision 454 binds the
signed-in identity into account, position, and portfolio-history reads. The
service remains fail-closed on active membership; the compatibility repair did
not make `user_id` optional or weaken authorization.
