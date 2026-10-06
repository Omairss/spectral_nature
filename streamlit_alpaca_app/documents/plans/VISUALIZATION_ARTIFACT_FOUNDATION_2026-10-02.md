# Visualization Artifact Foundation — 2026-10-02

Status: transport verified and deployed to the isolated development app; product UI rejected. Production was not changed. The replacement workbench is being rebuilt locally and has not been deployed.

## Outcome

The new frontend has one production-shaped visualization transport path without changing the analytical backend. Stock Investigator requests the existing technical price-channel chart through a versioned visualization artifact, an authorized private API, and the same-origin Next.js BFF. The browser selects an allowlisted renderer and never executes generated JavaScript. The UI built around this path was rejected and must not be treated as product completion or route parity.

## Delivered

- Versioned `dataset.v1` and `visualization.v1` contracts with content identities, typed columns, provenance, linked-selection identity, row counts, encoded size, and delivery metadata.
- FastAPI visualization endpoint protected by the existing `query:execute` and `chart:read` scopes.
- Same-origin BFF forwarding with the existing HTTP-only session cookies, refresh rotation, exact-origin validation, CSRF, bounded JSON input, and HTTPS-only backend policy.
- Dynamically loaded Plotly registry adapter with responsive desktop/mobile layout, normalized selection output, CSP-bundled CSS, and no Chart Studio sharing action.
- Stock Investigator ticker/window controls, shareable URL state, and the real price-channel chart path.
- Symbol-hash partition manifests for future `price_history` and `technical_signal_history` publications. Large unpartitioned legacy snapshots are not downloaded for a one-symbol request; the data layer falls back to its existing on-demand source.
- A pre-hydration login guard so credentials cannot fall through to a default query-string form submission.

## Transport Verification

- Real development-data probe: AAPL artifact built with 252 rows and four traces in 8.54 seconds while the 1.45-million-row legacy global artifact was skipped.
- Local browser review: desktop and 390×844 mobile layouts render cleanly; ticker/window submission updates the deep link and chart artifact; Plotly cloud sharing is absent.
- Next.js lint, typecheck, and optimized production build pass.
- FastAPI/data-access suite: 69 passed.
- Pipeline store suite: 26 passed.
- Pipeline job suite: 53 passed.
- Development API `sn-api-next-dev--0000005`: healthy on `api-next-dev@sha256:51c5679d2004efc7834d461dc397c946993f0607532c361cf58f91dfb91bf50a`.
- Development web `sn-next-ui-dev--0000002`: healthy on `next-ui-dev@sha256:0afe7efab7e5809d237e565636e416281a674ade6ffbaa99d6779d988cf3582c`.
- In-container development probe: AAPL returned `plotly.figure.v1`, 251 rows, and computed provenance; the public BFF rejects an unauthenticated visualization request with HTTP 401.
- The deployed Stock Investigator route carries per-request script nonces, hydrates, and redirects an unauthenticated browser to the existing shared sign-in page.

These checks verify the boundary and deployment mechanics. They are not product acceptance.

## Next Slice

The local workbench can now open the existing price-channel, pullback, income, balance-sheet, and cash-flow artifacts and inspect their datasets and provenance. A separate typed Stock Investigator read model now carries compact technical state, probabilistic forecast summary, company context, and recent evidence; it excludes raw simulation paths. The next slice is forecast visualization, full page-summary/taxonomy presentation, cross-surface handoffs, and common provenance/export behavior. Do not add another renderer until the route has behavioral parity.
