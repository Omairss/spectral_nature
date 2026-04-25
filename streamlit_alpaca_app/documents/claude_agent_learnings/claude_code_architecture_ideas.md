# Claude Code Architecture — Ideas for spectral_nature / AQL

Source: leaked Claude Code source (claude-code-main), March 2026.  
Purpose: extract patterns applicable to the AQL pipeline and attention intelligence layer.

---

## 1. Coordinator-Worker Multi-Agent Pattern

**Source:** `src/coordinator/coordinatorMode.ts`

Claude Code's multi-agent system splits work cleanly into phases:

| Phase | Agent type | Notes |
|-------|-----------|-------|
| Research | Multiple workers in **parallel** | Read-only, fan-out freely |
| Synthesis | **Coordinator** (no tools) | Reads findings, writes a precise spec |
| Implementation | Worker(s) | Narrow, targeted changes per spec |
| Verification | Independent worker | Fresh eyes, proves work not rubber-stamps |

Key rules:
- Coordinator never delegates understanding. It synthesises results into a specific prompt with file paths, line numbers, exact expected outcome.
- "Based on your findings, do X" is an anti-pattern — it pushes synthesis to the worker.
- Continue a worker (send it a message) when it has useful loaded context. Spawn fresh when its prior context would pollute the next task.
- Worker results arrive as `<task-notification>` XML messages — the coordinator parses status, result, and uses the task-id to continue the worker.

**AQL application:**  
AQL's pipeline stages (collect → extract → cluster → write → assemble) map naturally onto this. A coordinator could:
1. Fan out `collect` workers per ticker in parallel
2. Synthesise collected evidence into a structured brief
3. Dispatch `writer` workers with precise briefs per event cluster
4. Dispatch a `verifier` worker that checks event quality against the evidence

The coordinator keeps a scratchpad (temp dir) for durable cross-worker state — useful for passing the evidence index between stages.

---

## 2. Speculation / Speculative Execution

**Source:** `src/services/PromptSuggestion/speculation.ts`

Claude Code runs a "speculation" agent in the background on the most likely next user action. Key design:

- Uses an **overlay filesystem with isolated writes**: writes go to a temp overlay dir, reads are redirected to the overlay if a file was already written there. Main filesystem is untouched until the user accepts.
- **Boundary detection**: speculation stops (aborts) the moment it hits a non-read-only Bash command, a file edit without permission, or an unknown tool. It stores the `boundary` (what caused the stop + file path/command).
- On accept: the overlay is atomically copied to main, speculated messages are injected into the real conversation.
- On reject/abort: overlay is cleaned up, no side effects.
- **Pipelined suggestion**: when speculation completes, it immediately pre-generates the *next* prompt suggestion and starts the next speculation round. This creates a cascading pipeline.

**AQL application:**  
- Run the next attention pipeline cycle speculatively in a staging area. Only promote if quality gates pass (confidence score, dedup, entity check). This is an isolated-write staging model.
- Pre-start the next cycle as the current one finishes. Instead of `collect → wait → next collect`, the next collect starts as soon as the previous write phase finishes.
- The "boundary detection" idea: AQL jobs could declare `is_destructive` or `requires_approval` markers, and a supervisor stops at those boundaries pending an explicit trigger.

---

## 3. autoDream — Background Memory Consolidation

**Source:** `src/services/autoDream/autoDream.ts`, `consolidationPrompt.ts`

The dream system fires a background subagent to consolidate session memory after a threshold of accumulated sessions. Gate order (cheapest check first):

1. **Time gate**: hours since last consolidation ≥ minHours (one file stat)
2. **Session gate**: sessions accumulated since last consolidation ≥ minSessions (scan transcript dir)
3. **Lock**: no other process currently consolidating (file lock with rollback on failure)

