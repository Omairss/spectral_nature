# Dependency Graph Integration Plan

Date: 2026-04-10

## Goal

Add dependency-graph JSONs as a reusable product feature in Spectral Nature without hardcoding relationship maps in Python.

## Best injection point

Phase 1 should enter through the live commodity dashboard in `streamlit_alpaca_app`:

- UI render point: `streamlit_alpaca_app/app.py::_render_commodity_experiment()`
- Source seam: `streamlit_alpaca_app/services/market.py::commodity_dependency_graph()`

Why this is the right first seam:

- it is already a real user-facing graph
- it already has a simple upstream/downstream mental model
- the old implementation was hardcoded, so moving data to JSON fixes the source problem directly
- the same loader can later feed macro, supply-chain, and attention views

## Delivered shape

Phase 1 now uses:

- `streamlit_alpaca_app/data/dependency_graphs/schemas/dependency_graph.schema.json`
- `streamlit_alpaca_app/data/dependency_graphs/graphs/*.json`
- `streamlit_alpaca_app/services/dependency_graphs.py`

Current live graph families:

- commodity energy inputs
- metals and electrification
- precious metals rotation
- agriculture and softs
- one macro example for future reuse

## Data contract

Each graph file should:

- define nodes and edges in JSON
- use `graph.tags` for routing and filtering
- keep semantic strength in `severity` and `confidence`
- keep UI-only sizing in `edge.attributes.display_weight`

This keeps the schema reusable. The UI can change how it draws a graph without changing the meaning of the graph.

## Current behavior

The commodity dashboard now:

- loads graphs tagged `commodities`
- filters them to the commodity symbols active in the view
- flattens the selected edges into the existing Sankey/table shape

This means the UI did not need a large rewrite to gain the new feature.

## Next extension points

After the commodity path is stable, the next good places are:

1. macro dashboard relationships for rates, credit, housing, and duration-sensitive equities
2. attention/homepage graph context so event clusters can show curated transmission paths next to observed moves
3. stock investigator or ticker detail pages where a selected symbol can show upstream and downstream dependency context

## Guardrails

- Keep graph payloads in data files, not Python constants.
- Do not make the loader depend on one dashboard only.
- Validate required fields and node references before rendering.
- Prefer one reusable loader over multiple feature-specific parsers.
