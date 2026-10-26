# October 26 Home freshness diagnosis

Read-only production investigation, observed 17:07–17:13 UTC (10:07–10:13 a.m. Pacific). No runtime changes, job starts, deployments or model inference.

## Confirmed cause

The October 26 Home publication exists and FastAPI serves it. Both production Next.js gateways continue serving the October 23 publication because their cached backend fetch cannot store today's larger entry. Production logs repeatedly report:

> Failed to set Next.js data cache for https://sn-api-next.internal.bluefield-2d27dcf2.centralus.azurecontainerapps.io/v1/home, items over 2MB can not be cached (2351160 bytes)

This number describes the serialized cache entry, including its encoded response, rather than the raw HTTP response size. Next.js 16.3.8's incremental cache checks `JSON.stringify(data).length` against `2 * 1024 * 1024`; in production it warns and returns without replacing the entry. The configured 15-second revalidation therefore repeatedly fails to replace Friday's value.

The backend fetch cache was introduced by commit `f29c623ea1e79335596ea18d8fb1b4c12a12db9a` on October 22 in `web/lib/server/home.ts`, shared by the page and `/api/home`. The preceding gateway used `backendFetch`'s default `no-store`. This is a serving regression exposed by publication growth; the acceptance checks missed replacement by a larger publication.

## Evidence

| Observation | Result |
| --- | --- |
| Production schedule | `50 14,16,18,20 * * 1-5` UTC; unchanged scheduled trigger |
| Today's first execution | `attention-home-build-29883770`, 14:50–16:07 UTC; Azure `Succeeded` |
| Application run | `c88ae637-ec0e-4d4b-90cc-1f97ad33415d`; application status `Warning` for an unrelated macro dataset SQL indexing failure |
| Home publication | Blob latest pointer and ready SQL version both identify `attention_home_snapshots_1d__20261026T145037Z__c88ae637`; ingested 15:57:47 UTC (8:57:47 a.m. Pacific) |
| Stored Home frame | One row, run `c88ae637…`, generated-at `2026-10-26T14:50:37.093797+00:00` |
| Direct FastAPI read | Container-local HTTP `/v1/home` returned today's run in 0.317 seconds |
| Public live gateway | `/api/home` returned run `294b87f9-a39b-4794-a2d8-9753664dc3c9`, generated-at `2026-10-23T20:50:37.879460+00:00`; both Azure web hostnames returned the same old run |
| Explicit dated read | `/api/home?date=2026-10-26` returned today's run; this uses historical SQL discovery and a separate fetch key |
| Gateway warnings | Repeated on both `sn-streamlit-ui` and `sn-next-ui`; observed from 16:42 through 17:11 UTC |
| Next scheduled execution | `attention-home-build-29883890`, started 16:50 UTC and still running during this investigation |

Current immutable images match the October 23 accepted publication release: worker `40cd5c6f0e6648b9cef3507aea676bbb5caf1781a65baa64c85dc59b19bb0aae`, API `7697aadde0e64086c708f2e65bdb859e160fda314976111044126e39e2edddce`, web `6f143ff4bcea1089031cd9a7f1a2576e9437bdafe2bcc8d501e44aff0ca45c05`. The observed API revision is `sn-api-next--0000023`. No historical image rollback is indicated by these observations.

## Separate Highlights issue and repair scope

The first run's daily datapoint selection timed out with `Final synthesis exceeded its remaining phase budget` at approximately 15:55 UTC. It did not publish a new `daily_market_datapoints_1d` version. Direct FastAPI still reports Highlights selected October 23 at 18:50 UTC. This is separate from the gateway retaining the entire old Home response; removing the cache fault alone would not create today's missing Highlights.

The main repair should remove the live gateway's dependence on caching the whole publication in Next's size-limited fetch cache. The backend already serves a materialized publication quickly. Preserve conditional ETags, client retention, audio continuity and independent ticker/Highlight updates. Do not truncate research or charts to fit the cache. The owner subsequently authorized the scoped loader patch. [Local review](LOCAL.md), [complete large-publication HTTP checks](local-http.json) and [exact-image Node 22 verification](packaged-image.json) pass. [Development revision 36](DEV.json) is deployed, Healthy and verified against its backend. The owner subsequently authorized production promotion, now completed and [verified on both gateways](PRODUCTION.json). Highlights retain their separate older selection after the producer timeout.
