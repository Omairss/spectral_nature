# Current resource index

Azure is authoritative for live configuration and revisions; Key Vault owns secret values. Generated local context belongs in ignored `infra/.generated/*.local.env`. This file describes current resource roles, not deployment approval.

Resource group: `sn-pipeline-rg-03130136`. Registry: `snpipelineacr03130136.azurecr.io`. Shared runtime vault: `spectral-nature-kvault`.

| Role | Development | Production |
|---|---|---|
| Next.js frontend | `sn-next-ui-dev` | `sn-streamlit-ui` (public domains), `sn-next-ui` (alias) |
| FastAPI backend | `sn-api-next-dev` | `sn-api-next` |
| Research/data jobs | Development targets in the committed acceptance record | Production targets in the committed acceptance record |
| Broker ingestion | `robinhood-main-sync-dev` | `robinhood-main-sync-prod` |
| Bounded durable recovery | `system-recovery-dev` (manual) | `system-recovery` (scheduled; 60-second batches) |

The production name `sn-streamlit-ui` is historical: it serves Next.js. Do not rename/delete it as a frontend cleanup, because it owns current ingress/domain bindings. The obsolete development resources `sn-streamlit-ui-dev` and `sn-api-dev` are deleted. The legacy production API `sn-api` is also deleted following explicit owner approval. All are outside normal release eligibility; see the [retirement report](../verification/legacy-retirement-2026-10-16/README.md). Do not use them to obtain current application source or environment defaults.

For current revisions/images, read Azure, the [Trading release](../verification/trading-calls-2026-10-18/PRODUCTION.md) and the subsequent [reliability restoration](../verification/complete-reliability-restoration-2026-10-18/PRODUCTION.md). Older sections in deployment history do not describe current runtime ownership.

```bash
bash scripts/show_infra_inventory.sh
bash scripts/show_infra_inventory.sh --write-local
```

The inventory tool reads modern API targets for shared runtime/mail settings. The frontend contains browser transport settings, not provider/database secrets. Provider ingestion, shared auth and research retain their existing data stores and identities.

The five-minute shared production evidence-index job is `evidence-index-dev`. Its historical resource name and development news dataset namespace do not isolate the corpus-wide evidence/embedding tables from production. Keep it in production worker release coverage. Embeddings and original source projection remain independent background work. Job schedules are preserved during this recovery. Changing schedules, credentials, paid tiers or schema is outside an image-only release.

Production readers use the existing Azure text-embedding-3-small deployment zopedia-embedding with 1,536 dimensions, independently of DeepSeek chat. The owner-authorized profile is [infra/production_embeddings.json](../../infra/production_embeddings.json); all 18 production Python consumers are configured consistently. Verify both shared-index coverage and real semantic/hybrid results from the serving API and research worker. See the [October 25 activation readback](../verification/evidence-index-2026-10-25/reader-deployment-verification.json).

The [October 25 cost optimization](../verification/azure-cost-retirement-2026-10-25/plan.json) retires the two empty registries and two empty older PostgreSQL servers after archive validation. The two 03130136 databases remain the data/authentication boundary. Web/API allocations are declared in [infra/container_resources.json](../../infra/container_resources.json), with exact existing images retained. Registry retirement follows [infra/registry_retention.json](../../infra/registry_retention.json): protected live/rollback/dependency digests remain available, and obsolete images receive 30 days of native recovery before automatic purge.

Two ML compute instances, `spectral-nature` and `spectral-nature3`, are now retired after full encrypted-archive verification. Shared workspace user files have a separate verified archive, and their originals remain intact. `spectral-nature2` remains stopped pending authorized local-disk export; see the [access record](../verification/azure-cost-retirement-2026-10-25/spectral-nature2-export-blocker.json). Preserve the workspace storage and shared managed identity.
