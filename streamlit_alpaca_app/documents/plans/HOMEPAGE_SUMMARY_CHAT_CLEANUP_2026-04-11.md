# Homepage Summary And Chat Cleanup

Date: 2026-04-11

## Goal

Clean up `Chat + Search`, move the summary experiment onto the main homepage under the graph, and keep the admin `Experiment` page as a simple placeholder.

## Changes

- Remove internal explainer copy from `Chat + Search` that describes resolver routing and mode behavior.
- Render the shared summary card on `Home` directly below the homepage graph.
- Rename the card heading from `Tape Summary Experiment` to `Market Summary`.
- Keep `Experiment` admin-only and reduce it to a placeholder message instead of duplicating the summary/audio card.

## Verification

- Run `python3 -m py_compile streamlit_alpaca_app/app.py`.
- Use `streamlit.testing.v1.AppTest` with `DASHBOARD_AUTH_ENABLED=false` and `PYTHONPATH=streamlit_alpaca_app` to verify:
  - `Home` renders the summary block after the graph block.
  - `Chat + Search` no longer shows the removed helper text.
  - `Experiment` remains available to admin users and no longer renders the summary card.

## Rollout

- Deploy the updated build to `dev`.
- Promote the verified `dev` image to `prod`.
