# UI Deployment Status Tracker

Last updated (UTC): 2026-03-18 07:35

## Environment Mapping

| Role | Container App | URL | Latest Revision | Image | Health |
|---|---|---|---|---|---|
| **Production (stable)** | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000006` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:bc552720057a6ab2b5033714bd9fff9e8715ec1774c2b47dbe72cca99daae13c` | HTTP 200 |
| **Development** | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000011` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:cc136dbb6458c7d61e63a5ce571919def1ed971d042d6368cc24afb53eadc6a7` | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- Both apps use the same managed identity and Key Vault-based auth configuration.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
