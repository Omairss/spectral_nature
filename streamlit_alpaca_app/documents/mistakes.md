# Mistakes — Active Guidelines

Distilled from 41 past incidents (archived in `past_mistakes.md`). These are the rules to follow, not the stories behind them.

---

## Deploy & Operations

1. **Run `scripts/which_deploy.sh` before every deploy.** It tells you which container has changed files. Deploying the wrong one is a no-op that wastes 10 minutes.
2. **Prod promotion = image + runtime env.** When promoting dev to prod, diff env keys between the two apps. A missing env key makes a working feature look broken.
3. **Identity attachment runs on every update, not just create.** `az containerapp job update` does not reattach user-assigned identities automatically.
4. **Optional shell lookups return 0 with empty output.** Under `set -e`, a "not found" exit code kills the entire deploy script.
5. **Dev/prod behavioral differences must be explicit env vars**, visible in the deployment tracker. Never rely on code defaults or tribal memory.

## Verification & Testing

6. **Verify the user-facing path, not just the service layer.** A passing unit test does not prove the homepage, the API endpoint, or the materialized view is correct.
7. **After deploy, test the exact affected screen or endpoint.** HTTP 200 on the health check is not enough.
8. **Raw SQL changes need a direct store-level test.** Service and API tests cannot catch a misaliased `FROM` clause.
9. **"Code exists" does not mean "feature is live."** Verify the deployment, the config, and the runtime dependency. Embedding client constructed ≠ embedding deployment provisioned.
10. **Test Azure features from inside the live container**, not from local CLI. Managed identity RBAC and CLI credentials are different.
11. **Use the project `.venv` for local pytest.** System Python can miss repo dependencies such as `networkx`, creating false failures and wasted debugging time.

## Secrets & Security

11. **Pre-commit hook scans for secrets.** Installed at `.git/hooks/pre-commit` via `scripts/scan_secrets.sh`. If it blocks your commit, the secret must be removed — not the hook.
12. **Auth fails closed.** When the auth store, Key Vault, or config is unavailable, deny access. Never fall back to anonymous.
13. **Notebooks, legacy folders, and docs are full secret-scan scope.** No file is "low risk."
14. **Secrets live in Key Vault. Config carries secret names, never values.**

## Data Pipeline

15. **Mandatory datasets fail the job. Optional datasets degrade gracefully.** A green job with stale output is worse than a failed job with a clear error. `_MANDATORY_DATASETS` in `attention_home_build.py` enforces this.
16. **One evidence trace contract for all research paths.** Every workflow that gathers evidence routes into `search_results -> source_documents -> evidence_chunks -> claims`. No parallel corpora.
17. **Raw observations are the source of truth. Summaries are cache.** Rebuild derived fields from the observation frame; do not trust a stale summary table.
18. **Retain full text, not just snippets.** When the fetch layer has richer content, carry it forward. A tight char cap turns page browsing into snippet collection. Do not truncate data by default. If text is genuinely too large and risks performance issues, use a generous limit and explicitly note that truncation occurred.
19. **Durable storage needs a read path immediately.** Storage without access traps value in implementation details.
20. **Chunks are the reasoning unit.** Document search alone is not "historical retrieval complete" when the runtime reasons over chunks.
21. **Match signals separate from rerank signals.** Lexical/semantic overlap decides whether a row matched. Authority and freshness refine ordering after.

## LLM & Narrative

22. **No hardcoded sentence templates for user-facing narrative.** Any string the user reads that contains a data value must be LLM-generated, not string-interpolated. No `f"Oil is {direction}"`.
23. **No if/elif dispatch for narrative variation.** Theme, direction, and intensity differences go into the LLM prompt, not branching code.
24. **LLM readiness is logged at startup.** `check_llm_readiness()` runs once per session. If embeddings show "disabled," `EMBEDDING_DEPLOYMENT` is not set.
25. **Generic phrasing is a quality signal.** `"with no clear catalyst"` or `"most plausible chain"` should trigger another research pass, not be accepted as final text.

## Agent Architecture

26. **Fix retention and retrieval before adding agent choreography.** Fancy orchestration does not rescue weak evidence.
27. **Ship the smallest reliable improvement first.** A bounded helper with graceful fallback beats waiting for full Playwright provisioning.
28. **Agent tools must match the product promise.** If the feature promises search-backed analysis, the tools must include live search. The model cannot use tools it cannot see.
29. **Agent must search retained evidence before going external.** The SAA evidence store often already contains the answer. Chat + Search was unable to find an obvious Avis short squeeze because it only did live web search and anomaly checks, missing 15+ articles already in the retained store. Always wire internal evidence search as a first-class tool.
30. **Live search must preserve the user's original query phrasing.** Intent classification strips domain-specific terms ("short squeeze" → "risk"). Pass the user's raw query to the search query builder so it reaches the web search API intact.
31. **Gated sources fail fast.** Fetch the page first, authenticate only when gated. Blocked login pages are fast-fail, not retry-loop.
30. **Match the ambition level of the request.** When the user asks for something ambitious, build the ambitious version — don't downgrade to the easiest thing that vaguely fits. "Agentic" doesn't mean "automated." "Dynamic" doesn't mean "fixed." The path of least resistance is often the wrong path when the ask is ambitious.

## Azure

32. **Resolve subscription from the resource group, not from list order.** Multi-subscription credentials pick the wrong one silently.
33. **ARM fallback ids are for error reporting, not for scoring.** They must never count as discovered resources.
34. **Keep admin observability config separate from pipeline env** when the monitored resources are in a different resource group.

## UI & Product

33. **Start with the actions, then choose the container.** Per-row actions and state transitions first. Grid/table/card second.
37. **Conversational agents need conversation history.** If the UI is a chat model with follow-ups, the backend agent must receive prior turns. A bare follow-up string like "I mean the recent weeks" is meaningless without the prior exchange. Thread compact conversation context through the full call chain.
34. **Implementation details stay in docs, not on user screens.** If the text does not change what the next click should be, remove it.
35. **Loading states reuse real backend stages.** If the agent already emits progress events, surface them — do not invent a fake progress bar.
36. **Precompute expensive media in the scheduled job.** TTS, image generation, and heavy enrichment should not sit on the render path.

## Search Pipeline

37. **Hardcoded intent classifiers destroy domain-specific terms.** A 5-category enum ("oil/rates/defensives/risk/generic") mapped "short squeeze" to "risk" and then searched for SPY/QQQ. The LLM never saw the user's actual query. Classifiers must preserve the user's original phrasing through the search pipeline.
38. **Relevance gates scored against the classified theme, not the user's query.** When the classifier was wrong, the gate rejected every relevant article. Always check match against the user's original query terms as a bypass.
39. **Corrupted timestamps crash psycopg silently.** A year-48113 value in `published_at` crashed `cur.fetchall()` inside a `try/finally` (no `except`), making the function return empty. Cast timestamps to text in SELECTs when the table can contain corrupted data.
40. **Three-stage truncation (2600→1600→1600 chars) starves the LLM of evidence.** Each truncation point was independently "reasonable" but the cascade lost 60-80% of the evidence. Treat the full pipeline's truncation budget as one budget.

## Caching & Vendor

41. **Local cache is disposable. Blob + manifests are durable.** Cache caps and pruning belong in the shared cache helper, not in cleanup scripts.
42. **Vendor bulk endpoints are optional** when the stable per-series path still works. One broken auth key should not look like a full data outage.
