# Active architecture

Updated 2026-10-07. Ownership map; deployment revisions belong in `infra/UI_DEPLOYMENT_STATUS.md`.

| Boundary | Owner | Role |
|---|---|---|
| Product clients | `web/`, `ios_app/SpectralNatureMVP/`, legacy `app.py` and `views/` | Render shared API/data contracts; native client uses the web `/api/` gateway. |
| HTTP and account transport | `api/`, web API routes, `services/auth*` | Shared identity and authorization, browser/native transport. |
| Dataset and chart reads | `data_access/layer.py`, `query_service.py`, `query_registry.py`, `contracts.py` | Retained research reads, local transforms, explicitly supported market/account provider reads. No construction of model, embedding, or search clients. |
| Research and knowledge | `services/aql_zopedia_engine.py`, `aql_zopedia_gateway.py`, `services/aql/`, Zopedia services | Explicit research execution, model gateway, durable knowledge and progress. |
| Research production | `pipeline/jobs/attention_home_build.py` (`attention-home-build`) | Acquires sources and produces retained attention/research artifacts through existing research services. |
| Market snapshot production | `pipeline/jobs/attention_ticker_snapshot_build.py` | Independent deterministic price/fundamental materialization. |
| Broker ingestion | `services/robinhood_main_sync.py`, `robinhood_main_store.py` | Provider-isolated durable ingestion; untouched by this cleanup. |
| Presentation | `presentation/` | UI caching and rendering; further loader consolidation remains open. |

## Retired research paths

The 2026-10-07 cleanup deletes the DAL's `_resolve_live_attention_artifacts`,
`_resolve_symbol_agentic_bundle`, `_resolve_web_search_news`, and their private
filing acquisition, mover-combination, and evidence-scoring machinery. These were
a duplicate research runtime, not a separate supported product feature.
`ATTENTION_SYMBOL_BUNDLE_PRECOMPUTED_ONLY` no longer controls behavior. Research
bundle reads always return a retained artifact or structured unavailable state,
even with `force_refresh=True`; explicit research is owned by AQL/Zopedia.
Recent news retains the existing materialized sources and raw Alpaca news fallback
where permitted, but never starts web search or synthesis.

## Remaining cleanup

The September cleanup score is historical and has not been recomputed. Its metric
script was never implemented. Remaining work includes import-cycle measurement,
legacy root/dependency retirement, presentation loader consolidation, Streamlit
shell reduction, and pipeline dispatch consolidation. The root `src/` and root
`requirements.txt` are legacy; the application Dockerfiles use the requirements
within `streamlit_alpaca_app/` as their build context. Do not remove either root
until all external launch/deployment references are accounted for.
