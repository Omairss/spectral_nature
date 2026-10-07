# Workbench research depth

Date: 2026-10-06

Status: local implementation reviewed; alternative-development release pending.

## Daily Brief

Replace coverage counters with five datapoints selected by AQL/Zopedia from the day's
market context and a bounded catalog of price, quarterly financial, and FRED time series.
The model chooses references and explains significance; values and chart coordinates
come from the referenced datasets. Persist the selection with its Home identity, input
dates, and gateway trace. Rendering never starts model work.

Attention ticker snapshots retain quarterly revenue, net income, and free cash flow
history. Compact charts expose the reporting periods and actual values, including
negative values and gaps. Latest values remain useful chart labels rather than replacing
the history.

## Analytical pages

Stock Investigator loads and displays price channel, pullback, income, balance sheet, and
cash flow together. Each chart retains its own data and provenance inspector and settles
independently. Portfolio opens with a five-year lookback.

Broad Economy keeps the raw change trace faint and adds a trailing exponential moving
average with a frequency-aware observation window. A shared overlay control allows raw,
smoothed, combined, or hidden change series. Smoothing must not use future observations.

## Zopedia

Search must query the full retained corpus before limiting results, visibly show results,
and preserve query state. The knowledge browser exposes actual wiki folders, internal
pages, page bodies, outgoing links, backlinks, and source references. Source documents
and synthesized wiki pages remain distinct navigable views. Read endpoints retain
authentication, public-page visibility rules, bounded payloads, and read-only behavior.

The product browser uses matching indexed search for both wiki pages and source pages,
not the research retriever's broad lexical fallback. Retained-document search filters
the corpus before applying its scan limit. Folder navigation shows immediate children
instead of flattening hundreds of generated communities into the root.

## Development data boundary

The development technical-history snapshot was still an unpartitioned September copy.
Its 1.45-million-row size correctly exceeded the bounded reader's full-scan limit, which
left price/pullback artifacts empty. Namespace refresh now preserves and relocates
existing partition blobs and offers `--partition-symbol-datasets` to migrate legacy
price/technical histories. Complete all partition uploads before publishing the new
destination manifest. Never relax the dashboard's global-scan limit.

## Local review

- Real development inputs: 71 ticker snapshots, 35 FRED series, 218 eligible charts.
- The first actual AQL selection repeated five movers and overclaimed relative ranking;
  rejected. Subsequent prompt review emphasizes complementary quantitative explanations,
  correct reporting dates, and a chart that directly illustrates its stated insight.
  Values are always supplied by the dataset, never by the model.
- Personally reviewed the final real-model selection against its source observations:
  Ciena revenue, Comfort Systems revenue, Seagate free cash flow, Twist free cash flow,
  and the ten-year Treasury yield. Rejected intermediate claims about financing
  dependence and consecutive quarterly growth; strengthened the shared selection
  instructions instead of editing the model's answers or forcing chart categories.
- Live materialization also exposed a wording mismatch between recent observations
  supplied to the model and longer histories displayed in the chart. Every candidate now
  supplies every plotted observation, plus explicit chart ranges/counts. Daily macro
  mini-charts use the latest twenty observations; the full macro history remains in
  Broad Economy. Price candidates retain their complete bounded histories and financial
  candidates retain twenty quarters. The model and renderer share one history contract.
- Successful Nvidia search: 45 internal pages plus matching retained sources. An unknown
  query now gives zero wiki and source results rather than unrelated fallback sources.
- Opened actual IOVA ticker/entity pages, followed outgoing links and backlinks, and
  verified the reader opens at the top of each destination.
- Reviewed raw-plus-EMA and smoothed-only macro charts. EMA uses only trailing data.
- Reviewed all five AAPL investigator panels without switching chart tabs; data and
  provenance remain independent per panel. Empty datasets get an honest empty state.
- Portfolio selector opens at 5Y. Brokerage content requires deployed owner-session QA;
  the local transport does not borrow private brokerage credentials.
- At 390 × 844, Home and the wiki reader have no horizontal overflow.
- Legacy partition migration preserves all 1,449,535 rows across 64 buckets; AAPL's
  bounded bucket contains 20,784 rows, including all 1,545 AAPL observations.

Local browser review uses an ephemeral loopback transport backed by real datasets and
shared application services. Its preview session is not application authentication and
is neither committed nor shipped. Live authentication must be checked separately.

## Acceptance and release

Inspect the actual model selection and rendered charts using development datasets.
Exercise successful and empty Zopedia searches, open an internal page, and traverse a
real wiki link. Review desktop and mobile layouts. Deploy to the separate Next.js
development stack after local acceptance, then verify the deployed routes and managed
materialization. Record exact revisions and remaining source coverage limits.
