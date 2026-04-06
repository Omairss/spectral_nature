# UI Deployment Status Tracker

Last updated (UTC): 2026-04-06 01:20

## Environment Mapping

| Role | Container App | URL | Latest Revision | Image | Health |
|---|---|---|---|---|---|
| **Production (stable)** | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000045` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:a9cc3ab8b22aa4b4d5ce4b6619ee5e5a1fab3b1c13f695b7ab8bab1c71a13831` | HTTP 200 |
| **Development** | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000142` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:9c3289a84751a7592e38db3c0e7c59084f5e16f76ebaaa294a72e07457f7b2af` | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- Both apps use the same managed identity and Key Vault-based auth configuration.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
