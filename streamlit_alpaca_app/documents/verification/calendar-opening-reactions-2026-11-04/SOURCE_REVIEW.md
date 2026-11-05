# Next-open calendar source review

Baseline is the accepted Home wiki production source, including its complete 200-file Python runtime and current worker digest `72fd68cd`. No historical image application tree supplies the change.

Reviewed `services/market_calendar_reactions.py`: keep consolidated SIP and corporate-action adjustment, exact next exchange session, prior close, date/revision/cancellation rules, independent earnings confirmation and retained-observation validation. Replace the target close with its open; use official open plus the existing data delay for eligibility. Preserve missing-data neutrality and require the new measurement basis before retaining a previous observation. No symbol-specific logic or inferred release timestamps.

Reviewed `pipeline/jobs/market_calendar_refresh.py`: retain its existing providers, shared free allowance, event merge, numerical ranking, persistence and market-calendar acquisition. Include cached observations with an old basis in the existing quote refresh, so the full history migrates through the normal producer. Existing schedules, provider settings and other jobs are unchanged.

Reviewed all application differences against accepted production source: these are the only two runtime changes. Existing API/web/native projections consume `return_pct` and remain compatible. Shared database, publication and broker contracts are unchanged; retained consumer images must match their existing complete package proofs. Complete source acceptance and configuration readback are required before packaging/promotion.

Twenty-eight focused operational checks pass using the existing application development environment. Coverage includes next-open versus opposite-sign current close, DST open/data-delay boundaries, missing openings without closing-price substitution, weekend/holiday sessions, invalid old-basis cache, valid new-basis retention, event revisions, EPS independence and the SIP field adapter. Fresh real SIP replay measures all 279 November 3 entries (177 green, 99 red, three unchanged), preserving event and confirmation identities without production writes. See LOCAL.json and FULL_REPLAY.json for current-data evidence.

Alpaca's [official aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq) exclude extended-hours trades from daily open/close updates. This supports using the daily bar's regular-session opening field rather than selecting an arbitrary after-hours trade. The existing historical-data delay is retained.
