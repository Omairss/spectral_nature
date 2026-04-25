# Knowledge Graph Into AQL Integration Plan

**Date:** 2026-04-25

## Goal

Use the persistent knowledge graph as AQL's reusable memory for dependency, supply-chain, and macro transmission logic, and let AQL propose graph changes when current evidence shows the durable graph is incomplete, stale, or wrong.

AQL should not own graph storage. AQL should ask the graph:

- what entities are related to this query or candidate?
- which upstream/downstream paths could explain this move?
- what expected beneficiaries, losers, or second-order assets should be checked?
- which parts of the explanation are graph priors versus current evidence?
- did current evidence strengthen, weaken, or invalidate an existing edge?
- should a new node or edge be proposed for review?

The knowledge graph remains the durable relationship store. AQL remains the research, evidence, hypothesis, and writing layer. The bridge between them should be a controlled proposal and commit workflow, not free-form graph mutation inside narrative generation.

## Current System Shape

The repository already has the two halves needed:

- `services/knowledge_graph.py`
  - loads seeded + committed graph snapshots
  - resolves seed queries with deterministic, context, and optional embedding search
  - collects local neighborhoods around seed nodes
  - builds and commits reviewed graph drafts
- `services/aql/`
  - plans research for attention candidates
  - gathers source documents
  - chunks evidence and extracts claims
  - builds macro hypotheses and verifies them
  - writes symbol, event, and homepage bundles

The clean integration is to make the knowledge graph a first-class AQL context provider, not a new evidence corpus and not a replacement for current market evidence.

The existing graph store already has most of the commit machinery needed:

- `commit_knowledge_graph_review(...)` can upsert nodes and edges
- removed nodes and edges are tombstoned with `is_deleted = TRUE`
- every commit stores `draft_payload_json` and `applied_delta_json`

So the gap is not raw write support. The gap is an AQL-native proposal layer that turns research results into reviewable graph deltas.

## Proposed Design

Add a small adapter module:

```text
services/aql/knowledge.py
```

This module is the only AQL code that imports `services.knowledge_graph`.

Responsibilities:

1. resolve AQL subjects into graph nodes
2. retrieve bounded graph neighborhoods
3. convert graph nodes and edges into stable AQL frames
4. derive candidate hypotheses from graph paths
5. expose compact prompt context for AQL writers
6. produce graph change proposals from evidence
7. submit approved proposals to the graph commit service

AQL should not silently mutate the graph during normal writing. It can propose changes and, under strict policy, commit low-risk changes through the same graph commit path used by the admin graph builder.

## New AQL Frames

Add graph-derived trace frames to `AgenticAttentionArtifacts.frames`:

### `aql_knowledge_graph_matches`

One row per candidate-to-node match.

Suggested columns:

- `run_id`
- `asof_time_utc`
- `subject_id`
- `subject_type` (`symbol`, `event`, `macro_release`, `query`)
- `subject_label`
- `node_id`
- `canonical_label`
- `node_type`
- `matched_alias`
- `match_source`
- `score`
- `score_deterministic`
- `score_embedding`

### `aql_knowledge_graph_edges`

One row per graph edge retrieved for AQL reasoning.

Suggested columns:

- `run_id`
- `asof_time_utc`
- `subject_id`
- `source_node_id`
- `source_label`
- `target_node_id`
- `target_label`
- `relationship`
- `mechanism`
- `polarity`
- `directness`
- `severity`
- `confidence`
- `conditions_json`
- `path_distance`
- `source_status`

### `aql_knowledge_graph_hypotheses`

One row per graph-derived hypothesis AQL should test against current evidence.

Suggested columns:

- `run_id`
- `asof_time_utc`
- `hypothesis_id`
- `subject_id`
- `anchor_node_id`
- `target_node_id`
- `hypothesis_text`
- `expected_direction`
- `mechanism`
- `supporting_edge_ids_json`
- `graph_confidence`
- `evidence_status` (`untested`, `supported`, `contradicted`, `stale`, `insufficient`)
- `supporting_claim_ids_json`

