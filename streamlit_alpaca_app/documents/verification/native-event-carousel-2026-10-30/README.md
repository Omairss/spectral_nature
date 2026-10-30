# Native event carousel review — October 30, 2026

Local SwiftUI interaction review in the Spectral Nature app. Source is an isolated Git worktree based on `9e621e93`; no production servers, schedules, providers or physical phones change. The regular `/api/home` decoder accepts an optional calendar. Older Home responses retain their existing behavior.

The loopback fixture contains the previously authored calendar/feed and the actual retained October 29 attention graph. It is not live calendar ingestion. Calendar/provider qualification remains in the source plan. The fixture server exposes only public Home and an unauthenticated session response; it has no credentials or trade endpoints. Use the app’s existing DEBUG-only `SN_SERVER_URL` override to select it. The fixture is outside application runtime and is not embedded in the app.

The native carousel shows five centered date positions, stacked ticker labels, continuous center-out scale/fade and no circles. A tap expands all event rows inside that carousel. A ticker tap unfolds its context below; collapse restores the compact height. Scrolling changes focus without opening a sheet or navigation destination. The graph moves down as content expands. Its compact and full presentations share the existing native node/edge/marker parser; original full-graph interactions remain available.

The accepted application inventory remains unchanged because this is scoped to native SwiftUI source and verification documents. The committed source gate must pass before building. Native source changes are separately reviewed; that gate’s backend role does not imply a new server release or qualitative acceptance of this UI.
