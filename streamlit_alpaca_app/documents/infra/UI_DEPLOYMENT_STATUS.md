# UI Deployment Status Tracker

Last updated: October 16, 2026. The operational reliability release is promoted to four production apps and sixteen job templates. Both frontends and both APIs reached ready revisions. Production Health includes the source-guarded bounded collector tested in dev; the shared research engine is unchanged at `7281e3631a1764cf48c9`. Allocations, schedules, credentials and inference settings are preserved. The held reasoning/cost flow and hierarchy defaults are excluded.

**Production remains operationally degraded:** database saturation continues, the retained-data research check failed, and scheduled monitoring exceeded its platform window while draining recovery work. Ready images are not an overall health certification. The [current production receipt](../verification/system-reliability-production-2026-10-16/README.md) owns exact final images, revisions, SQL protection, checks and remaining work. The [development receipt](../verification/system-reliability-2026-10-16/README.md) records the original local/dev acceptance.

## Earlier deployment history

Native build 7 is installed on the owner's iPhone over USB. It retains build 5's
accepted compact price/fundamentals and conversation client, and improves Markets
with interpretable returns, numeric colors, point identities and named return bars.
Signed simulator/device builds and the normal production Markets path pass.
CoreDevice confirms installed build 7. After owner unlock, launch succeeded and
the app remained running as PID 30309.
No server image, setting, credential or account data changed during this native task.
See the [native Markets record](../plans/NATIVE_MARKETS_CHARTS_2026-10-14.md) and
[build 7 receipt](../verification/native-analytics-2026-10-14/build7/release.json).

October 11 operational recovery: all nine development and fifteen production worker templates used guarded r19 images; protected configuration checks passed, and all 27 jobs were provisioned. Both full r18 news runs succeeded with 25 company narratives and 12 event interpretations each; every event interpretation was personally read. The acquired-reference transport repair preserved nine development pages without durable writes and restored eight production pages through an audited mutation with durable readback. Full Home r15 completed in both namespaces (production Succeeded; development Warning for partial research), with five populated charts, playable audio and all five optional page narratives per namespace checked. Ordinary final-image health execution `system-health-29862570` succeeded and reported zero open incidents. Development Streamlit revision `sn-streamlit-ui-dev--0000457` was ready; production frontend/API images were unchanged by that repair. See the [repair record](../plans/PIPELINE_RELIABILITY_REPAIR_2026-10-09.md).

## October 9 pre-repair diagnosis

- Maintenance and learning leave transactions open across lengthy work. The
  pipeline's October 7 sixty-second idle-transaction limit disconnects them before
  result persistence. The same exceptions occurred yesterday.
- Both attempts of the 18:05 news execution exited 137 during acquisition. Memory
  reached 2.1057 GiB against a 2 GiB allocation; exhaustion is strongly supported,
  but the precise allocation and kernel termination reason remain unverified.
- Home published the 18:50 snapshot and completed at 19:29, with partial market
  inputs recorded as Warning. The 20:05 news execution is still running at capture.
- These failures describe the 20:12 UTC diagnosis. Transaction ownership,
  independent tracking, browser capacity, metadata waits and maintenance catalog
  indexing are now repaired at source. Full news/learning acceptance is underway;
  the earlier accepted recovery below describes specific runs, not global health.

## October 9 operational recovery

- Fixed the missing research-budget clock import across affected worker images.
  Production Trading Agent retained eight completed company packages, five
  successful horizons and sixteen candidates. Its shared page reader loads the
  fresh run as available.
- Converted fully paginated market-data batches immediately and increased equity
  workers to two CPUs/4 GiB. The normal production refresh published 1,001 symbols
  and 1,463,634 history rows, then completed successfully.
- Job records now retain Azure execution IDs. The health worker reconciles exact
  terminal status with literal namespace matching and execution-read permission.
  Scheduled execution `system-health-29859495` succeeded on the final image and
  left zero open incidents.