These frames should be materialized with the rest of AQL so graph influence is auditable.

### `aql_knowledge_graph_change_proposals`

One row per graph edit proposed by AQL.

Suggested columns:

- `run_id`
- `asof_time_utc`
- `proposal_id`
- `proposal_type` (`add_node`, `add_edge`, `update_edge`, `remove_edge`, `merge_nodes`, `deprecate_node`)
- `status` (`proposed`, `needs_review`, `auto_committed`, `rejected`, `superseded`)
- `subject_id`
- `source_node_id`
- `target_node_id`
- `edge_id`
- `proposed_node_json`
- `proposed_edge_json`
- `current_edge_json`
- `rationale`
- `evidence_summary`
- `supporting_claim_ids_json`
- `contradicting_claim_ids_json`
- `confidence`
- `risk_level` (`low`, `medium`, `high`)
- `review_url` or `commit_id`

This frame is the durable audit trail for graph write intent.

## Integration Points

### 1. Candidate Research Planning

Before `_plan_candidate_research(...)`, resolve each candidate symbol and company name against the graph.

Use the result to enrich the plan with:

- priority entities
- upstream dependencies
- downstream beneficiaries
- likely substitutes
- known macro sensitivities
- terms that should be preserved in web search queries

This fixes a known search-pipeline weakness: domain-specific terms should survive planning and retrieval.

### 2. Evidence Retrieval

Graph context should add targeted search routes, not replace live evidence.

Examples:

- if a uranium miner moves, include searches for uranium spot prices, nuclear fuel cycle, utilities, and enrichment
- if a semiconductor equipment name moves, include AI capex, advanced packaging, lithography, and memory supply-chain terms
- if a macro release moves rates, include graph-linked rate-sensitive assets and expected transmission paths

The important rule: graph context creates questions; evidence still answers them.

### 3. Candidate Graph Construction

The current attention candidate graph is built from same-industry, role, and correlation logic.

Add knowledge-graph edges as a separate edge type:

```text
edge_type = "knowledge_graph"
```

Keep these fields distinct from price-correlation edges:

- `relationship`
- `mechanism`
- `polarity`
- `directness`
- `confidence`
- `source_status`

Do not blend graph confidence into observed market correlation. Use it as a prior for clustering and explanation, not proof.

### 4. Macro Hypotheses

The current macro path has configured causal edges. The knowledge graph can generalize that.

For each macro release or macro surprise:

1. resolve the release concept into graph nodes
2. traverse to affected asset classes, sectors, commodities, and symbols
3. generate expected-direction hypotheses
4. verify those hypotheses with current market moves and current evidence

This should complement the existing macro profile, then later absorb parts of it only when coverage is clearly better.

### 5. Writers

Writers should receive graph context as labeled prior context:

```text
Graph prior:
- Edge: helium -> MRI magnets
- Mechanism: helium is used for cooling superconducting magnets
- Confidence: 0.82
- Status: reviewed

Current evidence:
- claim ids...
```

Prompt rule:

- graph priors may suggest explanations
- current evidence must support final causal language
- unsupported graph paths should be phrased as watch items or possible channels, not as confirmed causes

### 6. Graph Change Proposals

After claims and hypotheses are extracted, AQL can compare current evidence against the graph neighborhood.

Proposal types:

- `add_node`: evidence repeatedly mentions a relevant entity that does not resolve to an existing node
- `add_edge`: evidence supports a relationship that is missing from the current graph
- `update_edge`: evidence supports changing mechanism, polarity, directness, confidence, lag, or conditions
- `remove_edge`: evidence contradicts an existing edge or shows it no longer applies
- `merge_nodes`: evidence shows two nodes represent the same entity
- `deprecate_node`: a node is obsolete, too broad, duplicated, or no longer useful

