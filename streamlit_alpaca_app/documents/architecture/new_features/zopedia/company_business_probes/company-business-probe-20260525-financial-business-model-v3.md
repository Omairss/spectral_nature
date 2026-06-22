# Company Business Memory Probe

Run ID: `company-business-probe-20260525-financial-business-model-v3`
Started UTC: `2026-05-25T07:42:15.026327+00:00`
Tickers: `BX, OBDC, MAIN`
Facets: `business_model`

## Summary

| Ticker | Facet | Status | Confidence | Tools | Seconds |
| --- | --- | --- | --- | --- | ---: |
| `BX` | `business_model` | **completed** | low | investigator.company_context, zopedia.search_pages, dataset.ticker_business_model_stack, research.retained_context, research.search_evidence, research.live_event_evidence | 104.553 |
| `OBDC` | `business_model` | **fail** | low | investigator.company_context, dataset.ticker_business_model_stack, research.search_evidence, research.live_event_evidence, research.open_page, research.open_page | 112.089 |
| `MAIN` | `business_model` | **completed** | low | zopedia.search_pages, dataset.ticker_business_model_stack, investigator.company_context, research.live_event_evidence, research.open_page | 101.294 |

## Answers

### BX

#### business_model

**No business-model evidence was found for Blackstone (BX) in any of the searched tools or retained context.** ### VERDICT Blackstone Inc. (BX) trades on the NYSE, but the collected evidence contains zero description of what it sells, who pays it, or its operating model. The local pipeline shows financial figures (revenue, operating income, free cash flow) but no business-model details. ### FACTS - **BX** is listed on the NYSE, common stock, not an ETF (source: pipeline compact_fundamentals listing). - Available financials (2025 quarters): Total Revenue ranging from **$3.09B** (Q3) to **$4.36B** (Q4); Operating Income from **$1.43B** to **$2.52B**; Net Income from **$625M** to **$1.02B** (pipeline compact_fundamentals). - No Zopedia page, retained context, or SAA evidence describes Blackstone's revenue sources (management fees, performance fees, carried interest), fund structures, or lending/investment model. ### GAPS - Business-model stack returned zero rows. - Live event evidence timed out; Tavily web research failed and returned zero relevant articles. - Retained context only contained the ticker/symbol, no narrative. - No data confirms the user's premise about fee income, carried interest, or fund economics. ### SOURCE REFS - Pipeline compact_fundamentals for BX (quarters 2025Q1–2025Q4). - Retained context item: "BX (Blackstone Inc. Common Stock)" (agtc_4). - All other tool calls returned empty or error results.

Limitations:

[
  "No business model evidence from Zopedia, retained context, or live research tools.",
  "Live web research timed out or failed.",
  "User premise about fee income/carried interest cannot be verified.",
  "No description of Blackstone's revenue sources, fund structures, or investment model.",
  "No data on management fees, performance fees, carried interest, or lending/spread income."
]

### OBDC

#### business_model

I collected tool output but did not get a clean final synthesis from the model.
Question: OBDC ticker-specific business-memory facet. What does Blue Owl Capital Corporation (OBDC) sell, who pays it, and what is the operating business model? For financial firms, explain the fund/lending/investment model, fee income, spread income, or carried-interest economics if relevant. Local pipeline compact facts JSON: {"ticker": "OBDC", "company_baseline": [], "listing": [{"symbol": "OBDC", "exchange": "NYSE", "security_name": "Blue Owl Capital Corporation Common Stock", "is_etf": false, "is_test_issue": false, "source_file": "otherlisted"}], "compact_fundamentals": [], "recent_news": []} The local JSON is typed pipeline state, not a user guess. Use it to identify the company and available local metrics. If Zopedia or retained evidence is empty, call live AQL research tools before concluding that no business evidence exists. Return concise output with exactly these headings: VERDICT, FACTS, GAPS, SOURCE REFS. Evidence labels are not analysis; say what the evidence means. No stock-chart or price-technical commentary.

