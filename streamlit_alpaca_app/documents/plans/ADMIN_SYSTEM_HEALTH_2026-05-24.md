# Admin System Health - 2026-05-24

## Goal

Expand the old Admin `Pipeline Jobs` section into `System Health` so silent dependency failures are visible in the app.

## Implemented Slice

- Renamed the admin tab to `System Health`.
- Kept existing job timeline, failure chart, dataset row-count chart, and job controls.
- Added top-level health metrics:
  - failed jobs
  - running jobs
  - connector failures
  - retained provider-error rows
- Added dataset freshness from latest `dataset_versions` rows.
- Added connector-call telemetry for shared SerpApi and Tavily clients.
- Added retained provider-evidence fallback so Admin still shows provider/error signal before the new telemetry table has history.

## Storage Contract

New best-effort table:

- `connector_call_events`
- provider, operation, status, start time, duration, HTTP status, result count, error type/summary, job/run context, metadata JSON

Telemetry stores query hashes and sizes, not raw query text.

## Follow-Ups

- Add the same telemetry wrapper around other external connectors as they are centralized.
- Backfill/repair provider error rows that were stored as evidence.
- Add per-job expected freshness thresholds once the simplified pipeline contract lands.
- Deploy dev once unrelated dirty changes are either included intentionally or separated.
