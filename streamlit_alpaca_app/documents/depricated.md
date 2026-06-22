# Deprecated Paths

This file tracks stale paths that should not be revived. If a deprecated path is still present, it must be there only for compatibility, redirect, or a short-lived migration.

## 2026-05-27

- `Home v2` and `Experiment` workspace routes: removed from `app.py` routing and admin navigation. Old saved section names are compatibility aliases only; they normalize back to `Home`.
- Old Home right-rail renderers: removed from `app.py`. Research and ticker details must stay nested inside the opened Home narrative group.
- Old Home click-time ticker background panel: removed from `app.py`. Home may reveal only precomputed bundle/background/Zopedia rows.
- `services/homepage_v2.py`: deleted. Shared Home helpers now live in `services/homepage_support.py`.
- Old Home v2 presentation helper tests/functions: deleted from `presentation/attention_content.py` and the matching tests because the active Home uses the nested materialized Home renderer.
- `documents/plans/HOMEPAGE_V2_RAIL_REFACTOR_PLAN.md`: deleted because it described the rejected right-rail layout and stale Home v2 state keys.
- Historical docs that referenced `services/homepage_v2.py` or `_render_homepage_exp(...)`: updated with explicit deprecated status so they are not mistaken for active implementation plans.
