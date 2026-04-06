# Stock Investigator Split - 2026-04-06

## Goal
- Keep `Market Opportunity` focused on market-wide discovery.
- Move single-ticker investigation into a dedicated workspace section.
- Keep ticker handoff simple and shared across sections.

## Scope
- UI routing and section layout in `app.py`.
- No changes to DAL/compute contracts.
- No schema or pipeline changes.

## Design decisions
1. `Market Opportunity` stays as the scanner:
- Keeps Markets/Broad Markets/Commodity views.
- Keeps momentum/mover tables and treemap.
- Removes inline ticker detail stack.

2. New `Stock Investigator` section:
- Dedicated ticker workspace with:
  - technical view + signal diagnostics
  - company/news context block
  - quarterly fundamentals block
- Reuses existing cached loaders and presentation functions.

3. Shared ticker state helper:
- Added `_set_workspace_ticker(...)` to sync:
  - `market_selected_ticker`
  - `opt_ticker`
  - `stock_investigator_ticker`
- This avoids hardcoded one-off state updates in multiple places.

## Implemented changes
- Added `Stock Investigator` to workspace options.
- Added new `elif section == STOCK_INVESTIGATOR_SECTION` renderer block.
- Added `_render_stock_investigator_workspace(...)`.
- Updated stock-focused handoffs to open `Stock Investigator`:
  - cross-page `inspect_view=market`
  - ticker snapshot open action
  - Home company background panel open action
- Kept attention drilldown default section as `Market Opportunity` for market anomaly workflows.
- Added Market Opportunity handoff CTA: `Open Stock Investigator`.
- Removed `Technical Strategizer` and `Fundamental Strategizer` from workspace navigation.
- Deleted the old `Technical Strategizer` and `Fundamental Strategizer` section render branches from `app.py`.

## Reliability and complexity notes
- This is a source-level split, not a duplicate implementation.
- Existing cached loaders are reused to reduce regression risk.
- No hardcoded ticker lists were added.

## Follow-up options
1. Add small UI test coverage for section routing and ticker handoff state.
