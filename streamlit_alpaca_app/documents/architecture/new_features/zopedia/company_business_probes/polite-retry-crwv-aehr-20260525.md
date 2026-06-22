# Company Business Stack Probe

Run ID: `polite-retry-crwv-aehr-20260525`
Started UTC: `2026-05-26T03:23:42.421925+00:00`
Tickers: `CRWV, AEHR`

## Results

| Ticker | Status | Seconds | Stack Status | Confidence | Queries | Requests | Results | Opened Pages |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| `CRWV` | **completed** | 383.289 | ready | medium | 10 | 40 | 100 | 11 |
| `AEHR` | **completed** | 780.803 | ready | medium | 10 | 40 | 100 | 29 |

## Stack Summaries

### CRWV — CoreWeave, Inc. - Class A Common Stock

Status: `ready` / `medium`

Coverage: `{"not_planned": 3, "searched_needs_synthesis": 2, "supported": 11}`

Dossier: `18` source lead(s), `14` finding(s)

Source scopes: `{"primary_company": 18}`

Source statuses: `{"full_text_available": 8, "partial_text_available": 1, "snippet_only": 9}`

Source intents: `company_filing, credible_news, employee_reviews, investor_debate, policy_regulatory, web_traffic`

Warnings:

[
  "aql_zopedia_research_plan_gap::No SEC filings or EDGAR evidence currently available in memory; query for 10-Q/10-K is needed.",
  "aql_zopedia_research_plan_gap::No employee review data in memory; query targets Glassdoor and hiring pages.",
  "aql_zopedia_research_plan_gap::No technical or capacity constraint details in memory; supply chain query addresses this.",
  "aql_zopedia_research_plan_gap::No policy or regulatory specifics in memory; query covers AI regulation and export controls.",
  "aql_zopedia_slot_unresolved::workforce_and_hiring::No official headcount numbers, No hiring or layoff announcements, No job posting trends, No analyst estimates on workforce changes",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::No web traffic analytics data (e.g., SimilarWeb, SEMrush), No developer community metrics (e.g., GitHub stars, forks, contributions trend..."
]

Gaps:

[
  "cash_and_runway",
  "workforce_and_hiring",
  "employee_sentiment",
  "web_or_developer_attention",
  "invalidation_events"
]

Story:

CoreWeave is an AI-native cloud platform that provides high-performance GPU infrastructure to AI developers, enterprises, and AI labs. It differentiates by offering purpose-built cloud services for AI workloads, including inference, training, and sandboxed environments. The company has experienced explosive growth, surpassing $5B in revenue in 2025, driven by surging demand from customers like Microsoft (67% of revenue) and OpenAI. It has a massive backlog of $66.8B, indicating strong future revenue visibility. However, execution risks include extreme customer concentration, widening net losses, and the need for massive capex ($31B-$35B in 2026) to sustain growth. The company is the largest AI-native cloud with 850+ MW capacity and 43 data centers.

Facts:

{
  "backlog_or_rpo": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Backlog of $66.8B, scaling with 850+ MW and 43 data centers."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Backlog anchored by major customer agreements including OpenAI (~$22.4B)."
    }
  ],
  "business_model": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave is an AI-native cloud platform that provides cloud-based GPU infrastructure to AI developers and enterprises. It operates its own data centers in the US and Europe."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave Cloud is an AI-native platform combining next-generation infrastructure, intelligent tools, and expert support to power complex AI workloads."
    },
    {
      "confidence": "medium",
      "source": "company_baselines",
      "text": "CoreWeave, Inc. is an American artificial intelligence (AI) cloud-computing company based in Livingston, New Jersey."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "CoreWeave was founded in 2017 as Atlantic Crypto and later pivoted to AI cloud, now specializing in GPU infrastructure for AI workloads."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "CoreWeave brands itself as 'The Essential Cloud for AI' and 'AI-native cloud platform'."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::confirmation_events",
      "text": "CoreWeave achieved $5B annual revenue in 2025, the fastest cloud to do so, with a revenue backlog of $99.4B as of Q1 2026. Q1 2026 revenue was $2.078B with adjusted EBITDA margin of 56%, confirming strong demand for AI infrastructure. The company's rapid growth and large backlog validate its positioning as the essential cloud for AI."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Published first fiscal year as public company, surpassing $5B revenue, and reported $66.8B backlog."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Q1 2026 revenue of $2.078B, up 112% YoY, with strong adjusted EBITDA margin of 56%."
    }
  ],
  "customer_demand": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "CoreWeave achieved $5B annual revenue in 2025, the fastest cloud platform to do so, with revenue backlog reaching $99.4B as of Q1 2026."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Revenue is heavily concentrated with Microsoft accounting for approximately 67% of FY2025 revenue, though backlog is diversifying with major OpenAI commitments up to $22.4B."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Demand is driven by AI workload growth, with quarterly revenue ramping from $981.6M in Q1 2025 to $2.078B in Q1 2026."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::customer_demand",
      "text": "Demand is improving strongly. Revenue grew from $1.915B in 2024 to $5.131B in 2025 (170% YoY), with Q1 2026 revenue of $2.078B (112% YoY). Backlog reached $99.4B as of Q1 2026, anchored by a $22.4B OpenAI commitment. However, revenue mix is concentrated (Microsoft ~67% in FY2025), though backlog diversification is underway. Customer demand remains strong across AI-native and enterprise segments."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave surpassed $5B in annual revenue in 2025, becoming the fastest cloud platform to reach that milestone, driven by surging demand for GPU compute."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Revenue grew 170% YoY from $1.915B in 2024 to $5.131B in 2025, with quarterly ramp from $981.6M in Q1 to $1.572B in Q4."
    }
  ],
  "customer_segments": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "CoreWeave's primary customer is **Microsoft**, which accounted for approximately 67% of FY2025 revenue. Additional customers include **OpenAI** (with commitments up to $22.4 billion) and other AI labs, enterprises, media studios, and HPC researchers. The company targets AI-native companies and enterprise adopters. However, detailed customer names and segment-level revenue breakdowns are not publicly disclosed in audited filings."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Customers are primarily AI labs, media/visual effects studios, HPC researchers, and enterprises needing low-latency GPU clusters."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Trusted by the world's leading AI pioneers."
    }
  ],
  "execution_risks": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Customer concentration with Microsoft (67% of revenue) is a significant operational risk, though backlog is diversifying with OpenAI and others."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::execution_risks",
      "text": "- **Customer concentration**: Microsoft accounts for ~67% of revenue; loss or reduction of this contract would severely impact revenue. - **Competitive pressure**: Intense competition from AWS, Azure, GCP, and other AI cloud providers could erode market share and pricing power. - **Capital intensity**: Massive capex ($31B-$35B in 2026) for GPU infrastructure creates execution risk and dependence on continued demand growth. - **Path to profitability**: Persistent net losses ($740M in Q1 2026) despite high revenue growth; unclear timeline to sustainable profitability. - **AI demand cyclicality**: If AI workload growth slows or shifts to on-premise solutions, demand for CoreWeave's cloud services could decline. - **Regulatory/export control risk**: Export controls on advanced chips (e.g., NVIDIA GPUs) could limit access to hardware or increase costs."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Revenue concentration with Microsoft accounting for 62-67% of revenue is a significant operational risk."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Net loss widened to $1.167B in FY2025, and aggressive capex of $31B\u2013$35B in 2026 raises questions about capital efficiency."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Central question is whether AI compute demand will validate aggressive scale; competition and operational challenges exist."
    }
  ],
  "fundamentals": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Q1 2026 revenue reached $2.078B with adjusted EBITDA of $1.157B (56% margin), net loss of $740M. Backlog $99.4B."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Q2 2025 revenue was $1.213B with backlog $30.1B. Q3 2025 revenue $1.365B with operating income $51.85M."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "FY2025 estimated revenue $5.131B, net loss $1.167B, adjusted EBITDA $3.093B. Guidance for 2026 revenue $12B-$13B."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::fundamentals",
      "text": "CoreWeave's revenue is growing rapidly ($2.078B in Q1 2026, up from $1.365B in Q3 2025) with a massive revenue backlog of $99.4B. Adjusted EBITDA margin was 56% in Q1 2026, but net loss persists ($740M in Q1 2026). Revenue concentration on Microsoft (67% in FY2025) is notable. Cash flow, balance sheet, and debt details are not available from the evidence."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "FY2025 revenue $5.131B, net loss $1.167B, adjusted EBITDA $3.093B. Q1 2026 revenue $2.078B, net loss $740M, adjusted EBITDA $1.157B."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Guided Q2 2026 revenue $2.45B\u2013$2.60B and full-year 2026 revenue $12B\u2013$13B, with exit 2026 ARR of $18B\u2013$19B and capex of $31B\u2013$35B."
    }
  ],
  "named_customers": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Microsoft accounted for approximately 67% of FY2025 revenue."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "OpenAI contracted up to ~$22.4B in total commitments via a March 2025 initial deal of up to $11.9B, expanded later."
    }
  ],
  "policy_or_regulatory_environment": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "CoreWeave has engaged with policymakers, submitting written testimony on export controls and AI leadership to the Senate Commerce Committee."
    }
  ],
  "products_and_services": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::products_and_services",
      "text": "CoreWeave's most important products and services are its GPU cloud infrastructure and AI cloud platform, specifically offering access to NVIDIA GPUs (including the latest Blackwell architecture) for AI model training and inference. The company's core capability is delivering high-performance, low-latency GPU computing at scale for AI workloads, targeting AI labs, enterprises, and HPC researchers. The platform is designed to accelerate AI development and deployment, with a focus on performance and scalability."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Offers GPU cloud services including inference, Sandboxes (isolated environments for RL and agent tool use), and SUNK for unified training at scale."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Specializes in providing cloud-based GPU infrastructure to AI developers and enterprises, and develops its own chip management software."
    }
  ],
  "supply_chain_or_capacity_constraints": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::supply_chain_or_capacity_constraints",
      "text": "**Yes**, capacity and supply chain constraints are critically important to CoreWeave's business model. The company's entire value proposition depends on access to NVIDIA GPUs (especially H100 and future generations) and the ability to rapidly scale data center infrastructure with reliable power. Multiple sources confirm CoreWeave's purpose-built GPU fleet, data center capacity plans, and large-scale deployments (e.g., 8,192 H100 cluster). The company's massive capex guidance of $31B-$35B in 2026 underscores the centrality of capacity expansion. However, specific GPU supply agreements, power availability, and construction timelines are not fully detailed in current evidence."
    }
  ]
}

