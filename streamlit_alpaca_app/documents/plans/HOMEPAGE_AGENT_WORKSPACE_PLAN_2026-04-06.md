# Homepage Agent Workspace Plan (2026-04-06)

## Goal

Add a homepage agent workspace that feels like a Claude Code-style interface, but is grounded in Spectral Nature's existing data/query stack.

The agent should be able to:

- read any supported dataset, chart model, anomaly feed, research bundle, or run trace
- search news and the web with citations
- use RAG over internal research/evidence/document corpora
- run bounded analysis code in a sandbox
- save user-facing notes, plans, and intermediate observations
- return charts, tables, code, and written outputs as first-class artifacts

## What This Should Not Be

- Not raw chain-of-thought exposure.
- Not direct `app.py` scraping.
- Not unrestricted shell access from the UI process.
- Not a second parallel data stack that bypasses `data_access/` and `api/`.

The right product is a tool-using workspace with explicit notes and artifacts, not hidden reasoning dumped into the UI.

## Current State

The existing app already has useful seams we should extend:

1. Shared query layer
- `data_access/query_service.py`
- `data_access/query_registry.py`
- `data_access/layer.py`

2. Existing agentic research and evidence flow
- `services/attention_agentic.py`
- current search/news/evidence/claim generation
- existing run-trace datasets exposed through `resolve_attention_run_trace(...)`

3. Homepage bundle/detail model
- `services/homepage_v2.py`
- materialized homepage and research bundle reads already exist

4. Thin API surface
- `api/main.py`
- current API already exposes dataset/chart/query calls for non-UI clients

What is missing today:

- persistent agent sessions and messages
- a general tool registry for the homepage agent
- a sandbox execution service
- user-facing notes/scratchpad storage
- a homepage chat/workspace shell
- a proper RAG index over internal unstructured content

## Product Shape

Add a new `Home -> Agent Workspace` mode instead of replacing the current homepage feed.

Recommended UI layout:

1. Left rail
- session history
- pinned homepage context
- recent bundles/events

2. Center pane
- chat transcript
- tool-call status blocks
- artifact cards (chart, table, text, code, file)

3. Right rail
- selected context objects
- notes / working pad
- run status and budgets

4. Launch points
- global "Ask the workspace" composer on the homepage
- "Open in workspace" from any homepage beat, chart, anomaly, or research bundle

This keeps the homepage useful as a market dashboard while adding an agentic work surface on top.

## Design Principles

- Fix at source: the agent should call stable tool contracts, not UI internals.
- Structured data stays query-driven; RAG is for unstructured evidence.
- Materialized-first remains the default when data already exists.
- Sandbox execution is isolated from the Streamlit app process.
- Every answer should carry provenance: datasets, documents, searches, tool calls, and timestamps.
- Start in dev only. Do not wire prod execution or broad permissions first.

## Target Architecture

### 1) Agent Orchestrator

Add a new service layer, for example:

- `services/agent_workspace.py`
- `services/agent_tools.py`
- `services/agent_notes.py`
- `services/agent_rag.py`
- `services/agent_sandbox.py`

Responsibilities:

- manage sessions, turns, and run state
- choose and execute tools
- store artifacts and notes
- enforce budgets, timeouts, and tool permissions
- emit a clean event stream for the UI

The orchestrator should stay stateless between calls except for persisted session/run records.

### 2) Tool Surface

Do not let the model invent ad hoc tool behavior. Define a small explicit tool registry first.

Phase 1 tool set:

1. `list_capabilities`
- wraps `QueryService.list_capabilities()`

2. `get_dataset`
- wraps dataset operations in `data_access/query_service.py`

3. `get_chart`
- wraps chart operations in `data_access/query_service.py`
- returns canonical chart model plus optional Plotly render artifact

4. `get_attention_feed`
- wraps `resolve_attention_feed(...)`

5. `get_attention_bundle`
- wraps `resolve_attention_research_bundle(...)`

6. `get_attention_run_trace`
- wraps `resolve_attention_run_trace(...)`

7. `search_news`
- reuse/normalize current search flow from `services/attention_agentic.py`

8. `search_web`
- reuse SerpApi/Tavily routing and relevance filtering from `services/attention_agentic.py`

9. `rag_search`
- semantic retrieval over internal evidence/doc corpora

10. `write_note`
- create/update structured user-visible notes

11. `read_notes`
- fetch prior notes for the current session

Phase 2 tool:

12. `run_python`
- submit bounded analysis code to the sandbox service

### 3) RAG Layer

RAG should only cover content that benefits from semantic retrieval.

Initial corpora:

- `attention_source_documents`
- `attention_evidence_chunks`
- `attention_claims`
- materialized attention research bundles
- internal docs under `documents/reference/`, `documents/architecture/`, and selected plans
- later: filings/transcripts if those are materialized cleanly

Rules:

- Do not use embeddings as the primary access path for tabular market data.
- For prices, anomalies, macro series, and charts, call tools directly.
- RAG chunks must keep citation metadata: dataset/source id, URL or file path, title, timestamp, symbol/topic tags.

Suggested persisted assets:

- `agent_rag_documents`
- `agent_rag_chunks`
- `agent_rag_embeddings`

### 4) Sandbox Execution

This is a separate bounded service, not code running inside Streamlit or the main API worker.

Recommended contract:

- input:
  - code
  - declared language (`python` first)
  - referenced artifacts/datasets
  - execution budget
- output:
  - stdout/stderr
  - generated tables/files/charts
  - exit status
  - execution metadata

Guardrails:

