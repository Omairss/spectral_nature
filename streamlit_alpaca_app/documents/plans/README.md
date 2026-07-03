# Plans Index

This directory is the single tracked home for working plans in this repo.

Paths in this index use `streamlit_alpaca_app/` as the implicit root, so `documents/...` points into this tree.

Use the docs as follows:

- `../README.md`: doc hub and runtime overview
- `../architecture/README.md`: durable architecture and system design map
- `../operations/PROJECT_SETUP_AND_OPERATIONS.md`: setup, deployment, and operator context
- `../reference/ATTENTION_FEED_GUIDELINES.md`: active product and evidence rules
- files in `documents/plans/`: implementation plans, recovery notes, and short working specs

## Product and UX

- `BRANDING_REFRESH_PLAN.md`: app-shell branding cleanup and presentation notes
- `BRANDING_ASSET_INTEGRATION_2026-04-03.md`: branding asset rollout notes
- `UI_SHELL_REFINEMENT_2026-04-02.md`: shell-level UI cleanup plan
- `HOMEPAGE_IMPLIED_OPEN_EXPERIMENT_2026-04-02.md`: homepage implied-open experiment notes
- `EXPERIMENT_PAGE_RENAME_2026-04-11.md`: Daily Market Overview removal, admin-only Experiment page, and workspace renames
- `EXPERIMENT_SUMMARY_CLARITY_ELEVENLABS_KEYVAULT_2026-04-11.md`: cleaner experiment summary text plus Key Vault-backed ElevenLabs setup notes
- `DAILY_MARKET_OVERVIEW_SUMMARY_AUDIO_2026-04-10.md`: shared daily market overview summary plus on-demand ElevenLabs audio
- `STOCK_INVESTIGATOR_SPLIT_2026-04-06.md`: market-vs-stock workspace split and ticker handoff
- `YIELD_VIEW_PLAN_2026-04-06.md`: market-explorer fixed-income and yield view plan
- `TRUE_BOND_VIEW_PLAN_2026-04-06.md`: bond-level view plan across Treasuries, corporates, and munis

## Access, Auth, and Email

- `USER_ACCESS_MODEL_PLAN.md`: user and access model architecture
- `ACCESS_ADMIN_USAGE_SECURITY_STATS_2026-04-11.md`: durable access-event tracking and admin usage/security dashboard notes
- `UI_BROWSER_PERSISTENCE_ENV_ALIGNMENT_2026-04-12.md`: prod browser-persistence restore plus deploy-script and tracker alignment notes
- `PENDING_INVITE_CARD_ACTIONS_2026-04-11.md`: pending-invite card UI and backend update path
- `INVITE_EMAIL_BRANDING_REFRESH_2026-04-05.md`: branded invite email rollout notes
- `INVITE_EMAIL_DESIGNER_ADMIN_2026-04-05.md`: admin invite template editor and template-library notes
- `EMAIL_DELIVERY_SETUP_PLAN.md`: Azure email delivery design and rollout notes
- `EMAIL_DELIVERY_KEYVAULT_REGRESSION_2026-04-10.md`: root-cause notes and source fixes for the invite/reset email regression after vault consolidation
- `INBOUND_MAIL_SETUP_PLAN.md`: prerequisites and architecture for support and newsletter inbound mail

## Data, Analytics, and Research

- Architecture-level docs moved to `../architecture/README.md`.
- AQL docs live in `../architecture/AQL/`.
- SAA docs live in `../architecture/SAA/`.
- Attention docs live in `../architecture/attention/`.
- Data pipeline docs live in `../architecture/data_pipelines/`.
- Agent research docs live in `../architecture/agents/`.
- Overall app architecture docs live in `../architecture/overall/`.
- `BMY_BACKGROUND_FALLBACK_FIX_2026-04-03.md`: targeted ticker-background recovery notes
- `CHAT_SEARCH_RESEARCH_ENABLEMENT_2026-04-12.md`: plan to make Chat + Search use retained narratives, live evidence, and config-driven impact expansion for analysis prompts
- `MARKET_OPPORTUNITY_PRECOMPUTE_RECOVERY_PLAN.md`: market precompute recovery work
- `PORTFOLIO_LIVE_MODE_FIX_PLAN.md`: live portfolio-mode reliability work
- `SEEKING_ALPHA_NOTEBOOK_2026-04-08.md`: notebook and helper design for headed Playwright extraction from Seeking Alpha

