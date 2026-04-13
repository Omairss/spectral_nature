# Open Knowledge Graph Experiment Plan

Date: 2026-04-12

## Implementation Status

PoC implemented on the admin-only `Experiment` page.

Current scope:

- query resolver over the merged seeded + committed graph snapshot
- seed graph baseline for helium, AI infrastructure, and uranium-power concepts
- draft graph generation with local neighborhood expansion plus optional agentic suggestions
- editable node and edge review tables in Streamlit
- explicit commit into a Postgres-backed core knowledge graph
- recent commit log on the page

Current limits:

- review is table-based, not direct graph-canvas editing
- commit writes reviewed nodes, aliases, edges, and tombstones, but not a richer version graph yet
- query resolution is deterministic-first with optional embeddings; it is not a fully vector-native retriever
- agentic expansion can start from an unseen query, but quality still depends on the LLM and optional web-search runtime

## Goal

Build an admin-only PoC on the `Experiment` page where a user can type an open-ended dependency seed such as `helium`, `ASML`, or `fertilizer`.

The system should:

1. map the query to the closest existing nodes
2. expand a draft dependency graph around those nodes
3. let the user review, add, delete, or edit nodes and edges
4. commit the approved result into a durable core knowledge graph
5. use that committed graph as reference input for future graph generation

## This is different from the homepage graph

The homepage graph is a generated market-attention artifact.

- source: `services/attention_agentic.py` -> `build_homepage_attention_graph_payload(...)`
- purpose: summarize daily market structure from the latest attention pipeline
- lifetime: snapshot-style, tied to one run

This experiment should be a separate system.

- purpose: open-ended dependency and transmission knowledge graph
- lifetime: persistent and cumulative
- ownership: reviewed and committed by a human
- output: reusable graph memory that later features can query

Do not couple the PoC to the homepage graph contract.

## Best injection point

Use the current admin-only `Experiment` workspace.

- page entry: `streamlit_alpaca_app/app.py::_render_homepage_exp(...)`
- current state: placeholder page
- reason: clean surface, admin-only, low risk to existing user workflows

This is the right place for a PoC because it avoids mixing an unfinished editing workflow into `Home`.

## Product flow

### Phase 1 PoC user flow

1. User opens `Experiment`.
2. User types a seed query such as `helium`.
3. System resolves the query to candidate existing nodes.
4. User chooses one or more seed matches.
5. System builds a draft graph:
   - existing committed neighbors
   - suggested new nodes and edges from agentic generation
   - provenance and confidence on every proposed item
6. User reviews the graph in two ways:
   - graph visualization
   - editable node and edge tables
7. User can:
   - remove proposed nodes
   - remove proposed edges
   - add manual nodes
   - add manual edges
   - edit labels, mechanisms, confidence, or tags
8. User clicks `Commit`.
9. System writes the approved delta into the core knowledge graph store with audit metadata.
10. Future graph builds read from that committed graph first.

## Architecture

### 1. Core store

Use a small Postgres-backed knowledge graph store for the committed state.

Reason:

- this is row-level interactive data, not a batch analytics snapshot
- the user wants reviewed commits, auditability, and future reuse
- the app already has a Postgres store pattern in `services/auth_store.py`

Initial tables:

- `knowledge_graph_nodes`
  - `node_id`
  - `canonical_label`
  - `node_type`
  - `description`
  - `status`
  - `attributes_json`
  - `source_status` (`seeded`, `agent_suggested`, `user_added`, `committed`)
  - `created_at`
  - `updated_at`

- `knowledge_graph_aliases`
  - `alias_id`
  - `node_id`
  - `alias`
  - `alias_type` (`ticker`, `commodity`, `company_name`, `common_name`, `llm_generated`)
  - `embedding_vector_json` optional for PoC
  - `created_at`

- `knowledge_graph_edges`
  - `edge_id`
  - `source_node_id`
  - `target_node_id`
  - `relationship`
  - `mechanism`
  - `polarity`
  - `directness`
  - `severity`
  - `confidence`
  - `lag_value`
  - `lag_unit`
  - `conditions_json`
  - `attributes_json`
  - `source_status`
  - `created_at`
  - `updated_at`

- `knowledge_graph_commits`
  - `commit_id`
  - `query_text`
  - `summary`
  - `created_by`
  - `created_at`
  - `draft_payload_json`
  - `applied_delta_json`

### 2. Draft store

Use session-backed draft state for the live review pass.

PoC draft lifecycle:

- generated on request
- editable in Streamlit session state
- only durable after explicit `Commit`

If drafts need cross-session persistence later, add `knowledge_graph_drafts`.

### 3. Read model

Create a dedicated service, for example `services/knowledge_graph.py`, with four responsibilities:

1. load committed nodes, edges, and aliases
2. resolve seed queries to closest nodes
3. build a draft subgraph around selected seeds
4. apply reviewed graph deltas into the committed store

Keep this separate from the commodity-only dependency graph helper.

## Query to node matching

The seed resolver should be hybrid, not LLM-only.

### Step A. deterministic lookup first

Match against:

- exact node id
- ticker aliases
- canonical labels
- known aliases
- commodity proxy metadata
- taxonomy entities already in the app

### Step B. embedding similarity second

Use the existing embedding client support in `services/llm.py`.

- embed the incoming query
- compare it against stored node alias embeddings
- return top candidate nodes with scores

