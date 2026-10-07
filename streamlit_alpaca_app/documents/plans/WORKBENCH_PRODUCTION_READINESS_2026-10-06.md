# Workbench production readiness

Date: 2026-10-06 (verification continues October 7 UTC)

Status: production staging authorized by the owner; live traffic must remain on Streamlit.
No domain swap is authorized. Do not confuse an accepted development UI, generated release
templates, healthy containers, and a verified production candidate.

Staging exists at `https://sn-next-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io`:
web revision `sn-next-ui--naqb7s0`, private API revision `sn-api-next--77ejayy`, and manual
`sn-research-run`. Web image source is `c14f0b22`; the staged API/worker use the accepted
development digests. Production Streamlit remains on revision `0000087`; all domain
bindings match the pre-staging snapshot. The candidate needs the owner's sign-in for
production session/portfolio/research acceptance.

## Deployment boundary

Keep `sn-streamlit-ui` and `sn-streamlit-ui-dev` unchanged. Stage the accepted images as
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

The production pipeline already runs. Only these two producers need the accepted image:

| Existing job | Existing UTC schedule | New read contract |
| --- | --- | --- |
| `attention-home-build` | `20 14,16,18,20 * * 1-5` | embedded five-chart model selection and quarterly financial histories |
| `equities-intraday-preload` | `35 13,16,18,20 * * 1-5` | symbol partitions for price and technical histories |

Before promotion, production Home and ticker artifacts were current through October 6,
but ticker rows lacked financial histories, the daily-datapoint artifact was absent, and
the 1.47-million-row price and technical histories had no symbol partitions. Freshness
alone therefore did not prove compatibility with the new consumer.

Use template-only JSON Merge PATCH for these existing jobs, preserving configuration,
native secrets, identity, schedules, retry/timeout budgets, and commands. Full ARM PUT of
an existing job cannot preserve a write-only native secret by copying its GET response;
`listSecrets(self)` introduces an ARM circular dependency. Do not deploy the all-jobs
pipeline script for this scoped promotion.

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

Current domains: `spectral-nature.com`, `www.spectral-nature.com`, `torres-cap.com`, and
`www.torres-cap.com`. Preserve their existing managed-certificate IDs. Apex A records point
to the shared environment ingress IP, `172.168.33.46`; the two www CNAMEs currently point
directly to the old Streamlit generated hostname. Managed certificate renewal requires
each www CNAME to point directly to the destination app, not an intermediate alias.
See [Azure managed-certificate requirements](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-managed-certificates).

No DNS or domain bindings change during staging. Before an approved swap, capture current
DNS TTL/records, validate domain ownership on the destination, and prepare the exact
certificate/domain transfer and www DNS changes together. Verify HTTPS and same-host
login/mutations on all four aliases after transfer. Keep Streamlit running for rollback.
Restore original domain/certificate bindings and www CNAMEs if acceptance fails; restoring
the old app alone does not restore DNS routing.

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

The production Home refresh is running as `attention-home-build-ltpmk4j`. Account-flow
inspection also found that Next can issue invitation/reset links but does not consume
the native root-query links. Those screens and the existing auth service's API access are
required before cutover; do not describe sign-in alone as account lifecycle parity.
