# Infrastructure and releases

Read the [release-source policy](RELEASE_SOURCE_POLICY.md) before building or changing a runtime. The product frontend is Next.js. The [resource index](RESOURCE_INDEX.md) identifies the actual frontend, API and worker owners; Azure names inherited from Streamlit do not identify their current framework.

`scripts/release_source.py` checks the committed acceptance record. `scripts/release_application.py build --role worker|api-next|broker --output PATH` packages the accepted Python source. Its `deploy-development` command accepts verified immutable images and changes only the reviewed development image templates, preserving settings, secrets, allocation and schedules. It does not start jobs or deploy to production. See `--help` for required arguments.

`Dockerfile.web` packages the existing Next.js frontend when an accepted frontend change requires it. Backend recovery does not require a frontend rebuild. Source acceptance and image registration must cover that role before any web promotion.

The old Streamlit launcher/Dockerfile/deployer, filename deployment classifier, API/pipeline bootstrap deployers and completed first-migration/cutover generators are archived. They are not alternate deployment routes. Historical rollout receipts remain evidence only; do not execute their commands or reuse their source trees.

Local operator context lives in ignored `infra/.generated/*.local.env` files. Refresh it with `scripts/show_infra_inventory.sh --write-local`, which now reads modern API targets. Old tracked-output fallbacks are removed.

Email belongs to the API: `scripts/setup_email_delivery_azure.sh` is an explicitly invoked provisioning tool, not part of ordinary deployments. It defaults to `sn-api-next-dev`; production invocation requires owner permission. Do not use its test-mail option without an instruction to send email.

Historical sources and completed migration instructions are retained outside the checkout for 30 days. The [retirement inventory](../verification/legacy-retirement-2026-10-16/README.md) records paths and archive batches. The [October 18 production record](../verification/home-presentation-cleanup-2026-10-18/PRODUCTION.md) now identifies the deployed, explicitly owner-authorized release. Future production changes still require owner authorization.
