# Next.js + Agent Harness Vertical Slice — 2026-10-02

Status: implemented, deployed, and proven in an isolated development stack. Production is out of scope.

## Outcome

Prove the target frontend and agent-runtime architecture with one real path:

- a Next.js App Router client preserves the Spectral Nature desktop identity;
- public Home reads a safe materialized view model;
- authenticated Zopedia creates a durable research run;
- a private Container Apps Job executes the existing AQL/Zopedia harness;
- progress survives browser refresh because Postgres events, not a socket, are authoritative;
- FastAPI remains the identity, authorization, product-contract, and research authority;
- the Next.js BFF holds access and refresh material only in secure HTTP-only cookies;
- the new stack deploys beside the existing Streamlit development app.

## Deployment Result

The vertical slice is live at
`https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`.

- Next.js app: `sn-next-ui-dev`
- Internal FastAPI app: `sn-api-next-dev`
- Private manual worker job: `sn-research-run-dev`
- Development research run: `agrun_67bf7681c8024b41b72f2914`
- Worker execution: `sn-research-run-dev-3j8yv31` (`Succeeded`)
- Existing Streamlit development and production apps: unchanged

The proving run moved from queued to running to completed through the deployed BFF,
internal API, Postgres queue, and one-run worker. It produced 64 durable sanitized
events, a 5,376-character answer, four citations, and eight explicit limitations.
The answer was reviewed for evidence use and correctly separated supported same-day
crypto-equity evidence from unrelated semiconductor movement and later-dated news.

## Architecture

```text
Browser
  -> Next.js development app
      -> same-origin BFF and secure cookies
          -> isolated FastAPI development app
              -> public Home read model
              -> authenticated research-run commands and reads
                  -> Postgres queued run + append-only events
                  -> exact private Azure research worker job trigger
                      -> claim one queued run
                      -> AQL/Zopedia engine
                          -> provider-neutral model transport
                          -> ToolRegistry
                          -> evidence journal and critic
                      -> final result + events
```

The BFF is a transport boundary only. It does not query storage, select tools,
assemble market stories, or implement research behavior.

## Security Contract

- User research requires an authenticated FastAPI user principal. Agent API keys cannot create browser research runs.
- Every run, event read, result read, and cancellation request is scoped by the durable user id.
- Start requests use a user-scoped idempotency key.
- Queries and event payloads have bounded sizes. Prompts, tokens, credentials, and hidden reasoning are never returned as progress.
- FastAPI authorizes every operation independently of frontend navigation.
- The BFF uses host-only `HttpOnly`, `Secure`, `SameSite=Lax` cookies in deployed development.
- Mutating BFF routes require a same-origin request and a matching CSRF token.
- Next.js receives a per-request CSP nonce; framing, objects, ambient browser capabilities, and cross-origin connections are denied.
- The worker has no ingress. The API managed identity receives only permission to start the exact development research job.
- The API passes no prompt, token, provider setting, or secret through the Azure job start request. The job claims its input from Postgres.
- Research defaults to `write_policy=none`; retained-memory mutation is not exposed by this slice.
- Cancellation is cooperative and checked at the harness progress boundary. A cancelled one-run job exits so in-flight daemon work cannot continue in the worker process.

## Durable Run Contract

Run states:

```text
queued -> running -> completed | failed | cancelled
              \-> cancel_requested -> cancelled
```

The database owns:

- run identity, user ownership, task, query hash, budget, and write policy;
- idempotency, dispatch, claim, attempt, cancellation, and completion state;
- monotonically ordered sanitized events;
- a bounded final result document and summary;
- provider/model identity and executor audit fields.

The first slice is replayable after refresh but is not checkpoint-resumable inside a
partially completed model/tool step. A process loss becomes an explicit failed/stale
run; it is never reported as cancelled or complete.

## Public API

- `GET /v1/home` — safe materialized Home view model; no live research.
- `POST /v1/research-runs` — authenticated, idempotent creation and worker dispatch.
- `GET /v1/research-runs/{run_id}` — user-owned run status and final result when ready.
- `GET /v1/research-runs/{run_id}/events?after_sequence=N` — ordered event replay.
- `POST /v1/research-runs/{run_id}/cancel` — user-owned cooperative cancellation request.

The initial frontend polls the event endpoint. This deliberately proves durable replay
without making an SSE connection the product state. SSE can be added later as another
transport over the same event contract.

## Frontend Scope

- Product shell, permanent desktop rail, compact responsive navigation, and existing visual tokens.
- Public Home summary and market narrative from `/v1/home`.
- Login/logout through the BFF.
- Zopedia research form, durable journal, answer-first result, limitations, and refresh-safe `run_id` URLs.
- No placeholder dashboards, fake charts, fake progress, demo answers, or frontend-authored market prose.

## Deployment Isolation

- Next.js app: `sn-next-ui-dev`
- FastAPI app: `sn-api-next-dev`
- Research worker job: `sn-research-run-dev`
- Image repositories are separate from existing Streamlit/API/pipeline repositories.
- Existing production and development Streamlit apps remain unchanged.
- The stack may read the development dataset namespace and shared development Postgres, but every new run is marked with the development environment and user owner.

## Verification

Completed on 2026-10-02:

1. The repository/run lifecycle was exercised against development Postgres.
2. The focused Python suite passed, including run ownership, sanitization, cancellation,
   planner/critic handling, and gateway behavior.
3. The Next.js TypeScript check, ESLint check, and production build passed.
4. A local production-mode UI and API served the materialized Home and completed a live
   research run before any cloud deployment.
5. The deployed Home returned the real materialized summary, seven narrative cards, and
   the stored coverage counts; the deployed desktop view was inspected in Chrome.
6. Deployed cookies are host-only, `HttpOnly`, `Secure`, and `SameSite=Lax`; the BFF
   rejected both a missing CSRF token and a hostile `Origin` with HTTP 403.
7. The deployed response uses a per-request nonce CSP, denies framing and objects, limits
   browser capabilities, enables HSTS, and does not expose access or refresh tokens.
8. The FastAPI app has internal ingress only. A public request to its internal hostname
   does not reach the service, while the Next.js app can read it inside the environment.
9. The API identity can start only the exact development job resource. The worker has no
   ingress, uses a pinned image digest, and receives no user prompt or token at dispatch.
10. The live development run completed in Azure, its Container Apps execution succeeded,
    its durable result reloaded through the BFF, and no event contained a `reasoning` field.

No production deployment is authorized or performed.
