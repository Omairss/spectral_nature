# Plans Index

This directory is the single tracked home for working plans in this repo.

Paths in this index use `streamlit_alpaca_app/` as the implicit root, so `documents/...` points into this tree.

Use the docs as follows:

- `../README.md`: doc hub and runtime overview
- `../operations/PROJECT_SETUP_AND_OPERATIONS.md`: setup, deployment, and operator context
- `../reference/ATTENTION_FEED_GUIDELINES.md`: active product and evidence rules
- files in `documents/plans/`: implementation plans, refactor plans, recovery notes, and short working specs

## Product and UX

- `APP_SIMPLIFICATION_TRACK.md`: active simplification and modularization status
- `BRANDING_REFRESH_PLAN.md`: app-shell branding cleanup and presentation notes
- `BRANDING_ASSET_INTEGRATION_2026-04-03.md`: branding asset rollout notes
- `UI_SHELL_REFINEMENT_2026-04-02.md`: shell-level UI cleanup plan
- `HOMEPAGE_V2_RAIL_REFACTOR_PLAN.md`: homepage rail refactor work
- `HOMEPAGE_IMPLIED_OPEN_EXPERIMENT_2026-04-02.md`: homepage implied-open experiment notes
- `OPEN_KNOWLEDGE_GRAPH_EXPERIMENT_2026-04-12.md`: admin-only Experiment-page PoC for open-ended graph generation, review, and commit
- `EXPERIMENT_PAGE_RENAME_2026-04-11.md`: Daily Tape removal, admin-only Experiment page, and workspace renames
- `EXPERIMENT_SUMMARY_CLARITY_ELEVENLABS_KEYVAULT_2026-04-11.md`: cleaner experiment summary copy plus Key Vault-backed ElevenLabs setup notes
- `DAILY_TAPE_SUMMARY_AUDIO_2026-04-10.md`: shared daily tape summary plus on-demand ElevenLabs audio
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

- `ATTENTION_FEED_IMPLEMENTATION_PLAN.md`: canonical implementation plan for the attention feed stack
- `ATTENTION_FEED_EVENT_REDESIGN_PLAN.md`: product and background context for the event redesign
- `ATTENTION_RESEARCH_QUALITY_FIX_2026-04-14.md`: root-cause notes and staged fix plan for flat homepage hypotheses, clipped homepage beats, and generic `Why It Happened` event-card copy
- `ATTENTION_LAYER_PLAN.md`: historical layer-design context
- `ATTENTION_FRED_INTEGRATION_PLAN_2026-04-06.md`: phased architecture plan to integrate FRED into attention scoring
- `AGENTIC_TICKER_BACKGROUND_TAVILY_2026-04-03.md`: background enrichment plan and Tavily integration notes
- `AGENTIC_SUMMARY_2026-04-13.md`: whole-tape homepage summary planning, search, and hypothesis synthesis
- `AQL_EVIDENCE_INDEX_2026-04-14.md`: shared deterministic indexing and search contract for AQL evidence chunks, including homepage summary research
- `AQL_NLP_IR_AGENT_ARCHITECTURE_2026-04-14.md`: north-star architecture for AQL plus `SAA`, the supporting search and retrieval system with durable full-text retention, hybrid retrieval, explicit agent loops, and evidence-pack writers
- `AQL_SAA_PHASE0_RETENTION_RETRIEVAL_2026-04-14.md`: implemented Phase 0 fix for richer search-result retention, better homepage-summary document capture, and ranked chunk retrieval
- `AQL_SAA_PHASE1_RETENTION_FOUNDATION_2026-04-14.md`: implemented Phase 1 retention foundation with canonical document ids, durable raw-document blobs, and Postgres metadata for retained source documents
- `AQL_SAA_V1_IMPLEMENTATION_ROADMAP_2026-04-14.md`: concrete build roadmap for AQL plus SAA v1, including workstreams, phases, acceptance criteria, module boundaries, and rollout order
- `BMY_BACKGROUND_FALLBACK_FIX_2026-04-03.md`: targeted ticker-background recovery notes
- `COMMODITY_PRELOAD_ROOT_CAUSE_FIX_2026-04-09.md`: commodity preload recovery and alignment plan
- `DEPENDENCY_GRAPH_INTEGRATION_2026-04-10.md`: JSON-backed dependency graph feature plan and commodity-dashboard seam
- `CHAT_SEARCH_RESEARCH_ENABLEMENT_2026-04-12.md`: plan to make Chat + Search use retained narratives, live evidence, and config-driven impact expansion for analysis prompts
- `MARKET_OPPORTUNITY_PRECOMPUTE_RECOVERY_PLAN.md`: market precompute recovery work
- `PIPELINE_IDENTITY_AND_FRED_RETRY_FIX_2026-04-13.md`: root cause and source fixes for the attention job identity drift plus FRED retry hardening
- `PIPELINE_CACHE_GUARDRAILS_2026-04-14.md`: local pipeline-store cache bounds plus git-ignore cleanup for runtime cache artifacts
- `PORTFOLIO_LIVE_MODE_FIX_PLAN.md`: live portfolio-mode reliability work
- `UNIVERSE_EXPANSION_PLAN_V2.md`: universe expansion design
- `SEEKING_ALPHA_NOTEBOOK_2026-04-08.md`: notebook and helper design for headed Playwright extraction from Seeking Alpha

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
- `spectral_nature_2/SN2_NEGOTIATION_RESPONSE_2026-04-07.md`: copy-ready response based on the merged negotiation baseline
- `spectral_nature_2/SN2_IPHONE_AGENT_REFERENCE_MAP_2026-04-07.md`: doc map for Spectral Nature 2, omnibar, agent workspace, shared API, and iPhone work
- `spectral_nature_2/AGENTIC_API_AUTH_MCP_2026-04-07.md`: production-grade auth model, scoped agent keys, and MCP-compatible gateway
- `spectral_nature_2/AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`: concrete `/v1/agent/*` REST resource schemas and payload examples for homepage and iPhone reuse
- `spectral_nature_2/HOMEPAGE_AGENT_WORKSPACE_PLAN_2026-04-06.md`: phased plan for a homepage agent workspace with tools, RAG, notes, and sandbox execution
- `spectral_nature_2/IPHONE_APP_STRATEGY_2026-04-05.md`: source-first migration plan from Streamlit UI to native iPhone app
- `spectral_nature_2/IPHONE_MVP_SCAFFOLD_2026-04-06.md`: delivered backend/API plus SwiftUI scaffold for the iPhone MVP
- `spectral_nature_2/agent_omnibar/`: dedicated doc hub for the separate agent omnibar task
- `spectral_nature_2/agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`: copy-ready resolved response for the separate omnibar task