### AEHR — Aehr Test Systems - Common Stock

Status: `ready` / `medium`

Coverage: `{"not_planned": 3, "searched_needs_synthesis": 3, "supported": 10}`

Dossier: `17` source lead(s), `23` finding(s)

Source scopes: `{"primary_company": 17}`

Source statuses: `{"full_article_available": 8, "partial_snippet": 9}`

Source intents: `company_filing, credible_news, earnings_transcript, employee_reviews, investor_debate, investor_presentation, policy_regulatory, web_traffic`

Warnings:

[
  "aql_zopedia_slot_unresolved::workforce_and_hiring::No official headcount or hiring data from company filings or credible sources, No employee count or trend information available",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::No web traffic data (e.g., SimilarWeb, Alexa), No developer community metrics (e.g., GitHub, Stack Overflow activity), No search trend da...",
  "aql_zopedia_slot_unresolved::policy_or_regulatory_environment::No analysis of semiconductor policy (e.g., CHIPS Act, export controls) or regulatory backdrop specific to Aehr Test Systems, No recent ne..."
]

Gaps:

[
  "named_customers",
  "workforce_and_hiring",
  "employee_sentiment",
  "web_or_developer_attention",
  "policy_or_regulatory_environment",
  "invalidation_events"
]

Story:

Aehr Test Systems is a specialized semiconductor test and burn-in equipment provider, selling wafer-level and package-level burn-in systems primarily to AI and data center customers. The company's revenue model is transactional product sales plus service. Recent quarters show a mixed picture: revenue declined to $10.3M in Q3 FY2026 with widening net losses, but bookings surged to a record $37.2M on strong AI demand, driving backlog above $50M. A record $41M order from a hyperscale AI customer and a new silicon photonics customer win confirm strong demand. Cash position is adequate at $37.1M, supplemented by a $60M ATM offering. Risks include customer concentration, revenue volatility, and ongoing losses. Key information gaps remain in workforce/hiring, employee sentiment, web attention, regulatory environment, and supply chain constraints.

