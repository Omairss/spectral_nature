# Trading Agent Business-First Dry Run

Date: 2026-05-24

## Candidate

Ticker: QBTS, D-Wave Quantum

Why this candidate: the latest local `attention_ticker_zopedia_enrichments` cache includes QBTS as a completed, medium-confidence candidate from the 2026-05-23 Attention run. The existing generated summary focuses on the stock move and catalyst. This dry run rewrites the same kind of candidate as an underlying-business review.

Local snapshot anchor:

- `streamlit_alpaca_app/cache/pipeline_store/attention_ticker_zopedia_enrichments/attention_ticker_zopedia_enrichments__20260523T222418Z__25ab8947/frame.pkl`

## Business-First Dry Run

### Read

D-Wave is not primarily a "quantum stock that moved." It is a commercialization-stage quantum computing company selling access to quantum systems, on-premises systems, developer tooling, and services that help customers map optimization, simulation, AI, and research problems onto its machines.

The business question is: are real customers moving beyond experiments and committing dollars to D-Wave's platform, and is the political/industrial environment making that easier?

### What The Company Sells

D-Wave sells a dual-platform quantum portfolio:

- Advantage2 annealing quantum computers for optimization, materials simulation, and AI-adjacent workloads.
- Leap cloud access with hybrid solvers for large optimization problems.
- Professional services through D-Wave Launch, moving customers from use-case discovery to proof of concept, pilot, and production.
- Ocean open-source developer tools that make D-Wave systems usable by developers and partners.
- A developing gate-model roadmap after acquiring Quantum Circuits.

This matters because the near-term business is not "wait for universal fault-tolerant quantum." The near-term business is selling annealing systems, cloud access, and services into customers that have optimization problems today.

### Demand Evidence

Demand is improving, but it is still lumpy and early.

Positive signals:

- Q1 2026 bookings were $33.4 million, up sharply year over year, including a $20 million FAU system purchase and a $10 million two-year enterprise QCaaS agreement with a Fortune 100 company.
- Remaining performance obligations reached $42.4 million as of March 31, 2026, with about 54% expected to convert to revenue within 12 months.
- D-Wave recognized Q1 revenue from more than 100 individual customers, with over half commercial enterprises.
- FY 2025 revenue grew 179% to $24.6 million, and FY 2025 customers included more than 135 individual customers and more than 70 commercial enterprises.

Negative or unresolved signals:

- Q1 2026 revenue was only $2.9 million, down from a system-sale-heavy prior-year quarter.
- Bookings are not revenue. The thesis needs conversion evidence across 2026, not only order announcements.
- Customer count is broad, but the dollar concentration and renewal quality are not fully visible from public data.

### Customer And Use-Case Quality

The customer examples are more concrete than most early quantum companies:

- FAU is buying and installing an Advantage2 system.
- A Fortune 100 customer signed a two-year QCaaS agreement.
- Shionogi is using Advantage2 in drug discovery work and moved to another phase after improved molecule-generation results.
- Postquant Labs is using Advantage2 in a quantum-classical blockchain testnet.
- Past customer references include Pattison Food Group, Ford Otosan, BASF, Shionogi, and Julich Supercomputing Centre.
- D-Wave also cites government, defense, logistics, manufacturing, finance, research, and life sciences use cases.

The quality check is whether these become repeatable workflows with renewals, capacity expansion, or system purchases, rather than bespoke demonstrations.

### Workforce, Hiring, And Culture

Headcount appears to be expanding:

- The 2025 10-K says D-Wave had about 388 employees as of February 5, 2026, including Quantum Circuits employees, versus 220 at the end of 2024.
- About 58% were near R&D/manufacturing facilities in Burnaby and New Haven, and the company said it was growing its U.S. presence in fabrication, software, professional services, and go-to-market.
- The public careers page currently shows multiple R&D-heavy openings in New Haven and Burnaby, including quantum engineering, fabrication, embedded software, quantum error correction software, calibration automation, and process integration.
- The hiring pattern fits the strategy: integrate Quantum Circuits, build New Haven gate-model capability, and deepen annealing system development.

Employee sentiment is currently unresolved:

- The accessible Glassdoor page did not expose usable employee reviews in this environment, so employee happiness remains a data gap for this dry run.
- Company-owned career copy is strongly positive but should be treated as marketing.
- The hiring and headcount signals are stronger evidence than individual review snippets.

### Website And Attention

Digital traffic data is noisy. Third-party estimates conflict:

- AltIndex search results suggested materially higher D-Wave web interest, but the page did not load reliably in this environment.
- HypeStat shows much lower traffic for `dwavequantum.com`, likely because it is estimating a specific domain and may not capture older `dwavesys.com`, investor, cloud, and support properties.

The platform should treat web traffic as a weak directional signal unless it has a stable provider and a canonical domain map. For D-Wave, customer demand and bookings are much better evidence than website traffic.

### Global And Political Environment

The external regime is supportive:

- The U.S. Department of Commerce signed an LOI for $100 million of proposed CHIPS Act funding for D-Wave, with the government receiving equity if final documents are executed.
- The proposed award is part of a broader U.S. quantum industrial-policy push.
- The UK announced up to GBP 2 billion of quantum support in March 2026.
- McKinsey's 2026 Quantum Technology Monitor projects a large 2035 market, while acknowledging the sector is still early and uneven.

Policy support does not validate D-Wave's unit economics by itself. It does reduce financing and ecosystem risk, and it makes government/defense/public-sector demand more plausible.

### Business Thesis

QBTS is a business-first watch candidate because D-Wave has real commercial proof points in a speculative sector: installed or contracted systems, cloud/service agreements, customer names, a growing R&D workforce, and fresh policy support. The core thesis is not that the stock is up. The core thesis is that D-Wave may be one of the few quantum companies converting the theme into customer commitments now.

