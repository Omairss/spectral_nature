# Legacy retirement — October 16

Status: **176 obsolete tracked files removed, 82 retired roots recorded, exact originals verified in external archives before deletion.** Local source/import/auth/release checks are recorded below. Development cloud retirement is tracked separately from the pending backend recovery deployment. The owner subsequently approved deletion of the obsolete production API `sn-api`; that deletion is complete and separately verified.

## Removed from active source

- The earlier root `src/` Dash/Flask/notebook prototype, root dependency list and unused prototype metadata.
- Streamlit `app.py`, all `views/`, `.streamlit/`, UI lock, UI-only presentation loaders/rendering, launch command, Dockerfile, mobile renderer check and UI-only tests.
- Duplicate Python runtime Dockerfiles (all current Python roles now use `Dockerfile.runtime`), the old UI/API/pipeline bootstrap deployers, filename-based deployment classifier, completed first-migration/cutover generators and old infrastructure-output fallback loading.
- Streamlit's in-process settings editor, static-login selection, UI session-cookie helpers and migration-only session-bearer authentication. Current database accounts, signed access/refresh sessions and scoped agent keys remain.
- The inactive per-horizon Trading rollback producer/mode and its unreachable timeout branches. The whole module matches the accepted source after mechanically removing only those inactive paths; 69 untouched functions match exactly. Current shared research, prompts, horizons, selection and model transport are retained.
- Tests for removed UI/rollback and held experimental paths, and the last held paper-call proxy. Stopped database/Trading work, research inputs/results and scoped resume material remain in the held checkpoint and prior verified archives. Completed acquisition does not need repeating.
- Completed UI/migration/obsolete deployment plans, stale health/debug snapshots and duplicate past notes. Current setup, architecture, infra and philosophy now describe Next.js/FastAPI. Historical UI lessons are compacted into the verified notes archive; retained research and release receipts are evidence only.

The [complete path/hash/archive inventory](retired-paths.json) records every deleted tracked file. Archives live outside the repository under `~/Library/Application Support/SpectralNature/retired-release-sources/`, without restored source directories or symlinks. Their manifests retain exact content and archive hashes. The existing daily cleanup discards completed batches only after 30 days, beginning November 15. Git history also retains the originals.

## Release prevention

The committed `infra/retired_paths.json` records retired roots. `scripts/release_source.py` rejects a restored retired root, including the root prototype outside the application inventory, and independently rejects the retired Streamlit/legacy-API roles even if an acceptance manifest attempts to add them. The committed acceptance record still requires complete source review and role-specific verified image digests. Runtime packaging excludes retired frontend code and current requirements no longer install Streamlit.

The historical directory name `streamlit_alpaca_app` is retained. Production `sn-streamlit-ui` is the **current Next.js site** with live public domains; `sn-next-ui` is its Next.js alias. Neither is a legacy Streamlit service. The current frontend needs no rebuild for this backend recovery.

## Cloud disposition

The reviewed development retirement targets are `sn-streamlit-ui-dev` and `sn-api-dev`. Full definitions were saved privately. The live app/job inventory has no other configured consumer of their endpoints, and neither owns a custom domain. Both resources are deleted. The [development receipt](development-retirement.json) verifies that all 34 remaining app/job definitions retained their exact configuration and image.

After the iPhone/backend route was explained, the owner explicitly approved deleting `sn-api`. It is deleted. The [production receipt](production-retirement.json) verifies all 33 remaining app/job definitions retained their exact configuration and image, including the Next.js frontend, private API and four current public domain bindings. The current Next.js/native path continues through `sn-api-next`. The old public API endpoint is retired. This approval covers this resource deletion only, not the pending recovery-image promotion.

Old and superseded registry images remain historical evidence/rollback records; none of the Streamlit recovery builds was deployed or registered as eligible. This cleanup does not promote the failed release or any held implementation.

## Verification and remaining work

- API, workers and broker import successfully with every Streamlit import explicitly blocked; no surviving Python import references a retired application module.
- Release-source, API authentication, account and API-contract scope: **63 passed**. This includes refusal of restored retired roots and an attempted Streamlit role allowlist. These are execution/security/provenance checks, not language grading.
- The broader broker-data scope exposed two failures in untouched analytics: a fixture did not reach its expected series read, and the local system NumPy/Pandas combination produced a read-only-array error. They are recorded rather than hidden or patched during frontend retirement.
- The [source-fidelity record](../behavior-recovery-2026-10-16/source-fidelity.json) preserves the accepted Home/data/DB behavior; Trading retirement has its own [complete comparison](trading-retirement-fidelity.json).
- No fresh Home output has been accepted or published by this cleanup. Worker packaging hit an inherited image layer-depth limit; the broad-copy images were also superseded before deployment. Backend packaging, development rollout and fresh full-output review remain open in the [recovery record](../behavior-recovery-2026-10-16/README.md). The later owner instruction conditionally authorizes production recovery if full development review passes; after a development failure, fix the cause and ask for owner review.

## Unused stored settings

Four obsolete, unregistered prompt keys have been exported and verified in the external 30-day archive batch `20261017T005920Z`. The [retirement record](unused-settings-retirement.json) is prepared; no database mutation has occurred. These keys do not affect current prompts or the language rule. Their final removal is held with the conditionally authorized production completion; all five active custom prompts and eleven parameter overrides are preserved.
