# Hierarchy experiment cases — October 15, 2026

[Research brief](README.md) · [Evidence guide](evidence/2026-10-15/README.md) · [Original audit](../../architecture/AQL/audits/HIERARCHICAL_SUMMARIZATION_EXPERIMENTS_2026-10-15.md)

These are qualitative observations from complete answers against retained originals. They are not expected-answer tests or a claim that 38 answers failed. The evidence archive includes all 38 final answers, including useful results and controls. Record names below identify entries in [answers.json](evidence/2026-10-15/answers.json); exact source text and generated summaries are in [inputs.json](evidence/2026-10-15/inputs.json).

## Case index

| Case | Observation | Interpretation |
| --- | --- | --- |
| [HS-01](#hs-01-manufacturer-calculations) | Necessary figures survived; central calculations disappeared. | Consequential analytical omission. |
| [HS-02](#hs-02-competing-business-explanations) | Useful alternative hypotheses in both arms; unsupported ranking in a control. | Preserve exploration; separate hypothesis from attribution. |
| [HS-03](#hs-03-transport-closure) | Keep-alive/closure observations overinterpreted in both arms. | Shared reasoning failure. |
| [HS-04](#hs-04-total-latency) | End-to-end latency correctly distinguished, then phase timing overclaimed. | Partial usefulness with shared inference errors. |
| [HS-05](#hs-05-current-tier-database-advice) | Restriction missed in an invalid-catalog run; corrected setup preserved it. | Protocol confound and genuine improvement. |
| [HS-06](#hs-06-independent-pools) | Aggregate capacity reasoning survived compression. | Useful result; some objective drift. |
| [HS-07](#hs-07-null-and-zero-minimum-pools) | Useful pool distinction, with setting-specific exceptions mishandled. | Boundary/qualification problem. |
| [HS-08](#hs-08-currency-growth-direction) | Reported versus constant-currency growth interpreted inconsistently. | Shared numerical reasoning failure. |
| [HS-09](#hs-09-cash-flow-salience-and-attribution) | Summary surfaced cash-flow data missed by the full-source control. | Coverage improvement followed by overreach. |
| [HS-10](#hs-10-dated-policy-and-conditional-outlook) | Both arms generally preserved the policy decision and conditional path. | Useful control; minor editorial issues. |
| [HS-11](#hs-11-research-paper-method-and-cost) | Broad method survived; comparator changed meaning and appendix cost was missed. | Different failures across representations. |
| [HS-12](#hs-12-layer-ablation-counterexamples) | Mixed layer results became a broad full-tree advantage. | Consequential exception loss. |
| [HS-13](#hs-13-benchmark-comparisons-and-revision) | Subset/baseline confusion; original reads corrected only part of the answer. | Retrieval and revision remain separate problems. |

## HS-01 Manufacturer calculations

Records: `financial-0-{flat,hierarchy,guided_hierarchy,adaptive}-catalog.private.json`.

The supplied table gives gross profit 36→33, operating expenses 24→27, operating cash flow 6→11 and capex 4→5. Operating profit therefore falls **12→6**, while cash after capex rises **2→6**. The full-source control derived both. Basic hierarchy and initial guidance answers reproduced the raw figures but omitted those calculations, discussing gross profit and missing net income instead. The catalog-first variant also missed the central analysis.

This is loss of analytical usefulness even with sufficient figures present. It does not establish whether the main cause is the derived framing, the analytical handoff or model variability. A future comparison should examine those boundaries on unfamiliar quantitative tasks.

## HS-02 Competing business explanations

Records: `financial-1-{flat,hierarchy,adaptive}-catalog.private.json`.

Both approaches retained useful pricing, product-mix and working-capital hypotheses. The full-source answer nevertheless ranked a one-time working-capital release as most consistent without a reconciliation that established it. Hierarchy lost the operating-profit contrast while still exploring alternatives. Preserve the useful hypotheses; assess unsupported ranking separately from missing arithmetic. More conservative wording alone would not restore the omitted analysis.

## HS-03 Transport closure

Records: `technical-0-{flat,hierarchy}-catalog.private.json`.

Both arms overread keep-alive lines as evidence of pre-inference queueing and treated the documented ten-minute no-inference close condition as an exhaustive server-close rule. The hierarchy answer also suggested a 200 response with an empty body confirmed queueing. Those observations do not establish that diagnosis. This is a shared source/causal reasoning failure, not evidence that compression uniquely caused it.

## HS-04 Total latency

Records: `technical-1-{flat,hierarchy}-catalog.private.json`.

Both answers correctly separated a 70-second end-to-end request duration from inference duration and proposed useful observations. They then overstated what first-byte/first-token timing could isolate about queueing versus computation. These measures include other phases and do not alone establish the proposed cause. The useful investigative suggestions survive; the complete causal explanation needs improvement.

## HS-05 Current-tier database advice

Final records: `database-0-{flat,hierarchy,guided_hierarchy,adaptive}-catalog.private.json`. Earlier restricted-catalog observations are retained in the original audit and private archive, outside the final 38-answer export.

An earlier restricted-summary answer proposed built-in PgBouncer despite the supplied Burstable restriction. That run omitted a shared read-context tool and is **not** a clean final matched regression. The corrected hierarchy retained the restriction and total-process pool sizing; guidance and catalog-first variants read originals. The latter still attributed idle-timeout guidance to a supplied page that did not contain it. Protocol repair improved the answer but did not settle source attribution or pool-lifecycle reasoning.

## HS-06 Independent pools

Records: `database-1-{flat,hierarchy}-catalog.private.json`.

Both recognized that four independent per-process pools could consume the stated non-reserved connection budget and leave no headroom. Compression preserved the main capacity argument. Some recommendations drifted toward a paid tier despite the question's current-tier objective. This is useful counterevidence to a blanket hierarchy-failure claim, with a remaining task-objective issue.

## HS-07 Null and zero-minimum pools

Records: `database-2-{flat,hierarchy,guided_hierarchy,adaptive}-catalog.private.json`.

The corrected setup and original reads preserved a useful distinction between `NullConnectionPool` and `ConnectionPool(min_size=0)`, including returned-connection handoff, idle retention and per-pool scope. Advice still needed care around idle expiry and the documented zero/unbounded maximum setting. A statement about a positive maximum cannot be applied to every configuration. Do not turn these exceptions into a claim that all pool advice failed.

## HS-08 Currency-growth direction

Records: `microsoft-0-{flat,hierarchy}-catalog.private.json`.

The full-source answer called 24% reported versus 22% constant-currency growth a currency drag, even after correctly stating the opposite directional rule. The hierarchy answer called the gap mostly negative before describing it as a positive contribution. Many source figures survived in both. These internally inconsistent relationships are shared reasoning errors; adding source access alone is not an established repair.

## HS-09 Cash-flow salience and attribution

Records: `microsoft-1-{flat,hierarchy,adaptive}-catalog.private.json`.

The full-source control said the release lacked a cash-flow statement even though the retained original contained it. The summary-based answer surfaced annual operating cash flow and property/equipment additions, improving coverage. It then over-attributed spending to AI, overstated self-funding and handled annual cash generation versus quarterly shareholder returns too loosely. Preserve the improved salience while investigating the unsupported causal and period comparisons.

## HS-10 Dated policy and conditional outlook

Records: `fed-{0,1}-{flat,hierarchy}-catalog.private.json`.

Both arms generally preserved the dated cut, dual-mandate risks and conditional future decisions. Hierarchy added unnecessary source-process caveats. These were broadly useful answers; editorial weakness is not a reason to reject a useful research capability. Keep these controls when evaluating later designs.

## HS-11 Research-paper method and cost

Records: `summarization-0-{flat,hierarchy}-catalog.private.json`.

Both described the broad method and need for financial-domain evaluation. The full-source answer overlooked build-time/cost information present in an appendix. Hierarchy changed a contiguous/adjacency grouping comparator into a recency-based comparator. This changes the experiment's meaning. Neither representation guarantees complete coverage, and retaining the paper is insufficient if later interpretation does not correct the mistaken comparison.

## HS-12 Layer-ablation counterexamples

Records: `summarization-1-{flat,hierarchy}-catalog.private.json`; detailed follow-ups in HS-13.

The full-source path read the original and retained cases where all-layer retrieval underperformed a narrower selection. Summary answers described a broad full-tree advantage, smoothing away the counterexamples relevant to choosing a fixed depth. This is consequential loss of a qualification. The observation supports investigating adaptive evidence use; it does not establish one universally correct retrieval depth.

## HS-13 Benchmark comparisons and revision

Records: `summarization-2-{flat,hierarchy,guided_hierarchy,adaptive}-catalog.private.json`.

Basic hierarchy omitted the requested GPT-3 DPR value and confused CoLISA overall accuracy with its hard-subset result. The original distinguishes **62.4 versus 60.4** for GPT-3 RAPTOR/DPR, and **82.6/76.2 versus 62.3/54.7** for test/hard results of GPT-4 RAPTOR and CoLISA. It also does not isolate one universal causal share of gains across these different comparisons.

Guidance triggered an original read and recovered key values, but the answer retained the wrong recency comparator and broad layer claim. The catalog-first detailed answer did not read the original and again omitted the requested baseline. Original availability, a recorded read and successful final output are distinct from meaningful correction of an earlier view.

## Protocol failures to preserve separately

- Early trials used concurrently changing source. Their observations/cost remain exploratory. A first snapshot copied no files because of a duplicated path; that empty snapshot was rejected.
- The first frozen catalog omitted `research.read_context` despite instructions referring to it. Those outputs are excluded from the final matched comparison.
- Catalog entries and appended guidance could fall beyond the planner's 4,000-character preview. The catalog-first variant changed visibility, so it is an ablation, not an identical repeat.
- One interrupted attempt has no final usage receipt. It remains unpriced; it is neither a completed answer nor known zero cost.

The next investigation should identify where consequential relationships are lost and whether original evidence actually changes final reasoning. Case-specific prompts, mandatory source reads and deterministic prose gates would not establish that improvement generalizes.
