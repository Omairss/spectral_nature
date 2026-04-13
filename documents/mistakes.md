# Mistakes Log

## 2026-04-12 - Used Playwright Sync API In A Notebook

### What went wrong

- I built the first version of the Seeking Alpha notebook helper on Playwright sync API.
- That works in plain Python scripts, but it fails under Jupyter because the notebook kernel already has an active asyncio loop.

### Impact

- Local notebook execution failed before the browser session could even start.

### Never repeat checklist

1. For notebook-first browser automation, default to Playwright async API.
2. Validate notebook execution through a real notebook runner, not only module imports.
3. When a site has bot protection, verify headed mode separately from headless mode before assuming extraction works.
