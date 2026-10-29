

### Paper Trading workspace (production October 29)

`GET /v1/trading-agent?period=1D|1W|1M|3M|1Y|ALL&cursor=…` adds the owner-scoped durable workspace. `GET /v1/trading-agent/paper/calls/{call_id}` returns original thesis, actual fill performance and history. Paper review accepts entry/reduce/close/cancel; confirmation remains one-use and account-bound. `POST /v1/trading-agent/paper/policy` saves execution mode, pause, risk limits and expected revision. The existing Trading producer monitors positions through AQL/Zopedia. See [source review](../verification/trading-system-2026-10-29/SOURCE_REVIEW.md) and [UX design](../plans/TRADING_SYSTEM_UX_2026-10-29.md).
