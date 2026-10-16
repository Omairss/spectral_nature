# Audit of missed sustained stock declines

Date: 2026-10-07. Scope: production discovery from July 1 through October 7, covering Home, Market Explorer, scheduled attention research, Trading Agent, source acquisition, and publication. This is an audit and proposed architecture change; runtime behavior was not changed.

Design follow-up: the [LLM discovery redesign](../../../../output/pdf/SPECTRAL_NATURE_LLM_DISCOVERY_REDESIGN_2026-10-07.pdf) incorporates the owner's subsequent corrections. DeepSeek is restored. Current residuals do not establish why the original onset was missed; that needs historical reconstruction. Identity context should help LLM inference, not require exact issuer details in each article or complete provenance. Financial pattern names are descriptions, not accepted detection rules. Market Explorer's ranked Market view is truncated, while Broad Markets already exposes the full universe. These clarifications supersede any stricter interpretation of the proposed repair below.

## Assessment

**There is an architectural gap, compounded by a major publication outage and several recall and precision failures.** The system already has multi-horizon returns and drawdowns. It does not reliably turn sustained company deterioration or a broad industry decline into a persistent research priority. Its main discovery paths favor daily movers and deviations from existing trends and peers. Those are different questions from “which important businesses or industries have been falling for months?”

The largest historical failure was operational: the Home job recorded 310 failures and only 13 successes after July 1. Of the failures, 309 explicitly report an exhausted DeepSeek balance. The architecture amplified that outage: a narrative quality review gates publication of the numerical Market Opportunity dataset as well as Home stories. Trading Agent then consumed stale opportunities and repeatedly attempted the same companies.

Changing a threshold or upgrading the model alone would leave these failures intact. The repair needs independent observation publication, discovery across companies and cohorts, persistent research priorities, and stronger identity and freshness contracts inside the existing AQL/Zopedia architecture.

## Evidence and boundaries

The [captured evidence](evidence/DECLINE_DISCOVERY_AUDIT_2026-10-07.json) contains dataset versions, job counts and errors, current Azure configuration, target ranks, historical coverage, price calculations, and the offline Trading Agent selection replay.

The read-only audit examined 664 retained versions: 14 each of Home, daily attention candidates, and attention research plans; 267 Trading Agent candidate versions; 213 research-package versions; and 71 daily versions each of universe and momentum snapshots. It also inspected current expectations, anomalies, news, filings, taxonomy, technicals, business memory, and Azure execution status. Production names are unprefixed; development datasets were excluded from target histories.

These findings establish what scheduled, retained discovery produced. They do not reconstruct every user's screen, historical on-demand research, or user-initiated chat. Source references describe the current working tree; the captured deployed image digests differ between jobs. Historical claims below come from retained artifacts and job records rather than an assumption that today's source ran unchanged throughout the period.

### Price movement and discovery results

Latest momentum snapshot: October 7 at 20:38 UTC. Returns below are the app's stored values, not a new external price series.

| Company | Stored 1 month return | Stored 3 month return | Market Explorer 3 month rank | Trading Agent 3 month rank | Daily universe presence |
|---|---:|---:|---:|---:|---:|
| Intuit | −5.3% | +8.1% | 616 | 252 | 71/71 |
| Nike | −8.0% | −21.8% | 62 | 214 | 71/71 |
| McDonald's | −9.0% | −15.3% | 328 | 580 | 71/71 |
| D.R. Horton | −4.1% | −11.8% | 563 | 447 | 71/71 |
| Lennar | −5.1% | −9.1% | 872 | 129 | 69/71 |
| PulteGroup | −5.4% | −9.8% | 508 | 684 | 61/71 |
| Toll Brothers | −1.0% | −10.4% | 273 | 82 | 58/71 |

**Intuit requires a different description.** Its trailing three-month return is positive because that window starts after an earlier selloff. The retained daily history shows it down 19.7% from its August 24 closing peak, down 27.0% from April 7, and down 52.3% from January 2. A trailing-return-only detector misses the recent drawdown inside the recovery. The relevant observation is a failed recovery and renewed drawdown, not a negative three-month return.

