# UI Shell Refinement 2026-04-02

## Goal

Replace the gradient-heavy app shell with a cleaner, sharper dark product language without changing page logic or dashboard content.

## Design Direction

- use near-solid graphite surfaces instead of layered radial gradients
- keep corners and controls tighter so the interface reads more deliberate and less playful
- reduce glow and oversized shadows so data panels feel calmer and more premium
- keep a single restrained steel-blue accent for hierarchy and interaction states
- preserve compatibility with the existing dark Plotly figures and dashboard content

## Source Changes

- updated `_ensure_app_shell_styles()` in `app.py` so the main background, sidebar, page intro, cards, metrics, buttons, tabs, and expanders all inherit the same flatter surface system
- updated `_ensure_inline_loading_banner_styles()` in `app.py` so loading states no longer reintroduce bright gradients
- updated `.streamlit/config.toml` so default Streamlit theme colors align with the refreshed shell rather than fighting it

## Verification

- `python -m py_compile app.py`

## Follow-Ups

- capture before and after screenshots once the dev deployment path is isolated from unrelated local workspace changes
- if any section still feels visually noisy, move repeated content wrappers onto shared helpers instead of adding one-off CSS patches
