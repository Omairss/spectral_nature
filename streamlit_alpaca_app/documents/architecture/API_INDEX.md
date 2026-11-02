

### Paper Trading workspace (production October 29)

`GET /v1/trading-agent?period=1D|1W|1M|3M|1Y|ALL&cursor=…` adds the owner-scoped durable workspace. `GET /v1/trading-agent/paper/calls/{call_id}` returns original thesis, actual fill performance and history. Paper review accepts entry/reduce/close/cancel; confirmation remains one-use and account-bound. `POST /v1/trading-agent/paper/policy` saves execution mode, pause, risk limits and expected revision. The existing Trading producer monitors positions through AQL/Zopedia. See [source review](../verification/trading-system-2026-10-29/SOURCE_REVIEW.md) and [UX design](../plans/TRADING_SYSTEM_UX_2026-10-29.md).

## Development market-event calendar — November 1, 2026

The DEV Home response (`GET /api/home`, backend `GET /v1/home`) includes optional `calendar` with `reference_date`, `as_of` and continuous `days`; each day contains separate events with source, expected/scheduled status and date/session/exact timing. The shared `market-calendar-refresh` producer publishes `market_event_calendar` every six hours independently of Brief inference. Clients expand details in place above the attention graph. Calendar changes participate in the Home ETag; replay reads the retained calendar available at the requested date. No provider calls run during rendering. This is DEV-qualified; production is unchanged. See [implementation](../plans/LIVE_MARKET_CALENDAR_2026-11-01.md) and [release evidence](../verification/live-market-calendar-2026-11-01/DEVELOPMENT.json).
