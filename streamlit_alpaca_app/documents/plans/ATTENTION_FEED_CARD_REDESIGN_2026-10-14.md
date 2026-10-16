# Attention feed card correction

The owner wants the fundamentals charts retained, with each value and its history
displayed together once. The first correction removed those charts; that was an
overcorrection. This revision restores them in a compact two-column grid while
preserving the combined price chart and removing the repeated financial-value grid.
Market acquisition, research and data contracts are unchanged.

## Design

- One price chart combines the preceding week's daily closes with an expanded
  latest intraday session. Both sections share the same price axis and show their
  actual dates. Separate paths preserve missing-session gaps.
- No period selector or repeated default quote caption. Market cap and monthly
  return appear once above the financial grid. Revenue, net income, cash flow,
  assets and equity each have one caption/value and one compact historical chart.
  The separate grid of those same financial values is removed.
- Preserve available histories when the latest scalar is absent and scalar values
  when history is absent. Do not invent charts or fill absent fundamentals with zero.
- The instrument heading links to existing Investigator price and financial
  statement analyses. Chart inspection stays on the feed and supports keyboard,
  mouse and touch; unavailable histories are not invented.
- If only daily or intraday observations exist, use the available series. An old
  snapshot with neither series has no fabricated chart.

## Fundamentals restoration — local verification

Personally reviewed complete desktop and 320-pixel phone cards using retained UTHR,
MU and HUM data, missing financial history for SKHY/WFF, and a scalar-only projection
of retained UTHR values. All five available financial charts remain on each complete
card. Each financial caption/value appears once, with no selector or horizontal
overflow. UTHR is 599 pixels tall at a 291-pixel card width. Web types and targeted
lint pass. Dev deployment and live verification of the restoration are complete.

Development revision `sn-next-ui-dev--0000020` is healthy, provisioned and serving
100% of traffic. Build `cj148` published
`attention-fundamentals-dev@sha256:8380f82bf07cc249ee8a6761569c0a344fc30aed162d64ab3c45618292fd2c1d`.
The live feed has 21 ticker cards, 19 combined price charts and 65 fundamentals
charts across the 13 cards with financial history. Each metric label/value appears
once per card; there are no period selectors. Personally reviewed the full deployed
UTHR card at a 390-pixel phone viewport. Public Home still has eight stories, five
key datapoints and available audio. Protected configuration and the newer
conversation frontend are preserved; both production frontend images are unchanged.
[Restoration release receipt](../../tmp/attention-fundamentals-20261014/release.json).

The isolated build starts from the newer conversation frontend, development revision
19. Its source hashes are verified and its conversation changes preserved. The only
new edits are the shared ticker card, the optional displayed value in TrendChart,
and card grid styling.

## Authorized production promotion — October 14

The owner requested “Push to prod” after the restored cards were verified in dev.
Promote the accepted Attention-card changes to both production frontends. The dev
image also includes a separate conversation frontend requiring the newer dev API;
this card release preserves the existing production Zopedia UI and backend.

The production-compatible build starts from the verified source of both production
frontends' current `ios-analytics-web-dev@sha256:fa9cee18da4a0d9349457bd068d14785a22991112a996f30a3f17936c14c0572` image.
Exactly five files change: the ticker card, combined price chart, shared TrendChart,
Home snapshot key and card CSS. Card/chart code is identical to the accepted dev
release; CSS contains only the accepted card rules. All other production source
remains byte-identical. Local desktop and phone review uses retained production
MU, DELL, AAPL and DRAM data. Production release and live checks are complete.

Build `cj149` published
`attention-fundamentals-prod@sha256:46dc7015427b659a197c308fb1404cb0f95f05318c7f590f46f67e0653b67467`.
Custom-domain frontend `sn-streamlit-ui--0000091` and alias frontend
`sn-next-ui--0000007` are healthy, provisioned and each serving 100% traffic.
Container settings, identities, application configuration and production API are
preserved. The live feed has 20 ticker cards, 20 price charts and 70 fundamentals
charts across 14 cards with financial history, with no period selectors or repeated
metric labels within a card. Complete production cards were personally reviewed on
desktop and phone. All four custom domains and the Azure alias return health/Home
HTTP 200. The same production Home run and available audio are preserved; its key
datapoint count was already zero before this frontend deployment and remains zero.

[Production source and release evidence](../../tmp/attention-fundamentals-prod-20261014/).

## Previous price-only release — rejected feature removal

Personally reviewed desktop and 390-pixel phone previews using retained development
UTHR and MU observations, intraday-only SKHY, a daily-only projection of retained
observations, and an actual October 7 replay without numeric history. Weekly and
latest-day keyboard inspection preserve exact prices and timestamps. Targeted lint
and web type checks pass. Development deployment and live verification are complete.

Development web revision `sn-next-ui-dev--0000018` is ready with 100% traffic.
ACR build `cj13u` published
`attention-card-dev@sha256:3a0084afe56d38871343f308e6450652673ecf99a9eaa05a36a42580c0d0fc58`.
All application configuration, container settings and identity were preserved.
Both production frontend images remain unchanged by this correction.

The actual browser feed contains 21 ticker cards: 19 with exactly one price chart,
two without price observations, zero selectors, zero financial chart stacks and no
repeated financial field labels. Personally reviewed the deployed UTHR phone card.
The existing UTHR Investigator renders Price channel, Pullback, Income, Balance and
Cash flow analyses without errors. Public Home retains eight stories, five key
datapoints and its available audio. [Release receipt](../../tmp/attention-chart-20261014/release.json).

That previous build starts from the source snapshot of development web revision 17, verified
against its accepted source hashes, and includes only the shared card, price chart,
card CSS and snapshot identity changes. That price-only revision was not promoted
to production. [Source and release evidence](../../tmp/attention-chart-20261014/).
