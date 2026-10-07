# Workbench production readiness

Date: 2026-10-06 (verification continues October 7 UTC)

Status: the new frontend is live on all four production domains. Revision
`sn-streamlit-ui--workbench-1007171849` is healthy and receives 100% of traffic. The owner
authorized proceeding on October 7 and will resolve Robinhood separately. Final private
checks on the custom domain await the owner's sign-in; candidate owner-session checks passed.
Earlier staging-only restrictions below describe historical checkpoints.

Release scope: replace the production frontend while preserving the existing backend,
authentication, credentials, scheduled jobs, domains, and certificates. Robinhood linkage,
broker connection-management UI, and trade execution are deferred, not certified as complete.
Provider failures remain unavailable rather than substituting another account. Shared database
lock recovery passed actual knowledge search, linked-page traversal, and a retained-source
research workflow before switching the frontend. This is frontend acceptance, not certification
of exhaustive research coverage or a new agent harness.

Staging exists at `https://sn-next-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io`:
web revision `sn-next-ui--0000003`, private API revision `sn-api-next--0000002`, and manual
`sn-research-run`. Web/API source is `6856a1de`; the worker uses the accepted
development base plus the accepted lock fix. Production now runs the same web image on
the existing `sn-streamlit-ui` app; the old Streamlit revision `0000087` is inactive and its
complete template remains captured for rollback. All domain bindings match the pre-staging
snapshot. Authentication and Admin/users passed candidate owner-session review; production
Robinhood is explicitly deferred. The historical research failure below was resolved by
shared lock recovery.

## Deployment boundary

Keep `sn-streamlit-ui-dev` unchanged. Stage the accepted images as
`sn-next-ui`, private `sn-api-next`, and manual `sn-research-run` in the existing environment.
The candidate uses production datasets and the same control database, users, password
hashes, memberships, auth policy, and API-signing secret. This is not a backend rewrite or
a second account system. No new database or refresh pipeline is needed.

The production API must inherit the existing production broker encryption-key reference
from Streamlit, not development's key. Native secret values are resolved inside Azure
using ARM expressions; do not retrieve them to the workstation. Control-vault access stays
with the control-only identity. The web process receives no data/control credentials and
cannot obtain its ACR-pull managed identity from the application runtime. Research dispatch
permission is scoped to the one production research job.

Broker execution is explicitly disabled on the staging candidate. The current production
UI's execution policy remains unchanged and is recorded in the release manifest; restoring
it on the replacement requires the separate approved cutover. Do not expose a second
money-moving UI while staging only read/research behavior.

Both web and API stay warm at one replica. Explicit startup/readiness/liveness probes use
the minimal `/health` response. The API remains private; the worker has no ingress. Preserve
HTTP-only host-prefixed cookies, rotation, exact same-host Origin checks, CSRF, nonce CSP,
HSTS, and no-store private responses. All four current production aliases are configured
for mutations, but one alias cannot mutate another alias's host-local session.

## Existing refresh pipeline

The production pipeline already runs. These two producers needed the new frontend read contract:

| Existing job | Existing UTC schedule | New read contract |
| --- | --- | --- |
| `attention-home-build` | `20 14,16,18,20 * * 1-5` | embedded five-chart model selection and quarterly financial histories |
| `equities-intraday-preload` | `35 13,16,18,20 * * 1-5` | symbol partitions for price and technical histories |

Before promotion, production Home and ticker artifacts were current through October 6,
but ticker rows lacked financial histories, the daily-datapoint artifact was absent, and
the 1.47-million-row price and technical histories had no symbol partitions. Freshness
alone therefore did not prove compatibility with the new consumer.

Use template-only PATCH with `Content-Type: application/json` for these existing jobs, preserving configuration,
native secrets, identity, schedules, retry/timeout budgets, and commands. Full ARM PUT of
an existing job cannot preserve a write-only native secret by copying its GET response;
`listSecrets(self)` introduces an ARM circular dependency. Do not deploy the all-jobs
pipeline script for this scoped promotion. The jobs API rejects the otherwise customary
`application/merge-patch+json` media type; a rejected request does not update the template.

`scripts/prepare_workbench_release.py` reads live configuration and emits a new immutable,
ignored release directory. It has no deployment option. The directory contains:

- `candidate.apps.json`: new candidate web/API/worker and exact dispatch role.
- `existing-producers.patch.json`: two template-only operations with current config/template fingerprints.
- `rollback-producers.patch.json`: original templates with immutable old-image digests.
- `release.json`: candidate images and existing production UI revision/domain/certificate bindings.

Compare fingerprints before applying a producer operation; abort on drift. Afterward,
verify that its protected configuration is unchanged and its template matches the target.
Run the existing equities job before validating the new bounded history reads. Run the
existing Home job and personally review its actual model-authored five-chart selection.
Do not copy development model selections into production.

## Verification

Use `scripts/check_workbench_data.py --namespace production` with the normal local Azure
configuration to inspect stable manifests and bounded Home/ticker/daily artifacts. It
checks identity, history coverage, and partitions without provider/model calls or writes.
Its result is a transport/data-contract check, not a model-quality verdict.

Locally completed: auth-boundary checks, lint, type checking, and the Next.js production
build. Auth outage responses preserve the existing cookies and produce an unavailable
state rather than logout. Shell and route share their in-flight session read. A real
deployed reload after token expiry preserved the owner session. Same-host alias and CSRF
checks cover allowed and hostile origins. Existing backend rotation is unchanged; this
does not certify multi-replica or simultaneous multi-tab refresh behavior.

Owner-session dev review confirmed the five aligned daily charts and a real retained
source article through the final opaque-ID route. Earlier review covered financial
trends, all five Investigator panels, portfolio 5Y, macro smoothing, search, and wiki
links. Development's price-history partition gap was repaired using its existing manual
namespace-refresh job, not by weakening the read limit. Its final audit passes: both large
histories have 64 symbol partitions, five daily charts match Home identity, and 16 ticker
snapshots contain all three financial histories.

Production candidate acceptance still requires actual deployed auth/Admin, bounded price
and technical plots, Home selection/trends, portfolio read, Zopedia search/link traversal,
and one safe research run. Do not exercise broker execution as a frontend acceptance check.
Runtime dependency audit is clean; five pre-existing development-only lint-tool audit
entries remain. Do not force npm's suggested framework downgrade.

## Cutover and rollback

The approved October 7 cutover uses the existing `sn-streamlit-ui` Container App rather
than transferring domains to `sn-next-ui`. Replace only its frontend template and runtime
identity lifecycle. Next listens on the existing ingress port through `PORT=8501`, so both
old and new revisions use the same port while the replacement warms. This retains its generated hostname, all
four domain/certificate bindings, and existing DNS, including the direct www CNAMEs.
The old Streamlit template, ingress port, and identity lifecycle are captured for rollback.
The web process receives only non-secret frontend settings; all retained managed identities
are unavailable to its runtime. The private Python API keeps the existing production
control/data credentials. Robinhood settings and connections are unchanged.

`scripts/prepare_workbench_cutover.py` only prepares an immutable ignored release directory.
Compare its original-properties fingerprint immediately before applying the template-only
PATCH; verify protected configuration, identities, domain bindings, and HTTPS afterward.
Use the same version-matched `az resource show` representation as preparation for fingerprints;
`az rest` omits nullable identity metadata that the generic resource client includes. Compare
identity lifecycle entries by identity ID, not list order. Read back actual state after any
verification exception before deciding whether to retry a write.
The final prepared bundle is `infra/.generated/workbench-production/cutover-20261007-same-port/`.
The earlier `cutover-20261007-6856a1de/` bundle was not applied; retain it for its lock-rollout records.
Do not overwrite it or apply a historical domain-transfer procedure.

Current domains: `spectral-nature.com`, `www.spectral-nature.com`, `torres-cap.com`, and
`www.torres-cap.com`. Preserve their existing managed-certificate IDs. Apex A records point
to the shared environment ingress IP, `172.168.33.46`; the two www CNAMEs currently point
directly to the old Streamlit generated hostname. Managed certificate renewal requires
each www CNAME to point directly to the destination app, not an intermediate alias.
See [Azure managed-certificate requirements](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-managed-certificates).

No DNS or domain binding changes are needed for this cutover. Verify HTTPS and same-host
login/mutations after replacing the frontend. Rollback restores the captured Streamlit
template and managed-identity lifecycle on the same app; domains and DNS stay untouched.

