# Zopedia Parity Test Plan

Date: 2026-05-15

## Goal

Make "as good as Zopedia" testable enough that we can decide whether to ship.

This is partly subjective, but it cannot stay vague. The product should only ship when it matches or beats standalone Zopedia on the flows that make Zopedia valuable:

```text
source intake -> durable memory -> wiki navigation -> reasoning/tool trace -> graph exploration -> maintenance -> reviewed improvement
```

## Principle

Do not compare isolated helpers. Compare complete user flows.

The question is not:

> Can we call a transcript API?

The question is:

> Can a user paste a YouTube link, see it ingested, ask a question, watch the agent read the transcript/wiki memory, get a cited answer, inspect the memory graph, and review proposed memory improvements?

## Test Environments

Run every parity test against:

1. Standalone Zopedia from `https://github.com/zohairshafi/zopedia`.
2. Current Spectral Nature Chat + Search baseline.
3. New native Spectral Nature Zopedia implementation.

Keep a small golden corpus under a test fixture folder or documented replay manifest.

## Golden Corpus

Use sources that exercise different parts of the stack:

1. **YouTube transcript**
   - market/macro video with a clear topic and named entities.
   - Expected: transcript captured or clear transcript-unavailable state.
   - Initial fixtures:
     - `https://www.youtube.com/watch?v=BOT2rrm10RM`
       - Title: "Jeff Currie on the diesel pinch point, Hormuz, and equity "la la land" | EA Forum Ep.18"
       - Channel: Energy Aspects
       - Dev transcript spot-check on 2026-05-15: available, 801 transcript entries.
     - `https://www.youtube.com/watch?v=t6y_VmxuO28`
       - Title: "Inflation Surges= Bond Market Disaster- What's driving inflation, and where are we headed?"
       - Channel: Infranomics
       - Dev transcript spot-check on 2026-05-15: available, 745 transcript entries.
     - `https://www.youtube.com/watch?v=n889nI8sR84`
       - Title: "Nasdaq Euphoria is Hitting its Limit | TCAF 242"
       - Channel: The Compound
       - Dev transcript spot-check on 2026-05-15: available, 2,565 transcript entries.
   - Dependency gate: standalone Zopedia's graphify ingestion uses `youtube-transcript-api`; Spectral Nature needs an equivalent dependency or service before this test can pass.
   - Do not treat YouTube HTML fallback as parity. Passing this fixture means transcript text is captured, or the system returns a clear transcript-unavailable state with the video metadata.

2. **PDF / long document**
   - investor letter, policy note, or company deck.
   - Expected: source summary, entity/concept extraction, chunks retained.

3. **Web article**
   - current market/news article.
   - Expected: canonical URL, captured text, source page.

4. **Prior attention summary**
   - one of our own generated attention summaries.
   - Expected: analysis page and links to tickers/themes.

5. **Duplicate concept pair**
   - two sources that refer to the same idea by different names.
   - Expected: duplicate/merge proposal, not silent merge.

6. **Broken-link fixture**
   - one wiki page with a bad `[[missing/page]]` link.
   - Expected: lint reports broken link.

7. **Orphan fixture**
   - one page with no inbound links.
   - Expected: lint reports orphan.

## Core User Journeys

### Journey 1: First Question

Prompt:

```text
What is the most important thing I should know from the uploaded source?
```

Pass criteria:

- answer cites the uploaded source
- answer names the relevant entities/concepts
- answer does not invent facts not present in the source
- Thinking/trace shows source read or retained evidence retrieval
- source can be reopened from the answer

### Journey 2: YouTube Memory

Flow:

1. Paste a YouTube URL.
2. Wait for ingestion.
3. Ask a question about a claim made in the video.
4. Ask a follow-up question.

Pass criteria:

- transcript or explicit unavailable state is shown
- transcript is retained as SAA source evidence
- source/wiki page is searchable
- answer cites transcript/source page
- follow-up resolves context without re-explaining the whole source
- graph shows source linked to extracted entities/concepts

Run this journey against all three initial YouTube fixtures in the golden corpus.

### Journey 3: Wiki Navigation

Prompt:

```text
What do we know about [entity/concept] across the uploaded sources and prior analyses?
```

Pass criteria:

- agent uses wiki memory before live web if memory has coverage
- agent reads entity/concept page
- agent follows at least one relevant backlink/analysis/source link when needed
- answer distinguishes retained memory from fresh/current evidence
- citations include exact page IDs or source IDs

### Journey 4: Current Market Question

Prompt:

```text
What changed today for [ticker/theme], and does our prior memory help explain it?
```

Pass criteria:

- agent uses live evidence for current facts
- agent uses Zopedia memory for background/context
- agent does not treat stale memory as current evidence
- answer labels uncertainty and gaps
- AQL critique does not flag major unsupported claims

### Journey 5: Graph Exploration

Flow:

1. Open Zopedia memory graph.
2. Search for an entity.
3. Focus 1-hop.
4. Expand 2-hop.
5. Hide unfocused nodes.
6. Add a hidden node back into focus.

Pass criteria:

- graph remains readable
- disjoint networks do not visually collapse into one unreadable mass
- hide-unfocused reduces noise
- search/list selection still works while nodes are hidden
- selected seed neighborhood is deterministic
- source/entity/concept/analysis node types are visually distinct

### Journey 6: Maintenance And Review

Flow:

1. Run lint.
2. Inspect stale/orphan/broken/duplicate findings.
3. Review a proposed link or merge.
4. Accept one proposal and reject one.

