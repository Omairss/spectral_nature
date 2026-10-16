# October 16 reasoning repair review

**The handoff changes work mechanically; the reasoning repair is unfinished. No
build, deployment or normal dev/production setting write was performed.**

The live checks corrected the original logical and numerical failures in some
runs, but later runs still omitted a necessary numerical check. Review caught
that omission, while the corrective Pro call exceeded the unchanged 90-second
step deadline. A valid counterexample also received an unnecessary review edit,
causing another paid correction that timed out. Neither passing tests nor a
completed run establishes answer quality.

## What changed

- The configured analytical model keeps ownership across tool calls and returns
  its finished answer directly. The compact analytical contract avoids repeated
  planner bookkeeping and an additional automatic synthesis.
- The existing Python tool supports computation without an uploaded dataset.
  Analysis/review receive actual bounded computation outputs as original evidence,
  including printed values; the optional index can retain its shorter view.
  Structured results are also retained in the existing result JSONB.
- Review edits live beside their checked claims. The existing overall status is
  derived from those edits, so a separate model verdict cannot drop them. Review
  uses originals without the author's extra self-justification.
- Completed review is checkpointed before correction inference. A pending
  correction resumes directly, and analytical follow-up resolves the same cycle.
  Invalid structured analytical output does not replace a valid retained draft.
- Malformed supplied-evidence containers fail at the shared engine boundary.
  The engine revision now includes the numerical tool implementation.

The unreleased default uses **Pro/high** for core analysis and deep reasoning.
Max remains configurable, and explicit saved routes are preserved. Pro/max
repeatedly exceeded the existing step limit; high completed several useful
analyses but still has correction timeouts. Flash/low remains the reviewer;
removing its thinking produced incorrect arithmetic and logic. Background
News/Knowledge Flash synthesis retains its previous max setting. Coverage,
research depth, search limits, reviewer count/cycles, runtime budgets and schedules
are unchanged.

## Personal review of complete outputs

The retained files contain actual model outputs, not expected-answer tests:
[complete answers and reviews](./case-outputs.json),
[case questions and source identities](./case-inputs.json).

| Check | Observation | Assessment |
| --- | --- | --- |
| Logistics mix, full high run | Corrected the impossible fixed-rate explanation: implied express on-time rate 109%. | The original error was corrected in this run, but not consistently across later runs. |
| Logistics mix, final batch | Pro omitted the feasibility calculation. Flash identified it; correction timed out. | Unaccepted. The useful draft and pending correction remain retained. |
| Correction-only continuation | Exactly one `agent.review_corrections` request; no acquisition or review repeated. It also timed out. | Recovery contract works; reasoning completion remains unresolved. |
| Valid patient mix, final batch | Python verified constant class rates of 88%/98% reproducing aggregate 90%/94%; Pro received the actual stdout and used it correctly. | Computation handoff works. Review requested an aggregation qualification that did not change the main conclusion and triggered another timeout. |
| Source interpretation | One full high run removed the reversed implication. Other attempts overread a pre-inference limit or inferred causes too strongly from transport observations. | Improved, insufficient evidence for reliable acceptance. |
| New replacement-policy example | Review caught registration being mistaken for approval, preserving ownership/correct-submission assumptions. | Useful independent transfer check. |
| Randomized trial and business hypothesis | Role probes retained a causal estimate with uncertainty and a useful tentative commercial connection. | Encouraging; not complete-flow acceptance. |

The numerical check is not just a generic warning about aggregation. With old
class rates `s` and `e`, `0.7s + 0.3e = 0.88`. Holding them fixed, new performance
is `0.5s + 0.5e`. Since `e <= 1`, its maximum is `0.914286`. Thus mix alone can
explain at most **3.43 points** of the observed six-point gain; at least one class
rate must change. Solving both observed totals as fixed rates gives `s = 0.79`
and `e = 1.09`, exposing the impossibility. This does not identify the new routing
model as the cause. In contrast, outpatient/inpatient rates `0.88`/`0.98` genuinely
match both totals in the patient-mix case. These are human checks of the outputs,
not production question-specific rules.

The source's 75-second setting was an observation, not an immutable service
requirement. The earlier evaluation overstated that constraint. A longer client
deadline can be legitimate when its objective and assumptions are explained.
The unambiguous logical error was reversing the documented implication from
no inference start to connection closure. Similarly, HTTPX's read timeout waits
for a data chunk; it does not distinguish blank lines from meaningful JSON.

The earlier logistics harness passed a dictionary envelope where a list of
source objects was required. It silently lost those records. The arithmetic
failure remains meaningful, but that earlier probe did not verify source transport.
This pass used the correct list and added validation before inference.

## Validation and cost

**71 mechanical checks passed** with:

```sh
.venv/bin/python -m pytest tests/test_llm_execution.py tests/test_llm_settings.py tests/test_llm_deepseek.py tests/test_zopedia_analysis.py -q
```

These protect transport, routing, persistence, numerical execution and continuation
contracts. They are not prose or reasoning acceptance tests.

The October 16 investigation made **76 HTTP attempts**: 61 successful attempts
with receipt-price estimates totaling **$0.22565–$0.45130**, plus **15 failed
attempts with unknown charge**. Counts include rejected prompt/effort prototypes,
JSON repair, timeouts, full loops and the correction-only continuation. Start and
completion events are deduplicated; reasoning tokens are already included in
completion usage. The range reflects the retained price estimates, not an invoice.
Unknown usage is not zero cost.

The correction-only continuation retained the 90-second model limit and used only
the original 300-second budget minus the preceding run's 177.61 seconds. No
research or review was repurchased. This is separate from claiming the production
workload fits **+20% overall**; that condition remains unverified.

Receipts: [attempt accounting](./attempt-costs.json).
The 41 retained experiment outputs include exploratory source/prompt variants;
they are not a matched statistical benchmark. Raw attempts, checkpoints and
original inputs remain in `/tmp/sn-reasoning-repair-20261016`.

## Source and deployment handoff

[Source identities](./source-manifest.json) distinguish the final live batch from
the latest code. After that batch, only review checkpoint timing and follow-up
bookkeeping changed; the final correction-only continuation and mechanical checks
used those changes. Do not silently treat earlier experimental variants as runs
of the latest source.

The exact continuation handoff is:
`/tmp/sn-reasoning-repair-20261016/handoff/handoff.json`.
It includes source hashes, the five-file runtime delta against this pass's entry
baseline, full test sources and remaining acceptance work. The coordination
pointer is `/tmp/sn-reliability-20261015/LLM_COST_HANDOFF.json`.

The integration agent owns deployment. The candidate remains unaccepted and must
stay out of the independent backend-reliability release. Its imported-source
snapshot is for reproduction, not an overlay to deploy. This handoff makes no
claim about independently owned backend readiness.