Producer rollback restores original templates and pinned images, not previously written
data. New partition metadata and optional Home fields are backward compatible with old
readers. Record original manifests before refresh if a data-pointer rollback is needed;
never delete new dataset versions as part of traffic rollback.

## Staging findings and recovery

The original producer rollback bundle is
`infra/.generated/workbench-production/release-c14f0b22-readonly/rollback-producers.patch.json`.
Keep it even when preparing subsequent app releases. Original Home/ticker version suffix:
`20261006T202028Z__b548d917`; original price/technical-history/latest suffix:
`20261006T203529Z__9835a55c`. Their immutable manifests remain available.

`equities-intraday-preload-9hhes79` published the price history with 64 partitions, then both
attempts exited with code 137 during technical-history processing. The job was restored
to its original image, pinned as
`pipeline-jobs@sha256:dea12fe927000c241a75df12a06a35c442f1381cd6ba2dd7b88ace8c298d589a`.
Its configuration, schedule, native secrets, and resource limits did not change.

The source-level memory fix releases per-symbol frames after concatenation, uses shallow
column-safe Parquet normalization, and groups partition row positions rather than making
and sorting another full table. It releases the global technical table after publication.
On the actual 1,471,130-row production technical artifact, local serialization peak RSS
fell from 1,695.2 MB to 923.4 MB. All 64 partition counts sum to the full row count and the
input symbol column remains unchanged. The review captured uploads locally: no cloud or
database writes. Five focused serialization/partition checks pass, including duplicate
indexes and source-object preservation. The complete producer still needs a successful
managed execution before promotion is accepted.

The production Home refresh succeeded as `attention-home-build-ltpmk4j` at 06:53:38 UTC. Account-flow
inspection also found that Next can issue invitation/reset links but does not consume
the native root-query links. Those screens and the existing auth service's API access are
required before cutover; do not describe sign-in alone as account lifecycle parity.

## Account lifecycle completion

Next now routes existing root-query invitation/reset links to dedicated account forms;
forgot-password uses the same native auth service. No alternate users, hashes, roles,
memberships, email provider, signing secret, or password policy is introduced. Thin API
endpoints expose the existing operations; the BFF allowlists input and returns only public
status, retaining issued credentials in HTTP-only cookies. It never returns generated
reset URLs, matched-account state, validation input, or delivery diagnostics publicly.
Email links continue using the existing production base URL; staging does not alter it.

Token consumption rechecks validity under a row lock. Invalid tokens are rejected before
password hashing. Password reset preserves the native revocation of all user sessions.
Recovery requests reserve a short transaction in the existing control access-event log:
at most three per normalized recipient and twenty per observed source per fifteen minutes.
The private API sees the BFF source, so that source limit also bounds the BFF cohort; it
does not trust a caller-supplied forwarding header. No new schema or database is needed.
Global no-referrer prevents query tokens leaking in same-origin referrers.

Local review verified the activation layout, reset link routing, recovery entry/back
navigation, and invalid-link state without creating accounts or changing credentials.
The 67 native auth/API checks and seven BFF/session security checks pass. These verify
mechanics, not live email delivery or a completed real-user credential change. Actual
credential entry/confirmation remains an owner handoff, not an automated browser action.

Pipeline image `d12069a7` built successfully with digest
`sha256:60075ee767052753140c8a3a50be9fc13d7b93b3217284186c9c600d3e6a67b2`.
The complete producer succeeded as manual `equities-next-dev-vaqgz72` at 06:51:51 UTC with
the same one CPU/two GiB limit in development. Only then was the existing production
producer promoted. `equities-intraday-preload-43erzcz` succeeded at 06:57:49 UTC. Protected
configuration, identities, schedules, resource limits and native secrets match the original
fingerprint. No new production schedule has been introduced.

The production data-contract audit now passes: both histories have 64 partitions,
Home/ticker/daily identities agree, and 39 of the 71 ticker snapshots contain all three
financial trends. This does not accept the initial model captions: personal review found
a cross-season revenue-collapse claim contradicted by same-quarter prior-year behavior
and a credit-spread caption borrowing another chart's start date. The shared selector now
uses AQL/Zopedia semantic review and bounded revision over the exact selected histories
and daily evidence, failing closed if review cannot accept the result. Local qualitative
evaluation completed on the production inputs. The accepted local result selected Ciena
revenue, Seagate free cash flow, 10x Genomics revenue, the ten-year Treasury yield, and
Vistra revenue. Personal review checked actual fiscal-quarter comparisons, all cited
endpoints, and the relevant Home evidence/watch fields: Ciena +33% year over year,
Seagate $1.118B versus $425M, 10x $151.0M versus $172.9M, ten-year yield 4.78% to 5.28%
within its exact displayed dates, and Vistra $4.017B versus $4.250B. Background periods
are explicit rather than described as new releases. No hand-authored choices or captions
were substituted. Managed development execution and production publication remain next.

