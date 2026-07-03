# Agentic Market Task Guardrails

Use this short checklist before changing Market Opportunity, page agentic summaries, or Trading Agent.

For v0.6 gateway work, also read:
[v0.6 AQL/Zopedia Gateway Roadmap](../plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md).

## Required Pattern

- Fixed market feeds are attention-job outputs. Streamlit selects `market_opportunity_feed`; it does not rebuild the feed from movers or momentum during render.
- Page agentic summaries are attention-job outputs. Streamlit reads `page_agentic_summaries`; AQL does not run from a panel constructor.
- Trading Agent is a materialized admin experiment. AQL runs in `trading-agent-build`; Streamlit reads `trading_agent_runs` and `trading_agent_candidates`.
- Trading Agent Place / Reject actions are durable audit events. Place stays `log_only` until Alpaca order submission is explicitly enabled, but the audit record must keep broker handoff fields.
- Feature pages do not show job trigger controls. Job starts, retries, connector call health, and refresh controls live in Admin > System Health.
- Shared AQL/Zopedia owns evidence and model-call governance. Do not add a parallel research agent or raw `generate_json` synthesis path for page summaries, trading synthesis, or reviews.
- Context signatures matter. If the UI expects an exact summary, build the same compact context in the job and UI. For cross-surface consumers, use the latest materialized surface/ticker summary intentionally.
- Deterministic job outputs should fail loudly if they break. External AQL/LLM summary failures may degrade to a materialized fallback with a visible data gap.
- Required Trading Agent horizons need auditable persisted run states. v0.6 should not create research candidates without gateway-governed AQL/Zopedia support; if provider, budget, or evidence failures prevent synthesis, persist the horizon run with explicit unavailable/gap state and model-call telemetry.
- External AQL/search/LLM steps inside materialization jobs need per-step timeouts. A stuck candidate, event bundle, macro verification, summary, or search path must not block fixed feed persistence.
- Outer job timeout handlers must write the same `fallback` schema as the summary service. Do not materialize `error` rows just because the timeout happened outside the wrapped helper.

## Must-Read IDs

- Mistakes: 32, 37, 38, 39, 40, 41, 44, 54, 56, 57, 58, 78.
- Learnings: 49, 50, 51, 52, 53, 54, 56, 59, 71, 74, 75, 76, 307.

## Regression Checks

- Market Explorer first load does not call daily mover or momentum scanners for the main feed.
- Broad Economy and Stock Investigator summary panels only read materialized summaries.
- Trading Agent UI does not run AQL or scan/build market opportunity itself before rendering.
- Trading Agent Build appears in Admin > System Health and produces all five required horizon run states: 1w, 1m, 3m, 1y, 5y.
- Trading Agent candidates are present only for horizons where gateway-governed AQL/Zopedia research produced supported packages; otherwise the horizon exposes explicit unavailable/data-gap state and model-call failure metadata.
- Trading Agent model calls are visible by surface and purpose: ticker research, final synthesis, and review.
- List-like payloads from numpy, pandas, and LLM JSON never use truthiness checks such as `value or []`.
- A slow homepage/page AQL summary falls back within the configured job timeout instead of preventing `market_opportunity_feed` from being written or producing raw `error` rows.
- Dev deploy includes both UI and pipeline images when shared services or attention-job code changed.
