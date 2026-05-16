# Knowledge Graph AQL Evaluation Report

**Date:** 2026-04-25

## Scope

I tested the proposed knowledge-graph-to-AQL workflow against cached attention artifacts:

- past homepage summaries
- level 1 attention feed groups from `top_events_json`
- level 2 stock feed items from `must_read_movers_json`

Data used:

- `attention_home_snapshots_1d` cached runs from 2026-04-01 through 2026-04-23
- current seeded + committed knowledge graph snapshot
- no graph writes were made

Knowledge graph snapshot:

- 47 nodes
- 49 edges
- main coverage: AI infrastructure, uranium/SMR/nuclear, helium/industrial gases, commodity proxy dependency graphs

## Method

I ran two passes.

### Pass 1: Direct Full-Text Resolver

This passed the full summary/event/stock text into `search_knowledge_graph_nodes(...)`.

Result: not reliable enough.

The resolver produced false high-confidence matches such as `helium`, `LIN`, and `LIT` on unrelated software, biotech, and financial-stock text. Root cause: the resolver scores context overlap using common words such as `and`, `on`, `as`, `company`, and `demand`. That is fine for short seed queries like `helium`, but not for long feed narratives.

Conclusion: AQL should not pass full narrative blobs directly into the current KG search function.

### Pass 2: Stricter AQL-Style Resolver

I simulated the adapter AQL should use:

- extract explicit subject symbol when available
- prefer exact ticker and exact alias matches
- use stopword-filtered context overlap
- require useful domain terms, not generic words
- build one-hop graph neighborhoods around matched nodes
- compare against an offline text-similarity baseline standing in for embedding-only retrieval

This produced usable results for domains covered by the graph.

## Constructed Graph Results

### 1. Past Summary: 2026-04-14 AI / Travel / Resource Rotation

Summary included AI infrastructure, travel cyclicals, resource pressure, Bloom Energy, Oracle, fuel cells, and quantum computing.

Constructed graph made sense:

- `NVDA -> ai_compute`
- `ai_compute -> datacenter_power`
- `datacenter_power -> power_grid`
- `power_grid -> CPER`
- `power_grid -> natural_gas_generation`
- `advanced_packaging -> NVDA`

Individual proposals:

- add node: `fuel_cells`
- add node: `BE`
- add node: `ORCL`
- add node: `quantum_computing`
- add edge: `fuel_cells -> datacenter_power`
- add edge: `BE -> fuel_cells`

Assessment: these adds make sense. They are not safe for automatic commit on one run, but they are strong review-queue candidates because the summary and stock item directly mention Bloom Energy, Oracle, fuel cells, and AI data centers.

No deletes made sense.

### 2. Level 2 Stock: BE, “Bloom Energy–Oracle fuel cell buildout tied to AI data centers”

Constructed graph made sense:

- `ai_compute -> datacenter_power`
- `datacenter_power -> power_grid`
- `baseload_power -> datacenter_power`
- `power_grid -> CPER`
- `power_grid -> natural_gas_generation`

Missing graph concepts were clear:

- `BE`
- `ORCL`
- `fuel_cells`

Individual proposals:

- add node: `BE`
- add node: `ORCL`
- add node: `fuel_cells`
- add edge: `BE -> fuel_cells`
- add edge: `fuel_cells -> datacenter_power`
- possible edge: `ORCL -> datacenter_power`

Assessment: good example of why write-back matters. The existing graph got the power-demand chain right but missed the specific company/product bridge.

Delete assessment: do not delete any existing power-grid or gas-generation edges. If anything, this evidence strengthens the `ai_compute -> datacenter_power` path.

### 3. Level 1 Group: 2026-04-15 AI–Quantum / SMR Event

Event: “AI–quantum narrative revival lifts quantum hardware and SMR developers.”

Constructed graph made sense:

- `NVDA -> ai_compute`
- `ai_compute -> datacenter_power`
- `SMR -> advanced_reactors`
- `advanced_reactors -> uranium`
- `advanced_packaging -> NVDA`
- `hbm_memory -> NVDA`

Individual proposals:

- add node: `quantum_computing`
- possible add edge: `ai_compute -> quantum_computing` or `NVDA -> quantum_computing`, but review only

