# Zopedia LLM Researcher Runtime Plan

Date: 2026-05-29

## Objective

Replace the current stepwise Zopedia planner/monitor loop with an LLM-led research runtime.

The goal is not more deterministic control. The goal is to give the model the right job: act like a serious researcher, use tools as perception, decide what matters, write useful partial answers when that is the honest answer, and revise itself when a critic finds weak reasoning.

This should become the shared spine for:

- Zopedia chat
- Trading Agent ticker packages
- page summaries
- ticker background research
- Home/Market/Broad Economy research
- future long-running research jobs

The public entrypoint remains `run_aql_zopedia_agent(...)` while the internals are rebuilt.

## Current Failure

The current chat backend has these pieces:

- `services/aql_zopedia_engine.py` exposes `run_aql_zopedia_agent(...)`
- `services/omnibar_agent.py` owns the actual loop
- the loop asks a planner for one next action
- a trajectory monitor inspects raw tool calls and can restart or kill
- final synthesis runs after the loop
- a judge can revise a completed answer

The latest Zopedia chat failure showed the core problem:

- The agent gathered useful evidence.
- The user premise was not confirmed by the short-term data.
- The monitor killed the thread because the original framing was not fully resolved.
- The user got "No answer available."
- The follow-up explanation misdiagnosed the failure using unrelated retained memory instead of the actual failed run trace.

That was not a missing-data failure. It was the wrong agent job shape.

## Design Principle

Code should not decide the research thesis.

Code should own:

- tool execution
- timeouts
- budgets
- source capture
- persistence
- UI progress events
- auditability
- safety boundaries

The LLM should own:

- what the user is really asking
- how to investigate it
- whether the framing is right
- what evidence matters
- when the answer is good enough
- when the answer should be partial
- when to ask a clarifying question
- how to explain uncertainty without filler
- whether the final answer is sophisticated enough

This is a research loop, not a fixed workflow.

## Target Runtime

The target shape:

```text
run_aql_zopedia_agent
  -> researcher session
      -> researcher LLM chooses strategy and tools
      -> tools run through the existing catalog
      -> researcher updates an observable research journal
      -> researcher may decompose broad work into parallel research passes
      -> researcher drafts answer
      -> critic reviews answer against evidence and user intent
      -> researcher revises or gathers more evidence
  -> final answer + evidence + limitations + research journal
```

The important change is ownership. The LLM researcher owns the investigation. The critic is qualitative, not a phrase scanner or deterministic gate.

## Runtime Components

### 1. Researcher

Create a new implementation module, likely `services/zopedia_researcher.py`.

It should replace the planner/monitor/final-answer split inside `services/omnibar_agent.py`, but initially stay behind `run_aql_zopedia_agent(...)`.

The researcher prompt should say:

- You own the full investigation.
- Tools are available, but tool use is not the product.
- The answer should be useful, grounded, and direct.
- If the evidence changes the question, say so.
- If only a partial answer is justified, write the partial answer and name the exact next evidence target.
- Do not keep searching just because the original framing was messy.
- Do not declare failure when you have enough evidence to say something useful.
- Use broad adjacent context when it helps, but organize it clearly around the user's question.

The researcher output can remain structured enough for code to execute tools:

- next action: call tool, answer, ask clarifying question, spawn research passes
- tool name and arguments when needed
- short observable reason for progress logs
- current draft answer when ready
- visible limitations

This structure is for orchestration only. It is not a deterministic research model.

### 2. Research Journal

Add a durable, user-safe research journal to each run.

This is not hidden chain-of-thought. It is observable work:

- what the researcher is checking
- which tool was called
- what came back
- what became clearer
- what remains unresolved
- why the answer is now ready or not ready

The journal should be included in debug/admin traces and persisted with the agent result. Product UI can show a compact version when useful.

### 3. Soft Monitor

Demote the trajectory monitor.

It should no longer decide whether an incomplete answer is allowed. It should only catch clear system failures:

- wrong entity
- subject swap
- hallucinated source claim
- looped identical tool calls
- unsafe action
- tool selection that cannot possibly help
- malformed tool request that needs repair

When the monitor sees incomplete evidence, it should usually let the researcher continue or answer partially. It should not kill because the problem is hard.

