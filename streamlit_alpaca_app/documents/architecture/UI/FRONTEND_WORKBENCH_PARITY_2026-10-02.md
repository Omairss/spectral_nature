# Frontend Workbench Parity and Product Standard — 2026-10-02

Status: active migration standard with the feature-parity workbench deployed to the isolated Next.js development stack. The first visualization prototype remains rejected; the current workbench is the development product baseline. Streamlit development and production remain independent and unchanged by this migration.

## Product definition

Spectral Nature is a chat-first market-research workbench. It is not a chart gallery and it is not a conventional dashboard with an assistant bolted onto it.

The permanent desktop composition is:

```text
product and artifact navigation | analytical canvas | research conversation
```

The conversation is the control surface. Users ask for research, analysis, revisions, comparisons, code execution, and long-running ML work through chat. The canvas exposes the resulting reports, datasets, charts, graphs, notebooks, code, diffs, sources, logs, and provenance as inspectable artifacts. Users do not edit or execute code directly.

On small screens the same component tree becomes a compact route header, one analytical canvas, and an always-reachable conversation composer. Mobile is not a second product path.

## Visual standard

Preserve the established Spectral Nature identity:

- near-black neutral surfaces rather than blue dashboard gradients;
- the waveform mark, spaced wordmark, and Torres Capital signoff;
- compact, information-dense controls and restrained borders;
- cool silver text with teal, violet, blue, and coral reserved for analytical meaning;
- sans-serif product typography with tabular numerals for data;
- answer-first hierarchy, with operational detail available but subordinate;
- motion used for state and spatial continuity, never decoration.

Avoid generic SaaS cards, giant editorial headings, letter-in-a-circle navigation marks, marketing copy, decorative empty panels, and fake terminal chrome.

## Feature-parity ledger

Parity means retaining capabilities, actions, state, unavailable behavior, and cross-surface handoffs—not reproducing Streamlit's component tree.

| Surface | Capabilities that cannot be lost | Target workbench expression |
| --- | --- | --- |
| Home | materialized daily summary, attention graph, grouped event/mover stories, ticker drilldowns, retained research, replay date, audio | Daily Brief report artifact with linked graph, story groups, audio, and entity drilldowns |
| Zopedia | durable threads, live research journal, answer, citations, source upload/inspection, page and proposal memory, community and health views, follow-up | Conversation plus report/source/memory artifacts; completed answers lead, journal collapses into run inspection |
| Broad Economy | category tabs, indicator cards, time series, comparative views, tables, summary | Macro workbook with linked indicator, chart, and table artifacts |
| Market Explorer | market/business filters, horizons, mover table, treemap, ticker handoff | Screener dataset with linked treemap and entity inspector |
| Portfolio | account state, holdings across asset classes, history, exposure, P/L contribution, equity risk/return, factor/correlation diagnostics, lists/screeners, guarded order review | Portfolio workbook with position, risk, factor, list, and review artifacts |
| Portfolio Performance | account and benchmark curves, drawdown, monthly returns, period/metric switching, tables | Performance workbook with linked time-series and return-table artifacts |
| Stock Investigator | ticker and period controls, technical chart set, recent prices, company context, fundamentals, forecasts, cross-surface handoffs | Entity workspace with overview, technical, company, financial, dataset, and provenance artifacts |
| Option Strategizer | expiry selection, calls/puts ranking, IV, scenario surface, Greek tradeoffs, recommendation table | Option-analysis workbook with contract table and linked scenario artifacts |
| Trading Agent | market backdrop, long/short/watch candidates, company context, research actions, decision history, guarded broker connection and order review | Agent run with candidate, thesis, decision, and explicitly authorized order-review artifacts |
| Admin | users, invites/reset, invite design, API keys, usage/security, LLM configuration, health, dataset freshness, jobs and history | Role-gated control-plane workspace, outside the investor research canvas |

## Migration acceptance gates

A route is not migrated until all of these are true:

1. Its old renderer has a written feature inventory and every feature is included, deliberately retired, or explicitly deferred with owner-visible status.
2. The route uses the existing FastAPI/auth/data spine; business rules are not recreated in TypeScript.
3. Conversation can act on the visible artifact with explicit context.
4. URL, refresh, session expiry, unavailable data, and long-running work behave correctly.
5. Desktop and phone layouts are personally inspected at representative sizes.
6. The result is compared visually and behaviorally with the current product, not merely compiled.
7. Development deployment happens only after the local result meets the product brief. Production still requires explicit permission.

## Current implementation status

