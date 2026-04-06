# Invite Email Designer Admin (2026-04-05)

## Goal

Provide an admin-facing invite template system with two built-in templates and full template lifecycle controls:

- dark template (default)
- white template (current style)
- save / load / delete template workflows
- chart asset control (built-in or uploaded `.png` / `.gif`)

## Requirements Covered

- live invite email preview in Access Admin
- editable look-and-feel controls (copy + palette + graph toggle)
- template library persistence in `app_settings`
- default dark template with white logo + dark chart
- white template kept as built-in current style
- uploaded chart support (`image/png`, `image/gif`)
- invite send path and preview path both use the same template resolver
- admin provision to delete/revoke pending invites

## Architecture

1. Persistence layer
- `app_access.app_settings` in `services/auth_store.py` stores template library JSON.
- Access helpers: `get_app_setting()` / `set_app_setting()`.

2. Invite template service (`services/auth_service.py`)
- Template library key: `invite_email_template_library_v1`.
- Built-ins:
  - `dark-default` (protected, active fallback, white logo, dark chart)
  - `white-default` (protected, color logo, light chart)
- Core APIs:
  - `get_invite_email_template_library()`
  - `get_active_invite_email_template()`
  - `set_active_invite_email_template()`
  - `save_invite_email_template()`
  - `delete_invite_email_template()`
- Backward-compatible wrappers remain:
  - `get_invite_email_theme()`
  - `save_invite_email_theme()`
- Chart asset model:
  - built-in: `{"kind":"builtin","name":"light|dark"}`
  - upload: `{"kind":"upload","filename":...,"mime_type":"image/png|image/gif","data_b64":...}`
- Upload constraints:
  - MIME allowlist: PNG/GIF
  - size limit: 2 MB
- Renderer path:
  - `build_invite_email_preview()` and `issue_invite()` resolve from the same template logic.

3. Admin UI (`app.py`)
- Invite Email Designer now exposes:
  - template selection
  - `Load Template`, `Set Active Template`, `Delete Selected`
  - `Save Current`, `Save As New`
  - logo variant switch (color/white)
  - chart source switch (built-in/upload)
  - upload control for `.png`/`.gif` with validation
- Preview calls `build_invite_email_preview(..., template_override=...)`.

4. Access Admin invite operations (`app.py`, `services/auth_service.py`, `services/auth_store.py`)
- Added pending invite deletion integrated with the Pending Invites table view (row select + delete action).
- Deletion is implemented as status transition from `pending` to `revoked` in the auth store.
- Service layer enforces admin-only access for pending invite deletion.

5. Branding assets
- Added dark chart asset: `branding/email/invite-performance-graph-dark.png`.
- Dark template uses white logo and dark chart by default.

## Reliability Notes

- Built-in templates are protected from deletion to ensure a known fallback state.
- Template payloads are sanitized before persistence and again before render/send.
- If uploaded chart payload is missing/invalid, renderer falls back to built-in chart.
- Preview/send parity is maintained by using one shared rendering pipeline.
- Pending invite deletion is non-destructive (revocation), preserving invite audit history.

## Validation

- Targeted tests:
  - `tests/test_auth_service.py`
  - `tests/test_emailer.py`
- Syntax check:
  - `python -m py_compile app.py services/auth_service.py tests/test_auth_service.py`
