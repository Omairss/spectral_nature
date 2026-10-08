# Native iPhone Workbench

Created: 2026-10-06 · Updated: 2026-10-07

Status: October 7 parity implementation installed locally; principal live signed-in read workflows reviewed. Credential-changing and server-mutating workflow acceptance remains outstanding. Product reference: current `web/` Next.js workbench, including account activation/recovery. The May SwiftUI scaffold has been replaced in place.

## Architecture

Use SwiftUI, Swift Charts, SceneKit, AVFoundation, and native document selection. No embedded website, generated JavaScript, new agent path, or duplicated financial/research rules. The existing public development BFF forwards the same route-ready FastAPI contracts used by the modern web client. Internal API ingress stays private.

The native transport owns an ephemeral URLSession and origin-bound cookie jar. Persist session cookies in a device-only Keychain item; never UserDefaults. Retain the gateway's same-origin and CSRF checks. Serialize authenticated transport to avoid concurrent refresh-token rotation. Reject redirects and clear private screen state on sign-out or confirmed session expiry. Transient outages must not erase a valid session.

## Product implementation

- Daily Brief: retained answer, historical replay, native audio, attention groups, inline ticker fundamentals, key datapoint trends, ticker navigation.
- Markets: server-defined business/commodity lenses and return horizons, native ranking and momentum plot, ticker handoff.
- Zopedia: durable research threads, contextual composer, ordered live journal, cancellation, follow-up, linked citations, folder/page reader, source upload.
- Portfolio: snapshot-backed account/holding views, native value and benchmark charts, exposure, risk/correlation, performance, and opportunity sets.
- Additional workspaces: macro categories and native series, company investigator, option chains/scenarios, Trading Agent candidates/decisions, role-gated admin inspection.
- Shared design: neutral near-black surfaces, waveform identity, compact typography, tabular numbers, semantic teal/violet/coral. Concise empty/error states and useful actions only.

Renderer parity is evaluated by analytical meaning. Native charts use existing numeric data and contracts; unsupported browser-only visualization types are not silently converted into a different chart.

## Acceptance and release

Inspect actual development responses and the running iPhone simulator, including the smaller iPhone SE, before calling this accepted. Compilation is a mechanics check after implementation, not product acceptance. No new LLM behavior is introduced.

The development app points at the isolated Next.js development hostname. Server deployment is unnecessary if all required endpoints already exist. Local simulator installation is authorized. TestFlight/App Store distribution requires an Apple development team and provisioning; do not claim device distribution without those prerequisites. Production stays untouched.

## Environment

Full Xcode 16.2 and iOS 18.3 simulators are installed at `/Applications/Xcode.app`; the global developer selection currently points to Command Line Tools. Use a command-scoped `DEVELOPER_DIR` without changing the user's global selection. XcodeGen is installed.

## Implemented result

The included `SpectralNature.xcodeproj` and shared `SpectralNature` scheme run without XcodeGen or a local web/backend server. XcodeGen remains the project metadata source. Deployment target is iOS 17, device family is iPhone, and both Debug and Release use the existing isolated development gateway. There are no external Swift packages.

Five native tabs provide Brief, Markets, Research, Portfolio, and Explore. Explore includes macro, investigator, options, Trading Agent, and admin inspection. Source files are grouped under App, Design, Networking, Core/Security, and Features. Native blocks render Markdown headings, paragraphs, links, lists, code, and tables without executing HTML. Financial statements and technical charts consume the existing visualization artifact datasets. Macro level and change use separate native axes/plots so their different units are preserved.

The phone Brief has explicit Brief/Attention selection, an expandable retained report, horizontal datapoint charts, collapsible supporting business context, native audio, and replay controls. The attention graph renders the published coordinates/edges through SwiftUI Canvas and native node controls; it does not run the Plotly specification as code. Raw graph hover diagnostics are not placed in the product.

All auth/data calls use the existing public `/api/` boundary. Private API ingress and server authorization remain unchanged. Cookie storage is scoped to the configured origin; passwords remain transient. Mutations preserve CSRF and Origin verification, research submissions retain an idempotency key across an ambiguous retry, and no automatic mutation retry occurs after a network failure. A cancelled read cannot subsequently expire the current UI session. Server-confirmed expiry/sign-out remove private view state; transport/service failures preserve a valid session.

## October 6 acceptance (historical)

