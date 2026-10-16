# Configurable Zopedia/AQL flow — local acceptance

Status: implemented locally; development release held for source-reasoning
acceptance. No application build or deployment was performed for this follow-up.
The previously accepted development and production releases remain unchanged.
The governing design is the
[conversation repair plan](../../../plans/ZOPEDIA_CONVERSATION_REPAIR_2026-10-14.md).

## Implemented behavior

- One backend stage graph supplies the Admin → LLM canvas and execution routes.
  Every step has its settings, instructions and input/output handoffs. Observed
  attempts show requested/reported models, failures, unfinished work, tokens,
  request time and estimated spend. Missing usage remains unpriced.
- Pro/max owns planning, synthesis, analytical corrections and durable knowledge.
  Flash owns query planning, relevance, monitoring and reviews by default; source
  summaries, presentation and schema repair use Flash without thinking. Each
  route can be changed. Reviewer count and review cycles are independent.
- A run freezes models, prompts, workload, budgets and write policy. Nested
  helpers share the run's provider client/transport. Existing queued research keeps
  its settings after an admin save; a new submission receives the new settings.
- Originals and complete numerical observations reach synthesis, review and
  memory through one source-reference resolver. Captures retain immutable body,
  metadata and read coverage in the existing archive/corpus. Optional relevance
  and summary stages run after acquisition is checkpointed.
- Page summaries request their typed artifact from the shared lifecycle. Their
  second formatter and separate review loop were removed. Daily datapoints use
  the shared configurable artifact-review cycle.
- Server knowledge policy reaches session, worker, tools and memory. Requests
  may narrow it. Defaults remain read-only; proposals and audited direct updates
  become available when the admin explicitly configures them.

## Evidence

The full local records are in `tmp/llm-flow-20261014/`. These are different kinds
of evidence; protocol checks do not establish LLM quality.
Selected receipts and screenshots are retained under
[`documents/verification/llm-flow-2026-10-15/`](../../../verification/llm-flow-2026-10-15/).

| Check | Result |
| --- | --- |
| Actual admin GET/PATCH, full reload, desktop and 390-pixel controls | Passed in isolated settings scope `llm-flow-review-20261014`; no authentication bypass ships in application code |
| Two independent reviewers, follow-up acquisition and premium correction within one cycle | Transport check passed; no semantic-quality claim |
| Twelve retained queries, 40 results/query, configurable Pro/max relevance | Transport check passed; all candidates retained and chosen relevance timeout reached the provider |
| Summary succeeds or times out after acquisition | Both preserve the original and checkpoint it before the optional model request |
| Exact capture/version/window and lexical retrieval | Actual private PostgreSQL corpus with clearly identified injected archive transport; passed |
| Worker managed-identity archive upload/readback | Execution `sn-research-run-dev-siplxed` succeeded; image, environment and job template unchanged |
| Total HTTP deadline despite continuous whitespace | 0.402 seconds against a 0.4-second deadline; passed |
| Started and terminal attempt receipts | Separate immutable receipt pointers; protocol check passed |
| Existing settings/DeepSeek/reader checks | 26 passed |
| TypeScript and changed Python source compilation | Passed |

The archive object is
`verification/llm-flow-20261015/79c87b919af243c38fbae2ef4843b841.json`, SHA-256
`53d26216391f4ad7af83ad3374c0f99b9a3662088eb29f633b821a819cf68c23`.
This verified storage under the existing worker identity, not a new application
release or an end-to-end capture with the unreleased code.

## Personal qualitative review

Accepted numerical examples use supplied fictional records, not verified company
filings. Pro correctly distinguished revenue, gross profit, margin and cash-flow
changes; cheap critics found seeded units/causality errors and Pro corrected them.

The actual queued cash-flow answer used six HTTP attempts: Pro researcher,
Pro synthesis, two parallel Flash critics, Pro correction and Flash presentation.
It correctly calculated gross profit 80 → 72 on revenue 200 → 240; removing the
supplied working-capital releases left operating cash flow unchanged at 45 and
free cash flow falling 15 → 10. Requested and reported model IDs agreed. Known
estimated charge was $0.02075–$0.04151, with no unpriced attempts.

A typed background page summary completed in 77.6 seconds with three HTTP
attempts. It preserved the supplied observations, calculated gross profit
52.8 → 54 despite revenue growth, and reconciled the cash-flow bridge without
inventing its components. Both reviewers accepted the premium draft; an incorrect
causal suggestion from one critic did not replace it. Known estimated charge was
$0.01031–$0.02063, with no unpriced attempts.

