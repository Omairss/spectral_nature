# Application release source

Effective October 16, 2026, following the Home source rollback.

Every new application release must use one identified, accepted Git revision. Integrate accepted work in Git before packaging it. Build the application files for API, workers and frontend from that revision. Record the revision and immutable image digest together. A commit records provenance; experimental work still needs its own qualitative acceptance.

Older images and extracted source snapshots are historical evidence and explicit rollback targets. They are not sources for new application builds. Do not copy application files from another consumer's image, a temporary overlay, an archive or an old task directory. A label such as `accepted-admin-preview` certifies no unrelated application behavior. Base images may provide pinned dependencies; their old application code must not become the new source tree.

The supported release entrypoint rejects execution outside the actual Git application checkout. New builds require a clean, committed application tree; status reads and immutable-image promotion remain separate operations. Docker contexts exclude temporary trees and archives. Keep current release preparation in tracked scripts. Historical deployment scripts are retired with their source trees; do not recreate missing historical paths or point symlinks at archives to make them run again.

## Source acceptance before packaging

`scripts/release_source.py` is mandatory before an application build. The committed `infra/release_acceptance.json` identifies the reviewed source revision, complete application-file inventory, every changed runtime file, component verification and the immutable previous images for each consumer. An edit outside the recorded inventory, a new file, a deletion or an unreviewed changed file rejects packaging. This inventory covers all application code and packaging, not only the shared research-engine fingerprint. Updating an inventory without reviewing preserved behavior is not acceptance.

Existing-image promotions require a role-specific verified digest recorded against the same accepted source inventory. The checker reads the committed acceptance record, so editing an uncommitted allowlist cannot authorize an image. Resource names must match their recorded application role; the production resource named `sn-streamlit-ui` now hosts the modern web frontend and cannot receive a Streamlit promotion. A historical rollback remains a separate explicitly authorized image-template operation with before/after records; it is not an exception silently supplied to normal promotion commands.

The recovery reconciles the checked-out Git implementation against read-only historical evidence and reverts held changes in Git. Historical evidence must never become an extracted application tree, an image overlay or a build context. `Dockerfile.runtime` may inherit a role's pinned installed dependencies, but first removes `/app` completely and copies the declared Python runtime directories and configuration from the accepted checkout. Build files and deployment tools remain in the complete source inventory; the separate runtime inventory defines exactly what may be in the application image. Verify every packaged file against Git; an OCI revision label alone does not establish the packaged content. Recursive exclusions remove compiled caches, and explicit COPY paths exclude build-service generated files.

Streamlit and Dash are retired. Their code, UI dependencies/tests, launchers, old API/UI/pipeline deployers, filename deployment classifier and completed migration/cutover tools are archived outside the checkout. The product frontend is Next.js; the Python runtime roles are modern API, workers and broker. The production resource named `sn-streamlit-ui` is a Next.js ingress alias. It must never receive a Streamlit image. See the [retired-path inventory](../verification/legacy-retirement-2026-10-16/README.md).

The retired image-backport utility and broker's two-file overlay are archived. Broker packaging now replaces the complete application source too. Held reasoning and Trading-call implementations remain in their held Git checkpoint and retained task archives; they are excluded from the recovery branch.

## Retirement and retention

Completed release trees are compressed outside the repository under `~/Library/Application Support/SpectralNature/retired-release-sources/`. Private batches have an original-path index, content verification and archive hashes. No extracted application tree remains at the old path. Read historical evidence directly from an archive; do not use it as a build context.

Retain each completed batch for 30 days. `python3 scripts/cleanup_retired_releases.py` removes only completed, expired batches in this dedicated archive directory. A local macOS LaunchAgent runs an installed copy from `~/Library/Application Support/SpectralNature/archive-maintenance/` daily and at login, keeping background maintenance outside macOS-protected Documents paths. Keep that copy identical to the tracked script when updating maintenance. Pending or incomplete batches are retained for investigation. The [retirement record](../verification/release-source-retirement-2026-10-16/README.md) identifies the batches and resume material.

Active database and Trading work must preserve its scoped changes while reconciling application source in Git. Existing copies that include the obsolete Home editor are not eligible for a new release. Do not promote those copies as a side effect of an operational repair. Their runtime repair, Home quality acceptance and production authorization remain separate work.
