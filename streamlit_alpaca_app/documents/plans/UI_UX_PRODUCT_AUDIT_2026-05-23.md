# Spectral Nature UI/UX Product Audit - 2026-05-23

## Scope

I used Spectral Nature locally in two modes:

- `http://127.0.0.1:8501` with auth disabled and no runtime secrets, to inspect degraded states.
- `http://127.0.0.1:8502` with Key Vault-backed LLM/FRED/Alpaca env configured, to exercise live product paths where possible.

Screenshots are stored in `documents/ui_ux_audit_artifacts_2026_05_23/`.

Covered surfaces:

- Home
- Zopedia
- Broad Economy
- Market Explorer
- Portfolio
- Portfolio Performance
- Stock Investigator
- Option Strategizer
- normal mobile URL at iPhone-sized viewport

Trading Agent could not be reached through the visible sidebar navigation in this run.

## Executive Summary

The strongest product surfaces are the Home market narrative, Market Explorer feed, Broad Economy dashboard, and Zopedia's actual grounded answer path. The product has real substance: cached market narrative, working FRED dashboard, useful ticker handoff patterns, and Zopedia can combine retained evidence with current data.

The main UX problem is not capability. It is visual and information hierarchy. Several screens expose setup/debug language, raw trace internals, confidence plumbing, or dense controls before the user's primary answer. Mobile is the biggest product risk: the normal mobile URL rendered the desktop sidebar and started with an operational credential banner and graph before the answer.

## High Priority Findings

1. **Normal mobile URL rendered the desktop shell.**

   Evidence: `mobile_home_first_view.png` shows the desktop sidebar/navigation and first viewport layout at `390x844`.

   Why it matters: mobile rollout cannot be considered live if a normal phone-sized browser reaches the desktop shell. This repeats the exact risk captured in `learnings.md` #152-153 and `mistakes.md` #113-115.

   Recommendation: make normal-URL mobile detection a required browser gate. Verify desktop and phone viewports after every UI deploy, not only `?layout=mobile`.

2. **Degraded local/runtime states use operational copy as the primary product content.**

   Evidence: Home/Market Explorer first viewport showed "Market data credentials are unavailable" plus Key Vault shell commands above the product.

   Why it matters: setup instructions are useful for developers, but they crowd out the product and violate the "product screens hide plumbing" principle. A cached Home narrative was available below the banner, so the product had something useful to show.

   Recommendation: show cached product content first. Move Key Vault and command details behind Workspace Status or Admin/debug. The user-facing banner should be one compact unavailable-state sentence.

3. **Zopedia trace can overwhelm the answer after a successful run.**

   Evidence: `kv_zopedia_quantum_answer.png` landed on steps 12-14 of the thinking trace, not the answer. The visible text included model reasoning and memory-action deliberation.

   Why it matters: the trace is valuable, but when it takes over the viewport it makes Zopedia feel like an agent console rather than a research conversation.

   Recommendation: default the saved/completed state to answer-first, with a compact trace summary and an expandable full trace. Keep live progress visible during execution, but collapse raw model reasoning after completion unless the user opens it.

4. **Broad Economy includes strong content but mixed hierarchy.**

   Evidence: `kv_broad_economy.png` showed a useful Zopedia Summary and macro charts, but the floating Zopedia/chat layer text appears interleaved in the page text and the screenshot lands deep in chart content after load.

   Why it matters: the page has enough data to be excellent, but the primary macro readout competes with global chat/context controls and low-confidence labels.

   Recommendation: keep Broad Economy answer-first: summary, watch items, top macro gauges, then charts. Move chat/source rail controls out of the main reading flow and hide low-confidence labels unless they change a decision.

5. **Trading Agent exists in code but was not visible in main navigation.**

   Evidence: `TRADING_AGENT_SECTION` exists, but the sidebar showed Home, Zopedia, Broad Economy, Market Explorer, Portfolio, Portfolio Performance, Stock Investigator, and Option Strategizer only.

   Why it matters: users cannot use a feature they cannot discover. If this is intentional, it needs a clear product state: hidden, admin-only, or retired.

   Recommendation: decide whether Trading Agent is a user-facing section. If yes, add it to the navigation with a guarded unavailable state. If no, remove user-facing references and keep it in Admin/docs.

## Medium Priority Findings

1. **Market Explorer is useful but the table is hard to scan.**

   Evidence: the first visible table output collapses into a long ticker list in page text; the screenshot shows the summary first but the dense table and treemap dominate below.

   Recommendation: make the selected row and top 5 rows feel intentional. Add compact reason chips or columns for "why this matters" and make row selection visibly persistent.

2. **Home graph-first layout is risky on mobile.**

   Evidence: the mobile screenshot shows the network graph before the dominant theme text.

   Recommendation: on phone screens, lead with the dominant theme and key beats. Put graph exploration below the answer or behind an "Explore map" affordance.

3. **Option Strategizer exposes model caveats and Greek mechanics as product prose.**

   Evidence: the page shows a paragraph explaining delta/gamma/theta approximation and notes that vega/vol-surface changes are not modeled.

   Recommendation: keep the caveat, but make the first viewport decision-oriented: best contract, expected move, risk, decay, liquidity. Move mechanics to a details drawer.

4. **Stock Investigator works well for ticker switching but lacks a narrative bridge.**

   Evidence: switching from AAPL to RGTI produced the right taxonomy and chart, but the page starts with technicals and recent data controls.

   Recommendation: add a compact ticker summary above charts: what moved, what evidence supports it, what is missing, and what to inspect next. Use the shared page-summary/Zopedia contract rather than a local LLM path.

5. **Portfolio and Portfolio Performance have different degraded behavior.**

   Evidence: Portfolio showed a live-account unavailable state; Portfolio Performance still rendered benchmark performance from snapshots.

   Recommendation: make the distinction explicit in user language. "Portfolio positions need broker access" and "historical performance is available from snapshots" are clearer than treating both as generic connection states.

## Positive Observations

- Zopedia successfully answered a current quantum-computing prompt using retained evidence plus `daily_movers`, and correctly avoided claiming a catalyst it did not have.
- Broad Economy loaded real macro data and surfaced a coherent summary with watch items.
- Home has a useful daily narrative, audio, and event beats.
- Stock Investigator ticker switching works and preserves the market workflow.
- Option Strategizer gives a concrete contract recommendation and scenario scores.

## Reliability Notes

- The first local run without Key Vault was useful for degraded-state UX, but not representative of the full product.
- With Key Vault env set, LLM was configured and FRED loaded, but embeddings were disabled because `EMBEDDING_DEPLOYMENT` was not set.
- Alpaca account calls returned `40110000 request is not authorized`; snapshot-backed market data still rendered in multiple places.
- Streamlit emitted repeated `use_container_width` deprecation warnings. This is not a user-facing bug today, but it is a future compatibility cleanup.

## Recommended Next Work

1. Fix and verify normal mobile URL routing.
2. Move developer/setup instructions out of first-viewport product content.
3. Refactor completed Zopedia answers to answer-first trace presentation.
4. Decide and document Trading Agent's navigation state.
5. Make mobile Home answer-first, graph-second.
