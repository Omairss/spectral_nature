# Company Business Stack Probe

Run ID: `company-business-4co-new-design-20260525-v3`
Started UTC: `2026-05-26T00:23:31.785696+00:00`
Tickers: `NVDA, CRWV, BX, MAIN`

## Results

| Ticker | Status | Seconds | Stack Status | Confidence | Queries | Requests | Results | Opened Pages |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| `NVDA` | **completed** | 195.678 | ready | medium | 16 | 128 | 256 | 10 |
| `CRWV` | **completed** | 315.985 | ready | medium | 16 | 128 | 256 | 24 |
| `BX` | **completed** | 215.827 | partial | medium | 16 | 128 | 256 | 4 |
| `MAIN` | **completed** | 171.082 | ready | medium | 16 | 128 | 256 | 23 |

## Stack Summaries

### NVDA — NVIDIA Corporation

Status: `ready` / `medium`

Coverage: `{"searched_needs_synthesis": 3, "supported": 13}`

Dossier: `24` source lead(s), `17` finding(s)

Source scopes: `{"peer_or_customer": 1, "primary_company": 23}`

Source statuses: `{"accessible": 24}`

Source intents: `company_filing, credible_news, earnings_transcript, employee_reviews, investor_presentation, policy_regulatory, web_traffic`

Warnings:

[
  "aql_zopedia_slot_unresolved::backlog_or_rpo::Official backlog/RPO data from Nvidia SEC filings",
  "aql_zopedia_slot_unresolved::cash_and_runway::Operating cash flow, Free cash flow, Cash and equivalents balance, Debt levels, Liquidity ratios",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::Web traffic data over time, GitHub activity metrics for CUDA and related repos, Developer conference attendance trends, Community forum a..."
]

Gaps:

[
  "backlog_or_rpo",
  "cash_and_runway",
  "web_or_developer_attention"
]

Story:

Nvidia is the dominant provider of AI computing hardware, primarily GPUs and networking for data centers. In Q1 FY2027, total revenue was $81.6B (up 85% YoY), with Data Center contributing $75.2B. Customer demand remains extremely strong, with company guidance and CEO statements indicating sustained multi-year growth as hyperscalers and enterprises invest in AI infrastructure. The company consistently beats earnings estimates, confirming strong execution. However, supply constraints persist with demand still outpacing capacity. Key gaps include visibility into backlog/RPO, cash runway details, workforce dynamics, developer attention signals, and regulatory risks. Overall, the business model is validated by explosive revenue growth and high demand, though operational details remain opaque.

Facts:

