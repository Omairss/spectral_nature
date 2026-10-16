# Restore the last accepted Home release

**Production restoration proposal is on hold following the owner's blast-radius question.** The [read-only release audit](../verification/release-source-regressions-2026-10-16/README.md) confirmed regressions beyond Home. Restoring its worker alone would leave the API's key-chart regression, market-data safeguards, scheduled monitoring and Robinhood pacing unresolved. The owner has not approved a production change. The development restoration below is accurately scoped to one manual Home job; it is not a complete stack repair.

## Owner direction

Restore the previously working functionality. Do not redesign the editor, invent a new quality process or release the held reasoning/cost work. The last accepted Home worker is the October 14 release, not the obsolete Home baseline accidentally included in the October 16 reliability release.

## Confirmed regression

The October 16 reliability build started with an older Admin/API source copy and copied 157 files across consumers. That replaced the newer accepted Home builder and restored the sentence-cutting attention cleaner. File hashes verified the selected source copy; they did not establish preservation of accepted Home behavior. Source retirement prevents reuse of those loose copies but does not itself replace a runtime or a published artifact.

The accepted Home image remains available in ACR: `snpipelineacr03130136.azurecr.io/zopedia-conversation-dev@sha256:7d3cac3abeb3e7453db77333c419ccc68ee0fd7c1e661518d92fb8e5a1dc7bff`, created October 14 at 18:56 UTC, tag `20261014-r1-3`. Its Home builder and lossless text handling were verified during the regression investigation. Restore that immutable image as an explicit rollback target; do not reconstruct an application tree from archives or introduce new editorial behavior.

## Scope correction

I ran the canonical Home builder through the current checkpoint's unaccepted shared research engine, then began changing editorial inputs and review instructions after rejecting the result. That was scope drift: it was not a replay of the previously accepted release. The unrequested Home changes were reverted before further inference. The new model-routing changes were also reverted to preserve the stopped work exactly. No image was built or deployed from that experiment. Its three paid Flash calls, complete output, review and frozen evidence remain private under `~/Library/Application Support/SpectralNature/home-recovery-20261016/`; they do not establish the quality of the accepted October 14 image.

## Runtime boundary

Development may restore the exact accepted Home image, preserving the rest of the job template. Keep all other database-repair and Trading services unchanged. The stopped tasks' complete work remains retained; no research needs to be redone because of this restoration. The Home worker's database lifecycle patch can be reconciled later in Git without replacing its accepted Home behavior. Production restoration and regeneration require explicit owner permission. A job-image restoration and replacement of the already-published bad Home artifact are separate operations; report each accurately.

## Development restoration — complete

`attention-home-next-dev` now uses the exact accepted October 14 digest above; Azure provisioning reports `Succeeded`. Compared the complete job definitions: only the container image changed. Environment, resources, command/arguments, job configuration, manual trigger and identity are identical. The prior database-lifecycle image and complete job definition remain in the private rollback receipt. Its Home-specific lifecycle changes remain retained for the stopped database task; no other database or Trading service was changed.

The development public Home endpoint returned HTTP 200 in 1.12 seconds. Its existing October 14 publication and complete story fields are unchanged. No new research job was started and no generated experimental output was published. The exact image restoration is complete in development; this does not claim a fresh publication. Production remains unchanged. Explicit approval to restore the same image in production and regenerate Home has been requested under the owner's production rule.
