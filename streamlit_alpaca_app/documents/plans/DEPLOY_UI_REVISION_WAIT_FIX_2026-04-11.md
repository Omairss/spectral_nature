# Deploy UI Revision Wait Fix

Date: 2026-04-11
Owner: Codex
Status: Done

## Problem

`streamlit_alpaca_app/scripts/deploy_ui_azure.sh` could mark a deploy complete before Azure created the new Container App revision.

Why:
- The script waited for `latestRevisionName == latestReadyRevisionName`.
- Immediately after the patch call, Azure could still report the old revision as both latest and ready.
- That let the script exit early and write a stale revision into the deployment tracker.

## Fix

- Capture the target app's current latest revision before the update.
- After the patch, wait until Azure reports a different revision ID.
- Only declare success once that new revision is also the ready revision.

## Outcome

- The live `dev` rollout settled on `sn-streamlit-ui-dev--0000177`.
- The deployment tracker can now be refreshed against the correct ready revision.
- Future deploys should no longer pass on the old revision during the Azure propagation window.
