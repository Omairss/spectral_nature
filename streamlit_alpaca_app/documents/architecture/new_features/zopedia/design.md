# Wiki Graph Explorer Design Guide

## 1. Why This Exists
This design document captures how the current Wiki Data dialog was designed, debugged, and hardened so a new engineer or coding agent can reproduce it without repeating prior mistakes.

Primary goals:

- make graph exploration useful on dense, real-world corpora
- keep destructive delete operations safe and intentional
- ensure hidden-node workflows are still discoverable and actionable
- preserve clear mental models for users and maintainers

Primary source material:

- implementation in `studio/frontend/src/components/wiki-data-dialog.tsx`
- backend graph/delete contracts from route/manager/engine
- issue history in `updates.md` and this implementation cycle

## 2. Product Requirements (Final)

### 2.1 Core UX requirements

1. Non-destructive exploration and destructive deletion must be separate modes.
2. Explore mode must support strict 1-hop neighborhood focus.
3. Explore mode must support optional 2-hop expansion.
4. Massive graphs must support hiding non-focused nodes.
5. Users must still be able to add hidden nodes to focus.
6. Search/list selection must work regardless of rendered-graph hiding.
7. Dialog should default to Explore mode for safer first interaction.

### 2.2 Data/semantic requirements

1. Graph should represent wiki-link structure among selected page kinds.
2. Include/exclude analysis must be explicit and predictable.
3. Enrichment links should be reflected when represented as wikilinks and page kind is included.

## 3. Design Principles Used

1. Safety by default
- default mode is Explore, not Delete Queue.
- destructive actions require explicit mode and confirmation.

2. Separation of concerns
- exploration state and deletion state are isolated.
- list selection logic is independent from graph rendering projection.

3. Progressive disclosure for large graphs
- users can hide non-focus nodes and re-introduce them through seed search.

4. Deterministic topology interactions
- focus neighborhoods are computed using depth-limited traversal.

5. Keep contracts thin and stable
- frontend computes interactions client-side from a stable backend graph snapshot.

## 4. Interaction Model

### 4.1 Mode model

- `Delete Queue` mode:
  - selected nodes populate delete preview/apply.
  - no exploration side effects.

- `Explore` mode:
  - selected nodes are focus seeds.
  - focus neighborhood visualized by hop depth.
  - no delete preview/apply behavior tied to explore selection.

### 4.2 Selection model

- delete selection: `selectedNodeIds`
- explore selection: `exploreNodeIds`
- active selection is mode-dependent

### 4.3 Focus model

- seed set = `exploreNodeIds` intersected with currently kind-filtered nodes
- focus set = BFS expansion from seed set to configured depth
  - `1-hop`: direct neighbors only
  - `2-hop`: neighbors-of-neighbors included

### 4.4 Visibility model

- `searchFilteredGraphNodes` defines selectable/filter universe
- `displayGraphNodes` defines rendered projection
- if hide-unfocused is on, rendered projection is constrained to focus set

This dual-model is the most important design decision for large-graph usability.

## 5. UI Components and Controls

### 5.1 Top-level controls

- mode toggle: Delete Queue vs Explore
- include/hide analysis
- kind visibility toggles (source/entity/concept)
- graph layout switcher
- refresh graph
- clear current mode selection

### 5.2 Search and filters section

- text filter over `label + id`
- kind pills
- select filtered / clear filtered
- explore-only controls:
  - focus depth toggle (1-hop / 2-hop)
  - hide/show non-focused
  - add-seed input and candidates

### 5.3 Filtered nodes section

- checkbox list from `searchFilteredGraphNodes`
- selection writes into mode-appropriate selection store
- capped list rendering with guidance text

### 5.4 Selection section

- shows mode-appropriate selected nodes
- kind badges
- focus-size badge in Explore mode
- per-item remove

### 5.5 Delete preview section

- only meaningful in Delete Queue mode
- shows planned impact and grouped page lists
- apply action performs per-kind delete calls

## 6. Derived Data Pipeline (Implementation)

This is the exact order to implement and debug:

1. fetch backend graph snapshot (`graphNodes`, `graphEdges`)
2. apply kind + include-analysis filtering -> `kindFilteredGraphNodes`
3. project valid edges by filtered node IDs -> `kindFilteredGraphEdges`
4. apply text filter -> `searchFilteredGraphNodes`
5. compute explore focus set via adjacency + BFS depth
6. compute rendered nodes (`displayGraphNodes`)
7. compute rendered edges over displayed node IDs
8. compute layout positions and flow node/edge styles

Pseudo code:

```text
seedSet = exploreNodeIds intersect kindFilteredNodeIds
if seedSet empty: focusSet = empty
else:
  focusSet = seedSet
  frontier = seedSet
  repeat hopDepth times:
    nextFrontier = all unseen neighbors(frontier)
    add nextFrontier to focusSet
    frontier = nextFrontier

if explore mode and hideUnfocused and focusSet not empty:
  displayNodes = searchFilteredNodes intersect focusSet
else:
  displayNodes = searchFilteredNodes
```

## 7. Bugs Encountered and How We Fixed Them

### 7.1 Explore selection was tied to delete queue

Observed problem:
- users exploring graph unintentionally built delete queue.

