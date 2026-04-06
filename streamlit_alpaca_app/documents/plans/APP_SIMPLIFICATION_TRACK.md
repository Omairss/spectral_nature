# App Simplification Track

## Goal

Reduce `app.py` and other coordinator modules by moving pure shaping, loading, and rendering helpers behind stable module boundaries without changing behavior.

## Completed

### Query dispatch

- `data_access/query_service.py` now routes through `data_access/query_registry.py`.
- Capability metadata and handler registration live in one place instead of a long branch chain.

### Presentation loaders

- `presentation/dashboard_loaders.py` owns DAL-backed loader wrappers, public price fallback, ticker snapshot assembly, attention bundle loaders, and homepage digest loaders.
- `app.py` configures the loader module once and keeps compatibility aliases for existing call sites.

### Attention content shaping

- `presentation/attention_content.py` owns:
  - attention event keys and copy normalization
  - homepage/detail payload shaping
  - brief input assembly
  - attention news/context payload collection
  - attention micro-chart construction
- `app.py` keeps compatibility aliases so the render flow did not need to change.
- The extraction also removed one dead duplicate helper from `app.py` and fixed article summary fallback so missing values no longer render as the literal string `"nan"`.

## Current boundaries

- `app.py`: Streamlit shell, state wiring, and large render sections
- `presentation/dashboard_loaders.py`: data-loading and cached aggregation helpers
- `presentation/attention_content.py`: attention/homepage content shaping helpers

## Next safe cuts

1. Move one render domain at a time out of `app.py`, starting with homepage/attention panels.
2. Split `data_access/layer.py` by domain resolver once current churn settles.
3. Break `pipeline/jobs/main.py` into per-domain job modules.

## Guardrails

- Keep compatibility aliases in `app.py` during each extraction so call sites do not move at the same time as logic.
- Add direct unit tests for newly extracted helper modules.
- Do not deploy a dirty worktree snapshot when unrelated in-progress changes are mixed into the same build context.