Managed execution `daily-datapoints-next-dev-he97kh7` succeeded, but personal review
rejected one interpretation: historical revenue growth cannot establish that a current
de-rating was not a revenue event. This is a negative causal inference, not a transport
failure. The shared selection/review instructions are being strengthened for that general
reasoning error. Source-backed review is useful but its approval alone is not acceptance.
Do not call the candidate production-ready while this qualitative gate remains open.

Account deployment receipts: development web `0000008`, private API `0000010`;
production candidate web/API `0000001`. Pinned web digest:
`sha256:98794b4aacac7734111944a45a0325f03bee0e8d3d5a72bc19f6e4aea18b3f16`;
API digest: `sha256:59cffe43e1d45002d60eb2253c5c6783dd5f7d79564cff1d6219f8a85b1bb7e3`.
Both deployed BFFs reject invalid activation/reset tokens with 400, invalid recovery input
with 422 and a safe error, and missing/hostile Origin/CSRF with 403. Production anonymous
Admin/research access remains rejected. No-referrer is deployed. Existing authenticated
development session and Investigator plots still work after the update with no console
warnings/errors. Production owner sign-in remains pending.

Post-login destinations now use parsed same-origin normalization rather than a slash
prefix check. Backslash, control-character, external, scheme, and oversized destinations
fall back to the local research page; ordinary routes/query/fragment remain intact.
Eight local auth/BFF checks, lint, type checking, and the production build pass. This
additional web hardening is being prepared for development, then candidate rollout.

## Selection audit and remaining release gates

Before the revision-context correction, a local production-input selection failed its
semantic review after bounded revision. That was not production acceptance. The development-input result
avoided the negative causal claim, but that does not certify the larger production
catalog. Do not publish a manually curated replacement, call transport checks a quality
verdict, or swap traffic. Reviewer issues are included in internal failure diagnostics
for the next evaluation. At that checkpoint, the refinement was source-only pending acceptance.

At 07:18 UTC, `attention-home-build` was restored from the original rollback bundle to
`pipeline-jobs@sha256:dea12fe927000c241a75df12a06a35c442f1381cd6ba2dd7b88ace8c298d589a`.
Its template and protected configuration were re-read and matched the original receipt.
The already published data and immutable versions were retained. The verified equities
job stays on `d12069a7`; do not promote an unaccepted Home producer merely because its
container or materialization succeeds. Current data-contract checks pass, but that alone
does not certify the next scheduled legacy Home output for the new consumer.

The remaining owner-session gate covers production Admin/users, portfolio reads, guarded
broker/account state, and a safe research dispatch. No credentials have been changed or
copied between development and production origins. The production sign-in tab is open.

Public Daily Brief behavior is preserved from the existing Streamlit public-Home branch
(`app.py`, public routing before the auth gate), not introduced as an auth bypass. Public
Home and audio contain the materialized public market view. Private portfolio, research,
knowledge and administration remain authenticated. The staging candidate has no custom
domains; live domains and both Streamlit revisions still match the original baseline.

The revision-loop audit found that feedback omitted the rejected draft. Revision now
receives both draft and declared critique, preserving supported findings rather than
restarting selection. The next local production-input run converged: review caught a
neighboring-series yield and an overstated Ciena growth count, and revision corrected them.
Personal acceptance checked the final five choices: Ciena revenue, Seagate cash flow,
Constellation price, ten-year yield and 10x revenue. Constellation's September pre-jump
range is $254.13–$299.23 versus the $300.40 close; Seagate's $1.118B cash flow is the high
of the exact supplied history; quarter-matched 10x revenue and Treasury endpoints match
the source. Managed verification remains required before re-promoting the Home job.

