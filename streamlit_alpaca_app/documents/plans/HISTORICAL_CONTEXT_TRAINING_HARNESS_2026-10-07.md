# Historical context corpus and training harness

Date: 2026-10-07, America/Los_Angeles.
Status: acquisition running locally into development Azure Blob. Raw and cleaned objects, partial immutable training releases and return labels are uploaded. Full collection and source expansion remain incomplete. See the [current machine-readable status](../training/HISTORICAL_CORPUS_STATUS_2026-10-07.json) and [coverage and pricing TODO](../training/HISTORICAL_SOURCE_TODO_2026-10-07.md).

## Outcome and decisions

Freeze rich contextual frames around the recent missed-stock examples and the requested episodes: Covid decline, Covid recovery, low-rate/inflation/speculative expansion, rapid tightening and the 2022 decline, banking stress, the first ChatGPT-centered AI phase, Iran war, and the Claude Code-centered 2026 AI phase. Preserve before, during and after observations, original source material, and outcomes in Blob Storage for training, development and held-out evaluation.

These are collection seeds and historical views. Episode names and proposed explanations do not become programmed market classes, causal ground truth, or requirements that models repeat a known narrative. Banking stress receives its own dated timeline within the related tightening episode. The same acquisition and replay contracts must support another episode without new code.

This supplies the data foundation of the [discovery architecture](../architecture/overall/redesigns/DISCOVERY_MODEL_ARCHITECTURE_2026-10-07.json). Numerical representation, relationship estimation, research selection and LLM investigation have separate objectives.

Owner decisions on October 7:

1. Research episode boundaries from the earliest observable price/adoption acceleration, retaining buildup before named announcements or military action. Keep both 2025 and 2026 Iran episodes.
2. Collect all accessible free and existing-entitlement sources, clean them and upload them to Azure Blob. Do not purchase additional data. Record paid and inaccessible coverage with prices and acquisition actions.
3. Support forward return/risk prediction and research discovery. Returns must direct research selection; qualitative usefulness and explanations still need separate review.

Proposed scope: historical US-listed equities/ETFs including inactive instruments where obtainable; crypto; global rates, currencies, commodities and relevant overseas markets; worldwide news and public discussion. Coverage must come from historical source catalogs, not today's liquidity shortlist or selected winners.

Proposed baseline: continuous history from 2018 through collection cutoff, expanding earlier where useful. Event views start with approximately a year of lead-in and follow-through, then expand with researched chronology. Full daily numerical history supplies broad coverage; inventory intraday availability and cost around acute developments before deciding the larger high-frequency pull. These are implemented acquisition bounds, not a claim that every source/date has finished. Ongoing episodes retain their actual observed end and incomplete outcome horizons.

## Static frame contract

Keep one deduplicated archive and multiple immutable views into it. A retrospective essay alone cannot serve as the historical input.

| Object | Contents |
| --- | --- |
| Episode definition | Original request, researched anchors, alternate boundary choices, windows, related episodes, version and observed end |
| Context at a cutoff | Exact observations and source versions available by that cutoff, preceding history, coverage/missingness and historically available memory |
| Subsequent observations | Later prices, releases, business results and developments, separately addressable from the earlier input |
| Retrospective interpretation | Versioned AQL/Zopedia chronology, connections, disagreement and lessons with raw-source references; separate from historical inputs and reviewed labels |
| Corpus release | Exact object references/hashes, catalogs, extraction/model versions, collection receipts, split definitions and review history |

Create multiple cutoffs through lead-in, disruption, response and follow-through. Hide outcome-revealing episode names from blinded replay input. Overlapping episodes reference shared objects rather than duplicate their archives.

Numerical records preserve entity/instrument identity, variable, raw value, unit, observation interval, frequency, session/timezone, first availability, acquisition time, provider/feed, vintage/revision and raw-object reference. Observation dates are not release dates. Preserve original filing acceptance and macro release timestamps.

Text records preserve original provider payload or source artifact, full available body, title, URL/ID, publication/update times, archive-version time where known, acquisition time, language, author/community metadata where supplied, thread/reply/quote relations, lineage and hashes. Full text lives beside passages/embeddings. A missing body stays recorded as missing rather than silently becoming a headline or search preview.

