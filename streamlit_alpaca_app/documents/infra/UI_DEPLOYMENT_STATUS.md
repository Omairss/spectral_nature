# UI Deployment Status Tracker

Last updated (UTC): 2026-04-14 22:17

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000055` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:866b5fe1dc59048ea53db045f4e6b23a378f0bda4b67bb91f3c7290c17f6110e` | browser cookie (default) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000207` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:aa23335bb32e71310036df3f0a80c996e9fe89250c0e8682a6f3fa4876e8e7a4` | browser cookie (default) | HTTP 200 |

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
