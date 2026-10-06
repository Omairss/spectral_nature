# Next.js Agent Development Deployment — 2026-10-02

Status: live, isolated Next.js development workbench accepted for feature-parity evaluation. The replacement was inspected locally before deployment and then verified through the public development endpoint. Production and both Streamlit applications were not changed.

## Endpoint

`https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io`

## Resources

| Role | Azure resource | Exposure | Image |
| --- | --- | --- | --- |
| Browser UI and BFF | `sn-next-ui-dev` | External HTTPS, port 3000 | `next-ui-dev@sha256:3d21a9163e4979eb1b5d4bda9ffeedcd87d781df185bd086baad9321ab0dc7c1` |
| Product API | `sn-api-next-dev` | Internal HTTPS, port 8080 | `api-next-dev@sha256:ca9718c8f0b0b1448b481bbbd2017473bdde24ccf36f00d61fcaf051a585cdc2` |
| Research executor | `sn-research-run-dev` | No ingress; manual one-run job | `research-worker-dev@sha256:73b8bf0ce462d4295a56e1fa610a52941aba552cce44d8a14ad26ca38721d51f` |

All three resources run in `sn-pipeline-env` and read the `development` dataset
namespace. They are separate from the active Streamlit app and its images.

## Request Path

```text
Browser
  -> Next.js same-origin BFF
      -> internal FastAPI
          -> Postgres research run and event journal
          -> exact Azure Container Apps Job start
              -> claim oldest queued development run
              -> AQL/Zopedia research harness
              -> sanitized events and bounded final result
```

The job start request is empty. Query text, identity tokens, model configuration, and
credentials are not carried in a job override or environment variable. The worker claims
the durable run from Postgres after startup.

The frontend does not own a second account store. It authenticates through the private
API against the same database users, password hashes, memberships, account status, and
lockout policy used by the Streamlit applications. The BFF exists to keep browser tokens
in HTTP-only cookies and enforce same-origin mutation controls.

## Security Boundaries

- Access and refresh tokens exist only in host-prefixed HTTP-only browser cookies.
- BFF mutations require an exact configured `Origin` and matching CSRF cookie/header.
- FastAPI independently authenticates, authorizes, and scopes every research operation
  to the durable user id.
- Agent API keys cannot create or read browser research runs.
- The API has internal ingress. The worker has no ingress.
- The API uses the `developer-1` managed identity. Its Container Apps Jobs Operator role
  assignment is scoped to `sn-research-run-dev`, not the resource group or environment.
- The worker uses the pipeline managed identity for Key Vault, storage, and Postgres.
- The API token-signing secret is randomly generated and stored in Key Vault as
  `api-access-token-secret`; it is not present in source or Container Apps environment
  values.
- Browser responses use a per-request nonce CSP, deny framing and objects, restrict
  connections to the same origin, disable unused browser capabilities, and enable HSTS.
- Run inputs and idempotency keys are bounded. Final result DTOs omit prompts, tool dumps,
  critic internals, and hidden reasoning.
- The first release fixes `write_policy=none`; the browser cannot mutate retained memory.

## Acceptance Evidence

- Active isolated revisions: web `sn-next-ui-dev--0000004`; API `sn-api-next-dev--0000007`; research worker remains a manual job with the digest above.
- Deployed Home: HTTP 200 with the real materialized market summary, coverage data, nonce-bearing scripts, and the complete security-header set.
- Deployed login: HTTP 200. Anonymous session inspection returns only `{"authenticated":false}` and clears legacy/non-host cookies; it does not expose auth diagnostics.
- Existing browser authentication restored as `Admin User`; the deployed Admin directory loaded 12 users and one pending invitation rather than fabricating an empty directory.
- Deployed Market Explorer loaded the 1,000-symbol taxonomy catalog, an 80-name ranked view, treemap, and momentum chart without console warnings or errors.
- Daily Brief, Zopedia, Broad Economy, Market Explorer, Portfolio, Performance, Investigator, Options, Trading Agent, and Admin were rendered locally with real data before deployment. Mobile Broad Economy and Market Explorer were inspected at `390x844`.
- Local checks passed: ESLint, TypeScript, Python compilation, a production Next.js build with 28 routes, and `npm audit --omit=dev` with zero runtime vulnerabilities.
- Streamlit development stayed on `sn-streamlit-ui-dev--0000456` throughout the release. No production resource was targeted.
- Missing CSRF token: HTTP 403.
- Correct CSRF token with hostile origin: HTTP 403.
- Public request to the internal API hostname: not routed to the API.
- Live run: `agrun_67bf7681c8024b41b72f2914`.
- Azure execution: `sn-research-run-dev-3j8yv31`, status `Succeeded`.
- Result: completed, medium confidence, 5,376 answer characters, four citations, eight
  limitations, 64 ordered events, and no persisted `reasoning` field.

