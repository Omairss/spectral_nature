# Chat + Search Debug Panel Simplification

Date: 2026-04-12

## Goal

Keep the main Chat + Search interface focused on the answer and resolved actions, while moving route and agent diagnostics into an admin-only debug panel.

## Changes

- Remove route metrics, request metadata, agent status metrics, tool call traces, and transcript messaging from the main user-facing flow.
- Render the shared agent answer without the extra debug chrome for non-admin users.
- Show a collapsed `Admin Debug` expander only for admin users.
- Keep the transcript and tool-call inspection inside that admin-only debug area.
- Suppress the `No direct matches were found` message when an agent answer already exists and the query intentionally routed to agent mode.

## Verification

- Run `python3 -m py_compile streamlit_alpaca_app/app.py`.
- Use `streamlit.testing.v1.AppTest` with seeded Chat + Search session state to verify:
  - non-admin users do not see the removed debug text
  - admin users still get the `Admin Debug` panel
  - the main agent answer remains visible in both cases

## Rollout

- Deploy to `dev` after verification.
