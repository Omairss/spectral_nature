# LLM settings, language, and research audit

Requested 2026-10-07. Status: local implementation, audit, development release and explicitly authorized modern production promotion complete.

The language/settings changes and three accepted harness fixes are now deployed to
the modern production workbench and fifteen non-broker worker templates. See the
[production receipt](../architecture/AQL/audits/PROD_HARNESS_RELEASE_2026-10-07.json)
and [implementation/release record](HARNESS_RELIABILITY_FIXES_2026-10-07.md).
The phase-specific development notes below retain the original sequence and scope.

## Findings

- The Next.js Admin page renders prompts in `pre` blocks and parameters in a table.
  It has no mutation route or editing controls. The Streamlit predecessor had both.
  “Overridden” identifies an existing database value; it is not a permission lock.
- Narrative style is embedded in module-level prompt constants. The separately
  editable global style is not applied by model adapters. Overrides load once per
  process, including when the first database read fails. Workers can keep old state.
- The prompt store replaces its entire table on save. It does not isolate deployment
  environments or preserve concurrent edits. Development and production share stores.
- Provider adapters run regex substitutions over every returned string, including
  source text, URLs, code, and structured values. Language must instead be generated
  correctly, with technical and quoted content preserved.

## Implementation

1. Keep settings in the existing LLM service and table. Save only changed keys in
   a transaction, scope new writes by APP_TRACK, preserve legacy reads, and use
   scoped reset markers so resets can suppress legacy overrides. No runtime DDL.
2. Restore authenticated admin editing for global language, prompts, and tuning.
   Expose model selection and inference controls within the configured provider;
   credentials, provider, endpoint, and Azure deployment identity remain deployment-owned.
3. Apply the active language policy when each provider request is assembled, across
   direct callers and the gateway. Default to an 80% ASD-STE100-inspired style:
   simple vocabulary, active voice, short sentences, one idea at a time, stable
   terminology, and explicit actors. Preserve research depth, necessary financial
   terms, citations, literal quotations, names, URLs, code, and machine fields.
   The percentage is a style target, not a compliance score. Remove destructive
   string replacement and frozen prompt style copies.
4. Refresh saved settings in running processes with a bounded cache; explicit admin
   reads force refresh. Existing generated artifacts are not rewritten.
5. Inspect actual model identity and harness behavior. Compare current primary
   documentation with the adapter, context handling, tool loop, review, cancellation,
   memory writes, and telemetry. Record evidence and recommendations before any
   broader harness rewrite or production/model rollout.

## Validation and release

Check persistence isolation, atomic failure, resets, API admin authorization, and
request assembly. Judge language quality by personally reading live model outputs
over varied research tasks; no lexical score or fixed prose assertions. Verify the
local Admin editing flow before building. Deploy only the isolated development API,
Next.js frontend, and all eight non-broker development model jobs after local acceptance. Production
requires explicit permission. Do not create verification conversations in the owner's
account or modify retained Zopedia knowledge for this audit.

## Accepted locally

- Edit/save/reset passed in the local browser through the real API contract and a
  loopback account/store fixture. Production CSRF/origin checks remain intact.
- 37 focused Python plumbing checks, TypeScript checking, targeted lint, and eight
  existing browser-auth boundary checks passed. The optimized Next build passed
  after browser acceptance. These checks do not establish prose quality.
- Personally reviewed eight live Flash/Pro outputs. The shared rule now explicitly
  preserves requested length and format and avoids repeated conclusions. The
  comparison supports better prose discipline, not a universal model ranking.
- [Audit findings and proposed architecture work](../architecture/AQL/audits/LLM_AND_HARNESS_AUDIT_2026-10-07.md)
  record actual Flash resolution, context loss, review failure behavior,
  cancellation gaps, incomplete checkpoints, and gateway bypasses. No harness
  rewrite or model migration was included in this request's audit phase.
- Development-only style reset selects the new 80% default. No production setting
  or retained Zopedia knowledge was changed. Live model probes record telemetry only.

## Development release method

Use pinned existing API/job images with hash-verified source overlays. Job overlays
contain only the shared LLM adapter and gateway; unrelated current source is not
copied onto older job families. Build the frontend from its isolated tracked source
plus the new editor/route. Preserve every deployment setting except the intended
images and the API's five non-secret worker-routing defaults. Keep exact rollback
images and compare protected configuration after each update.

## Development acceptance

- API revision `sn-api-next-dev--0000014` and frontend revision
  `sn-next-ui-dev--0000011` are latest ready. The frontend receives 100% traffic.
- All eight non-broker development job templates use the new shared adapter and
  gateway. Their schedules, commands, identity, resources, secrets, and environment
  configuration were preserved. No full research/ingestion job was started for acceptance.
- In-container API inspection reads scope `development`, DeepSeek reasoning enabled,
  the active 80% default, 17 prompts, and 42 tuning parameters. The API has no model key.
- The signed-in development Admin page displays the editor. Saving the requested
  default language rule succeeds through the real authenticated BFF/API path and
  returns its saved confirmation. Local acceptance already checked custom/save/reset.
- Public health and Home return 200 (`ok` / `available`); unauthenticated Admin
  returns 401. Exact image/rollback/config receipts are in
  [the non-secret release manifest](../architecture/AQL/audits/DEV_RELEASE_2026-10-07.json).
- [Editor screenshot](../architecture/AQL/audits/DEV_LLM_SETTINGS_2026-10-07.jpg) and
  [language screenshot](../architecture/AQL/audits/DEV_LLM_LANGUAGE_2026-10-07.jpg)
  show the released user path. Production and retained knowledge remain unchanged.