{
  "business_model": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Nvidia's Q4 FY2025 revenue was $39.3B, up 78% YoY, and Compute & Networking is the largest segment."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Nvidia's business model is heavily reliant on Data Center and AI compute."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::business_model",
      "text": "NVIDIA sells graphics processing units (GPUs) and networking hardware for data centers, gaming, and AI. Its primary revenue driver is the Data Center segment (part of Compute & Networking), which includes AI chips and related infrastructure. The company also earns from gaming GPUs and professional visualization. As of Q4 FY2025, total revenue was $39.3B, up 78% YoY, with Compute & Networking being the largest segment."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Nvidia generates revenue primarily from Data Center compute and networking, including AI GPUs. In Q1 FY2027, Data Center revenue was $75.2B out of total $81.6B."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Nvidia's primary business segment is Compute and Networking, which includes AI. It also has a Gaming segment, but Data Center dominates."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Earnings beats and strong data center growth reported by Guardian and SiliconANGLE for recent quarters, but official confirmation for those specific periods is not present in snippet."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::confirmation_events",
      "text": "Recent earnings beats and strong data center growth reported by The Guardian (Feb 2026) and SiliconANGLE (May 2024) confirm the existing business story of Nvidia's dominance in AI and data center chips. The Guardian reported earnings per share of $1.62 beating $1.53 estimates, while SiliconANGLE noted a 427% rise in data center chip sales. These events reinforce the thesis of sustained demand and growth."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Multiple earnings beats confirm strong demand and execution: Q1 FY2025 data center chip sales up 427% YoY; Q4 FY2025 revenue up 78% YoY; Q1 FY2027 revenue up 85% YoY."
    }
  ],
  "customer_demand": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CEO Jensen Huang predicted data center expenditures could reach $1T by 2028, implying 3x growth in 3 years."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Nvidia expects to generate at least $1T from AI chips through 2027."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::customer_demand",
      "text": "Evidence indicates demand is improving significantly. Revenue grew 78% YoY in Q4 FY2025 ($39.3B) and 22% QoQ in Q3 FY2026 ($57B). CEO Jensen Huang expects data center expenditures to triple by 2028 and Nvidia anticipates at least $1 trillion in AI chip revenue through 2027. Supply continues to struggle to keep pace with demand per industry commentary."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Demand remains extremely strong: Nvidia still cannot keep up with demand for data center GPUs as of Q4 2025. CEO expects AI spending to triple by 2028 and company expects $1T in cumulative AI chip revenue by 2027."
    }
  ],
  "customer_segments": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Customer base splits into Enterprise/Data Center and Consumer/Gaming segments; Data center is dominant with ~90% of revenue (per Reddit comment) and B2B enterprise is high-margin."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "NVIDIA's customer base is split into two main segments: Enterprise/Data Center (B2B) and Consumer/Gaming (B2C). The Data Center segment is dominant, accounting for approximately 90% of revenue (per recent Reddit commentary), while the Gaming segment contributes around 7%. Enterprise clients include cloud service providers, AI startups, and large corporations; consumers include gamers and PC enthusiasts. However, an official segment revenue breakdown from NVIDIA's filings is needed for precise verification."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Primary customer segments are B2B enterprise/data center (88% of revenue in FY2025) and B2C consumer/gaming."
    }
  ],
  "employee_sentiment": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "93% of employees would recommend working at Nvidia based on Glassdoor reviews, and work culture is described as collaborative and innovative."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::employee_sentiment",
      "text": "Employee sentiment is positive based on Glassdoor reviews (93% recommend, collaborative culture). Morale appears to be an asset rather than a risk."
    }
  ],
  "execution_risks": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Increased competition from AMD, Intel, and cloud providers threatens Nvidia's market share."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::execution_risks",
      "text": "Increased competition from AMD, Intel, and cloud providers threatens Nvidia's market share. The business plan could be derailed if competitors gain significant share or customers develop in-house AI chips. Partnerships like Intel-Nvidia may also shift market dynamics. However, quantitative market share trends and customer defection data are lacking."
    }
  ],
  "fundamentals": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Record revenue in Q3 FY2026: $57B, up 22% QoQ."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Q4 FY2025 revenue $39.3B, up 78% YoY."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::fundamentals",
      "text": "Revenue shows strong growth: Q3 FY2026 record $57B (up 22% QoQ) and Q4 FY2025 $39.3B (up 78% YoY). However, no data on margins, cash flow, balance sheet, or debt is available from the provided sources. Missing: margin breakdown (gross, operating, net), cash flow statements (operating, free), balance sheet items (cash, debt, equity)."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Q1 FY2027 revenue $81.6B, up 85% YoY; Data Center $75.2B. FY2025 revenue $130.5B. Earnings consistently beat estimates."
    }
  ],
  "invalidation_events": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::invalidation_events",
      "text": "Recent events contradicting Nvidia's positive business story include the imposition of new U.S. export controls on AI chips to China, which led to a significant stock decline and market cap loss of nearly $270 billion. The H20 chip, a slowed-down version for China, was also affected. These regulatory headwinds challenge the narrative of uninterrupted growth."
    }
  ],
  "named_customers": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::named_customers",
      "text": "Source-backed named partners include Argonne National Laboratory, Los Alamos National Laboratory, World Wide Technology, Insight Enterprises, International Computer Concepts, and Sterling. End markets are not specified by name in evidence."
    }
  ],
  "policy_or_regulatory_environment": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::policy_or_regulatory_environment",
      "text": "The regulatory backdrop appears supportive based on a recent policy loosening export restrictions on Nvidia H200 chips and similar AI chips to China (CFR article, Jan 2026). This easing reduces a key regulatory risk for Nvidia's international sales."
    }
  ],
  "products_and_services": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::products_and_services",
      "text": "NVIDIA's most important products and services are its data center GPUs (e.g., H100, B200) and AI computing platforms, which drive the majority of revenue through the Compute & Networking segment. The company also offers automotive AI solutions, including physical AI and simulation, and maintains a core gaming GPU business. These capabilities are central to NVIDIA's market leadership in accelerated computing."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Core products include data center GPUs (e.g., Blackwell), networking solutions, and AI computing platforms."
    }
  ],
  "supply_chain_or_capacity_constraints": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::supply_chain_or_capacity_constraints",
      "text": "Evidence indicates that capacity and supply chain constraints are important for NVIDIA. NVIDIA is asking TSMC to ramp production starting Q2 2026, and has pre-ordered 60-70% of TSMC's CoWoS capacity for 2024. TSMC is capacity-limited, and demand is outpacing supply."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Demand still outpacing supply for data center GPUs as of Q4 2025, indicating capacity constraints."
    }
  ],
  "workforce_and_hiring": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::workforce_and_hiring",
      "text": "Headcount is expanding significantly. NVIDIA's workforce grew from ~26,196 in 2023 to ~29,600 in 2024 (12.99% growth) and further to ~36,000 in FY2025 (21.62% increase), with over 15,000 employees added between 2020 and 2024."
    }
  ]
}

