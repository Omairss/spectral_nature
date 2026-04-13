# Chat + Search Thinking Stream

Date: 2026-04-12

## Goal

Give Chat + Search a visible live wait state so the user sees real progress while the agent is working, without bringing back the old debug-heavy interface.

## Changes

- Replace the generic progress bar with a compact `Thinking` panel that updates from the existing omnibar progress events.
- Translate technical progress stages into short user-facing messages such as direct match checks, evidence gathering, and answer writing.
- Keep the streamed status temporary so it disappears once the answer is ready and the screen stays clean.
- Preserve the admin-only debug panel for route, tool, and transcript details.

## Verification

- Run `python3 -m py_compile streamlit_alpaca_app/app.py`.
- Verify the new progress copy mapping with a small `uv run` assertion script against `_agentic_omnibar_progress_message(...)`.
- Use `streamlit.testing.v1.AppTest` to confirm:
  - non-admin users still only see the answer surface
  - admin users still get the `Admin Debug` expander

## Rollout

- Deploy to `dev` after verification.