Across all 14 retained Home snapshots, none of these seven companies had a dedicated public card or appeared as a linked supporting ticker in the current narrative-beat projection. None was the primary subject of an attention research plan, a Trading Agent research package, or a Trading Agent candidate in the audited versions. The rendered market summaries also contained no explicit mention of these companies. There was limited adjacent coverage: an October 5 Millrose card discussed Lennar's difficulty selling homes. This was not complete silence about housing, but it did not surface the sustained homebuilder decline as its own research subject.

There were four daily-candidate appearances: McDonald's on July 3 UTC, Nike on October 2, and Intuit twice on October 7. Their ranks were 22, 32, 66, and 59 respectively. McDonald's appearance was a positive daily move. None entered the retained primary research plans.

## Findings

### 1 Publication failed for long periods

Recorded production attempts after July 1:

| Producer | Succeeded | Failed | Still recorded as running |
|---|---:|---:|---:|
| Home | 13 | 310 | 3 |
| Trading Agent | 65 | 202 | 0 |
| Equities preload | 192 | 0 | 7 |
| News and features | 156 | 2 | 172 |

These count recorded attempts, including retries and manual executions, rather than unique market sessions. Fourteen ready Home versions were published; that differs from the success count because artifact publication and terminal job status are separate events. The ready Home history has 38.6-day and 36.4-day gaps between successive versions.

Home's failures include 309 provider-balance errors and one October 7 quality-review timeout. All 202 Trading Agent failures report that no horizon synthesis succeeded. Its research packages make the underlying cause more specific: 1,616 package failures report the same provider-balance problem. The historical balance failure is not evidence that the provider is currently unavailable: October runs have succeeded.

The critical dependency is visible in [Home production](../../../../pipeline/jobs/attention_home_build.py): `_review_home_public_surface` raises before `_materialize_market_opportunity_feed` runs, and `run_attention_home_build` persists the returned frames only after that work completes. **An LLM review failure therefore freezes numerical discovery even while equities ingestion keeps working.** The independent ticker snapshot producer added recently does not remove this dependency for Market Opportunity.

A September 18 Trading Agent package retained opportunities dated August 14. The same small group—principally LUNR, LQDA, TWST, OSCR, NU, OKTA, VIK, and FPS—was attempted roughly 200 times each. This is a concrete stale-input and repeated-work failure, not a hypothetical consequence of the dependency.

System-health monitoring already exists and began recording incidents on September 19. Its retained Home and Trading Agent incidents are now resolved. The appropriate change is to extend its existing ownership and effective artifact freshness checks, not create another monitoring service.

### 2 Sustained deterioration is not the detector's objective

[Expectation construction and anomaly filtering](../../../../compute/anomalies.py) blend the stock's existing trend, peer returns, and benchmark behavior, then filter on the cross-sectional residual z-score. A broad industry decline can be explained away by the declining industry itself. A long-running downtrend can become its own expected behavior.

Current three-month examples from `price_expectations`:

| Company | Observed return | Peer expected return | Blended expected return | Residual z-score |
|---|---:|---:|---:|---:|
| Nike | −18.78% | −22.73% | −15.66% | −0.16 |
| McDonald's | −15.92% | +13.85% | −1.11% | −0.99 |
| D.R. Horton | −10.61% | −8.86% | −6.67% | −0.22 |
| Lennar | −8.62% | −9.36% | −7.36% | −0.03 |
| PulteGroup | −9.01% | −9.26% | −6.65% | −0.11 |
| Toll Brothers | −9.60% | −9.11% | −4.89% | −0.27 |

Every row is marked `resolved` in the current anomaly-candidate dataset and absent from qualifying anomaly events. Here `resolved` means it is below the anomaly threshold; it does not mean the business concern or drawdown has recovered. The default qualification threshold is an absolute z-score of 2.

The normalization also compares very different stocks in a heavy-tailed distribution. Current one-year residual standard deviation is 54.1 percentage points; Intuit's observed one-year loss of 55.1% becomes a residual z-score of only −1.02. More robust normalization may help, but cannot replace an explicit question about economically meaningful deterioration.

