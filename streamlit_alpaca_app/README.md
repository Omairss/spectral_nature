# Spectral Nature (Alpaca + Streamlit)

This is a Streamlit rewrite of the Robinhood/Dash app using Alpaca as the brokerage/data backend.

## What was migrated

- `Current Portfolio` -> Alpaca account + open positions + portfolio history
- `Past Performance` -> Portfolio vs benchmark performance metrics (annual return, Sharpe, beta, alpha, max drawdown)
- `Market Opportunity` -> Daily mover scan from Alpaca snapshots + price chart
- `Strategizer - Technical` -> Candlestick + SMA(20/50), RSI(14), MACD
- `Strategizer - Option` -> Option chain explorer/ranking (via Alpaca option contracts + snapshots)
- `Strategizer - Fundamental` -> Quarterly income/balance/cashflow charts (via local SimFin quarterly dataset)

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
# set APCA_API_KEY and APCA_API_SECRET_KEY
# set APCA_API_BASE_URL to paper or live endpoint matching your key pair
```

3. Load env and run:

```bash
set -a
source .env
set +a
streamlit run app.py
```

## App sections

- Portfolio Overview
- Performance
- Market Opportunity
- Technical Strategizer
- Option Strategizer
- Fundamental Strategizer

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

Agents and other non-Streamlit consumers should call the shared query entrypoint instead of importing `app.py`.

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

## Azure UI deploy

Use the dedicated UI deploy script from repo root:

```bash
# Build and deploy current repo state to Development.
./scripts/deploy_ui_azure.sh

# Promote the currently running Development image to Production.
./scripts/deploy_ui_azure.sh --target prod --promote-from dev
```

The script updates the target Container App, waits for the latest revision, smoke-checks the Streamlit endpoint, and refreshes `infra/UI_DEPLOYMENT_STATUS.md`.

## Notes

- Uses Alpaca paper endpoint by default.
- If your market-data plan has limitations, some symbols may return no bars/snapshots.
- Fundamentals load from the quarterly CSVs under `Users/omai.r/data/stock_fundamental` or `SIMFIN_DATA_DIR` if set.
- Benchmarks used: `SPY, DIA, QQQ, VOO, BRK.B, ARKK`.
- Live data loaders now persist CSV caches under `streamlit_alpaca_app/cache/data/`.
- Cache staleness is controlled by `streamlit_alpaca_app/cache/cache_policy.json`.
- Use the sidebar `Update Cached Data` button to force a refresh before the configured stale window.
