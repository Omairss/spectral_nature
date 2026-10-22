# Aggregate approved-paper portfolio — local review

The owner confirms approved paper calls as portfolio membership. Trading now
opens on Paper portfolio in SwiftUI and Next.js. Calls retains all prior research,
horizon selection, collapsed candidate cards and approval/rejection controls.
No call was approved, rejected or executed on a real service for this work.

## Accounting and read ownership

The existing immutable approval/outcome service owns a pure aggregate. Each entry
adds one equal unit after the session's gain/loss; daily returns chain around
external funding. Maturity retains value as cash, shorts keep their original
signed-price convention, and SPY uses the same dates/directions. Linked call
contributions reconcile to portfolio return. Overlapping symbols show net and
gross exposure and retain each underlying approval. Missing marks, missing SPY,
pending entries and exhausted capital remain explicit. Individual paths are
loaded only when opened; the main DTO contains one aggregate path and summaries.

Eleven objective accounting/ownership checks pass. They cover staggered funding,
closed cash, long/short overlap, matched SPY, missing marks/benchmark, pending and
empty books, drawdown/nonpositive capital, historical gaps, invalid paths and
owner-scoped joins. These are arithmetic/authorization checks, not NLP grading.

## Web review

Personally inspected a local eight-call portfolio: four active symbols, including
overlapping long/short NVDA, two closed calls and one pending entry. The overview
shows combined return, drawdown, matched SPY, signed exposure and cash, with all
contributor tickers/units visible. Positions are collapsed by symbol. Opening
NVDA reveals its two approvals; selecting the short approval loads its correct
dated chart and original thesis, then returns to the aggregate. The empty book
leads to Calls, preserving the collapsed candidate and decision actions.

The first layout inherited the generic chart's tall viewport and auto-skipped
categorical ticks. Portfolio containers now specify their own chart sizes and
explicit contributor ticks/units; no shared renderer or other chart changed.
Types, changed-file lint and all sixteen existing auth/Home regressions pass.
The first native build passes and personally inspected portfolio and short-call
sheets agree with web totals. Review found clipped native dates and a zero
contribution placeholder for an unpriced holding; the shared return chart now
uses compact UTC labels, and the source DTO marks unknown contribution/value
explicitly. Missing path JSON is isolated to that call, retaining the other
approvals and individual thesis. Final native compilation/screen review and
deployment verification remain pending.
The current checked-in acceptance is source/build approval only, not permission
for production or a claim that native visual QA has completed.

## Release separation

Native build 10 keeps the production origin, bundle/team and session ownership.
The current DEV API has a separate accepted maintenance rollout (`ff432b74`);
its seventeen Python changes must remain intact for a DEV API update. Any DEV
integration of that source is separately recorded and cannot authorize an
unrelated research promotion to production. The original dirty IDE checkout is
not release source and all concurrent research remains preserved.

Local stress observation: 1,000 calls / 250,000 input observations aggregate in
0.231 seconds to 250 shared observations and a 72,496-byte portfolio DTO. Individual
price histories are absent from the main response and remain click-loaded.
