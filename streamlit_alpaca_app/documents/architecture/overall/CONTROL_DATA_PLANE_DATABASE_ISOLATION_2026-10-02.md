# Control-Plane and Data-Plane Database Isolation

Date: 2026-10-02

Status: implemented and live in development and production on 2026-10-02.

## Decision

Separate account state from analytical job state at the PostgreSQL
server boundary. More connections provide immediate headroom, but only isolation keeps a
runaway research workload from taking sign-in, sessions, invitations, and Admin offline.

```text
Browsers / Streamlit / BFF / product API
                    |
                    v
          Control-plane PostgreSQL
          users, credentials, sessions
          memberships, invites, resets
          admin/audit state, API keys
          user-owned brokerage metadata

            Data-plane PostgreSQL
          datasets, job state, Zopedia
          agent run queue and event journal
          analytical and materialized data
```

The initial cut keeps agent-run coordination with the research jobs on the data plane.
That provides the requested auth/admin isolation without introducing a cross-database
transaction in the live agent harness. If agent coordination later needs independent
availability, it can move behind a dedicated control repository with idempotent handoff;
that is a separate migration, not part of this incident response.

## Why Capacity Alone Is Insufficient

The current Central US server is `Standard_B1ms`: one vCore, 2 GiB memory, 50 total
connections, and 35 user connections after Azure reservations. On 2026-10-02 the
scheduled `news-ingest-and-features` process drove active connections to 44–47, and
PostgreSQL rejected auth/API connections because the remaining slots were reserved.

`Standard_B2s` provides useful emergency headroom, but an unbounded job can eventually
consume a larger shared pool too. Raising `max_connections` on the 2 GiB B1ms without
adding memory is not the safe option; every connection consumes memory, and Microsoft
recommends pooling or scaling instead. Built-in Azure PgBouncer is not supported on the
Burstable tier, so it is not available merely by resizing from B1ms to B2s.

## Current Retail Cost Envelope

The figures below use the Azure Retail Prices API for Central US on 2026-10-02, 730
hours/month, and the existing 32 GiB storage allocation at $0.13/GiB-month. They exclude
tax, negotiated discounts, and backup storage beyond the included allocation.

| Shape | Compute | Documented user connections | Approx. monthly total | Change from current |
| --- | ---: | ---: | ---: | ---: |
| B1ms, current | $14.02 | 35 | $18.18 | — |
| B2s | $56.09 | 414 | $60.25 | +$42.07 |
| B2ms | $112.19 | 844 | $116.35 | +$98.17 |
| New B1ms control server, existing server remains data | $14.02 plus $4.16 storage | 35 isolated | +$18.18 | +$18.18 |

Changing an existing server's compute product does not automatically update its current
`max_connections` value. A resize plan must explicitly set the intended value and account
for the required server restart and brief downtime.

## Staged Rollout

### Stage 0 — Protect availability now

- Keep the source-level connection leak fix and bounded statement/lock/idle transaction
  timeouts as the primary correction.
- Put explicit connection budgets on every job and fail closed when a budget is exhausted.
- Alert before reserved slots are reached: warning at 24 user connections and critical at
  30 on B1ms.
- Consider B2s as temporary headroom only after accepting the recurring cost and restart.
- Do not increase B1ms `max_connections` beyond its memory-supported default.

### Stage 1 — Create the control store

- Provision a separate production PostgreSQL Flexible Server with backups, monitoring,
  TLS, an explicit firewall, and a dedicated Key Vault connection secret.
- Start at B1ms if observed control-plane concurrency remains comfortably below its
  35-user-connection allowance.
- Create a dedicated UI/API role with access only to `app_access`; it has no data-plane
  privileges and a bounded connection limit.
- Move identity, session, invitation, reset, membership, admin audit, API-key, and
  user-owned brokerage metadata to the control server.

### Stage 2 — Cut over deliberately

- Backfill and reconcile all control tables, including 12 existing users, credential
  hashes, memberships, active sessions, and pending account actions.
- Briefly quiesce control-plane writes, copy the final delta, switch UI/API secrets, and
  validate login, session restoration, Admin users, invitation, and reset before reopening
  writes.
- Keep the data-plane connection string unchanged for scheduled analytical jobs.
- Do not attach the control identity or secret to analytical workers.

### Stage 3 — Prove the isolation boundary

- Saturate or deliberately block the data-plane pool in development.
- Confirm login, session restore, user listing, and invitation creation remain healthy.
- Confirm data-plane failures appear as scoped job/data errors rather than auth failures.
- Confirm public status routes remain allowlisted and never serialize connection strings,
  secret diagnostics, usernames, or exception details.

## Operating recommendation

Use both layers of defense:

1. Keep the control server at B1ms while its measured peak remains well below the
   20-connection application-role cap.
2. Size the data plane from measured analytical load rather than authentication
   availability. B2s remains available as temporary headroom, but it is not a substitute
   for fixing the scheduled job's connection lifecycle.
3. Retire the stale `app_access` copy from the data server after the rollback window, then
   verify that data-plane credentials cannot read current or historical account state.

