# Access Admin list_users SQL Alias Fix

Date: 2026-04-11
Owner: Codex
Status: Done

## Problem

The Access Admin screen crashed while loading users with:

`psycopg.errors.UndefinedTable: missing FROM-clause entry for table "u"`

Root cause:
- `services/auth_store.py:list_users()` was rewritten to join credential and session rollups.
- The query still referenced `u.id`, `u.created_at`, and `u.email`.
- The `FROM` clause no longer declared `users` as `u`.

## Fix

- Restored the missing alias in `FROM {schema}.users u`.
- Added `tests/test_auth_store.py` with direct coverage for the `list_users()` SQL path.
- The test checks the alias-bearing query shape and the merged user/session payload.

## Verification

- `python -m pytest tests/test_auth_store.py tests/test_auth_service.py tests/test_emailer.py tests/test_secrets.py tests/test_api_v1.py`
- Result: `39 passed`

## Rollout

- Deploy fix to `dev`.
- Verify Container App revision, image digest, and HTTP health after rollout.
