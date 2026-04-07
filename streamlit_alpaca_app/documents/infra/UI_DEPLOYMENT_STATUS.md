# UI Deployment Status Tracker

Last updated (UTC): 2026-04-07 19:22

## Environment Mapping

| Role | Container App | URL | Latest Revision | Image | Health |
|---|---|---|---|---|---|
| **Production (stable)** | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000046` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:d855cec36c76c1733edcf32312dbad95dc75288bd04b5d9ee33da2a9edb4132c` | HTTP 200 |
| **Development** | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000154` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:d855cec36c76c1733edcf32312dbad95dc75288bd04b5d9ee33da2a9edb4132c` | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- Both apps use the same managed identity and Key Vault-based auth configuration.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
