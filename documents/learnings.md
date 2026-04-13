# Learnings

## 2026-04-12

### Seeking Alpha notebook execution

- Jupyter notebooks should use Playwright async API, not sync API, because the kernel already runs an asyncio loop.
- Seeking Alpha is materially more reliable in headed Chromium mode than headless mode.
- A notebook that needs both manual browsing and automated validation should expose mode switches through env vars instead of forking the notebook into separate versions.
