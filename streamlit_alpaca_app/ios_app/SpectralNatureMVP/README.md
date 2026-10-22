# Spectral Nature for iPhone

Native SwiftUI client for the modern Spectral Nature workbench. Requires iOS 17 or later and Xcode 16 or later. The historical `SpectralNatureMVP` directory is retained; the application and shared scheme are named `SpectralNature`.

## Run

Open `SpectralNature.xcodeproj`, select the `SpectralNature` scheme and an iPhone simulator, and press Run. The project is included; XcodeGen is only needed when changing `project.yml`.

The app connects to the live modern workbench:

`https://spectral-nature.com`

Daily Brief is public. Use your existing Spectral Nature account for Markets, Research, Portfolio, and the other private workspaces. No local Python or Node server is required.

For a physical iPhone, connect and unlock the phone, accept Trust This Computer, and select that physical phone as Xcode's run destination before configuring your team under Signing & Capabilities. Keep automatic signing enabled, then press Command-R. If signing says the team has no devices, check that the physical phone is selected and click Try Again (or Register if offered); automatic signing registers the connected device. A simulator cannot supply the device registration for an iPhone development profile. See [Apple's device signing instructions](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices).

TestFlight distribution additionally needs your Apple Developer membership and App Store Connect configuration. The app is installed directly on the owner's iPhone; TestFlight/App Store distribution is not configured.

## Native surfaces

- Brief: full report, retained audio, replay date, attention stories, a combined preceding-week/latest-session price chart, compact bank/insurance fundamentals and fiscal-quarter histories, and attention network. Longer price/financial analysis remains in Investigator.
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

## Installation and validation

Build 9 is prepared for the Paper performance route repair and automatic live Brief
refresh. It passed populated simulator checks through the normal authenticated DEV
gateway. Production promotion and physical installation are pending; build 8 remains
on the phone. [Current work](../../documents/plans/CLIENT_REFRESH_2026-10-22.md).

Version 1.0.0 build 8 was renewed, installed in place and launched on the owner's
physical iPhone on October 22. The new Personal Team profile expires October 29,
2026 at 9:52 a.m. Pacific. The same bundle identifier and development team preserve
application data and Keychain storage. Device readback confirms the running app.
See the [renewal receipt](../../documents/verification/native-renewal-2026-10-22/release.json).

Free Personal Team profiles expire seven days after issuance, independently of
backend updates. To renew, connect and unlock the phone, build the accepted Git
source with automatic signing and provisioning updates enabled, install over the
existing app, and verify launch. iOS may require renewed developer trust under
Settings → General → VPN & Device Management. Host signature verification does not
establish that the phone has granted trust. See [Apple's account limits](https://developer.apple.com/help/account/basics/about-your-developer-account).

The original [build 8 acceptance](../../documents/verification/native-workbench-2026-10-15/release.json)
records the October 16 production promotion and installation. The current native
source also includes the owner-approved October 18 paper-call presentation and
dated performance axis; its prior simulator review is recorded in the
[Trading release](../../documents/verification/trading-calls-2026-10-18/PRODUCTION.md).
The October 22 renewal changes signing, not application logic or production servers.

Build 8 includes peripheral scatter direction and volume, softer text, full Brief
with network access, dated Highlights, five-second foreground research progress
and completion notices, consolidated Research navigation, collapsed Trading cards,
and request threads with follow-ups. The signed-in native development request flow,
999-instrument Markets view and research completion were verified before release.
Earlier chart and compact-device evidence remains in the
[build 7 receipt](../../documents/verification/native-analytics-2026-10-14/build7/release.json).

The free Personal Team cannot enable remote push. Research checks resume when the
app returns to the foreground; locked or closed delivery requires Developer Program
enrollment and APNs setup. TestFlight/App Store distribution remains unconfigured.