Multi-horizon data is present. `_taxonomy_horizon_trends` in [the clusterer](../../../../services/aql/clusterer.py) groups already-filtered attention rows and restricts them to daily candidate symbols. It does not independently discover the decline of the whole homebuilding cohort. The current public Home DTO also omits this trend structure. The older attention guideline's strict “today only” description therefore does not describe the complete implemented system.

### 3 Early selection prevents the agent from seeing the problem

[Daily candidate selection](../../../../services/attention_home_1d.py) uses a $25 million dollar-volume gate, macro-role exceptions, and bounded daily macro/loser/gainer lists before final scoring. It appends only the top 25 existing attention rows. Production is configured for `legacy` scoring; calculating anomaly fields does not mean the alternate anomaly mode controls selection.

Lennar, PulteGroup, and Toll Brothers have current universe dollar volumes below that $25 million gate. These volumes use IEX snapshots, not consolidated market-wide trading volume. Consequently, a company can be present in the 1,000-name price universe yet excluded from normal daily attention eligibility. Other paths can admit it, but there is no independent sustained-decline eligibility path.

Production researches the first 12 scored candidates. The [AQL pipeline](../../../../services/aql/pipeline.py) explicitly takes `candidates.head(research_limit)`. A model cannot recover a missed business from a research request it never receives. Search backfill can enrich shortlisted names, but does not give an excluded stock a persistent place in the research queue.

The macro exception is also too broad: any macro role tag, defensive role, rates role, or commodity role makes a stock a “macro anchor.” Current coverage calls 563 of 999 movers macro anchors; 51 of 76 daily candidates carry that label. Examples include software, semiconductor, and industrial companies with general exposure tags. An exposure to an economic theme and an instrument that measures that theme need separate roles. Their conflation changes eligibility, ranking bonuses, and presentation.

### 4 Housing coverage is unstable

[Universe construction](../../../../services/universe.py) rebuilds a liquidity-ranked, bounded universe. No stable industry-coverage obligation protects the members needed to recognize a continuing sector problem.

In the 71 daily snapshots, KB Home appears on only four dates and Meritage on six. Opendoor appears on 34, and Builders FirstSource on 64; neither is in the current universe. Lennar, PulteGroup, and Toll Brothers also disappear on some dates. Being present in the taxonomy does not make a company part of price ingestion or discovery.

The issue is broader than adding these tickers. Discovery needs a declared coverage policy for companies, peer cohorts, and relevant exposures, plus continuity for an active research subject when its daily liquidity rank changes. A permanent ticker exception would conceal the same failure in the next industry.

### 5 Source acquisition reinforces the selection bias

The current [news job](../../../../pipeline/jobs/main.py), `run_news`, requests news for the first 50 universe symbols and at most 50 articles per call. The live job has no explicit universe override. All seven core examples fall outside today's first 50. Filing context is separately limited to 20 attention-seed rows in production. This makes both acquisition paths depend on earlier market selection rather than independently discovering important business changes.

In the latest captured source snapshots, none of the seven has retained filing context. Six have no news article; Nike has one broad options-activity roundup. This does not establish that relevant news was unavailable on the web. It establishes that the scheduled current source set was inadequate for these companies.

The latest business-resolution and business-stack snapshots are dated September 18. Azure reports the five latest scheduled news executions as failed, while application metadata leaves many corresponding attempts `Running` at business resolution. Their exact platform termination cause still needs log inspection; the state disagreement and stale business artifacts are established. Existing health checks focus on Home and Trading Agent and do not fully cover this stalled producer.

This creates a reinforcing loop: low initial rank produces little research and source coverage; weak evidence then makes the stock less competitive for further attention.

### 6 A generic combined rank does not guarantee useful recall

[Market Opportunity](../../../../services/market_opportunity.py) combines absolute horizon return, absolute momentum change, absolute daily move, and trend consistency. Large gains and losses compete for the same places. Persistent weakness can rank below stocks that are more extreme on other measurements.

