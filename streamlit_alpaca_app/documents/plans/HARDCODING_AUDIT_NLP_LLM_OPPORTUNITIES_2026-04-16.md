# Hardcoding Audit — NLP / IR / LLM Replacement Opportunities
_2026-04-16_

This document captures areas across the pipeline where hardcoded keyword lists, symbol tables, if/elif dispatch chains, and fixed thresholds can be replaced with LLM calls, embeddings, or IR techniques. Ordered roughly by impact.

---

## Already Done

- **`omnibar_research.py` — theme/direction/symbol classification**
  Replaced `_query_theme`, `_query_direction`, `_market_impact_rows`, and `query_needs_evidence` with a single `_llm_query_intent` call returning `event_type`, `direction`, `evidence_needed`, and `relevant_symbols`.

---

## High Priority

### 1. Event tag classification — `aql/evidence_index.py` ~L55–98
**What**: 17 hardcoded event type buckets (earnings, m_and_a, clinical_trial, regulatory, etc.) each with a keyword list matched against headline text. Also includes commodity alias lists (oil, gold, copper, lithium, agriculture, etc.) for mention detection.

**Replace with**: LLM structured extraction. One call per article returns `event_type`, `commodity_mentions`, and `confidence`. Handles edge cases and novel event types keyword lists will never cover (e.g. "strategic review", "CEO transition framing as restructuring").

**Impact**: High — this feeds the evidence index that drives attention scoring and narrative generation across the whole pipeline.

---

### 2. Theme keyword matching — `attention_market_events.py` ~L62–82
**What**: Fixed keyword tuples (`_OIL_KEYWORDS`, `_RATE_KEYWORDS`, `_DEFENSIVE_KEYWORDS`, `_RISK_KEYWORDS`) matched via substring against headlines to classify market events into themes. Same 4-category limitation as the omnibar layer had.

**Replace with**: Embedding similarity against theme centroids, or LLM classification. Would allow themes beyond the 4 buckets (e.g. semiconductors, freight, housing) without code changes.

**Impact**: High — theme classification here drives cross-asset narrative routing and which symbols get grouped together in attention cards.

---

### 3. Cross-asset expectation mapping — `attention_market_events.py` ~L337–399
**What**: Nested if/elif chains that hardcode expected directional reactions per theme and direction (e.g. "if oil up, energy equities should be up, airlines should be down"). Used to generate "expected vs observed" narratives.

**Replace with**: LLM reasoning over the event context. Given theme, direction, and asset class, the LLM infers expected reaction and explains it in natural language. No lookup table needed, handles novel combinations naturally.

**Impact**: High — this is the core "what should have moved" logic that drives the quality of attention card narratives.

---

### 4. Narrative template text — `attention_market_events.py` ~L176–195 and `attention_live_research.py` ~L1131
**What**: `_theme_tape_why` and `_event_tape_why_text` return hardcoded strings like "Oil is higher, which points to more supply-risk and more inflation pressure across the tape." Fixed copy per (theme, direction) pair.

**Replace with**: LLM-generated narrative using actual event context (what moved, by how much, what the trigger was). The copy becomes dynamic and grounded in real data rather than a canned sentence.

**Impact**: High — this is user-facing copy on attention cards.

---

### 5. `EVENT_THEME_KEYWORDS` and `EVENT_QUERY_TEMPLATES` — `attention_live_research.py` ~L96–112
**What**: Hardcoded keyword sets used by `_passes_event_relevance_gate` to score article relevance when building attention bundles. Also fixed search query strings per (theme, direction) pair used as web search fallback.

**Replace with**: Same approach as `omnibar_research.py`. LLM extracts search terms and relevant keywords from the event context directly, rather than mapping to a fixed template. The relevance gate scores against those dynamic terms.

**Impact**: Medium-high — affects attention card quality when live web search fallback is used. Same 4-category ceiling as the omnibar layer had before the fix.

---

## Medium Priority

