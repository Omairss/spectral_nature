# Current frontend/API deployment status

Captured October 18, 2026 after the owner-authorized production rollout. [Current production record](../verification/research-resilience-2026-10-18/PRODUCTION.md), [configuration audit](../verification/research-resilience-2026-10-18/production-configuration-audit.json) and committed `infra/release_acceptance.json` are the active release references. All five app revisions are ready.

| Resource | Role | Image | Ready revision |
|---|---|---|---|
| sn-streamlit-ui | Next.js public domains | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-streamlit-ui--0000095` |
| sn-api-next-dev | FastAPI development | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:9c28b4c4b3b389a59efb552de33e15961f63fc0369120f2918847e98a5a55104` | `sn-api-next-dev--0000045` |
| sn-next-ui-dev | Next.js development | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-next-ui-dev--0000023` |
| sn-next-ui | Next.js production alias | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-next-ui--0000011` |
| sn-api-next | FastAPI production | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:9c28b4c4b3b389a59efb552de33e15961f63fc0369120f2918847e98a5a55104` | `sn-api-next--0000013` |

The Next.js images and four public domains are unchanged by this backend/worker rollout. The retired Streamlit/Dash frontend and legacy APIs remain deleted. No native client binary was changed. Earlier deployment snapshots are historical evidence, retained in Git and the verified private 30-day archive; they are not current commands or source baselines.
