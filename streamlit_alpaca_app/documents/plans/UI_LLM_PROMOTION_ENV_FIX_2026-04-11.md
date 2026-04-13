# UI LLM Promotion Env Fix

Date: 2026-04-11

## Problem

Production Chat + Search lost tool-based analysis after a prod promotion even though development still worked.

## Root Cause

- The UI deploy script loaded the shared deployment env file but did not include any LLM runtime env keys when updating the UI container app.
- A prod promotion reused the dev image successfully, but the prod app never received the Azure OpenAI runtime config needed by `services/llm.py`.
- The local env example also failed to document the UI LLM runtime keys, which made the expected config easier to miss.

## Fix

- Treat UI LLM runtime settings as managed deploy-time env in `scripts/deploy_ui_azure.sh`.
- During `--promote-from`, backfill missing target values from the source app so new runtime keys do not silently disappear on prod.
- Document the required UI LLM env in `.env.example`.

## Verification

- Run `bash -n streamlit_alpaca_app/scripts/deploy_ui_azure.sh`.
- Promote prod from dev after the script change.
- Verify `sn-streamlit-ui` includes the expected `LLM_*` and `AZURE_OPENAI_*` env values.
- Verify Chat + Search no longer reports `LLM runtime is unavailable`.
