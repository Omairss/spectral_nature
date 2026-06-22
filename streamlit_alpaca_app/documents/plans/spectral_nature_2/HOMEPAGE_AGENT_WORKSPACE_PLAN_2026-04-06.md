# Homepage Agent Workspace Plan (2026-04-06)

Deprecated status: historical planning note only. Do not use Home v2 names from this document as implementation targets; active Home helpers now live under route-neutral modules and stale paths are tracked in `documents/depricated.md`.

## Goal

Add a homepage agent workspace that feels like a Claude Code-style interface, but is grounded in Spectral Nature's existing data/query stack.

For Spectral Nature 2, the primary entrypoint should be a single text bar that serves as both:

- quick search / navigation, and
- agentic chat / analysis

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
- `services/homepage_support.py` for small shared Home helpers
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

For Spectral Nature 2, that workspace should be entered through one shared omnibar rather than separate search and chat widgets.

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
- global Spectral Nature 2 omnibar on the homepage
- "Open in workspace" from any homepage beat, chart, anomaly, or research bundle

This keeps the homepage useful as a market dashboard while adding an agentic work surface on top.

## Spectral Nature 2 Omnibar

The default homepage control in Spectral Nature 2 should be one text bar that can do two jobs:

1. quick search
- resolve symbols, macro releases, research bundles, saved sessions, charts, datasets, and documents
- support immediate navigation or result-list expansion

2. agentic chat
- accept natural-language questions, follow-ups, and analysis prompts
- create or resume an agent session when the request needs multi-step reasoning or tool use

The product rule should be:

- one input surface
- one backend intent resolver
- two possible outcomes: `search/navigate` or `agent run`

Recommended UX:

- typing shows direct matches and recent items
- pressing Enter on a strong exact match opens the resolved target quickly
- pressing Enter on a natural-language prompt starts or resumes an agent run
- if intent is ambiguous, show two explicit actions:
  - `Search`
  - `Ask Spectral Nature`

This avoids splitting user intent across separate bars while still keeping the fast path fast.

## Design Principles

- Fix at source: the agent should call stable tool contracts, not UI internals.
- API-first: Streamlit homepage and iPhone should both consume the same backend agent/session/tool contracts.
- One-bar entry: Spectral Nature 2 should expose one omnibar with intent routing, not separate search and chat systems.
- Structured data stays query-driven; RAG is for unstructured evidence.
- Materialized-first remains the default when data already exists.
- Sandbox execution is isolated from the Streamlit app process.
- Every answer should carry provenance: datasets, documents, searches, tool calls, and timestamps.
- Start in dev only. Do not wire prod execution or broad permissions first.

## Cross-Client Requirement

This workspace cannot be implemented as a homepage-only feature.

The correct architecture is:

1. shared backend agent/session/tool services
2. shared API surface over those services
3. Streamlit homepage workspace as one client
4. iPhone app as another client

That means:

- no Streamlit-only session model
- no tool execution logic embedded in `app.py`
- no artifact format that only the homepage can render
- no duplicate mobile-specific orchestration layer

If a feature is required by the homepage workspace, assume it should be reachable by the iPhone app too unless there is a strong reason not to expose it.

Concrete shared REST resource shapes now live in:

- `documents/plans/spectral_nature_2/AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`

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
It should be callable from the API layer first, with Streamlit acting as a client of that API contract.

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

1. add the Spectral Nature 2 omnibar at the top of the homepage
2. add a new homepage workspace panel in `app.py`
3. use the existing homepage selection state to prefill context
4. allow the user to pin:
- a symbol
- a homepage beat
- a chart
- a research bundle
- an anomaly row
5. render artifact blocks from API payloads instead of rebuilding logic in the UI

The omnibar should support:

- `auto` mode: resolve whether the input is quick search or agent chat
- `search` mode: force search/navigation behavior
- `agent` mode: force agent run behavior

The interface should expose:

- omnibar with inline intent hints
- session history
- tool activity
- artifact viewer
- notes viewer/editor
- rerun / stop / clear context controls

Intent routing behavior:

- strong entity/ticker/release match -> quick open / result list
- natural-language question or follow-up -> agent session message
- ambiguous short query -> show both search and ask actions

