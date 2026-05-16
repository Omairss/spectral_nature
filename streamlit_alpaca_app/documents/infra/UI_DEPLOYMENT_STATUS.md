# UI Deployment Status Tracker

Last updated (UTC): 2026-05-16 02:32

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000066` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:382aa37a0954f8c431143a62d7346be2c518971382ae143b68acd2d4f38f364a` | browser cookie (default) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000306` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:382aa37a0954f8c431143a62d7346be2c518971382ae143b68acd2d4f38f364a` | browser cookie (default) | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Browser session persistence is **on by default**. Disable with `UI_DISABLE_BROWSER_SESSION_COOKIE=1`. The legacy `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0` also disables it.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
