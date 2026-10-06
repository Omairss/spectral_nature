# System Health Alerting

## Goal

Notify the operator when a scheduled product surface is unhealthy instead of letting the dashboard silently degrade.

## Implemented Boundary

`system-health` is a small scheduled pipeline job. It reads the durable `job_runs` and dataset metadata that the product already uses; it does not call a model, market-data provider, or introduce another cache.

Every 15 minutes it opens or resolves durable incidents for:

- failed Market Overview (`attention-home-build`) and Trading Agent (`trading-agent-build`) jobs;
- a Market Overview dataset older than the configured weekday freshness limit;
- a completed Trading Agent run with no successful horizon output;
- recent provider billing, quota, rate-limit, or authentication failures.

It sends a single email when an incident opens and a single email when it recovers. Repeated checks update the incident timestamp without sending repeated mail. Failed email delivery leaves the notification pending so the next check retries it.

## Configuration

- `SYSTEM_HEALTH_CRON` defaults to `*/15 * * * *` UTC.
- `SYSTEM_HEALTH_ALERT_EMAIL_SECRET` defaults to `dashboard-bootstrap-admin-email`.
- `SYSTEM_HEALTH_HOME_MAX_AGE_HOURS` defaults to `12`.
- `SYSTEM_HEALTH_HOME_DUE_HOUR_UTC` defaults to `15`; stale-home checks only run after that weekday hour.
- `SYSTEM_HEALTH_PROVIDER_FAILURE_WINDOW_HOURS` defaults to `6`.

The job uses the existing SMTP Key Vault secrets. Alert state lives in `system_health_incidents` in the existing pipeline Postgres database.

## Verification

After deployment, start one execution manually. It should create incidents for any existing failed jobs and send one concise action-required email. A second execution with unchanged state should complete without another email.
