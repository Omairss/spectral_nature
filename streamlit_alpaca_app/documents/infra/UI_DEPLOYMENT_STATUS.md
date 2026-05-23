# UI Deployment Status Tracker

Last updated (UTC): 2026-05-23 23:03

## Environment Mapping

| Role | Resource Group | Container App | URL | Latest Revision | Image | Auth Persistence | Health |
|---|---|---|---|---|---|---|---|
| **Production (stable)** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui` | https://sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui--0000069` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:59b26661a001a98450eb8508082f55b974619855f242be418b2e59a63c3aec0c` | browser cookie (default) | HTTP 200 |
| **Development** | `sn-pipeline-rg-03130136` | `sn-streamlit-ui-dev` | https://sn-streamlit-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io | `sn-streamlit-ui-dev--0000371` | `snpipelineacr03130136.azurecr.io/streamlit-ui@sha256:59b26661a001a98450eb8508082f55b974619855f242be418b2e59a63c3aec0c` | browser cookie (default) | HTTP 200 |

## Promotion Workflow

1. Deploy new UI changes to **Development** app (`sn-streamlit-ui-dev`) first.
2. Validate key views and auth in Development.
3. Promote by updating **Production** app (`sn-streamlit-ui`) to the approved image/revision.
4. Update this tracker file with new revision IDs and verification status.

## Notes

- UI container apps live in resource group `sn-pipeline-rg-03130136`.
- Both apps use the same managed identity and Key Vault-based auth configuration.
- Browser session persistence is **on by default**. Disable with `UI_DISABLE_BROWSER_SESSION_COOKIE=1`. The legacy `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=0` also disables it.
- Development UI deploys default to automatic web layout (`STREAMLIT_MOBILE_UI_ENABLED=true`, `STREAMLIT_LAYOUT_MODE_DEFAULT=auto`) unless the deploy command explicitly overrides those vars.
- Sidebar now displays `Environment: production` or `Environment: development` via `APP_TRACK`.
- Keep Production stable by avoiding direct experimental changes to `sn-streamlit-ui`.
- 2026-05-23 custom-domain check: `www.spectral-nature.com` and the generated production FQDN load the Streamlit app and `/_stcore/health` returns `ok`. The apex `spectral-nature.com` currently resolves to Squarespace A records (`198.185.159.144`, `198.185.159.145`, `198.49.23.144`, `198.49.23.145`) even though Azure has a bound managed certificate. Fix DNS at Google Cloud DNS by replacing the apex A records with the Container Apps environment static IP `172.168.33.46`; keep `asuid` TXT records unchanged.