Pass criteria:

- findings are understandable
- destructive changes have preview
- accepted change updates graph/search
- rejected proposal stays rejected and does not reappear immediately
- all changes have audit trail

## Quantitative Gates

These are not enough by themselves, but they prevent obvious regressions.

### Ingestion

- YouTube source ingest completes or returns explicit transcript-unavailable state within 60 seconds.
- URL source ingest completes within 45 seconds for normal web pages.
- PDF/text source ingest completes within 120 seconds for fixture documents.
- 100% of ingested sources have:
  - canonical source ID
  - source type
  - captured timestamp
  - raw/normalized text pointer
  - searchable chunks or explicit no-text status

### Retrieval

- For fixture queries with known relevant pages, top 5 wiki search includes the expected page at least 80% of the time.
- `read_wiki_page` returns exact requested page or clear error, never a guessed page.
- 1-hop neighborhood output is deterministic for the same seed and graph version.
- Archived pages never appear in active search or active graph.

### Answer Quality

For golden prompts:

- citation coverage: at least 90% of factual answer paragraphs include source/page support
- hallucination rate: 0 critical unsupported claims in acceptance set
- critique pass: no high-severity unsupported/numeric/contradiction issues
- follow-up resolution: at least 80% of follow-ups resolve the intended prior context

### UX

- first visible tool/status update within 3 seconds for agent runs
- progress heartbeat at least every 8 seconds during long LLM/tool calls
- graph initial render under 5 seconds for test graph
- memory page open under 2 seconds for normal page
- no text overflow or unreadable controls on desktop and mobile target widths

## Qualitative Review Rubric

Score each journey 1-5 against standalone Zopedia.

1 = much worse  
3 = roughly acceptable but clearly weaker  
5 = equal or better

### Criteria

- **Usefulness:** Did the answer actually help?
- **Trust:** Were sources, caveats, and confidence visible?
- **Memory feel:** Did it feel like the system remembered, or just searched?
- **Trace clarity:** Could the user understand what the agent did?
- **Graph usability:** Could the user navigate without fighting the UI?
- **Control:** Could the user review/undo/avoid destructive changes?
- **Speed:** Did it feel responsive enough?
- **Polish:** Did it feel intentional, not bolted on?

Ship gate:

```text
Every core journey average >= 4.0
No critical journey criterion below 3.0
No high-severity critique failures
No unreviewed destructive memory changes
```

## Automated Test Layers

### Unit Tests

SAA wiki:

- page ID normalization
- slug generation
- wikilink parsing
- backlink generation
- archive filtering
- graph construction
- 1-hop/2-hop neighborhood expansion
- source metadata normalization
- YouTube URL detection
- transcript-unavailable fallback

AQL/Agents:

- tool schema validation
- planner can select `zopedia.read_page`
- duplicate tool-call blocking
- stale memory guardrails
- proposal payload construction

UI helpers:

- graph filtering
- focus set computation
- mode-separated selection
- delete preview state

### Integration Tests

- ingest URL -> SAA source -> wiki page -> search -> read
- ingest YouTube -> source -> chunks -> answer
- wiki page with links -> graph nodes/edges
- lint broken link fixture
- AQL query uses wiki tool and cites page
- accepted proposal updates graph

### End-To-End Tests

Use Playwright or equivalent browser tests:

- Zopedia page loads
- user submits query
- tool trace appears
- answer appears
- source strip appears
- upload/URL dialog submits
- graph explorer focuses seed node
- maintenance panel shows lint result

### Offline Eval

Create a replay harness:

```text
input corpus + prompt -> captured answer + tool trace + citations + proposals
```

Store each run as JSON so we can diff:

- sources retrieved
- pages read
- answer text
- citations
- critique issues
- proposal deltas
- latency

## Manual Acceptance Sessions

Before shipping, run three manual sessions:

### Session A: New User Feel

No prior explanation. User opens Zopedia page, ingests one source, asks one question, explores graph.

Pass:

- user can complete without instruction
- user understands what was saved
- graph is discoverable

### Session B: Power User Research

User ingests several mixed sources, asks layered market questions, uses follow-ups, reviews proposals.

Pass:

- memory compounds across sources
- agent uses prior memory without being forced
- review queue feels safe

### Session C: Failure Handling

Use bad URL, missing transcript, noisy PDF, unsupported file, stale page, broken link.

Pass:

- failures are explicit
- partial success is preserved
- no silent bad memory is created

## What Would Make This Not Worth Shipping

Stop or hold if any of these are true:

- It is just a renamed Chat + Search page.
- Uploaded sources do not become durable memory.
- Agent cannot reliably read exact wiki pages.
- Graph is pretty but not useful for navigation.
- Memory writes happen without review/audit.
- Answers cite wiki memory but not underlying sources for current market claims.
- Maintenance findings are noisy enough that users ignore them.
- The product feels slower and less reliable than current Chat + Search.
- Standalone Zopedia beats native Zopedia on most core journeys.

## Release Decision Template

For each release candidate, record:

```text
Candidate:
Commit/image:
Dataset/corpus version:
Standalone Zopedia score:
Native Zopedia score:
Current Chat + Search score:

Quantitative gates passed:
Critical failures:
Known gaps:
Decision: ship / dev-only / hold
Reason:
```

## First Implementation Gate

Before building broadly, implement a small parity harness around three flows:

1. YouTube ingest and answer.
2. Uploaded/text source ingest and answer.
3. Wiki graph focus and lint.

If those three do not feel materially better than current Chat + Search, pause the broader build.
