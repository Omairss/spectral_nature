# Streamlit Mobile Remediation Plan (2026-05-05)

## Goal

Make the existing Streamlit app usable on iPhone/mobile web without building or relying on the native iPhone app.

This is a Streamlit web remediation track. It should keep the same backend contracts and materialized data paths. Do not create mobile-only business logic.

## Render Findings

Validated locally with Streamlit at an iPhone-sized viewport (`390x844`) using Chrome/Playwright screenshots under `/tmp/spectral-mobile-check/`.

Screens checked:

- Home
- Chat + Search
- Market Explorer
- Stock Investigator
- Broad Economy
- Portfolio

Main findings:

- Sidebar navigation is functionally broken at iPhone width. Navigation buttons render off-screen around `left: -280px`, so normal clicks fail.
- Pages render as compressed desktop dashboards, not mobile-first flows.
- Typography and controls are too large for phone workflows: huge page titles, large cards, large buttons, and excessive vertical spacing.
- Chat + Search is the worst mobile workflow: Back/Clear become huge stacked buttons, suggestions clip mid-text, and the input sits too low.
- Data-dense pages need a summary-first mobile layout, not side-by-side chart/table regions or multi-column metric bands.
- Portfolio could not be fully data-validated locally because Alpaca returned `401`, but the layout showed the same desktop-card pattern.

## Implementation Status — 2026-05-18

First high-standard slice is implemented locally:

- Feature flag: `STREAMLIT_MOBILE_UI_ENABLED=true`.
- Layout resolver: `desktop`, `mobile`, `auto`, plus explicit `?layout=mobile` / `?layout=desktop`.
- Mobile shell: top-of-page brand, navigation selectbox, snapshot date on Home, workspace status, logout.
- Desktop shell remains the default and still uses the sidebar.
- Mobile CSS is only injected for mobile mode and fixes page padding, heading density, controls, dataframes, tabs, and mobile code-block wrapping.
- Render harness added at `scripts/mobile_render_check.mjs`.

Verified locally with:

```bash
STREAMLIT_MOBILE_UI_ENABLED=true STREAMLIT_LAYOUT_MODE_DEFAULT=desktop DASHBOARD_AUTH_ENABLED=false APP_TRACK=local \
  .venv/bin/streamlit run app.py --server.port 8509 --server.address 127.0.0.1 --server.headless true

NODE_PATH=/tmp/spectral-pw/node_modules \
  node scripts/mobile_render_check.mjs --url http://127.0.0.1:8509 --out /tmp/spectral-mobile-check-final2 --mode both --sections Home,Zopedia
```

Latest local artifacts:

- `/tmp/spectral-mobile-check-final2/mobile-home.png`
- `/tmp/spectral-mobile-check-final2/mobile-zopedia.png`
- `/tmp/spectral-mobile-check-final2/desktop-home.png`
- `/tmp/spectral-mobile-check-final2/report.json`

Result: no document horizontal overflow at `390x844`; mobile navigation selects Home and Zopedia without sidebar buttons; desktop Home still renders sidebar navigation at `1440x1000`.

Dev deploy remains pending because the current worktree contains broad unrelated changes. Deploying UI now would ship more than this mobile slice unless the diff is isolated or explicitly accepted.

## Implementation Status — 2026-05-19

Second remediation slice is implemented locally:

- All direct `st.columns(...)` calls in `app.py` now route through `_responsive_columns(...)`, so mobile mode gets stacked containers while desktop keeps the original column specs.
- Mobile CSS now wraps labels, button text, metric labels, tables/dataframes, Plotly surfaces, images, iframes, code blocks, and chat surfaces.
- Zopedia mobile no longer renders the redundant Back/Clear control row before the page title. The mobile shell owns navigation and the Zopedia toolbar owns "New".
- Home mobile now shows the narrative summary first and moves the graph into a collapsed "Market graph" expander so the first viewport is not dominated by a dense network plot.
- The render harness was rerun across the original remediation matrix: Home, Zopedia, Market Explorer, Stock Investigator, Broad Economy, Portfolio, plus desktop Home.

Verified locally with:

