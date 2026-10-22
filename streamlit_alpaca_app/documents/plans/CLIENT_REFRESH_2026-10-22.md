# Paper performance and publication refresh — October 22

The owner requests feedback items 1 and 2: repair native Paper performance detail
and refresh the open iPhone/web Brief when retained data changes. Work continues
in the accepted Git worktree; concurrent research/cleanup changes in the original
checkout must remain intact. Production promotion requires separate owner approval.

## Design

- Native uses the existing authenticated `/api/trading-agent/approved-calls/{id}`
  gateway. Its backend `/v1/trading-agent/calls/{id}` route remains unchanged.
- The server-rendered web page and existing Home gateway share one retained-response
  loader and a 15-second server cache. A representation ETag covers the complete
  Home DTO, including independent ticker and Highlight publications. Conditional
  reads return 304 without sending or decoding the unchanged chart payload.
- Both clients check every 30 seconds while the live Brief is visible and on
  foreground return; web also checks on focus/network return. Stop background timers, cancel obsolete work,
  prevent overlapping reads, and preserve the last displayed publication on failure.
  Replay never follows the live feed.
- Update content in place with stable identities. Keep active audio uninterrupted;
  retain the newest pending publication until playback pauses or finishes. An audio
  item changes only when its narrative publication changes, not on a price refresh.
- No research, provider data fetch, generated prose, model/prompt change or new
  notification mechanism is introduced by screen refresh.

## Acceptance work

Review changed source, then exercise real HTTP conditional responses and complete
native/web rendering against controlled retained snapshots: unchanged publication,
new narrative, price-only and Highlight-only changes, active playback, failures,
foreground return, tab/navigation changes and replay. Exercise a populated native
paper-performance detail through the gateway and existing authenticated DEV data.
Compilation alone does not establish that the 404 is fixed. After local acceptance,
prepare the committed source record, verify builds and deploy the scoped web change
to DEV. Prepare the native build for the compatible gateway without changing the
phone's production default, bundle identity or saved session.

Status: local/native acceptance complete and signed build 9 prepared. The first
DEV web candidate passed conditional transport checks; a timestamp hydration fix
is undergoing replacement-package verification. Production and the physical
iPhone remain unchanged. See [verification](../verification/client-refresh-2026-10-22/LOCAL.md).
