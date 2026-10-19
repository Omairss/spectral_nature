# Home presentation cleanup — October 18

Status: locally reviewed; packaging and DEV verification follow. Production currently remains on the preceding accepted release.

The owner requested removal of Home's semantic suppression, preservation of confidence cutoffs, explanation of the learning fields, and cleanup of obsolete vestiges. The completed preceding production recovery was pushed to origin/master at `6b0f93c2` before edits.

Home projection now preserves model-written explanations and summaries regardless of unresolved status, individual words, punctuation, repetition or overlap scores. It no longer rewrites percentage observations into canned intensity prose. The existing summary preparation receives all published research beats rather than filtering unresolved ones through causal-language patterns. No model, prompt, confidence cutoff, research budget or write permission changes.

[Complete local comparison](local-review.json) replays eight actual production stories without new inference. All eight reviewed narration beats preserve their original summary/explanation fields. Four hydration records recover previously cleared explanations; three also retain their short original model summary. Every complete projected story was personally read. Existing fourteen search/supplied-evidence checks pass; exact field and import checks protect contracts rather than grade prose.

The uncalled live-bundle fallback and its private helper graph, unused Home hydration/rebalance entrypoints, retired exports/imports and eight obsolete prose-expectation/fallback tests are removed. Current event-search acquisition remains, with its five callable dependencies unchanged. Previous source and three superseded Home design documents are verified in private archives outside the checkout with the existing 30-day automatic retention. Active indices link here; retired document paths are recorded in `infra/retired_paths.json`. Historical audit receipts remain evidence, not restoration instructions. Current shared AQL cause/quality scoring and learning behavior are outside this change.

## Origin and visibility of learning guidance

The May 17 [Zopedia parity plan](../../architecture/new_features/zopedia/ZOPEDIA_PARITY_COMPLETION_PLAN_2026-05-17.md) specified `forbidden_future_claims` and `required_future_evidence`. Commit `b8ad4c6da0bef36b9dee5efc2fe3e6b6d82c9174` implemented them on May 20. The existing LLM critic supplies their content from the episode; backend code supplies their fixed schema and memory-page headings. Replay presents them as semantic guidance, not phrase matching. This format is explained, not changed, by this task. The owner explicitly permits the existing confidence cutoffs.

The four reviewed learning examples are in [the assessment record](../research-resilience-2026-10-18/local-review.json), and [guidance reuse](../research-resilience-2026-10-18/learning-guidance-reuse.json) shows a later answer. These evaluation records were not published into a user account. Runtime learning events persist in `saa_zopedia_learning_events`; eligible tool-affordance updates can become Zopedia concept pages. There is no dedicated learning-history screen in the current web/native app. Repository `documents/learnings.md` is a separate development log.