```bash
streamlit_alpaca_app/.venv/bin/python -m py_compile streamlit_alpaca_app/app.py

PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/pytest -q \
  streamlit_alpaca_app/tests/test_auth_routing_guards.py \
  streamlit_alpaca_app/tests/test_chat_log.py \
  streamlit_alpaca_app/tests/test_omnibar_agent.py \
  streamlit_alpaca_app/tests/test_zopedia_memory.py \
  streamlit_alpaca_app/tests/test_presentation_attention_content.py

NODE_PATH=/tmp/spectral-pw/node_modules \
  node scripts/mobile_render_check.mjs \
  --url http://127.0.0.1:8509 \
  --out /tmp/spectral-mobile-check-20260519c \
  --mode both \
  --sections "Home,Zopedia,Market Explorer,Stock Investigator,Broad Economy,Portfolio"
```

Results:

- `51 passed in 4.85s` for the final targeted test run.
- Render check passed with no horizontal overflow at `390x844` for every mobile section and no horizontal overflow at `1440x1000` for desktop Home.
- Latest local artifacts:
  - `/tmp/spectral-mobile-check-20260519c/mobile-home.png`
  - `/tmp/spectral-mobile-check-20260519c/mobile-zopedia.png`
  - `/tmp/spectral-mobile-check-20260519c/mobile-market-explorer.png`
  - `/tmp/spectral-mobile-check-20260519c/mobile-stock-investigator.png`
  - `/tmp/spectral-mobile-check-20260519c/mobile-broad-economy.png`
  - `/tmp/spectral-mobile-check-20260519c/mobile-portfolio.png`
  - `/tmp/spectral-mobile-check-20260519c/desktop-home.png`
  - `/tmp/spectral-mobile-check-20260519c/report.json`

Observed limitation:

- Local Portfolio still shows an Alpaca authorization warning in the screenshot because the local dev credentials are not authorized. The mobile layout path itself rendered without overflow and stacked account cards correctly.

Dev deployment:

- Dev UI deploy completed on 2026-05-19.
- Ready revision: `sn-streamlit-ui-dev--0000332`.
- Image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:d9e141be22606daa82eadfc174f36623409ebdb9ca7032a1255fa0ea710aef36`.
- Root smoke check returned HTTP 200.
- Runtime env verified: `STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=desktop`, `APP_TRACK=development`.
- No production deployment was performed.

Post-trace-fix regression check:

- Local mobile render harness passed after the Zopedia thinking-trace persistence fix.
- Command:

```bash
NODE_PATH=/tmp/spectral-pw/node_modules \
  node scripts/mobile_render_check.mjs \
  --url http://127.0.0.1:8511 \
  --out /tmp/spectral-mobile-check-local-20260519-trace \
  --mode both \
  --sections "Home,Zopedia,Market Explorer,Stock Investigator,Broad Economy,Portfolio"
