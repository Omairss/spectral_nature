# Branding Asset Integration

## Goal

Use the newly added repo branding assets in the Streamlit shell without scattering logos across the app.

## Scope

- use the provided browser favicon for the page icon
- load the favicon asset as an image object so Streamlit replaces its default tab icon reliably
- render the provided white logo lockup inside the existing sidebar brand card
- keep the integration resilient with a text fallback if the asset files are missing
- use the exported image asset rather than raw inline SVG markup so Streamlit does not surface the logo source as text
- reduce the sidebar logo footprint and shift the text hierarchy to `Spectral Nature` with `by Torres Capital` below it
- treat `by Torres Capital` as a small right-aligned serif signoff rather than body text
- use a Bodoni-style signoff treatment and bump the logo closer to the full width of the brand card
- keep the signoff on a local serif fallback stack instead of relying on a remote font import so the sidebar stays stable across pages and environments
- frame the signoff inside its own subtle divider row so it stays aligned and intentional across every page shell
- inject the shared shell CSS on every Streamlit rerun so sidebar framing does not disappear after switching workspaces or pages
- leave prod untouched until the branding looks right in dev