Examples:

- If AQL sees several current sources linking `SMR` demand to uranium fuel services, but the graph only links uranium miners to spot uranium, propose an edge from `small_modular_reactors` to `uranium_fuel_services`.
- If the graph says a company benefits from lower rates, but repeated evidence shows its current issue is balance-sheet stress or customer concentration, propose lowering edge confidence or adding conditions.
- If an edge says `oil_prices -> airline_margins` with negative polarity, but the current event is about hedged fuel exposure, propose an edge condition rather than deleting the relationship.

Important: a single article should almost never delete an edge. Removal needs stronger evidence than addition, because many edges are conditional rather than false.

## Add/Delete Scenarios

These are the concrete situations where AQL should propose graph changes.

### Add Edge Scenarios

Add an edge when current evidence supports a missing relationship between two existing nodes.

Good scenarios:

- **New supply-chain link:** multiple sources say a component, material, or service is now a key input to another node. Example: `advanced_packaging_capacity -> ai_accelerator_supply`.
- **New customer/end-market exposure:** filings or earnings calls show a company now depends on a specific end market. Example: `data_center_capex -> power_equipment_supplier`.
- **New macro transmission path:** current market reaction repeatedly shows a macro release flowing into an asset class or sector not covered by the graph. Example: `real_yields -> unprofitable_growth_equities`.
- **Regulatory dependency:** a policy or permitting process becomes material to a company, commodity, or sector. Example: `nuclear_license_extensions -> uranium_demand`.
- **Substitution relationship:** evidence shows buyers switching from one material, product, or vendor to another. Example: `copper_shortage -> aluminum_substitution`.
- **Geographic chokepoint:** an event makes a route, port, country, or region a relevant dependency. Example: `red_sea_shipping_disruption -> container_shipping_rates`.

AQL should require:

- at least one strong source, preferably two independent sources
- a clear mechanism
- a direction or relationship type
- enough specificity to avoid generic edges like `economy -> stocks`

### Add Node Scenarios

Add a node when evidence repeatedly mentions a relevant entity that cannot be resolved to an existing node.

Good scenarios:

- **Named commodity, input, or material:** `gallium`, `high-purity quartz`, `HALEU`, `helium_3`.
- **Named process or bottleneck:** `advanced_packaging`, `EUV_lithography`, `LNG_liquefaction_capacity`.
- **Named regulation or policy:** `IRA_tax_credits`, `Basel_III_endgame`, `nuclear_license_extension`.
- **Named infrastructure category:** `grid_transformers`, `subsea_cables`, `gas_storage`.
- **Named end market:** `AI_data_centers`, `electric_arc_furnaces`, `SMR_deployment`.
- **New investable public entity:** a ticker or company appears in evidence and does not resolve to the current entity set.

AQL should not add a node for:

- one-off article phrasing
- vague themes like `risk`, `sentiment`, or `growth`
- duplicate labels that should be aliases of existing nodes
- overly broad categories that will connect to everything

### Update Edge Scenarios

Update an edge when the relationship still makes sense, but the graph metadata is wrong or incomplete.

Good scenarios:

- **Condition needed:** `oil_prices -> airline_margins` remains true, but evidence shows hedging changes near-term impact. Add `conditions_json`, do not remove the edge.
- **Polarity correction:** the graph says positive, but the relationship is negative for this node. Example: higher rates hurt levered real estate.
- **Directness correction:** a relationship is real but indirect. Example: `AI_capex -> copper_demand` may flow through data-center power buildout.
- **Confidence adjustment:** repeated current evidence supports raising confidence, while repeated failed hypothesis checks support lowering confidence.
- **Mechanism rewrite:** evidence supports the edge but the stored mechanism is too vague or wrong.

### Remove Edge Scenarios

Remove means tombstone or suppress the edge, not physically delete it.

Good scenarios:

