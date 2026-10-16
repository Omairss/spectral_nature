# Zopedia/AQL language, model, and harness audit

Requested October 7, 2026. Reviewed against local source, live development and
production worker configuration, the authenticated DeepSeek API, and current
provider documentation. The original audit did not change production configuration
or retained knowledge. Subsequent authorized releases are recorded below.

## Follow-up implementation

The three bounded harness fixes are implemented and deployed to development and
the modern production workbench:
review-schema validation and guarded memory promotion, complete collected text
and references for synthesis/review/retention, and shared monotonic deadlines
across provider attempts and repair. Drafts remain available with review status.
See the [implementation and acceptance record](../../../plans/HARNESS_RELIABILITY_FIXES_2026-10-07.md)
and the [development](DEV_HARNESS_RELEASE_2026-10-07.json) and
[production](PROD_HARNESS_RELEASE_2026-10-07.json) release receipts.
The audit below records the original findings. Hard cancellation, checkpoints,
semantic context management and broader gateway coverage remain open.

## Decision

The current deployment is not using an explicitly selected top reasoning model.
It requests `deepseek-reasoner`; a live API response identifies the resolved model
as `deepseek-flash`. The account's model list contains `deepseek-flash` and
`deepseek-v4-pro`. The legacy name does not establish the underlying model version.

There is no evidence that the current harness is the best available for this
product. It has valuable application-owned tools, evidence, permissions, progress,
and memory. However, it loses source detail before synthesis, uses short-lived
threads without cancellation, and permits unreviewed drafts when review fails.
These are architectural constraints, not a reason to add another agent framework.

Keep the application-owned runtime. Improve evidence/context handling and execution
control in the shared research loop. Compare explicit Flash and Pro IDs under the
same evidence and budgets, then compare other providers when credentials and
provider-native transports are available. No automatic model migration was made.

## Why the UI said “Overridden”

The Next Admin implementation displayed prompt text and a parameter table without
editing controls or a mutation route. “Overridden” meant that a saved database value
replaced the code default. It was not a permission restriction. The Streamlit
predecessor had an editor; the frontend replacement lost that behavior.

The underlying store also needed repair. It loaded overrides once per process,
including a permanently cached failed first read. Saving deleted and rewrote the
entire shared table. Style text was embedded in imported prompt constants rather
than applied from the active configuration at inference time.

The repaired source provides authenticated editing, per-key transactions, scoped
development/production writes, reset markers that suppress legacy defaults, and a
30-second successful refresh cache with retry after a failed read. Admin reads
refresh immediately. Provider credentials and endpoint remain deployment-owned;
the editor controls the model and supported inference settings within that provider.
The API can inspect non-secret model configuration without possessing a model key.

## Global language behavior

The user's “80% ADS-STE100” request is implemented as an **80% ASD-STE100-inspired
style target**. It is a strong preference for simple vocabulary, active voice,
short sentences where useful, stable terms, clear actors, and connected explanations.
It is not a measured compliance percentage or a restricted technical vocabulary.
Research depth and necessary financial/scientific terms remain available.

The shared provider adapters add the active rule to each request, including direct
callers that bypass gateway telemetry. The rule covers human-facing answers,
summaries, captions, explanations, and audio scripts. It preserves numbers, names,
source titles, citations, literal quotes, URLs, code, enums, and machine keys.
The previous regex rewriting of every output string was removed. Existing stored
answers are not rewritten by this change. Prompt-specific task/schema requirements
remain in effect; stale prose-style instructions yield to the global policy.

Eight live controlled outputs were read personally: two models across three research
cases, followed by a second comparison after tightening the shared policy. Cases
covered a business mechanism, conflicting orders/revenue evidence, and correlation
versus causation with literal code/source preservation. Both models kept the main
facts distinct. Flash repeated conclusions and initially added an unrequested audio
script. Pro was generally more direct but initially exceeded a requested two-sentence
audio length. The revised policy improved format discipline; Pro's final answer was
more concise, while Flash still repeated the orders/revenue distinction. This is a
small language review, not proof that either model is the best researcher. The raw
outputs are in [initial review](LANGUAGE_REVIEW_2026-10-07.json) and
[final review](LANGUAGE_REVIEW_FINAL_2026-10-07.json).

## Model and transport findings

| Area | Observed behavior | Consequence |
| --- | --- | --- |
| Live worker route | Both research workers request `deepseek-reasoner`; the actual API reports Flash. | Audit requested and reported identities separately. Use explicit IDs for comparisons. |
| Current DeepSeek offering | The official catalog identifies Flash and Pro families and supports thinking plus tools. | Pro is a relevant alternative; a legacy alias is insufficient selection evidence. |
| Thinking controls | The old adapter discarded configured reasoning effort. | The repaired adapter forwards supported effort and thinking controls; temperature is omitted in thinking mode. |
| Model telemetry | The gateway previously attributed the configured name. | It now records reported identity, usage, finish reason, requested identity, settings scope, and language hash. |
| Repair calls | JSON repair can add an extra model call. | Current metadata does not aggregate repair-call cost separately; accounting remains incomplete. |
| Direct inference | Static AST review found 25 `.generate_json` call sites outside the gateway. | The language rule reaches them at the adapter, but the shared research audit does not cover all calls. |
| API configuration | Development API lacked the worker's non-secret LLM defaults. | Release must mirror routing defaults into the API without copying the worker's secret. |