Capacity is reversible. The control/data boundary is the long-term architecture.

## Live Implementation

### Resources and access

| Boundary | Live resource | Configuration |
| --- | --- | --- |
| Control database | `sn-control-pg3-03130136` | Central US zone 2, PostgreSQL 16, `Standard_B1ms`, 32 GiB, seven-day backup retention |
| Control secret vault | `sn-control-kv-03130136` | RBAC, soft delete, 90-day retention, purge protection |
| Runtime identity | `sn-control-mi-03130136` | `Key Vault Secrets User` only on the control vault; attached only to UI/API apps |
| Runtime database role | `sn_control_app` | Login, 20-connection limit, owner of `app_access`, bounded statement/lock/idle-transaction timeouts |
| Data database | `sn-pg-03130136` | Unchanged pipeline DSN for datasets, scheduled jobs, Zopedia, and agent runs |

The control server contains 20 `app_access` tables and no `public` application tables.
The final transactional copy reconciled 1,577 rows, including all 12 users. The runtime
role owns only `app_access`; it is not a server administrator and receives no credential
for the data server. The SQL bootstrap password was rotated to an unretained value after
the application role was created. Operator maintenance uses the configured Microsoft
Entra administrator.

`CanNotDelete` resource locks protect both the live control server and its vault. They
allow normal updates and scaling but require an explicit reviewed lock removal before
either resource can be deleted.

The existing Container Apps environment was created without a customer VNet, and Azure
does not provide it a fixed outbound address. The control server therefore currently uses
its public endpoint with TLS, one operator-IP rule, and Azure's `0.0.0.0` service rule.
That Azure rule admits connection attempts from other Azure tenants, so the unique random
database credential and least-privilege role remain essential. Moving to a private
endpoint requires a separate VNet-integrated Container Apps environment or controlled NAT
egress; it was not folded into this $18 database change.

The DSN is stored only in the control vault. Each Container App receives it through a
native Key Vault secret reference backed by the control identity. Scheduled jobs neither
receive that identity nor reference the control secret.

### Source and migration boundary

- `services/auth_store.py` prefers `CONTROL_POSTGRES_CONNECTION_STRING`, or the
  `CONTROL_POSTGRES_CONNECTION_STRING_SECRET` resolver for non-Container-Apps runtimes.
- A configured control setting fails closed; it never silently falls back to the data
  database when the control secret cannot be resolved.
- Runtime readiness checks required relations and does not execute DDL. Explicit schema
  creation and data reconciliation use `scripts/migrate_control_store.py`.
- `services.pipeline_store` is unchanged and continues to resolve the data-plane DSN.
- `services.agents.research_runs` remains on `pipeline_store`; agent execution cannot
  consume control-server connections.

### Deployed revisions

| Surface | Revision | Image digest |
| --- | --- | --- |
| Development Streamlit | `sn-streamlit-ui-dev--0000451` | `streamlit-ui@sha256:da3cee410c8d29dbc44069bbff924c194793d4d0747f0614435a7634e12fe4cf` |
| Development API | `sn-api-dev--0000049` | `api@sha256:6e2cd73a734cb51e08963f8bffbfcb568f62872efbf16e95ae0a7646c41aae45` |
| Next.js development API | `sn-api-next-dev--0000002` | `api-next-dev@sha256:eb48a1778971f2e0c39d6ec96ee40cb212c1229c5d508f2c527bb62cefaf8e9c` |
| Production Streamlit | `sn-streamlit-ui--0000086` | `streamlit-ui@sha256:a706a30cb1b1f556cbbe8aefc670bff802c79fac389428bc3147af936561be80` |
| Production API | `sn-api--0000003` | `api@sha256:6e2cd73a734cb51e08963f8bffbfcb568f62872efbf16e95ae0a7646c41aae45` |

The release images are one-file overlays on the exact preceding images, changing only
`services/auth_store.py`. This avoided promoting unrelated worktree changes or changing
the Streamlit presentation.

### Acceptance evidence

- Development and production UI root and health routes returned HTTP 200.
- Development and production `/v1/auth/status` returned HTTP 200 with `available=true`,
  `ready=true`, `has_users=true`, and only the seven public allowlisted fields.
- An invalid production login returned HTTP 401 rather than an outage-shaped 503; its
  synthetic audit row was removed immediately after verification.
- In-container checks on the development, Next.js development, and production APIs
  resolved auth to `sn-control-pg3-03130136`, data to `sn-pg-03130136`, and listed 12 users.
- After cutover, both servers had one observed connection. The stopped 20:05 UTC
  `news-ingest-and-features` execution did not recur during the migration.

### Rollback and retirement

The preceding Container Apps revisions and the original data-server secret remain intact.
Rolling an app image back makes the older code ignore the control setting and use the old
store. The old `app_access` copy is intentionally retained only for the initial rollback
window; it is already stale after cutover and must not become a second writable account
store. After the soak period, remove it from the data server through a separately reviewed,
recoverable retirement change so analytical credentials no longer retain historical
account data.
