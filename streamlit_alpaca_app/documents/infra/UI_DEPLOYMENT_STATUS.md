# Current frontend/API deployment status

Captured October 16, 2026 during legacy retirement. This records existing live images, not acceptance of the pending backend recovery. The production backend remains affected by the October 16 release regression; see the [recovery record](../verification/behavior-recovery-2026-10-16/README.md).

| Resource | Current role | Image | Ready revision |
|---|---|---|---|
| sn-streamlit-ui | Next.js public site; current domains | `snpipelineacr03130136.azurecr.io/system-reliability-dev-web@sha256:cabfd78f4590d96bc4b6b7c62cd3206885ef2a633963f58e934a55453d02e17a` | `sn-streamlit-ui--0000094` |
| sn-api | Legacy production API; retirement pending approval | `snpipelineacr03130136.azurecr.io/system-reliability-health-v2-api-legacy@sha256:788a2fa572efd106e71e672347292585b2b0d6dac0b0191bf341420dca1fdb3f` | `sn-api--0000005` |
| sn-api-next-dev | Current development FastAPI | `snpipelineacr03130136.azurecr.io/database-lifecycle-dev-api-next@sha256:ebe45efccea4554e050e5f8bd713d5b578736c25a19b5375e76eccbd4d5276c4` | `sn-api-next-dev--0000036` |
| sn-next-ui-dev | Next.js development | `snpipelineacr03130136.azurecr.io/system-reliability-dev-web@sha256:cabfd78f4590d96bc4b6b7c62cd3206885ef2a633963f58e934a55453d02e17a` | `sn-next-ui-dev--0000022` |
| sn-next-ui | Next.js production alias | `snpipelineacr03130136.azurecr.io/system-reliability-dev-web@sha256:cabfd78f4590d96bc4b6b7c62cd3206885ef2a633963f58e934a55453d02e17a` | `sn-next-ui--0000010` |
| sn-api-next | Current production FastAPI | `snpipelineacr03130136.azurecr.io/system-reliability-health-v2-api-next@sha256:a9aa26c09f84bf07f01acd9eff55da415894ff1952e3a8589730ff7634fe610f` | `sn-api-next--0000010` |

`sn-streamlit-ui-dev` and `sn-api-dev` are deleted. All six remaining apps and 28 jobs retained their exact images/configuration. No recovery image was deployed during this cleanup.

Streamlit/Dash deployment and first-cutover tools are retired. Current release preparation follows the [release policy](RELEASE_SOURCE_POLICY.md). The previous long status history is retained in the verified 30-day documentation archive and in Git; it does not supply current deployment instructions.
