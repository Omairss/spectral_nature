# Robinhood MCP Integration

Date: 2026-10-02

Status: hardened execution deployed and verified in development

## Intent

Connect the Spectral Nature application—not an operator's Codex session—to
Robinhood's hosted Streamable HTTP MCP server at
`https://agent.robinhood.com/mcp/trading`.

Robinhood is a user-authorized brokerage connection. It does not replace the
shared AQL/Zopedia research spine and it does not become a second research
agent. Spectral Nature owns user identity, connection lifecycle, tool policy,
audit history, and the product actions that call the remote tools.

## Product Boundary

The integration has four layers:

1. `brokerage_mcp_store` owns per-user OAuth state, encrypted token records,
   remembered dynamic client registrations, the latest discovered tool
   catalog, and call audit rows in the existing `app_access` Postgres schema.
2. `robinhood_mcp` owns OAuth discovery, dynamic registration, PKCE,
   authorization-code exchange, refresh, Streamable HTTP sessions, tool
   discovery, invocation, and the Robinhood-specific safety policy.
3. The Trading Agent view owns explicit user actions: connect, refresh account
   state, disconnect, review an exact equity order, and place that unchanged
   order with one confirmation.
4. AQL/Zopedia may eventually receive explicitly allowed read-only Robinhood
   tools for an authenticated foreground user. Order, cancellation, exercise,
   watchlist mutation, alert mutation, and settings tools never enter the
   autonomous research registry.

## Authorization Flow

1. A signed-in Spectral Nature user chooses `Connect Robinhood`.
2. The app discovers RFC 9728 protected-resource metadata and authorization
   server metadata from Robinhood.
3. The app reuses a dynamic client registration for the exact deployment
   callback URI or registers one and stores the returned client metadata.
4. The app creates a short-lived PKCE flow tied to the Spectral Nature user.
   The state value is stored only as a digest; the code verifier is encrypted.
5. Robinhood redirects to the same Streamlit deployment with `code`, `state`,
   and `iss`. The app requires the same signed-in Spectral Nature user, consumes
   the state once, exchanges the code, encrypts the tokens, and immediately
   discovers the live MCP tool catalog.
6. Refresh tokens are used server-side. Reauthentication and remote failures
   become explicit connection state.

## Security Contract

- OAuth access tokens, refresh tokens, and PKCE verifiers are encrypted with a
  dedicated Fernet key loaded from Key Vault. No ephemeral production key and
  no plaintext token persistence are allowed.
- The callback URI is derived from `APP_PUBLIC_BASE_URL`, never from an
  untrusted request header.
- OAuth state is one-time, expires quickly, and is bound to a user, client,
  resource, redirect URI, and issuer.
- Tool schemas come from the live MCP server and are stored for inspection.
  Secrets and full account payloads are not copied into audit rows.
- A remote annotation is a hint, not authorization. Spectral Nature maintains
  its own fail-closed tool policy. Unknown tools are unavailable until their
  behavior is classified.
- Portfolio and position reads run only after an explicit refresh action; page
  rendering never starts hidden Robinhood calls.
- Research approval and order execution remain different states. Existing
  `Trading Agent` approval records do not place an order.
- The generic MCP invoker cannot execute any placement, cancellation, exercise,
  or other money-moving tool. Equity placement is reachable only through the
  reviewed-order ticket state machine.
- A reviewed-order ticket is bound to the signed-in user, environment,
  connection, canonical argument hash, expiry, and a fresh Robinhood `ref_id`.
  The database claims it atomically once before placement. Changed, expired,
  canceled, or already-used tickets fail closed.
- A transport failure after placement begins becomes `unknown`, not retryable.
  The same logical order is never resent with a fresh `ref_id`.
- `ROBINHOOD_EXECUTION_ENABLED` is an environment kill switch and defaults off.
- CORS and XSRF protections are enabled. Browser-persistent JavaScript cookies
  are opt-in; the default authenticated browser state is session-only.
- Account payloads shown in the UI are redacted. Only accounts returned with
  `agentic_allowed=true` enter the order selector, and the browser receives a
  masked label rather than the account number as the selector option.

## Tool Policy

Robinhood's official catalog separates account/portfolio reads, market data,
order review, order placement, and cancellation. Spectral Nature mirrors that
separation:

- foreground read: selected `get_*`, `search`, `run_scan`, and preview/review
  tools after local policy validation;
- review: `review_equity_order`, `review_option_order`,
  `preview_crypto_order`, and `review_advanced_order`;
- execution: place, exercise, cancel, decline, and other mutating tools;
- autonomous AQL/Zopedia: no execution tools.

The product connects the account, discovers the server, and loads portfolio,
equity-position, and Robinhood trade-approval state on demand. Equity execution
is not inferred from a research candidate. The user enters an order, Robinhood
reviews it, and one `Place order` action consumes the unchanged ticket. There
is no committee, queue, repeated prompt, or autonomous placement path.

