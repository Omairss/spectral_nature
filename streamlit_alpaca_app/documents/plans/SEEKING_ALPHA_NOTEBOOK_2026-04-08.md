# Seeking Alpha Notebook Plan

## Status: Extended

## Objective

Create a notebook that can open Seeking Alpha in a real browser window and extract visible page content into structured JSON without hardcoding brittle page selectors.

## Deliverables

- `src/utils/seeking_alpha_browser.py`
  - reusable Playwright helper
  - persistent browser profile support
  - generic extraction for text blocks, tables, links, meta tags, and JSON-LD
- `notebooks/seeking_alpha_extractor.ipynb`
  - setup cells
  - headed browser workflow
  - manual login pause
  - batch extraction and save flow

## Design choices

- Use Playwright instead of Selenium.
  - simpler notebook setup
  - more reliable waiting and browser control on modern sites
- Use a persistent Chromium profile under `data/browser_profiles/seeking_alpha/`.
  - avoids fragile scripted login flows
  - keeps cookies and session state between notebook runs
- Extract from visible DOM plus metadata.
  - less brittle than targeting site-specific classes
  - keeps the notebook useful across article pages and symbol pages

## Reliability limits

- This only extracts content the browser session can actually access.
- It does not bypass paywalls, login gates, CAPTCHAs, or anti-bot controls.
- If Seeking Alpha changes its page structure heavily, generic extraction should degrade more gracefully than hardcoded selectors, but it can still miss some sections.
- Manual login in the headed browser is the low-risk path for sites with 2FA or bot checks.

## Output shape

Each capture is saved as JSON and includes:

- URL and capture time
- page title and high-level page facts
- headings
- visible text blocks
- visible tables
- visible links
- selected meta tags
- parsed JSON-LD blocks

## Next extension points

- add page-to-DataFrame flattening helpers for downstream analysis
- add optional screenshot capture per page
- add site-specific enrichers only if generic extraction misses material data

## Shared Runtime Follow-on

The first version stayed notebook-only on purpose. That is still the safest path for manual login and 2FA.

The shared app and pipeline runtime now also has an authenticated runtime path:

- `services/seeking_alpha_access.py`
  - resolves username and password from Key Vault or env
  - uses a persistent browser profile
  - uses `Scrapling` `StealthySession` as the primary access path
  - falls back to the older Playwright login path if the stealth session fails
  - attempts login before reading a Seeking Alpha page
  - rejects known anti-bot and preview-only responses instead of treating them as success
- `services/page_browsing.py`
  - routes `seekingalpha.com` URLs through that helper first
  - falls back to generic Playwright or HTTP browsing if needed
- `services/aql/summarizer.py`
  - uses that deeper page text to enrich selected Seeking Alpha search hits in the homepage summary loop

## Secret Names

Shared defaults:

- `seeking-alpha-username`
- `seeking-alpha-password`

These names are read from the Key Vault already configured for the runtime. The values are not stored in the repo.

## Reliability Notes

- The headed notebook is still the safest path for manual workflows and unexpected login challenges.
- The shared runtime is now materially stronger than the original Playwright-only path because it can get past the common anti-bot gate and confirm that full article text, not just the shell page, was returned.
- Login can still fail if Seeking Alpha changes its form, adds CAPTCHA, or requires extra verification.
- The runtime path is meant to improve article access when search returns a strong Seeking Alpha hit, not to replace the manual notebook for every site workflow.