- Signed Debug simulator build succeeded with Xcode 16.2 and the iOS 18.2 SDK on the installed iOS 18.3 runtime.
- The final formatted sources also passed a generic physical-iPhone Release compilation (`iphoneos`, signing disabled for compile verification only). No provisioning profile, installable signed device package, or distribution upload is implied by that check. Both final builds completed without compiler warnings or errors; whitespace validation passed.
- Installed and launched on iPhone 16 and iPhone SE (3rd generation).
- Live development Home decoded as six attention groups and five datapoint charts. A native Foundation decode also verified actual price and daily/monthly changes against the returned JSON, including CIEN $443.75 / +13.94% / +30.02%.
- Personally inspected the live native Brief, compact chart card, attention selection/story, tab access gate, and native login screen. The SE navigation bar, compact number axis labels, content margins, segmented selector, and tab bar fit the smaller display.
- Retained development audio downloaded, entered native playback, changed the control to Pause, and paused successfully.
- The initial unsigned simulator build could launch but failed Keychain writes. Normal Xcode simulator signing supplies the simulated application identity and fixed storage; do not disable it for a running simulator build.
- Model decoding respects Foundation's digit-containing snake-case conversion (`price_change_1d_pct` becomes `priceChange1DPct`). The v1 portfolio decoder accepts the service's explicit empty correlation-array representation for portfolios with no equities as well as its populated matrix object.
- User sign-in was requested in the simulator. No password was requested in chat or extracted from another application. Private live screens, refresh-token expiry/rotation, source upload, research submission/cancellation, and administrative/Trading Agent actions have not been exercised through the native UI yet.

The initial delivery had the following gaps. The October 7 implementation supersedes the rendering, administration and knowledge entries; distribution still requires the owner's configuration.

| Area | Current native boundary |
| --- | --- |
| Advanced rendering | Native line/scatter/bar/correlation views and a 2D attention graph. Desktop treemap/3D/scenario-surface/monthly heatmap fidelity and advanced renderer interactions remain deferred. |
| Administration | Role-gated inspection. Invitation/reset issuance, prompt/configuration editing, and API-key management remain in the existing web workbench. |
| Brokerage | Snapshot viewing and existing Trading Agent decision contract. Native broker connection and guarded order-entry workflows are not introduced. |
| Knowledge | Folder/search/page/source upload and citations. Proposal/maintenance workflows and a linked backlinks inspector remain in the web workbench. |
| Distribution | Simulator installation only. Physical-device signing, TestFlight, and App Store delivery await the user's Apple team/distribution configuration. |

No server change or deployment is required for this native implementation. Existing Next.js development, Streamlit, production apps, and pipeline jobs were not modified by this task.

## Documentation consulted