After the reviewer-role prompt cleanup, an unseen conflicting-records example
also passed in 61.0 seconds under the normal 90-second model limit. One Flash
reviewer and premium correction replaced a deliberately flawed supplied artifact.
The answer calculated operating profit 12 → 6 despite revenue 84 → 96, free cash
flow 2 → 6, and rejected the analyst memo's unsupported claim that collections
proved sustainable earnings improvement. Its follow-ups targeted the cash-flow
reconciliation and margin drivers. This does not resolve the technical case below.

The technical source case is **not accepted**:

| Evaluation | Elapsed | Finding |
| --- | ---: | --- |
| Original acquisition and continuation | 260.9 + 98.5 seconds | Sources survived, but continuation confused observed traffic with the underlying cause |
| Supplied-source evaluation with 180-second whole-run limit | 184.6 seconds | Valid draft survived a correction timeout; answer treated a documented waiting limit as a client target |
| Same source question, normal 300-second run/90-second model limit | 95.7 seconds | Synthesis timed out; no answer |
| Evaluation-only 180-second model limit | 258.0 seconds | Completed, but asserted that late JSON establishes slow generation and a close near ten minutes establishes inference never started |
| Review replay after removing answer-rewriting instructions from critics | 93.4 seconds | Completed, but retained causal overclaims and recommended an unbounded diagnostic probe; rejected |

The premium correction already contained those overclaims; presentation did not
introduce them. Both originals were available, so this is not established as a
missing-evidence handoff failure. The reviewer prompt cleanup aligns ownership
but has not solved this reasoning case. Do not add technical-question phrase
rules, source-count gates or another mandatory review to make this example pass.

The named source evaluations above used 27 unique HTTP attempts across five run
identities, including recovery: known estimated charge $0.11200–$0.22401 plus
three attempts without usage. The original and its continuation share one run's
13-attempt ledger and are counted once. These figures exclude separate provider
boundary probes and are estimates, not billing totals. No cost saving or overall
research-quality improvement has been established by these samples.

## Cost-constrained reasoning follow-up — October 15

The owner clarified that the allowance is up to +20% combined-workload LLM cost;
individual difficult runs may cost more. An overall +50% increase is rejected. The subsequent
[investigation](../../../plans/ZOPEDIA_REASONING_QUALITY_COST_2026-10-15.md)
has not produced an accepted quality/cost candidate. Higher-priced or
higher-effort review did not solve the technical case. Explicit assertion/basis
preparation let Flash catch the original errors, but Pro's resolution then
questioned an explicit user premise and retained a telemetry overclaim.

The prepared-review tail projected −3.4% on the measured ordinary research
flow but +45.7% on a replay sharing the same retained initial direct analysis.
The former combines stages from different drafts; neither is a fresh matched
candidate whole run. The individual +45.7% increase does not itself breach the
clarified allowance. The quality failure and absent weighted workload comparison
still prevent adoption. The next candidate
improves task-premise provenance and feedback inside the existing calls, followed
by unfamiliar direct-query and research comparisons. Defaults and deployments
remain unchanged. Retained [outputs and attempt receipts](../../../verification/llm-flow-2026-10-15/quality-cost/README.md)
keep this investigation separate from the earlier 27-attempt source ledger.

## Continue from here

1. Preserve the implementation and all rejected outputs. Decide how the premium
   synthesis should assess original facts and critic proposals without adopting
   unsupported diagnoses. Evaluate on unrelated questions and retained company/
   event inputs too; do not tune only the timeout example. A broader retained
   research acceptance is still outstanding.
2. The optional timeout preference question is unanswered. Defaults remain
   90 seconds per model step and 300 seconds per run; the 180-second model limit
   was used only for evaluation. A longer limit demonstrably allowed one answer
   to finish, but did not establish better reasoning.
3. When local acceptance passes, isolate this task against each consumer's
   accepted image. Baselines for 38 files and current task hashes are retained
   in `baseline.json`, `baseline/` and `current-task-sources.json` under the local
   evidence directory. The workspace contains unrelated edits; do not ship whole
   dirty files by assumption.
4. The task also changes `services/agent_tools.py` (capture continuation dispatch),
   `services/saa/storage.py` (capture/version and retrieval state),
   `services/saa/semantic_index.py` (semantic availability),
   `services/pipeline_store.py` (attempt transitions/receipts/stage usage),
   `requirements.txt` (`httpx`) and `.env.example` (supported DeepSeek default).
   These did not all receive pre-task file baselines; reconstruct their precise
   deltas against accepted sources before preparing release overlays.
5. Release the same engine generation to the development API, queued worker
   and affected background consumers; verify a queued conversation and a
   background investigation under deployed identities. Preserve rollback images.
   Production still requires explicit authorization for this follow-up.
