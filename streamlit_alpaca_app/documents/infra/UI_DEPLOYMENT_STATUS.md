# Current frontend/API deployment status

Captured October 18, 2026 after the owner-authorized Home presentation cleanup. [Current production record](../verification/home-presentation-cleanup-2026-10-18/PRODUCTION.md), [configuration audit](../verification/home-presentation-cleanup-2026-10-18/production-configuration-audit.json) and committed `infra/release_acceptance.json` are active release references. All five app revisions are ready.

| Resource | Role | Image | Ready revision |
|---|---|---|---|
| sn-streamlit-ui | Next.js public domains | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-streamlit-ui--0000095` |
| sn-api-next-dev | FastAPI development | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:6e6dbd9bfdcbe4d85e5dcb2f4d9f66510f62a8fd52e267db0f5ce00e66957ba5` | `sn-api-next-dev--0000046` |
| sn-next-ui-dev | Next.js development | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-next-ui-dev--0000023` |
| sn-next-ui | Next.js production alias | `snpipelineacr03130136.azurecr.io/native-workbench-web-dev@sha256:ba0aa2bd6912eae732b95f122ac02b1d2cd7a6133b798c58c5691c33176ef0b6` | `sn-next-ui--0000011` |
| sn-api-next | FastAPI production | `snpipelineacr03130136.azurecr.io/accepted-api-next@sha256:6e6dbd9bfdcbe4d85e5dcb2f4d9f66510f62a8fd52e267db0f5ce00e66957ba5` | `sn-api-next--0000014` |

Next.js images and all four public domains are unchanged. Streamlit/Dash and legacy APIs remain retired. Native binaries were not changed. Previous deployment snapshots are historical evidence, not current source baselines.
