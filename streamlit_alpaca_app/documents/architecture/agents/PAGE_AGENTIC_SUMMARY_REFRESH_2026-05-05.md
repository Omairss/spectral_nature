# Page Agentic Summary Refresh

Date: 2026-05-05

## Problem

The Agentic Summary panel reads `page_agentic_summaries`, which is a precomputed dataset produced by `attention-home-build`.

The panel also has a manual "Run Summary Job" button. That button used `az containerapp job start` from inside the Streamlit runtime. The deployed UI image does not include Azure CLI, so users saw:

```text
Failed to run Azure CLI: [Errno 2] No such file or directory: 'az'
```

## Decision

Keep page summaries precomputed. Do not generate them on page load.

Use Azure Resource Manager directly for manual refresh:

- `services.pipeline_store.start_source_refresh_job`
- managed identity via `build_azure_credential`
- `POST /subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.App/jobs/{jobName}/start?api-version=2025-07-01`

Azure CLI remains a local fallback only when ARM startup is unavailable and `az` exists.

## Runtime Config

The UI container should have:

- `PIPELINE_RESOURCE_GROUP`
- `AZURE_SUBSCRIPTION_ID`

The deploy script preserves both values on UI updates.

## Why The Summary Can Still Be Empty

If the panel says no materialized summary is available, the UI did read the precomputed dataset path, but no row matched that page context. Common causes:

- the latest `attention-home-build` run happened before the page-summary materializer existed
- the job timed out or skipped `page_agentic_summaries`
- the UI context signature differs from the job-built context
- the selected ticker was outside the configured stock-summary materialization limit

The fix is to rerun `attention-home-build` and inspect the `page_agentic_summaries` output, not to run AQL on page render.