Redirect hardening source `5f7a2665` is deployed: development web `0000009`, production
candidate web `0000002`, pinned digest
`sha256:9390474c34a5311255df95e89333272fa8ea52186f8e15a0df1631a434df94e7`.
Both revisions are healthy. Actual deployed server-rendered login props normalize a
backslash-host destination to `/zopedia`, while retaining the legitimate Investigator
query and fragment. Anonymous production portfolio/Admin/research requests return 401.

## Managed selection checkpoint — 07:39 UTC (historical)

Selection image source `5ff5c5ea`, pinned as
`pipeline-jobs@sha256:42743fcdfbbbaa039c88796043bc6195603f5e0060fa29f3781c945a9f9449d9`,
passed managed execution in both namespaces:

- `daily-datapoints-next-dev-p4b76d0` succeeded at 07:29:54 UTC.
- `attention-home-build-y58i6sk` succeeded at 07:32:09 UTC with a one-execution override
  selecting `daily-market-datapoints-build` and the explicit production namespace. The
  scheduled Home template was unchanged during this isolated verification.

The actual production result selected Ciena revenue, Seagate revenue, Twist free cash
flow, Argan revenue, and effective Fed funds. Personal review compared each caption with
the complete selected series and Home context: Ciena $1.427042B versus $1.072260B (33%
year over year); Seagate $3.629B versus $2.444B (48%); Twist -$8.648M with persistent
negative cash flow; Argan $383.976M versus $237.743M (62%); and September Fed funds 3.75%
versus August 3.63% after the preceding pause. The financial comparisons name their actual
fiscal periods rather than inventing a current release. The charts add optical demand,
storage divergence, life-science cash use, power infrastructure growth, and rate context.
No manually selected replacement or caption was published. The deployed public candidate
renders these five charts together and retains inline ticker price and financial histories.

After that acceptance, `attention-home-next-dev` and the existing production
`attention-home-build` templates were promoted to the same pinned image. Fresh ARM reads
confirmed the intended templates and unchanged protected configuration, identities,
resources, schedules, and secrets. The complete production refresh
`attention-home-build-rim6fga` started at 07:36:21 UTC and is still running at this receipt.
Its Home-embedded selection and retained replay must be checked after completion; the
standalone latest selection does not repair an older immutable replay snapshot.

The data-contract audit passes in both namespaces. Production has five matching charts,
71 ticker snapshots with 39 containing all three financial histories, and 64 symbol
partitions for each of the 1,472,688 price and 1,471,130 technical-history rows. These
checks do not replace the pending signed-in production review.

A recovery request for a generated nonexistent `@invalid.example` recipient completed
against the production control store and returned only the generic success DTO; it did
not create or change an account or reveal a reset link/delivery state. Real activation,
email delivery, and credential changes were not performed. The owner must sign in to the
new candidate origin for Admin/users, portfolio/account reads, private knowledge traversal,
and one safe read-only research run. Development cookies were not copied. Live traffic
and all domain bindings remain on Streamlit; no swap is authorized.

## Owner-session findings — October 7, 16:30 UTC

The owner signed in directly to the candidate. Admin displayed 12 actual users. The full
Home execution `attention-home-build-rim6fga` succeeded at 08:06:11 UTC. Subsequent existing
schedules published a 14:20 UTC Home artifact, 81 ticker snapshots (37 with all three
financial histories), and 64 partitions for each of 1,466,598 price and 1,465,039 technical
rows. The latest Home and its five charts rendered after normal cache revalidation;
the October 7 replay also rendered its retained 07:36 UTC snapshot and embedded charts.
The replay was not rewritten to match a newer standalone selection.

Zopedia workspace returned 503. A normal owner research request, `agrun_9093fcffaa2c44168abb5445`,
dispatched worker `sn-research-run-aqnkaea` and completed, but retrieval failed with a
page-table lock timeout and supplied no retained evidence. This is failed research
acceptance, not a successful answer. Its policy remained `none`; no knowledge edit or
broker action was requested.

Read-only database inspection established that every required Zopedia relation and both
migrated columns exist; readiness is true. An unnamed legacy session retained a page
SELECT transaction for over 14 minutes. Older sessions queued `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS` on Zopedia/documents, blocking later readers. Existing scheduled jobs still
use September images. The shared source fix skips complete SAA schemas without DDL,
resolves Zopedia columns against the actual relation, propagates catalog failures, gives
pipeline sessions an identifying name and bounded lock/statement/idle-transaction limits,
and closes the news-resolution wiki read before external/model work. A broad existing-job
rollout and clearing the confirmed stuck transaction require the newly requested approval;
neither has been performed at this checkpoint.