DeepSeek documents thinking effort and a native tool conversation contract. During
native tool use, required reasoning state must be returned to the provider. The
current JSON-action loop does not use that native tool protocol, so this is a missed
capability, not evidence that today's requests violate a tool-message requirement.
See [models](https://api-docs.deepseek.com/quick_start/pricing/),
[thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/), and
[tool calls](https://api-docs.deepseek.com/guides/tool_calls/).

For a broader comparison, the current official candidates include GPT-6 Astra for
demanding reasoning, Claude Opus 5.5, and Gemini 3.1 Pro. Vendor descriptions and
benchmarks establish candidates, not a winner on retained financial research. No
cross-provider live comparison was performed. The present OpenAI adapter uses Chat
Completions; a model-name change alone does not provide the Responses reasoning/tool
state behavior. Provider-native inference transports can preserve that state while
keeping sessions, permissions, tools, and memory in this application. Sources:
[OpenAI](https://developers.openai.com/api/docs/guides/latest-model),
[Anthropic](https://www.anthropic.com/claude-opus-5-5),
[Google](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-pro/).

## Harness findings

The main implementation is `services/zopedia_agent.py`, approximately 4,470 lines.
It asks a model for a JSON decision, invokes one selected tool, then asks again.
The primary client is reused for planning, trajectory review, synthesis, answer
review, and memory proposals. A second call to the same model with the same reduced
evidence does not independently verify missing source content.

| Priority | Finding | Source evidence | Required shared change |
| --- | --- | --- | --- |
| High | Evidence is shortened before synthesis and review. | Defaults: tool context 4,000 characters, history 3,000 per tool, retained prefetch eight chunks of 300 characters. `_tool_history_prompt` is reused by planner, writer, and judge. | Keep durable full evidence by reference; let the model retrieve exact passages. Compact semantically against the question with explicit source coverage and recoverable originals. |
| High | Review can fail open. | `_run_answer_judge` returns timeout/failure while retaining the draft; missing verdict defaults to `accept`. | Distinguish validated, incomplete, and failed review. The researcher should resolve gaps or produce a supported bounded answer; no malformed-review acceptance. |
| High | Outer timeouts do not stop the underlying work. | Daemon threads for planner, tools, synthesis, monitor, and judge; adapter HTTP timeout can exceed the logical step deadline. | One propagated deadline, cancellable provider/tool execution, durable step state, and no late writes after cancellation. |
| High | The runtime is not fully resumable. | Durable run/progress/tool records exist, but the researcher loop itself is not checkpointed for continuation. Existing May/September plans acknowledge this. | Persist logical execution state and evidence references at each step, then resume under the same authorization and write policy. |
| Medium | Native tool schemas/state are flattened. | Planner receives a textual tool catalog and emits one custom JSON action. | Preserve typed schemas and provider-native reasoning/tool state behind the replaceable transport interface. Keep the owned registry and permission checks. |
| Medium | Semantic sufficiency partly depends on text markers/counts. | `_tool_call_is_low_signal` uses fixed text markers; some confidence limits count evidence tool results. | Let the shared model assess coverage and support against retained source content; code enforces IDs, schemas, budgets, and permissions. |
| Medium | Research-grade calls bypass one audit boundary. | Direct callers remain in extraction, synthesis, critique, attention, and other services. | Route them through the gateway, with real surface/purpose/artifact metadata and one deadline/config contract. |

DeepSeek's open-source harness is an available reference, not something this app
currently uses. Its own repository labels it a rapidly changing developer preview.
There is no product-specific comparison that justifies replacing the existing
research/memory runtime with it. See the
[official repository](https://github.com/deepseek-ai/deepseek-harness). The useful
direction is a smaller, coherent owned research loop with better context and
execution contracts. Semantic compaction and recoverable notes are also consistent
with [Anthropic's context engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

## Next research comparison

Use the same retained source set and explicit model IDs. Include old/new business
context, contradictory figures, insufficient evidence, source opening, long
conversation continuation, and interrupted/resumed work. Disable memory writes
and use a separate audit surface. Read every final answer and the observable source
trajectory; assess factual support, omitted evidence, task completion, prose,
latency, and cost. Do not use word-count tests or a model's self-score as quality
evidence. Change the default only after this comparison and the shared harness
corrections justify it.

## Validation boundary

Plumbing checks cover scope isolation, per-key persistence, resets, retry, runtime
assembly, literal preservation, thinking payloads, and admin authorization. Local
browser acceptance covered edit/save/reset using a loopback account/store fixture.
No verification conversation was inserted into the owner's account, and no retained
Zopedia knowledge was modified. The controlled live probes created gateway telemetry
only. Production release remains separately gated by the owner's explicit approval.
