# Hierarchical summarization and agentic context

[Master research index](../README.md) · [Observed cases](FAILURE_CASES_2026-10-15.md) · [Complete retained evidence](evidence/2026-10-15/README.md)

Status, October 16, 2026: **open research; prototype not enabled or scaled**. The question is whether an agent can use cached summaries as optional navigation while retaining the analytical value of complete originals. The October 15 results reject this particular candidate for defaults; they do not demonstrate that hierarchical summarization is generally ineffective.

## What we actually tried

The shared AQL/Zopedia engine built per-source summaries and, for multi-source corpora, an overview. Originals and intermediate summaries remained callable. The six corpora covered manufacturer records, transport documentation, database documentation, a dated earnings release, a dated monetary-policy statement and a complete research paper. The longest original was about 79,000 characters. This was not a million-token test or an implementation of RAPTOR clustering.

All routes used DeepSeek Flash, thinking disabled, a 4,096-token output allowance and no review cycles in either arm. The final comparison used one frozen 159-file source snapshot. The application’s normal configured research flow was not the control.

| Final batch | Answers | Known estimated answering cost |
| --- | ---: | ---: |
| Full-source controls | 14 | $0.06308 |
| Basic navigable hierarchy | 14 | $0.03638 |
| Initial navigation guidance | 4 | $0.01642 |
| Catalog-first, planner-visible guidance | 6 | $0.01842 |

The final answers cost $0.13429 across 127 HTTP attempts. Eleven reused summary builds cost another $0.00874. The **entire investigation** cost $0.37509 known, plus one unfinished attempt with unknown charge; it includes earlier exploratory and invalid-protocol work. The answering reduction does not establish an acceptable quality/cost tradeoff or a saving for the production workload. Provider caches were warm in places, and summaries were reused.

## What remains unresolved

1. **Analytical relationships can disappear while facts survive.** The manufacturer summary retained the necessary numbers, but answers omitted operating profit and free-cash-flow calculations. This is not explained solely by loss of source text.
2. **Access is different from effective use.** Making the catalog visible caused some original reads. It did not consistently make final analysis revise an earlier summary-derived claim.
3. **Compression can improve salience.** The earnings summary surfaced a cash-flow statement the full-source control overlooked. The resulting answer still overreached on attribution. A useful design must preserve that coverage benefit while improving inference.
4. **Several errors are shared reasoning failures.** Both representations overread transport observations and mishandled a currency-growth comparison. Charging all errors to summarization would misdiagnose them.
5. **Exceptions are vulnerable.** Paper summaries flattened mixed layer-ablation results into a broad full-tree advantage. A later original read corrected some requested values but left other earlier errors in the answer.

The [case catalog](FAILURE_CASES_2026-10-15.md) preserves the observations and their limits. No case-specific answer rule follows from it.

## Questions for the next cheap experiment

| Question | Comparison that would help answer it |
| --- | --- |
| Where is a relationship lost: summary, retrieval choice, analytical handoff or final revision? | Trace the same unfamiliar task through original text, derived view, chosen reads and final answer. Compare whether a consequential relationship is present at each boundary. |
| Does original evidence change an anchored answer? | Supply an original containing a missing calculation, qualification or counterexample after an initial view. Review the complete revision, including new errors and useful retained hypotheses. |
| Is a failure specific to the cheap configuration? | Compare the candidate and full-source control under the same normal configured flow as well as the original cheap setting. Change one mechanism at a time. |
| Can summaries preserve conflicting or updated evidence without forcing a fixed template? | Use unfamiliar multi-document and dated-update cases. Examine whether distinctions survive reuse and revision. |
| When is compression worth its cost? | Count construction, invalidation, reuse, original reads, repairs, review, failed work and latency across repeated questions; include small requests where a summary can expand the context. |

These are proposed investigations, not experiments already performed. Start with retained evidence and small unfamiliar cases; establish a useful quality/cost tradeoff before larger corpora. Keep summaries optional and originals addressable. Fixed depth, mandatory reads, source quotas and blanket premium routing have not been established as solutions.

The intended outcome is useful exploration and sound conclusions, with recoverable failures tolerated. A failed intermediate step should not discard a useful answer; a repeatedly missing calculation or distorted counterexample should not be hidden by successful execution.
