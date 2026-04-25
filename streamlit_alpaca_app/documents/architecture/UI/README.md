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

- `app.py`
- `presentation/`
- UI-facing sections of `data_access/query_service.py`
- UI-facing docs and operations pages

## Target Contract

UI calls public service APIs and renders their returned payloads. Business rules should live in AQL, Attention, Market Data, Agents, SAA, Data Access, or pipelines.

## Boundary Rules

- UI may call public service APIs and presentation helpers.
- UI may render chart models and tabular payloads.
- UI must not write retained evidence directly.
- UI must not duplicate anomaly, market event, or query planning logic.
- UI should not import private helpers from service modules.

## Migration Steps

1. Keep page routing in `app.py`, but move repeated page bodies into presentation modules when safe.
2. Replace private service imports with public namespace imports.
3. Move chart-only transforms into presentation helpers.
4. Add render tests for each migrated page.
5. Keep API and agent consumers independent from Streamlit.
