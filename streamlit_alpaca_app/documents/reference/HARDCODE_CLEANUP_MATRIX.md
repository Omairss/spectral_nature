# Hardcode Cleanup Matrix

## Scope
This matrix tracks runtime-significant hardcoded behavior in the Streamlit app and supporting services.
It excludes simple schema column lists and purely presentational text unless they materially affect runtime behavior.

## Decision Legend
- `KEEP`: Keep hardcoded in code as stable internal contract.
- `CONFIG`: Move to environment or persisted config.
- `DYNAMIC`: Generate from data/jobs/models at runtime.

## Matrix

| Area | Hardcoded Element | Current Hardcoded Description | Location | Decision | Target State | Priority |
|---|---|---|---|---|---|---|
| Taxonomy | Sector vocabulary (`ALLOWED_SECTORS`) | Taxonomy rows are normalized against a fixed in-code sector list. | `services/entity_taxonomy.py` | `CONFIG` | Store allowed taxonomy vocab in DB config table or versioned JSON config blob. | P1 |
| Taxonomy | Source/confidence merge priority maps | Row merge precedence is fixed by static source and confidence priority dicts. | `services/entity_taxonomy.py` | `KEEP` | Keep as deterministic merge policy; document as contract. | P2 |
| Taxonomy | LLM schema constraints (`maxItems`, `minItems`, required tags) | Classifier output shape and tag cardinality are hardwired in code. | `services/entity_taxonomy.py` | `CONFIG` | Externalize to taxonomy-classifier config (DB/blob) with versioning. | P1 |
| Taxonomy | LLM prompts and classifier version string | Prompt text and `classifier_version` are embedded literals in the job path. | `services/entity_taxonomy.py` | `CONFIG` | Prompt templates and model profile in job config table keyed by version. | P0 |
| Taxonomy | Default country = `US` | New rows default country to `US` regardless of listing provenance. | `services/entity_taxonomy.py` | `CONFIG` | Set by listing source profile; default from config. | P2 |
| Market modeling | Default equity universe | Many scans start from a fixed list of large-cap symbols. | `services/market.py` | `DYNAMIC` | Use liquidity-ranked universe snapshot and/or user scope, not static symbol list. | P0 |
| Market modeling | Business focus universes + descriptions | Focus groups and membership are static symbol arrays plus static text descriptions. | `services/market.py` | `DYNAMIC` | Read peer-group taxonomy from DB rows produced by monthly taxonomy job. | P0 |
| Anomaly modeling | Peer-group membership generation | `build_peer_group_membership` and commodity membership still enumerate static universes. | `compute/anomalies.py` | `DYNAMIC` | Build `peer_group_membership` from taxonomy labels (`industry`, `sector`, `business_role_tags`, commodity roles) with versioned snapshots. | P0 |
| Anomaly modeling | Drilldown filter routing | Drilldown params map business filters through static focus universe names. | `compute/anomalies.py` | `DYNAMIC` | Resolve drilldown filters from a taxonomy-backed peer-group catalog published with each run. | P1 |
| Rollups | Taxonomy hierarchy rollups | Rollups stop at market/portfolio/current peer group and do not emit taxonomy lineage levels. | `compute/anomalies.py` | `DYNAMIC` | Publish rollups by taxonomy hierarchy (`sector`, `industry`, `business_role`) with stable lineage IDs. | P1 |
| Commodities | Commodity proxy metadata | Commodity ETF/proxy identity and descriptions are fixed in one dictionary. | `services/market.py` | `CONFIG` | Move to managed reference dataset (`commodity_reference_metadata`). | P1 |
| Commodities | Commodity focus groups + dependency edges | Commodity thematic buckets and relation edges are fixed in code. | `services/market.py` | `CONFIG` | Move to configurable reference dataset (`commodity_focus_graph`). | P1 |
| Universe builder | Listing URLs + symbol regex + security token filters | Provider endpoints, symbol pattern, and exclusion tokens are fixed literals. | `services/universe.py` | `CONFIG` | Source registry config per provider (Nasdaq Trader, others). | P2 |
| Universe builder | Liquidity thresholds (`min_price`, `min_volume`, `min_dollar_volume`) | Universe admission thresholds are defaulted to fixed numeric values in function args. | `services/universe.py` | `CONFIG` | Job/runtime config with environment overrides and defaults in one place. | P0 |
| Universe builder | Curated pinned symbols fallback | If no custom pins are passed, the builder injects a static curated pin set. | `services/universe.py` | `DYNAMIC` | Replace with deterministic fallback from latest ranked snapshot only. | P0 |
| Attention candidateing | Macro anchor symbols list | Candidate shortlisting reserves slots for a static macro anchor ticker list. | `services/attention_home_1d.py` | `DYNAMIC` | Derive from taxonomy roles/tags and recency criteria instead of fixed ticker list. | P0 |
| Attention candidateing | Source authority token lists | Source classification relies on fixed keyword lists for official, wire, and press. | `services/attention_home_1d.py`, `services/attention_agentic.py` | `CONFIG` | Shared authority profile config file/table used by both services. | P1 |
| Attention candidateing | Quality and confidence thresholds | Confidence labels and scoring branches use fixed cutoffs and coefficients. | `services/attention_home_1d.py` | `CONFIG` | Single scoring config object loaded at runtime and versioned. | P0 |
| Attention candidateing | Liquidity cutoff (`25_000_000`) and shortlist caps | Liquid mover gating and shortlist size have fixed numeric gates. | `services/attention_home_1d.py` | `CONFIG` | Move to candidate-selection config with environment/profile overrides. | P0 |
| Attention surface | 1d-only symbol-first home layout | Home attention payload focuses on daily movers/events and omits taxonomy cohort trend surfaces across longer horizons. | `services/attention_agentic.py`, `services/attention_home_1d.py`, `app.py` | `DYNAMIC` | Add taxonomy cohort trend bundles and multi-horizon cards (`1w`, `1mo`, `3mo`, `1yr`) alongside daily tape. | P0 |
| Graph building | Edge weights and threshold (`0.34`, `0.18`, `0.42`, etc.) | Pairwise edge construction uses hardcoded additive weights and fixed acceptance cutoff. | `services/attention_agentic.py` | `CONFIG` | Versioned graph scoring profile, selectable per run. | P0 |
| Graph building | Writer prompt version and gating thresholds | Planner/writer prompt version and claim gating thresholds are fixed literals. | `services/attention_agentic.py` | `CONFIG` | Centralized model/prompt config with auditability in job metadata. | P1 |
| UI shell | Section list, source labels, job labels | Navigation options and labels are static dictionaries in app bootstrap. | `app.py` | `CONFIG` | Pull from a lightweight UI config file or DB table. | P2 |
| UI behavior | Horizon options and sensitivity order | Attention horizon chips and sensitivity order are fixed arrays. | `app.py` | `CONFIG` | Bind to anomaly config object served to UI. | P1 |
| Auth | Cookie name + TTL | Session cookie key and TTL are fixed constants in app code. | `app.py` | `CONFIG` | Environment-based auth policy defaults with strict validation. | P1 |
| Narratives | Company role hints map | Narrative role text for many symbols is defined in a static symbol-to-sentence map. | `services/company.py` | `DYNAMIC` | Build from taxonomy + fundamentals + latest context; keep optional fallback config. | P1 |
| Narratives | Business narrative hints and theme keywords | Theme extraction and narrative framing depend on hardcoded keyword dictionaries. | `services/company.py` | `CONFIG` | Store as editable narrative policy pack and version it. | P2 |
| Analytics | Benchmark list (`SPY`, `DIA`, etc.) | Analytics default benchmark candidates are fixed in code. | `compute/analytics.py` | `CONFIG` | Portfolio/market benchmark config per workspace/user. | P2 |
| Fundamentals | Metric alias maps and statement file names | Parser uses static alias maps and static statement filename mapping. | `compute/fundamentals.py` | `CONFIG` | Data-source adapter config with mapping registry. | P2 |
| Fundamentals | Ticker alias rules (`GOOG/GOOGL`, `META/FB`) | Symbol normalization includes a manual alias set for known ticker pairs. | `compute/fundamentals.py` | `CONFIG` | Symbol alias reference table hydrated from listings metadata. | P2 |
| Pipeline orchestration | Source to job and source to dataset maps | Refresh routing and dataset grouping are fixed by in-code mapping tables. | `services/pipeline_store.py` | `KEEP` | Keep deterministic internal contract; update only via code review. | P3 |
| Pipeline schedules | Cron expressions for all jobs | Job run cadence is fixed by explicit cron strings in env schedule file. | `infra/job_schedules.env` | `CONFIG` | Keep environment-managed and document as deployment contract in `documents/infra/README.md`. | P3 |
| Data access | Legacy snippet heuristics for fallback mode | Legacy-mode detection and fallback behavior depend on fixed text snippets. | `data_access/layer.py` | `CONFIG` | Move to fallback policy config or remove when fallback fully retired. | P2 |
| Data access | Default universe fallback in multiple paths | If no symbols provided, several paths fall back to static default universe list. | `data_access/layer.py` | `DYNAMIC` | Always prefer latest universe snapshot; no static symbol fallback. | P0 |
| Data contracts | Missing taxonomy peer-group catalog dataset | Consumers rely on ad hoc derivation instead of a first-class dataset describing active peer groups and lineage. | `pipeline/jobs/main.py`, `services/pipeline_store.py`, `compute/anomalies.py` | `DYNAMIC` | Add taxonomy peer-group catalog and membership datasets consumed by anomalies, filters, and drilldowns. | P0 |
| Observability | No taxonomy coverage KPI in attention/anomaly runs | Runs do not emit the fraction of entities scored with taxonomy-backed grouping versus fallback logic. | `pipeline/jobs/main.py`, `compute/anomalies.py`, `services/attention_agentic.py` | `CONFIG` | Emit taxonomy coverage and fallback-mode metrics; alert when coverage drops below policy. | P1 |
| Fallback policy | Taxonomy read degradation is implicit | Runtime can degrade to non-taxonomy behavior without explicit mode signaling to users/operators. | `services/entity_taxonomy.py`, `data_access/layer.py`, `app.py` | `CONFIG` | Add explicit fallback modes (`taxonomy_ok`, `taxonomy_degraded`, `taxonomy_unavailable`) surfaced in payloads and UI. | P1 |

