# Access Admin Cloud Audit RBAC Fix - 2026-04-12

## Problem

The Access Admin `Cloud Audit Coverage` block was showing false failures in the live app:

- `sql_server spectral-nature-server error`
- `sql_database spectral-nature-db error`
- `sql_database master error`
- `key_vault spectral-nature-kvault missing`

## Root cause

This was not a source-code regression.

The live UI runtime identity was:

- client id: `4f7c0e9a-2828-45cf-9153-86bbf52acf4e`
- object id: `295ef653-49a9-4e49-9464-72f36e45d7e5`

That identity had secret-access roles on Key Vault, but it did not have management-plane read access on resource group `spectral-nature-2`.

The admin status reader calls Azure ARM for:

- SQL server auditing settings
- SQL database auditing settings
- diagnostic settings on SQL and Key Vault
- workspace/resource metadata

Without a read role on the resource group, the app got live `HTTP 403 AuthorizationFailed` responses for those management-plane reads.

## Fix

Applied Azure RBAC:

- role: `Reader`
- scope: `/subscriptions/b69da224-4a61-4fc4-a5b2-3bb567436762/resourceGroups/spectral-nature-2`
- assignee object id: `295ef653-49a9-4e49-9464-72f36e45d7e5`

Why `Reader`:

- it is read-only
- it covers the needed management-plane reads across SQL, Key Vault, Log Analytics, and diagnostic settings
- it is simpler and more reliable than stitching together several narrower resource-specific read roles for this admin health view

## Verification

Verified from inside the live app containers with:

- `az containerapp exec ... sn-streamlit-ui ...`
- `az containerapp exec ... sn-streamlit-ui-dev ...`

Result after RBAC propagation:

- healthy resources: `4/4`
- audit enabled: `3/3`
- diagnostics enabled: `4/4`
- workspace mismatches: `0`
- error count: `0`

No UI redeploy was required. The existing app revisions began returning healthy audit coverage once the new RBAC assignment propagated.
