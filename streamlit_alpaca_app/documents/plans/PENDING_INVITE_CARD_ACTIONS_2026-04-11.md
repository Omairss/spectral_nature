# Pending Invite Card Actions

Date: 2026-04-11

## Goal

Replace the pending-invites grid with a row/card layout that can show direct actions per invite without relying on table-row selection.

## Decision

- Keep `Users` on `st.dataframe`.
- Replace `Pending Invites` with one bordered row/card per invite.
- Add a source-level pending-invite update path so `Change stake %` is a real backend operation, not a UI-only edit.

## Source changes

1. Pending invite update path
- Added `update_pending_invite(...)` to `streamlit_alpaca_app/services/auth_store.py`.
- Added `update_pending_invite(...)` to `streamlit_alpaca_app/services/auth_service.py`.
- Update validation matches invite creation rules:
  - investor invites require a positive share
  - active investor shares cannot exceed 100%
  - non-investor invites store `0.0` share

2. Access Admin UI
- Replaced the selection-based pending-invite table actions with per-invite cards in `streamlit_alpaca_app/app.py`.
- Each card now shows:
  - email
  - role
  - stake for investor invites
  - portfolio slug
  - expiry timestamp
  - short invite id
- Each investor invite card now has:
  - editable `Stake %`
  - `Save Stake`
  - `Resend Invite`
  - `Delete Invite`
- Non-investor invite cards still expose `Resend Invite` and `Delete Invite`.

## Why this shape

- Plot-style tables are poor CRUD surfaces.
- Native Streamlit row cards are simpler than adding a third-party grid.
- This keeps actions obvious and local to the invite they affect.

## Verification

- `python -m pytest tests/test_auth_service.py`
- `python -m pytest tests/test_auth_service.py tests/test_emailer.py tests/test_secrets.py`
- `python3 -m py_compile streamlit_alpaca_app/app.py streamlit_alpaca_app/services/auth_service.py streamlit_alpaca_app/services/auth_store.py`
- Deployed to dev:
  - Container App: `sn-streamlit-ui-dev`
  - Ready revision: `sn-streamlit-ui-dev--0000175`
  - Image: `sha256:6da6d17d1802975e27e010be79fde699b4ffa9318d43413c7f9444506dfa60da`
  - Root HTTP status: `200`

## Follow-up

- If the pending-invite list grows large, add filtering/search above the card list instead of going back to table-bound actions.

## Hotfix

- The first `dev` rollout referenced `_format_access_admin_timestamp(...)` from the card renderer.
- That helper existed in the dirty local workspace but not in the older clean `HEAD` app structure used for the isolated deploy build.
- Fixed by making the card renderer use its own local expiry formatter instead of depending on a helper that may not exist in the deploy source tree.
- Hotfix rollout:
  - Container App: `sn-streamlit-ui-dev`
  - Ready revision: `sn-streamlit-ui-dev--0000176`
  - Image: `sha256:258a4c94c855a46c554327655dbec20b0eca8f4a5df3ad4ca0c544a3bfe2547a`
  - Root HTTP status: `200`
