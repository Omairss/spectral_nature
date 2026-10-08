# UI Architecture

The UI layer owns rendering and user flow. It should not own business logic.

## Ownership

This module owns:

- Streamlit page routing
- controls, filters, and layout
- chart placement
- admin screens
- presentation helpers
- browser-facing user flows

## Current Code

- `web/` — deployed Next.js App Router development client and same-origin BFF
- `ios_app/SpectralNatureMVP/` — native SwiftUI iPhone workbench using the same development BFF and FastAPI contracts; [implementation and acceptance](../../plans/NATIVE_IPHONE_WORKBENCH_2026-10-06.md)
- `api/main.py` — typed browser-facing API boundary
- `services/home_view.py` — safe materialized Home view model
- `services/agents/research_runs.py` — durable user-owned research-run contract
- `app.py`
- `presentation/`
- UI-facing sections of `data_access/query_service.py`
- UI-facing docs and operations pages

## Active Frontend Decision

- [Frontend Framework Evaluation — 2026-09-20](./FRONTEND_FRAMEWORK_EVALUATION_2026-09-20.md): Next.js + React + TypeScript is the target product frontend.
- [Advanced Visualization Architecture — 2026-10-02](./ADVANCED_VISUALIZATION_ARCHITECTURE_2026-10-02.md): renderer-neutral artifacts, the focused renderer portfolio, agent-to-sandbox flow, linked interaction, performance, and browser security boundaries.
- [Next.js + Agent Harness Vertical Slice — 2026-10-02](../../plans/NEXT_AGENT_HARNESS_VERTICAL_SLICE_2026-10-02.md): the typed FastAPI, durable Zopedia run, secure BFF, and real Home proof are implemented and deployed in a separate development stack.
- Streamlit remains active. Route migration is incremental, and an existing route is retired only after its Next.js replacement is proven with real product data and user workflows.
- The requested native iPhone surface shares service, identity, and artifact contracts with the web workbench. Navigation and charts use SwiftUI and Swift Charts; SceneKit renders numeric 3D points and surfaces. Shared native renderers preserve units, selections, missing values, and full-universe scrolling. Account activation/recovery use the existing BFF identity routes. There is no embedded website or independent research engine.

## Target Contract

UI calls public service APIs and renders their returned payloads. Business rules should live in AQL, Attention, Market Data, Agents, SAA, Data Access, or pipelines.

## Boundary Rules

- UI may call public service APIs and presentation helpers.
- UI may render chart models and tabular payloads.
- UI must not write retained evidence directly.
- UI must not duplicate anomaly, market event, or query planning logic.
- UI should not import private helpers from service modules.

## Migration Steps

1. Keep the new Next.js client thin: render typed DTOs and issue commands through the same-origin BFF.
2. Move one product route at a time behind typed FastAPI contracts; Home and durable Zopedia research are the reference slice.
3. Keep Streamlit routes live until the corresponding desktop, mobile, auth, refresh, and failure paths are proven in Next.js.
4. Replace private service imports with public namespace or API contracts.
5. Keep business logic, research planning, storage, and authorization out of both frontend implementations.
