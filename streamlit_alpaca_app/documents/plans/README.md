# Plans Index

Current working plans live here. Completed Streamlit/Dash and first-migration plans are archived outside the checkout; see the [retirement inventory](../verification/legacy-retirement-2026-10-16/README.md). Dated release/research evidence does not authorize restoring its runtime.

Active recovery: [accepted behavior](RELEASE_BEHAVIOR_RECOVERY_2026-10-16.md). Held reasoning/model-routing and Trading-call implementations remain in `held/pre-recovery-20261016`; they are not accepted release source.

Paths in this index use `streamlit_alpaca_app/` as the implicit root, so `documents/...` points into this tree.

Use the docs as follows:

- `../README.md`: doc hub and runtime overview
- `../architecture/README.md`: durable architecture and system design map
- [Master research index](../research/README.md): open investigations, failure cases and experiment evidence
- `../operations/PROJECT_SETUP_AND_OPERATIONS.md`: setup, deployment, and operator context
- `../reference/ATTENTION_FEED_GUIDELINES.md`: active product and evidence rules
- files in `documents/plans/`: implementation plans, recovery notes, and short working specs

## Product and UX

- [Client refresh and native paper performance](CLIENT_REFRESH_2026-10-22.md): conditional live Brief refresh, replay/playback preservation and corrected native detail routing.

- [Native Markets chart clarity](NATIVE_MARKETS_CHARTS_2026-10-14.md): interpretable return comparisons, numeric colors, point identities and named return bars.
- `EXPERIMENT_SUMMARY_CLARITY_ELEVENLABS_KEYVAULT_2026-04-11.md`: cleaner experiment summary text plus Key Vault-backed ElevenLabs setup notes
- `DAILY_MARKET_OVERVIEW_SUMMARY_AUDIO_2026-04-10.md`: shared daily market overview summary plus on-demand ElevenLabs audio
- `YIELD_VIEW_PLAN_2026-04-06.md`: market-explorer fixed-income and yield view plan
- `TRUE_BOND_VIEW_PLAN_2026-04-06.md`: bond-level view plan across Treasuries, corporates, and munis

## Access, Auth, and Email

- `USER_ACCESS_MODEL_PLAN.md`: user and access model architecture
- `INVITE_EMAIL_BRANDING_REFRESH_2026-04-05.md`: branded invite email rollout notes
- `INBOUND_MAIL_SETUP_PLAN.md`: prerequisites and architecture for support and newsletter inbound mail

## Data, Analytics, and Research

- [October Applied Research Fall 2026 Master plan](OCTOBER_APPLIED_RESEARCH_FALL_2026_MASTER_PLAN.md): approved current-position and gap-closure table, evaluation objectives, cheap-open-model and +20% workload-cost constraints, and the next-agent work queue. Start with the [handoff](OCTOBER_APPLIED_RESEARCH_FALL_2026_MASTER_PLAN.md#next-agent-handoff).
- `ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md`: accepted conversation release; verified source/evidence/review/knowledge-policy repair handoff; proposed premium-analysis and cheap-peripheral DeepSeek routing, with acceptance and release scope
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

- [Codebase deep cleanup](CODEBASE_DEEP_CLEAN_2026-10-18.md): repository audit, verified local retirements and the remaining storage, execution, semantic-contract and transport cleanup.

- [Database work lifecycle and refresh coordination](DATABASE_WORK_LIFECYCLE_2026-10-16.md): cancellation ownership, shared database admission, publication caching, incremental projection and bounded independent recovery.

- `V0_5_ROADMAP_2026-06-22.md`: v0.5 priority consolidation across pipeline simplification, Zopedia retrieval, company memory, hardcode cleanup, attention scoring, and module boundaries
- `V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md`: v0.6 deepfix for enforceable AQL/Zopedia model-call governance, request telemetry, direct-call audits, and migration of Trading Agent, Attention/Home, Zopedia memory, KG, and page summary model calls

## Infra and Operations

- [Whole-stack production promotion — 2026-10-14](STACK_PARITY_PROMOTION_2026-10-14.md): accepted development frontend/API, Zopedia conversation/research and LLM cost changes across all active non-broker production workers; source guards, environment ownership, live verification and rollback.


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
- `spectral_nature_2/IPHONE_MVP_SCAFFOLD_2026-04-06.md`: delivered backend/API plus SwiftUI scaffold for the iPhone MVP
- `spectral_nature_2/agent_omnibar/`: dedicated doc hub for the separate agent omnibar task
- `spectral_nature_2/agent_omnibar/OTHER_TASK_RESPONSE_2026-04-08.md`: ready-to-send resolved response for the separate omnibar task
