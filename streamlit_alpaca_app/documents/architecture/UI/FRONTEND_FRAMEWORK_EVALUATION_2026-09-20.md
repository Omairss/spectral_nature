# Frontend Framework Evaluation — 2026-09-20

Status: evaluation complete. No frontend implementation or deployment was performed.

## Decision

Adopt **Next.js App Router with React and TypeScript** as the target product frontend, backed by the existing FastAPI application.

Do not start a broad rewrite yet. Streamlit remains the active client until the shared service/API boundary can support a real frontend without importing Python product services into the presentation layer.

The important distinction is:

- **Target decision:** Next.js is a better long-term product UI framework for Spectral Nature.
- **Immediate action:** finish making the current UI replaceable, extend the FastAPI contract, then prove the choice with one end-to-end vertical slice.

This supersedes neither the global cleanup plan nor its frontend decision gate. It resolves which framework should be tested when those gates are met.

## Why The Current UI Has Crossed The Streamlit Threshold

Streamlit is still effective for internal analytics and fast Python-native dashboards. Spectral Nature now asks it to behave like a full product application:

- authenticated public and private flows;
- persistent navigation and deep-link handoffs;
- distinct desktop and mobile shells;
- conversational research with durable threads, sources, uploads, progress, and recovery;
- dense interactive market, graph, portfolio, options, and admin surfaces;
- background materialization with explicit freshness and run identity;
- user-specific state that must survive reruns and page transitions.

The current source confirms that this is not a theoretical concern:

- the Streamlit UI surface is 15 Python files and 16,316 lines;
- `app.py` is 7,757 lines and still owns the shell, authentication, Home, and Zopedia;
- the UI contains 430 direct `st.session_state` references and 57 explicit `st.rerun()` calls;
- it renders 48 Plotly charts and 42 dataframes;
- custom JavaScript is required for viewport detection and browser-cookie synchronization;
- large custom CSS blocks are required to override the framework shell and create separate mobile behavior.

