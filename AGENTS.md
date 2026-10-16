# Repository working rules

Read `streamlit_alpaca_app/documents/philosophy.md` before making changes.
Read `streamlit_alpaca_app/documents/infra/RELEASE_SOURCE_POLICY.md` before preparing any release.

The checked-out Git source is the application source. Reconcile accepted changes in Git before preparing a release. A repository checkpoint does not grant qualitative acceptance to experimental work.

Every application build must pass `scripts/release_source.py` against the committed `infra/release_acceptance.json`. Review each changed runtime file and each consumer's previous accepted behavior, including producers and adapters outside the research-engine fingerprint. A clean Git tree, copied-file hashes and successful imports do not establish that review. Build inherited dependencies only after removing the inherited application tree; no historical application file may survive into a new runtime.

Historical images, extracted application trees, temporary overlays and retired archives are evidence or explicit rollback records. Never assemble a new application release from them. Do not restore retired source paths or add compatibility symlinks to archived trees. Dependencies may come from a pinned base image; application code must come from the selected, accepted Git revision.

Keep experiments separate from accepted release source. Archive superseded release work outside the repository, retain it for 30 days, and use the archive cleanup command documented in the release policy. Preserve active work in other sessions; record unresolved source integration before any promotion.

Do not deploy until the requested behavior is accepted locally. Deploy authorized application changes to development; production requires explicit owner permission. Archiving and local release maintenance do not require an application deployment.