- **Relationship is factually wrong:** a graph edge links an input to the wrong product, customer, or company, and reliable evidence contradicts it.
- **Company exposure changed structurally:** a company sold a business, exited a market, spun off a segment, or changed supplier/customer mix.
- **Ticker/entity mismatch:** an edge was attached to the wrong security or company due to name collision.
- **Old dependency no longer applies:** a supply relationship ended, a contract expired, a mine closed, a route was abandoned, or a regulation sunset.
- **Duplicate or worse edge:** another edge captures the same relationship with better endpoints and mechanism. Remove or deprecate the weaker duplicate.
- **Spurious correlation became graph fact:** a prior market co-move was incorrectly stored as a durable causal relationship.

Removal should be review-only by default. AQL should not auto-remove reviewed or seeded edges from one research run.

### Delete/Deprecate Node Scenarios

Delete means tombstone or deprecate the node, not physically remove history.

Good scenarios:

- **Duplicate node:** `AI_data_centers` and `data_centers_ai` represent the same concept. Prefer merge over delete.
- **Bad extraction:** a node was created from a sentence fragment, article title, or generic phrase.
- **Wrong entity type:** a company node was created for a product name, or a concept node was created for a ticker alias.
- **No durable meaning:** the node is too broad to support useful traversal, such as `market`, `demand`, `uncertainty`, or `technology`.
- **Obsolete entity:** a product, policy, facility, or ticker is no longer active and should not appear in current graph traversal.
- **Privacy or compliance issue:** a node should not be retained in the graph.

Node deletion needs extra care because deleting a node affects all connected edges. Prefer:

1. merge duplicate nodes
2. deprecate broad or obsolete nodes
3. tombstone only when the node is clearly wrong or unsafe

### Do Not Change The Graph Scenarios

AQL should refuse or only log a low-confidence proposal when:

- evidence is only price action with no mechanism
- sources disagree and there is no clear majority
- the change depends on a single weak source
- the relationship is probably temporary market positioning
- the proposed node would be a synonym or alias of an existing node
- the edge is really a condition on an existing edge
- the evidence supports a one-time event, not a durable relationship

## Proposal Decision Matrix

| Situation | Preferred proposal | Auto-commit? |
|---|---|---|
| Strong evidence of missing relationship | `add_edge` | Later, low-risk only |
| Strong evidence of missing concept/entity | `add_node` | Review first |
| Existing relationship true only under conditions | `update_edge` | Later, low-risk only |
| Existing relationship contradicted | `remove_edge` | No |
| Duplicate concepts | `merge_nodes` | No |
| Broad/generic/bad node | `deprecate_node` | No |
| One-off event with no durable relationship | reject/log only | No |

## Read/Write Architecture

Use a three-layer write path:

```text
AQL evidence + claims
  -> graph proposal generator
  -> graph review/commit policy
  -> services.knowledge_graph commit path
```

### 1. Proposal Generator

This lives in `services/aql/knowledge.py` or a sibling module such as `services/aql/knowledge_proposals.py`.

Inputs:

- graph matches and edges
- extracted claims
- candidate/event/macro context
- current source document metadata
- existing graph confidence and source status

Outputs:

- normalized proposal rows
- optional draft payload compatible with `commit_knowledge_graph_review(...)`

The proposal generator should be evidence-first and deterministic where possible. Use LLMs to summarize rationale and normalize messy relationships, not as the only judge.

### 2. Review/Commit Policy

Add a policy helper:

```python
def classify_graph_change_proposal(proposal: dict[str, Any]) -> str:
    ...
```

Suggested outcomes:

- `reject`: weak evidence, duplicate, bad nodes, or ambiguous relationship
- `needs_review`: useful but risky or high-impact
- `auto_commit`: low-risk addition/update with strong support

Default policy should be conservative:

- auto-commit disabled at first
- add/update proposals go to admin review
- remove/merge/deprecate always require review
- seeded edges require review before modification
- committed user-reviewed edges require stronger evidence than agent-suggested edges