These are not individually defects. Together they show that the product is implementing a client application inside a top-to-bottom rerun framework. Streamlit documents that widget interaction reruns the Python script by default and warns that excessive explicit reruns can be inefficient and make control flow harder to follow. Fragments reduce the scope of some reruns, but they do not change the underlying ownership model. See the official [execution-flow documentation](https://docs.streamlit.io/develop/api-reference/execution-flow) and [`st.rerun` caveats](https://docs.streamlit.io/develop/api-reference/execution-flow/st.rerun).

The conclusion is not that Streamlit is bad. It is that Spectral Nature's interaction model has outgrown the reason Streamlit was originally the right choice.

## Product-Specific Evaluation Criteria

The framework must support the existing philosophy rather than create another application spine.

1. **Product UI control:** responsive composition, durable navigation, accessibility, keyboard behavior, URL state, and design-system ownership.
2. **Interaction model:** localized state updates, optimistic interactions, long-running research progress, resumable runs, uploads, drawers, tables, and graph exploration.
3. **Backend boundary:** Python remains the owner of AQL/Zopedia, market data, auth truth, materialized artifacts, jobs, and persistence.
4. **Data-intensive ecosystem:** Plotly continuity, virtualized tables, charts, graphs, and large result sets.
5. **Mobile quality:** one responsive component system rather than Python-selected duplicate page renderers and CSS shell overrides.
6. **Incremental migration:** the new client must coexist with Streamlit while routes are proven.
7. **Operational maturity:** self-hosting in Azure Container Apps, browser testing, observability, and a maintainable hiring/tooling ecosystem.

## Product Form Addendum — 2026-10-02

The destination is not a conventional dashboard or visualization platform. It is an **agentic market-research workbench** that combines four interaction models:

- Bloomberg-like density, keyboard speed, live market context, and linked views;
- Jupyter-like agent-authored, reproducible analytical documents;
- VS Code-like tabs, panes, explorer, read-only code and diff views, task controls, and output surfaces;
- Claude Code-like collaboration with an agent that can inspect explicit context, propose changes, run authorized tools, and return durable artifacts.

Chat is the sole authoring and control interface. Users describe the analysis, question results, request revisions, approve bounded actions, cancel work, and ask the agent to rerun it. They do not type into code cells, edit source files, or invoke a shell. The agent writes code and notebook steps, and the workspace makes that work inspectable through code views, diffs, execution state, logs, outputs, and provenance.

Visualization is one artifact family inside that workbench. Tables, datasets, agent-authored notebooks, code, reports, Zopedia pages, source evidence, knowledge graphs, model runs, and job outputs are peers. The shared workspace must let users inspect, arrange, link, save, and resume them without turning each capability into a separate dashboard silo.

The target desktop composition is:

```text
Workspace/explorer rail | tabbed analytical canvas         | chat/context rail
                        | notebooks, code views, tables,   | conversation, plan,
                        | charts, reports, graph canvases  | sources, proposed edits
--------------------------------------------------------------------------------
command palette / status / durable jobs, output, logs, and run controls
```

This changes the migration shape, but not the framework decision or backend boundary:

1. Feature parity remains the first milestone and defines the capabilities, data, actions, and failure states that cannot be lost.
2. Parity routes should be implemented on shared workspace primitives and become openable artifacts rather than permanent isolated pages.
3. The agent receives only explicit workspace context: the active conversation, selected outputs, open entities, pinned datasets, artifacts, source refs, and user-approved files.
4. Notebook, code, and long-running ML execution happen in isolated per-run workers. The browser, Next.js BFF, API process, and research harness are orchestration clients, not execution sandboxes. Platform data enters through scoped read-only APIs or immutable input artifacts rather than production credentials inside the sandbox.
5. Agent-generated code and changes are inspectable artifacts or diffs. Users steer revisions through chat. A chat message does not implicitly authorize database writes, broker operations, host access, or arbitrary repository changes.
6. AQL, SAA, Zopedia, market services, and portfolio services remain backend capabilities behind typed contracts. The workspace composes them; it does not recreate them in TypeScript.

The resulting product model is **chat + typed artifacts + isolated execution + durable agent sessions**. The VS Code-like shell is an observability and artifact-navigation harness for the agent's work, not a user programming environment. Choosing chart libraries alone is therefore insufficient; the implementation evaluation must also cover read-only code/diff, notebook-rendering, data-grid, graph-canvas, pane/layout, command, and background-job surfaces as an integrated system.

## Comparison

Scores are a product-fit rubric from 1 to 5, not a general framework benchmark. The weighted total emphasizes Spectral Nature's current needs.

| Candidate | Product UI (25%) | Interaction (20%) | Python spine fit (20%) | Data UI (10%) | Mobile (10%) | Migration (10%) | Operations (5%) | Weighted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Next.js + React + TypeScript | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 2.5 | 4.0 | **4.70** |
| React Router framework mode | 5.0 | 5.0 | 4.5 | 5.0 | 5.0 | 2.5 | 4.0 | **4.60** |
| SvelteKit | 5.0 | 5.0 | 4.5 | 4.0 | 5.0 | 2.0 | 3.5 | **4.43** |
| NiceGUI | 3.5 | 4.0 | 3.0 | 4.0 | 4.0 | 3.0 | 2.5 | **3.50** |
| Reflex | 4.0 | 3.5 | 2.5 | 3.5 | 4.0 | 2.5 | 2.5 | **3.33** |
| Current Streamlit | 2.0 | 2.0 | 5.0 | 4.5 | 2.0 | 5.0 | 4.0 | **3.25** |

### Next.js + React + TypeScript — recommended

This is the best match for a product frontend with public pages, authenticated workspaces, server-mediated cookies, partial rendering, route-level loading, and complex client interactions.

Why it fits:

- React provides direct ownership of browser state and component behavior instead of reconstructing the page through Python reruns.
- App Router supports layouts, route loading boundaries, and streaming. The official [streaming documentation](https://nextjs.org/learn/dashboard-app/streaming) describes progressive rendering with `loading.tsx` and component-level Suspense.
- Next.js explicitly supports a Backend-for-Frontend layer through route handlers. That layer can keep tokens out of browser JavaScript and proxy narrowly to FastAPI without becoming a second domain backend. See the official [Backend-for-Frontend guide](https://nextjs.org/docs/app/guides/backend-for-frontend).
- Its authentication guidance separates authentication, session management, and authorization and recommends centralized data-access/DTO boundaries. See the official [authentication guide](https://nextjs.org/docs/app/guides/authentication).
- It is self-hostable as a Node server or Docker image. The official [self-hosting guide](https://nextjs.org/docs/app/guides/self-hosting) documents reverse-proxy, cache, multi-instance, version-skew, and streaming concerns relevant to Azure Container Apps.
- Existing Plotly figure JSON can be rendered through the official [`react-plotly.js` component](https://plotly.com/javascript/react/), which reduces chart migration risk.

The cost is real: TypeScript becomes a first-class language, the UI is substantially rewritten, and the deployment gains a Node service. Next.js caching also needs explicit restraint. Current-feed and user-specific routes should begin uncached or request-scoped; application freshness remains keyed by the materialized artifact identity, not an independent frontend cache policy.

### React Router framework mode — strong second choice

React Router's data and framework modes provide loaders, actions, pending UI, and SPA or server-rendered operation. Its official [mode comparison](https://reactrouter.com/start/modes) and [pending UI guide](https://reactrouter.com/start/framework/pending-ui) map well to this product.

It offers more architectural control and less framework machinery than Next.js. It ranks second because Spectral Nature benefits from a conventional server-side BFF for auth cookies, proxying, public Home rendering, and gradual route migration. React Router can provide those capabilities, but more of the integration and deployment shape would be assembled locally. If Next.js cache/server conventions prove to be an operational mismatch during the proof, React Router framework mode is the fallback—not another Python UI framework.

### SvelteKit — excellent UI, weaker strategic fit

SvelteKit would produce a responsive, high-quality product with less component boilerplate. It loses to React here because the React ecosystem is materially stronger for institutional data grids, Plotly integration, graph tools, accessible component systems, and future contributors. Choosing it would add the same API and language boundary as React while accepting a smaller ecosystem.

### NiceGUI — a better Python UI, but the wrong migration

NiceGUI improves on Streamlit's rerun model: it is event-driven, maps Python elements to Vue components, uses FastAPI, and synchronizes over Socket.IO. The official [technology overview](https://nicegui.io/documentation/section_foundations) also documents a single-Uvicorn-worker architecture and server-side UI event ownership.

That would make local UI composition easier, but it would keep browser interaction and application state centered on a Python UI server. Spectral Nature would pay most of a rewrite while preserving the architectural pressure that now needs to be removed. It is appropriate for Python-first internal tools, not the preferred destination for this product.

### Reflex — more indirection than value here

Reflex compiles Python components to a React/Next.js frontend while keeping state and logic in a Python backend connected by WebSockets. Its official [architecture description](https://reflex.dev/docs/advanced-onboarding/how-reflex-works) makes that boundary explicit.

Spectral Nature already has a FastAPI application and a deliberate Python service spine. Adding a generated React frontend and a second Python state runtime would obscure rather than strengthen the contract. Direct React/TypeScript gives the product the same browser ecosystem without putting an additional compiler/state abstraction between the UI and the existing API.

### Dash, Panel, and similar Python dashboard frameworks — lateral moves

These can improve particular charting and callback workflows but do not materially change the product boundary. They remain good tools for notebooks, internal experiments, and specialist dashboards. Rebuilding the full workspace in one of them would be a costly lateral migration.

## Target Architecture

```text
Browser
  |
  v
Next.js product client
  - routes, layout, responsive UI
  - client interaction state
  - thin same-origin BFF
  - no market/research/business rules
  |
  v
FastAPI application contract
  - auth authority and authorization
  - typed page/query/action DTOs
  - research run start/status/events/result
  - source and upload contracts
  |
  v
QueryService + AQL/Zopedia + materialized data spine
  - unchanged product ownership
```

The BFF is a security and transport adapter, not a second backend. It may:

- store access/refresh material in secure, HTTP-only, same-site cookies;
- proxy requests to FastAPI under the user's identity;
- normalize browser transport errors;
- stream or relay durable research progress;
- expose only the DTO required by a route.

It must not:

- query product databases directly;
- import or reproduce market, portfolio, AQL, Zopedia, ranking, or freshness rules;
- generate investor-facing narrative;
- create a second cache of research knowledge;
- silently synthesize missing API fields in TypeScript.

## Why The Existing FastAPI App Is Necessary But Not Yet Sufficient

The API already provides a meaningful migration seam:

- 20 routes;
- password login, refresh, logout, and current-user identity;
- scoped query, dataset, and chart endpoints;
- 44 registered dataset contracts and 4 registered chart contracts;
- Zopedia resolve and suggestions;
- agent tool and research export endpoints.

The Streamlit UI still imports many services directly, so a new client would not yet have behavioral parity. Missing browser-facing contracts include:

- account creation, password reset, invite activation, and related public auth flows;
- durable Zopedia run start, progress/events, resume, result, and cancellation;
- Zopedia thread, memory page, proposal, source inspection, and upload operations;
- first-class Home and page view models rather than client assembly across many datasets;
- portfolio and option actions with explicit authorization and audit results;
- admin health, job, access, and mutation contracts;
- knowledge-graph exploration and proposal review contracts;
- file/media delivery contracts for audio, exports, and retained sources.

Adding React before these exist would force domain assembly into TypeScript and violate the project's one-spine philosophy.

## Readiness Gates

Begin the frontend proof only when all of the following are true:

1. Home and Zopedia no longer depend on private `app.py` state or renderer helpers.
2. UI/read paths cannot start hidden AQL, search, embedding, or model work.
3. FastAPI exposes typed, versioned contracts for the proof route, including unavailable and freshness state.
4. Browser auth works through a secure same-origin session/BFF contract; refresh tokens are not kept in browser-readable storage.
5. Long research work has durable run identity and observable progress outside the Streamlit process.
6. OpenAPI is used to generate or validate TypeScript request/response types so Python and frontend contracts cannot drift silently.
7. The proof can deploy to development without sharing mutable development pointers with production.

These gates are stricter than “the API endpoint exists.” They require the real user path to be independent of Streamlit.

## Migration Strategy

Use a strangler migration. Do not translate `app.py` line by line and do not run a big-bang replacement.

### Phase 0 — make the Python side replaceable

- Complete the cleanup plan's read-path, page extraction, and cycle-breaking work.
- Define page DTOs from existing shared contracts.
- Add the missing browser auth and durable research-run APIs.
- Generate a TypeScript client from FastAPI OpenAPI.

### Phase 1 — prove the shell and read path

- Create the Next.js app as a separate development deployment.
- Build the product shell, responsive navigation, public/auth screens, and Home read path.
- Preserve current product language and visual identity; do not reproduce Streamlit's DOM or CSS overrides.
- Render existing Plotly JSON rather than rewriting analytical chart builders.

This phase proves deployment, auth, responsive layout, artifact freshness, and same-origin API transport. It does not prove the whole framework decision.

### Phase 2 — prove Zopedia end to end

Zopedia is the decisive vertical slice because it exercises the interaction model Streamlit handles least naturally:

- durable threads and deep links;
- answer-first rendering;
- live research journal from real backend stages;
- source upload and inspection;
- citations and context drawer;
- reload/resume by research run id;
- desktop and mobile composition without separate business paths.

Do not declare the frontend choice proven until this path runs through generation, durable progress, persistence, reload, and browser rendering.

### Phase 3 — migrate coherent product areas

Recommended order:

1. Market Explorer and Stock Investigator;
2. Broad Economy;
3. Portfolio and Portfolio Performance;
4. Option Strategizer and Trading Agent;
5. Admin and experiment-only tooling.

Each route moves only after its API/view-model contract exists and its Streamlit feature inventory has been audited. Streamlit stays available for the remaining routes during the transition.

### Phase 4 — cut over and retire

- Put Next.js on the primary development hostname and run the full browser matrix.
- Cut production over only with explicit permission and a reversible routing plan.
- Keep Streamlit available for rollback until product-critical route parity is verified.
- Retire Streamlit UI code rather than maintaining two permanent product clients.

## Frontend Stack Boundaries

Recommended starting set:

- Next.js App Router, React, and TypeScript;
- a small accessible component system owned in the repo;
- [TanStack Query](https://tanstack.com/query/latest) only for client-side server state that genuinely needs refetch, mutation, or polling; its official documentation describes caching, request deduplication, retries, and background refetching;
- `react-plotly.js` for current Plotly contracts, loaded per route to control bundle cost;
- Playwright for desktop/mobile/auth/reload browser acceptance;
- generated OpenAPI types/client for the FastAPI boundary.

Avoid at the start:

- GraphQL: the current QueryService and OpenAPI surface are enough;
- Redux or a global client store: URL state, route state, component state, and server-state queries should cover the initial product;
- a frontend-owned database or ORM;
- a second authentication provider or user store;
- recreating all Python chart transformations in TypeScript;
- Next.js server actions that bypass FastAPI for product behavior.

## Risks And Controls

### Domain logic leaks into TypeScript

Control: FastAPI returns route-ready DTOs. TypeScript may sort or filter already-present display data, but it does not infer causes, eligibility, freshness, access, or business meaning.

### Two caches create stale product state

Control: default current-feed and user-specific requests to no-store/request scope. Client query keys include dataset version, run id, or as-of identity. The frontend never invents a longer freshness policy than the artifact contract.

### Auth is accidentally reimplemented

Control: FastAPI remains the identity and authorization authority. The Next.js BFF only protects and forwards the existing session/token contract through HTTP-only cookies.

### Long research becomes an ephemeral socket demo

Control: progress comes from durable research events keyed by run id. SSE or bounded polling is transport; the event log is product state. Reloading the browser resumes the same run.

### Plotly makes every route heavy

Control: dynamic-import Plotly only on chart routes, use smaller bundles where feasible, and retain server-built chart JSON until a specific chart needs a native React rewrite.

### The migration becomes a permanent dual UI

Control: every migrated route has a retirement entry for its Streamlit renderer. The end state is one product client, not indefinite parity maintenance.

## Proof Acceptance

The target is proven only if the development vertical slice demonstrates:

- a normal URL can deep-link to the selected route/entity and survives refresh;
- desktop and phone layouts use one responsive component tree and have no horizontal document overflow;
- navigation and local interactions update only the affected UI rather than restarting page assembly;
- authentication, logout, reset, invite, and expired-session paths are browser-verified;
- current materialized content renders before optional enrichment and never starts hidden research;
- a Zopedia run exposes real progress, can be resumed after reload, and ends answer-first;
- FastAPI authorization protects every proxied operation independently of frontend visibility;
- no product-domain rule or narrative synthesis was added to the Next.js application;
- development deployment is isolated and production remains unchanged.

## Final Verdict

**Next.js is the better UI framework for the product Spectral Nature has become. Streamlit is still the better framework for the product's current degree of backend coupling.**

The correct move is therefore to commit to Next.js as the target, finish the replaceability boundary, and validate it with Home plus a full Zopedia vertical slice. A Python-to-Python UI rewrite would reduce some friction but would not solve the fundamental interaction and ownership problem.