Portfolio selected 5Y but displayed a $1,000 Alpaca account. The control store has a linked
Robinhood portfolio, development/local connections, and a development snapshot, but no
production connection or snapshot. The API caught a connection/authorization failure and
fell through to another broker. The source fix permits Alpaca only when the shared service
explicitly reports an unlinked portfolio; missing credentials, storage, pending refresh,
and failed snapshots fail closed. Both Portfolio and Performance consume that contract.
No development credentials or snapshot were copied into production. Production broker
linking requires the owner's normal credential/verification flow. The replacement still
needs a secure connection-management surface before full old-UI parity can be asserted.

Zopedia source UI now treats workspace failure separately from successful empty results,
clears stale search/folder data, and offers retry instead of claiming zero pages. Focused
technical checks passed (13), existing API/auth/SAA/wiki checks passed (55), and web lint
and type checks passed. These are local source checks, not deployment or qualitative
research acceptance. Live Streamlit, domains, public API, and broker policy remain unchanged.

## Staged corrective release — October 7, 16:39 UTC

Source `6856a1de` is committed. Clean, minimal Git-archive contexts excluded unrelated
native work and all local credentials. ACR builds `cjr3`, `cjr4`, and `cjr5` succeeded:

| Component | Pinned digest | Deployment |
| --- | --- | --- |
| Web | `sha256:9c3de0f897e9c6b56d459ac925dca8045f46b91626cbeac081c8da92a8d66d98` | dev `0000010`, candidate `0000003` |
| API | `sha256:45e87d1cd5895d853d4a094ed16405a024b5b15fa9d1ae90f812acfb5bb9c4a5` | dev `0000011`, candidate `0000002` |
| Pipeline | `sha256:9a40f9862a84c6cd045a750d4522abdd05921e2c26eb57583866f235637d0bbc` | existing scoped Home/equities jobs only |

Both web/API environments report healthy revisions. The dev owner portfolio still loads
its real linked holdings with 5Y selected. The production candidate now returns unavailable
rather than substituting Alpaca. The original production Streamlit also uses
`APP_TRACK=production`; this is not a candidate-induced broker-environment mismatch.
Actual candidate Investigator review loaded all five CIEN plots without tabs. Broad
Economy displayed multiple charts, raw/smoothed choices, and a real correlation matrix.
Candidate private portfolio/Admin/research routes still reject anonymous requests with 401.

One-execution dev override `equities-next-dev-yd27cgo` succeeded at 16:36:33 UTC. It ran the
new shared SAA bootstrap under `SET TRANSACTION READ ONLY` and logged
`SAA_SCHEMA_READY_WITHOUT_DDL`. The default template/configuration were unchanged by that
probe. This verifies the complete existing schema takes the non-DDL path; it does not
clear pre-existing blocking sessions or certify research quality.

After that probe, template-only PATCH promoted `equities-next-dev`,
`attention-home-next-dev`, and the already-approved production `equities-intraday-preload`
and `attention-home-build` to the pinned pipeline image. Fresh reads verified identities,
configuration/secrets, schedules, resources, commands, and all environment values except
the intended `CODE_VERSION`. ARM omits an empty `ephemeralStorage` default on PATCH; only
that empty/default field was normalized for template comparison. No execution was stopped.
The original immutable rollback bundle remains intact. No other production job was changed.

The newly staged Zopedia UI visibly reports unavailable with Retry and no false page count
or empty-folder claim. Shared legacy locks still block the workspace. Wider job rollout
and the specific stuck-transaction cleanup await owner approval. Broker connection-management
and guarded order-review/execution UI remain explicit parity gaps; do not promote a
read-only candidate as a full replacement. Streamlit prod `0000087`, Streamlit dev `0000456`,
public API `0000003`, and all four live domain bindings were re-read unchanged.

## October 7 approved recovery and cutover

The owner's “Proceed, I'll fix the robinhood separately” authorizes frontend cutover and
leaves broker repair outside this release. Read-only checks in both live Streamlit and the
candidate confirmed the same control database/schema, a portfolio linked to development,
and only development snapshots. The independent production Robinhood worker runs every
15 minutes; its eight observed executions through 17:00 UTC each processed zero work.
This existing state is not a frontend migration requirement to create new credentials.

