# BMY Background Fallback Fix (2026-04-03)

## Problem
`resolve_attention_ticker_background("BMY")` was returning fallback text:
- `No relevant catalyst found in web coverage...`
- no evidence links

This occurred even when the materialized ticker-background snapshot already contained non-empty `description_text`, `news_summary_lines`, and `recent_headlines`.

## Root Cause
In `_overlay_background_payload_from_bundle(...)` the bundle overlay path replaced materialized fields whenever bundle-derived `recent_headlines` were empty. That path forcibly wrote fallback text and an empty headline list, even when base payload had usable context and links.

## Changes
- Updated overlay merge logic in `data_access/layer.py`:
  - Prefer bundle headlines only when bundle extraction returns relevant headlines.
  - Otherwise preserve base materialized `description_text`, `news_summary_lines`, and `recent_headlines`.
  - Keep fallback behavior only when both bundle and base headline sets are empty.
  - Preserve existing source-trace counters and provider mix when bundle contributes no relevant headlines.
- Added stale memoized fallback recovery in `presentation/dashboard_loaders.py`:
  - Detect stale compact fallback payloads (`No relevant catalyst found...`) when `source_trace.headline_count > 0` but `recent_headlines` is empty.
  - Automatically re-run uncached background load with `force_refresh=False` to pull fresh materialized payload without on-demand recompute.
- Updated compact section rendering in `app.py`:
  - If either `Background` or `What Happened` text exists, reuse it for both sections before using generic fallback.

## Reliability / Complexity
- Reliability: improved (removes false-negative fallback for symbols like BMY).
- Complexity: low (localized merge logic update + targeted regression coverage).
- No deployment-only compute changes were introduced.

## Validation
- Unit tests:
  - `test_resolve_attention_ticker_background_reports_no_relevant_agentic_news` (still passes)
  - `test_resolve_attention_ticker_background_keeps_materialized_context_when_bundle_has_no_web_headlines` (new)
- Manual resolver check:
  - `DataAccessLayer().resolve_attention_ticker_background("BMY", force_refresh=False)` now returns:
    - non-fallback `description_text`
    - non-empty `news_summary_lines`
    - `recent_headlines_count = 6`
- Loader tests:
  - `tests/test_dashboard_loaders.py` validates stale payload detection and cache bypass behavior.

## Deployment
- Development UI deployed on 2026-04-03:
  - revision: `sn-streamlit-ui-dev--0000132`
  - image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:608b88ca9d62d6e1657c22708239e49df4d398c3b797c48ef57a1d7b1f5d9c2d`
  - health check: `/_stcore/health -> ok`