- CPU, memory, and wall-time limits
- ephemeral filesystem
- no direct prod credentials
- network off by default
- if web access is needed, go through dedicated search tools instead
- read-only mounted input artifacts

Fastest reliable first cut:

- Python-only
- preinstalled analysis stack (`pandas`, `numpy`, `plotly`)
- no package install at runtime
- artifact capture for CSV, JSON, PNG, Plotly JSON, and text

### 5) Notes / Working Pad

The user asked for the ability to jot down thoughts. Implement this as an explicit scratchpad, not hidden model reasoning.

Suggested note types:

- `plan`
- `observation`
- `todo`
- `assumption`
- `result`

Suggested persistence:

- `agent_sessions`
- `agent_messages`
- `agent_runs`
- `agent_tool_calls`
- `agent_artifacts`
- `agent_notes`

This gives us a proper audit trail and resumable workspace behavior.

### 6) Homepage UI Integration

Keep the UI thin. The homepage should render persisted agent state and poll/stream run updates.

Recommended sequence:

1. add a new homepage workspace panel in `app.py`
2. use the existing homepage selection state to prefill context
3. allow the user to pin:
- a symbol
- a homepage beat
- a chart
- a research bundle
- an anomaly row
4. render artifact blocks from API payloads instead of rebuilding logic in the UI

The interface should expose:

- prompt composer
- session history
- tool activity
- artifact viewer
- notes viewer/editor
- rerun / stop / clear context controls

## API Additions

Extend `api/main.py` with agent-specific endpoints rather than overloading the generic query route forever.

Suggested endpoints:

- `POST /v1/agent/sessions`
- `GET /v1/agent/sessions/{session_id}`
- `POST /v1/agent/sessions/{session_id}/messages`
- `GET /v1/agent/runs/{run_id}`
- `GET /v1/agent/runs/{run_id}/events`
- `POST /v1/agent/sessions/{session_id}/notes`
- `GET /v1/agent/sessions/{session_id}/artifacts`

Reliability-first delivery:

- start with polling `GET /runs/{id}`
- add streaming later if needed

Polling is simpler and good enough for the first internal version.

## Data Flow

1. User opens homepage workspace.
2. Homepage injects current context objects.
3. API creates or resumes an `agent_session`.
4. Orchestrator plans tool calls.
5. Tool calls hit:
- query service for datasets/charts/anomalies
- search/research adapters for news/web
- RAG index for semantic document retrieval
- sandbox service for bounded code execution
6. Results are stored as artifacts and summarized into the transcript.
7. Notes can be created manually by the user or explicitly via the `write_note` tool.
8. Final answer references artifacts and sources.

## Phased Delivery

### Phase 0 - Contracts First

- define session/run/artifact/note schemas
- define tool registry and response envelopes
- define homepage workspace UI contract
- define sandbox contract, but do not implement execution yet

### Phase 1 - Read-Only Agent

- homepage workspace UI shell
- session/message persistence
- tool calls for datasets, charts, anomalies, bundles, run traces
- news/web search tool reuse from `attention_agentic`
- citations and provenance in every answer

Outcome:

- the agent can inspect the full current research surface but cannot execute code yet

### Phase 2 - RAG and Notes

- build internal chunking/indexing jobs
- add `rag_search`
- add notes/scratchpad UI and persistence
- allow pinning artifacts and notes into future turns

Outcome:

- the workspace can answer cross-document questions and keep user-visible working state

### Phase 3 - Sandbox Execution

- add sandbox service
- add `run_python`
- allow agent to request dataset/chart inputs as files or in-memory tables
- capture generated artifacts back into the workspace

Outcome:

- the agent can test calculations, transform data, and produce custom analysis artifacts safely

### Phase 4 - Better Operator UX

- run history
- cancel/retry
- artifact compare
- prompt templates
- one-click "open selected homepage context in workspace"

### Phase 5 - Wider Rollout

- dev validation period
- usage/budget monitoring
- failure and timeout review
- only then consider broader non-dev rollout

## Reliability and Complexity Assessment

- Product value: **High**
- Reliability if phased: **Moderate to High**
- Complexity if done all at once: **High**

Important constraint:

Trying to ship chat + RAG + arbitrary code + unrestricted web + persistent memory in one pass is too much risk for this codebase.

The reliable path is:

1. read-only tool use first
2. RAG second
3. sandbox third

## Key Technical Decisions

1. Reuse `QueryService` as the main structured-data contract.
2. Reuse `attention_agentic` search and evidence logic instead of creating new search clients.
3. Keep RAG for unstructured content only.
4. Keep notes explicit and user-visible.
5. Keep sandbox isolated and dev-scoped first.
6. Do not expose raw model reasoning; expose notes, plans, tool logs, and artifacts instead.

## Acceptance Criteria

- A homepage user can open a workspace and ask questions about current homepage context.
- The agent can fetch supported datasets/charts through stable tool contracts.
- The agent can cite internal documents, evidence chunks, and web results.
- The agent can save and retrieve notes within a session.
- Sandbox runs are isolated, time-bounded, and return artifacts cleanly.
- The UI can resume a prior session without losing artifacts or notes.
- Failures are explicit: tool error, timeout, missing dataset, missing citation, sandbox failure.

## Suggested First Build Slice

If we want the fastest useful version, build only this first:

1. homepage workspace shell
2. session/message persistence
3. `get_dataset`, `get_chart`, `get_attention_bundle`, `search_news`, `search_web`
4. artifact rendering
5. notes panel

Do not include sandbox execution in the first slice.

That gets a usable internal agent on the homepage with much lower risk.