### 3. Commit Adapter

Do not write separate SQL from AQL. Add a public graph-service function that accepts a normalized change set:

```python
def commit_knowledge_graph_delta(
    *,
    query: str,
    base_snapshot_id: str | None,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    deleted_node_ids: list[str],
    deleted_edge_ids: list[str],
    summary: str,
    created_by: str,
    source_run_id: str,
) -> dict[str, Any]:
    ...
```

Internally this can reuse the same normalization and SQL path as `commit_knowledge_graph_review(...)`.

Why add this instead of calling `commit_knowledge_graph_review(...)` directly:

- AQL proposals are deltas, not Streamlit-edited tables
- the commit should preserve `source_run_id`
- the commit should store proposal ids and supporting claim ids
- the graph service remains the only owner of graph persistence

## Suggested Data Model Additions

The existing tables can support basic writes today, but write-back gets cleaner with two additions.

### `knowledge_graph_change_proposals`

Durable queue for AQL-generated proposals.

Columns:

- `proposal_id`
- `run_id`
- `proposal_type`
- `status`
- `risk_level`
- `query_text`
- `subject_id`
- `payload_json`
- `evidence_json`
- `created_by`
- `created_at`
- `reviewed_by`
- `reviewed_at`
- `commit_id`

### Edge attributes for evidence lifecycle

Add or store in `attributes_json`:

- `supporting_claim_ids`
- `last_supported_at`
- `last_contradicted_at`
- `support_count`
- `contradiction_count`
- `review_status`
- `source_run_ids`

This lets the graph evolve without throwing away why a relationship exists.

## Adapter API

Suggested public helpers in `services/aql/knowledge.py`:

```python
def build_knowledge_context_for_candidates(
    candidates: pd.DataFrame,
    *,
    run_id: str,
    asof_time_utc: pd.Timestamp,
    max_matches_per_subject: int = 5,
    neighborhood_depth: int = 2,
    max_edges_per_subject: int = 30,
) -> KnowledgeContext:
    ...
```

`KnowledgeContext` can hold:

- `matches_frame`
- `edges_frame`
- `hypotheses_frame`
- `subject_context`

Also add a smaller helper for macro releases:

```python
def build_knowledge_context_for_macro_releases(...) -> KnowledgeContext:
    ...
```

Keep the adapter deterministic-first. If embeddings are unavailable, it should still work with id, label, alias, and description matching.

For write-back:

```python
def propose_knowledge_graph_updates(
    *,
    context: KnowledgeContext,
    claims_frame: pd.DataFrame,
    run_id: str,
    asof_time_utc: pd.Timestamp,
) -> pd.DataFrame:
    ...

def submit_knowledge_graph_proposals(
    proposals: pd.DataFrame,
    *,
    mode: str = "review_queue",
    created_by: str = "aql",
) -> pd.DataFrame:
    ...
```

`mode` should start with `review_queue`. Later modes can include `auto_commit_low_risk` after enough review history exists.

## Phased Implementation

### Phase 1: Read-only Graph Context

- Add `services/aql/knowledge.py`
- Resolve candidates to graph nodes
- Materialize `aql_knowledge_graph_matches` and `aql_knowledge_graph_edges`
- Add no behavior change to writers yet

Reliability: high. This is read-only and auditable.

### Phase 2: Research Planning Enrichment

- Feed compact graph neighborhoods into `_plan_candidate_research(...)`
- Add graph-derived priority entities and query terms
- Keep current web evidence and claim extraction unchanged

Reliability: medium-high. It improves search recall without changing final-writing rules.

### Phase 3: Graph-Derived Hypotheses

- Generate `aql_knowledge_graph_hypotheses`
- Link hypotheses to claims after extraction
- Mark each hypothesis as supported, contradicted, or insufficient

Reliability: medium. Needs careful tests to avoid treating graph priors as proof.

### Phase 4: Writer Context