### CRWV — CRWV (identity not resolved)

Status: `ready` / `medium`

Coverage: `{"searched_needs_synthesis": 4, "supported": 12}`

Dossier: `21` source lead(s), `18` finding(s)

Source scopes: `{"peer_or_customer": 1, "primary_company": 20}`

Source statuses: `{"Full text available": 5, "Partial text available": 2, "Snippet only": 14}`

Source intents: `company_filing, credible_news, employee_reviews, hiring_page, policy_regulatory, web_traffic`

Warnings:

[
  "aql_zopedia_research_plan_gap::Company legal name and aliases are unknown; searches across SAA, Zopedia, and live web returned no results for CRWV.",
  "aql_zopedia_research_plan_gap::No company profile, filings, or news found; all slots are unfilled.",
  "aql_zopedia_slot_unresolved::workforce_and_hiring::No headcount or hiring trend data available, No recent employee count or hiring rate, No information on workforce expansion or contraction",
  "aql_zopedia_slot_unresolved::employee_sentiment::No employee review data accessed; Glassdoor or similar sources not retrieved",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::No evidence regarding website traffic, developer interest, community engagement, or search trends for CRWV., No data from web analytics p...",
  "aql_zopedia_slot_unresolved::invalidation_events::Reasons for the Q3 2025 disappointment (e.g., revenue miss, margin decline, guidance cut), Fundamental context for the 5.25% drop (e.g.,..."
]

Gaps:

[
  "workforce_and_hiring",
  "employee_sentiment",
  "web_or_developer_attention",
  "invalidation_events"
]

Story:

CoreWeave (CRWV) is a specialized cloud infrastructure provider focused on GPU-accelerated computing for AI workloads. The company sells access to high-performance Nvidia GPU clusters, deployed in its own data centers, targeting hyperscalers, AI labs, and enterprise customers. Demand is extremely strong, with a reported backlog of ~$100 billion and customers being turned away. Revenue reached $5.1B in FY2025 and surged 112% YoY to $2.08B in Q1 2026. However, the business carries high financial use (debt-to-equity >520%) and relies heavily on Nvidia for hardware, creating concentration risk. Cash runway is limited but has been extended by recent debt raises. The company has significant execution risk tied to capacity expansion, debt servicing, and maintaining access to Nvidia's latest GPUs. Enterprise customer diversification is growing, including a $10B+ mandate from financial services. Overall, the business model is validated by massive backlog and customer demand, but sustainability depends on managing use and supply chain dependence. Missing evidence on workforce, employee sentiment, regulatory environment, and specific product details weakens the complete picture.

Facts:

