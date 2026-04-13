# Access Admin Cloud Status And Sankey - 2026-04-11

## Problem

Two admin analytics gaps were still open:

1. `Cloud Audit Coverage` could show false `error` or `missing` rows when Azure resource discovery drifted to the wrong subscription or trusted a bad resource-group hint.
2. `Usage and Security` still lacked a compact cross-user flow view that showed which pages and tracked items the most active users were actually touching.

## Changes

### 1. Hardened Azure resource resolution

Changed file:

- `streamlit_alpaca_app/services/admin_security_status.py`

What changed:

- Added top-level ARM resource discovery for the tracked SQL server and Key Vault before audit checks run.
- Changed subscription scoring so only real ARM matches count. Fallback ids are still usable for error reporting, but they no longer bias subscription selection.
- Switched SQL database discovery to follow the resolved SQL server resource id instead of rebuilding the path from a resource-group hint.
- Added a regression test for the fallback listing path and another for the subscription tie-break bug.

Why this matters:

- The admin panel can now recover even when the configured resource-group hint is stale or wrong.
- The panel stops showing false failures just because the credential can see more than one Azure subscription.

### 2. Added preloaded usage Sankey flow

Changed files:

- `streamlit_alpaca_app/services/auth_store.py`
- `streamlit_alpaca_app/services/auth_service.py`
- `streamlit_alpaca_app/app.py`

What changed:

- Added one bounded aggregate query that preloads usage flow data from the top N active users in the selected usage window.
- The query stays on the existing `access_events` table and excludes login/session noise so the Sankey focuses on page, item, and feature usage.
- Page-only views stop at the page node. Tracked item and feature clicks continue to the target node.
- When an admin applies a specific user filter, the same chart collapses to that single user instead of the top-N preload.

Latency guardrails:

- The Sankey query is one grouped read over the current usage window.
- The top-user count is capped in code.
- The chart only keeps the top targets per user and page so the payload stays small enough for the Streamlit admin view.

## Verification

- `python -m py_compile` passed for:
  - `app.py`
  - `services/admin_security_status.py`
  - `services/auth_service.py`
  - `services/auth_store.py`
- Targeted tests passed:
  - `tests/test_admin_security_status.py`
  - `tests/test_auth_store.py`
  - `tests/test_auth_service.py -k get_access_admin_dashboard`
- Live local Azure verification:
  - normal admin security status resolves all tracked resources as `healthy`
  - the same status check stays healthy even when `ADMIN_SECURITY_RESOURCE_GROUP=wrong-rg`

## Deployment

- Dev deployment completed successfully.
- Container App: `sn-streamlit-ui-dev`
- Ready revision: `sn-streamlit-ui-dev--0000184`
- Image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:e1e52cab33af292c85b4e44677b0b7b42372cbeaeed68a554f2cdca9e4fb7d0d`
- Root health check: `HTTP 200`
