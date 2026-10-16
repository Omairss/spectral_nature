# Setup and operations

Current architecture: Next.js web frontend (`web/`), FastAPI (`api/`), shared research/data services and scheduled workers, plus a SwiftUI client. Streamlit and the root Dash prototype are retired. The historical `streamlit_alpaca_app/` directory continues to hold the current application.

## Local setup

From the application directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Configure Key Vault references and the intended environment in `.env`; keep credentials out of tracked files. Local Python scripts load `infra/.generated/deployment.local.env`, `infra/.generated/email_delivery.local.env`, then `.env`. Old `infra/*outputs.env` fallback files are retired. To refresh generated context, use `bash scripts/show_infra_inventory.sh --write-local` after confirming the intended environment.

Start FastAPI:

```bash
./scripts/run_api_local.sh
```

From `web/` in another terminal:

```bash
npm ci
SPECTRAL_API_BASE_URL=http://localhost:8080 APP_PUBLIC_ORIGINS=http://localhost:3000 APP_COOKIE_SECURE=false npm run dev
```

Use `http://localhost:3000`. The local insecure-cookie setting applies to localhost only; deployed frontends retain secure host cookies. Next.js forwards authenticated requests to FastAPI. Users, memberships, password recovery and refresh sessions belong to the shared account service; provider keys and database credentials never enter the browser.

`Dockerfile.dev` and `scripts/docker_dev.sh` support Python development/API work. The frontend runs with its own Node dependencies. No Streamlit port or launch command remains.

## Runtime ownership

- Web frontend: `web/`, packaged by `Dockerfile.web`.
- HTTP/account transport: `api/`, web gateway routes and `services/auth*`.
- Dataset/chart reads: `data_access/`, pure `compute/`, retained datasets and typed visualization artifacts.
- Research: shared AQL/Zopedia services and durable research runs.
- Producers: `pipeline/jobs/`; Home is precomputed, not researched during page rendering.
- Broker ingestion: dedicated `scripts/sync_robinhood_main.py` worker and provider-isolated store. No broker orders are part of release verification.

## Release and infrastructure

Follow the [release-source policy](../infra/RELEASE_SOURCE_POLICY.md). `scripts/release_application.py` builds accepted Git source and updates reviewed development image templates. It does not execute research jobs or change production. There is no supported historical-overlay, Streamlit, first-migration, or whole-infrastructure bootstrap deployment path in the checkout.

The [resource index](../infra/RESOURCE_INDEX.md) identifies actual consumers. Production `sn-streamlit-ui` hosts **Next.js** and retains its domains; its historical name does not permit a Streamlit image. Production changes require explicit owner approval after the requested behavior is verified locally and in development.

Invites/password resets are sent by the Python backend through `services/emailer.py`. `scripts/setup_email_delivery_azure.sh` targets the modern API, not the frontend. Run it only for an authorized email infrastructure change; it can provision resources, rotate credentials and optionally send a test email. Routine releases preserve mail settings.

The [recovery record](../verification/behavior-recovery-2026-10-16/README.md) and [retirement inventory](../verification/legacy-retirement-2026-10-16/README.md) distinguish completed source cleanup, cloud changes and outstanding recovery work.
