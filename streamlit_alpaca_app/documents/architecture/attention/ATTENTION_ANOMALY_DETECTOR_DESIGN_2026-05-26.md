# Attention Feed Redesign: Data-Anomaly Detector

Status: design / brainstorm capture (not implemented). Verified against code on 2026-05-26.
Supersedes the scoring approach described in
`ATTENTION_FEED_GENERATION_TRACE_AND_OVERINDEXING_2026-05-19.md` (read that first for how
the current system works and why it overindexes).

## Objective function

> "What should I pay attention to based on the underlying data — regardless of whether or
> not it's covered by the news."

This is a **detection** objective, not a prediction objective. The feed answers *what is
abnormal in the data right now*. It does not forecast where prices go, and it does not
require a news story to surface something.

## Why the current system is wrong for this objective

The shipped feed ranks on an additive, hand-tuned point score
(`services/attention_home_1d.py::_candidate_score`, weights in
`services/runtime_policy.py::attention_candidate_policy`): `move*6 + surprise*11 +
liquidity*6.5 + …`, capped, plus bucketed selection (top 25 gainers / 25 losers / 40
"large"). Consequences, verified empirically:

- It ranks on **raw percent move**, so volatile-by-nature small/mid-caps dominate
  (the solar/AEHR overindexing).
- `must_read_movers` actually **requires same-day news** (`cause_status="supported" AND
  same_day_evidence>0`) — the exact opposite of "regardless of news."
- A routine 21% wiggle in a perennially-volatile microcap (SIDU) outranks a 5σ move in a
  steady large-cap (AZO). The magic weights have no calibration or objective.

## The design: one standardized anomaly score

For each name, today's data is a vector of channels. The name has a "normal" distribution
for that vector over a trailing window. **The standardized distance of today's vector from
that normal is the attention score.** Rank by it; threshold by rarity.

Minimal channel set (start here; add channels later, each is just another dimension):

1. `z_ret` — market-adjusted daily return / the name's own trailing residual vol.
   - residual = `r_t - beta * market_t` (beta from trailing window) → removes market-wide
     moves so a broad selloff doesn't light up everything.
   - standardized by the name's *own* residual vol → a 5% move in a sleepy name scores
     high; a 5% move in a name that always swings 5% does not.
2. `z_vol` — log-volume vs the name's own trailing log-volume distribution.
3. `z_rng` — intraday range `(high-low)/close` vs its own trailing distribution.

Score: `anomaly = sqrt(z_ret^2 + z_vol^2 + z_rng^2)` (diagonal / independent-channel start;
upgrade to full Mahalanobis with shrinkage later only if worth the complexity).

Selection: surface everything past a rarity threshold, ranked by `anomaly`. Under a normal
model the squared distance is ~chi-squared, so the score maps to an interpretable rarity
("a 1-in-400 day for this name") and replaces the arbitrary "top 25" bucket cutoffs.

Hard gates only (not weighted into the score): a consolidated-liquidity floor so
untradeable microcaps don't win on noise, and a minimum-history requirement.

### Look-ahead discipline (load-bearing)

Every baseline statistic (beta, residual vol, volume mean/std, range mean/std) must be
computed from days **strictly before** the scored day (`.shift(1)` on the rolling window).
The score for day t uses only data up to t. This was honored in all prototyping.

## Non-goals: prediction

We tested, on real `price_history` (~994 names, 2021–2026), whether anything reliably
predicts continuation vs reversal after a spike. With a proper time split (train ≤2024-05,
test ≥2024-07, 30-day embargo) and Newey–West t-stats (lag = horizon, on daily
cross-sectional means, to defeat overlapping-window inflation):

- **Direction is unpredictable**: continuation rate is 46–54% in every cell (sigma ×
  direction × volume-confirmation × horizon). Not tradeable per-name.
- There **is** a weak, directionally-significant *descriptive* drift out-of-sample:
  up-spikes continue (+1.5%/20d, t≈3.0), down-spikes rebound (+1.2%/20d, t≈3.3). But it is
  economically small, **regime-dependent** (flat/negative through 2023–24), and ~50% hit
  rate.
- **Not survivorship**: the rebound is strongest in mega-caps (which never delist), where
  survivorship cannot operate — it would be ~0 there if it were an artifact. So it's real,
  just weak and unstable.

Implication: the system must **not** add continuation/reversal/drift terms to selection.
The investigations justified *removing* machinery, not adding it. Detect → rank → show.

## Sustenance: a descriptive tag, not a ranker

Day-over-day the single-day anomaly ranking is unstable (Spearman ≈0.18) — expected, since
it detects transient events. Move sustenance (consecutive same-direction days; multi-day
standardized drift) cleanly separates "day 3 of a developing move" (HLIT) from "one-day
volume blip" (NVRI). Use it as:

