# Spectral Nature

Spectral Nature is a market intelligence workspace for portfolio context, macro tracking, and idea discovery.

## Documentation home

`streamlit_alpaca_app/documents/` is the single tracked documentation home for this repo.

When a doc below mentions `documents/...`, treat that as a path relative to `streamlit_alpaca_app/`.

Keep docs here:

- `plans/`: active implementation notes, recovery plans, and short design docs
- `operations/`: setup, deploy, and operator runbooks
- `infra/`: tracked infra references and live-status trackers
- `reference/`: product and runtime reference docs
- `learnings.md`: reusable takeaways from completed work
- `mistakes.md`: repeated failure modes and "never repeat" checklists

## What was migrated

- `Current Portfolio` -> live account + open positions + portfolio history
- `Past Performance` -> Portfolio vs benchmark performance metrics (annual return, Sharpe, beta, alpha, max drawdown)
- `Market Opportunity` -> Market-wide mover and momentum scans (Markets/Broad Markets/Commodity lenses)
- `Stock Investigator` -> Single-ticker technicals + company context + fundamentals (consolidated stock drilldown)
- `Strategizer - Option` -> Option chain explorer/ranking (via live option contracts + snapshots)

## Setup

1. Create env and install dependencies:

```bash
cd Users/omai.r/spectral_nature/streamlit_alpaca_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure credentials:

```bash
cp .env.example .env
# set AZURE_KEY_VAULT_NAME / KEY_VAULT_NAME
# keep Alpaca keys in Key Vault under apca-api-key and apca-api-secret-key
# set APCA_API_BASE_URL to paper or live endpoint matching your key pair
```

3. Run locally:

```bash
./scripts/run_ui_local.sh
```

4. Optional: run the backend API for external clients (iOS, agents, integrations):

```bash
./scripts/run_api_local.sh
```

## Docker dev environment

Use Docker when the host Python environment is missing app/test dependencies or when you want a pinned local runtime that matches the repo setup more closely.
Local runtime scripts now auto-load, in this order:

- `infra/.generated/deployment.local.env`
- `infra/.generated/email_delivery.local.env`
- `.env`

Refresh the local generated files from live Azure when needed:

```bash
bash ./scripts/show_infra_inventory.sh --write-local
```

Build the image once:

```bash
./scripts/docker_dev.sh build
```

Open a shell inside the container:

```bash
./scripts/docker_dev.sh shell
```

Run tests in the container:

```bash
./scripts/docker_dev.sh test tests/test_api_v1.py tests/test_api_auth.py
```

Run the API or Streamlit UI with container ports exposed:

```bash
./scripts/docker_dev.sh api
./scripts/docker_dev.sh ui
```

## App sections

- Portfolio Overview
- Performance
- Market Opportunity
- Stock Investigator
- Option Strategizer

## Documentation map

- `documents/README.md`: doc hub and runtime overview
- `documents/learnings.md`: reusable takeaways across sessions
- `documents/mistakes.md`: repeated mistakes and guardrails
- `documents/operations/PROJECT_SETUP_AND_OPERATIONS.md`: contributor setup and deployment workflow
- `documents/infra/README.md`: Azure deployment entrypoints and outputs
- `documents/reference/ATTENTION_FEED_GUIDELINES.md`: current product rules for the attention feed
- `documents/plans/README.md`: index of active implementation and recovery plans
- `ios_app/SpectralNatureMVP/README.md`: native iPhone scaffold setup and generation steps

## Shared data access

The app now has a shared data access layer under `data_access/`:

- `data_access/layer.py` resolves data from the best available source:
  - precomputed pipeline snapshots first
  - on-demand cached/live computation second
- `compute/` now holds pure transformation logic that is shared by the DAL, query layer, and legacy service wrappers
- `data_access/query_service.py` exposes agent/query-friendly dataset and chart operations, including resolution hints such as `materialized_first` and `live_cached`
- `presentation/plotly.py` renders canonical chart models into Plotly figures when needed

Today the heavy shared analytics are snapshot-first, while portfolio/account-style views remain live-backed and lazy by request.

The important contract is:

`materialized data -> shared DAL -> compute -> chart/data model -> optional Plotly rendering`

That means Plotly JSON is treated as an output format, not the primary shared artifact.

## Agent / query access

Agents and other non-UI consumers should call the shared query entrypoint instead of importing `app.py`.

List supported dataset and chart operations:

```bash
python scripts/run_query.py --operation capabilities
```

Fetch a canonical dataset response:

```bash
python scripts/run_query.py \
  --operation dataset \
  --name price_history \
  --params-json '{"ticker":"AAPL","days":180}'
