# Knowledge Graph Seed Data

This directory holds seed graph files for the open knowledge graph experiment.

The format is intentionally simple for the PoC:

- top-level `title`
- `nodes`: array of objects with `id`, `label`, `type`, optional `description`, optional `aliases`, optional `attributes`
- `edges`: array of objects with `source`, `target`, `relationship`, and optional `mechanism`, `polarity`, `directness`, `severity`, `confidence`, `conditions`, `attributes`

The runtime merges these seed files with:

- committed Postgres knowledge-graph rows
- JSON-backed commodity dependency graphs

Keep node ids stable. Prefer:

- upper-case ids for public tickers such as `LIN` or `NVDA`
- lower snake case for concepts such as `helium` or `advanced_packaging`

Do not treat these files as the final source of truth. They are the baseline memory layer that the reviewed `Commit` flow can extend or override.
