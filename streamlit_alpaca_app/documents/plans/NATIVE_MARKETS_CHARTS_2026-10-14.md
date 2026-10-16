# Native Markets chart clarity

The owner requested useful, interpretable Markets plots after build 5 was installed.
The old business scatter plotted an opaque log-price slope against returns, colored
every positive reciprocal-rank opportunity score green, sized by raw share volume,
and selected the nearest x value regardless of y. Broad/commodity views opened
with an unlabeled 3D cloud. The web view remains on its existing design in this task.

## Design

- Use the retained `momentum_profiles` dataset for complete numerical histories
  in all three Markets modes. The 999-row October 14 snapshot covers all names
  for 1D/7D/1M/3M and 983/917 for 1Y/5Y. The ranked research feed supplies optional
  company, volume and direction metadata; its shortlist must not define the plot
  universe. Keep data access on the authenticated shared dataset API.
- Compare two explicitly named return periods on percent axes. Vertical period
  follows the page selection; horizontal period is independently selectable and
  cannot equal it. Zero reference lines identify gains and losses. These overlapping
  return periods are comparisons, not a measure of acceleration or risk.
- Every dot is one instrument, with equal area. Daily percent change uses a
  symmetric coral/neutral/teal scale with an actual numeric legend. Missing color
  values remain neutral and do not become zero. Show plotted/available coverage.
  The color extent uses the 95th percentile of absolute daily moves so ordinary
  changes remain distinguishable. Saturated tails use explicit ≤/≥ endpoints;
  inspection retains exact values. Axis domains include every observed return.
- Label extremes, highlight the selected instrument, and select by two-dimensional
  distance in plot pixels. A searchable point inspector exposes every identity;
  selected instrument details link to the existing Investigator.
- Add named return bars for the five highest and five lowest values, or all names
  when there are ten or fewer. Each SwiftUI row keeps its symbol, same-scale signed
  bar and exact percentage in separate columns and links to Investigator. Preserve
  full ranked rows. Retain volume, 3D and
  multi-horizon views under disclosures rather than remove existing analysis.
- Fix the shared native scatter renderer so Portfolio and Options also receive
  numeric color legends and correct point selection. Retain breadth's 0–100%
  contract and numeric Greek/factor axes. The 3D renderer uses the same numeric
  scale as its legend.

Apple's [ChartProxy](https://developer.apple.com/documentation/charts/chartproxy)
provides plot-relative point positions; overlay input must subtract the plot frame
origin before measuring distance. No financial data is derived from screen geometry.

## Verification and release

Reviewed the actual retained dense market, the eight-instrument Advertising lens,
point tap (MRNA), searched point selection (HUBS), and 5Y coverage (917/999).
Both iPhone 16 and iPhone SE layouts retain readable percent axes, identities and
legends. The first visual pass revealed weak color contrast from an extreme daily
move and a truncated final x tick; corrected the scale and fixed tick sizing before
release. The existing Portfolio list preview retains its 0–100% breadth legend,
percent axes, group identity and size information. Group keys own selection identity.

The retained All Market research feed supplies names for all 999 matching profiles
and volume for 998 when requested without the old 80-row presentation limit.

Build 6 compiled and loaded all 999 instruments through the normal authenticated
production session. MRNA inspection correctly joined the retained company name,
price and both returns. CoreDevice confirmed installation over build 5, but launch
was blocked explicitly by the locked phone. Final bar-label review replaced the
category-axis annotations with separate symbol/value columns; final build 7 was
reviewed in the full signed simulator app and has replaced that preliminary copy.

The separate local preview uses retained public market
rows; shipping account gates and API paths are unchanged. Source hashes confirm
build 5's accepted Attention, fundamentals, conversation and main Portfolio remain
unchanged. No server or web deployment is required.

The existing retained `momentum_profiles` snapshot has no matching rows for the
commodity catalog symbols. That source coverage gap is unchanged; the native UI
now states that price history is unavailable for such a lens. This task does not
introduce provider calls during page rendering or change the backend pipeline.


## Final release — October 15

Version 1.0.0 build 7 passes signed simulator/device compilation and signature
verification. Sources and device binary were frozen by SHA-256 before transfer.
The complete simulator app restored the normal production session, loaded all
999 instruments, showed the final bar rows without overlapping labels, and opened
Corteva analysis from the CTVA return row. Final bars were reviewed on iPhone 16;
the earlier compact iPhone SE review covers the shared scatter/legend layout.

CoreDevice confirms physical build 7 is installed. After owner unlock on October
15, `passcodeRequired=false`; build 7 launched successfully and remained running
as PID 30309. This resolves the earlier locked-device launch block. Physical
private-screen visual review is not claimed. No uninstall, credential reset,
server deployment or web change occurred.

[Build 7 receipt](../verification/native-analytics-2026-10-14/build7/release.json)
and [final complete-app return bars](../verification/native-analytics-2026-10-14/build7/markets-production-return-bars.png).
