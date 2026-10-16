# Native iPhone Workbench

Created: 2026-10-06 · Updated: 2026-10-15

Status: Version 1.0.0 build 7 is installed on the owner's iPhone. It preserves the
accepted combined price/fundamentals feed and conversation client from build 5,
with native Portfolio/Sankey analysis intact. Markets now uses explicit return
comparisons, daily-change color legends, two-dimensional point selection and named
return bars with company navigation. The full signed simulator app loaded the
normal production session and all 999 instruments. Signed device compilation,
signature verification and CoreDevice's installed build check pass. After owner
unlock, build 7 launched successfully and remained running as PID 30309. No
app/Keychain reset occurred. Gateway remains
`https://spectral-nature.com`. See the [Markets release](NATIVE_MARKETS_CHARTS_2026-10-14.md),
[build 7 receipt](../verification/native-analytics-2026-10-14/build7/release.json) and
[accepted Attention baseline](NATIVE_ANALYTICS_PARITY_2026-10-13.md).
Product reference: current `web/` Next.js workbench, including account
activation/recovery. The May SwiftUI scaffold has been replaced in place.

## October 7 portfolio environment correction — October 8 UTC

October 8 follow-up: a fresh device inventory again confirms version 1.0.0 build
3 on the owner's paired iPhone; no reinstall was performed. Running simulator
Attention visibly renders the retained October 7 PENG card with `Last close`,
$72.60 and its daily move below the company name. The current canonical Home
response supplies 28 scalar quotes among 31 tickers and no numeric `price_trend`
arrays. Distinguish the installed native quote renderer from the development-only
history contract when advising on updates. Native code changes require app
installation; published data changes arrive through the existing gateway.

Builds 1 and 2 defaulted to development in both configurations. A signed-in
development web request reproduced the portfolio failure: the shared endpoint
returned 503. A read-only control-store query confirmed one active linked
portfolio whose connection is `production / connected`, while `sn-api-next-dev`
runs with `APP_TRACK=development`. The environment-scoped broker lookup correctly
rejects that connection. The signed-in live web portfolio loads 29 equity, four
option and one futures positions with risk and list analytics. The defect is
client environment selection, not a missing native holdings view.

The shared `AppEnvironment` now defaults to `https://spectral-nature.com` for all
native workspaces, authentication and account links. Debug retains its explicit
`SN_SERVER_URL` override for development/local work. The Portfolio default is 5Y,
matching the web workbench. Keychain cookies remain scoped by origin; no cookie,
broker credential or snapshot is copied across environments. Existing live
servers, broker jobs, authorization and provider-selection rules are unchanged.

Version 1.0.0 build 3 passes signed simulator and physical-device Debug builds
without compiler warnings/errors. It is installed on the connected iPhone 13 Pro;
device inspection confirms bundle version 3. Automatic launch was denied because
the phone is locked. The owner must unlock, open the app and sign in with the
existing account before native private-screen acceptance can complete. Receipts:
`/tmp/sn-ios-phone-install-build3.json`, `/tmp/sn-ios-phone-app-build3.json`,
`/tmp/sn-ios-phone-launch-build3.json`.

The updated simulator visibly loads the live Daily Brief. Pointer navigation
again failed with `noWindowsAvailable` despite readable screenshots, so no fresh
signed-in native portfolio is claimed. Canonical Home/session requests return
200, with the fresh session unauthenticated. Authenticated canonical JSON
navigation was blocked by the browser client; the live candidate alias provides
the authenticated portfolio evidence and uses the same private production API.
Debug and Release Foundation probes decode the actual canonical Home with the
real Swift models and select the live gateway. Live Home provides 21 quotes
among 22 tickers; missing quotes remain unavailable. Optional numeric Home price
histories and the new LLM editor are still development-server features pending
separately authorized production promotion.

## Architecture

Use SwiftUI, Swift Charts, SceneKit, AVFoundation, and native document selection. No embedded website, generated JavaScript, new agent path, or duplicated financial/research rules. The existing public live BFF forwards the same route-ready FastAPI contracts used by the modern web client. Debug can explicitly select the isolated development BFF. Internal API ingress stays private.

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

The installed client defaults to the existing live workbench. Server deployment is unnecessary when its required endpoints already exist. Debug development access is an explicit scheme override. Local simulator and connected-phone installation are authorized by the native app task. TestFlight/App Store distribution requires separate configuration. No production server deployment is authorized by this client correction.

## Environment

Full Xcode 16.2 and iOS 18.3 simulators are installed at `/Applications/Xcode.app`; the global developer selection currently points to Command Line Tools. Use a command-scoped `DEVELOPER_DIR` without changing the user's global selection. XcodeGen is installed.

## Implemented result

The included `SpectralNature.xcodeproj` and shared `SpectralNature` scheme run without XcodeGen or a local web/backend server. XcodeGen remains the project metadata source. Deployment target is iOS 17, device family is iPhone, and both Debug and Release use the existing live gateway by default. There are no external Swift packages.

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

