# Dependency Graph Data

This folder holds reusable dependency-graph payloads for Spectral Nature.

Structure:

- `schemas/dependency_graph.schema.json`: shared JSON schema for graph payloads
- `graphs/*.json`: graph instances grouped by market theme

Current live injection point:

- `services/market.py::commodity_dependency_graph()`
- rendered by `app.py::_render_commodity_experiment()`

Notes:

- Keep graph payloads data-first. Avoid adding new hardcoded relationship maps in Python when a graph file can express the same idea.
- Use `graph.tags` to group graphs by domain such as `commodities`, `macro`, `supply_chain`, or `equities`.
- The current Sankey view reads `edge.attributes.display_weight` when present. That keeps UI sizing separate from semantic fields like `severity` and `confidence`.
