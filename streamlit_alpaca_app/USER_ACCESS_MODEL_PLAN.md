# User Access + Portfolio Ownership Plan

## Goal

Move the app from a single shared dashboard login to named investor accounts.

Each user should:

- have a profile (`email`, name, status, role)
- log in with their own credentials
- see only their ownership share of the portfolio (`0.0` to `1.0`)
- have portfolio summary, positions, equity history, and dollar P/L mapped to that share
- be modeled as a first-class account that can later own content, requests, comments, and workflow actions

## Current State

Today the app is still single-user from an access-control perspective:

- `app.py` uses one shared username/password login gate and an in-memory cookie-backed session registry.
- `services/config.py` loads one Alpaca account from env/Key Vault.
- `data_access/layer.py` resolves account, positions, and portfolio performance with no viewer context.
- `compute/portfolio.py` builds one aggregate portfolio equity series for the full Alpaca account.

That means the app already has a login checkpoint, but it does not yet have:

- a user directory
- per-user sessions
- portfolio ownership assignments
- ownership-aware portfolio projections
- a durable model for user-authored content such as posts/comments
- a secure workflow for contribution and withdrawal requests

## Recommended Scope

### MVP

- One underlying Alpaca account
- Many app users
- Read-only investor access
- One active ownership percentage per user for the portfolio
- Ownership percentages across active users must sum to `<= 100%`

### Near-Term Future Scope To Design For Now

- user-authored posts, comments, and activity history
- investor contribution requests
- investor withdrawal requests
- admin approval and settlement workflow for money movement
- ownership updates that may lag behind user requests until funds actually settle

### Important Assumption

This plan treats ownership as an economic share of one master portfolio, not as separate brokerage sub-accounts.

That means:

- dollar values scale by ownership share
- benchmark prices do not change
- return-based metrics only change when ownership percentages change over time

If a user always owns `20%`, then their dollar equity is `20%` of the master portfolio, but their annual return percentage is the same as the master portfolio's return percentage.

Also, requested capital changes should not immediately change ownership. A request to invest more or withdraw should remain pending until it is reviewed and actually settled.

## Data Model

Use the existing Postgres-backed application environment, but put auth/access tables in a separate schema such as `app_access`.

### 1. `portfolios`

Represents a logical investable portfolio.

Suggested columns:

- `id UUID PRIMARY KEY`
- `slug TEXT UNIQUE NOT NULL`
- `name TEXT NOT NULL`
- `brokerage_source TEXT NOT NULL` (`alpaca`)
- `brokerage_account_ref TEXT NULL`
- `status TEXT NOT NULL` (`active`, `inactive`)
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

For MVP, this will likely contain one row.

### 2. `users`

Suggested columns:

- `id UUID PRIMARY KEY`
- `email CITEXT UNIQUE NOT NULL`
- `first_name TEXT NOT NULL`
- `last_name TEXT NOT NULL`
- `display_name TEXT NULL`
- `status TEXT NOT NULL` (`invited`, `active`, `disabled`)
- `role TEXT NOT NULL` (`admin`, `investor`)
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`
- `last_login_at TIMESTAMPTZ NULL`

### 3. `user_credentials`

Recommended for MVP if we keep auth in-app.

Suggested columns:

- `user_id UUID PRIMARY KEY REFERENCES users(id)`
- `password_hash TEXT NOT NULL`
- `password_set_at TIMESTAMPTZ NOT NULL`
- `must_rotate_password BOOLEAN NOT NULL DEFAULT FALSE`
- `failed_login_count INTEGER NOT NULL DEFAULT 0`
- `locked_until TIMESTAMPTZ NULL`

Use Argon2 or bcrypt. Never store plain-text passwords in env once this is introduced.

### 4. `user_invites`

Recommended for invite-only onboarding.

Suggested columns:

- `id UUID PRIMARY KEY`
- `email CITEXT NOT NULL`
- `role TEXT NOT NULL`
- `portfolio_id UUID NULL REFERENCES portfolios(id)`
- `proposed_share_fraction NUMERIC(9,6) NULL`
- `invite_token_hash TEXT NOT NULL`
- `status TEXT NOT NULL` (`pending`, `accepted`, `expired`, `revoked`)
- `expires_at TIMESTAMPTZ NOT NULL`
- `accepted_by_user_id UUID NULL REFERENCES users(id)`
- `created_by UUID NULL REFERENCES users(id)`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

This supports admin-created accounts without exposing open self-signup on day one.

### 5. `password_reset_tokens`

Suggested columns:

- `id UUID PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `reset_token_hash TEXT NOT NULL`
- `status TEXT NOT NULL` (`pending`, `used`, `expired`, `revoked`)
- `expires_at TIMESTAMPTZ NOT NULL`
- `used_at TIMESTAMPTZ NULL`
- `requested_ip TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

Rules:

- store only a hash of the reset token
- single use only
- short TTL such as 15 to 30 minutes
- revoke older unused reset tokens when a new one is issued

### 6. `portfolio_memberships`

This is the key authorization and ownership table.

Suggested columns:

- `id UUID PRIMARY KEY`
- `portfolio_id UUID NOT NULL REFERENCES portfolios(id)`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `role TEXT NOT NULL` (`owner`, `investor`, `viewer`, `admin`)
- `share_fraction NUMERIC(9,6) NOT NULL`
- `effective_from TIMESTAMPTZ NOT NULL`
- `effective_to TIMESTAMPTZ NULL`
- `can_view_full_portfolio BOOLEAN NOT NULL DEFAULT FALSE`
- `created_by UUID NULL REFERENCES users(id)`
- `notes TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

