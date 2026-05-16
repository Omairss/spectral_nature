# Attention Knowledge Graph Proposals

## Current Decision

Attention runs should generate **reviewable knowledge-graph proposals**, not directly mutate the core graph.

The next Attention job writes a materialized dataset:

```text
knowledge_graph_update_proposals
```

Those rows can be loaded on the Experiment page, edited, and committed through the existing `commit_knowledge_graph_review(...)` path.

## Write Path

The job path is:

```text
attention-home-build
  -> build_attention_home_output_frames(...)
  -> build_bottom_up_attention_artifacts(...)
  -> build_attention_knowledge_graph_proposals(...)
  -> persist knowledge_graph_update_proposals
```

The proposal builder consumes:

- `attention_claims`
- `macro_causal_graph_edges_v1`
- `macro_relationship_checks_1d`
- the current core KG snapshot

It emits proposals with:

- `proposal_type`: `node` or `edge`
- `operation`: `add_node`, `add_edge`, or `update_edge`
- `review_status`: defaults to `proposed`
- source/target direction for edges
- severity and confidence
- evidence refs and rationale

Delete proposals are intentionally not automatic yet. A broken macro relationship means "review this edge", not "delete it", until we have stronger contradiction evidence.

## Read Path For Future Attention Runs

The knowledge graph should be used as a **prior**, not as final evidence.

Recommended next read-side flow:

```text
attention candidate / event
  -> extract linked entities
  -> retrieve small KG neighborhood
  -> add graph paths as hypotheses/context
  -> search current evidence
  -> write narrative only from evidence-supported claims
  -> emit KG proposal deltas from new evidence
```

Useful read-side features:

- Better search planning: use graph neighbors to add likely suppliers, customers, inputs, commodities, and macro channels.
- Better grouping: use shared KG concepts to group related symbols that do not have high short-term price correlation.
- Better "why now" hypotheses: convert paths like `helium -> semiconductor_etch -> ASML` into candidate hypotheses, then require current evidence before using them in text.
- Better unresolved explanations: if the graph suggests a plausible channel but evidence is missing, say the channel is unconfirmed instead of inventing a reason.

## Guardrails

- No silent core graph mutation from scheduled runs.
- No graph-only causal claims in user-facing Attention text.
- Keep proposal confidence separate from evidence confidence.
- Use entity extraction before graph retrieval. Do not run full summaries directly through KG search.
- Prefer add/update proposals. Only create delete proposals after explicit contradiction detection is built and tested.

## Open Work

1. Add an approval queue/table for proposal status beyond the materialized latest dataset.
2. Add proposal filters in the Experiment page: new nodes, new edges, updates, low confidence, macro-only.
3. Add KG read-side retrieval into AQL planning.
4. Add contradiction detection for possible delete proposals.
5. Track proposal acceptance/rejection rates to tune extraction thresholds.
