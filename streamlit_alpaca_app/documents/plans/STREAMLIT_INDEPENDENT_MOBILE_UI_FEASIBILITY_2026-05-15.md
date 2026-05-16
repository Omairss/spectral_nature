# Streamlit Independent Mobile UI Feasibility (2026-05-15)

## Question

Can we build an independent mobile UI for the existing Streamlit app, without using the native iPhone app path?

## Short Answer

Yes, it is feasible.

The right target is an independent **mobile presentation layer** inside the Streamlit web app, not an independent mobile product stack.

That means:

- Same data loaders, services, auth model, materialized datasets, and agent contracts.
- Same section names and navigation state.
- Different mobile renderers, mobile shell, page order, and mobile interaction design.

Do not duplicate business logic or create mobile-only data paths.

## High-Standard Review Result

The original proposal is directionally correct, but it is not implementation-ready without tightening.

It passes on:

- source boundary: mobile is presentation-only
- avoiding a separate Streamlit app by default
- preserving shared data and agent contracts
- identifying the sidebar as the first source problem

It needs corrections on:

- layout detection cannot rely mainly on user-agent heuristics
- mobile renderers need shared page view-models to avoid drift
- auth, invite, reset, and public-home flows must be in scope
- rollback controls need to exist before dev deployment
- acceptance criteria need actual device/view matrices, not only "looks better"

Verdict: feasible, but only high-standard if built as a feature-flagged, view-model-backed mobile presentation layer with browser-render regression checks.

## Why This Makes Sense

The rendered iPhone check showed a source-level shell problem:

- Streamlit sidebar navigation renders off-screen on mobile.
- Desktop page bodies technically render, but they read like compressed dashboards.
- Chat + Search, Market Explorer, Stock Investigator, Broad Economy, and Portfolio need different first-screen priorities on a phone.

CSS-only patching can fix some spacing, but it cannot reliably turn desktop workflows into phone workflows. A mobile presentation layer is the cleaner path.

## Current System Fit

The repo already has a workable boundary:

- `app.py` owns routing, layout, widgets, and browser-facing flow.
- `services/`, `compute/`, `data_access/`, and pipelines own product logic and data.
- `presentation/` already exists as a place for UI-facing helpers.
- `st.context.headers` is available in Streamlit `1.56.0`, and the app already has `_request_user_agent()`.

This means we can detect likely mobile clients and route them through a mobile shell without changing backend contracts.

## Key Constraint

Streamlit is server-rendered. Pure CSS breakpoints can hide or style elements, but Python still decides which widgets and layouts are emitted.

For a truly different mobile UI, the app needs a Python-side layout mode:

```python
layout_mode = _resolve_layout_mode()
if layout_mode == "mobile":
    _render_mobile_shell(...)
else:
    _render_desktop_shell(...)
```

Initial detection can use:

- explicit query param: `?layout=mobile`
- user preference in `st.session_state`
- user-agent heuristic from `st.context.headers`

For precise viewport-width detection, we would need a small Streamlit component or a dependency that reports browser width back to Python. That is optional for the first usable version.

## Recommended Architecture

### 1. Layout Mode Resolver

Add a small resolver near the routing layer:

- explicit modes: `desktop`, `mobile`, `auto`
- desktop remains the default unless a feature flag enables mobile auto mode
- `layout=mobile` and `layout=desktop` query params override auto detection
- a user/session preference can persist the override
- user-agent should only seed `auto`; it must not be the only way to enter or exit mobile layout
- allow manual override to desktop because tablets, narrow desktop windows, and browser devtools are ambiguous

Suggested controls:

- `STREAMLIT_MOBILE_UI_ENABLED=false|true`
- `STREAMLIT_LAYOUT_MODE_DEFAULT=desktop|mobile|auto`
- `?layout=mobile`
- `?layout=desktop`

### 2. Mobile Shell

Create a mobile shell that replaces the sidebar dependency.

