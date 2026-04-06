# Homepage Implied Open Experiment

## Goal

Make the homepage narrative thread feel less like a list of utility buttons and more like an editorial surface.

## Experiment

- remove the explicit `Open research` CTA from each narrative beat
- use the beat headline itself as the native Streamlit selection control
- keep the interaction in-app and session-state driven so the drilldown rail still updates without hyperlink behavior
- remove the separate homepage ticker `Inspect` button and use the ticker preview label itself as the native selection control
- reduce the shell chrome so the homepage starts closer to the actual narrative content
- simplify the sidebar brand block to `Torres Capital` and `Spectral Nature` only
- add a separate `Home Exp` workspace route so the existing `Home` flow remains unchanged while testing rail behavior
- remove the visible retained-research vs company-background rail toggle in the experimental route
- make ticker clicks carry bundle context so the active beat and the drilldown rail stay visually linked
- add a compact `X` close action in company background that returns the rail to retained research for the selected beat
- remove redundant experimental section labels and use a normal left-aligned retained-research title instead of a custom rail explainer block
- promote the experiment into `Home` once validated and remove `Home Exp` from the workspace picker so there is one canonical homepage path again
- bind ticker-highlight state to the selected beat as well as the selected symbol so clicking a stock does not light up unrelated repeated tickers elsewhere in the narrative thread
- keep the company-background rail header visually aligned with retained research so the interface does not feel like it changes modes after a ticker click
- remove callback-side `st.rerun()` calls from ticker selection handlers because Streamlit already reruns after `on_click`, and the extra call only emits a warning
- remove small state labels and tooltip copy that restate the obvious once headline and ticker surfaces are already clickable
- keep the rail title inside the same bordered shell as its content so retained research and company background share one visual structure
- shorten empty-state copy so the homepage reads like a product surface rather than a guided demo

## Guardrails

- keep the change scoped to the homepage narrative thread
- avoid raw HTML anchors or query-param navigation for this interaction
- preserve the existing retained research rail behavior and company background inspect path
- version the injected Streamlit shell CSS so existing sessions pick up homepage styling revisions after deploy
- keep `Home Exp` on the same data payload as `Home` so the experiment isolates interaction changes rather than content drift
