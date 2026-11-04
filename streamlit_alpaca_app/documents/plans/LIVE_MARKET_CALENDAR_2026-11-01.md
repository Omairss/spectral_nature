# Live market calendar — November 1, 2026

The owner approved implementing Alpha Vantage earnings discovery plus official Fed, BLS and BEA schedules. Replace authored timeline events with retained real source records. Preserve the approved native carousel, inline expansion and attention graph. The owner subsequently authorized production deployment and the physical-phone update on November 1.

Status: live in production and installed/launched as native build 18 on the owner’s iPhone. The production job runs every six hours; its first managed-identity execution published 3,454 expected earnings and 59 agency events. Both production web entry points read that publication. See [production verification](../verification/live-market-calendar-2026-11-01/PRODUCTION.json) and preceding [DEV qualification](../verification/live-market-calendar-2026-11-01/DEVELOPMENT.json).

## Ownership and publication

The calendar has one shared background producer and one retained dataset, independent of brief inference. Provider adapters return source identities, dates, timing precision and provenance. The producer merges source refreshes with last-known events and publishes one versioned artifact through the existing materialized-dataset store. The existing public Home contract projects that artifact into its optional calendar field. API reads never fetch a provider or invoke a model. Historical Home requests use the calendar publication available at the selected date; a later schedule must not leak into replay.

Adapters: Alpha Vantage bulk expected earnings CSV (one request per refresh), official BEA iCalendar, BLS iCalendar with official HTML/FRED date fallback when available, and the Federal Reserve's annual and monthly schedules. ICS is parsed with the maintained icalendar library, including folded lines and timezone-aware values. FRED release-date capability belongs in the existing FRED client. Provider errors and missing keys are isolated; they do not erase previous events or block other calendars or the brief. No data access depends on brokerage login.

Provider/issuer IDs and fiscal periods identify earnings; official ICS UIDs identify agency releases. Revisions retain prior scheduled dates. Missing rows do not imply cancellation; only an explicit source cancellation can do so. Original fetched/source update times survive partial failures. A passing scheduled time does not establish actual release, company confirmation, sentiment, or market reaction. Release time and conference-call time remain distinct; date-only and session-only records do not acquire invented clock times. Economic release dates on FRED are distinct from observation availability.

## Clients and scope

Render the same published calendar in native and web clients. Native retains five centered dates, stacked event labels, center-out scale/fade, expansion within the carousel, and the graph immediately below. Event detail reveals timing/confirmation and source links. The compact calendar has no permanent explanatory legend. Sources supply event names and facts; no client keyword rules generate news summaries or causal explanations. Existing shared research may enrich a future version; this work incurs no model inference.

Refresh is separately schedulable every six hours, including weekends, within Alpha Vantage's published free request allowance. Reuse the existing job runner, publication fencing and namespace isolation. Development receives only locally qualified application changes. Preserve all unrelated job/provider/research settings and active worktrees.

## Integration and qualification

Implementation worktree: `/Users/omairs/Documents/code/spectral_nature_calendar_20261101`, based on the accepted runtime and the user-reviewed native calendar branch `d3cad0cf`. The original IDE checkout remains dirty with other sessions' work; do not build or release that checkout. Review every changed runtime file, update the committed source acceptance with meaningful evidence, then pass the source gate before packaging. Native build/interaction acceptance remains separate from the backend gate.

Read-only live source captures are kept under `documents/verification/live-market-calendar-2026-11-01/raw/`; authored October calendar examples must never be merged into the live dataset. Inspect real multi-event days, date changes, empty days, source outage retention, DST, replay and the actual native/web display. Use operational contract tests for parsing/publication reliability, and personal review for display usefulness. Do not treat tests as evidence of LLM quality.

The owner authorized storing the supplied key as `AlphaVantage` in the existing Key Vault. Live ingestion uses that secret. The public demo remains verification evidence only, and is rejected as a serving key.

## Owner-key activation

The owner explicitly authorized saving AlphaVantage in the existing Key Vault. A live request succeeded with 3,454 earnings rows and the normal producer published them alongside 61 agency events in the development namespace. Calendar refreshes remain independent of brief generation. Busy-date review exposed eager rendering; the clients now create visible event rows as needed. Shared ordering uses event importance without a fixed company shortlist. Exact-image development qualification and the first scheduled-worker execution passed. The subsequent authorized production promotion is recorded below.

## Initial DEV delivery and source integration

Only the existing DEV API and Next.js images changed. The new `market-calendar-refresh-dev` job runs at 00:00/06:00/12:00/18:00UTC, with a 300-second deadline and the existing managed identity. All 36 existing configurations, all 21 production images and the other 13 existing DEV images were read back unchanged. Conditional Home reads include the calendar in the response fingerprint; an unchanged body returns 304. Replay before the first retained calendar does not receive the later live schedule.

Scoped changes were also applied to the original IDE checkout with a clean patch check, preserving its concurrent jobs/types changes. That checkout remains unaccepted release source. Build/release only the committed `feature/live-market-calendar-20261101` worktree. Issuer-confirmation ingestion remains future work; Alpha Vantage entries are explicitly expected.

## Production and physical-phone delivery

The owner requested the real production scan and phone update. Added `market-calendar-refresh` with the same qualified worker image, four daily UTC refreshes and a 300-second bound. Its `production` namespace matches the production API; the store canonically maps that production name to its existing production publication path. The first execution succeeded without inference. Promoted the exact DEV API/web images to the existing production API and both Next.js entry points. All 37 pre-existing configurations, all 16 DEV images and the other 18 existing production images remain unchanged across the 38-consumer readback; all three new app revisions are Healthy.

Signed Release build 18 is installed in place over build 17, independently read back and running on the paired iPhone. The initial launch was rejected under its renewed profile despite valid local signing, certificate membership and device registration. The installed executable subsequently ran, and a final ordinary launch succeeded with the same process. No physical-screen inspection is claimed. Current source and verification remain in this committed worktree; the original IDE checkout retains concurrent work.
