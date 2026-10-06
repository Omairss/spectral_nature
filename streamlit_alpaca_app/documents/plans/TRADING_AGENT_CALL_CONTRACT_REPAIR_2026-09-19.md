# Trading Agent Call Contract Repair

Date: 2026-09-19

## Goal

Make Trading Agent output honest and measurable without changing the shared
AQL/Zopedia reliability runtime that is being repaired separately.

The current production output mixes research classifications with directional
calls. `watch` is scored as long and `avoid` is scored as short even though
neither label represents a trade. Horizon scouts can also admit rows whose
selected-horizon return is missing. Those two contracts make candidate counts
and win rates misleading even when the research job itself succeeds.

## Ownership Boundary

This change owns:

- horizon-valid Market Opportunity selection;
- Trading Agent stance and research-status semantics;
- scoreable versus research-only outcome handling;
- benchmark-relative outcome calculation from existing price history;
- Trading Agent admin presentation of directional and research-only rows.

This change does not modify:

- the shared AQL/Zopedia planner, tool registry, trajectory monitor, provider
  runtime, or gateway;
- pipeline scheduling or deployment scripts;
- production configuration or images;
- historical evidence replay.

## Contract

Every materialized Trading Agent row keeps a display `direction` for backward
compatibility and adds:

- `stance`: `long`, `short`, or `no_call`;
- `research_status`: `supported`, `watch`, `avoid`, or
  `insufficient_evidence`;
- `decision_status`: `open` for directional calls and `research_only` for
  `no_call` rows.

Only `long` and `short` rows are scoreable. `watch` and `avoid` remain useful
research output but produce `outcome_status=not_scoreable`, no signed return,
and no win/loss label.

Directional outcomes use the first available price bar on or after the call
date and the first bar on or after the target date. When SPY bars are present,
the outcome also records SPY return and direction-adjusted excess return.

Market Opportunity rows retain daily-dislocation visibility for product
surfaces, but Trading Agent horizon selection requires a numeric value in the
selected horizon column. Missing five-year history cannot become a five-year
Trading Agent scout merely because daily movement is large.

## Acceptance

- A missing selected-horizon metric remains displayable in Market Explorer but
  is excluded from Trading Agent context.
- Legacy `watch` and `avoid` rows become `no_call` and are never counted as
  wins or losses.
- Explicit long and short rows retain directional scoring.
- SPY-relative return is populated when matching benchmark bars exist.
- The admin UI separates directional calls from research-only rows and disables
  Place for `no_call` rows.
- Focused service and view tests pass without changing shared reliability
  modules.

## Implementation Status

Completed on 2026-09-19:

- Market Opportunity keeps rows available for display but exposes horizon
  availability and excludes missing selected-horizon metrics from Trading
  Agent context.
- Trading synthesis requests explicit `stance` and `research_status` values.
  The materialization boundary normalizes legacy rows and demotes contradictory
  outputs, such as `stance=long` with `research_status=insufficient_evidence`,
  to `no_call`.
- Candidate persistence and the admin UI distinguish directional calls from
  research-only classifications. Place is unavailable for research-only rows.
- Outcome tracking scores only explicit long/short calls and records aligned
  SPY and excess returns when benchmark bars exist.
- The performance notebook now calls the production outcome builder. It no
  longer has a second scoring implementation or cached claims that count
  `watch` as long and `avoid` as short.

Verification:

- 28 focused Market Opportunity, Trading Agent service, and Trading Agent view
  tests passed, including contradictory-call demotion at the service and UI
  boundaries.
- 2 Trading Agent pipeline integration tests passed.
- Replaying current artifacts produced 78 historical candidate rows: 76
  research-only rows and 2 explicit long observations. Neither explicit call
  is mature; both currently show about -20.65% signed return and -23.15%
  direction-adjusted excess return versus SPY.
- The latest clean production candidate artifact contains 11 research-only
  rows and zero scoreable calls. There is therefore no defensible realized win
  rate yet.

This implements schema-level candidate admission, not the deferred independent
semantic package critic. A candidate can only cross the scoring boundary when
its normalized contract is explicit and internally consistent; qualitative
support review remains owned by the shared AQL/Zopedia reliability work.

## Deployment

Deployed to the development UI on 2026-09-20 from an isolated detached worktree
based on clean commit `01c53d1`, with only the verified Market Opportunity,
Trading Agent service, Trading Agent view, and focused test diffs applied. This
avoided bundling the active unrelated AQL/runtime and pipeline changes in the
shared worktree.

- App: `sn-streamlit-ui-dev`
- Revision: `sn-streamlit-ui-dev--0000438`
- Image: `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:dc680650eafd90415a25041bc7ca952132e0cb41ded810c7d5454af70b9544da`
- Verification: 32 focused tests passed; 2 Trading Agent pipeline integration
  tests passed; the revision became ready with 100% development traffic; root
  and `/_stcore/health` returned HTTP 200. This revision supersedes `0000437`
  and includes the v2 concise research contract and cleaned investor view.