```

- Report: `/tmp/spectral-mobile-check-local-20260519-trace/report.json`.

## Principles

1. Fix the shell first. If users cannot navigate, page-level tweaks do not matter.
2. Use one responsive Streamlit app, not separate mobile business logic.
3. Keep expensive work out of render paths. Mobile should read the same materialized datasets.
4. Collapse columns at the rendering boundary. Do not change upstream data contracts for layout.
5. Test with real browser screenshots at `390x844`, not only source inspection.
6. Use shared page view-models before adding broad mobile renderers, so desktop and mobile cannot drift.
7. Include public/auth flows; a mobile dashboard is not usable if sign-in, reset, invite, or logout states are broken.
8. Keep a rollback flag for mobile presentation mode before deploying to dev.

## Phase 1 — Mobile Shell

Fix navigation and global density.

Changes:

- Add a mobile UI feature flag and layout-mode resolver (`desktop`, `mobile`, `auto`).
- Add a mobile breakpoint in `_ensure_app_shell_styles()`.
- At small widths, replace reliance on Streamlit's sidebar with an in-page top navigation control.
- Keep desktop sidebar behavior for wider screens.
- Reduce mobile page padding and card padding.
- Remove negative heading letter-spacing on mobile and cap large headings.
- Add a compact mobile page header helper for title + primary action/back.

Target files:

- `app.py`
- possibly `presentation/` if helpers are extracted

Validation:

- iPhone viewport can navigate to every top-level section without forced JS.
- No section requires the off-screen sidebar to proceed.
- Screenshots: Home, Chat + Search, Market Explorer, Stock Investigator, Broad Economy, Portfolio.

## Phase 2 — Shared Responsive Layout Helpers

Stop scattering raw `st.columns(...)` decisions through page bodies.

Changes:

- Extract shared page view-models for the first mobile-ready sections.
- Add small UI helpers for:
  - metric stacks
  - header/action rows
  - two-column panels that collapse to vertical on mobile
  - button rows that become stacked mobile actions
- Use helpers in the highest-impact pages first.
- Keep helper behavior simple and deterministic. Prefer container-width layouts over hardcoded pixel widths.

Initial targets:

- Home attention overview
- Chat + Search
- Market Explorer
- Stock Investigator
- Broad Economy
- Portfolio

Validation:

- Multi-metric bands become readable vertical or 2-up mobile stacks.
- Page controls remain tappable and not clipped.
- Charts/tables appear after a short summary, not above all context.

## Phase 3 — Chat + Search Mobile Workflow

Make Chat + Search feel like a phone workflow.

Changes:

- Replace giant Back/Clear button row with compact controls.
- Keep the query input visible and reachable near the first viewport.
- Shorten or wrap suggestion buttons without clipping.
- Use a single-column answer layout on mobile.
- Keep thinking trace and debug details collapsed by default.

Validation:

- User can type a question without scrolling past large suggestion blocks.
- Suggestions fit without clipped words.
- Answer, sources, and trace are readable as a vertical flow.

## Phase 4 — Data-Dense Page Mobile Pass

Adapt dashboard pages without changing data ownership.

Changes:

- Market Explorer: summary first, filters second, table/chart sections below.
- Stock Investigator: ticker input + core snapshot first, charts below, summary card not dimmed by loading state.
- Broad Economy: top macro summary first, then controls, then chart/table groups.
- Portfolio: account status first, then core balances, then positions and charts.
- Home: reduce graph dominance on mobile; show narrative headline first or make graph optional/collapsed.

Validation:

- First viewport answers "where am I and what should I do next?"
- Tables are scrollable when needed, but not the primary first-screen object.
- No critical action is below large decorative or secondary content.

## Phase 5 — Regression Harness

Add repeatable render checks.

Changes:

- Add a small script under `scripts/` that starts or targets a Streamlit URL and captures mobile screenshots.
- Check:
  - viewport width
  - document horizontal overflow
  - visible navigation availability
  - key text presence per section
  - public/auth flows at mobile width
  - desktop first-screen parity
- Keep screenshots as local artifacts, not committed generated files.

Suggested command shape:

```bash
DASHBOARD_AUTH_ENABLED=false streamlit run app.py --server.port 8509 --server.address 127.0.0.1
node scripts/mobile_render_check.mjs --url http://127.0.0.1:8509 --out /tmp/spectral-mobile-check
```

Validation:

- Render check fails if navigation is off-screen or if document width exceeds the viewport.
- Manual screenshot review remains part of UI changes.
- Minimum viewport matrix: `375x667`, `390x844`, `430x932`, `768x1024`, `1440x900`.

## Reliability / Complexity Assessment

High reliability:

- Mobile shell breakpoint and in-page navigation.
- Mobile typography/padding adjustments.
- Shared wrappers around existing `st.columns` usage.

Medium complexity:

- Refactoring page layouts out of monolithic `app.py` without changing behavior.
- Making Chat + Search mobile-friendly while preserving desktop behavior.

Avoid:

- A WebView-only workaround.
- Mobile-only backend logic.
- CSS hacks that hide broken desktop assumptions without fixing navigation.
- user-agent-only mobile detection with no manual override.
- rendering both desktop and mobile expensive page bodies and hiding one with CSS.
- Large page rewrites before the shell is fixed.

## Execution Order

1. Feature flag + layout-mode resolver.
2. Shell/navigation fix.
3. Render regression script.
4. Shared page view-models and responsive layout helpers.
5. Chat + Search mobile pass.
6. Public/auth flow mobile pass.
7. Home + Market Explorer + Stock Investigator + Broad Economy page passes.
8. Portfolio pass after a valid local/dev data path is available.
9. Dev deploy through the repo deploy path and verify the affected screens.

## Deployment Note

Per project policy, deploy Streamlit UI changes to dev after implementation using:

```bash
scripts/which_deploy.sh --check ui
bash scripts/deploy_ui_azure.sh --target dev
```

Do not promote to prod without explicit permission.
