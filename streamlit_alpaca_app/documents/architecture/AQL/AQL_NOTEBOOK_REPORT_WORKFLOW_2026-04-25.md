# AQL Notebook Report Workflow

**Date:** 2026-04-25

## Goal

Let AQL create, execute, review, iterate, and present research reports using a notebook-like workflow without making arbitrary notebook execution part of the trusted production path.

The useful product is not just a `.ipynb` download. The useful product is a reproducible research run with:

- a structured research plan
- retained evidence and citations
- executable analysis steps
- captured outputs
- critique and gap checks
- reruns when evidence is weak
- a final report that can be shown in the UI or exported

## Current Fit

AQL already has most of the research pipeline pieces:

- candidate planning
- web search routing
- source document assembly
- evidence chunking
- claim extraction
- macro hypothesis checks
- bundle writing
- persisted trace frames

The current gap is execution as an explicit artifact. AQL builds frames and narratives, but it does not yet create a durable notebook/run object with cells, outputs, review state, and final report rendering.

## Recommended Design

Do not let the LLM freely write and execute arbitrary Python. Use a controlled notebook builder.

Add these layers:

1. `EvidencePack`
   - One shared object built from AQL/SAA frames.
   - Contains documents, chunks, claims, source diversity, gaps, and citations.

2. `NotebookSpec`
   - A safe, typed plan for notebook sections.
   - Sections include markdown cells, dataframe summaries, charts, and approved analysis blocks.
   - Code cells are generated only from approved templates or a small analysis DSL.

3. `NotebookExecutor`
   - Executes generated notebooks in an isolated worker.
   - Uses timeouts, memory limits, dependency allowlists, no hidden secrets, and read-only inputs.
   - Captures outputs, errors, charts, and execution metadata.

4. `ReviewLoop`
   - Runs gap detection and critique after execution.
   - Sends weak sections back to AQL retrieval/planning.
   - Limits iteration count and records why the loop stopped.

5. `ReportRenderer`
   - Converts the executed notebook plus reviewed conclusions into UI-ready markdown/HTML/PDF.
   - Shows citations and confidence.
   - Keeps raw notebook artifacts available for audit/export.

## Storage Shape

Persist these frames or tables:

- `aql_notebook_runs`
- `aql_notebook_cells`
- `aql_notebook_outputs`
- `aql_notebook_reviews`
- `aql_report_snapshots`

Each row should carry `run_id`, `asof_time_utc`, `status`, `schema_version`, and enough metadata to reproduce the run inputs.

Generated `.ipynb` files and rendered reports should live in generated/blob storage, not tracked source files.

## Execution Policy

Start with safe generated notebooks:

- markdown cells from AQL writers
- dataframe cells that only read prepared AQL frames
- chart cells from approved templates
- no network access from notebook execution
- no direct database writes
- no arbitrary shell commands
- no secrets printed into cell outputs

Only after this works should custom user-authored code execution be considered. That would require a stronger sandbox and explicit user trust boundaries.

## Workflow

1. User asks for a report.
2. AQL plans the report and builds an `EvidencePack`.
3. AQL creates a `NotebookSpec`.
4. The notebook builder emits an `.ipynb` plus a machine-readable manifest.
5. The executor runs it in a sandboxed job.
6. The reviewer checks outputs, claims, citations, and gaps.
7. If needed, AQL retrieves more evidence and regenerates affected sections.
8. The renderer publishes a final report snapshot.
9. The UI shows report status, review notes, source coverage, final report, and optional notebook export.

## Complexity / Reliability

MVP reliability is good if execution is template-only and read-only.

Reliability becomes low if arbitrary LLM-written Python is allowed. That is too risky for the first version because it can leak secrets, hang jobs, create non-reproducible outputs, or silently produce wrong analysis.

The first version should prove the research loop and report value before adding open-ended execution.

## Suggested Phases

### Phase 1: Report Artifact Without Execution

- Add `EvidencePack`.
- Add report section planning.
- Render markdown reports from existing AQL frames.
- Persist report snapshots and review notes.

### Phase 2: Notebook Creation

- Add `NotebookSpec`.
- Generate valid `.ipynb` files from approved markdown/table/chart templates.
- Validate with `nbformat`.

### Phase 3: Safe Execution

- Add an isolated executor.
- Execute only template-generated cells.
- Capture outputs and failures.
- Persist cell outputs and execution metadata.

### Phase 4: Review And Loop

- Add critic/gap checks over the executed notebook.
- Re-run targeted retrieval for weak sections.
- Limit retries and record unresolved gaps.

### Phase 5: Presentation

- Add report UI with status, citations, confidence, review notes, and exports.
- Support markdown/HTML first, PDF later.

### Phase 6: Advanced Code

- Consider a narrow analysis DSL or signed approved Python blocks.
- Avoid arbitrary user/LLM code until there is a strong sandbox and operational need.

## Open Questions

- Should reports be requested from the omnibar, from Attention pages, or from a dedicated Research Reports page first?
- Should execution run inside the existing pipeline job system or a separate worker?
- Which export format matters first: UI report, `.ipynb`, HTML, PDF, or zip bundle?
- Should notebooks be private per user, shared by workspace, or attached to market events?