## Execution Order

1. `P0`: publish taxonomy peer-group catalog datasets, switch anomaly membership/drilldowns/home trend surfacing to taxonomy-driven paths, and remove static symbol universes from runtime decisions.
2. `P0`: externalize candidate/graph scoring profiles and shortlist thresholds into a single versioned runtime config object.
3. `P1`: move taxonomy prompt/schema and authority/narrative policies into versioned config datasets.
4. `P1`: add taxonomy coverage and fallback-mode observability with explicit run and UI signaling.
5. `P2`: clean remaining utility-level constants (analytics/fundamentals/UI polish maps).
6. `P3`: keep internal contracts and deployment env schedules documented, not dynamic.

## Success Criteria

- Attention and taxonomy pipelines run without static symbol lists.
- `peer_group_membership`, anomaly rollups, drilldowns, and home trend cards are generated from taxonomy datasets, not static focus lists.
- Graph scoring and candidate thresholds are loaded from a versioned config profile.
- Every run emits taxonomy coverage and fallback mode metrics with alert thresholds.
- UI and job behavior are reproducible from config + datasets, not code edits.
- A new environment can be tuned via config changes without touching Python source.

## Progress Snapshot (2026-03-29)

### P0
- Completed:
  - dynamic equity universe selection (no static default universe in runtime scans)
  - dynamic business focus mapping from taxonomy (no static business-lens symbol arrays in runtime logic)
  - anomaly peer-group membership from taxonomy labels
  - taxonomy peer-group catalog/membership datasets persisted and registered
  - attention shortlist and graph scoring thresholds moved to runtime policy config
  - macro anchor selection is taxonomy-derived
  - curated pinned-symbol fallback removed from universe builder path
  - daily home payload now includes taxonomy multi-horizon (`1d/1w/1mo/3mo/1yr`) trend cohorts
  - commodity regime job symbol set now resolves dynamically from taxonomy labels
- Remaining:
  - none flagged as blocking in P0 scope

### P1
- Completed:
  - source authority token policy consolidated into shared runtime config used by attention services
  - taxonomy allowed sector vocabulary now comes from runtime config
  - UI attention horizon options and sensitivity order now come from runtime config
- Remaining:
  - taxonomy schema-cardinality constraints externalization (`maxItems`/`minItems`/required-tag policy)
  - commodity metadata/focus graph moved to managed reference datasets
  - taxonomy lineage rollups (`sector`/`industry`/`business_role`) as first-class outputs
  - taxonomy coverage/fallback mode operator signaling in run metrics + UI

### P2
- Remaining:
  - narrative hint dictionaries and keyword packs externalization
  - residual utility-level hardcoded maps in fundamentals/analytics/UI polish paths