Core pieces:

- compact top brand/header
- top section selector or compact nav menu
- snapshot date control only where relevant
- workspace status collapsed low on the page
- mobile-specific page padding and typography

The desktop sidebar should remain unchanged for desktop mode.

### 3. Shared Page View-Models

Before adding many mobile renderers, extract page data assembly into shared page view-model functions where the desktop page currently mixes loading and rendering.

Examples:

- `build_home_page_view_model(...)`
- `build_chat_search_view_model(...)`
- `build_market_explorer_view_model(...)`
- `build_stock_investigator_view_model(...)`
- `build_broad_economy_view_model(...)`
- `build_portfolio_view_model(...)`

These do not own business logic. They should:

- call existing loaders/services
- normalize display-ready rows
- carry provenance, empty states, loading/error state, and stable anchors
- return one payload consumed by both desktop and mobile renderers

This is the main guard against desktop/mobile drift.

### 4. Mobile Page Renderers

Introduce mobile renderers incrementally:

- `_render_mobile_home(...)`
- `_render_mobile_agentic_omnibar_section(...)`
- `_render_mobile_market_explorer(...)`
- `_render_mobile_stock_investigator(...)`
- `_render_mobile_broad_economy(...)`
- `_render_mobile_portfolio(...)`

These should consume shared view-models or shared loader outputs. They should not recompute data differently.

### 5. Shared Presentation Helpers

Add helpers under `presentation/` or a small UI module:

- mobile metric stack
- mobile card renderer
- mobile action row
- mobile section header
- mobile data table wrapper
- mobile chart wrapper

Keep helpers boring and explicit. The goal is fewer scattered `st.columns(...)` calls in mobile renderers.

### 6. Section Registry

Keep one section list and one normalization path.

The current app already has:

- `BASE_SECTION_OPTIONS`
- `_section_options()`
- `_normalize_workspace_section(...)`
- `_pending_workspace_section`
- `workspace_section`

Mobile should reuse those. Do not create a parallel section taxonomy.

### 7. Auth and Public Flows

Mobile cannot be considered usable if only authenticated dashboard pages work.

Include:

- public home
- sign in
- create account
- forgot password
- reset password links
- invite activation links
- logout/session-expired states

These flows should remain backed by the existing auth service and store. Do not add mobile-specific auth contracts.

## Implementation Needs

### Code Work

1. Add layout mode resolver.
2. Add mobile feature flag and rollback envs.
3. Split current shell into desktop shell and mobile shell.
4. Add mobile nav that works without the Streamlit sidebar.
5. Extract shared view-models for the first two target pages.
6. Build mobile renderers for the highest-value sections.
7. Add mobile-specific CSS scoped to a mobile wrapper class.
8. Add screenshot regression script.
9. Deploy to dev and verify mobile plus desktop.

### Testing Work

Need browser render checks, not just unit tests.

Minimum checks:

- iPhone viewport can navigate sections without off-screen sidebar.
- no horizontal document overflow.
- key text appears for Home, Chat + Search, Market Explorer, Stock Investigator, Broad Economy, Portfolio.
- public-home and auth-action screens render at mobile width.
- desktop screenshots still show the current sidebar and expected first screen.
- mobile and desktop run paths do not both render expensive page bodies.

Minimum viewport matrix:

- `375x667` iPhone SE-style small phone
- `390x844` iPhone 13-style phone
- `430x932` large phone
- `768x1024` tablet boundary
- `1440x900` desktop

Minimum state matrix:

- auth disabled local
- unauthenticated public home
- authenticated normal user
- admin user for Admin visibility only; Admin does not need full mobile redesign in the first pass
- degraded data state, including missing live Alpaca account or missing materialized snapshot

Use local artifact output like:

```bash
/tmp/spectral-mobile-check/
```

### Documentation Work

Keep updates in:

