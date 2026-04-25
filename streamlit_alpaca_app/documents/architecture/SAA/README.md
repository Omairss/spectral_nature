# SAA Architecture

SAA is the Supporting Analysis Archive. It retains source documents and evidence chunks so AQL, Attention, agents, and search flows can reuse historical research reliably.

Boundary rule: consumers import SAA through `services.saa`. Direct imports from `services.saa.storage` are implementation details unless a migration shim has not been replaced yet.

## Docs

- `AQL_SAA_V1_IMPLEMENTATION_ROADMAP_2026-04-14.md`: build roadmap, workstreams, acceptance criteria, and rollout order
- `AQL_SAA_PHASE0_RETENTION_RETRIEVAL_2026-04-14.md`: richer search-result retention and ranked chunk retrieval
- `AQL_SAA_PHASE1_RETENTION_FOUNDATION_2026-04-14.md`: canonical document ids, raw blobs, and Postgres metadata
- `AQL_SAA_PHASE2_QUERY_SURFACE_2026-04-14.md`: shared historical query surface and direct document-open paths
- `AQL_SAA_PHASE3_HISTORICAL_CHUNK_SEARCH_2026-04-14.md`: durable chunk-history retention and historical chunk search
- `AQL_SAA_PHASE4_HYBRID_RETRIEVAL_2026-04-14.md`: hybrid structured, lexical, and optional semantic retrieval