Constraints:

- `share_fraction >= 0 AND share_fraction <= 1`
- only one active membership row per `user_id + portfolio_id + as_of_time`
- active shares for a portfolio should sum to `<= 1`

### 7. `user_sessions`

Suggested columns:

- `id UUID PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `session_token_hash TEXT NOT NULL`
- `expires_at TIMESTAMPTZ NOT NULL`
- `revoked_at TIMESTAMPTZ NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `last_seen_at TIMESTAMPTZ NULL`
- `user_agent TEXT NULL`
- `ip_address TEXT NULL`

Store only a hash of the session token server-side.

### 8. Optional: `portfolio_ownership_daily`

Recommended if ownership can change over time.

Suggested columns:

- `portfolio_id UUID NOT NULL`
- `user_id UUID NOT NULL`
- `as_of_date DATE NOT NULL`
- `share_fraction NUMERIC(9,6) NOT NULL`

This can be materialized from `portfolio_memberships` and makes historical performance joins much easier and safer.

### 9. `capital_requests`

Tracks user intent to contribute or withdraw capital before anything is finalized.

Suggested columns:

- `id UUID PRIMARY KEY`
- `portfolio_id UUID NOT NULL REFERENCES portfolios(id)`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `request_type TEXT NOT NULL` (`contribution`, `withdrawal`)
- `amount NUMERIC(18,2) NOT NULL`
- `currency TEXT NOT NULL DEFAULT 'USD'`
- `status TEXT NOT NULL` (`requested`, `under_review`, `approved`, `rejected`, `cancelled`, `processing`, `settled`)
- `requested_at TIMESTAMPTZ NOT NULL`
- `reviewed_by UUID NULL REFERENCES users(id)`
- `reviewed_at TIMESTAMPTZ NULL`
- `settled_at TIMESTAMPTZ NULL`
- `external_transfer_ref TEXT NULL`
- `notes TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

Important rule:

- a `capital_requests` row is not itself the source of truth for ownership percentage

### 10. `capital_events`

Represents the actual settled cash movement after review/operations complete.

Suggested columns:

- `id UUID PRIMARY KEY`
- `portfolio_id UUID NOT NULL REFERENCES portfolios(id)`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `capital_request_id UUID NULL REFERENCES capital_requests(id)`
- `event_type TEXT NOT NULL` (`contribution_settled`, `withdrawal_settled`, `manual_adjustment`)
- `amount NUMERIC(18,2) NOT NULL`
- `effective_at TIMESTAMPTZ NOT NULL`
- `created_by UUID NULL REFERENCES users(id)`
- `notes TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

This is the better audit anchor for downstream ownership recalculation.

### 11. Optional: `content_posts`

Recommended if the app may later include investor updates, commentary, or shared notes.

Suggested columns:

- `id UUID PRIMARY KEY`
- `author_user_id UUID NOT NULL REFERENCES users(id)`
- `portfolio_id UUID NULL REFERENCES portfolios(id)`
- `title TEXT NULL`
- `body TEXT NOT NULL`
- `visibility TEXT NOT NULL` (`private`, `portfolio`, `admin_only`)
- `status TEXT NOT NULL` (`active`, `deleted`, `hidden`)
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

