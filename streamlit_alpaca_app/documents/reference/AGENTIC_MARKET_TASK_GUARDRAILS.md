# Agentic Market Task Guardrails

Use this short checklist before changing Market Opportunity, page agentic summaries, or Trading Agent.

## Required Pattern

- Fixed market feeds are attention-job outputs. Streamlit selects `market_opportunity_feed`; it does not rebuild the feed from movers or momentum during render.
- Page agentic summaries are attention-job outputs. Streamlit reads `page_agentic_summaries`; AQL does not run from a panel constructor.
- Trading Agent is a materialized admin experiment. AQL runs in `trading-agent-build`; Streamlit reads `trading_agent_runs` and `trading_agent_candidates`.
- Trading Agent Place / Reject actions are durable audit events. Place stays `log_only` until Alpaca order submission is explicitly enabled, but the audit record must keep broker handoff fields.
- Feature pages do not show job trigger controls. Job starts, retries, connector call health, and refresh controls live in Admin > System Health.
- Shared AQL / Chat + Search owns evidence. Do not add a parallel research agent for page summaries or trading synthesis.
- Context signatures matter. If the UI expects an exact summary, build the same compact context in the job and UI. For cross-surface consumers, use the latest materialized surface/ticker summary intentionally.
- Deterministic job outputs should fail loudly if they break. External AQL/LLM summary failures may degrade to a materialized fallback with a visible data gap.
- Required Trading Agent horizons need usable persisted candidates. If AQL times out for one horizon, persist conservative low-confidence fallback candidates from `market_opportunity_feed` and record the AQL gap.
- External AQL/search/LLM steps inside materialization jobs need per-step timeouts. A stuck candidate, event bundle, macro verification, summary, or search path must not block fixed feed persistence.
- Outer job timeout handlers must write the same `fallback` schema as the summary service. Do not materialize `error` rows just because the timeout happened outside the wrapped helper.

## Must-Read IDs

- Mistakes: 32, 37, 38, 39, 40, 41, 44, 54, 56, 57, 58.
- Learnings: 49, 50, 51, 52, 53, 54, 56, 59, 71, 74, 75, 76.

## Regression Checks

- Market Explorer first load does not call daily mover or momentum scanners for the main feed.
- Broad Economy and Stock Investigator summary panels only read materialized summaries.
- Trading Agent UI does not run AQL or scan/build market opportunity itself before rendering.
- Trading Agent Build appears in Admin > System Health and produces all five required horizons: 1w, 1m, 3m, 1y, 5y.
- Trading Agent candidates cover all five required horizons, either from grounded AQL output or explicit fallback rows with `status=fallback` and data gaps.
- List-like payloads from numpy, pandas, and LLM JSON never use truthiness checks such as `value or []`.
- A slow homepage/page AQL summary falls back within the configured job timeout instead of preventing `market_opportunity_feed` from being written or producing raw `error` rows.
- Dev deploy includes both UI and pipeline images when shared services or attention-job code changed.
