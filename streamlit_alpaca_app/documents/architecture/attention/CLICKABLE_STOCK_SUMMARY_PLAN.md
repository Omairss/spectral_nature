# Clickable Attention Stock Summary — Deep-Dive on Click

Status: TODO

## Goal

Make individual sentences and concepts in the attention stock summary clickable. Clicking a sentence launches a Zopedia query for "past context in light of new information" — explaining what changed, what the historical backdrop is, and why it matters now.

## User Experience

1. Each bullet point or key sentence in a stock summary renders as a clickable element
2. On click, a Zopedia query fires: *"Given that [selected sentence], explain the past context and how this changes the picture for [SYMBOL]"*
3. The result renders inline (expander below the clicked sentence) or in a side panel

## Design Options

### Option A: On-click (lazy load)

- Simpler to build — no pipeline changes
- ~10-15s latency per click while Zopedia runs
- Only generates follow-ups the user actually wants
- Uses `run_omnibar_agent` with the sentence as query context

### Option B: Pre-computed in pipeline

- Zero latency on click — instant render
- Requires parsing the summary into key claims during enrichment
- Multiplies LLM calls: ~5-10 sentences per ticker × 15 tickers = 75-150 extra calls per run
- Stored as nested JSON in `attention_ticker_zopedia_enrichments` dataset
- Follows the pipeline-first enrichment philosophy

### Recommendation

Start with Option A (on-click) to validate the UX. If users click frequently and the latency is painful, migrate to Option B. The on-click approach lets us iterate on what makes a good follow-up query without burning pipeline compute on unused outputs.

## Implementation Sketch

### Parsing sentences from summary

```python
def _extract_clickable_claims(sections: list[tuple[str, str]]) -> list[dict]:
    """Extract key sentences/bullets from parsed sections."""
    claims = []
    for heading, body in sections:
        for line in body.split("\n"):
            line = line.strip().lstrip("- ")
            if len(line) > 30:  # skip short fragments
                claims.append({"text": line, "section": heading})
    return claims
```

### Rendering

Each claim renders as a small button. On click, set session state and trigger a Zopedia query in a fragment rerun.

### Query template

```
Given the following observation about {SYMBOL}:
"{selected_sentence}"

Explain the past context: what was the situation before this, how does this change the picture, and what should an investor watch for next?
```

## Files to modify

- `app.py` — `_render_zopedia_ticker_result()`: parse claims, render as clickable elements
- `app.py` — new fragment function for on-click Zopedia follow-up
- `services/omnibar_agent.py` — may need a lighter query mode (fewer tool calls) for follow-ups
