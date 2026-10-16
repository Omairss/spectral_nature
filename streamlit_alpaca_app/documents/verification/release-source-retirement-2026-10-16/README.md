# Release-source retirement — October 16, 2026

Status: complete. The owner authorized retiring historical release sources, then confirmed the database and Trading tasks were stopped. This task made no application deployment, Azure configuration change, new model call or research-data mutation.

## Archives

Moved **37 historical task/source directories containing 57,313 regular files** into verified compressed archives outside the checkout. Archive storage is `/Users/omairs/Library/Application Support/SpectralNature/retired-release-sources` (2,126,662,889 compressed bytes). Both batches and their contents are private to the local owner. Each manifest maps original paths to archives, records per-file content hashes and compressed archive hashes, and records completion and expiry. Every source was checked against its archive and checked again for concurrent modification before its original directory was removed.

| Batch | Archives | Files | First eligible discard date |
| --- | ---: | ---: | --- |
| `20261016T204950Z` | 31 | 53,706 | November 15, 2026 |
| `20261016T205249Z` | 6 | 3,607 | November 15, 2026 |

The old repository `streamlit_alpaca_app/tmp/` directory, the completed reliability trees and overlays, and the stopped database/Trading source trees no longer exist at their original paths. No compatibility symlinks were left. No loose duplicate Home producer or attention cleaner remains in the checkout or `/tmp/sn-*` task directories. The canonical current Home source has neither the obsolete editor's evidence rules nor the sentence-deleting cleaner.

Historical receipts keep their original paths as provenance. Resolve those paths through the batch manifests rather than recreating old directories. Read individual historical files from a tar archive without restoring an executable application tree. Full historical deployment recipes are retained only inside those archives.

## Resume the stopped work

Completed implementations, original source baselines, paid research, full candidate outputs, provider records, numerical data and verification/deployment receipts remain retained. Archival does not require restarting either task.

- **Database repair:** batch `20261016T205249Z`, `sn-db-repair-20261016.tar.gz`. Read `sn-db-repair-20261016/RESUME_AFTER_RETIREMENT.json`, `source.json` and `retirement-resume/database-scoped-changes.patch`. The patch retains the eleven owned files against the prior baseline; the metadata identifies function ownership and excludes unrelated candidates. At retirement, 29 owned function/class definitions already matched the current checkout and 2 required reconciliation. The deployment and verification records are retained alongside the source. Resume the scoped changes in Git and repeat checks affected by integration; do not rerun completed research or restore the full old baseline.
- **Trading:** the same batch, `sn-trading-calls-20261016.tar.gz`. Read `sn-trading-calls-20261016/RESUME_AFTER_RETIREMENT.json` and `retirement-resume/`. Eight candidate diffs are preserved with all source variants and complete evaluation outputs. The archived `sn-trading-memory-20261015` and `sn-production-build8-20261016` directories preserve their referenced original evidence and receipts. Read the complete outputs to select the next candidate; retaining a candidate does not grant acceptance. Reuse completed acquisition and research. Changed prompts or reasoning may still require focused new inference.

Current plans: [database lifecycle](../../plans/DATABASE_WORK_LIFECYCLE_2026-10-16.md), [Trading calls](../../plans/TRADING_CALLS_AND_APPROVAL_TRACKING_2026-10-16.md). Their old source-overlay instructions have been replaced by the [release-source policy](../../infra/RELEASE_SOURCE_POLICY.md).

## Release boundary and verification

Root `AGENTS.md` and philosophy now require accepted application source to be reconciled in Git. The API, UI and pipeline deployment entrypoints reject loose source copies outside the Git application checkout. New builds require committed, clean application source. Docker contexts exclude `tmp/`, `archive/` and `archives/`. A Git checkpoint records source identity; the held reasoning/cost candidate remains unaccepted.

All three deployment scripts passed shell syntax checks and refused execution from detached copies before environment loading or Azure calls. API/UI build functions also refused the dirty checkout before registry access. Each archive's regular file contents and symlinks were verified before removal; all 37 original directories were checked absent afterward. The filesystem scan found only the canonical Home producer and attention cleaner. No build or deployment was started for these checks.

## Automatic retention

`com.spectral-nature.retired-release-cleanup` is installed as a user LaunchAgent, running at login and daily at 03:15 local time. It runs the identical installed copy of [cleanup_retired_releases.py](../../../scripts/cleanup_retired_releases.py) from `~/Library/Application Support/SpectralNature/archive-maintenance/`. Only completed batches past the full 30-day retention can be discarded. Recent, incomplete, malformed and symlink entries are preserved; shortened expiry metadata cannot shorten retention.

The first scheduled run could not open the repository script under macOS-protected Documents. Moved maintenance and the archives into Application Support and verified the actual scheduled job afterward. The initial error remains in its local log. Fixture checks verified expired removal and preservation of recent, incomplete, shortened-expiry and symlink cases.