- `documents/plans/STREAMLIT_MOBILE_REMEDIATION_2026-05-05.md`
- this feasibility file
- `documents/learnings.md` or `documents/mistakes.md` only when a durable rule or mistake emerges

## Scope Options

### Option A — CSS-Only Responsive Patch

Feasibility: high  
Time: short  
Quality: limited

Would fix:

- sidebar visibility
- typography scale
- padding/card size
- some clipped buttons

Would not fully fix:

- page order
- Chat + Search workflow
- dashboard-first information architecture

Use only as a temporary patch.

### Option B — Independent Mobile Presentation Layer

Feasibility: high  
Time: medium  
Quality: good

Would fix:

- mobile shell and navigation
- mobile-first page order
- compact controls
- phone-specific Chat + Search workflow
- summary-first dashboard views

This is the recommended path.

### Option C — Separate Streamlit Mobile App

Feasibility: medium  
Time: medium/high  
Quality: mixed

A separate Streamlit entrypoint could work, but it adds deployment and auth complexity. It risks drifting from the main app unless it imports only shared render/data modules.

This is not recommended unless the single-app layout resolver becomes too awkward.

### Option D — Native iPhone App

Feasibility: already documented  
Time: high  
Quality: best long-term mobile product

Out of scope for this request.

## Risks

1. **Desktop regressions**
   - Mitigation: desktop remains default; mobile CSS is scoped; screenshot both modes.

2. **Duplicate business logic**
   - Mitigation: shared page view-models feed both desktop and mobile renderers.

3. **Widget key collisions**
   - Mitigation: mobile widgets use intentional key prefixes, e.g. `mobile_...`, except where shared state is desired.

4. **Unreliable mobile detection**
   - Mitigation: use user-agent heuristic plus manual override; add viewport component only if needed.

5. **Monolithic `app.py` slows changes**
   - Mitigation: extract mobile renderers and helpers into `presentation/` or focused modules once the first shell works.

6. **Render-time cost**
   - Mitigation: do not render both desktop and mobile page bodies and hide one with CSS. Choose one branch server-side.

7. **Auth regressions**
   - Mitigation: include login, reset, invite, logout, and session-expired paths in the mobile acceptance matrix.

8. **False sense of mobile support**
   - Mitigation: define which sections are mobile-ready and show a deliberate fallback for sections not yet redesigned.

## Proposed Build Sequence

1. Add feature flag, `layout_mode` resolver, and manual override.
2. Add mobile shell and mobile nav.
3. Move existing desktop sidebar/routing into a desktop shell wrapper without changing behavior.
4. Build a render-check script before doing broad page work.
5. Extract shared view-models for Home and Chat + Search.
6. Implement mobile Home and Chat + Search first.
7. Verify desktop parity for Home and Chat + Search.
8. Add mobile auth/public flow coverage.
9. Add mobile Market Explorer, Stock Investigator, Broad Economy.
10. Add mobile Portfolio once the dev/local account data path can be verified.
11. Run desktop and mobile screenshots locally.
12. Deploy to dev only with rollback env documented.
13. Verify dev mobile URL manually.

## Definition of Done

The mobile UI is not done until all of these are true:

- iPhone users can navigate without opening or depending on the Streamlit sidebar.
- first viewport on each mobile-ready page has a clear task and primary content.
- no clipped primary controls or suggestion buttons.
- no horizontal overflow in the viewport matrix.
- desktop default view is visually and functionally unchanged except for intentional improvements.
- mobile and desktop use the same page data/view-model contracts.
- auth and public account-management flows work on mobile.
- a single env/config rollback can disable the mobile presentation layer.
- dev deployment is verified on actual dev URL before any prod discussion.

## Feasibility Verdict

This is possible and worth doing if mobile web matters.

The most reliable version is a mobile presentation layer inside the existing Streamlit app. It avoids a second product stack while giving mobile enough independence to solve the actual workflow problems. The critical engineering rule is to branch only at the presentation layer; data, agents, auth, and materialized snapshots stay shared.
