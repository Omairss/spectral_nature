# Inline brief expansion — October 30, 2026

The owner wants a sentence to reveal a short explanation when a phrase is tapped, while retaining a readable sentence. This is a native interaction experiment following the accepted local carousel review, not permission to publish generated market claims or deploy to production.

## Interaction and content contract

Each paragraph is an ordered list of stable-ID segments. Plain segments have `text`; expandable segments also have `expansion`, a complete replacement that fits the same surrounding sentence. The app joins those runs into one native attributed text paragraph. It does not append a separate sentence fragment or insert individual button views into a text baseline. Subtle teal/dotted-underlined phrases invite a tap. An expanded span returns to regular text color, retains its underline, and collapses when tapped. Several phrases can stay open independently. Native layout handles wrapping and Dynamic Type; reduced motion removes the transition.

Example compact: “Intel fell today after **weak earnings**, renewing doubts about its recovery.” Expanded: “Intel fell today after **revenue missed forecasts and higher factory costs squeezed margins**, renewing doubts about its recovery.” The prefix, suffix and punctuation outside the replacement are unchanged. The producer must choose both versions together; a generic explanation cannot simply be inserted into arbitrary prose.

The optional `summary.expandable_brief.paragraphs` extends the existing Home payload. Each paragraph has `id` and `segments`; each segment has `id`, `text`, and optional `expansion`. Existing Markdown remains the fallback and audio source. Replay, publication caching, audio, Attention and the calendar retain their existing paths. Expansion state belongs to the displayed publication and resets when publication identity changes. No new window, navigation, research request or network call occurs on tap.

## Experiment boundary

The POC uses authored illustrative Intel/Microsoft/Meta/Fed examples in an external loopback fixture, not verified current market reporting. The app contains no company-specific text, keyword matcher, phrase selection rules or inference path. The normal API client and decoder render the fixture through the existing DEBUG origin override. The carousel's fixture and implementation remain intact; this separate Home fixture omits the optional calendar to put the brief at the top for inspection.

For a production experiment, the shared Zopedia final brief composer would create compact/replacement pairs from its retained evidence, with source references carried by that publication. Its semantic review must read complete sentences with individual and simultaneous expansions and assess factual support, grammar, brevity, and preservation of meaning. This POC makes no claim to have qualified that generation. Do not add a client-side LLM or generate explanations on tap.

## Local review

Use the existing accepted-source native worktree, commit scoped source, run the committed source gate and build the signed Simulator app. Personally inspect compact, expanded, independently toggled and collapsed paragraphs in the actual native app, including multiple expandable phrases in one sentence. Inspect a larger text size for natural wrapping. Retain screenshots and build/source identity under `documents/verification/native-brief-expansion-2026-10-30/`. Backend source-gate success does not certify this native text interaction. No server or physical-phone release is part of this POC.