Assessment: the SMR/nuclear side is already modeled well. The quantum side is missing. Add the node first. Be careful with the edge because “AI–quantum integration narrative” may be a market narrative, not a durable dependency.

Delete assessment: no deletes. The existing `SMR -> advanced_reactors -> uranium` path looked useful.

### 4. Level 1 Group: 2026-04-10 CoreWeave / AI Compute Contract

Event: “AI compute contract lifts CoreWeave while CDN and edge cloud peers slide.”

Constructed graph made sense:

- `ai_compute -> datacenter_power`
- `NVDA -> ai_compute`
- `advanced_packaging -> NVDA`
- `datacenter_power -> power_grid`
- `power_grid -> CPER`

Individual proposals:

- possible add node: `CRWV` / `CoreWeave`
- possible add node: `cloud_compute_capacity`
- possible add edge: `cloud_compute_capacity -> ai_compute`

Assessment: current graph gives the right second-order context. New nodes depend on whether the graph should cover individual AI infrastructure operators or stay at the supply-chain/theme level.

Delete assessment: no deletes.

### 5. Level 1 Group: 2026-04-01 Chip / Optical Networking Rally

Event: “Chip and optical networking names rally together without a clear catalyst.”

Constructed graph mostly made sense:

- `advanced_packaging -> NVDA`
- `NVDA -> ai_compute`
- `ai_compute -> datacenter_power`
- `semiconductor_etch -> AMAT`
- `semiconductor_etch -> ASML`

Missing concepts:

- `optical_networking`
- `800g_transceivers`

Individual proposals:

- add node: `optical_networking`
- add node: `800g_transceivers`
- add edge: `optical_networking -> ai_compute`

Assessment: add proposals make sense, but should be review-only. The evidence says the group rallied together; AQL would need retained claims or source docs to confirm durable networking demand, not just price co-movement.

Delete assessment: no deletes. This is a coverage gap, not an invalid existing edge.

### 6. Level 1 Group: 2026-04-23 ServiceNow / SaaS Weakness

Event: “ServiceNow guidance shock drags SaaS peers lower.”

Constructed graph did not make sense because the KG has little SaaS/software coverage. The strict pass still found weak generic matches like `NVDA`, `APD`, and `AMAT` from terms like `company`, `demand`, and `exposure`.

Individual proposals:

- possible add node: `NOW`
- possible add node: `enterprise_software`
- possible add node: `SaaS`
- possible add edge: `NOW -> enterprise_software`

Assessment: add only if AQL wants software-sector graph coverage. Otherwise log this as “no graph coverage” and avoid graph influence.

Delete assessment: do not delete any KG edge because the bad result came from resolver mismatch, not a bad stored graph edge.

### 7. Negative Controls: Biotech, Morgan Stanley, CarMax

Several unrelated stock/group items still produced weak matches to commodity or AI nodes when the resolver used generic words.

Examples:

- biotech regulatory momentum matched nodes like `GEHC`, `SMR`, or `CCJ`
- Morgan Stanley earnings matched `NVDA` or `CCJ`
- CarMax earnings matched power/uranium nodes

Assessment: these are resolver failures. AQL should return no KG context unless it has exact entities or strong domain terms.

Delete assessment: no deletes. Bad retrieval is not evidence that graph edges are wrong.

## Individual Add/Remove Assessment

### Adds That Make Sense

| Proposal | Evidence context | Assessment |
|---|---|---|
| `fuel_cells` node | Bloom Energy / Oracle / AI data centers | Strong review candidate |
| `BE` node | Stock-level BE mover | Strong review candidate |
| `ORCL` node | Oracle named as AI data-center customer | Strong review candidate |
| `fuel_cells -> datacenter_power` | Fuel cells tied to AI data-center buildout | Strong review candidate |
| `BE -> fuel_cells` | Bloom Energy supplies fuel-cell systems | Strong review candidate |
| `quantum_computing` node | Recurring quantum-computing group events | Strong review candidate |
| `optical_networking` node | Optical networking group event and AAOI stock item | Medium review candidate |
| `800g_transceivers` node | AAOI 800G transceiver order item | Medium review candidate |
| `optical_networking -> ai_compute` | AI networking demand context | Medium review candidate |