### 6. Symbol universe buckets — `attention_market_events.py` ~L33–52
**What**: Seven hardcoded symbol sets (`_OIL_DRIVER_SYMBOLS`, `_TRAVEL_SYMBOLS`, `_RATE_SYMBOLS`, `_DEFENSIVE_SYMBOLS`, `_BROAD_MARKET_SYMBOLS`, `_ENERGY_EQUITY_SYMBOLS`, `_OIL_DRIVER_SYMBOLS`). Manually maintained lists used to classify assets into theme buckets.

**Replace with**: Embedding-based clustering of assets by return correlation or semantic description, or LLM classification of a ticker's market role given its name and sector. New tickers enter the system correctly without list maintenance.

**Impact**: Medium — affects which symbols get grouped into events and which get per-symbol searches. Currently requires manual updates when new relevant instruments exist.

---

### 7. Company news theme classification — `company.py` ~L70–85
**What**: 14 hardcoded narrative investment themes (AI rollout, data center buildout, product cycle, etc.) with keyword lists matched against news headlines to categorize company news.

**Replace with**: LLM classification with a curated theme taxonomy. The taxonomy itself can be open-ended rather than fixed to 14 categories, and new themes (e.g. "quantum computing", "GLP-1 weight loss") are handled without code changes.

**Impact**: Medium — affects fundamental context cards and company narrative quality.

---

### 8. Omnibar confidence thresholds — `omnibar.py` ~L137–141, L282–290, L309–317
**What**: Magic number thresholds (0.92, 0.7, 0.84, 0.52, etc.) controlling intent classification, search result promotion, and navigate vs. search routing.

**Replace with**: LLM-based re-ranking that reasons over the query and candidate matches, returning a confidence score with justification. More interpretable than tuned floats, and adapts to query phrasing variation.

**Impact**: Medium — affects UX discoverability. Wrong routing causes search when a direct lookup was intended, or vice versa.

---

### 9. Source authority bucketing — `attention_live_research.py` (and shared module) ~L188–199
**What**: Hardcoded token lists (`official_tokens`, `wire_tokens`, `press_tokens`) to bucket news sources by credibility tier. Used to weight evidence quality in ranking.

**Replace with**: LLM or pretrained classifier on source name and domain. Understands source credibility in context (e.g. Reuters vs. a press release vs. a blog) without manual token lists that miss new sources.

**Impact**: Low-medium — affects evidence ranking quality. Current lists miss sources not on the whitelist.

---

### 10. Macro surprise thresholds — `aql/macro.py` ~L324, L393
**What**: Hardcoded `surprise_threshold = 60.0` and `importance_tier == "high"` gate controlling which macro releases get promoted to top attention events.

**Replace with**: Dynamic thresholds informed by market regime (a 60bps CPI surprise matters differently in a hiking cycle vs. stable rates), or LLM assessment of whether a release result is likely to be market-moving given current context.

**Impact**: Low-medium — affects which macro events surface prominently.

---

## Lower Priority / Cleanup

### 11. SEC filing detection — `attention_live_research.py` ~L150–163
5 hardcoded regex patterns to detect and filter generic SEC filing references from narrative context. Fine for now, but an LLM classifier would handle edge cases (e.g. a filing reference that's actually material news).

### 12. Sector whitelist — `entity_taxonomy.py` ~L54–71
14 hardcoded allowed sectors for taxonomy normalization. Low risk of missing sectors but adds friction when new GICS subcategories appear.

---

## Approach Notes

- **LLM calls**: Best for classification, extraction, and narrative generation where semantic understanding matters. Add to existing `generate_json` pattern with structured output schemas.
- **Embeddings**: Best for similarity matching (symbol clustering, article-to-theme matching). Infrastructure already exists in `knowledge_graph.py`.
- **Replacement order**: Start with items that affect main pipeline quality and are user-facing (items 1–5). Items 6–10 are maintenance burden reducers more than quality improvements.
- **Backward compatibility**: For each replacement, keep the existing logic as a clearly-labeled fallback for LLM-unavailable environments (as done in `_llm_query_intent`), then remove once stable.