After the 17:03 UTC inspection, the confirmed blocking read session was terminated only after matching PID
1161355, its exact backend start, idle-in-transaction state, no assigned write transaction,
and a Zopedia SELECT older than 30 minutes. Queued schema updates completed; subsequent
inspection found zero blocked queries and 1,442 stored pages. The owner UI then loaded
1,320 knowledge pages and 60 retained sources; Ciena search returned 13 knowledge matches
and 26 sources. Stored page counts and visible category counts describe different sets.

Recurrence prevention backports only accepted schema guards, tracking-connection limits,
and closing the news-resolution read to the existing immutable pipeline image families.
It does not upgrade their dependencies or unrelated job behavior. Runtime acceptance and
the final production revision are recorded below.

## Production cutover verification

The narrow backports built successfully as `cjr7`, `cjr8`, `cjr9`, `cjra`, `cjrc`, and
`cjrd`. Each passed a managed read-only probe against the installed SAA/Zopedia schema:
the bootstrap performed no DDL, page reads worked, and tracking sessions used 5-second
lock, 120-second statement, and 60-second idle-transaction limits. Probe executions were
`equities-next-dev-71gk4qi`, `-zbg22hc`, `-k58w8cj`, `-spruiut`, `-vynk0jk`, and `-35pbdif`.
All six emitted `LOCK_BACKPORT_READONLY_ACCEPTED`; their default job template was unchanged
by these probes.

Template-only rollout updated 20 existing jobs, including the production and development
research workers, with immutable base-preserving lock fixes. The four already corrected
Home/equities templates remain on `9a40f986`; the same accepted three-file overlay was
also applied to the development daily-datapoint job. Protected configuration, identities,
native secret references, schedules, resources, commands, and environment values were
verified unchanged. No job execution was stopped. Robinhood jobs were excluded. Per-job
original and target templates are retained in `cutover-20261007-6856a1de/lock-rollout.json`
and `lock-rollout-dev.json`.

The signed-in candidate searched Ciena, opened its internal wiki page, and traversed a
connected optical-interconnect theme with sources and backlinks. Research run
`agrun_304443a9458f4cfbb7d1d0a1` dispatched `sn-research-run-3p7t4sw`, which succeeded at
17:15:34 UTC. Actual journal/output review confirmed internal search and page reads,
a completed business explanation with retained-source links, and no live web acquisition
or requested knowledge edits. Its chosen evidence emphasized October 1 and older context;
it did not exhaust the newer October 6 pages visible in search. Do not describe this smoke
check as exhaustive research-quality acceptance or introduce a harness rewrite into this
frontend release.

The approved cutover replaced the frontend template on the existing production app.
Revision `sn-streamlit-ui--workbench-1007171849` runs web digest `9c3de0f8` on port 8501,
is healthy, and receives 100% of traffic. DNS, all four domain/certificate bindings, existing
native secrets, identity assignments, and all other protected app configuration match the
captured baseline. Streamlit dev remains `0000456`; the public API remains `0000003` at
digest `6e2cd73a`. The private production adapter remains `sn-api-next--0000002` at digest
`45e87d1c` with no public ingress and the same production control/data credentials.

All four HTTPS aliases returned 200 for Home and minimal health, 401 for anonymous
Admin/portfolio/research APIs, and 403 for a hostile-origin login request. CSP, HSTS,
no-referrer, and content-type protections are present. The actual web process runs as
UID 1001 with no backend credential environment names. Azure still advertises an identity
endpoint, but token requests for each of the three retained identities returned 400 with
no token. All three have runtime lifecycle `None`, preserving platform image-pull/secret
resolution without granting the Node process their data/control permissions.

At 17:27:46 UTC, the shared database had zero blocked queries and zero transactions idle
for more than two minutes. The actual production Home renders its five model-selected
trend charts and inline ticker price/fundamental histories. The owner sign-in handoff is
open at `https://spectral-nature.com/login?next=%2Fadmin`; final private custom-domain
acceptance remains pending rather than inferred from candidate cookies. No credentials
were copied between origins. Rollback restores `cutover-20261007-same-port/rollback.patch.json`
on the same app; no DNS or certificate rollback is needed.
