# Zopedia DB Connection Full Picture - 2026-05-24

## Problem

Local Zopedia probes returned no wiki rows because `services.saa.storage._db_connection()` returned `None`.

The local shell did not have `POSTGRES_CONNECTION_STRING` or Key Vault aliases exported, but the repo already had generated deployment context at `infra/.generated/deployment.local.env`. Runtime scripts source that file; direct Python service calls did not.

## Fix Applied

- `services.secrets` now reads `infra/.generated/deployment.local.env` and `infra/deployment.outputs.env` as a fallback for local service calls.
- `describe_secret_resolution()` now preserves the real Key Vault lookup reason instead of collapsing it into a generic missing-secret state.
- Because `_db_connection()` calls `resolve_secret_value()`, this fixes Zopedia/SAA storage and also benefits other Postgres-backed stores that use the same resolver.
- Retained document reads now cast timestamp columns to text before fetching, matching the existing chunk path. This prevents psycopg from crashing when stored timestamps exceed Python datetime limits.
- Retained document/chunk frames now coerce impossible timestamp years to `NaT` quietly.

## Verified Runtime State

After the fix:

- `_db_connection()` connects successfully.
- Public Postgres tables visible: 20.
- `saa_zopedia_pages`: 50 pages.
- `saa_zopedia_change_proposals`: 9.
- `saa_zopedia_mutation_audit`: 43.
- `saa_zopedia_backlinks`: 82.
- `saa_zopedia_community_index`: 8.
- `saa_documents`: 2,685.
- `saa_evidence_chunks`: 5,357.
- `dataset_versions`: 10,289.

Current Zopedia page mix:

- `concept`: 18 active.
- `source`: 14 active.
- `theme`: 8 active.
- `market_event`: 5 active.
- `macro`: 3 active.
- `entity`: 2 active.

## Company Coverage Check

The wiki layer currently has no pages for the company/business candidates checked:

- `QBTS` / `D-Wave`: 0 Zopedia pages.
- `NVDA` / `NVIDIA`: 0 Zopedia pages.
- `CRWV` / `CoreWeave`: 0 Zopedia pages.
- `NBIS` / `Nebius`: 0 Zopedia pages.
- `quantum`: 0 Zopedia pages.

The retained evidence substrate does contain company evidence:

- `QBTS`: 62 documents, 159 chunks.
- `NVDA`: 12 documents, 27 chunks.
- `CRWV`: 2 documents, 3 chunks.
- `NBIS`: 1 document, 2 chunks.
- `IONQ`: 76 documents, 175 chunks.
- `RGTI`: 67 documents, 181 chunks.

## Implications

The blocker was not only connection wiring. The deeper production picture is:

1. Zopedia has a working DB-backed wiki, but it is mostly macro/eval memory right now.
2. Company evidence exists in retained documents/chunks, but it has not been promoted into durable company wiki pages.
3. News/business resolution should therefore run a source-to-page promotion loop: retained evidence -> company/business pages -> typed claims -> news resolution against those claims.
4. Timestamp hygiene needs an ingestion repair job. The read path is now defensive, but the stored year-48113 rows should be cleaned at source.
5. `search_retained_documents()` is still less reliable than chunk search for company discovery because it scans recent rows first and filters in memory. The next hardening pass should push lexical/ticker filters into SQL like `search_retained_evidence_chunks()` already does.

## Tests

Targeted verification:

```bash
PYTHONPATH=streamlit_alpaca_app streamlit_alpaca_app/.venv/bin/python -m pytest \
  streamlit_alpaca_app/tests/test_secrets.py \
  streamlit_alpaca_app/tests/test_saa_storage.py
```

Result: 15 passed.
