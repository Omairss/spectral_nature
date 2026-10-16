# Product clients

The deployed web frontend is Next.js + React + TypeScript in `web/`. The SwiftUI client is in `ios_app/SpectralNatureMVP/`. Both use shared FastAPI, account, research and artifact contracts. Streamlit and Dash are retired; they are not fallback product surfaces.

The frontend owns navigation, controls and rendering. The shared backend owns research planning, acquisition, synthesis/review, account authorization and persistence. Home reads materialized outputs; opening a page or detail must not start model research.

- `web/app/` and `web/components/`: web pages and renderer components.
- `web/app/api/` and `web/lib/server/`: same-origin browser/native gateway, secure cookies and backend transport.
- `api/main.py`: typed service boundary.
- `services/*_view.py`: shared product DTOs.
- `services/agents/research_runs.py`: durable owned research/conversation execution.
- `presentation/plotly.py` and `visualization_artifacts.py`: shared canonical chart projection.
- [Visualization architecture](ADVANCED_VISUALIZATION_ARCHITECTURE_2026-10-02.md): renderer contracts and browser execution boundaries.
- [Native workbench](../../plans/NATIVE_IPHONE_WORKBENCH_2026-10-06.md): native client scope and acceptance.

Completed frontend selection, Streamlit parity and first-cutover plans are archived in the [retirement inventory](../../verification/legacy-retirement-2026-10-16/README.md). They do not authorize restoring a retired route or deploying a historical frontend.