- Authentication/BFF transport: deployed against the existing users, hashes, memberships, lockouts, control database, and authorization rules. Browser tokens remain in HTTP-only cookies; mutations retain same-origin CSRF enforcement. No second account system or frontend-specific identity store exists.
- Workbench shell: deployed around product navigation, a dense analytical canvas, and one context-aware research conversation. Analytical routes own the right conversation rail; Zopedia owns its central conversation canvas so duplicate composers do not compete.
- Daily Brief: deployed with the materialized summary, typed attention network, coverage, grouped movements, ticker handoffs, historical replay, retained audio, and contextual research.
- Zopedia: deployed with durable threads and runs, live journal, completed answers, citations, retained sources, upload, page/proposal memory, run inspection, cancellation, and follow-up that preserves parent context.
- Broad Economy and Market Explorer: deployed as real-data workbooks with category/view switching, time-series and comparison artifacts, tables, taxonomy-backed business lenses, horizons, treemap, momentum structure, ranking, and ticker handoff.
- Portfolio and Performance: deployed with account state, holdings, exposure and P/L analysis, risk/correlation/factor views, lists and screeners, account and benchmark curves, drawdown, monthly-return heatmap, period/metric switching, and linked tables.
- Stock Investigator: deployed with entity/company context, why-now and management signals, probabilistic forecast summary, technical levels, linked evidence, technical and financial chart contracts, datasets, and provenance.
- Options and Trading Agent: deployed with expiry/contract ranking, IV and scenario surfaces, Greek tradeoffs, market backdrop, candidates, thesis/context, research actions, and decision history. Broker connection, order review, and execution retain their existing guarded service boundaries and are not broadened by this frontend release.
- Admin: deployed as a role-gated control-plane workspace with real users and invitations, password-reset issuance, usage/security, LLM, API-key, system-health, freshness, job, and history surfaces.
- Visualization artifacts: Plotly currently renders the migrated real-data chart set behind a typed, renderer-neutral artifact boundary. Plotly is an adapter rather than the platform contract.
- Overall frontend feature parity is not certified. Development represents many read, analysis, research, decision, and administration workflows, but production owner-session review exposed unavailable knowledge/research and an unsafe cross-broker fallback. Broker connection management and old-UI guarded order review/execution are not implemented in the replacement; they remain explicit parity gaps, not implied acceptance through shared service code. Streamlit remains the live fallback.
- Agentic notebook/code/sandbox harness: parked until frontend parity, as requested.
- Advanced renderer integrations beyond Plotly: the typed artifact boundary is ready, but the future graph/WebGL/3D renderer portfolio is not part of this parity release.
- Isolated development remains on `sn-next-ui-dev`, `sn-api-next-dev`, and `sn-research-run-dev`. An approved separate production candidate now exists; neither Streamlit environment nor live domains were changed.

## Acceptance review

The rebuilt workbench was rendered at `1512x982` and `390x844` against live development data before deployment, then inspected again through the public isolated development URL after deployment.

- The visual language now matches the existing product more closely: near-black surfaces, compact Spectral Nature wordmark, Torres Capital signoff, dense route controls, tabular data, and restrained semantic color.
- Desktop Daily Brief, Zopedia, Broad Economy, Market Explorer, Portfolio, Performance, Stock Investigator, Options, Trading Agent, Admin, and login states were inspected directly with real data.
- Mobile Broad Economy and Market Explorer were inspected directly; the same component tree produced a compact header, readable canvas, and reachable conversation control without horizontal overflow.
- Zopedia's duplicate global/central composer was found during review and removed by giving the central conversation canvas ownership of that route.
- Stock Investigator was re-reviewed with the company/forecast/evidence read model. Technical artifact requests settle independently, share one CSRF acquisition, and retain a usable artifact if its sibling request fails.
- Home replay and the retained-audio control were inspected in the desktop workbench. Audio bytes use a separate bounded media response instead of inflating the Home JSON document.
- Zopedia completed-answer Markdown was inspected with semantic GitHub-flavored tables and a bounded responsive table wrapper; raw pipe syntax no longer leaks into the answer surface.
- Market Explorer's taxonomy lenses now resolve from one catalog snapshot rather than repeating database-backed focus lookups for every option, removing the timeout that affected the real route.
- The deployed browser restored the existing admin session, rendered all 12 users and the pending invitation, loaded the 80-name Market Explorer universe and both primary charts, and produced no browser console warnings or errors.
- Local lint, type checking, Python compilation, and the 28-route production build passed. The production dependency audit reported zero vulnerabilities.

This is completion of the requested feature-parity development release, not completion of the longer-term renderer portfolio or agentic sandbox.

## Production staging review — 2026-10-07 UTC

The statements above describe the October 2 development baseline, not production
acceptance. Owner-authorized staging now exists independently at `sn-next-ui` with private
`sn-api-next` and manual `sn-research-run`. Streamlit apps, production domains, and live
traffic remain unchanged. Broker execution is disabled on the candidate.

Production review found that issuing invitation/reset links in Admin did not prove those
links could be consumed by Next. Source `95f8d697` adds the native root-query link routes,
activation, password recovery, and reset completion against the existing account service.
It is deployed to development and the production candidate. Invalid-token and validation
responses were verified through both deployed BFFs; real credential changes are an owner
handoff. Do not infer production account acceptance from those rejection checks alone.

The existing production Home and equities jobs were promoted and exercised, not replaced
with duplicate schedules. Both completed successfully; their data-contract audit passes.
The first production five-chart selection failed personal semantic review despite passing
transport checks. Shared source-backed review and draft-preserving revision now pass
managed development/production selection and personal review (`5ff5c5ea`). The existing
Home producer has been promoted to that image and its complete refresh succeeded. Latest
Home and retained replay render their own embedded selections. Production owner-session
Admin/users passed, but knowledge/research and correct-provider portfolio reads failed.
Corrective source `6856a1de` is staged on dev and the separate production candidate: real
dev holdings still load, unavailable production brokerage no longer falls back to another
provider, and Zopedia outages no longer claim an empty wiki. Shared legacy lock recovery,
production broker linking, and connection/order-management UI parity remain open. Web also
normalizes parsed same-origin login return paths. The authoritative
release state and rollback boundary are in
[production readiness](../../plans/WORKBENCH_PRODUCTION_READINESS_2026-10-06.md).
