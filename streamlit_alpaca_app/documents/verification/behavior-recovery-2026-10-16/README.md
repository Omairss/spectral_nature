# Accepted-behavior recovery — October 16

Status: source restoration accepted locally for development verification. A fresh normal Home publication, cloud packaging verification and development rollout are still pending. Production has not changed and requires explicit approval after that verification.

The [audit](../release-source-regressions-2026-10-16/README.md) owns the eight regressions. The [recovery plan](../../plans/RELEASE_BEHAVIOR_RECOVERY_2026-10-16.md) owns the work. This record does not accept the held reasoning/model-routing or Trading-call experiments.

## Reconciled behavior

The recovery branch starts from the checked-out Git source and removes the held changes, preserving them in `held/pre-recovery-20261016` and the verified stopped-task archives. Historical evidence was read without extracting an application tree or creating a build overlay.

The [source comparisons](source-fidelity.json) establish 56 exact comparisons, one reviewed execution-budget difference and the subsequently authorized inactive Trading retirement across the restored components and preserved database work:

- The Home editorial functions, full-intake query, shared answer-review request and lossless text boundary match the accepted October 14 implementation. Its remaining outer differences propagate cancellation/context and log intraday collection; they do not redesign its prompts or selection. The LLM transport and gateway match that accepted implementation. The accepted source-navigation/checkpoint changes remain.
- Independent chart selection and selection timestamps match the actual preceding modern API. The cold read below returns five retained charts without requiring their Home run to match.
- The stock-bars method matches the accepted Home wrapper, including `adjustment`. Market-data preparation, row-position partitioning, equities processing and observation-date loading match the accepted equities producer.
- Source-page indexing and maintenance transaction boundaries match the preceding general worker. Broker cadence/bucket calculation and duplicate exclusion match the preceding dedicated broker. Monitoring reconciliation, environment scope and wider failure checks match the preceding health worker. Home completion again propagates degraded research as Warning.
- The accepted Trading producer initially matched the preceding accepted image byte-for-byte. Subsequent owner-directed cleanup removes only its inactive per-horizon rollback entrypoints; the shared-research producer and its research/checkpoint/memory behavior remain unchanged. Held directional-call/paper-ledger changes are excluded from the source selected for recovery.

## Preserved database repair

Development already has the locally accepted [database-lifecycle repair](../database-lifecycle-2026-10-16/README.md). This recovery preserves it rather than replacing it with an older worker's database owner. The database owner and cancellation-scope modules match their accepted digests; 30 scoped functions match exactly. Home's timeout wrapper also rejects a nonpositive budget and uses an equivalent positive deadline/context expression, while retaining cancellation.

This includes global database admission, cancellation, Blob-first publication reads, monotonic publication, incremental source projection and independent backlog recovery. Those changes are already deployed in development, **not production**. Any later proposal to promote these recovered development images must explicitly include that distinction; it is not a pure immutable October 14 image rollback. No database role, paid tier, credentials, schedules or allocation is changed by this recovery work.

## Local observations

- [Real intraday collection](intraday-live-contract.json): SPY and MSFT each returned 78 five-minute regular-session points, dated October 16, with October 15 prior closes. The provider call completed in 5.86 seconds. This exercises the actual restored wrapper/caller pair, not a mock accepting arbitrary keywords.
- [Cold published Home read](local-home-cold-blob-only.json): the normal shared Home view returned its retained overview, eight stories and five charts with zero SQL attempts. It took 15.29 seconds from the local machine including Azure credential/network/cold data reads; this is an independence check, not a claim of cloud request latency.
- Scoped execution/storage checks passed: 25 passed with eight real-database cases not rerun; a second scope covering provenance, Admin isolation, supplied evidence and existing Trading views passed 26. A follow-up signature/projection scope passed 12 with four real-database cases not rerun. The previous owned-database/Blob evidence remains in the accepted lifecycle record. These checks do not grade language.
- The frozen Home editor replay used all 300 retained source records and 96 retained bundles, unchanged prompts and the original configured provider alias. Its two real provider attempts reported `deepseek-flash`, used 308,835 input / 46,549 output tokens and cost an estimated $0.07413. It completed in 216.8 seconds, with real answer review and an overview. Personally read the complete output: it restores substantive explanations, but inherited bad drafts still led to unsupported follow-up claims and awkward handling of contradictory Iran reporting. The replay was **not published**. This is not permission to redesign the accepted editor or release the held reasoning candidate. Verify a fresh development publication from the restored normal producer before presenting recovery as complete.

Private complete replay inputs/output, attempts, source comparisons and operator definitions are under `~/Library/Application Support/SpectralNature/behavior-recovery-20261016/`. No research regeneration, broker order or production write occurred during those local checks.

## Release enforcement

`scripts/release_source.py` requires the committed acceptance record, complete application inventory, reviewed changed files, component evidence and each role's actual consumer baselines. A clean checkpoint without acceptance, an added producer, changed source and hashes updated without changed-file review are rejected. Target-role checks also prevent a Streamlit image replacing a resource that now hosts the modern web frontend. These are packaging/operational contracts, not language rules.

Existing-image promotion requires a verified digest recorded for that role and the exact accepted source inventory, through the supported release entrypoint. Historical tags or digests do not become eligible because their name sounds accepted. The initial packaging batch preceded this final promotion-path check and is superseded before any deployment; only the completed, verified batch will be registered for release.

`Dockerfile.runtime` retains pinned installed dependencies, removes the inherited `/app` and copies the complete declared Python runtime from the accepted checkout. Broker packaging now does the same. Packaging tools/frontend source remain in the full source inventory; Python images have a separate explicit runtime file inventory. Inspection of the initial batches found that root-level cache exclusions did not remove nested compiled files, and ACR injected a generated Dockerfile into broad COPY contexts. Recursive exclusions and explicit runtime COPY paths correct those differences before deployment. The obsolete image-backport utility and previous broker overlay were verified into the 30-day archive batch `20261016T230849Z`. No application source is copied from an older image during the build.

`scripts/release_application.py` builds accepted source and performs development image-template updates only. It checks the reviewed prior target image/configuration, saves before/after definitions and verifies that only the image changed. It does not start jobs, change settings or expose a production-deployment command. Immutable-image production promotion remains a separately authorized action.

The recovery targets the affected modern API, shared workers and broker: 13 development targets. Streamlit is retired and excluded. The legacy API and existing modern web frontends retain their current images; they do not need a blind application replacement to restore the eight audited paths. Their existing source and runtime records remain retained.

The owner-directed [legacy retirement](../legacy-retirement-2026-10-16/README.md) removes obsolete frontend/deployment paths and records separate development service retirement. The worker build also hit the inherited base image layer-depth limit. No superseded image has been deployed; resolving packaging and accepting a fresh normal Home publication remain required.
