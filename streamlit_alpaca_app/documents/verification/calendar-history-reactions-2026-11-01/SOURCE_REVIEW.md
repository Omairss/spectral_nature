# Calendar history and measured reactions

The owner requested past events colored by reaction. The initial baseline was `4430267b`, the live calendar release and build18. During implementation the accepted Market-First backend `59e8c7e5` was promoted in another session. Integrated that exact Git source before packaging; its thirteen runtime changes remain byte-for-byte identical and its full package/production proofs are retained. The final review base is `59e8c7e5`. This isolated checkout changes the shared calendar producer and both clients. Existing Brief research, audio, Highlights, attention graph, Trading, authorization and all existing schedules stay on their accepted paths. No model or research allocation was used.

## Source and numerical acceptance

Alpha Vantage's real owner-key EARNINGS response supplies reported dates, fiscal period, report session and EPS. Its future bulk calendar is retained separately. Reported records supersede the same expected symbol/period and cannot regress when the next bulk response repeats an estimate. History survives provider failures. Failed company requests have a retry cooldown. Priority uses retained market capitalization, falling back to universe liquidity; there is no company shortlist.

The existing transactional execution-state API owns a shared rolling 24-hour limit: 20 application calls, of which at most 12 are history requests, with four companies per producer execution. Successful provider responses are shared across DEV/production. Calendar responses are reused for five hours and company history for 30 days, or one day for an elapsed unconfirmed report. This local allowance leaves provider headroom but cannot account for requests outside the application. The publisher retains coverage rather than claiming complete company history.

The existing Alpaca trading-calendar adapter identifies actual sessions. Following the owner's November2 clarification, every event measures only the next trading day's close versus its preceding close. No report-session-dependent or cumulative window remains. Exact required bars must exist, and current/future sessions cannot supply a finished reaction. Macro colors use SPY under the same convention. The visible reaction is only event-label color; measurement metadata remains internal. No causal direction is inferred from EPS or headlines.

The source cross-check prompted by Microsoft's color found an IEX/consolidated difference. Microsoft's official lookup and a fresh Alpaca SIP request both show October28 close531.01 and October29 close530.14 (−0.16%). Its next open540.22 was green, explaining the distinction. The initial local IEX-based preview (−0.35%) is superseded. Calendar reactions now request only consolidated adjusted SIP bars in the background, using the existing bounded Alpaca transport. No IEX fallback is permitted. Valid retained SIP observations survive quote outages only while their event, symbol, sessions and adjustment basis remain valid. The general price-history publication is unchanged.

Twenty-two targeted operational checks cover source contracts, the single next-session window, missing/partial bars, future/expected/cancelled events, source revisions, reported-date preservation, shared quota, provider-failure history retention and valid SIP reaction retention. These check dates and arithmetic; they do not grade generated prose. Web TypeScript passes. Personally reviewed the real Next.js component at 390px: red/green stacked labels, color-only reaction, compact expansion and attention graph. The first provider probe exposed a duplicate adapter method; removed it and reused the existing method before any build/deployment. Source replay reads the historical retained publication as before. Native Simulator inspection and exact packaged DEV qualification follow before promotion.

## Changed runtime review

| File | Review |
|---|---|
| `services/alpha_vantage.py` | Adds shared cache and transactionally reserved request allowance; existing CSV validation and owner-key secret resolution remain. Provider/network details are sanitized. No secret is persisted in cache keys or evidence. |
| `services/market_calendar.py` | Actual history, date/period identity reconciliation, coverage, 30 past days and optional numeric observation projection. Existing agency parsing, revision/cancellation retention and replay read paths remain. |
| `services/market_calendar_reactions.py` | Background-only consolidated SIP price fetching and single next-session return arithmetic. Missing data stays neutral; previously valid SIP observations survive outages only for the same measurement window. |
| `pipeline/jobs/market_calendar_refresh.py` | Existing isolated dataset publication now includes progressive history and reactions. Uses retained universe/company metadata and existing read-only Alpaca sessions. No order, balance mutation or inference. Existing six-hour bindings, managed identity, 300-second budget and namespace isolation remain. |
| `web/components/market-event-calendar.tsx` and CSS | Optional numeric reaction drives label color without adding reaction text. Existing five positions, fading, stacked labels, source links and collapse retained. Expansion height follows the visible dates; viewport-width accounting preserves focus through resizing. |
| `web/lib/types.ts` | Optional reaction DTO; older publications and unrelated client contracts remain compatible. |

