# Live market calendar — November 1, 2026

The owner approved implementing Alpha Vantage earnings discovery plus official Fed, BLS and BEA schedules. Replace authored timeline events with retained real source records. Preserve the approved native carousel, inline expansion and attention graph. Production promotion is not authorized by this request.

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

Alpha Vantage key is absent from process configuration and Key Vault metadata. Asked the owner to save an existing key as `AlphaVantage`; do not paste secrets in chat, create an account or buy a subscription. The documented public demo may be used for local read-only verification, but must never become a default key or a public serving feed.