### 12. Optional: `content_comments`

Suggested columns:

- `id UUID PRIMARY KEY`
- `post_id UUID NOT NULL REFERENCES content_posts(id)`
- `author_user_id UUID NOT NULL REFERENCES users(id)`
- `parent_comment_id UUID NULL REFERENCES content_comments(id)`
- `body TEXT NOT NULL`
- `status TEXT NOT NULL` (`active`, `deleted`, `hidden`)
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

The important modeling point is that `users` should be durable principals now, so future content is naturally owned by user identity instead of bolted on later.

## Auth Model

### Recommended MVP

Keep authentication in the app for now, but replace the single shared credential with DB-backed users and hashed passwords.

Why this is the best near-term fit:

- the app already has a custom login gate in `app.py`
- it avoids blocking on a full external identity rollout
- it keeps the first version focused on investor access and ownership logic

### Future Upgrade

Keep the auth service behind an interface so credentials can later move to Microsoft Entra, Auth0, or another external IdP without changing the ownership tables.

For any future money-movement workflow, strongly consider step-up auth for sensitive actions:

- recent-password confirmation
- email verification link or one-time code
- MFA if the product expands beyond a small internal/invite-only group

## Current Implementation Details

These notes reflect the current implemented version in the app code.

### Login identifier storage

Database auth uses `email` as the login identifier.

Stored in:

- `app_access.users.email`

There is no separate per-user username field in the current implementation.

### Password storage

User passwords are not stored in plain text in the database.

Stored in:

- `app_access.user_credentials.password_hash`

Current hashing approach:

- `services/auth_service.py` uses Python `hashlib.scrypt(...)`
- the stored format is:
  - `scrypt$<n>$<r>$<p>$<salt_b64>$<derived_key_b64>`

That means:

- each password gets a random salt
- the DB stores only the scrypt hash record
- password verification recomputes and compares the derived key

### Bootstrap admin credential storage

The first admin bootstrap credentials are currently sourced from Key Vault secrets, not from source code.

Current secret names:

- `dashboard-bootstrap-admin-email`
- `dashboard-bootstrap-admin-password`

Behavior:

- on startup, if database auth is enabled and no users exist yet, the app reads these secrets
- it creates the first admin user
- it stores the hashed password in Postgres

Important security note:

- after the first admin account is created, the bootstrap password secret should be rotated or removed so it is not left as a standing credential longer than needed

### Legacy shared login credential storage

The old shared dashboard login still exists as a fallback path when `DASHBOARD_AUTH_MODE=legacy`.

Current secret names:

- `dashboard-auth-username`
- `dashboard-auth-password`

Those are not the preferred long-term path and are not used when `DASHBOARD_AUTH_MODE=database`.

### Session storage

Database-backed auth stores sessions in Postgres.

Stored in:

- `app_access.user_sessions`

Important detail:

- only `session_token_hash` is stored server-side
- the browser cookie contains the raw opaque token
- the server hashes the cookie token and compares it to the stored hash

### Invite token generation

Invite tokens are generated in:

- `services/auth_service.py`

Current implementation:

- `generate_token()` uses `secrets.token_urlsafe(32)`
- the raw token is placed into the invite URL as `?invite_token=...`
- the DB does not store the raw token
- the DB stores only `sha256(raw_token)` in:
  - `app_access.user_invites.invite_token_hash`

Validation flow:

1. generate a cryptographically random raw token
2. send or display the raw link to the invited user
3. hash the raw token with SHA-256
4. store only the hash in Postgres
5. when the user opens the link, hash the presented token and compare against the stored hash

### Password reset token generation

Password reset tokens use the same pattern as invite tokens.

Stored in:

- `app_access.password_reset_tokens.reset_token_hash`

Current implementation:

- raw token generated with `secrets.token_urlsafe(32)`
- only SHA-256 hash stored in DB
- reset tokens are single use
- reset tokens are short lived
- older pending reset tokens are revoked when a new one is issued

## Account Lifecycle

### Recommended MVP stance

Use invite-only account creation.

That means:

- users do not freely self-register
- an admin creates an invite for a specific email
- the invite can optionally include the initial portfolio membership and share percentage
- the invited user activates their account by setting a password

