# Company Business Memory Probe

Run ID: `company-business-probe-20260525-main-after-crawler`
Started UTC: `2026-05-25T08:52:51.843020+00:00`
Tickers: `MAIN`
Facets: `business_model, fundamentals`

## Summary

| Ticker | Facet | Status | Confidence | Tools | Seconds |
| --- | --- | --- | --- | --- | ---: |
| `MAIN` | `business_model` | **completed** | low | investigator.company_context, dataset.ticker_business_model_stack, research.retained_context, research.live_event_evidence | 86.211 |
| `MAIN` | `fundamentals` | **completed** | low | research.retained_context, investigator.fundamentals, zopedia.search_pages, dataset.ticker_business_model_stack | 90.606 |

## Answers

### MAIN

#### business_model

**Main Street Capital (MAIN) is a business development company (BDC) that lends to middle-market companies and earns interest income, fee income, and potential capital gains—but the available evidence lacks specific financial details.** ### VERDICT MAIN operates as a BDC, providing debt and equity financing to lower-middle-market businesses. Its revenue model relies on interest from loans, dividend income from equity investments, and fee income from origination, management, and advisory services. However, no concrete numbers or breakdowns are present in the retrieved data. ### FACTS - **MAIN** is publicly traded on the NYSE as a BDC (common stock). - Multiple articles describe it as a “blue chip BDC” and a “monthly dividend stock.” - One source notes it trades at **1.8x book value**, implying a premium over net asset value. - BDCs typically generate returns through the spread between borrowing costs and lending rates, plus fee income. ### GAPS - No evidence of exact fee structure, spread income, or carried-interest mechanics. - No recent financial statements or fund-level data were retrieved. - Tool calls timed out or returned empty datasets for business model details. ### SOURCE REFS - Retained Zopedia context matched one item (title/content not shown). - Live evidence includes a Seeking Alpha article: “What Makes Main Street Capital's Business Model Unique” (Kavout) and a 24/7 Wall St. piece on its dividend. - Key gaps remain: the underlying business-model stack and company-context tools returned no data.

Limitations:

[
  "No financial fundamentals or operating model specifics retrieved.",
  "Company context and business model stack queries returned empty.",
  "Live event evidence only includes article metadata; no data extraction from articles.",
  "Missing fee structure, spread income, and carried-interest details.",
  "No recent financial statements or fund-level data.",
  "Business-model stack and company-context tools returned no data."
]

#### fundamentals

## VERDICT Insufficient data for a full assessment. Only 2025Q1 fundamentals are available, showing strong revenue and net income but negative operating cash flow. Key metrics like dividend coverage, credit quality, NAV, AUM, and historical trends are missing. ## FACTS - Revenue: $137.0M - Operating Income: $82.0M - Net Income: $116.1M - Operating Margin: 59.8% - Operating Cash Flow: -$20.4M - Total Assets: $5.27B - Total Liabilities: $1.59B - Liabilities/Assets ratio: ~30% ## GAPS - No data on credit quality, NAV, AUM, dividend coverage, capital expenditure burden, or historical trends. - Only one quarter of data; no context for trend. ## SOURCE REFS - investigator.fundamentals for MAIN 2025Q1 - retained_context confirmed ticker MAIN - Zopedia page on inflation context (not directly related) - ticker_business_model_stack returned no data

Limitations:

[
  "Missing credit quality, NAV, AUM, dividend coverage, capex, and historical trends",
  "Operating cash flow negative without explanation",
  "Only one quarter of fundamentals available",
  "No credit quality, NAV, AUM, dividend coverage, or capex data",
  "'low use' is not a standard metric and not supported by evidence",
  "Missing credit quality metrics",
  "Missing NAV and AUM",
  "Missing dividend coverage ratio",
  "Missing historical trends for comparison",
  "Missing capital expenditure information"
]
