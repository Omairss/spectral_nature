# UI Deployment Status Tracker

Last updated (UTC): 2026-10-03 02:13

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000087` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5` | browser cookie (explicit opt-in) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000456` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5` | session only (disabled) | HTTP 200 |

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
- Development revision 455 maps Robinhood `queued` and `running` refreshes to
  the visible `In progress` state on both portfolio surfaces and prevents a
  linked Robinhood portfolio from silently falling back to Alpaca before its
  first local snapshot exists.
- Development revision 456 and production revision 87 run the same verified
  image. The list Sankey ranks all analytics-ready groups on the selected
  lookback, renders balanced best/worst tails with their returns, and exposes
  one-to-five groups per side without coupling the ranking universe to the
  equal-weight comparison selection.
- Production has a distinct Robinhood encryption secret and the scheduled
  `robinhood-main-sync-prod` worker at digest
  `sha256:cb476de492ffd31f29b15c41ca396433f33df782a798f62d5d6a63fe214cd9ef`.
  The worker runs every 15 minutes with parallelism one, retry limit zero, and
  a 40-minute timeout. Its empty-queue verification completed without a broker
  call.
- Custom-domain health must be checked on the exact hostname users open, including DNS, `/_stcore/health`, and a browser WebSocket session. Azure custom-domain binding alone is not enough proof.