The current Next.js [Market Explorer](../../../../web/app/market-explorer/page.tsx) requests 80 ranked rows, then sorts those returned rows by descending horizon return. Sorting afterward cannot recover omitted stocks. Nike currently makes the 80-row set, so its data is discoverable there; the other six examples do not. Nike's eligibility is not evidence that its decline received research or prominence.

[Trading Agent](../../../../services/trading_agent.py) scouts six stocks per horizon and selects eight unique companies across five horizons. The newer [all-signal ranker](../../../../services/all_signal_ranker.py) includes technical drawdowns and fundamental inputs, so it would be incorrect to say those features are absent. But it still reduces all objectives to one combined rank. Its peer-relative, two-sided normalization can make shared industry weakness unexceptional, and horizon-independent source frames contribute multiple horizon metrics together.

An offline replay of the captured ranked dataset reproduced all eight retained research subjects exactly. None of the seven examples entered the pool. This locates the present failure before company research and final synthesis. More final-answer precision cannot repair this recall loss.

### 7 Identity and measurement errors also reduce precision

The latest public Home artifact gives `NIB` a story about Australian health insurer nib Holdings. The application's [commodity registry](../../../../services/market.py) identifies this input as a cocoa instrument. Research resolved an ambiguous ticker to a different entity, and public quality review still accepted the card. Another card for BNO describes a historical option-contract price page as the development worth attention. These are concrete precision failures consuming scarce public space; their exact contribution to any particular omitted stock cannot be quantified from the retained outputs.

The source contract needs instrument identity, venue, asset type, and company or fund name carried through retrieval and synthesis. This belongs at the shared acquisition and research boundary, not in a ticker-specific prompt patch.

The numerical horizons also disagree. [Momentum](../../../../services/market.py) uses `window_return_pct(close, 63)`, which measures 62 intervals; [technicals](../../../../compute/signals.py) use `pct_change(63)`, which measures 63. On the same retained Nike bars those produce −21.76% and −18.78%. The off-by-one explains the difference between the two tables above. It is not the main reason for omission—both show material weakness—but a shared observation contract must define dates, periods, adjustment, and partial-session status consistently.

## Precision and recall diagnosis

| Layer | Diagnosis |
|---|---|
| Publication | Severe historical availability failure; model failure freezes unrelated numerical output. |
| Universe | Recall loss and continuity gap, especially for smaller housing names. |
| Observation | Long-horizon data exists; peak drawdown and horizon definitions are not consistently used by discovery. |
| Discovery | Architectural objective mismatch: residual surprise is not sustained deterioration or industry weakness. |
| Research selection | Recall loss before the LLM; constrained pools repeatedly select from narrow or stale inputs. |
| Research and publication quality | Confirmed precision failures in instrument identity and source relevance. |
| Presentation | Truncation and descending return ordering reduce prominence; Home does not expose independent ongoing concerns. |
| Measurement | No comprehensive durable record of why each eligible concern was researched, deferred, rejected, or published. |

An overall precision or recall percentage cannot be inferred from this deliberately selected set of misses. The audit demonstrates specific stage failures; it is not a representative labeled sample of all market developments.

## Proposed repair at the existing owners

### First make observations and producer health independent

Publish Market Opportunity and other numerical observations from the existing equities production boundary, before narrative research. Keep Home stories subject to evidence and quality review. Attach exact source versions and observation dates to both; stop downstream “current” research when its seed is stale rather than relabeling old opportunities with today's job time.

Extend the existing system-health job to cover news/business-memory production, effective observation freshness, and application-versus-platform terminal-state disagreement. Inspect the stalled news executions and provider failures before increasing research budgets. Billing recovery alone is insufficient because the same publication dependency would amplify the next provider outage.

### Give discovery the missing questions

Within the existing attention/AQL production flow, provide shared observations for companies and structured cohorts: rolling return, drawdown from a dated peak, duration, market and peer-relative performance, deterioration in business evidence, and industry breadth. Preserve daily surprise as one useful reason to investigate alongside sustained decline, failed recovery, and coherent sector deterioration.

Let AQL/Zopedia judge business importance, coherence, explanations, contradictions, and research priority using that context. Numerical observation and time/version integrity belong in code. Do not introduce hardcoded ticker lists, keyword causes, stock-specific thresholds, a new private agent, or a requirement to invent a same-day explanation for a months-long development.

