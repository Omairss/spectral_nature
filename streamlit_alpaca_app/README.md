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

## Notes

- Uses Alpaca paper endpoint by default.
- If your market-data plan has limitations, some symbols may return no bars/snapshots.
- Fundamentals load from the quarterly CSVs under `Users/omai.r/data/stock_fundamental` or `SIMFIN_DATA_DIR` if set.
- Benchmarks used: `SPY, DIA, QQQ, VOO, BRK.B, ARKK`.
- Live data loaders now persist CSV caches under `streamlit_alpaca_app/cache/data/`.
- Cache staleness is controlled by `streamlit_alpaca_app/cache/cache_policy.json`.
- Use the sidebar `Update Cached Data` button to force a refresh before the configured stale window.
