# Experiment Page + Section Rename Plan

## Goal

- Remove the visible Daily Tape workspace.
- Replace it with an admin-only Experiment workspace.
- Move the tape summary + ElevenLabs audio feature onto that Experiment page.
- Rename the main workspace labels:
  - `Agentic Omnibar` -> `Chat + Search`
  - `Portfolio Overview` -> `Portfolio`
  - `Performance` -> `Portfolio Performance`
  - `Market Opportunity` -> `Market Explorer`
  - `FRED Macro` -> `Broad Economy`
  - `Access Admin` -> `Admin`

## Approach

1. Rename section labels at the source constants level.
2. Keep alias normalization for old section names so stored drilldowns and older links still resolve.
3. Show `Experiment` only to admins in the workspace selector.
4. Turn the Experiment page into a placeholder page with the tape summary/audio experiment only.
5. Leave the underlying tape loaders intact for now so the experiment can still read the same attention snapshot.

## Reliability Notes

- Old drilldown values like `Market Opportunity` should continue to work through alias normalization.
- The full Daily Tape route should not remain visible to non-admin users.
