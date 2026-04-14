# Pipeline Identity And FRED Retry Fix

## Goal

Fix the April 13, 2026 pipeline regressions at the source:

- `attention-home-build` was showing green runs in Azure while the homepage stayed stale at April 10, 2026.
- `macro-fred-daily` was failing on transient upstream FRED 500 responses.

## Root Cause

### Attention job

- The job deployment script updated env vars on existing jobs but did not reattach the intended user-assigned managed identity.
- `attention-home-build` ended up with `AZURE_CLIENT_ID` pointing at the shared pipeline identity while the job resource still only had `developer-1` attached.
- The shared Azure credential builder trusted the configured client id too strongly. When that id was stale for the running job, storage and Key Vault reads failed instead of falling back to the attached default managed identity.
- The attention job then treated missing mover inputs as a skip instead of a failure, so Azure showed `Succeeded` even though no fresh attention home datasets were written.

### FRED job

- `services/fred.py` treated transient HTTP 500/429 and basic connection failures as immediate hard errors.
- The job already had a partial-success guard, so Treasury yield datasets still persisted, but the FRED portion failed the overall run and left `fred_summary` stale.

## Source Fixes

### Shared Azure credential fallback

- `services/secrets.py` now prefers Azure runtime managed identity before Azure CLI.
- In Azure runtime it now builds a managed-identity chain:
  1. configured client id, if present
  2. default attached managed identity
- This keeps jobs working when `AZURE_CLIENT_ID` drifts before the deployment identity is corrected.

### Deployment drift guard

- `scripts/deploy_pipeline_azure.sh` now runs `az containerapp job identity assign` on both create and update.
- That makes the attached identity part of the steady-state deployment path instead of a one-time create-only side effect.

### Attention job failure signaling

- `pipeline/jobs/attention_home_build.py` now fails the job when both `daily_movers` and `macro_anchor_daily_movers` are unavailable.
- It also fails if the job produces zero output datasets.
- This prevents stale homepage data from being reported as a successful refresh.

### FRED retry and backoff

- `services/fred.py` now retries transient FRED failures:
  - `429`
  - `500`
  - `502`
  - `503`
  - `504`
  - connection and timeout errors
- Backoff is configurable with:
  - `FRED_HTTP_MAX_ATTEMPTS`
  - `FRED_HTTP_INITIAL_BACKOFF_SECONDS`
  - `FRED_HTTP_BACKOFF_MULTIPLIER`
  - `FRED_HTTP_BACKOFF_JITTER_SECONDS`
- FRED Key Vault lookup now uses the shared Azure credential builder so auth behavior is consistent across services.

## Validation

- `test_secrets.py`: managed-identity chain fallback coverage
- `test_services.py`: transient FRED 500 retry coverage
- `test_pipeline_jobs.py`: attention job fails when required mover inputs are missing
- `py_compile`: touched modules compile cleanly

## Dev Verification

- Deployed pipeline job image `snpipelineacr03130136.azurecr.io/pipeline-jobs:20260413105630`
- `macro-fred-daily-q3i2ge5` succeeded on April 13, 2026
- `attention-home-build-l9uxl11` succeeded on April 13, 2026
- Dev app-backed Postgres `job_runs` now shows:
  - `attention-home-build` -> run `712c841e-0d19-43a9-a6cf-b9ace055291e`, `Succeeded`, `completed`, heartbeat `2026-04-13 19:08:20+00:00`
  - `macro-fred-daily` -> run `c48fb11e-4003-406a-9049-9cbc55908ef9`, `Succeeded`, `completed`, heartbeat `2026-04-13 18:39:48+00:00`
- Fresh storage manifests written by the repaired runs:
  - `attention_home_1d__20260413T183822Z__712c841e` last modified `2026-04-13T19:08:20+00:00`
  - `fred_summary__20260413T183825Z__c48fb11e` last modified `2026-04-13T18:39:19+00:00`
- Local pipeline metadata cache now resolves:
  - `attention_home_1d` -> as-of `2026-04-13T18:38:22.098123+00:00`
  - `fred_summary` -> as-of `2026-04-13T18:38:25.678267+00:00`

## Rollout

1. Deploy the pipeline jobs to dev with the updated deployment script.
2. Trigger `attention-home-build` and `macro-fred-daily`.
3. Verify:
   - `attention_home_snapshots_1d` and `attention_home_1d` write fresh manifests
   - `attention-home-build` no longer reports green when inputs are missing
   - `macro-fred-daily` survives transient upstream FRED failures when they recover within the retry window