The production UI and scheduled pipeline jobs were not changed. The bounded
empty-response retry is present in the shared source but was intentionally
excluded from this UI-only image alongside concurrent System Health pipeline
work; it requires a separate verified pipeline release. The repository-wide
suite remains red on unrelated baseline and concurrent work, so this dev
release is intentionally scoped rather than an integrated application release.

## Deferred

- Semantic package-quality review through the shared AQL/Zopedia critic.
- Upstream dataset-version barrier between Attention/Home and Trading Agent.
- Sector benchmark selection.
- Evidence-native historical backtesting.

## Plain-Language Research Contract

Added on 2026-09-20 after the latest TWST candidate exposed research-system
language as investor analysis. The sentence `One materialized momentum row with
no source trace, no price history, and no average-volume baseline to judge the
86,122 print.` was persisted as a tail risk even though it described data
quality, not a risk to the company or thesis.

The corrected contract now:

- sends AQL a company research brief rather than raw market-opportunity records;
- omits unqualified volume, internal scanner scores, storage metadata, and tool
  traces from synthesis input;
- separates supported evidence, market/business risks, and forward-looking
  research questions;
- keeps raw AQL limitations in the audit payload instead of appending them to
  user-facing data gaps;
- versions generated prose as `plain_investor_research_v2` and hides older
  generated narrative in the UI;
- retains raw volume as structured scanner data but removes it from the summary
  text when there is no comparison against typical volume.

Verification completed against the latest persisted one-week TWST/ASAN
packages. The first live synthesis exposed a remaining weakness by moving the
raw volume complaint into evidence limits. After tightening the semantic
contract, the second live synthesis omitted the unexplained `86,122` value and
all `materialized row` / `source trace` language, expressed follow-up work as
plain research tasks, and kept data-quality issues out of business-risk fields.
The check did not publish or replace any dataset.

Focused verification: 29 Market Opportunity, Trading Agent service, and view
tests passed; 2 Trading Agent pipeline integration tests passed; edited modules
compiled and `git diff --check` passed.

## End-to-End Verification

Completed on 2026-09-21 with shared writes and broker actions disabled. The
check loaded the current Azure artifacts through the normal readers: 6,060
Market Opportunity rows, the current page summary, and 1,457,417 price-history
rows. It then ran the real shared-research Trading Agent path with one company
across all five horizons and intercepted the production parquet and manifest
uploads into an isolated temporary store.

The first browser-backed run proved the plumbing but exposed unexplained UI
diagnostics such as `Scanner rank`, `Momentum pace -11.44`, duplicated metrics,
and overly dense candidate prose. Those diagnostics were removed from the
investor surface, the prose contract was bounded to three decision-relevant
evidence, risk, and follow-up items, and the narrative contract advanced to
`plain_investor_research_v2`.

A subsequent full run failed closed when the live model returned empty content:
zero candidates were persisted and the job reported failure. Empty model
responses are now retryable and the scheduled job default is two bounded
research attempts. The final isolated run completed with one TWST research
package, five horizon runs, four successful horizons, four research-only
candidates, and no fabricated five-year candidate where context was absent.

The isolated parquet artifacts were reloaded through the Trading Agent view
boundary and rendered in Chromium at 1440x1000 and 390x844. All five horizon
controls were exercised. The four supported horizons rendered the v2 watchlist;
the five-year horizon rendered the unavailable state. There were no browser or
console errors, no horizontal mobile overflow, no enabled Approve action for
the research-only candidates, and no occurrences of `materialized momentum
row`, `source trace`, `86,122`, scanner diagnostics, database, or provenance
language. No shared dataset, Zopedia memory, broker action, production UI, or
scheduled pipeline job was changed by the check.

Final focused verification: 32 service/view tests plus 2 pipeline integration
tests passed; edited modules compiled and `git diff --check` passed.

## Development Namespace Live Run

Completed on 2026-09-21 after introducing the shared
`PIPELINE_DATASET_NAMESPACE` boundary and cloning 83 latest production
snapshots into `development`. The manual-only Azure job
`trading-agent-build-dev` ran image
`pipeline-jobs:dev-namespace-20260921020436` with Zopedia writes disabled. The
scheduled production job and production dataset pointers were not changed.

Execution `trading-agent-build-dev-q5xv656` succeeded with run ID
`b24b3ae9-48d5-44be-87b2-d6b800d3cfcd`: eight of eight company packages and
five of five horizon syntheses completed, producing 14 candidates and 14
outcomes. Every candidate was an explicit `no_call`; therefore every outcome
was `not_scoreable`, there were no mature research reviews, and no action rows
were created. This verifies the research and materialization path but does not
create a win-rate claim or constitute a historical backtest.

The persisted development artifacts were loaded through the production view
adapters. All five horizon results and all 14 candidates carried
`plain_investor_research_v2`, so the UI no longer falls through to `Analysis
unavailable for this horizon.` for this run. A qualitative scan found no
`materialized`, `source trace`, scanner-diagnostic, database, provenance, or
unexplained `86,122` language in investor-facing fields. The development UI is
revision `sn-streamlit-ui-dev--0000440`, and its health endpoint returned
`ok`.
