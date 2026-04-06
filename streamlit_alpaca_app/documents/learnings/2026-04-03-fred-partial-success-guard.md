# FRED Partial-Success Guard

## Problem

`macro-fred-daily` could report a successful run even when the FRED preload failed.

- `run_fred()` caught `FredAPIError` and only logged it.
- The same run then continued and persisted Treasury yield datasets.
- Job status was still `Succeeded`, which masked FRED data staleness.

## Fix

Treat FRED preload as a required step for the `macro-fred-daily` job.

1. `run_fred()` now accumulates step errors.
2. Missing FRED key is treated as an error condition for this job.
3. Treasury yields still run so useful side data can persist.
4. If any step fails, `run_fred()` raises `RuntimeError` and records failed progress state.

## Regression Coverage

- Added tests that assert `run_fred()` raises when:
  - FRED preload throws an exception.
  - FRED key is missing.
- Tests also verify yield datasets can still persist in those cases.
