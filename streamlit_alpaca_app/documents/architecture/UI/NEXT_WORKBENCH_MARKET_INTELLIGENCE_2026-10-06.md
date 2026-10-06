# Next Workbench Market Intelligence

Date: 2026-10-06

Status: accepted locally and deployed to the isolated Next.js development stack.

## Outcome

The Daily Brief and Broad Economy surfaces now behave as research workspaces rather than gateways to separate detail pages.

- Attention events carry a compact, inline snapshot for as many as four public symbols: latest price, one-day and one-month change, sparkline, market capitalization, quarterly revenue and growth, net income, and free cash flow.
- Broad Economy renders every indicator in the selected category at once, overlays an observation-to-observation change series, and adds a category correlation heatmap.
- The existing detail routes remain available through the ticker cards, but they are optional drill-downs rather than prerequisites for seeing the market state.

## Data contracts

### Attention ticker snapshots

`attention-home-build` remains the owner of the complete Home read model. It now joins:

- `price_history` for latest price, 1-day and 20-session returns, and sparklines;
- `universe_snapshot` and share-count data for names and market capitalization;
- `quarterly_fundamentals` for revenue, revenue growth, operating income, net income, cash flow, and reporting-period identity.

The resulting public-symbol subset is embedded in `attention_home_snapshots_1d`. This keeps the Home request to one bounded materialized read. `attention_ticker_snapshots_1d` remains separately addressable for other surfaces and for compatibility with older Home snapshots.

Ticker market data also has an independent `attention-ticker-snapshot-build` job. It reads the published Home symbol set and materializes `attention_ticker_snapshots_1d` directly from price, universe, and quarterly-fundamental inputs. This prevents deterministic price and fundamental freshness from depending on the much longer agentic narrative and qualitative-review lifecycle. The complete Home build still embeds the same contract when it succeeds; the API bulk-loads the separate snapshot for older Home artifacts.

The qualitative public-surface review retries bounded transient provider failures such as prematurely terminated streamed responses. Semantic rejection remains fail-closed and is never converted into a retry or automatic publish.

The public DTO validates numeric values, bounds text, and only accepts the generated SVG data-URI form used by the snapshot builder. Raw filings, provider responses, prompts, model traces, and credentials do not cross the browser boundary.

### Macro derived views

FRED observations are enriched once at the compute/service boundary:

- rate, ratio, spread, percent, and non-positive series use an observation-to-observation delta;
- positive level series use observation-to-observation percent change;
- the same transformation is applied after monthly-last normalization for correlation analysis;
- correlations use pairwise complete observations, no forward fill, and require at least eight aligned months;
- matrices are restricted to indicators within the selected macro category.

The API returns the normalized observation table, summaries, correlations, category descriptions, and series index. Legacy per-series frames and duplicate metadata are removed from the workbench response.

## Rendering and performance

Home places Attention before the network graph. The Plotly renderer is code-split and mounts only when a visualization approaches the viewport, so the first screen does not pay the graph-rendering cost. Current Home data uses a short server revalidation window and historical replay uses a longer immutable-style window.

Broad Economy groups observations once by series and reuses those arrays across the chart grid. Mini charts omit Plotly mode bars, use dual axes for level and change, and retain hover details. The heatmap renders numeric values for smaller matrices and relies on hover for dense categories.

The deployed web and API applications should keep one warm development replica. The previous zero-minimum configuration caused a platform cold start before any browser optimization could matter.

## Local acceptance

The accepted local review used the latest available cached market, fundamentals, and FRED artifacts rather than synthetic UI fixtures.

- Desktop Home showed four ticker cards per attention event at the common workbench width.
- Mobile Home showed one readable card per row without clipping.
- Desktop Broad Economy showed three simultaneous indicator charts per row, visible change overlays, and a labeled correlation matrix.
- Mobile Broad Economy collapsed charts to one column and kept wide metrics, category tabs, heatmaps, and data tables inside deliberate horizontal scroll regions.
- The browser console contained no warnings or errors.
- ESLint, TypeScript, Python compilation, whitespace validation, the 28-route Next.js production build, and the runtime dependency audit completed successfully. The runtime audit reported zero vulnerabilities.

## Release boundary

This work targets only the isolated Next.js development stack:

- `sn-next-ui-dev`
- `sn-api-next-dev`
- a development-namespaced materialization path when a fresh Home artifact is required

It does not replace or update `sn-streamlit-ui-dev`, any production frontend, or any production dataset pointer.

## Development acceptance

The accepted release is live at
`https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`.

- Web revision: `sn-next-ui-dev--0000004`, image
  `next-ui-dev@sha256:3d21a9163e4979eb1b5d4bda9ffeedcd87d781df185bd086baad9321ab0dc7c1`.
- API revision: `sn-api-next-dev--0000007`, image
  `api-next-dev@sha256:ca9718c8f0b0b1448b481bbbd2017473bdde24ccf36f00d61fcaf051a585cdc2`.
- Both web and API keep one warm replica. The API remains internal-only.
- `dataset-refresh-dev-rvolsod` refreshed 22 development snapshot pointers without
  modifying production pointers.
- `attention-ticker-next-dev-gabf62n` published 29 current attention symbols, including
  18 with quarterly fundamentals, through the manual development-only job.
- The deployed Home showed inline values for price, daily and monthly change, market cap,
  revenue and growth, net income, and free cash flow. Broad Economy showed three charts
  per desktop row, visible change overlays, and the correlation heatmap. The browser
  console contained no warnings or errors.
- Warm authenticated requests completed without a platform cold start. The login and
  session checks returned in approximately 0.19 and 0.26 seconds respectively.
- Streamlit development remained on `sn-streamlit-ui-dev--0000456` and production was
  not targeted.
