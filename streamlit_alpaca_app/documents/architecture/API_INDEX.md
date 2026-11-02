

### Paper Trading workspace (production October 29)

`GET /v1/trading-agent?period=1D|1W|1M|3M|1Y|ALL&cursor=…` adds the owner-scoped durable workspace. `GET /v1/trading-agent/paper/calls/{call_id}` returns original thesis, actual fill performance and history. Paper review accepts entry/reduce/close/cancel; confirmation remains one-use and account-bound. `POST /v1/trading-agent/paper/policy` saves execution mode, pause, risk limits and expected revision. The existing Trading producer monitors positions through AQL/Zopedia. See [source review](../verification/trading-system-2026-10-29/SOURCE_REVIEW.md) and [UX design](../plans/TRADING_SYSTEM_UX_2026-10-29.md).

## Market-event calendar — November 2, 2026

The production and DEV Home responses (`GET /api/home`, backend `GET /v1/home`) include an optional `calendar` with thirty past days and ninety future days. The dedicated `market-calendar-refresh` producer refreshes it every six hours independently of Brief inference. Past event labels use red/green from the next trading day's consolidated Alpaca close-to-close move; future and unmeasured events stay neutral. Reaction numbers and closing dates remain internal. Existing inline event detail and the attention graph remain.

Official agency schedules and Alpha Vantage's expected/reported earnings feed the retained calendar. Company history fills gradually under one shared free-provider request allowance. Calendar changes participate in the Home ETag; replay reads the publication available at its requested date. No provider calls run during rendering. Build 19 is installed on the owner's iPhone; iOS device lock is blocking its launch check. See [the design](../plans/CALENDAR_HISTORY_REACTIONS_2026-11-01.md), [production verification](../verification/calendar-history-reactions-2026-11-01/PRODUCTION.json) and [native installation](../verification/calendar-history-reactions-2026-11-01/NATIVE_INSTALLATION.json).
