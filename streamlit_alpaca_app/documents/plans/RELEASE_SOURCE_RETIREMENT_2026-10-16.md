# Retire superseded release sources

Status: complete. Archived 37 historical task/source directories, verified 57,313 files, removed the original paths without symlinks, and verified daily 30-day expiry maintenance. The owner stopped the database and Trading tasks; their full work, scoped patches and resume metadata are retained. No application deployment or restoration of retired application behavior was performed.

The October 16 combined release began with an Admin/API source copy, then copied 157 files across workers. This restored the older Home editor and destructive attention cleaner. Hash checks verified the chosen copy, but did not preserve the newer accepted producer. The repository has since been checkpointed by a separate cleanup; that checkpoint still does not accept the held reasoning candidate.

## Work

1. Inventory completed release copies and inspect active database/Trading dependencies before moving anything.
2. Archive completed historical task directories and the repository temporary release directory outside the checkout. Verify retained file contents and original paths before removing each source tree. Leave no symlinks at old source paths.
3. Exclude temporary trees from Docker contexts, establish the repository release-source instructions, and schedule deletion of completed archives after 30 days.
4. The owner confirmed both concurrent tasks were stopped. Preserve their full evidence and scoped work in the second archive batch, with explicit resume metadata. Their obsolete Home copies cannot be treated as accepted source for a future release.
5. Record the archive inventory, verification, active exceptions and any remaining work in the [retirement receipt](../verification/release-source-retirement-2026-10-16/README.md). Update mistakes and learnings.

The archive operation changes local source organization and release instructions. It does not repair the currently deployed Home artifact or change live images, settings, jobs, model routes or research data.
