- [x] Compress market opportunity to a single table. Right now it's 3 different table + many other tools we can call on a ticker. I'd like to consume all that data, compress it to one feed of Market opportity for each stock and columns can add further details.

- [x] Agentic summary (Home page style) for Market Explorer, Stock Investigator and Broad Economy pages. Updates, what is worth looking into, etc.

- [x] Trading Agent - A Brand new experiment page (available only to admin) that consumes summaries from all agentic summaries, gathers additional evidence as neccesary and makes trade suggestions. Trading philosophy is largely - observe broad economic patterns (go with the wind), observe momentum, build hypothesis, validate hypothesis, identify tail risk, trade.


- You did a terrible job of reinventing things and putting it in the hot path riddled with bugs. You did not follow the existing arhcitecture, instead invented a new one. You did not test end to end. (Trading agent is showing a bug ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()), Market opportunity is is slow to load and borad economy is doing this - AQL agent failed: ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')). MAKE SURE ALL THIS IS FIXED.

- Go back and rethink your strategy. Go through the learnings and mistakes relevant here. Come up with a better way to reference learnings, mistakes etc if it's too large. (Do we need a directory?) And make sure this never happens again.

- Make sure the new features fit in elegantly with the current setup. Clean up any mess, loose ends, irrelavant fallbacks. Make everything **sharp** and **clean**. Remove code that doesn't add value.

- [x] Inventory and move UI job trigger buttons to Admin > Pipeline Jobs so normal users do not see or trigger jobs.

- [x] Run Trading Agent as a materialized pipeline job across 1 week, 1 month, 3 month, 1 year, and 5 year horizons.

- [x] Log Trading Agent Place / Reject decisions. Place is log-only for now, but the audit row keeps Alpaca handoff fields for future order submission.
