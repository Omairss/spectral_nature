# Development Dataset Namespace

Date: 2026-09-20

## Purpose

Development must be able to run materialized pipelines and render their output
without replacing production data pointers, Postgres dataset metadata, job
status, or Trading Agent decisions.

The shared contract is `PIPELINE_DATASET_NAMESPACE`:

- unset, `production`, `prod`, or `default`: existing production behavior;
- `development`: isolated development behavior;
- another validated lowercase slug: another isolated namespace.

Production paths and identifiers are unchanged when the namespace is empty.

## Isolation Contract

For `PIPELINE_DATASET_NAMESPACE=development`:

- blobs live under `namespaces/development/datasets/...`;
- manifests live under `namespaces/development/manifests/...`;
- dataset versions use the `development__...` prefix;
- Postgres `dataset_versions.dataset_name` uses
  `development::{canonical_dataset_name}`;
- Postgres `job_runs.job_name` uses
  `development::{canonical_job_name}`;
- Trading Agent actions use `dataset_namespace=development`;
- local frame and metadata caches use the namespaced dataset identity;
- product readers continue to expose canonical dataset and job names.

The namespace applies to materialized pipeline artifacts and their operational
metadata. Zopedia is still the shared knowledge system, so isolated Trading
Agent jobs must set `TRADING_AGENT_ZOPEDIA_WRITE_POLICY=none` unless a separate
memory namespace is designed and explicitly approved.

## Initialization

`scripts/initialize_dataset_namespace.py` copies the latest materialized
snapshot for every dataset by default. It copies the referenced parquet,
rewrites the version and blob identities, and writes both a version manifest
and stable `latest.json` pointer in the destination namespace.

The initializer is idempotent by default: an existing destination latest
manifest is skipped. `--overwrite` is required to refresh an initialized
namespace from production.

Example:

```bash
.venv/bin/python scripts/initialize_dataset_namespace.py \
  --source production \
  --destination development
```

Use repeated `--dataset` arguments to initialize only selected datasets, or
`--dry-run` to verify the copy plan without writes.

## Azure Development Resources

Initialized on 2026-09-21:

- Namespace: `development`
- Storage container: `datasets`
- Baseline copied: 83 latest production snapshots
- Manual job: `trading-agent-build-dev`
- Job trigger: manual only
- Job image: `pipeline-jobs:dev-all-signal-v3-20260921051835`
- Job image digest: `sha256:4d32fd0ea536235309151e1f76f2a09ae35040944d38a9e9681c910b2c2aede5`
- Dev UI: `sn-streamlit-ui-dev`
- Dev UI revision: `sn-streamlit-ui-dev--0000440`
- Dev UI image digest: `sha256:b22bdbb6bbd168f7e6ab0a6398f888a5e11b5f02e2d78df67a03d85d5c9b84fd`

The scheduled production `trading-agent-build` job and production UI were not
changed.

## Verification

- Namespace-focused pipeline, store, Trading Agent service, and view tests:
  `103 passed`.
- The development UI revision became ready and returned HTTP 200.
- The live UI has `PIPELINE_DATASET_NAMESPACE=development`.
- The manual development job has `PIPELINE_DATASET_NAMESPACE=development` and
  `TRADING_AGENT_ZOPEDIA_WRITE_POLICY=none`.

The first manual execution, `trading-agent-build-dev-q5xv656`, succeeded on
2026-09-21. Its internal run ID was
`b24b3ae9-48d5-44be-87b2-d6b800d3cfcd`. It completed all eight company
research packages and all five horizon runs, then wrote 14 candidates and 14
outcomes into development-owned dataset versions. All candidates were
research-only `no_call` rows, so all outcomes were correctly unscoreable and
no action rows were created.

The namespaced artifacts were reloaded through the exact Trading Agent view
parsers. All 5 horizon results and all 14 candidates passed the
`plain_investor_research_v2` presentation gate. A scan of the persisted
investor prose found no occurrences of the rejected storage, scanner, or raw
volume language. The deployed development UI health endpoint returned `ok`.

The ranking upgrade was accepted in a later manual execution,
`trading-agent-build-dev-j6jgxgt`. It consumed a development-only 999-symbol
All Market retrieval corpus, completed 8/8 company packages and 5/5 horizons,
and persisted 12 current candidates. All 12 passed the same view contract; the
only directional call was a one-week FLNC short and no action row was created.

The all-signal upgrade was verified by execution
`trading-agent-build-dev-ue1wrmg`, internal run ID
`0ce40dea-5b75-4460-a654-69df89f13773`. It saved 5,994 ranked rows covering
999 stocks across six market horizons, completed 8/8 company packages and 5/5
Trading Agent runs, and saved 18 candidates. All saved rank rows use
`domain_balanced_all_signal_v1`, and all five run contexts record
`candidate_retrieval_method=all_signal_rank`. Production pointers and Zopedia
memory were unchanged.

The first namespaced Trading Agent execution and artifact review are recorded
in `documents/plans/TRADING_AGENT_CALL_CONTRACT_REPAIR_2026-09-19.md`.