Cohort discovery must happen before subtracting peers or restricting members to daily movers. For housing, that means the system can observe broad homebuilder weakness and investigate rates, affordability, incentives, orders, margins, suppliers, and financing together. The same design should apply to restaurants, apparel, software, or another future cohort without a new feature branch.

### Preserve research priorities and coverage over time

Use the existing retained attention/research contracts to preserve an active company or cohort concern, its observation dates, evidence, last research/publication, and what would resolve it. Budget selection should account for importance, deterioration, incomplete coverage, and prior work, rather than repeatedly taking the highest combined scores. Track concrete deferral and rejection reasons so the next audit can distinguish missed coverage from a deliberate decision.

Maintain a stable coverage obligation for the chosen research universe and its cohorts. Daily liquidity can constrain execution and refresh cost without silently deleting a continuing research subject. Discovery from filings and business events must also be able to create a research priority independently of a price shortlist.

### Correct shared identity and presentation contracts

Distinguish economic exposures from actual macro measuring instruments. Carry instrument identity through every search, source, evidence, and narrative artifact. Resolve numerical horizons through one dated observation contract. Expose meaningful ongoing company and industry concerns through shared Home/Market/API contracts; avoid trying to squeeze them into a daily mover sentence.

The existing combined ranks can allocate some research capacity, but should not be the sole eligibility rule for every discovery question. Market Explorer needs access to the full eligible downside and upside observations before presentation limits are applied.

## How to evaluate the repair

Replay discovery at historical observation dates, using only material then available. Read the resulting candidate choices, retained sources, company and cohort analyses, deferrals, and public outputs personally. Confirm that the research explains why the development matters and distinguishes price weakness from business deterioration, with dated evidence and counterevidence.

Use these cases as diagnosis examples, then review additional unseen companies, common industry declines, recovering businesses, volatile small stocks, and stocks with positive trailing returns but material peak drawdowns. Assess irrelevant promotions and missed important developments together. Do not optimize fixed ticker inclusion tests or phrase checks.

Separately verify the operational contract: a model outage must leave numerical observations current, narratives accurately dated, stale research seeds rejected, and affected producers visible in existing health state. This verifies availability and data integrity rather than claiming to prove qualitative research quality.

## External business evidence to anchor that review

These primary releases establish that substantive business evidence was available. They support research questions and company facts; they do not by themselves prove the cause of every day in the stock decline.

- Nike's October 1 release reports weakness in Direct and Greater China and guides fiscal 2027 revenue to a high-single-digit decline. [Nike fiscal first quarter results](https://investors.nike.com/investors/news-events-and-reports/investor-news/investor-news-details/2026/NIKE-Inc--Reports-Fiscal-2027-First-Quarter-Results/default.aspx).
- McDonald's August 4 release reports U.S. comparable sales growth of 0.8%, global comparable sales growth of 1.3%, and a new U.S. president. [McDonald's second quarter results](https://corporate.mcdonalds.com/corpmcd/our-stories/article/Q2-2026-results.html).
- Lennar's September 16 release reports orders down 9%, home-sales gross margin of 15.8% versus 17.5% a year earlier, incentives around 12%, and reduced delivery guidance. Management explicitly connects its outlook with interest-rate and market pressure. [Lennar third quarter results](https://investors.lennar.com/press-releases/2026/09-16-2026-213038087).
- Intuit's August 25 release reports fiscal 2026 revenue growth of 14%, fiscal 2027 revenue guidance of 9–10%, and weak Mailchimp guidance. It also changes non-GAAP treatment of share-based compensation, so EPS comparisons require care. This is a mixed business picture that needs investigation rather than a blanket claim of collapse. [Intuit fiscal year results and guidance](https://investors.intuit.com/news-events/press-releases/detail/1320/intuit-reports-fourth-quarter-and-full-year-fiscal-2026-results-sets-fiscal-2027-guidance).

No application implementation or deployment was performed in this audit. The outstanding decision is the discovery and publication architecture described above, not a per-company patch.
