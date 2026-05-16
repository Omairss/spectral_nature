# AQL Architecture

The Attention Query Layer owns reusable analysis over Attention evidence: evidence search, package boundaries, causal checks, graph relationships, and consolidation of duplicate Attention logic.

Boundary rule: AQL may call SAA through `services.saa`, market data through public market data APIs, and external research adapters. AQL should not import UI, pipeline jobs, or Attention private helpers.

## Docs

- `AQL_NLP_IR_AGENT_ARCHITECTURE_2026-04-14.md`: north-star AQL plus SAA architecture
- `AQL_EVIDENCE_INDEX_2026-04-14.md`: deterministic evidence indexing and search contract
- `KG_AQL_INTEGRATION_2026-04-25.md`: plan for using the persistent knowledge graph as AQL graph memory, search context, and hypothesis source
- `KG_AQL_EVAL_REPORT_2026-04-25.md`: offline evaluation of KG read/write proposals against cached summaries and attention feed items
- `AQL_PACKAGE_REFACTOR.md`: package boundaries and refactor plan
- `ATTENTION_AQL_CONSOLIDATION_2026-04-24.md`: consolidation plan and implementation status for moving shared Attention logic into AQL and source-owned helpers