{
  "backlog_or_rpo": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::backlog_or_rpo",
      "text": "CoreWeave reports substantial backlog figures: Q3 2025 added $25 billion, and total backlog reached nearly $100 billion as of FY2025. However, these figures come from third-party articles rather than audited financial statements, so they should be treated as indicative but not fully verified."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave has a contracted backlog of approximately $99.4 billion, with $40 billion in new bookings in a single quarter."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "In Q1 2026, CoreWeave raised $8.5 billion in new debt, adding to backlog."
    }
  ],
  "business_model": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave is a specialized cloud infrastructure provider for GPU-intensive computing, targeting AI workloads. It was founded as a crypto mining venture and pivoted to cloud computing."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave reported Q3 2025 revenue of $1.4 billion (134% YoY growth) and added $25 billion in backlog. FY2025 revenue was $5.1 billion, with nearly $100 billion in total backlog."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::business_model",
      "text": "CoreWeave sells specialized cloud infrastructure for GPU-intensive computing, primarily targeting AI training and inference workloads. It makes money by renting out Nvidia GPUs and associated data center services to hyperscalers and AI labs (e.g., OpenAI) under long-term contracts, generating revenue from cloud service fees. The company has reported rapid revenue growth ($1.4B in Q3 2025, $5.1B in FY2025) and a large backlog (~$100B), indicating a contracted revenue stream."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave is a cloud infrastructure provider specializing in GPU-based computing, primarily for AI training and inference workloads, having pivoted from cryptocurrency mining."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "The company's access to Nvidia GPUs is a key resource, and it uses its GPU inventory as collateral for debt financing."
    }
  ],
  "cash_and_runway": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave has a high debt-to-equity ratio (522.4%) with total debt of $24.86 billion and cash of $2.27 billion. Cash runway was estimated at 3 months based on free cash flow, but subsequent capital raises may have extended it."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "The company has a financing obligation with a 14-year term and 15% imputed interest rate, indicating costly debt financing."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::cash_and_runway",
      "text": "CoreWeave's cash runway is critically short. Based on the latest available data, the company had $2.27 billion in cash against $24.86 billion in total debt and a 3-month cash runway from free cash flow. Although subsequent debt raises (e.g., $8.5 billion in Q1 2026) may have extended the runway, the heavy debt load (debt-to-equity 522%), high imputed interest rate of 15% on a 14-year financing obligation, and negative operating income indicate insufficient internal cash generation. The plan likely requires continuous external financing, which is costly and may not be sustainable. Therefore, there is not enough cash or financing capacity to support the plan without additional dilutive or restrictive debt measures."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Cash runway is approximately 3 months based on last reported free cash flow, but subsequent capital raising has occurred."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "The company has a 14-year financing obligation with an imputed interest rate of 15% on critical infrastructure assets."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave raised $8.5 billion in new debt in Q1 2026 and announced deals with AI startups Cline and Perplexity."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::confirmation_events",
      "text": "CoreWeave raised $8.5 billion in new debt in Q1 2026 and announced deals with AI startups Cline and Perplexity, confirming continued capital access and customer growth. Additionally, management reiterated an optimistic revenue outlook, though without specific guidance."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Q1 2026 earnings showed strong revenue growth and raised $8.5 billion in new debt, signaling continued investment in capacity."
    }
  ],
  "customer_demand": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_demand",
      "text": "Demand is improving. CoreWeave is in a hypergrowth phase driven by AI cloud demand, with revenue growing 134% YoY to $1.4B in Q3 2025 and a growing pipeline diversified across media, healthcare, finance, industrials. Total backlog exceeds $100B. However, quantitative demand metrics and customer concentration data are not available from public sources."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Demand for AI infrastructure is described as insatiable, with customers being turned away due to capacity constraints."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Pipeline continues to grow and diversify across media, healthcare, finance, industrials and more."
    }
  ],
  "customer_segments": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave's client base includes hyperscalers, nine of the top ten global non-China AI labs, and enterprise customers. Financial services represent over $10 billion of backlog, and enterprise logos are growing rapidly."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave maintains relationships with semiconductor manufacturers, OEMs, ODMs, and software vendors."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "CoreWeave's customers include hyperscalers, nine of the top ten global non-China AI labs, enterprise clients (with rapidly growing logos), and financial services (over $10B backlog). It also maintains relationships with semiconductor manufacturers, OEMs, ODMs, and software vendors. Named but lower-confidence customers include OpenAI, Microsoft, Meta, and Google."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Client base spans hyperscalers, AI labs (including nine of the top ten global non-China labs), and enterprise customers across media, healthcare, finance, and industrials."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Financial services represent over $10 billion of current backlog, heavily using infrastructure for inference workloads."
    }
  ],
  "execution_risks": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Concentration risk: all customer contracts require Nvidia GPUs; reliance on Nvidia for hardware access."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "High debt-to-equity ratio and use of GPU inventory as collateral increase financial use risk."
    }
  ],
  "fundamentals": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave reported Total Revenue of $6.227 billion and $5.131 billion for two recent periods (likely FY2025 and FY2024), with Gross Profit of $4.320 billion and $3.678 billion respectively."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Operating expenses exceeded gross profit, resulting in operating losses of -$162.53 million and -$46 million in the respective periods."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::fundamentals",
      "text": "CoreWeave's recent financials show strong revenue growth from $5.131B to $6.227B (likely FY2024 to FY2025), with gross profit rising from $3.678B to $4.320B, implying gross margins of ~69-72%. However, operating expenses exceeded gross profit, leading to operating losses of -$46M and -$162.53M in the respective periods, indicating negative operating margins. The balance sheet is highly use: debt-to-equity ratio of 522.4%, total debt of $24.86B, and cash of only $2.27B, resulting in a cash runway of approximately 3 months based on free cash flow (though subsequent debt raises may have extended this). Additionally, the company carries a financing obligation with a 14-year term and 15% imputed interest rate, highlighting costly debt. Net income and cash flow statements were not available in the evidence."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "FY2025 revenue was $5.1 billion; Q1 2026 revenue surged 112% YoY to $2.08 billion. Gross profit for latest fiscal year was $4.32 billion on revenue of $6.23 billion, with operating loss of $162.5 million."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Debt-to-equity ratio is 522.4%, with total debt of $24.86 billion and cash of $2.27 billion."
    }
  ],
  "named_customers": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "NVIDIA is both a partner and investor, with a $2 billion investment and a goal to build 5 GW of AI factories by 2030."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::named_customers",
      "text": "Named customers and partners source-backed: **NVIDIA** is both a partner and investor with a $2 billion investment and goal to build 5 GW of AI factories by 2030 (source: NVIDIA Newsroom, high authority). Additionally, CoreWeave counts **OpenAI, Microsoft, Meta, and Google** among its customer base (source: Yahoo Finance, medium authority). The NVIDIA relationship is firmly documented; the other names are reported but lack direct confirmation from official customer lists or contracts."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "CoreWeave has deals with AI startups Cline and Perplexity, and works with OpenAI."
    }
  ],
  "policy_or_regulatory_environment": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "CoreWeave is subject to laws and regulations regarding data privacy, data protection, information security, user protection, and AI."
    }
  ],
  "products_and_services": [
    {
      "confidence": "medium",
      "source": "aql_zoped

### BX — Blackstone Inc.

Status: `partial` / `medium`

Coverage: `{"searched_needs_synthesis": 5, "supported": 11}`

Dossier: `31` source lead(s), `13` finding(s)

Source scopes: `{"primary_company": 30, "sector_or_macro": 1}`

Source statuses: `{"Company earnings release": 7, "Company fund website (BCRED)": 1, "Company investor presentation": 1, "Company marketing page": 2, "Company press release": 2, "Customer (CPP Investments) press release": 1, "Glassdoor employee reviews": 3, "Industry news (Private Debt Investor)": 1, "Industry publication (PERE)": 1, "LinkedIn social media post": 1, "Press article on Yahoo Finance": 1, "Press coverage of earnings call": 1, "SEC 10-K filing": 3, "SEC 10-Q filing": 1, "SEC S-1 filing (historical)": 1, "Third-party YouTube summary of earnings call": 1, "Third-party blog analysis": 1, "Third-party explanatory article": 1, "Third-party social media summary": 1}`

Source intents: `company_filing, credible_news, earnings_transcript, employee_reviews, web_traffic`

Warnings:

[
  "aql_zopedia_research_plan_gap::Specific fee rates and management fee breakdown by strategy",
  "aql_zopedia_research_plan_gap::Detailed LP composition and concentration",
  "aql_zopedia_research_plan_gap::Exact dry powder by vintage and deployment timeline",
  "aql_zopedia_research_plan_gap::Employee satisfaction scores and turnover rates",
  "aql_zopedia_research_plan_gap::Recent regulatory actions or investigations specifics",
  "aql_zopedia_slot_unresolved::fundamentals::Current revenue breakdown and net income, Margins (e.g., operating, net), Cash flow statements, Balance sheet: total assets, liabilities,...",
  "aql_zopedia_slot_unresolved::policy_or_regulatory_environment::Recent regulatory changes or proposals affecting private equity, credit, real estate, and infrastructure funds, Analysis of SEC private f...",
  "aql_zopedia_slot_unresolved::supply_chain_or_capacity_constraints::Direct statements on capacity constraints in capital deployment or deal flow, Information on talent acquisition and retention challenges,...",
  "aql_zopedia_slot_unresolved::execution_risks::Official risk factors from 10-K filing (e.g., Item 1A), Segment performance data (real estate, credit, PE), Size and composition of softw...",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::Website traffic analytics for Blackstone.com, Investor portal usage metrics, Google Trends or search interest data for 'Blackstone', Deve...",
  "business_stack_not_ready_missing_core_slots::fundamentals,execution_risks",
  "durable_memory_not_written_low_confidence_or_missing_business_model"
]

Gaps:

[
  "fundamentals",
  "web_or_developer_attention",
  "policy_or_regulatory_environment",
  "supply_chain_or_capacity_constraints",
  "execution_risks"
]

Story:

Blackstone (BX) is the world's largest alternative asset manager, generating revenue through management fees, performance fees, and carried interest from its Private Equity, Real Estate, Credit & Insurance, and Multi-Asset funds. Its customer base spans large institutional investors and a rapidly growing private wealth channel, which raised $43 billion in 2025 (up 53% year-over-year). Demand remains strong, with record AUM of $1.26 trillion, increasing deployment ($133.9B in 2024, $138.2B in 2025), and dry powder of $177.2 billion as of Q1 2025. The business is increasingly anchored by perpetual capital (46% of fee-earning AUM), providing stable fee income. Execution risks include portfolio performance, but the private credit fund BCRED notes its software exposure is well-positioned. Employee sentiment is moderate (71% Glassdoor recommendation). Capital markets access is demonstrated by a $750M note issuance. Gaps remain in detailed recent fundamentals, named customers, workforce trends, web attention, and regulatory landscape.

Facts:

{
  "backlog_or_rpo": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::backlog_or_rpo",
      "text": "Yes, Blackstone reports dry powder (undrawn capital) as $168.6B (Q4 2024) and $177.2B (Q1 2025), serving as a proxy for future fee-earning potential and analogous to backlog/RPO for asset managers."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Undrawn capital (dry powder) was $168.6B at end of 2024 and $177.2B at end of Q1 2025, indicating a strong investment backlog."
    }
  ],
  "business_model": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::business_model",
      "text": "Blackstone Inc. is an alternative asset manager that sells investment management services across private equity, real estate, credit, infrastructure, secondaries, and hedge fund advisory. It generates revenue primarily through two streams: (1) management fees, typically a percentage of assets under management (e.g., 1.25% annual management fee, plus additional AIFM and servicing fees), and (2) performance-based fees, such as carried interest or performance fees (e.g., 12.5% above a 5% hurdle)."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Blackstone earns revenue through management fees (e.g., 1.25% annual management fee), performance fees (12.5% above a 5% hurdle), and carried interest. It is an alternative asset manager."
    }
  ],
  "cash_and_runway": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::cash_and_runway",
      "text": "Blackstone has demonstrated access to capital markets (issued $750M of 10-year notes at 5.00% in Dec 2024) and holds significant dry powder ($168.6B-$181.2B in 2024-2025). These factors suggest sufficient financing capacity for growth plans, though a full cash and liquidity analysis is limited by the absence of reported cash, total debt, and liquidity facility details."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Blackstone raised $750M in 10-year notes at 5.00% coupon in December 2024, indicating access to debt capital."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::confirmation_events",
      "text": "Recent events confirm Blackstone's existing business story: private wealth fundraising rose 53% YoY to $43B in 2025; fee-earning perpetual capital AUM reached $380.1B, representing 46% of fee-earning AUM; and Blackstone holds an estimated 50% share of private wealth revenue among major alternatives firms (Q4 2024/2025 earnings calls)."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Strong private wealth fundraising (53% growth to $43B in 2025), growth in fee-earning perpetual capital to $380.1B (46% of fee-earning AUM), and record total AUM confirm the business story of scaling perpetual capital and private wealth."
    }
  ],
  "customer_demand": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::customer_demand",
      "text": "**Improving and changing mix.** Demand is strong as evidenced by record AUM of $1.26tn (Q3 2025), 8% AUM growth in 2024 to >$1.1tn, and capital deployment of $133.9B (2024) and $138.2B (2025). Notably, fundraising in the private wealth channel grew 53% to $43B in 2025, indicating a shift toward that segment."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Demand is strong, evidenced by record AUM ($1.26tn), increasing deployment ($133.9B in 2024, $138.2B in 2025), and strong fundraising, particularly in private wealth (53% growth to $43B in 2025)."
    }
  ],
  "customer_segments": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "Blackstone's buyers are primarily large institutional investors (pension funds, sovereign wealth funds) and a growing segment of private wealth clients. Named institutional clients include CPP Investments and CalPERS. Private wealth fundraising grew 53% to $43B in 2025."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Customers include large institutional investors (pension funds, sovereign wealth funds) and a growing private wealth channel."
    }
  ],
  "employee_sentiment": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Employee sentiment is moderate: Glassdoor ratings show work-life balance 3.3/5, culture 3.6/5, career opportunities 3.9/5, and 71% would recommend."
    }
  ],
  "invalidation_events": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::invalidation_events",
      "text": "Real estate fee-earning AUM declined from $299B (Q2 2024) to $285.8B (Q2 2025), indicating potential headwinds in the real estate segment, contradicting the narrative of broad-based AUM growth."
    }
  ],
  "named_customers": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::named_customers",
      "text": "Blackstone's source-backed named institutional clients include **CPP Investments** (secondary sale of private equity fund interests with Blackstone Strategic Partners) and **CalPERS** (selected BAAM as advisor for a $1B hedge fund program)."
    }
  ],
  "products_and_services": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::products_and_services",
      "text": "Blackstone's most important offerings span private equity, credit, real estate, infrastructure, secondaries, and hedge fund advisory (BAAM). Private credit has been highlighted as a particular growth area, with the firm's private credit business described as a 'juggernaut.' Additionally, the private wealth channel is a key growth platform, with fee-earning perpetual capital AUM reaching $380.1B, representing 46% of fee-earning AUM."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Blackstone operates in Private Equity, Real Estate, Credit & Insurance, and Multi-Asset segments. It also has a real estate debt platform (BREDS) including BXMT."
    }
  ],
  "workforce_and_hiring": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::workforce_and_hiring",
      "text": "Headcount is expanding. As of 2025, Blackstone Inc. had 9,900 employees, a 7.1% increase from 9,220 in 2024, based on Revelio Labs data. The Blackstone Consulting segment also saw a 4.3% rise to 998 employees. No evidence of shrinking or shifting."
    }
  ]
}