- a **confidence / maturity label** ("developing" vs "fresh"), and
- a **feed-stability / de-noising** overlay (e.g. EWMA decay of the daily anomaly) so the
  feed doesn't fully reshuffle daily and doesn't re-alert on blips that already reversed.

It is **not** a return predictor (see non-goals) and must not be sold as one.

## Integration with existing systems (verified 2026-05-26)

### Where it slots into the pipeline

`pipeline/jobs/attention_home_build.py` → `shortlist_attention_symbols_1d` (line ~1866) +
`build_bottom_up_attention_artifacts` (`services/aql/pipeline.py`) →
`build_attention_event_candidates_1d` / `_candidate_score` (`services/attention_home_1d.py`).

The detector **replaces the selection + `_candidate_score` step**: instead of bucketed
percent-move selection and the additive score, compute the anomaly score over the universe
and take the top-N by rarity. Everything downstream of candidate selection (clustering,
graph, bundles, the `home_payload` shape with `top_events` / `must_read_movers` /
`unresolved_large_moves`) can stay — the detector just produces a better-ordered candidate
set. Drop the `same_day_evidence` requirement from `must_read` so news stops gating.

### LLM enrichment = the existing AQL/Zopedia summarizer (no new machinery)

The detector is purely quantitative. LLM stays *after* selection, as enrichment, which is
exactly what already exists and consumes the `home_payload`:

- `services/aql/summarizer.py::build_attention_home_summary` (heuristic + LLM narrative
  beats from `home_payload`).
- `services/aql_zopedia_engine.py::build_aql_zopedia_attention_home_summary_with_trace`
  (agentic summary with evidence retrieval + trace; gated by registered params
  `_P_ATTENTION_SUMMARY_EVIDENCE_ENABLED`, `_P_ATTENTION_SUMMARY_MAX_TOOL_CALLS`).

These take the ranked payload and write the "what / why / context" plus the optional
causal/evidence layer. Causal attribution (the LLM's real job) stays here and remains
optional — a name surfaces because its data is abnormal, with or without a found cause.

### Knobs via the admin config registry

The admin already has a tuning-parameter mechanism (`services/llm.py`:
`register_config_param(name, group, default, description)` → key; `get_config_param(key)`;
overrides persisted to Postgres `llm_prompt_overrides`; rendered by
`views/access_admin.py::_render_llm_config_admin`, line ~1372, grouped `st.number_input`s
with reset-to-default). Register the detector's knobs the same way, in groups e.g.
"Attention / Detection" and "Attention / Selection":

- baseline lookback window (days), default ~120 — *stable knob*: ranking is robust to
  60/120/250 (Spearman 0.73–0.90), so this is low-risk to expose.
- channel toggles (return / volume / range / …) as 0/1 params.
- optional per-channel weights (floats) if moving beyond pure `sqrt(Σz²)`.
- rarity threshold and/or number surfaced per rail.
- consolidated-liquidity floor (hard gate).
- minimum history days (shrinkage trigger).
- sustenance run-length threshold for the "developing" tag (display only).

Two constraints that must be designed around:

1. **The registry is int/float only** (`st.number_input`). String knobs (e.g. feed
   `iex` vs `sip`) don't fit — expose as a 0/1 toggle or a separate small mechanism.
2. **The pipeline process does not load overrides.** `load_prompt_overrides()` is called
   only in `app.py` (the Streamlit/UI process); the `attention-home-build` job never calls
   it, so today a config-param override would **not** reach the scoring code that runs in
   the job. Exposing detection knobs requires either (a) calling `load_prompt_overrides()`
   at the start of the pipeline job, and (b) having the detector read values via
   `get_config_param(...)` (not `os.getenv` + `@lru_cache`, which freezes the first read).
   Without this wiring the admin knobs are inert for the pipeline.

## Open items / caveats

- **Consolidated vs IEX volume**: the universe and movers are built on the IEX feed (~2–3%
  of consolidated, uneven by symbol). The liquidity gate and `z_vol` channel should use
  consolidated/SIP volume or a stored ADV; otherwise the gate is a noisy proxy (this is the
  same root issue flagged in the 2026-05-19 trace).
- **Thin-history / new listings**: shrink the baseline toward a peer/sector estimate, or
  require N days, before scoring.
- **Fat tails**: chi-square rarity p-values are approximate; fine for ranking, don't
  over-trust the exact "1-in-N".
- **Diagonal vs full covariance**: start diagonal; only add Mahalanobis + Ledoit-Wolf
  shrinkage if channel correlation (volume/range co-move) proves to matter.

## Minimal-change summary

- Replace bucketed selection + `_candidate_score` with the anomaly score; keep the
  `home_payload` contract and everything downstream.
- Drop the same-day-news gate on `must_read`.
- Register detection knobs in the existing config registry; wire
  `load_prompt_overrides()` into the pipeline job and read via `get_config_param`.
- Reuse the AQL/Zopedia summarizer unchanged for enrichment.
- Add sustenance as a display tag + optional decay, never as a ranker or predictor.