```

Fetch a canonical chart model:

```bash
python scripts/run_query.py \
  --operation chart \
  --name technical_price_channel \
  --params-json '{"ticker":"AAPL","days":180}'
```

Render the same chart model as Plotly JSON only at the edge:

```bash
python scripts/run_query.py \
  --operation chart \
  --name technical_price_channel \
  --params-json '{"ticker":"AAPL","days":180}' \
  --render plotly
```

Each response includes provenance so callers can tell whether the result came from:

- `materialized` pipeline artifacts
- `on_demand` local cache/live fetches
- `computed` derived transforms built from lower-level datasets

## HTTP API access

For non-Python clients, use the FastAPI layer:

- `GET /health`
- `GET /v1/auth/status`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `GET /v1/me`
- `GET /v1/auth/agent-keys` (admin)
- `POST /v1/auth/agent-keys` (admin)
- `POST /v1/auth/agent-keys/{key_id}/revoke` (admin)
- `POST /v1/omnibar/resolve`
- `GET /v1/omnibar/suggestions`
- `GET /v1/capabilities`
- `POST /v1/query`
- `POST /v1/dataset/{name}`
- `POST /v1/chart/{name}`
- `GET /v1/agent/tools`
- `POST /v1/agent/tools/{tool_name}/invoke`
- `POST /v1/agent/rpc` (MCP-compatible JSON-RPC)

Auth model:

- user login returns short-lived `access_token` + refresh `refresh_token`
- bearer uses the short-lived `access_token` by default; legacy session-token bearer support is opt-in only for controlled migration work
- machine agents can use scoped `X-API-Key` credentials from `agent_api_keys`

Run locally with:

```bash
./scripts/run_api_local.sh
```

## Azure UI deploy

Use the dedicated UI deploy script from repo root:

```bash
# Build and deploy current repo state to Development.
./scripts/deploy_ui_azure.sh

# Promote the currently running Development image to Production.
./scripts/deploy_ui_azure.sh --target prod --promote-from dev
```

The script updates the target Container App, waits for the latest revision, smoke-checks the UI endpoint, and refreshes `documents/infra/UI_DEPLOYMENT_STATUS.md`.

## Azure email delivery

Password resets and invite emails use the SMTP path in `services/emailer.py`.

For the Azure-hosted UI, provision and wire the mail resources with:

```bash
./scripts/setup_ui_email_delivery_azure.sh --target dev --test-to you@example.com
```

That workflow:

- creates or reuses Azure Communication Services Email + Communication resources
- creates an Azure-managed sender domain and sender username
- creates or reuses an Entra app for SMTP auth
- stores SMTP secrets in the UI Key Vault
- updates the selected Container App with the expected SMTP env vars

The setup script writes local-only outputs to `infra/.generated/email_delivery.local.env`.

## Notes

- Uses the paper trading endpoint by default.
- If your market-data plan has limitations, some symbols may return no bars/snapshots.
- Fundamentals load from the quarterly CSVs under `Users/omai.r/data/stock_fundamental` or `SIMFIN_DATA_DIR` if set.
- If `SIMFIN_API_KEY` is configured, the `equities-intraday-preload` job can refresh quarterly fundamentals from upstream SimFin before materializing `quarterly_fundamentals`.
- The `macro-fred-daily` job now also materializes official Treasury yield datasets: `yield_curve_observations`, `yield_curve_summary`, and `yield_curve_facts_1d`.
- The `attention-home-build` job materializes the homepage attention datasets and research bundles from upstream snapshots; the UI is intended to read those outputs rather than compute inline.
- The `entity-taxonomy-refresh` job materializes `us_equity_listings` and `entity_taxonomy_labels`, and it is scheduled monthly by default.
- A flow-chart version of the taxonomy setup lives in `documents/infra/TAXONOMY_PIPELINE_FLOW.md`.
- Active design plans live under `documents/plans/`.
- Treasury direct yields are used for official daily rate facts; FRED remains available for broader macro history and dashboard context.
- Benchmarks used: `SPY, DIA, QQQ, VOO, BRK.B, ARKK`.
- Live data loaders now persist CSV caches under `streamlit_alpaca_app/cache/data/`.
- Cache staleness is controlled by `streamlit_alpaca_app/cache/cache_policy.json`.
- Use the sidebar `Update Cached Data` button to force a refresh before the configured stale window.
