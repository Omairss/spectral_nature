# Learnings (2026-04-03)

## What burned resources
- I ran one overly broad recursive search (`rg` across the full repo with permissive patterns), which produced very large output and wasted tokens/time.
- I initially searched the wrong paths before resolving the real workspace location, causing an avoidable extra loop.
- I used short-interval polling repeatedly during deployment; this increased command churn without adding much signal.

## What worked better
- Reproducing with direct resolver calls (`DataAccessLayer().resolve_attention_ticker_background("BMY")`) gave fast, deterministic truth before touching deploy.
- Fixing at source (merge logic + stale cache detector) solved the issue without adding noisy UI-specific hacks.
- Adding targeted regression tests prevented rework and made follow-up deploy confidence higher.
- Treating critical source failures as job-failing conditions (instead of warning-only logs) prevents silent partial-success runs and stale datasets.

## Process changes going forward
- Resolve exact file roots first (`find`/targeted `rg`) before any broad search.
- Default to scoped grep commands (specific files/dirs); avoid repo-wide wildcard scans unless strictly necessary.
- For long-running deploy/build commands, poll less frequently (10-30s) and only request high-detail logs when state changes.
- Validate fixes via focused local function checks and tests before deployment.
- Keep deploy scope explicit: dev only unless prod permission is explicitly provided.
- For multi-source jobs, explicitly encode which steps are mandatory and fail the run if mandatory sources do not persist.

## Practical checklist for similar incidents
1. Reproduce issue through smallest callable unit (resolver/service), not full UI first.
2. Confirm cache vs source-of-truth mismatch explicitly.
3. Patch source logic and add one regression test for the exact failing shape.
4. Run narrow tests + one direct payload sanity check.
5. Deploy to dev once, verify revision/image/health, then stop.