Evidence collected:
- research.live_event_evidence: research summary with 5 rows; columns=scope, symbol, headline, summary_text, source, published_at, url; sample=[{"headline": "Blue Owl Capital BDCs Sell $1.4 Billion in Direct Lending Assets, Providing Liquidity to Investors and Opportunity for Portfolio Optimization", "published_at": "", "scope": "market_event", "source": "Yahoo Finance", "summary_text": "Blue Owl Capital BDCs Sell $1.4 Billion in Direct Lending Assets, Providing Liquidity to Investors and Opportunity for Portfolio Optimization", "symbol": "", "url": "https://finance.yahoo.com/news/blue-owl-capital-bdcs-sell-175128417.html"}, {"headline": "Blue Owl Capital Corporation and Blue Owl Capital Corporation II Announce Merger Agreement", "published_at": "", "scope": "market_event", "source": "PR Newswire", "summary_text": "Blue Owl Capital Corporation and Blue Owl Capital Corporation II Announce Merger Agreement", "symbol": "", "url": "https://www.prnewswire.com/news-releases/blue-owl-capital-corporation-and-blue-owl-capital-corporation-ii-announce-merger-agreement-302606211.html"}, {"headline": "Blue Owl Offers a Harsh Lesson for Semiliquid Fund Investors", "published_at": "", "scope": "market_event", "source": "Morningstar", "summary_text": "Blue Owl Offers a Harsh Lesson for Semiliquid Fund Investors", "symbol": "", "url": "https://www.morningstar.com/alternative-investments/blue-owl-offers-harsh-lesson-semiliquid-fund-investors"}, {"headline": "Blue Owl shares slide again as latest capital-return plan unnerves some investors", "published_at": "", "scope": "market_event", "source": "Reuters", "summary_text": "Blue Owl shares slide again as latest capital-return plan unnerves some investors", "symbol": "", "url": "https://www.reuters.com/business/blue-owl-shares-slide-again-latest-capital-return-plan-unnerves-some-investors-2026-02-19/"}, {"headline": "Blue Owl: The Great Private Credit Opportunity (NYSE:OWL)", "published_at": "", "scope": "market_event", "source": "Seeking Alpha", "summary_text": "Blue Owl: The Great Private Credit Opportunity (NYSE:OWL)", "symbol": "", "url": "https://seekingalpha.com/article/4907579-blue-owl-the-great-private-credit-opportunity"}]
- research.open_page: research summary with 1 rows; columns=url, title, mode, excerpt, warning; sample=[{"excerpt": "/PRNewswire/ -- Blue Owl Capital Corporation (NYSE: OBDC) (\"OBDC\") and Blue Owl Capital Corporation II (\"OBDC II\") announced today that they have entered into...", "mode": "playwright", "title": "Blue Owl Capital Corporation and Blue Owl Capital Corporation II Announce Merger Agreement", "url": "https://www.prnewswire.com/news-releases/blue-owl-capital-corporation-and-blue-owl-capital-corporation-ii-announce-merger-agreement-302606211.html", "warning": ""}]
- research.open_page: research summary with 1 rows; columns=url, title, mode, excerpt, warning; sample=[{"excerpt": "px-captcha", "mode": "playwright", "title": "Access to this page has been denied", "url": "https://seekingalpha.com/article/4907579-blue-owl-the-great-private-credit-opportunity", "warning": ""}]

Limitations:

[
  "LLM error: LLMAPIError: DeepSeek returned empty content."
]

### MAIN

#### business_model

**VERDICT** Main Street Capital (MAIN) is a Business Development Company (BDC) that lends to and invests in lower-middle-market companies. Its primary revenue comes from interest on debt investments and dividend income from equity stakes; capital gains from exits also contribute. The internally managed structure avoids external management fees, supporting net income. **FACTS** - **What it sells:** Debt financing (senior secured loans, mezzanine) and equity capital to small and mid-sized businesses in the lower middle market (LMM). - **Who pays it:** Portfolio companies pay interest on loans and issue dividends on equity held by MAIN. - **Business model:** Internally managed BDC – no external manager. Earns interest income from loan portfolio and dividend income from equity holdings. Also realizes capital gains when portfolio companies are sold or go public. The model relies on conservative use (0.73x debt-to-equity as of recent disclosure). - **Dividend policy:** Pays a regular monthly dividend ($0.26/share) plus supplemental dividends from excess earnings (e.g., $0.30 supplemental in March 2026). Attracts income-focused investors. - **Recent context:** Q1 2026 revenue missed estimates; stock dipped but business model unchanged per news reports. **GAPS** - Precise breakdown of fee income vs. interest income is not available in the retrieved evidence. - Exact portfolio composition (sectors, average loan size) not detailed. - No official financial statements reviewed; the primary source is a third-party analysis (Kavout). **SOURCE REFS** 1. Kavout article "What Makes Main Street Capital's Business Model Unique" (2 months old) – describes BDC model, internally managed structure, dual dividends, use. 2. Investing.com – Q1 2026 earnings miss confirms ongoing operations. 3. Seeking Alpha – notes MAIN is 'finally interesting again', reinforcing income focus. **TAKEAWAY** MAIN operates a standard BDC lending model with internal management and conservative use. Its monthly and supplemental dividends are supported by interest and dividend income from lower-middle-market investments. No evidence on specific fee income or carried-interest economics was found.

Limitations:

[
  "Only one business-model source (Kavout, 2 months old) used.",
  "No official SEC filings or company presentations reviewed.",
  "Revenue breakdown (fee vs interest) not provided.",
  "Carried interest economics not supported by evidence.",
  "Specific fee income types (origination, advisory, commitment fees) not mentioned in sources.",
  "No detailed fee income or spread income data.",
  "No portfolio sector or loan size breakdown.",
  "No information on carried-interest structures."
]