### MAIN — Main Street Capital Corporation

Status: `ready` / `medium`

Coverage: `{"searched_needs_synthesis": 4, "supported": 12}`

Dossier: `16` source lead(s), `7` finding(s)

Source scopes: `{"primary_company": 16}`

Source statuses: `{"open": 16}`

Source intents: `company_filing, credible_news, earnings_transcript, employee_reviews, hiring_page, investor_presentation, policy_regulatory, web_traffic`

Warnings:

[
  "aql_zopedia_slot_unresolved::invalidation_events::No evidence of dividend cuts, credit losses, or other negative events in provided sources",
  "aql_zopedia_slot_unresolved::workforce_and_hiring::No recent employee count or headcount trend data from official filings., No earnings call transcripts or investor presentations discussin...",
  "aql_zopedia_slot_unresolved::employee_sentiment::Larger sample of employee reviews, Recent review trends, Data from multiple platforms (Indeed, Blind, etc.)",
  "aql_zopedia_slot_unresolved::web_or_developer_attention::Web traffic analytics for mainstcapital.com, Search volume trends (Google Trends, etc.) for Main Street Capital or MAIN ticker, Developer..."
]

Gaps:

[
  "workforce_and_hiring",
  "employee_sentiment",
  "web_or_developer_attention",
  "invalidation_events"
]

