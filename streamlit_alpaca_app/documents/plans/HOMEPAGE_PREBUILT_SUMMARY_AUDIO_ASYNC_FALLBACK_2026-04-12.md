# Homepage Prebuilt Summary Audio Async Fallback

## Goal

Move homepage narration off the normal page-render path.

## Decisions

- The `attention-home-build` job now builds the homepage summary payload and, when ElevenLabs is configured, attaches prebuilt audio before persisting the `attention_home_snapshots_1d` snapshot.
- The materialized home snapshot now stores `homepage_summary_json` so the UI can reuse the exact summary text and prebuilt narration bytes.
- The homepage UI prefers the stored summary payload. If audio is missing, it can still synthesize narration on demand, but only through a background async task instead of a blocking render-time call.

## Why

- The summary text is deterministic from the stored attention payload, so it should be computed once and reused.
- ElevenLabs is an external dependency with noticeable latency. Keeping that on the synchronous render path makes the homepage feel stalled.
- The async fallback keeps the page usable even if the snapshot predates the new job output or narration generation fails during a scheduled run.

## Validation

- `tests/test_services.py` covers summary payload generation, narration-safe text cleanup, ElevenLabs payload attachment, and home snapshot serialization.
- `tests/test_pipeline_jobs.py` covers that `run_attention_home` persists `homepage_summary_json` with prebuilt audio metadata.
- `python3 -m py_compile` passes for the touched app, service, and job modules.

## Deployment Notes

- The UI change can ship safely to the dev container app.
- The scheduled `attention-home-build` rollout uses the shared pipeline job deployment path, not a separate dev-only target, so prebuilt narration in live snapshots should be promoted deliberately.