- Identity, secrets, provider settings, schedules and commands are verified
  against original snapshots. The only setting changes are equity capacity and
  the monitor's resource-group configuration. See the
  [repair receipt](../architecture/overall/audits/evidence/SERVICE_REPAIR_2026-10-09.json).

Native client build 3 now uses `https://spectral-nature.com` by default. It is
installed on the owner's iPhone; fresh native sign-in is pending. The development
portfolio returns 503 because the active linked connection belongs to production;
the authenticated live web portfolio loads correctly. This correction changes
only the native client and does not deploy or modify production servers.

## Earlier environment mapping — October 15

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (modern workbench)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://spectral-nature.com | `sn-streamlit-ui--0000092` | `snpipelineacr03130136.azurecr.io/attention-fundamentals-dev@sha256:8380f82bf07cc249ee8a6761569c0a344fc30aed162d64ab3c45618292fd2c1d` | HTTP-only host cookies through same auth store | HTTP 200; healthy, latest ready, full traffic; accepted dev frontend promoted |
| **Production alias** | `sn-pipeline-rg-03130136` | `sn-next-ui` | https://sn-next-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-next-ui--0000008` | `snpipelineacr03130136.azurecr.io/attention-fundamentals-dev@sha256:8380f82bf07cc249ee8a6761569c0a344fc30aed162d64ab3c45618292fd2c1d` | HTTP-only host cookies through same auth store | HTTP 200; healthy, latest ready, full traffic |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000457` | `snpipelineacr03130136.azurecr.io/pipeline-reliability@sha256:759c4e3cd7e5e4e860812954e3bc39159f2a1f6837a5506673a7ea43e850e027` | session only (disabled) | HTTP 200 |
| **Next.js Development** | `sn-pipeline-rg-03130136` | `sn-next-ui-dev` | https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-next-ui-dev--0000020` | `snpipelineacr03130136.azurecr.io/attention-fundamentals-dev@sha256:8380f82bf07cc249ee8a6761569c0a344fc30aed162d64ab3c45618292fd2c1d` | HTTP-only host cookies through same auth store | HTTP 200; one warm replica; latest ready with full traffic |

## Promotion Workflow

1. Deploy modern UI changes to **Next.js Development** (`sn-next-ui-dev`) first; use `sn-streamlit-ui-dev` for legacy Streamlit changes.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- The modern web process uses the private API for the existing auth/control store;
  it receives no backend credentials. Legacy Streamlit settings below apply to
  Streamlit only.
- Legacy Streamlit session persistence is explicit:
  `UI_DISABLE_BROWSER_SESSION_COOKIE=1` or `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0`
  selects session-only behavior; `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=1` is an
  explicit browser-cookie opt-in.
- Legacy Streamlit deploys default to automatic web layout (`STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=auto`); its sidebar displays the `APP_TRACK` environment.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
- The modern development private API is `sn-api-next-dev--0000030`; production is
  `sn-api-next--0000007`. They use the identical accepted conversation API image,
  with separate production/development namespaces and queued-worker targets.
  Both APIs retain private ingress and their existing replica configuration.
- Development revision 455 maps Robinhood `queued` and `running` refreshes to
  the visible `In progress` state on both portfolio surfaces and prevents a
  linked Robinhood portfolio from silently falling back to Alpaca before its
  first local snapshot exists.
- Development revision 456 and production revision 87 run the same verified
  image. The list Sankey ranks all analytics-ready groups on the selected
  lookback, renders balanced best/worst tails with their returns, and exposes
  one-to-five groups per side without coupling the ranking universe to the
  equal-weight comparison selection.
- Production owns the only active Robinhood connection and uses its distinct
  encryption secret. `robinhood-main-sync-prod` runs hourly at `0 * * * *`
  with parallelism one, retry limit zero, and a 40-minute timeout.
- Development ingestion is parked at `0 0 1 1 *`; its connection is
  disconnected and its credential columns are empty. Both jobs run the same
  environment-neutral worker digest
  `sha256:2a8e36cdb6d9fc84e2d6fe568d3f6ac2d03f267c8634977e831e0be35c1a16c3`.
  Safe post-deployment executions enqueued and processed zero work; the
  production session was separately verified with one controlled two-account
  read and no login challenge.
- Custom-domain health must be checked on the exact hostname users open, including DNS, `/health`, authentication and a real private workbench read. Legacy Streamlit also needs `/_stcore/health` and its browser WebSocket session. Azure custom-domain binding alone is not enough proof.

## Shared read-layer cleanup — 2026-10-07

- Source: `9b96e693`; checkpoint including the native app: `35ea4cb6`.
- Isolated development API: `sn-api-next-dev--0000012`, healthy, latest ready,
  with 100% traffic on the latest revision.
- Image: `snpipelineacr03130136.azurecr.io/api-next-dev@sha256:2a75475ae3ab04e35e8624042a04a6d66a5364059ad214a57013a8afa0dafc46`.
- ACR run `cjrf` used a 25.874 KiB context and overlaid only
  `data_access/layer.py` on revision 11's pinned image. API and DAL imports passed
  inside the image build. The web image, Streamlit images, broker jobs, and
  production were not changed by this release.
- Local validation: 81 DAL/QueryService, API, and presentation checks passed.
- Running-revision verification: `/health` returned 200; the public development
  `/api/home` returned 200 with `status=available`. The retained bundle dataset
  had 79 rows; a retained bundle loaded as `materialized` with `force_refresh=True`.
  A missing ticker returned `unavailable`; the deleted symbol research runtime
  was absent from the live DAL.
- Rollback: update only this development API's image to its previous digest,
  `sha256:45e87d1cd5895d853d4a094ed16405a024b5b15fa9d1ae90f812acfb5bb9c4a5`.

## Native price-history contract — 2026-10-08 UTC / October 7 local

- Development API revision `sn-api-next-dev--0000013` is healthy, provisioned,
  latest ready, with 100% traffic on the latest revision.
- Image: `snpipelineacr03130136.azurecr.io/api-next-dev@sha256:ce178f784163ae6048865af59c08829170f179260ffe4018d6c6c6339d110724`.
- ACR run `cjrg` used a 4.302 KiB source context, overlaying only
  `services/home_view.py` onto revision 12's pinned image. It adds numeric
  `price_trend` observations from the existing retained ticker snapshot. There
  is no new provider, model call, price computation or ingestion job.
- Public development `/api/home` returned `available`, 21/21 ticker quotes and
  21/21 price histories. CIEN closes at $443.75 and AEHR at $91.92 on October 6;
  each history contains 41 observations. The actual Swift DTO decodes all 21.
- Native version 1.0.0, build 2, compiled with the owner's existing automatic
  signing, installed on the connected iPhone 13 Pro, and launched successfully.
  Native simulator build also passes. Installation and launch receipts are in
  `/tmp/sn-ios-phone-install.json` and `/tmp/sn-ios-phone-launch.json`.
- Concurrent LLM/backend/frontend work was excluded. Production, web images,
  Streamlit and pipeline jobs were not updated. Native admin editing remains
  gated on its separately released settings contract.
- Rollback only this development API to its previous digest,
  `sha256:2a75475ae3ab04e35e8624042a04a6d66a5364059ad214a57013a8afa0dafc46`.

## Shared LLM settings and language — 2026-10-08 UTC / October 7 local

- Development API `sn-api-next-dev--0000014` and web
  `sn-next-ui-dev--0000011` are latest ready. Web latest-revision traffic is 100%.
- API image: `snpipelineacr03130136.azurecr.io/llm-language-dev@sha256:b48e44b851b025905b64d69041640875ad66e63b702bcae5d6a54a5c019047d5`.
- Web image: `snpipelineacr03130136.azurecr.io/next-ui-dev@sha256:a6d674a1b4916d4c02258ac0e118d01e961ae0a0072eb2fcb1422afe6374aec7`.
- Eight development non-broker job templates now apply the shared request-time
  language policy. Jobs received only hash-verified `services/llm.py` and
  `services/aql_zopedia_gateway.py` overlays on their exact previous images.
  API overlay base verification passed and preserves revision 13's native price
  history contract. Web used an isolated source build. ACR runs: `cjrh`, `cjrj`,
  `cjrk`, `cjrq`, `cjrp`, `cjrm`, `cjrn`.
- Protected configuration comparisons passed on all ten targets. The API additionally
  receives only the worker's non-secret provider/model/base URL/temperature/timeout
  defaults. Model credentials were not copied to API or web. Broker jobs, legacy
  Streamlit apps, and production were not updated.
- Running API inspection confirms development scope, reasoning enabled, the 80%
  default style, 17 prompts, and 42 parameters. Signed-in browser acceptance saved
  the requested default through the new authenticated editor. Public health/Home
  return 200 and unauthenticated Admin returns 401. Controlled live language samples
  were reviewed personally; no owner conversation or retained knowledge was written.
- Full target/image/rollback details are in
  [DEV_RELEASE_2026-10-07.json](../architecture/AQL/audits/DEV_RELEASE_2026-10-07.json).
  Rollback API image to `api-next-dev@sha256:ce178f784163ae6048865af59c08829170f179260ffe4018d6c6c6339d110724`
  and remove only the five newly added LLM environment defaults. Rollback web image
  to `next-ui-dev@sha256:9c3de0f897e9c6b56d459ac925dca8045f46b91626cbeac081c8da92a8d66d98`.
  Rollback each job to its manifest's exact previous image. Prefix rollback image
  names with `snpipelineacr03130136.azurecr.io/`.
- [Audit](../architecture/AQL/audits/LLM_AND_HARNESS_AUDIT_2026-10-07.md) records actual
  Flash resolution for the legacy reasoner alias and shared harness work still
  recommended. The release changes language/configuration behavior, not the
  selected model or research-loop architecture. Production requires explicit approval.

## Shared harness fixes — development, 2026-10-07

- Ready API: `sn-api-next-dev--0000015`; ready frontend: `sn-next-ui-dev--0000012`. Both serve 100% latest-revision traffic. All eight non-broker dev job templates use the new shared harness images. Production unchanged.
- Malformed reviews cannot approve memory updates. Drafts remain available with review state. Writer, reviewer and approved retention receive complete collected passages and references; provider attempts and repair share a monotonic deadline. Hard cancellation remains open.
- Personally inspected three live final answers, four reviews and the local draft view. Runtime/source checks, typed response state, live Home, signed-in Admin and protected configuration checks passed. No acceptance conversation, knowledge write or job execution was created.
- [Exact build, source, image and rollback receipt](../architecture/AQL/audits/DEV_HARNESS_RELEASE_2026-10-07.json). [Design and acceptance record](../plans/HARNESS_RELIABILITY_FIXES_2026-10-07.md).

## Shared language/settings and harness — production, 2026-10-08 UTC / October 7 local

- Explicitly authorized by the user. `sn-streamlit-ui--0000088` serves the custom
  domains; `sn-next-ui--0000004` serves the Azure frontend alias. Both use the exact
  accepted development web digest, with their original runtime API URLs and ports.
- Private API `sn-api-next--0000003` uses
  `harness-prod@sha256:2c91dc77d99424aa5c93fe4ca2160c6546acdb168ac440198bb4e0bc07fcce5a`.
  ACR build `cjsa` overlays only accepted LLM/settings/harness modules on the original
  production base. It excludes the concurrent development DAL, Home/native and
  discovery changes. All three application revisions are ready with 100% traffic.
- Fifteen non-broker production worker templates are updated. Matching bases reuse
  exact accepted dev digests. The ten older workers use build `cjse`, which verifies
  original source, applies the exact shared agent patch with zero fuzz, and passes
  imports. Its projection also passes 26 local harness contract checks.
- Thirteen unlabeled production workers received only `APP_TRACK=production` in
  addition to their image update. All other settings, identities, credentials,
  commands, schedules and resources compare equal to their private snapshots.
  Sixteen excluded resources, including both broker jobs, all development resources
  and the separate legacy public `sn-api`, remain unchanged.
- Production scope already resolves to the 80% default; no LLM settings write was
  needed. The signed-in Admin on the production Azure alias shows editable model,
  prompt and language controls. Custom-domain root/health/Home/Zopedia return 200;
  Home is available. Its browser correctly requires a separate host session.
  API health is 200, unauthenticated Admin is 401, and the delivered research bundle
  includes the draft review-state label. Runtime draft serialization and memory
  guards match the accepted source. No research conversation, knowledge entry or
  manual job execution was created for acceptance.
- [Production receipt and exact rollback images](../architecture/AQL/audits/PROD_HARNESS_RELEASE_2026-10-07.json).
  Restore each recorded previous image and remove only its recorded added
  `APP_TRACK` variable. Provider/model selection is unchanged.

## Home discovery and explanation repair — production, October 8 local

- Explicit user authorization: “Okay deploy to prod.” All 15 non-broker workers,
  the private API and both modern frontends are promoted. API revision 5, custom
  domain frontend revision 89 and alias revision 5 are ready with full traffic.
- Both frontends use the exact accepted development digest. Source-guarded API
  and legacy-worker builds preserve their actual production baselines. Home and
  equity workers include the shared typed-metadata loader correction from `cjt8`;
  the three corresponding development workers also receive that correction.
- Protected runtime comparisons pass for all 18 production targets. Home's cron
  moves to minute 50 after equity data at minute 35. Credentials, identities,
  namespaces, model/language settings, resources and other schedules are preserved.
  Broker jobs, old public API and existing dev frontend are unchanged.
- Eight source-guarded transport builds add incomplete-response retry handling
  within the existing deadline on all 25 active Python targets across prod/dev.
  Dev API is now revision 19. Protected runtime comparisons pass on all targets.
- Custom-domain root, health and Home return HTTP 200. The standard Home run
  `f905bd8a-9c63-4b97-8abc-0b482e74d9a1` completed research but failed on a
  truncated provider response during publication. Its failed database state is
  preserved; the duplicate full-research execution retry was stopped. The earlier
  loader failure exposed and corrected the shared typed-metadata regression.
- Continuation `attention-home-build-bcw7x2s`, run
  `4893138a-fa36-4ecd-8d38-2557600ab07e`, published from retained production
  research. Azure succeeded; database `Warning` records advisory review timeouts
  and partial market inputs. No dev content or mocked narratives were used.
  The live API preserves all six canonical explanation fields on sixteen cards.
  Chrome shows the fresh overview, OpenAI story first, business context and
  follow-up. Reasoning gaps and the incomplete checkpoint design remain recorded
  in the Home repair plan; no claim of perfect causal accuracy is made.
- [Release and exact rollback receipt](../architecture/AQL/audits/PROD_HOME_DISCOVERY_REPAIR_2026-10-08.json).


## Native feed and portfolio analytics — development, October 14

- Development API revision 29 and modern web revision 17 are ready. Home and ticker
  producer images are updated through baseline-preserving numeric-only overlays.
  Their schedules, commands, identities, credentials and other configuration are
  preserved. Production has not changed for this task.
- The development-only quarterly refresh published bank/insurance families. The
  subsequent ticker execution publishes 19 intraday charts for 21 feed tickers and
  up to 1,250 daily points. Session change uses the previous close in the same feed;
  longer history dates remain visible. Source coverage still excludes Citigroup.
- Native build 4 passes signing and full-app development-gateway review in the
  simulator. It is not installed on the physical phone, whose paired network tunnel
  is disconnected. Private portfolio previews use retained real data without moving
  production connections or broker credentials into development.
- Five production image candidates and exact rollback images are prepared. Promotion,
  production data refresh and physical installation remain pending explicit approval
  and a reachable phone. [Acceptance and release scope](../plans/NATIVE_ANALYTICS_PARITY_2026-10-13.md)
  and [immutable release receipt](../verification/native-analytics-2026-10-14/release-receipt.json).
