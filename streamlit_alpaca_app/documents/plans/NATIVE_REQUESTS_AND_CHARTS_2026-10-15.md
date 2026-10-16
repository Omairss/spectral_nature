# Attention direction, peripheral Markets points and feature requests

The owner requests three native improvements after installed build 7: visible
up/down movement in the Attention network, a lighter Markets plot containing the
outer K instruments, and in-app feature requests with follow-up messages. The owner
confirmed that updates mean follow-up messages, not release notes or build delivery.

## Design

- Preserve the Attention artifact's existing Plotly marker semantics. Its producer
  already emits triangle-up/down for movement, circles for flat securities and
  distinct markers for path/context nodes; the native adapter currently discards
  them. Render direction using both shape and color, keep the selected outline
  separate, and display a concise legend and selected-node movement. Edges remain
  relationships; do not invent directed causality or infer direction from prose.
- Add a shared native geometric selector for outer K points using successive
  convex-hull layers. Spread partial layers around the boundary rather than taking
  a contiguous arc. Handle collinear and coincident points without synthetic
  positions. Markets defaults to 40, with an adjustable count. Axes and color scale
  continue to describe the full paired-data universe; ranked instruments and return
  bars remain complete. The shared scatter renderer owns the point reduction.
- Add durable feature-request threads and messages behind the existing signed-in
  application API. Users see their own threads; active admins can inspect and reply
  to requests. Server-derived identity and environment own every read/write. Store
  initial request/message atomically and use client-generated UUIDs for safe retries.
  No LLM, email, Slack, external issue tracker or execution automation is involved.
- Native Explore exposes Feature requests, a new-request form, thread history and
  a message composer. Keep drafts on submission errors and only clear them after
  server confirmation. The Next.js gateway uses the existing cookie, Origin/CSRF
  and authenticated-backend transport. Production and development stay separate.

## Release

Read the current graph producer, native adapter, shared scatter renderer, app auth,
database and gateway contracts before implementation. Review real graph/market data
and phone layouts before creating a signed release. Verify persistence, retries,
ownership and environment isolation with the real dev path. Deploy only the scoped
API/gateway changes to dev after local acceptance; no unrelated dirty source enters
an image. The owner's AGENTS instruction says: “Auto deploy changes to dev, but not
to prod without explicit permission.” Prepare a concrete, verified production
candidate before asking for that promotion. Do not install a production client
whose required feedback endpoints are unavailable.

## Local acceptance progress

Implemented the native direction adapter, shared peripheral selector, request/thread
screens and authenticated API/gateway. The 999-instrument snapshot selects 40 outer
points while retaining all four axis extremes. Collinear, coincident and singleton
cases passed the standalone selector review. Real PostgreSQL persistence, retries,
ownership, admin replies, namespace isolation and atomic rollback passed (4 checks;
QA connection timeout 15 seconds). Workspace TypeScript checking passed.

The first visual review confirmed sparse Markets points, stable full-universe scales
and distinct up/down markers. It exposed expanded hidden network labels, now corrected
at the adapter. The request inbox loads; complete submission/thread UI acceptance is
pending because the host became heavily overloaded and simulator discovery/input
stalled. No successful submission is claimed from that interrupted run.

Scoped candidates preserve the accepted API image and reconstruct the accepted web
source from its verified conversation snapshot plus the three accepted Attention
files. The web build context is `/tmp/sn-native-requests-web-release`; no dotenv files
are present and Docker excludes dotenv, dependencies and Git. Remote image builds succeeded: `native-requests-api-dev:20261015-r1` (ACR run
`cj14d`) and `native-requests-web-dev:20261015-r1` (`cj14e`). Neither has been
deployed. Build 7 remains the last accepted installed phone version.


## Resume point — 2026-10-15 12:35 Pacific

Final UI review is blocked by the Mac: load average 484, Simulator application
launch timeout, and `simctl install` failed with invalid state / Mach server died.
Reopening the QA simulators and restarting CoreSimulator did not restore reliable
operation. Do not keep retrying or report this as successful native acceptance.
A host restart is needed before resuming simulator review. No production or phone
update has been attempted. Native release version remains 7.

After restart: boot only iPhone 16 (`525BDA40-486D-4991-8938-9B91667E7D23`),
install the successful preview at
`/tmp/sn-native-requests-preview-derived/Build/Products/Debug-iphonesimulator/RequestsPreview.app`,
then launch with `SIMCTL_CHILD_SN_SERVER_URL=http://127.0.0.1:8768`.
Restart the isolated QA server from `/tmp/sn-native-requests-preview/qa_server.py`
using `.venv/bin/python -m uvicorn qa_server:app --app-dir /tmp/sn-native-requests-preview --host 127.0.0.1 --port 8768`.
Its namespace is `feedback-qa-native-20261015` and actor is synthetic; production
credentials are never copied. Review a new request, a follow-up, reopened persistence,
the final network labels/selection and a compact phone layout. Then deploy the
pinned scoped candidates to dev, verify normal auth and environment/config integrity,
prepare signed native 8, and ask for production promotion under the owner rule.

Candidate manifest: `tmp/native-requests-20261015/candidate-manifest.json`.
Web context: `/tmp/sn-native-requests-web-release` (dependency clone removed after
successful isolated build). API context is adjacent to the manifest. Existing
baseline config snapshots are `/tmp/sn-native-requests-{api,web}-before.json`;
keep private config snapshots out of published receipts. The current dev images
were still the accepted conversation API / Attention web digests before building.

Local review receipt: `documents/verification/native-requests-2026-10-15/review.json`.
Both immutable candidate image digests are recorded there and in the manifest.