## Reliability and Mistake Prevention

- `MISTAKE_PREVENTION_HARDENING_2026-04-21.md`: deploy guards, secret scanning, mandatory dataset validation, LLM readiness checks, and attention service package reorganization
- `V0_5_ROADMAP_2026-06-22.md`: v0.5 priority consolidation across pipeline simplification, Zopedia retrieval, company memory, hardcode cleanup, attention scoring, and module boundaries
- `V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md`: v0.6 deepfix for enforceable AQL/Zopedia model-call governance, request telemetry, direct-call audits, and migration of Trading Agent, Attention/Home, Zopedia memory, KG, and page summary model calls

## Infra and Operations

- `ALPACA_KEY_VAULT_HARDENING_2026-04-10.md`: repo and deploy hardening plan for removing tracked Alpaca secrets and enforcing Key Vault-only runtime resolution
- `ALPACA_VAULT_CONSOLIDATION_2026-04-10.md`: migration plan for consolidating Alpaca secrets into one shared Key Vault across UI and pipeline
- `DEPLOY_UI_REVISION_WAIT_FIX_2026-04-11.md`: deploy-script fix for waiting on the new Azure Container App revision instead of the old ready revision
- `INFRA_INDEX_HARDENING_2026-04-10.md`: plan for replacing tracked live infra outputs with ignored local files plus a committed lookup index

## Security

- `security/README.md`: security review hub
- `security/SECURITY_REVIEW_2026-04-11.md`: repo security review covering secrets, auth behavior, plaintext exposure, local repo inventory, and hotlinks
- `security/SECURITY_REMEDIATION_2026-04-11.md`: immediate remediation log covering rotations, source cleanup, auth hardening, and Azure log-check limits

## Working Notes

- `Guidelines.md`: short working notes that have not yet been folded into a canonical doc

## Spectral Nature 2

- `spectral_nature_2/`: grouped plan set for Spectral Nature 2, omnibar, shared agent/API contracts, and iPhone work
- `spectral_nature_2/SN2_NEGOTIATION_RESOLUTION_2026-04-07.md`: resolved contract for iPhone and omnibar requirements, clarifications, and sequencing
- `spectral_nature_2/SN2_NEGOTIATION_BASELINE_2026-04-07.md`: historical merged negotiation baseline for iPhone and omnibar requirements and constraints
- `spectral_nature_2/SN2_NEGOTIATION_RESPONSE_2026-04-07.md`: ready-to-send response based on the merged negotiation baseline
- `spectral_nature_2/SN2_IPHONE_AGENT_REFERENCE_MAP_2026-04-07.md`: doc map for Spectral Nature 2, omnibar, agent workspace, shared API, and iPhone work
- `spectral_nature_2/AGENTIC_API_AUTH_MCP_2026-04-07.md`: production-grade auth model, scoped agent keys, and MCP-compatible gateway
- `spectral_nature_2/AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`: concrete `/v1/agent/*` REST resource schemas and payload examples for homepage and iPhone reuse
- `spectral_nature_2/HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`: phased plan for a homepage agent workspace with tools, RAG, notes, and sandbox execution
- `spectral_nature_2/IPHONE_APP_STRATEGY_2026-04-05.md`: source-first migration plan from Streamlit UI to native iPhone app
- `spectral_nature_2/IPHONE_MVP_SCAFFOLD_2026-04-06.md`: delivered backend/API plus SwiftUI scaffold for the iPhone MVP
- `spectral_nature_2/agent_omnibar/`: dedicated doc hub for the separate agent omnibar task
- `spectral_nature_2/agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`: ready-to-send resolved response for the separate omnibar task