The main risk is the same evidence in reverse: revenue remains small, bookings are lumpy, the CHIPS award is not final, and the company must prove that customer experiments become durable revenue.

### What Would Change The View

Positive confirmation:

- FAU installation begins on schedule.
- The Fortune 100 QCaaS customer expands or is joined by similar enterprise contracts.
- Q2/Q3 2026 RPO converts into recognized revenue.
- New Haven hiring maps to visible gate-model milestones.
- Government business unit wins non-dilutive contracts or paid programs.

Negative confirmation:

- Bookings do not convert into revenue.
- CHIPS award documents fail or milestones slip.
- Hiring slows in New Haven/Burnaby while roadmap commitments remain aggressive.
- Customer announcements remain one-off proof-of-concepts with no renewals.
- Employee sentiment weakens around management, retention, or integration after Quantum Circuits.

## Encoding Into Spectral Nature

### Do Not Build A Side Agent

This should be a Trading Agent mode inside the shared AQL/Zopedia spine. The pipeline should not add a separate scraper or hardcoded "business score" writer. Deterministic code should enforce evidence slots and source quality; the LLM should write the business narrative from evidence.

### Evidence Slots

For each company candidate, require these slots:

- `business_model`: what the company sells, how it charges, which products matter now.
- `customer_demand`: bookings, revenue, RPO/backlog, customer count, retention/renewal evidence, major customer wins.
- `customer_quality`: named customers, use cases, whether deployments are pilots or production.
- `workforce`: headcount, growth, employee distribution, R&D vs go-to-market mix.
- `hiring`: open roles by function/location, recent leadership or acquisition-driven team changes.
- `employee_sentiment`: review ratings and recurring themes, with source caveats.
- `digital_attention`: web traffic, search/social/developer indicators, with confidence labels.
- `macro_policy`: global trend, public funding, regulation, industrial policy, geopolitical support or pressure.
- `business_risks`: revenue quality, concentration, dilution, technical execution, customer conversion.
- `watch_triggers`: concrete future events that would confirm or break the thesis.

### Data Sources By Cadence

Slow monthly baseline:

- company website product pages
- 10-K / 10-Q / 8-K filings
- investor presentations and earnings transcripts
- employee count and hiring pages
- Glassdoor/Great Place to Work/LinkedIn-style workforce data when available
- canonical company domain map

Daily or event-driven:

- latest earnings and press releases
- material contract/customer announcements
- policy/funding news
- job openings delta
- web/search/social/developer attention deltas
- current Attention candidate and market-move context

### Output Shape

The Trading Agent candidate schema should gain a business thesis object, for example:

```json
{
  "ticker": "QBTS",
  "company_name": "D-Wave Quantum",
  "business_read": {
    "what_they_sell": "...",
    "demand_read": "...",
    "customer_read": "...",
    "workforce_read": "...",
    "external_regime": "...",
    "thesis": "...",
    "confirmation_events": ["..."],
    "invalidation_events": ["..."],
    "evidence_gaps": ["..."]
  },
  "market_context": {
    "why_it_appeared_today": "...",
    "price_move_context": "..."
  }
}
```

`market_context` is supporting context. `business_read` is the center of the card.

### UI Implication

The card should lead with:

1. What the company does.
2. Why customer demand appears real or not.
3. What changed recently in the business.
4. What could confirm or break the thesis.

Then show price action, technicals, and statistical anomaly as a small "why surfaced" block.

### Reliability Rules

- Official filings, earnings releases, and product pages are high-authority.
- Job openings and career pages are medium-authority.
- Glassdoor, Similarweb-style traffic, and social posts are directional only.
- If a slot is weak, say exactly which slot is weak. Do not fill it with news-count metadata.
- Do not let a strong stock move upgrade business confidence unless business evidence improved too.

## Source Register

- D-Wave Q1 2026 results: https://ir.dwavequantum.com/news/news-details/2026/D-Wave-Reports-First-Quarter-2026-Results/default.aspx
- D-Wave FY 2025 results: https://www.dwavequantum.com/company/newsroom/press-release/d-wave-reports-fourth-quarter-and-year-end-2025-results/
- D-Wave CHIPS Act LOI: https://www.dwavequantum.com/company/newsroom/press-release/d-wave-and-department-of-commerce-sign-letter-of-intent-for-100-million-in-chips-and-science-act-funding/
- D-Wave product overview: https://www.dwavequantum.com/solutions-and-products/product-overview/
- D-Wave Leap cloud platform: https://www.dwavequantum.com/solutions-and-products/cloud-platform/
- D-Wave professional services: https://www.dwavequantum.com/solutions-and-products/professional-services/
- D-Wave careers: https://www.dwavequantum.com/company/careers/
- D-Wave Rippling job list: https://ats.rippling.com/d-wave-quantum/jobs
- D-Wave 2025 10-K PDF: https://www.sec.gov/Archives/edgar/data/1907982/000190798226000043/annualreport2025.pdf
- Glassdoor employee review page, unusable in this run: https://www.glassdoor.com/Reviews/D-Wave-Systems-Reviews-E756566.htm
- AltIndex web traffic page, unreliable in this run: https://altindex.com/ticker/qbts/webtraffic
- HypeStat traffic estimate: https://hypestat.com/info/dwavequantum.com
- UK quantum funding announcement: https://www.gov.uk/government/news/uks-quantum-leap-tohelp-beat-diseasedeliver-high-paid-jobs-and-strengthen-national-security-as-first-country-in-the-world-to-roll-out-Quantum
- McKinsey Quantum Technology Monitor 2026: https://www.mckinsey.com/capabilities/mckinsey-technology/our-insights/mckinsey-quantum-technology-monitor-2026-a-commercial-tipping-point
