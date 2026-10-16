# Spectral Nature for iPhone

Native SwiftUI client for the modern Spectral Nature workbench. Requires iOS 17 or later and Xcode 16 or later. The historical `SpectralNatureMVP` directory is retained; the application and shared scheme are named `SpectralNature`.

## Run

Open `SpectralNature.xcodeproj`, select the `SpectralNature` scheme and an iPhone simulator, and press Run. The project is included; XcodeGen is only needed when changing `project.yml`.

The app connects to the live modern workbench:

`https://spectral-nature.com`

Daily Brief is public. Use your existing Spectral Nature account for Markets, Research, Portfolio, and the other private workspaces. No local Python or Node server is required.

For a physical iPhone, connect and unlock the phone, accept Trust This Computer, and select that physical phone as Xcode's run destination before configuring your team under Signing & Capabilities. Keep automatic signing enabled, then press Command-R. If signing says the team has no devices, check that the physical phone is selected and click Try Again (or Register if offered); automatic signing registers the connected device. A simulator cannot supply the device registration for an iPhone development profile. See [Apple's device signing instructions](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices).

TestFlight distribution additionally needs your Apple Developer membership and App Store Connect configuration. Build 7 is installed directly on the owner's iPhone; TestFlight/App Store distribution is not configured.

## Native surfaces

- Brief: expandable report, retained audio, replay date, attention stories, a combined preceding-week/latest-session price chart, compact bank/insurance fundamentals and fiscal-quarter histories, and attention network. Longer price/financial analysis remains in Investigator.
- Markets: business/broad/commodity modes, server lenses/horizons, full-universe return comparisons with percent axes and daily-change color legends, point identity/search/inspection, named return leaders/laggards, volume treemap, optional 3D/heatmap comparisons, ranking, and company navigation.
- Research: durable conversations, ordered progress journal, cancellation, follow-up, result metadata, citations, knowledge folders/search/connected pages, retained sources, source upload, and administrative maintenance inspection.
- Portfolio: all holdings, asset exposure, position treemap and P/L contributions, value and indexed benchmarks, drawdown, monthly return matrix, risk/correlation/factors, performance statistics, indexed list comparisons, risk/return/breadth analysis, and return/risk/momentum contributor Sankeys.
- Explore: macro overlays/readings/searchable series catalog, company financials/technicals, complete option chains/candidate scenarios and native IV/return surfaces, Trading Agent candidates/decisions, and role-gated admin workspaces.
- Account: sign-in, recovery requests, invitation acceptance and password reset through existing account links. Paste an environment-matching email link in the native account sheet. Universal email links require additional Apple/domain configuration.

Research is accessible from the analytical navigation bar with the current entity, date, period, or artifact context. Model calls and financial rules remain in the existing backend. No web view, JavaScript renderer, or new inference provider is embedded in the app.

Ticker headings preserve price cents and keep prices separate from market/position values. Investigator retains its quote across analysis tabs. Linked-portfolio failures clear the displayed snapshot, and failed knowledge refreshes cannot leave old search results visible. Admin supports model, language-rule, prompt and parameter editing when the connected server publishes the corresponding settings contract; saving uses the same role-gated CSRF-protected endpoint as the web client.

## Transport and development

`Networking/APIClient.swift` uses the same `/api/` gateway as the modern web client, including Origin and CSRF verification. Its private ephemeral cookie jar is persisted in a device-only Keychain item. Authenticated requests are serialized so refresh-token rotation cannot race; redirects are rejected. Sign-out clears private view state and local credentials. Transient service outages preserve a valid session.

Debug and Release use the live gateway by default. For isolated development, set `SN_SERVER_URL=https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io` in the Debug scheme environment. A local server must expose the same Next.js `/api/` routes, not the older direct `/v1/` API. Sessions and account links stay scoped to the selected origin; switching from build 2's development gateway requires signing in again with the existing account. No browser or development cookies are transferred.

Regenerate project metadata after changing the project specification:

```bash
xcodegen generate
```

On this machine, full Xcode is installed but the global developer selection points to Command Line Tools. Use a command-scoped selection for CLI builds:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project SpectralNature.xcodeproj -scheme SpectralNature \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -derivedDataPath /tmp/spectral-nature-ios build
```

Keep normal simulator signing enabled: Keychain needs Xcode's simulated application identity. Disabling signing can produce a launchable app whose session storage fails. The project specification now retains the owner's configured development team so regeneration preserves signing.

## Validation and remaining work

Build 8 (October 15) is prepared and signed for simulator and the physical iPhone.
It adds peripheral scatter direction/volume encodings, softer text, full Brief with
network access, dated Highlights, session-owned five-second research progress and
in-app completion notices, consolidated Research navigation, collapsed Trading
cards, and request threads with follow-ups. Zero volume is valid; missing direction
or volume has an explicit legend. The new API/gateway/Trading images are deployed
to dev with all protected configuration unchanged. Production and the phone remain
on the accepted prior release until approval. The normal signed-in native dev request flow, full999 Markets view and real
research completion passed. Full Trading generation hit an exit137 failure; its retry was stopped, and
worker promotion is held pending a deeper memory/reliability repair.

The confirmed free Personal Team cannot enable remote push. This build checks
research while foregrounded and resumes checks on return; notification delivery
while locked/closed remains blocked on Developer Program enrollment and APNs setup.
See the [refinement plan](../../documents/plans/NATIVE_WORKBENCH_REFINEMENT_2026-10-15.md)
and [build 8 receipt](../../documents/verification/native-workbench-2026-10-15/release.json).

The following describes the previously accepted installed release:


Build 7 passes signed simulator and physical-device Debug compilation and signature
verification. The complete native simulator app restored the normal production
session, rendered all 999 market instruments, and showed the final return bars with
separate ticker/value columns. Point selection includes the correct company, quote
and both returns; return rows open the matching Investigator. The compact iPhone SE
review covers the shared scatter and color legend. Build 5's accepted Attention,
fundamentals, conversation and main Portfolio source hashes remain unchanged.

CoreDevice confirms version 1.0.0 build 7 is installed on the owner's iPhone. After
owner unlock, launch succeeded and the app remained running as PID 30309. The
earlier locked-device rejection is resolved. No uninstall or Keychain reset
occurred. Production server/web images and
settings are unchanged. Commodity symbols still lack history in the retained source
snapshot; no provider backfill was added. See the [Markets acceptance record](../../documents/plans/NATIVE_MARKETS_CHARTS_2026-10-14.md)
and [build 7 receipt](../../documents/verification/native-analytics-2026-10-14/build7/release.json).

The earlier live signed-in review covers the principal read workflows, including the full 999-symbol market universe and option scenario/surface values. Public Brief, sign-in and recovery layouts were reviewed on iPhone SE. Credential-changing actions, source uploads, new research submissions/cancellation, and admin/Trading Agent mutations still need owner acceptance. The separate server release must expose LLM settings metadata before native editing appears. Retained development option data limits multi-maturity surface review. See [the implementation and acceptance record](../../documents/plans/NATIVE_IPHONE_WORKBENCH_2026-10-06.md) for exact evidence. TestFlight/App Store setup remains pending.