### Adds To Be Careful With

| Proposal | Concern |
|---|---|
| `ai_compute -> quantum_computing` | May be narrative/market sympathy rather than dependency |
| `NVDA -> quantum_computing` | Needs stronger source support than a stock-rally headline |
| `CoreWeave` / `CRWV` | Good if graph includes operators; noise if graph stays supply-chain focused |
| `NOW` / `SaaS` | Useful only if KG scope expands beyond physical dependency chains |

### Deletes That Make Sense

None from this test set.

The closest “remove” candidates were actually retrieval problems:

- commodity/industrial nodes appearing in software or finance cases
- AI/nuclear nodes appearing in biotech or consumer-finance cases

Those should be fixed in the AQL resolver, not by deleting graph nodes or edges.

### Removal Rule Confirmed

Do not delete edges because they were irrelevant to a feed item. Delete only when retained evidence directly contradicts the stored relationship, shows the relationship ended, or proves the endpoints are wrong.

## Graph Quality Findings

What worked:

- AI infrastructure paths are useful and readable.
- SMR/nuclear/uranium paths are useful.
- The graph gives better second-order context than stock/event text alone.
- Graph gaps are easy to identify as add proposals.

What did not work:

- The existing KG resolver is unsafe for long AQL text.
- Graph coverage is sparse for SaaS, financials, consumer, biotech, and many single-name stories.
- Generic context overlap creates false positives.
- The current graph can over-connect if AQL takes the top nodes without confidence gating.

Required source fixes before production:

1. Add an AQL-specific KG resolver that extracts subjects first.
2. Filter stopwords and generic words before context scoring.
3. Require exact symbol/alias or strong domain terms for graph activation.
4. Return “no graph coverage” rather than forcing weak matches.
5. Keep graph proposals separate from graph commits.

## KG Versus Embedding-Only

I could not run a live external embedding provider locally, so I compared KG traversal against an offline text-similarity baseline over node descriptions. This is not as strong as true embeddings, but it shows the main tradeoff.

### Where Embedding-Only Helps

Embedding-style retrieval is better at fuzzy matching:

- `BE` fuel-cell data-center text found `ai_compute`, `datacenter_power`, and `nuclear_fuel`.
- AAOI 800G transceiver text found semiconductor/AI nodes.
- AI compute summaries found `ai_compute` reliably.

Embedding-only is also simpler:

- no graph schema
- no edge maintenance
- no add/delete workflow
- fewer false graph paths

### Where Embedding-Only Falls Short

Embedding-only returns related nodes, not durable relationships.

It does not naturally answer:

- what is upstream versus downstream?
- is the relationship positive, negative, conditional, or indirect?
- should this become durable graph memory?
- which edge should be updated or removed?
- what second-order nodes should be traversed?
- what graph edit should be queued for review?

For example, embedding-style retrieval can say BE is close to `datacenter_power`, but the KG path can say:

```text
BE -> fuel_cells -> datacenter_power -> power_grid -> CPER / natural_gas_generation
```

That path is what AQL needs for second-order reasoning and add/update proposals.

### Best Architecture

Use both.

Recommended flow:

```text
exact entity extraction
  -> embedding retrieval for candidate node recall
  -> KG typed traversal for mechanisms and second-order paths
  -> evidence checks
  -> graph change proposals
  -> review or narrow auto-commit
```

Embeddings should help find candidate nodes. The KG should carry the durable relationship structure and write-back workflow.

## Final Recommendation

Do not ship direct full-text KG lookup into AQL.

Ship a stricter adapter:

1. exact symbols and aliases first
2. embedding/node-similarity recall second
3. stopword-filtered context gates third
4. typed graph traversal only after confident node matches
5. add/update/remove proposals generated only from evidence-backed claims

Initial write-back should only create a review queue. Based on this test, the first high-value proposal queue should target:

- `fuel_cells`
- `BE`
- `ORCL`
- `quantum_computing`
- `optical_networking`
- `800g_transceivers`

No edge or node removals should be committed from this test set.

