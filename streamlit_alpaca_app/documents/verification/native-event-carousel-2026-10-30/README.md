# Native event carousel review — October 30, 2026

Local SwiftUI interaction review in the Spectral Nature app. Source is an isolated Git worktree based on `9e621e93`; no production servers, schedules, providers or physical phones change. The regular `/api/home` decoder accepts an optional calendar. Older Home responses retain their existing behavior.

The loopback fixture contains the previously authored calendar/feed and the actual retained October 29 attention graph. It is not live calendar ingestion. Calendar/provider qualification remains in the source plan. The fixture server exposes only public Home and an unauthenticated session response; it has no credentials or trade endpoints. Use the app’s existing DEBUG-only `SN_SERVER_URL` override to select it. The fixture is outside application runtime and is not embedded in the app.

The native carousel shows five centered date positions, stacked ticker labels, continuous center-out scale/fade and no circles. A tap expands all event rows inside that carousel. A ticker tap unfolds its context below; collapse restores the compact height. Scrolling changes focus without opening a sheet or navigation destination. The graph moves down as content expands. Its compact and full presentations share the existing native node/edge/marker parser; original full-graph interactions remain available.

The accepted application inventory remains unchanged because this is scoped to native SwiftUI source and verification documents. The committed source gate must pass before building. Native source changes are separately reviewed; that gate’s backend role does not imply a new server release or qualitative acceptance of this UI.

## Verified native review

The signed Debug build succeeded without compiler warnings or errors from committed source. [Local receipt](LOCAL.json) records the source gate, complete native input hashes, bundle identity, executable hash and simulator readback. The app was launched using its existing DEBUG loopback override, with no production account or broker credentials.

Personally reviewed the reference date centered with two dates on each side, vertical event stacks, progressive scale/fade, and absence of calendar circles. A peripheral Wednesday date tap centered that date and expanded six earnings in the same screen. Microsoft’s summary, context, facts and instrument-specific reaction unfolded below the carousel. Collapse restored the compact height and selected date; the attention graph moved with the content. The final preview is left expanded on October 29.

[Collapsed](native-collapsed.png) and [expanded](native-expanded.png) show the actual native app. The computer-use drag in Simulator behaved as a tap, so this automation did not qualify physical touch-pan behavior. Native UIScrollView scrolling and snap logic remain in the component; do not describe the gesture as verified from these taps.

The initial native scroll-position implementation exposed mismatched centering and a dropped request while height changed. The component now measures one named viewport and preserves requested focus through actual geometry changes. It uses no timed delay or device-specific offset. The source gate’s accepted backend inventory remains unchanged; native UI quality is separately reviewed and awaits the owner’s assessment.
