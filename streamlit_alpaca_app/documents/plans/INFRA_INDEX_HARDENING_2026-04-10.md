# Infra Index Hardening Plan

Date: 2026-04-10

## Goal

Keep an operator-friendly infra index without tracking live generated infrastructure details in git.

## Problem

- `streamlit_alpaca_app/infra/deployment.outputs.env` and `streamlit_alpaca_app/infra/email_delivery.outputs.env` were tracked.
- They did not contain secret values, but they did expose live resource names, client IDs, endpoints, and service topology.
- Local scripts depended on those tracked files, so simply deleting them would have hurt operator workflow.

## Approach

1. Move generated outputs to ignored local files under `streamlit_alpaca_app/infra/.generated/`.
2. Keep a tracked lookup index in `streamlit_alpaca_app/documents/infra/RESOURCE_INDEX.md`.
3. Add a live inventory script that can print the current Azure inventory and regenerate the ignored local files.
4. Update local runtime scripts to auto-load the ignored local files instead of expecting tracked outputs.
5. Remove the tracked output files from git.

## New file layout

- Ignored local files:
  - `streamlit_alpaca_app/infra/.generated/deployment.local.env`
  - `streamlit_alpaca_app/infra/.generated/email_delivery.local.env`
- Tracked docs:
  - `streamlit_alpaca_app/documents/infra/RESOURCE_INDEX.md`
  - `streamlit_alpaca_app/documents/infra/UI_DEPLOYMENT_STATUS.md`

## Reliability notes

- This keeps git clean without adding a decryption key workflow.
- The live inventory script uses Azure as the source of truth when local files are missing.
- The generated local files still exist for operator convenience, but they are no longer part of the tracked repo state.
