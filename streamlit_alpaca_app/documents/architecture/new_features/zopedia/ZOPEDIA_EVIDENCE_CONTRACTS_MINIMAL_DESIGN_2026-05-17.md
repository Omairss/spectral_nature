# Zopedia Evidence Contracts Minimal Design

Date: 2026-05-17

## Goal

Fix the difficult-question gap without adding hardcoded routing or a large new agent framework.

The problem is not that Zopedia lacks tools. The problem is that the agent can stop after one plausible tool and then write a complete-sounding answer. The fix is a small evidence sufficiency layer between tool collection and final synthesis.

## Design Principle

**Do not classify user questions in code.**

No keyword lists for company, macro, current, broad market, AI, rates, oil, or anything similar.

Instead:

- The LLM planner interprets the question.
- The live tool catalog tells it what tools exist.
- Tool metadata says what each tool can prove.
- A sufficiency judge checks whether the collected evidence covers the answer.

This keeps product behavior LLM-native while still preventing unsupported answers.

## Best Minimal Architecture

```text
User question
  -> existing Zopedia planner loop
  -> tool calls
  -> planner attempts final
  -> evidence sufficiency check
       if enough evidence:
         final synthesis
       if missing evidence and budget remains:
         execute the judge's next tool suggestion
         return to planner loop
       if missing evidence and budget exhausted:
         final synthesis with forced limitations + lower confidence
```

This is better than adding a separate up-front research-plan step because it only adds one extra model call when the agent is ready to answer. It also lets easy questions stay cheap.

## New Component

Add one small module:

```text
services/aql/evidence_contract.py
```

Responsibilities:

1. Build a compact evidence matrix from `tool_calls`.
2. Ask a structured LLM judge whether the evidence is sufficient.
3. Return:
   - `can_answer`
   - `confidence_ceiling`
   - `covered_claims`
   - `missing_claims`
   - `required_disclosures`
   - optional `next_tool_name`
   - optional `next_tool_arguments`

No database table is needed for v1. Store the result inside the AQL evidence pack.

## Minimal Schema

```json
{
  "can_answer": true,
  "confidence_ceiling": "medium",
  "covered_claims": [
    {
      "claim": "Latest official CPI is not near 9%.",
      "supporting_tool_call_ids": ["agtc_1"]
    }
  ],
  "missing_claims": [
    {
      "claim": "Whether small caps are currently rallying with lower yields.",
      "why_missing": "No small-cap market evidence was collected."
    }
  ],
  "required_disclosures": [
    "Small-cap price action was not checked."
  ],
  "next_tool_name": "dataset.fred_dashboard",
  "next_tool_arguments": {
    "years": 3
  }
}
```

The judge invents the claim/evidence slots from the question. Code does not define domain-specific slots.

## Tool Metadata

Add generic metadata to tool definitions in `services/agent_tools.py`.

Example:

```json
{
  "name": "zopedia.search_pages",
  "evidence_role": "navigation",
  "can_support_final_claims": false
}
```

Suggested roles:

- `navigation`
- `local_dataset`
- `retained_evidence`
- `live_evidence`
- `wiki_page`
- `expansion`
- `verification`
- `proposal`

This is not query hardcoding. It is tool capability metadata.

Important examples:

- `zopedia.search_pages`: navigation, not final evidence.
- `zopedia.read_page`: wiki evidence.
- `research.market_impact_map`: expansion/hypothesis map, not enough by itself for current factual claims.
- `dataset.*`: local dataset evidence.
- `investigator.fundamentals`: local company fundamentals evidence.
- `zopedia.propose_change`: reviewable memory mutation evidence.

## Agent Integration

The smallest useful change is in `services/omnibar_agent.py`.

When the planner returns `action="final"`:

1. Run `assess_evidence_sufficiency(...)`.
2. If `can_answer=true`, proceed to final synthesis.
3. If `can_answer=false` and `next_tool_name` is valid, non-duplicate, and budget remains, execute that tool and continue.
4. If `can_answer=false` and budget is exhausted, final synthesis must include `required_disclosures`, and confidence cannot exceed `confidence_ceiling`.

This avoids adding a pre-planning call and avoids turning the agent into a multi-agent system.

## Source Reference Extraction

Add a generic recursive source extractor to `_summarize_tool_result`.

