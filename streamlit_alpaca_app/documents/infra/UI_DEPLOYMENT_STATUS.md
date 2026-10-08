# UI Deployment Status Tracker

Last updated (UTC): 2026-10-07 17:38

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000087` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5` | browser cookie (explicit opt-in) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000456` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5` | session only (disabled) | HTTP 200 |
| **Alternative Next.js Development** | `sn-pipeline-rg-03130136` | `sn-next-ui-dev` | https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-next-ui-dev--0000004` | `snpipelineacr03130136.azurecr.io/next-ui-dev@sha256:3d21a9163e4979eb1b5d4bda9ffeedcd87d781df185bd086baad9321ab0dc7c1` | HTTP-only host cookies through same auth store | HTTP 200; one warm replica |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Session persistence should be set explicitly during the current image transition.
  `UI_DISABLE_BROWSER_SESSION_COOKIE=1` or `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0`
  selects session-only behavior; `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=1` is an
  explicit browser-cookie opt-in.
- UI deploys default to automatic web layout (`STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=auto`) unless the deploy command explicitly overrides those vars.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
- The alternative Next.js development app is an independent frontend. Its private API is
  `sn-api-next-dev--0000007` at digest
  `sha256:ca9718c8f0b0b1448b481bbbd2017473bdde24ccf36f00d61fcaf051a585cdc2`;
  it has no public ingress and keeps one warm replica. This release did not update either
  Streamlit app.
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
- Custom-domain health must be checked on the exact hostname users open, including DNS, `/_stcore/health`, and a browser WebSocket session. Azure custom-domain binding alone is not enough proof.

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
