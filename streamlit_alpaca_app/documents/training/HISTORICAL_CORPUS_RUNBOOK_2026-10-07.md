# Historical corpus collection and frozen replay

Run from `streamlit_alpaca_app` using its `.venv`. The frozen specification and acquisition journal are local under ignored `cache/historical_corpus/`; uploaded immutable releases retain the specification, object receipts and task status. Credentials come from existing environment/Key Vault/operator access. Do not copy credential values into commands, documents or source files.

## Active collection

The current backfill runs under macOS launchd for this login, independently of the chat session. Start or resume the five provider families and the release publisher together:

```sh
.venv/bin/python -u scripts/collect_historical_corpus.py \
  --spec cache/historical_corpus/collection_spec.json \
  --task-file documents/training/FREE_SOURCE_SUPPLEMENT_2026-10-07.json \
  --training-catalog documents/training/HISTORICAL_EPISODES_2026-10-07.json \
  --local-jobs start --storage-auth account-key
```

Use `--local-jobs status` or `--local-jobs stop` with the same specification to inspect or stop these services. Service definitions live under `cache/historical_corpus/launchd/`; logs are `cache/historical_corpus/supervised_*.log`. Start skips loaded services and resumes stopped families from task/page receipts. Failed processes restart with a five-minute throttle; successful completed collectors stay stopped. These services require this workstation to remain awake and connected, and need `start` again after logout/reboot. The owner enabled python3's Documents-folder access after an initial denial; live background access was then verified. Cloud scheduling remains open.

Separate processes pace independent providers and write task/page checkpoints. The source families are `markets,news`, `macro,public`, `social,social_full`, `gdelt`, `crypto_archive` and `sec`. Binance and SEC catalogs expand the task plan after discovery. Only one collector should own a family at a time; do not start duplicates over an already running family.

The launcher holds an OS lease for each source family or the training publisher before authenticated source access. Each exact frozen task also holds a lease across acquisition/checkpoint publication, including supplemental tasks. Process exit releases these leases. A duplicate launcher returns `already_running`; unique sibling temporary files preserve atomic receipt writes. Failure traces retain module/function/line metadata without exception URLs, local variables or credentials.

```sh
.venv/bin/python -u scripts/collect_historical_corpus.py \
  --spec cache/historical_corpus/collection_spec.json \
  --groups markets,news --workers 8 --storage-auth account-key
```

Replace `--groups` and the worker count for the desired stopped family: macro/public 5, combined social/social_full 5, GDELT 4, crypto archive 4, SEC 3. `--storage-auth account-key` obtains an existing operator-authorized write credential in memory; it does not change roles or save the key. `--storage-auth runtime` uses the existing shared credential when that identity has write permission.

```sh
.venv/bin/python -u scripts/collect_historical_corpus.py \
  --spec cache/historical_corpus/collection_spec.json --groups '' \
  --task-file documents/training/FREE_SOURCE_SUPPLEMENT_2026-10-07.json \
  --workers 3 --storage-auth account-key
```

`--publish-only` refreshes an acquisition manifest without fetching sources. Completed tasks are skipped on rerun; failed tasks retry, and paginated market/news/archive tasks resume saved pages. HN capped searches retain a resumable tree of time partitions, including completed leaves of large unfinished months. Source errors remain visible. Model-based document cleaning uses the shared AQL/Zopedia gateway and current development model settings; it retains verbatim blocks and cleaning traces.

## Training release publication

One independent watcher derives newly completed return partitions and freezes updated releases while collection continues. It writes the exact release receipt to `documents/training/HISTORICAL_CORPUS_STATUS_2026-10-07.json` and to the local journal. Each release is immutable. The discoverable Azure pointer is `datasets/namespaces/development/historical_context/latest_training_release.json`.

```sh
.venv/bin/python -u scripts/collect_historical_corpus.py \
  --spec cache/historical_corpus/collection_spec.json \
  --groups macro,markets,news,public,social,social_full,gdelt,crypto_archive,sec \
  --task-file documents/training/FREE_SOURCE_SUPPLEMENT_2026-10-07.json \
  --training-catalog documents/training/HISTORICAL_EPISODES_2026-10-07.json \
  --watch-finalize --storage-auth account-key
```

Use `--finalize-only` instead of `--watch-finalize` for a single publication. The watcher reloads the editable catalog and supplemental task plan between releases. It retains cached derivations and limits new derivation work per publication so acquisition progress remains visible. A release stays partial while acquisition, availability canonicalization or derivation is incomplete or source failures remain. Earlier availability rules and target versions are superseded by canonical partitions and label contract version 4.

## Frozen inputs and labels

`services.historical_training.FrozenTrainingCorpus` opens an immutable manifest through `ImmutableBlobArchive`; every payload read verifies its SHA256. `partitions(dataset, frame_id=..., mode='strict')` yields available-at-time input partitions. `mode='reconstructed'` additionally admits unverified archived text by publication date and removes current engagement. Current catalogs and revised histories stay outside strict historical inputs.

`labels('stock_forward_returns', split='train')` separately yields forward-return and adverse/favorable-excursion targets. Splits are train 2018–2021, development 2022–2023, test 2024 through October 7, 2026. Crossing targets and incomplete horizons are excluded from fitting/scoring. Prices, target dates, missingness and elapsed calendar days remain explicit. Do not pass labels, episode titles/phases, later interpretations or present-day Zopedia memory into a historical model input.

These interfaces supply data to the shared research harness; a trained research selector, reviewed explanations and prospective performance evaluation are separate remaining work. See the [collection design](../plans/HISTORICAL_CONTEXT_TRAINING_HARNESS_2026-10-07.md) and [coverage TODO](HISTORICAL_SOURCE_TODO_2026-10-07.md).
