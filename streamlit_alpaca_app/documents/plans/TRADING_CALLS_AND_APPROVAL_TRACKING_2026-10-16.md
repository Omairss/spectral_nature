# Trading calls and approval tracking

Status: implementation in progress. Owner request: production iOS request `86043829-96ea-458e-b9ba-81dcfbac7030`, followed by explicit instruction to implement it in chat. [Research index](../research/README.md).

## Scope and evidence

The October 16 production run completed eight company reports/five horizons but produced nine non-directional cards out of twenty. Full-output review also found reversed invalidations, confused valuation logic, and long-horizon extrapolation. This request requires better reasoning followed by an explicit probabilistic call, then observation of approved calls.

Three source contracts currently undermine this: the company prompt permits neither-side conclusions; final synthesis explicitly prefers no candidate and permits `no_call`; the normalizer erases long/short when research status is uncertain. Confidence, source coverage, direction and whether to risk money are different concepts. A weak edge remains a directional forecast with low confidence. Failed execution or unresolved identity remains a failed analysis, never a fabricated call.

Approval currently writes `place_requested` with `log_only` and stores the candidate, but no persistent approval-time position or path is shown. Existing research outcomes start at the generation day (which can predate approval) and do not filter bars against the requested evaluation time. These cannot stand in for performance after approval.

## Design

- Advance the Trading forecast contract. Successful selected candidates must choose long/short at the exact horizon; retain uncertainty as estimated probabilities and explicit alternative assumptions. Separate facts, forward business changes, valuation/expectations and the strongest opposing mechanism. Use the existing cheap open-source model and shared AQL/Zopedia engine; no model upgrade, provider bypass, mandatory new agent, ticker rule or call quota.
- Preserve legacy research-only records. Never retroactively turn watch/avoid into a scored trade. A new contract cannot be erased by the old research-status gate.
- Add a durable, environment/user-owned paper-call ledger at the existing approval boundary. Snapshot the exact candidate, forecast, approval time and immutable entry convention. Approval is idempotent. Broker execution retains its separate confirmation.
- Use completed daily price data with an explicit next-session entry policy and matching benchmark. Exclude pre-approval and post-evaluation observations. Preserve the original entry artifact and compare adjusted prices on one consistent basis across corporate actions. Missing or delisted prices remain explicit coverage, never zero returns or invented fills.
- Materialize approved-call observations with price refresh, independently of new model research. Keep every approved call available after it leaves the recent candidate window. Show individual dated performance paths and approval history in web/native; maintain the shared source owner for legacy callers.
- Frozen comparison uses retained production company evidence and unfamiliar retained company cases. Read complete outputs, including assumptions/contradictions; software checks protect arithmetic, time, persistence and permissions only. Track changed-role and complete-workflow cost separately, preserving failed attempts.

## Deployment boundaries

Current production/development baselines changed after native build8: accepted system-reliability engine is `7281e3631a1764cf48c9`. Current root also contains unrelated/unaccepted reasoning and database-lifecycle work. Isolated builds must inherit the actual accepted role-specific image and apply owned changes only. Do not replace queued Research entrypoints or deploy the dirty workspace. Preserve allocations, schedules, credentials and existing data namespaces.

Local acceptance precedes builds. Auto-deploy verified changes to dev; production requires new explicit approval for this release. No trade or broker order is authorized by this task. Existing paid model/depth/research settings and the +20% complete-workload allowance remain constraints. Temporary before-edit hashes are at `/tmp/sn-trading-calls-20261016/baseline/manifest.json`.

## Acceptance record

Pending: complete source implementation, frozen output comparison, arithmetic/time/persistence checks, native/web review, scoped dev deployment and normal authenticated verification. Closed/locked APNs remains outside the free Personal Team capability.