Native `MarketEventCarousel.swift` decodes the optional numeric DTO and uses semantic gain/loss colors from `WorkbenchDesign.swift`, preserving existing colors elsewhere. Selection changes weight without overriding reaction color. Existing event detail remains inline without reaction numbers or close dates. Native project metadata advances to19, retaining bundle/team and default production origin. Native source is independently built/inspected and is excluded from the server inventory.

## Release preservation

Fresh ARM readback contains 38 application consumers. Only the existing DEV calendar worker/API/web are development targets; production counterparts are the existing calendar worker/API and both Next gateways. All other images, managed identities, configuration, schedules, secrets and namespaces are retained. Previous complete-image proofs remain linked in the consumer acceptance. Application trees are removed from dependency images before accepted Git files are copied. No extracted application tree supplies release source.

The owner's preceding instruction explicitly requests the production calendar job and phone update. This follow-up extends that same calendar delivery, after local and DEV acceptance. Exact-image qualification, deployed job execution, publication readback and native verification are recorded alongside this review as they finish.


The initial native Simulator review included reaction numbers and close dates. Those previews are historical and superseded by the minimal presentation below.

The owner rejected the added percentage and close-date text as clutter. Removed the entire reaction-detail block from both clients and narrowed client DTOs to the value required for color. Earlier screenshots with reaction numbers are superseded; final minimal screenshots record the accepted presentation. Backend measurement/provenance stays internal.


## Final delivery — November 2

The exact color-only DEV images are live in the existing production calendar job, API and both web gateways. The ordinary production job succeeded and both public gateways expose the same retained publication with 30 past days, 90 future days, 20 measured events and the attention graph. Conditional reads return 304. All 38 configurations and 31 other consumer images are preserved. See PRODUCTION.json and PRODUCTION_CONSUMERS.json. Actual production native and browser screens show only reaction color; no reaction percentage or close-date text is rendered.

Signed Release build 19 is installed on the owner’s iPhone and independently read back. The profile/signature/device registration are valid. iOS rejected the launch check because the phone is locked; this is recorded explicitly, without claiming a physical screen review. The Simulator is running the same minimal client against the actual production gateway.


The owner requested remote verification. The existing paired connection became reachable, iOS accepted launch, and independent process readback matched the installed executable (PID6943). Build19 installation and launch are now verified. No additional build or installation was needed; the earlier device-lock rejection remains historical. Physical screen inspection is not claimed.


## Open current-results defect — November 3

Current publication and successful refreshes do not establish current earnings results. Production has 152 November 2 expected entries, zero confirmed results and zero reactions; history coverage is 14/1,000 companies and the application history allowance is full. General backfill precedes pending-report priority, and current-date session closes are excluded even after market close. The [follow-up diagnosis](../../plans/CALENDAR_HISTORY_REACTIONS_2026-11-01.md#november-3-follow-up-missing-november-2-results--unresolved) remains unresolved. No application change or deployment occurred during this inspection.


## Current-results correction — November 3 local acceptance

Review against minimal source `3e6064a7`: three Python runtime files change; clients, API contracts, all other application behavior and shared runtime contracts remain identical. `market_calendar_reactions.py` computes a price observation independently of optional EPS/report confirmation, preserving the publisher's expected/reported status. Exchange-provided close times and the documented 15-minute Alpaca historical access delay allow the completed current session without accepting partial/future bars. Missing close/time/quote data stays neutral; current-session times handle DST and early closes. Prior valid observations still survive an outage under the same symbol/session/feed/adjustment checks. A price color does not assert that an expected earnings date was confirmed or infer an earnings surprise.

`market_calendar_refresh.py` requests consolidated prices for all unmeasured past tickers, rather than only history-confirmed reports, and requests through the delayed current timestamp. Retained observations avoid repeated broad price fetching. Alpaca's existing batching/pagination/deadline remains authoritative. `market_calendar.py` prioritizes recent pending reports ahead of broader market-cap backfill; the same shared Alpha Vantage limits remain intact. Optional fundamentals can still be incomplete without suppressing measured prices.

Fresh real production-calendar replay fetched 4,437 consolidated daily prices for 153 symbols and measured all 152 November 2 entries from November 2 to November 3. Every existing event identity and confirmation status is preserved. Personally inspected the unchanged native build19 with that captured publication: November 2 shows red and green labels, future dates remain neutral, inline expansion and attention graph remain. The 25 meaningful numerical/source checks pass, including same-day completion, 15-minute availability, DST, holiday/early close, partial/missing bars, future/cancelled events, revision invalidation, outage retention and pending-report priority. See CURRENT_RESULTS_LOCAL.json. This is worker-only release scope: retain the current API/web/native images and all other workers. The reader's projection and optional color DTO are unchanged. Exact-image DEV and production verification follow; this local replay is not a production publication.