The initial delivery had the following gaps. The October 7 updates below supersede rendering, administration, knowledge and physical-device installation; TestFlight distribution remains pending.

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

Reference source for the earlier parity pass: web route audit at `5f7a2665`, including the October 6 account lifecycle release; checked through `b1864dae`. Subsequent changes concern server-side Home chart selection and its release record; the native Brief consumes the revised returned artifacts without a second selection engine. Audit actual current Next.js routes rather than infer capabilities from older migration promises. At that reference, Next Admin inspected prompts/configuration/API keys and issued invites/resets without editing prompts or keys. The new settings editor is covered in the follow-up below. Current brokerage routes do not expose connection or order entry.

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

## October 7 stock quotes and follow-up parity

Audit reference: `5744fd12`, with committed web/API changes since `b1864dae`. Native failure semantics now match the linked-portfolio outage handling and Zopedia workspace refresh behavior. The current frontend working tree also introduces a server-backed LLM settings editor; native editing appears only when the returned admin contract includes its language-rule/settings metadata. Concurrent server/frontend work and the owner's existing development signing were preserved.

The reported missing scalar price was not reproduced in the pre-update iPhone 16 simulator: Attention showed CIEN $443.75 alongside its daily move, and all 21 returned tickers contained `latest_close`. The verified parity gap was the omitted web price-history sparkline. Numeric `price_trend_json` already existed in the retained ticker snapshot; Home now publishes it as `price_trend`, without new acquisition or synthesis. Swift Charts renders it above the fundamentals. Explicit adaptive quote headings span Attention, Markets, holdings, Investigator and Options; price cents are preserved independently of financial-total formatting. Investigator's quote stays outside its tab-specific content and reads its existing price artifact when company research is unavailable.

Native LLM editing supports runtime/model selection, the global language rule, full prompts, dynamic string/numeric/choice parameters, reset to defaults, discard and explicit Save. It uses the existing authenticated CSRF-protected PATCH transport. Credentials/provider/deployment remain server-owned. The current development server has not released this separate settings contract, so the installed client retains inspection until that capability appears. No settings or research decisions were changed during this task.

Follow-up evidence:

- Signed simulator Debug and signed physical-iPhone Debug builds pass without compiler warnings/errors. The actual owner-provisioned build 2 installed and launched on the connected iPhone 13 Pro. Receipts: `/tmp/sn-ios-build.log`, `/tmp/sn-ios-phone-build.log`, `/tmp/sn-ios-phone-install.json`, `/tmp/sn-ios-phone-app.json`, `/tmp/sn-ios-phone-launch.json`.
- Shared Home contract checks verified sorted finite price observations and absent-history handling. Actual Swift source decoded all 21 live prices/histories and preserved $1,818.32 in the quote formatter. Resource checks verified portfolio/knowledge clearing, default last-good retention and stale-response suppression.
- Development API revision `sn-api-next-dev--0000013` is healthy and latest ready with 100% traffic. ACR `cjrg` overlaid only `services/home_view.py` on the pinned previous image; 21/21 public ticker histories now contain actual retained observations. Image and rollback: `documents/infra/UI_DEPLOYMENT_STATUS.md`. Concurrent backend/LLM work was excluded; production is unchanged.
- Personally inspected a native rendering of the actual CIEN card at 303- and 321-point widths, produced from the real SwiftUI/Charts source and live DTO: labeled $443.75 close, +13.94% daily change, 41-point price chart and separate fundamentals. Compact card receipt: `/tmp/sn-ios-quotes-probe/CIEN-303.png`.
- The updated simulator launches and its Brief renders. Subsequent coordinate interactions fail in the computer-use bridge with `noWindowsAvailable`; direct accessibility actions still expand the Brief. Rebinding/resetting the bridge did not restore pointer navigation. New Investigator tab navigation, complete updated screen review and admin editing therefore remain unverified through live UI. No claim of complete interaction acceptance follows from the source render or successful compilation.

## Remaining acceptance and distribution

| Area | Remaining check |
| --- | --- |
| Account lifecycle | Owner-entered invitation activation and password reset; recovery email delivery. Implemented against current routes, not submitted with the owner's credentials during review. |
| Writes | New research submission/follow-up/cancellation, document upload, admin invite/reset issuance, LLM settings save after the separate server release, and Trading Agent decisions. Existing server contracts remain authoritative. |
| Numerical options | Multi-maturity mesh and current-market freshness review with suitable development data. Retained single-maturity IV, Greeks and projected-return values were reviewed. |
| Interaction coverage | Remaining lower-panel touch/scroll review and physical-device camera/pinch behavior. Simulator automation is incomplete for some SwiftUI gestures. |
| Distribution | Owner's physical-device signing, installation and launch now succeed. TestFlight/App Store configuration remains pending. Production promotion remains unapproved. |

The follow-up delivers native build 2 to the physical phone and simulator, plus the numeric Home price-history contract to the isolated development API. Concurrent language/settings work belongs to its separate release and was preserved.
