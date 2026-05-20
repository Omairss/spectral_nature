# Zopedia Difficult Question Gap Report

Date: 2026-05-17

Raw probe output:

- `documents/architecture/new_features/zopedia/question_probes/zopedia-difficult-question-probe-20260517.md`
- `documents/architecture/new_features/zopedia/question_probes/zopedia-difficult-question-probe-20260517.json`

Probe command:

```bash
streamlit_alpaca_app/.venv/bin/python -u streamlit_alpaca_app/scripts/zopedia_question_probe.py \
  --tag zopedia-difficult-question-probe-20260517 \
  --question-timeout-seconds 240 \
  --max-tool-calls 10
```

## Summary Verdict

The system can produce fluent answers, and the false-information test was strong. But the hard questions exposed the main gap:

**The agent often stops after one or two plausible tools instead of proving that the evidence covers the whole question.**

This is not a hardcoding problem. The fix should not be keyword routing. The fix is an evidence contract:

1. The planner creates a research plan from the question and live tool catalog.
2. The run records which evidence slots were actually filled.
3. A sufficiency gate checks whether the final answer is allowed.
4. If evidence is incomplete, the answer must say so or continue tool use.

## Question Results

| Question | Tools Used | What Worked | Main Gap |
| --- | --- | --- | --- |
| NVDA fundamentals vs narrative | `investigator.company_context`, `investigator.fundamentals`, `investigator.recent_news` | Correctly used company context, fundamentals, and news. | Did not search retained Spectral Nature updates or Zopedia memory. Answer used broad claims without explicit numeric support or source links. |
| Lower yields and small caps | `dataset.yield_curve_facts_1d` | Used local yield-curve data. | Did not fetch small-cap evidence, credit spreads, equity performance, or macro context. Answer became mostly generic relationship reasoning. |
| False CPI/unemployment claim | `dataset.fred_dashboard` | Strong result. It checked local official data and rejected the false premise. | Minor gap: if an article URL/text is provided, the system should also inspect that source before judging why it is wrong. |
| Stale wiki claim about power constraints | `zopedia.search_pages`, `research.live_event_evidence` | Correctly identified that Zopedia lacks the exact page/claim. | Did not read pages before relying on search results. Did not create a reviewable `zopedia.propose_change` proposal. Live evidence timed out. |
| Oil spike + credit spreads + AI capex | `research.market_impact_map` | Produced a coherent second-order risk map. | Too little evidence. It did not fetch oil, credit spread, rates, equity, AI capex, retained memory, or Zopedia graph context. This is the clearest "sounds smart but under-evidenced" failure. |

## Gaps

### 1. No Evidence Coverage Matrix

The system records tool traces, but it does not explicitly track whether each part of a user question has evidence.

Example:

The small-cap question has at least three evidence needs:

- Treasury yield direction
- small-cap/risk appetite behavior
- failure mode through growth or credit stress

The run only fetched yield-curve facts, but the answer covered all three.

Fix, without hardcoding:

- Add an LLM-generated `research_plan` object before tool calls:
  - `claims_to_answer`
  - `required_evidence`
  - `candidate_tools`
  - `stop_conditions`
- After tools run, add a generic sufficiency check:
  - `covered`
  - `partially_covered`
  - `missing`
  - `can_answer`
- The final answer can only be high confidence if important evidence slots are covered.

### 2. Tool Presence Is Being Treated As Enough

For complex questions, one tool call can lead to an answer that reads complete.

Example:

The oil/credit/AI capex question used only `research.market_impact_map`. That tool can map a theme, but it is not enough evidence for an answer about current market risks.

Fix, without hardcoding:

- Classify tools by evidence strength in the tool catalog metadata:
  - `planner`
  - `local_dataset`
  - `retained_evidence`
  - `live_evidence`
  - `wiki_memory`
  - `verification`
- Do not allow `planner` or expansion tools alone to satisfy a final answer unless the answer is explicitly labeled as a hypothesis map.

### 3. Zopedia Search Is Not Always Followed By Page Read

The stale wiki question used `zopedia.search_pages`, then answered from titles/summaries.

Fix, without hardcoding:

- Add a generic rule to the planner contract: search results are navigation, not evidence.
- Add a sufficiency gate: any final answer that cites or critiques Zopedia memory must include `zopedia.read_page` unless no matching page exists.
- If no page exists but the user says the wiki contains a claim, the answer should say the page was not found and offer a reviewable proposal to add or investigate the missing page.

### 4. Wiki Update Path Is Too Passive

The stale wiki question should have produced a reviewable proposal. It did not.

Fix, without hardcoding:

- Add a generic memory-maintenance contract:
  - If the user asks what should change in the wiki, the run must end with either:
    - a `zopedia.propose_change` call, or
    - a clear refusal because evidence is insufficient, plus a proposed source-ingest/investigation step.
- Extend proposal payloads to support:
  - `add_page`
  - `update_page`
  - `delete_or_archive_page`
  - `add_link`
  - `remove_link`
  - `investigate_missing_page`
- Keep all writes reviewable.

### 5. Source Links Are Not Preserved Well Enough

All five probe rows had `source_links=0`, even when recent news or live evidence was involved.

Fix, without hardcoding:

- Replace tool-specific link extraction with a generic recursive source extractor.
- Any nested dict/list with fields like `url`, `source`, `title`, `headline`, `published_at`, or `published_date` should become a source ref.
- Evidence packs should preserve these refs so the final answer can cite concrete sources.

### 6. Final Answer Confidence Is Too Easy

Some answers are fluent and plausible, but confidence does not reflect missing evidence.

Fix, without hardcoding:

- Add an answer judge after synthesis:
  - Does each major claim have evidence?
  - Did the answer use only tools actually called?
  - Did it overstate confidence?
  - Did it mention missing evidence?
- If the judge fails, either:
  - ask the planner for more tools, or
  - downgrade the answer and list gaps.

### 7. Live Evidence Reliability Still Matters

The wiki/power-constraint question hit a live evidence timeout. The final answer handled the limitation, but the product still felt blocked.

Fix, without hardcoding:

- Keep per-tool timeouts.
- Add source fallback providers where possible.
- Preserve a reviewable `research_gap` proposal when live evidence fails during a wiki-update request.
- Surface timeout as an evidence gap, not as a final dead end.

## Go / No-Go

### Go

- False-information resilience for local macro facts is promising.
- The agent can use company tools correctly when the planner selects them.
- The probe infrastructure now has hard process timeouts and writes durable artifacts.

### No-Go

Do not claim Zopedia is "Zopedia-quality" for complex research yet.

The system still needs a generic evidence coverage and sufficiency layer before it can reliably answer hard multi-domain questions without sounding more confident than its evidence supports.

## Recommended Next Build

Build **Zopedia Evidence Contracts v1**:

1. Planner emits `research_plan`.
2. Tool catalog includes evidence-strength metadata.
3. Tool results fill `evidence_slots`.
4. Sufficiency judge decides whether the answer can ship.
5. Final answer cites evidence refs and lists missing slots.
6. Wiki-change questions must produce reviewable proposals or explicit evidence-gap proposals.

This is the right fix because it improves reasoning quality without adding hardcoded domain routing.
