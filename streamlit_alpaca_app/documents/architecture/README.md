# Architecture

This folder holds durable system architecture, service boundaries, data contracts, and architecture plans.

Use `documents/plans/` for active implementation notes, recovery plans, and short working specs. Use this folder when the doc should remain useful after the immediate task is done.

## Map

- `overall/`: app-wide architecture, simplification, signal extraction, universe, and pipeline redesign context
- `AQL/`: Attention Query Layer ownership, package boundaries, evidence contracts, and AQL consolidation work. Active gateway roadmap: `../plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md`.
- `SAA/`: Supporting Analysis Archive retrieval, retention, hybrid search, and SAA rollout phases
- `attention/`: Attention feed, homepage research quality, and Attention UI/data wiring design
- `market_data/`: market data, fundamentals, options, macro, anomaly, and signal extraction boundaries
- `entity_extraction/`: shared entity extraction, canonical linking, and KG node linking boundaries
- `data_pipelines/`: materialized pipeline architecture, FRED, taxonomy, cache, and identity/retry design
- `agents/`: agentic research, dependency graph, open graph experiments, and research export design
- `UI/`: Streamlit and presentation-layer boundaries

## Folder Rule

Add or move docs here when they describe a stable shape of the system. Keep dated incident fixes and short-lived task notes in `documents/plans/` unless they define architecture that future work should reuse.