It should scan nested dict/list structures for fields like:

- `url`
- `source`
- `title`
- `headline`
- `published_at`
- `published_date`
- `canonical_document_id`
- `chunk_record_id`
- `page_id`
- `proposal_id`

This is generic data extraction, not domain routing.

Why this matters:

The difficult-question probe showed `source_links=0` even when news/live evidence was involved. The final answer cannot cite well if source refs are lost before synthesis.

## Wiki Update Handling

Do not add special code that detects words like "wiki", "change", or "stale".

Instead, the sufficiency judge reads the user's question and tool history. If the task asks for memory maintenance, the judge should require one of:

- `zopedia.propose_change`
- a clear evidence-gap disclosure
- a reviewable `investigate_missing_page` proposal

Add `investigate_missing_page` as a proposal type, not as an automatic write.

## Why This Is Complexity-Minimizing

Rejected options:

- **Hardcoded deterministic routers:** brittle and against the product rule.
- **Full agent framework rewrite:** too much complexity before proving the guard works.
- **Per-domain validators:** company/macro/wiki/oil/rates validators would grow into hidden hardcoding.
- **Up-front research plan for every query:** useful later, but adds latency to simple questions.

Chosen option:

- One small sufficiency module.
- One generic tool metadata extension.
- One integration point when planner attempts final.
- One evidence-pack extension.
- Existing eval harnesses continue to work.

## Implementation Sequence

### Step 1: Tool Metadata

Add `evidence_role` and `can_support_final_claims` to the existing tool catalog.

No behavior change yet.

### Step 2: Source Ref Extraction

Replace narrow link extraction with generic recursive evidence-ref extraction.

This improves citations immediately and is low risk.

### Step 3: Sufficiency Judge

Implement `assess_evidence_sufficiency`.

Use the named Zopedia runtime boundary:

```text
load_zopedia_llm_client(surface="zopedia.evidence_contract")
```

No raw LLM loader.

### Step 4: Agent Gate

Call the judge only when the planner wants to finalize.

This keeps easy queries fast and prevents under-evidenced final answers.

### Step 5: Evidence Pack

Store the sufficiency result in `aql_evidence_pack`.

This makes later UI/debug/eval work easier without creating a new table.

### Step 6: Evals

Update `zopedia_question_probe.py` so "completed" is not enough.

A hard question passes only when:

- `can_answer=true`, or the answer clearly says it is incomplete.
- important claims have supporting tool refs.
- `navigation` tools alone do not count as evidence.
- wiki-change questions produce a reviewable proposal or explicit investigation gap.

## Expected Impact On The Five Probe Questions

### NVDA Fundamentals vs Narrative

Current behavior: good company tools, but no retained history or source refs.

With evidence contract:

- Judge sees the answer compares "fundamentals" vs "narrative."
- If fundamentals are present but retained/history is missing, it either asks for retained evidence or forces a limitation.

### Lower Yields And Small Caps

Current behavior: yield curve only.

With evidence contract:

- Judge marks small-cap behavior and credit conditions as missing.
- It asks for a local macro/market dataset or retained evidence before allowing a high-confidence answer.

### False CPI / Unemployment Claim

Current behavior: strong.

With evidence contract:

- Judge allows answer from FRED because the question is about checking official current data.
- Confidence can stay high.

### Stale Wiki Power Claim

Current behavior: search only, no proposal.

With evidence contract:

- Search result is navigation, not evidence.
- If no page is read and no proposal is made, judge blocks final or forces a clear investigation/proposal path.

### Oil + Credit Spreads + AI Capex

Current behavior: market impact map only.

With evidence contract:

- Market impact map is an expansion tool.
- Judge requires actual evidence before allowing a complete answer, or labels the output as a hypothesis map.

## Success Criteria

The next difficult-question probe should show:

- Fewer one-tool answers for multi-part questions.
- `source_links` or evidence refs preserved when source-like data exists.
- Lower confidence when evidence is incomplete.
- Wiki update questions ending in proposals or explicit investigation gaps.
- No code-level query keyword routing.

## Final Recommendation

Build Evidence Contracts v1 before adding more Zopedia features.

This is the smallest change that directly attacks the core quality gap: answers that sound complete before the system has earned that confidence.