Availability can be exact, date-only, inferred or unknown. Today's edited article with an old publication date is not automatically historical evidence. Preserve uncertain material in the archive and retrospective view; strict replay admits versions supported by its availability policy. ALFRED vintages can be date-only: use original release timestamps or a conservative explicit availability bound for intraday replay. Distinguish reconstructed public availability from actual historical product ingestion; acquisition today does not prove a source was unavailable then.

Preserve raw prices and dated corporate actions alongside adjusted series. Today's fully adjusted bars must not silently become earlier features. Retain listings, delistings, mergers, identifiers and historical membership independently of price coverage. Social engagement counts fetched today are not contemporary measurements without dated archival support.

## Collection coverage

| Domain | Retain | Acquisition direction |
| --- | --- | --- |
| Equities/ETFs | Raw OHLCV, available trades/quotes, corporate actions, listings/exits, identifiers, fundamentals and membership | Existing Alpaca transport; verify historical feed and inactive coverage; price supplementary archives |
| Crypto | Venue-specific spot/derivatives prices/volume, funding, open interest, liquidations and market structure where available | Existing capabilities, public exchange archives and priced historical sources |
| Rates/bonds | Nominal/real Treasury curves, bills, policy rates, inflation compensation, spreads; bond-level prices/yields/trades/issuance where available | Existing Treasury owner, FRED/ALFRED and separate bond transaction inventory; label ETF/index proxies explicitly |
| Macro/liquidity | Vintage inflation, employment, output, money/credit, balance sheets, lending/deposits, housing, fiscal actions and release calendars | Existing FRED client extended for vintages; original Fed/Treasury/BLS/BEA and international releases |
| News/commentary | Business, markets, policy, local/overseas reporting, original articles, updates, syndicated lineage and competing accounts | Alpaca/Benzinga, shared search/full-text acquisition, publisher archives and historical bulk indexes |
| Public discussion | Posts/comments, threads, quote/reply context, dated engagement where archived, community/time coverage | Inventory X, Reddit, Stocktwits, Hacker News and other relevant archives separately; web search is not bulk social access |
| Company evidence | Filings/amendments, earnings releases/calls, guidance, presentations, financing, failures, products and demand | Existing EDGAR/company acquisition; inspect transcript/fundamental archive access |
| Other context | Health/restriction/mobility archives; energy/inventories/shipping; adoption/hiring/developer activity; historical search interest | Catalog discovery and shared AQL/Zopedia research; preserve source-native resolution and availability |

The matrix specifies coverage areas, not coded relevance rules. Include broad background samples, contradictory narratives and indirect evidence. Related sectors, private companies, customers, suppliers and countries can enter without an exact ticker in each source. Record source-specific storage and training/evaluation entitlements as acquisition metadata.

## Verified boundaries and access

Repository inspection on October 7:

- `pipeline/jobs/main.py` owns frame/manifests and Postgres indexing. `services/pipeline_store.py` owns namespace resolution/shared loading. Extend these owners rather than add another artifact platform.
- `services/alpaca_api.py:get_news` requires symbols, caps the total result at 50 and terminates pagination at that cap. `run_news` starts from 50 universe symbols. Historical collection needs complete pagination, explicit time windows, full content and broad coverage at this shared acquisition boundary. Current-feed budgets can remain intentional.
- `get_stock_bars` requests `adjustment=all`; the equities job requests IEX. Historical acquisition needs explicit adjustment/feed semantics. IEX volume cannot be relabeled consolidated volume.
- `services/fred.py:get_series_observations` does not request real-time periods/vintages. Today's historical values do not establish what an earlier model could know. Add vintage acquisition here.
- `services/treasury_yields.py` already acquires Treasury curve history. Reuse it with raw responses and availability retained. A curve is not a bond transaction archive.
- `_upload_bytes` permits overwrites and skips uploads when Blob is unavailable. Corpus publication needs confirmed immutable-object writes/checksums at shared persistence. Mutable latest pointers remain distinct from immutable releases; no success claim without storage receipts.

Read-only Azure inventory confirmed an enabled CLI session and existing `datasets` and `exports` containers. Key Vault metadata lists enabled Alpaca, FRED, SimFin, Seeking Alpha, search-provider and model-provider credentials. Secret values were not read/printed. Credential names do not establish working access, historical coverage or training rights. No explicit social-archive credential appeared in that inventory; other access has not been ruled out.

Official documentation checked October 7:

