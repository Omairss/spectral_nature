# Enrichment Philosophy

## Principle: Pre-compute, never block the user

Any data enrichment that a user might see on a page should be pre-computed as a pipeline job. The UI should never run an expensive analysis on-click — if the user has to wait, the architecture is wrong.

## Why

- **Latency kills engagement.** A 30-second spinner after a click is a dead end. Pre-computed results render instantly.
- **Pipeline budget is elastic, user patience is not.** The pipeline runs on a schedule with no user watching. Spending 10 extra minutes in a pipeline job is invisible; spending 10 extra seconds in the UI is painful.
- **Predictable cost.** Pipeline jobs run once per cycle. On-click analysis runs N times for N users, each time a page loads. Pre-computing amortizes the LLM cost to a fixed number of calls per pipeline run.

## How it applies

### Zopedia ticker enrichment (`attention_ticker_zopedia_enrichments`)

When the homepage pipeline builds the daily narrative, it already knows which tickers will appear in the beats. After the homepage summary is assembled, the pipeline runs Zopedia analysis for each beat ticker (parallelized, capped at `ATTENTION_HOME_ZOPEDIA_ENRICHMENT_LIMIT` symbols, default 15). Results are stored as a dataset.

The UI loads the dataset once per session and displays results inline — no spinner, no button, no waiting.

### When on-click is acceptable

- **Conversational/exploratory queries** (e.g., Omnibar) where the input is unpredictable
- **Admin-only diagnostic tools** where latency is an acceptable tradeoff for flexibility
- **One-off deep dives** beyond what the pipeline covers (e.g., a user searching for a ticker not in today's beats)

### When to add a new pipeline enrichment

If you find the UI running an LLM call that could have been predicted from the pipeline's inputs, move it to the pipeline. The test: "Could the pipeline have known this would be needed?" If yes, pre-compute it.

## Principle: Every word the user reads must add information

The token-to-information ratio of rendered content must be high. Internal quality signals, implementation vocabulary, and verbose restating of "we don't know" are waste.

### What the user should never see

- **Critique/review summaries.** "The draft accurately reflects the complete lack of retrievable evidence from all tools. No unsupported claims, overconfidence, or misrepresentations." This is an internal quality gate, not user content. It belongs in admin/debug views.
- **Raw tool/source names.** "Sources: Zopedia.Search Pages, Investigator.Company Context, Investigator.Recent News, Research.Live Event Evidence, Dataset.Attention Ticker Snapshot, Dataset.Recent News" — this is pipeline vocabulary. The user does not know what "Investigator.Company Context" is.
- **Verbose "no data" paragraphs.** If there's no catalyst, say "No catalyst identified" — not a 40-word paragraph explaining that the absence of evidence is accurately reflected and the low confidence is appropriate.
- **Confidence labels for low/medium results.** Showing "Confidence: Low" on a "no data" summary adds nothing. Only surface confidence when it's high enough to be meaningful.
- **Limitations expanders with restated gaps.** If the summary already says evidence is thin, a separate "Limitations" section restating the same thing doubles the noise.

### What the user should see

- **The answer.** Structured sections with clear headings.
- **High-confidence signals.** Only when confidence is genuinely high.
- **Actionable context.** What to watch for, what changed, what matters next.

### The test

For every rendered element, ask: "If I removed this, would the user lose any ability to make a decision?" If no, remove it.