Fix:
- introduced `interactionMode` and separate selection arrays.

Guardrail:
- delete preview only uses delete selection.

### 7.2 Requested strict 1-hop did not behave correctly

Observed problem:
- focus looked like connected component rather than one hop.

Fix:
- replaced broad traversal with hop-bounded BFS.

Guardrail:
- explicit 1-hop and 2-hop controls in UI.

### 7.3 Large graph remained unusable with opacity-only focus

Observed problem:
- dimming did not reduce enough visual noise.

Fix:
- added hide-unfocused projection and hidden-count telemetry.

Guardrail:
- keep optional show-all toggle.

### 7.4 Hidden-node workflows became hard to extend

Observed problem:
- once hidden, relevant nodes were hard to reintroduce.

Fix:
- added add-seed query over kind-filtered universe.
- supports row-click, keyboard enter, and button action.

Guardrail:
- seed add forces Explore mode, hide-unfocused on, and clears restrictive search.

### 7.5 Selection from add-seed list felt non-functional

Observed problem:
- user could search candidates but selection behavior felt inconsistent.

Fix:
- made entire row interactive and keyboard accessible.
- retained explicit action button state (`Select` or `Focus`).

Guardrail:
- selected seeds are deduplicated and immediately visible in selection badges.

### 7.6 Confusion about enrichment-link participation in graph

Observed problem:
- unclear whether enrichment links influence graph edges.

Fix:
- traced engine graph construction and documented exact semantics.

Guardrail:
- this design doc and architecture doc now define caveats explicitly.

## 8. Backend Contract Assumptions

### 8.1 Graph endpoint assumptions

- endpoint: `GET /api/inference/wiki/data/graph`
- supports `include_analysis` query param
- returns canonical kind values and normalized IDs

### 8.2 Delete workflow assumptions

- preview endpoint supports dry-run impact planning
- apply endpoint supports archive-first behavior by kind

### 8.3 Link semantics assumptions

- edges are based on `[[wikilink]]` parsing in included pages
- archived pages excluded from active graph page set

## 9. Layout Strategy

Supported layouts:

- force
- kind columns
- dagre vertical
- dagre horizontal
- radial

Design choices:

- force layout has node-count threshold and auto-fallback to dagre for stability
- viewport key includes mode/focus dimensions to ensure fit recalculation when context changes

## 10. Performance Constraints and Practical Limits

Known caps in current UX:

- `FORCE_LAYOUT_MAX_NODES` to avoid expensive force simulation
- seed candidate list trimmed to 24
- filtered node list display capped to first 200 rows

Rationale:

- maintain smooth interaction while preserving useful breadth

## 11. Accessibility and Input Behavior

Implemented behaviors:

- keyboard enter on add-seed input selects first candidate
- seed candidate rows are clickable and keyboard-triggerable (enter/space)
- checkbox list supports standard keyboard focus and toggle

Recommended future improvements:

- explicit ARIA labels for mode toggles and hop controls
- focus ring tuning for dense list interactions

## 12. Observability and User Feedback

Visual diagnostics included in dialog header badges:

- total nodes
- visible nodes
- displayed/total edges
- hidden node count in Explore mode
- current mode
- selection counts by mode
- current focus depth

User feedback patterns:

- toast notifications on graph load failure and delete preview/apply failures
- warning toast on partial delete apply

## 13. Rebuild Guide for Another Engineer/Agent

Follow this exact order:

1. implement API contract consumption and graph rendering baseline
2. add delete preview/apply path in isolation
3. add mode state and split selections
4. add filtered universe (`searchFilteredGraphNodes`)
5. add focus traversal and hop depth controls
6. add hide-unfocused projection
7. add seed-search + candidate interactions
8. harden empty states and badge telemetry
9. verify delete flows are unaffected in Explore mode
10. run regression checks from section 14

## 14. Regression Checklist

Functional checks:

1. open dialog -> defaults to Explore mode
2. select node in Explore mode -> delete queue count does not change
3. switch to Delete Queue mode -> prior explore seeds remain isolated
4. 1-hop focus shows only direct neighbors
5. 2-hop focus expands beyond 1-hop set
6. hide-unfocused removes non-focus nodes from rendered graph
7. hidden node can be found via add-seed search and focused
8. select filtered/clear filtered operate on search-filtered universe, not rendered subset
9. delete preview appears only for delete selection
10. delete apply updates graph and clears delete queue

Semantic checks:

1. include-analysis false removes analysis nodes from graph
2. wikilinks in analysis enrichment participate only when analysis is included
3. non-wikilink URLs do not create edges
4. archived pages do not appear in active graph

## 15. Teaching Script (Practical)

Suggested 30-minute walkthrough:

1. Explain the two-mode safety model.
2. Show the derived-data pipeline board: filtered universe vs rendered projection.
3. Demonstrate 1-hop, then 2-hop.
4. Toggle hide-unfocused and add hidden seed node.
5. Switch to delete mode and show preview/apply separation.
6. Close with backend link semantics and include-analysis caveat.

## 16. Future Design Iterations

- focus path highlighting between selected seeds
- save/load explore sessions
- server-assisted graph windowing for very large corpora
- broken-link visualization layer and repair actions
- timeline playback for graph changes over maintenance cycles
