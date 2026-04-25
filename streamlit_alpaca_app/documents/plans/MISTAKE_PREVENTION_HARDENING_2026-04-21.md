# Mistake Prevention Hardening

**Date:** 2026-04-21
**Status:** Implemented

Review of all 40 mistakes and 38 learnings, grouped into 6 structural failure patterns, with preventive changes applied.

---

## What Was Implemented

### 1. Deploy Guard Script — `scripts/which_deploy.sh`
**Prevents:** Mistakes #13, #19, #40 (deploying wrong container, missing env, identity drift)

- New script that classifies changed files by container (UI / Pipeline / API / Shared)
- `--check ui|pipeline|api` mode for automated warnings
- Wired into `deploy_ui_azure.sh` — warns and prompts for confirmation if no UI-relevant files changed

### 2. Deploy Boundary Comments
**Prevents:** Mistake #40 (deployed wrong container for pipeline-only change)

- Each deploy script now has a header block listing which source directories it covers
- Cross-references to the correct deploy script when files don't match

### 3. Pre-Commit Secret Scan — `scripts/scan_secrets.sh`
**Prevents:** Mistakes #2, #8, #10 (tracked secrets, fail-open auth)

- Scans staged files for patterns: AWS keys, Azure storage keys, connection strings, bearer tokens, private keys, password assignments
- Installed as `.git/hooks/pre-commit`
- Allowlist for .example, .md, .sh files that reference secret names (not values)
- Can also run standalone with `--all` flag for full-repo audit

### 4. Mandatory Dataset Validation in Attention Job
**Prevents:** Mistakes #3, #19 (stale data looking successful, green job hiding failures)

- Added `_MANDATORY_DATASETS` tuple in `attention_home_build.py`
- `_validate_mandatory_datasets()` function that loads and checks each before the build
- Currently gates on `price_history` — without it, narratives have no data
- Job now fails with a clear error instead of silently producing empty output

### 5. LLM/Embedding Readiness Checks — `services/llm.py`
**Prevents:** Mistakes #15, #36 (assumed embeddings live because client constructed, trusted local Azure CLI)

- `check_llm_readiness()` function returns status dict for LLM provider, model, deployment, embeddings
- Explicitly flags when `EMBEDDING_DEPLOYMENT` is unset (semantic retrieval disabled)
- Called once per UI session at startup, logged to `spectral_nature.ui_app` logger
- Operators can see in logs which capabilities are actually live

### 6. Attention Services Package — `services/attention/`
**Prevents:** Mistake #40 (cognitive overhead, wrong-file edits)

- New package at `services/attention/__init__.py`
- Re-exports all 12 attention_* modules as clean submodule names
- Both old (`from services.attention_market_events import ...`) and new (`from services.attention import market_events`) paths work
- Establishes the convention for new code to use the package namespace

---

## What Was NOT Implemented (Future Work)

These were identified as important but deferred:

1. **Hardcoding audit items 1-4** — Replace if/elif dispatch and template strings in `attention_market_events.py` and `attention_live_research.py` with LLM calls. See `HARDCODING_AUDIT_NLP_LLM_OPPORTUNITIES_2026-04-16.md`.

2. **SAA wiring for homepage summary** — Route homepage summary evidence through shared AQL trace. See `documents/architecture/attention/ATTENTION_HOME_SUMMARY_SAA_WIRING_2026-04-15.md`.

3. **Post-deploy smoke test script** — `scripts/smoke_test.sh` that hits health endpoints and product paths after deploy.

4. **Pipeline store `max_age_hours`** — Add staleness check to `pipeline_store.load()` so old datasets get flagged.

5. **Plans status audit** — Mark all 87 plan files with explicit Status headers and group README by status.