Robinhood's trade-approval setting remains a broker-owned choice. With broker
approvals off, the Spectral Nature confirmation is the single product
confirmation and placement is direct. With approvals on, Robinhood may require
its own approval after submission. Application controls reduce credential and
session exposure, but they cannot make a stolen Robinhood bearer token harmless;
the broker's dedicated MCP account boundary and optional broker approval remain
the controls outside Spectral Nature.

## Durable Data

The `app_access` schema gains:

- `brokerage_mcp_clients`: one dynamic client registration per provider,
  resource, and exact redirect URI;
- `brokerage_connections`: one current connection per
  user/provider/resource/environment, encrypted tokens, status, expiry, server
  info, and discovered tools;
- `brokerage_oauth_flows`: short-lived, one-time PKCE authorization state;
- `brokerage_mcp_audit_events`: tool name, local intent, timing, status, and
  redacted request/result summaries.
- `brokerage_order_tickets`: short-lived review state containing only the exact
  argument hash, masked summary, one Robinhood `ref_id`, lifecycle state, and
  timestamps. Full order arguments are not duplicated into Postgres.

These records use real foreign keys to `app_access.users`. Disconnect revokes
local access by erasing encrypted tokens even if the remote provider does not
offer token revocation.

## Deployment

The UI image owns this integration because Streamlit handles the connection
and callback surface. Development and production have separate connection
identities, callback registrations, and Fernet keys. The direct
`ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY` environment secret takes precedence so a
shared database or vault identity does not make ciphertext portable across
deployments. Production is not deployed without explicit permission.

The UI image installs from the hash-locked `requirements-ui.lock` graph.
`cryptography` is constrained to the fixed 50.x line and the full locked graph
must pass the dependency vulnerability audit before rollout.

## Acceptance

Local acceptance requires:

1. OAuth metadata discovery and redirect construction against Robinhood's live
   endpoints.
2. One-time state validation, wrong-user rejection, expiry rejection, and no
   plaintext secrets in persistence or logs.
3. Token refresh and MCP initialization/list-tools behavior through the shared
   client.
4. Fail-closed policy checks proving execution tools cannot enter read-only
   calls.
5. A rendered Trading Agent connection panel with no background network work.
6. A real development OAuth connection, tool discovery, and read-only account
   refresh after the user completes Robinhood authorization.

## Local Verification

Completed 2026-10-02:

- Robinhood's live RFC 9728 and authorization-server metadata resolved and
  passed the trusted-host, issuer, PKCE, grant, and public-client checks.
- Dynamic registration succeeded for
  `http://localhost:8501/?robinhood_oauth=callback`.
- Postgres contains the five application-owned brokerage tables. The pending
  flow is environment-scoped, stores a 64-character state digest rather than
  the state, and stores the PKCE verifier as Fernet ciphertext.
- The remembered client record contains no client secret or registration
  access token.
- Connected and disconnected Streamlit panels rendered through `AppTest`
  without exceptions. The disconnected path exposes only `Connect
  Robinhood`; the connected path exposes `Refresh account` and `Disconnect`.
- The focused Robinhood, authentication, and Trading Agent UI suite passes: 49
  checks cover OAuth boundaries, generic execution denial, exact-payload ticket
  binding, one-time consumption, `ref_id` reuse, account eligibility, and the
  single-confirmation UI.
- The repository secret scan and dependency consistency check pass.
- The hash-locked UI dependency graph reports no known vulnerabilities through
  `pip-audit`.

## Development Verification

Deployed 2026-10-02 from an isolated worktree at base commit
`01c53d1300b8dcd8bfb603c261750b475ba6e499` so unrelated working-tree changes
were not bundled.

- Development revision: `sn-streamlit-ui-dev--0000446`.
- Image digest:
  `sha256:35171ff61b0b9a7a5c534e376f73a60d5e508556ee48257f5d69c4f29a1d8f77`.
- Root and Streamlit health endpoints both return HTTP 200.
- `APP_PUBLIC_BASE_URL` is the development Container App hostname, so OAuth
  codes cannot be redirected into production.
- The running container imports the MCP and brokerage-store modules, creates
  the order-ticket schema, resolves Robinhood's live OAuth issuer, and loads
  the pinned `cryptography` and `pypdf` versions.
- The rollout was probed with execution disabled, then enabled only after the
  runtime checks passed. Revision 446 reports execution enabled in the
  development environment with the schema ready.
- Development uses the dedicated `robinhood-mcp-token-key-dev` secret,
  session-only browser authentication, a 120-second confirmation ticket, and
  contains no Robinhood access token before user authorization.
- Production remains on revision `sn-streamlit-ui--0000085` and was not
  changed.

The remaining acceptance step is user-owned: an admin must open Trading Agent,
select `Connect Robinhood`, and approve access at Robinhood. The callback then
performs live tool discovery and enables account refresh and the explicit
review-then-place path. Verification deliberately did not submit a real order.
