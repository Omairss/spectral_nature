# UI Browser Persistence Env Alignment

Date: 2026-04-12

## Problem

- Production showed the login caption:
  - `Browser-persistent login is disabled in this environment. Reloading or reopening the page requires signing in again.`
- The root cause was not just code. The deployed production app had no explicit `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE` env var, so it inherited the code default.
- The deployment tracker also did not surface the UI resource group or the auth-persistence mode, which made the mismatch harder to spot quickly.

## Source fix

1. Keep the code default conservative unless an env var explicitly enables browser persistence.
2. Make the UI deploy script set `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=1` explicitly for production unless overridden.
3. Stop showing the auth-persistence caption in production login flows.
4. Extend the UI deployment tracker to show:
   - actual UI resource group
   - current auth-persistence mode per app

## Live fix

- Re-enable browser persistence on the production app by setting:
  - `UI_ALLOW_INSECURE_BROWSER_SESSION_COOKIE=1`

## Notes

- This restores the previous product behavior in production.
- It also keeps the readable-cookie risk as an explicit operational choice until a true `HttpOnly` path exists.
