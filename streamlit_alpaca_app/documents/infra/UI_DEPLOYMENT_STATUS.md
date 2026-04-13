# UI Deployment Status Tracker

Last updated (UTC): 2026-04-13 02:38

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000054` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:e1e52cab33af292c85b4e44677b0b7b42372cbeaeed68a554f2cdca9e4fb7d0d` | browser cookie | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000192` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:6d6e70e3d53a23d3e135369260203f2015c37a9cf0911ce49c24d168d9b087d3` | session only | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Browser persistence is controlled by `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE` and is tracked above for each app.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