Qualitative review found the answer useful rather than merely complete: it tied the
strongest same-session crypto-linked moves to opened regulatory evidence, distinguished
later-dated provider snippets, rejected unsupported attribution of semiconductor names,
and proposed specific evidence that would invalidate the interpretation.

## Operating Notes

- Keep the worker trigger manual. The API dispatches one execution per newly queued run.
- Scale remains bounded at one API and one web replica for this development slice.
- A failed or interrupted worker is reconciled as a stale/failed run; the UI does not
  invent completion from missing telemetry.
- Event polling is a transport choice, not the state store. SSE can be added later over
  the same journal without changing run semantics.
- Production promotion requires a separate review of capacity, migrations, rate limits,
  observability, custom domain, and rollback. This deployment is not that promotion.

## Authentication Incident — 2026-10-02

All three frontends reported sign-in unavailable while the shared Postgres server was at
46–47 active connections out of 50. A scheduled `news-ingest-and-features` execution had
left timed-out Zopedia tool threads alive while read calls repeated runtime schema DDL.
After that execution failed, connections returned to 6–9 and both Streamlit containers
plus the private Next.js API reported authentication available with existing users.

Development API hardening is deployed:

- `sn-api-dev--0000047`, image `api@sha256:ec43b010cbcaaef1c1f66bf4326445e9980a9a38b96bff9cf70750f2fe7fa1b4`;
- `sn-api-next-dev--0000001`, image `api-next-dev@sha256:9a7ddf3479615759c91587a3da79a3317d85938344f70cbe099daf62c2d14393`.

The APIs now distinguish store outages from bad credentials, return 503 for unavailable
authentication, and expose only an allowlisted public auth-status payload. The source
reliability fix makes Zopedia bootstrap a cheap schema-readiness check after initialization
and gives SAA-owned Postgres sessions bounded statement, lock, and idle-transaction
timeouts. That scheduled-job change is locally verified but not deployed because the job
is shared with production.

SMTP credentials were revoked after a public status payload was found to include resolved
email diagnostics. Production API revision `sn-api--0000002` now runs the tested hardened
image and exposes only the allowlisted auth-status DTO. One replacement SMTP credential
was created, stored in Key Vault, and cached consumers were restarted. A controlled mail
send produced one Azure `SendMail` request and a `Delivered / Success` result without a
hard bounce.

The connection incident recurred during the restoration. The 18:05 UTC
`news-ingest-and-features-29849405` execution again drove the B1ms server to its connection
ceiling while a Zopedia read stalled. The execution was stopped; auth status returned to
`ready=true`, the production Admin directory returned all 12 users, and the user data was
unchanged. Its schedule remains enabled; the locally verified job/session fix has not been
promoted to the shared production pipeline image.

Development Streamlit revision `sn-streamlit-ui-dev--0000449` now preserves the difference
between an empty directory and an unavailable auth store. Access Management reports the
directory outage explicitly instead of rendering `No users found.` Production UI remains
on revision `sn-streamlit-ui--0000085`; its user list is currently healthy because the
shared store is reachable.

## Control-plane database cutover — 2026-10-02

Authentication and user-owned application state now use the separate B1ms server
`sn-control-pg3-03130136`; pipeline datasets, scheduled jobs, Zopedia, and research-run
state remain on `sn-pg-03130136`. The control migration copied 20 tables and 1,577 rows,
including all 12 users, through one repeatable-read source snapshot and one target
transaction.

The Next.js API is live on `sn-api-next-dev--0000002`, image
`api-next-dev@sha256:eb48a1778971f2e0c39d6ec96ee40cb212c1229c5d508f2c527bb62cefaf8e9c`.
An in-container check resolved auth to the control server, research/data to the original
server, and listed 12 users. The Streamlit dev/API, production Streamlit/API, and Next.js
API all use a native Container Apps secret reference backed by the control-only managed
identity; scheduled jobs cannot read the control vault.

The 20:05 UTC `news-ingest-and-features-29849525` execution was stopped before it could
repeat the shared-server incident. Its schedule remains configured; control-plane
isolation prevents that workload from consuming auth connections, but its data-plane
connection behavior still requires the separate source-level job fix.

## Visualization artifact foundation — 2026-10-02

Historical note: the first visualization transport slice was deployed to the isolated
Next.js development stack, but its surrounding UI was rejected and was not a
frontend-parity milestone:

- web revision `sn-next-ui-dev--0000002`, image
  `next-ui-dev@sha256:0afe7efab7e5809d237e565636e416281a674ade6ffbaa99d6779d988cf3582c`;
- API revision `sn-api-next-dev--0000005`, image
  `api-next-dev@sha256:51c5679d2004efc7834d461dc397c946993f0607532c361cf58f91dfb91bf50a`.

Stock Investigator now loads the existing technical price-channel analysis through the
versioned visualization artifact boundary and the same authenticated BFF. The Plotly
adapter is client-only, uses compiled same-origin CSS under the strict CSP, removes cloud
sharing, and adapts its layout to the container. All application routes are dynamically
rendered because the per-request nonce policy is incompatible with nonce-free static HTML.

The API image was built as an isolated layer on the prior API digest. It adds the artifact
contracts and applies only the symbol-slice calls to the base data-access file; it does not
bundle unrelated working-tree services. A first candidate revision copied the current
dirty data-access file, failed its import at startup, and was immediately rolled back before
the web update. The active revision passed `/health` and an in-container AAPL artifact probe
with 251 rows and computed provenance.

The shared scheduled pipeline image was not deployed. Its locally verified partition
writer will publish deterministic symbol-hash partitions when that image is promoted in a
separate controlled release. Until then, the development API refuses to download large
legacy global artifacts for one-symbol reads and uses the existing on-demand source.

That transport revision has been superseded by the accepted workbench revisions recorded
at the top of this document. The renderer-neutral artifact boundary and strict CSP remain;
the rejected shell does not.

## Feature-parity workbench release — 2026-10-02

The accepted development release deploys the full research workbench shell and its current
Daily Brief, Zopedia, macro, market, portfolio, performance, investigator, options, trading,
and administration surfaces. Zopedia retains durable runs, sources, memory, uploads, and
context-preserving follow-up. The browser talks only to the same-origin BFF; the API remains
internal and the research worker remains a no-ingress manual job.

The release also removes a real Market Explorer failure at its source. Taxonomy-backed
business-lens names, descriptions, and symbol memberships now come from one consistent
catalog snapshot instead of performing repeated database-backed loads for every lens.
Completed Zopedia answers render GitHub-flavored Markdown tables as semantic, responsive
tables rather than raw pipe text.

The images were built from a 67 MiB staged source context rather than the dirty repository
root. Azure Container Registry builds `cjqc`, `cjqb`, and `cjqa` succeeded and the deployed
resources reference their immutable digests. Earlier oversized build runs `cjq7` and `cjq8`
were explicitly canceled before deployment.

## Market intelligence release — 2026-10-06

The isolated Next.js development stack now carries the information-dense Daily Brief and
Broad Economy release. Attention cards expose price, daily and monthly change, a sparkline,
market capitalization, revenue and growth, net income, and free cash flow without requiring
a ticker drill-down. Broad Economy renders every indicator in the chosen category in a
responsive chart grid, adds an observation-to-observation change line on a second axis, and
computes a monthly-last, pairwise-complete correlation heatmap.

- Web: `sn-next-ui-dev--0000004`, immutable digest
  `sha256:3d21a9163e4979eb1b5d4bda9ffeedcd87d781df185bd086baad9321ab0dc7c1`.
- Internal API: `sn-api-next-dev--0000007`, immutable digest
  `sha256:ca9718c8f0b0b1448b481bbbd2017473bdde24ccf36f00d61fcaf051a585cdc2`.
- Web and API scale are each fixed at one warm development replica; the API still has no
  public ingress.
- Manual development pipeline jobs use
  `pipeline-jobs@sha256:2b1865c2a74e11b82ad18b19c36a4d10b59d540e9cbce630a2ff3b0d0a2ace3c`.
- `dataset-refresh-dev-rvolsod` succeeded and copied 22 current snapshot pointers from
  production into the isolated development namespace without changing production.
- `attention-ticker-next-dev-gabf62n` succeeded and published
  `development__attention_ticker_snapshots_1d__20261006T213435Z__3958cd66` with 29 rows,
  including quarterly fundamentals for 18 symbols.
- The full Home builder persisted the development Home and related core datasets before
  beginning optional page-summary work. Market-card freshness does not depend on that
  optional LLM stage.
- Live authenticated review found no browser warnings or errors on Home or Broad Economy.
  Security headers retained HSTS, a request-nonce CSP, frame denial, content sniffing
  protection, and a restrictive permissions policy.
- Streamlit development remained on `sn-streamlit-ui-dev--0000456` at digest
  `sha256:7e059ad6122682792975fbf534b7acf864070a4ac2b6f110fdec1d6a534cd3e5`.
  No production resource was targeted.