- [Apple SwiftUI navigation](https://developer.apple.com/documentation/swiftui/navigation)
- [Apple Swift Charts](https://developer.apple.com/documentation/charts)
- [Apple URLSession cookie storage](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/httpcookiestorage)
- [Apple Keychain application-identity/entitlement behavior](https://developer.apple.com/documentation/security/errsecmissingentitlement)
- Current Next.js workbench parity, market-intelligence, public BFF, private API, shared visualization, portfolio, options, and knowledge source contracts in this repository.

## October 7 parity implementation

Reference source: web route audit at `5f7a2665`, including the October 6 account lifecycle release; checked through `b1864dae`. Subsequent changes concern server-side Home chart selection and its release record; the native Brief consumes the revised returned artifacts without a second selection engine. Audit actual current Next.js routes rather than infer capabilities from older migration promises. Current Next Admin inspects prompts/configuration/API keys and issues invites/resets; it does not edit prompts or keys. Current brokerage routes do not expose connection or order entry. Those are not new native requirements.

Implemented the missing current workflows natively:

1. Account recovery, invitation acceptance, password reset, and origin-validated account-link entry. Use the existing BFF account endpoints and server password policy. No second identity system or credential transfer from the browser.
2. Markets business/broad/commodity modes, volume treemap with daily-move colors, return topology and multi-horizon heatmap, complete ranking fields and ticker handoff.
3. Options IV and projected-return topology, Greek tradeoffs, best-fit contract metrics, complete chain/candidate tables and scenario inspection. Request existing option datasets; never duplicate pricing or ranking rules.
4. Portfolio asset exposure, all-position P/L contribution, benchmark history, monthly return matrix, complete holding/risk/annual statistics.
5. Macro raw/smoothed/off overlay choices, complete indicator metrics/readings and searchable release-filtered series catalog.
6. Knowledge/source separation, retained document search and inspection, connected-page navigation and internal Markdown links, administrative maintenance inspection.
7. Admin invites/reset actions, runtime/prompts/parameters/API keys, usage charts, security and full system inspection with history controls.

Shared native visualization primitives own layout, selection, units, empty cells and accessibility. SceneKit renders numeric 3D points/meshes with camera control; it never executes web specifications or model-generated code. Source values remain available through selections, searchable point inspection and complete tables. IV meshes require all four corners of a cell, so missing observations remain holes. Signed return heatmaps show missing cells explicitly. Market heatmaps and large tables virtualize rows inside bounded viewports; short tables fit their content. All position contributions remain visible, and monthly returns require consecutive month-end observations. Portfolio dollars and indexed benchmark histories stay in separate views.

Account links accept only the configured origin and valid invitation/reset tokens. The native sheet supports original email links and a registered `spectralnature://account?link=...` wrapper. Universal Links are not configured: they need Apple team and domain association. Existing BFF routes remain the sole identity authority. Session transitions ignore stale reads and clear private view identity when authentication changes. Credential entry remains transient; opening another account link clears entered passwords and identity fields.

## October 7 observed acceptance

- Installed the signed Debug build on iPhone 16, restored the owner's existing native Keychain session, and reviewed private screens through the real development BFF. No credentials were extracted or copied from another app.
- Markets: inspected the 80-row business treemap/scatter and 999-symbol broad-universe return topology/heatmap. The first eager heatmap froze the app; a main-thread profile led to the shared lazy-row/one-time-scale fix. The full universe then remained responsive. Opened searchable 3D inspection and selected an actual symbol with exact returned values.
- Portfolio: loaded the actual 34-position portfolio, inspected value and indexed performance/drawdown, and verified the monthly matrix's returned month/value cells through accessibility. Lower-panel gesture review is incomplete: simulator automation did not reliably move the outer SwiftUI scroll view.
- Knowledge: navigated Root Wiki into a real nested folder using its path; opened a retained page, then its connected company page. Completed research conversations load their retained Markdown answers. Sources and maintenance inspections consume existing workspace records.
- Macro: loaded actual indicators with separate level/change charts, complete readings and the searchable series catalog. Observation dates now use the shared UTC day formatter rather than wrapping raw ISO timestamps.
- Admin: inspected the live user table and role-gated navigation. Summary fields match actual auth-store keys. No invitation, recovery email, access change, or Trading Agent decision was issued during acceptance.
- Options: loaded an actual chain. The development NVDA snapshot advertised an expired first expiration (2026-09-21) with unavailable IV/Greeks. Selecting October 16 returned usable IV and Greeks. Evaluated the server-backed scenario, inspected best-fit cost/return/breakeven, and opened native IV/opportunity plots. Searchable inspection returned the exact October 16 $240 call at IV 31.16%. The retained dataset reports one maturity with DTE 25; it does not establish current-market freshness or multi-maturity mesh acceptance. No client-side pricing substitute was introduced.
- Account parser validation compiled the actual Foundation source and checked valid invitation/reset/wrapper links plus rejection of foreign-origin, downgraded HTTP, userinfo, wrong-path, duplicate, conflicting and malformed tokens. No committed fixture or authentication bypass was added.
- Final signed simulator Debug and generic physical-iPhone Release builds pass without compiler warnings/errors; `git diff --check` passes. Local receipts: `/tmp/sn-ios-build.log` and `/tmp/sn-ios-device-build.log`. Generic Release verification disables signing only for compilation; it does not produce an owner-provisioned distribution.
- Installed the parity build on iPhone SE (third generation) and personally reviewed the live Brief, account access gate, sign-in and password-recovery layouts. The final shared chart-label adjustment centers date ticks inside the plot bounds. Returned the final installed app to the iPhone 16 simulator after compact-screen review.

## Remaining acceptance and distribution

| Area | Remaining check |
| --- | --- |
| Account lifecycle | Owner-entered invitation activation and password reset; recovery email delivery. Implemented against current routes, not submitted with the owner's credentials during review. |
| Writes | New research submission/follow-up/cancellation, document upload, admin invite/reset issuance, and Trading Agent decisions. Existing server contracts remain authoritative. |
| Numerical options | Multi-maturity mesh and current-market freshness review with suitable development data. Retained single-maturity IV, Greeks and projected-return values were reviewed. |
| Interaction coverage | Remaining lower-panel touch/scroll review and physical-device camera/pinch behavior. Simulator automation is incomplete for some SwiftUI gestures. |
| Distribution | Apple development team, physical-device signing/provisioning and TestFlight configuration. Production promotion remains unapproved. |

This update changes only the native client and its documentation. The simulator install is the development delivery; required server routes already exist. Concurrent server-side Home selection changes belong to the separate release work and were preserved.