This is the safest starting point for a finance-adjacent product.

### Create account flow

Recommended flow:

1. admin creates an invite for `email`, `role`, and optionally initial `portfolio_id` and `share_fraction`
2. system creates a `user_invites` row with a short-lived token
3. user opens the invite link
4. if the token is valid, user sets password and completes profile basics
5. system creates `users` and `user_credentials` records or activates the pre-created user
6. system marks invite as accepted
7. system creates the initial `portfolio_memberships` row if it was part of the invite

Security notes:

- invite tokens should be random, long, hashed at rest, and single use
- invites should expire automatically
- do not reveal whether an uninvited email is eligible for access

### Forgot password flow

Yes, this should be explicitly defined.

Recommended flow:

1. user enters email on `Forgot password`
2. system always returns a generic response such as `If an account exists, a reset link has been sent`
3. if the user exists and is active, create a `password_reset_tokens` row
4. email a short-lived reset link
5. user opens link and sets a new password
6. token is marked used
7. all existing sessions are revoked or at least all high-risk sessions are revoked

Security notes:

- never reveal whether the email exists
- reset links must be single use and short lived
- rate limit reset requests by account and IP
- log reset issuance and completion events
- consider forcing recent-session reauthentication after password change

### Password reset alternative for MVP

If email delivery is not ready yet, MVP can start with admin-issued invite/reset links only.

That means:

- `Forgot password` can initially say `Contact support/admin`
- the same token infrastructure can still be used behind the scenes

This is acceptable for a first internal release, but it should be treated as temporary.

## Authorization Model

Introduce a user context object that is resolved right after login and passed into the data access layer.

Suggested shape:

```python
@dataclass(frozen=True)
class UserContext:
    user_id: str
    email: str
    role: str
    portfolio_id: str
    share_fraction: float
    can_view_full_portfolio: bool = False
```

Rules:

- `admin` can view full raw portfolio data
- `investor` sees only ownership-adjusted portfolio views
- market research sections can remain shared for MVP
- account/performance/portfolio-derived views become ownership-aware
- future post/comment permissions should be based on the same `user_id` and membership model
- contribution/withdrawal requests should be allowed only for authenticated users with an active portfolio membership
- approval and settlement actions should be restricted to `admin` users and fully audited

## Ownership Projection Rules

The cleanest design is:

`master portfolio data -> ownership projection -> UI response`

Do not fetch separate live Alpaca data per user. Fetch once, then project based on share.

### Account summary

Scale:

- `equity`
- `cash`
- `portfolio_value`
- total unrealized P/L

Do not blindly scale or expose without review:

- `buying_power`
- `daytrade_count`
- `account_status`
- margin-specific operational fields

For investor users, some operational brokerage fields should be hidden or replaced with investor-friendly labels.

### Positions

Keep unchanged:

- `symbol`
- `avg_entry_price`
- `current_price`
- return percentages like `unrealized_plpc`

Scale into effective investor exposure:

- `qty`
- `market_value`
- `unrealized_pl`

If scaled `qty` is shown, label it clearly as `effective_qty` or `economic_qty` so it is not mistaken for the broker's actual share count.

### Equity timeseries

Build:

- `master_equity`
- `user_equity = master_equity * share_fraction(as_of_date)`

If ownership is static, this is a simple scalar multiplication.
If ownership changes over time, join against `portfolio_ownership_daily`.

### Contributions and withdrawals

This needs a separate lifecycle from display ownership.

Recommended flow:

1. user submits a contribution/withdrawal request
2. request is reviewed and approved or rejected
3. funds are processed externally
4. settlement is recorded as a `capital_event`
5. ownership percentages are recalculated or a new effective membership row is created
6. the user-facing portfolio view changes only after settlement

This avoids showing ownership that has not actually materialized yet.

### Performance metrics

For constant ownership:

- `annual_return`
- `sharpe_ratio`
- `beta_vs_spy`
- `alpha_vs_spy`
- `max_drawdown`

will be the same as the master portfolio because they are return-based, not dollar-based.

What should still be ownership-aware:

- equity chart in dollars
- cumulative dollar gain/loss
- current market value
- per-position dollar exposure

For changing ownership schedules, compute metrics from the ownership-adjusted timeseries instead of copying master metrics.

