# Native feed and portfolio analytics parity

The owner wants prefetched intraday price charts in Attention, missing bank
fundamentals repaired at source, and useful portfolio list/flow analysis. Intraday
bars fetched at feed generation are sufficient; continuous streaming is unnecessary.

Updated October 15: native build 7 is installed on the owner's iPhone. It preserves
build 5's accepted Attention/fundamentals/conversation implementation and adds clear
Markets return comparisons, numeric daily-change colors, point inspection and named
return bars. Signed simulator/device builds pass, and the full app's production
Markets path was reviewed. After the owner unlocked the phone, build 7 launched
successfully and remained running as PID 30309.
See the [Markets release plan](NATIVE_MARKETS_CHARTS_2026-10-14.md) and
[build 7 receipt](../verification/native-analytics-2026-10-14/build7/release.json).
The October 14 build 5 installation history follows.

## Installation readiness audit — October 14

### Authorized native parity and installation

The owner connected the phone and requested the fixes and installation. USB and
the CoreDevice tunnel are now connected. The separate stack promotion has since
published conversation API revision 7 and web revisions 92/8; native can continue
using the production gateway. No additional server promotion is needed here.

Native build 5 replaces the Attention selector/stack with one shared-price-axis
week/latest-session chart and five compact fundamentals histories. The two time
scales remain separate paths; point inspection retains real dates and prices.
Financial period labels now cross the native DTO boundary, including fiscal
quarters. Missing latest values retain their histories; scalar-only metrics remain
visible without fabricated charts. All available fundamentals are retained, and
each financial value/caption appears once. The current conversation client and
existing portfolio analytics are included in the new signed device build.

The full native simulator loaded the real production Home feed. Personally reviewed
MU's separate five-close and 78-bar price paths, all five financial histories and
their fiscal-quarter labels, plus compact DELL headings and the intraday-only DRAM
and SKHY contracts. Scalar-only/daily-only behavior was inspected in the renderer;
no additional fixture rendering is claimed. Signed simulator/device builds pass.
The final device signature and provisioning-profile device membership pass.

CoreDevice confirmed build 5 replaced physical build 3. Initial launch returned
iOS's broad trust/signing rejection. The owner checked trust; the subsequent launch
succeeded and the app remained running as PID 29992. No uninstall, sign-out or
Keychain reset occurred. Production images and settings were unchanged by this
phone release. Evidence: [build 5 receipt](../verification/native-analytics-2026-10-14/build5/release.json)
and [full native production chart review](../verification/native-analytics-2026-10-14/build5/native-production-fundamentals.png).
Private physical-device workflows were not exercised during this installation.
DeepSeek research
generation currently has a documented provider timeout/503 issue; this phone
release does not change models or claim to repair that service condition.

The earlier readiness audit below preceded the separate stack promotion and this
successful native installation. The prepared physical build 4 was linked at 02:33 local. Native ResearchView and
APIModels changed at 11:28 for conversation continuity; the newer simulator binary
was linked at 11:44. That simulator build does not update the signed device binary.

- Attention has drifted: native still renders the period selector, repeated
  financial values and stacked/disclosed histories. Both current web releases use
  one combined week/latest-day price chart and a compact value-plus-history grid.
  Port that accepted design while preserving all available fundamentals histories.
- The native conversation source is updated, but requires a fresh signed device
  build. Its conversation endpoints are deployed only in development (API revision
  30, web revision 20); production remains on API revision 6. The native default
  server is production. Resolve that server/client release dependency before
  installing a binary containing the newer conversation client for normal use.
- Portfolio list, page and BFF source hashes match the accepted analytics release;
  native Sankey, portfolio list and main portfolio source hashes also match. No
  additional portfolio drift was found in these reviewed paths.
- CoreDevice still reports the paired phone's local-network tunnel disconnected.
  No physical installation was performed during this audit.

The prepared build is therefore not the latest web-parity release. Next steps are
native Attention alignment, device compilation/acceptance of the combined native
source, and a compatible conversation server release. Production conversation
promotion is separate from the previously approved analytics/card releases.

## Owned data paths

- Both Home generation and independent ticker refresh collect five-minute regular-session
  bars through the existing Alpaca adapter. Publish numeric points, session, feed,
  observation and prefetch times alongside up to five years of daily history.
  Replay consumes the retained snapshot. Native/web reads never call market providers.
  Session change compares the prior observed regular-session close in the same feed;
  it must not use an older daily cache. Retain that reference timestamp in provenance.
  Show the ending date of longer daily histories independently.
- The quarterly fundamentals producer and local loader cover general, bank and
  insurance SimFin statement families. Preserve absent values; unavailable provider
  coverage does not become zero or inferred revenue.
