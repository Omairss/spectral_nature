# Active architecture

Updated October 20, 2026. Current ownership; historical source and migration plans do not define release scope.

| Boundary | Owner | Role |
|---|---|---|
| Product clients | `web/`, `ios_app/SpectralNatureMVP/` | Next.js web and SwiftUI native clients render shared contracts. |
| HTTP/account transport | `api/`, web gateway routes, `services/auth*` | Shared identity, authorization, access/refresh tokens and agent keys. |
| Dataset/chart reads | `data_access/`, `compute/`, shared view services | Retained research reads and explicitly supported data/account operations. No hidden research during rendering. |
| Research/knowledge | Shared AQL/Zopedia engine, gateway and services | Planning, acquisition, synthesis/review, durable progress, knowledge and cost ownership. |
| Home production | `pipeline/jobs/attention_home_build.py` | Full-intake accepted editor and shared review; retained output consumed by the API. |
| Data production | `pipeline/jobs/` | Independent market, fundamental, news, index and maintenance producers. |
| Broker ingestion | `services/robinhood_main_sync.py`, `robinhood_main_store.py` | Dedicated provider-isolated ingestion and last-good retention. |
| Chart presentation | `presentation/plotly.py`, `visualization_artifacts.py`, client renderers | Canonical chart/data contracts and edge rendering; no Streamlit loaders. |
| Release | `scripts/release_source.py`, `release_application.py`, committed acceptance record | Reviewed Git source and verified role-specific immutable images. |

Streamlit (`app.py`, `views/`, UI loaders/config/lock and its tools) and the earlier root Dash `src/` are retired. Their archive is outside the checkout; no importable tree or launcher remains. The production resource named `sn-streamlit-ui` hosts Next.js. The application directory name is also historical.

The DAL's former live attention research runtime remains removed. A retained artifact or structured unavailable result is returned on reads; explicit research stays in the shared engine. Current Home scoring and stored-artifact/data-shape compatibility still used by the accepted product remain current behavior, regardless of historical identifiers. Do not replace an active algorithm merely because a variable contains `legacy`.

The October 20 stock-detail integration reads complete company analyses from `zopedia_ticker_business_model_stacks` through the existing DAL. The shared company producer retains earlier analyses across subset refreshes and prioritizes current Attention subjects. Web and installed SwiftUI clients consume the same stock contract; page reads do not start research. Development verification and remaining research-quality findings are recorded in the [stock-detail review](../verification/stock-detail-shared-research-2026-10-20/review.json). Production promotion is separate.

The [October 18 cleanup audit](cleanup-2026-10-18/README.md) records retirement of unused AQL/attention facades, the obsolete summary/critique stack and the staggered-source slice adapter. Research/search callers use their AQL implementation owners; dataset slicing belongs directly to `services.pipeline_store`; chat/scratchpad state belongs to `services.agents`. `aql/summarizer.py` now only projects retained stories. The accepted cleanup source was promoted to production on October 20; see the [release policy](../infra/RELEASE_SOURCE_POLICY.md) for its exact source and images.

The cleanup continuation makes `services.database.DatabaseSession` own transaction discard and lease return, removes repeated store teardown, and exposes auth/pipeline connections through their existing public factories. `services.json_utils` owns DAL/view JSON normalization. Factory outages remain optional where already supported; cancellation and unexpected defects propagate. These changes are part of that accepted cleanup release; concurrent dirty work in the original shared checkout remains outside release authority.

See the [retirement inventory](../verification/legacy-retirement-2026-10-16/README.md), [release policy](../infra/RELEASE_SOURCE_POLICY.md), [regression audit](../verification/release-source-regressions-2026-10-16/README.md) and [recovery state](../verification/behavior-recovery-2026-10-16/README.md).