## Implementation Shape

### New modules

Recommended additions:

- `services/auth_store.py`
- `services/auth_service.py`
- `services/access_store.py`
- `services/session_store.py`
- `compute/ownership.py`
- `services/capital_requests.py`
- `services/content_store.py`
- `services/invite_store.py`
- `services/password_reset.py`

### Likely changes to existing files

- `app.py`
  - replace shared username/password flow with user lookup
  - load `UserContext` into Streamlit session state
  - render investor-specific account and performance views
  - leave room for future authenticated post/comment and request screens
- `data_access/layer.py`
  - accept `user_context` for ownership-aware resolvers
  - keep raw resolvers for admin/internal use
- `compute/portfolio.py`
  - leave aggregate portfolio fetch logic in place
  - add ownership-adjusted time series helper or move that logic into `compute/ownership.py`
- `data_access/contracts.py`
  - optionally add a serialized `UserContext` / `AccessContext`
- `tests/*`
  - add auth, access, and ownership-projection coverage

## Resolver Plan

Keep the raw data path and add projection wrappers instead of rewriting everything.

### Raw resolvers stay as-is

- `resolve_account()`
- `resolve_positions()`
- `resolve_portfolio_timeseries()`
- `resolve_portfolio_performance()`

### Add ownership-aware resolvers

- `resolve_user_account(user_context, ...)`
- `resolve_user_positions(user_context, ...)`
- `resolve_user_portfolio_timeseries(user_context, period, ...)`
- `resolve_user_portfolio_performance(user_context, period, ...)`

Those should:

1. call the existing raw resolver
2. apply ownership projection
3. return projected payload plus provenance showing that projection occurred

## UI Plan

### Login screen

Replace:

- shared `Username`
- shared `Password`

With:

- `Email`
- `Password`

After login:

- store `user_id`, `email`, `role`, `portfolio_id`, `share_fraction`
- refresh the session cookie/session token

Future authenticated features should reuse this identity context rather than creating a separate user model for comments or requests.

### Create account screen

For MVP, this is better treated as an invite acceptance screen rather than open self-signup.

Suggested fields:

- `Email` (pre-filled from invite when possible)
- `First name`
- `Last name`
- `Password`
- `Confirm password`

### Forgot password screen

Suggested fields:

- `Email`

Suggested behavior:

- always show a generic confirmation message
- never disclose whether the account exists
- route the user to a reset-password page via emailed token

### Sidebar/header

Show:

- investor display name
- role
- ownership percentage

Example:

- `Signed in as Jane Doe`
- `Portfolio share: 20.00%`

### Portfolio Overview

Show investor-adjusted values by default:

- ownership-adjusted equity
- ownership-adjusted position table
- ownership-adjusted dollar P/L

Keep a clear label like:

- `Viewing your 20.00% economic share of the master portfolio`

### Performance page

Show both:

- dollar-equity chart for the investor
- return metrics computed from the projected series

For constant-share users, add a small note that return percentages match the master portfolio because ownership is fixed.

### Future contribution/withdrawal screens

When introduced, these should show:

- current ownership share
- pending contribution/withdrawal requests
- settled capital history
- clear status labels such as `Requested`, `Approved`, `Processing`, `Settled`, `Rejected`

Do not mix pending requests into current portfolio ownership until settlement is complete.

### Future posts/comments

If introduced later, use:

- `author_user_id`
- per-post visibility
- admin moderation states
- immutable audit fields for edits/deletes where needed

## Rollout Phases

### Phase 1: Data model and admin plumbing

- create access/auth tables
- add migrations
- seed first portfolio row
- add admin scripts to create/invite users and assign shares
- add invite and password-reset token tables
- create capital request/event tables even if their UI comes later
- leave room for content tables or add them when the feature is prioritized

### Phase 2: Authentication

- replace env-based shared credential flow
- implement email/password login against `users` + `user_credentials`
- move session storage out of in-memory cache and into `user_sessions`
- implement invite acceptance flow
- implement forgot-password/reset-password flow

### Phase 3: Ownership projection

- add `UserContext`
- implement `compute/ownership.py`
- add ownership-aware resolvers in the DAL
- update cache keys so projected responses are keyed by `user_id` or `share_fraction`

### Phase 4: UI updates

