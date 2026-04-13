# Seeking Alpha Notebook Local Run

## What changed

- Switched the notebook helper from Playwright sync API to async API.
- Updated the notebook cells to use `await` for browser start, page open, extraction, and close.
- Added env-driven notebook flags so the same notebook can run in headed/manual mode or automated mode:
  - `SA_HEADLESS`
  - `SA_WAIT_FOR_LOGIN`
  - `SA_TARGETS`
  - `SA_SLOW_MO_MS`
  - `SA_SCROLL_STEPS`
- Added notebook cell ids so modern Jupyter validation does not warn about missing ids.

## Why

- Jupyter runs an asyncio loop already, so Playwright sync API fails inside notebook execution.
- Seeking Alpha returned bot-protection denial pages in headless mode during validation.
- Headed Chromium mode worked and extracted real Seeking Alpha content for `AAPL`.

## Validation

- Helper compiles with `python3 -m py_compile`.
- Notebook JSON parses and validates with `nbformat.validate(...)`.
- Executed notebook locally in a dedicated kernel with:
  - `SA_HEADLESS=0`
  - `SA_WAIT_FOR_LOGIN=0`
  - `SA_TARGETS=AAPL`
- Successful capture saved under `data/seeking_alpha/captures/`.

## Practical note

- Use headed mode for real extraction.
- Use manual login when needed.
- Treat headless mode as best-effort only on Seeking Alpha.