Implementation path:

- Keep the existing monitor initially.
- Change its prompt and call site so it reviews only drift/safety/loop risk.
- Remove "insufficient evidence" as a normal kill reason.
- If it wants to kill, require it to name an unrecoverable off-contract issue, not just a missing source.

### 4. Critic

Make the critic the main quality mechanism.

The critic should read:

- user question
- final answer draft
- research journal
- tool evidence summaries
- source links
- limitations

It should judge qualitatively:

- Is this actually answering the user's question?
- Is it sophisticated enough?
- Is it overclaiming?
- Are important tensions ignored?
- Is the answer too generic?
- Did the researcher miss an obvious angle?
- Would a smart user trust this?

The critic can return:

- accept
- revise answer
- send researcher back for one more focused evidence pass
- fail closed with an exact explanation

The critic should not be a deterministic checklist. Its job is editorial and analytical judgment.

### 5. Broad Question Decomposition

For broad or multi-entity questions, let the researcher choose decomposition.

Examples:

- "Compare SNOW/MDB vs cybersecurity"
- "What is happening with these ten tickers?"
- "Why are quantum names moving?"
- "Explain the divergence in mid-cap tech"

The researcher can request parallel research passes:

- one pass for the data/cloud side
- one pass for the security side
- one pass for price/timeframe verification
- one pass for current news/events

Each pass is still LLM-led and tool-using. The parent researcher synthesizes across the completed pass outputs.

This replaces hardcoded "one ticker package" as the general solution. Trading Agent can use this same mechanism for tickers, but the runtime should not be ticker-specific.

### 6. Answer Modes

Do not make these rigid product branches. They are ways the LLM can responsibly respond:

- direct answer
- partial answer with exact gaps
- corrected framing
- comparative explanation
- source-backed "cannot determine"
- clarifying question
- research failed because the system/tooling failed

"No answer available" should become rare. If the system has useful evidence, the researcher should write the best grounded answer and explain the unresolved parts.

## Implementation Plan

### Phase 0: Preserve The Public Boundary

Do not change callers first.

Keep:

- `run_aql_zopedia_agent(...)`
- chat UI call sites in `app.py`
- Trading Agent calls
- page summary calls
- materialized output shape where possible

Add the new researcher runtime behind a feature flag or config param:

- old path: current `omnibar_agent._run_zopedia_agent_loop`
- new path: `zopedia_researcher.run_researcher_session`

This lets us probe the new runtime on recent failed threads before switching product traffic.

### Phase 1: Build The Researcher Session

Create `services/zopedia_researcher.py`.

Start with the existing tool catalog and invocation helpers. Do not rebuild tools.

The first version should:

- accept the same inputs as `_run_zopedia_agent_loop`
- call the same tools
- persist the same agent result fields
- maintain a research journal
- let the researcher decide tool calls or final answer
- synthesize answer inside the researcher loop, not as a separate fallback after a killed loop

The researcher should be allowed to answer after any useful evidence, even if the answer is partial.

### Phase 2: Move Quality From Monitor To Critic

Add a critic pass that runs after the researcher draft.

The critic can:

- accept
- revise
- ask the researcher for one more targeted pass
- fail closed

The critic prompt should be direct and demanding. It should reject:

- generic answer
- unsupported cause
- wrong timeframe
- wrong entity
- evidence dump
- "no data" when useful evidence exists
- overconfident answer with major gaps

Acceptance is qualitative. We judge live outputs, not just schemas.

### Phase 3: Demote The Existing Trajectory Monitor

Change the monitor role after the critic exists.

The monitor becomes a safety/drift guard, not an answerability judge.

Specific changes:

- remove kill language based on "insufficient evidence"
- allow partial answer as valid work
- allow contradiction, under-specified framing, and source gaps as normal research outcomes
- kill only on clear off-contract behavior
- when in doubt, prefer "continue" or "critic review" over kill

The goal is to stop losing useful research because a monitor decided the thread was imperfect.

### Phase 4: Add LLM-Led Decomposition

Add support for researcher-requested subpasses.

The parent researcher can ask for parallel passes with natural-language scopes, for example:

- "verify price movement and timeframe for these names"
- "research data/cloud earnings context"
- "research cybersecurity earnings and sector context"
- "check retained Zopedia memory for prior explanations"

