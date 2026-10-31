# Native brief expansion POC — October 30, 2026

The owner requested short explanations that expand inside a sentence when a phrase is tapped. This native SwiftUI experiment uses a single attributed text layout per paragraph, not a web view, modal, or separate explanation card. Tapping an underlined phrase swaps only that segment for a syntactically compatible replacement; tapping its expanded span collapses it. Multiple phrases are independent.

The external [Home fixture](home-fixture.json) contains authored illustrative claims, not verified current market reporting. It exercises Intel earnings, two independent phrases in a Microsoft/Meta sentence, and a Fed context explanation. The app renderer has no ticker-specific content or NLP rules. The [design plan](../../plans/INLINE_BRIEF_EXPANSION_POC_2026-10-30.md) separates this display experiment from future evidence-backed generation through the shared Zopedia brief composer.

Run the existing loopback server from the application directory:

```sh
python3 documents/verification/native-event-carousel-2026-10-30/fixture_server.py \
  --fixture documents/verification/native-brief-expansion-2026-10-30/home-fixture.json \
  --port 3192
```

Launch the signed Debug Simulator app using the existing `SIMCTL_CHILD_SN_SERVER_URL=http://127.0.0.1:3192` override. This fixture omits the optional carousel to place the brief first for review; the approved carousel and its original fixture are preserved. Server defaults still serve the calendar review at port3191. Both listeners are loopback-only, without broker endpoints or credentials.

## Verified native review

The signed Simulator build from Git `c87ec9f5` passes the committed source gate and compiles without warnings or errors. [Local receipt](LOCAL.json) records native inputs, the build identity and review limits. The backend gate covers the unchanged accepted server inventory; it does not certify native behavior.

Personally read all eight complete paragraph combinations, then inspected the actual app. Intel expands into two lines of explanation while retaining its prefix, suffix and punctuation. Microsoft can remain open alongside Intel; collapsing Intel leaves Microsoft open. Microsoft and Fed each restore their original compact wording when collapsed. The previous carousel/Home publication, without the new field, still renders its calendar, retained graph and Markdown brief through its existing cache/304 path.

The native app is left open on Intel's explanation. [Compact](native-compact.jpg) and [expanded](native-expanded.jpg) screenshots show the real iPhone 16 Simulator. Multiple phrases are supported by the same data-driven handler. Simulator coordinate input returned `noWindowsAvailable`; native accessibility activation verified Intel, Microsoft and Fed, while a separate Meta-specific tap, VoiceOver and larger Dynamic Type settings remain unqualified. No product code was added to work around the automation limitation.

The examples are authored interaction fixtures. Automated brief generation is not qualified by this review. No web, API, production, or physical-iPhone release is part of this POC.