### Step C. LLM resolution last

Use the LLM only to disambiguate or group close matches.

Example:

- query: `helium`
- deterministic matches: none or weak
- embedding matches: `helium`, `industrial gases`, `MRI magnets`, `semiconductor etch`, `party balloons`, `Linde`, `Air Products`
- LLM decides the best seeds to propose and explains why

This is more reliable than asking the LLM to invent the seed mapping from scratch.

## Draft graph generation

The draft builder should combine three sources.

### 1. committed graph neighborhood

Start with nodes and edges already committed in the core graph.

### 2. local structured data

Use existing local sources when relevant:

- commodity proxy metadata
- entity taxonomy
- attention graph relationships
- company descriptions

### 3. agentic expansion

Use an LLM or agent to propose additional nodes and edges around the selected seeds.

Recommended shape:

1. retrieve the current neighborhood and local context
2. optionally run external web research for missing context
3. ask the model to return structured proposed nodes and edges
4. keep every suggestion marked as `proposed`, not committed

## Agentic generation path

Use the existing internal LLM and retrieval setup first.

Why:

- `services/llm.py` already supports JSON generation and embeddings
- `services/attention_agentic.py` already shows the pattern for planning, web search, chunking, and structured outputs
- `services/web_research.py` already supports SerpApi and Tavily

### Proposed PoC chain

1. resolve seeds from committed graph
2. collect nearby committed graph context
3. collect optional local evidence and company or commodity context
4. if the neighborhood is sparse, run web search
5. ask the model for:
   - candidate new nodes
   - candidate edges
   - short evidence summary per suggestion
   - confidence and rationale

### Reliability rule

The model should propose graph additions, not silently mutate the core graph.

Every agent-generated node and edge must remain reviewable until the user commits it.

## UI plan for the PoC

Keep the UI simple.

Do not start with a fully interactive drag-and-drop graph editor.

### Experiment page sections

1. `Seed Query`
   - text input
   - `Resolve` button
   - candidate seed matches with scores

2. `Draft Graph`
   - plotly or networkx graph view
   - color nodes by source status and type
   - style proposed edges differently from committed edges

3. `Review Nodes`
   - editable table
   - allow delete, rename, type change, description edit

4. `Review Edges`
   - editable table
   - allow delete, relationship edit, confidence edit, mechanism edit

5. `Commit`
   - commit summary text
   - explicit button
   - post-commit success state

### Graph rendering

Reuse existing graph plotting patterns rather than inventing a new renderer first.

The current best base is:

- `services/attention_graph_topology.py`
- `services/attention_graph_network.py`

For the PoC, a static network graph plus editable tables is enough.

## Commit model

Commit should write a delta, not overwrite the whole graph blindly.

On commit:

1. upsert approved nodes
2. upsert approved aliases
3. upsert approved edges
4. record one commit row with:
   - query
   - user
   - timestamp
   - graph summary
   - applied delta

This is important for audit and rollback.

## What gets referenced later

Future graph generations should consult the committed knowledge graph first.

Priority order:

1. committed graph store
2. local app metadata and structured datasets
3. agentic expansion and web research

That makes the graph cumulative instead of stateless.

## Scope for the first PoC

### In scope

- admin-only experiment page
- seed query resolution
- draft graph generation
- graph review tables
- commit to durable store
- future runs read committed graph
- one good domain demo such as `helium`

### Out of scope

- homepage graph replacement
- multi-user review workflow
- full graph diff UI
- automatic background retraining or batch graph extraction
- autonomous commit with no human approval

## Recommended implementation order

### Step 1. store and service layer

- add `services/knowledge_graph_store.py`
- add schema bootstrap similar to `auth_store`
- add read and write helpers

### Step 2. resolver

- deterministic alias lookup
- embedding similarity helper
- LLM disambiguation response schema

### Step 3. draft generator

- merge committed neighbors with agentic suggestions
- normalize into one node and edge contract

### Step 4. Experiment page UI

- replace placeholder with PoC workspace
- render graph
- render editable tables
- add commit button

### Step 5. audit and tests

- service tests for resolver and commit behavior
- UI contract checks for the experiment page payloads
- one regression test that a committed node becomes reusable in the next query

### Step 6. dev deployment

- deploy only after end-to-end testing on the actual Experiment path

## External tool decision

For the PoC, do not bring in a separate graph database or a third-party graph-generation service yet.

Reason:

- it adds complexity before the product loop is proven
- the repo already has enough primitives for a useful first pass
- the core risk is graph quality and review workflow, not graph-database scale

If the PoC works, a later phase can evaluate:

- Neo4j or another graph store
- vector index for alias search
- richer graph-edit UI

## Main risks

### 1. false or weak LLM edges

Mitigation:

- human review required
- show confidence and rationale
- keep proposed vs committed state visible

### 2. bad seed matching

Mitigation:

- hybrid resolver
- expose top candidate matches before graph generation

### 3. overloading the homepage graph

Mitigation:

- keep Experiment graph separate from attention-home graph

### 4. weak persistence

Mitigation:

- use durable DB-backed committed store, not local files

## Success criteria

The PoC is successful if a user can:

1. type `helium`
2. get sensible seed matches
3. generate a draft graph with related companies, commodities, and downstream dependencies
4. edit the graph
5. commit it
6. type a related query later and see the committed graph influence the new result