- [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html) and [vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html) support reconstructing historical knowledge and revisions.
- [Alpaca historical news](https://docs.alpaca.markets/us/docs/historical-news-data) documents Benzinga history back to 2015, a useful source that does not establish comprehensive world-news coverage.
- [X search](https://docs.x.com/x-api/posts/search/introduction) distinguishes seven-day recent search from paid/Enterprise full-archive access. Actual entitlement and collection cost still need inspection.
- [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Treasury XML files](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rate-xml-files), and [GDELT data](https://gdeltproject.org/data.html) supply candidate official boundaries. A news index is not an archived article body; verify bulk history and full-text acquisition separately.

## Storage and collection execution

Use existing Blob account/container/namespace and metadata ownership. Retain original content-addressed source objects; normalized observations/documents/actions/identities/catalogs; episode and cutoff manifests; derived passages/embeddings/relations/syntheses; outcomes/reviewed comparisons; immutable corpus/split releases; and run/review outputs. Derived artifacts reference exact inputs and model/prompt versions. New corrections create new versions.

Keep raw sources once and preserve separate publication observations for syndicated duplicates. Index coverage by source/domain/period/geography/language/entity/time reliability. Distinguish exhausted pagination, no observations, failed requests, unsupported history, unavailable bodies and intentional sampling.

Inventory requests, expected bytes and cost before fan-out. Collection resumes from durable progress and paces each provider. Stop at the agreed budget. Freeze releases after confirmed upload/checksum receipts, resolvable manifests and personal coverage inspection; partial releases retain explicit gaps. Do not write collection payloads into production namespaces during development.

## Training, evaluation and qualitative review

Forward returns and downside risk drive research direction. Temporal learners consume available-at-time numerical histories with training-only transforms and missingness masks. Empirical relationship learning and semantic extraction remain separate. Research selection learns from reviewed usefulness, corrections and follow-ups; unknown/unselected investigations stay unlabeled. Later price movement is not automatically research value. Model-generated labels stay distinct from human-reviewed labels.

The existing AQL/Zopedia harness replays against frozen inputs and dated memory, retaining questions, candidates, retrievals, source versions, selections/deferrals, connections, answers, cost and review state. Strict historical replay cannot query today's web or present-day Zopedia memory.

Use chronological training/development/final evaluation blocks and additional held-out episodes/entities where coverage permits. Do not randomly split adjacent frames. Purge training targets that extend into held-out periods; prevent leakage through later summaries, related labels and duplicate derived examples. Earlier history can legitimately support a later cutoff, without authorizing fitting on later observations. Review overlapping Covid/recovery/speculation and tightening/banking/AI views before assigning splits.

Include randomly sampled ordinary periods, unrelated developments and low-attention subjects. Handpicked important episodes alone cannot estimate recall or irrelevant promotion. Deterministic old shortlists provide no outcome for research never performed.

Personally read source material and outputs at several cutoffs across every episode: useful discovery, coherent relationships, disagreement, chronology, understanding added and worthwhile next questions. Schema/storage/pagination/time checks verify plumbing, not LLM quality. LLM pretraining can still contain later events despite frozen tools; historical replay supports diagnosis and controlled comparisons, while prospective shadow runs establish real-time usefulness.

## Sequence

1. Resolve ambiguity, objective and budget; probe existing source access and price gaps.
2. Research an editable episode catalog with primary anchors, alternate dates, windows and broad background coverage.
3. Implement source/persistence changes: historical pagination, raw/full-content retention, feed/adjustment settings, FRED vintages, immutable writes and upload verification.
4. Collect and personally inspect a complete cross-domain frame with multiple cutoffs before scheduling full acquisition. Avoid fixed ticker-inclusion or phrase tests.
5. Run resumable acquisition, address coverage gaps, and freeze the corpus and chronological split manifests.
6. Integrate historical replay into the shared harness, review episode/background results and run prospective shadow comparisons. Deploy development runtime changes after local acceptance; production requires explicit permission.


## Implemented collection and replay

The editable [episode catalog](../training/HISTORICAL_EPISODES_2026-10-07.json) contains researched anchors and their sources. Its revisions are separate from the frozen acquisition specification. Continuous baseline history remains scheduled even when an anchor changes. The Claude Code buildup extends into May–June 2025 based on actual package-download acceleration, with February preview, May general availability, November capability, January expansion and February security anchors retained. Downloads represent package fetches, including automation, rather than unique users.

`services/historical_corpus.py` orchestrates shared Alpaca, FRED, Treasury, EDGAR and Blob owners. It retains original payloads plus typed Parquet, complete pagination, provider pacing, per-page checkpoints, unavailable identifiers, source errors and SHA256 receipts. Daily news tasks and event-prioritized acquisition prevent a huge month or invalid catalog identifiers from blocking every episode. GDELT records are event metadata and source URLs; they are not full news articles. HN is one public-discussion community, not representative coverage of all social platforms.

`services/historical_training.py` freezes exact source-object references, weekly and anchor cutoffs, before/during/after windows, chronological splits and separately addressable targets. Train: 2018–2021; development: 2022–2023; test: 2024 through cutoff. These are retrospective diagnostic splits, not a claim of prospective validation. Targets crossing a split end are retained for retrospective analysis and excluded from fitting/scoring in that split.

Targets use the next observed session open and the close after 1, 5, 21, 63, 126 or 252 observed sessions. Maximum adverse/favorable price excursions, target dates, label availability and incomplete horizons are explicit. Missing/delisted outcomes remain missing. Provider gaps can lengthen the elapsed calendar horizon; the dataset retains elapsed days rather than inventing sessions or terminal returns. Fully adjusted prices are restricted to outcomes.

The frozen reader has no live web fallback. Strict inputs require a declared available-at time at or before the cutoff. Macro values are selected across all frozen vintage partitions. Reconstructed text views separately admit currently archived bodies by publication date and remove present-day engagement. Current catalogs and revised-history-only series are excluded from strict historical inputs. Date-only availability starts after the source local day ends: New York for stock daily bars, Treasury, SEC facts, retained US company statements and corporate-action process dates; Chicago for FRED vintages. Earlier uploaded tables are canonicalized into that contract before inclusion. Unknown historical body versions stay explicitly unverified.

Web extraction belongs in shared `services/web_research.py`. Trafilatura supplies text blocks; the shared AQL/Zopedia gateway selects the central document's verbatim blocks, excluding unrelated current recommendation cards. It does not summarize or invent text. Raw HTML/PDF, preselection blocks, selected indices, model metadata and cleaning traces remain retained. This matters because otherwise a 2025 article can include recommended stories about 2027.

Azure location: existing `datasets` container, `namespaces/development/historical_context/`. Content-addressed objects and release manifests are immutable. `latest_training_release.json` is the separately mutable discovery pointer. The local acquisition journal is ignored by Git; authoritative uploaded releases contain task/source status, object receipts and the exact specification. Existing operator access supplied the Blob write credential in process memory; no credentials were printed/stored, no roles changed, and production data was not modified.

Current collection families: all provider-catalog equity identifiers (active and inactive), raw/adjusted SIP daily bars, corporate actions, provider crypto bars, ALFRED revisions and current-only FRED series, nominal/real Treasury curves and bills, all accessible Alpaca historical news with bodies, GDELT daily global events, indexed HN stories, seeded discussion searches and an additional complete-comment backfill, SEC filing indexes/company facts, retained SimFin statements, OWID Covid, Oxford policy/vaccine histories, npm adoption history, Binance historical spot pairs and futures funding. Free expansion and source failures remain in the TODO; this list does not establish complete free-world coverage.

Collection runs under macOS launchd for this login with start/status/stop controls and failure restart. The owner enabled the previously denied python3 Documents-folder permission, and background repository access was verified. The machine and local journal must survive for acquisition to continue; cloud scheduling/failover has not been deployed, and logout/reboot requires starting services again. Re-running the commands in the [runbook](../training/HISTORICAL_CORPUS_RUNBOOK_2026-10-07.md) resumes completed tasks/pages. The release watcher publishes partial releases and derives new return partitions while ingestion continues. Local source/plumbing acceptance used real provider pagination, uploaded-object readbacks, actual return paths and human review of extracted documents. No LLM usefulness or prediction-quality claim follows from those checks.

Treasury schema acceptance exposed a genuine source difference: curves use NEW_DATE while bills use INDEX_DATE. The shared Treasury parser now retains every XML field with its declared EDM type, including numerical rates, maturity dates and string CUSIPs. The publisher reprocesses earlier tables from their retained XML under Treasury schema version 2. Actual 2018, 2023 and 2026 feeds and a 2026 Blob readback were inspected; bill observations now have conservative New York availability and typed yields.
