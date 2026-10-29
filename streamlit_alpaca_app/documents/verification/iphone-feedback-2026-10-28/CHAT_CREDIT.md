# Research-chat credit repair — October 28, 2026

Production run `agrun_c5eae19f6fcd46b8b0f96be5` reached the worker and failed before inference. Read-only diagnosis found an authoritative available balance of $1.26 and 40 expired reservations totalling $12.33853852: ten pending ($2.77700752) and thirty unknown ($9.561531). No inference, account top-up or production mutation was performed.

This branch starts at the exact accepted production worker source `a105f517`. It reuses only `reserve_provider_budget` and `settle_provider_budget` from `4f78e927`; all other runtime files match the current accepted worker. The rejected event/feed candidate is excluded. A successful later authoritative balance observation stops ended/expired holds from withholding current liquidity while preserving every unknown charge in its original run and account record. Genuine active requests and probe-overlapping outcomes remain reserved. Failed balance reads and actual empty credit still reject work. Late receipts settle once without moving their original finish time.

Thirty-two checks pass, including seven real isolated-PostgreSQL liquidity/race checks and existing recurring-accounting checks. No research wording, model, provider, production budget or schedule changes. Current $1.26 credit remains a real constraint; repairing false holds does not add funds or guarantee a complete investigation.

All 36 live consumers were read before packaging. The 29 worker consumers already use digest `0e3ab767`; its application inventory matches the chosen baseline. Other seven images/configurations match the accepted record. Only development worker templates may change after exact-image verification. Production requires a fresh explicit owner instruction.
