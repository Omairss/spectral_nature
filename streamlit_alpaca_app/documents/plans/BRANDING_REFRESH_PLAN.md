# Branding Refresh Plan

## Goal

Make Spectral Nature read like a polished consumer-facing product rather than an internal implementation shell.

## Scope

- remove exposed `Streamlit` and `Alpaca` references from user-facing UI copy
- hide default framework chrome that weakens the brand
- establish a cleaner app shell with a distinct sidebar wordmark, softer surfaces, and more deliberate hierarchy
- keep infrastructure names and package paths unchanged unless changing them materially improves operator experience

## Implemented

- browser title now uses `Spectral Nature`
- a reusable shell stylesheet now controls background, cards, buttons, tabs, and sidebar presentation
- the sidebar now renders a branded identity panel instead of surfacing framework/provider labels
- login and homepage use clearer product copy
- local startup instructions now prefer `scripts/run_ui_local.sh` so docs and UI setup snippets do not expose raw framework commands
- the shell was corrected back to a dark theme after the light palette caused contrast and layout regressions against the existing dark charts and dashboard surfaces
- the shell now uses flatter graphite surfaces, restrained steel accents, and lower-contrast shadows instead of decorative cyan and teal gradients
- right-rail company background actions now render in a stacked control column so long labels do not collapse into one-character vertical text

## Follow-ups

- replace the remaining generic section titles with a shared page-header component if the rest of the workspace needs the same treatment
- introduce a first-party icon or wordmark asset under `branding/` so the browser icon and auth views use the same identity system
