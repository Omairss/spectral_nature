# Attention Feed Guidelines

## Product Standard

The attention feed is a strict `today / 1d` market-intelligence product.

Every homepage card and every drilldown must answer:

1. What changed today versus expectation?
2. Why did it change today?
3. What else moved because of it?

If a card or drilldown cannot answer those questions cleanly, it is not ready.

## Hard Rules

- Do not mix `1w`, `1mo`, `3mo`, or `1yr` logic into homepage ranking.
- Do not let generic anomaly text drive final homepage or drilldown prose.
- Do not classify sector or macro role from keywords alone.
- Do not write uncited causal prose.
- Do not let a stale filing headline masquerade as a same-day catalyst.
- Do not let a single-name filing explain a macro event unless the evidence explicitly supports it.
- Do not repeat the same symbol across homepage rails unless it is nested inside drilldown evidence.
- Do not show unrelated article roundups inside a symbol drilldown.

## Deterministic Versus Agentic Rules

- Deterministic market logic decides what deserves attention.
- Agentic search and retrieval explain why it happened.
- Agentic research may enrich or confirm a move, but it may not override obvious market reality.
- Search results are inputs to evidence, not final truth.

## Classification Rules

- Structured metadata and curated overrides decide sector, industry, and macro role.
- Unknown names stay `Unknown` until mapped.
- Text may enrich explanation, but it may not assign macro identity.
- APGE/APG-style names must remain unclassified for macro roles unless explicitly mapped.
- Single-name drilldowns should show a real sector and industry whenever structured data exists.

## Evidence Rules

### Source hierarchy

1. official / primary
2. top wires
3. reputable finance press
4. broader web

### Evidence requirements

Homepage and drilldown cause text must be backed by retained evidence rows.

Each retained row should be evaluated on:

- freshness
- relevance to the symbol or event
- causal relevance
- source authority
- contradiction risk

### Cause labels

Use only:

- `supported`
- `continuation`
- `unresolved`
- `conflicting`

### When to use each label

- `supported`: same-day or clearly causal evidence exists
- `continuation`: move appears to continue reacting to an older catalyst, but no fresh same-day catalyst is confirmed
- `unresolved`: the move matters, but the explanation is still weak or absent
- `conflicting`: materially different explanations survive evidence review

### Evidence quality

Do not label evidence `High` just because there are several official links.

Evidence quality must reflect:

- freshness
- authority
- specificity
- causal clarity

Old official documents can still be low-quality evidence for `why today`.

## SEC Filing Rules

- Do not treat filing labels like `8-K` or `10-K` as sufficient evidence.
- Fetch and parse filing body text from EDGAR.
- Extract material sections and facts.
- Distinguish `fresh catalyst` from `background context`.
- Routine or stale filings must not become the lead explanation for a sharp move.
- Auditor changes, governance notices, and similar filings should only drive narrative if there is explicit evidence that the market is reacting to them.

## Search And Retrieval Rules

- Search is for discovery.
- Final causal text must come from retained, extracted, and judged evidence.
- Prefer direct official connectors over search when available.
- Dedupe repeated syndications and duplicate articles.
- Reject broad roundup articles as a primary explanation for a single-name move unless they truly discuss that name in substance.

## Narrative Rules

Allowed in the final product:

- concise move summary
- concise cause summary
- concise spillover summary
- explicit uncertainty when needed

Not allowed in final product text:

- residual math prose
- z-score prose as explanation
- generic `coverage is clustering around ...` filler
- generic `helps explain why the move looks idiosyncratic` filler
- repeated source boilerplate
- stale background context presented as if it were same-day causality

Numbers belong in badges, tables, or detail panels. The main sentence should read like analyst-grade market commentary, not debug output.

## Homepage Rail Rules

### `Top Market Events Today`

- only real clustered events
- event-first
- no generic single-name fallback
- must show actual affected assets

### `Must-Read Movers Today`

- large or important single-name moves not absorbed into a top event
- should include real why-today status and spillover or explicit lack of spillover

### `Unresolved Large Moves`

- important moves with weak or conflicting evidence
- uncertainty must be visible, not hidden

## Drilldown Rules

Each drilldown must include:

1. `What Changed Today`
2. `Why Today`
3. `What Else Moved`
4. `Evidence`
5. `Background Context`

### Drilldown quality bar

- `What Changed Today` must compare today versus expectation
- `Why Today` must reflect same-day evidence, continuation, or unresolved status
- `What Else Moved` must show actual peers, related instruments, or explicit lack of spillover
- `Evidence` must be relevant to the symbol or event
- `Background Context` must be separated from immediate catalysts

### Drilldown failure examples

These are explicitly forbidden:

- oil `jumps` wording when oil ETFs are down sharply
- `Unknown | Unknown` on a well-known company with available structured metadata
- an unrelated Benzinga roundup article inside a focused single-name drilldown
- a March 5 filing being treated as the cause of a March 24 move without supporting evidence

## Speed And Reliability Rules

- homepage initial load must use prebuilt or cached day-only outputs
- agentic research should run hourly for shortlisted items and on-demand for drilldown refresh
- drilldown detail may load lazily, but the homepage should remain fast
- if one search provider or scraper fails, the system should degrade to lower confidence, not lower honesty
- cached official evidence should be reused aggressively

## Success Test

If a user opens the product on a major market-activity day, they should immediately see the obvious things they need to read, trust the explanations that are present, and understand when the system is still uncertain.
