## Homepage v2 rail refactor

Date: 2026-03-31

### Problem

The previous Home experience mixed three detail surfaces at once:

- raw event and mover cards in the main flow
- inline research expansion inside those cards
- a separate full-width ticker background panel appended after the page

That made inspect actions feel unreliable because a single click could rerun the page, duplicate narrative text, and push the user into a long full-page detail block.

### Decision

Keep two distinct products:

- `Home`: narrative-first homepage with a single drilldown rail
- `Daily Market Overview`: raw materialized attention cards for deeper operational review

### Homepage state model

Homepage v2 now keeps one active drilldown surface at a time:

- `research`: retained evidence bundle for the selected narrative beat
- `company`: company background for the inspected ticker

Selection is normalized from a single state contract:

- `homepage_v2_selected_bundle_id`
- `homepage_v2_selected_ticker`
- `homepage_v2_active_panel`

If bundle state is stale, the UI falls back to the first valid beat. If company state is empty, the rail falls back to research instead of rendering a blank panel.

### Expected UX

- The left side stays compact and scannable.
- `Open research` always targets the right rail.
- ticker `Inspect` actions swap the right rail into company background instead of appending a new page section
- cross-page inspect links into `Home` open directly into the company rail
