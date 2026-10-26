# Home cache repair — October 26

The shared Home publication loader now uses an explicit `no-store` backend fetch, with no revalidation option. Both the server-rendered page and polling gateway consume this loader. The ETag remains a hash of the complete response, including independently updated tickers and Highlights. Response bodies, replay dates, error handling, client retention and audio behavior are unchanged. The patch removes the size-limited persistent Next fetch cache for live and dated reads.

Source review and the three existing Home gateway/chart checks pass. This is a deterministic transport change; no research inference, prompt, producer, data copy or truncation is involved. Production-mode HTTP review with a replacement larger than 2 MB and cloud development verification remain pending at this source checkpoint.

Development source is based on `030331d0`, whose complete frontend matches the current development image built at `c452f799`. This preserves its accepted paper Trading UI and Health behavior. The only frontend input change is `web/lib/server/home.ts`. Production has a separate accepted frontend baseline; this development branch must not promote its Trading features to production. The same scoped loader correction has been applied to the shared working checkout.

The complete live 36-consumer definitions were read before release preparation. This release targets only `sn-next-ui-dev`; all other images and every non-image configuration must remain unchanged. Existing accepted package/source evidence is retained for unchanged Python consumers. No production promotion is authorized by this patch request.
