# Access Admin User Activity Drilldown

Date: 2026-04-11
Owner: Codex
Status: Done

## Goal

Add a user filter to the Access Admin Usage + Security view and make the selected-user view useful for product decisions.

## What changed

- Added a `User filter` control to the Usage + Security tab.
- The dashboard queries now support optional `user_id` and `user_email` filters.
- When a specific user is selected, the dashboard loads:
  - filtered section usage
  - filtered security events
  - selected-user activity targets
  - selected-user activity trail
- Added high-signal interaction tracking for:
  - narrative bundle opens
  - ticker opens
  - outbound content/news/filing links

## Latency approach

- Kept the default overview path cheap.
- Only load the detailed selected-user activity queries when a user is actually selected.
- Reused the existing `access_events` table and existing `user_id, created_at` index pattern instead of adding a new heavier analytics path.

## Verification

- `python -m pytest tests/test_auth_store.py tests/test_auth_service.py tests/test_emailer.py tests/test_secrets.py tests/test_api_v1.py`
- Result: `42 passed`

## Notes

- Historical data will still show older event types only.
- The richer click trail starts when the new event instrumentation is live.