Story:

Main Street Capital Corporation (MAIN) is a business development company (BDC) that provides private credit and private equity capital to lower middle market companies. With $9.2 billion in capital under management, it generates income primarily from interest on debt investments and dividends from equity positions. The company's strategy involves actively investing as an equity owner in most portfolio companies, differentiating it from passive BDCs. Recent financial results show stable distributable net investment income of $4.21 per share for 2025, exceeding consensus, and consistent origination activity ($123.4 million in Q4 2024). However, detailed data on specific portfolio companies, workforce trends, and the policy environment are absent, limiting full business model validation. The overall business appears stable with confirmed earnings, but gaps in customer names, hiring, and web attention remain unverified.

Facts:

{
  "backlog_or_rpo": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::backlog_or_rpo",
      "text": "Backlog, RPO, bookings, or contracted demand is not explicitly disclosed. The total private loan portfolio investments of $149.1M (Q1 2026) and $40.5M historical investment (2013) are proxies but do not constitute a defined backlog or RPO."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Total private loan portfolio investments reported at $149.1 million (as of a press release snippet). Origination activity in Q4 2024 was $123.4 million. No formal backlog or RPO metric provided."
    }
  ],
  "business_model": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Main Street Capital is a BDC that invests in lower and middle market private companies, often taking equity positions, distinguishing it from pure debt BDCs."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::business_model",
      "text": "Main Street Capital is a Business Development Company (BDC) that provides debt and equity capital to lower middle market companies. Its revenue is generated primarily from interest income on debt investments, dividend income from equity positions, and capital gains from exits. The firm distinguishes itself by actively taking equity stakes in portfolio companies, often through minority recapitalizations."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Main Street Capital is a BDC that provides private debt and private equity financing to lower middle market companies, with a unique strategy of actively investing as an equity owner in most portfolio companies."
    }
  ],
  "cash_and_runway": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::cash_and_runway",
      "text": "Historical filings from 2016 and 2019 show outstanding debt of approximately $120 million, but no recent cash, credit facility, or liquidity data is available. Current capacity to fund the plan cannot be determined."
    }
  ],
  "confirmation_events": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Q3 2025 preliminary NII $0.95-$0.99/share; FY2025 distributable NII $4.21/share beat consensus; cumulative AUM $9.2B."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::confirmation_events",
      "text": "Recent events confirming the business story include: (1) Preliminary Q3 2025 net investment income (NII) estimated at $0.95\u2013$0.99 per share (source: Main Street press release, Oct 14, 2025); (2) Full-year 2025 distributable NII of $4.21 per share, beating the Zacks Consensus Estimate of $4.19 (source: Yahoo Finance, Feb 27, 2026); (3) Cumulative AUM reached $9.2B as of early 2026 (source: Main Street financial results page). These results affirm Main Street's ability to generate consistent NII growth and asset accumulation, reinforcing its BDC business model."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "Preliminary Q3 2025 NII estimate of $0.95-$0.99 per share and full-year 2025 DNII of $4.21 per share beating consensus confirm consistent earnings performance."
    }
  ],
  "customer_demand": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Q4 2024 new/increased commitments of $123.4M; target companies with $10M-$150M revenue."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "In Q4 2024, MAIN originated $123.4 million in new or increased commitments in its private loan portfolio, indicating steady demand for financing. Preliminary Q3 2025 NII estimate of $0.95-$0.99 per share suggests stable income generation."
    }
  ],
  "customer_segments": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::research_dossier::company-business-4co-new-design-20260525-v3",
      "text": "Generalist investor across industries/geographies; portfolio includes companies such as Affiliati, Batjer, etc."
    },
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::customer_segments",
      "text": "Main Street Capital Corporation's customers are lower and middle market private companies across numerous industries and geographic regions. The company invests debt and equity capital in these firms, with a target revenue range of $10M-$150M. Specific portfolio companies include Affiliati, Batjer & Associates, Blackhawk Datacom, Boccella Precast, Bolder Panther Group, Brewer Crane, CBT Nuggets, and California Splendor."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Targets lower middle market private companies, typically with EBITDA between $10 million and $150 million (inferred from typical BDC focus, not explicitly stated in evidence)."
    }
  ],
  "execution_risks": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::execution_risks",
      "text": "Main Street Capital's business plan could be broken by a significant deterioration in credit quality, as evidenced by the increase in non-accrual investments from 0.6% at YE23 to 2.1% at June 30, 2025. Additionally, although the portfolio has floating rate exposure with minimum floors, a prolonged low-rate environment could compress net interest income. Other potential but unverified risks include use, dividend coverage, and portfolio concentration."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Risks include credit risk from portfolio companies and potential liquidity constraints, as highlighted in 10-K disclosures about credit risk in excess of balance sheet amounts."
    }
  ],
  "fundamentals": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent",
      "text": "2025 distributable net investment income (DNII) was $4.21 per share, beating the Zacks Consensus Estimate of $4.19. Capital under management is $9.2B, with cumulative dividends of $50.11 per share and 178 cumulative investments."
    }
  ],
  "named_customers": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::named_customers",
      "text": "Source-backed named end markets (portfolio companies) include: American Shooting Centers, CBT Nuggets, California Splendor, Cody Pools, DMA Industries, Charps, Gulf Energy Information, Houston Plating &..., Affiliati, Batjer & Associates, Blackhawk Datacom, Boccella Precast, Bolder Panther Group, Brewer Crane, as listed on Main Street Capital's official website."
    }
  ],
  "policy_or_regulatory_environment": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::policy_or_regulatory_environment",
      "text": "The policy and regulatory backdrop for Main Street Capital, as a BDC and RIC, is established and generally supportive, with requirements such as distributing 90% of taxable income to maintain tax-advantaged status. However, no evidence of recent specific regulatory changes (e.g., BDC use limits or tax reforms) that could alter the backdrop was found in the provided sources. The regulatory environment appears neutral to supportive based on existing structure, but lacks coverage of recent policy shifts."
    }
  ],
  "products_and_services": [
    {
      "confidence": "high",
      "source": "aql_zopedia_agent::products_and_services",
      "text": "Main Street Capital's most important offerings are its **customized long-term debt and equity capital solutions** for lower middle market companies, including first-lien, second-lien, mezzanine debt, and equity co-investments. Its **Private Credit Group** provides debt capital for sponsor-backed companies. The firm differentiates by acting as a principal investor with an active equity ownership model, providing a 'one-stop' platform that combines private credit and private equity capabilities."
    },
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent",
      "text": "Products include Lower Middle Market Solutions and Private Credit Solutions, offering structured debt and equity capital to portfolio companies."
    }
  ],
  "supply_chain_or_capacity_constraints": [
    {
      "confidence": "medium",
      "source": "aql_zopedia_agent::supply_chain_or_capacity_constraints",
      "text": "For Main Street Capital, a BDC, capacity constraints primarily relate to capital availability. The company has demonstrated ongoing access to debt and equity capital markets through recent note issuances (e.g., $200M and $350M offerings) and equity growth. No evidence suggests material constraints on raising capital, sourcing investments, or delivering financing. However, current liquidity, undrawn credit facility capacity, and investment pipeline details are not available in the provided evidence."
    }
  ]
}
