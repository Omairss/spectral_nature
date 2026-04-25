# iPhone MVP Scaffold (2026-04-06)

## Objective

Create a working native iPhone client scaffold that talks to Spectral Nature data through a stable backend API seam, without duplicating business logic into mobile code.

Update (2026-04-07):

- The API/auth layer has been upgraded beyond MVP in `AGENTIC_API_AUTH_MCP_2026-04-07.md`.
- iOS scaffold now needs to treat access/refresh tokens as first-class and can also reuse agent-tool endpoints when needed.
- Shared REST resource shapes for agent sessions/runs/messages/artifacts/notes are now defined in `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md`.
- Spectral Nature 2 should use one omnibar entrypoint across homepage and iPhone, backed by shared intent resolution (`POST /v1/omnibar/resolve`).

## What changed

## Backend API seam

- Added `api/main.py` with a thin FastAPI layer over `QueryService`.
- Added endpoints:
  - `GET /health`
  - `GET /v1/auth/status`
  - `POST /v1/auth/login`
  - `POST /v1/auth/logout`
  - `GET /v1/me`
  - `GET /v1/capabilities`
  - `POST /v1/query`
  - `POST /v1/dataset/{name}`
  - `POST /v1/chart/{name}`
- Auth behavior:
  - If database auth is enabled, query endpoints require a bearer token.
  - If auth is disabled, query endpoints remain accessible for local/dev guest usage.

## Local API runtime

- Added `scripts/run_api_local.sh` to run `uvicorn api.main:app`.
- Added dependencies in `requirements.txt`:
  - `fastapi`
  - `uvicorn`

## iOS scaffold

- Added `ios_app/SpectralNatureMVP/` using XcodeGen (`project.yml`).
- Added SwiftUI app skeleton with:
  - session/auth state + Keychain token storage
  - API client for `/v1/*` endpoints
  - feature screens: Login, Home, Portfolio, Ticker
  - environment-based API base URL via `SNApiBaseURL`
- Added iOS setup/run instructions in `ios_app/SpectralNatureMVP/README.md`.

## Test coverage

- Added `tests/test_api_v1.py` for:
  - successful `/v1/query` response path
  - auth-required gating behavior
  - login response mapping

## Design choices

- Keep all analytics and data shaping in Python (`compute/`, `data_access/`, `services/`).
- Keep mobile app as client-only orchestration and rendering.
- Avoid hardcoded endpoint payload assumptions by preserving existing query contracts.

## Known limitations in this scaffold

- iOS UI currently displays raw JSON payloads for rapid integration validation.
- Dedicated mobile-specific resource endpoints are not split out yet; it still uses the generic query contract.
- Push notifications/background refresh workflows are not included in this first scaffold.

## Next implementation step

Implement typed mobile resource endpoints (`/v1/home/attention`, `/v1/tickers/{ticker}/snapshot`, `/v1/portfolio/summary`) and update SwiftUI views to render structured models instead of raw JSON text.

For agent/workspace features, build iOS against `AGENT_API_RESOURCE_CONTRACT_2026-04-07.md` rather than adding iPhone-only payload shapes.
