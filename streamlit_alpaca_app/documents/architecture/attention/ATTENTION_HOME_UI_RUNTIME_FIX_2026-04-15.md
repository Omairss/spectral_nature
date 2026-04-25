# Attention Home UI Runtime Fix

Date: 2026-04-15

## Problem

The homepage summary code was deployed, but the live UI still looked weak for three different reasons:

1. The summary card did not expose a visible research trace.
2. Seeking Alpha auth failures could stall the post-research summary step for minutes.
3. Azure embedding calls were configured against the chat deployment, so semantic chunk scoring never actually turned on.

## What changed

- The homepage summary payload now includes `top_sources` and `supporting_claims`, and the UI renders them in a compact `Research trace` block.
- The Seeking Alpha access path now:
  - fetches the target article first
  - only attempts login when the page is clearly gated or redirected to login
  - fails fast when the login page itself is blocked by anti-bot protection
- Azure embeddings are now explicit:
  - chat deployment and embedding deployment are separate settings
  - `EMBEDDING_DEPLOYMENT` / `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` are the switch for Azure semantic retrieval
  - if that deployment is not configured, the runtime now skips embeddings instead of repeatedly failing and falling back silently

## Why this is the right fix

- It fixes the visible user problem at source, not with a UI-only patch.
- It keeps the homepage summary moving even when a gated source is flaky.
- It avoids wasting time and requests on embeddings that cannot work in the current Azure setup.

## Current runtime state

- UI trace fields are live in the homepage payload.
- Seeking Alpha failures no longer need to block the summary path before falling back.
- Semantic retrieval is ready in code, but Azure still needs a real embedding deployment before it can be enabled live.
