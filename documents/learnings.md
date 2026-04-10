# Learnings

## 2026-04-08

## Seeking Alpha extraction notebook

- For login-gated finance sites, a persistent Playwright profile plus manual login is more reliable than scripted credential entry in a notebook.
- Generic extraction from visible DOM content, tables, meta tags, and JSON-LD is a better starting point than hardcoding CSS classes for one page layout.
- Keeping browser automation in a small helper module makes the notebook easier to reuse and less likely to drift into copy-pasted browser logic.

## 2026-04-10

## Alpaca Key Vault hardening

- Rotating the external credential is not enough if the repo still tracks the old `.env`; the repo and build context have to be hardened in the same pass.
- For app/runtime config, resolving secret names first and then reading only from Key Vault is a simpler and safer contract than mixing secret values and secret-name indirection in the same env surface.
- Azure Container App deploy scripts should not depend on preview-only CLI extension behavior when a stable ARM resource path is available.

## Dependency graph integration

- Moving relationship maps from Python constants into JSON graph files is a low-risk way to add new graph-driven features because the current UI can keep the same render contract while the data model gets more flexible.
- Keeping display-only weight in `edge.attributes.display_weight` avoids mixing UI sizing with semantic fields like `severity` and `confidence`.