The workspace transcript can still expand below the omnibar after an agent run starts.

## API-First Delivery Requirement

Before building the homepage UI shell, define the shared API contract for agent workflows.

Minimum shared resources:

- `agent_session`
- `agent_message`
- `agent_run`
- `agent_tool_call`
- `agent_artifact`
- `agent_note`

The homepage should consume those resources through API-shaped payloads even if, in local dev, the initial implementation calls the same Python services in-process.

This keeps the iPhone path aligned and avoids a later rewrite from Streamlit widget state into mobile-safe contracts.
Use `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md` as the source of truth for those resource shapes.

## API Additions

Extend `api/main.py` with agent-specific endpoints rather than overloading the generic query route forever.

Suggested endpoints:

- `POST /v1/omnibar/resolve`
- `POST /v1/agent/sessions`
- `GET /v1/agent/sessions/{session_id}`
- `POST /v1/agent/sessions/{session_id}/messages`
- `GET /v1/agent/runs/{run_id}`
- `GET /v1/agent/runs/{run_id}/events`
- `POST /v1/agent/sessions/{session_id}/notes`
- `GET /v1/agent/sessions/{session_id}/artifacts`

Nice-to-have follow-up endpoints for mobile-friendly screens:

- `GET /v1/omnibar/suggestions`
- `GET /v1/agent/sessions/{session_id}/summary`
- `GET /v1/agent/artifacts/{artifact_id}`
- `POST /v1/agent/runs/{run_id}/cancel`

Reliability-first delivery:

- start with polling `GET /runs/{id}`
- add streaming later if needed

Polling is simpler and good enough for the first internal version.

## Data Flow

1. User types into the Spectral Nature 2 omnibar.
2. API resolves input intent.
3. If intent is `search` or `navigate`, the client opens the resolved item or displays result suggestions.
4. If intent is `agent`, the API creates or resumes an `agent_session`.
5. Orchestrator plans tool calls.
6. Tool calls hit:
- query service for datasets/charts/anomalies
- search/research adapters for news/web
- RAG index for semantic document retrieval
- sandbox service for bounded code execution
7. Results are stored as artifacts and summarized into the transcript.
8. Notes can be created manually by the user or explicitly via the `write_note` tool.
9. Final answer references artifacts and sources.

## Phased Delivery

### Phase 0 - Contracts First

- define session/run/artifact/note schemas
- define omnibar intent schema (`search`, `navigate`, `agent`, `ambiguous`)
- define tool registry and response envelopes
- define shared API contract for homepage + iPhone
- define homepage workspace UI contract as a client of that API
- define sandbox contract, but do not implement execution yet

### Phase 1 - Read-Only Agent

- add Spectral Nature 2 omnibar with shared intent routing
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
3. Put the agent/session/run/artifact model behind a shared API so iPhone can reuse it directly.
4. Keep RAG for unstructured content only.
5. Keep notes explicit and user-visible.
6. Keep sandbox isolated and dev-scoped first.
7. Do not expose raw model reasoning; expose notes, plans, tool logs, and artifacts instead.

## Acceptance Criteria

- A homepage user can use one Spectral Nature 2 text bar for both quick search and agentic chat.
- Strong direct matches open quickly without forcing an agent run.
- Natural-language prompts start or resume an agent session from the same bar.
- The agent can fetch supported datasets/charts through stable tool contracts.
- The agent can cite internal documents, evidence chunks, and web results.
- The agent can save and retrieve notes within a session.
- Sandbox runs are isolated, time-bounded, and return artifacts cleanly.
- The UI can resume a prior session without losing artifacts or notes.
- Failures are explicit: tool error, timeout, missing dataset, missing citation, sandbox failure.

## Suggested First Build Slice

If we want the fastest useful version, build only this first:

1. shared omnibar intent endpoint
2. shared agent session/run/artifact API endpoints
3. session/message persistence
4. `get_dataset`, `get_chart`, `get_attention_bundle`, `search_news`, `search_web`
5. homepage omnibar + workspace shell consuming those endpoints
6. artifact rendering
7. notes panel

Do not include sandbox execution in the first slice.

That gets a usable internal agent on the homepage with much lower risk.
