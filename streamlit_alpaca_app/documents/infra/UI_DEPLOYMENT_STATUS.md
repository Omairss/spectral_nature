# Current frontend/API deployment status

Captured October 18, 2026 after the development database continuation and Admin/Health frontend correction. [Current production record](../verification/home-presentation-cleanup-2026-10-18/PRODUCTION.md), [development database verification](../verification/database-lifecycle-2026-10-18/README.md), [development frontend verification](../verification/admin-health-frontend-2026-10-18/README.md) and committed `infra/release_acceptance.json` are active release references. All five app revisions are ready.

| Resource | Role | Image | Ready revision |
|---|---|---|---|
| sn-streamlit-ui | Next.js public domains | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-streamlit-ui--0000095` |
| sn-api-next-dev | FastAPI development | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:1cba5594ecbb4d9bd87d12840b84802da792ae944cb042cce57e1a72bb8c46a3` | `sn-api-next-dev--0000047` |
| sn-next-ui-dev | Next.js development | `snpipelineacr03130136.azurecr.io/accepted-web@sha256:91730986547ca814a857528f7ca6d536dcf75b03db694ed9ed4a3f8b0679033d` | `sn-next-ui-dev--0000024` |
| sn-next-ui | Next.js production alias | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-next-ui--0000011` |
| sn-api-next | FastAPI production | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:6e6dbd9bfdcbe4d85e5dcb2f4d9f66510f62a8fd52e267db0f5ce00e66957ba5` | `sn-api-next--0000014` |

Production images and all four public domains are unchanged by these development continuations. The current DEV frontend includes the verified Admin/Health route and screen. Streamlit/Dash and legacy APIs remain retired. Native binaries were not changed. Previous deployment snapshots are historical evidence, not current source baselines.
