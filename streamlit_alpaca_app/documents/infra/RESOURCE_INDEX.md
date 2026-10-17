# Current resource index

Azure is authoritative for live configuration and revisions; Key Vault owns secret values. Generated local context belongs in ignored `infra/.generated/*.local.env`. This file describes current resource roles, not deployment approval.

Resource group: `sn-pipeline-rg-03130136`. Registry: `snpipelineacr03130136.azurecr.io`. Shared runtime vault: `spectral-nature-kvault`.

| Role | Development | Production |
|---|---|---|
| Next.js frontend | `sn-next-ui-dev` | `sn-streamlit-ui` (public domains), `sn-next-ui` (alias) |
| FastAPI backend | `sn-api-next-dev` | `sn-api-next` |
| Research/data jobs | Development targets in the committed acceptance record | Production targets in the committed acceptance record |
| Broker ingestion | `robinhood-main-sync-dev` | `robinhood-main-sync-prod` |

The production name `sn-streamlit-ui` is historical: it serves Next.js. Do not rename/delete it as a frontend cleanup, because it owns current ingress/domain bindings. The obsolete development resources `sn-streamlit-ui-dev` and `sn-api-dev` are deleted. The legacy production API `sn-api` is also deleted following explicit owner approval. All are outside normal release eligibility; see the [retirement report](../verification/legacy-retirement-2026-10-16/README.md). Do not use them to obtain current application source or environment defaults.

For current revisions/images, read Azure or the newest explicit release receipt. Older sections in deployment history do not describe current runtime ownership.

```bash
bash scripts/show_infra_inventory.sh
bash scripts/show_infra_inventory.sh --write-local
```

The inventory tool reads modern API targets for shared runtime/mail settings. The frontend contains browser transport settings, not provider/database secrets. Provider ingestion, shared auth and research retain their existing data stores and identities.

The five-minute development evidence-index job is `evidence-index-dev`; embeddings and original source projection remain independent background work. Job schedules are preserved during this recovery. Changing schedules, credentials, paid tiers or schema is outside an image-only release.