- update login form
- update sidebar/header
- update Portfolio Overview and Performance to use ownership-aware loaders
- hide admin-only brokerage fields from investor users

### Phase 5: Hardening

- lockout / rate limiting
- password reset flow or invite reset flow
- audit logging for login and membership changes
- tests for changed ownership schedules and share sum constraints
- strong review/audit controls around contribution and withdrawal requests
- moderation and visibility controls for future user-authored content

## Security Priorities

Because this model may later include both money-related actions and user-generated content, security should be part of the design from the start.

### Authentication security

- password hashing with Argon2 or bcrypt
- server-side session storage with hashed session tokens
- session expiration, logout, and revocation
- failed-login throttling and temporary lockouts
- no plain-text shared credentials in long-term production design
- invite and reset tokens hashed at rest, single use, and short lived
- generic responses on reset endpoints to prevent account enumeration

### Authorization security

- every data access path should resolve `UserContext` first
- ownership-aware views should never trust client-submitted share percentages
- admin operations must be enforced server-side, not only hidden in the UI
- future post/comment visibility rules should be enforced in queries, not just presentation

### Money-movement security

- contributions/withdrawals should be request-based, never direct side effects from a UI form
- approvals and settlement should be separate actions
- require full audit trail: requester, reviewer, timestamps, amount changes, notes
- consider step-up auth for contribution/withdrawal submission and certainly for approval
- never update ownership from a pending request alone

### Data protection

- minimize PII returned to non-admin users
- encrypt secrets and external credentials in Key Vault
- avoid storing bank/payment details in this app unless absolutely necessary
- log security-relevant events without leaking secrets or password material

### Abuse/moderation readiness

- if posts/comments are added, plan for moderation status and soft-delete
- sanitize/escape rendered user content
- rate limit content creation endpoints
- keep edit/delete attribution for admin review

## Test Plan

Add coverage for:

- login success/failure
- disabled user blocked from login
- session expiry and logout
- invite acceptance success/failure/expiry
- forgot-password request flow and reset token expiry
- password reset revoking prior sessions
- two users with different ownership shares seeing different dollar values
- constant-share user seeing same return percentages but different dollar values
- changed ownership schedule altering historical user equity correctly
- membership constraints preventing overlapping active rows
- portfolio share total not exceeding `100%`
- contribution request status transitions and permission checks
- ownership remaining unchanged while a capital request is still pending
- settled capital events updating downstream ownership correctly
- future content visibility and moderation rules if posts/comments are added

## Non-Goals for MVP

- brokerage trading permissions per investor
- multiple independent Alpaca accounts per user
- tax-lot accounting by investor
- user-specific market research universes
- direct automated cash transfer execution from the first release

## Recommended MVP Decisions

If we want the fastest useful version, I would lock in these choices:

- one portfolio row
- invite-only users
- in-app email/password auth with hashed passwords
- explicit invite acceptance and forgot-password flows
- one active share assignment per user
- ownership-aware Portfolio Overview and Performance pages first
- admin-only access to raw portfolio/account fields
- contribution/withdrawal requests modeled early, but operational settlement can come later
- user identity designed once so posts/comments can reuse it later

## Open Questions

These do not block the schema, but they should be confirmed before implementation:

1. Is ownership expected to change over time, or is it effectively static?
2. Should investor users see effective position quantities, or only dollar exposure?
3. Do we want self-service email reset in MVP, or admin-managed reset links first?
4. Should the sum of active investor shares equal exactly `100%`, or can some ownership remain unassigned?
5. Do we need multiple portfolios in the next 1-2 releases, or can we keep a single-portfolio model for now?
6. Should contribution/withdrawal requests be visible to only the requesting user and admins, or to all portfolio members?
7. Will posts/comments be portfolio-wide discussions, admin broadcasts, private notes, or some mix of these?

## First Implementation Slice

If we build this incrementally, the best first slice is:

1. add `users`, `user_credentials`, `portfolios`, and `portfolio_memberships`
2. add `user_invites` and `password_reset_tokens`
3. swap the login form to email/password
4. add invite acceptance and forgot-password flows
5. add `UserContext`
6. project account summary, positions, and portfolio equity by ownership share
7. update the Performance page to show investor-adjusted dollar views
8. add `capital_requests` now if we want to avoid another auth/permissions redesign later

That gets named-user access live without forcing a full rewrite of the existing portfolio data path.