Code executes these passes concurrently with safe worker limits. The subpass result is a compact answer, evidence summary, limitations, and source trace.

The parent researcher then writes the answer from those pass outputs.

This generalizes the Trading Agent per-ticker package idea without hardcoding tickers or task types.

### Phase 5: Route Trading Agent Through The Shared Runtime

2026-07-01 update: this phase must run through the v0.6 AQL/Zopedia gateway,
not through another local runtime wrapper. See
`documents/plans/V0_6_AQL_ZOPEDIA_GATEWAY_ROADMAP_2026-07-01.md`.

After the new researcher runtime works in chat, move Trading Agent from its local per-ticker package implementation toward the shared decomposition path.

Trading Agent should request:

- research one or more candidates
- use company/business-first context
- return research-only watchlist output
- run critic before candidate publication

The shared runtime decides decomposition and sufficiency. Trading Agent supplies product intent and materialized context, not bespoke research policy. Every ticker research, final synthesis, and review model call should record a gateway event with surface, purpose, provider, model, status, and artifact links.

### Phase 6: Replace `omnibar_agent.py` Ownership

Once probes pass, move ownership:

- `services/zopedia_researcher.py` owns the loop
- `services/aql_zopedia_engine.py` remains the public entrypoint
- `services/omnibar_agent.py` becomes obsolete and is deleted during pipeline simplification

Do not keep a compatibility maze. The old implementation can remain only while the feature flag is active.

### Phase 7: Materialized Surfaces

After chat works, use the runtime for materialized research surfaces:

- page summaries
- ticker background
- Home research
- Broad Economy
- Trading Agent

Each surface should pass:

- user/task intent
- materialized context
- allowed tools
- budget profile
- output contract

Each materialized surface should receive a gateway event trail. A product row
that contains model-written claims should be able to identify which gateway call
created or reviewed those claims, without storing raw prompts in user-facing
payloads.

The researcher/critic loop remains shared.

## Qualitative Evaluation

Do not judge this by fixed string tests.

Use replay cases:

1. Latest failed Zopedia chat: SNOW/MDB vs OKTA/cybersecurity divergence.
2. MXL identity case: make sure MaxLinear stays MaxLinear.
3. A broad multi-ticker "what is happening" query.
4. A thin-evidence ticker where the right answer is partial.
5. A wrong-framing question where the answer should correct the frame.
6. A real company background query with retained memory plus live evidence.
7. A current market move where freshness matters.

For each replay, personally judge:

- Did it answer the actual question?
- Did it notice uncertainty without hiding behind it?
- Did it use tools intelligently?
- Did it stop when it had enough?
- Did it avoid filler?
- Did it avoid overclaiming?
- Was the critic useful?
- Would this be acceptable to show a serious user?

Only after these pass should we wire it into product traffic.

## Engineering Verification

Deterministic tests are still useful for plumbing:

- tool invocation works
- feature flag routes correctly
- old and new result shapes are compatible
- chat persistence saves journal and agent result
- critic revision is applied
- subpasses preserve source traces
- failures remain visible and non-silent

But these tests do not prove research quality.

## Deployment Plan

1. Build behind config flag.
2. Run local replay probes.
3. Run dev chat with the new runtime for selected sessions.
4. Compare old vs new results on the same questions.
5. Enable for Zopedia chat in dev.
6. Move Trading Agent to shared runtime after chat quality is acceptable.
7. Do not enable prod without explicit approval.

## Open Design Choices

- Whether subpasses should reuse the same LLM client or allow a cheaper model for acquisition and a stronger model for critic.
- Whether chat should show the research journal by default or only in debug.
- Whether long-running research should stream pass-level updates into the existing thinking trace panel.
- Whether Zopedia learning should review failed researcher sessions automatically.
- Whether the critic should be allowed one or two repair cycles before fail-closed.

## First Implementation Target

The first target is not all surfaces.

The first target is:

> Zopedia chat can answer the latest failed divergence thread with a useful, grounded answer, without hardcoded premise logic and without the monitor killing recoverable research.

If that works, replay MXL and one broad multi-ticker query. Then decide whether the runtime is ready to replace the old loop in dev.
