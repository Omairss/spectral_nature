# Custom Domain Cutover - 2026-04-05

## Summary

Moved custom domain ownership from Development UI app (`sn-streamlit-ui-dev`) to Production UI app (`sn-streamlit-ui`) in Azure Container Apps.

## Scope

- Resource group: `sn-pipeline-rg-03130136`
- Environment: `sn-pipeline-env`
- Domains:
  - `torres-cap.com`
  - `www.torres-cap.com`

## What changed

1. Removed both hostnames from `sn-streamlit-ui-dev`.
2. Added and bound both hostnames on `sn-streamlit-ui`.
3. Reused existing managed certs:
   - `mc-sn-pipeline-en-torres-cap-com-8958`
   - `mc-sn-pipeline-en-www-torres-cap-c-5247`
4. Refreshed `documents/infra/UI_DEPLOYMENT_STATUS.md`.

## Verification

- Hostname ownership:
  - `az containerapp hostname list -g sn-pipeline-rg-03130136 -n sn-streamlit-ui-dev` => empty
  - `az containerapp hostname list -g sn-pipeline-rg-03130136 -n sn-streamlit-ui` => both domains present
- HTTPS checks:
  - `https://torres-cap.com` => HTTP 200
  - `https://www.torres-cap.com` => HTTP 200
- Response headers match Production app (`last-modified` aligns to prod FQDN response).

## DNS note

DNS is external (Google Domains/Cloud DNS nameservers for `torres-cap.com`). Current resolution for `www.torres-cap.com` still chains through the dev generated FQDN but lands on the same environment static IP (`172.168.33.46`), so traffic is healthy after hostname cutover.

Recommended cleanup:

- Update `CNAME www` to:
  - `sn-streamlit-ui.bluefield-2d27dcf2.centralus.azurecontainerapps.io`

## Rollback

Reverse the same hostname delete/add/bind sequence from prod back to dev using the same certificates.
