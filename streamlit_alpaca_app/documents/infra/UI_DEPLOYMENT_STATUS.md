# UI Deployment Status Tracker

Last updated (UTC): 2026-06-24 19:31

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000083` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:fc6e492d00ce3022049e853400a45ae0a5f82a520206dc2a945b8cfc6242d7f5` | browser cookie (default) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000429` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:b218f2d06de33c9135f461f9cd30b265bf88faba306b568a4e6246841cd08dbf` | browser cookie (default) | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Browser session persistence is **on by default**. Disable with `UI_DISABLE_BROWSER_SESSION_COOKIE=1`. The legacy `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0` also disables it.
- UI deploys default to automatic web layout (`STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=auto`) unless the deploy command explicitly overrides those vars.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
- Custom-domain health must be checked on the exact hostname users open, including DNS, `/_stcore/health`, and a browser WebSocket session. Azure custom-domain binding alone is not enough proof.
