# Company Business Memory Probe

Run ID: `company-business-probe-20260525-nvda-main-timeout-budget`
Started UTC: `2026-05-25T18:43:04.999885+00:00`
Tickers: `NVDA, MAIN`
Facets: `business_model`

## Summary

| Ticker | Facet | Status | Confidence | Tools | Seconds |
| --- | --- | --- | --- | --- | ---: |
| `NVDA` | `business_model` | **completed** | medium | dataset.ticker_business_model_stack, investigator.company_context, zopedia.search_pages, research.search_evidence, research.live_event_evidence, research.open_page | 76.506 |
| `MAIN` | `business_model` | **completed** | low | research.retained_context, investigator.company_context, dataset.ticker_business_model_stack, zopedia.search_pages, research.search_evidence, research.live_event_evidence | 72.189 |

## Answers

### NVDA

#### business_model

**NVIDIA (NVDA) sells accelerated-computing chips and software, primarily to data-center and AI customers, generating high-margin revenue through product sales and licensing.** ### Products and Customers - **What it sells:** Accelerated-computing chips (GPUs) and software for AI, data-center, and graphics workloads. This is from the company context tool. - **Who pays:** Enterprise cloud providers, AI companies, and gamers. Not explicitly stated in evidence, but implied by market coverage. The Trefis article references revenue growth tied to AI and data-center demand. ### Operating Business Model - **Revenue model:** Product sales (chips) and software licensing. Evidence shows revenue of **$68.1B** in Q4 2025 (pipeline data). - **Margins:** Operating margins of **60–65%** (from pipeline fundamentals). Very high profitability, typical of a fabless semiconductor company. - **Cost structure:** Heavy R&D; capital expenditure of ~$1.3–$1.9B per quarter (pipeline data). Free cash flow is large (e.g., **$34.9B** in Q4 2025). - **Growth drivers:** AI and data-center expansion. Revenue grew **70.7%** over last 12 months per Trefis article, though below 3-year CAGR of 121.7%. ### Financial Firm Model (Not Applicable) NVDA is not a financial firm. It does not have a fund/lending/investment model, fee income, spread income, or carried-interest economics. No evidence suggests otherwise. ### Recent Guidance Update - On May 20, 2026, NVDA raised Q2 2027 revenue guidance by **17%** versus prior (Trefis). Stock at **$219.51** as of May 22. - The market initially reacted negatively (-1.8% on day-of), but longer-term trend remains positive (16% return over 3 months, 21% over 6 months). ### GAPS - No official segment revenue breakdown (e.g., Data Center vs. Gaming vs. Automotive) available from the evidence. - No explicit list of top customers. - Business model details (e.g., software vs. hardware revenue split) not provided by any tool. ### SOURCE REFS - Company context tool (agtc_2): describes chip/software design. - Trefis article (agtc_6): guidance raise, revenue growth, margin data. - Pipeline compact fundamentals: revenue, margins, capex, FCF.

Limitations:

[
  "No explicit business model breakdown from Zopedia or SAA evidence; most data comes from general company context and a single news article.",
  "Segment-level revenue and customer names are missing.",
  "Pipeline fundamental data is local, not from a live tool, and may not reflect latest quarters.",
  "Segment revenue breakdown (Data Center, Gaming, Automotive, etc.) not available.",
  "List of top customers not provided.",
  "Software vs. hardware revenue split not detailed."
]

### MAIN

#### business_model

**No business-model evidence for MAIN was returned by any tool.** ### VERDICT The collected data does not describe what Main Street Capital sells, who pays it, or how it makes money. ### FACTS - **Retained context** matched 1 item but provided no details on the business model. - **company_context** returned empty. - **ticker_business_model_stack** returned 0 rows. - **Zopedia search pages** returned concept pages (Duration, Inflation, etc.) that are unrelated to MAIN's operations. - **Live event evidence** showed a few news headlines (e.g., "Why Main Street Capital (MAIN) Shares Are Sliding Today") but none explain the business model. ### GAPS - No structured business-model data (stack, context, or Zopedia page) for MAIN. - Live news articles timed out or did not cover business fundamentals. - The local pipeline JSON also had empty fundamentals and news arrays. ### SOURCE REFS No sources provided useful business-model information. **Conclusion:** Without direct business-model evidence, a grounded answer is not possible at this time.

Limitations:

[
  "No business-model data from any tool",
  "Zopedia search returned irrelevant concept pages",
  "Live evidence timed out or lacked fundamental detail",
  "Local pipeline JSON had empty fundamentals and news",
  "The draft's takeaway to run fresh web research is not supported by evidence and is beyond the scope.",
  "No description of what MAIN sells or its operating model",
  "No details on fee income, spread income, or carried-interest economics",
  "No portfolio composition or fund structure information"
]