The consolidation prompt is structured into four explicit phases:
1. **Orient** — read the current memory index, skim existing topic files to avoid duplicates
2. **Gather** — look for new signal in daily logs and transcripts (grep narrowly, don't read whole files)
3. **Consolidate** — write/update memory files, merge new signal into existing topics
4. **Prune and index** — keep the index file under a line/size limit

The agent is constrained to read-only Bash + write to memory directory only. No grepping source files to verify facts — only the session messages are the source of truth.

**AQL application:**  
AQL's evidence index (`evidence_index.py`) is exactly this pattern. After N pipeline runs, a consolidation pass should:
1. Scan which evidence chunks are stale (contradicted by newer data)
2. Merge near-duplicate event records
3. Prune low-confidence items
4. Update the evidence index manifest

The gate pattern (time + count + lock) is directly applicable to AQL's scheduled consolidation — run it after 5+ pipeline cycles OR 24+ hours, whichever comes first, with a file lock to prevent concurrent runs.

The "4-phase consolidation prompt" is a clean template for any AQL consolidation agent.

---

## 4. Memory Extraction — 2-Turn Read-Write Pattern

**Source:** `src/services/extractMemories/prompts.ts`

When a memory-extraction subagent has a **limited turn budget**, it uses a strict 2-turn pattern:

> "Turn 1 — issue all Read calls in parallel for every file you might update.  
>  Turn 2 — issue all Write/Edit calls in parallel."

Do not interleave reads and writes across multiple turns. This halves the minimum turn cost.

The agent is also prohibited from "investigating" facts in the source code — it can only save what's in the last N messages. This prevents expensive digressions.

**AQL application:**  
Any AQL agent that reads current state then writes updates (e.g. evidence index consolidator, event writer) should structure its work this way: one parallel read fan-out, then one parallel write fan-out. Avoids the common pattern of read-edit-read-edit-... which wastes turns.

---

## 5. Tool Design Contract

**Source:** `src/Tool.ts`

Every Claude Code tool declares a rich contract beyond just `call()`:

| Property | Purpose | AQL relevance |
|---------|---------|---------------|
| `isConcurrencySafe(input)` | Safe to run in parallel with other instances? | AQL tools that read-only are concurrency-safe; writers are not |
| `isReadOnly(input)` | Declare read vs write intent | Lets a supervisor decide whether to allow in speculation/staging |
| `isDestructive(input)` | Irreversible operations (delete, overwrite) | Mark evidence deletion as destructive |
| `maxResultSizeChars` | Budget result size; large results persisted to disk | Keep LLM context clean by capping evidence chunk size |
| `validateInput()` | Pre-call validation (tells model why it failed) | AQL tools can validate ticker format, date range, etc. |
| `checkPermissions()` | Tool-level permission logic separate from general system | Separate "can run this tool?" from "is the input valid?" |
| `interruptBehavior()` | `'cancel'` or `'block'` when user interrupts | Long pipeline runs should be `'cancel'`-able |
| `backfillObservableInput()` | Add computed/derived fields to the input before hooks see it | Useful for adding resolved ticker metadata before logging |

The `buildTool()` factory fills in safe defaults (fail-closed: `isConcurrencySafe → false`, `isReadOnly → false`).

**AQL application:**  
If AQL tools are ever exposed as Claude API tools (for use in an agent loop), this contract is the right interface. Specifically:
- `isConcurrencySafe` enables safe parallel dispatch of collect/extract workers
- `maxResultSizeChars` prevents oversized search results from flooding context
- `isDestructive` gates evidence deletion behind explicit approval

---

## 6. Task State Machine

**Source:** `src/Task.ts`

Task lifecycle: `pending → running → completed | failed | killed`

Key design decisions:
- Task IDs have type-prefixed alphabets: `b` = bash, `a` = local_agent, `r` = remote_agent, `d` = dream. Allows instant type identification from an ID string.
- Each task gets an `outputFile` path assigned at creation — output streams to disk regardless of status.
- `isTerminalTaskStatus()` is a single guard used everywhere to prevent injecting messages into dead tasks.
- `totalPausedMs` tracks how long a task was paused — useful for billing and timeout enforcement.

Task types available:
- `local_bash`, `local_agent`, `remote_agent` — standard execution modes
- `in_process_teammate` — lightweight in-process subagent (transcript visible to user)
- `local_workflow` — multi-step workflow
- `monitor_mcp` — passive monitor
- `dream` — memory consolidation (background, no user prompt)

**AQL application:**  
AQL's `AgenticAttentionArtifacts` and pipeline jobs should be modelled as tasks with this state machine. Specifically:
- Use type prefixes on job IDs to make logs scannable
- Assign an output file per job at creation for streaming log capture
- `dream`-equivalent type for background evidence consolidation runs
- A `monitor` type for KAIROS-style always-on market monitors

---

## 7. ULTRAPLAN — Remote Deep Planning

**Source:** `src/commands/ultraplan.tsx`

ULTRAPLAN offloads complex planning to a remote Opus session with a 30-minute timeout. Key design:
- The local session sends the task + a seed draft plan (if available) to a remote CCR session
- The remote session runs Opus 4.6 with full multi-agent search capabilities
- Results are polled; on approval the plan is injected back into the local session
- The model is read from a GrowthBook feature flag — easy to swap

**AQL application:**  
For AQL's north-star "deep research" mode (from `documents/architecture/AQL/AQL_NLP_IR_AGENT_ARCHITECTURE_2026-04-14.md`), this pattern applies directly:
- For complex hypotheses (e.g. "why did BMY underperform vs sector?"), offload to a long-running Opus session
- The attention pipeline generates a seed hypothesis brief; Opus researches it deeply
- Results come back as a structured evidence pack, injected into the AQL assembler
- Timeout of 30 min matches the kind of depth needed for earnings/macro analysis

---

## 8. Agent Summary — Live Progress Summarization

**Source:** `src/services/AgentSummary/agentSummary.ts`

Every 30 seconds, a forked agent reads the running worker's current transcript and generates a 3-5 word present-tense activity description: "Reading runAgent.ts", "Fixing null check in validate.ts".

Key implementation detail: the fork uses the **same CacheSafeParams** as the parent agent to share the prompt cache. Tools are kept in the API request (for cache key matching) but denied via `canUseTool`. This avoids cache misses.

The summary prompt avoids branch names and past tense. It names the specific file or function, not the abstract task.

**AQL application:**  
Long pipeline jobs (collect + extract + write for 30 tickers) should expose a live summary. The same pattern — a lightweight fork of the job's current state, denied all tool use, asked for a one-sentence description — can feed a Streamlit status indicator.

---

## 9. KAIROS — Always-On Proactive Monitor

**Source:** Multiple files reference `kairosActive` / `getKairosActive()`

KAIROS is an "always-on" mode where the assistant watches logs and acts proactively without waiting for user input. It uses `disk-skill dream` (a different consolidation path than the normal autoDream). The normal autoDream is explicitly skipped when KAIROS is active:

```typescript
function isGateOpen(): boolean {
  if (getKairosActive()) return false // KAIROS mode uses disk-skill dream
  ...
}
```

**AQL application:**  
The attention pipeline is essentially KAIROS for markets: it should run continuously, watching for new signals (price moves, news, FRED releases) and proactively updating the evidence index and attention scores — without waiting for a user to request a refresh.

The architecture: a lightweight scheduler (cron or event-driven) fires the pipeline when signal thresholds are crossed. This replaces the current polling model with a reactive model.

---

## 10. Coordinator Scratchpad

**Source:** `src/coordinator/coordinatorMode.ts` (scratchpad section)

When the scratchpad feature is enabled, workers get a shared scratchpad directory where they can read and write without permission prompts. The coordinator includes it in worker context:

> "Scratchpad directory: {scratchpadDir}  
> Workers can read and write here without permission prompts.  
> Use this for durable cross-worker knowledge — structure files however fits the work."

**AQL application:**  
Between AQL pipeline stages, a shared staging directory serves the same role. The collector writes raw search results there. The extractor reads and writes claims there. The writer reads claims and writes event bundles there. The assembler reads event bundles and writes the final market overview there.

This is cleaner than passing data through function arguments across the pipeline — each stage owns its portion of the scratchpad and doesn't need to know about the others' implementations.

---

## Summary Table

| Pattern | Claude Code location | AQL application |
|---------|---------------------|-----------------|
| Coordinator-Worker | coordinatorMode.ts | Pipeline stage orchestration, parallel collect per ticker |
| Speculation / staging | speculation.ts | Staging area for pipeline runs, promote on quality gate pass |
| autoDream gates | autoDream.ts | Evidence index consolidation scheduler |
| 2-turn read-write | extractMemories/prompts.ts | Evidence index agent efficiency |
| Tool contract | Tool.ts | AQL tools as proper agent tools with rich metadata |
| Task state machine | Task.ts | Pipeline job lifecycle tracking |
| ULTRAPLAN | ultraplan.tsx | Deep hypothesis research via long-running Opus session |
| Agent summary | AgentSummary.ts | Live pipeline status indicator in Streamlit |
| KAIROS | bootstrap/state.ts | Always-on market signal monitor |
| Scratchpad | coordinatorMode.ts | Inter-stage data passing via shared staging dir |
