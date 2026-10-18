# Shared context recovery

Status: **accepted locally; DEV API and six workers are deployed; fresh macro/Home verification is in progress. Production has not received this candidate.** The current [release acceptance record](../../../infra/release_acceptance.json) is the deployment authority. The owner authorized production only after DEV works and meets the earlier subjective research quality bar.

## Current source

The [complete local review](CONTEXT_HANDOFF_LOCAL_REVIEW.md) and [full outputs](context-handoff-local.json) cover the current repair. Final writing and existing review reuse Zopedia's existing evidence reader. The LLM chooses working context and can reopen originals; complete traces remain retained. The writer no longer expands selected whole containers. Capacity feedback preserves the original task and valid direct answers. No financial rules, phrase filters, new critic, model change or larger task budget is added.

The complete replay also found and corrected annual FRED comparisons that used row counts. Calendar periods/dates now define a year, and actual comparison dates/values are retained. All original observations and latest levels remain intact. The final normal-finalizer/Home/chart replay finishes in 380.09 seconds with five new exact-history charts; four nonfinancial cases separately exercise shared context recovery. These local results authorize DEV verification, not production by themselves.

Eight cumulative application/packaging files differ from the accepted review base. The complete application inventory has 312 files and the Python runtime inventory has 197. Builds replace the whole application tree from committed Git. The API and worker dependency bases are the separately verified role-specific database-lifecycle images; actual consumer images remain rollback baselines. The first API package incorrectly used its preincident baseline as dependencies and failed imports for missing `psycopg_pool`; it was rejected before deployment and rebuilt with the verified API dependency pin.

## Deployment scope and acceptance

DEV targets are the modern API and six workers: Home, daily highlights, macro, queued research, Trading research and equities. Conditional production targets are the modern API and twelve existing research/data workers. The API also serves the current web and iPhone clients. The accepted database lifecycle/cancellation integration is explicit in these source builds. Held Trading-call/reasoning experiments remain excluded.

Thirteen other resources retain their images, including every Next.js frontend and broker worker. All credentials, identities, model settings, schedules, allocations and public domain bindings must remain unchanged. Do not run unrelated paid research or broker operations to exercise image templates.

The [packaged-image verification](context-handoff-packaged-images.json) passes for both roles: all 197 files match Git, imports pass and cold context accounting works with downloads blocked. Worker dependency metadata is unchanged; API adds only the exact tokenizer versions to its reviewed pool-enabled dependency base. The [API dependency comparison](api-dependencies-review.json) separately establishes the pool addition against its actual previous consumer. Refresh DEV macro data first, then run the normal complete Home workflow and assess its own newly generated highlights. Read all public stories, overview and captions against original sources and observations. Verify serving latency, native-compatible JSON, narration, intraday histories, provenance, protected access and actual revision readiness. Only accepted exact DEV image digests may move to production under the owner's conditional permission.

The existing API renders the latest 1,200 observations per highlight; complete stored originals remain available to the model. That unchanged display window is distinct from model-context integrity.

## Current DEV runtime

[All seven image-only updates](context-handoff-development-deployment.json) succeeded. The [full audit](context-handoff-development-configuration.json) confirms all 33 resources, unchanged effective configuration, four domain bindings and retired resources still absent. The new API revision is ready. Actual signed-in browser checks load [all five existing Trading horizons](context-handoff-development-trading.json), with no decision or broker operation and no regenerated Trading output. Public Home still serves the preceding artifact while fresh generation runs; that output is not accepted as this candidate's quality evidence. The DEV macro execution `macro-fred-daily-dev-qx4055p` succeeded in 5m06s. The [35-series numerical check](context-handoff-development-macro.json) verifies actual reference observations and annual arithmetic. Current rolling-window refresh expires eight October 17, 2016 observations; all overlapping values are unchanged. The complete Home execution is `attention-home-next-dev-bzfcjgg`. Production images and generated artifacts remain unchanged in this repair.

## Historical evidence

These records describe failed preceding candidates and must not be used as the current source or deployment scope:

- [First full DEV quality failure](DEVELOPMENT_QUALITY_REVIEW.md): process prose, categorical interpretations and unresolved chart feedback.
- [Continued DEV quality failure](CONTINUED_DEVELOPMENT_QUALITY_REVIEW.md): 1,199,136-token final chart request exceeded the 1,048,576 window and retained earlier highlights. Later source investigation corrected the mistaken treatment of a stored annual CPI figure as authoritative.
- [Earlier local continuation review](LOCAL_CONTINUATION_REVIEW.md) and [earlier replay outputs](local-replays.json).
- [Actual per-consumer historical rollback](../exact-production-recovery-2026-10-16/README.md) and [accepted source comparisons](../behavior-recovery-2026-10-16/source-fidelity.json).

Earlier worker-only and chart-only proposals are superseded by the current shared source repair and complete DEV publication requirement. Historical images and extracted trees remain evidence or explicit rollback targets; they cannot seed a new application build.