- An authenticated `/v1/portfolio/opportunities` read publishes the existing shared
  group analytics: equal-weight indexed histories, rankings, return/risk/regime
  contributions and clustered correlations. Membership/environment checks match
  holdings. Chart availability is independent of the holdings endpoint.
- Native and modern web surfaces consume this contract. Comparison selection does
  not alter the best/worst ranking universe. Sankey width uses contribution magnitude;
  signs and units remain inspectable, and omitted contributors are aggregated as Other. Native label slots scale with text
  independently of magnitude-proportional node bars and ribbons.
  Return horizon and the one-year risk-estimation window are explicitly identified.

- Current catalog IDs define the analytics universe. Empty selections return empty
  instead of loading all retired resources; correlations and joins use IDs rather
  than display names. Truly identical current names receive qualified display labels.

## October 14 acceptance

- Development API revision 29 and web revision 17 are ready. Isolated image overlays
  preserve actual deployed baselines and exclude concurrent backend/LLM work. Existing
  Home/ticker commands, schedules, identities, environment values and resources compare
  equal after GET; only their images changed.
- The development-only fundamentals execution downloaded all nine SimFin statement
  datasets and published 182,887 metric rows. GS has 351 rows, JPM 273, BAC 182 and HUM
  313. Citigroup has zero rows in this provider's bulk source and remains a real gap.
- The public development feed has 21 tickers, 19 intraday charts, and daily histories
  with up to 1,250 points. SSNLF/BTCUSD have no stock bars. Longer daily data currently
  ends October 9; the intraday session is October 13. These dates stay visible.
- Full signed native build 4 was installed only on the simulator and loaded the real
  development gateway. Attention rendered MU's 78 five-minute bars and corrected
  previous-session change of −3.63%. Range changes remained on Attention. Actual
  bank/insurance statements and four ticker cases were reviewed separately.
- Retained real production portfolio data was read without writes or connection
  transfers. It produces 38 current groups with 38 distinct labels. Native and web
  previews render indexed comparisons, risk/breadth/member analysis, correlations,
  and return/risk/momentum contributor Sankeys with conserved Other magnitudes.
- Signed simulator/device builds, web types and targeted lint pass. Seventeen focused
  numeric/privacy/identity checks pass. Private unauthenticated list reads return 401
  with no-store. These checks do not claim new LLM or narrative quality evaluation.
- The paired physical phone was discovered over local network with its tunnel
  disconnected. Its last known installation remains build 3; there is no cloud
  replacement of the native binary.

Evidence and exact candidates/rollback images:
[release receipt](../verification/native-analytics-2026-10-14/release-receipt.json),
[full native development chart](../verification/native-analytics-2026-10-14/native-development-attention.png),
[native portfolio preview](../verification/native-analytics-2026-10-14/native-risk-sankey.png),
and [web preview](../verification/native-analytics-2026-10-14/web-portfolio.jpg).

## Approved production release — October 14

The owner explicitly approved the prepared release. API revision 6, custom-domain
web revision 90 and alias web revision 6 serve the approved images. Both worker
templates retain their schedules, commands, identity, secrets and environment.
Quarterly execution `fundamentals-quarterly-refresh-jq0lbh1` is refreshing all nine
statement datasets before the independent numeric ticker execution.

The deployed portfolio service returns 34 holdings, 636 portfolio history points,
38 current list histories, 589 contributor rows and a 38×38 correlation matrix.
This read-only probe used a current active membership and the production connection
checks. It checked existing schema readiness instead of executing bootstrap DDL in
the isolated process. The public unauthenticated list route returns 401/no-store;
an authenticated production browser review has not been performed in this release.

Physical installation was attempted with the signed build 4. CoreDevice returned
a network connection reset. The phone remains at its last known build 3. Reconnect
it through Xcode to complete the binary update; production publishing does not
replace the installed iPhone binary.

### Release procedure

After explicit owner approval, update the private API, both modern frontends, existing
Home producer and quarterly fundamentals producer to the recorded immutable candidates.
Keep all existing configuration. Refresh quarterly metrics, then run only independent
numeric ticker materialization through an execution-only Home template override. This
does not regenerate retained research or audio. Verify public histories, bank fields,
private list analytics and preservation of holdings, then install build 4 on a reachable
phone. Rollback restores each recorded prior image; the new optional numeric fields
remain backward compatible with the previous public readers.

## Acceptance and release boundary

Inspect actual intraday bars, downloaded bank/insurance statements, and retained
broker list analytics; review real native and web charts before building. Preserve
concurrent source changes and deploy only isolated development overlays. Production
API/web and producer changes require explicit permission after concrete development
verification. The installed phone uses production, so a native install alone cannot
publish these new server contracts. Record precise release/acceptance evidence here.
