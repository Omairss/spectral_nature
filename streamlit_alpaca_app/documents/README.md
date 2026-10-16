# Spectral Nature

Spectral Nature is a market research workbench. The web frontend is **Next.js + React + TypeScript** in `web/`; `api/` is its shared FastAPI backend. The SwiftUI iPhone client uses the same account, research and artifact contracts. `streamlit_alpaca_app` is the historical directory name, not the frontend framework.

## Current documentation

- [Active architecture](architecture/ACTIVE_MAP.md): current owners and execution boundaries.
- [Setup and operations](operations/PROJECT_SETUP_AND_OPERATIONS.md): local Next.js/API setup.
- [Release policy](infra/RELEASE_SOURCE_POLICY.md): accepted Git source, immutable images and deployment authorization.
- [Infrastructure](infra/README.md) and [resource index](infra/RESOURCE_INDEX.md): current frontend/backend targets.
- [Accepted-behavior recovery](verification/behavior-recovery-2026-10-16/README.md): eight audited release regressions and remaining verification.
- [Retired paths](verification/legacy-retirement-2026-10-16/README.md): removed Streamlit/Dash code, obsolete tools, archival inventory and remaining cloud resources.
- [Research index](research/README.md): retained experiments, complete outputs and open research questions.
- [Plans](plans/README.md): current recovery, implementation and research work.
- [Philosophy](philosophy.md), [learnings](learnings.md), [mistakes](mistakes.md): operating rules.

Streamlit and Dash are retired. Their source, UI-only dependencies, tests and deployment instructions are archived outside the checkout for 30 days. Completed release evidence and retained research do not authorize a new release. No historical image or archived tree may supply application source.

## Local development

From `streamlit_alpaca_app/`, install `requirements-dev.txt`, configure `.env` from `.env.example`, then start the backend with `./scripts/run_api_local.sh`.

In a second terminal, from `web/`:

```bash
npm ci
SPECTRAL_API_BASE_URL=http://localhost:8080 APP_PUBLIC_ORIGINS=http://localhost:3000 APP_COOKIE_SECURE=false npm run dev
```

Open `http://localhost:3000`. Browser requests use the same-origin Next.js gateway; provider/database credentials stay in Python. See the operations guide for details.