Facts:

{
  "backlog_or_rpo": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Backlog was $15.5M as of Aug 29, 2025 (Q1 FY26), with effective backlog $17.5M including post-quarter bookings."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Backlog surged to $38.7M by Q3 FY26 end, exceeding $50.9M including post-quarter bookings. Book-to-bill over 3.5x."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Received record $41M production order from lead hyperscale AI customer in April 2026."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::backlog_or_rpo",
      "text": "Backlog, bookings, and contracted demand are clearly visible. As of Q1 FY26 (Aug 2025), backlog was $15.5M, effective backlog $17.5M. By Q3 FY26 (Feb 2026), backlog surged to $38.7M, exceeding $50.9M including post-quarter bookings. Book-to-bill was over 3.5x. A record $41M production order from a lead hyperscale AI customer was received in April 2026. Multiple press releases confirm follow-on orders for AI optical I/O and new silicon photonics customers. The company highlighted sharply higher bookings and record backlog above $50M, driven by AI and data center demand."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Backlog at end of Q3 FY2026 was $38.7M, up from $15.5M in August 2025. Including post-quarter bookings, backlog exceeds $50M. Record $41M production order adds to visibility."
    }
  ],
  "business_model": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Fiscal 2024 revenue was ~$66.2M, declining to ~$59.0M in fiscal 2025 (year ended May 2025). TTM revenue is $45.3M."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::business_model",
      "text": "Aehr Test Systems sells wafer-level burn-in and test equipment for semiconductor devices, focusing on reliability-sensitive niches such as silicon carbide (SiC), gallium nitride (GaN), silicon photonics, memory/logic, and high-power packaged devices. Its revenue model is primarily transactional product sales of systems (e.g., FOX-XP, Sonoma platforms) and aftermarket service/support, not subscription, rental, or pay-per-use. Fiscal 2024 revenue was ~$66.2M, declining to ~$59.0M in fiscal 2025. TTM revenue is $45.3M. Customer segments include AI hyperscale data centers, automotive SiC manufacturers, and other semiconductor producers."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Aehr Test Systems sells production test and burn-in equipment for semiconductor devices, primarily wafer-level burn-in (WLBI) and package-level burn-in (PLBI) systems. Revenue model is transactional product sales plus service and support, not subscription."
    }
  ],
  "cash_and_runway": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Cash and equivalents $37.1M as of Feb 27, 2026, up from $31.0M in Nov 2025. Company also announced a $60M ATM equity offering in April 2026, providing additional financing capacity."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Recent confirmations: record $41M order from lead hyperscale AI customer (April 2026), $14M order from lead AI processor customer (Feb 2026), $37.2M quarterly bookings (April 2026), new silicon photonics customer win (March 2026). These validate strong AI-driven demand."
    }
  ],
  "customer_demand": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Demand is strongly improving, driven by AI and data center infrastructure. Bookings surged to $37.2M in Q3 FY2026 (book-to-bill >3.5x), and a record $41M production order from a hyperscale AI customer was received. Backlog reached record levels above $50M including post-quarter orders."
    }
  ],
  "customer_segments": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Demand driven by rapid growth in AI and data centers; Q3 FY26 bookings $37.2M."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "Aehr Test Systems' primary customers are hyperscale AI companies (including an unnamed lead customer that placed a record $41M order in April 2026) and silicon carbide/EV manufacturers, with heavy dependence on a single major SiC customer. The company also serves silicon photonics, memory/logic, and high-power device markets. Customers are largely unnamed in public filings."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Customers include AI processor companies, hyperscale data center operators, automotive semiconductor makers, silicon photonics firms, and memory/logic device manufacturers. Key named: lead AI processor customer, lead hyperscale AI customer (both unnamed)."
    }
  ],
  "execution_risks": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::execution_risks",
      "text": "Aehr Test Systems' business plan could break due to: (1) **Customer concentration** \u2013 heavy reliance on a single hyperscale AI customer (record $41M order) and SiC customer, with no official disclosure of revenue dependency. (2) **Declining revenue and margins** \u2013 Q2 FY26 revenue fell 27% YoY, TTM gross margin ~30.7% vs ~49.1% in FY2024, and net losses deepening. (3) **Valuation risk** \u2013 stock surged ~900% despite weak fundamentals, trading above analyst average target. (4) **Dilution** \u2013 recent $10M equity offering adds share count risk. (5) **Execution dependency on unconfirmed AI demand** \u2013 management's $60-80M H2 bookings guidance hinges on AI processor orders not yet fully confirmed."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Key risks include customer concentration (dependence on a few large AI/hyperscale customers), revenue volatility, net losses, and execution on ramping production to meet record backlog. The $60M ATM offering may dilute existing shareholders."
    }
  ],
  "fundamentals": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "TTM revenue $45.3M, gross profit $13.9M, net loss -$11.4M as of most recent fiscal year (ending May 2025)."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Fiscal year ending May 2025: revenue $58.97M, net loss -$3.91M. Fiscal 2024: revenue $66.22M, net income $33.16M (includes tax benefit)."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::polite-retry-crwv-aehr-20260525",
      "text": "Q3 FY26 revenue $10.3M, net loss -$3.2M. Cash, cash equivalents and restricted cash $37.1M as of Q3 FY26."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::fundamentals",
      "text": "Aehr Test Systems' revenue peaked at $66.2M in FY2024 (May 2024) and has since declined to $45.3M TTM (May 2025) with net losses of -$11.4M. Gross margin has compressed to ~30.7% (TTM) from ~49% in FY2024, driven by lower volumes and product mix. Operating and net margins are deeply negative. Cash and equivalents stood at $37.1M as of Q3 FY2026. Debt is not disclosed in available evidence but appears negligible given the cash position and lack of interest expense. The company has no significant debt-related risks evident from the data."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Q3 FY2026 revenue $10.3M (down from $18.3M YoY), GAAP net loss $3.2M, non-GAAP net loss $1.5M. Nine-month GAAP net loss $8.5M. Full-year FY2026 revenue guidance $45-50M (high side). Cash $37.1M. Revenue decline and widening losses are concerning, but management expects second-half improvement."
    }
  ],
  "products_and_services": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Products include FOX-XP and FOX-NP wafer-level burn-in systems, Sonoma and Tahoe package-level burn-in systems, WaferPak contactors, DiePak carriers, and system level test solutions. Targets AI, automotive, silicon photonics, memory, and silicon carbide markets."
    }
  ],
  "supply_chain_or_capacity_constraints": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::supply_chain_or_capacity_constraints",
      "text": "Yes, capacity, inputs, supply chain, and delivery constraints are important. Aehr Test Systems has highlighted material availability and manufacturing capacity to shorten lead times (source: Feb 2024 press release). The 10-K filing notes that a market shortage of semiconductor and other component supply could affect lead times. The company's record backlog and large orders indicate potential constraints in meeting demand."
    }
  ]
}
