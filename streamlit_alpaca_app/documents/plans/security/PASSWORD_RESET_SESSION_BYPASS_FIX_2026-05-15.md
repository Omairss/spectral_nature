# Password Reset Session Bypass Fix - 2026-05-15

## Problem

A password-reset path could inherit an existing browser session before the app rendered the reset form. That made clicking a reset-password link look like a login path.

## Source Fix

- Treat `reset_token` and `invite_token` query params as public auth actions, not workspace routes.
- Clear local auth state and browser cookies before cookie restore when an auth-action URL is present.
- Skip the unauthenticated public Home shortcut for auth-action URLs so the reset/create-account forms render directly.
- Keep password reset completion sessionless. Users must log in with the new password after reset.
- Re-check active user status, active portfolio status, and active membership when restoring sessions.
- Reject password login when a user has no active portfolio membership.
- Consume auth-action query params after any successful login so a stale reset/invite URL cannot force logout again on reload.
- Do not immediately rerun after database login/account creation; render the cookie-sync component and continue into the authenticated app so browser persistence can complete.

## Verification

- Added reset service regressions for missing reset token and sessionless reset completion.
- Added session restore regressions for inactive users and inactive portfolios.
- Run focused auth tests before deploy.
