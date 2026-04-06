# Inbound Mail Setup Plan

## Goal

Set up:

- `support@...` for human support handling
- `newsletter@...` for machine-readable newsletter ingestion

while keeping the current Azure Communication Services SMTP path for outbound auth mail.

## Current state as of 2026-04-01

Verified from the current Azure/Graph session:

- Tenant domain list only includes `omairme.onmicrosoft.com`
- `subscribedSkus` is empty
- existing Entra users do not have mailbox-backed `mail` addresses
- no Azure DNS zones were found in the current subscription for mail-domain automation

That means the Microsoft mailbox path is currently blocked by missing tenant prerequisites, not by missing app code.

## What is blocked

We cannot create working shared mailboxes yet because:

1. There is no Exchange Online / Microsoft 365 mail SKU in the tenant.
2. The desired custom mail domain is not present in the tenant.
3. Without those, `support@...` and `newsletter@...` cannot become real inbound mailboxes.

## Recommended Microsoft architecture

- Keep outbound auth email on Azure Communication Services Email.
- Add Exchange Online shared mailboxes for:
  - `support@<mail-domain>`
  - `newsletter@<mail-domain>`
- Use one licensed admin/user account for mailbox access and administration.
- Read `newsletter@...` via Microsoft Graph from a scheduled ingestion job.

## Minimum prerequisite steps outside this repo

### 1. Acquire an Exchange-capable SKU

Minimum expected path:

- at least one Exchange Online or Microsoft 365 license in the tenant

### 2. Add and verify the mail domain in Microsoft 365

Likely desired domain:

- `torres-cap.com`

Required DNS records are controlled outside this repo unless the domain is moved into Azure DNS.

### 3. Create the shared mailboxes

- `support@torres-cap.com`
- `newsletter@torres-cap.com`

### 4. Grant access

- human support/admin user gets mailbox access
- app registration gets Graph mail-read access for `newsletter@...`

## App work once prerequisites exist

### Phase 1

- Create an Entra app for Graph-based newsletter ingestion
- Store client credentials in Key Vault
- Add a scheduled polling job that reads `newsletter@...`
- Normalize subject/from/received date/body preview into Postgres

### Phase 2

- Add HTML-to-text extraction and sender allowlist / folder rules
- Attach newsletter-derived signals to the existing news/attention pipeline

## Practical next action

Once the tenant has:

- a mail-capable SKU
- a verified custom mail domain

the next Codex task should be:

"Set up Microsoft Graph newsletter ingestion for `newsletter@torres-cap.com` and wire it into the pipeline."
