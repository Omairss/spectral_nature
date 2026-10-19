# Current frontend and API deployment status

Captured October 18 local time / October 19 UTC after the owner-authorized [Trading production release](../verification/trading-calls-2026-10-18/PRODUCTION.md). Live Azure verification agrees with the table. All five app revisions are ready. The release preserves the Home/research fixes, database recovery timing correction and restored Admin/Health frontend.

| Resource | Role | Image digest prefix | Ready revision |
| --- | --- | --- | --- |
| sn-streamlit-ui | Next.js public domains | `accepted-web@sha256:5e4bfe16c2f7` | `sn-streamlit-ui--0000096` |
| sn-api-next-dev | FastAPI development | `accepted-api-next@sha256:f1b58d382c27` | `sn-api-next-dev--0000048` |
| sn-next-ui-dev | Next.js development | `accepted-web@sha256:5e4bfe16c2f7` | `sn-next-ui-dev--0000025` |
| sn-next-ui | Next.js production alias | `accepted-web@sha256:5e4bfe16c2f7` | `sn-next-ui--0000012` |
| sn-api-next | FastAPI production | `accepted-api-next@sha256:f1b58d382c27` | `sn-api-next--0000015` |

The common current worker is `accepted-worker@sha256:35fde874c971`. Complete immutable digests, Git source, the authenticated production Health observation and all-resource configuration audit are in the linked production record. The public Home response retains its eight stories and five highlights. Protected Admin/Health now requires authentication instead of returning 404.

The [completed comparison of four retained jobs](../verification/rollback-consumer-comparison-2026-10-18/README.md) confirms that universe building, quarterly fundamentals, scheduled monitoring and Robinhood pacing are preserved. Their older shared database implementation bypasses the new admission/cancellation contract; additional publication/completion gaps and the absent production recovery binding are documented there. These remain reconciliation work, not four established losses of their core product functions.

Streamlit/Dash and legacy APIs remain retired. The historical Azure resource name `sn-streamlit-ui` hosts Next.js. The [source policy](RELEASE_SOURCE_POLICY.md) and committed `infra/release_acceptance.json` govern new releases; dated snapshots do not authorize promotion.
