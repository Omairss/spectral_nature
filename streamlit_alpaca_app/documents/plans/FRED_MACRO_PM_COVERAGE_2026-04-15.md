# FRED Macro PM Coverage Expansion

## Goal

Expand the curated FRED dashboard from a basic macro monitor into a more useful macro PM panel by adding market-pricing and transmission series that help detect regime shifts earlier.

## Why

- The first curated set covered inflation, labor, housing activity, credit stress, and money supply reasonably well.
- It was still light on what a macro hedge-fund workflow cares about most:
  - curve shape
  - real rates
  - breakeven inflation
  - dollar conditions
  - bank-credit transmission
  - consumer demand and saving buffer
  - housing prices, not just housing activity
- That meant the dashboard could describe the economy, but it was weaker at spotting turning points already being priced by markets.

## Added Series

### Growth

- `PCEC96`: real personal consumption
- `PSAVERT`: personal saving rate

### Housing

- `CSUSHPINSA`: Case-Shiller national home-price index

### Policy & Liquidity

- `DGS2`: 2-year Treasury yield
- `DGS10`: 10-year Treasury yield
- `T10Y2Y`: 10Y-2Y curve slope
- `DFII10`: 10-year real yield
- `T5YIE`: 5-year breakeven inflation
- `T10YIE`: 10-year breakeven inflation
- `DTWEXBGS`: broad trade-weighted dollar
- `BUSLOANS`: commercial bank loans

## Design

- Keep the same shared FRED payload contract.
- Reuse the existing curated-series model so both:
  - the v2 bulk path
  - the v1 fallback path
  automatically pick up the expanded set.
- Avoid adding a large second-tier watchlist until the first-tier macro PM set proves useful in the UI.

## Validation

- Extend curated-series coverage tests so the new macro PM series are part of the expected set.
- Run the local loader with the live v1-capable key to confirm the larger set still builds successfully.
- Deploy dev UI and pipeline jobs so:
  - the job materializes the added series
  - the UI renders the new categories and entries

## Expected Outcome

- `fred_summary` becomes more useful for macro regime work, not just macro description.
- The refreshed panel should now show:
  - labor and inflation
  - spending and savings
  - housing activity and housing prices
  - credit stress and bank-credit flow
  - front-end rates, long rates, curve slope, real yields, breakevens, and the dollar
