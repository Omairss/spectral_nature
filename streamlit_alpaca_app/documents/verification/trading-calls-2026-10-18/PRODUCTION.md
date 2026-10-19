# Production — Trading paper pilot, October 18–19

Deployed under the owner’s explicit instruction **“Push to prod.”** This accepts the general framework for measured iteration; prior model valuation errors remain documented improvement evidence. No claim of perfect forecasts or demonstrated automatic return improvement is made.

Production uses the exact development-accepted images built from Git `aa43e3d2ef617acfa67616f586149f1c60b4d0e3`:

| Role | Immutable digest |
| --- | --- |
| Worker | `35fde874c971770a5d0250b8facb476e2a4047bdf06532754b58e7c578f16dd6` |
| API | `f1b58d382c271b91889eee3f97a367b8f40e242d84ad961928636a6862fc8827` |
| Next.js | `5e4bfe16c2f70cd2d2a71089958d403b993238a4f1526b80c45559d0450449f2` |

[Complete source/image verification](packaged-images.json) · [DEV acceptance](DEVELOPMENT.md) · [Production deployment](production-deployment.json) · [All-resource audit](production-configuration-audit.json) · [HTTP checks](production-http.json).

Fifteen production templates updated: twelve workers, modern API and both Next.js entrypoints, including the historical `sn-streamlit-ui` resource that hosts Next.js. API revision 15, primary web revision 12 and alias revision 96 are ready/running. The complete audit covers 33 resources: 25 intended image changes across DEV/production and **zero configuration changes**. Untargeted images, credentials, identity, domains, schedules, resources and scaling are preserved. Hotfixes and other sessions’ uncommitted work remain separate from this accepted source.

Production `/health` and Home return 200. Trading, approved-call detail and Admin/Health return 401 anonymously; Health is restored from its previous 404. Complete Home JSON remains equal to the pre-release capture, SHA256 `f6acdd8303ff64b46fe92bde625a7ab0fc429a9f76ae06e223b5eab61e809a12`. This preserves the actual summaries, graph, stories, fundamental/price histories and highlights, not only response status.

A fresh normal authenticated production session renders collapsed legacy calls, all five horizons, retained decisions and Paper performance. This owner has no eligible production approvals, so its history displays the approval empty state; no call was fabricated or approved for QA. The real DEV approved call was independently valued and visually verified with its dated call/SPY chart. Production price-only execution `trading-agent-build-tuf0b5p` **succeeded**, using the valid empty production namespace. It made no model calls or broker orders and did not change the saved template. Fresh authenticated Admin/Health now loads production telemetry observed October 19 at 03:46:53 UTC, confirming current deployed code rather than the old cached October 16 tab.

Normal Trading refreshes remain in progress: DEV `trading-agent-build-dev-fobq3lt`, production `trading-agent-build-6mz16u9`. Their last-good generation reports remain visible until publication; this record does not mislabel pending generation as completed or claim a fresh v3 report is already published. The shared company-research workflow and its existing model/budgets remain unchanged apart from the reviewed directional synthesis contract. Pending provider receipts are retained through the existing reconciliation outbox. The existing production learner schedule will review completed approved outcomes through the shared queue.

The native simulator build passed. No new physical-phone installation or broker action was performed in this release.
