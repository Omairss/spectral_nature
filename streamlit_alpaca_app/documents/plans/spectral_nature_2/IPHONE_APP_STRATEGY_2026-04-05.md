# iPhone App Strategy (2026-04-05)

## Goal

Take Spectral Nature from a Streamlit-first UI to an iPhone app without duplicating business logic or introducing brittle wrappers.

## Current baseline

- UI: `app.py` (large Streamlit coordinator)
- Domain logic: `compute/`, `services/`, `data_access/`
- Shared query boundary: `data_access/query_service.py` + `data_access/query_registry.py`
- Existing JSON-ready contracts: `data_access/contracts.py`
- Pipeline-first snapshots available via `services/pipeline_store.py`
- Auth/data model foundations already in place in `services/auth_service.py` + `services/auth_store.py`

## Recommendation (source-first, reliable path)

Do **not** treat Streamlit as the mobile runtime (for example, WebView wrapper).  
Use the existing query layer as the source boundary and introduce a first-class API service consumed by:

1. existing Streamlit UI, and
2. new native iOS app (SwiftUI).

This keeps one backend truth and avoids hardcoded mobile-only logic.

## Architecture target

1. **Backend API service (Python)**
- Add API endpoints around `QueryService.execute(...)`.
- Keep the response contract aligned with `QueryResponse`.
- Add typed endpoints for high-value screens first (home/attention, ticker detail, portfolio snapshot).

2. **Auth + session modernization**
- Use existing app-access schema and user context model.
- Move from Streamlit cookie/session assumptions to token-based API auth (short-lived access token + refresh flow).
- Keep role/ownership checks server-side.

3. **iOS app (SwiftUI)**
- Native shell, navigation, offline cache, and push lifecycle.
- Render server-provided canonical chart datasets/traces with iOS charting (or pre-rendered image fallback for complex traces in MVP).

4. **Data freshness + reliability**
- Default mobile views to materialized datasets for fast/consistent latency.
- Explicit pull-to-refresh and background refresh endpoints for on-demand paths.
- Preserve provenance (`materialized` vs `on_demand`) in API responses for debugging.

## Phased delivery plan

## Phase 0 - API seam hardening (1-2 weeks)

- Extract API-safe DTO models from current query contracts.
- Add input validation/rate limiting/error envelope.
- Add endpoint-level tests for top 10 operations from `query_registry`.
- Keep Streamlit running unchanged.

Exit criteria:
- Same dataset/chart outputs via API and existing local query runner for selected operations.

## Phase 1 - Mobile-ready backend (2-3 weeks)

- Build auth endpoints (login, refresh, logout, session revoke).
- Introduce user-scoped query execution (ownership/view permissions).
- Add endpoint observability: latency, provider fallback, dataset provenance.
- Deploy backend to dev and gate with a versioned `/v1`.

Exit criteria:
- iOS client can authenticate and fetch portfolio + attention + ticker flows only through API.

## Phase 2 - iOS MVP (3-5 weeks)

Screens:
- Login
- Home attention feed
- Ticker detail (price/technical/context)
- Portfolio snapshot

Implementation:
- SwiftUI + async networking layer + secure token storage in Keychain.
- Deterministic local cache for last successful payload per screen.
- Structured error states for auth expiry, stale data, unavailable sources.

Exit criteria:
- TestFlight build usable by internal users with dev backend.

## Phase 3 - Production hardening (2-4 weeks)

- Push notifications for major events/alerts.
- Background app refresh policy and stale-budget SLOs.
- Security review, App Store readiness, analytics instrumentation.
- Expand endpoints from generic query operation to purpose-specific resources where needed.

Exit criteria:
- Production release candidate with monitoring + rollback runbook.

## Reliability and complexity assessment

## High reliability / low complexity
- Reusing `QueryService` as backend core.
- Materialized-first read paths for mobile.
- SwiftUI native app calling versioned API.

## Medium complexity
- Converting chart models into native iOS chart rendering for every chart type.
- Aligning existing auth/session logic with mobile token flows.

## Higher complexity / lower reliability if chosen
- Wrapping Streamlit in an iOS WebView as long-term strategy.
- Running heavy on-demand computations directly from mobile-triggered calls without queueing/caching controls.

## Initial API surface (MVP shortlist)

- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `GET /v1/me`
- `GET /v1/home/attention`
- `GET /v1/tickers/{ticker}/snapshot`
- `GET /v1/tickers/{ticker}/background`
- `GET /v1/portfolio/summary`
- `GET /v1/portfolio/timeseries?period=1Y`

These can map internally to existing query operations first, then gradually migrate to explicit endpoint handlers.

## Guardrails

- Keep business logic in `compute/` and `data_access/`; API layer should orchestrate only.
- No hardcoded iOS-specific branching in domain logic.
- Enforce dev-first deploys; no prod promotion without explicit approval.
- Add regression tests for both default/materialized and forced-refresh/on-demand paths for critical ticker and homepage flows.

## Suggested next action

Start with Phase 0: create a thin API package in this repo that wraps `QueryService` and ships a `/v1/capabilities` + `/v1/dataset/{name}` + `/v1/chart/{name}` prototype in dev.
