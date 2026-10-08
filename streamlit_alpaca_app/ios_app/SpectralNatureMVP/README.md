# Spectral Nature for iPhone

Native SwiftUI client for the modern Spectral Nature workbench. Requires iOS 17 or later and Xcode 16 or later. The historical `SpectralNatureMVP` directory is retained; the application and shared scheme are named `SpectralNature`.

## Run

Open `SpectralNature.xcodeproj`, select the `SpectralNature` scheme and an iPhone simulator, and press Run. The project is included; XcodeGen is only needed when changing `project.yml`.

The app connects to the existing isolated development service:

`https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`

Daily Brief is public. Use your existing Spectral Nature account for Markets, Research, Portfolio, and the other private workspaces. No local Python or Node server is required.

For a physical iPhone, select your team under Signing & Capabilities, connect the phone, select it as the run destination, and run from Xcode. TestFlight distribution additionally needs your Apple Developer membership and App Store Connect configuration; neither distribution nor production promotion has been performed.

## Native surfaces

- Brief: expandable report, retained audio, replay date, five native trend charts, attention stories, inline ticker fundamentals, and attention network.
- Markets: business/broad/commodity modes, server lenses/horizons, volume treemap, momentum scatter, 3D return topology, full-universe heatmap, ranking, search, and company navigation.
- Research: durable conversations, ordered progress journal, cancellation, follow-up, result metadata, citations, knowledge folders/search/connected pages, retained sources, source upload, and administrative maintenance inspection.
- Portfolio: all holdings, asset exposure, position treemap and P/L contributions, value and indexed benchmarks, drawdown, monthly return matrix, risk/correlation/factors, performance statistics, and lists.
- Explore: macro overlays/readings/searchable series catalog, company financials/technicals, complete option chains/candidate scenarios and native IV/return surfaces, Trading Agent candidates/decisions, and role-gated admin workspaces.
- Account: sign-in, recovery requests, invitation acceptance and password reset through existing account links. Paste an environment-matching email link in the native account sheet. Universal email links require additional Apple/domain configuration.

Research is accessible from the analytical navigation bar with the current entity, date, period, or artifact context. Model calls and financial rules remain in the existing backend. No web view, JavaScript renderer, or new inference provider is embedded in the app.

## Transport and development

`Networking/APIClient.swift` uses the same `/api/` gateway as the modern web client, including Origin and CSRF verification. Its private ephemeral cookie jar is persisted in a device-only Keychain item. Authenticated requests are serialized so refresh-token rotation cannot race; redirects are rejected. Sign-out clears private view state and local credentials. Transient service outages preserve a valid session.

Release deliberately also targets development until production is explicitly approved. A Debug scheme can use `SN_SERVER_URL` for a local or HTTPS development gateway. The local server must expose the same Next.js `/api/` routes, not the older direct `/v1/` API.

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

Keep normal simulator signing enabled: Keychain needs Xcode's simulated application identity. Disabling signing can produce a launchable app whose session storage fails.

## Validation and remaining work

Simulator and generic physical-device Release compilation pass. Live signed-in review covers the principal read workflows, including the full 999-symbol market universe and option scenario/surface values. Public Brief, sign-in and recovery layouts were reviewed on iPhone SE. Credential-changing actions, source uploads, new research submissions/cancellation, and admin/Trading Agent mutations still need owner acceptance. Retained development option data limits multi-maturity surface review. See [the implementation and acceptance record](../../documents/plans/NATIVE_IPHONE_WORKBENCH_2026-10-06.md) for the exact evidence and remaining checks. Apple signing and TestFlight setup remain outstanding.
