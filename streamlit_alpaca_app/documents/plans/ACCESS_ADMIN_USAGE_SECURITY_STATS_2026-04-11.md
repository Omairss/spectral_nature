# Access Admin Usage + Security Stats

Date: 2026-04-11

## Goal

Expose two admin questions directly in `Access Admin`:

1. Who is using the product?
2. How are they using it, and are there any security issues to watch?

## Source-first design

- Persist access analytics in Postgres instead of trying to reconstruct them from container logs.
- Keep the tracking close to auth/runtime boundaries:
  - auth events in `services/auth_service.py`
  - durable storage + aggregate queries in `services/auth_store.py`
  - section usage capture in `app.py`

## New durable dataset

Added `app_access.access_events` with:

- `user_id`
- `email`
- `event_type`
- `event_category`
- `section_name`
- `session_token_hash`
- `ip_address`
- `user_agent`
- `detail_json`
- `created_at`

This keeps the admin stats queryable without parsing stdout or Azure logs.

## Tracked events

### Usage

- `login_success`
- `session_restored`
- `section_view`
- `logout`
- `account_created`

### Security

- `login_failed`
- `login_locked`
- `password_reset_requested`
- `password_reset_issued_admin`
- `password_reset_completed`

### Admin

- `invite_created`

## Admin page outputs

The `Usage + Security` tab now shows:

- summary metrics for usage and security windows
- cloud audit and diagnostic coverage for the tracked SQL and Key Vault resources
- section usage counts
- per-user activity table
- open sessions table
- recent security events table

The existing `Users` table also now shows:

- active/open session counts
- last seen timestamp
- failed login count
- lockout timestamp

## Reliability notes

- Section usage starts when this tracker is deployed; there is no reliable historical backfill for section-level behavior.
- Current login/session state does backfill immediately from existing auth tables.
- Section views are deduped per session + section transition so Streamlit reruns do not inflate counts.
- Cloud audit coverage uses Azure control-plane reads, so it depends on Azure credentials being available to the app runtime.
- Azure control-plane credentials can span multiple subscriptions. Resolve the target subscription from the configured resource group and tracked resources, not from the first enabled subscription returned by ARM.
- Do not default this panel to `PIPELINE_RESOURCE_GROUP`; the pipeline RG can differ from the SQL and Key Vault RG that the panel is meant to monitor.

## Follow-up options

- add API key usage stats to the same page
- split actor vs subject fields more explicitly for admin-issued actions
- add retention/cleanup for `access_events` if row volume grows materially
