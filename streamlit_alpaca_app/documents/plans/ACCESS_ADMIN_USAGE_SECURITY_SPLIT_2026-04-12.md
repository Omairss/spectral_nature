# Access Admin Usage Security Split - 2026-04-12

## Goal

Split the combined `Usage + Security` admin screen into separate admin pages so:

- product usage analysis is easier to scan
- security and cloud-audit checks have their own dedicated surface
- the app avoids rendering both heavy sections on every admin visit

## What changed

Changed file:

- `streamlit_alpaca_app/app.py`

Implementation:

- Replaced the Access Admin top-level `st.tabs(...)` layout with an `st.segmented_control(...)` page switch:
  - `Access Management`
  - `Usage`
  - `Security`
  - `Invite Email Designer`
- Moved usage rendering into a dedicated usage dashboard renderer.
- Moved security metrics, open sessions, recent security events, and cloud audit coverage into a dedicated security dashboard renderer.
- Kept the same shared dashboard data source so the underlying SQL and event model did not change.
- Filter controls are now page-specific:
  - `Usage` shows usage window, active session window, user filter, and flow-user limit
  - `Security` shows security window, active session window, and user filter

## Latency impact

- This should be lighter than the previous combined view because only the selected admin page renders.
- The same bounded access-dashboard query is still reused for the selected page, so there is no new analytics subsystem or extra fanout.
- The usage Sankey preload remains capped to the selected top-N users.

## Verification

- `python -m py_compile streamlit_alpaca_app/app.py`
- `pytest streamlit_alpaca_app/tests/test_auth_store.py streamlit_alpaca_app/tests/test_auth_service.py -k 'access_admin_dashboard or get_access_admin_dashboard'`

## Deployment

- Auto-deployed to `dev` after verification.
- Ready revision: `sn-streamlit-ui-dev--0000185`
- Image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:626942bf03c07e60e52612c855d34de12015fdcd81a86611a2d8b9ce634736e3`
- Root health: `HTTP 200`
