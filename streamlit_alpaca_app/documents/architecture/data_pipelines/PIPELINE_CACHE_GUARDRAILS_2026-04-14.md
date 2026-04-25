# Pipeline Cache Guardrails - 2026-04-14

## Goal

Stop `cache/pipeline_store` from:

- creating repo churn
- growing without bound in local or container runtime
- pretending to be durable storage when it is only a local read-through cache

## Design

- Keep Azure Blob + dataset manifests as the durable source of truth.
- Treat `cache/pipeline_store` as a best-effort local cache only.
- Ignore `cache/pipeline_store` in git.
- Add a shared cache cap in `services/pipeline_store.py`.

## Runtime Policy

- Default local pipeline-store cache cap: `256 MiB`
- Override env: `PIPELINE_CACHE_MAX_BYTES`
- If a frame cache file is larger than the cap by itself, do not keep it locally.
- After each cache write, prune the oldest cached dataset-version directories and metadata files until usage is back under the cap.

## Why This Approach

- It fixes the real problem at source in the shared cache layer.
- It avoids adding new infrastructure for a local-only concern.
- It keeps cold-start behavior correct because the durable data already lives in blob storage.

## Outcome

- New containers can still rebuild cache from blob storage.
- Long-lived containers no longer accumulate unbounded cached frames.
- Repo status no longer gets polluted by new pipeline cache artifacts.