- Give writers the verified graph hypotheses
- Require causal sentences to cite current evidence, not only graph paths
- Surface unsupported graph paths as watch items

Reliability: medium. This affects user-facing narrative, so it needs UI-path verification.

### Phase 5: Macro Profile Simplification

- Compare graph-derived macro relationships against `attention_macro_signal_profile.v1.yaml`
- Move stable reviewed relationships into the graph over time
- Keep config for scoring thresholds and release-specific runtime policy

Reliability: lower until graph coverage is broad. Do this after Phases 1-4 prove useful.

### Phase 6: Proposal Queue

- Generate `aql_knowledge_graph_change_proposals`
- Store proposals in a graph proposal table or materialized AQL frame
- Add an admin review view that shows proposed add/update/remove actions with supporting and contradicting claims
- No auto-commit yet

Reliability: medium-high. This adds write intent without letting the pipeline rewrite durable graph state.

### Phase 7: Reviewed Commit From AQL Proposals

- Add `commit_knowledge_graph_delta(...)` to `services/knowledge_graph.py`
- Let the admin review page approve proposals into graph commits
- Store `source_run_id`, proposal ids, and claim ids in commit metadata
- Clear graph snapshot cache after commit

Reliability: medium. This is real graph mutation, but still human-approved.

### Phase 8: Narrow Auto-Commit

- Enable only for low-risk `add_edge` or `update_edge` proposals with strong evidence
- Keep removals, merges, seeded-edge changes, and user-committed-edge changes review-only
- Add rollback through tombstone/revert commits, not destructive deletes

Reliability: low-medium until there is enough proposal/review history. This should be opt-in dev first.

## Testing

Unit tests:

- adapter works when the Postgres graph store is not configured
- seeded graph nodes resolve deterministically
- graph edges are bounded by depth and max edge limits
- no duplicate frames for duplicate symbols
- graph confidence does not overwrite evidence confidence
- proposal generator emits add/update/remove proposal types without writing to Postgres
- removal proposals require contradicting claims
- seeded and committed edge removals are classified as review-only
- commit adapter preserves proposal ids and supporting claim ids

Pipeline tests:

- `build_bottom_up_attention_artifacts(...)` includes empty graph frames when no matches exist
- graph frames are populated for seeded concepts
- graph proposal frame is populated when claims support missing relationships
- writer inputs label graph priors separately from current evidence

Store tests:

- approved add-edge proposal creates an edge commit
- approved remove-edge proposal tombstones the edge rather than deleting the row
- commit metadata includes source run id, proposal ids, and claim ids
- rejected proposals do not change the graph snapshot

User-path test:

- run the dev Attention build against a known seeded concept such as uranium, helium, or AI infrastructure
- verify the homepage/event bundle shows better second-order context without making unsupported causal claims

## Complexity And Reliability Notes

Reliable now:

- read-only graph context
- trace materialization
- deterministic query enrichment
- graph-edge display or debug inspection
- review-queue proposals backed by evidence and claims

Risky if done too early:

- letting graph priors directly drive final causal language
- replacing macro config with graph traversal before coverage is proven
- committing graph edits automatically from AQL research without review history
- using generic LLM graph expansion on the render path
- deleting or merging reviewed graph entities from one research run

The first useful version should be boring: read graph, materialize trace, improve search plans, and verify with evidence.

The first useful write version should also be boring: propose changes, show evidence, let an admin approve, and commit through the existing graph store. Auto-commit comes later and only for narrow low-risk changes.

## Open Questions

- Should `subject_id` for candidates be `candidate_id` or normalized `symbol`? Prefer `candidate_id` for trace rows and keep `symbol` as a column.
- Should knowledge graph node embeddings be precomputed in the pipeline job? Current graph search already degrades without embeddings, so this is optional.
- Should graph hypotheses be shown in the admin Experiment page first before surfacing them in Home? Prefer yes for Phase 3.
