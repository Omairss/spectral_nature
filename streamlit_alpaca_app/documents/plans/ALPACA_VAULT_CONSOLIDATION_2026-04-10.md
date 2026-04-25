# Alpaca Vault Consolidation

Date: 2026-04-10

## Goal

Move the shared Alpaca credentials into one Key Vault and remove the duplicate version from the old pipeline-specific vault.

## Live state before change

- UI apps pointed at `spectral-nature-kvault`.
- Pipeline jobs pointed at `snpipelinekv03130136`.
- The Alpaca secret names were the same in both places:
  - `apca-api-key`
  - `apca-api-secret-key`
- The UI apps were also missing `APCA_API_BASE_URL`, so they would default to the paper trading endpoint.

## Migration plan

1. Move the current Alpaca secrets from `snpipelinekv03130136` into `spectral-nature-kvault`.
2. Update the pipeline jobs to use `spectral-nature-kvault`.
3. Set the UI apps to use the live trading base URL.
4. Verify real Alpaca account and market-data calls through the new vault path.
5. Delete the old Alpaca secrets from `snpipelinekv03130136`.

## Source fixes

- Make `deploy_pipeline_azure.sh` support a Key Vault that lives outside the pipeline resource group.
- Make `show_infra_inventory.sh` discover the active Key Vault from live pipeline job env vars before falling back to vault enumeration.
- Make `deploy_ui_azure.sh` preserve or override `APCA_API_BASE_URL` and `ALPACA_DATA_BASE_URL`.

## Outcome

- All UI apps and pipeline jobs now use `spectral-nature-kvault` for the Alpaca secret names.
- The UI apps now set the live trading endpoint explicitly instead of falling back to paper mode.
- The duplicate Alpaca secrets were deleted and purged from `snpipelinekv03130136`.
- Verification on 2026-04-10 succeeded against the shared vault for both `https://api.alpaca.markets/v2/account` and `https://data.alpaca.markets/v2/stocks/AAPL/bars`.
