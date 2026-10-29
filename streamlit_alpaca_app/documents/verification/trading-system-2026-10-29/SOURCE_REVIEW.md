# Trading system source integration · October 29

Scope: development delivery of persistent paper Trading, preserving accepted DEV maintenance and macro summary, current production credit/runtime behavior, and native build 16 behavior. Production is not authorized for this change.

Selected Git parents: production API 7c21bfae and DEV 79e95c65. Native inputs were reconciled from accepted Git 975b073c. Historical images provide dependencies and preservation evidence only. Main checkout remains concurrent and is not a release source.

## Behavior review

- Paper account authority remains the scoped Alpaca brokerage snapshot, independent of Robinhood/main Portfolio. Public output explicitly selects account/position/order fields; credentials and account IDs are omitted. History now retains cashflows and intraday observations, with Modified Dietz midpoint adjustment for cashflow windows. Missing flow coverage withholds return; it does not invent zero transfers. Daily metrics and chart use the same chosen period. Negative/zero balances remain visible.
- Calls, decisions, policies, order mappings, activities, and valuation marks persist in the existing authentication/brokerage database under user, portfolio, and environment. New research cannot replace an older thesis or erase its positions. Research-only approvals remain under Research observations.
- Exact broker order IDs assign fills to calls. Weighted cost accounts for partial fills, long/short direction, reductions, exits and linked fees. Unmapped orders remain account-level. Quantity discrepancies, incomplete activity capture and corporate actions require reconciliation. Call return is P/L divided by filled entry capital, not a portfolio return or weighted average of calls.
- Reviews pin exact shares, side, account, thesis, risk policy and revision for 120 seconds. Confirmation rechecks active membership, account, available shares, current risk capacity, market hours and price. A shared account lock serializes submission and policy/denial changes. Consumed/unknown tickets reconcile stable client IDs; ambiguous transport errors cannot send a second order. Cancellation is a request until the broker reports a terminal state.
- Existing Trading producer retains new calls, refreshes account observations, and reviews open calls through shared AQL/Zopedia using private reuse scope and no knowledge writes. NLP is model-owned. Approval is default. Autopilot is an explicit bounded paper-only opt-in for future proposals; pause blocks new entries and preserves monitoring/position-change reviews. No QA trade or owner policy change was performed.
- Web and SwiftUI render the same workspace: account timeline first, Activity/Positions/Performance, direct Approve/Deny, full thesis/fills/call P/L detail, reduce/close/cancel review, and policy settings. Legacy research observations retain their separate individual reference-price plots. Native Home, Markets, charts, feedback and session transport stay at build 16 inputs.

## Preserved DEV and current shared behavior

Reviewed producer, service and adapter differences across the two Git parents, including files outside the research-engine fingerprint. DEV maintenance keeps complete memory publications, version-checked semantic mutations, aliases/redirects, original revision evidence, rollback conflicts, search metadata, lifecycle-safe migrations and the dedicated maintenance telemetry connection. The macro summary keeps its accepted dataset gateway and native/web presentation. Existing inference provider defaults and production schedules are unchanged; the native repair transport remains conditional on the existing saved API-style setting.

Production core-work credit, economical continuation, browser process-group cancellation, full DataFrame analysis inputs, chart artifacts and output budgets are preserved. Merge resolution combines current producer deadlines/completion context with same-company serial draft memory, and current complete analysis frames with cancellable Pipe output. Embedding/audio transport measurements now distinguish native structured inputs from token arrays and audio text. Regression checks exposed two stale assertions (optional analysis objective and normalized missing usage) and old paper review fixtures; these were updated to their actual accepted contracts. Financial behavior has separate real PostgreSQL integration coverage.

## Local verification before packaging

98 targeted checks pass: account snapshots, paper sizing/order boundaries, new fill accounting and database ownership/CAS/retry behavior, numerical sandbox, inference transport, tool schemas, and news adapters. PostgreSQL runs only in a disposable local UTF8 database on port 55439. All Swift sources pass iOS Simulator SDK type checking. Web TypeScript and focused ESLint pass. Python module compilation and Git whitespace checks pass.

This source review authorizes local packaging for actual visual/runtime inspection. Packaged-image verification, populated/empty visual review and DEV deployment receipts are separate records; they must pass before DEV delivery is called complete. No new LLM thesis-quality or portfolio-return claim is made by software checks.


Populated web and signed Simulator screens were personally inspected through the actual product components against a loopback-only display fixture. The account timeline, approval review, call P/L chart and position controls render correctly; screenshots are adjacent. This fixture has no broker credentials and refuses confirmation. Live DEV account and deployed package checks remain separate. Final native navigation uses a single sheet state, and autopilot consent is bound to the exact connected paper account.
