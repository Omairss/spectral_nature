# Production Home cache correction — October 26

Owner authorization: “push to prod”, received after the Home transport repair was accepted and deployed to development.

Production's accepted web image is `6f143ff4…`, packaged from Git `90e514f9`. The selected real Git checkout starts at `86d3ba12`, whose frontend is identical to that complete accepted input inventory. Apply only the same reviewed shared-loader patch from development: explicit `cache: "no-store"`, removal of the revalidation option, and correction of the source comment. This preserves production's research charts, approved-call Trading, authentication, replay, ETags, client retention, audio and independent ticker/Highlight delivery. Review confirms every other frontend input and the complete Python runtime are unchanged. No source is extracted from an image or archive.

The development application has separate paper-Trading and Health work; its entire image is not the production candidate. Its existing deployment and all other consumers will be preserved. The exact production candidate must pass the same eight HTTP checks in an isolated development Linux container, plus complete input/compiled-file checks, before production promotion. This verifies the candidate without replacing the shared DEV frontend or writing fixture data into either environment.

Existing local acceptance: [large real-publication replacement](local-http.json), [development image qualification](packaged-image.json), and [live development gateway acceptance](DEV.json). Exact production-image qualification and deployment receipts will be recorded separately. This is a transport repair; research and Highlights producer behavior are outside this release.
