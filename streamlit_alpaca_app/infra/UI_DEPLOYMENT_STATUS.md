# UI Deployment Status Tracker

Last updated (UTC): 2026-03-26 17:19

## Environment Mapping

| Role | Container App | URL | Latest Revision | Image | Health |
|---|---|---|---|---|---|
| **Production (stable)** | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000030` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:129b48cdc9432536a3b0c55ef5d7361bd8068467e3ed86807cca7f33bcb51e0a` | HTTP 200 |
| **Development** | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000054` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:129b48cdc9432536a3b0c55ef5d7361bd8068467e3ed86807cca7f33bcb51e0a` | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- Both apps use the same managed identity and Key Vault-based auth configuration.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
