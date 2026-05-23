# Attention Feed: Generation Trace + Overindexing Diagnosis

Verified against code on 2026-05-19. This documents how each homepage feed item is
actually produced today (not the aspirational redesign), and answers whether the
overindexing on names like solar and AEHR is hardcoding/sampling or real market dynamics.

Short answer on overindexing: **not hardcoded**. There are no solar/AEHR symbol lists
anywhere in the code. It is a **selection + scoring artifact** layered on top of a
genuine market signal. The system equates "biggest percent mover" with "most important
to read," and under-weights liquidity / market importance, so whichever high-volatility
theme moved most that day (solar, AI semis, crypto miners) floods the feed.

## End-to-end generation trace

### Layer 0 — Upstream universe + movers (`pipeline/jobs/main.py::run_equities`)

1. `_resolve_equity_symbols` resolves the equity universe via
   `services/universe.py::build_liquidity_ranked_equity_universe`:
   - all US common stocks (ETFs and non-common excluded by default)
   - filters: `min_price=$5`, `min_volume=100,000 sh`, `min_dollar_volume=$5M`
   - ranks survivors by dollar volume, then **pads up to `target_size=1000`** with
     symbols that *failed* the filters, tagged `selection_reason="liquidity_fallback"`
   - all snapshots use the **IEX feed** (`feed="iex"`)
   - persisted as `universe_snapshot`
2. `scan_daily_movers(api, symbols=universe)` builds `daily_movers`
   (`symbol, close, prev_close, change_pct, volume`), IEX feed.
3. `resolve_macro_anchor_symbols(symbols)` selects taxonomy-tagged macro anchors
   (commodity/rates/defensive role or macro tags) → `macro_anchor_daily_movers`.
4. `positions_snapshot` captures portfolio holdings.

### Layer 1 — Shortlist + enrichment (`pipeline/jobs/attention_home_build.py`)

5. `daily_movers` + `macro_anchor_daily_movers` are concatenated and deduped → `movers`.
6. `shortlist_attention_symbols_1d(movers, holdings, attention_rows, max_count=100)`
   (`services/attention_home_1d.py`) builds the candidate set, in this priority order:
   - top 20 macro anchors by `|change_pct|`
   - **top 25 losers** (most negative `change_pct`)
   - **top 25 gainers** (largest `change_pct`)
   - **top 40 "large" movers** (`|change_pct| >= 4%`)
   - top 20 holdings by `|change_pct|` — **bypass the liquidity floor**
   - top 25 attention-seed rows by `attention_score`
   - liquidity gate: `dollar_volume >= $25M` (IEX) **or** is a macro anchor
   - hard cap of 100 symbols
7. Enrichment: `entity_master` (taxonomy), 120d bars, news payloads (from
   `news_articles` + web-search backfill for missing names), context payloads, FRED,
   yield-curve facts.

### Layer 2 — Candidates, research, events (`services/aql/pipeline.py::build_bottom_up_attention_artifacts`)

8. `build_attention_event_candidates_1d` builds one candidate row per shortlisted symbol:
   - `change_pct`, 20-day expected move, `surprise_pct`, `surprise_z`
   - evidence rows (news + context + filings), `cause_status`, `confidence_label`
   - **`candidate_score`** (see scoring below). Frame is sorted by `candidate_score` desc.
9. The top `research_limit` (default 12) candidates get full agentic research
   (plan → web search → documents → chunks → claims → judge → bundle). The rest get
   heuristic bundles. Symbol bundle id = `symbol::<SYM>`.
10. `_graph_edges` + `_cluster_candidates` group candidates into event clusters;
    `_build_event_bundle` writes each event. Macro release events come from FRED.
11. `_build_home_payload` (`services/aql/assembler.py`) assembles the three rails:
    - **`top_events`**: top 5 clusters by `event_score` (+ forced macro releases)
    - **`must_read_movers`**: top 10 candidates with `cause_status="supported"` and
      `same_day_evidence_count > 0`, not already absorbed into a top event; sorted by
      `(same_day_evidence_count, candidate_score, |change_pct|)`
    - **`unresolved_large_moves`**: top 5 candidates with `|change_pct| >= 4%` that are
      neither supported nor absorbed; sorted by `(candidate_score, |change_pct|)`
    - dedup is **symbol-level only** (a symbol inside a top event is removed from the
      mover rails). There is **no sector/theme/issuer-group diversity cap**.
12. Materialized as `attention_home_1d` / `attention_home_snapshots_1d` (plus graph,
    ticker snapshots, claims, search trace, bundles).

### Layer 3 — UI

13. `presentation/dashboard_loaders.py::_load_attention_home_1d` → `resolve_attention_home_1d`
    deserializes the payload; `presentation/attention_content.py` renders the three rails
    and lazy drilldown bundles.

### candidate_score (the ranking that decides the feed)

From `attention_home_1d.py::_candidate_score`, with defaults from
`runtime_policy.py::attention_candidate_policy`:

| Component   | Formula                                              | Max |
|-------------|------------------------------------------------------|-----|
| move        | `min(|Δ%| * 6, 75)`                                  | 75  |
| surprise    | `min(|surprise_z| * 11, 45)` (fallback `|Δ%|*2.5≤18`)| 45  |
| liquidity   | `min(max(log10($vol) - 6, 0) * 6.5, 26)`             | 26  |
| evidence    | base 6 + per-item, + authority bonus                 | ~22 |
| attention   | `min(attention_score * 0.15, 18)`                    | 18  |
| macro_bonus | +10 if macro anchor                                  | 10  |
| portfolio   | +6 if held                                           | 6   |

Move + surprise alone reach 120 points and dominate; liquidity is capped at 26 and is
logarithmic, so it barely separates a $10M name from a $10B name.

## Overindexing diagnosis (verified, not hardcoded)

Confirmed by grep: no `AEHR`, `solar`, `FSLR`, `ENPH`, etc. anywhere in the pipeline,
no `UNIVERSE_SYMBOLS` override, no `pinned_symbols`. The cause is structural:

**A. Selection samples by raw percent move.** Gainers/losers/large rails are sorted on
`change_pct`. High-volatility small/mid-caps produce the biggest daily percent swings,
so they are structurally over-sampled into the shortlist.

**B. Scoring under-weights liquidity / importance.** Empirically, on the 2026-05-20
1000-symbol universe cross-section, the top 30 by replicated `candidate_score` had a
**median 38 move+surprise points vs 10.7 liquidity points**. NVDA — the single most
liquid name — does not appear because it moved <1%, while ALAB (+13.2%) tops the list at
106 points. The feed therefore ranks "how much it moved in percent," not "how much it
matters."

**C. No theme diversity cap.** When a whole volatile theme moves together (solar selloff,
AI-semis rip, crypto-miner day), several of its names clear the same thresholds and fill
`must_read`/`unresolved` simultaneously, because dedup is symbol-level only.

**D. `liquidity_fallback` padding.** On that snapshot, **337 of 1000** universe symbols
entered via `liquidity_fallback` — i.e. they *failed* the liquidity filters and were
padded in to hit the 1000 target. AEHR and FSLR are exactly these: their IEX volume
(~90k shares) fell under the 100k floor. Padded-in low/mid-liquidity names then become
eligible to surface on big-move days.

**E. IEX-only volume.** All volume/`dollar_volume` is measured on the IEX feed
(~2-3% of consolidated volume), and IEX share differs by symbol. So both the universe
liquidity ranking and the `$25M` shortlist floor operate on a noisy, biased proxy for
true liquidity, further distorting which names are treated as "liquid enough."

**Verdict.** The signal is real — these names genuinely had the largest percent moves —
but the *prominence* is an artifact. The system conflates volatility with importance and
has no counterweight (market cap, consolidated liquidity, breadth, or per-theme caps),
so volatile low/mid-cap themes are over-represented relative to their market significance.

## Suggested improvements (ordered by impact / effort)

1. **Fix the liquidity proxy (highest impact, low effort).** Stop ranking and gating on
   IEX-only volume. Use consolidated/SIP volume, or a stored average dollar volume
   (e.g. 20-day ADV), instead of a single IEX day. This alone removes the
   `liquidity_fallback` distortion and makes the `$25M` floor mean what it says.

2. **Rebalance `candidate_score` toward importance.** Either raise the liquidity
   component cap/weight, or multiply move/surprise by a liquidity (or market-cap) factor
   so a +13% move in a $30M ADV name cannot outrank a -4% move in a $5B ADV name. Make
   these weights explicit policy knobs (already env-driven) and tune on golden days.

3. **Add a theme/issuer diversity cap on the mover rails.** Limit how many names from the
   same `sector`/`industry`/`peer_group_id` can occupy `must_read`/`unresolved` (e.g. max
   2), promoting the strongest as a single representative and rolling the rest into a
   "others in this theme" line. The redesign doc already wants events to absorb peers;
   the cap enforces it when no event cluster formed.

4. **Replace `liquidity_fallback` padding with a smaller, honest universe.** If fewer than
   `target_size` names pass real liquidity filters, ship a smaller universe rather than
   padding with sub-threshold names. Padding to a round 1000 is what injects the low-
   liquidity tail that later overindexes.

5. **Separate "biggest movers" from "most important."** Consider an explicit
   importance/notability score (liquidity x breadth x portfolio/macro relevance) used for
   ranking the rails, while still keeping a raw "biggest movers" strip for completeness.
   This matches the product goal ("what the user needs to read today") better than a pure
   percent-move sort.

## Doc inconsistencies found and fixed

- `ATTENTION_FEED_EVENT_REDESIGN_PLAN.md` Coverage Model said "top ~1500 liquid US
  equities by dollar volume." Actual default is `EQUITY_UNIVERSE_TARGET_SIZE=1000`, and
  the universe is padded with a sub-threshold `liquidity_fallback` tier on the IEX feed.
  Corrected inline and cross-referenced to this trace.
- The redesign/implementation plans describe many standalone datasets
  (`attention_event_feed_1d`, `attention_must_read_movers_1d`, the Layer 1–9
  `attention_*_1d` contracts). Those are **forward-looking targets**. The shipped system
  produces a single `attention_home_1d` payload (with embedded `top_events`,
  `must_read_movers`, `unresolved_large_moves`) plus `attention_candidates_1d` and the
  agentic trace datasets. The plans are left as plans but this gap is noted here.
