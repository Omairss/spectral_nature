# Seeking Alpha Notebook Plan

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
